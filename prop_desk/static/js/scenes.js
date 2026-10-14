// Decision scenes for the Ascendant (the Conclave) and the Brood (the Hive Mind).
import { seer, drone, queen } from "./sprites.js";

const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}, parent) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
};

function wrap(text, n) {
  if (text.length <= n) return [text, ""];
  const cut = text.lastIndexOf(" ", n);
  const a = text.slice(0, cut > 0 ? cut : n), b = text.slice(a.length).trim();
  return [a, b.length > n ? b.slice(0, n - 1) + "…" : b];
}

// Shared bits: the ticket card and the stamp.
class SceneBase {
  ticket(color) {
    this.tk = el("g", { transform: "translate(305,528)", class: "ticket hidden" }, this.svg);
    el("rect", { width: 390, height: 80, rx: 12, fill: "#0b0f17", "fill-opacity": .92, stroke: color, "stroke-width": 1.5 }, this.tk);
    this.tKind = el("text", { x: 16, y: 22, class: "t-kind", style: `fill:${color} !important` }, this.tk);
    this.tTitle = el("text", { x: 16, y: 44, class: "t-title" }, this.tk);
    this.tTitle2 = el("text", { x: 16, y: 61, class: "t-title" }, this.tk);
    this.tMetrics = el("text", { x: 374, y: 22, "text-anchor": "end", class: "t-metrics" }, this.tk);
  }

  stampInit() {
    this.stampG = el("g", { transform: "translate(500,215) rotate(-10)", class: "stamp hidden" }, this.svg);
    const inner = el("g", { class: "stamp-inner" }, this.stampG);
    this.stampRect = el("rect", { x: -170, y: -42, width: 340, height: 84, rx: 10, fill: "#0b0820", "fill-opacity": .55, "stroke-width": 7 }, inner);
    this.stampText = el("text", { x: 0, y: 18, "text-anchor": "middle", class: "stamp-text" }, inner);
  }

  stamp(label) {
    if (!label) { this.stampG.classList.add("hidden"); return; }
    const ok = label === "APPROVED";
    this.stampText.textContent = label;
    this.stampText.setAttribute("fill", ok ? "#22c55e" : "#ef4444");
    this.stampRect.setAttribute("stroke", ok ? "#22c55e" : "#ef4444");
    this.stampG.classList.remove("hidden", "slam"); void this.stampG.getBBox(); this.stampG.classList.add("slam");
  }

  subjectText(e) {
    const contract = e.kind === "contract";
    this.tk.classList.remove("hidden");
    this.tKind.textContent = contract ? `PREDICTION MARKET · ${(e.contract?.category || "").toUpperCase()}` : `STOCK · ${e.symbol}`;
    const [a, b] = wrap(contract ? e.title : `${e.symbol} · $${e.price}${e.fundamentals?.sector ? " · " + e.fundamentals.sector : ""}`, 44);
    this.tTitle.textContent = a; this.tTitle2.textContent = b;
    this.tMetrics.textContent = contract ? `${e.price}% implied` : "";
    this.stamp(null);
  }

  setStatus(id, status) {
    const c = this.chars[id];
    if (!c) return;
    c.g.classList.toggle("thinking", status === "thinking");
    c.g.classList.toggle("speaking", status === "speaking");
  }

  anchor(id, stage) {
    const c = this.chars[id];
    if (!c) return null;
    const p = this.svg.createSVGPoint(); p.x = c.x; p.y = c.y - c.h;
    const m = this.svg.getScreenCTM(); if (!m) return null;
    const q = p.matrixTransform(m), r = stage.getBoundingClientRect();
    return { x: q.x - r.left, y: q.y - r.top };
  }
}

// ---------------------------------------------------------------- The Conclave
const SEER_POS = { vael: [205, 455], kyth: [372, 372], oriel: [628, 372], lumen: [795, 455] };

export class Conclave extends SceneBase {
  constructor(svg, agents) {
    super();
    this.svg = svg;
    this.agents = agents;
    this.chars = {};
    this.gauges = {};
    svg.innerHTML = `<defs>
      <radialGradient id="cbg" cx="50%" cy="35%" r="75%"><stop offset="0" stop-color="#3b2a0b"/><stop offset=".55" stop-color="#14100a"/><stop offset="1" stop-color="#05040a"/></radialGradient>
      <radialGradient id="orb"><stop offset="0" stop-color="#fffbeb"/><stop offset=".35" stop-color="#fde68a"/><stop offset=".7" stop-color="#b45309"/><stop offset="1" stop-color="#78350f" stop-opacity="0"/></radialGradient>
      <filter id="cg2" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    </defs>`;
    el("rect", { width: 1000, height: 620, fill: "url(#cbg)" }, svg);
    for (let i = 0; i < 8; i++) {
      const x = 60 + i * 125;
      el("rect", { x: x - 9, y: 60, width: 18, height: 300, fill: "#2a1d07", stroke: "#a16207", "stroke-opacity": .4 }, svg);
      el("circle", { cx: x, cy: 56, r: 6, fill: "#67e8f9", filter: "url(#cg2)", class: "blink", style: `animation-delay:${i * .25}s` }, svg);
    }
    el("ellipse", { cx: 500, cy: 470, rx: 430, ry: 120, fill: "#1f1606", stroke: "#a16207", "stroke-width": 3 }, svg);
    el("ellipse", { cx: 500, cy: 470, rx: 360, ry: 96, fill: "none", stroke: "#fde68a", "stroke-opacity": .35, "stroke-dasharray": "10 12", class: "spin-slow", style: "transform-origin:500px 470px" }, svg);
    el("ellipse", { cx: 500, cy: 470, rx: 250, ry: 66, fill: "none", stroke: "#67e8f9", "stroke-opacity": .3, "stroke-dasharray": "3 9", class: "spin-rev", style: "transform-origin:500px 470px" }, svg);
    const sign = el("text", { x: 500, y: 44, "text-anchor": "middle", class: "scene-sign gold" }, svg); sign.textContent = "THE CONCLAVE";
    this.beams = el("g", {}, svg);
    // Convergence orb + gauge
    el("circle", { cx: 500, cy: 330, r: 70, fill: "url(#orb)", filter: "url(#cg2)", class: "pulse" }, svg);
    el("circle", { cx: 500, cy: 330, r: 86, fill: "none", stroke: "#3f2d0a", "stroke-width": 10 }, svg);
    this.arc = el("circle", { cx: 500, cy: 330, r: 86, fill: "none", stroke: "#fbbf24", "stroke-width": 10, "stroke-linecap": "round",
      transform: "rotate(-90 500 330)", "stroke-dasharray": `0 ${2 * Math.PI * 86}`, class: "arc" }, svg);
    this.orbPct = el("text", { x: 500, y: 342, "text-anchor": "middle", class: "orb-pct" }, svg);
    this.orbSub = el("text", { x: 500, y: 364, "text-anchor": "middle", class: "orb-sub" }, svg);
    this.orbPct.textContent = "—"; this.orbSub.textContent = "CONVERGENCE";
    // Seers and the Exarch
    for (const [id, [x, y]] of Object.entries(SEER_POS)) {
      const a = agents[id]; if (!a) continue;
      const g = el("g", { class: "agent", transform: `translate(${x},${y})` }, svg);
      g.innerHTML = seer(a, 1.9);
      el("text", { y: 22, "text-anchor": "middle", class: "c-nametag", fill: a.color }, g).textContent = a.name;
      this.chars[id] = { g, x, y, h: 170 };
      const gg = el("g", { transform: `translate(${x},${y - 190})`, class: "gauge hidden" }, svg);
      el("circle", { r: 24, fill: "#0b0820", stroke: "#3f2d0a", "stroke-width": 5 }, gg);
      const arc = el("circle", { r: 24, fill: "none", stroke: a.color, "stroke-width": 5, transform: "rotate(-90)", "stroke-dasharray": `0 ${2 * Math.PI * 24}` }, gg);
      const t = el("text", { y: 5, "text-anchor": "middle", class: "g-pct" }, gg);
      this.gauges[id] = { gg, arc, t };
    }
    const ex = agents.thalor;
    if (ex) {
      const g = el("g", { class: "agent", transform: "translate(500,206)" }, svg);
      g.innerHTML = seer(ex, 1.5);
      el("text", { y: 18, "text-anchor": "middle", class: "c-nametag", fill: ex.color }, g).textContent = ex.name;
      this.chars.thalor = { g, x: 500, y: 206, h: 130 };
    }
    this.ticket("#fbbf24");
    this.stampInit();
  }

  setSubject(e) {
    this.subjectText(e);
    this.beams.innerHTML = "";
    this.orbPct.textContent = "—"; this.orbSub.textContent = "CONVERGENCE";
    this.arc.setAttribute("stroke-dasharray", `0 ${2 * Math.PI * 86}`);
    for (const g of Object.values(this.gauges)) g.gg.classList.add("hidden");
  }

  model(e) {
    const g = this.gauges[e.agent], pos = SEER_POS[e.agent];
    if (!g) return;
    g.gg.classList.remove("hidden");
    g.arc.setAttribute("stroke-dasharray", `${2 * Math.PI * 24 * e.p} ${2 * Math.PI * 24}`);
    g.t.textContent = Math.round(e.p * 100) + "%";
    const col = e.stance === "LONG" ? "#22c55e" : e.stance === "SHORT" ? "#ef4444" : "#94a3b8";
    el("line", { x1: pos[0], y1: pos[1] - 90, x2: 500, y2: 330, stroke: col, "stroke-width": 1 + 3 * (e.weight || 1), "stroke-opacity": .55, class: "beam", filter: "url(#cg2)" }, this.beams);
  }

  converge(e) {
    this.arc.setAttribute("stroke-dasharray", `${2 * Math.PI * 86 * e.p} ${2 * Math.PI * 86}`);
    this.orbPct.textContent = Math.round(e.p * 100) + "%";
    this.orbSub.textContent = e.approved ? `CONVERGED · ${e.direction}` : "NOT CONVERGED";
  }

  edge(e) {
    this.orbPct.textContent = Math.round(e.prob) + "%";
    this.orbSub.textContent = `EDGE ${e.edge > 0 ? "+" : ""}${e.edge}¢ · ${e.side}`;
    this.arc.setAttribute("stroke-dasharray", `${2 * Math.PI * 86 * e.prob / 100} ${2 * Math.PI * 86}`);
  }
}

// ---------------------------------------------------------------- The Hive Mind
export class HiveMind extends SceneBase {
  constructor(svg, agents, droneIds) {
    super();
    this.svg = svg;
    this.chars = {};
    this.droneIds = droneIds;
    svg.innerHTML = `<defs>
      <radialGradient id="hbg" cx="50%" cy="55%" r="75%"><stop offset="0" stop-color="#3b0764"/><stop offset=".6" stop-color="#1a0429"/><stop offset="1" stop-color="#07010c"/></radialGradient>
      <filter id="flesh" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency="0.02" numOctaves="3" seed="9"/>
        <feColorMatrix values="0 0 0 0 .45  0 0 0 0 .05  0 0 0 0 .5  0 0 0 -1.2 .9"/></filter>
      <filter id="hg" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    </defs>`;
    el("rect", { width: 1000, height: 620, fill: "url(#hbg)" }, svg);
    el("rect", { width: 1000, height: 620, filter: "url(#flesh)", opacity: .7 }, svg);
    const sign = el("text", { x: 500, y: 44, "text-anchor": "middle", class: "scene-sign pink" }, svg); sign.textContent = "THE HIVE MIND";
    this.veins = el("g", {}, svg);
    const n = droneIds.length;
    droneIds.forEach((id, i) => {
      const a = agents[id]; if (!a) return;
      const ang = -Math.PI / 2 + (i / n) * Math.PI * 2;
      const x = 500 + Math.cos(ang) * 410, y = 330 + Math.sin(ang) * 215;
      const v = el("line", { x1: x, y1: y - 8, x2: 500, y2: 330, stroke: "#6b21a8", "stroke-width": 1.5, opacity: .4 }, this.veins);
      const g = el("g", { class: "agent", transform: `translate(${x},${y})` }, svg);
      g.innerHTML = drone(a, 1.25);
      this.chars[id] = { g, x, y, h: 30, v };
    });
    const q = agents.vexa;
    if (q) {
      el("circle", { cx: 500, cy: 300, r: 120, fill: "#f0abfc", opacity: .08, class: "pulse" }, svg);
      const g = el("g", { class: "agent", transform: "translate(500,360)" }, svg);
      g.innerHTML = queen(q, 3.2);
      el("text", { y: 26, "text-anchor": "middle", class: "c-nametag", fill: q.color }, g).textContent = q.name;
      this.chars.vexa = { g, x: 500, y: 360, h: 220 };
    }
    // Agreement meter
    this.meter = el("g", { transform: "translate(380,415)" }, svg);
    el("rect", { width: 240, height: 14, rx: 7, fill: "#1e1b2e", stroke: "#581c87" }, this.meter);
    this.mUp = el("rect", { width: 0, height: 14, rx: 7, fill: "#22c55e" }, this.meter);
    this.mDn = el("rect", { x: 240, width: 0, height: 14, rx: 7, fill: "#ef4444" }, this.meter);
    this.mText = el("text", { x: 120, y: 34, "text-anchor": "middle", class: "orb-sub" }, this.meter);
    this.ticket("#d946ef");
    this.stampInit();
  }

  setSubject(e) {
    this.subjectText(e);
    for (const c of Object.values(this.chars)) if (c.v) { c.v.setAttribute("stroke", "#6b21a8"); c.v.setAttribute("opacity", .4); c.g.style.opacity = 1; }
    this.mUp.setAttribute("width", 0); this.mDn.setAttribute("width", 0); this.mDn.setAttribute("x", 240);
    this.mText.textContent = "THE SWARM GATHERS…";
  }

  swarmVote(e) {
    let up = 0, dn = 0, quiet = 0;
    for (const [id, v] of Object.entries(e.votes)) {
      const c = this.chars[id]; if (!c) continue;
      const w = e.weights?.[id] ?? 1;
      c.v.setAttribute("stroke", v > 0 ? "#22c55e" : v < 0 ? "#ef4444" : "#4c1d95");
      c.v.setAttribute("stroke-width", v ? 1 + 2.5 * w : 1);
      c.v.setAttribute("opacity", v ? .85 : .25);
      c.g.style.opacity = v ? 1 : .45;
      if (v > 0) up += w; else if (v < 0) dn += w; else quiet++;
    }
    const tot = up + dn || 1;
    this.mUp.setAttribute("width", 240 * up / tot);
    this.mDn.setAttribute("width", 240 * dn / tot); this.mDn.setAttribute("x", 240 - 240 * dn / tot);
    const nu = Object.values(e.votes).filter((v) => v > 0).length, nd = Object.values(e.votes).filter((v) => v < 0).length;
    this.mText.textContent = `${nu} ▲ · ${nd} ▼ · ${quiet} silent · ${Math.round(e.agree * 100)}% agree`;
  }

  edge(e) { this.mText.textContent = `SWARM WANTS ${e.side} · EDGE ${e.edge > 0 ? "+" : ""}${e.edge}¢`; }
}
