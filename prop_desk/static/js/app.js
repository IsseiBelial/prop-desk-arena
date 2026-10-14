// Prop Desk Arena client: turns desk events into an RTS battlefield, faction scenes and panels.
import { BattleMap, BASES, drawMinimap } from "./map.js";
import { Council, VOTERS } from "./council.js";
import { Conclave, HiveMind } from "./scenes.js";
import { candleChart, probabilityBar, raceChart } from "./charts.js";
import { unitSprite } from "./sprites.js";
import { Voice } from "./voice.js";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
// Small accounts show cents; big ones round to dollars.
const kmoney = (v) => (v < 0 ? "-$" : "$") + (Math.abs(v) >= 1e6 ? (Math.abs(v) / 1e6).toFixed(2) + "M"
  : Math.abs(v) >= 1e4 ? Math.round(Math.abs(v)).toLocaleString() : Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const sgn = (v, d = 2) => (v > 0 ? "+" : "") + Number(v).toFixed(d);
const short = (s) => (s && s.length > 16 ? s.slice(0, 15) + "…" : s || "");

const PHASE_TEXT = {
  housekeeping: "Upkeep - settling bets, checking stops, grading old calls",
  review: "Reviewing open positions", research: "Scouting the mineral fields", markets: "Heading for the gas vents (prediction markets)",
  pitch: "A pitch reaches the War Room", bear: "The devil's advocate attacks", round1: "Round 1 - blind vote",
  debate: "Debate - round 2", vote: "Tallying the vote", risk: "Quartermaster sizing the trade", execute: "Executing", idle: "Round complete",
};
const SCENE_PHASES = new Set(["bear", "round1", "debate", "vote"]);

const S = {
  agents: {}, factions: [], fmap: {}, standings: [], portfolios: {}, idea: null, votes: { 1: {}, 2: {} },
  models: {}, swarm: null, levels: {}, edge: null, turn: null, view: "map", unitTab: "legion", bookTab: "legion",
  msgCount: 0, replaying: false, focus: null, labSpot: 0, running: false, settings: {}, season: null,
};
let battle, council, conclave, hive;
const voice = new Voice();

// ------------------------------------------------------------------ setup
async function init() {
  const st = await (await fetch("/api/state")).json();
  S.settings = st.settings;
  st.agents.forEach((a) => (S.agents[a.id] = a));
  S.factions = st.factions;
  st.factions.forEach((f) => (S.fmap[f.id] = f));
  S.portfolios = st.portfolios;
  battle = new BattleMap($("#map"), st.agents);
  const legionAgents = st.agents.filter((a) => a.faction === "legion" || a.id === "risk");
  council = new Council($("#warroom"), legionAgents, { sign: "WAR ROOM" });
  conclave = new Conclave($("#conclave"), S.agents);
  hive = new HiveMind($("#hive"), S.agents, st.agents.filter((a) => a.style === "drone").map((a) => a.id));
  $("#mode").textContent = st.mode;
  $("#mode").className = "badge" + (st.mode.startsWith("Claude") ? " ai" : "");
  $("#cost").textContent = "$" + (st.cost_usd || 0).toFixed(2);
  const k = st.settings;
  $("#rules").textContent =
    `Same rules for every faction: ${k.risk_per_trade_pct}% risk per trade, ${k.max_position_pct}% max position, ` +
    `${k.max_open_positions} positions, ${k.max_daily_loss_pct}% daily loss limit, ${kmoney(k.starting_cash)} starting capital each. ` +
    `${k.allow_shorts ? "Shorts allowed." : "Long-only."}` + (k.kalshi ? ` Gas (prediction markets) only when a faction made no stock trade; ${k.kalshi_min_edge}¢+ edge after fees.` : "") +
    (st.autorun_minutes ? ` Auto-runs every ${st.autorun_minutes} min.` : "");
  buildTabs();
  S.season = st.season;
  renderStandings(st.standings);
  for (const f of S.factions) claims(f.id);
  renderBook();
  renderRace();
  setRunning(st.running);
  showPortrait(S.agents.chair, "Iron Legion, reporting for duty.", "Press R to run a session. 1/2/3 jump to each faction's room.");
  setInterval(() => drawMinimap($("#minimap"), battle), 300);
  connect();
}

function buildTabs() {
  const mk = (box, cur, on) => {
    box.innerHTML = S.factions.map((f) => `<button class="subtab ${f.id === cur ? "active" : ""}" data-f="${f.id}" style="--c:${f.color}">${esc(f.name)}</button>`).join("");
    box.querySelectorAll(".subtab").forEach((b) => b.addEventListener("click", () => { on(b.dataset.f); box.querySelectorAll(".subtab").forEach((x) => x.classList.toggle("active", x === b)); }));
  };
  mk($("#unitTabs"), S.unitTab, (f) => { S.unitTab = f; renderUnits(); });
  mk($("#bookTabs"), S.bookTab, (f) => { S.bookTab = f; renderBook(); });
}

function connect() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onmessage = (m) => handle(JSON.parse(m.data));
  ws.onclose = () => setTimeout(connect, 1500);
}

// ------------------------------------------------------------------ events
function handle(e) {
  S.replaying = !!e.replay;
  const f = e.faction;
  switch (e.type) {
    case "session":
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
    case "map":
      battle.setMinerals(e.minerals); battle.setGas(e.gas);
      for (const fac of S.factions) claims(fac.id);
      break;
    case "turn": onTurn(e); break;
    case "turn_end": unitsOf(f).forEach((id) => battle.goHome(id)); battle.clearDroneVotes(); break;
    case "phase": onPhase(e); break;
    case "tool": onTool(e); break;
    case "warp": onWarp(e); break;
    case "swarm": for (const [d, sym] of Object.entries(e.assign)) { const same = Object.entries(e.assign).filter(([, s]) => s === sym); battle.toField(d, sym, same.findIndex(([x]) => x === d), same.length); } break;
    case "candidate": onCandidate(e); break;
    case "status": battle.setStatus(e.agent, e.status); sceneFor(f)?.setStatus(e.agent, e.status); break;
    case "message": addMessage(e); break;
    case "vote": onVote(e); break;
    case "tally": council.tally(e); if (S.idea) S.idea[`tally${e.round}`] = e; renderIdea(); break;
    case "model": S.models[e.agent] = e; conclave.model(e); renderIdea(); break;
    case "convergence": conclave.converge(e); if (S.idea) S.idea.convergence = e; renderIdea(); break;
    case "swarm_vote":
      S.swarm = e; hive.swarmVote(e);
      for (const [d, v] of Object.entries(e.votes)) battle.droneVote(d, v);
      renderIdea();
      break;
    case "edge":
      S.edge = e;
      if (f === "legion") council.metrics(`Council ${e.prob}% YES · buy ${e.side} @ ${e.ask}¢ · edge ${sgn(e.edge, 1)}¢`);
      else sceneFor(f)?.edge?.(e);
      renderIdea();
      break;
    case "verdict":
      sceneFor(f)?.stamp(e.label);
      if (e.approved) { battle.harvest(f, e.symbol); voice.blip("trade"); }
      setBanner(f, `${e.label}: ${short(e.symbol)}`);
      if (!S.replaying) setTimeout(() => camera("map"), 2600);
      break;
    case "order":
      if (e.kind !== "contract" && S.idea?.symbol === e.symbol && S.idea?.faction === f) { S.levels = { entry: e.price, stop: e.stop, target: e.target }; renderIdea(); }
      break;
    case "contest":
      battle.contest(e.symbol);
      addNotice(`Contested field: ${e.symbol} - ` + Object.entries(e.verdicts).map(([k, v]) => `${S.fmap[k]?.name} ${v.toLowerCase()}`).join(", "));
      callout(S.agents.risk, voice.line({ faction: "neutral" }, "contest"), `${e.symbol} is contested`);
      break;
    case "portfolio": S.portfolios[f] = e; claims(f); if (f === S.bookTab) renderBook(); renderResources(); break;
    case "season_end": {
      const w = S.fmap[e.winner];
      addNotice(`Season ${e.number} is over! ` + e.results.map((r) => `${S.fmap[r.faction]?.name || r.faction} ${sgn(r.return_pct)}%`).join(" · ") + ". Profits roll into the next season.");
      if (w) { const lead = S.agents[w.leader]; callout(lead, voice.line(lead, "win"), `${w.name} wins season ${e.number}`, true); voice.blip("win"); }
      break;
    }
    case "standings": if (e.season) S.season = e.season; renderStandings(e.factions); $("#cost").textContent = "$" + (e.cost_usd || 0).toFixed(2); renderRace(); break;
    case "notice": addNotice(e.text); break;
    case "error": addNotice(e.text); setRunning(false); break;
    case "session_end": {
      $("#cost").textContent = "$" + (e.cost_usd || 0).toFixed(2);
      setRunning(false);
      const top = S.standings[0];
      if (top && top.calls) { const lead = S.agents[S.fmap[top.id]?.leader]; callout(lead, voice.line(lead, "win"), `${top.name} leads the standings`, true); }
      break;
    }
  }
}

const unitsOf = (f) => Object.values(S.agents).filter((a) => a.faction === f).map((a) => a.id);
const sceneFor = (f) => ({ legion: council, ascendant: conclave, brood: hive })[f];

function onTurn(e) {
  S.turn = e.faction;
  setBanner(e.faction, e.motto);
  const lead = S.agents[S.fmap[e.faction]?.leader];
  callout(lead, voice.line(lead, "turn"), `${e.name}'s turn`, true);
  divider(`${e.name.toUpperCase()} · TURN`, S.fmap[e.faction]?.color);
  camera("map");
}

function onPhase(e) {
  const f = e.faction || S.turn, p = e.phase;
  if (p === "idle") {
    S.turn = null;
    setBanner(null, "Round complete. Press R for another.");
    if (!S.replaying) setTimeout(() => { Object.keys(S.agents).forEach((id) => battle.goHome(id)); battle.select(null); camera("map"); }, 2500);
    return;
  }
  if (p === "housekeeping") { setRunning(true); battle.moveTo("risk", BASES.neutral.x + 40, BASES.neutral.y + 4); }
  setBanner(f, (PHASE_TEXT[p] || p) + (e.symbol ? ` · ${short(e.symbol)}` : ""));
  if (f === "legion" && p === "pitch") {
    const presenter = S.idea?.kind === "contract" ? "oracle" : "scout";
    [presenter, "newshound", "chair", ...VOTERS].forEach((id, i) => battle.moveTo(id, BASES.legion.x - 120 + (i % 5) * 26, BASES.legion.y + 8 + Math.floor(i / 5) * 22));
  }
  if (f === "legion" && p === "bear") battle.moveTo("bear", BASES.legion.x - 60, BASES.legion.y + 58);
  if (SCENE_PHASES.has(p) && f) camera(f);
  if (p === "risk") setTimeout(() => camera("map"), S.replaying ? 0 : 1200);
  if (p === "risk" && S.idea) battle.moveTo("risk", BASES.neutral.x, BASES.neutral.y - 30);
}

function onTool(e) {
  const sym = e.input?.symbol || e.input?.ticker;
  if (sym && battle.spot(sym)) battle.toField(e.agent, sym, (S.labSpot = (S.labSpot + 1) % 4), 4);
  const a = S.agents[e.agent];
  if (a) callout(a, voice.line(a, "research"), `${e.tool.replace(/_/g, " ")}${sym ? " · " + sym : ""}`);
}

function onWarp(e) {
  const syms = e.symbols || [];
  if (!syms.length) return;
  if (S.replaying) return;
  syms.slice(0, 4).forEach((sym, i) => setTimeout(() => battle.toField(e.agent, sym, 0, 1), i * 420));
  setTimeout(() => battle.goHome(e.agent), syms.slice(0, 4).length * 420 + 300);
  if (e.agent === "vael") { const a = S.agents.thalor; callout(a, voice.line(a, "research"), "The seers read every field"); }
}

function onCandidate(e) {
  S.idea = e; S.votes = { 1: {}, 2: {} }; S.models = {}; S.swarm = null; S.levels = {}; S.edge = null;
  battle.select(e.symbol);
  sceneFor(e.faction)?.setSubject(e);
  if (e.faction === "ascendant") ["vael", "kyth", "oriel", "lumen"].forEach((id, i) => battle.toField(id, e.symbol, i, 4));
  $("#ideaTitle").textContent = `· ${S.fmap[e.faction]?.name || ""}`;
  divider(e.kind === "contract" ? `GAS · ${short(e.title)}` : `${e.symbol} · $${e.price}`, S.fmap[e.faction]?.color);
  renderIdea();
}

function onVote(e) {
  S.votes[e.round][e.agent] = e;
  const n = Object.keys(S.votes[e.round]).length;
  if (e.round === 2 && n === 1) council.clearVotes();
  council.vote(e.agent, e.stance, e.conviction, S.replaying);
  if (n === VOTERS.length && !S.replaying) setTimeout(() => council.reveal(), 500);
  renderIdea();
}

// ------------------------------------------------------------------ camera, banner, bubbles, portrait
function camera(v, manual = false) {
  if (!manual && !$("#autocam").checked) return;
  S.view = v;
  for (const k of ["map", "legion", "ascendant", "brood"]) $("#view-" + k).classList.toggle("hidden", k !== v);
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === v));
  clearBubble();
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => camera(t.dataset.view, true)));

function setBanner(f, text) {
  const b = $("#banner"), fac = S.fmap[f];
  b.style.setProperty("--fc", fac?.color || "#5eead4");
  $("#bannerFaction").textContent = fac ? fac.name : (S.running ? "ARENA" : seasonText() || "AWAITING ORDERS");
  $("#bannerPhase").textContent = text || "";
  b.classList.remove("flash"); void b.offsetWidth; b.classList.add("flash");
}

let bubble = null;
function showBubble(agentId, text) {
  clearBubble();
  const a = S.agents[agentId];
  if (!a) return;
  const el = document.createElement("div");
  el.className = "bubble";
  el.style.setProperty("--c", a.color);
  const t = text.replace(/^\[(LONG|SHORT|PASS|YES|NO) ?\d*\]\s*/, (m, s) => s + ": ");
  el.innerHTML = `<b>${esc(a.name.split(" ").slice(-1)[0])}</b> ${esc(t.length > 160 ? t.slice(0, 157) + "…" : t)}`;
  $("#bubbles").appendChild(el);
  bubble = { el, agentId, until: performance.now() + 4800 };
  placeBubble();
}
function clearBubble() { if (bubble) bubble.el.remove(); bubble = null; }
function placeBubble() {
  if (!bubble) return;
  if (performance.now() > bubble.until) { clearBubble(); return; }
  const stage = $("#stage");
  const src = S.view === "map" ? battle : sceneFor(S.view);
  const p = src?.anchor(bubble.agentId, stage);
  if (!p) bubble.el.style.display = "none";
  else {
    bubble.el.style.display = "";
    const w = bubble.el.offsetWidth, maxX = stage.clientWidth - w - 6, left = Math.max(6, Math.min(maxX, p.x - w / 2));
    bubble.el.style.left = left + "px";
    bubble.el.style.top = Math.max(6, p.y - bubble.el.offsetHeight - 6) + "px";
    bubble.el.style.setProperty("--tail", Math.max(14, Math.min(w - 14, p.x - left)) + "px");
  }
  requestAnimationFrame(placeBubble);
}

function showPortrait(a, quote, subtitle) {
  if (!a) return;
  const sc = { human: 1.15, seer: 1.05, drone: 2.2, queen: 1.0 }[a.style] || 1.1;
  $("#portraitSvg").innerHTML = unitSprite(a, sc);
  $("#portraitFrame").style.setProperty("--pc", a.color);
  $("#pname").textContent = a.name;
  $("#pname").style.setProperty("--pc", a.color);
  $("#prole").textContent = `${a.role}${S.fmap[a.faction] ? " · " + S.fmap[a.faction].name : a.faction === "neutral" ? " · Neutral" : ""}`;
  $("#quote").textContent = quote ? `"${quote}"` : "";
  $("#subtitle").textContent = subtitle || "";
  const fr = $("#portraitFrame");
  fr.classList.add("talking"); clearTimeout(showPortrait.t); showPortrait.t = setTimeout(() => fr.classList.remove("talking"), 2500);
}

function callout(a, line, subtitle, force = false) {
  if (!a || S.replaying) return;
  showPortrait(a, line, subtitle);
  voice.speak(a, line, force);
}

function calloutKind(e) {
  const tag = (e.text.match(/^\[(LONG|SHORT|PASS|YES|NO)/) || [])[1];
  if (e.kind === "opinion" || e.kind === "rebuttal") return tag === "PASS" ? "pass" : tag === "SHORT" || tag === "NO" ? "no" : "yes";
  if (e.kind === "decision") return e.approved ? "approve" : "reject";
  if (e.kind === "risk") return e.approved === false ? "veto" : "approve";
  if (["research", "pitch"].includes(e.kind)) return "research";
  return "any";
}

// ------------------------------------------------------------------ comms
function addMessage(e) {
  const a = S.agents[e.agent] || { name: e.agent, color: "#999", initials: "?", role: "" };
  const fac = S.fmap[e.faction];
  const chat = $("#chat");
  const stick = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 80;
  const el = document.createElement("div");
  el.className = "msg k-" + e.kind;
  el.style.setProperty("--c", a.color);
  if (fac) el.style.setProperty("--fc", fac.color);
  const tag = e.kind === "rebuttal" ? " · debate" : e.kind === "bear" ? " · devil's advocate" : e.kind === "thinking" ? " · thinking" : "";
  el.innerHTML = `<div class="av">${esc(a.initials)}</div><div>
      <div class="who"><b>${esc(a.name)}</b> <span class="muted">${esc(a.role)}${tag}</span>${fac && a.faction === "neutral" ? `<span class="fx">${esc(fac.name)}</span>` : ""}</div>
      <div class="txt"><span class="body"></span><div class="detail"></div></div></div>`;
  chat.appendChild(el);
  while (chat.children.length > 400) chat.firstChild.remove();
  const body = el.querySelector(".body"), detail = el.querySelector(".detail");
  const html = esc(e.text).replace(/^\[(LONG|SHORT|PASS|YES|NO) ?(\d*)\]/, (_, s, c) => `<span class="tag ${s}">${s} ${c}</span>`);
  const finish = () => {
    body.innerHTML = html;
    if (e.detail) detail.textContent = e.detail; else detail.remove();
    if (stick) chat.scrollTop = chat.scrollHeight;
  };
  if (S.replaying) finish();
  else {
    const plain = e.text, per = Math.max(2, Math.ceil(plain.length / 60));
    let i = 0;
    const iv = setInterval(() => {
      i += per; body.textContent = plain.slice(0, i);
      if (stick) chat.scrollTop = chat.scrollHeight;
      if (i >= plain.length) { clearInterval(iv); finish(); }
    }, 14);
    if (e.kind !== "thinking") showBubble(e.agent, e.text);
    const kind = calloutKind(e);
    const line = voice.line(a.faction === "neutral" ? { ...a } : a, kind);
    callout(a, line || "", e.text, ["decision"].includes(e.kind));
  }
  $("#chatCount").textContent = `· ${++S.msgCount} messages`;
}

function divider(label, color) {
  const d = document.createElement("div");
  d.className = "divider";
  if (color) d.style.setProperty("--fc", color);
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
function claims(f) {
  const p = S.portfolios[f];
  if (!p || !battle) return;
  battle.setClaims(f, [...(p.positions || []).map((x) => x.symbol), ...(p.contracts || []).map((c) => c.ticker)]);
}

function seasonText() {
  const z = S.season;
  return z && z.number ? `SEASON ${z.number} · DAY ${z.day} OF ${z.days}` : "";
}

function seasonReturn(fid, equity) {
  const start = S.season?.start_equity?.[fid];
  return start ? (equity / start - 1) * 100 : 0;
}

function renderResources() {
  $("#resources").innerHTML = S.factions.map((f) => {
    const p = S.portfolios[f.id] || { equity: 0, total_pnl: 0, positions: [], contracts: [] };
    const r = seasonReturn(f.id, p.equity);
    return `<div class="res" style="--c:${f.color}" title="${esc(f.name)}: equity, return this season, open positions">
      <span class="ico">◆</span><span class="eq">${kmoney(p.equity)}</span>
      <span class="${r >= 0 ? "pos" : "neg"}">${sgn(r)}%</span>
      <span class="sup">${(p.positions || []).length + (p.contracts || []).length}/${S.settings.max_open_positions}</span></div>`;
  }).join("");
}

function renderStandings(rows) {
  if (!rows) return;
  S.standings = rows;
  renderResources();
  const lead = rows[0] && rows[0].season_return_pct !== rows[rows.length - 1]?.season_return_pct;
  $("#standings").innerHTML = rows.map((r, i) => {
    const f = S.fmap[r.id] || {};
    const pct = r.accuracy == null ? "–" : Math.round(r.accuracy * 100) + "%";
    const form = (r.recent || []).map((x) => `<i class="${x === "W" ? "w" : "l"}"></i>`).join("");
    const sr = r.season_return_pct ?? 0;
    return `<div class="fcard ${i === 0 && lead ? "leader" : ""}" style="--c:${r.color}">
      <div class="rank">#${i + 1}${i === 0 && lead ? " 👑" : ""}</div>
      <div class="fname">${esc(r.name)}</div><div class="motto">${esc(f.motto || "")}${r.seasons_won ? ` · 🏆×${r.seasons_won}` : ""}</div>
      <div class="season-ret ${sr >= 0 ? "pos" : "neg"}">${sgn(sr)}%<span>this season · from ${kmoney(r.season_start ?? r.equity)}</span></div>
      <div class="nums">
        <div><label>Net calls</label><b>${r.net > 0 ? "+" : ""}${r.net}</b></div>
        <div><label>Hit rate</label><b>${pct}</b></div>
        <div><label>Graded</label><b>${r.calls}</b></div>
        <div><label>Equity</label><b>${kmoney(r.equity)}</b></div>
        <div><label>Total P&amp;L</label><b class="${r.total_pnl >= 0 ? "pos" : "neg"}">${money(r.total_pnl)}</b></div>
        <div><label>AI cost</label><b>$${(r.cost_usd || 0).toFixed(2)}</b></div>
      </div><div class="form">${form}</div></div>`;
  }).join("");
  const hist = S.season?.history || [];
  $("#seasonHistory").innerHTML = (seasonText() ? `<b>${seasonText()}</b> · ends ${esc((S.season.ends || "").slice(0, 10))} · winner = highest return; profits roll over` : "") +
    hist.slice().reverse().map((h) => `<div>Season ${h.number}: ` + h.results.map((x) =>
      `<span style="color:${S.fmap[x.faction]?.color || "#ccc"}">${esc(S.fmap[x.faction]?.name || x.faction)} ${sgn(x.return_pct)}%${x.faction === h.winner ? " 🏆" : ""}</span>`).join(" · ") + `</div>`).join("");
  const graded = rows.reduce((n, r) => n + r.calls, 0);
  $("#lbNote").textContent = graded ? `${graded} graded faction calls` : "calls are graded after a few days, or when a market settles";
  const legion = rows.find((r) => r.id === "legion");
  if (legion) council.board(legion.units, S.agents);
  renderUnits();
}

function renderUnits() {
  const r = S.standings.find((x) => x.id === S.unitTab);
  if (!r) { $("#leaderboard").innerHTML = ""; return; }
  const sorted = [...r.units].sort((a, b) => (b.accuracy ?? -1) - (a.accuracy ?? -1) || b.calls - a.calls || b.weight - a.weight);
  $("#leaderboard").innerHTML = sorted.map((u, i) => {
    const a = S.agents[u.agent] || { name: u.agent, color: "#999", initials: "?", role: "" };
    const pct = u.accuracy == null ? null : Math.round(u.accuracy * 100);
    const streak = u.streak >= 2 ? `<span class="hot">🔥 ${u.streak}</span>` : u.streak <= -2 ? `<span class="cold">❄ ${-u.streak}</span>` : "";
    const form = (u.recent || []).map((x) => `<i class="${x === "W" ? "w" : "l"}"></i>`).join("");
    return `<div class="lb-row" style="--c:${a.color}">
      <div class="rank">${i + 1}</div><div class="av">${esc(a.initials)}</div>
      <div class="who"><b>${esc(a.name)}</b><span class="muted small">${esc(a.role)}</span><div class="form">${form}</div></div>
      <div class="acc"><div class="bar"><i style="width:${pct ?? 0}%"></i></div><span>${pct == null ? "no graded calls" : pct + "% · " + u.calls + " calls"}</span></div>
      <div class="meta"><span title="Return if you'd followed only this unit" class="${u.edge >= 0 ? "pos" : "neg"}">${sgn(u.edge, 1)}%</span>
        <span title="Vote weight earned from the track record">×${u.weight.toFixed(2)}</span>${streak}</div></div>`;
  }).join("");
}

function voteCell(v, contract) {
  if (!v) return `<td class="muted">…</td>`;
  const extra = contract && v.stance !== "PASS" && v.probability != null ? ` <span class="muted">${v.probability}%</span>` : "";
  return `<td><span class="tag ${v.stance}">${v.stance}</span>${v.stance === "PASS" ? "" : v.conviction}${extra}</td>`;
}

function renderIdea() {
  const e = S.idea;
  if (!e) return;
  const fac = S.fmap[e.faction] || {};
  const contract = e.kind === "contract";
  let head, chart = "", body = "";
  if (contract) {
    const c = e.contract;
    head = `<div class="idea-head" style="--fc:${fac.color}"><b>${esc(e.title)}</b><span class="by">${esc(fac.name)}</span>
      <span class="muted">${esc(c.category)} · closes ${esc(String(c.close_time).slice(0, 16).replace("T", " "))}</span></div>`;
    chart = probabilityBar(c, e.fair, S.edge?.prob) +
      (S.edge ? `<div class="edge ${S.edge.edge >= S.settings.kalshi_min_edge ? "pos" : "neg"}">Buy ${S.edge.side} @ ${S.edge.ask}¢ · edge ${sgn(S.edge.edge, 1)}¢ per contract after fees</div>` : "");
  } else {
    const fu = e.fundamentals || {};
    head = `<div class="idea-head" style="--fc:${fac.color}"><b>${esc(e.symbol)}</b> <span class="mono">$${e.price}</span><span class="by">${esc(fac.name)}</span>
      <span class="muted">${esc(fu.sector || "")}${fu.next_earnings ? " · earnings " + esc(String(fu.next_earnings).slice(0, 10)) : ""}</span></div>`;
    chart = e.candles?.length ? candleChart(e.candles, e.cloud || [], S.levels, $("#idea").clientWidth) : "";
  }
  if (e.faction === "legion") {
    const t2 = e.tally2 || e.tally1;
    const gauge = t2 ? `<div class="gauge"><div class="g-track"><div class="g-band" style="left:${50 - S.settings.threshold * 50}%;width:${S.settings.threshold * 100}%"></div>
        ${e.tally1 ? `<div class="g-ghost" style="left:${50 + e.tally1.score * 50}%"></div>` : ""}<div class="g-needle" style="left:${50 + t2.score * 50}%"></div></div>
        <div class="g-scale"><span>${contract ? "NO" : "SHORT"}</span><span>R${e.tally2 ? 2 : 1} score ${sgn(t2.score)}</span><span>${contract ? "YES" : "LONG"}</span></div></div>` : "";
    const rows = VOTERS.map((id) => {
      const a = S.agents[id], v1 = S.votes[1][id], v2 = S.votes[2][id];
      const changed = v1 && v2 && v1.stance !== v2.stance;
      return `<tr><td class="n"><b style="color:${a.color}">${esc(a.name.split(" ")[0])}</b> <span class="muted small">${esc(a.role.replace(" Trader", ""))}</span></td>
        ${voteCell(v1, contract)}${voteCell(v2, contract)}<td class="changed">${changed ? "changed mind" : ""}</td></tr>`;
    }).join("");
    body = gauge + `<table class="votes"><thead><tr><th>Trader</th><th>Round 1</th><th>Round 2</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
  } else if (e.faction === "ascendant" && !contract) {
    const rows = ["vael", "kyth", "oriel", "lumen"].map((id) => {
      const a = S.agents[id], m = S.models[id];
      return `<tr><td class="n"><b style="color:${a.color}">${esc(a.name)}</b> <span class="muted small">${esc(a.role)}</span></td>
        <td>${m ? `<span class="tag ${m.stance}">${m.stance}</span>` : "…"}</td><td>${m ? Math.round(m.p * 100) + "% up" : ""}</td><td>${m ? "×" + m.weight.toFixed(2) : ""}</td></tr>`;
    }).join("");
    const cv = e.convergence;
    body = (cv ? `<div class="edge ${cv.approved ? "pos" : "neg"}">Convergence ${Math.round(cv.p * 100)}% · ${esc(cv.reason)}</div>` : "") +
      `<table class="votes"><thead><tr><th>Seer</th><th>Stance</th><th>P(up)</th><th>Weight</th></tr></thead><tbody>${rows}</tbody></table>`;
  } else if (e.faction === "brood" && !contract && S.swarm) {
    const cells = Object.values(S.swarm.votes).map((v) => `<i class="${v > 0 ? "up" : v < 0 ? "dn" : ""}"></i>`).join("");
    body = `<div class="swarmgrid">${cells}</div><div class="edge ${S.swarm.agree >= 0.68 && S.swarm.active >= 8 ? "pos" : "neg"}">
      ${Object.values(S.swarm.votes).filter((v) => v > 0).length} drones bullish · ${Object.values(S.swarm.votes).filter((v) => v < 0).length} bearish ·
      ${Math.round(S.swarm.agree * 100)}% agreement (needs 68% of 8+ active)</div>`;
  }
  $("#idea").innerHTML = head + chart + body;
}

function renderBook() {
  const p = S.portfolios[S.bookTab];
  if (!p) return;
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
  renderBlotter();
}

function renderBlotter() {
  const all = S.factions.flatMap((f) => (S.portfolios[f.id]?.trades || []).map((t) => ({ ...t, f })));
  all.sort((a, b) => (a.time < b.time ? 1 : -1));
  $("#blotter").innerHTML = all.length ? all.slice(0, 80).map((t) => {
    const name = t.kind === "contract" ? (t.title || t.symbol) : t.symbol;
    const px = t.kind === "contract" ? `${t.price}¢` : t.price;
    const tail = t.pnl !== undefined ? ` · <span class="${t.pnl >= 0 ? "pos" : "neg"}">${money(t.pnl)}</span> (${esc(t.reason)})`
      : t.stop ? ` · stop ${t.stop} · tgt ${t.target}` : t.fee ? ` · fee $${t.fee}` : "";
    return `<div><span class="fx" style="color:${t.f.color}">${esc(t.f.name.replace("The ", "").toUpperCase())}</span> ${esc(t.time.slice(5, 16).replace("T", " "))} · <b>${esc(t.action)}</b> ${t.qty} ${esc(short(name))} @ ${px}${tail}</div>`;
  }).join("") : `<div class="empty">No trades yet.</div>`;
}

function renderRace() {
  const series = S.factions.map((f) => ({ id: f.id, name: f.name, color: f.color, history: S.portfolios[f.id]?.history || [] }));
  raceChart($("#race"), series);
  $("#raceLegend").innerHTML = series.map((s) => `<span><span class="sw" style="background:${s.color}"></span>${esc(s.name)}</span>`).join("") +
    `<span><span class="sw" style="background:#94a3b8"></span>SPY</span>`;
}

// ------------------------------------------------------------------ commands
function setRunning(r) {
  S.running = r;
  $("#runbtn").disabled = r;
  $("#resetbtn").disabled = r;
  $("#runbtn span").textContent = r ? "Running" : "Run";
}

async function run() {
  if (S.running) return;
  const raw = $("#symbols").value.trim();
  const symbols = raw ? raw.split(/[\s,]+/).filter(Boolean) : null;
  $("#chat").innerHTML = ""; S.msgCount = 0;
  setRunning(true);
  voice.blip("select");
  const r = await (await fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbols }) })).json();
  if (!r.started) { addNotice(r.reason); setRunning(false); }
}

$("#runbtn").addEventListener("click", run);
$("#resetbtn").addEventListener("click", async () => {
  if (!confirm("Start a new season? This resets every faction's paper account AND track record.")) return;
  await fetch("/api/reset", { method: "POST" });
});
$("#soundbtn").addEventListener("click", () => {
  const on = voice.toggle();
  $("#soundbtn").classList.toggle("on", on);
  $("#soundbtn i").textContent = on ? "🔊" : "🔇";
  if (on) { voice.blip("select"); voice.speak(S.agents.chair, "Voice systems online.", true); }
});
$("#focusbtn").addEventListener("click", () => { $("#focusform").classList.toggle("hidden"); $("#symbols").focus(); });
$("#focusform").addEventListener("submit", (ev) => { ev.preventDefault(); $("#focusform").classList.add("hidden"); addNotice(`Next round focuses on: ${$("#symbols").value || "the whole watchlist"}`); });
$("#camkey").addEventListener("click", () => { $("#autocam").checked = !$("#autocam").checked; $("#camkey").classList.toggle("on", $("#autocam").checked); });
$("#camkey").classList.toggle("on", true);
document.querySelectorAll("[data-cam]").forEach((b) => b.addEventListener("click", () => camera(b.dataset.cam, true)));
document.addEventListener("keydown", (ev) => {
  if (ev.target.matches("input:not([type=checkbox]), textarea") || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  const btn = document.querySelector(`.commands [data-key="${ev.key.toUpperCase()}"]`);
  if (btn && !btn.disabled) { ev.preventDefault(); btn.click(); }
});
window.addEventListener("resize", () => { renderIdea(); renderRace(); });

init();
