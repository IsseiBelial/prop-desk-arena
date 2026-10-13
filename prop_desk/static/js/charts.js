// Small dependency-free SVG charts.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Candlesticks with the Ichimoku cloud shaded behind them and optional entry/stop/target lines.
export function candleChart(candles, cloud, levels = {}, width = 600) {
  const W = Math.max(300, Math.round(width)), H = 250, P = { l: 8, r: 58, t: 10, b: 18 };
  const vals = candles.flatMap((c) => [c[2], c[3]]).concat(cloud.flat().filter((v) => v != null));
  for (const k of ["stop", "target", "entry"]) if (levels[k]) vals.push(levels[k]);
  const lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo) * 0.04;
  const y = (v) => P.t + (1 - (v - (lo - pad)) / (hi - lo + 2 * pad)) * (H - P.t - P.b);
  const n = candles.length, step = (W - P.l - P.r) / n, x = (i) => P.l + step * (i + 0.5);

  // Cloud: fill green where span A > B (bullish), red otherwise, segment by segment.
  let cloudSvg = "";
  for (let i = 1; i < n; i++) {
    const [a0, b0] = cloud[i - 1] || [], [a1, b1] = cloud[i] || [];
    if ([a0, b0, a1, b1].some((v) => v == null)) continue;
    const col = a1 >= b1 ? "#22c55e" : "#ef4444";
    cloudSvg += `<polygon points="${x(i - 1)},${y(a0)} ${x(i)},${y(a1)} ${x(i)},${y(b1)} ${x(i - 1)},${y(b0)}" fill="${col}" opacity=".16"/>`;
  }
  const bodies = candles.map((c, i) => {
    const [, o, h, l, cl] = c, up = cl >= o, col = up ? "#22c55e" : "#ef4444";
    const top = y(Math.max(o, cl)), bot = y(Math.min(o, cl));
    return `<line x1="${x(i)}" x2="${x(i)}" y1="${y(h)}" y2="${y(l)}" stroke="${col}" stroke-width="1"/>
      <rect x="${x(i) - step * 0.32}" y="${top}" width="${Math.max(1, step * 0.64)}" height="${Math.max(1, bot - top)}" fill="${col}"/>`;
  }).join("");
  const line = (v, col, label) => v ? `<line x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}" stroke="${col}" stroke-dasharray="5 4" stroke-width="1.3"/>
      <rect x="${W - P.r + 2}" y="${y(v) - 9}" width="${P.r - 4}" height="18" rx="4" fill="${col}"/>
      <text x="${W - P.r / 2}" y="${y(v) + 4}" text-anchor="middle" class="lvl">${label}</text>` : "";
  const last = candles[n - 1][4];
  const ticks = [lo, (lo + hi) / 2, hi].map((v) => `<text x="${W - P.r + 6}" y="${y(v) + 4}" class="axis">${v.toFixed(v > 100 ? 0 : 2)}</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="candles" style="aspect-ratio:${W}/${H}">
    ${cloudSvg}${bodies}${ticks}
    ${line(levels.target, "#22c55e", "TGT")}${line(levels.entry || last, "#e5e7eb", levels.entry ? "ENTRY" : "LAST")}${line(levels.stop, "#ef4444", "STOP")}
    <text x="${P.l}" y="${H - 4}" class="axis">${esc(candles[0][0])}</text>
    <text x="${W - P.r}" y="${H - 4}" text-anchor="end" class="axis">${esc(candles[n - 1][0])}</text>
  </svg>`;
}

// Market price vs. our estimates, on a 0-100% bar.
export function probabilityBar(c, fair, council) {
  const mark = (v, cls, label) => v == null ? "" :
    `<div class="pb-mark ${cls}" style="left:${v}%"><span>${label} ${Math.round(v)}%</span></div>`;
  return `<div class="pb">
    <div class="pb-track"><div class="pb-spread" style="left:${c.yes_bid}%;width:${Math.max(1, c.yes_ask - c.yes_bid)}%"></div>
      ${mark(c.implied, "mkt", "Market")}${mark(fair, "res", "Nova")}${mark(council, "cou", "Council")}</div>
    <div class="pb-scale"><span>0% · NO</span><span>50%</span><span>YES · 100%</span></div></div>`;
}

// Equity curve vs. the benchmark, both as % change from the first point.
export function equityChart(svg, history) {
  const W = Math.max(300, Math.round(svg.clientWidth || 600)), H = 200;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const P = { l: 40, r: 10, t: 12, b: 20 };
  const pts = history.filter((h) => h.equity != null);
  if (pts.length < 2) {
    svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" class="axis">The curve starts after your first session or two.</text>`;
    return null;
  }
  const e0 = pts[0].equity, b0 = pts.find((h) => h.bench)?.bench;
  const us = pts.map((h) => (h.equity / e0 - 1) * 100);
  const spy = pts.map((h) => (b0 && h.bench ? (h.bench / b0 - 1) * 100 : null));
  const all = us.concat(spy.filter((v) => v != null), [0]);
  let lo = Math.min(...all), hi = Math.max(...all);
  if (hi - lo < 0.5) { hi += 0.25; lo -= 0.25; }
  const x = (i) => P.l + (i / (pts.length - 1)) * (W - P.l - P.r);
  const y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
  const path = (arr) => arr.map((v, i) => (v == null ? "" : `${i && arr[i - 1] != null ? "L" : "M"}${x(i)},${y(v)}`)).join("");
  const area = `M${x(0)},${y(0)} ${us.map((v, i) => `L${x(i)},${y(v)}`).join(" ")} L${x(us.length - 1)},${y(0)} Z`;
  const up = us[us.length - 1] >= 0;
  svg.innerHTML = `<defs><linearGradient id="eqg" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="${up ? "#a78bfa" : "#ef4444"}" stop-opacity=".45"/><stop offset="1" stop-color="#a78bfa" stop-opacity="0"/></linearGradient></defs>
    <line x1="${P.l}" x2="${W - P.r}" y1="${y(0)}" y2="${y(0)}" stroke="#334155" stroke-dasharray="3 3"/>
    <text x="${P.l - 6}" y="${y(hi) + 4}" text-anchor="end" class="axis">${hi.toFixed(1)}%</text>
    <text x="${P.l - 6}" y="${y(lo) + 4}" text-anchor="end" class="axis">${lo.toFixed(1)}%</text>
    <path d="${area}" fill="url(#eqg)"/>
    <path d="${path(spy)}" fill="none" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="4 3"/>
    <path d="${path(us)}" fill="none" stroke="#a78bfa" stroke-width="2.4"/>`;
  return { us: us[us.length - 1], spy: spy[spy.length - 1] };
}

// The race: every faction's equity (% from its first point) on one chart, plus SPY.
export function raceChart(svg, series) {
  // series: [{id, name, color, history}]
  const W = Math.max(300, Math.round(svg.clientWidth || 600)), H = 210;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const P = { l: 42, r: 70, t: 12, b: 18 };
  const lines = series.map((s) => {
    const h = (s.history || []).filter((p) => p.equity != null);
    const e0 = h[0]?.equity;
    return { ...s, pts: h.map((p) => ({ t: Date.parse(p.t), v: (p.equity / e0 - 1) * 100, b: p.bench })) };
  }).filter((s) => s.pts.length);
  if (!lines.length || lines.every((s) => s.pts.length < 2)) {
    svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" class="axis">The race starts after a session or two.</text>`;
    return null;
  }
  // SPY from the longest history.
  const ref = lines.reduce((a, b) => (b.pts.length > a.pts.length ? b : a));
  const b0 = ref.pts.find((p) => p.b)?.b;
  const spy = b0 ? ref.pts.filter((p) => p.b).map((p) => ({ t: p.t, v: (p.b / b0 - 1) * 100 })) : [];
  const all = lines.flatMap((s) => s.pts).concat(spy);
  const t0 = Math.min(...all.map((p) => p.t)), t1 = Math.max(...all.map((p) => p.t)) || t0 + 1;
  let lo = Math.min(0, ...all.map((p) => p.v)), hi = Math.max(0, ...all.map((p) => p.v));
  if (hi - lo < 0.5) { hi += 0.25; lo -= 0.25; }
  const x = (t) => P.l + ((t - t0) / (t1 - t0 || 1)) * (W - P.l - P.r);
  const y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
  const path = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
  const tags = lines.map((s) => { const p = s.pts[s.pts.length - 1];
    return `<circle cx="${x(p.t)}" cy="${y(p.v)}" r="3.5" fill="${s.color}"/>
      <text x="${x(p.t) + 6}" y="${y(p.v) + 4}" class="race-tag" fill="${s.color}">${p.v >= 0 ? "+" : ""}${p.v.toFixed(2)}%</text>`; }).join("");
  svg.innerHTML = `<line x1="${P.l}" x2="${W - P.r}" y1="${y(0)}" y2="${y(0)}" stroke="#334155" stroke-dasharray="3 3"/>
    <text x="${P.l - 6}" y="${y(hi) + 4}" text-anchor="end" class="axis">${hi.toFixed(1)}%</text>
    <text x="${P.l - 6}" y="${y(lo) + 4}" text-anchor="end" class="axis">${lo.toFixed(1)}%</text>
    ${spy.length > 1 ? `<path d="${path(spy)}" fill="none" stroke="#94a3b8" stroke-width="1.4" stroke-dasharray="4 3"/>` : ""}
    ${lines.map((s) => `<path d="${path(s.pts)}" fill="none" stroke="${s.color}" stroke-width="2.4"/>`).join("")}${tags}`;
  return true;
}
