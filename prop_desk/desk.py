"""The trading desk: runs one session from housekeeping to execution.

Session flow (every step is streamed to the GUI as an event):
  housekeeping   settle bets, check stops/targets, grade old calls ("Who called it?")
  reviews        the Council re-checks every open position; exits if it now leans against
  stock hunt     research agent finds ideas -> devil's advocate attacks each one
  Council        round 1 blind vote -> round 2 debate -> consensus -> risk -> execute -> memo
  markets        if no stock trade got done, the same process runs on Kalshi prediction markets
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from datetime import date, datetime, timezone
from typing import Awaitable, Callable

from .agents import personas as P
from .agents.brain import ClaudeBrain, has_credentials
from .agents.research_agent import DeskContext, ResearchAgent
from .agents.schemas import (BearCase, ContractOpinion, DeskMemo, MarketIdea, MarketSlate, NewsDigest, Opinion,
                             Pitch, StockSlate)
from .broker import PaperBroker, review_contract, review_trade
from .config import Settings
from .consensus import Decision, tally
from .indicators import compute_features, ichimoku
from .kalshi import Contract, edge_cents, make_kalshi
from .market_data import Snapshot, make_provider
from .scorecard import Scorecard

log = logging.getLogger(__name__)
Emit = Callable[[dict], Awaitable[None]]
_SIGN = {"LONG": 1, "YES": 1, "SHORT": -1, "NO": -1, "PASS": 0}


def _fmt(aid: str, o) -> str:
    p = P.BY_ID[aid]
    prob = f" (thinks {o.probability}% YES)" if hasattr(o, "probability") else ""
    return (f"- {p.name} ({p.role}): {o.stance} conviction {o.conviction}{prob} - \"{o.headline}\"\n"
            f"  reasoning: {o.reasoning}\n  concerns: {'; '.join(o.concerns) or 'none'}")


class Desk:
    def __init__(self, settings: Settings, emit: Emit, broker: PaperBroker | None = None):
        self.s = settings
        self._emit = emit
        self.broker = broker or PaperBroker(settings.ledger_path, settings.starting_cash)
        self.scorecard = Scorecard(settings.scorecard_path, settings.call_horizon_days)
        use_claude = settings.ai_mode == "claude" or (settings.ai_mode == "auto" and has_credentials())
        self.brain = ClaudeBrain(settings.model, settings.effort) if use_claude else None
        self.research_cost = 0.0
        self.provider = None
        self.kalshi = None
        self.running = False
        self.transcript: list[dict] = []
        self.bench_price: float | None = None
        self.risk_on = True

    @property
    def mode(self) -> str:
        return f"Claude ({self.s.model})" if self.brain else "Rules (offline)"

    @property
    def cost_usd(self) -> float:
        return round((self.brain.cost_usd if self.brain else 0) + self.research_cost, 4)

    # ------------------------------------------------------------ plumbing
    async def emit(self, e: dict) -> None:
        e = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **e}
        self.transcript.append(e)
        if self._emit:
            await self._emit(e)

    async def _say(self, agent: str, text: str, kind: str = "chat", symbol: str | None = None, **extra):
        await self.emit({"type": "status", "agent": agent, "status": "speaking"})
        await self.emit({"type": "message", "agent": agent, "text": text, "kind": kind, "symbol": symbol, **extra})
        if not self.brain:
            await asyncio.sleep(self.s.pace)
        await self.emit({"type": "status", "agent": agent, "status": "idle"})

    async def _phase(self, name: str, symbol: str | None = None):
        await self.emit({"type": "phase", "phase": name, "symbol": symbol})

    async def _tool(self, agent: str, tool: str, **inp):
        """Researchers 'walk to a terminal' in the GUI when they use a tool."""
        await self.emit({"type": "tool", "agent": agent, "tool": tool, "input": inp})
        if not self.brain:
            await asyncio.sleep(self.s.pace / 3)

    async def _ask(self, persona: P.Persona, system: str, prompt: str, schema, fallback):
        """Ask Claude; on any failure use the rule-based fallback so the session continues."""
        if not self.brain:
            return fallback()
        await self.emit({"type": "status", "agent": persona.id, "status": "thinking"})
        try:
            out = await self.brain.think(system, prompt, schema)
            if out is not None:
                return out
            note = "Claude declined this request"
        except Exception as e:  # network, rate limit, bad key...
            log.exception("Claude call failed for %s", persona.id)
            note = f"Claude error: {type(e).__name__}"
        await self.emit({"type": "notice", "text": f"{persona.name}: {note} - using rule-based view"})
        return fallback()

    def _weights(self) -> dict[str, float]:
        return {t.id: self.scorecard.weight(t.id, t.weight) for t in P.TRADERS}

    async def _publish_book(self):
        await self.emit({"type": "portfolio", **self.broker.summary()})
        stats = self.scorecard.stats()
        board = [{"agent": t.id, **stats.get(t.id, {"calls": 0, "hits": 0, "accuracy": None, "edge": 0,
                                                     "streak": 0, "recent": [], "passes": 0}),
                  "weight": self.scorecard.weight(t.id, t.weight)} for t in P.TRADERS]
        await self.emit({"type": "scorecard", "board": board})

    def _alert(self, text: str) -> None:
        """Post to a Slack-compatible incoming webhook (Slack, Discord /slack, many others)."""
        if not self.s.alert_webhook:
            return

        def post():
            try:
                req = urllib.request.Request(self.s.alert_webhook, data=json.dumps({"text": text}).encode(),
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10).close()
            except Exception as e:
                log.warning("alert webhook failed: %s", e)

        asyncio.get_running_loop().run_in_executor(None, post)

    def _view(self, persona: P.Persona, snap: Snapshot, feats: dict) -> dict:
        view = {"symbol": snap.symbol, "price": feats["price"], "volatility": feats["volatility"]}
        for group in persona.sees:
            if group == "fundamentals":
                view["fundamentals"] = snap.fundamentals
            elif group == "news":
                view["headlines"] = [{"title": h.title, "source": h.source, "published": h.published,
                                      "summary": h.summary} for h in snap.news]
            elif group in feats:
                view[group] = feats[group]
        return view

    # ------------------------------------------------------------ housekeeping
    async def _housekeeping(self, symbols: list[str] | None):
        loop = asyncio.get_running_loop()
        if self.provider is None:
            self.provider = await loop.run_in_executor(None, make_provider, self.s.data_source, self.s.benchmark)
        if self.kalshi is None and self.s.kalshi_enabled:
            self.kalshi = await loop.run_in_executor(None, make_kalshi, self.s.kalshi_data)
        await self.emit({"type": "session", "mode": self.mode, "data_source": self.provider.name,
                         "markets_source": self.kalshi.name if self.kalshi else "off"})

        bench = await loop.run_in_executor(None, self.provider.snapshot, self.s.benchmark)
        bf = compute_features(bench.bars)
        self.bench_price = bf["price"]
        self.risk_on = (bf["momentum"]["price_vs_sma200_pct"] or 0) > 0
        await self.emit({"type": "regime", "risk_on": self.risk_on, "spy": bf["price"],
                         "spy_vs_200d": bf["momentum"]["price_vs_sma200_pct"]})

        universe = list(dict.fromkeys((symbols or self.s.watchlist) + list(self.broker.positions)
                                      + [c["key"] for c in self.scorecard.pending("stock")]))
        snaps, feats = {}, {}
        for sym in universe:
            try:
                snap = await loop.run_in_executor(None, self.provider.snapshot, sym)
                snaps[sym], feats[sym] = snap, compute_features(snap.bars, bench.bars)
            except Exception as e:
                await self.emit({"type": "notice", "text": f"Skipping {sym}: {e}"})

        for t in self.broker.mark({s: sn.price for s, sn in snaps.items()}):
            await self._say("risk", f"Closed {t['symbol']} - {t['reason']} at {t['price']} (P&L ${t['pnl']:+,.2f}).", "risk")
            self._alert(f"Prop Desk: closed {t['symbol']} ({t['reason']}) P&L ${t['pnl']:+,.2f}")

        if self.kalshi:
            for tk in list(self.broker.contracts):
                try:
                    c = await loop.run_in_executor(None, self.kalshi.market, tk)
                except Exception as e:
                    await self.emit({"type": "notice", "text": f"Couldn't price {tk}: {e}"})
                    continue
                if t := self.broker.mark_contract(c):
                    await self._say("risk", f"\"{t['title']}\" settled {c.result.upper()} - we {t['reason']} "
                                            f"(P&L ${t['pnl']:+,.2f}).", "risk")
                    self._alert(f"Prop Desk: {t['title']} settled {c.result.upper()}, P&L ${t['pnl']:+,.2f}")

        graded = 0
        for call in self.scorecard.pending("stock"):
            if call["key"] in snaps:
                self.scorecard.resolve_stock(call, snaps[call["key"]].price)
                graded += 1
        if self.kalshi:
            for call in self.scorecard.pending("contract", due_only=False):
                try:
                    c = await loop.run_in_executor(None, self.kalshi.market, call["key"])
                except Exception:
                    continue
                if c.result in ("yes", "no"):
                    self.scorecard.resolve_contract(call, c.result)
                    graded += 1
        if graded:
            self.scorecard.save()
            await self._say("chair", f"Graded {graded} old call(s) - check the Who Called It board.", "chair")

        self.broker.snapshot(self.bench_price)
        await self._publish_book()
        return snaps, feats

    async def _review_positions(self, snaps, feats):
        """Rule-based re-vote on each open stock; exit if the Council now leans against it."""
        if not self.broker.positions:
            return
        await self._phase("review")
        for sym, pos in list(self.broker.positions.items()):
            if sym not in snaps:
                continue
            votes = {}
            for t in P.TRADERS:
                sc, _, _ = t.heuristic(feats[sym], snaps[sym])
                stance = "PASS" if abs(sc) < 0.15 else ("LONG" if sc > 0 else "SHORT")
                votes[t.id] = Opinion(stance=stance, conviction=int(min(95, abs(sc) * 100)), headline="",
                                      reasoning="", key_signals=[], concerns=[])
            score = sum(_SIGN[o.stance] * o.conviction for o in votes.values()) / (100 * len(votes))
            held = 1 if pos.side == "LONG" else -1
            if score * held < -0.2:
                t = self.broker.close(sym, snaps[sym].price, "Council voted to exit")
                await self._say("chair", f"Position review: the Council has turned on {sym} ({score:+.2f}). "
                                         f"Exited at {t['price']}, P&L ${t['pnl']:+,.2f}.", "decision", sym)
                self._alert(f"Prop Desk: exited {sym}, P&L ${t['pnl']:+,.2f}")
            else:
                await self._say("chair", f"Position review: still comfortable holding {sym} ({score:+.2f}).", "chair", sym)
        await self._publish_book()

    # ------------------------------------------------------------ stock research
    def _screen_score(self, feats: dict, snap: Snapshot) -> float:
        parts = [P.momentum_view(feats, snap)[0], P.cloud_view(feats, snap)[0], P.money_flow_view(feats, snap)[0]]
        return sum(parts) / len(parts)

    def _rules_pitch(self, sym, snap, feats, score) -> Pitch:
        direction = "LONG" if score >= 0 or not self.s.allow_shorts else "SHORT"
        views = [v(feats, snap) for v in (P.momentum_view, P.cloud_view, P.money_flow_view, P.fundamental_view)]
        sig = [x for v in views for x in v[1]][:4]
        con = [x for v in views for x in v[2]][:3]
        return Pitch(direction=direction, thesis=f"{sym} screens {score:+.2f} on our technical composite.",
                     catalysts=sig or ["composite screen"], risks=con or ["none flagged by the screen"])

    async def _stock_hunt(self, snaps, feats, symbols) -> list[tuple[str, Pitch]]:
        await self._phase("research")
        scout = P.BY_ID["scout"]
        scores = {s: round(self._screen_score(feats[s], snaps[s]), 3) for s in snaps
                  if s in (symbols or self.s.watchlist)}
        pool = [s for s in scores if s not in self.broker.positions]

        if self.brain and not symbols:
            ctx = DeskContext(
                reports={s: {**self._view(scout, snaps[s], feats[s]), "headlines": [h.title for h in snaps[s].news]}
                         for s in scores},
                screen=scores, holdings=list(self.broker.positions))
            agent = ResearchAgent(scout, self.s.model, self.s.effort, self.emit, self.s.research_budget_usd)
            await self.emit({"type": "status", "agent": "scout", "status": "thinking"})
            try:
                slate = await agent.hunt(
                    f"Find up to {self.s.candidates_per_session} stock trades from our watchlist worth bringing to "
                    f"the Council today. The desk is {'long/short' if self.s.allow_shorts else 'long-only'}. "
                    f"Skip names we already hold: {', '.join(self.broker.positions) or 'none'}.",
                    ctx, StockSlate)
            except Exception as e:
                log.exception("stock hunt failed")
                slate = None
                await self.emit({"type": "notice", "text": f"Research agent failed ({type(e).__name__}) - using the screen"})
            finally:
                self.research_cost += agent.cost_usd
            if slate is not None:
                ideas = [(i.symbol.upper(), Pitch(direction=i.direction, thesis=i.thesis, catalysts=i.catalysts,
                                                  risks=i.risks))
                         for i in slate.ideas if i.symbol.upper() in pool][: self.s.candidates_per_session]
                await self._say("scout", slate.summary + (f" Bringing {', '.join(s for s, _ in ideas)} to the Council."
                                                          if ideas else " Nothing worth the Council's time."), "research")
                return ideas

        await self._tool("scout", "screen_watchlist")
        key = (lambda s: abs(scores[s])) if self.s.allow_shorts else (lambda s: scores[s])
        picks = [s.upper() for s in symbols] if symbols else sorted(pool, key=key, reverse=True)[: self.s.candidates_per_session]
        picks = [s for s in picks if s in snaps]
        board = ", ".join(f"{s} {scores[s]:+.2f}" for s in sorted(scores, key=scores.get, reverse=True))
        await self._say("scout", f"Screened {len(scores)} names (setup score: {board}). "
                                 f"Bringing {', '.join(picks) or 'nothing'} to the Council.", "research")
        ideas = []
        for sym in picks:
            await self._tool("scout", "symbol_report", symbol=sym)
            system = (P.SHARED_RULES + "\n\nYou are the desk's quant researcher. Turn the screen output into a crisp "
                      "trade pitch. Be honest about weaknesses." + ("" if self.s.allow_shorts else " The desk is long-only."))
            pitch = await self._ask(scout, system, f"Screen data for {sym}:\n{json.dumps(self._view(scout, snaps[sym], feats[sym]), indent=1)}",
                                    Pitch, lambda sym=sym: self._rules_pitch(sym, snaps[sym], feats[sym], scores.get(sym, 0)))
            ideas.append((sym, pitch))
        return ideas

    async def _news_brief(self, snap: Snapshot) -> NewsDigest:
        hound = P.BY_ID["newshound"]
        await self._tool("newshound", "headlines", symbol=snap.symbol)

        def rules():
            s, sig, con = P.news_view({}, snap)
            mood = "quiet" if not snap.news else "bullish" if s > 0.2 else "bearish" if s < -0.2 else "mixed"
            return NewsDigest(sentiment=mood, summary=f"{len(snap.news)} headlines in the last few days.",
                              market_moving=(sig + con)[:3])

        system = (P.SHARED_RULES + "\n\nYou are the desk's news researcher. Summarize what the headlines mean for "
                  "the stock. If headlines are missing or stale, say the tape is quiet.")
        heads = [{"title": h.title, "source": h.source, "published": h.published, "summary": h.summary} for h in snap.news]
        return await self._ask(hound, system, f"Headlines for {snap.symbol}:\n{json.dumps(heads, indent=1)}", NewsDigest, rules)

    async def _bear_case(self, subject: str, data: dict, pitch_text: str, rules_args) -> BearCase:
        bear = P.BY_ID["bear"]

        def rules():
            args = rules_args() or ["the setup is crowded - everyone can see the same chart"]
            return BearCase(verdict=f"I'd pass on {subject}: {args[0]}.", arguments=args[:4],
                            what_would_prove_me_wrong="The weak signals turning before we enter.")

        system = (P.SHARED_RULES + "\n\nYou are the desk's devil's advocate. Your job is to make the strongest honest "
                  "case AGAINST the pitch, using only the data given. Don't be contrarian for sport - find the real "
                  "weaknesses: valuation, crowded trades, fading momentum, event risk, liquidity, missing evidence.")
        return await self._ask(bear, system, f"{pitch_text}\n\nDATA:\n{json.dumps(data, indent=1, default=str)}",
                               BearCase, rules)

    # ------------------------------------------------------------ the Council (shared by stocks and markets)
    async def _council(self, subject: str, kind: str, brief: str, round1, round2) -> tuple[Decision, dict]:
        await self._phase("round1", subject)
        r1 = await self._vote_round(1, kind, {t.id: round1(t) for t in P.TRADERS})
        weights = self._weights()
        fams = {t.id: t.family for t in P.TRADERS}
        quorum = self.s.min_agreeing_traders if kind == "stock" else self.s.kalshi_quorum
        args = dict(weights=weights, threshold=self.s.approve_threshold, quorum=quorum,
                    allow_shorts=self.s.allow_shorts, families=fams, min_families=self.s.min_families, kind=kind)
        d1 = tally(r1, **args)
        await self.emit({"type": "tally", "round": 1, "symbol": subject, "weights": weights, **d1.to_dict()})

        await self._phase("debate", subject)
        await self._say("chair", f"Round 1 on {subject} scores {d1.score:+.2f}. Bruno, Council - let's hear the disagreements.",
                        "chair", subject)
        r2 = await self._vote_round(2, kind, {
            t.id: round2(t, r1[t.id], {a: o for a, o in r1.items() if a != t.id}) for t in P.TRADERS})
        d2 = tally(r2, **args)
        await self._phase("vote", subject)
        await self.emit({"type": "tally", "round": 2, "symbol": subject, "weights": weights, **d2.to_dict()})
        return d2, r2

    async def _vote_round(self, rnd: int, kind: str, coros: dict) -> dict:
        results: dict = {}
        if self.brain:  # traders think in parallel; announce as each finishes
            async def run(aid, c):
                results[aid] = await c
                await self._announce(rnd, aid, results[aid])
            await asyncio.gather(*(run(a, c) for a, c in coros.items()))
        else:
            for aid, c in coros.items():
                await self.emit({"type": "status", "agent": aid, "status": "thinking"})
                await asyncio.sleep(self.s.pace / 2)
                results[aid] = await c
                await self._announce(rnd, aid, results[aid])
        return results

    async def _announce(self, rnd: int, aid: str, o):
        await self.emit({"type": "vote", "round": rnd, "agent": aid, **o.model_dump()})
        await self._say(aid, f"[{o.stance} {o.conviction}] {o.headline}", "opinion" if rnd == 1 else "rebuttal",
                        detail=o.reasoning)

    def _rules_debate(self, mine, peers: dict, kind: str):
        w = self._weights()
        peer = sum(w[a] * o.conviction / 100 * _SIGN[o.stance] for a, o in peers.items()) / (sum(w[a] for a in peers) or 1)
        up, down = ("LONG", "SHORT") if kind == "stock" else ("YES", "NO")
        leaning = up if peer > 0 else down
        o = mine.model_copy()
        if mine.stance == "PASS" and abs(peer) > 0.35 and kind == "stock":
            o.stance, o.conviction, o.changed_mind = leaning, 35, True
            o.headline = "Nothing in my numbers argues against it - I'll go along with the room, small."
        elif mine.stance != "PASS" and _SIGN[mine.stance] * peer < -0.3 and mine.conviction < 50:
            o.stance, o.conviction, o.changed_mind = "PASS", 0, True
            o.headline = "The room's case is stronger than my signal - I'll step aside."
        elif mine.stance != "PASS" and _SIGN[mine.stance] * peer > 0:
            o.conviction = min(95, mine.conviction + 5)
            o.headline = "Bruno's points are fair, but the other disciplines back me - sticking with it."
        else:
            o.headline = "Heard the arguments, Bruno's included; my read hasn't changed."
        return o

    async def _memo(self, subject, decision, votes: dict, order, risk_notes):
        def rules():
            yes = ", ".join(P.BY_ID[a].name.split()[0] for a in decision.agreeing) or "nobody"
            no = ", ".join(P.BY_ID[a].name.split()[0] for a in decision.dissenting + decision.passing) or "nobody"
            act = f"{order['action'].title()} {order['qty']} {subject}" if order else f"Stayed out of {subject}"
            return DeskMemo(summary=f"{act}. For: {yes}. Against/passing: {no}.",
                            what_would_change_our_mind="A shift in the dissenting traders' signals.")

        floor = "\n".join(_fmt(a, o) for a, o in votes.items())
        system = (P.SHARED_RULES + "\n\nYou are the Head of Desk, chair of the Council. Write the desk memo for the "
                  "trade log: what we did and why, in plain English a non-trader executive can follow.")
        prompt = (f"Subject: {subject}\nDecision: {decision.reason}\nOrder: {order or 'none'}\n"
                  f"Risk notes: {'; '.join(risk_notes) or 'n/a'}\nFinal votes:\n{floor}")
        memo = await self._ask(P.CHAIR, system, prompt, DeskMemo, rules)
        await self._say("chair", memo.summary, "memo", subject, detail="Would change our mind: " + memo.what_would_change_our_mind)

    # ------------------------------------------------------------ one stock idea
    async def _consider_stock(self, sym: str, pitch: Pitch, snap: Snapshot, feats: dict) -> bool:
        bars = snap.bars.tail(90)
        _, _, span_a, span_b = ichimoku(snap.bars)
        # The cloud under each bar is the spans computed 26 bars earlier.
        cloud = [[None if a != a else round(float(a), 2), None if b != b else round(float(b), 2)]
                 for a, b in zip(span_a.shift(26).tail(90), span_b.shift(26).tail(90))]
        await self.emit({"type": "candidate", "kind": "stock", "symbol": sym, "title": sym, "price": feats["price"],
                         "screen_score": round(self._screen_score(feats, snap), 2), "features": feats,
                         "fundamentals": snap.fundamentals,
                         "candles": [[str(i.date()), round(r.open, 2), round(r.high, 2), round(r.low, 2), round(r.close, 2)]
                                     for i, r in bars.iterrows()],
                         "cloud": cloud})
        await self._phase("pitch", sym)
        await self._say("scout", f"{sym} {pitch.direction}: {pitch.thesis}", "pitch", sym,
                        detail="Catalysts: " + "; ".join(pitch.catalysts) + " | Risks: " + "; ".join(pitch.risks))
        news = await self._news_brief(snap)
        await self._say("newshound", f"Tape is {news.sentiment}. {news.summary}", "research", sym,
                        detail=" | ".join(news.market_moving))

        await self._phase("bear", sym)
        pitch_text = f"PITCH: {sym} {pitch.direction} at {feats['price']}. {pitch.thesis}"
        bear_data = self._view(P.BY_ID["bear"], snap, feats)
        bear = await self._bear_case(sym, bear_data, pitch_text,
                                     lambda: [c for v in (P.fundamental_view, P.momentum_view, P.cloud_view,
                                                          P.money_flow_view, P.news_view) for c in v(feats, snap)[2]])
        await self._say("bear", bear.verdict, "bear", sym, detail=" | ".join(bear.arguments))

        brief = (f"PITCH ({P.BY_ID['scout'].name}, Quant Researcher): {sym} {pitch.direction} at {feats['price']}\n"
                 f"Thesis: {pitch.thesis}\nCatalysts: {'; '.join(pitch.catalysts)}\nRisks: {'; '.join(pitch.risks)}\n\n"
                 f"NEWS BRIEF ({P.BY_ID['newshound'].name}): {news.sentiment} - {news.summary} "
                 f"Market-moving: {'; '.join(news.market_moving) or 'none'}\n\n"
                 f"DEVIL'S ADVOCATE ({P.BY_ID['bear'].name}): {bear.verdict}\n- " + "\n- ".join(bear.arguments))

        def round1(t):
            def rules():
                sc, sig, con = t.heuristic(feats, snap)
                stance = "PASS" if abs(sc) < 0.15 else ("LONG" if sc > 0 else "SHORT")
                head = {"LONG": f"I like it - {sig[0] if sig else 'setup checks out'}.",
                        "SHORT": f"I'm against it - {con[0] if con else 'setup is weak'}.",
                        "PASS": f"No edge for me here{' - ' + con[0] if con else ''}."}[stance]
                return Opinion(stance=stance, conviction=int(min(95, abs(sc) * 100)), headline=head,
                               reasoning="; ".join(sig[:3] + con[:2]) or "Mixed signals.",
                               key_signals=sig[:4], concerns=con[:3])
            prompt = (f"{brief}\n\nYOUR DATA:\n{json.dumps(self._view(t, snap, feats), indent=1)}\n\n"
                      "Round 1 (blind vote): give your independent opinion on this pitch.")
            return self._ask(t, P.SHARED_RULES + "\n\n" + P.DISCIPLINE_PROMPTS[t.id], prompt, Opinion, rules)

        def round2(t, mine, peers):
            prompt = (f"{brief}\n\nYOUR DATA:\n{json.dumps(self._view(t, snap, feats), indent=1)}\n\n"
                      f"YOUR ROUND 1 VOTE: {mine.stance} conviction {mine.conviction} - {mine.headline}\n\n"
                      f"THE REST OF THE COUNCIL SAID:\n" + "\n".join(_fmt(a, o) for a, o in peers.items()) + "\n\n"
                      "Round 2 (debate): respond to your colleagues and the devil's advocate, then give your final vote. "
                      "Change your stance only if someone raised a point your discipline genuinely has to respect - do "
                      "not simply follow the majority. Address the strongest opposing argument in your headline. "
                      "Set changed_mind if your stance changed.")
            return self._ask(t, P.SHARED_RULES + "\n\n" + P.DISCIPLINE_PROMPTS[t.id], prompt, Opinion,
                             lambda: self._rules_debate(mine, peers, "stock"))

        d, votes = await self._council(sym, "stock", brief, round1, round2)
        await self._say("chair", ("APPROVED - " if d.approved else "REJECTED - ") + d.reason, "decision", sym,
                        approved=d.approved)

        order, notes = None, []
        if d.approved:
            await self._phase("risk", sym)
            ne = snap.fundamentals.get("next_earnings")
            dte = (date.fromisoformat(str(ne)[:10]) - date.today()).days if ne else None
            rv = review_trade(self.broker, sym, d.direction, feats["price"], feats["volatility"]["atr14"], self.s,
                              sector=snap.fundamentals.get("sector") or "", risk_on=self.risk_on, days_to_earnings=dte)
            notes = rv.notes
            verdict = (f"Signed off: {rv.qty} shares, stop {rv.stop}, target {rv.target}, max loss ${rv.risk_dollars:,.0f}."
                       if rv.approved else "VETO.")
            await self._say("risk", f"{verdict} {' '.join(rv.notes)}", "risk", sym, approved=rv.approved)
            if rv.approved:
                await self._phase("execute", sym)
                order = self.broker.open(sym, d.direction, rv,
                                         {a: {"stance": o.stance, "conviction": o.conviction} for a, o in votes.items()},
                                         snap.fundamentals.get("sector") or "")
                await self.emit({"type": "order", **order})
                self._alert(f"Prop Desk: {order['action']} {order['qty']} {sym} @ {order['price']} "
                            f"(stop {order['stop']}, target {order['target']}) - {d.reason}")
                self.broker.snapshot(self.bench_price)
                await self._publish_book()
        await self.emit({"type": "verdict", "symbol": sym, "approved": order is not None,
                         "label": "APPROVED" if order else ("VETOED" if d.approved else "REJECTED")})
        self.scorecard.record("stock", sym, sym, feats["price"], {a: o.stance for a, o in votes.items()}, order is not None)
        await self._memo(sym, d, votes, order, notes)
        return order is not None

    # ------------------------------------------------------------ prediction markets
    def _rules_contract_view(self, t, c: Contract) -> ContractOpinion:
        est, sig, con = P.CONTRACT_HEURISTICS[t.id](c)
        if est is None:
            return ContractOpinion(stance="PASS", probability=c.implied, conviction=0,
                                   headline=f"No edge for me - {con[0] if con else 'nothing to go on'}.",
                                   reasoning="; ".join(con) or "-", key_signals=[], concerns=con)
        yes_edge, no_edge = edge_cents(est, "YES", c.yes_ask), edge_cents(est, "NO", c.no_ask)
        stance = "YES" if yes_edge >= 3 else "NO" if no_edge >= 3 else "PASS"
        conv = 0 if stance == "PASS" else int(min(90, max(yes_edge, no_edge) * 6))
        return ContractOpinion(stance=stance, probability=int(est), conviction=conv,
                               headline=f"{'Fair at this price' if stance == 'PASS' else 'Value on ' + stance} - "
                                        f"{sig[0] if sig else ''}.",
                               reasoning="; ".join(sig + con) or "-", key_signals=sig, concerns=con)

    async def _market_hunt(self) -> list[tuple[Contract, MarketIdea]]:
        await self._phase("markets")
        oracle = P.BY_ID["oracle"]
        loop = asyncio.get_running_loop()
        await self._say("chair", "No stock trade today. Nova - anything mispriced on the prediction markets?", "chair")
        try:
            markets = await loop.run_in_executor(None, self.kalshi.markets)
        except Exception as e:
            await self.emit({"type": "notice", "text": f"Couldn't load prediction markets: {e}"})
            return []
        markets = [c for c in markets if c.ticker not in self.broker.contracts]
        by_ticker = {c.ticker: c for c in markets}

        if self.brain:
            agent = ResearchAgent(oracle, self.s.model, self.s.effort, self.emit, self.s.research_budget_usd)
            await self.emit({"type": "status", "agent": "oracle", "status": "thinking"})
            try:
                slate = await agent.hunt(
                    f"Find up to {self.s.kalshi_candidates} prediction markets where you believe the true probability "
                    f"differs from the price by at least {self.s.kalshi_min_edge_cents + 3:.0f} cents after fees. "
                    "Use web search to check the facts that decide each market.",
                    DeskContext(contracts={c.ticker: c.to_dict() for c in markets}), MarketSlate)
            except Exception as e:
                log.exception("market hunt failed")
                slate = None
                await self.emit({"type": "notice", "text": f"Research agent failed ({type(e).__name__}) - using rules"})
            finally:
                self.research_cost += agent.cost_usd
            if slate is not None:
                ideas = [(by_ticker[i.ticker], i) for i in slate.ideas if i.ticker in by_ticker][: self.s.kalshi_candidates]
                await self._say("oracle", slate.summary, "research")
                return ideas

        await self._tool("oracle", "prediction_markets")
        scored = []
        for c in markets:
            best = None
            for t in P.TRADERS:
                est, sig, _ = P.CONTRACT_HEURISTICS[t.id](c)
                if est is not None and abs(est - c.implied) >= 4 and (best is None or abs(est - c.implied) > abs(best[0] - c.implied)):
                    best = (est, sig)
            if best:
                side = "YES" if best[0] > c.implied else "NO"
                scored.append((abs(best[0] - c.implied), c, MarketIdea(
                    ticker=c.ticker, side=side, fair_probability=int(best[0]),
                    thesis=f"Priced at {c.implied}¢ but our models say ~{int(best[0])}%.", evidence=best[1],
                    risks=["rules-mode estimate - no news check"])))
        scored.sort(key=lambda x: x[0], reverse=True)
        ideas = [(c, i) for _, c, i in scored[: self.s.kalshi_candidates]]
        await self._say("oracle", f"Scanned {len(markets)} markets. "
                        + (f"Bringing {len(ideas)} to the Council." if ideas else "Nothing mispriced enough."), "research")
        return ideas

    async def _consider_contract(self, c: Contract, idea: MarketIdea) -> bool:
        await self.emit({"type": "candidate", "kind": "contract", "symbol": c.ticker, "title": c.title,
                         "price": c.implied, "contract": c.to_dict(), "side": idea.side,
                         "fair": idea.fair_probability})
        await self._phase("pitch", c.ticker)
        await self._tool("oracle", "market_detail", ticker=c.ticker)
        await self._say("oracle", f"\"{c.title}\" - {idea.side} at {c.ask(idea.side)}¢. {idea.thesis}", "pitch", c.ticker,
                        detail="Evidence: " + "; ".join(idea.evidence) + " | Risks: " + "; ".join(idea.risks))
        data = c.to_dict()
        await self._phase("bear", c.ticker)
        bear = await self._bear_case(c.title, data, f"PITCH: buy {idea.side} on '{c.title}' at {c.ask(idea.side)}¢. {idea.thesis}",
                                     lambda: [f"the crowd prices this at {c.implied}% and crowds are usually right",
                                              f"{c.yes_ask - c.yes_bid}¢ spread plus fees eat the edge",
                                              "rules-mode estimates have no information the market lacks"])
        await self._say("bear", bear.verdict, "bear", c.ticker, detail=" | ".join(bear.arguments))

        brief = (f"PREDICTION MARKET: {c.title}\nResolution rules: {c.rules}\nCloses: {c.close_time}\n"
                 f"YES bid/ask {c.yes_bid}/{c.yes_ask}¢ (NO costs {c.no_ask}¢). Volume {c.volume:,}, "
                 f"previous price {c.previous_price}¢.\n\nPITCH ({P.BY_ID['oracle'].name}): {idea.side}, she estimates "
                 f"{idea.fair_probability}% YES. {idea.thesis}\nEvidence: {'; '.join(idea.evidence)}\n\n"
                 f"DEVIL'S ADVOCATE: {bear.verdict}\n- " + "\n- ".join(bear.arguments) +
                 "\n\nVote YES or NO only if you think buying that side has positive expected value after fees; "
                 "give your own probability that the event resolves YES.")

        def system(t):
            return P.SHARED_RULES + "\n\n" + P.DISCIPLINE_PROMPTS[t.id] + "\n" + P.CONTRACT_PROMPTS[t.id]

        def round1(t):
            return self._ask(t, system(t), brief + "\n\nRound 1 (blind vote).", ContractOpinion,
                             lambda: self._rules_contract_view(t, c))

        def round2(t, mine, peers):
            prompt = (brief + f"\n\nYOUR ROUND 1 VOTE: {mine.stance} ({mine.probability}% YES) - {mine.headline}\n\n"
                      "THE REST OF THE COUNCIL SAID:\n" + "\n".join(_fmt(a, o) for a, o in peers.items()) +
                      "\n\nRound 2 (debate): final vote. Only change if a colleague raised something you must respect.")
            return self._ask(t, system(t), prompt, ContractOpinion, lambda: self._rules_debate(mine, peers, "contract"))

        d, votes = await self._council(c.ticker, "contract", brief, round1, round2)
        w = self._weights()
        voters = [a for a, o in votes.items() if o.stance != "PASS"]
        prob = (sum(w[a] * votes[a].probability for a in voters) / sum(w[a] for a in voters)) if voters else idea.fair_probability
        side = d.direction if d.agreeing else idea.side
        edge = edge_cents(prob, side, c.ask(side))
        approved = d.approved and edge >= self.s.kalshi_min_edge_cents
        reason = d.reason if not d.approved else (
            f"{d.reason}; Council fair value {prob:.0f}% YES, edge {edge:+.1f}¢ after fees"
            + ("" if approved else f" - below our {self.s.kalshi_min_edge_cents:g}¢ minimum"))
        await self.emit({"type": "edge", "symbol": c.ticker, "prob": round(prob, 1), "side": side, "edge": edge,
                         "ask": c.ask(side)})
        await self._say("chair", ("APPROVED - " if approved else "REJECTED - ") + reason, "decision", c.ticker,
                        approved=approved)

        order, notes = None, []
        if approved:
            await self._phase("risk", c.ticker)
            rv = review_contract(self.broker, c, side, prob, self.s)
            notes = rv.notes
            await self._say("risk", ("Signed off. " if rv.approved else "VETO. ") + " ".join(rv.notes), "risk",
                            c.ticker, approved=rv.approved)
            if rv.approved:
                await self._phase("execute", c.ticker)
                order = self.broker.open_contract(c, side, rv, {a: o.stance for a, o in votes.items()})
                await self.emit({"type": "order", **order})
                self._alert(f"Prop Desk: bought {order['qty']} {side} on \"{c.title}\" @ {order['price']}¢")
                self.broker.snapshot(self.bench_price)
                await self._publish_book()
        await self.emit({"type": "verdict", "symbol": c.ticker, "approved": order is not None,
                         "label": "APPROVED" if order else ("VETOED" if approved else "REJECTED")})
        self.scorecard.record("contract", c.ticker, c.title, c.implied, {a: o.stance for a, o in votes.items()},
                              order is not None, due=c.close_time)
        await self._memo(c.title, Decision(d.score, side, d.agreeing, d.dissenting, d.passing, approved, reason),
                         votes, order, notes)
        return order is not None

    # ------------------------------------------------------------ session
    async def run_session(self, symbols: list[str] | None = None):
        if self.running:
            return
        self.running = True
        self.transcript = []
        started = datetime.now(timezone.utc)
        try:
            await self._phase("housekeeping")
            snaps, feats = await self._housekeeping([s.upper() for s in symbols] if symbols else None)
            await self._review_positions(snaps, feats)
            traded = False
            for sym, pitch in await self._stock_hunt(snaps, feats, [s.upper() for s in symbols] if symbols else None):
                traded |= await self._consider_stock(sym, pitch, snaps[sym], feats[sym])
            if not traded and self.kalshi and not symbols:
                for c, idea in await self._market_hunt():
                    await self._consider_contract(c, idea)
            await self._publish_book()
            await self.emit({"type": "session_end", "cost_usd": self.cost_usd})
        except Exception as e:
            log.exception("session failed")
            await self.emit({"type": "error", "text": f"Session failed: {e}"})
        finally:
            self.running = False
            await self._phase("idle")
            self._save_transcript(started)

    def _save_transcript(self, started: datetime) -> None:
        try:
            os.makedirs(self.s.sessions_dir, exist_ok=True)
            path = os.path.join(self.s.sessions_dir, started.strftime("%Y%m%d-%H%M%S") + ".jsonl")
            with open(path, "w") as f:
                for e in self.transcript:
                    f.write(json.dumps(e, default=str) + "\n")
        except Exception as e:
            log.warning("couldn't save transcript: %s", e)
