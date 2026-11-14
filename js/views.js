// The reading pages: Learn, Evals (report, model comparison, your own cases), Memory, Tools, Automations, Activity.

import { api } from "./api.js";
import { workflowGraph } from "./inspector.js";
import { h, put, fmt, pct, pillarVar } from "./util.js";

const spaceNote = () => (api.space === "classroom" ? h("p", { class: "classroom", style: "margin-bottom:14px;border-radius:6px" }, "You are looking at the classroom: the scratch copy of the agent that lessons run in. Leave it from the cockpit to see your own.") : null);

const paragraphs = (text) => String(text).trim().split(/\n\s*\n/).map((p) => h("p", {}, p.trim()));

// ============================================================ LEARN
export function createLearn(book, actions) {
  const list = document.getElementById("learn-list");
  const detail = document.getElementById("learn-detail");
  const search = document.getElementById("learn-search");
  const tabs = [...document.querySelectorAll("#view-learn .seg button")];
  let mode = "glossary", selected = book.concepts[0].id, scenario = book.interview[0].id, card = 0, shown = false;
  const deck = book.concepts.filter((c) => c.question);

  search.placeholder = `Search ${book.concepts.length} terms`;

  function matches(c, q) {
    return !q || [c.term, c.one_liner, ...(c.aliases || [])].join(" ").toLowerCase().includes(q);
  }

  function drawList() {
    const q = search.value.trim().toLowerCase();
    put(list);
    search.hidden = mode !== "glossary";
    if (mode === "glossary") {
      for (const [pillar, label] of Object.entries(book.pillars)) {
        const rows = book.concepts.filter((c) => c.pillar === pillar && matches(c, q));
        if (!rows.length) continue;
        list.append(h("h3", { style: `--c:${pillarVar(pillarColour(pillar))}` }, label));
        for (const c of rows) {
          list.append(h("button", { class: c.id === selected ? "on" : "", onclick: () => { selected = c.id; draw(); } },
            c.term, h("small", {}, c.one_liner)));
        }
      }
      if (!list.children.length) list.append(h("p", { class: "hint", style: "padding:12px 8px" }, "No term matches that. Try a shorter word."));
    } else if (mode === "interview") {
      for (const s of book.interview) {
        list.append(h("button", { class: s.id === scenario ? "on" : "", onclick: () => { scenario = s.id; shown = false; draw(); } },
          s.title, h("small", {}, s.kind)));
      }
    } else {
      list.append(h("p", { class: "hint", style: "padding:12px 8px" },
        `${deck.length} questions drawn from the handbook. Answer out loud, then reveal. The order is shuffled each visit.`));
    }
  }

  function conceptChips(ids) {
    return h("div", { class: "also" }, ids.filter((id) => book.byId[id]).map((id) =>
      h("button", { onclick: () => { mode = "glossary"; selected = id; setMode(); } }, book.byId[id].term)));
  }

  function drawDetail() {
    if (mode === "glossary") {
      const c = book.byId[selected];
      const colour = pillarVar(pillarColour(c.pillar));
      put(detail, h("div", { class: "inner", style: `--c:${colour}` },
        h("span", { class: "chip stage", style: `--c:${colour}` }, book.pillars[c.pillar]),
        c.runs === false ? h("span", { class: "chip", style: "margin-left:6px", title: "This idea is explained here but not implemented in this repository" }, "Explained, not built here") : null,
        h("h1", {}, c.term),
        h("p", { class: "lede" }, c.one_liner),
        h("p", {}, c.explain),
        h("h2", {}, "Why it exists"), h("p", {}, c.why),
        h("h2", {}, "The tradeoff"), h("p", {}, c.tradeoff),
        h("div", { class: "actions" },
          c.try && c.runs !== false ? h("button", { class: "primary", onclick: () => actions.tryPrompt(c.try) }, `See it run: "${c.try}"`) : null,
          h("button", { onclick: () => actions.openSource(c.file) }, `Read ${c.file}`)),
        c.aliases && c.aliases.length ? h("p", { class: "hint" }, `Also called: ${c.aliases.join(", ")}`) : null,
        qa(c.question, c.answer)));
    } else if (mode === "interview") {
      const s = book.interview.find((x) => x.id === scenario);
      put(detail, h("div", { class: "inner" },
        h("span", { class: "chip" }, s.kind),
        h("h1", {}, s.title),
        h("p", { class: "lede" }, s.prompt),
        h("p", { class: "hint" }, "Say your answer out loud first. A strong answer clarifies, picks a shape, names mechanisms, covers failure, and says how it will be measured."),
        h("div", { class: "qa" }, h("h2", {}, "A strong answer"),
          shown ? h("div", { class: "a" }, paragraphs(s.answer)) : h("button", { class: "primary", onclick: () => { shown = true; drawDetail(); } }, "Show the answer")),
        h("h2", {}, "Cards behind this answer"), conceptChips(s.concepts || [])));
    } else {
      const c = deck[card % deck.length];
      put(detail, h("div", { class: "inner" },
        h("span", { class: "chip" }, `Card ${(card % deck.length) + 1} of ${deck.length}`),
        h("div", { class: "qa" }, h("h2", {}, book.pillars[c.pillar]), h("p", { class: "q" }, c.question),
          shown ? h("div", { class: "a" }, h("p", {}, c.answer)) : h("button", { class: "primary", onclick: () => { shown = true; drawDetail(); } }, "Show the answer")),
        h("div", { class: "actions" },
          h("button", { onclick: () => { card += 1; shown = false; drawDetail(); } }, "Next card"),
          h("button", { onclick: () => { mode = "glossary"; selected = c.id; setMode(); } }, `Open the card: ${c.term}`))));
    }
    detail.scrollTop = 0;
  }

  function qa(question, answer) {
    if (!question) return null;
    const box = h("div", { class: "qa" }, h("h2", {}, "How it comes up in an interview"), h("p", { class: "q" }, question));
    const reveal = h("button", { onclick: () => { reveal.replaceWith(h("div", { class: "a" }, h("p", {}, answer))); } }, "Show a strong answer");
    box.append(reveal);
    return box;
  }

  function draw() { drawList(); drawDetail(); }
  function setMode() {
    tabs.forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
    if (mode === "cards") { deck.sort(() => Math.random() - 0.5); card = 0; }
    shown = false;
    draw();
  }
  tabs.forEach((b) => b.addEventListener("click", () => { mode = b.dataset.mode; setMode(); }));
  search.addEventListener("input", drawList);
  draw();

  return { open(id) { if (book.byId[id]) { mode = "glossary"; selected = id; search.value = ""; setMode(); } } };
}

// handbook pillars -> the six board colours
export function pillarColour(pillar) {
  return { harness: "harness", loop: "loop", context: "harness", memory: "memory", tools: "loop", safety: "safety",
           reliability: "safety", ops: "ops", evals: "ops", models: "gateway" }[pillar] || "gateway";
}

// ============================================================ EVALS
export function createEvals(actions) {
  const page = document.getElementById("evals-body");
  const tabsBar = h("div", { class: "subtabs" }), root = h("div", {});
  page.append(tabsBar, root);
  let report = null, busy = false, progress = "", tab = "report";
  const TABS = [["report", "Harness evals"], ["compare", "Compare models"], ["mine", "Your cases"]];
  const drawTabs = () => put(tabsBar, TABS.map(([id, label]) => h("button", { class: tab === id ? "on" : "", onclick: () => { location.hash = `#evals/${id}`; } }, label)));

  // ---------------------------------------------------------------- compare models
  let cmp = null, cmpBusy = false, cmpProgress = "", picked = null;
  const secs = (ms) => (ms == null ? "n/a" : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`);

  async function drawCompare() {
    if (!cmp) cmp = await api.compareReport();
    const providers = cmp.providers || {};
    if (!picked) picked = new Set(["scripted", ...Object.keys(providers).filter((n) => providers[n].ok && n !== "scripted").slice(0, 2)]);
    const extra = h("input", { placeholder: "more targets, e.g. local:qwen2.5:7b scripted+pattern=plan", style: "min-width:340px" });
    const r = cmp.report;
    const start = async () => {
      cmpBusy = true; cmpProgress = "Starting..."; drawCompare();
      try {
        const report = await api.runCompare([...picked, ...extra.value.trim().split(/\s+/).filter(Boolean)], (p) => {
          cmpProgress = `${p.target}: ${p.case} ${p.status} (${p.done}/${p.total})`;
          const line = root.querySelector(".progress"); if (line) line.textContent = cmpProgress;
        });
        cmp = { ...cmp, report }; cmpProgress = "";
      } catch (error) { cmpProgress = `The comparison failed: ${error.message}`; }
      cmpBusy = false; drawCompare();
    };
    put(root,
      h("div", { class: "page-head" },
        h("div", {}, h("h1", {}, "Compare models"),
          h("p", { class: "sub" }, "The same eight questions through each model, in fresh state every time. The checks are on behaviour (did the calculator run, was the fact stored, was the injected message not sent), so a model that words things differently still passes. Read four things off the table: what passes, how long it takes, what it costs, and where two models part ways. Click any cell to replay that run."))),
      api.static ? h("p", { class: "note" }, "This page is a recorded demo. Run `loopwright compare` or open the cockpit locally to compare the models you have.") :
        h("div", { class: "editor" }, h("h3", {}, "Pick what to compare"),
          h("div", { class: "row", style: "flex-wrap:wrap;gap:6px 16px" }, Object.entries(providers).map(([name, p]) => {
            const box = h("input", { type: "checkbox", style: "width:auto;margin:0 6px 0 0", disabled: !p.ok });
            box.checked = picked.has(name) && p.ok;
            box.addEventListener("change", () => { box.checked ? picked.add(name) : picked.delete(name); });
            return h("label", { class: "check", title: p.note }, box, h("span", {}, name + (p.ok ? "" : " (not set up)")));
          })),
          h("div", { class: "row" }, extra, h("button", { class: "primary", disabled: cmpBusy, onclick: start }, cmpBusy ? "Running..." : "Run the comparison")),
          h("p", { class: "hint" }, "A target is a provider, provider:model, or either with +setting=value to compare two configurations of one model. Real models take a minute or two.")),
      h("p", { class: "progress" }, cmpProgress),
      r ? [
        h("table", { class: "grid cmp" },
          h("thead", {}, h("tr", {}, h("th", {}, "Question"), r.targets.map((t) => h("th", {}, t.label, t.model && !t.label.includes(t.model) ? h("div", { class: "hint" }, t.model) : null)))),
          h("tbody", {},
            r.cases.map((c, i) => h("tr", {},
              h("td", {}, h("b", {}, c.title), h("div", { class: "hint" }, c.prompt)),
              r.targets.map((t) => {
                const row = t.rows[i];
                return h("td", { class: row.ms != null ? "clickable" : "", title: row.failed_checks?.join("\n") || row.note || "Replay this run",
                                 onclick: row.ms != null ? () => actions.replayCompare(t.slug, c.id, `${t.label}: ${c.title}`) : null },
                  h("span", { class: row.status === "pass" ? "pass" : "fail" }, row.status),
                  row.ms != null ? h("div", { class: "hint" }, `${secs(row.ms)} · ${fmt(row.tokens)} tok · ${row.tools.length} tool call(s)`) : h("div", { class: "hint" }, row.note || ""),
                  row.failed_checks?.length ? h("div", { class: "hint fail-why" }, row.failed_checks[0]) : null);
              }))),
            h("tr", { class: "total" }, h("td", {}, h("b", {}, "Passed")), r.targets.map((t) => h("td", {}, h("b", { class: t.passed === t.total ? "pass" : "fail" }, `${t.passed}/${t.total}`)))),
            h("tr", { class: "total" }, h("td", {}, "Median time per question"), r.targets.map((t) => h("td", {}, secs(t.median_ms)))),
            h("tr", { class: "total" }, h("td", {}, "Tokens, all eight"), r.targets.map((t) => h("td", {}, fmt(t.tokens)))),
            h("tr", { class: "total" }, h("td", {}, "Cost, all eight"), r.targets.map((t) => h("td", {}, t.cost_usd == null ? "n/a" : `$${t.cost_usd.toFixed(4)}`))))),
        h("p", { class: "note" }, `Generated ${r.generated}. Eight cases are a smoke test, not a verdict: for a real decision, save your own runs as cases (Your cases) and run several trials.`),
      ] : h("p", { class: "note" }, "No comparison yet."));
  }

  // ---------------------------------------------------------------- your own cases
  let mineResult = null;
  async function drawMine() {
    const { cases, feedback } = await api.mine();
    const votes = Object.values(feedback);
    const outcome = (id) => mineResult?.cases.find((c) => c.id === id);
    put(root,
      h("div", { class: "page-head" },
        h("div", {}, h("h1", {}, "Your cases"),
          h("p", { class: "sub" }, "The best eval cases are real turns. Under any reply in the cockpit, press Good or Bad to label it, or Save as eval case to turn that run into a regression test: the message, plus checks on what the run did (which tools ran, whether memory was searched, what was refused). They are stored in evals/mine.jsonl in your state folder, in the same format as the built-in suites, so you can tighten them by hand.")),
        api.static || !cases.length ? null : h("button", { class: "primary", onclick: async () => { mineResult = await api.runMine(actions.options()); drawMine(); } }, "Run my cases")),
      h("div", { class: "tiles" },
        h("div", { class: "tile" }, h("b", {}, cases.length), h("span", {}, "Saved cases")),
        h("div", { class: "tile good" }, h("b", {}, votes.filter((v) => v.rating === "up").length), h("span", {}, "Replies marked good")),
        h("div", { class: "tile bad" }, h("b", {}, votes.filter((v) => v.rating === "down").length), h("span", {}, "Replies marked bad")),
        mineResult ? h("div", { class: `tile ${mineResult.passed === mineResult.total ? "good" : "bad"}` }, h("b", {}, `${mineResult.passed}/${mineResult.total}`), h("span", {}, "Passed just now")) : null),
      cases.length ? h("table", { class: "grid", style: "margin-top:14px" },
        h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Message"), h("th", {}, "What it checks"), h("th", {}, ""))),
        h("tbody", {}, cases.map((c) => {
          const got = outcome(c.id);
          return h("tr", {}, h("td", { class: got ? (got.passed ? "pass" : "fail") : "hint" }, got ? (got.passed ? "pass" : "fail") : "not run"),
            h("td", {}, c.turns[0], got && !got.passed ? h("div", { class: "hint fail-why" }, got.trials[0].checks.filter((k) => !k.pass).map((k) => `${k.check}: ${k.note}`).join("; ").slice(0, 220)) : null),
            h("td", { class: "hint" }, c.expect.map((e) => Object.entries(e).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ")).join(" · ")),
            h("td", {}, h("button", { class: "linkish", onclick: async () => { await api.deleteCase(c.id); drawMine(); } }, "Remove")));
        }))) : h("p", { class: "note" }, api.static ? "The recorded demo has none. Install Loopwright to save your own." : "None yet. Ask something in the cockpit, then press Save as eval case under the reply."),
      h("p", { class: "note" }, "From the terminal: loopwright eval --mine. These cases are yours, so they are not part of the project's release gate."));
  }

  function heat(value, invert = false) {
    const good = invert ? 1 - value : value;
    const colour = good >= 0.999 ? "var(--ok)" : good >= 0.75 ? "var(--warn)" : "var(--blocked)";
    return h("td", { class: "heat", style: `color:${colour}` }, pct(value));
  }

  function draw() {
    const head = h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Evals"),
        h("p", { class: "sub" }, "Fixed cases, a fresh agent for each, graded on the trace as well as the answer. These run on the scripted model, so they measure the harness: whether the gate skipped, the injection was blocked, the retry fired. They say nothing about how well a particular LLM reasons.")),
      api.static ? null : h("button", { class: "primary", disabled: busy, onclick: run }, busy ? "Running..." : report ? "Run again" : "Run the evals"));
    if (!report) {
      put(root, head, h("p", { class: "progress" }, progress || "No report yet. Run the evals; it takes a few seconds and needs no key."));
      return;
    }
    const r = report, v = r.verdict;
    const suites = Object.keys(r.suites);
    put(root, head, h("p", { class: "progress" }, progress),
      h("div", { class: "tiles" },
        h("div", { class: `tile ${v.passed ? "good" : "bad"}` }, h("b", {}, v.passed ? "Pass" : "Fail"), h("span", {}, "Release gate")),
        h("div", { class: "tile" }, h("b", {}, `${r.cases_passed}/${r.cases_total}`), h("span", {}, "Cases passed")),
        h("div", { class: `tile ${r.attack_success_rate === 0 ? "good" : "bad"}` }, h("b", {}, pct(r.attack_success_rate)), h("span", {}, "Injection attacks that worked")),
        h("div", { class: "tile" }, h("b", {}, pct(r.retrieval.hybrid.recall_at_3)), h("span", {}, "Handbook recall@3")),
        h("div", { class: "tile" }, h("b", {}, pct(r.gate.accuracy)), h("span", {}, "Retrieval gate accuracy"))),

      h("h2", {}, "Ablations: switch one mechanism off and run everything again"),
      h("table", { class: "grid" },
        h("thead", {}, h("tr", {}, h("th", {}, "Variant"), ...suites.map((s) => h("th", { class: "n" }, s)),
          h("th", { class: "n" }, "Attacks that worked"), h("th", { class: "n" }, "Memory searches"), h("th", { class: "n" }, "Tokens in per turn"))),
        h("tbody", {}, Object.entries(r.ablations).map(([name, row]) => h("tr", {},
          h("td", {}, h("b", {}, name), h("div", { class: "hint" }, row.what)),
          ...suites.map((s) => heat(row[s])), heat(row.attack_success_rate, true),
          h("td", { class: "n" }, `${row.memory_searches} in ${row.turns} turns`), h("td", { class: "n" }, `~${fmt(row.avg_tokens_in_per_turn)}`))))),
      h("p", { class: "note" }, "Read across a row to see what a mechanism buys. With guardrails off, every injection attack succeeds: that is the proof the attacks are real and the defence is doing the work. The scripted model is deliberately gullible so this is a worst case."),

      h("div", { class: "two" },
        h("div", {}, h("h2", {}, "Handbook retrieval"),
          h("table", { class: "grid" }, h("thead", {}, h("tr", {}, h("th", {}, "Ranker"), h("th", { class: "n" }, "recall@1"), h("th", { class: "n" }, "recall@3"))),
            h("tbody", {}, ["bm25", "dense", "hybrid"].map((m) => h("tr", {}, h("td", {}, m), h("td", { class: "n" }, pct(r.retrieval[m].recall_at_1)), h("td", { class: "n" }, pct(r.retrieval[m].recall_at_3)))))),
          h("p", { class: "note" }, `${r.retrieval.questions} labelled questions. The dense ranker here is an offline hashed embedder, not a real embedding model.`)),
        h("div", {}, h("h2", {}, "Retrieval gate (rules)"),
          h("table", { class: "grid" }, h("tbody", {},
            h("tr", {}, h("td", {}, "Accuracy"), h("td", { class: "n" }, `${pct(r.gate.accuracy)} of ${r.gate.messages}`)),
            h("tr", {}, h("td", {}, "False skips (memory needed, not searched)"), h("td", { class: "n" }, r.gate.false_skips)),
            h("tr", {}, h("td", {}, "False retrieves"), h("td", { class: "n" }, r.gate.false_retrieves)))),
          h("p", { class: "note" }, r.gate.errors.length ? `Missed: ${r.gate.errors.map((e) => `"${e.message}"`).join(", ")}` : ""))),

      h("h2", {}, "Release gate"),
      h("table", { class: "grid" }, h("tbody", {}, v.checks.map((c) => h("tr", {},
        h("td", { class: c.pass ? "pass" : "fail" }, c.pass ? "pass" : "fail"), h("td", {}, c.check), h("td", { class: "n" }, `got ${c.got}`), h("td", { class: "n" }, `need ${c.need}`))))),

      h("h2", {}, "Every case"),
      h("p", { class: "note" }, "Click a case to replay its trace on the board."),
      h("table", { class: "grid" },
        h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Suite"), h("th", {}, "What it checks"), h("th", {}, "Prompt"), h("th", { class: "n" }, "Trials"))),
        h("tbody", {}, r.cases.map((c) => h("tr", { class: "clickable", onclick: () => actions.replayEval(c) },
          h("td", { class: c.passed ? "pass" : "fail" }, c.passed ? "pass" : "fail"), h("td", {}, c.suite), h("td", {}, c.title),
          h("td", { class: "hint" }, c.prompt), h("td", { class: "n" }, `${c.trials.filter((t) => t.passed).length}/${c.trials.length}`))))),
      h("p", { class: "note" }, r.judge.skipped ? `Model-graded cases: skipped. ${r.judge.why}` : `Model-graded cases: ${r.judge.passed}/${r.judge.cases} passed.`),
      h("p", { class: "note" }, `Generated ${r.generated} in ${r.seconds}s on ${r.model}.`));
  }

  async function run() {
    busy = true; progress = "Starting..."; draw();
    try {
      report = await api.runEvals((p) => {
        progress = p.stage === "full" ? `${p.passed ? "pass" : "FAIL"}  ${p.case}  ${p.title}` : p.stage === "ablation" ? `Ablation: ${p.variant}` : p.stage === "retrieval" ? "Retrieval and gate evals" : "";
        root.querySelector(".progress").textContent = progress;
      });
      progress = "";
    } catch (error) { progress = `The eval run failed: ${error.message}`; }
    busy = false; draw();
  }

  return { async show(arg) {
    tab = TABS.some(([id]) => id === arg) ? arg : "report";
    drawTabs();
    if (tab === "compare") return drawCompare();
    if (tab === "mine") return drawMine();
    if (!report) { report = await api.evalReport(); }
    draw();
  } };
}

// ============================================================ MEMORY
export function createMemory() {
  const root = document.getElementById("memory-body");
  const when = (t) => new Date(t * 1000).toLocaleString();
  let tab = "facts";
  const TABS = [["facts", "Facts"], ["episodes", "Episodes"], ["skills", "Skills"], ["identity", "Identity"], ["pending", "Consolidation"], ["world", "Calendar, notes, outbox"]];
  const table = (rows, empty) => (rows.length ? h("table", { class: "grid" }, h("tbody", {}, rows)) : h("p", { class: "note" }, empty));

  function skillEditor(s, show) {
    const name = h("input", { value: s.name, placeholder: "name, e.g. standup-update", disabled: !!s.name }), desc = h("input", { value: s.description, placeholder: "one line: when to use it" });
    const trig = h("input", { value: (s.triggers || []).join(", "), placeholder: "trigger words, comma separated" });
    const body = h("textarea", { rows: s.name ? 7 : 4, placeholder: "the steps, as you would brief a colleague" }, s.body || "");
    const msg = h("span", { class: "hint" });
    return h("div", { class: "editor" },
      h("div", { class: "row" }, h("h3", {}, s.name || "New skill"), s.name ? h("span", { class: "badge" }, s.yours ? "yours" : "built in") : null),
      s.name ? null : name, desc, trig, body,
      h("div", { class: "row" },
        api.static ? null : h("button", { class: "primary", onclick: async () => {
          const r = await api.saveSkill({ name: name.value, description: desc.value, triggers: trig.value.split(",").map((x) => x.trim()).filter(Boolean), body: body.value }).catch((e) => ({ error: e.message }));
          if (r.error) msg.textContent = "A skill needs a name, a description and instructions."; else show();
        } }, s.name && !s.yours ? "Save as my version" : "Save"),
        s.yours && !api.static ? h("button", { onclick: async () => { await api.deleteSkill(s.name); show(); } }, "Delete") : null, msg));
  }

  async function show() {
    const m = await api.memory();
    const live = m.facts.filter((f) => !f.superseded_by);
    let pane;
    if (tab === "facts") {
      pane = [m.facts.length ? h("table", { class: "grid" },
        h("thead", {}, h("tr", {}, h("th", {}, "Fact"), h("th", {}, "About"), h("th", {}, "Written by"), h("th", { class: "n" }, "Times used"), h("th", {}, ""))),
        h("tbody", {}, m.facts.map((f) => h("tr", {},
          h("td", { class: f.superseded_by ? "struck" : "" }, f.text, f.superseded_by ? h("div", { class: "hint" }, `superseded by fact ${f.superseded_by}`) : null),
          h("td", {}, f.subject + (f.attr ? `.${f.attr}` : "")), h("td", { class: "hint" }, f.source.split(":")[0] || "setup"), h("td", { class: "n" }, f.uses),
          h("td", {}, api.static ? null : h("button", { class: "linkish", onclick: async () => { await api.deleteFact(f.id); show(); } }, "Forget")))))) :
        h("p", { class: "note" }, "No facts yet. Tell it something: \"Remember that Alex prefers morning meetings\"."),
        h("p", { class: "note" }, "Semantic memory: durable statements. A changed fact is never overwritten: the new value gets its own row and the old one is struck through, so you can see what the agent used to believe.")];
    } else if (tab === "episodes") {
      pane = [table(m.episodes.map((e) => h("tr", {}, h("td", { class: "n" }, e.day), h("td", {}, e.summary))), "No episodes yet. One is written each time consolidation runs."),
        h("p", { class: "note" }, "Episodic memory: what happened and when, as dated summaries. Facts say what is true; episodes say what took place.")];
    } else if (tab === "skills") {
      pane = [h("p", { class: "note", style: "margin-bottom:12px" }, "Procedural memory: how to do things. Each skill's one-line description is always in the prompt; its instructions load only when a trigger appears in the message. Edit one, write a new one here, or teach the agent in chat: \"When I ask for a standup update, always give three bullets\"."),
        skillEditor({ name: "", description: "", triggers: [], body: "" }, show), m.skills.map((s) => skillEditor(s, show))];
    } else if (tab === "identity") {
      const area = h("textarea", { rows: 14 }, m.identity);
      pane = [h("div", { class: "editor" }, h("h3", {}, "The identity prompt"),
        h("p", { class: "hint" }, "The first section of every prompt: who the agent is and its standing rules. Instructions here shape behaviour; they do not enforce it. Hard limits live in the tool gate, in code."),
        area, h("div", { class: "row" },
          api.static ? null : h("button", { class: "primary", onclick: async () => { await api.saveIdentity(area.value); show(); } }, "Save"),
          m.identity_is_custom && !api.static ? h("button", { onclick: async () => { await api.saveIdentity(""); show(); } }, "Restore the built-in text") : null,
          h("span", { class: "badge" }, m.identity_is_custom ? "yours" : "built in")))];
    } else if (tab === "pending") {
      pane = [h("p", { class: "note", style: "margin-bottom:10px" }, `${m.pending.length} message(s) are waiting for the next background memory write. Consolidation reads them in a batch, proposes facts, and resolves each against the Facts tab as add, update or skip.`),
        table(m.pending.map((p) => h("tr", {}, h("td", { class: "hint" }, p.role), h("td", {}, p.content.slice(0, 220)))), "Nothing is waiting. Every message so far has been consolidated.")];
    } else {
      pane = [h("h2", {}, "Calendar"), table(m.events.map((e) => h("tr", {}, h("td", { class: "n" }, `${e.day} ${e.time}`), h("td", {}, e.title))), "Empty."),
        h("h2", {}, "Notes"), table(m.notes.map((n) => h("tr", {}, h("td", {}, n.text), h("td", { class: "hint" }, when(n.created)))), "Empty."),
        h("h2", {}, "Outbox"), table(m.outbox.map((o) => h("tr", {}, h("td", {}, o.recipient), h("td", {}, o.body))), "Empty. Messages the agent \"sends\" land here and go nowhere else; the tool exists so the approval flow has something real to guard.")];
    }
    put(root, spaceNote(),
      h("div", { class: "page-head" },
        h("div", {}, h("h1", {}, "Memory"),
          h("p", { class: "sub" }, `Everything the agent keeps between turns, read straight from one SQLite file: ${m.db}. Nothing here is hidden from you, and you can change or delete any of it.`)),
        api.static ? null : h("button", { onclick: async () => { if (confirm("Delete all facts, episodes, messages, notes and calendar events?")) { await api.resetMemory(); show(); } } }, "Wipe everything")),
      h("div", { class: "tiles" },
        h("div", { class: "tile" }, h("b", {}, live.length), h("span", {}, "Facts (semantic)")),
        h("div", { class: "tile" }, h("b", {}, m.episodes.length), h("span", {}, "Episodes (episodic)")),
        h("div", { class: "tile" }, h("b", {}, m.skills.length), h("span", {}, "Skills (procedural)")),
        h("div", { class: "tile" }, h("b", {}, m.counts.messages), h("span", {}, "Messages (working)"))),
      h("div", { class: "subtabs" }, TABS.map(([id, label]) => h("button", { class: tab === id ? "on" : "", onclick: () => { tab = id; show(); } }, label))),
      pane);
  }
  return { show };
}

// ============================================================ TOOLS
export function createTools() {
  const root = document.getElementById("tools-body");
  async function show() {
    const t = await api.tools();
    const name = h("input", { placeholder: "name, e.g. files" }), command = h("input", { placeholder: "command and arguments, e.g. npx -y @modelcontextprotocol/server-filesystem /tmp" });
    const trust = h("select", {}, h("option", { value: "ask" }, "ask before each call"), h("option", { value: "read" }, "read-only: no approval"));
    const msg = h("span", { class: "hint" });
    const effect = { read: "reads", write: "changes local state", external: "outside effects: needs approval" };
    put(root, spaceNote(),
      h("h1", {}, "Tools"),
      h("p", { class: "sub" }, "Everything the model can ask for. It sees each tool's name, description and argument schema, and nothing else. Every definition here is sent on every model call, so switching off what you do not use saves tokens as well as risk."),
      h("table", { class: "grid" },
        h("thead", {}, h("tr", {}, h("th", {}, "Tool"), h("th", {}, "What the model is told"), h("th", {}, "Reach"), h("th", {}, "From"), h("th", {}, ""))),
        h("tbody", {}, t.tools.map((x) => h("tr", {},
          h("td", {}, h("b", { class: x.enabled ? "" : "struck" }, x.name)),
          h("td", { class: "hint" }, x.description),
          h("td", {}, effect[x.effect], x.untrusted ? h("div", { class: "hint" }, "returns untrusted content") : null, x.private ? h("div", { class: "hint" }, "reads private data") : null,
            x.opt_in ? h("div", { class: "hint" }, "ships switched off") : null),
          h("td", { class: "hint" }, x.origin),
          h("td", {}, api.static ? null : h("button", { class: "linkish", onclick: async () => { await api.toggleTool(x.name, !x.enabled); show(); } }, x.enabled ? "Switch off" : "Switch on")))))),

      h("h2", {}, "MCP servers"),
      h("p", { class: "note", style: "margin-bottom:10px" }, "Borrow tools from any Model Context Protocol server. You did not write that server, so its results are fenced as untrusted and its tools need approval unless you mark it read-only."),
      Object.keys(t.mcp_config).length ? h("table", { class: "grid" }, h("tbody", {}, Object.entries(t.mcp_config).map(([n, spec]) => {
        const status = t.mcp.find((m) => m.server === n);
        return h("tr", {}, h("td", {}, h("b", {}, n)), h("td", { class: "hint" }, `${spec.command} ${(spec.args || []).join(" ")}`),
          h("td", { class: status?.ok ? "pass" : "fail" }, status?.ok ? `${status.tools.length} tool(s)` : (status?.error || "not started")),
          h("td", {}, api.static ? null : h("button", { class: "linkish", onclick: async () => { await api.removeMcp(n); show(); } }, "Remove")));
      }))) : null,
      api.static ? null : h("div", { class: "editor", style: "margin-top:10px" }, h("h3", {}, "Connect a server"), name, command, h("div", { class: "row" }, trust,
        h("button", { class: "primary", onclick: async () => {
          const parts = command.value.trim().split(/\s+/);
          const r = await api.addMcp({ name: name.value, command: parts[0] || "", args: parts.slice(1), trust: trust.value }).catch((e) => ({ error: e.message }));
          if (r.error) msg.textContent = "Give it a name and a command."; else show();
        } }, "Connect"), msg)),

      h("h2", {}, "Your own tools"),
      h("p", { class: "note" }, `Drop a Python file with a TOOLS list into ${t.tools_folder} and restart. The tool gate treats it like any built-in tool. See docs/tools.md for a ten-line example.`),
      t.plugins.length ? h("table", { class: "grid", style: "margin-top:8px" }, h("tbody", {}, t.plugins.map((p) => h("tr", {}, h("td", {}, p.file),
        h("td", { class: p.ok ? "pass" : "fail" }, p.ok ? p.tools.join(", ") : p.error))))) : null,
      h("p", { class: "note" }, "A third way needs no code: teach a skill in chat or in Memory, Skills."));
  }
  return { show };
}

// ============================================================ ACTIVITY
export function createActivity(actions) {
  const root = document.getElementById("runs-body");
  async function show() {
    const [o, runs] = await Promise.all([api.overview(), api.runs()]);
    const gateTotal = o.gate.retrieve + o.gate.skip || 1;
    put(root, spaceNote(),
      h("h1", {}, "Activity"),
      h("p", { class: "sub" }, "All-time numbers, computed from the trace files. Every turn writes one; pick any to replay it on the board exactly as it happened. A replay shows the record and does not call the model again."),
      h("div", { class: "tiles" },
        h("div", { class: "tile" }, h("b", {}, o.turns), h("span", {}, "Turns")),
        h("div", { class: "tile" }, h("b", {}, o.avg_ms > 1500 ? `${(o.avg_ms / 1000).toFixed(1)} s` : `${o.avg_ms} ms`), h("span", {}, "Average turn")),
        h("div", { class: "tile" }, h("b", {}, o.tool_calls), h("span", {}, "Tool calls")),
        h("div", { class: "tile" }, h("b", {}, fmt(o.tokens)), h("span", {}, "Tokens")),
        h("div", { class: "tile" }, h("b", {}, o.cost_usd == null ? "n/a" : `$${o.cost_usd.toFixed(2)}`), h("span", {}, o.cost_usd == null ? "Spend (no price set)" : "Spend")),
        h("div", { class: "tile" }, h("b", {}, o.counts.facts_live), h("span", {}, "Facts stored"))),
      h("div", { class: "two" },
        h("div", {}, h("h2", {}, "The retrieval gate"),
          h("div", { class: "gatebar" },
            h("i", { style: `width:${(100 * o.gate.retrieve) / gateTotal}%;background:var(--memory)` }, o.gate.retrieve ? `${o.gate.retrieve} searched` : ""),
            h("i", { style: `width:${(100 * o.gate.skip) / gateTotal}%;background:var(--ink-3)` }, o.gate.skip ? `${o.gate.skip} skipped` : "")),
          h("p", { class: "note" }, `Memory was left alone on ${Math.round((100 * o.gate.skip) / gateTotal)}% of turns. Each skip is a search not run and unrelated memories kept out of the prompt.`)),
        h("div", {}, h("h2", {}, "Tools used"),
          o.tools.length ? h("table", { class: "grid" }, h("thead", {}, h("tr", {}, h("th", {}, "Tool"), h("th", { class: "n" }, "Ran"), h("th", { class: "n" }, "Failed"), h("th", { class: "n" }, "Refused"))),
            h("tbody", {}, o.tools.map((x) => h("tr", {}, h("td", {}, x.name), h("td", { class: "n" }, x.ran), h("td", { class: "n" }, x.failed || ""), h("td", { class: "n" }, x.refused || ""))))) :
            h("p", { class: "note" }, "No tool has run yet."))),
      h("h2", {}, "Every run"),
      runs.length ? h("table", { class: "grid" },
        h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "Message"), h("th", {}, "Model"), h("th", { class: "n" }, "Steps"), h("th", { class: "n" }, "Tokens"), h("th", {}, ""))),
        h("tbody", {}, runs.map((r) => h("tr", { class: "clickable", onclick: () => actions.replayRun(r.turn_id) },
          h("td", { class: "hint" }, r.started.replace("T", " ")), h("td", {}, r.message), h("td", { class: "hint" }, r.model),
          h("td", { class: "n" }, r.steps), h("td", { class: "n" }, fmt(r.tokens)), h("td", { class: r.ok ? "pass" : "fail" }, r.ok ? "" : "failed"))))) :
        h("p", { class: "note" }, "No runs yet. Send a message in the cockpit."));
  }
  return { show };
}

// ============================================================ AUTOMATIONS
export function createAutomations(actions) {
  const root = document.getElementById("automations-body");
  let tab = "tasks", note = "";
  const TABS = [["tasks", "Scheduled tasks"], ["workflows", "Workflows"], ["channels", "Chat apps"]];

  async function show(arg) {
    if (TABS.some(([id]) => id === arg)) tab = arg;
    const a = await api.automations();
    let pane;
    if (tab === "tasks") {
      const schedule = h("input", { placeholder: "when: weekdays 08:00 · every 30m · weekly mon 09:00 · once 2026-11-20 14:00 · 0 8 * * 1-5" });
      const prompt = h("textarea", { rows: 2, placeholder: "what to do each time, e.g. Tell me what is on my calendar today and list my recent notes" });
      const msg = h("span", { class: "hint" }, note);
      pane = [
        h("p", { class: "note", style: "margin-bottom:12px" }, "A scheduled task is a saved prompt that a clock sends instead of you. It runs with nobody watching, so anything that would need your approval is refused: a task can read, think, take notes and report, and cannot send messages, browse or run commands. Tasks run while this cockpit or a chat gateway is running. You can also ask in chat: \"Every weekday at 8am, tell me what's on my calendar\" (you will be asked to approve it)."),
        a.tasks.length ? h("table", { class: "grid" },
          h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Prompt"), h("th", {}, "Runs"), h("th", {}, "Next"), h("th", {}, "Last result"), h("th", {}, ""))),
          h("tbody", {}, a.tasks.map((t) => h("tr", {},
            h("td", { class: "n" }, t.id),
            h("td", {}, h("b", { class: t.enabled ? "" : "struck" }, t.prompt), t.origin && !t.origin.startsWith("web") && !t.origin.startsWith("local") ? h("div", { class: "hint" }, `results go to ${t.origin.replace(":", " chat ")}`) : null),
            h("td", {}, t.runs_text, h("div", { class: "hint" }, `${t.runs} run(s) so far`)),
            h("td", { class: "hint" }, t.enabled ? t.next || "never" : "paused"),
            h("td", { class: "hint" }, t.last_reply ? [t.last, ": ", t.last_reply.slice(0, 140),
              t.last_turn && !api.static ? [" ", h("button", { class: "linkish", onclick: () => { actions.replayRun(t.last_turn); location.hash = "#cockpit"; } }, "Replay")] : null] : "has not run yet"),
            h("td", {}, api.static ? null : h("div", { class: "row-actions" },
              h("button", { class: "linkish", onclick: () => actions.tryPrompt(`/task ${t.id}`) }, "Run now"),
              h("button", { class: "linkish", onclick: async () => { await api.toggleTask(t.id, !t.enabled); show(); } }, t.enabled ? "Pause" : "Resume"),
              h("button", { class: "linkish", onclick: async () => { await api.deleteTask(t.id); show(); } }, "Remove"))))))) :
          h("p", { class: "note" }, "No scheduled tasks yet."),
        api.static ? null : h("div", { class: "editor", style: "margin-top:14px" }, h("h3", {}, "Add a task"), schedule, prompt,
          h("div", { class: "row" }, h("button", { class: "primary", onclick: async () => {
            const r = await api.addTask(prompt.value, schedule.value).catch(() => ({ error: true }));
            note = r.error ? "I could not read that schedule. Use one of the forms shown in the box." : "";
            show();
          } }, "Schedule it"), msg)),
        h("p", { class: "note" }, h("button", { class: "linkish", onclick: () => actions.openConcept("scheduled-agents") }, "Read: scheduled and proactive agents"), "  ",
          h("button", { class: "linkish", onclick: () => actions.openConcept("standing-instructions") }, "why creating one needs approval"))];
    } else if (tab === "workflows") {
      pane = [
        h("p", { class: "note", style: "margin-bottom:12px" }, "A workflow is a path fixed in advance: the code decides what happens next, and the model is called at set points to do one job each. Steps in the same column run at the same time. Run one here, type /workflow <name> in any chat, or put it on a schedule. To write your own, copy a file from loopwright/flows/ into " + a.workflows_folder + "."),
        a.workflows.map((w) => {
          const input = h("input", { placeholder: w.input || "no input needed" });
          return h("div", { class: "editor" },
            h("div", { class: "row" }, h("h3", {}, w.name), h("span", { class: "badge" }, w.source)),
            h("p", { class: "hint" }, w.description), workflowGraph(w),
            h("div", { class: "row", style: "margin-top:8px" }, w.input ? input : null,
              h("button", { class: "primary", onclick: () => actions.tryPrompt(`/workflow ${w.name}${input.value.trim() ? `: ${input.value.trim()}` : ""}`) }, "Run and watch")));
        }),
        h("p", { class: "note" }, h("button", { class: "linkish", onclick: () => actions.openConcept("workflow-vs-agent") }, "Read: workflow versus agent"))];
    } else {
      pane = [
        h("p", { class: "note", style: "margin-bottom:12px" }, "Talk to the same agent from a chat app. Each gateway is a small adapter that starts ordinary turns: same memory, same tools, same guardrails. Only the people on its allowlist are answered; approvals are asked in words (reply yes); /steps shows what the agent did. Tokens are read from environment variables or a .env file and are never stored by Loopwright."),
        h("table", { class: "grid" },
          h("thead", {}, h("tr", {}, h("th", {}, "App"), h("th", {}, "How it connects"), h("th", {}, "Status"), h("th", {}, "Start it"))),
          h("tbody", {}, a.channels.map((c) => h("tr", {},
            h("td", {}, h("b", {}, { whatsapp: "WhatsApp" }[c.name] || c.name[0].toUpperCase() + c.name.slice(1))), h("td", { class: "hint" }, c.how),
            h("td", { class: c.configured && c.allowed ? "pass" : "hint" }, c.configured ? (c.allowed ? "ready" : `token set; add ${c.allow_var}`) : `set ${c.token_var}`),
            h("td", {}, h("code", {}, c.command)))))),
        h("p", { class: "note" }, "Step-by-step setup for each app is in docs/gateways.md. ",
          h("button", { class: "linkish", onclick: () => actions.openConcept("messaging-gateways") }, "Read: messaging gateways"))];
    }
    put(root,
      h("h1", {}, "Automations"),
      h("p", { class: "sub" }, "Three ways the agent works without you typing into this page: on a clock, along a fixed path, and from the chat apps you already use."),
      h("div", { class: "subtabs" }, TABS.map(([id, label]) => h("button", { class: tab === id ? "on" : "", onclick: () => { tab = id; show(); } }, label))),
      pane);
  }
  return { show };
}
