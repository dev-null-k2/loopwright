// One place that talks to the server, so the rest of the cockpit does not care
// where the agent is.
//
// Live:    /api/* on the server that also serves this page, with turns streamed
//          as Server-Sent Events.
// Remote:  this page is the public copy (GitHub Pages) and was opened with a
//          connect link from `loopwright serve --tunnel`. The same /api/* calls go
//          to that computer, each carrying the access key in a header.
// Static:  no server at all. Everything comes from demo/data.json, which
//          scripts/build_demo.py records from real runs of the scripted model.

let demo = null;
let remote = null;            // { url: "https://…/", key, space } when this page drives an agent on another computer

const SAVED = "loopwright.connection";
const saved = {
  get() { try { return JSON.parse(localStorage.getItem(SAVED)); } catch { return null; } },
  set(value) { try { value ? localStorage.setItem(SAVED, JSON.stringify(value)) : localStorage.removeItem(SAVED); } catch { /* private mode */ } },
};

// A connect link in any of the forms a person might paste:
//   https://<page>/#connect=https://<computer>&key=<key>     what `loopwright serve --tunnel` prints
//   https://<computer>/?key=<key>                             the computer's own address
export function parseLink(text) {
  const raw = String(text || "").trim();
  try {
    const hash = raw.includes("#") ? new URLSearchParams(raw.slice(raw.indexOf("#") + 1)) : null;
    if (hash && hash.get("connect")) {
      const url = new URL(hash.get("connect"));
      if (!["http:", "https:"].includes(url.protocol) || !hash.get("key")) return null;
      const link = { url: url.origin + "/", key: hash.get("key") };
      try { if (new URL(hash.get("wake")).protocol === "https:") link.wake = hash.get("wake"); } catch { /* no wake address */ }
      return link;
    }
    const url = new URL(raw);
    const key = url.searchParams.get("key");
    return ["http:", "https:"].includes(url.protocol) && key ? { url: url.origin + "/", key } : null;
  } catch { return null; }
}

// An http address on the home network, called from this https page. Chrome allows that once
// the person grants "local network access", if the request says where it is going.
function addressSpace(url) {
  const { protocol, hostname } = new URL(url);
  if (protocol !== "http:" || location.protocol !== "https:") return null;
  if (hostname === "localhost" || hostname.startsWith("127.")) return "loopback";
  return /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(hostname) || hostname.endsWith(".local") ? "local" : null;
}

function request(path, init = {}) {
  if (!remote) return fetch(path, init);
  const options = { ...init, headers: { ...(init.headers || {}), "x-loopwright-key": remote.key } };
  if (remote.space) options.targetAddressSpace = remote.space;
  return fetch(remote.url + path, options);
}

async function getJSON(url, init) {
  const response = await request(url, init);
  if (!response.ok) throw Object.assign(new Error(`${url}: ${response.status}`), { status: response.status });
  return response.json();
}

// Is there a tunnel or forwarder between this page and the server? Those may hold a stream back
// until it ends (Cloudflare's quick tunnels do), and a phone drops streams when its screen locks.
function proxied() {
  if (remote) return true;
  const host = location.hostname;
  return !(host === "localhost" || host === "[::1]" || /^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host) || host.endsWith(".local"));
}

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Long work (a turn, an eval run) arrives as a series of messages. Directly connected, the server
// streams them down one response. Through a proxy, the work runs as a job on the server and this
// page collects the messages with short requests, picking up where it left off after a dropped one.
async function stream(url, body, onMessage) {
  if (proxied()) {
    const { job } = await getJSON(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ...body, poll: true }) });
    let next = 0;
    let failures = 0;
    for (;;) {
      let batch;
      try {
        batch = await getJSON(`api/jobs/${job}?after=${next}`);
        failures = 0;
      } catch (error) {
        if (error.status || ++failures > 20) throw error;      // the server said no, or it has been gone too long
        await pause(1500);
        continue;
      }
      batch.messages.forEach(onMessage);
      next = batch.next;
      if (batch.done) return;
    }
  }
  const response = await request(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok || !response.body) throw Object.assign(new Error(`${url}: ${response.status}`), { status: response.status });
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

  remote: null,             // the address of the computer this page is driving, when it is
  unreachable: null,        // { url, why } when a saved computer did not answer and the demo is showing instead

  async init() {
    const link = parseLink(location.href);                      // arrived by a connect link
    if (link && link.url !== location.origin + "/") {
      saved.set(link);
      history.replaceState(null, "", location.pathname + location.search + "#cockpit");   // keep the key out of the address bar
    }
    try {
      return await getJSON("api/health");                       // this page's own server
    } catch { /* a static copy of the page: no server behind it */ }
    const target = saved.get();
    if (target) {
      remote = { ...target, space: addressSpace(target.url) };
      try {
        const health = await getJSON("api/health", { signal: AbortSignal.timeout(12000) });
        this.remote = target.url;
        return health;
      } catch (error) {
        remote = null;
        this.unreachable = { url: target.url, wake: target.wake || "", why: error.status === 403 ? "key" : addressSpace(target.url) ? "blocked" : "offline" };
      }
    }
    demo = await (await fetch("demo/data.json")).json();
    this.static = true;
    return demo.health;
  },

  // Point this page at a computer (a pasted connect link), or back at the recorded demo.
  connect(text) {
    const link = parseLink(text);
    if (!link) return false;
    saved.set(link);
    location.reload();
    return true;
  },
  disconnect() { saved.set(null); location.reload(); },
  // Is the saved computer answering yet? Asked again and again while it wakes up.
  async reachable() {
    const target = saved.get();
    if (!target) return false;
    try {
      const response = await fetch(target.url + "api/health", { headers: { "x-loopwright-key": target.key }, signal: AbortSignal.timeout(5000) });
      return response.ok;
    } catch { return false; }
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

  // Run a turn. onEvent gets each step, onDelta each piece of the reply while the model is
  // still writing it; resolves with the final result.
  async chat(message, session, options, onEvent, lesson = null, onDelta = null) {
    if (demo) {
      const recorded = lesson && demo.runs.find((r) => r.lesson === lesson && r.turn.message === message);
      const { run, exact } = recorded ? { run: recorded, exact: true } : closestRun(message);
      for (const event of run.events) onEvent(event);
      const note = exact ? "" : `This page is a recorded demo, so it cannot run new requests. Showing the closest recorded run: "${run.turn.message}". To ask anything, connect your computer (the button at the top).\n\n`;
      return { ...run.end, reply: note + run.end.reply, turn_id: run.turn.turn_id, recorded: true };
    }
    let result = null;
    await stream("api/chat", { message, session, options, space: this.space }, (msg) => {
      if (msg.type === "event") onEvent(msg);
      else if (msg.type === "delta") onDelta?.(msg);
      else if (msg.type === "done") result = msg;
    });
    return result || { ok: false, reply: "The connection closed before the turn finished.", receipt: {}, checks: [] };
  },

  approve: (call_id, approved) => post("api/approve", { call_id, approved }),
  deleteFact(id) { return getJSON(`api/memory/facts/${id}${this.q()}`, { method: "DELETE" }); },
  resetMemory() { return post("api/memory/reset", { confirm: "reset", space: this.space }); },

  // screenshots the browser agent saved, one per action
  // (from another computer the picture needs the key, so it is fetched and handed over as a blob)
  async shot(name) {
    if (demo) return `demo/shots/${name}`;
    if (!remote) return `api/shots/${name}${this.q()}`;
    const response = await request(`api/shots/${name}${this.q()}`);
    if (!response.ok) throw new Error("no screenshot");
    return URL.createObjectURL(await response.blob());
  },

  // automations: scheduled tasks, workflows, chat apps
  automations() { return demo ? demo.automations : getJSON(`api/automations${this.q()}`); },
  addTask(prompt, schedule) { return post("api/tasks", { prompt, schedule, space: this.space }); },
  runTask(id) { return post(`api/tasks/${id}/run`, { space: this.space }); },
  toggleTask(id, enabled) { return post(`api/tasks/${id}/toggle`, { enabled, space: this.space }); },
  deleteTask(id) { return getJSON(`api/tasks/${id}${this.q()}`, { method: "DELETE" }); },

  // model comparison
  async compareReport() { return demo ? { report: demo.compare, providers: demo.health.providers } : getJSON("api/compare/report"); },
  async compareTrace(slug, id) { return demo ? demo.compareTraces[`${slug}/${id}`] : getJSON(`api/compare/trace/${slug}/${id}`); },
  async runCompare(targets, onProgress, trials = 1) {
    let report = null;
    await stream("api/compare", { targets, trials }, (msg) => {
      if (msg.type === "progress") onProgress(msg);
      else if (msg.type === "done") report = msg.report;
      else if (msg.type === "error") throw new Error(msg.error);
    });
    return report;
  },

  // the workshop: exercises where the learner writes code. Checking needs a computer to run it on.
  workshop() { return demo ? null : getJSON("api/workshop"); },
  exerciseCheck(id, code) { return post(`api/workshop/${id}/check`, { code }); },
  exerciseSave(id, code) { return demo ? null : post(`api/workshop/${id}/save`, { code }); },
  exerciseReset(id) { return post(`api/workshop/${id}/reset`, {}); },
  exerciseInstall(id) { return post(`api/workshop/${id}/install`, {}); },

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
