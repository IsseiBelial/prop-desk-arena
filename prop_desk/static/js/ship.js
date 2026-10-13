// The spaceship: an isometric map of the firm. Agents walk between rooms as the session moves.
import { sprite, shade } from "./sprites.js";

const NS = "http://www.w3.org/2000/svg";
const TW = 23, TH = 10.5, OX = 352, OY = 100, WALL = 36;
const iso = (x, y, z = 0) => [OX + (x - y) * TW, OY + (x + y) * TH - z];
const pts = (list) => list.map((p) => iso(...p).join(",")).join(" ");
const rnd = (seed) => () => ((seed = (seed * 16807) % 2147483647) / 2147483647);

// Rooms in tile coordinates [x0, y0, x1, y1]. The corridor runs along y 5..7.
export const ROOMS = {
  lab:      { name: "Research Lab", icon: "🔬", rect: [0, 0, 8, 5], color: "#38bdf8" },
  den:      { name: "Bear Den", icon: "🐻", rect: [8, 0, 12, 5], color: "#c08457" },
  obs:      { name: "Markets Observatory", icon: "🔭", rect: [12, 0, 18, 5], color: "#2dd4bf" },
  riskbay:  { name: "Risk Bay", icon: "🛡️", rect: [18, 0, 23, 5], color: "#facc15" },
  bridge:   { name: "Bridge", icon: "👑", rect: [23, 0, 29, 5], color: "#e5e7eb" },
  floor:    { name: "Trading Floor", icon: "💹", rect: [0, 7, 8, 12], color: "#f59e0b" },
  council:  { name: "Council Chamber", icon: "⚖️", rect: [8, 7, 18, 12], color: "#a78bfa" },
  exec:     { name: "Execution Desk", icon: "⚡", rect: [18, 7, 23, 12], color: "#fb7185" },
  vault:    { name: "Win/Loss Vault", icon: "💰", rect: [23, 7, 29, 12], color: "#22c55e" },
};

// Named standing spots inside rooms.
const SPOTS = {
  lab: [[2, 2.6], [4, 2.6], [6, 2.6], [3, 4], [5, 4]],
  den: [[10, 3]],
  obs: [[14, 3.2], [16, 3.4]],
  riskbay: [[20.5, 3], [21.5, 3.8]],
  bridge: [[26, 2.5], [25, 3.8], [27.5, 3.8]],
  floor: [[1.6, 9], [3.4, 9], [5.2, 9], [2.5, 10.8], [4.6, 10.8], [6.6, 10.8]],
  // Council: seats around the table at (13, 9.5), then presenter spots.
  council: [[11.2, 8.4], [12.6, 7.9], [14.2, 7.9], [15.6, 8.4], [16.3, 9.6], [10.4, 9.6], [12, 11.2], [14, 11.3], [15.6, 11]],
  exec: [[20.5, 9.5], [19.5, 10.5]],
  vault: [[25.5, 9.5], [26.5, 10.5]],
};

export const HOME = {
  scout: ["lab", 0], newshound: ["lab", 1], oracle: ["obs", 0], bear: ["den", 0],
  fundamental: ["floor", 0], momentum: ["floor", 1], cloud: ["floor", 2], flow: ["floor", 3], news: ["floor", 4],
  risk: ["riskbay", 0], chair: ["bridge", 0],
};
export const COUNCIL_SEAT = {
  fundamental: 0, momentum: 1, chair: 2, cloud: 3, flow: 4, news: 5, bear: 6, scout: 7, newshound: 8, oracle: 7, risk: 8,
};

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

function box(g, x, y, w, d, h, color, z = 0) {
  // Iso box: right face, left face, top.
  el("polygon", { points: pts([[x + w, y, z], [x + w, y + d, z], [x + w, y + d, z + h], [x + w, y, z + h]]), fill: shade(color, -0.25) }, g);
  el("polygon", { points: pts([[x, y + d, z], [x + w, y + d, z], [x + w, y + d, z + h], [x, y + d, z + h]]), fill: shade(color, -0.12) }, g);
  el("polygon", { points: pts([[x, y, z + h], [x + w, y, z + h], [x + w, y + d, z + h], [x, y + d, z + h]]), fill: color }, g);
}

export class Ship {
  constructor(svg, agents) {
    this.svg = svg;
    this.agents = {};
    this.particles = [];
    this.screens = {};
    this.labels = {};
    this.build();
    for (const a of agents) this.addAgent(a);
    this.last = performance.now();
    requestAnimationFrame((t) => this.tick(t));
  }

  build() {
    const s = this.svg;
    s.innerHTML = `<defs>
      <radialGradient id="space" cx="60%" cy="40%" r="80%"><stop offset="0" stop-color="#2a1259"/><stop offset=".55" stop-color="#140a33"/><stop offset="1" stop-color="#05030f"/></radialGradient>
      <radialGradient id="planet" cx="35%" cy="30%" r="75%"><stop offset="0" stop-color="#f0abfc"/><stop offset=".45" stop-color="#a21caf"/><stop offset="1" stop-color="#2e1065"/></radialGradient>
      <linearGradient id="wall" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#ffffff" stop-opacity=".16"/><stop offset="1" stop-color="#ffffff" stop-opacity=".03"/></linearGradient>
      <radialGradient id="engine"><stop offset="0" stop-color="#e0f2fe"/><stop offset=".4" stop-color="#38bdf8"/><stop offset="1" stop-color="#38bdf8" stop-opacity="0"/></radialGradient>
      <filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    </defs>`;
    el("rect", { width: 1000, height: 620, fill: "url(#space)" }, s);
    const r = rnd(7), stars = el("g", {}, s);
    for (let i = 0; i < 140; i++) {
      el("circle", { cx: r() * 1000, cy: r() * 620, r: r() * 1.3 + 0.2, fill: "#fff", opacity: r() * 0.7 + 0.2,
        class: i % 5 === 0 ? "twinkle" : "", style: `animation-delay:${(r() * 4).toFixed(2)}s` }, stars);
    }
    el("circle", { cx: 905, cy: 585, r: 150, fill: "url(#planet)", opacity: 0.9 }, s);
    el("ellipse", { cx: 905, cy: 585, rx: 230, ry: 34, fill: "none", stroke: "#f0abfc", "stroke-opacity": 0.35, "stroke-width": 3, transform: "rotate(-14 905 585)" }, s);

    // Hull: a skirt for thickness, then the deck.
    const hull = [[-1.5, -1.5], [29, -1.5], [33, 6], [29, 13.5], [-1.5, 13.5]];
    el("polygon", { points: hull.map(([x, y]) => { const [a, b] = iso(x, y); return `${a},${b + 22}`; }).join(" "), fill: "#120b2c", stroke: "#6d28d9", "stroke-opacity": .5 }, s);
    el("polygon", { points: pts(hull.map(([x, y]) => [x, y, 0])), fill: "#1d1442", stroke: "#a78bfa", "stroke-width": 2, filter: "url(#glow)" }, s);
    for (const y of [2.5, 9.5]) {
      const [ex, ey] = iso(-1.8, y);
      el("ellipse", { cx: ex - 22, cy: ey - 6, rx: 30, ry: 14, fill: "url(#engine)", class: "engine" }, s);
    }
    // Corridor with guide lights.
    el("polygon", { points: pts([[0, 5, 0], [29, 5, 0], [29, 7, 0], [0, 7, 0]]), fill: "#2a1f5c" }, s);
    const lights = el("g", { class: "guides" }, s);
    for (let x = 0.5; x < 29; x += 1.5) { const [a, b] = iso(x, 6); el("circle", { cx: a, cy: b, r: 1.6, fill: "#a78bfa" }, lights); }

    this.floorLayer = el("g", {}, s);
    for (const [key, room] of Object.entries(ROOMS)) this.drawRoom(key, room);
    this.agentLayer = el("g", {}, s);
    this.fxLayer = el("g", {}, s);
    this.labelLayer = el("g", {}, s);
    for (const [key, room] of Object.entries(ROOMS)) this.drawLabel(key, room);
  }

  drawRoom(key, room) {
    const g = el("g", { class: "room", id: "room-" + key }, this.floorLayer);
    const [x0, y0, x1, y1] = room.rect, c = room.color;
    el("polygon", { points: pts([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]), fill: shade("#1e1648", 0), stroke: c, "stroke-opacity": .35 }, g);
    // floor tiles
    const tiles = el("g", { stroke: c, "stroke-opacity": .08 }, g);
    for (let x = x0 + 1; x < x1; x++) el("polyline", { points: pts([[x, y0], [x, y1]]), fill: "none" }, tiles);
    for (let y = y0 + 1; y < y1; y++) el("polyline", { points: pts([[x0, y], [x1, y]]), fill: "none" }, tiles);
    el("polygon", { class: "room-glow", points: pts([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]), fill: c, opacity: 0 }, g);
    // Back walls (only where they face the viewer's back).
    el("polygon", { points: pts([[x0, y0, 0], [x1, y0, 0], [x1, y0, WALL], [x0, y0, WALL]]), fill: "url(#wall)" }, g);
    el("polygon", { points: pts([[x0, y0, 0], [x0, y1, 0], [x0, y1, WALL], [x0, y0, WALL]]), fill: "url(#wall)" }, g);
    el("polyline", { points: pts([[x0, y1, WALL], [x0, y0, WALL], [x1, y0, WALL]]), fill: "none", stroke: c, "stroke-width": 2, filter: "url(#glow)", class: "neon" }, g);
    this.furnish(key, g, room);
  }

  screen(g, key, x, y, w, color) {
    const scr = el("polygon", { points: pts([[x, y, 10], [x + w, y, 10], [x + w, y, 20], [x, y, 20]]), fill: color, opacity: .75, class: "screen" }, g);
    (this.screens[key] ||= []).push(scr);
  }

  furnish(key, g, room) {
    const c = room.color;
    if (key === "lab") {
      for (const x of [1.4, 3.4, 5.4]) { box(g, x, 1.2, 1.2, 0.7, 8, "#334155"); this.screen(g, key, x, 1.2, 1.2, c); }
      box(g, 6.6, 3.6, 0.8, 0.8, 14, "#0e7490");
    } else if (key === "den") {
      box(g, 8.6, 1, 2.6, 1, 6, "#7c2d12"); box(g, 8.6, 1, 2.6, 0.35, 12, "#9a3412");
      el("ellipse", { cx: iso(10, 3.6)[0], cy: iso(10, 3.6)[1], rx: 22, ry: 9, fill: "#78350f", opacity: .8 }, g);
    } else if (key === "obs") {
      const [cx, cy] = iso(15, 2);
      el("ellipse", { cx, cy: cy - 18, rx: 34, ry: 17, fill: "none", stroke: c, "stroke-width": 1.5, opacity: .8 }, g);
      el("ellipse", { cx, cy: cy - 18, rx: 20, ry: 10, fill: "none", stroke: c, "stroke-width": 1, opacity: .6 }, g);
      el("line", { x1: cx, y1: cy - 18, x2: cx + 30, y2: cy - 24, stroke: c, "stroke-width": 2, class: "sweep",
        style: `transform-origin:${cx}px ${cy - 18}px` }, g);
      el("circle", { cx: cx + 12, cy: cy - 22, r: 2.5, fill: "#fbbf24", class: "blip" }, g);
      el("circle", { cx: cx - 16, cy: cy - 14, r: 2, fill: "#f472b6", class: "blip", style: "animation-delay:1s" }, g);
    } else if (key === "riskbay") {
      box(g, 19.2, 1.2, 1.2, 0.7, 8, "#334155"); this.screen(g, key, 19.2, 1.2, 1.2, c);
      const [cx, cy] = iso(21.8, 2);
      el("path", { d: `M${cx} ${cy - 40} l14 6 v12 c0 10 -7 16 -14 20 c-7 -4 -14 -10 -14 -20 v-12 z`, fill: c, opacity: .25, stroke: c, filter: "url(#glow)" }, g);
    } else if (key === "bridge") {
      el("polygon", { points: pts([[24, 0, 8], [28.5, 0, 8], [28.5, 0, 30], [24, 0, 30]]), fill: "#38bdf8", opacity: .18 }, g);
      box(g, 25.5, 1.6, 1, 1, 9, "#475569"); box(g, 24.2, 1.2, 1, 0.6, 7, "#334155"); box(g, 27.2, 1.2, 1, 0.6, 7, "#334155");
      this.screen(g, key, 24.2, 1.2, 1, "#38bdf8"); this.screen(g, key, 27.2, 1.2, 1, "#38bdf8");
    } else if (key === "floor") {
      for (const [x, y] of [[1, 8], [2.8, 8], [4.6, 8], [1.9, 9.8], [4, 9.8], [6, 9.8]]) {
        box(g, x, y, 1.2, 0.6, 6, "#3f3f46"); this.screen(g, key, x, y, 1.2, ["#22c55e", "#ef4444", "#f59e0b"][Math.floor(x) % 3]);
      }
    } else if (key === "council") {
      const [cx, cy] = iso(13.3, 9.6);
      el("ellipse", { cx, cy: cy - 5, rx: 62, ry: 30, fill: "#14532d", stroke: "#fbbf24", "stroke-width": 3 }, g);
      el("ellipse", { cx, cy: cy - 5, rx: 22, ry: 10, fill: "#67e8f9", opacity: .25, class: "holo" }, g);
      this.holo = el("text", { x: cx, y: cy - 1, "text-anchor": "middle", class: "holo-text" }, g);
    } else if (key === "exec") {
      box(g, 19, 7.8, 3, 0.6, 7, "#3f3f46");
      this.screen(g, key, 19, 7.8, 3, c);
      this.ticker = el("text", { x: iso(20.5, 7.8)[0], y: iso(20.5, 7.8)[1] - 12, "text-anchor": "middle", class: "ticker-text" }, g);
    } else if (key === "vault") {
      const [cx, cy] = iso(26, 7.2);
      el("circle", { cx, cy: cy - 18, r: 17, fill: "#334155", stroke: c, "stroke-width": 3, filter: "url(#glow)" }, g);
      el("circle", { cx, cy: cy - 18, r: 6, fill: "none", stroke: c, "stroke-width": 2 }, g);
      for (const [x, y] of [[24, 10.6], [24.6, 11], [28, 10.4]]) box(g, x, y, 0.7, 0.5, 5, "#ca8a04");
    }
  }

  drawLabel(key, room) {
    // Top-row rooms: label above the back corner. Bottom-row rooms: just outside the hull
    // below the room's front corner, so labels never sit on top of other rooms.
    const [x0, y0, x1, y1] = room.rect;
    const top = y0 < 5;
    const [lx, ly] = top ? iso(x0 + 0.6, y0, WALL + 8) : iso(x1 - 0.5, y1 + 1.6);
    const g = el("g", { class: "label", transform: `translate(${lx},${ly})` }, this.labelLayer);
    const bg = el("rect", { x: 0, y: -13, rx: 8, height: 18, fill: "#0b0820", stroke: room.color, "stroke-opacity": .8 }, g);
    const t = el("text", { x: 8, y: 0, class: "label-text", fill: "#e5e7eb" }, g);
    t.textContent = `${room.icon} ${room.name.toUpperCase()}`;
    const st = el("text", { x: 8, y: 13, class: "label-status", fill: room.color }, g);
    this.labels[key] = { g, bg, t, st, top, lx, ly };
    this.fitLabel(key);
  }

  fitLabel(key) {
    // Sized from character counts (monospace) - measuring fails while the view is hidden.
    const L = this.labels[key];
    const w = Math.max([...L.t.textContent].length * 5.9 + 4, L.st.textContent.length * 5.2) + 16;
    if (!L.top) L.g.setAttribute("transform", `translate(${L.lx - w / 2},${L.ly})`);
    L.bg.setAttribute("width", w);
    L.bg.setAttribute("height", L.st.textContent ? 32 : 18);
  }

  status(key, text) {
    if (!this.labels[key]) return;
    this.labels[key].st.textContent = text || "";
    this.fitLabel(key);
  }

  pulse(key) {
    const g = this.svg.querySelector(`#room-${key} .room-glow`);
    if (!g) return;
    g.classList.remove("pulse"); void g.getBBox(); g.classList.add("pulse");
  }

  flashScreen(key) {
    const list = this.screens[key] || [];
    const s = list[Math.floor(Math.random() * list.length)];
    if (!s) return;
    s.classList.remove("flash"); void s.getBBox(); s.classList.add("flash");
  }

  setHolo(text) { if (this.holo) this.holo.textContent = text; }
  setTicker(text) { if (this.ticker) this.ticker.textContent = text; }

  // ------------------------------------------------------------ agents
  addAgent(a) {
    const [room, i] = HOME[a.id] || ["council", 0];
    const [x, y] = SPOTS[room][i];
    const g = el("g", { class: "agent", "data-id": a.id }, this.agentLayer);
    g.innerHTML = sprite(a, 0.62);
    const tag = el("text", { y: 12, "text-anchor": "middle", class: "nametag", fill: a.color }, g);
    tag.textContent = a.name.split(" ")[0];
    const t = el("title", {}, g); t.textContent = `${a.name} - ${a.role}`;
    this.agents[a.id] = { a, g, x, y, room, path: [], walking: false };
    this.place(this.agents[a.id]);
  }

  place(ag) {
    const [sx, sy] = iso(ag.x, ag.y);
    ag.g.setAttribute("transform", `translate(${sx.toFixed(1)},${sy.toFixed(1)})`);
  }

  // Send an agent to a room (optionally a specific spot index).
  go(id, room, spot) {
    const ag = this.agents[id];
    if (!ag) return;
    const list = SPOTS[room];
    const idx = spot ?? (room === "council" ? COUNCIL_SEAT[id] ?? 0 : (HOME[id]?.[0] === room ? HOME[id][1] : 0));
    const [tx, ty] = list[Math.min(idx, list.length - 1)];
    if (ag.room === room) ag.path = [[tx, ty]];
    else ag.path = [[ag.x, 6], [tx, 6], [tx, ty]];
    ag.room = room;
  }

  home(id) { const h = HOME[id]; if (h) this.go(id, h[0], h[1]); }

  setStatus(id, status) {
    const ag = this.agents[id];
    if (!ag) return;
    ag.g.classList.toggle("thinking", status === "thinking");
    ag.g.classList.toggle("speaking", status === "speaking");
  }

  // Screen position (relative to the stage element) for a speech bubble above an agent.
  anchor(id, stage) {
    const ag = this.agents[id];
    if (!ag) return null;
    const [sx, sy] = iso(ag.x, ag.y);
    const p = this.svg.createSVGPoint(); p.x = sx; p.y = sy - 44;
    const m = this.svg.getScreenCTM(); if (!m) return null;
    const q = p.matrixTransform(m), r = stage.getBoundingClientRect();
    return { x: q.x - r.left, y: q.y - r.top };
  }

  coins(from = "exec", to = "vault", color = "#fbbf24") {
    const a = SPOTS[from][0], b = SPOTS[to][0];
    for (let i = 0; i < 10; i++) {
      const c = el("circle", { r: 3.2, fill: color, stroke: "#92400e", "stroke-width": 1 }, this.fxLayer);
      this.particles.push({ c, a, b, t: -i * 0.08 });
    }
  }

  tick(now) {
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    const speed = 5.5; // tiles per second
    for (const ag of Object.values(this.agents)) {
      if (ag.path.length) {
        const [tx, ty] = ag.path[0];
        const dx = tx - ag.x, dy = ty - ag.y, d = Math.hypot(dx, dy);
        if (d < 0.05) { ag.x = tx; ag.y = ty; ag.path.shift(); }
        else { const k = Math.min(1, (speed * dt) / d); ag.x += dx * k; ag.y += dy * k; }
        this.place(ag);
      }
      const walking = ag.path.length > 0;
      if (walking !== ag.walking) { ag.walking = walking; ag.g.classList.toggle("walking", walking); }
    }
    // Depth-sort agents so nearer ones draw on top.
    // (Only when the order changes - moving DOM nodes restarts their CSS animations.)
    const order = Object.values(this.agents).sort((p, q) => (p.x + p.y) - (q.x + q.y));
    const key = order.map((ag) => ag.a.id).join();
    if (key !== this.orderKey) { this.orderKey = key; order.forEach((ag) => this.agentLayer.appendChild(ag.g)); }
    // Coin particles arc from the execution desk to the vault.
    this.particles = this.particles.filter((p) => {
      p.t += dt * 0.9;
      if (p.t < 0) return true;
      if (p.t >= 1) { p.c.remove(); return false; }
      const x = p.a[0] + (p.b[0] - p.a[0]) * p.t, y = p.a[1] + (p.b[1] - p.a[1]) * p.t;
      const [sx, sy] = iso(x, y, 30 + Math.sin(p.t * Math.PI) * 60);
      p.c.setAttribute("cx", sx); p.c.setAttribute("cy", sy);
      return true;
    });
    requestAnimationFrame((t) => this.tick(t));
  }
}
