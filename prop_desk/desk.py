"""The trading floor: runs one session from research to execution.

Session flow (each step is streamed to the GUI as an event):
  research -> pitch -> round 1 (blind votes) -> debate (round 2) -> consensus -> risk -> execute -> memo
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

from .agents import personas as P
from .agents.brain import ClaudeBrain, has_credentials
from .agents.schemas import DeskMemo, NewsDigest, Opinion, Pitch
from .broker import PaperBroker, review_trade
from .config import Settings
from .consensus import tally
from .indicators import compute_features
from .market_data import Snapshot, make_provider

log = logging.getLogger(__name__)
Emit = Callable[[dict], Awaitable[None]]


def _fmt_opinion(name: str, role: str, o: Opinion) -> str:
    return (f"- {name} ({role}): {o.stance} conviction {o.conviction} - \"{o.headline}\"\n"
            f"  reasoning: {o.reasoning}\n  concerns: {'; '.join(o.concerns) or 'none'}")


class Desk:
    def __init__(self, settings: Settings, emit: Emit, broker: PaperBroker | None = None):
        self.s = settings
        self.emit = emit
        self.broker = broker or PaperBroker(settings.ledger_path, settings.starting_cash)
        use_claude = settings.ai_mode == "claude" or (settings.ai_mode == "auto" and has_credentials())
        self.brain = ClaudeBrain(settings.model, settings.effort) if use_claude else None
        self.provider = None
        self.running = False

    @property
    def mode(self) -> str:
        return f"Claude ({self.s.model})" if self.brain else "Rules (offline)"

    # ------------------------------------------------------------ helpers
    async def _say(self, agent: str, text: str, kind: str = "chat", symbol: str | None = None, **extra):
        await self.emit({"type": "status", "agent": agent, "status": "speaking"})
        await self.emit({"type": "message", "agent": agent, "text": text, "kind": kind, "symbol": symbol, **extra})
        if not self.brain:
            await asyncio.sleep(self.s.pace)
        await self.emit({"type": "status", "agent": agent, "status": "idle"})

    async def _phase(self, name: str, symbol: str | None = None):
        await self.emit({"type": "phase", "phase": name, "symbol": symbol})

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

    def _view(self, persona: P.Persona, snap: Snapshot, feats: dict) -> dict:
        view = {"symbol": snap.symbol, "price": feats["price"], "volatility": feats["volatility"]}
        for group in persona.sees:
            if group == "fundamentals":
                view["fundamentals"] = snap.fundamentals
            elif group == "news":
                view["headlines"] = [{"title": h.title, "source": h.source, "published": h.published,
                                      "summary": h.summary} for h in snap.news]
            else:
                view[group] = feats[group]
        return view

    # ------------------------------------------------------------ research
    def _screen_score(self, feats: dict, snap: Snapshot) -> float:
        parts = [P.momentum_view(feats, snap)[0], P.cloud_view(feats, snap)[0], P.money_flow_view(feats, snap)[0]]
        return sum(parts) / len(parts)

    async def _research(self, symbols: list[str] | None):
        loop = asyncio.get_running_loop()
        if self.provider is None:
            self.provider = await loop.run_in_executor(None, make_provider, self.s.data_source, self.s.benchmark)
        await self.emit({"type": "session", "mode": self.mode, "data_source": self.provider.name})

        await self.emit({"type": "status", "agent": "scout", "status": "thinking"})
        bench = (await loop.run_in_executor(None, self.provider.snapshot, self.s.benchmark)).bars
        universe = symbols or self.s.watchlist
        snaps, feats = {}, {}
        for sym in universe:
            try:
                snap = await loop.run_in_executor(None, self.provider.snapshot, sym)
                snaps[sym], feats[sym] = snap, compute_features(snap.bars, bench)
            except Exception as e:
                await self.emit({"type": "notice", "text": f"Skipping {sym}: {e}"})

        for t in self.broker.mark({s: sn.price for s, sn in snaps.items()}):
            await self._say("risk", f"Closed {t['symbol']} - {t['reason']} at {t['price']} (P&L ${t['pnl']:+,.2f})", "risk")
        await self.emit({"type": "portfolio", **self.broker.summary()})

        scores = {s: self._screen_score(feats[s], snaps[s]) for s in snaps}
        pool = [s for s in scores if s not in self.broker.positions]
        key = (lambda s: abs(scores[s])) if self.s.allow_shorts else (lambda s: scores[s])
        picks = symbols or sorted(pool, key=key, reverse=True)[: self.s.candidates_per_session]
        board = ", ".join(f"{s} {scores[s]:+.2f}" for s in sorted(scores, key=scores.get, reverse=True))
        await self._say("scout", f"Screened {len(snaps)} names (setup score: {board}). "
                                 f"Bringing {', '.join(picks)} to the floor.", "research")
        return [(s, snaps[s], feats[s], scores[s]) for s in picks if s in snaps]

    # ------------------------------------------------------------ one idea
    async def _pitch(self, snap: Snapshot, feats: dict, score: float) -> Pitch:
        scout = P.BY_ID["scout"]

        def rules():
            direction = "LONG" if score >= 0 or not self.s.allow_shorts else "SHORT"
            sig = [x for v in (P.momentum_view, P.cloud_view, P.money_flow_view) for x in v(feats, snap)[1]][:4]
            con = [x for v in (P.momentum_view, P.cloud_view, P.money_flow_view) for x in v(feats, snap)[2]][:3]
            return Pitch(direction=direction, thesis=f"{snap.symbol} screens {score:+.2f} on our technical composite.",
                         catalysts=sig or ["composite screen"], risks=con or ["none flagged by the screen"])

        system = (P.SHARED_RULES + "\n\nYou are the desk's quant researcher. Turn the screen output into a crisp "
                  "trade pitch for the traders. Be honest about weaknesses in the setup."
                  + ("" if self.s.allow_shorts else " The desk is long-only, so pitch LONG."))
        prompt = f"Screen data for {snap.symbol}:\n{json.dumps(self._view(scout, snap, feats), indent=1)}"
        return await self._ask(scout, system, prompt, Pitch, rules)

    async def _news_brief(self, snap: Snapshot) -> NewsDigest:
        hound = P.BY_ID["newshound"]

        def rules():
            s, sig, con = P.news_view({}, snap)
            mood = "quiet" if not snap.news else "bullish" if s > 0.2 else "bearish" if s < -0.2 else "mixed"
            return NewsDigest(sentiment=mood, summary=f"{len(snap.news)} headlines in the last few days.",
                              market_moving=(sig + con)[:3])

        system = (P.SHARED_RULES + "\n\nYou are the desk's news researcher. Summarize what the headlines mean for "
                  "the stock. If headlines are missing or stale, say the tape is quiet.")
        prompt = f"Headlines for {snap.symbol}:\n{json.dumps(self._view(hound, snap, {'price': snap.price, 'volatility': {}}), indent=1)}"
        return await self._ask(hound, system, prompt, NewsDigest, rules)

    async def _round1(self, persona: P.Persona, snap, feats, brief: str) -> Opinion:
        def rules():
            sc, sig, con = persona.heuristic(feats, snap)
            stance = "PASS" if abs(sc) < 0.15 else ("LONG" if sc > 0 else "SHORT")
            conv = int(min(95, abs(sc) * 100))
            head = {
                "LONG": f"I like it - {sig[0] if sig else 'setup checks out'}.",
                "SHORT": f"I'm against it - {con[0] if con else 'setup is weak'}.",
                "PASS": f"No edge for me here{' - ' + con[0] if con else ''}.",
            }[stance]
            return Opinion(stance=stance, conviction=conv, headline=head,
                           reasoning="; ".join(sig[:3] + con[:2]) or "Mixed signals.",
                           key_signals=sig[:4], concerns=con[:3])

        system = P.SHARED_RULES + "\n\n" + P.DISCIPLINE_PROMPTS[persona.id]
        prompt = (f"{brief}\n\nYOUR DATA:\n{json.dumps(self._view(persona, snap, feats), indent=1)}\n\n"
                  "Round 1 (blind vote): give your independent opinion on this pitch.")
        return await self._ask(persona, system, prompt, Opinion, rules)

    async def _round2(self, persona: P.Persona, mine: Opinion, peers: dict[str, Opinion],
                      snap, feats, brief: str) -> Opinion:
        def rules():
            w = {a: P.BY_ID[a].weight for a in peers}
            sign = {"LONG": 1, "SHORT": -1, "PASS": 0}
            peer = sum(w[a] * o.conviction / 100 * sign[o.stance] for a, o in peers.items()) / (sum(w.values()) or 1)
            leaning = "LONG" if peer > 0 else "SHORT"
            o = mine.model_copy()
            if mine.stance == "PASS" and abs(peer) > 0.35:
                o.stance, o.conviction, o.changed_mind = leaning, 35, True
                o.headline = f"Nothing in my numbers argues against it - I'll go along with the room, small."
            elif mine.stance != "PASS" and sign[mine.stance] * peer < -0.3 and mine.conviction < 50:
                o.stance, o.conviction, o.changed_mind = "PASS", 0, True
                o.headline = "The room's case is stronger than my signal - I'll step aside."
            elif mine.stance != "PASS" and sign[mine.stance] * peer > 0:
                o.conviction = min(95, mine.conviction + 5)
                o.headline = "Good to see the other disciplines agree - sticking with it."
            else:
                o.headline = "Heard the arguments; my read hasn't changed."
            return o

        floor = "\n".join(_fmt_opinion(P.BY_ID[a].name, P.BY_ID[a].role, o) for a, o in peers.items())
        system = P.SHARED_RULES + "\n\n" + P.DISCIPLINE_PROMPTS[persona.id]
        prompt = (f"{brief}\n\nYOUR DATA:\n{json.dumps(self._view(persona, snap, feats), indent=1)}\n\n"
                  f"YOUR ROUND 1 VOTE: {mine.stance} conviction {mine.conviction} - {mine.headline}\n\n"
                  f"THE REST OF THE FLOOR SAID:\n{floor}\n\n"
                  "Round 2 (debate): respond to your colleagues and give your final vote. Change your stance only if "
                  "someone raised a point your discipline genuinely has to respect - do not simply follow the majority. "
                  "Address the strongest opposing argument in your headline. Set changed_mind if your stance changed.")
        return await self._ask(persona, system, prompt, Opinion, rules)

    async def _vote_round(self, rnd: int, coros: dict) -> dict[str, Opinion]:
        results: dict[str, Opinion] = {}
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

    async def _announce(self, rnd: int, aid: str, o: Opinion):
        await self.emit({"type": "vote", "round": rnd, "agent": aid, **o.model_dump()})
        txt = f"[{o.stance} {o.conviction}] {o.headline}"
        await self._say(aid, txt, "opinion" if rnd == 1 else "rebuttal", detail=o.reasoning)

    async def _consider(self, symbol: str, snap: Snapshot, feats: dict, score: float):
        closes = [round(float(x), 2) for x in snap.bars["close"].tail(120)]
        await self.emit({"type": "candidate", "symbol": symbol, "price": feats["price"], "closes": closes,
                         "screen_score": round(score, 2), "features": feats, "fundamentals": snap.fundamentals})

        await self._phase("pitch", symbol)
        pitch = await self._pitch(snap, feats, score)
        await self._say("scout", f"{symbol} {pitch.direction}: {pitch.thesis}", "pitch", symbol,
                        detail="Catalysts: " + "; ".join(pitch.catalysts) + " | Risks: " + "; ".join(pitch.risks))
        news = await self._news_brief(snap)
        await self._say("newshound", f"Tape is {news.sentiment}. {news.summary}", "research", symbol,
                        detail=" | ".join(news.market_moving))
        brief = (f"PITCH ({P.BY_ID['scout'].name}, Quant Researcher): {symbol} {pitch.direction} at {feats['price']}\n"
                 f"Thesis: {pitch.thesis}\nCatalysts: {'; '.join(pitch.catalysts)}\nRisks: {'; '.join(pitch.risks)}\n\n"
                 f"NEWS BRIEF ({P.BY_ID['newshound'].name}): {news.sentiment} - {news.summary} "
                 f"Market-moving: {'; '.join(news.market_moving) or 'none'}")

        await self._phase("round1", symbol)
        r1 = await self._vote_round(1, {t.id: self._round1(t, snap, feats, brief) for t in P.TRADERS})
        weights = {t.id: t.weight for t in P.TRADERS}
        d1 = tally(r1, weights, self.s.approve_threshold, self.s.min_agreeing_traders, self.s.allow_shorts)
        await self.emit({"type": "tally", "round": 1, "symbol": symbol, **d1.to_dict()})

        await self._phase("debate", symbol)
        await self._say("chair", f"Round 1 on {symbol} scores {d1.score:+.2f}. Let's hear the disagreements.", "chair", symbol)
        r2 = await self._vote_round(2, {
            t.id: self._round2(t, r1[t.id], {a: o for a, o in r1.items() if a != t.id}, snap, feats, brief)
            for t in P.TRADERS})
        d2 = tally(r2, weights, self.s.approve_threshold, self.s.min_agreeing_traders, self.s.allow_shorts)
        await self._phase("vote", symbol)
        await self.emit({"type": "tally", "round": 2, "symbol": symbol, **d2.to_dict()})
        await self._say("chair", ("APPROVED - " if d2.approved else "NO TRADE - ") + d2.reason, "decision", symbol)

        order = None
        risk_notes: list[str] = []
        if d2.approved:
            await self._phase("risk", symbol)
            rv = review_trade(self.broker, symbol, d2.direction, feats["price"], feats["volatility"]["atr14"], self.s)
            risk_notes = rv.notes
            verdict = (f"Approved: {rv.qty} shares, stop {rv.stop}, target {rv.target}, "
                       f"max loss ${rv.risk_dollars:,.0f}." if rv.approved else "VETO.")
            await self._say("risk", f"{verdict} {' '.join(rv.notes)}", "risk", symbol)
            if rv.approved:
                await self._phase("execute", symbol)
                votes = {a: {"stance": o.stance, "conviction": o.conviction} for a, o in r2.items()}
                order = self.broker.open(symbol, d2.direction, rv, votes)
                await self.emit({"type": "order", **order})
                await self.emit({"type": "portfolio", **self.broker.summary()})

        await self._memo(symbol, d2, r2, order, risk_notes)

    async def _memo(self, symbol, decision, votes: dict[str, Opinion], order, risk_notes):
        chair = P.CHAIR

        def rules():
            yes = ", ".join(P.BY_ID[a].name.split()[0] for a in decision.agreeing) or "nobody"
            no = ", ".join(P.BY_ID[a].name.split()[0] for a in decision.dissenting + decision.passing) or "nobody"
            act = f"Bought {order['qty']} {symbol}" if order else f"Stayed out of {symbol}"
            return DeskMemo(summary=f"{act}. For: {yes}. Against/passing: {no}.",
                            what_would_change_our_mind="A shift in the dissenting traders' signals.")

        floor = "\n".join(_fmt_opinion(P.BY_ID[a].name, P.BY_ID[a].role, o) for a, o in votes.items())
        system = P.SHARED_RULES + "\n\nYou are the Head of Desk. Write the desk memo for the trade log: what we did and why, in plain English a non-trader executive can follow."
        prompt = (f"Symbol: {symbol}\nDecision: {decision.reason}\nOrder: {order or 'none'}\n"
                  f"Risk notes: {'; '.join(risk_notes) or 'n/a'}\nFinal votes:\n{floor}")
        memo = await self._ask(chair, system, prompt, DeskMemo, rules)
        await self._say("chair", memo.summary, "memo", symbol, detail="Would change our mind: " + memo.what_would_change_our_mind)

    # ------------------------------------------------------------ session
    async def run_session(self, symbols: list[str] | None = None):
        if self.running:
            return
        self.running = True
        try:
            await self._phase("research")
            ideas = await self._research([s.upper() for s in symbols] if symbols else None)
            for sym, snap, feats, score in ideas:
                await self._consider(sym, snap, feats, score)
            await self.emit({"type": "portfolio", **self.broker.summary()})
            await self.emit({"type": "session_end", "cost_usd": round(self.brain.cost_usd, 4) if self.brain else 0})
        except Exception as e:
            log.exception("session failed")
            await self.emit({"type": "error", "text": f"Session failed: {e}"})
        finally:
            self.running = False
            await self._phase("idle")
