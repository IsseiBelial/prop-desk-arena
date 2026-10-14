"""The Syndicate: setup hunters.

Each hunter knows exactly one published swing-trading setup and grades it A, B or C
(or finds nothing). The rules are ported from tradermonty/claude-trading-skills
(MIT License, https://github.com/tradermonty/claude-trading-skills) and run on the
same Yahoo bars every faction sees - no extra data keys.

  Rook  - Momentum Burst (stockbee-momentum-burst-screener)
  Mara  - Volatility Contraction Pattern (vcp-screener: Trend Template + contractions)
  Jax   - Gap & Drift (stockbee-episodic-pivot-analyzer on the gap day, pead-screener after it)
  Sable - Exhaustion Hammer (stockbee-exhaustion-hammer-screener)
  Theo  - Growth Leader (canslim-screener, the C/N/S/L/M parts our data supports)

The Lookout sets the market posture from SPY distribution days (ibd-distribution-day-monitor),
follow-through days (ftd-detector) and S&P 500 breadth (tradermonty's public breadth data).
The Fixer pays a bounty only when enough graded setups line up for that posture, and
only if the drawdown circuit breaker and the pre-trade discipline gate allow a new entry.
Claude, if connected, only narrates the Fixer's verdict.

Where our data can't support a rule exactly, the substitute is noted next to it.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ..agents import personas as P
from ..agents.schemas import DeskMemo
from ..market_data import Snapshot
from .base import Faction, Field

log = logging.getLogger(__name__)

HUNTERS = ["burst", "vcp", "pivot", "hammer", "growth"]
POINTS = {"A": 3.0, "B": 2.0, "C": 1.0}
# Setup points the Fixer needs before paying, by market posture.
NEEDED = {"green": 3.0, "yellow": 4.0, "red": 6.0}
# The screeners' market gate: allowed / neutral / restrictive.
GATE = {"green": 5, "yellow": 3, "red": 0}
BREADTH_URL = "https://tradermonty.github.io/market-breadth-analysis/market_breadth_data.csv"


def _band(x: float, steps: list[tuple[float, float]], default: float = 0.0) -> float:
    """First points value whose threshold x reaches (thresholds descending)."""
    for th, pts in steps:
        if x >= th:
            return pts
    return default


def _close_loc(o, h, l, c) -> float:
    return 50.0 if h == l else (c - l) / (h - l) * 100


def _arrays(bars: pd.DataFrame):
    return (bars["open"].to_numpy(float), bars["high"].to_numpy(float), bars["low"].to_numpy(float),
            bars["close"].to_numpy(float), bars["volume"].to_numpy(float))


def _ret(c: np.ndarray, p: int) -> float | None:
    return (c[-1] / c[-1 - p] - 1) * 100 if len(c) > p else None


def _setup(grade: str | None, score: float, detail: str, stop: float | None = None) -> dict:
    return {"grade": grade, "score": round(score, 1), "detail": detail, "stop": stop}


def _none(why: str) -> dict:
    return _setup(None, 0, why)


# ---------------------------------------------------------------- Rook: Momentum Burst
def momentum_burst(bars: pd.DataFrame, gate: int) -> dict:
    o, h, l, c, v = _arrays(bars)
    n = len(c)
    if n < 25:
        return _none("not enough history")
    C, prev, vol, pvol = c[-1], c[-2], v[-1], v[-2]
    gain = (C / prev - 1) * 100
    floor = vol >= 100_000
    four = gain >= 4 and vol > pvol and floor
    dollar = C - o[-1] >= 0.90 and floor
    rng = h[-1] - l[-1]
    prev_gain = (c[-2] / c[-3] - 1) * 100
    rexp = rng > max(h[-4:-1] - l[-4:-1]) and prev_gain <= 2 and vol > pvol and floor
    if not (four or rexp or dollar):
        return _none("no burst today")
    stop, risk = l[-1], (C - l[-1]) / C * 100
    if C < 5 or C <= stop or risk > 10:
        return _none(f"burst, but stop {risk:.1f}% away" if risk > 10 else "burst fails the filters")
    # Base: the longest quiet stretch before today (<= 15% wide, <= 5% average daily range).
    base_days, width, dry = 0, None, False
    for w in range(3, 21):
        if n < 2 * w + 2:
            break
        ph, pl, pc = h[-1 - w:-1], l[-1 - w:-1], c[-1 - w:-1]
        wd = (ph.max() - pl.min()) / c[-2] * 100
        if wd <= 15 and np.mean((ph - pl) / pc * 100) <= 5:
            base_days, width = w, wd
            dry = v[-1 - w:-1].mean() <= 0.85 * v[-1 - 2 * w:-1 - w].mean()
    if width is None:
        width = (h[-4:-1].max() - l[-4:-1].min()) / c[-2] * 100
    trig = min(20, (14 + (3 if gain >= 7 else 0) if four else 0) + (10 if rexp else 0) + (8 if dollar else 0)
               + (2 if vol >= 9e6 else 0))
    r = max(vol / pvol if pvol else 0, vol / (v[-21:-1].mean() or 1))
    volume = _band(r, [(3, 15), (2, 12), (1.5, 9), (1, 6)])
    setup = _band(base_days, [(10, 10), (5, 8), (3, 6), (1, 3)]) + _band(-width, [(-8, 7), (-12, 5), (-15, 3)])
    setup += 5 if (h[-2] - l[-2]) / c[-2] * 100 <= 3 else 4 if c[-2] < o[-2] else 0
    setup += 3 if dry else 0
    cl = _close_loc(o[-1], h[-1], l[-1], C)
    close_q = _band(cl, [(90, 10), (80, 9), (70, 7), (60, 5), (50, 3)])
    risk_pts = _band(-risk, [(-2.5, 15), (-4, 12), (-6, 8), (-8, 5), (-10, 2)])
    ups = 0
    for i in range(5):  # consecutive up closes right before today
        if c[-2 - i] > c[-3 - i]:
            ups += 1
        else:
            break
    fail = 10 - (4 if ups >= 3 else 0) - (4 if any(c[-2 - i] / c[-3 - i] - 1 <= -0.04 for i in range(5)) else 0) \
        - (3 if width > 15 else 0) - (2 if cl < 50 else 0)
    score = trig + volume + setup + close_q + risk_pts + max(0, fail) + gate
    grade = "A" if score >= 80 else "B" if score >= 70 else "C" if score >= 55 else None
    kind = "4% breakout" if four else "range expansion" if rexp else "dollar breakout"
    return _setup(grade, score, f"{kind} {gain:+.1f}%, {r:.1f}x vol, {base_days}d base", stop)


# ---------------------------------------------------------------- Mara: Volatility Contraction Pattern
def _rs_score(weighted: float) -> tuple[float, float]:
    for th, sc, rank in ((50, 100, 99), (30, 95, 95), (20, 90, 90), (10, 80, 80), (5, 70, 70), (0, 60, 60),
                         (-5, 50, 50), (-10, 40, 40), (-20, 20, 25)):
        if weighted >= th:
            return sc, rank
    return 0, 10


def _weighted_rs(c: np.ndarray, b: np.ndarray, periods, weights) -> float:
    tot = tw = 0.0
    for p, w in zip(periods, weights):
        if len(c) > p and len(b) > p:
            tot += w * ((_ret(c, p) or 0) - (_ret(b, p) or 0))
            tw += w
    return tot / tw if tw else 0.0


def _zigzag(h: np.ndarray, l: np.ndarray, c: np.ndarray):
    """ATR ZigZag swing points (threshold 1.5 x ATR14); fractal swings as a fallback."""
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    thr = 1.5 * tr[-14:].mean()
    highs, lows = [], []
    i0 = int(np.argmax(h[:14]))
    seek_high, ext, ext_i = True, h[i0], i0
    for i in range(i0 + 1, len(h)):
        if seek_high:
            if h[i] > ext:
                ext, ext_i = h[i], i
            elif ext - l[i] >= thr:
                highs.append((ext_i, ext))
                seek_high, ext, ext_i = False, l[i], i
        else:
            if l[i] < ext:
                ext, ext_i = l[i], i
            elif h[i] - ext >= thr:
                lows.append((ext_i, ext))
                seek_high, ext, ext_i = True, h[i], i
    if not highs or not lows:
        highs = [(i, h[i]) for i in range(5, len(h) - 5) if h[i] == h[i - 5:i + 6].max()]
        lows = [(i, l[i]) for i in range(5, len(l) - 5) if l[i] == l[i - 5:i + 6].min()]
    return highs, lows


def _contractions(h, l, c):
    highs, lows = _zigzag(h, l, c)
    best = None
    for hi, H1 in sorted(highs, key=lambda x: -x[1])[:3]:
        cons, cur_i, cur_h = [], hi, H1
        while len(cons) < 4:
            nxt_h = next((x for x in highs if x[0] > cur_i), None)
            cands = [x for x in lows if x[0] > cur_i and (nxt_h is None or x[0] < nxt_h[0])]
            low = next((x for x in cands if x[0] - cur_i >= 5), None)
            if low is None:
                break
            cons.append({"hi_i": cur_i, "high": cur_h, "lo_i": low[0], "low": low[1], "depth": (cur_h - low[1]) / cur_h * 100})
            nh = next((x for x in highs if x[0] > low[0]), None)
            if nh is None or abs(nh[1] / H1 - 1) > 0.05:
                break
            cur_i, cur_h = nh
        n = len(cons)
        valid = n >= 2 and cons[0]["depth"] >= 10 and \
            all(cons[k]["depth"] / cons[k - 1]["depth"] <= 0.70 for k in range(1, n)) and \
            cons[-1]["lo_i"] - cons[0]["hi_i"] >= 15
        if not valid:
            score = min(40, 15 * n)
        else:
            ratios = [cons[k]["depth"] / cons[k - 1]["depth"] for k in range(1, n)]
            score = {2: 60, 3: 80}.get(n, 90) + (10 if cons[-1]["depth"] < 5 else 0) \
                + (10 if np.mean(ratios) < 0.4 else 0) - (10 if cons[0]["depth"] > 30 else 0)
        key = (valid, max(0, min(100, score)), n)
        if cons and (best is None or key > best[0]):
            best = (key, cons)
    return (best[0][0], best[0][1], best[1]) if best else (False, 0, [])


def vcp(bars: pd.DataFrame, bench: pd.DataFrame) -> dict:
    o, h, l, c, v = _arrays(bars)
    if len(c) < 222:
        return _none("not enough history")
    P_ = c[-1]
    s50, s150, s200 = c[-50:].mean(), c[-150:].mean(), c[-200:].mean()
    s200_prev = c[-222:-22].mean()
    hi52, lo52 = h[-252:].max(), l[-252:].min()
    b = bench["close"].to_numpy(float)
    wrs = _weighted_rs(c, b, (63, 126, 189, 252), (.4, .2, .2, .2))
    rs_sc, rs_rank = _rs_score(wrs)
    crit = [P_ > s150 and P_ > s200, s150 > s200, s200 > s200_prev, P_ > s50,
            (P_ - lo52) / lo52 >= 0.25, (hi52 - P_) / hi52 <= 0.25, rs_rank > 70]
    if sum(crit) * 14.3 < 85:
        return _none(f"not a stage-2 uptrend ({sum(crit)}/7)")
    if P_ <= 10 or v[-50:].mean() < 200_000 or P_ / lo52 - 1 < 0.20 or hi52 / P_ - 1 > 0.30 \
            or np.mean((h[-10:] - l[-10:]) / c[-10:]) < 0.01:
        return _none("fails the VCP pre-filter")
    ext = (P_ / s50 - 1) * 100 - 8
    tt = min(100, sum(crit) * 14.3) - (0 if ext < 0 else 5 if ext < 4 else 10 if ext < 10 else 15 if ext < 17 else 20)
    w = 120
    valid, con_score, cons = _contractions(h[-w:], l[-w:], c[-w:])
    if not cons:
        return _none("no contractions found")
    pivot, stop = cons[-1]["high"], cons[-1]["low"] * 0.99
    avg50 = v[-50:].mean()
    dry = v[-11:-1].mean() / avg50
    vol_sc = _band(-dry, [(-0.30, 90), (-0.50, 75), (-0.70, 60), (-1.0, 40)], 20)
    brk_vol = P_ > pivot and v[-1] / avg50 >= 1.5
    vol_sc += 10 if brk_vol else 0
    heavy = [int(np.sign(c[i] - c[i - 1])) for i in range(len(c) - 20, len(c)) if v[i] > avg50]
    vol_sc += 10 if sum(heavy) > 3 else -10 if sum(heavy) < -3 else 0
    base = len(c) - w
    cvol = [v[base + x["hi_i"]:base + x["lo_i"] + 1].mean() for x in cons]
    vol_sc += 10 if all(cvol[k] < cvol[k - 1] for k in range(1, len(cvol))) and len(cvol) > 1 else 0
    vol_sc = max(0, min(100, vol_sc))
    d = (P_ / pivot - 1) * 100
    if P_ <= stop:
        piv_sc = 0
    elif d > 0:
        piv_sc = 20 if d > 20 else 35 if d > 10 else 50 if d > 5 else 65 if d > 3 else 90
    else:
        piv_sc = _band(d, [(-2, 90), (-5, 75), (-8, 60), (-10, 45), (-15, 30)], 10)
    piv_sc += 10 if brk_vol and 0 < d <= 5 else 0
    if P_ < s50 < s200:
        state = "invalid"
    elif P_ < cons[-1]["low"] or P_ < s50:
        state = "damaged"
    elif P_ / s200 - 1 > 0.5 or d > 10:
        state = "overextended"
    elif d > 5:
        state = "extended"
    elif d > 3 or (0 <= d <= 3 and not brk_vol):
        state = "early post-breakout"
    elif d >= 0:
        state = "breakout"
    else:
        state = "pre-breakout"
    comp = 0.25 * tt + 0.25 * con_score + 0.20 * vol_sc + 0.15 * piv_sc + 0.15 * rs_sc
    wide_loose = cons[-1]["depth"] > 15 and cons[-1]["lo_i"] - cons[-1]["hi_i"] < 10
    if state in ("invalid", "damaged"):
        comp = min(comp, 49)
    elif state == "overextended":
        comp = min(comp, 59)
    elif state == "extended" or wide_loose or (not valid and comp >= 70):
        comp = min(comp, 69)
    elif state == "early post-breakout":
        comp = min(comp, 89)
    risk = (P_ - stop) / P_ * 100
    entry_ready = state in ("pre-breakout", "breakout") and valid and -8 <= d <= 3 and dry <= 1.0 and 0 < risk <= 15
    grade = "A" if comp >= 80 else "B" if comp >= 70 else "C" if comp >= 60 else None
    if grade in ("A", "B") and not entry_ready:
        grade = "C"  # a good chart, but not at a buy point today
    return _setup(grade, comp, f"{len(cons)} contractions, {d:+.1f}% vs pivot, {state}", stop)


# ---------------------------------------------------------------- Jax: Gap & Drift
_CATALYSTS = [  # (type, base points, headline pattern) from the episodic-pivot analyzer
    ("guidance raise", 95, r"raises? (guidance|outlook|forecast)|guidance (raise|hike)"),
    ("FDA", 94, r"\bfda\b|approval"), ("earnings", 88, r"earnings|results|beats?|quarter|eps"),
    ("M&A", 84, r"acquir|merger|buyout|takeover|to buy"), ("regulatory", 82, r"regulat|clearance|license"),
    ("contract", 76, r"contract|award|order"), ("partnership", 72, r"partner|collaborat|alliance"),
    ("product", 66, r"launch|unveil|product"), ("analyst", 54, r"upgrade|price target|initiat"),
]


def _catalyst(snap: Snapshot) -> tuple[str, float]:
    heads = " ".join(x.title.lower() for x in snap.news)
    for name, base, pat in _CATALYSTS:
        if re.search(pat, heads):
            bonus = (6 if re.search(r"first|record|accelerat", heads) else 0) \
                - (12 if re.search(r"dilution|offering|lawsuit", heads) else 0)
            return name, (base + bonus) * 0.35
    return "unknown", 35 * 0.35


def _weekly(bars: pd.DataFrame, event_i: int) -> tuple[pd.DataFrame, int]:
    """Weekly candles (ISO, Monday-start weeks), and the row of the event's week."""
    idx = bars.index.tz_localize(None) if getattr(bars.index, "tz", None) is not None else bars.index
    wk = idx.to_period("W-SUN")
    keep = (np.arange(len(bars)) >= event_i) | (wk != wk[event_i])  # drop the event week's days before the event
    df = bars[keep].copy()
    df["wk"] = wk[keep]
    out = df.groupby("wk").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                               close=("close", "last"), volume=("volume", "sum"))
    return out, out.index.get_loc(wk[event_i])


def gap_drift(snap: Snapshot) -> dict:
    """Day 1: score the gap as an episodic pivot. After it: score the drift the PEAD way.

    Substitute: the skills key everything on a known earnings/event date, which our free
    data doesn't have. A gap of 3%+ on 1.5x+ average volume stands in for the event day,
    and today's headlines stand in for the catalyst.
    """
    bars = snap.bars
    o, h, l, c, v = _arrays(bars)
    n = len(c)
    ev = None
    for i in range(n - 1, max(n - 21, 51), -1):
        if o[i] / c[i - 1] - 1 >= 0.03 and v[i] >= 1.5 * v[i - 50:i].mean():
            ev = i
            break
    if ev is None:
        return _none("no recent gap on heavy volume")
    gap = (o[ev] / c[ev - 1] - 1) * 100
    if ev == n - 1:  # the gap is today: episodic pivot (day 1)
        kind, cat = _catalyst(snap)
        day = (c[-1] / c[-2] - 1) * 100
        cl = _close_loc(o[-1], h[-1], l[-1], c[-1])
        price = _band(day, [(12, 12), (8, 10), (4, 8), (2, 4)]) + _band(gap, [(4, 4), (2, 2)]) \
            + (4 if cl >= 80 else 2 if cl >= 60 else -4 if cl < 40 else 0) + (2 if (h[-1] - l[-1]) / c[-2] >= 0.06 else 0)
        ratio = v[-1] / v[-51:-1].mean()
        volume = min(20, _band(ratio, [(10, 20), (5, 17), (3, 14), (1.5, 8)], 2) + (3 if v[-1] >= 9e6 else 0))
        prior20 = (c[-2] / c[-22] - 1) * 100
        mcap = (snap.fundamentals or {}).get("market_cap_b")
        neglect = _band(-prior20, [(-5, 8), (-15, 6), (-30, 3)], 1) + (2 if mcap and mcap < 10 else -1 if mcap and mcap > 100 else 0)
        liq = _band(c[-1] * v[-1], [(50e6, 10), (10e6, 8), (2e6, 4)], 1)
        liq = min(liq, 3) if c[-1] < 5 else liq
        risk = (c[-1] - l[-1]) / c[-1] * 100
        risk_pts = _band(-risk, [(-4, 5), (-7, 3.5), (-10, 1.5)])
        score = cat + price + volume + neglect + liq + risk_pts
        if cl < 30 and score >= 55:
            score -= 8  # it faded into the close
        grade = "A" if score >= 80 else "B" if score >= 70 else "C" if score >= 55 else None
        if grade in ("A", "B") and (risk > 10 or cl < 40 or not (day >= 4 or gap >= 4)):
            grade = "C"
        return _setup(grade, score, f"gap {gap:+.1f}% today, {ratio:.1f}x vol, {kind}", l[-1])
    # Days after the gap: post-earnings-announcement drift, read on weekly candles.
    wk, e = _weekly(bars, ev)
    weeks_since = len(wk) - 1 - e
    if weeks_since > 5:
        return _none("the gap is over 5 weeks old")
    after = wk.iloc[e + 1:] if weeks_since else wk.iloc[0:0]
    reds = after[after["close"] < after["open"]]
    cur = wk.iloc[-1]
    if reds.empty:
        stage, stage_pts, stop = "monitoring", 25 if weeks_since <= 2 else 15, l[ev]
        red_high = None
    else:
        red = reds.iloc[-1]
        stop, red_high = red["low"], red["high"]
        if cur["close"] >= cur["open"] and cur["close"] > red_high:
            stage, stage_pts = "weekly breakout", 50
        else:
            wick = (min(red["open"], red["close"]) - red["low"]) / ((red["high"] - red["low"]) or 1)
            stage, stage_pts = "signal ready", 40 + (5 if wick > 0.3 else 0)
    if c[-1] <= stop:
        return _none(f"the gap from {weeks_since}w ago failed")
    setup = min(100, _band(gap, [(10, 50), (7, 40), (5, 30), (3, 20)], 10) + stage_pts)
    if red_high:
        bo = (c[-1] / red_high - 1) * 100
        vconf = len(wk) > 4 and cur["volume"] > wk["volume"].iloc[-5:-1].mean()
        brk = 100 if bo >= 3 and vconf else 85 if bo >= 2 and vconf else 70 if bo >= 1 else 55 if bo > 0 else 0
    else:
        brk = 0
    adv, avgv = (c[-20:] * v[-20:]).mean(), v[-20:].mean()
    ok = (adv >= 25e6) + (avgv >= 1e6) + (c[-1] >= 10)
    liq = (100 if adv > 100e6 else 85 if adv > 50e6 else 70) if ok == 3 else 40 if ok == 2 else 15
    score = 0.30 * setup + 0.25 * brk + 0.25 * liq + 0.20 * 70
    grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else None
    return _setup(grade, score, f"{gap:+.1f}% gap {weeks_since}w ago, {stage}", stop)


# ---------------------------------------------------------------- Sable: Exhaustion Hammer
def exhaustion_hammer(snap: Snapshot, gate: int) -> dict:
    o, h, l, c, v = _arrays(snap.bars)
    if len(c) < 62:
        return _none("not enough history")
    O, H, L, C, V = o[-1], h[-1], l[-1], c[-1], v[-1]
    rng = H - L
    adv = (c[-21:-1] * v[-21:-1]).mean()
    mcap = (snap.fundamentals or {}).get("market_cap_b")
    if C < 20 or V < 100_000 or adv < 20e6 or (mcap is not None and mcap < 2) or rng <= 0:
        return _none("too small or thin for this setup")
    lower, body, upper = min(O, C) - L, abs(C - O), H - max(O, C)
    cl = _close_loc(O, H, L, C)
    lb = lower / body if body else 999
    if not (lower / rng >= 0.40 and body / rng <= 0.35 and cl >= 60 and lb >= 1.5 and C / L - 1 >= 0.02):
        return _none("no hammer today")
    hi_i = int(np.argmax(h[-41:-1][::-1]))  # 0 = yesterday
    recent_high, days_since = h[-2 - hi_i], hi_i + 1
    pb = (C / recent_high - 1) * 100
    if not (6 <= abs(pb) <= 35 and 3 <= days_since <= 30):
        return _none(f"hammer, but pullback {pb:.0f}% doesn't fit")
    prior_low = l[-6:-1].min()
    undercut, reclaim = L < prior_low, L < prior_low and C > prior_low
    ret20, ret60 = (c[-2] / c[-22] - 1) * 100, (c[-2] / c[-62] - 1) * 100
    quality = (4 if C >= 40 else 3) + (6 if adv >= 60e6 else 5) + \
        (5 if mcap and mcap >= 10 else 4 if mcap else 2) + (2 if v[-20:].mean() >= 100_000 else 0)
    momentum = 5 + (5 if ret60 >= 30 else 4 if ret60 >= 15 else 3 if ret20 >= 10 else 0) \
        + (3 if C >= c[-50:].mean() else 0) + 2
    reds = sum(c[i] < o[i] for i in range(-11, -1))
    downs = [c[i] < c[i - 1] for i in range(-6, -1)]
    consec = any(downs[k] and downs[k + 1] for k in range(len(downs) - 1))
    vr = V / v[-21:-1].mean()
    pullback = (6 if abs(pb) <= 25 else 4) + (6 if reclaim else 3 if undercut else 0) \
        + (3 if reds >= 5 else 2 if reds >= 3 else 0) + (2 if sum(downs) >= 3 or consec else 0) \
        + (3 if vr >= 1.5 else 2 if vr >= 1 else 0)
    geometry = (8 if lower / rng >= 0.6 else 6) + (5 if body / rng <= 0.2 else 4) + (5 if cl >= 75 else 4) \
        + (3 if lb >= 3 else 2) + (2 if C / L - 1 >= 0.04 else 1) + (1 if C > O else 0) + (1 if upper / rng <= 0.35 else 0)
    stop = L * 0.999
    risk = (C - stop) / C * 100
    if risk > 12:
        return _none(f"hammer, but stop {risk:.1f}% away")
    risk_pts = _band(-risk, [(-3, 15), (-5, 12), (-7.5, 9), (-10, 6), (-12, 3)])
    score = quality + momentum + pullback + geometry + risk_pts + gate
    grade = "A" if score >= 82 else "B" if score >= 70 else "C" if score >= 55 else None
    return _setup(grade, score, f"hammer after {pb:.0f}% pullback, {vr:.1f}x vol", stop)


# ---------------------------------------------------------------- Theo: Growth Leader (CANSLIM-lite)
def growth_leader(snap: Snapshot, bench: pd.DataFrame) -> dict:
    """C, N, S, L and M of CANSLIM. A (3 years of annual EPS) and I (institutional
    ownership) need data we don't have, so the weights are renormalized without them."""
    o, h, l, c, v = _arrays(snap.bars)
    d = snap.fundamentals or {}
    eg, rg = d.get("earnings_growth_pct"), d.get("revenue_growth_pct")
    if eg is None:
        return _none("no earnings data")
    rg = rg or 0
    C_ = 100 if eg >= 50 and rg >= 25 else 80 if eg >= 30 and rg >= 15 else 60 if eg >= 18 and rg >= 10 else 40 if eg >= 10 else 0
    hi52 = h[-252:].max()
    dist = (c[-1] / hi52 - 1) * 100
    brk = c[-1] >= 0.995 * hi52 and v[-1] > 1.4 * v[-50:].mean()
    N_ = 100 if dist >= -5 and brk else 80 if dist >= -10 and brk else 60 if dist >= -15 or brk else 40 if dist >= -25 else 20
    up = [v[i] for i in range(-60, 0) if c[i] > c[i - 1]]
    dn = [v[i] for i in range(-60, 0) if c[i] < c[i - 1]]
    S_ = _band(np.mean(up) / np.mean(dn), [(2, 100), (1.5, 80), (1, 60), (0.7, 40), (0.5, 20)]) \
        if len(up) >= 10 and len(dn) >= 10 else 40
    b = bench["close"].to_numpy(float)
    lrs = _weighted_rs(c, b, (63, 126, 252), (.4, .3, .3))
    L_ = _band(lrs, [(50, 100), (30, 95), (20, 90), (10, 80), (5, 70), (0, 60), (-5, 50), (-10, 40), (-20, 20)])
    ema50 = bench["close"].ewm(span=50, adjust=False).mean().iloc[-1]
    md = (b[-1] / ema50 - 1) * 100
    M_ = _band(md, [(2, 90), (0, 70), (-2, 40), (-5, 20)])
    comp = (0.15 * C_ + 0.15 * N_ + 0.15 * S_ + 0.20 * L_ + 0.05 * M_) / 0.70
    detail = f"EPS {eg:+.0f}%, {dist:+.0f}% from high, RS {lrs:+.0f}%"
    if M_ == 0 or L_ < 40:
        return _setup(None, comp, detail)
    fails = sum([C_ < 60, N_ < 40, S_ < 40, L_ < 50, M_ < 40])
    if fails > 1:
        return _setup(None, comp, detail)
    grade = "A" if comp >= 80 else "B" if comp >= 70 else "C" if comp >= 60 else None
    if fails == 1 and grade:
        grade = "C"  # one weak letter: watchlist only
    return _setup(grade, comp, detail)


# ---------------------------------------------------------------- Wren: market posture
def distribution_days(bench: pd.DataFrame) -> dict:
    o, h, l, c, v = _arrays(bench)
    n = len(c)
    ages = []
    for j in range(max(1, n - 80), n):
        if c[j] / c[j - 1] - 1 <= -0.002 + 1e-12 and v[j] > v[j - 1]:
            age = n - 1 - j
            if age > 25 or (h[j + 1:j + 26] >= c[j] * 1.05).any():
                continue  # expired, or the index rallied 5% above it
            ages.append(age)
    d5, d15, d25 = (sum(a <= k for a in ages) for k in (5, 15, 25))
    ema21 = bench["close"].ewm(span=21, adjust=False).mean().iloc[-1]
    below = c[-1] < ema21 and c[-1] < c[-50:].mean()
    risk = "severe" if d25 >= 6 or d15 >= 4 or (below and d25 >= 5) else \
        "high" if d25 >= 5 or d15 >= 3 or d5 >= 2 else "caution" if d25 >= 3 else "normal"
    return {"d5": d5, "d15": d15, "d25": d25, "risk": risk}


def follow_through(bench: pd.DataFrame) -> dict:
    """O'Neil's follow-through day on SPY: after a correction, day 4-10 of a rally attempt
    closes up 1.25%+ on higher volume. (The skill also checks the Nasdaq; we only have SPY.)"""
    o, h, l, c, v = _arrays(bench)
    n = len(c)
    start = max(41, n - 60)
    low = None
    for i in range(n - 2, start - 1, -1):
        hi_i = i - 40 + int(np.argmax(c[i - 40:i]))
        if c[i] <= c[hi_i] * 0.97 and c[i - 1] >= c[i] and c[i + 1] >= c[i] \
                and sum(c[k] < c[k - 1] for k in range(hi_i + 1, i + 1)) >= 3:
            low = i
            break
    if low is None:
        return {"state": "no correction", "day": 0}
    day1 = None
    for k in range(low + 1, n):
        if c[k] < c[low]:
            return {"state": "rally failed", "day": 0}
        if day1 is None and (c[k] > c[k - 1] or _close_loc(o[k], h[k], l[k], c[k]) >= 50):
            day1 = k
        if day1 is not None and day1 < k <= day1 + 2 and c[k] < l[day1]:
            return {"state": "rally failed", "day": 0}
        if day1 is not None and 3 <= k - day1 <= 9 and c[k] / c[k - 1] - 1 >= 0.0125 and v[k] > v[k - 1]:
            if (c[k + 1:] < l[k]).any():
                return {"state": "follow-through failed", "day": k - day1 + 1}
            gain = (c[k] / c[k - 1] - 1) * 100
            q = (60 if k - day1 <= 6 else 50) + _band(gain, [(2, 15), (1.5, 10), (1.25, 5)]) \
                + (10 if v[k] > v[k - 50:k].mean() else 0)
            dds = [m - k for m in range(k + 1, min(k + 6, n)) if c[m] / c[m - 1] - 1 <= -0.002 and v[m] > v[m - 1]]
            q += 10 if not dds else -30 if dds[0] <= 2 else -15 if dds[0] == 3 else -5
            q -= 20 if len(dds) >= 2 else 0
            return {"state": "follow-through", "day": k - day1 + 1, "quality": max(0, min(100, q))}
    if day1 is None:
        return {"state": "correction", "day": 0}
    day = n - day1
    return {"state": "rally failed" if day > 10 else "rally attempt", "day": day}


def market_posture(bench: pd.DataFrame, breadth: dict | None) -> dict:
    c = bench["close"].to_numpy(float)
    dd, ftd = distribution_days(bench), follow_through(bench)
    above50 = c[-1] > c[-50:].mean()
    correction = ftd["state"] in ("correction", "rally attempt", "rally failed", "follow-through failed")
    # Our addition: the FTD detector only watches the latest 3% dip, so a market that has
    # since climbed back near its high above the 50-day isn't treated as still correcting.
    if correction and above50 and c[-1] >= 0.97 * c[-60:].max():
        correction = False
    bearish_breadth = bool(breadth and breadth.get("bearish"))
    if dd["risk"] == "severe" or (correction and not above50):
        state, label = "red", "Market in correction"
    elif dd["risk"] in ("high", "caution") or correction or not above50 or bearish_breadth:
        state, label = "yellow", "Uptrend under pressure"
    else:
        state, label = "green", "Confirmed uptrend"
    parts = [f"{dd['d25']} distribution day{'s' if dd['d25'] != 1 else ''} in 25 sessions"]
    if ftd["state"] == "follow-through":
        parts.append(f"follow-through day on rally day {ftd['day']}")
    elif ftd["state"] != "no correction":
        parts.append(ftd["state"] + (f" (day {ftd['day']})" if ftd.get("day") else ""))
    if breadth:
        parts.append(f"{breadth['raw']:.0%} of the S&P 500 above its 200-day" + (", breadth turning down" if bearish_breadth else ""))
    return {"state": state, "label": label, "detail": "; ".join(parts), "dd": dd, "ftd": ftd, "breadth": breadth}


def fetch_breadth() -> dict | None:
    """Latest row of tradermonty's public S&P 500 breadth data (free, no key)."""
    with urllib.request.urlopen(BREADTH_URL, timeout=15) as r:
        rows = list(csv.DictReader(io.StringIO(r.read().decode())))
    last = rows[-1]
    return {"date": last["Date"], "raw": float(last["Breadth_Index_Raw"]), "ma8": float(last["Breadth_Index_8MA"]),
            "ma200": float(last["Breadth_Index_200MA"]), "bearish": last["Bearish_Signal"] == "True"}


# ---------------------------------------------------------------- discipline
def circuit_breaker(trades: list[dict], account: float, now: datetime | None = None) -> str | None:
    """drawdown-circuit-breaker + pre-trade-discipline-gate, on realized P&L.
    Returns why new entries are blocked, or None."""
    now = now or datetime.now(timezone.utc)
    closed = sorted((t for t in trades if "pnl" in t and t.get("time")), key=lambda t: t["time"])
    if not closed or account <= 0:
        return None
    when = lambda t: datetime.fromisoformat(t["time"]).astimezone(timezone.utc)  # noqa: E731

    def loss_since(start: datetime) -> float:
        return -sum(t["pnl"] for t in closed if when(t) >= start) / account * 100

    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if loss_since(day0) >= 2:
        return "realized losses today passed 2% of the account: halted until tomorrow"
    if loss_since(day0 - timedelta(days=now.weekday())) >= 5:
        return "realized losses this week passed 5%: halted until Monday"
    if loss_since(day0.replace(day=1)) >= 8:
        return "realized losses this month passed 8%: halted until next month"
    last = closed[-2:]
    if len(last) == 2 and all(t["pnl"] < 0 for t in last) and now - when(last[-1]) < timedelta(hours=24):
        return "two losing trades in a row: 24-hour cooldown"
    if closed[-1]["pnl"] < 0 and now - when(closed[-1]) < timedelta(hours=24):
        return "a loss in the last 24 hours: no revenge trades"
    return None


# ---------------------------------------------------------------- the faction
class Syndicate(Faction):
    id = "syndicate"
    name = "The Syndicate"
    color = "#f97316"
    motto = "No setup, no trade."
    leader = "fixer"

    def __init__(self, arena):
        super().__init__(arena)
        self.posture: dict | None = None
        self._breadth: tuple[str, dict | None] | None = None

    def hunt(self, sym: str, fld: Field) -> dict[str, dict]:
        """Every hunter's read on one symbol. The setups are for US stocks only."""
        snap = fld.snaps[sym]
        if fld.lanes.get(sym, snap.asset) not in ("stock", "lively"):
            return {u: _none("not a stock - not our game") for u in HUNTERS}
        gate = GATE[(self.posture or {}).get("state", "yellow")]
        out = {}
        for u, fn in (("burst", lambda: momentum_burst(snap.bars, gate)), ("vcp", lambda: vcp(snap.bars, fld.bench_bars)),
                      ("pivot", lambda: gap_drift(snap)), ("hammer", lambda: exhaustion_hammer(snap, gate)),
                      ("growth", lambda: growth_leader(snap, fld.bench_bars))):
            try:
                out[u] = fn()
            except Exception as e:  # a malformed series shouldn't stop the hunt
                log.warning("%s failed on %s: %s", u, sym, e)
                out[u] = _none("couldn't read the chart")
        return out

    def points(self, setups: dict[str, dict]) -> float:
        return sum(POINTS[s["grade"]] * self.weight(u) for u, s in setups.items() if s["grade"])

    def quick_view(self, sym: str, fld: Field) -> float:
        """Exit rules: a setup is busted once price loses the 50-day (Minervini/O'Neil)."""
        c = fld.snaps[sym].bars["close"]
        s50, s200 = c.tail(50).mean(), c.tail(200).mean()
        if c.iloc[-1] < s50 < s200:
            return -1.0
        if c.iloc[-1] < s50:
            return -0.5
        return 0.3 if (self.posture or {}).get("state") != "red" else 0.0

    async def _scout_posture(self, fld: Field) -> dict:
        today = date.today().isoformat()
        if self.arena.provider and self.arena.provider.name == "yahoo" and (not self._breadth or self._breadth[0] != today):
            try:
                self._breadth = (today, await asyncio.get_running_loop().run_in_executor(None, fetch_breadth))
            except Exception as e:
                log.warning("breadth data unavailable: %s", e)
                self._breadth = (today, None)
        self.posture = market_posture(fld.bench_bars, self._breadth[1] if self._breadth else None)
        return self.posture

    async def stock_turn(self, fld: Field) -> bool:
        await self.phase("research")
        p = await self._scout_posture(fld)
        await self.emit({"type": "posture", "state": p["state"], "label": p["label"], "detail": p["detail"],
                         "needed": NEEDED[p["state"]]})
        await self.say("lookout", f"{p['label']}: {p['detail']}. "
                                  f"The Fixer wants {NEEDED[p['state']]:g} setup points before paying.", "research")
        stop = circuit_breaker(self.broker.trades, self.arena.season.start_equity.get(self.id, self.broker.equity))
        if stop:
            await self.say("fixer", f"No new bounties: {stop}.", "decision", approved=False)
            return False
        universe = [s for s in (fld.focus or fld.watchlist) if s in fld.feats]
        board = {s: self.hunt(s, fld) for s in universe}
        best = {}
        for u in HUNTERS:  # each hunter rides out to their best find
            found = [(POINTS[b[u]["grade"]], s) for s, b in board.items() if b[u]["grade"]]
            if found:
                best[u] = max(found)[1]
        if best:
            await self.emit({"type": "hunt", "assign": best})
        await self.pause(1.5)
        pts = {s: self.points(b) for s, b in board.items()}
        posted = sorted(((s, v) for s, v in pts.items() if v > 0), key=lambda kv: -kv[1])
        await self.say("fixer", ("Bounties on the board: " + ", ".join(f"{s} {v:.1f}" for s, v in posted) + "."
                                 if posted else "Nothing on the board today. No setup, no trade."), "research")
        pool_ = [s for s, _ in posted if s not in self.broker.positions]
        picks = fld.focus or pool_[: self.s.candidates_per_session]
        traded = False
        for sym in picks:
            if sym in fld.feats:
                traded |= await self._consider(sym, fld, board.get(sym) or self.hunt(sym, fld))
        return traded

    async def _consider(self, sym: str, fld: Field, setups: dict[str, dict]) -> bool:
        feats = fld.feats[sym]
        state = self.posture["state"]
        await self.emit(self.stock_candidate(sym, fld))
        await self.emit({"type": "hunt", "assign": {u: sym for u in HUNTERS}})
        await self.phase("round1", sym)
        for u in HUNTERS:
            s = setups[u]
            pts = POINTS[s["grade"]] * self.weight(u) if s["grade"] else 0
            await self.emit({"type": "setup", "agent": u, "symbol": sym, "grade": s["grade"], "score": s["score"],
                             "detail": s["detail"], "points": round(pts, 2), "weight": self.weight(u)})
            tag = f"[LONG {s['score']:.0f}] {s['grade']}-grade setup: " if s["grade"] else "[PASS] "
            await self.say(u, tag + s["detail"] + ".", "opinion")
        await self.phase("vote", sym)
        points, needed = self.points(setups), NEEDED[state]
        graded = {u: s for u, s in setups.items() if s["grade"]}
        ok = points >= needed
        reason = (f"{points:.1f} setup points from {len(graded)} hunter{'s' if len(graded) != 1 else ''}, "
                  f"{needed:g} needed ({self.posture['label'].lower()})")
        await self.emit({"type": "bounty", "symbol": sym, "points": round(points, 2), "needed": needed, "ok": ok,
                         "reason": reason})
        memo = await self.ask(
            P.BY_ID["fixer"],
            P.SHARED_RULES + "\n\nYou are the Fixer of the Syndicate, a crew of setup hunters who each trade one "
            "published chart setup. Explain the verdict in two sentences, in plain English, without changing it.",
            f"{sym}: market posture {self.posture['label']} ({self.posture['detail']}). Setups: " +
            "; ".join(f"{P.BY_ID[u].role} {s['grade'] or 'none'} ({s['detail']})" for u, s in setups.items()) +
            f". Verdict: {'PAY THE BOUNTY' if ok else 'NO BOUNTY'} - {reason}.",
            DeskMemo,
            lambda: DeskMemo(summary=("The setups line up. We ride." if ok else "Not enough setups. We wait."),
                             what_would_change_our_mind="Another hunter grading the chart, or a healthier market."))
        await self.say("fixer", ("APPROVED - " if ok else "REJECTED - ") + f"{reason}. {memo.summary}", "decision", sym,
                       approved=ok)
        detail = {u: {"stance": "LONG" if s["grade"] else "PASS", "grade": s["grade"]} for u, s in setups.items()}
        order = await self.execute_stock(sym, "LONG", fld, detail) if ok else None
        # Two A-grade setups in a confirmed uptrend: also buy a call.
        option = None
        if ok and state == "green" and sum(s["grade"] == "A" for s in setups.values()) >= 2:
            option = await self.option_play(sym, "call", fld, detail, "two A-grade setups in a confirmed uptrend")
        acted = order is not None or option is not None
        await self.emit({"type": "verdict", "symbol": sym, "approved": acted,
                         "label": "APPROVED" if acted else ("VETOED" if ok else "REJECTED")})
        stances = {u: "LONG" if s["grade"] else "PASS" for u, s in setups.items()}
        self.record("stock", sym, sym, feats["price"], stances, "LONG" if ok else "PASS", acted)
        if acted:
            await self.arena.publish()
        return acted
