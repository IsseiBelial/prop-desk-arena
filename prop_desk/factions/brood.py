"""The Brood: a hive mind of single-signal drones.

Two dozen drones each know exactly one thing (an RSI bounce, a 20-day breakout,
money flowing in...). They never talk to each other. The Hive Queen feels all
of them at once and acts when the swarm overwhelmingly agrees.

Natural selection: every drone's track record sets its weight, so drones that
keep being wrong fade and drones that keep being right grow louder.
"""
from __future__ import annotations

import random

from ..agents import personas as P
from ..agents.schemas import DeskMemo
from ..kalshi import Contract, edge_cents
from ..market_data import Snapshot
from .base import Faction, Field

_POS = ("beat", "raise", "upgrade", "buyback", "wins", "record", "surge", "approval", "strong")
_NEG = ("miss", "cut", "downgrade", "probe", "lawsuit", "short seller", "recall", "weak", "plunge", "investigation")


def _s(cond_up: bool | None, cond_down: bool | None = None) -> int:
    if cond_up:
        return 1
    if cond_down:
        return -1
    return 0


def drone_votes(f: dict, snap: Snapshot) -> dict[str, int]:
    """Every drone's vote: +1 bullish, -1 bearish, 0 nothing to say."""
    m, i, mf, v = f["momentum"], f["ichimoku"], f["money_flow"], f["volatility"]
    bars = snap.bars
    c, h, l = bars["close"], bars["high"], bars["low"]
    d = snap.fundamentals or {}
    heads = " ".join(x.title.lower() for x in snap.news)
    news = sum(w in heads for w in _POS) - sum(w in heads for w in _NEG)
    lows = l.tail(15)
    atr_now, atr_then = v["atr_pct"] or 0, (bars["high"] - bars["low"]).tail(60).head(20).mean() / c.iloc[-60] * 100
    return {
        "d_rsi_bounce": _s(m["rsi14"] < 32),
        "d_rsi_trend": _s(50 < m["rsi14"] < 72, m["rsi14"] < 45),
        "d_macd": _s(m["macd_above_signal"], not m["macd_above_signal"]),
        "d_sma20": _s(m["price_vs_sma20_pct"] > 0, m["price_vs_sma20_pct"] < 0),
        "d_sma50": _s(m["price_vs_sma50_pct"] > 0, m["price_vs_sma50_pct"] < 0),
        "d_golden": _s(m["golden_cross"] is True, m["golden_cross"] is False),
        "d_breakout": _s(c.iloc[-1] >= h.iloc[-21:-1].max()),
        "d_breakdown": _s(None, c.iloc[-1] <= l.iloc[-21:-1].min()),
        "d_52w": _s(m["pct_from_52w_high"] > -4),
        "d_gap": _s(bars["open"].iloc[-1] > h.iloc[-2], bars["open"].iloc[-1] < l.iloc[-2]),
        "d_volume": _s(mf["volume_20d_vs_50d"] and mf["volume_20d_vs_50d"] > 1.2 and m["return_1w_pct"] > 0,
                       mf["volume_20d_vs_50d"] and mf["volume_20d_vs_50d"] > 1.2 and m["return_1w_pct"] < 0),
        "d_obv": _s((mf["obv_trend_20d"] or 0) > 0.1, (mf["obv_trend_20d"] or 0) < -0.1),
        "d_cmf": _s((mf["cmf20"] or 0) > 0.05, (mf["cmf20"] or 0) < -0.05),
        "d_mfi": _s(55 < (mf["mfi14"] or 50) < 80, (mf["mfi14"] or 50) < 40),
        "d_cloud": _s(i["price_vs_cloud"] == "above", i["price_vs_cloud"] == "below"),
        "d_tk": _s(i["tenkan_above_kijun"], not i["tenkan_above_kijun"]),
        "d_rs": _s((mf["relative_strength_vs_spy_3m_pct"] or 0) > 0, (mf["relative_strength_vs_spy_3m_pct"] or 0) < 0),
        "d_reversal": _s((m["return_1w_pct"] or 0) < -2 * (v["atr_pct"] or 99)),
        "d_higher_lows": _s(lows.iloc[-5:].min() > lows.iloc[-10:-5].min() > lows.iloc[:5].min()),
        "d_squeeze": _s(atr_now < atr_then * 0.7 and m["price_vs_sma50_pct"] > 0),
        "d_value": _s(d.get("peg") is not None and d["peg"] < 1.2, d.get("peg") is not None and d["peg"] > 3),
        "d_growth": _s((d.get("earnings_growth_pct") or 0) > 10, (d.get("earnings_growth_pct") or 0) < -5),
        "d_news": _s(news > 0, news < 0),
        "d_trend3m": _s((m["return_3m_pct"] or 0) > 5, (m["return_3m_pct"] or 0) < -5),
    }


def contract_drones(c: Contract) -> dict[str, int]:
    """A handful of drones also forage the gas vents (prediction markets)."""
    move = c.last_price - c.previous_price
    return {
        "d_rsi_bounce": _s(None, c.implied < 12),  # long shots: fade them (buy NO)
        "d_52w": _s(c.implied > 88),  # heavy favourites: back them
        "d_trend3m": _s(move > 6, move < -6),  # repricing crowd: ride it
        "d_reversal": _s(move < -12, move > 12),  # overreaction: fade it
    }


class Brood(Faction):
    id = "brood"
    name = "The Brood"
    color = "#d946ef"
    motto = "Many signals. One will."
    leader = "vexa"

    def tally(self, votes: dict[str, int]) -> tuple[float, float, int]:
        """(agreement 0..1 for the leading side, net -1..+1, active drones)."""
        w = {k: self.weight(k) for k in votes}
        up = sum(w[k] for k, v in votes.items() if v > 0)
        dn = sum(w[k] for k, v in votes.items() if v < 0)
        active = sum(1 for v in votes.values() if v)
        if not up + dn:
            return 0.5, 0.0, 0
        return max(up, dn) / (up + dn), (up - dn) / (up + dn), active

    def quick_view(self, sym: str, fld: Field) -> float:
        _, net, _ = self.tally(drone_votes(fld.feats[sym], fld.snaps[sym]))
        return net

    async def stock_turn(self, fld: Field) -> bool:
        await self.phase("research")
        universe = [s for s in (fld.focus or fld.watchlist) if s in fld.feats]
        rng = random.Random(len(self.scorecard.calls))
        # The swarm spreads over every mineral field.
        await self.emit({"type": "swarm", "assign": {d: rng.choice(universe) for d, *_ in P.DRONES}})
        await self.pause(1.5)
        sentiment = {}
        for s in universe:
            agree, net, active = self.tally(drone_votes(fld.feats[s], fld.snaps[s]))
            sentiment[s] = (net, agree, active)
        board = ", ".join(f"{s} {v[0]:+.2f}" for s, v in sorted(sentiment.items(), key=lambda kv: -kv[1][0]))
        await self.say("vexa", f"The swarm tastes every field: {board}.", "research")
        pool_ = [s for s in sentiment if s not in self.broker.positions]
        key = (lambda s: abs(sentiment[s][0])) if self.s.allow_shorts else (lambda s: sentiment[s][0])
        picks = fld.focus or sorted(pool_, key=key, reverse=True)[: self.s.candidates_per_session]
        traded = False
        for sym in picks:
            if sym in fld.feats:
                traded |= await self._consider(sym, fld)
        return traded

    async def _consider(self, sym: str, fld: Field) -> bool:
        snap, feats = fld.snaps[sym], fld.feats[sym]
        votes = drone_votes(feats, snap)
        agree, net, active = self.tally(votes)
        await self.emit(self.stock_candidate(sym, fld))
        await self.emit({"type": "swarm", "assign": {d: sym for d in votes}})
        await self.phase("round1", sym)
        await self.emit({"type": "swarm_vote", "symbol": sym, "votes": votes,
                         "weights": {k: self.weight(k) for k in votes}, "agree": round(agree, 3),
                         "net": round(net, 3), "active": active})
        await self.pause(1.5)
        direction = "LONG" if net > 0 else "SHORT"
        ok = active >= 8 and agree >= 0.68 and (direction == "LONG" or self.s.allow_shorts)
        reason = (f"{agree:.0%} of {active} active drones push {direction}" if ok else
                  f"the swarm is split - {agree:.0%} of {active} active drones agree" if active >= 8 else
                  f"only {active} drones stirred")
        memo = await self.ask(
            P.BY_ID["vexa"],
            P.SHARED_RULES + "\n\nYou are Hive Queen Vexa of the Brood. Your drones are simple single-signal "
            "indicators. Speak as the hive in two short sentences, explaining what the swarm felt. Don't change the verdict.",
            f"{sym}: {sum(v > 0 for v in votes.values())} drones bullish, {sum(v < 0 for v in votes.values())} bearish, "
            f"{sum(v == 0 for v in votes.values())} silent. Verdict: {'TRADE ' + direction if ok else 'NO TRADE'} - {reason}.",
            DeskMemo,
            lambda: DeskMemo(summary=("The swarm hungers. We feed." if ok else "The swarm is restless, not hungry."),
                             what_would_change_our_mind="More drones waking on the same side."))
        await self.say("vexa", ("APPROVED - " if ok else "REJECTED - ") + f"{reason}. {memo.summary}", "decision", sym,
                       approved=ok)
        order = await self.execute_stock(sym, direction, fld, {d: {"stance": v} for d, v in votes.items()}) if ok else None
        await self.emit({"type": "verdict", "symbol": sym, "approved": order is not None,
                         "label": "APPROVED" if order else ("VETOED" if ok else "REJECTED")})
        stances = {d: "LONG" if v > 0 else "SHORT" if v < 0 else "PASS" for d, v in votes.items()}
        self.record("stock", sym, sym, feats["price"], stances, direction if ok else "PASS", order is not None)
        if order:
            await self.arena.publish()
        return order is not None

    async def gas_turn(self, fld: Field) -> bool:
        await self.phase("markets")
        traded = False
        found = []
        for c in fld.contracts:
            if c.ticker in self.broker.contracts:
                continue
            votes = contract_drones(c)
            agree, net, active = self.tally(votes)
            if active >= 2 and agree >= 0.99:
                side = "YES" if net > 0 else "NO"
                p = c.implied + (6 if side == "YES" else -6)
                e = edge_cents(p, side, c.ask(side))
                if e >= self.s.kalshi_min_edge_cents:
                    found.append((e, c, side, p, votes))
        await self.say("vexa", f"The swarm sniffs {len(fld.contracts)} gas vents... "
                               f"{len(found) or 'none'} smell of profit.", "research")
        for e, c, side, p, votes in sorted(found, key=lambda x: -x[0])[: self.s.kalshi_candidates]:
            await self.emit({"type": "candidate", "kind": "contract", "symbol": c.ticker, "title": c.title,
                             "price": c.implied, "contract": c.to_dict(), "side": side, "fair": p})
            await self.emit({"type": "edge", "symbol": c.ticker, "prob": p, "side": side, "edge": e, "ask": c.ask(side)})
            await self.say("vexa", f"APPROVED - \"{c.title}\": the swarm wants {side}. Edge {e:+.1f}¢.", "decision",
                           c.ticker, approved=True)
            order = await self.execute_contract(c, side, p, votes)
            await self.emit({"type": "verdict", "symbol": c.ticker, "approved": order is not None,
                             "label": "APPROVED" if order else "VETOED"})
            stances = {d: "YES" if v > 0 else "NO" if v < 0 else "PASS" for d, v in votes.items()}
            self.record("contract", c.ticker, c.title, c.implied, stances, side, order is not None, due=c.close_time)
            traded |= order is not None
        if traded:
            await self.arena.publish()
        return traded
