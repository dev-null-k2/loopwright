// THE BOARD: the architecture as a diagram whose boxes light up.
//
// One rule drives everything: an event names the box it happened in (`stage`)
// and the box its data came from (`src`). The board fills the first, and
// sends a dot along the line from the second. Box ids here are the keys of
// STAGES in loopwright/events.py; a test keeps the two lists identical.

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

const NS = "http://www.w3.org/2000/svg";
const el = (name, attrs = {}, text) => {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
};
const centre = (id) => [LAYOUT[id][0] + W / 2, LAYOUT[id][1] + H / 2];

// Where a line leaves box `a` towards box `b`: the middle of the facing side.
function port(a, b) {
  const [ax, ay] = centre(a), [bx, by] = centre(b);
  const dx = bx - ax, dy = by - ay;
  if (Math.abs(dx) * H > Math.abs(dy) * W) return [ax + Math.sign(dx) * W / 2, ay, "h"];
  return [ax, ay + Math.sign(dy) * H / 2, "v"];
}

// An elbow path: leave one box, turn once or twice, arrive at the other.
function route(a, b) {
  const [x1, y1, d1] = port(a, b), [x2, y2, d2] = port(b, a);
  if (d1 === "h" && d2 === "h") {
    const mid = (x1 + x2) / 2;
    return Math.abs(y1 - y2) < 1 ? `M${x1} ${y1}H${x2}` : `M${x1} ${y1}H${mid}V${y2}H${x2}`;
  }
  if (d1 === "v" && d2 === "v") {
    const mid = (y1 + y2) / 2;
    return Math.abs(x1 - x2) < 1 ? `M${x1} ${y1}V${y2}` : `M${x1} ${y1}V${mid}H${x2}V${y2}`;
  }
  return d1 === "h" ? `M${x1} ${y1}H${x2}V${y2}` : `M${x1} ${y1}V${y2}H${x2}`;
}

const colourOf = (pillar) => `var(--${pillar})`;
const WORST = { ok: 0, skip: 0, warn: 1, blocked: 2, error: 3 };
const FLAG = { warn: ["var(--warn)", "!"], blocked: ["var(--blocked)", "x"], error: ["var(--error)", "x"] };

export function createBoard(container, stages, onPick) {
  const svg = el("svg", { viewBox: "0 0 940 750", role: "img", "aria-label": "Architecture of the agent" });
  const gZones = el("g"), gEdges = el("g"), gHops = el("g"), gNodes = el("g"), gToken = el("g");
  svg.append(gZones, gEdges, gHops, gNodes, gToken);

  for (const [x, y, w, h, label] of ZONES) {
    gZones.append(el("rect", { class: "zone", x, y, width: w, height: h, rx: 10 }));
    gZones.append(el("text", { class: "zone-label", x: x + 14, y: y + 19 }, label));
  }

  const edges = new Map();        // "a>b" -> <path>, stored under both directions
  for (const [a, b] of EDGES) {
    const path = el("path", { class: "edge", d: DETOURS[`${a}>${b}`] || route(a, b) });
    gEdges.append(path);
    edges.set(`${a}>${b}`, { path, forward: true });
    edges.set(`${b}>${a}`, { path, forward: false });
  }

  const nodes = {};
  for (const [id, [x, y, sub]] of Object.entries(LAYOUT)) {
    const stage = stages[id] || { label: id, pillar: "gateway" };
    const g = el("g", { class: "node", "data-id": id, "data-pillar": stage.pillar, tabindex: 0, role: "button",
                        "aria-label": `${stage.label}: ${sub}` });
    g.append(el("rect", { class: "box", x, y, width: W, height: H, rx: 5 }));
    g.append(el("rect", { class: "stripe", x, y, width: 5, height: H, rx: 2 }));
    g.append(el("text", { x: x + 14, y: y + 23 }, stage.label));
    g.append(el("text", { class: "sub", x: x + 14, y: y + 40 }, sub));
    const count = el("text", { class: "count", x: x + W - 8, y: y + 17, "text-anchor": "end" });
    const flag = el("g", { class: "flag", transform: `translate(${x + W - 2} ${y + H - 2})` });
    g.append(count, flag);
    g.addEventListener("click", () => onPick(id));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(id); } });
    gNodes.append(g);
    nodes[id] = { g, count, flag, pillar: stage.pillar };
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

  // Draw the board as it stands after `events[index]`.
  function show(events, index, { animate = false, speed = 1, focus = true } = {}) {
    const inRun = new Set(events.flatMap((e) => [e.stage, e.src]));
    animation++;
    gToken.replaceChildren();
    gHops.replaceChildren();
    for (const { path } of edges.values()) path.setAttribute("class", "edge");
    const counts = {}, worst = {};
    for (let i = 0; i <= index && i < events.length; i++) {
      const e = events[i];
      counts[e.stage] = (counts[e.stage] || 0) + 1;
      if ((WORST[e.status] || 0) > (WORST[worst[e.stage]] || 0)) worst[e.stage] = e.status;
      const edge = edges.get(`${e.src}>${e.stage}`);
      if (edge) edge.path.setAttribute("class", "edge used");
    }
    const now = events[index];
    for (const [id, node] of Object.entries(nodes)) {
      node.g.classList.toggle("seen", !!counts[id]);
      node.g.classList.toggle("unused", focus && events.length > 3 && !inRun.has(id));    // not part of this run
      node.g.classList.toggle("on", !!now && now.stage === id);
      node.g.classList.toggle("with", !!now && now.span !== "main" && id === "subagent" && now.stage !== "subagent");
      node.count.textContent = counts[id] > 1 ? `x${counts[id]}` : "";
      node.flag.replaceChildren();
      if (FLAG[worst[id]]) {
        node.flag.append(el("circle", { r: 8, fill: FLAG[worst[id]][0] }), el("text", { y: 4 }, FLAG[worst[id]][1]));
      }
    }
    if (!now || now.src === now.stage || !LAYOUT[now.src] || !LAYOUT[now.stage]) return;
    const colour = colourOf(nodes[now.stage].pillar);
    let edge = edges.get(`${now.src}>${now.stage}`);
    if (edge) {
      edge.path.setAttribute("class", "edge live");
      edge.path.style.setProperty("--c", colour);
    } else {                                   // no permanent line: a temporary dashed hop
      const [x1, y1] = port(now.src, now.stage), [x2, y2] = port(now.stage, now.src);
      const hop = el("path", { class: "hop", d: `M${x1} ${y1}L${x2} ${y2}`, style: `--c:${colour}` });
      gHops.append(hop);
      edge = { path: hop, forward: true };
    }
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (animate && !reduced) travel(edge.path, edge.forward, colour, Math.max(140, 460 / speed));
  }

  return { show, ids: Object.keys(LAYOUT) };
}
