// The Council chamber: traders around a table, voting with paddles; the chair stamps the result.
import { sprite } from "./sprites.js";

const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}, parent) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// [x, y (feet), scale, layer]  layer "back" is drawn behind the table (seated look).
const SEATS = {
  fundamental: [262, 425, 1.75, "back"], momentum: [378, 398, 1.75, "back"], chair: [500, 382, 1.85, "back"],
  cloud: [622, 398, 1.75, "back"], flow: [738, 425, 1.75, "back"],
  bear: [222, 520, 2, "front"], news: [860, 505, 2, "front"],
  scout: [58, 612, 2.05, "front"], oracle: [58, 612, 2.05, "front"], newshound: [140, 618, 2.05, "front"],
  risk: [935, 612, 2.05, "front"],
};
export const VOTERS = ["fundamental", "momentum", "cloud", "flow", "news"];

export class Council {
  constructor(svg, agents) {
    this.svg = svg;
    this.agents = Object.fromEntries(agents.map((a) => [a.id, a]));
    this.chars = {};
    this.paddles = {};
    this.build();
  }

  build() {
    const s = this.svg;
    s.innerHTML = `<defs>
      <linearGradient id="cwall" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#2b1407"/><stop offset="1" stop-color="#170a04"/></linearGradient>
      <radialGradient id="carpet" cx="50%" cy="20%" r="80%"><stop offset="0" stop-color="#7f1d1d"/><stop offset="1" stop-color="#2a0909"/></radialGradient>
      <radialGradient id="felt" cx="50%" cy="40%" r="60%"><stop offset="0" stop-color="#2f8f4e"/><stop offset="1" stop-color="#0f4d27"/></radialGradient>
      <radialGradient id="lamp" cx="50%" cy="0%" r="100%"><stop offset="0" stop-color="#fde68a" stop-opacity=".55"/><stop offset="1" stop-color="#fde68a" stop-opacity="0"/></radialGradient>
      <linearGradient id="beam" x1="0" x2="0" y1="1" y2="0"><stop offset="0" stop-color="#67e8f9" stop-opacity=".5"/><stop offset="1" stop-color="#67e8f9" stop-opacity="0"/></linearGradient>
      <filter id="cglow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    </defs>`;
    el("rect", { width: 1000, height: 340, fill: "url(#cwall)" }, s);
    const panels = el("g", { stroke: "#3b1d0c", "stroke-width": 2 }, s);
    for (let x = 40; x < 1000; x += 80) el("line", { x1: x, y1: 0, x2: x, y2: 340 }, panels);
    el("rect", { y: 330, width: 1000, height: 290, fill: "url(#carpet)" }, s);
    el("rect", { y: 326, width: 1000, height: 8, fill: "#4a2410" }, s);

    // Neon sign
    const sign = el("g", { transform: "translate(70,28)" }, s);
    el("rect", { width: 210, height: 44, rx: 6, fill: "#1a0c05", stroke: "#f97316", "stroke-width": 2, filter: "url(#cglow)" }, sign);
    const st = el("text", { x: 105, y: 30, "text-anchor": "middle", class: "neon-sign" }, sign); st.textContent = "THE COUNCIL";

    // Monitor wall
    this.monitors = [];
    for (let i = 0; i < 6; i++) {
      const x = 60 + (i % 3) * 82, y = 92 + Math.floor(i / 3) * 62;
      el("rect", { x, y, width: 74, height: 52, rx: 4, fill: "#06140c", stroke: "#14532d", "stroke-width": 2 }, s);
      this.monitors.push(el("polyline", { fill: "none", stroke: i % 3 === 1 ? "#f87171" : "#4ade80", "stroke-width": 1.6, class: "mon" }, s));
      this.monitors[i].dataset.box = `${x},${y}`;
    }
    this.randomMonitors();

    // Who called it? board
    const b = el("g", { transform: "translate(690,40)" }, s);
    el("rect", { width: 270, height: 222, rx: 8, fill: "#0b0f17", stroke: "#a78bfa", "stroke-opacity": .6 }, b);
    const bt = el("text", { x: 14, y: 26, class: "board-title" }, b); bt.textContent = "WHO CALLED IT?";
    const bs = el("text", { x: 14, y: 42, class: "board-sub" }, b); bs.textContent = "hit rate on graded calls · weight earns votes";
    this.boardRows = el("g", { transform: "translate(14,56)" }, b);

    // Lamp + light cone
    el("rect", { x: 497, y: 0, width: 6, height: 70, fill: "#111" }, s);
    el("path", { d: "M455 70 h90 l20 34 h-130 z", fill: "#14532d", stroke: "#052e16" }, s);
    el("ellipse", { cx: 500, cy: 300, rx: 360, ry: 260, fill: "url(#lamp)" }, s);

    // Throne behind the chair
    el("path", { d: "M455 392 v-118 q45 -40 90 0 v118 z", fill: "#7f1d1d", stroke: "#fbbf24", "stroke-width": 3 }, s);

    this.backLayer = el("g", {}, s);
    // Table
    el("ellipse", { cx: 500, cy: 478, rx: 330, ry: 104, fill: "#78350f" }, s);
    el("ellipse", { cx: 500, cy: 470, rx: 322, ry: 98, fill: "#b45309" }, s);
    el("ellipse", { cx: 500, cy: 468, rx: 304, ry: 88, fill: "url(#felt)" }, s);
    el("ellipse", { cx: 500, cy: 468, rx: 250, ry: 66, fill: "none", stroke: "#fbbf24", "stroke-opacity": .25 }, s);
    // Hologram
    el("rect", { x: 440, y: 330, width: 120, height: 130, fill: "url(#beam)", class: "holo" }, s);
    el("ellipse", { cx: 500, cy: 462, rx: 70, ry: 20, fill: "none", stroke: "#67e8f9", "stroke-width": 2, filter: "url(#cglow)", class: "holo" }, s);
    this.holoTitle = el("text", { x: 500, y: 410, "text-anchor": "middle", class: "holo-big" }, s);
    this.holoSub = el("text", { x: 500, y: 436, "text-anchor": "middle", class: "holo-small" }, s);
    this.frontLayer = el("g", {}, s);

    // Ticket card
    this.ticket = el("g", { transform: "translate(305,520)", class: "ticket hidden" }, s);
    el("rect", { width: 390, height: 86, rx: 12, fill: "#0b0f17", "fill-opacity": .92, stroke: "#a78bfa", "stroke-width": 1.5 }, this.ticket);
    this.tKind = el("text", { x: 16, y: 22, class: "t-kind" }, this.ticket);
    this.tTitle = el("text", { x: 16, y: 44, class: "t-title" }, this.ticket);
    this.tTitle2 = el("text", { x: 16, y: 61, class: "t-title" }, this.ticket);
    this.tMetrics = el("text", { x: 16, y: 78, class: "t-metrics" }, this.ticket);
    this.tDots = el("g", { transform: "translate(374,22)" }, this.ticket);

    // Characters
    for (const [id, [x, y, sc, layer]] of Object.entries(SEATS)) {
      const a = this.agents[id];
      if (!a) continue;
      const g = el("g", { class: "agent", transform: `translate(${x},${y})` }, layer === "back" ? this.backLayer : this.frontLayer);
      g.innerHTML = sprite(a, sc);
      const tag = el("text", { y: 18, "text-anchor": "middle", class: "c-nametag", fill: a.color }, g);
      tag.textContent = a.name.split(" ")[0];
      this.chars[id] = { g, x, y, sc };
    }
    this.show("oracle", false);

    // Paddles above each voter
    for (const id of VOTERS) {
      const { x, y, sc } = this.chars[id];
      // Outer group holds the position; CSS animations run on the inner group so they
      // don't override the SVG transform attribute.
      const g = el("g", { class: "paddle hidden", transform: `translate(${x + 24},${y - 98 * sc / 1.75})` }, s);
      const inner = el("g", { class: "paddle-inner" }, g);
      el("line", { x1: 0, y1: 0, x2: 0, y2: 34, stroke: "#a16207", "stroke-width": 4, "stroke-linecap": "round" }, inner);
      const face = el("g", { class: "face" }, inner);
      const r = el("rect", { x: -31, y: -30, width: 62, height: 32, rx: 9 }, face);
      const t = el("text", { x: 0, y: -9, "text-anchor": "middle", class: "paddle-text" }, face);
      this.paddles[id] = { g, face, r, t };
    }

    // Stamp
    this.stampG = el("g", { transform: "translate(500,250) rotate(-10)", class: "stamp hidden" }, s);
    this.stampInner = el("g", { class: "stamp-inner" }, this.stampG);
    this.stampRect = el("rect", { x: -170, y: -42, width: 340, height: 84, rx: 10, fill: "#0b0820", "fill-opacity": .55, "stroke-width": 7 }, this.stampInner);
    this.stampText = el("text", { x: 0, y: 18, "text-anchor": "middle", class: "stamp-text" }, this.stampInner);
  }

  randomMonitors(closes) {
    this.monitors.forEach((m, i) => {
      const [bx, by] = m.dataset.box.split(",").map(Number);
      let series = closes && i === 0 ? closes : null;
      if (!series) { let v = 50; series = Array.from({ length: 24 }, () => (v += (Math.random() - 0.48) * 10)); }
      const lo = Math.min(...series), hi = Math.max(...series);
      m.setAttribute("points", series.map((v, k) => `${bx + 4 + (k / (series.length - 1)) * 66},${by + 46 - ((v - lo) / (hi - lo || 1)) * 40}`).join(" "));
    });
  }

  show(id, on) { if (this.chars[id]) this.chars[id].g.style.display = on ? "" : "none"; }

  setSubject(e) {
    const contract = e.kind === "contract";
    this.show("oracle", contract); this.show("scout", !contract);
    this.holoTitle.textContent = contract ? "KALSHI" : e.symbol;
    this.holoSub.textContent = contract ? `${e.price}¢ YES` : `$${e.price}`;
    this.ticket.classList.remove("hidden");
    this.tKind.textContent = contract ? `PREDICTION MARKET · ${(e.contract?.category || "").toUpperCase()}` : `STOCK · ${e.symbol}`;
    const title = contract ? e.title : `${e.symbol} ${e.fundamentals?.sector ? "· " + e.fundamentals.sector : ""}`;
    const [l1, l2] = wrap(title, 44);
    this.tTitle.textContent = l1; this.tTitle2.textContent = l2 || "";
    this.tMetrics.textContent = contract
      ? `YES ${e.contract.yes_bid}/${e.contract.yes_ask}¢ · NO ${e.contract.no_ask}¢ · vol ${Number(e.contract.volume).toLocaleString()}`
      : `price $${e.price} · screen ${e.screen_score > 0 ? "+" : ""}${e.screen_score}`;
    this.tDots.innerHTML = "";
    this.clearVotes();
    this.stamp(null);
    if (e.candles) this.randomMonitors(e.candles.map((c) => c[4]).slice(-40)); else this.randomMonitors();
  }

  metrics(text) { this.tMetrics.textContent = text; }

  clearVotes() {
    for (const p of Object.values(this.paddles)) { p.g.classList.add("hidden"); p.g.classList.remove("revealed", "flip"); }
    this.pending = {};
  }

  // A vote arrives face-down ("?"); reveal() flips them all at once.
  vote(id, stance, conviction, faceUp) {
    const p = this.paddles[id];
    if (!p) return;
    p.stance = stance; p.conviction = conviction;
    p.g.classList.remove("hidden");
    if (faceUp) this.flip(p); else { p.g.classList.remove("revealed"); p.r.setAttribute("class", "down"); p.t.textContent = "?"; }
  }

  flip(p) {
    p.g.classList.remove("flip"); void p.g.getBBox(); p.g.classList.add("flip", "revealed");
    p.r.setAttribute("class", p.stance);
    p.t.textContent = p.stance === "PASS" ? "PASS" : `${p.stance} ${p.conviction}`;
  }

  reveal() { for (const p of Object.values(this.paddles)) if (!p.g.classList.contains("hidden")) this.flip(p); }

  tally(t) {
    this.holoSub.textContent = `score ${t.score > 0 ? "+" : ""}${t.score.toFixed(2)}`;
    const yes = t.agreeing.length, no = t.dissenting.length;
    this.tDots.innerHTML = VOTERS.map((id, i) => {
      const c = t.agreeing.includes(id) ? "#22c55e" : t.dissenting.includes(id) ? "#ef4444" : "#475569";
      return `<circle cx="${-i * 13}" cy="0" r="5" fill="${c}"/>`;
    }).join("") + `<text x="-60" y="22" text-anchor="middle" class="t-tally">${yes}-${no}</text>`;
  }

  stamp(label) {
    if (!label) { this.stampG.classList.add("hidden"); return; }
    const ok = label === "APPROVED";
    this.stampText.textContent = label;
    this.stampText.setAttribute("fill", ok ? "#22c55e" : "#ef4444");
    this.stampRect.setAttribute("stroke", ok ? "#22c55e" : "#ef4444");
    this.stampG.classList.remove("hidden", "slam"); void this.stampG.getBBox(); this.stampG.classList.add("slam");
  }

  board(rows, agents) {
    const sorted = [...rows].sort((a, b) => (b.accuracy ?? -1) - (a.accuracy ?? -1) || b.calls - a.calls);
    this.boardRows.innerHTML = sorted.map((r, i) => {
      const a = agents[r.agent], pct = r.accuracy == null ? null : Math.round(r.accuracy * 100);
      const y = i * 31;
      return `<text x="0" y="${y + 12}" class="board-name" fill="${a.color}">${esc(a.name.split(" ")[0].toUpperCase())}</text>
        <rect x="74" y="${y + 3}" width="130" height="10" rx="5" fill="#1e293b"/>
        <rect x="74" y="${y + 3}" width="${pct == null ? 0 : 1.3 * pct}" height="10" rx="5" fill="${a.color}"/>
        <text x="242" y="${y + 12}" text-anchor="end" class="board-pct">${pct == null ? "–" : pct + "%"}</text>
        <text x="74" y="${y + 25}" class="board-sub">${r.calls} calls${r.streak >= 2 ? ` · 🔥${r.streak}` : r.streak <= -2 ? ` · ❄${-r.streak}` : ""}</text>`;
    }).join("");
  }

  setStatus(id, status) {
    const c = this.chars[id];
    if (!c) return;
    c.g.classList.toggle("thinking", status === "thinking");
    c.g.classList.toggle("speaking", status === "speaking");
  }

  anchor(id, stage) {
    const c = this.chars[id];
    if (!c || c.g.style.display === "none") return null;
    const p = this.svg.createSVGPoint(); p.x = c.x; p.y = c.y - 82 * c.sc;
    const m = this.svg.getScreenCTM(); if (!m) return null;
    const q = p.matrixTransform(m), r = stage.getBoundingClientRect();
    return { x: q.x - r.left, y: q.y - r.top };
  }
}

function wrap(text, n) {
  if (text.length <= n) return [text];
  const cut = text.lastIndexOf(" ", n);
  const a = text.slice(0, cut > 0 ? cut : n), b = text.slice(a.length).trim();
  return [a, b.length > n ? b.slice(0, n - 1) + "…" : b];
}
