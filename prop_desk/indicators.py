"""Technical indicators, computed in plain pandas.

The AI traders never calculate numbers themselves - they reason over the
values produced here. That keeps the math exact and auditable.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _r(x, nd=2):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def macd(close: pd.Series, fast=12, slow=26, signal=9):
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    flow = tp * df["volume"]
    up = flow.where(tp > tp.shift(), 0.0).rolling(n).sum()
    down = flow.where(tp < tp.shift(), 0.0).rolling(n).sum()
    return 100 - 100 / (1 + up / down.replace(0, np.nan))


def cmf(df: pd.DataFrame, n: int = 20) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng
    return (mfm * df["volume"]).rolling(n).sum() / df["volume"].rolling(n).sum()


def obv(df: pd.DataFrame) -> pd.Series:
    return (np.sign(df["close"].diff()).fillna(0) * df["volume"]).cumsum()


def ichimoku(df: pd.DataFrame):
    hi, lo = df["high"], df["low"]
    tenkan = (hi.rolling(9).max() + lo.rolling(9).min()) / 2
    kijun = (hi.rolling(26).max() + lo.rolling(26).min()) / 2
    span_a = (tenkan + kijun) / 2  # plotted 26 bars ahead
    span_b = (hi.rolling(52).max() + lo.rolling(52).min()) / 2  # plotted 26 bars ahead
    return tenkan, kijun, span_a, span_b


def _ret(close: pd.Series, n: int):
    return _r((close.iloc[-1] / close.iloc[-1 - n] - 1) * 100) if len(close) > n else None


def compute_features(bars: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> dict:
    """Return a nested dict of indicator readings, grouped by trading discipline."""
    c = bars["close"]
    price = float(c.iloc[-1])
    sma20, sma50, sma200 = (c.rolling(n).mean().iloc[-1] for n in (20, 50, 200))
    m_line, m_sig, m_hist = macd(c)
    a = atr(bars)
    hi52 = c.tail(252).max()
    lo52 = c.tail(252).min()

    tenkan, kijun, span_a, span_b = ichimoku(bars)
    # The cloud under today's price is the spans computed 26 bars ago.
    cloud_a, cloud_b = span_a.iloc[-27], span_b.iloc[-27]
    cloud_top, cloud_bot = max(cloud_a, cloud_b), min(cloud_a, cloud_b)
    position = "above" if price > cloud_top else "below" if price < cloud_bot else "inside"
    tk_now = tenkan.iloc[-1] - kijun.iloc[-1]
    tk_prev = tenkan.iloc[-4] - kijun.iloc[-4]
    tk_cross = "bullish" if tk_prev <= 0 < tk_now else "bearish" if tk_prev >= 0 > tk_now else "none"

    ob = obv(bars)
    obv_slope = (ob.iloc[-1] - ob.iloc[-21]) / (bars["volume"].tail(20).mean() * 20)
    up_vol = bars["volume"].tail(20)[c.diff().tail(20) > 0].sum()
    dn_vol = bars["volume"].tail(20)[c.diff().tail(20) < 0].sum()

    rel_3m = rel_6m = None
    if benchmark is not None and len(benchmark) > 126:
        b = benchmark["close"]
        rel_3m = _r((_ret(c, 63) or 0) - (_ret(b, 63) or 0))
        rel_6m = _r((_ret(c, 126) or 0) - (_ret(b, 126) or 0))

    return {
        "price": _r(price),
        "momentum": {
            "return_1w_pct": _ret(c, 5),
            "return_1m_pct": _ret(c, 21),
            "return_3m_pct": _ret(c, 63),
            "return_6m_pct": _ret(c, 126),
            "rsi14": _r(rsi(c).iloc[-1], 1),
            "macd_hist": _r(m_hist.iloc[-1], 3),
            "macd_above_signal": bool(m_line.iloc[-1] > m_sig.iloc[-1]),
            "price_vs_sma20_pct": _r((price / sma20 - 1) * 100),
            "price_vs_sma50_pct": _r((price / sma50 - 1) * 100),
            "price_vs_sma200_pct": _r((price / sma200 - 1) * 100) if not math.isnan(sma200) else None,
            "golden_cross": bool(sma50 > sma200) if not math.isnan(sma200) else None,
            "pct_from_52w_high": _r((price / hi52 - 1) * 100),
            "pct_from_52w_low": _r((price / lo52 - 1) * 100),
        },
        "ichimoku": {
            "tenkan": _r(tenkan.iloc[-1]),
            "kijun": _r(kijun.iloc[-1]),
            "cloud_top": _r(cloud_top),
            "cloud_bottom": _r(cloud_bot),
            "price_vs_cloud": position,
            "future_cloud": "bullish" if span_a.iloc[-1] > span_b.iloc[-1] else "bearish",
            "tk_cross_last_3_bars": tk_cross,
            "tenkan_above_kijun": bool(tk_now > 0),
            "chikou_above_price_26_ago": bool(price > c.iloc[-27]),
            "cloud_thickness_pct": _r(abs(cloud_a - cloud_b) / price * 100),
        },
        "money_flow": {
            "mfi14": _r(mfi(bars).iloc[-1], 1),
            "cmf20": _r(cmf(bars).iloc[-1], 3),
            "obv_trend_20d": _r(obv_slope, 3),
            "up_down_volume_ratio_20d": _r(up_vol / dn_vol if dn_vol else None),
            "volume_20d_vs_50d": _r(bars["volume"].tail(20).mean() / bars["volume"].tail(50).mean()),
            "relative_strength_vs_spy_3m_pct": rel_3m,
            "relative_strength_vs_spy_6m_pct": rel_6m,
        },
        "volatility": {
            "atr14": _r(a.iloc[-1]),
            "atr_pct": _r(a.iloc[-1] / price * 100),
            "realized_vol_20d_annual_pct": _r(c.pct_change().tail(20).std() * math.sqrt(252) * 100, 1),
        },
    }
