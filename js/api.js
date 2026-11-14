// One place that talks to the server, so the rest of the cockpit does not care
// whether it is running against a live Loopwright or as the static demo.
//
// Live:   /api/* on the local server, with turns streamed as Server-Sent Events.
// Static: no server (GitHub Pages). Everything comes from demo/data.json, which
//         scripts/build_demo.py records from real runs of the scripted model.

let demo = null;

async function getJSON(url, init) {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}

async function stream(url, body, onMessage) {
  const response = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok || !response.body) throw new Error(`${url}: ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      if (chunk.startsWith("data: ")) onMessage(JSON.parse(chunk.slice(6)));
    }
  }
}

const words = (text) => new Set(text.toLowerCase().match(/[a-z0-9]+/g) || []);

// Static demo: find the recorded run whose prompt is closest to what was typed.
function closestRun(message) {
  const want = words(message);
  let best = null, score = -1;
  for (const run of demo.runs.filter((r) => r.main)) {
    const have = words(run.turn.message);
    const overlap = [...want].filter((w) => have.has(w)).length / Math.max(1, new Set([...want, ...have]).size);
    if (overlap > score) { best = run; score = overlap; }
  }
  return { run: best, exact: score > 0.95 };
}

const post = (url, body) => getJSON(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  static: false,
  space: "main",            // "main" is your agent; "classroom" is the scratch copy lessons run in
  q() { return this.space === "main" ? "" : "?space=classroom"; },

  async init() {
    try {
      const health = await getJSON("api/health");
      return health;
    } catch {
      demo = await getJSON("demo/data.json");
      this.static = true;
      return demo.health;
    }
  },

  handbook: () => (demo ? demo.handbook : getJSON("api/handbook")),
  memory() { return demo ? demo.memory : getJSON(`api/memory${this.q()}`); },
  overview() { return demo ? demo.overview : getJSON(`api/overview${this.q()}`); },
  tools() { return demo ? demo.tools : getJSON(`api/tools${this.q()}`); },
  async sessions() { return demo ? [] : (await getJSON("api/sessions")).sessions; },
  async transcript(id) { return (await getJSON(`api/sessions/${encodeURIComponent(id)}`)).messages; },
  async runs() { return demo ? demo.runs.filter((r) => !r.lesson || r.main).map((r) => r.summary) : (await getJSON(`api/runs${this.q()}`)).runs; },
  async run(id) { return demo ? demo.runs.find((r) => r.turn.turn_id === id) : getJSON(`api/runs/${id}${this.q()}`); },
  resetClassroom() { return demo ? null : post("api/classroom/reset", {}); },
  saveSkill(skill) { return post("api/skills", { ...skill, space: this.space }); },
  deleteSkill(name) { return getJSON(`api/skills/${name}${this.q()}`, { method: "DELETE" }); },
  saveIdentity(text) { return post("api/identity", { text, space: this.space }); },
  toggleTool(name, enabled) { return post("api/tools/toggle", { name, enabled, space: this.space }); },
  addMcp(server) { return post("api/mcp", { ...server, space: this.space }); },
  removeMcp(name) { return getJSON(`api/mcp/${name}${this.q()}`, { method: "DELETE" }); },
  async evalReport() { return demo ? demo.evals : (await getJSON("api/evals/report")).report; },
  async evalTrace(id) { return demo ? demo.evalTraces[id] : getJSON(`api/evals/trace/${id}`); },
  async source(path) {
    if (demo) return { path, text: demo.sources[path] || `Open ${path} in the repository to read it.` };
    return getJSON(`api/source?path=${encodeURIComponent(path)}`);
  },
  suggestions: () => (demo ? [...new Set(demo.runs.filter((r) => r.main).map((r) => r.turn.message))] : null),

  // Run a turn. onEvent gets each step; resolves with the final result.
  async chat(message, session, options, onEvent, lesson = null) {
    if (demo) {
      const recorded = lesson && demo.runs.find((r) => r.lesson === lesson && r.turn.message === message);
      const { run, exact } = recorded ? { run: recorded, exact: true } : closestRun(message);
      for (const event of run.events) onEvent(event);
      const note = exact ? "" : `This page is a recorded demo, so it cannot run new requests. Showing the closest recorded run: "${run.turn.message}". Install Loopwright to ask anything.\n\n`;
      return { ...run.end, reply: note + run.end.reply, turn_id: run.turn.turn_id, recorded: true };
    }
    let result = null;
    await stream("api/chat", { message, session, options, space: this.space }, (msg) => {
      if (msg.type === "event") onEvent(msg);
      else if (msg.type === "done") result = msg;
    });
    return result || { ok: false, reply: "The connection closed before the turn finished.", receipt: {}, checks: [] };
  },

  approve: (call_id, approved) => post("api/approve", { call_id, approved }),
  deleteFact(id) { return getJSON(`api/memory/facts/${id}${this.q()}`, { method: "DELETE" }); },
  resetMemory() { return post("api/memory/reset", { confirm: "reset", space: this.space }); },

  // screenshots the browser agent saved, one per action
  shotUrl(name) { return demo ? `demo/shots/${name}` : `api/shots/${name}${this.q()}`; },

  // automations: scheduled tasks, workflows, chat apps
  automations() { return demo ? demo.automations : getJSON(`api/automations${this.q()}`); },
  addTask(prompt, schedule) { return post("api/tasks", { prompt, schedule, space: this.space }); },
  runTask(id) { return post(`api/tasks/${id}/run`, { space: this.space }); },
  toggleTask(id, enabled) { return post(`api/tasks/${id}/toggle`, { enabled, space: this.space }); },
  deleteTask(id) { return getJSON(`api/tasks/${id}${this.q()}`, { method: "DELETE" }); },

  // model comparison
  async compareReport() { return demo ? { report: demo.compare, providers: demo.health.providers } : getJSON("api/compare/report"); },
  async compareTrace(slug, id) { return demo ? demo.compareTraces[`${slug}/${id}`] : getJSON(`api/compare/trace/${slug}/${id}`); },
  async runCompare(targets, onProgress) {
    let report = null;
    await stream("api/compare", { targets }, (msg) => {
      if (msg.type === "progress") onProgress(msg);
      else if (msg.type === "done") report = msg.report;
      else if (msg.type === "error") throw new Error(msg.error);
    });
    return report;
  },

  // feedback and your own eval cases
  feedback(turn_id, rating, note = "") { return post("api/feedback", { turn_id, rating, note, space: this.space }); },
  saveCase(turn_id, note = "") { return post("api/evals/mine", { turn_id, note, space: this.space }); },
  async mine() { return demo ? { cases: [], feedback: {} } : getJSON("api/evals/mine"); },
  runMine(options) { return post("api/evals/mine/run", { options }); },
  deleteCase(id) { return getJSON(`api/evals/mine/${id}`, { method: "DELETE" }); },

  async runEvals(onProgress) {
    let report = null;
    await stream("api/evals/run", {}, (msg) => {
      if (msg.type === "progress") onProgress(msg);
      else if (msg.type === "done") report = msg.report;
      else if (msg.type === "error") throw new Error(msg.error);
    });
    return report;
  },
};
