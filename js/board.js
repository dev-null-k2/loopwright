// THE BOARD: the architecture as a diagram whose boxes light up.
//
// One rule drives everything: an event names the box it happened in (`stage`)
// and the box its data came from (`src`). The board fills the first, and
// sends a dot along the line from the second.
//
// There are two drawings of the same system:
//
//   simple     eighteen boxes in three bands. The turn runs left to right
//              along the top (You, Gateway, Context, Model, Tools), memory
//              feeds it from below, and LLM-Ops records it at the bottom.
//              Related stages share a box. This is the one to learn from.
//   detailed   one box per stage, twenty-eight of them: every mechanism in
//              the harness shown separately, for when you want to see
//              exactly which one fired.
//
// Stage ids are the keys of STAGES in loopwright/events.py. A test keeps the
// detailed drawing identical to that list, and checks that every stage has a
// box in the simple one.

// ============================================================ DETAILED
const W = 148, H = 52;
const C0 = 16, C1 = 200, C2 = 386, C3 = 572, C4 = 758;     // column x positions

// id: [x, y, small line under the label]
export const LAYOUT = {
  user:        [C0, 56, "the person"],
  gateway:     [C0, 146, "terminal, web, chat apps"],
  reply:       [C0, 236, "out the same door"],

  scheduler:   [C1, 56, "a clock starts the turn"],
  planner:     [C2, 56, "optional: plan first"],
  checkpoint:  [C3, 56, "state saved each round"],
  human:       [C4, 56, "approve or decline"],
  guard_in:    [C1, 146, "mask secrets"],
  context:     [C2, 146, "what the model sees"],
  model:       [C3, 146, "reason: pick a move"],
  toolgate:    [C4, 146, "allowed? valid? budget?"],
  guard_out:   [C1, 236, "secrets, citations"],
  critic:      [C2, 236, "optional: review draft"],
  guard_tool:  [C3, 236, "fence untrusted text"],
  tools:       [C4, 236, "act: our code runs"],
  subagent:    [C4, 326, "workers, fresh context"],
  workflow:    [C2, 326, "a fixed graph of steps"],

  working:     [C1, 452, "this conversation"],
  gate:        [C2, 452, "retrieve at all?"],
  semantic:    [C3, 452, "durable facts"],
  episodic:    [C4, 452, "what happened, when"],
  procedural:  [C1, 542, "skills: how-to"],
  consolidate: [C2, 542, "distil into facts"],
  kb:          [C3, 542, "handbook search"],

  evals:       [C1, 668, "score the turn"],
  cost:        [C2, 668, "tokens, time, money"],
  trace:       [C3, 668, "every step, on disk"],
  release:     [C4, 668, "ship only if it passes"],
};

const ZONES = [
  [184, 22, 738, 372, "Harness: everything around the model"],
  [184, 418, 738, 192, "Memory: what persists"],
  [184, 634, 738, 100, "LLM-Ops: how it is kept good"],
];

// The lines drawn at rest. A step between two boxes with no line here still
// animates, along a temporary dashed hop.
const EDGES = [
  ["user", "gateway"], ["scheduler", "gateway"], ["gateway", "guard_in"], ["guard_in", "working"], ["working", "procedural"],
  ["procedural", "gate"], ["gate", "semantic"], ["gate", "context"], ["planner", "context"],
  ["context", "model"], ["model", "checkpoint"], ["model", "toolgate"], ["toolgate", "human"],
  ["toolgate", "tools"], ["tools", "guard_tool"], ["guard_tool", "model"], ["tools", "subagent"],
  ["model", "critic"], ["critic", "guard_out"], ["guard_out", "reply"], ["reply", "gateway"],
  ["consolidate", "semantic"], ["semantic", "episodic"], ["semantic", "kb"],
  ["evals", "cost"], ["cost", "trace"], ["trace", "release"],
];

// Lines that would otherwise run straight through another box get a hand-drawn detour.
const DETOURS = {
  "guard_in>working": `M${C1 + W} ${146 + H - 12}H${C1 + W + 19}V${452 + H / 2}H${C1 + W}`,
  "gate>context": `M${C2 + W} ${452 + 14}H${C2 + W + 19}V${146 + H - 12}H${C2 + W}`,
};

// ============================================================ SIMPLE
// Five columns, three bands. Each box lists the stages it stands for.
const X = [36, 214, 392, 586, 766], BW = 150, BH = 62;
const ROW = { a: 64, b: 168, m: 348, o: 494 };
const BUS = 312;            // the horizontal line memory travels along, up into Context

const SIMPLE_NODES = [
  { id: "you", x: X[0], y: ROW.a, label: "You", sub: "asks, and approves", pillar: "gateway", stages: ["user", "human"] },
  { id: "door", x: X[1], y: ROW.a, label: "Gateway", sub: "terminal, web, chat", pillar: "gateway", stages: ["gateway", "scheduler"] },
  { id: "context", x: X[2], y: ROW.a, label: "Context", sub: "what the model sees", pillar: "harness", stages: ["context"] },
  { id: "model", x: X[3], y: ROW.a, label: "Model", sub: "answer, or use a tool", pillar: "loop", stages: ["model", "planner", "critic"] },
  { id: "tools", x: X[4], y: ROW.a, label: "Tools", sub: "checked, then run", pillar: "harness", stages: ["toolgate", "tools"] },
  { id: "guard", x: X[0], y: ROW.b, label: "Guardrails", sub: "in · tool results · out", pillar: "safety", stages: ["guard_in", "guard_tool", "guard_out"], ambient: true },
  { id: "reply", x: X[1], y: ROW.b, label: "Reply", sub: "out the same door", pillar: "gateway", stages: ["reply"] },
  { id: "workflow", x: X[3], y: ROW.b, label: "Workflow", sub: "fixed steps, no loop", pillar: "loop", stages: ["workflow"] },
  { id: "workers", x: X[4], y: ROW.b, label: "Workers", sub: "sub-agents, own context", pillar: "loop", stages: ["subagent"] },

  { id: "working", x: X[0], y: ROW.m, label: "Working memory", sub: "this conversation", pillar: "memory", stages: ["working"] },
  { id: "skills", x: X[1], y: ROW.m, label: "Skills", sub: "procedural: how-to", pillar: "memory", stages: ["procedural"] },
  { id: "consolidate", x: X[2], y: ROW.m, label: "Consolidation", sub: "distil chats into facts", pillar: "memory", stages: ["consolidate"] },
  { id: "facts", x: X[3], y: ROW.m, label: "Facts", sub: "semantic, plus handbook", pillar: "memory", stages: ["semantic", "kb"] },
  { id: "episodes", x: X[4], y: ROW.m, label: "Episodes", sub: "episodic: what happened", pillar: "memory", stages: ["episodic"] },
  { id: "gate", x: 486, y: BUS - 20, w: 138, h: 40, label: "Retrieval gate", sub: "search memory at all?", pillar: "memory", stages: ["gate"], small: true },

  { id: "evals", x: X[0], y: ROW.o, label: "Evals", sub: "was this turn good?", pillar: "ops", stages: ["evals"] },
  { id: "cost", x: X[1], y: ROW.o, label: "Receipt", sub: "tokens, time, cost", pillar: "ops", stages: ["cost"] },
  { id: "trace", x: X[2], y: ROW.o, label: "Trace", sub: "every step, saved", pillar: "ops", stages: ["trace", "checkpoint"] },
  { id: "release", x: X[3], y: ROW.o, label: "Release gate", sub: "ship only if it passes", pillar: "ops", stages: ["release"] },
];

const SIMPLE_ZONES = [
  [12, 22, 920, 238, "Harness: one turn, from your message to the reply"],
  [574, 44, 348, 208, "Loop: decide, act, look at the result, repeat", "inner"],
  [12, 270, 920, 176, "Memory: what the agent keeps between turns"],
  [12, 460, 920, 108, "LLM-Ops: how every turn is recorded and judged"],
];

// Lines drawn at rest: [from, to, path, arrow?]. A step between these two boxes travels along the line.
const mid = (i) => X[i] + BW / 2;
const SIMPLE_LINKS = [
  ["you", "door", `M${X[0] + BW} 95H${X[1]}`],
  ["door", "context", `M${X[1] + BW} 95H${X[2]}`, true],
  ["context", "model", `M${X[2] + BW} 95H${X[3]}`, true],
  ["model", "tools", `M${X[3] + BW} 84H${X[4]}`, true],                      // the tool call
  ["tools", "model", `M${X[4]} 106H${X[3] + BW}`, true],                     // the result, back to the model
  ["tools", "workers", `M${mid(4)} ${ROW.a + BH}V${ROW.b}`],
  ["workflow", "model", `M690 ${ROW.b}V${ROW.a + BH}`],
  ["model", "reply", `M640 ${ROW.a + BH}V147H500V199H${X[1] + BW}`, true],   // the final answer leaves the loop
  ["reply", "door", `M${mid(1)} ${ROW.b}V${ROW.a + BH}`, true],
  ["working", "consolidate", `M${mid(0)} ${ROW.m + BH}V428H${mid(2)}V${ROW.m + BH}`, true],
  ["consolidate", "facts", `M${X[2] + BW} ${ROW.m + 31}H${X[3]}`, true],
  ["evals", "cost", `M${X[0] + BW} ${ROW.o + 31}H${X[1]}`],
  ["cost", "trace", `M${X[1] + BW} ${ROW.o + 31}H${X[2]}`],
  ["trace", "release", `M${X[2] + BW} ${ROW.o + 31}H${X[3]}`],
];

// The memory bus: every kind of memory reaches Context along one line. Long-term
// memory (facts, episodes) has to pass the retrieval gate on the way.
const SIMPLE_DECOR = [
  `M${mid(2)} ${ROW.a + BH}V${BUS}`,                                         // the trunk up into Context
  `M${mid(0)} ${ROW.m}V${BUS}H${mid(2)}`, `M${mid(1)} ${ROW.m}V${BUS}`,       // conversation and skills: always loaded
  `M${mid(2)} ${BUS}H${mid(4)}V${ROW.m}`, `M${mid(3)} ${BUS}V${ROW.m}`,       // facts and episodes: through the gate
];
const ON_BUS = { context: [mid(2), ROW.a + BH], working: [mid(0), ROW.m], skills: [mid(1), ROW.m], gate: [555, BUS],
                 facts: [mid(3), ROW.m], episodes: [mid(4), ROW.m] };
const busPath = (a, b) => `M${ON_BUS[a][0]} ${ON_BUS[a][1]}V${BUS}H${ON_BUS[b][0]}V${ON_BUS[b][1]}`;
const fromLoop = (b) => `M${X[4] + BW} 95H927V${BUS}H${ON_BUS[b][0]}V${ON_BUS[b][1]}`;      // a tool reaching into memory
const overTheTop = (b) => `M${mid(4)} ${ROW.a}V52H${mid(b)}V${ROW.a}`;                       // from Tools back to You or the Gateway

// Journeys that have no line of their own. The dot follows these, drawn dashed while it travels.
const SIMPLE_TRAVEL = {
  "door>working": `M${X[1] + BW} 95H${mid(2)}V${BUS}H${mid(0)}V${ROW.m}`,     // through Context, down the trunk, into memory
  "door>skills": `M${X[1] + BW} 95H${mid(2)}V${BUS}H${mid(1)}V${ROW.m}`,
  "door>gate": `M${X[1] + BW} 95H${mid(2)}V${BUS}H555`,
  "door>workflow": `M${X[1] + BW} 95H690V${ROW.b}`,
  "tools>you": overTheTop(0), "tools>door": overTheTop(1),
  "reply>working": `M250 ${ROW.b + BH}V265H${mid(0)}V${ROW.m}`,
  "reply>evals": `M${mid(1)} ${ROW.b + BH}V265H24V${ROW.o + 31}H${X[0]}`,
  "consolidate>episodes": `M${X[2] + BW} ${ROW.m + 31}H${X[4]}`,
  "tools>facts": fromLoop("facts"), "tools>episodes": fromLoop("episodes"), "tools>working": fromLoop("working"),
  "tools>skills": fromLoop("skills"),
};

const DETAILED = (() => {
  const nodes = Object.entries(LAYOUT).map(([id, [x, y, sub]]) => ({ id, x, y, w: W, h: H, sub, stages: [id] }));
  return { name: "detailed", viewBox: "0 0 940 750", nodes, zones: ZONES, decor: [], travel: {}, hops: "any", worker: "subagent",
           links: EDGES.map(([a, b]) => [a, b, DETOURS[`${a}>${b}`] || null]) };
})();
const SIMPLE = { name: "simple", viewBox: "0 0 944 580", zones: SIMPLE_ZONES, decor: SIMPLE_DECOR, travel: SIMPLE_TRAVEL,
                 hops: "near", worker: "workers", links: SIMPLE_LINKS,
                 nodes: SIMPLE_NODES.map((n) => ({ w: BW, h: BH, ...n })) };
export const MAPS = { simple: SIMPLE, detailed: DETAILED };

// ============================================================ DRAWING
const NS = "http://www.w3.org/2000/svg";
const el = (name, attrs = {}, text) => {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
};

const WORST = { ok: 0, skip: 0, warn: 1, blocked: 2, error: 3 };
const FLAG = { warn: ["var(--warn)", "!"], blocked: ["var(--blocked)", "x"], error: ["var(--error)", "x"] };

export function createBoard(container, stages, onPick, mapName = "simple") {
  const map = MAPS[mapName] || SIMPLE;
  const box = Object.fromEntries(map.nodes.map((n) => [n.id, n]));
  const nodeOf = {};                                    // stage id -> the box that stands for it
  for (const n of map.nodes) for (const s of n.stages) nodeOf[s] = n.id;
  const ambient = new Set(map.nodes.filter((n) => n.ambient).map((n) => n.id));
  const zoneOf = (id) => map.zones.findIndex(([x, y, w, h, , kind]) => !kind && box[id].y >= y && box[id].y < y + h);
  const centre = (id) => [box[id].x + box[id].w / 2, box[id].y + box[id].h / 2];

  // Where a line leaves box `a` towards box `b`: the middle of the facing side.
  function port(a, b) {
    const [ax, ay] = centre(a), [bx, by] = centre(b);
    const dx = bx - ax, dy = by - ay;
    if (Math.abs(dx) * box[a].h > Math.abs(dy) * box[a].w) return [ax + Math.sign(dx) * box[a].w / 2, ay, "h"];
    return [ax, ay + Math.sign(dy) * box[a].h / 2, "v"];
  }
  // An elbow path: leave one box, turn once or twice, arrive at the other.
  function route(a, b) {
    const [x1, y1, d1] = port(a, b), [x2, y2, d2] = port(b, a);
    if (d1 === "h" && d2 === "h") {
      const m = (x1 + x2) / 2;
      return Math.abs(y1 - y2) < 1 ? `M${x1} ${y1}H${x2}` : `M${x1} ${y1}H${m}V${y2}H${x2}`;
    }
    if (d1 === "v" && d2 === "v") {
      const m = (y1 + y2) / 2;
      return Math.abs(x1 - x2) < 1 ? `M${x1} ${y1}V${y2}` : `M${x1} ${y1}V${m}H${x2}V${y2}`;
    }
    return d1 === "h" ? `M${x1} ${y1}H${x2}V${y2}` : `M${x1} ${y1}V${y2}H${x2}`;
  }

  const svg = el("svg", { viewBox: map.viewBox, role: "img", class: `map-${map.name}`,
                          "aria-label": "Architecture of the agent. Each box lights up when a step happens in it." });
  const defs = el("defs");
  const arrow = el("marker", { id: `arrow-${map.name}`, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" });
  arrow.append(el("path", { class: "arrowhead", d: "M0 0L10 5L0 10z" }));
  defs.append(arrow);
  const gZones = el("g"), gEdges = el("g"), gHops = el("g"), gNodes = el("g"), gToken = el("g");
  svg.append(defs, gZones, gEdges, gHops, gNodes, gToken);

  for (const [x, y, w, h, label, kind] of map.zones) {
    gZones.append(el("rect", { class: `zone ${kind || ""}`, x, y, width: w, height: h, rx: 10 }));
    gZones.append(el("text", { class: `zone-label ${kind || ""}`, x: x + (kind ? 10 : 14), y: kind ? y + h - 6 : y + 19 }, label));
  }
  for (const d of map.decor) gEdges.append(el("path", { class: "edge", d }));

  const edges = new Map();        // "a>b" -> {path, forward}; a line with no twin also answers for the way back
  for (const [a, b, d, arrowed] of map.links) {
    const path = el("path", { class: "edge", d: d || route(a, b), ...(arrowed ? { "marker-end": `url(#arrow-${map.name})` } : {}) });
    gEdges.append(path);
    edges.set(`${a}>${b}`, { path, forward: true });
  }
  for (const [a, b] of map.links) if (!edges.has(`${b}>${a}`)) edges.set(`${b}>${a}`, { path: edges.get(`${a}>${b}`).path, forward: false });

  const nodes = {};
  for (const n of map.nodes) {
    const stage = stages[n.stages[0]] || { label: n.id, pillar: "gateway" };
    const label = n.label || stage.label, pillar = n.pillar || stage.pillar;
    const g = el("g", { class: `node ${n.small ? "small" : ""} ${n.ambient ? "ambient" : ""}`, "data-id": n.id, "data-pillar": pillar,
                        tabindex: 0, role: "button", "aria-label": `${label}: ${n.sub}` });
    g.append(el("rect", { class: "box", x: n.x, y: n.y, width: n.w, height: n.h, rx: n.small ? n.h / 2 : 6 }));
    if (!n.small) g.append(el("rect", { class: "stripe", x: n.x, y: n.y, width: 5, height: n.h, rx: 2 }));
    const tall = n.h > 56;
    g.append(el("text", { x: n.x + (n.small ? 16 : 14), y: n.y + (n.small ? 17 : tall ? 26 : 23) }, label));
    g.append(el("text", { class: "sub", x: n.x + (n.small ? 16 : 14), y: n.y + (n.small ? 32 : tall ? 46 : 40) }, n.sub));
    // the tally of steps and the warning mark sit in opposite corners, clear of the label
    const count = el("text", { class: "count", x: n.x + n.w - (tall ? 9 : 8), y: tall ? n.y + n.h - 12 : n.y + 17, "text-anchor": "end" });
    const flag = el("g", { class: "flag", transform: `translate(${n.x + n.w - 2} ${tall ? n.y + 2 : n.y + n.h - 2})` });
    g.append(count, flag);
    g.addEventListener("click", () => onPick(n.stages, label));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(n.stages, label); } });
    gNodes.append(g);
    nodes[n.id] = { g, count, flag, pillar };
  }
  container.replaceChildren(svg);

  let animation = 0;
  function travel(path, forward, colour, ms) {
    const id = ++animation;
    const length = path.getTotalLength();
    const dot = el("circle", { class: "token", r: 7.5, style: `--c:${colour}` });
    gToken.replaceChildren(dot);
    const t0 = performance.now();
    const frame = (now) => {
      if (id !== animation) return;
      const p = Math.min(1, (now - t0) / ms);
      const eased = p < .5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
      const point = path.getPointAtLength((forward ? eased : 1 - eased) * length);
      dot.setAttribute("cx", point.x);
      dot.setAttribute("cy", point.y);
      if (p < 1) requestAnimationFrame(frame); else gToken.replaceChildren();
    };
    requestAnimationFrame(frame);
  }

  // Which line, if any, a step travelled along. Guardrails are a layer, not a place on the
  // route: a step "from" them is drawn from the last real box before them.
  function journeys(events, upTo) {
    const out = [];
    let last = null;
    for (let i = 0; i <= upTo && i < events.length; i++) {
      const to = nodeOf[events[i].stage];
      let from = nodeOf[events[i].src];
      if (ambient.has(from)) from = last;
      out.push(to && from && from !== to && !ambient.has(to) ? [from, to] : null);
      if (to && !ambient.has(to)) last = to;
    }
    return out;
  }

  // Draw the board as it stands after `events[index]`.
  function show(events, index, { animate = false, speed = 1, focus = true } = {}) {
    const inRun = new Set(events.flatMap((e) => [nodeOf[e.stage], nodeOf[e.src]]));
    animation++;
    gToken.replaceChildren();
    gHops.replaceChildren();
    for (const { path } of edges.values()) path.setAttribute("class", "edge");
    const counts = {}, worst = {};
    const trips = journeys(events, index);
    for (let i = 0; i <= index && i < events.length; i++) {
      const e = events[i], id = nodeOf[e.stage];
      if (!id) continue;
      counts[id] = (counts[id] || 0) + 1;
      if ((WORST[e.status] || 0) > (WORST[worst[id]] || 0)) worst[id] = e.status;
      const edge = trips[i] && edges.get(trips[i].join(">"));
      if (edge) edge.path.setAttribute("class", "edge used");
    }
    const now = events[index];
    const here = now ? nodeOf[now.stage] : null;
    for (const [id, node] of Object.entries(nodes)) {
      node.g.classList.toggle("seen", !!counts[id]);
      node.g.classList.toggle("unused", focus && events.length > 3 && !inRun.has(id));    // not part of this run
      node.g.classList.toggle("on", id === here);
      node.g.classList.toggle("with", !!now && now.span !== "main" && id === map.worker && here !== map.worker);
      node.count.textContent = counts[id] > 1 ? `x${counts[id]}` : "";
      node.flag.replaceChildren();
      if (FLAG[worst[id]]) {
        node.flag.append(el("circle", { r: 8, fill: FLAG[worst[id]][0] }), el("text", { y: 4 }, FLAG[worst[id]][1]));
      }
    }
    if (here && animate) nodes[here].g.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });   // a phone pans to follow
    const trip = trips[index];
    if (!now || !trip) return;
    const [from, to] = trip, key = `${from}>${to}`;
    const colour = `var(--${nodes[to].pillar})`;
    let edge = edges.get(key);
    if (edge) {
      edge.path.setAttribute("class", "edge live");
      edge.path.style.setProperty("--c", colour);
    } else {                                  // no permanent line: a dashed one for as long as this step is shown
      let d = map.travel[key];
      if (!d && ON_BUS[from] && ON_BUS[to] && map === SIMPLE) d = busPath(from, to);
      if (!d && (map.hops === "any" || zoneOf(from) === zoneOf(to))) {
        const [x1, y1] = port(from, to), [x2, y2] = port(to, from);
        d = `M${x1} ${y1}L${x2} ${y2}`;
      }
      if (!d) return;                         // too far apart to draw without crossing everything: the box just lights
      const hop = el("path", { class: "hop", d, style: `--c:${colour}` });
      gHops.append(hop);
      edge = { path: hop, forward: true };
    }
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (animate && !reduced) travel(edge.path, edge.forward, colour, Math.max(140, (edge.path.getTotalLength() > 400 ? 620 : 460) / speed));
  }

  return { show, name: map.name };
}
