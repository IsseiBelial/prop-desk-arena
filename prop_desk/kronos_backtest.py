"""Would Kronos have called it? A walk-forward backtest of the Kronos Watch's forecasts.

The Arena grades calls live, which takes weeks to say anything. This replays the past
instead: on each test date Kronos sees only the candles up to that day (the same 260
the Arena gives it), draws its paths for the next 5 trading days, and the forecast is
checked against what really happened. Calls are graded the way the Arena grades them:
up more than 1% = up, down more than 1% = down, otherwise flat and not counted.

  python -m prop_desk.kronos_backtest                        # the core watchlist since Sep 2025
  python -m prop_desk.kronos_backtest NVDA AMD BTC-USD --since 2026-01-01 --samples 20

Next to Kronos it scores two baselines on the very same dates:
  * always long  - what buying everything would have scored (markets mostly rise)
  * momentum     - long if the last 20 days were up, short if down
If Kronos can't beat both, it isn't adding anything.

Only test dates AFTER Kronos was trained. It was published in August 2025, so earlier
prices may have been in its training data, and it would be "remembering", not forecasting.

Results go to the screen and to data/kronos_backtest.csv (one row per forecast).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time

import numpy as np
import pandas as pd

from . import forecaster
from .config import settings
from .factions.kronos import implausible
from .indicators import atr
from .market_data import make_provider
from .universe import asset_class


def grade(move_pct: float) -> str:
    return "up" if move_pct > 1 else "down" if move_pct < -1 else "flat"


def hit_rate(rows: list[dict], stance_key: str) -> tuple[float | None, int]:
    """Share of non-PASS, non-flat calls that were right."""
    graded = [r for r in rows if r[stance_key] != "PASS" and r["outcome"] != "flat"]
    hits = sum((r[stance_key], r["outcome"]) in {("LONG", "up"), ("SHORT", "down")} for r in graded)
    return (hits / len(graded) if graded else None), len(graded)


def run(symbols: list[str], since: str, step: int, horizon: int, samples: int, context: int, min_prob: float,
        engine=None, provider=None, out: str | None = None, log=print) -> dict:
    provider = provider or make_provider(settings.data_source, settings.benchmark)
    engine = engine or forecaster.KronosEngine(settings.kronos_model, settings.kronos_tokenizer or None,
                                               settings.kronos_device, samples, settings.kronos_temperature,
                                               settings.kronos_top_p, context)
    log(f"Kronos backtest: {engine.name}, {samples} paths, {engine.context} days of context, "
        f"{horizon}-day calls every {step} days since {since}, on {provider.name} data.")
    engine.load()
    rows, t0, n, skipped = [], time.time(), 0, 0
    for sym in symbols:
        try:
            bars = provider.bars(sym, days=1300)
        except Exception as e:
            log(f"  {sym}: skipped ({e})")
            continue
        idx = pd.DatetimeIndex(bars.index)
        idx = idx.tz_localize(None) if idx.tz is not None else idx
        start = max(int(np.searchsorted(idx, pd.Timestamp(since))), engine.context)
        dates = range(start, len(bars) - horizon, step)
        if not dates:
            log(f"  {sym}: not enough history after {since}")
            continue
        for i in dates:
            hist = bars.iloc[: i + 1]
            fc = engine.forecast(sym, hist, horizon, asset_class(sym))
            n += 1
            px, later = float(hist["close"].iloc[-1]), float(bars["close"].iloc[i + horizon])
            if implausible(fc, float(atr(hist).iloc[-1]) / px * 100):  # the Arena ignores these too
                skipped += 1
                continue
            past = float(hist["close"].iloc[-1] / hist["close"].iloc[-21] - 1) * 100
            move = (later / px - 1) * 100
            p = fc.p_up
            rows.append({"symbol": sym, "date": str(idx[i].date()), "price": round(px, 6), "p_up": round(p, 3),
                         "expected_pct": round(fc.expected_pct, 3), "actual_pct": round(move, 3),
                         "outcome": grade(move),
                         "kronos": "LONG" if p >= min_prob else "SHORT" if p <= 1 - min_prob else "PASS",
                         "always_long": "LONG", "momentum": "LONG" if past > 0 else "SHORT"})
        per = (time.time() - t0) / max(n, 1)
        log(f"  {sym}: {len(dates)} forecasts ({per:.1f}s each)")
    if skipped:
        log(f"  {skipped} implausible forecasts were ignored, as the Arena would.")
    return summarize(rows, horizon, out, log)


def summarize(rows: list[dict], horizon: int = 5, out: str | None = None, log=print) -> dict:
    if not rows:
        log("No forecasts were made.")
        return {}
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    df = pd.DataFrame(rows)
    res = {"forecasts": len(df)}
    log("")
    log(f"{'':28}{'hit rate':>10}{'graded calls':>14}")
    for key, label in (("kronos", "Kronos (Oracle Ione)"), ("always_long", "Always long"), ("momentum", "20-day momentum")):
        hr, k = hit_rate(rows, key)
        res[key] = {"hit_rate": hr, "calls": k}
        log(f"  {label:26}{'–' if hr is None else f'{hr:.1%}':>10}{k:>14}")
    # Does a higher forecast mean a higher return? (the paper's IC / RankIC)
    res["ic"] = float(df["expected_pct"].corr(df["actual_pct"])) if len(df) > 2 else None
    by_date = [g for _, g in df.groupby("date") if len(g) >= 4]
    rics = [g["expected_pct"].rank().corr(g["actual_pct"].rank()) for g in by_date]
    rics = [r for r in rics if r == r]
    res["rank_ic"] = float(np.mean(rics)) if rics else None
    # The paper's trade: buy the symbol Kronos ranks first, vs buying them all.
    tops = [g.loc[g["expected_pct"].idxmax(), "actual_pct"] - g["actual_pct"].mean() for g in by_date]
    res["top_pick_excess_pct"] = float(np.mean(tops)) if tops else None
    longs = df[df["kronos"] == "LONG"]["actual_pct"]
    res["avg_when_long_pct"], res["avg_all_pct"] = (float(longs.mean()) if len(longs) else None), float(df["actual_pct"].mean())
    log("")
    log(f"  Forecast vs result correlation (IC): {fmt(res['ic'], 3)}  (the paper reports about 0.03-0.05; 0 = no skill)")
    log(f"  Same, ranked across symbols each day (RankIC): {fmt(res['rank_ic'], 3)}  over {len(rics)} days")
    log(f"  Kronos's #1 pick each day vs the average symbol: {fmt(res['top_pick_excess_pct'], 2, '%')} "
        f"over {horizon} days (the paper's trade: buy the top of the ranking)")
    log(f"  Average {horizon}-day move when Kronos said LONG: {fmt(res['avg_when_long_pct'], 2, '%')}"
        f"  vs {fmt(res['avg_all_pct'], 2, '%')} on all dates")
    if out:
        log(f"\n  Every forecast: {out}")
    log("  A few hundred graded calls are needed before a few points of hit rate mean anything.")
    return res


def fmt(x, nd, unit=""):
    return "–" if x is None else f"{x:+.{nd}f}{unit}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Walk-forward backtest of Kronos's forecasts.")
    ap.add_argument("symbols", nargs="*", help="default: the core watchlist (PROP_DESK_WATCHLIST)")
    ap.add_argument("--since", default="2025-09-01", help="first test date; keep it after Kronos's release (Aug 2025)")
    ap.add_argument("--step", type=int, default=5, help="trading days between test dates")
    ap.add_argument("--samples", type=int, default=10, help="paths per forecast (the Arena uses PROP_DESK_KRONOS_SAMPLES)")
    ap.add_argument("--context", type=int, default=260, help="days of history Kronos sees (the Arena gives it 260)")
    ap.add_argument("--min-prob", type=float, default=settings.kronos_min_prob)
    a = ap.parse_args(argv)
    if not forecaster.available():
        print("PyTorch isn't installed. Run: pip install -r prop_desk/requirements-kronos.txt")
        return 1
    run([s.upper() for s in a.symbols] or settings.watchlist, a.since, a.step, settings.call_horizon_days, a.samples,
        a.context, a.min_prob, out=os.path.join(settings.data_dir, "kronos_backtest.csv"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
