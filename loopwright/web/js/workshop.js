// The workshop: exercises where the learner writes the code, in the Learn tab.
//
// The exercises themselves (brief, starter, hints, solution) come with the handbook, so they can
// be read anywhere, including the recorded demo. Checking an answer runs it, which needs a
// computer: /api/workshop on a live or connected server.

import { api } from "./api.js";
import { h, put } from "./util.js";

// A brief is plain text: paragraphs, lists (lines starting "- " or "1. "), and indented blocks of code.
function prose(text) {
  const out = [];
  for (const block of String(text).trim().split(/\n\s*\n/)) {
    const lines = block.split("\n");
    if (lines.every((line) => /^\s{4}/.test(line))) out.push(h("pre", { class: "sample" }, lines.map((line) => line.slice(4)).join("\n")));
    else if (lines.every((line) => /^\s*-\s/.test(line))) out.push(h("ul", {}, lines.map((line) => h("li", {}, inline(line.replace(/^\s*-\s/, ""))))));
    else if (lines.every((line) => /^\s*\d+\.\s/.test(line))) out.push(h("ol", {}, lines.map((line) => h("li", {}, inline(line.replace(/^\s*\d+\.\s/, ""))))));
    else out.push(h("p", {}, inline(lines.join(" "))));
  }
  return out;
}

// `code` in backticks becomes a code element; everything else stays text.
function inline(text) {
  return String(text).split(/(`[^`]+`)/).map((part) => (part.startsWith("`") && part.endsWith("`") && part.length > 2 ? h("code", {}, part.slice(1, -1)) : part));
}

export function createWorkshop(book, actions) {
  const exercises = book.exercises || [];
  const levels = book.levels || {};
  let selected = exercises[0] ? exercises[0].id : null;
  let live = null;                       // { id: {code, passed, attempts, path} } once a server has answered
  let loading = null;
  const drafts = {};                     // what is in the editor right now, per exercise
  const results = {};                    // the last check result, per exercise
  const hintsShown = {};
  const solutionShown = {};
  let redraw = () => {};

  async function load() {
    if (live || api.static || loading) return loading;
    loading = (async () => {
      try {
        const data = await api.workshop();
        live = Object.fromEntries(data.exercises.map((x) => [x.id, x]));
        live.folder = data.folder;
      } catch { live = null; }
      loading = null;
      redraw();
    })();
    return loading;
  }

  const state = (id) => (live && live[id]) || {};
  const current = (x) => (drafts[x.id] !== undefined ? drafts[x.id] : state(x.id).code !== undefined ? state(x.id).code : x.starter);
  const passedCount = () => exercises.filter((x) => state(x.id).passed).length;

  function drawList(list, onPick) {
    load();
    const done = passedCount();
    list.append(h("p", { class: "hint", style: "padding:10px 8px 2px" },
      api.static ? `${exercises.length} exercises. Read them here; checking your code needs a computer to run it on.`
                 : `${done} of ${exercises.length} done. You write a small function; the checker tells you what works.`));
    for (const [level, label] of Object.entries(levels)) {
      const rows = exercises.filter((x) => String(x.level) === String(level));
      if (!rows.length) continue;
      list.append(h("h3", {}, label));
      for (const x of rows) {
        list.append(h("button", { class: x.id === selected ? "on" : "", onclick: () => { keep(); selected = x.id; onPick(); } },
          h("span", { class: `tick ${state(x.id).passed ? "done" : ""}`, "aria-hidden": "true" }, state(x.id).passed ? "✓" : ""),
          x.title, h("small", {}, `${state(x.id).passed ? "Passed" : `About ${x.minutes} minutes`}`)));
      }
    }
  }

  // Save what is in the editor before leaving it, quietly.
  function keep() {
    const x = exercises.find((e) => e.id === selected);
    if (x && live && drafts[x.id] !== undefined && drafts[x.id] !== state(x.id).code) {
      state(x.id).code = drafts[x.id];
      api.exerciseSave(x.id, drafts[x.id])?.catch(() => {});
    }
  }

  function editor(x) {
    const area = h("textarea", { class: "code-editor", spellcheck: "false", autocapitalize: "off", autocomplete: "off", autocorrect: "off",
      "aria-label": `Your code for: ${x.title}`, rows: String(Math.min(34, Math.max(12, current(x).split("\n").length + 2))) });
    area.value = current(x);
    area.addEventListener("input", () => { drafts[x.id] = area.value; });
    area.addEventListener("blur", keep);
    area.addEventListener("keydown", (e) => {
      const { selectionStart: start, selectionEnd: end, value } = area;
      if (e.key === "Tab" && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey) {      // Tab indents; Escape then Tab leaves the editor
        if (area.dataset.escape) { delete area.dataset.escape; return; }
        e.preventDefault();
        area.setRangeText("    ", start, end, "end");
        drafts[x.id] = area.value;
      } else if (e.key === "Escape") {
        area.dataset.escape = "1";
      } else if (e.key === "Enter" && !e.shiftKey && (e.metaKey || e.ctrlKey)) {         // Cmd/Ctrl+Enter checks
        e.preventDefault();
        run(x);
      } else if (e.key === "Enter") {                                                     // keep the indentation of the line above
        const line = value.slice(value.lastIndexOf("\n", start - 1) + 1, start);
        const indent = line.match(/^\s*/)[0] + (/:\s*$/.test(line) ? "    " : "");
        e.preventDefault();
        area.setRangeText(`\n${indent}`, start, end, "end");
        drafts[x.id] = area.value;
      }
    });
    return area;
  }

  async function run(x) {
    if (api.static) return;
    results[x.id] = { running: true };
    redraw();
    try {
      const result = await api.exerciseCheck(x.id, current(x));
      results[x.id] = result;
      if (live && live[x.id]) Object.assign(live[x.id], { code: current(x), attempts: result.attempts, passed: live[x.id].passed || result.passed });
    } catch (error) {
      results[x.id] = { error: `The checker could not be reached: ${error.message}`, checks: [] };
    }
    redraw();
    document.getElementById("exercise-result")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function resultBox(x) {
    const r = results[x.id];
    if (!r) return null;
    if (r.running) return h("div", { class: "result", id: "exercise-result", role: "status" }, h("p", { class: "hint" }, "Running your code…"));
    const total = r.checks.length, good = r.checks.filter((c) => c.pass).length;
    return h("div", { class: `result ${r.passed ? "all-pass" : "some-fail"}`, id: "exercise-result", role: "status" },
      h("h2", {}, r.error ? "It did not run" : r.passed ? `All ${total} checks pass` : `${good} of ${total} checks pass`),
      r.error ? h("p", { class: "check-detail" }, r.error) : null,
      h("ul", { class: "check-list" }, r.checks.map((c) => h("li", { class: c.pass ? "is-pass" : "is-fail" },
        h("span", { class: "check-mark", "aria-hidden": "true" }, c.pass ? "✓" : "✗"),
        h("div", {}, h("span", { class: "sr" }, c.pass ? "Passed: " : "Failed: "), c.name, c.detail ? h("p", { class: "check-detail" }, c.detail) : null)))),
      r.output ? h("details", { open: true }, h("summary", {}, "What your code printed"), h("pre", { class: "sample" }, r.output)) : null,
      r.passed ? h("div", { class: "debrief" }, h("h2", {}, "What you just built"), h("p", {}, r.debrief || x.debrief),
        h("div", { class: "actions" },
          x.installs === "tool" ? installButton(x) : null,
          h("button", { onclick: () => actions.openSource(x.file) }, `Read ${x.file}`),
          next(x))) : null,
      r.sandbox ? h("p", { class: "hint" }, r.sandbox === "none"
        ? `Ran in ${r.seconds}s in a separate process with a time limit. This machine has no OS sandbox, so it ran with your permissions.`
        : `Ran in ${r.seconds}s inside ${r.sandbox}: no network, and it could write only to the workshop folder.`) : null);
  }

  function installButton(x) {
    const button = h("button", { class: "primary", onclick: async () => {
      button.disabled = true;
      try {
        await api.exerciseInstall(x.id);
        button.replaceWith(h("button", { class: "primary", onclick: () => actions.tryPrompt("How many words are in: the quick brown fox jumps over the lazy dog?") }, "Added. Ask the agent to use it"));
      } catch (error) { button.disabled = false; button.textContent = `Could not add it: ${error.message}`; }
    } }, "Add it to my agent");
    return button;
  }

  function next(x) {
    const following = exercises[exercises.findIndex((e) => e.id === x.id) + 1];
    return following ? h("button", { onclick: () => { keep(); selected = following.id; redraw(true); } }, `Next: ${following.title}`) : null;
  }

  function drawDetail(detail, conceptChips) {
    const x = exercises.find((e) => e.id === selected);
    if (!x) return put(detail, h("p", { class: "hint" }, "No exercises are available."));
    const shown = hintsShown[x.id] || 0;
    const hintButton = shown < x.hints.length
      ? h("button", { onclick: () => { hintsShown[x.id] = shown + 1; redraw(); } }, shown ? `Another hint (${shown + 1} of ${x.hints.length})` : "Give me a hint")
      : null;
    const st = state(x.id);
    put(detail, h("div", { class: "inner exercise" },
      h("span", { class: "chip" }, levels[x.level] || `Level ${x.level}`),
      h("span", { class: "chip", style: "margin-left:6px" }, `About ${x.minutes} minutes`),
      st.passed ? h("span", { class: "chip passed", style: "margin-left:6px" }, "Passed") : null,
      h("h1", {}, x.title),
      prose(x.brief),
      h("h2", {}, "Your code"),
      editor(x),
      st.path ? h("p", { class: "hint" }, "Saved as ", h("code", {}, st.path), ". Edit it there in your own editor if you prefer, then check it here or with ", h("code", {}, `loopwright exercise ${x.id} --check`), ".") : null,
      h("div", { class: "actions" },
        h("button", { class: "primary", disabled: api.static ? "" : null, onclick: () => run(x) }, "Check my work"),
        hintButton,
        h("button", { onclick: async () => {
          if (current(x) !== x.starter && !window.confirm("Replace your code with the starter?")) return;
          drafts[x.id] = x.starter; delete results[x.id];
          if (live) { try { await api.exerciseReset(x.id); live[x.id].code = x.starter; } catch { /* the editor still shows the starter */ } }
          redraw();
        } }, "Start again"),
        h("button", { onclick: () => { solutionShown[x.id] = !solutionShown[x.id]; redraw(); } }, solutionShown[x.id] ? "Hide the solution" : "Show a solution")),
      api.static ? h("p", { class: "hint" }, "Checking runs your code, and this page is a recording with no computer behind it. Connect yours with the button at the top, or install Loopwright, and the Check button works.") : null,
      shown ? h("ol", { class: "hints" }, x.hints.slice(0, shown).map((text) => h("li", {}, inline(text)))) : null,
      resultBox(x),
      solutionShown[x.id] ? h("div", { class: "solution" }, h("h2", {}, "One way to write it"),
        (st.attempts || results[x.id]) ? null : h("p", { class: "hint" }, "You have not tried yet. Reading a solution first teaches far less than a failed attempt."),
        h("pre", { class: "sample" }, x.solution)) : null,
      h("h2", {}, "How it comes up in an interview"), h("p", {}, x.interview),
      h("h2", {}, "Cards behind this exercise"), conceptChips(x.concepts || [])));
  }

  return {
    drawList, drawDetail, keep,
    onRedraw(fn) { redraw = fn; },
  };
}
