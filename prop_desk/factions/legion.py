"""Iron Legion: disciplined specialists who research, argue and vote.

The most human of the three factions. A quant researcher (a Claude Agent SDK
agent) hunts for ideas, a news officer briefs, a devil's advocate attacks, and
five discipline traders vote blind, debate, and vote again in the War Room.
"""
from __future__ import annotations

import asyncio
import json
import logging

from ..agents import personas as P
from ..agents.research_agent import DeskContext, ResearchAgent
from ..agents.schemas import (BearCase, ContractOpinion, DeskMemo, MarketIdea, MarketSlate, NewsDigest, Opinion,
                              Pitch, StockSlate)
from ..consensus import Decision, tally
from ..kalshi import Contract, edge_cents
from ..market_data import Snapshot
from .base import SIGN as _SIGN, Faction, Field

log = logging.getLogger(__name__)


def _fmt(aid: str, o) -> str:
    p = P.BY_ID[aid]
    prob = f" (thinks {o.probability}% YES)" if hasattr(o, "probability") else ""
    return (f"- {p.name} ({p.role}): {o.stance} conviction {o.conviction}{prob} - \"{o.headline}\"\n"
            f"  reasoning: {o.reasoning}\n  concerns: {'; '.join(o.concerns) or 'none'}")


class IronLegion(Faction):
    id = "legion"
    name = "Iron Legion"
    color = "#60a5fa"
    motto = "Discipline. Debate. Execute."
    leader = "chair"

    def weights(self) -> dict[str, float]:
        return {t.id: self.weight(t.id, t.weight) for t in P.TRADERS}

    async def tool(self, agent: str, tool: str, **inp):
        """Researchers head out to a mineral field (or terminal) when they use a tool."""
        await self.emit({"type": "tool", "agent": agent, "tool": tool, "input": inp})
        await self.pause(1 / 3)

    def quick_view(self, sym: str, fld: Field) -> float:
        return sum(t.heuristic(fld.feats[sym], fld.snaps[sym])[0] for t in P.TRADERS) / len(P.TRADERS)

    async def stock_turn(self, fld: Field) -> bool:
        self.fld = fld
        traded = False
        for sym, pitch in await self._stock_hunt(fld.snaps, fld.feats, fld.focus):
            traded |= await self._consider_stock(sym, pitch, fld.snaps[sym], fld.feats[sym])
        return traded

    async def gas_turn(self, fld: Field) -> bool:
        self.fld = fld
        traded = False
        for c, idea in await self._market_hunt():
            traded |= await self._consider_contract(c, idea)
        return traded

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
        await self.phase("research")
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
                await self.say("scout", slate.summary + (f" Bringing {', '.join(s for s, _ in ideas)} to the Council."
                                                          if ideas else " Nothing worth the Council's time."), "research")
                return ideas

        await self.tool("scout", "screen_watchlist")
        key = (lambda s: abs(scores[s])) if self.s.allow_shorts else (lambda s: scores[s])
        picks = [s.upper() for s in symbols] if symbols else sorted(pool, key=key, reverse=True)[: self.s.candidates_per_session]
        picks = [s for s in picks if s in snaps]
        board = ", ".join(f"{s} {scores[s]:+.2f}" for s in sorted(scores, key=scores.get, reverse=True))
        await self.say("scout", f"Screened {len(scores)} names (setup score: {board}). "
                                 f"Bringing {', '.join(picks) or 'nothing'} to the Council.", "research")
        ideas = []
        for sym in picks:
            await self.tool("scout", "symbol_report", symbol=sym)
            system = (P.SHARED_RULES + "\n\nYou are the desk's quant researcher. Turn the screen output into a crisp "
                      "trade pitch. Be honest about weaknesses." + ("" if self.s.allow_shorts else " The desk is long-only."))
            pitch = await self.ask(scout, system, f"Screen data for {sym}:\n{json.dumps(self._view(scout, snaps[sym], feats[sym]), indent=1)}",
                                    Pitch, lambda sym=sym: self._rules_pitch(sym, snaps[sym], feats[sym], scores.get(sym, 0)))
            ideas.append((sym, pitch))
        return ideas

    async def _news_brief(self, snap: Snapshot) -> NewsDigest:
        hound = P.BY_ID["newshound"]
        await self.tool("newshound", "headlines", symbol=snap.symbol)

        def rules():
            s, sig, con = P.news_view({}, snap)
            mood = "quiet" if not snap.news else "bullish" if s > 0.2 else "bearish" if s < -0.2 else "mixed"
            return NewsDigest(sentiment=mood, summary=f"{len(snap.news)} headlines in the last few days.",
                              market_moving=(sig + con)[:3])

        system = (P.SHARED_RULES + "\n\nYou are the desk's news researcher. Summarize what the headlines mean for "
                  "the stock. If headlines are missing or stale, say the tape is quiet.")
        heads = [{"title": h.title, "source": h.source, "published": h.published, "summary": h.summary} for h in snap.news]
        return await self.ask(hound, system, f"Headlines for {snap.symbol}:\n{json.dumps(heads, indent=1)}", NewsDigest, rules)

    async def _bear_case(self, subject: str, data: dict, pitch_text: str, rules_args) -> BearCase:
        bear = P.BY_ID["bear"]

        def rules():
            args = rules_args() or ["the setup is crowded - everyone can see the same chart"]
            return BearCase(verdict=f"I'd pass on {subject}: {args[0]}.", arguments=args[:4],
                            what_would_prove_me_wrong="The weak signals turning before we enter.")

        system = (P.SHARED_RULES + "\n\nYou are the desk's devil's advocate. Your job is to make the strongest honest "
                  "case AGAINST the pitch, using only the data given. Don't be contrarian for sport - find the real "
                  "weaknesses: valuation, crowded trades, fading momentum, event risk, liquidity, missing evidence.")
        return await self.ask(bear, system, f"{pitch_text}\n\nDATA:\n{json.dumps(data, indent=1, default=str)}",
                               BearCase, rules)

    # ------------------------------------------------------------ the Council (shared by stocks and markets)
    async def _council(self, subject: str, kind: str, brief: str, round1, round2) -> tuple[Decision, dict]:
        await self.phase("round1", subject)
        r1 = await self._vote_round(1, kind, {t.id: round1(t) for t in P.TRADERS})
        weights = self.weights()
        fams = {t.id: t.family for t in P.TRADERS}
        quorum = self.s.min_agreeing_traders if kind == "stock" else self.s.kalshi_quorum
        args = dict(weights=weights, threshold=self.s.approve_threshold, quorum=quorum,
                    allow_shorts=self.s.allow_shorts, families=fams, min_families=self.s.min_families, kind=kind)
        d1 = tally(r1, **args)
        await self.emit({"type": "tally", "round": 1, "symbol": subject, "weights": weights, **d1.to_dict()})

        await self.phase("debate", subject)
        await self.say("chair", f"Round 1 on {subject} scores {d1.score:+.2f}. Bruno, Council - let's hear the disagreements.",
                        "chair", subject)
        r2 = await self._vote_round(2, kind, {
            t.id: round2(t, r1[t.id], {a: o for a, o in r1.items() if a != t.id}) for t in P.TRADERS})
        d2 = tally(r2, **args)
        await self.phase("vote", subject)
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
        await self.say(aid, f"[{o.stance} {o.conviction}] {o.headline}", "opinion" if rnd == 1 else "rebuttal",
                        detail=o.reasoning)

    def _rules_debate(self, mine, peers: dict, kind: str):
        w = self.weights()
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
        memo = await self.ask(P.CHAIR, system, prompt, DeskMemo, rules)
        await self.say("chair", memo.summary, "memo", subject, detail="Would change our mind: " + memo.what_would_change_our_mind)

    # ------------------------------------------------------------ one stock idea
    async def _consider_stock(self, sym: str, pitch: Pitch, snap: Snapshot, feats: dict) -> bool:
        await self.emit({**self.stock_candidate(sym, self.fld),
                         "screen_score": round(self._screen_score(feats, snap), 2)})
        await self.phase("pitch", sym)
        await self.say("scout", f"{sym} {pitch.direction}: {pitch.thesis}", "pitch", sym,
                        detail="Catalysts: " + "; ".join(pitch.catalysts) + " | Risks: " + "; ".join(pitch.risks))
        news = await self._news_brief(snap)
        await self.say("newshound", f"Tape is {news.sentiment}. {news.summary}", "research", sym,
                        detail=" | ".join(news.market_moving))

        await self.phase("bear", sym)
        pitch_text = f"PITCH: {sym} {pitch.direction} at {feats['price']}. {pitch.thesis}"
        bear_data = self._view(P.BY_ID["bear"], snap, feats)
        bear = await self._bear_case(sym, bear_data, pitch_text,
                                     lambda: [c for v in (P.fundamental_view, P.momentum_view, P.cloud_view,
                                                          P.money_flow_view, P.news_view) for c in v(feats, snap)[2]])
        await self.say("bear", bear.verdict, "bear", sym, detail=" | ".join(bear.arguments))

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
            return self.ask(t, P.SHARED_RULES + "\n\n" + P.DISCIPLINE_PROMPTS[t.id], prompt, Opinion, rules)

        def round2(t, mine, peers):
            prompt = (f"{brief}\n\nYOUR DATA:\n{json.dumps(self._view(t, snap, feats), indent=1)}\n\n"
                      f"YOUR ROUND 1 VOTE: {mine.stance} conviction {mine.conviction} - {mine.headline}\n\n"
                      f"THE REST OF THE COUNCIL SAID:\n" + "\n".join(_fmt(a, o) for a, o in peers.items()) + "\n\n"
                      "Round 2 (debate): respond to your colleagues and the devil's advocate, then give your final vote. "
                      "Change your stance only if someone raised a point your discipline genuinely has to respect - do "
                      "not simply follow the majority. Address the strongest opposing argument in your headline. "
                      "Set changed_mind if your stance changed.")
            return self.ask(t, P.SHARED_RULES + "\n\n" + P.DISCIPLINE_PROMPTS[t.id], prompt, Opinion,
                             lambda: self._rules_debate(mine, peers, "stock"))

        d, votes = await self._council(sym, "stock", brief, round1, round2)
        await self.say("chair", ("APPROVED - " if d.approved else "REJECTED - ") + d.reason, "decision", sym,
                        approved=d.approved)

        order, notes = None, []
        if d.approved:
            order = await self.execute_stock(
                sym, d.direction, self.fld, {a: {"stance": o.stance, "conviction": o.conviction} for a, o in votes.items()})
            if order:
                await self.arena.publish()
        await self.emit({"type": "verdict", "symbol": sym, "approved": order is not None,
                         "label": "APPROVED" if order else ("VETOED" if d.approved else "REJECTED")})
        self.record("stock", sym, sym, feats["price"], {a: o.stance for a, o in votes.items()},
                    d.direction if d.approved else "PASS", order is not None)
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
        await self.phase("markets")
        oracle = P.BY_ID["oracle"]
        await self.say("chair", "No stock trade today. Nova - anything mispriced on the prediction markets?", "chair")
        markets = [c for c in self.fld.contracts if c.ticker not in self.broker.contracts]
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
                await self.say("oracle", slate.summary, "research")
                return ideas

        await self.tool("oracle", "prediction_markets")
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
        await self.say("oracle", f"Scanned {len(markets)} markets. "
                        + (f"Bringing {len(ideas)} to the Council." if ideas else "Nothing mispriced enough."), "research")
        return ideas

    async def _consider_contract(self, c: Contract, idea: MarketIdea) -> bool:
        await self.emit({"type": "candidate", "kind": "contract", "symbol": c.ticker, "title": c.title,
                         "price": c.implied, "contract": c.to_dict(), "side": idea.side,
                         "fair": idea.fair_probability})
        await self.phase("pitch", c.ticker)
        await self.tool("oracle", "market_detail", ticker=c.ticker)
        await self.say("oracle", f"\"{c.title}\" - {idea.side} at {c.ask(idea.side)}¢. {idea.thesis}", "pitch", c.ticker,
                        detail="Evidence: " + "; ".join(idea.evidence) + " | Risks: " + "; ".join(idea.risks))
        data = c.to_dict()
        await self.phase("bear", c.ticker)
        bear = await self._bear_case(c.title, data, f"PITCH: buy {idea.side} on '{c.title}' at {c.ask(idea.side)}¢. {idea.thesis}",
                                     lambda: [f"the crowd prices this at {c.implied}% and crowds are usually right",
                                              f"{c.yes_ask - c.yes_bid}¢ spread plus fees eat the edge",
                                              "rules-mode estimates have no information the market lacks"])
        await self.say("bear", bear.verdict, "bear", c.ticker, detail=" | ".join(bear.arguments))

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
            return self.ask(t, system(t), brief + "\n\nRound 1 (blind vote).", ContractOpinion,
                             lambda: self._rules_contract_view(t, c))

        def round2(t, mine, peers):
            prompt = (brief + f"\n\nYOUR ROUND 1 VOTE: {mine.stance} ({mine.probability}% YES) - {mine.headline}\n\n"
                      "THE REST OF THE COUNCIL SAID:\n" + "\n".join(_fmt(a, o) for a, o in peers.items()) +
                      "\n\nRound 2 (debate): final vote. Only change if a colleague raised something you must respect.")
            return self.ask(t, system(t), prompt, ContractOpinion, lambda: self._rules_debate(mine, peers, "contract"))

        d, votes = await self._council(c.ticker, "contract", brief, round1, round2)
        w = self.weights()
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
        await self.say("chair", ("APPROVED - " if approved else "REJECTED - ") + reason, "decision", c.ticker,
                        approved=approved)

        order, notes = None, []
        if approved:
            order = await self.execute_contract(c, side, prob, {a: o.stance for a, o in votes.items()})
            if order:
                await self.arena.publish()
        await self.emit({"type": "verdict", "symbol": c.ticker, "approved": order is not None,
                         "label": "APPROVED" if order else ("VETOED" if approved else "REJECTED")})
        self.record("contract", c.ticker, c.title, c.implied, {a: o.stance for a, o in votes.items()},
                    side if approved else "PASS", order is not None, due=c.close_time)
        await self._memo(c.title, Decision(d.score, side, d.agreeing, d.dissenting, d.passing, approved, reason),
                         votes, order, notes)
        return order is not None

