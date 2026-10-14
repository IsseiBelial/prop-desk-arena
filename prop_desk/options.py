"""Options: buy-only calls and puts on the stocks the factions trade.

A faction only reaches for an option when it is unusually sure of a stock's direction,
and only as a buyer, so the most it can lose is the premium it paid. Because one
contract covers 100 shares, a single option often costs more than a small account
allows; when it does, the desk tries a debit spread (buy one strike, sell the next one
out), which is cheaper and still has a fixed maximum loss.

Two chain sources share one interface:
  * YahooOptions     - real chains (bid/ask, volume, open interest, implied vol) via yfinance
  * SyntheticOptions - Black-Scholes prices from the stock's own volatility (demo/tests)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta

log = logging.getLogger(__name__)
RATE = 0.04  # risk-free rate used for pricing


@dataclass
class OptionQuote:
    contract: str
    underlying: str
    kind: str  # call | put
    strike: float
    expiry: str  # YYYY-MM-DD
    bid: float
    ask: float
    last: float
    iv: float  # implied volatility, annual (0.45 = 45%)
    volume: int = 0
    open_interest: int = 0
    source: str = "synthetic"

    @property
    def mid(self) -> float:
        return round((self.bid + self.ask) / 2, 2) if self.bid > 0 and self.ask > 0 else self.last

    def days(self, today: date | None = None) -> int:
        return (date.fromisoformat(self.expiry) - (today or date.today())).days

    def to_dict(self) -> dict:
        return {"contract": self.contract, "kind": self.kind, "strike": self.strike, "expiry": self.expiry,
                "bid": self.bid, "ask": self.ask, "iv": round(self.iv, 3), "volume": self.volume,
                "open_interest": self.open_interest}


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot: float, strike: float, years: float, vol: float, kind: str, r: float = RATE) -> float:
    """Black-Scholes value per share."""
    if years <= 0 or vol <= 0:
        return max(0.0, spot - strike) if kind == "call" else max(0.0, strike - spot)
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * years) / (vol * math.sqrt(years))
    d2 = d1 - vol * math.sqrt(years)
    if kind == "call":
        return spot * _ncdf(d1) - strike * math.exp(-r * years) * _ncdf(d2)
    return strike * math.exp(-r * years) * _ncdf(-d2) - spot * _ncdf(-d1)


def occ_symbol(underlying: str, expiry: str, kind: str, strike: float) -> str:
    """The standard option symbol, e.g. AAPL250117C00150000."""
    return f"{underlying}{expiry[2:4]}{expiry[5:7]}{expiry[8:10]}{kind[0].upper()}{int(round(strike * 1000)):08d}"


def strike_step(spot: float) -> float:
    return 0.5 if spot < 25 else 1 if spot < 100 else 2.5 if spot < 250 else 5 if spot < 500 else 10


class SyntheticOptions:
    name = "synthetic"

    def chain(self, symbol: str, spot: float, vol: float, kind: str, today: date | None = None,
              min_days: int = 14, max_days: int = 45) -> list[OptionQuote]:
        today = today or date.today()
        iv = max(0.15, vol * 1.15)  # options usually price a bit more volatility than has been realized
        step = strike_step(spot)
        first_friday = today + timedelta(days=(4 - today.weekday()) % 7)
        out = []
        for w in range(1, 9):
            exp = first_friday + timedelta(weeks=w)
            years = (exp - today).days / 365
            for i in range(-6, 7):
                k = round(round(spot / step) * step + i * step, 2)
                if k <= 0:
                    continue
                fair = bs_price(spot, k, years, iv, kind)
                half = max(0.01, fair * 0.025)
                bid = round(max(0.0, fair - half), 2)
                out.append(OptionQuote(occ_symbol(symbol, str(exp), kind, k), symbol, kind, k, str(exp), bid,
                                       round(fair + half, 2), round(fair, 2), iv, 100, 500, self.name))
        return out

    def value(self, pos, spot: float, today: date | None = None) -> float:
        """Re-price a held option (or spread) at today's stock price."""
        years = max(0.0, (date.fromisoformat(pos.expiry) - (today or date.today())).days / 365)
        v = bs_price(spot, pos.strike, years, pos.iv, pos.kind)
        if pos.short_contract:
            v -= bs_price(spot, pos.short_strike, years, pos.iv, pos.kind)
        return round(v, 2)


class YahooOptions(SyntheticOptions):
    name = "yahoo"

    def chain(self, symbol: str, spot: float, vol: float, kind: str, today: date | None = None,
              min_days: int = 14, max_days: int = 45) -> list[OptionQuote]:
        import yfinance as yf
        today = today or date.today()
        t = yf.Ticker(symbol)
        out = []
        expiries = [e for e in (t.options or []) if min_days <= (date.fromisoformat(e) - today).days <= max_days]
        for exp in expiries[:3]:
            ch = t.option_chain(exp)
            df = ch.calls if kind == "call" else ch.puts
            for r in df.itertuples():
                num = lambda v: 0.0 if v != v or v is None else float(v)  # noqa: E731  (NaN -> 0)
                out.append(OptionQuote(r.contractSymbol, symbol, kind, float(r.strike), exp, num(r.bid), num(r.ask),
                                       num(r.lastPrice), num(r.impliedVolatility), int(num(r.volume)),
                                       int(num(r.openInterest)), self.name))
        return out

    def value(self, pos, spot: float, today: date | None = None) -> float:
        """Live mid price if the chain answers, otherwise the model price."""
        try:
            import yfinance as yf
            ch = yf.Ticker(pos.underlying).option_chain(pos.expiry)
            df = ch.calls if pos.kind == "call" else ch.puts

            def mid(contract):
                row = df[df["contractSymbol"] == contract]
                if row.empty:
                    raise KeyError(contract)
                b, a = float(row["bid"].iloc[0]), float(row["ask"].iloc[0])
                return (b + a) / 2 if b > 0 and a > 0 else float(row["lastPrice"].iloc[0])
            v = mid(pos.contract) - (mid(pos.short_contract) if pos.short_contract else 0)
            return round(v, 2)
        except Exception as e:
            log.info("no live quote for %s (%s); using the model price", pos.contract, e)
            return super().value(pos, spot, today)


def make_options(data_source: str):
    return YahooOptions() if data_source == "yahoo" else SyntheticOptions()


def pick(quotes: list[OptionQuote], spot: float, kind: str, budget: float, min_days: int, max_days: int,
         today: date | None = None) -> tuple[OptionQuote | None, OptionQuote | None]:
    """Choose what to buy: a slightly out-of-the-money option 2-6 weeks out, liquid enough to trade.

    Returns (long_leg, short_leg). short_leg is None for a plain call/put; if the plain option
    is over budget, the next strike further out is sold against it (a debit spread).
    """
    sign = 1 if kind == "call" else -1
    target = spot * (1 + sign * 0.03)

    def ok(q: OptionQuote) -> bool:
        if not (min_days <= q.days(today) <= max_days) or q.ask <= 0 or q.bid <= 0:
            return False
        if abs(q.strike / spot - 1) > 0.10:
            return False
        if q.source != "synthetic" and q.open_interest < 10:
            return False
        return (q.ask - q.bid) <= max(0.10, 0.25 * q.mid)  # wide spreads eat the edge

    liquid = [q for q in quotes if ok(q)]
    if not liquid:
        return None, None
    # Nearest expiry to ~30 days, then the strike nearest our target.
    best_exp = min({q.expiry for q in liquid}, key=lambda e: abs((date.fromisoformat(e) - (today or date.today())).days - 30))
    same = sorted([q for q in liquid if q.expiry == best_exp], key=lambda q: q.strike)
    if not same:
        return None, None
    long_leg = min(same, key=lambda q: abs(q.strike - target))
    if long_leg.ask * 100 <= budget:
        return long_leg, None
    further = [q for q in same if (q.strike - long_leg.strike) * sign > 0]
    if further:
        short_leg = min(further, key=lambda q: abs(q.strike - long_leg.strike))
        return long_leg, short_leg
    return long_leg, None
