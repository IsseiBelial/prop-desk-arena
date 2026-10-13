// Trading-floor client: renders desk events streamed from the server.
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (el, v) => { el.textContent = (v > 0 ? "+" : "") + money(v); el.className = v > 0 ? "pos" : v < 0 ? "neg" : ""; };

// Where each person sits on the floor (percent of floor width/height).
const SEATS = {
  scout: [20, 12], chair: [50, 10], newshound: [80, 12],
  fundamental: [11, 40], momentum: [13, 76],
  cloud: [89, 40], flow: [87, 76],
  news: [36, 90], risk: [64, 90],
};
const KIND = { researcher: "Research", trader: "Trader", risk: "Risk", chair: "Head of Desk" };
const PHASES = ["research", "pitch", "round1", "debate", "vote", "risk", "execute"];

let agents = {};
let traders = [];
let settings = {};
let votes = {};          // agent -> {1: vote, 2: vote}
let threshold = 0.3;
let msgCount = 0;
const bubbleTimers = {};

// ------------------------------------------------------------------ setup
async function init() {
  const st = await (await fetch("/api/state")).json();
  settings = st.settings;
  threshold = settings.threshold;
  st.agents.forEach((a) => (agents[a.id] = a));
  traders = st.agents.filter((a) => a.kind === "trader").map((a) => a.id);
  $("#mode").textContent = st.mode;
  $("#mode").className = "badge" + (st.mode.startsWith("Claude") ? " ai" : "");
  $("#rules").textContent =
    `Trade needs a consensus score of ±${settings.threshold} and at least ${settings.quorum} traders agreeing. ` +
    `Risk: ${settings.risk_per_trade_pct}% of equity per trade, max ${settings.max_position_pct}% per position, ` +
    `${settings.max_open_positions} positions, ${settings.max_daily_loss_pct}% daily loss limit. ` +
    (settings.allow_shorts ? "Shorts allowed." : "Long-only.");
  renderSeats();
  renderVotes();
  renderPortfolio(st.portfolio);
  setRunning(st.running);
  connect();
}

function renderSeats() {
  const floor = $("#floor");
  Object.values(agents).forEach((a) => {
    const [x, y] = SEATS[a.id] || [50, 50];
    const el = document.createElement("div");
    el.className = "seat" + (y < 30 ? " below" : "") + (x < 25 ? " edge-l" : x > 75 ? " edge-r" : "");
    el.id = "seat-" + a.id;
    el.style.left = x + "%";
    el.style.top = y + "%";
    el.style.setProperty("--c", a.color);
    el.title = a.bio;
    el.innerHTML = `
      <div class="bubble"></div>
      <div class="avatar">${esc(a.initials)}<span class="dots">•••</span></div>
      <div class="kindtag">${KIND[a.kind]}</div>
      <div class="name">${esc(a.name)}</div>
      <div class="role">${esc(a.role)}</div>
      ${a.kind === "trader" ? `<div class="stance" id="st-${a.id}">—</div><div class="conv"><i id="cv-${a.id}"></i></div>` : ""}`;
    floor.appendChild(el);
  });
}

// ------------------------------------------------------------------ socket
function connect() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onmessage = (m) => handle(JSON.parse(m.data));
  ws.onclose = () => setTimeout(connect, 1500);
}

function handle(e) {
  const fx = !e.replay; // skip animations when replaying history
  switch (e.type) {
    case "session":
      $("#mode").textContent = e.mode;
      $("#datasrc").textContent = "data: " + e.data_source;
      break;
    case "phase": setPhase(e.phase); break;
    case "candidate": showCandidate(e); break;
    case "status": setStatus(e.agent, e.status); break;
    case "message": addMessage(e, fx); break;
    case "vote": setVote(e); break;
    case "tally": setTally(e); break;
    case "portfolio": renderPortfolio(e); break;
    case "notice": addNotice(e.text); break;
    case "error": addNotice(e.text); setRunning(false); break;
    case "session_end":
      $("#cost").textContent = "$" + (e.cost_usd || 0).toFixed(2);
      setRunning(false);
      break;
  }
}

// ------------------------------------------------------------------ floor
function setPhase(p) {
  const idx = PHASES.indexOf(p);
  document.querySelectorAll("#pipeline div").forEach((d, i) => {
    d.classList.toggle("active", i === idx);
    d.classList.toggle("done", idx > -1 && i < idx);
  });
  if (p === "research") setRunning(true);
}

function setStatus(id, status) {
  const el = $("#seat-" + id);
  if (!el) return;
  el.classList.toggle("thinking", status === "thinking");
  el.classList.toggle("speaking", status === "speaking");
}

function showCandidate(e) {
  $("#tEmpty").classList.add("hidden");
  $("#tBody").classList.remove("hidden");
  $("#tSym").textContent = e.symbol;
  $("#tPx").textContent = "$" + e.price.toLocaleString();
  $("#tScreen").textContent = "screen " + (e.screen_score > 0 ? "+" : "") + e.screen_score;
  $("#tVerdict").textContent = "";
  $("#tVerdict").className = "t-verdict";
  $("#gScore").textContent = "score —";
  moveNeedle("#gNeedle", 0);
  moveNeedle("#gGhost", 0);
  const b = $("#gBand");
  b.style.left = (50 - threshold * 50) + "%";
  b.style.width = threshold * 100 + "%";
  drawSpark(e.closes);
  document.querySelectorAll(".seat.talking").forEach((s) => s.classList.remove("talking"));
  votes = {};
  traders.forEach((id) => {
    const st = $("#st-" + id);
    st.className = "stance";
    st.textContent = "—";
    $("#cv-" + id).style.width = "0";
  });
  renderVotes();
  $("#voteSym").textContent = e.symbol;
  divider(e.symbol + " · $" + e.price);
}

function drawSpark(closes) {
  const svg = $("#spark");
  if (!closes || closes.length < 2) { svg.innerHTML = ""; return; }
  const lo = Math.min(...closes), hi = Math.max(...closes), n = closes.length;
  const pts = closes.map((c, i) => `${(i / (n - 1)) * 300},${66 - ((c - lo) / (hi - lo || 1)) * 62}`).join(" ");
  const up = closes[n - 1] >= closes[0];
  const col = up ? "#22c55e" : "#ef4444";
  svg.innerHTML = `
    <defs><linearGradient id="sg" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="${col}" stop-opacity=".35"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
    <polygon points="0,70 ${pts} 300,70" fill="url(#sg)"/>
    <polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.6" vector-effect="non-scaling-stroke"/>`;
}

function moveNeedle(sel, score) {
  $(sel).style.left = (50 + Math.max(-1, Math.min(1, score)) * 50) + "%";
}

function setVote(e) {
  votes[e.agent] = votes[e.agent] || {};
  votes[e.agent][e.round] = e;
  const st = $("#st-" + e.agent);
  if (st) {
    st.className = "stance " + e.stance;
    st.textContent = `${e.stance} ${e.stance === "PASS" ? "" : e.conviction}`;
    $("#cv-" + e.agent).style.width = (e.stance === "PASS" ? 0 : e.conviction) + "%";
  }
  renderVotes();
}

function setTally(e) {
  const txt = `R${e.round} score ${(e.score > 0 ? "+" : "") + e.score.toFixed(2)}`;
  $("#gScore").textContent = txt;
  if (e.round === 1) moveNeedle("#gGhost", e.score);
  moveNeedle("#gNeedle", e.score);
  if (e.round === 2) {
    const v = $("#tVerdict");
    v.textContent = (e.approved ? "✔ CONSENSUS: " : "✖ NO TRADE: ") + e.reason;
    v.className = "t-verdict " + (e.approved ? "yes" : "no");
  }
}

// ------------------------------------------------------------------ chat
function addMessage(e, fx) {
  const a = agents[e.agent] || { name: e.agent, color: "#999", initials: "?", role: "" };
  const chat = $("#chat");
  const stick = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 80;
  const text = esc(e.text).replace(/^\[(LONG|SHORT|PASS) ?(\d*)\]/, (_, s, c) => `<span class="tag ${s}">${s} ${c}</span>`);
  const el = document.createElement("div");
  el.className = "msg k-" + e.kind;
  el.style.setProperty("--c", a.color);
  el.innerHTML = `<div class="av">${esc(a.initials)}</div><div>
      <div class="who"><b>${esc(a.name)}</b> <span class="muted">${esc(a.role)}${e.kind === "rebuttal" ? " · debate" : ""}</span></div>
      <div class="txt">${text}${e.detail ? `<div class="detail">${esc(e.detail)}</div>` : ""}</div></div>`;
  chat.appendChild(el);
  if (stick) chat.scrollTop = chat.scrollHeight;
  $("#chatCount").textContent = `· ${++msgCount} messages`;
  if (fx) bubble(e.agent, e.text);
}

function bubble(id, text) {
  const seat = $("#seat-" + id);
  if (!seat) return;
  // One speaker at a time keeps the floor readable.
  document.querySelectorAll(".seat.talking").forEach((s) => s !== seat && s.classList.remove("talking"));
  text = text.replace(/^\[(LONG|SHORT|PASS) ?\d*\]\s*/, (m, st) => st + ": ");
  seat.querySelector(".bubble").textContent = text.length > 160 ? text.slice(0, 157) + "…" : text;
  seat.classList.add("talking");
  clearTimeout(bubbleTimers[id]);
  bubbleTimers[id] = setTimeout(() => seat.classList.remove("talking"), 4500);
}

function divider(label) {
  const d = document.createElement("div");
  d.className = "divider";
  d.textContent = label;
  $("#chat").appendChild(d);
}

function addNotice(t) {
  const d = document.createElement("div");
  d.className = "notice";
  d.textContent = "⚠ " + t;
  $("#chat").appendChild(d);
}

// ------------------------------------------------------------------ tables
function cell(v) {
  if (!v) return `<td class="muted">…</td>`;
  return `<td><span class="tag ${v.stance}">${v.stance}</span>${v.stance === "PASS" ? "" : v.conviction}</td>`;
}

function renderVotes() {
  $("#votes tbody").innerHTML = traders.map((id) => {
    const a = agents[id], v = votes[id] || {};
    const changed = v[1] && v[2] && v[1].stance !== v[2].stance;
    return `<tr><td class="n"><b style="color:${a.color}">${esc(a.name)}</b><br><span class="muted small">${esc(a.role)}</span></td>
      ${cell(v[1])}${cell(v[2])}<td class="changed">${changed ? "changed mind" : ""}</td></tr>`;
  }).join("");
}

function renderPortfolio(p) {
  $("#equity").textContent = money(p.equity);
  signed($("#daypnl"), p.day_pnl);
  signed($("#totpnl"), p.total_pnl);
  $("#positions tbody").innerHTML = p.positions.length
    ? p.positions.map((x) => `<tr><td><b>${esc(x.symbol)}</b></td><td>${x.side}</td><td>${x.qty}</td>
        <td>${x.entry}</td><td>${x.last}</td><td>${x.stop}</td><td>${x.target}</td>
        <td class="${x.pnl >= 0 ? "pos" : "neg"}">${money(x.pnl)}</td></tr>`).join("")
    : `<tr><td colspan="8" class="empty">No open positions.</td></tr>`;
  $("#blotter").innerHTML = p.trades.length
    ? p.trades.map((t) => `<div>${esc(t.time.slice(5, 16).replace("T", " "))} · <b>${esc(t.action)}</b> ${t.qty} ${esc(t.symbol)} @ ${t.price}` +
        (t.pnl !== undefined ? ` · <span class="${t.pnl >= 0 ? "pos" : "neg"}">${money(t.pnl)}</span> (${esc(t.reason)})` : ` · stop ${t.stop} · target ${t.target}`) + `</div>`).join("")
    : `<div class="empty">No trades yet.</div>`;
}

// ------------------------------------------------------------------ controls
function setRunning(r) {
  $("#runbtn").disabled = r;
  $("#resetbtn").disabled = r;
  $("#runbtn").textContent = r ? "Floor in session…" : "Run session";
}

$("#runform").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const raw = $("#symbols").value.trim();
  const symbols = raw ? raw.split(/[\s,]+/).filter(Boolean) : null;
  $("#chat").innerHTML = "";
  msgCount = 0;
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

init();
