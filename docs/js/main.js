// Wires the cockpit together: chat and lessons on the left, the board with its
// caption and transport in the middle, the inspector and step list on the right.

import { api } from "./api.js";
import { createBoard } from "./board.js";
import { Player } from "./player.js";
import { renderInspector } from "./inspector.js";
import { createLearn, createEvals, createMemory, createActivity, createTools, createAutomations } from "./views.js";
import { h, put, lightMarkdown, fmt, pillarVar } from "./util.js";

const $ = (id) => document.getElementById(id);
const store = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(`loopwright.${key}`)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(`loopwright.${key}`, JSON.stringify(value)); } catch { /* private mode */ } },
};
const newSession = () => `web-${Date.now().toString(36)}`;

const SUGGESTIONS = [
  ["Memory", "Remember that Alex prefers morning meetings"],
  ["Memory", "Book a catch-up with Alex on Friday"],
  ["Loop", "What's 18% of 2450, and save that as a note"],
  ["Handbook", "What is a harness?"],
  ["Workers", "Compare BM25 and embeddings"],
  ["Web", "Search the web for agent evaluation best practices"],
  ["Safety", "Summarise demo://poisoned-page"],
  ["Approval", "Send a message to Priya: running 10 minutes late"],
  ["Workflow", "/workflow morning-brief"],
  ["Workflow", "/workflow triage: what is a harness?"],
  ["Browser", "Use the browser: open demo://site, follow the Pricing link and tell me what the Team plan costs"],
  ["Schedule", "Every weekday at 8am, tell me what's on my calendar"],
  ["Teach", "When I ask for a standup update, always give three bullets: done, doing, blocked"],
];

const LAB = [
  ["pattern", "Orchestration", "react: decide step by step. plan: write the steps first. reflect: a critic reviews the draft.", ["react", "plan", "reflect"]],
  ["memory", "Long-term memory", "Off removes the gate, retrieval, the memory tools and consolidation.", "bool"],
  ["gate", "Retrieval gate", "rules or model decide per turn. always searches every turn. never forces the model to pull.", ["rules", "model", "always", "never"]],
  ["guardrails", "Guardrails", "Off removes the fence on outside content. Try it with the poisoned page.", "bool"],
  ["approvals", "Outside effects", "ask: you approve each one. approve or deny: a fixed policy.", ["ask", "approve", "deny"]],
  ["search", "Web search backend", "auto picks the first one set up. demo is an offline corpus.", ["auto", "demo", "duckduckgo", "tavily", "brave", "claude-code"]],
  ["max_iterations", "Loop round limit", "Set it to 1 and send a two-step request to see the cut-off.", "number"],
  ["max_tool_calls", "Tool call budget", "The gate refuses calls past this number.", "number"],
  ["context_budget", "Context budget (tokens)", "History is compacted past 60% of this. Lower it to trigger compaction sooner.", "number"],
  ["consolidate_every", "Consolidate every N messages", "How often the background memory write runs.", "number"],
  ["chaos_model_failures", "Fail the first N model calls", "Watch retries with backoff, then the fallback model.", "number"],
  ["chaos_tool_failures", "Fail the first N tool calls", "Reads are retried by the harness; writes go back to the model.", "number"],
  ["retries", "Retries per model call", "Set to 0 with fallback off to see a clean failure.", "number"],
  ["fallback", "Fallback model", "Off means a model outage ends the turn.", "bool"],
];

let book, board, player, health, learn, evals, memoryView, activity, toolsView, automations;
let defaults = {}, lab = {}, busy = false, session = newSession(), openLesson = null, lessonRun = null;
let messages = [];            // {role, node, ...}

// ------------------------------------------------------------------ boot
async function boot() {
  health = await api.init();
  book = await api.handbook();
  book.byId = Object.fromEntries(book.concepts.map((c) => [c.id, c]));
  defaults = health.options;
  lab = { ...defaults, ...store.get("lab", {}) };

  board = createBoard($("board"), book.stages, pickNode);
  player = new Player(onPlayerChange);
  learn = createLearn(book, { tryPrompt, openSource });
  evals = createEvals({ replayEval, replayCompare, options: () => ({ provider: $("provider").value.split("|")[0] }) });
  automations = createAutomations({ tryPrompt, replayRun, openConcept });
  memoryView = createMemory();
  activity = createActivity({ replayRun });
  toolsView = createTools();

  drawLegend(); drawProviders(); drawLab(); drawSuggestions(); drawChat(); drawLessons(); bindTransport(); bindChrome(); loadHistory();
  $("static-note").hidden = !api.static;
  if (api.static) {
    $("chat-input").placeholder = "Pick a recorded prompt, or type one";
    $("provider").disabled = true;
    $("history").hidden = true;
  }
  player.load([], {}, { autoplay: false });
  window.addEventListener("hashchange", route);
  route();
  if (!store.get("seen", false)) { setLeftMode("lessons"); openLesson = book.lessons[0].id; drawLessons(); }   // a first visit starts on the guided path
}

// ------------------------------------------------------------------ routing
function route() {
  const [tab, arg] = (location.hash.slice(1) || "cockpit").split("/");
  const name = ["cockpit", "learn", "evals", "memory", "tools", "automations", "activity"].includes(tab) ? tab : "cockpit";
  for (const view of document.querySelectorAll(".view")) view.hidden = view.id !== `view-${name}`;
  for (const a of document.querySelectorAll(".tabs a")) a.classList.toggle("on", a.dataset.tab === name);
  if (name === "learn" && arg) learn.open(arg);
  if (name === "evals") evals.show(arg);
  if (name === "automations") automations.show(arg);
  if (name === "memory") memoryView.show();
  if (name === "activity") activity.show();
  if (name === "tools") toolsView.show();
}

// ------------------------------------------------------------------ chrome
function bindChrome() {
  const theme = store.get("theme", window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const setTheme = (t) => { document.documentElement.dataset.theme = t; $("theme-toggle").textContent = t === "dark" ? "Light" : "Dark"; store.set("theme", t); };
  setTheme(theme);
  $("theme-toggle").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  $("lab-toggle").addEventListener("click", () => {
    const open = $("lab").hidden;
    $("lab").hidden = !open;
    $("lab-toggle").setAttribute("aria-expanded", String(open));
  });
  $("lab-reset").addEventListener("click", () => { lab = { ...defaults, provider: lab.provider }; store.set("lab", {}); drawLab(); });
  $("source-close").addEventListener("click", () => $("source-dialog").close());
  for (const b of document.querySelectorAll("#left-mode button")) b.addEventListener("click", () => setLeftMode(b.dataset.mode));
  $("new-chat").addEventListener("click", () => { leaveClassroom(); session = newSession(); messages = []; drawChat(); player.load([], {}, { autoplay: false }); $("history").value = ""; });
  $("history").addEventListener("change", () => $("history").value && openSession($("history").value));
  $("leave-classroom").addEventListener("click", () => { leaveClassroom(); setLeftMode("chat"); });
  $("show-all").checked = store.get("showAll", false);
  $("show-all").addEventListener("change", () => { store.set("showAll", $("show-all").checked); player.seek(player.index); });
}

function setLeftMode(mode) {
  store.set("seen", true);
  for (const b of document.querySelectorAll("#left-mode button")) b.classList.toggle("on", b.dataset.mode === mode);
  $("lessons").hidden = mode !== "lessons";
  $("suggest").hidden = mode === "lessons";
}

function leaveClassroom() {
  if (api.space === "main") return;
  api.space = "main";
  $("classroom-note").hidden = true;
  messages = []; session = newSession(); drawChat(); loadHistory();
}

function drawLegend() {
  const names = [["gateway", "Gateway"], ["harness", "Harness"], ["loop", "Loop"], ["memory", "Memory"], ["safety", "Safety"], ["ops", "LLM-Ops"]];
  put($("legend"), names.map(([key, label]) => h("span", {}, h("i", { style: `background:var(--${key})` }), label)));
}

function drawProviders() {
  const select = $("provider");
  const localModel = health.providers.local?.preferred || health.providers.local?.models?.[0];     // the one that can call tools
  const label = { scripted: "Scripted (offline)", local: `Local model${localModel ? `: ${localModel}` : ""}`, "claude-code": "Claude Code subscription", anthropic: "Anthropic API", openai: "OpenAI-compatible" };
  put(select, h("option", { value: "auto" }, `Auto: ${label[health.default_provider]}`),
    Object.entries(health.providers).map(([name, p]) => h("option", { value: name, disabled: !p.ok, title: p.note }, label[name] + (p.ok ? "" : " (not set up)"))));
  select.value = lab.provider && health.providers[lab.provider]?.ok ? lab.provider : "auto";
  // a local server can hold several models: one entry each
  for (const m of (health.providers.local?.models || []).filter((name) => name !== localModel).slice(0, 12)) select.append(h("option", { value: `local|${m}` }, `Local model: ${m}`));
  select.addEventListener("change", () => { lab.provider = select.value; saveLab(); });
}

function saveLab() {
  const changed = Object.fromEntries(Object.entries(lab).filter(([k, v]) => defaults[k] !== v));
  store.set("lab", changed);
  $("lab-toggle").textContent = Object.keys(changed).filter((k) => k !== "provider").length ? "Lab (changed)" : "Lab";
}

function drawLab() {
  put($("lab-grid"), LAB.map(([key, label, help, kind]) => {
    let input;
    if (kind === "bool") {
      input = h("select", {}, h("option", { value: "true" }, "on"), h("option", { value: "false" }, "off"));
      input.value = String(lab[key]);
      input.addEventListener("change", () => { lab[key] = input.value === "true"; drawLab(); });
    } else if (kind === "number") {
      input = h("input", { type: "number", min: 0, value: lab[key] });
      input.addEventListener("change", () => { lab[key] = Number(input.value); drawLab(); });
    } else {
      input = h("select", {}, kind.map((v) => h("option", { value: v }, v)));
      input.value = lab[key];
      input.addEventListener("change", () => { lab[key] = input.value; drawLab(); });
    }
    return h("div", { class: `lab-item ${lab[key] !== defaults[key] ? "changed" : ""}` }, h("label", {}, label, input), h("small", {}, help));
  }));
  saveLab();
}

// ------------------------------------------------------------------ the trail: what the agent did, under each reply
function oneLine(output) {
  let d;
  const fenced = /<untrusted_content[^>]*>\n?([\s\S]*?)\n?<\/untrusted_content>/.exec(String(output));
  try { d = JSON.parse(fenced ? fenced[1] : output); } catch { return String(fenced ? fenced[1] : output).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 110); }
  if (d.error) return d.error;
  if ("exit_code" in d) return `exit ${d.exit_code}: ${(d.stdout || d.stderr || "no output").trim().split("\n")[0].slice(0, 90)}`;
  if (d.final_url) return `${d.actions} browser action(s), ended on ${d.final_url}`;
  if (d.workflow) return `${d.steps_run.length} step(s) ran`;
  if (d.workflows) return `${d.workflows.length} workflow(s)`;
  if (d.tasks) return `${d.count} scheduled task(s)`;
  if (d.schedule) return `task #${d.id}: ${d.runs}`;
  if (d.cancelled) return `task #${d.cancelled} cancelled`;
  if ("result" in d) return `${d.expression} = ${d.result}`;
  if (d.action) return `${d.action}: ${d.text}`;
  if (d.created) return `"${d.title}" on ${d.date} at ${d.time}`;
  if (d.passages) return `${d.passages.length} passage(s): ${d.passages.map((p) => p.term).join(", ")}`;
  if (d.memories) return `${d.memories.length} memory item(s)`;
  if (d.events) return `${d.events.length} event(s)`;
  if (d.notes) return `${d.notes.length} note(s)`;
  if (d.results && d.backend) return `${d.results.length} result(s) via ${d.backend}`;
  if (d.results) return `${d.results.length} worker summaries`;
  if (d.sent) return `to ${d.to} (local outbox)`;
  if (d.saved) return d.text ? `"${d.text}"` : `skill ${d.name}`;
  if ("forgotten" in d) return d.forgotten ? `removed "${d.text}"` : d.why;
  if (d.date && d.time) return `${d.weekday} ${d.date} ${d.time}`;
  return JSON.stringify(d).slice(0, 110);
}

function trailRows(events) {
  const rows = [];
  for (const e of events) {
    if (e.span !== "main") continue;
    const d = e.data || {};
    const add = (cls, what, sum, extra = {}) => rows.push({ e, cls, what, sum, ...extra });
    if (e.kind === "memory.gate") add(d.retrieve ? "" : "skip", d.retrieve ? "Looked in memory" : "Skipped memory", d.reason);
    else if (e.kind === "memory.search" && d.hits?.length) add("", "Recalled", d.hits.map((x) => x.text).join("; "));
    else if (e.kind === "skills.match" && d.loaded?.length) add("", "Loaded skill", d.loaded.map((x) => x.name).join(", "));
    else if (e.kind === "plan.made") add("", "Planned", d.plan.replace(/\n/g, " "));
    else if (e.kind === "tool.run") add("", d.tool, oneLine(d.output), { args: d.args, output: d.output });
    else if (e.kind === "tool.error") add("bad", d.tool, `failed: ${d.error}`, { args: d.args ?? {}, output: d.error });
    else if (e.kind === "toolgate.refuse") add("bad", d.tool, `refused: ${d.reason}`, { args: d.args ?? {}, output: d.reason });
    else if (e.kind === "human.decision") add(d.approved ? "" : "bad", "Approval", `${d.approved ? "approved" : "declined"} by ${d.by}`);
    else if (e.kind === "guard.tool_result") add(e.status === "warn" ? "warn" : "", "Fenced as untrusted", d.findings?.length ? `injection suspected: ${d.findings[0].label}` : d.source);
    else if (e.kind === "schedule.fire") add("warn", "Started by the clock", `task #${d.task_id} (${d.schedule}); nobody to approve, so outside effects are denied`);
    else if (e.kind === "workflow.start") add("", "Workflow", `${d.name}: ${d.nodes.length} steps in ${d.waves.length} round(s)`);
    else if (e.kind === "workflow.route") add("", "Route", `chose ${d.chose} (${d.by === "model" ? "a model picked" : "rules in code"})`);
    else if (e.kind === "workflow.node" && d.status !== "skipped" && d.kind !== "tool") add(d.status === "done" ? "" : "bad", `Step ${d.id}`, `${d.kind}: ${String(d.output || "").replace(/\s+/g, " ").slice(0, 90)}`);
    else if (e.kind === "browser.return") add("", "Browser worker", `${d.steps} action(s), ended on ${d.final_url}`);
    else if (e.kind === "model.retry") add("warn", "Model retry", d.error);
    else if (e.kind === "model.fallback") add("warn", "Fell back", `to ${d.to}`);
    else if (e.kind === "critic.review") add(d.passed ? "" : "warn", "Critic", d.passed ? "draft passed" : d.feedback);
    else if (e.kind === "consolidate.resolve" && d.action !== "NOOP") add("", "Memory kept", `${d.action}: ${d.text}`);
    else if (e.kind === "context.compact") add("warn", "Compacted history", `${d.before_tokens} to ${d.after_tokens} tokens`);
    else if (e.kind === "loop.stop" && d.reason !== "answered") add("warn", "Loop cut off", d.reason.replace("_", " "));
  }
  return rows;
}

function trailNode(m, upTo) {
  const rows = trailRows(upTo == null ? m.events : m.events.filter((e) => e.seq <= upTo));
  if (!rows.length) return null;
  return h("ul", { class: "trail", "aria-label": "What the agent did" }, rows.map((r) => h("li", { class: r.cls },
    h("div", { class: "row", title: "Show this step on the board", onclick: () => jumpTo(m, r.e.seq) },
      h("span", { class: "dot" }), h("span", { class: "what" }, r.what), h("span", { class: "sum" }, r.sum)),
    r.args ? h("details", {}, h("summary", {}, "Arguments and raw output"),
      h("pre", {}, `${r.what}(${JSON.stringify(r.args, null, 1)})\n\n${r.output}`)) : null)));
}

function jumpTo(m, seq) {
  if (player.meta.owner !== m && player.meta.turn !== m) loadRun(m.events, { title: m.prompt, turn: m }, false);
  player.seekToSeq(seq);
}

// ------------------------------------------------------------------ chat
function drawSuggestions() {
  const recorded = api.suggestions();
  const items = recorded ? recorded.map((text) => ["", text]) : SUGGESTIONS;
  put($("suggest"), items.map(([group, text]) => h("button", { type: "button", onclick: () => send(text) }, group ? h("i", {}, group) : null, text)));
}

function drawChat() {
  const log = $("chat-log");
  if (!messages.length) {
    put(log, h("div", { class: "empty-chat" },
      h("h2", {}, "Ask it something and watch it work"),
      h("p", {}, "Each step the agent takes lights up a box on the board, at a pace you can follow. Under every reply is the list of what it did: memory, skills, and each tool call with its arguments and output."),
      h("p", { style: "margin-top:8px" }, "New here? Open Lessons above for a guided path through every part.")));
    return;
  }
  put(log, messages.map((m) => m.node));
  log.scrollTop = log.scrollHeight;
}

function agentNode(m) {
  const node = h("div", { class: "msg agent" });
  m.render = (upTo) => {
    if (!m.revealed) {
      node.className = "msg agent pending";
      put(node, h("div", { class: "body" }, m.status || "Working..."), trailNode(m, upTo ?? 0),
        m.result ? h("div", { class: "meta" }, h("button", { onclick: () => { player.pause(); player.seek(player.events.length - 1); reveal(m); } }, "Skip to the answer")) : null);
      return;
    }
    node.className = "msg agent";
    const r = m.result.receipt || {};
    put(node,
      h("div", { class: "body", html: lightMarkdown(m.result.reply) }),
      trailNode(m),
      h("div", { class: "meta" },
        m.events.length ? h("button", { onclick: () => loadRun(m.events, { title: m.prompt, turn: m }) }, "Replay") : null,
        r.model ? h("span", {}, r.model) : null,
        r.model_calls != null ? h("span", {}, `${r.model_calls} model call(s)`) : null,
        r.tokens_in != null ? h("span", {}, `${r.estimated ? "~" : ""}${fmt(r.tokens_in + r.tokens_out)} tokens`) : null,
        r.duration_ms != null ? h("span", {}, r.duration_ms > 1500 ? `${(r.duration_ms / 1000).toFixed(1)} s` : `${fmt(r.duration_ms)} ms`) : null,
        r.cost_usd ? h("span", {}, `$${r.cost_usd.toFixed(4)}`) : null,
        feedbackButtons(m)));
  };
  m.render();
  return node;
}

// A thumbs up or down, and "keep this run as a regression test": the feedback loop, in three buttons.
function feedbackButtons(m) {
  const id = m.result?.turn_id;
  if (api.static || !id || api.space !== "main") return null;
  const rate = async (rating) => { await api.feedback(id, rating); m.rated = rating; m.render(); };
  return h("span", { class: "fb" },
    h("button", { class: m.rated === "up" ? "on" : "", title: "This answer was good", "aria-label": "Good answer", onclick: () => rate("up") }, "Good"),
    h("button", { class: m.rated === "down" ? "on" : "", title: "This answer was wrong or unhelpful", "aria-label": "Bad answer", onclick: () => rate("down") }, "Bad"),
    h("button", { title: "Save this run as an eval case, so a later change cannot quietly break it",
      onclick: async () => { await api.saveCase(id); m.saved = true; m.render(); } }, m.saved ? "Saved as eval case" : "Save as eval case"));
}

function reveal(m) {
  if (m.revealed || !m.result) return;
  m.revealed = true;
  m.render();
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
  if (lessonRun && lessonRun.message === m) finishLesson();
}

function approvalCard(event, m) {
  const d = event.data;
  const card = h("div", { class: "approval" },
    h("h3", {}, "Approve this action?"),
    h("p", {}, "The agent wants to do something with effects outside this conversation. It is paused until you answer."),
    h("pre", {}, `${d.tool}(${JSON.stringify(d.args, null, 1)})`),
    h("div", { class: "row" },
      h("button", { class: "yes", onclick: () => answer(true) }, "Approve"),
      h("button", { onclick: () => answer(false) }, "Decline")));
  async function answer(approved) {
    put(card, h("p", {}, approved ? "You approved it." : "You declined it."));
    await api.approve(d.call_id, approved);
    player.play();
  }
  messages.splice(messages.indexOf(m), 0, { role: "approval", node: card });
  drawChat();
}

// Run one turn. `quiet` turns skip the board (used for a lesson's setup prompts).
async function send(text, { options = {}, quiet = false, inSession = null, lesson = null } = {}) {
  const message = (text ?? $("chat-input").value).trim();
  if (!message || (busy && !quiet)) return null;
  if (!quiet) { busy = true; $("send").disabled = true; $("chat-input").value = ""; location.hash = "#cockpit"; }
  messages.push({ role: "user", node: h("div", { class: "msg user" }, message), text: message });
  const m = { role: "agent", prompt: message, events: [], result: null, revealed: false, status: "Working..." };
  m.node = agentNode(m);
  messages.push(m);
  if (lessonRun && !quiet && !lessonRun.message) lessonRun.message = m;
  drawChat();
  const [provider, model] = $("provider").value.split("|");
  const turnOptions = { ...lab, provider, model: model || "", ...options };
  if (!quiet) {
    player.setSpeed(Number($("speed").value));
    player.load([], { title: message, live: true, owner: m }, { live: true, autoplay: true });
  }
  try {
    m.result = await api.chat(message, inSession || session, turnOptions, (event) => {
      m.events.push(event);
      if (quiet) return;
      player.push(event);
      if (event.kind === "human.request" && turnOptions.approvals === "ask" && !api.static) {
        player.seekToSeq(event.seq);              // jump the board to the step that is waiting on you
        approvalCard(event, m);
      }
    }, lesson);
  } catch (error) {
    m.result = { ok: false, reply: `Could not reach the server: ${error.message}`, receipt: {}, checks: [] };
  }
  if (quiet) { reveal(m); return m; }
  player.finish();
  m.render(player.current?.seq);
  if (!m.events.length || (!player.playing && player.index >= player.events.length - 1)) reveal(m);
  busy = false;
  $("send").disabled = false;
  if (api.space === "main") loadHistory();
  return m;
}

function tryPrompt(prompt) { leaveClassroom(); setLeftMode("chat"); location.hash = "#cockpit"; send(prompt); }

// ------------------------------------------------------------------ history
async function loadHistory() {
  if (api.static) return;
  const sessions = await api.sessions();
  put($("history"), h("option", { value: "" }, "History"),
    sessions.map((s) => h("option", { value: s.session }, `${(s.title || "").slice(0, 42)} (${Math.floor(s.messages / 2)})`)));
}

async function openSession(id) {
  leaveClassroom();
  session = id;
  const rows = await api.transcript(id);
  messages = [];
  for (const row of rows) {
    if (row.role === "user") { messages.push({ role: "user", node: h("div", { class: "msg user" }, row.content), text: row.content }); continue; }
    const m = { role: "agent", prompt: messages.at(-1)?.text || "", events: [], revealed: true, result: { reply: row.content, receipt: {}, turn_id: row.turn_id } };
    try {
      const trace = await api.run(row.turn_id);
      m.events = trace.events; m.result.receipt = trace.end.receipt || {};
    } catch { /* the trace file was deleted; the text is still shown */ }
    m.node = agentNode(m);
    messages.push(m);
  }
  setLeftMode("chat");
  drawChat();
}

// ------------------------------------------------------------------ lessons
function drawLessons() {
  const done = store.get("lessons", []);
  $("lesson-progress").textContent = `${done.length}/${book.lessons.length}`;
  put($("lessons"), book.lessons.map((lesson, i) => {
    const open = openLesson === lesson.id;
    const ran = !!(lessonRun && lessonRun.id === lesson.id && lessonRun.finished);
    const used = new Set();
    return h("div", { class: `lesson ${open ? "open" : ""}` },
      h("button", { onclick: () => { openLesson = open ? null : lesson.id; drawLessons(); } },
        h("span", { class: "n" }, i + 1), h("span", {}, lesson.title), done.includes(lesson.id) ? h("span", { class: "done" }, "done") : null),
      open ? h("div", { class: "inside" },
        h("p", {}, lesson.goal),
        lesson.open
          ? h("button", { class: "run", onclick: () => { markDone(lesson.id); location.hash = `#${lesson.open}`; } }, "Open the Evals tab")
          : h("button", { class: "run", disabled: busy || (lesson.needs === "browser" && !health.browser), onclick: () => runLesson(lesson) }, ran ? "Run it again" : "Run this lesson"),
        lesson.needs === "browser" && !health.browser ? h("p", { class: "hint", style: "margin-top:6px" }, "This lesson drives a real browser and found none here. Install Chrome, Chromium, Edge or Brave, or set LOOPWRIGHT_CHROME.") : null,
        lesson.prompt ? h("p", { class: "hint", style: "margin-top:6px" }, `It sends: "${lesson.prompt}"`) : null,
        lesson.watch.length ? h("ol", {}, lesson.watch.map((w) => {
          const fresh = (e) => e.kind === w.kind && !used.has(e.seq);
          const hit = ran ? (lessonRun.message.events.find((e) => fresh(e) && e.span === "main") || lessonRun.message.events.find(fresh)) : null;
          if (hit) used.add(hit.seq);
          return h("li", { class: ran && !hit ? "dim" : "" }, w.say, " ",
            hit ? h("button", { class: "linkish", onclick: () => jumpTo(lessonRun.message, hit.seq) }, `Show step ${hit.seq}`) : null);
        })) : null,
        ran || lesson.open ? h("p", { class: "take" }, lesson.takeaway) : null,
        h("div", { style: "display:flex;flex-wrap:wrap;gap:4px 12px;margin-top:8px" },
          lesson.concepts.filter((id) => book.byId[id]).map((id) => h("button", { class: "linkish", onclick: () => openConcept(id) }, book.byId[id].term)))) : null);
  }));
}

function markDone(id) {
  const done = new Set(store.get("lessons", []));
  done.add(id);
  store.set("lessons", [...done]);
  drawLessons();
}

async function runLesson(lesson) {
  if (busy) return;
  api.space = "classroom";
  $("classroom-note").hidden = false;
  await api.resetClassroom();
  for (const tool of lesson.enable || []) if (!api.static) await api.toggleTool(tool, true);      // switched on in the classroom only
  messages = [];
  lessonRun = null;
  const first = newSession();
  for (const prompt of lesson.before || []) await send(prompt, { quiet: true, inSession: first, options: { approvals: "approve" }, lesson: lesson.id });
  session = lesson.new_session ? `${first}-next` : first;
  lessonRun = { id: lesson.id, finished: false, message: null };
  drawLessons();
  const m = await send(lesson.prompt, { options: lesson.options || {}, lesson: lesson.id });
  if (m?.revealed) finishLesson();
}

function finishLesson() {
  if (!lessonRun || lessonRun.finished) return;
  lessonRun.finished = true;
  markDone(lessonRun.id);
}

// ------------------------------------------------------------------ player <-> page
function onPlayerChange(state) {
  board.show(state.events, state.index, { animate: state.animate, speed: state.speed, focus: !$("show-all").checked });
  renderInspector($("inspector"), state, book, { openConcept, openSource });
  drawStrip(state);
  drawTimeline(state);
  const e = state.current;
  const stage = e ? book.stages[e.stage] : null;
  $("caption").style.setProperty("--c", stage ? pillarVar(stage.pillar) : "var(--line)");
  put($("caption"), e
    ? [h("b", {}, `${state.index + 1}. ${e.title.toLowerCase().startsWith(stage.label.toLowerCase().split(" ")[0]) ? "" : `${stage.label}: `}${e.title}`), h("span", {}, e.detail)]
    : [h("b", {}, state.events.length ? "Press play to walk through this run" : "Nothing is running"),
       h("span", {}, state.events.length ? "Or step through it one event at a time with the arrow keys." : "Send a message, or open Lessons on the left. This line always says, in plain words, what the highlighted box just did.")]);
  put($("where"),
    e ? `Step ${state.index + 1} of ${state.events.length}` : state.events.length ? `${state.events.length} steps ready` : state.live ? "Waiting for the first step" : "No run loaded",
    state.waiting ? h("small", {}, "waiting for the agent...") : null);
  $("t-play").setAttribute("aria-label", state.playing ? "Pause" : "Play");
  $("t-play-icon").innerHTML = state.playing ? '<path d="M6 4v12M14 4v12"/>' : '<path d="M6 4l10 6-10 6z"/>';
  $("board-title").textContent = state.meta.title ? `${state.meta.live ? "" : "Replay: "}${state.meta.title}` : "Send a message to light up the board";
  $("step-count").textContent = state.events.length ? `${state.events.length} recorded` : "";
  const owner = state.meta.owner;
  if (owner && !owner.revealed) {
    if (e) { owner.status = `Step ${state.index + 1}: ${e.title}`; owner.render(e.seq); }
    if (owner.result && (e?.kind === "reply.send" || (!state.live && state.index >= state.events.length - 1 && state.events.length))) reveal(owner);
  }
}

function drawStrip(state) {
  const strip = $("strip");
  strip.setAttribute("aria-valuemax", Math.max(0, state.events.length - 1));
  strip.setAttribute("aria-valuenow", Math.max(0, state.index));
  if (strip.children.length !== state.events.length) {
    put(strip, state.events.map((e) => h("i", { title: e.title,
      class: `${e.span !== "main" ? "sub" : ""} ${["warn", "blocked", "error"].includes(e.status) ? "bad" : ""}`,
      style: `--c:${pillarVar(book.stages[e.stage]?.pillar)}` })));
  }
  [...strip.children].forEach((cell, i) => { cell.classList.toggle("past", i < state.index); cell.classList.toggle("now", i === state.index); });
}

function drawTimeline(state) {
  const list = $("timeline");
  if (list.children.length !== state.events.length) {
    put(list, state.events.map((e, i) => h("li", { style: `--c:${pillarVar(book.stages[e.stage]?.pillar)}`, class: e.span !== "main" ? "sub" : "",
      onclick: () => { player.pause(); player.seek(i, true); } },
      h("span", { class: "n" }, i + 1), h("span", { class: "dot" }), h("span", { class: "t" }, e.title),
      h("span", { class: `s ${e.status}` }, e.status === "ok" ? "" : e.status))));
  }
  [...list.children].forEach((row, i) => { row.classList.toggle("now", i === state.index); row.classList.toggle("future", i > state.index); });
  const now = list.children[state.index];
  if (now) now.scrollIntoView({ block: "nearest" });
}

function bindTransport() {
  $("t-play").addEventListener("click", () => player.toggle());
  $("t-back").addEventListener("click", () => player.step(-1));
  $("t-next").addEventListener("click", () => player.step(1));
  $("t-start").addEventListener("click", () => { player.pause(); player.seek(0); });
  $("t-end").addEventListener("click", () => { player.pause(); player.seek(player.events.length - 1); });
  $("speed").value = String(store.get("speed", 1));
  player.setSpeed(Number($("speed").value));
  $("speed").addEventListener("change", () => { player.setSpeed(Number($("speed").value)); store.set("speed", Number($("speed").value)); });
  $("only-main").addEventListener("change", () => player.setOnlyMain($("only-main").checked));

  const strip = $("strip");
  const seekAt = (clientX) => {
    const box = strip.getBoundingClientRect();
    const n = player.events.length;
    if (n) { player.pause(); player.seek(Math.min(n - 1, Math.max(0, Math.floor(((clientX - box.left) / box.width) * n)))); }
  };
  strip.addEventListener("pointerdown", (e) => { strip.setPointerCapture(e.pointerId); seekAt(e.clientX); });
  strip.addEventListener("pointermove", (e) => { if (e.buttons) seekAt(e.clientX); });

  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea, select") || $("view-cockpit").hidden || e.metaKey || e.ctrlKey) return;
    const keys = { " ": () => player.toggle(), ArrowLeft: () => player.step(-1), ArrowRight: () => player.step(1),
                   Home: () => { player.pause(); player.seek(0); }, End: () => { player.pause(); player.seek(player.events.length - 1); } };
    if (keys[e.key] && !e.target.closest(".node, button, summary")) { e.preventDefault(); keys[e.key](); }
  });

  $("chat-form").addEventListener("submit", (e) => { e.preventDefault(); send(); });
  $("chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
}

function loadRun(events, meta, autoplay = true) {
  location.hash = "#cockpit";
  player.setSpeed(Number($("speed").value));
  player.load(events, meta, { live: false, autoplay });
}

async function replayRun(id) {
  const trace = await api.run(id);
  loadRun(trace.events, { title: trace.turn.message, turn_id: id });
}

async function replayEval(c) {
  const trace = await api.evalTrace(c.id);
  if (trace) loadRun(trace.events, { title: `${c.id}: ${c.title}` });
}

async function replayCompare(slug, id, title) {
  const trace = await api.compareTrace(slug, id);
  if (trace) { loadRun(trace.events, { title }); location.hash = "#cockpit"; }
}

// ------------------------------------------------------------------ handbook hooks
function openConcept(id) { location.hash = `#learn/${id}`; }

function pickNode(id) {
  const cards = book.concepts.filter((c) => c.node === id && c.runs !== false);
  const card = cards.find((c) => (c.kinds || []).length) || cards[0];
  if (card) openConcept(card.id);
}

async function openSource(path) {
  const file = await api.source(path);
  $("source-path").textContent = file.path || path;
  $("source-text").textContent = file.text || file.error || "";
  $("source-dialog").showModal();
}

boot().catch((error) => {
  put(document.querySelector("main"), h("p", { style: "padding:30px" }, `Loopwright could not start: ${error.message}`));
});
