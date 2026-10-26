"""The Kronos Watch: trades what Kronos, a foundation model for candlesticks, foresees.

Kronos (Shi et al. 2025, https://arxiv.org/abs/2508.02739, MIT License) was trained on
12 billion candles from 45 exchanges. For every symbol on the map the Watch asks it
for many possible next weeks (the "paths", see forecaster.py). Three wardens read
those paths three different ways, all in code:

  Oracle Ione    - direction: the share of paths that end higher than today
  Archivist Sol  - rank: today's Kronos expected return against every other symbol.
                   This is how the paper traded (buy the top-ranked stocks); a rank
                   survives Kronos being too bullish or too bearish on everything at once
  Walker Venn    - the trade itself: walks each path through the Quartermaster's real
                   stop (2x ATR) and target (3x ATR) and averages the result in R

Keeper Aurel acts when Ione's share clears PROP_DESK_KRONOS_MIN_PROB (default 60%)
and at least 2 of the 3 wardens agree. Then Claude, as Interpreter Cass, reads the
forecast next to what Kronos can't see (headlines, fundamentals, the earnings date)
and may veto. Cass can stop a trade but never start one, and Cass's own call is
graded separately, so the scoreboard shows whether Claude's veto is worth having.
Without Claude, Kronos decides alone.

Kronos only reads candles, so the Watch doesn't trade prediction markets.
"""
from __future__ import annotations

import asyncio
import math

from .. import forecaster
from ..agents import personas as P
from ..agents.schemas import KronosReview
from ..forecaster import Forecast
from .base import Faction, Field

WARDENS = ["ione", "sol", "venn"]
FALLBACK = "Claude unavailable; Kronos decides alone."


def implausible(fc: Forecast, atr_pct: float | None) -> bool:
    """A typical path moving more than ~5x the stock's normal range means the forecast is broken."""
    daily = max(atr_pct or 2.0, 0.2)
    return abs(fc.median_pct) > max(10.0, 5 * daily * math.sqrt(fc.horizon))


def ranks(fcs: dict[str, Forecast]) -> dict[str, float]:
    """Each symbol's place (0 = worst, 1 = best) by Kronos's expected return today."""
    order = sorted(fcs, key=lambda s: fcs[s].expected_pct)
    n = len(order)
    return {s: i / (n - 1) if n > 1 else 0.5 for i, s in enumerate(order)}


def warden_votes(fc: Forecast, rank: float | None, atr: float, min_prob: float,
                 field: int = 0) -> dict[str, tuple[str, float, str]]:
    """Every warden's (stance, reading, explanation). `rank` is from ranks(); `field` is how many were ranked."""
    p = fc.p_up
    ione = "LONG" if p >= min_prob else "SHORT" if p <= 1 - min_prob else "PASS"
    if rank is None:  # too few symbols to rank against
        sol = "PASS"
    else:
        sol = ("LONG" if rank >= 0.75 and fc.expected_pct > 0 else
               "SHORT" if rank <= 0.25 and fc.expected_pct < 0 else "PASS")
    up, dn = fc.walk(2 * atr, 3 * atr, 1), fc.walk(2 * atr, 3 * atr, -1)
    venn = ("LONG" if up["r"] >= 0.15 and up["win"] >= 0.5 else
            "SHORT" if dn["r"] >= 0.15 and dn["win"] >= 0.5 else "PASS")
    lo, hi = fc.band_pct
    return {
        "ione": (ione, p, f"{p:.0%} of {len(fc.closes)} paths end higher in {fc.horizon} days "
                          f"(typical {fc.median_pct:+.1f}%, range {lo:+.1f}% to {hi:+.1f}%)"),
        "sol": (sol, rank if rank is not None else 0.5,
                f"expected {fc.expected_pct:+.2f}%, " + (f"#{round((1 - rank) * (field - 1)) + 1} of {field} symbols"
                                                         if rank is not None else "too few symbols to rank")),
        "venn": (venn, up["win"], f"a long wins on {up['win']:.0%} of paths, stopped on {up['stopped']:.0%}, "
                                  f"average {up['r']:+.2f}R (a short: {dn['r']:+.2f}R)"),
    }


class KronosWatch(Faction):
    id = "kronos"
    name = "The Kronos Watch"
    color = "#a3e635"
    motto = "Many tomorrows. One trade."
    leader = "aurel"

    def __init__(self, arena):
        super().__init__(arena)
        self.engine = None
        self.problem: str | None = None  # why Kronos couldn't run, said once per round
        self._fcs: dict[str, Forecast] = {}
        self._fld = None

    def public(self) -> dict:
        return {**super().public(), "forecaster": self.s.kronos_model.split("/")[-1]}

    # ------------------------------------------------------------ Kronos
    async def _ready(self) -> bool:
        if self.engine is None:
            if not forecaster.available():
                self.problem = "PyTorch isn't installed. Run: pip install -r prop_desk/requirements-kronos.txt"
                return False
            self.engine = forecaster.make_engine(self.s)
        try:
            await asyncio.get_running_loop().run_in_executor(None, self.engine.load)
            self.problem = None
            return True
        except Exception as e:
            self.problem = f"couldn't load {self.engine.model_id} ({type(e).__name__}: {e})"
            return False

    async def forecasts(self, syms: list[str], fld: Field) -> dict[str, Forecast]:
        """Kronos forecasts for these symbols, once per round each."""
        if fld is not self._fld:
            self._fld, self._fcs = fld, {}
        loop = asyncio.get_running_loop()
        for sym in syms:
            if sym in self._fcs or sym not in fld.snaps:
                continue
            snap = fld.snaps[sym]
            try:
                fc = await loop.run_in_executor(None, self.engine.forecast, sym, snap.bars,
                                                self.s.call_horizon_days, snap.asset)
            except Exception as e:
                await self.emit({"type": "notice", "text": f"Kronos couldn't forecast {sym}: {e}"})
                continue
            if implausible(fc, fld.feats[sym]["volatility"]["atr_pct"]):
                await self.emit({"type": "notice", "text": f"Kronos's {sym} forecast ({fc.median_pct:+.1f}% in "
                                                           f"{fc.horizon} days) is implausible - ignored."})
                continue
            self._fcs[sym] = fc
        return {s: self._fcs[s] for s in syms if s in self._fcs}

    def quick_view(self, sym: str, fld: Field) -> float:
        fc = self._fcs.get(sym) if fld is self._fld else None
        return (fc.p_up - 0.5) * 4 if fc else 0.0  # no forecast: hold

    async def review_positions(self, fld: Field):
        if self.broker.positions and await self._ready():
            await self.forecasts(list(self.broker.positions), fld)
        await super().review_positions(fld)

    # ------------------------------------------------------------ the turn
    async def stock_turn(self, fld: Field) -> bool:
        await self.phase("research")
        if not await self._ready():
            await self.say("aurel", f"The clock is stopped: {self.problem} The Watch sits this round out.", "research")
            return False
        universe = [s for s in (fld.focus or fld.watchlist) if s in fld.feats]
        await self.emit({"type": "hunt", "assign": {u: universe[i % len(universe)] for i, u in enumerate(WARDENS)}}
                        if universe else {"type": "notice", "text": "Nothing to forecast."})
        await self.say("aurel", f"Asking {self.engine.name} for {self.s.kronos_samples} possible next "
                                f"{self.s.call_horizon_days} days on {len(universe)} symbols.", "research")
        fcs = await self.forecasts(universe, fld)
        if not fcs:
            return False
        rk = ranks(fcs) if len(fcs) >= 4 else {}
        board = ", ".join(f"{s} {fcs[s].p_up:.0%}↑ {fcs[s].expected_pct:+.1f}%"
                          for s in sorted(fcs, key=lambda s: -fcs[s].expected_pct)[:8])
        await self.say("aurel", f"Tomorrow, as Kronos draws it (best first): {board}.", "research")
        pool_ = [s for s in fcs if s not in self.broker.positions]
        key = (lambda s: abs(fcs[s].p_up - 0.5)) if self.s.allow_shorts else (lambda s: fcs[s].expected_pct)
        picks = [s for s in (fld.focus or sorted(pool_, key=key, reverse=True)[: self.s.candidates_per_session])
                 if s in fcs]
        traded = False
        for sym in picks:
            traded |= await self._consider(sym, fcs[sym], rk.get(sym), len(rk), fld)
        return traded

    async def _consider(self, sym: str, fc: Forecast, rank: float | None, field: int, fld: Field) -> bool:
        snap, feats = fld.snaps[sym], fld.feats[sym]
        atr = feats["volatility"]["atr14"]
        await self.emit(self.stock_candidate(sym, fld))
        await self.phase("round1", sym)
        await self.emit({"type": "forecast", "symbol": sym, "model": self.engine.name, "rank": rank,
                         "walk": fc.walk(2 * atr, 3 * atr, 1), **fc.public(snap.bars["close"])})
        votes = warden_votes(fc, rank, atr, self.s.kronos_min_prob, field)
        for u in WARDENS:
            st, reading, why = votes[u]
            await self.emit({"type": "kvote", "agent": u, "symbol": sym, "stance": st, "value": round(reading, 3),
                             "detail": why, "weight": self.weight(u)})
            await self.say(u, f"[{st}] {why}.", "opinion", sym)
        await self.phase("vote", sym)
        stances = {u: v[0] for u, v in votes.items()}
        direction = stances["ione"]
        aligned = sum(v == direction for v in stances.values()) if direction != "PASS" else 0
        ok = aligned >= 2 and (direction == "LONG" or self.s.allow_shorts)
        # Very strong agreement also buys an option; on the downside that's a put, a way to bet
        # on a fall without shorting.
        strong = aligned == 3 and abs(fc.p_up - 0.5) >= 0.25
        reason = (f"{aligned}/3 wardens see {direction}, {fc.p_up:.0%} of paths up" if ok or strong else
                  f"{aligned}/3 wardens see {direction} and shorting is off" if aligned >= 2 else
                  f"the paths don't agree ({fc.p_up:.0%} up, {aligned}/3 aligned)")
        review = None
        if (ok or strong) and self.brain and self.s.kronos_claude_veto:
            review = await self._interpret(sym, fc, votes, direction, fld)
        confirmed = review is None or review.verdict == "CONFIRM"
        if review is not None:
            await self.emit({"type": "kvote", "agent": "cass", "symbol": sym, "stance": review.view,
                             "verdict": review.verdict, "detail": review.headline, "weight": self.weight("cass")})
            await self.say("cass", f"[{review.verdict}] {review.headline} {review.reasoning}", "opinion", sym)
        if (ok or strong) and not confirmed:
            reason += f", but the Interpreter vetoed it: {review.headline}"
        go = (ok or strong) and confirmed
        await self.say("aurel", ("APPROVED - " if go else "REJECTED - ") + reason + "." +
                       ("" if review is not None or not go or not self.brain else " (Claude's veto is off.)"),
                       "decision", sym, approved=go)
        detail = {u: {"stance": s} for u, s in stances.items()}
        order = await self.execute_stock(sym, direction, fld, detail) if ok and confirmed else None
        option = None
        if strong and confirmed:
            option = await self.option_play(sym, "call" if fc.p_up > 0.5 else "put", fld, detail,
                                            f"{fc.p_up:.0%} of Kronos paths up, all wardens agree")
        acted = order is not None or option is not None
        await self.emit({"type": "verdict", "symbol": sym, "approved": acted,
                         "label": "APPROVED" if acted else ("VETOED" if go or review is not None and (ok or strong)
                                                            else "REJECTED")})
        if review is not None and review.reasoning != FALLBACK:  # only Claude's real calls are graded
            stances["cass"] = review.view
        self.record("stock", sym, sym, feats["price"], stances, direction if go else "PASS", acted)
        if acted:
            await self.arena.publish()
        return acted

    async def _interpret(self, sym: str, fc: Forecast, votes: dict, direction: str, fld: Field) -> KronosReview:
        snap, f = fld.snaps[sym], fld.feats[sym]
        fu = snap.fundamentals or {}
        lo, hi = fc.band_pct
        heads = "; ".join(f"{h.title} ({h.source}, {str(h.published)[:10]})" for h in snap.news[:6]) or "none"
        facts = ", ".join(f"{k} {fu[k]}" for k in ("sector", "next_earnings", "pe", "peg", "earnings_growth_pct",
                                                    "revenue_growth_pct") if fu.get(k) not in (None, ""))
        prompt = (
            f"{sym} ({snap.asset}) at {f['price']}. Kronos drew {len(fc.closes)} possible next {fc.horizon} trading "
            f"days from the last {min(len(snap.bars), self.engine.context)} daily candles: {fc.p_up:.0%} end higher; "
            f"typical {fc.median_pct:+.2f}%, average {fc.expected_pct:+.2f}%, 10th-90th percentile {lo:+.1f}% to "
            f"{hi:+.1f}%; it expects {fc.vol_pct:.0f}% annualized volatility vs {f['volatility']['realized_vol_20d_annual_pct']}% "
            f"realized over 20 days.\nWardens: " + "; ".join(f"{P.BY_ID[u].name} {v[0]} - {v[2]}" for u, v in votes.items()) +
            f".\nKronos's call: {direction}.\n\nWhat Kronos can't see:\n- Market: SPY "
            f"{'above' if fld.risk_on else 'below'} its 200-day average.\n- Headlines: {heads}.\n- Facts: {facts or 'none'}."
            f"\n- Recent: 1-week {f['momentum']['return_1w_pct']}%, 3-month {f['momentum']['return_3m_pct']}%, "
            f"RSI {f['momentum']['rsi14']}.")
        return await self.ask(
            P.BY_ID["cass"],
            P.SHARED_RULES + "\n\nYou are Interpreter Cass of the Kronos Watch. Kronos is a foundation model "
            "trained only on price candles: it sees patterns, but no news, no earnings dates, no fundamentals. "
            "Your job is to catch what it can't see. VETO only for a concrete reason, such as earnings or another "
            "scheduled event inside the forecast window, news that changes the story, or data that looks broken. "
            "Don't veto just because the future is uncertain - the wardens already require agreement. "
            "Separately, give your own view of the next few days (LONG, SHORT or PASS); it is graded on its own.",
            prompt, KronosReview,
            lambda: KronosReview(verdict="CONFIRM", view=direction, headline="No reason to overrule the clock.",
                                 reasoning=FALLBACK, concerns=[]))
