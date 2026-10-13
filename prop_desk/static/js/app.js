// Prop Desk client: listens to desk events and directs the spaceship, the Council and the panels.
import { Ship } from "./ship.js";
import { Council, VOTERS } from "./council.js";
import { candleChart, probabilityBar, equityChart } from "./charts.js";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (el, v) => { el.textContent = (v > 0 ? "+" : "") + money(v); el.className = v > 0 ? "pos" : v < 0 ? "neg" : ""; };

const PHASES = ["housekeeping", "research", "pitch", "bear", "round1", "debate", "vote", "risk", "execute", "markets"];
const COUNCIL_PHASES = new Set(["bear", "round1", "debate", "vote"]);
const RESEARCHERS = ["scout", "newshound", "oracle"];

const S = {
  agents: {}, traders: [], settings: {}, idea: null, votes: { 1: {}, 2: {} }, levels: {}, edge: null,
  msgCount: 0, view: "ship", replaying: false, labSpot: 0, board: [],
};
let ship, council;

// ------------------------------------------------------------------ setup
async function init() {
  const st = await (await fetch("/api/state")).json();
  S.settings = st.settings;
  st.agents.forEach((a) => (S.agents[a.id] = a));
  S.traders = st.agents.filter((a) => a.kind === "trader").map((a) => a.id);
  ship = new Ship($("#ship"), st.agents);
  council = new Council($("#council"), st.agents);
  $("#mode").textContent = st.mode;
  $("#mode").className = "badge" + (st.mode.startsWith("Claude") ? " ai" : "");
  const k = st.settings;
  $("#rules").textContent =
    `Council approves at a score of ±${k.threshold} with ${k.quorum}+ traders using ${k.min_families}+ kinds of evidence. ` +
    `Risk: ${k.risk_per_trade_pct}% per trade, ${k.max_position_pct}% max position, ${k.max_open_positions} positions, ` +
    `${k.max_daily_loss_pct}% daily loss limit. ${k.allow_shorts ? "Shorts allowed." : "Long-only."}` +
    (k.kalshi ? ` Prediction markets only when no stock trade gets done, ${k.kalshi_min_edge}¢+ edge after fees.` : "") +
    (st.autorun_minutes ? ` Auto-runs every ${st.autorun_minutes} min.` : "");
  renderPortfolio(st.portfolio);
  renderBoard(st.scorecard);
  setRunning(st.running);
  ship.status("council", "in recess");
  ship.status("vault", money(st.portfolio.total_pnl) + " total");
  connect();
}

function connect() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onmessage = (m) => handle(JSON.parse(m.data));
  ws.onclose = () => setTimeout(connect, 1500);
}

// ------------------------------------------------------------------ events
function handle(e) {
  S.replaying = !!e.replay;
  switch (e.type) {
    case "session":
      $("#mode").textContent = e.mode;
      $("#datasrc").textContent = `data: ${e.data_source}` + (e.markets_source !== "off" ? ` · markets: ${e.markets_source}` : "");
      break;
    case "regime": {
      const r = $("#regime");
      r.classList.remove("hidden");
      r.textContent = e.risk_on ? "RISK-ON" : "RISK-OFF";
      r.className = "badge " + (e.risk_on ? "on" : "off");
      r.title = `SPY ${e.spy} is ${e.spy_vs_200d > 0 ? "above" : "below"} its 200-day average (${e.spy_vs_200d}%)`;
      break;
    }
    case "phase": onPhase(e); break;
    case "tool": onTool(e); break;
    case "candidate": onCandidate(e); break;
    case "status": ship.setStatus(e.agent, e.status); council.setStatus(e.agent, e.status); break;
    case "message": addMessage(e); break;
    case "vote": onVote(e); break;
    case "tally": onTally(e); break;
    case "edge": onEdge(e); break;
    case "verdict":
      council.stamp(e.label);
      ship.status("council", `${e.label.toLowerCase()}: ${short(e.symbol)}`);
      break;
    case "order": onOrder(e); break;
    case "portfolio": renderPortfolio(e); break;
    case "scorecard": renderBoard(e.board); break;
    case "notice": addNotice(e.text); break;
    case "error": addNotice(e.text); setRunning(false); break;
    case "session_end":
      $("#cost").textContent = "$" + (e.cost_usd || 0).toFixed(2);
      setRunning(false);
      break;
  }
}

const short = (sym) => (sym && sym.length > 14 ? sym.slice(0, 13) + "…" : sym || "");

function onPhase(e) {
  const p = e.phase, idx = PHASES.indexOf(p);
  document.querySelectorAll("#pipeline div").forEach((d) => {
    const i = PHASES.indexOf(d.dataset.p);
    d.classList.toggle("active", i === idx);
    d.classList.toggle("done", idx > -1 && i < idx && d.dataset.p !== "markets");
  });
  if (p === "markets") $("#pipeline .mk").classList.add("used");
  if (p === "housekeeping") { setRunning(true); $("#pipeline .mk").classList.remove("used"); }

  const all = Object.keys(S.agents);
  const sym = short(e.symbol);
  const contract = S.idea?.kind === "contract";
  const presenter = contract ? "oracle" : "scout";
  switch (p) {
    case "housekeeping":
      all.forEach((id) => ship.home(id));
      ship.go("risk", "vault");
      ship.status("vault", "settling the books");
      camera("ship");
      break;
    case "review":
      ship.go("chair", "council"); S.traders.forEach((id) => ship.go(id, "council"));
      ship.status("council", "position review");
      break;
    case "research":
      S.traders.forEach((id) => ship.home(id)); ship.home("chair");
      ship.go("scout", "lab", 0); ship.go("newshound", "lab", 1);
      ship.status("lab", "hunting for ideas…"); ship.pulse("lab");
      camera("ship");
      break;
    case "markets":
      ship.go("oracle", "obs", 0); ship.go("chair", "obs", 1);
      ship.status("obs", "scanning Kalshi…"); ship.pulse("obs");
      camera("ship");
      break;
    case "pitch":
      [presenter, "newshound", "chair", ...S.traders].forEach((id) => ship.go(id, "council"));
      if (!contract) ship.home("oracle");
      ship.status("council", `pitch: ${sym}`); ship.pulse("council");
      camera("ship");
      break;
    case "bear":
      ship.go("bear", "council");
      ship.status("den", "on the attack");
      camera("council");
      break;
    case "round1": ship.status("council", `blind vote: ${sym}`); camera("council"); break;
    case "debate": ship.status("council", `debating ${sym}`); camera("council"); break;
    case "vote": ship.status("council", `tallying ${sym}`); break;
    case "risk":
      ship.go("risk", "riskbay"); ship.status("riskbay", `sizing ${sym}`); ship.pulse("riskbay");
      setTimeout(() => camera("ship"), S.replaying ? 0 : 1600);
      break;
    case "execute":
      ship.go("risk", "exec"); ship.status("exec", `sending ${sym}`); ship.pulse("exec");
      camera("ship");
      break;
    case "idle":
      setTimeout(() => {
        all.forEach((id) => ship.home(id));
        ship.status("council", "in recess"); ship.status("lab", ""); ship.status("obs", "");
        ship.status("riskbay", ""); ship.status("exec", ""); ship.status("den", "");
        camera("ship");
      }, S.replaying ? 0 : 2500);
      break;
  }
}

function onTool(e) {
  const id = e.agent, room = id === "oracle" ? "obs" : "lab";
  ship.go(id, room, room === "lab" ? (S.labSpot = (S.labSpot + 1) % 3) : 0);
  ship.flashScreen(room);
  const what = Object.values(e.input || {}).join(" ");
  ship.status(room, `${S.agents[id]?.name.split(" ")[0]}: ${e.tool.replace(/_/g, " ")}${what ? " " + what : ""}`.slice(0, 42));
}

function onCandidate(e) {
  S.idea = e; S.votes = { 1: {}, 2: {} }; S.levels = {}; S.edge = null;
  council.setSubject(e);
  ship.setHolo(e.kind === "contract" ? "KALSHI" : e.symbol);
  $("#ideaTitle").textContent = e.kind === "contract" ? "· prediction market" : `· ${e.symbol}`;
  divider(e.kind === "contract" ? `KALSHI · ${e.title}` : `${e.symbol} · $${e.price}`);
  renderIdea();
}

function onVote(e) {
  S.votes[e.round][e.agent] = e;
  const n = Object.keys(S.votes[e.round]).length;
  if (e.round === 2 && n === 1) council.clearVotes();
  council.vote(e.agent, e.stance, e.conviction, S.replaying);
  // Blind vote: paddles stay face-down until every trader has voted, then flip together.
  if (n === VOTERS.length && !S.replaying) setTimeout(() => council.reveal(), 500);
  renderIdea();
}

function onTally(e) {
  council.tally(e);
  if (S.idea) S.idea[`tally${e.round}`] = e;
  renderIdea();
}

function onEdge(e) {
  S.edge = e;
  council.metrics(`Council ${e.prob}% YES · buy ${e.side} @ ${e.ask}¢ · edge ${e.edge > 0 ? "+" : ""}${e.edge}¢`);
  renderIdea();
}

function onOrder(e) {
  if (e.kind !== "contract" && S.idea?.symbol === e.symbol) {
    S.levels = { entry: e.price, stop: e.stop, target: e.target };
    renderIdea();
  }
  ship.setTicker(`${e.action} ${e.qty} ${short(e.kind === "contract" ? "contracts" : e.symbol)}`);
  if (!S.replaying) ship.coins();
}

// ------------------------------------------------------------------ camera + bubbles
function camera(v, manual = false) {
  if (!manual && !$("#autocam").checked) return;
  S.view = v;
  $("#view-ship").classList.toggle("hidden", v !== "ship");
  $("#view-council").classList.toggle("hidden", v !== "council");
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === v));
  clearBubble();
}

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => camera(t.dataset.view, true)));

let bubble = null;
function showBubble(agentId, text) {
  clearBubble();
  const a = S.agents[agentId];
  if (!a) return;
  const el = document.createElement("div");
  el.className = "bubble";
  el.style.setProperty("--c", a.color);
  const t = text.replace(/^\[(LONG|SHORT|PASS|YES|NO) ?\d*\]\s*/, (m, s) => s + ": ");
  el.innerHTML = `<b>${esc(a.name.split(" ")[0])}</b> ${esc(t.length > 170 ? t.slice(0, 167) + "…" : t)}`;
  $("#bubbles").appendChild(el);
  bubble = { el, agentId, until: performance.now() + 4800 };
  placeBubble();
}
function clearBubble() { if (bubble) bubble.el.remove(); bubble = null; }
function placeBubble() {
  if (!bubble) return;
  if (performance.now() > bubble.until) { clearBubble(); return; }
  const stage = $("#stage");
  const p = (S.view === "ship" ? ship : council).anchor(bubble.agentId, stage);
  if (!p) { bubble.el.style.display = "none"; }
  else {
    bubble.el.style.display = "";
    const w = bubble.el.offsetWidth, maxX = stage.clientWidth - w - 6;
    bubble.el.style.left = Math.max(6, Math.min(maxX, p.x - w / 2)) + "px";
    bubble.el.style.top = Math.max(6, p.y - bubble.el.offsetHeight - 6) + "px";
    bubble.el.style.setProperty("--tail", Math.max(14, Math.min(w - 14, p.x - Math.max(6, Math.min(maxX, p.x - w / 2)))) + "px");
  }
  requestAnimationFrame(placeBubble);
}

// ------------------------------------------------------------------ chat
function addMessage(e) {
  const a = S.agents[e.agent] || { name: e.agent, color: "#999", initials: "?", role: "" };
  const chat = $("#chat");
  const stick = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 80;
  const el = document.createElement("div");
  el.className = "msg k-" + e.kind;
  el.style.setProperty("--c", a.color);
  const tag = e.kind === "rebuttal" ? " · debate" : e.kind === "bear" ? " · devil's advocate" : e.kind === "thinking" ? " · thinking" : "";
  el.innerHTML = `<div class="av">${esc(a.initials)}</div><div>
      <div class="who"><b>${esc(a.name)}</b> <span class="muted">${esc(a.role)}${tag}</span></div>
      <div class="txt"><span class="body"></span><div class="detail"></div></div></div>`;
  chat.appendChild(el);
  const body = el.querySelector(".body"), detail = el.querySelector(".detail");
  const html = esc(e.text).replace(/^\[(LONG|SHORT|PASS|YES|NO) ?(\d*)\]/, (_, s, c) => `<span class="tag ${s}">${s} ${c}</span>`);
  const finish = () => {
    body.innerHTML = html;
    if (e.detail) detail.textContent = e.detail; else detail.remove();
    if (stick) chat.scrollTop = chat.scrollHeight;
  };
  if (S.replaying) finish();
  else {
    // Typewriter effect, capped so long messages don't hold up the floor.
    const plain = e.text, per = Math.max(2, Math.ceil(plain.length / 60));
    let i = 0;
    const iv = setInterval(() => {
      i += per;
      body.textContent = plain.slice(0, i);
      if (stick) chat.scrollTop = chat.scrollHeight;
      if (i >= plain.length) { clearInterval(iv); finish(); }
    }, 14);
    if (e.kind !== "thinking") showBubble(e.agent, e.text);
  }
  $("#chatCount").textContent = `· ${++S.msgCount} messages`;
}

function divider(label) {
  const d = document.createElement("div");
  d.className = "divider";
  d.textContent = label.length > 60 ? label.slice(0, 59) + "…" : label;
  $("#chat").appendChild(d);
}

function addNotice(t) {
  const d = document.createElement("div");
  d.className = "notice";
  d.textContent = "⚠ " + t;
  $("#chat").appendChild(d);
}

// ------------------------------------------------------------------ panels
function voteCell(v, contract) {
  if (!v) return `<td class="muted">…</td>`;
  const extra = contract && v.stance !== "PASS" ? ` <span class="muted">${v.probability}%</span>` : "";
  return `<td><span class="tag ${v.stance}">${v.stance}</span>${v.stance === "PASS" ? "" : v.conviction}${extra}</td>`;
}

function renderIdea() {
  const e = S.idea;
  if (!e) return;
  const contract = e.kind === "contract";
  let head, chart;
  if (contract) {
    const c = e.contract;
    head = `<div class="idea-head"><b>${esc(e.title)}</b>
      <span class="muted">${esc(c.category)} · closes ${esc(String(c.close_time).slice(0, 16).replace("T", " "))}</span></div>`;
    chart = probabilityBar(c, e.fair, S.edge?.prob) +
      (S.edge ? `<div class="edge ${S.edge.edge >= S.settings.kalshi_min_edge ? "pos" : "neg"}">Buy ${S.edge.side} @ ${S.edge.ask}¢ · edge ${S.edge.edge > 0 ? "+" : ""}${S.edge.edge}¢ per contract after fees</div>` : "");
  } else {
    const f = e.fundamentals || {};
    head = `<div class="idea-head"><b>${esc(e.symbol)}</b> <span class="mono">$${e.price}</span>
      <span class="muted">${esc(f.sector || "")}${f.next_earnings ? " · earnings " + esc(String(f.next_earnings).slice(0, 10)) : ""}</span></div>`;
    chart = e.candles?.length ? candleChart(e.candles, e.cloud || [], S.levels, $("#idea").clientWidth) : "";
  }
  const t2 = e.tally2 || e.tally1;
  const gauge = t2 ? `<div class="gauge"><div class="g-track"><div class="g-band" style="left:${50 - S.settings.threshold * 50}%;width:${S.settings.threshold * 100}%"></div>
      ${e.tally1 ? `<div class="g-ghost" style="left:${50 + e.tally1.score * 50}%"></div>` : ""}
      <div class="g-needle" style="left:${50 + t2.score * 50}%"></div></div>
      <div class="g-scale"><span>${contract ? "NO" : "SHORT"}</span><span>R${e.tally2 ? 2 : 1} score ${t2.score > 0 ? "+" : ""}${t2.score.toFixed(2)}</span><span>${contract ? "YES" : "LONG"}</span></div></div>` : "";
  const rows = S.traders.map((id) => {
    const a = S.agents[id], v1 = S.votes[1][id], v2 = S.votes[2][id];
    const changed = v1 && v2 && v1.stance !== v2.stance;
    return `<tr><td class="n"><b style="color:${a.color}">${esc(a.name.split(" ")[0])}</b> <span class="muted small">${esc(a.role.replace(" Trader", ""))}</span></td>
      ${voteCell(v1, contract)}${voteCell(v2, contract)}<td class="changed">${changed ? "changed mind" : ""}</td></tr>`;
  }).join("");
  $("#idea").innerHTML = `${head}${chart}${gauge}
    <table class="votes"><thead><tr><th>Trader</th><th>Round 1</th><th>Round 2</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderBoard(rows) {
  if (!rows) return;
  S.board = rows;
  council.board(rows, S.agents);
  const graded = rows.reduce((n, r) => n + r.calls, 0), hits = rows.reduce((n, r) => n + r.hits, 0);
  $("#hitrate").textContent = graded ? Math.round((hits / graded) * 100) + "%" : "–";
  $("#lbNote").textContent = graded ? `${graded} graded calls` : "calls are graded after a few days, or when a market settles";
  const sorted = [...rows].sort((a, b) => (b.accuracy ?? -1) - (a.accuracy ?? -1) || b.calls - a.calls);
  $("#leaderboard").innerHTML = sorted.map((r, i) => {
    const a = S.agents[r.agent], pct = r.accuracy == null ? null : Math.round(r.accuracy * 100);
    const streak = r.streak >= 2 ? `<span class="hot">🔥 ${r.streak}</span>` : r.streak <= -2 ? `<span class="cold">❄ ${-r.streak}</span>` : "";
    const form = (r.recent || []).map((x) => `<i class="${x === "W" ? "w" : "l"}"></i>`).join("");
    return `<div class="lb-row" style="--c:${a.color}">
      <div class="rank">${i + 1}</div><div class="av">${esc(a.initials)}</div>
      <div class="who"><b>${esc(a.name)}</b><span class="muted small">${esc(a.role)}</span><div class="form">${form}</div></div>
      <div class="acc"><div class="bar"><i style="width:${pct ?? 0}%"></i></div><span>${pct == null ? "no calls yet" : pct + "% · " + r.calls + " calls"}</span></div>
      <div class="meta"><span title="Return if you'd followed only this trader" class="${r.edge >= 0 ? "pos" : "neg"}">${r.edge >= 0 ? "+" : ""}${r.edge}%</span>
        <span title="Vote weight earned from track record">×${r.weight.toFixed(2)}</span>${streak}</div></div>`;
  }).join("");
}

function renderPortfolio(p) {
  $("#equity").textContent = money(p.equity);
  signed($("#daypnl"), p.day_pnl);
  signed($("#totpnl"), p.total_pnl);
  ship?.status("vault", `${p.total_pnl >= 0 ? "+" : ""}${money(p.total_pnl)} total`);
  $("#positions tbody").innerHTML = p.positions.length
    ? p.positions.map((x) => `<tr><td><b>${esc(x.symbol)}</b></td><td>${x.side}</td><td>${x.qty}</td>
        <td>${x.entry}</td><td>${(+x.last).toFixed(2)}</td><td>${x.stop}</td><td>${x.target}</td>
        <td class="${x.pnl >= 0 ? "pos" : "neg"}">${money(x.pnl)}</td></tr>`).join("")
    : `<tr><td colspan="8" class="empty">No open stock positions.</td></tr>`;
  $("#contracts tbody").innerHTML = (p.contracts || []).length
    ? p.contracts.map((c) => `<tr><td class="n" title="${esc(c.title)}">${esc(c.title.length > 38 ? c.title.slice(0, 37) + "…" : c.title)}</td>
        <td><span class="tag ${c.side}">${c.side}</span></td><td>${c.qty}</td><td>${c.price}¢</td><td>${c.last}¢</td>
        <td>${esc(String(c.close_time).slice(5, 16).replace("T", " "))}</td><td class="${c.pnl >= 0 ? "pos" : "neg"}">${money(c.pnl)}</td></tr>`).join("")
    : `<tr><td colspan="7" class="empty">No open prediction-market positions.</td></tr>`;
  $("#blotter").innerHTML = p.trades.length
    ? p.trades.map((t) => {
        const name = t.kind === "contract" ? (t.title || t.symbol) : t.symbol;
        const px = t.kind === "contract" ? `${t.price}¢` : t.price;
        const tail = t.pnl !== undefined ? ` · <span class="${t.pnl >= 0 ? "pos" : "neg"}">${money(t.pnl)}</span> (${esc(t.reason)})`
          : t.stop ? ` · stop ${t.stop} · target ${t.target}` : t.fee ? ` · fee $${t.fee}` : "";
        return `<div>${esc(t.time.slice(5, 16).replace("T", " "))} · <b>${esc(t.action)}</b> ${t.qty} ${esc(name)} @ ${px}${tail}</div>`;
      }).join("")
    : `<div class="empty">No trades yet.</div>`;
  const r = equityChart($("#equityChart"), p.history || []);
  $("#eqNote").textContent = r ? `us ${r.us >= 0 ? "+" : ""}${r.us.toFixed(2)}% · SPY ${r.spy == null ? "–" : (r.spy >= 0 ? "+" : "") + r.spy.toFixed(2) + "%"}` : "";
}

// ------------------------------------------------------------------ controls
function setRunning(r) {
  $("#runbtn").disabled = r;
  $("#resetbtn").disabled = r;
  $("#runbtn").textContent = r ? "Council in session…" : "Run session";
}

$("#runform").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const raw = $("#symbols").value.trim();
  const symbols = raw ? raw.split(/[\s,]+/).filter(Boolean) : null;
  $("#chat").innerHTML = "";
  S.msgCount = 0;
  setRunning(true);
  const r = await (await fetch("/api/session", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbols }),
  })).json();
  if (!r.started) { addNotice(r.reason); setRunning(false); }
});

$("#resetbtn").addEventListener("click", async () => {
  if (!confirm("Reset the paper account to starting cash and clear all positions?")) return;
  await fetch("/api/reset", { method: "POST" });
});

window.addEventListener("resize", () => { renderIdea(); });

init();
