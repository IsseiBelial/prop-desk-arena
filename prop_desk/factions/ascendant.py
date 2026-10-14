"""The Ascendant: hyper-advanced, model-driven trading.

Every seer is a quantitative model computed in code - no opinions, only
probabilities. The Exarch pools them with weighted log-odds (a standard way to
combine forecasts) and acts only when the Convergence is clear. Claude, if
connected, writes only the Exarch's narrative; the decision is pure math.

  Seer Vael   - analog forecasting: find the past 20-day patterns most like today's,
                look at what happened over the next 10 days (k-nearest neighbours)
  Augur Kyth  - market regime: SPY trend + breadth of the watchlist, scaled by beta
  Prism Oriel - risk-adjusted momentum: 3-month return relative to its own noise
  Lumen Sar   - cross-sectional rank vs. the rest of the watchlist on momentum,
                short-term reversal, value (PEG) and quality (ROE)
"""
from __future__ import annotations

import math

import numpy as np

from ..agents import personas as P
from ..agents.schemas import DeskMemo
from ..kalshi import Contract, edge_cents
from .base import Faction, Field

SEERS = ["vael", "kyth", "oriel", "lumen"]


def _clip_p(p: float) -> float:
    return float(min(0.97, max(0.03, p)))


def _logit(p: float) -> float:
    p = _clip_p(p)
    return math.log(p / (1 - p))


def pool(probs: dict[str, float], weights: dict[str, float], extremize: float = 1.25) -> float:
    """Weighted average in log-odds space, slightly extremized (independent evidence compounds)."""
    tw = sum(weights.get(k, 1.0) for k in probs) or 1.0
    z = sum(weights.get(k, 1.0) * _logit(p) for k, p in probs.items()) / tw * extremize
    return 1 / (1 + math.exp(-z))


# ---------------------------------------------------------------- the seers
def analog_forecast(closes: np.ndarray, window: int = 20, horizon: int = 10, k: int = 15):
    """P(up over `horizon` days) and mean forward return from the k most similar past windows."""
    r = np.diff(np.log(closes))
    if len(r) < window + horizon + 40:
        return 0.5, 0.0
    def norm(x):
        sd = x.std() or 1e-9
        return (x - x.mean()) / sd
    today = norm(r[-window:])
    cands, fwd = [], []
    for i in range(window, len(r) - horizon):
        cands.append(norm(r[i - window:i]))
        fwd.append(r[i:i + horizon].sum())
    d = np.linalg.norm(np.array(cands) - today, axis=1)
    idx = np.argsort(d)[:k]
    f = np.array(fwd)[idx]
    # Shrink toward 50% - k-NN on one stock is noisy.
    p = 0.5 + ((f > 0).mean() - 0.5) * 0.7
    return float(p), float(f.mean() * 100)


def regime(fld: Field, sym: str):
    breadth = np.mean([fld.feats[s]["momentum"]["price_vs_sma50_pct"] > 0 for s in fld.watchlist if s in fld.feats]) \
        if fld.watchlist else 0.5
    tide = (0.4 if fld.risk_on else -0.4) + (breadth - 0.5) * 1.2
    b = fld.bench_bars["close"].pct_change().tail(60)
    s = fld.snaps[sym].bars["close"].pct_change().tail(60)
    beta = float(np.cov(s, b)[0, 1] / (b.var() or 1e-9)) if len(b) == len(s) else 1.0
    p = 0.5 + 0.2 * tide * max(0.3, min(beta, 1.6)) / 1.6
    return float(p), float(breadth), float(beta)


def risk_adjusted(f: dict):
    m, v = f["momentum"], f["volatility"]
    ret, vol = (m["return_3m_pct"] or 0) / 100, (v["realized_vol_20d_annual_pct"] or 30) / 100
    t = ret / (vol * math.sqrt(63 / 252) or 1e-9)
    p = 1 / (1 + math.exp(-0.8 * t))
    if vol > 0.6:  # very noisy stocks: trust the signal less
        p = 0.5 + (p - 0.5) * 0.5
    return float(p), float(t)


def cross_section(fld: Field, sym: str):
    def z(vals: dict, key):
        xs = np.array([v for v in vals.values() if v is not None], dtype=float)
        if len(xs) < 3 or vals.get(key) is None:
            return 0.0
        return float((vals[key] - xs.mean()) / (xs.std() or 1e-9))
    syms = [s for s in fld.watchlist if s in fld.feats]
    mom = {s: fld.feats[s]["momentum"]["return_6m_pct"] for s in syms}
    rev = {s: fld.feats[s]["momentum"]["return_1w_pct"] for s in syms}
    peg = {s: fld.snaps[s].fundamentals.get("peg") for s in syms}
    roe = {s: fld.snaps[s].fundamentals.get("roe_pct") for s in syms}
    score = 0.45 * z(mom, sym) - 0.2 * z(rev, sym) - 0.2 * z(peg, sym) + 0.15 * z(roe, sym)
    p = 1 / (1 + math.exp(-0.9 * score))
    return float(p), float(score)


class Ascendant(Faction):
    id = "ascendant"
    name = "The Ascendant"
    color = "#fbbf24"
    motto = "Probability is destiny."
    leader = "thalor"

    def readings(self, sym: str, fld: Field) -> dict:
        f = fld.feats[sym]
        pv, er = analog_forecast(fld.snaps[sym].bars["close"].to_numpy())
        pk, breadth, beta = regime(fld, sym)
        po, t = risk_adjusted(f)
        pl, cs = cross_section(fld, sym)
        return {
            "vael": (pv, f"analog forecast {er:+.1f}% over 10 days"),
            "kyth": (pk, f"breadth {breadth:.0%}, beta {beta:.2f}, market {'risk-on' if fld.risk_on else 'risk-off'}"),
            "oriel": (po, f"risk-adjusted momentum t={t:+.2f}"),
            "lumen": (pl, f"cross-sectional score {cs:+.2f}"),
        }

    def convergence(self, sym: str, fld: Field) -> tuple[float, dict]:
        r = self.readings(sym, fld)
        w = {k: self.weight(k) for k in SEERS}
        return pool({k: v[0] for k, v in r.items()}, w), r

    def quick_view(self, sym: str, fld: Field) -> float:
        return (self.convergence(sym, fld)[0] - 0.5) * 4

    @staticmethod
    def stance(p: float) -> str:
        return "LONG" if p > 0.55 else "SHORT" if p < 0.45 else "PASS"

    async def stock_turn(self, fld: Field) -> bool:
        await self.phase("research")
        universe = [s for s in (fld.focus or fld.watchlist) if s in fld.feats]
        # The seers warp to every mineral field at once.
        for i, u in enumerate(SEERS):
            await self.emit({"type": "warp", "agent": u, "symbols": universe[i::len(SEERS)] or universe})
        await self.pause(1.2)
        conv = {s: self.convergence(s, fld)[0] for s in universe}
        board = ", ".join(f"{s} {p:.0%}" for s, p in sorted(conv.items(), key=lambda kv: -kv[1]))
        await self.say("thalor", f"The Convergence across all fields: {board}.", "research")
        pool_ = [s for s in conv if s not in self.broker.positions]
        key = (lambda s: abs(conv[s] - 0.5)) if self.s.allow_shorts else (lambda s: conv[s])
        picks = fld.focus or sorted(pool_, key=key, reverse=True)[: self.s.candidates_per_session]
        traded = False
        for sym in picks:
            if sym in fld.feats:
                traded |= await self._consider(sym, fld)
        return traded

    async def _consider(self, sym: str, fld: Field) -> bool:
        feats = fld.feats[sym]
        p, r = self.convergence(sym, fld)
        await self.emit(self.stock_candidate(sym, fld))
        await self.phase("round1", sym)
        votes = {}
        for u in SEERS:
            pu, why = r[u]
            st = self.stance(pu)
            votes[u] = st
            await self.emit({"type": "model", "agent": u, "symbol": sym, "p": round(pu, 3), "stance": st,
                             "weight": self.weight(u)})
            await self.say(u, f"[{st} {abs(pu - 0.5) * 200:.0f}] P(up) {pu:.0%} - {why}.", "opinion")
        await self.phase("vote", sym)
        aligned = sum(v == self.stance(p) for v in votes.values())
        direction = self.stance(p)
        ok = direction != "PASS" and aligned >= 3 and (direction == "LONG" or self.s.allow_shorts)
        reason = (f"Convergence {p:.0%} {direction}, {aligned}/4 seers aligned" if ok else
                  f"Convergence {p:.0%} is not decisive ({aligned}/4 aligned)")
        await self.emit({"type": "convergence", "symbol": sym, "p": round(p, 3), "approved": ok,
                         "direction": direction, "reason": reason})
        memo = await self.ask(
            P.BY_ID["thalor"],
            P.SHARED_RULES + "\n\nYou are Exarch Thalor of the Ascendant, a faction that trades purely on quantitative "
            "models. Explain the models' verdict in two sentences, in plain English, without changing it.",
            f"{sym}: pooled probability of rising {p:.0%}. Models: " +
            "; ".join(f"{P.BY_ID[u].role} {r[u][0]:.0%} ({r[u][1]})" for u in SEERS) + f". Verdict: {reason}.",
            DeskMemo,
            lambda: DeskMemo(summary=("The patterns converge. We act." if ok else "The patterns do not converge. We wait."),
                             what_would_change_our_mind="A shift in the pooled probability past 55%."))
        await self.say("thalor", ("APPROVED - " if ok else "REJECTED - ") + f"{reason}. {memo.summary}", "decision", sym,
                       approved=ok)
        detail = {u: {"stance": v} for u, v in votes.items()}
        order = await self.execute_stock(sym, direction, fld, detail) if ok else None
        # Very high conviction (65%+ either way, 3+ seers aligned) also buys an option.
        # Below 35% that's a put: the Ascendant's way to bet on a fall without shorting.
        option = None
        if aligned >= 3 and (p >= 0.65 or p <= 0.35):
            option = await self.option_play(sym, "call" if p >= 0.65 else "put", fld, detail, f"convergence {p:.0%}")
        acted = order is not None or option is not None
        await self.emit({"type": "verdict", "symbol": sym, "approved": acted,
                         "label": "APPROVED" if acted else ("VETOED" if ok else "REJECTED")})
        self.record("stock", sym, sym, feats["price"], votes, direction if (ok or option) else "PASS", acted)
        if acted:
            await self.arena.publish()
        return acted

    # ---------------------------------------------------------------- prediction markets
    def contract_readings(self, c: Contract) -> dict:
        imp = c.implied / 100
        # Lumen: favourite-longshot debiasing (long shots overpriced, favourites underpriced).
        pl = imp + (0.04 if imp > 0.8 else -0.04 if imp < 0.2 else 0.0)
        # Oriel: sharp one-day repricing partly mean-reverts.
        move = (c.last_price - c.previous_price) / 100
        po = imp - 0.35 * move if abs(move) > 0.06 else imp
        # Kyth: deep, tight markets are efficient - trust the price.
        pk = imp
        # Vael: no history for a binary contract - defers to the crowd.
        pv = imp
        return {"vael": pv, "kyth": pk, "oriel": po, "lumen": pl}

    async def gas_turn(self, fld: Field) -> bool:
        await self.phase("markets")
        best = []
        for c in fld.contracts:
            if c.ticker in self.broker.contracts:
                continue
            r = self.contract_readings(c)
            spread = (c.yes_ask - c.yes_bid) / 100
            weights = {k: self.weight(k) for k in r}
            weights["kyth"] *= 2 if c.volume > 100000 and spread <= 0.02 else 1
            p = sum(weights[k] * v for k, v in r.items()) / sum(weights.values()) * 100
            for side in ("YES", "NO"):
                e = edge_cents(p, side, c.ask(side))
                if e >= self.s.kalshi_min_edge_cents:
                    best.append((e, c, side, p, r))
        await self.say("thalor", f"The gas fields: {len(fld.contracts)} markets read, "
                                 f"{len(best)} with edge after fees.", "research")
        traded = False
        for e, c, side, p, r in sorted(best, key=lambda x: -x[0])[: self.s.kalshi_candidates]:
            await self.emit({"type": "candidate", "kind": "contract", "symbol": c.ticker, "title": c.title,
                             "price": c.implied, "contract": c.to_dict(), "side": side, "fair": round(p)})
            votes = {u: ("YES" if v * 100 > c.yes_ask else "NO" if v * 100 < c.yes_bid else "PASS") for u, v in r.items()}
            await self.emit({"type": "edge", "symbol": c.ticker, "prob": round(p, 1), "side": side, "edge": e,
                             "ask": c.ask(side)})
            await self.say("thalor", f"APPROVED - \"{c.title}\": we see {p:.0f}% vs a {c.ask(side)}¢ price. "
                                     f"Edge {e:+.1f}¢ after fees.", "decision", c.ticker, approved=True)
            order = await self.execute_contract(c, side, p, votes)
            await self.emit({"type": "verdict", "symbol": c.ticker, "approved": order is not None,
                             "label": "APPROVED" if order else "VETOED"})
            self.record("contract", c.ticker, c.title, c.implied, votes, side, order is not None, due=c.close_time)
            traded |= order is not None
        if traded:
            await self.arena.publish()
        return traded
