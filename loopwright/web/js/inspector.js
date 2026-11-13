// THE INSPECTOR: everything about the step under the playhead.
//
// For any step it answers three questions, in this order:
//   what happened      the event's own plain-English account
//   why it exists      the handbook card for this kind of step
//   what exactly       the data: the prompt, the arguments, the scores

import { h, put, pretty, fmt, pillarVar } from "./util.js";

const SECTION_COLOURS = ["var(--loop)", "var(--harness)", "var(--memory)", "var(--ops)", "var(--safety)", "var(--gateway)",
                         "color-mix(in srgb, var(--loop) 55%, var(--panel))", "color-mix(in srgb, var(--memory) 55%, var(--panel))"];

function checklist(checks) {
  return h("ul", { class: "checks" }, checks.map((c) =>
    h("li", { class: c.pass ? "" : "no" }, h("span", {}, c.check, c.note ? h("small", {}, `  ${c.note}`) : null))));
}

function hitsTable(hits) {
  if (!hits.length) return h("p", { class: "hint" }, "Nothing cleared the relevance floor, so nothing was added to the prompt.");
  return h("table", { class: "hits" },
    h("thead", {}, h("tr", {}, h("th", {}, "Result"), h("th", {}, "BM25 rank"), h("th", {}, "Vector rank"), h("th", {}, "Fused"))),
    h("tbody", {}, hits.map((x) => h("tr", {},
      h("td", {}, x.term || x.text || x.id),
      h("td", { class: "n" }, x.bm25_rank ?? "none"),
      h("td", { class: "n" }, x.dense_rank ?? "none"),
      h("td", { class: "n" }, x.score)))));
}

function budget(data) {
  const parts = [...data.sections.map((s) => ({ name: s.name, tokens: s.tokens, note: s.note, text: s.text })),
                 { name: "conversation", tokens: data.history_tokens, note: "this message and earlier ones, sent verbatim" },
                 { name: `tool definitions (${data.tool_count})`, tokens: data.tools_tokens, note: "every tool's schema, on every call" }];
  const total = Math.max(1, data.total);
  return [
    h("div", { class: "budget", title: `${fmt(data.total)} of ${fmt(data.budget)} tokens` },
      parts.map((p, i) => h("i", { style: `width:${(100 * p.tokens) / Math.max(total, data.budget)}%;background:${SECTION_COLOURS[i % SECTION_COLOURS.length]}` }))),
    h("div", { class: "budget-list" }, parts.map((p, i) => h("details", {},
      h("summary", {}, h("i", { style: `background:${SECTION_COLOURS[i % SECTION_COLOURS.length]}` }), h("b", {}, p.name), h("span", {}, `~${fmt(p.tokens)} tokens`)),
      h("p", { class: "hint" }, p.note), p.text ? h("pre", { class: "data" }, p.text) : null))),
  ];
}

function modelCall(data) {
  const out = [h("dl", { class: "kv" },
    h("dt", {}, "Model"), h("dd", {}, `${data.provider}: ${data.model}`),
    h("dt", {}, "Purpose"), h("dd", {}, data.purpose),
    h("dt", {}, "Tokens"), h("dd", {}, `${data.estimated ? "~" : ""}${fmt(data.tokens_in)} in, ${fmt(data.tokens_out)} out` + (data.cached_in ? `, ${fmt(data.cached_in)} from cache` : "")),
    h("dt", {}, "Stopped"), h("dd", {}, data.stop_reason))];
  if (data.thought) out.push(h("h3", {}, "Its stated reason"), h("p", {}, data.thought));
  if (data.tool_calls && data.tool_calls.length) {
    out.push(h("h3", {}, "Tool calls it asked for"), h("pre", { class: "data" }, data.tool_calls.map((c) => `${c.name}(${JSON.stringify(c.arguments)})`).join("\n")));
  }
  if (data.text) out.push(h("h3", {}, "Text it returned"), h("pre", { class: "data" }, data.text));
  if (data.prompt) {
    out.push(h("details", { class: "raw" }, h("summary", {}, `The exact prompt it was sent (${data.prompt.message_count} message(s), ${data.prompt.tools.length} tool(s))`),
      h("h3", {}, "System prompt"), h("pre", { class: "data" }, data.prompt.system),
      h("h3", {}, "Messages"), h("pre", { class: "data" }, pretty(data.prompt.messages))));
  }
  return out;
}

function body(event) {
  const d = event.data || {};
  if (event.kind === "context.build") return [h("h3", {}, `Where the ${fmt(d.total)} tokens went (budget ${fmt(d.budget)})`), ...budget(d)];
  if (event.kind === "model.call") return modelCall(d);
  const out = [];
  if (d.message && event.kind === "gateway.receive") out.push(h("h3", {}, "Message"), h("pre", { class: "data" }, d.message));
  if (d.reply) out.push(h("h3", {}, "Reply"), h("pre", { class: "data" }, d.reply));
  if (d.plan) out.push(h("h3", {}, "The plan"), h("pre", { class: "data" }, d.plan));
  if (d.source && d.tainted) out.push(h("h3", {}, "Source"), h("pre", { class: "data" }, d.source));
  if (d.query) out.push(h("h3", {}, "Query"), h("pre", { class: "data" }, d.query));
  if (Array.isArray(d.hits)) out.push(h("h3", {}, "What the two rankers returned"), hitsTable(d.hits));
  if (Array.isArray(d.checks)) out.push(h("h3", {}, "Checks"), checklist(d.checks));
  if (d.tool && (d.args !== undefined || d.output || d.error)) {
    out.push(h("h3", {}, "Tool call"), h("pre", { class: "data" }, `${d.tool}(${JSON.stringify(d.args ?? {}, null, 1)})`));
    if (d.output) out.push(h("h3", {}, "What it returned"), h("pre", { class: "data" }, d.output));
    if (d.error) out.push(h("h3", {}, "Error"), h("pre", { class: "data" }, d.error));
  }
  if (Array.isArray(d.findings) && d.findings.length) {
    out.push(h("h3", {}, "Findings"), h("ul", { class: "checks" }, d.findings.map((f) => h("li", { class: "no" }, h("span", {}, f.label, h("small", {}, `  ${f.excerpt}`))))));
  }
  if (d.action) out.push(h("dl", { class: "kv" }, h("dt", {}, "Decision"), h("dd", {}, d.action), h("dt", {}, "Fact"), h("dd", {}, d.text),
    d.old_text ? [h("dt", {}, "Replaces"), h("dd", {}, d.old_text)] : null));
  if (Array.isArray(d.candidates)) out.push(h("h3", {}, "Candidates"), h("pre", { class: "data" }, d.candidates.map((c) => `${c.subject}${c.attr ? "." + c.attr : ""}: ${c.text}`).join("\n") || "(none)"));
  if (Array.isArray(d.tasks)) out.push(h("h3", {}, "Tasks handed to workers"), h("pre", { class: "data" }, d.tasks.join("\n")));
  if (Array.isArray(d.results) && d.results[0] && d.results[0].summary) {
    out.push(h("h3", {}, `What came back (${fmt(d.tokens_spent)} tokens spent, ${fmt(d.tokens_returned)} returned)`),
      h("pre", { class: "data" }, d.results.map((r) => r.summary).join("\n\n")));
  }
  if (d.summary && typeof d.summary === "string") out.push(h("h3", {}, "Summary"), h("pre", { class: "data" }, d.summary));
  if (event.kind === "cost.receipt") {
    out.push(h("dl", { class: "kv" },
      h("dt", {}, "Model calls"), h("dd", {}, Object.entries(d.by_purpose).map(([k, v]) => `${k} x${v.calls}`).join(", ")),
      h("dt", {}, "Tokens"), h("dd", {}, `${d.estimated ? "~" : ""}${fmt(d.tokens_in)} in, ${fmt(d.tokens_out)} out`),
      h("dt", {}, "Cost"), h("dd", {}, d.cost_usd == null ? "n/a (no price set for this model)" : `$${d.cost_usd.toFixed(4)}`),
      h("dt", {}, "Tools"), h("dd", {}, d.tools.join(", ") || "none"),
      h("dt", {}, "Memory"), h("dd", {}, `${d.memory_used} used, ${d.memory_written} written`),
      h("dt", {}, "Time"), h("dd", {}, `${fmt(d.duration_ms)} ms`)));
  }
  out.push(h("details", { class: "raw" }, h("summary", {}, "Raw event"), h("pre", { class: "data" }, pretty({ ...event, data: d }))));
  return out;
}

export function renderInspector(container, state, book, actions) {
  const event = state.current;
  if (!event) {
    put(container, 
      h("h2", {}, state.events.length ? "Press play" : "Nothing loaded yet"),
      h("p", { class: "hint" }, state.events.length
        ? "The run is loaded. Play it, or step through one event at a time."
        : "Send a message. Each step the agent takes will light up a box on the board and appear here with its data and the reason it exists."),
      h("h3", {}, "Keys"),
      h("p", { class: "hint", html: "<kbd>Space</kbd> play or pause &nbsp; <kbd>Left</kbd> <kbd>Right</kbd> one step &nbsp; <kbd>Home</kbd> <kbd>End</kbd> first and last" }),
      h("h3", {}, "Tip"),
      h("p", { class: "hint" }, "Click any box on the board to read what that part of an agent is for."));
    return;
  }
  const stage = book.stages[event.stage] || { label: event.stage, pillar: "gateway" };
  const concept = book.byId[event.concept] || book.byId[book.kinds[event.kind]];
  const colour = pillarVar(stage.pillar);
  put(container, 
    h("div", { class: "chips" },
      h("span", { class: "chip stage", style: `--c:${colour}` }, stage.label),
      h("span", { class: `chip ${event.status}` }, event.status),
      event.span !== "main" ? h("span", { class: "chip" }, `worker ${event.span.split(":")[1]}`) : null,
      event.dur_ms != null ? h("span", { class: "chip" }, `${event.dur_ms} ms`) : null,
      h("span", { class: "chip" }, event.kind)),
    h("h2", {}, event.title),
    h("p", {}, event.detail),
    concept ? h("div", { class: "concept", style: `--c:${colour}` },
      h("strong", {}, concept.term),
      h("p", {}, concept.one_liner),
      h("p", {}, h("b", {}, "Why it exists. "), concept.why),
      h("div", { class: "links" },
        h("button", { class: "linkish", onclick: () => actions.openConcept(concept.id) }, "Read the full card"),
        h("button", { class: "linkish", onclick: () => actions.openSource(concept.file) }, concept.file))) : null,
    ...body(event));
  container.scrollTop = 0;
}
