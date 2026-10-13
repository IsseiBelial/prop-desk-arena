// The battlefield: three bases, mineral fields (stocks), gas vents (prediction markets) and the units.
import { unitSprite, shade } from "./sprites.js";

const NS = "http://www.w3.org/2000/svg";
export const W = 1200, H = 720;
const el = (tag, attrs = {}, parent) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
};
const rnd = (seed) => () => ((seed = (seed * 16807) % 2147483647) / 2147483647);

export const BASES = {
  legion: { x: 180, y: 585, label: "IRON LEGION · COMMAND POST" },
  ascendant: { x: 600, y: 112, label: "THE ASCENDANT · SPIRE" },
  brood: { x: 1020, y: 585, label: "THE BROOD · HIVE" },
  neutral: { x: 600, y: 668, label: "QUARTERMASTER" },
};
const RING = { x: 600, y: 395, rx: 300, ry: 160 };
const GAS_SPOTS = [[95, 300], [1105, 300], [330, 105], [870, 105], [355, 690], [845, 690]];

export class BattleMap {
  constructor(svg, agents) {
    this.svg = svg;
    this.units = {};
    this.minerals = {};
    this.gas = {};
    this.fx = [];
    this.claims = {};
    this.build();
    agents.forEach((a, i) => this.addUnit(a, i));
    this.last = performance.now();
    requestAnimationFrame((t) => this.tick(t));
  }

  // ------------------------------------------------------------ scenery
  build() {
    const s = this.svg;
    s.innerHTML = `<defs>
      <filter id="terrain" x="0" y="0" width="100%" height="100%">
        <feTurbulence type="fractalNoise" baseFrequency="0.011" numOctaves="4" seed="7"/>
        <feColorMatrix values="0 0 0 0 0.16  0 0 0 0 0.13  0 0 0 0 0.2  0 0 0 -1.6 1.05"/>
      </filter>
      <filter id="grit" x="0" y="0" width="100%" height="100%">
        <feTurbulence type="turbulence" baseFrequency="0.6" numOctaves="1" seed="2"/>
        <feColorMatrix values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 .035 0"/>
      </filter>
      <filter id="creep" x="-20%" y="-20%" width="140%" height="140%">
        <feTurbulence type="fractalNoise" baseFrequency="0.03" numOctaves="2" seed="4" result="n"/>
        <feDisplacementMap in="SourceGraphic" in2="n" scale="38"/>
      </filter>
      <filter id="glow" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="3.5" result="b"/>
        <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      <linearGradient id="crystal" x1="0" x2="1" y1="0" y2="1"><stop offset="0" stop-color="#bae6fd"/><stop offset=".45" stop-color="#38bdf8"/><stop offset="1" stop-color="#1e3a8a"/></linearGradient>
      <radialGradient id="plateau"><stop offset="0" stop-color="#2b2d42"/><stop offset=".8" stop-color="#1c1d2e"/><stop offset="1" stop-color="#14141f"/></radialGradient>
      <radialGradient id="creepFill"><stop offset="0" stop-color="#6b21a8"/><stop offset=".7" stop-color="#4a1167"/><stop offset="1" stop-color="#3b0d52" stop-opacity=".85"/></radialGradient>
      <radialGradient id="psi"><stop offset="0" stop-color="#fde68a" stop-opacity=".45"/><stop offset="1" stop-color="#fde68a" stop-opacity="0"/></radialGradient>
      <radialGradient id="vent"><stop offset="0" stop-color="#4ade80"/><stop offset=".5" stop-color="#166534"/><stop offset="1" stop-color="#052e16"/></radialGradient>
      <linearGradient id="spire" x1="0" x2="1"><stop offset="0" stop-color="#fef3c7"/><stop offset=".5" stop-color="#fbbf24"/><stop offset="1" stop-color="#92400e"/></linearGradient>
      <linearGradient id="steel" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#94a3b8"/><stop offset="1" stop-color="#334155"/></linearGradient>
    </defs>`;
    el("rect", { width: W, height: H, fill: "#0e0c15" }, s);
    el("rect", { width: W, height: H, filter: "url(#terrain)" }, s);
    el("rect", { width: W, height: H, filter: "url(#grit)" }, s);
    // Worn dirt roads from each base to the middle.
    const roads = el("g", { stroke: "#3a3346", "stroke-width": 46, "stroke-linecap": "round", opacity: .55, fill: "none" }, s);
    for (const b of [BASES.legion, BASES.ascendant, BASES.brood]) el("path", { d: `M${b.x} ${b.y} Q ${(b.x + RING.x) / 2} ${(b.y + RING.y) / 2 + 30} ${RING.x} ${RING.y}` }, roads);
    // Rocks
    const r = rnd(11), rocks = el("g", {}, s);
    for (let i = 0; i < 46; i++) {
      const x = r() * W, y = r() * H, w = 6 + r() * 16;
      if (Math.hypot(x - RING.x, (y - RING.y) * 1.6) < 360) continue;
      el("ellipse", { cx: x, cy: y, rx: w, ry: w * .55, fill: "#2a2636", stroke: "#0d0b14", "stroke-width": 1.2 }, rocks);
    }
    this.drawLegion(); this.drawAscendant(); this.drawBrood(); this.drawQuartermaster();
    this.resourceLayer = el("g", {}, s);
    this.claimLayer = el("g", {}, s);
    this.unitLayer = el("g", {}, s);
    this.fxLayer = el("g", {}, s);
    this.labelLayer = el("g", {}, s);
    for (const [k, b] of Object.entries(BASES)) {
      const t = el("text", { x: b.x, y: b.y + (k === "ascendant" ? -84 : 96), "text-anchor": "middle", class: "base-label " + k }, this.labelLayer);
      t.textContent = b.label;
    }
  }

  drawLegion() {
    const { x, y } = BASES.legion, g = el("g", { class: "base legion" }, this.svg);
    el("ellipse", { cx: x, cy: y + 10, rx: 150, ry: 92, fill: "url(#plateau)", stroke: "#475569", "stroke-width": 3 }, g);
    for (let i = -2; i <= 2; i++) el("rect", { x: x - 110 + (i + 2) * 46, y: y + 40, width: 40, height: 20, rx: 3, fill: "#1e293b", stroke: "#334155" }, g);
    // Command post
    el("path", { d: `M${x - 52} ${y - 8} l14 -30 h76 l14 30 l-14 26 h-76 z`, fill: "url(#steel)", stroke: "#0f172a", "stroke-width": 2 }, g);
    el("rect", { x: x - 30, y: y - 30, width: 60, height: 22, rx: 4, fill: "#0f172a", stroke: "#60a5fa" }, g);
    for (let i = 0; i < 4; i++) el("rect", { x: x - 24 + i * 13, y: y - 25, width: 8, height: 10, fill: "#60a5fa", class: "blink", style: `animation-delay:${i * .4}s` }, g);
    el("line", { x1: x + 40, y1: y - 30, x2: x + 52, y2: y - 78, stroke: "#94a3b8", "stroke-width": 3 }, g);
    el("circle", { cx: x + 52, cy: y - 80, r: 4, fill: "#ef4444", class: "blink" }, g);
    // War room bunker + comms dish
    el("rect", { x: x - 128, y: y - 26, width: 54, height: 34, rx: 6, fill: "#334155", stroke: "#0f172a", "stroke-width": 2 }, g);
    el("rect", { x: x - 120, y: y - 18, width: 38, height: 6, fill: "#fbbf24", opacity: .8 }, g);
    el("text", { x: x - 101, y: y + 22, "text-anchor": "middle", class: "bld-label" }, g).textContent = "WAR ROOM";
    el("ellipse", { cx: x + 92, cy: y - 26, rx: 16, ry: 9, fill: "#cbd5e1", stroke: "#475569", transform: `rotate(-25 ${x + 92} ${y - 26})` }, g);
    el("line", { x1: x + 92, y1: y - 26, x2: x + 92, y2: y + 4, stroke: "#64748b", "stroke-width": 3 }, g);
  }

  drawAscendant() {
    const { x, y } = BASES.ascendant, g = el("g", { class: "base ascendant" }, this.svg);
    el("ellipse", { cx: x, cy: y + 18, rx: 170, ry: 74, fill: "url(#plateau)", stroke: "#a16207", "stroke-width": 2 }, g);
    el("ellipse", { cx: x, cy: y + 18, rx: 150, ry: 62, fill: "url(#psi)" }, g);
    el("ellipse", { cx: x, cy: y + 18, rx: 120, ry: 48, fill: "none", stroke: "#fde68a", "stroke-opacity": .35, "stroke-dasharray": "6 8", class: "spin-slow", style: `transform-origin:${x}px ${y + 18}px` }, g);
    // Conclave pillars
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * Math.PI * 2, px = x + Math.cos(a) * 92, py = y + 22 + Math.sin(a) * 36;
      el("rect", { x: px - 4, y: py - 26, width: 8, height: 26, rx: 2, fill: "#fde68a", stroke: "#92400e" }, g);
      el("circle", { cx: px, cy: py - 30, r: 3.5, fill: "#67e8f9", filter: "url(#glow)", class: "blink", style: `animation-delay:${i * .3}s` }, g);
    }
    // The Spire
    const sp = el("g", { class: "hover-slow" }, g);
    el("path", { d: `M${x} ${y - 70} L${x + 22} ${y - 6} L${x} ${y + 14} L${x - 22} ${y - 6} Z`, fill: "url(#spire)", stroke: "#fef3c7", "stroke-width": 1.5, filter: "url(#glow)" }, sp);
    el("path", { d: `M${x} ${y - 70} L${x} ${y + 14}`, stroke: "#fff7ed", "stroke-opacity": .6 }, sp);
    el("circle", { cx: x, cy: y - 22, r: 6, fill: "#67e8f9", filter: "url(#glow)" }, sp);
    for (const dx of [-60, 60]) el("path", { d: `M${x + dx} ${y - 30} l8 14 l-8 10 l-8 -10 z`, fill: "#a5f3fc", opacity: .8, class: "hover", filter: "url(#glow)" }, g);
  }

  drawBrood() {
    const { x, y } = BASES.brood, g = el("g", { class: "base brood" }, this.svg);
    el("ellipse", { cx: x, cy: y + 5, rx: 175, ry: 112, fill: "url(#creepFill)", filter: "url(#creep)", class: "creep" }, g);
    const r = rnd(5);
    for (let i = 0; i < 14; i++) {
      const a = r() * Math.PI * 2, d = 60 + r() * 80;
      el("ellipse", { cx: x + Math.cos(a) * d, cy: y + Math.sin(a) * d * .6, rx: 7, ry: 5, fill: "#a21caf", stroke: "#f0abfc", "stroke-opacity": .4 }, g);
    }
    // Hive
    el("ellipse", { cx: x, cy: y - 6, rx: 60, ry: 42, fill: "#581c87", stroke: "#2e1065", "stroke-width": 3 }, g);
    for (let i = -2; i <= 2; i++) el("path", { d: `M${x + i * 20} ${y + 30} Q ${x + i * 24} ${y - 20} ${x + i * 8} ${y - 46}`, fill: "none", stroke: "#7e22ce", "stroke-width": 5 }, g);
    for (const [dx, h] of [[-44, 40], [-20, 56], [16, 60], [42, 42]]) el("path", { d: `M${x + dx - 6} ${y - 24} l6 -${h} l6 ${h} z`, fill: "#3b0764", stroke: "#a855f7" }, g);
    el("ellipse", { cx: x, cy: y - 2, rx: 18, ry: 12, fill: "#f0abfc", opacity: .55, class: "pulse" }, g);
  }

  drawQuartermaster() {
    const { x, y } = BASES.neutral, g = el("g", { class: "base neutral" }, this.svg);
    el("rect", { x: x - 36, y: y - 26, width: 72, height: 34, rx: 6, fill: "#3f3f46", stroke: "#facc15", "stroke-width": 2 }, g);
    el("path", { d: `M${x} ${y - 20} l11 5 v8 c0 8 -6 12 -11 15 c-5 -3 -11 -7 -11 -15 v-8 z`, fill: "#facc15", opacity: .85 }, g);
  }

  // ------------------------------------------------------------ resources
  setMinerals(list) {
    this.resourceLayer.querySelectorAll(".mineral").forEach((n) => n.remove());
    this.minerals = {};
    const n = list.length;
    list.forEach((m, i) => {
      const a = -Math.PI / 2 + (i / n) * Math.PI * 2;
      const x = RING.x + Math.cos(a) * RING.rx, y = RING.y + Math.sin(a) * RING.ry;
      const g = el("g", { class: "mineral", transform: `translate(${x},${y})` }, this.resourceLayer);
      el("ellipse", { cx: 0, cy: 4, rx: 34, ry: 11, fill: "#000", opacity: .35 }, g);
      const ring = el("ellipse", { cx: 0, cy: 3, rx: 42, ry: 15, class: "sel-ring" }, g);
      for (const [dx, h, w] of [[-18, 22, 9], [-6, 32, 10], [8, 26, 9], [19, 18, 8]]) {
        el("path", { d: `M${dx} ${-h} l${w} ${h * .45} l-${w * .25} ${h * .55} h-${w * 1.5} l-${w * .25} -${h * .55} z`, fill: "url(#crystal)", stroke: "#e0f2fe", "stroke-width": .8, filter: "url(#glow)" }, g);
      }
      const chg = m.change_1w ?? 0;
      const lab = el("g", { transform: "translate(0,26)" }, g);
      el("rect", { x: -42, y: -10, width: 84, height: 20, rx: 6, fill: "#0b0a14", "fill-opacity": .85, stroke: "#1e3a8a" }, lab);
      el("text", { x: -36, y: 4, class: "min-sym" }, lab).textContent = m.symbol;
      el("text", { x: 36, y: 4, "text-anchor": "end", class: "min-chg " + (chg >= 0 ? "up" : "down") }, lab).textContent = (chg >= 0 ? "+" : "") + chg.toFixed(1) + "%";
      this.minerals[m.symbol] = { x, y, g, ring };
    });
    this.drawClaims();
  }

  setGas(list) {
    this.resourceLayer.querySelectorAll(".gas").forEach((n) => n.remove());
    this.gas = {};
    list.slice(0, GAS_SPOTS.length).forEach((v, i) => {
      const [x, y] = GAS_SPOTS[i];
      const g = el("g", { class: "gas", transform: `translate(${x},${y})` }, this.resourceLayer);
      el("ellipse", { cx: 0, cy: 0, rx: 26, ry: 11, fill: "url(#vent)", stroke: "#14532d", "stroke-width": 2 }, g);
      for (let k = 0; k < 3; k++) el("circle", { cx: (k - 1) * 6, cy: -4, r: 6, fill: "#86efac", opacity: .5, class: "puff", style: `animation-delay:${k * .7}s` }, g);
      const ring = el("ellipse", { cx: 0, cy: 0, rx: 34, ry: 14, class: "sel-ring" }, g);
      const lab = el("g", { transform: "translate(0,24)" }, g);
      el("rect", { x: -44, y: -10, width: 88, height: 20, rx: 6, fill: "#0b0a14", "fill-opacity": .85, stroke: "#166534" }, lab);
      el("text", { x: -38, y: 4, class: "gas-cat" }, lab).textContent = (v.category || "market").slice(0, 10).toUpperCase();
      el("text", { x: 38, y: 4, "text-anchor": "end", class: "gas-pct" }, lab).textContent = v.implied + "%";
      const t = el("title", {}, g); t.textContent = v.title;
      this.gas[v.ticker] = { x, y, g, ring };
    });
  }

  spot(sym) { return this.minerals[sym] || this.gas[sym] || null; }

  select(sym) {
    this.resourceLayer.querySelectorAll(".selected").forEach((n) => n.classList.remove("selected"));
    const s = this.spot(sym);
    if (s) s.g.classList.add("selected");
  }

  // claims: {faction: [symbols]}
  setClaims(faction, symbols) { this.claims[faction] = symbols; this.drawClaims(); }

  drawClaims() {
    this.claimLayer.innerHTML = "";
    const colors = { legion: "#60a5fa", ascendant: "#fbbf24", brood: "#d946ef" };
    const per = {};
    for (const [f, syms] of Object.entries(this.claims)) for (const s of syms) (per[s] ||= []).push(f);
    for (const [sym, fs] of Object.entries(per)) {
      const m = this.spot(sym);
      if (!m) continue;
      fs.forEach((f, i) => {
        const x = m.x - 26 + i * 14, y = m.y - 36;
        const g = el("g", { class: "flag" }, this.claimLayer);
        el("line", { x1: x, y1: y, x2: x, y2: y + 26, stroke: "#e5e7eb", "stroke-width": 1.6 }, g);
        el("path", { d: `M${x} ${y} l14 4 l-14 5 z`, fill: colors[f] }, g);
      });
    }
  }

  contest(sym) {
    const m = this.spot(sym);
    if (!m) return;
    const g = el("g", { class: "contest", transform: `translate(${m.x + 30},${m.y - 30})` }, this.fxLayer);
    g.innerHTML = `<circle r="13" fill="#7f1d1d" stroke="#fca5a5" stroke-width="1.5"/><text y="5" text-anchor="middle" font-size="14">⚔️</text>`;
    setTimeout(() => g.remove(), 12000);
  }

  // A beam from the faction's base to the field, then resources streaming home.
  harvest(faction, sym) {
    const m = this.spot(sym), b = BASES[faction];
    if (!m || !b) return;
    const color = { legion: "#93c5fd", ascendant: "#fde68a", brood: "#f0abfc" }[faction];
    const beam = el("line", { x1: b.x, y1: b.y - 20, x2: m.x, y2: m.y - 10, stroke: color, "stroke-width": 4, class: "beam", filter: "url(#glow)" }, this.fxLayer);
    setTimeout(() => beam.remove(), 1600);
    for (let i = 0; i < 8; i++) {
      const c = el("circle", { r: 3.5, fill: color, filter: "url(#glow)" }, this.fxLayer);
      this.fx.push({ c, a: [m.x, m.y - 10], b: [b.x, b.y - 10], t: -i * 0.1 });
    }
  }

  // ------------------------------------------------------------ units
  home(id) {
    const u = this.units[id];
    return u ? [u.hx, u.hy] : [0, 0];
  }

  addUnit(a, i) {
    const b = BASES[a.faction] || BASES.neutral;
    const r = rnd(i * 97 + 13);
    let hx = b.x, hy = b.y;
    if (a.style === "drone") { const ang = r() * Math.PI * 2, d = 70 + r() * 70; hx += Math.cos(ang) * d; hy += Math.sin(ang) * d * .6 + 8; }
    else if (a.style === "queen") { hx += 0; hy += 52; }
    else if (a.faction === "ascendant") { const ang = (i % 5) / 5 * Math.PI * 2 + .3; hx += Math.cos(ang) * 70; hy += 22 + Math.sin(ang) * 30; }
    else if (a.faction === "legion") { const k = i % 10; hx += -70 + (k % 5) * 34; hy += 18 + Math.floor(k / 5) * 26; }
    else { hx += 52; hy += 8; }
    const g = el("g", { class: `unit u-${a.style}`, "data-id": a.id }, this.unitLayer);
    const sc = { human: .5, seer: .55, drone: .62, queen: .75 }[a.style] || .5;
    g.innerHTML = unitSprite(a, sc);
    const t = el("title", {}, g); t.textContent = `${a.name} - ${a.role}`;
    this.units[a.id] = { a, g, x: hx, y: hy, hx, hy, path: [], walking: false, speed: a.style === "drone" ? 190 : 140, phase: r() * 6 };
    this.place(this.units[a.id]);
  }

  place(u) {
    const jitter = u.a.style === "drone" && u.path.length ? Math.sin(u.phase * 9) * 2 : 0;
    u.g.setAttribute("transform", `translate(${(u.x + jitter).toFixed(1)},${u.y.toFixed(1)})`);
  }

  moveTo(id, x, y) {
    const u = this.units[id];
    if (!u) return;
    if (u.a.style === "seer") return this.warp(id, x, y);
    u.path = [[x, y]];
  }

  warp(id, x, y) {
    const u = this.units[id];
    if (!u) return;
    this.flash(u.x, u.y - 10, u.a.color);
    u.x = x; u.y = y; u.path = [];
    this.place(u);
    this.flash(x, y - 10, u.a.color);
    u.g.classList.remove("warped"); void u.g.getBBox(); u.g.classList.add("warped");
  }

  flash(x, y, color) {
    const c = el("circle", { cx: x, cy: y, r: 4, fill: "none", stroke: color, "stroke-width": 3, class: "warp-ring", filter: "url(#glow)" }, this.fxLayer);
    setTimeout(() => c.remove(), 700);
  }

  goHome(id) { const u = this.units[id]; if (u) this.moveTo(id, u.hx, u.hy); }

  // Send a unit to a resource; `slot`/`of` spread several units around it.
  toField(id, sym, slot = 0, of = 1) {
    const m = this.spot(sym);
    if (!m) return;
    const a = (slot / Math.max(1, of)) * Math.PI * 2 + (of > 6 ? 0 : 0.6);
    const r = of > 6 ? 46 + (slot % 3) * 10 : 34;
    this.moveTo(id, m.x + Math.cos(a) * r, m.y + 8 + Math.sin(a) * r * .5);
  }

  setStatus(id, status) {
    const u = this.units[id];
    if (!u) return;
    u.g.classList.toggle("thinking", status === "thinking");
    u.g.classList.toggle("speaking", status === "speaking");
  }

  droneVote(id, v) {
    const u = this.units[id];
    if (!u) return;
    const glow = u.g.querySelector(".vote-glow");
    if (!glow) return;
    glow.setAttribute("stroke", v > 0 ? "#22c55e" : v < 0 ? "#ef4444" : "#64748b");
    glow.setAttribute("opacity", v ? 1 : .5);
  }

  clearDroneVotes() { this.unitLayer.querySelectorAll(".vote-glow").forEach((g) => g.setAttribute("opacity", 0)); }

  anchor(id, stage) {
    const u = this.units[id];
    if (!u) return null;
    const p = this.svg.createSVGPoint(); p.x = u.x; p.y = u.y - (u.a.style === "queen" ? 58 : 38);
    const m = this.svg.getScreenCTM(); if (!m) return null;
    const q = p.matrixTransform(m), r = stage.getBoundingClientRect();
    return { x: q.x - r.left, y: q.y - r.top };
  }

  snapshot() {
    return Object.values(this.units).map((u) => ({ x: u.x, y: u.y, c: u.a.color, f: u.a.faction }));
  }

  tick(now) {
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    for (const u of Object.values(this.units)) {
      u.phase += dt;
      if (u.path.length) {
        const [tx, ty] = u.path[0];
        const dx = tx - u.x, dy = ty - u.y, d = Math.hypot(dx, dy);
        if (d < 1.5) { u.x = tx; u.y = ty; u.path.shift(); }
        else { const k = Math.min(1, (u.speed * dt) / d); u.x += dx * k; u.y += dy * k; }
        this.place(u);
      }
      const walking = u.path.length > 0;
      if (walking !== u.walking) { u.walking = walking; u.g.classList.toggle("walking", walking); if (!walking) this.place(u); }
    }
    const order = Object.values(this.units).sort((p, q) => p.y - q.y);
    const key = order.map((u) => u.a.id).join();
    if (key !== this.orderKey) { this.orderKey = key; order.forEach((u) => this.unitLayer.appendChild(u.g)); }
    this.fx = this.fx.filter((p) => {
      p.t += dt * 0.9;
      if (p.t < 0) return true;
      if (p.t >= 1) { p.c.remove(); return false; }
      const x = p.a[0] + (p.b[0] - p.a[0]) * p.t, y = p.a[1] + (p.b[1] - p.a[1]) * p.t - Math.sin(p.t * Math.PI) * 40;
      p.c.setAttribute("cx", x); p.c.setAttribute("cy", y);
      return true;
    });
    requestAnimationFrame((t) => this.tick(t));
  }
}

export function drawMinimap(svg, map) {
  const dots = map.snapshot().map((d) => `<circle cx="${d.x}" cy="${d.y}" r="${d.f === "brood" ? 9 : 13}" fill="${d.c}"/>`).join("");
  const res = Object.values(map.minerals).map((m) => `<rect x="${m.x - 14}" y="${m.y - 10}" width="28" height="20" fill="#38bdf8"/>`).join("")
    + Object.values(map.gas).map((m) => `<circle cx="${m.x}" cy="${m.y}" r="14" fill="#22c55e"/>`).join("");
  const bases = Object.entries(BASES).map(([k, b]) => `<circle cx="${b.x}" cy="${b.y}" r="${k === "neutral" ? 22 : 60}" fill="none" stroke="${{ legion: "#60a5fa", ascendant: "#fbbf24", brood: "#d946ef", neutral: "#facc15" }[k]}" stroke-width="10"/>`).join("");
  svg.innerHTML = `<rect width="${W}" height="${H}" fill="#0b0a12"/>${bases}${res}${dots}`;
}

export { shade };
