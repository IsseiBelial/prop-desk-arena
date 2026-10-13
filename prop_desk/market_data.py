"""Market data: prices, fundamentals and headlines.

Two providers share one interface:
  * YahooProvider     - free, delayed data via yfinance (no key required)
  * SyntheticProvider - realistic fake data so the desk runs anywhere (demo/tests)
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class Headline:
    title: str
    source: str
    published: str
    summary: str = ""


@dataclass
class Snapshot:
    """Everything the desk knows about one symbol at one moment."""
    symbol: str
    bars: pd.DataFrame  # columns: open, high, low, close, volume (daily, oldest first)
    fundamentals: dict = field(default_factory=dict)
    news: list[Headline] = field(default_factory=list)
    source: str = "synthetic"

    @property
    def price(self) -> float:
        return float(self.bars["close"].iloc[-1])


class SyntheticProvider:
    name = "synthetic"

    _SECTORS = ["Technology", "Healthcare", "Financials", "Energy", "Consumer", "Industrials"]
    _GOOD = [
        "{s} beats earnings estimates, raises full-year guidance",
        "Analysts upgrade {s} to Buy on strong demand outlook",
        "{s} announces $10B share buyback program",
        "{s} wins major multi-year contract",
    ]
    _BAD = [
        "{s} misses revenue estimates as demand softens",
        "Regulators open probe into {s} business practices",
        "{s} cuts guidance citing margin pressure",
        "Short seller report targets {s} accounting",
    ]
    _NEUTRAL = [
        "{s} to present at industry conference next week",
        "{s} names new chief financial officer",
        "Options activity in {s} picks up ahead of earnings",
    ]

    _EPOCH = pd.Timestamp("2019-01-01")

    def _rng(self, symbol: str, salt: str = "", daily: bool = False) -> np.random.Generator:
        # Price history is seeded per symbol only, so it stays consistent from day to
        # day (yesterday's bars don't change) and positions can be marked over time.
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d") if daily else ""
        seed = int(hashlib.sha256(f"{symbol}|{day}|{salt}".encode()).hexdigest()[:8], 16)
        return np.random.default_rng(seed)

    def bars(self, symbol: str, days: int = 260) -> pd.DataFrame:
        idx = pd.bdate_range(self._EPOCH, datetime.now().date())
        n = len(idx)
        rng = self._rng(symbol, "bars")
        drift = rng.normal(0.0004, 0.0008)
        vol = rng.uniform(0.012, 0.028)
        # Regimes: every ~60 days the stock's trend changes, so setups come and go.
        regime = np.repeat(rng.normal(0, 0.0025, n // 60 + 1), 60)[:n]
        rets = rng.normal(drift, vol, n) + regime
        path = np.exp(np.cumsum(rets))
        close = path / path[-2] * rng.uniform(30, 700)  # land at a realistic price today
        # Intraday drift for today's bar so prices move during the day.
        now = datetime.now(timezone.utc)
        steps = (now.hour * 60 + now.minute) // 10
        live = self._rng(symbol, "intraday", daily=True).normal(0, vol / 6, max(steps, 1)).sum()
        close[-1] *= float(np.exp(live))
        spread = np.abs(rng.normal(0, vol * 0.6, n)) * close
        open_ = close * (1 + rng.normal(0, vol * 0.3, n))
        high = np.maximum(open_, close) + spread
        low = np.minimum(open_, close) - spread
        base_vol = rng.uniform(5e6, 60e6)
        volume = base_vol * (1 + np.abs(rets) / vol * 0.5) * rng.uniform(0.7, 1.3, n)
        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume.round()}, index=idx)
        return df.tail(days)

    def fundamentals(self, symbol: str) -> dict:
        rng = self._rng(symbol, "fund")
        return {
            "sector": self._SECTORS[int(rng.integers(len(self._SECTORS)))],
            "market_cap_b": round(float(rng.uniform(20, 3000)), 1),
            "pe": round(float(rng.uniform(8, 60)), 1),
            "forward_pe": round(float(rng.uniform(7, 45)), 1),
            "peg": round(float(rng.uniform(0.6, 3.5)), 2),
            "revenue_growth_pct": round(float(rng.normal(9, 12)), 1),
            "earnings_growth_pct": round(float(rng.normal(10, 20)), 1),
            "profit_margin_pct": round(float(rng.uniform(-5, 40)), 1),
            "roe_pct": round(float(rng.uniform(-5, 60)), 1),
            "debt_to_equity": round(float(rng.uniform(0, 250)), 1),
            "fcf_yield_pct": round(float(rng.uniform(-1, 8)), 2),
            "next_earnings": str((datetime.now() + timedelta(days=int(self._rng(symbol, "earn", daily=True).integers(0, 75)))).date()),
        }

    def news(self, symbol: str) -> list[Headline]:
        rng = self._rng(symbol, "news", daily=True)
        mood = rng.normal(0, 1)
        out = []
        for i in range(int(rng.integers(2, 5))):
            pool = self._GOOD if rng.normal(mood, 1) > 0.6 else self._BAD if rng.normal(mood, 1) < -0.6 else self._NEUTRAL
            title = pool[int(rng.integers(len(pool)))].format(s=symbol)
            ts = datetime.now(timezone.utc) - timedelta(hours=int(rng.integers(1, 72)))
            out.append(Headline(title=title, source="Synthetic Wire", published=ts.isoformat(timespec="minutes")))
        return out

    def snapshot(self, symbol: str) -> Snapshot:
        return Snapshot(symbol, self.bars(symbol), self.fundamentals(symbol), self.news(symbol), self.name)


class YahooProvider:
    name = "yahoo"

    def __init__(self) -> None:
        import yfinance  # noqa: F401  (fail fast if missing)

    def bars(self, symbol: str, days: int = 260) -> pd.DataFrame:
        import yfinance as yf
        df = yf.Ticker(symbol).history(period="2y", auto_adjust=True)
        if df.empty:
            raise ValueError(f"no price data for {symbol}")
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        return df.tail(days)

    def fundamentals(self, symbol: str) -> dict:
        import yfinance as yf
        info = yf.Ticker(symbol).info or {}

        def pct(key):
            v = info.get(key)
            return round(v * 100, 1) if isinstance(v, (int, float)) else None

        mcap, fcf = info.get("marketCap"), info.get("freeCashflow")
        return {
            "sector": info.get("sector"),
            "market_cap_b": round(mcap / 1e9, 1) if mcap else None,
            "pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "peg": info.get("pegRatio") or info.get("trailingPegRatio"),
            "revenue_growth_pct": pct("revenueGrowth"),
            "earnings_growth_pct": pct("earningsGrowth"),
            "profit_margin_pct": pct("profitMargins"),
            "roe_pct": pct("returnOnEquity"),
            "debt_to_equity": info.get("debtToEquity"),
            "fcf_yield_pct": round(fcf / mcap * 100, 2) if fcf and mcap else None,
            "next_earnings": self._next_earnings(symbol),
        }

    @staticmethod
    def _next_earnings(symbol: str) -> str | None:
        import yfinance as yf
        try:
            cal = yf.Ticker(symbol).calendar or {}
            dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
            return str(dates[0]) if dates else None
        except Exception:
            return None

    def news(self, symbol: str) -> list[Headline]:
        import yfinance as yf
        out = []
        for item in (yf.Ticker(symbol).news or [])[:8]:
            c = item.get("content", item)  # yfinance changed shape across versions
            provider = c.get("provider") or {}
            out.append(Headline(
                title=c.get("title", ""),
                source=provider.get("displayName", "") if isinstance(provider, dict) else str(provider),
                published=str(c.get("pubDate") or c.get("providerPublishTime") or ""),
                summary=(c.get("summary") or "")[:400],
            ))
        return [h for h in out if h.title]

    def snapshot(self, symbol: str) -> Snapshot:
        bars = self.bars(symbol)
        try:
            fund = self.fundamentals(symbol)
        except Exception as e:  # fundamentals are nice-to-have
            log.warning("fundamentals failed for %s: %s", symbol, e)
            fund = {}
        try:
            news = self.news(symbol)
        except Exception as e:
            log.warning("news failed for %s: %s", symbol, e)
            news = []
        return Snapshot(symbol, bars, fund, news, self.name)


def make_provider(kind: str = "auto", probe_symbol: str = "SPY"):
    if kind == "synthetic":
        return SyntheticProvider()
    try:
        p = YahooProvider()
        p.bars(probe_symbol, days=5)
        return p
    except Exception as e:
        if kind == "yahoo":
            raise
        log.warning("Yahoo data unavailable (%s); using synthetic data", e)
        return SyntheticProvider()
