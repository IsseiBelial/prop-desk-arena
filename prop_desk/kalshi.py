"""Prediction markets (Kalshi): read-only market data plus a simulator.

The desk only *reads* Kalshi's public market data; every contract trade goes to
the paper ledger. Prices are in cents (1-99) - a YES contract pays $1 if the
event happens, so a 28¢ price means the market implies a 28% chance.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

log = logging.getLogger(__name__)
API = "https://api.elections.kalshi.com/trade-api/v2"


@dataclass
class Contract:
    ticker: str
    title: str
    category: str
    yes_bid: int
    yes_ask: int
    last_price: int
    previous_price: int
    volume: int
    open_interest: int
    close_time: str
    status: str = "active"
    result: str = ""  # "yes" | "no" once settled
    rules: str = ""
    source: str = "synthetic"

    @property
    def no_bid(self) -> int:
        return 100 - self.yes_ask

    @property
    def no_ask(self) -> int:
        return 100 - self.yes_bid

    @property
    def implied(self) -> int:
        return round((self.yes_bid + self.yes_ask) / 2)

    def ask(self, side: str) -> int:
        return self.yes_ask if side == "YES" else self.no_ask

    def mark(self, side: str) -> int:
        return self.yes_bid if side == "YES" else self.no_bid

    def to_dict(self) -> dict:
        return {**asdict(self), "no_bid": self.no_bid, "no_ask": self.no_ask, "implied": self.implied}


def fee_dollars(price_cents: int, qty: int) -> float:
    """Kalshi taker fee: 7% x contracts x P x (1-P), rounded up to the cent."""
    p = price_cents / 100
    return math.ceil(round(0.07 * qty * p * (1 - p) * 100, 6)) / 100  # round() strips float noise before ceil


def edge_cents(prob_yes: float, side: str, ask_cents: int) -> float:
    """Expected profit per contract in cents, after fees, if `prob_yes` (0-100) is right."""
    win_prob = prob_yes if side == "YES" else 100 - prob_yes
    return round(win_prob - ask_cents - fee_dollars(ask_cents, 100), 2)  # fee per contract, averaged over 100


# ---------------------------------------------------------------- live data
def _num(m: dict, key: str) -> int:
    """Kalshi has sent prices both as integer cents and as dollar strings."""
    if m.get(key) is not None:
        return int(m[key])
    d = m.get(f"{key}_dollars")
    return int(round(float(d) * 100)) if d not in (None, "") else 0


class KalshiProvider:
    name = "kalshi"

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(API + path, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)

    def _parse(self, m: dict, category: str = "") -> Contract:
        return Contract(
            ticker=m["ticker"],
            title=m.get("title") or m["ticker"],
            category=category or m.get("category", ""),
            yes_bid=_num(m, "yes_bid"),
            yes_ask=_num(m, "yes_ask"),
            last_price=_num(m, "last_price"),
            previous_price=_num(m, "previous_price"),
            volume=int(m.get("volume") or 0),
            open_interest=int(m.get("open_interest") or 0),
            close_time=m.get("close_time", ""),
            status=m.get("status", ""),
            result=m.get("result") or "",
            rules=(m.get("rules_primary") or "")[:600],
            source=self.name,
        )

    def markets(self, limit: int = 40) -> list[Contract]:
        cats = {}
        try:
            for e in self._get("/events?status=open&limit=200").get("events", []):
                cats[e.get("event_ticker")] = e.get("category", "")
        except Exception as e:
            log.warning("Kalshi events failed: %s", e)
        data = self._get("/markets?status=open&limit=1000")
        horizon = datetime.now(timezone.utc) + timedelta(days=45)
        out = []
        for m in data.get("markets", []):
            if m["ticker"].startswith("KXMVE"):  # multi-leg parlays
                continue
            c = self._parse(m, cats.get(m.get("event_ticker"), ""))
            try:
                closes = datetime.fromisoformat(c.close_time.replace("Z", "+00:00"))
            except ValueError:
                continue
            # Liquid, two-sided, resolving soon enough to matter.
            if c.volume >= 1000 and 4 <= c.yes_bid and c.yes_ask <= 96 and closes <= horizon:
                out.append(c)
        out.sort(key=lambda c: c.volume, reverse=True)
        return out[:limit]

    def market(self, ticker: str) -> Contract:
        return self._parse(self._get(f"/markets/{ticker}")["market"])


# ---------------------------------------------------------------- simulator
_CATALOG = [
    ("FED", "Will the Fed cut rates at its next meeting?", "Economics"),
    ("CPI", "Will CPI inflation come in above 3.0% year-over-year?", "Economics"),
    ("JOBS", "Will the unemployment rate be 4.5% or higher next report?", "Economics"),
    ("GAS", "Will national average gas prices be above $3.50 this month?", "Economics"),
    ("BTC", "Will Bitcoin be above $100,000 on Dec 31?", "Crypto"),
    ("ETH", "Will Ethereum be above $4,000 at month end?", "Crypto"),
    ("SPX", "Will the S&P 500 close the week higher?", "Financials"),
    ("TNX", "Will the 10-year Treasury yield close above 4.5% Friday?", "Financials"),
    ("NDX", "Will the Nasdaq 100 hit a record high this month?", "Financials"),
    ("HEAT", "Will New York City hit 80°F this week?", "Climate"),
    ("HURR", "Will a named hurricane make US landfall this month?", "Climate"),
    ("SHUT", "Will a federal government shutdown begin before month end?", "Politics"),
]


class SyntheticKalshi:
    """Plausible markets with a hidden 'true' probability. Some are mispriced."""
    name = "synthetic"

    def _rng(self, *parts) -> np.random.Generator:
        h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8]
        return np.random.default_rng(int(h, 16))

    def _truth(self, code: str, week: str) -> float:
        return float(self._rng(code, week, "truth").beta(1.4, 1.4) * 92 + 4)

    def _build(self, code: str, title: str, cat: str, week: str, now: datetime) -> Contract:
        rng = self._rng(code, week, "px")
        truth = self._truth(code, week)
        mid = int(np.clip(truth + rng.normal(0, 9), 3, 97))  # the crowd is noisy
        spread = int(rng.integers(1, 4))
        opened = datetime.fromisoformat(week).replace(tzinfo=timezone.utc)
        closes = opened + timedelta(days=int(rng.integers(2, 7)), hours=int(rng.integers(0, 23)))
        settled = now >= closes
        result = ("yes" if self._rng(code, week, "res").uniform(0, 100) < truth else "no") if settled else ""
        return Contract(
            ticker=f"SIM-{code}-{week.replace('-', '')}",
            title=title, category=cat,
            yes_bid=max(1, mid - spread), yes_ask=min(99, mid + spread),
            last_price=mid, previous_price=int(np.clip(mid + rng.normal(0, 5), 1, 99)),
            volume=int(rng.uniform(2e3, 4e5)), open_interest=int(rng.uniform(1e3, 1e5)),
            close_time=closes.isoformat(timespec="minutes"),
            status="settled" if settled else "active", result=result,
            rules=f"Resolves YES if: {title.removeprefix('Will ').rstrip('?')}.", source=self.name,
        )

    @staticmethod
    def _week(dt: datetime) -> str:
        return str((dt - timedelta(days=dt.weekday())).date())

    def markets(self, limit: int = 40) -> list[Contract]:
        now = datetime.now(timezone.utc)
        out = [self._build(c, t, cat, self._week(now), now) for c, t, cat in _CATALOG]
        return [c for c in out if c.status == "active"][:limit]

    def market(self, ticker: str) -> Contract:
        _, code, wk = ticker.split("-")
        week = f"{wk[:4]}-{wk[4:6]}-{wk[6:]}"
        title, cat = next((t, c) for k, t, c in _CATALOG if k == code)
        return self._build(code, title, cat, week, datetime.now(timezone.utc))


def make_kalshi(kind: str = "auto"):
    if kind == "synthetic":
        return SyntheticKalshi()
    try:
        p = KalshiProvider()
        p._get("/markets?limit=1")
        return p
    except Exception as e:
        if kind == "kalshi":
            raise
        log.warning("Kalshi unavailable (%s); using simulated markets", e)
        return SyntheticKalshi()
