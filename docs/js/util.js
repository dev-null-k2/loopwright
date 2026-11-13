// Small helpers shared by every view. No framework: these build DOM nodes.

export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "style") node.setAttribute("style", value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "html") node.innerHTML = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const escapeHTML = (text) => String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Just enough Markdown for replies: bold, inline code, and nothing that can inject markup.
export function lightMarkdown(text) {
  return escapeHTML(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

export const pretty = (value) => (typeof value === "string" ? value : JSON.stringify(value, null, 2));
export const fmt = (n) => (n == null ? "n/a" : Number(n).toLocaleString("en-US"));
export const pct = (x) => (x == null ? "n/a" : `${Math.round(x * 100)}%`);
export const pillarVar = (pillar) => `var(--${pillar || "gateway"})`;

// replaceChildren, minus the nulls (the DOM would print them as the word "null")
export function put(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((c) => c != null && c !== false));
  return node;
}
