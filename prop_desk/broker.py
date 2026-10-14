"""Paper-trading ledger and the risk manager's hard rules.

Nothing in this file calls an AI. Position sizing, stops and limits are plain
code so they can't be talked out of by a persuasive agent.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timezone

from .kalshi import fee_dollars


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def px(x: float) -> float:
    """Round a price sensibly: cents for stocks, more digits for currencies and cheap coins."""
    return round(x, 2 if abs(x) >= 10 else 4 if abs(x) >= 1 else 6)


def _build(cls, d: dict):
    """Load a dataclass from JSON, ignoring unknown keys (older ledger files)."""
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class Position:
    symbol: str
    side: str  # LONG | SHORT
    qty: float  # fractional shares allowed
    entry: float
    stop: float
    target: float
    opened_at: str
    last: float = 0.0
    votes: dict = field(default_factory=dict)
    sector: str = ""

    @property
    def pnl(self) -> float:
        sign = 1 if self.side == "LONG" else -1
        return round((self.last - self.entry) * self.qty * sign, 2)


@dataclass
class ContractPosition:
    ticker: str
    title: str
    side: str  # YES | NO
    qty: int
    price: int  # cents paid per contract
    fee: float  # dollars
    opened_at: str
    close_time: str
    category: str = ""
    last: int = 0  # cents, what we could sell for now
    votes: dict = field(default_factory=dict)

    @property
    def value(self) -> float:
        return self.qty * self.last / 100

    @property
    def pnl(self) -> float:
        return round(self.value - self.qty * self.price / 100 - self.fee, 2)


@dataclass
class OptionPosition:
    """A bought call or put (buy-only: the most we can lose is what we paid).

    If `short_contract` is set it's a debit spread: we also sold a further-out option of the
    same kind and expiry, which makes the trade cheaper but caps the gain at the strike gap.
    Prices are per share; one contract covers 100 shares.
    """
    contract: str
    underlying: str
    kind: str  # call | put
    strike: float
    expiry: str  # YYYY-MM-DD
    qty: int  # contracts
    price: float  # net premium paid per share
    opened_at: str
    iv: float = 0.0  # implied volatility at entry, used to re-price if no live quote
    last: float = 0.0  # net value per share now
    short_contract: str = ""
    short_strike: float = 0.0
    votes: dict = field(default_factory=dict)

    @property
    def spread(self) -> bool:
        return bool(self.short_contract)

    @property
    def cost(self) -> float:
        return round(self.qty * 100 * self.price, 2)

    @property
    def value(self) -> float:
        return self.qty * 100 * self.last

    @property
    def pnl(self) -> float:
        return round(self.value - self.cost, 2)

    def intrinsic(self, underlying_px: float) -> float:
        """Value per share at expiry."""
        def leg(k):
            return max(0.0, underlying_px - k) if self.kind == "call" else max(0.0, k - underlying_px)
        return leg(self.strike) - (leg(self.short_strike) if self.spread else 0.0)

    def label(self) -> str:
        k = f"${self.strike:g}" + (f"/${self.short_strike:g}" if self.spread else "")
        return f"{self.underlying} {k} {self.kind}{' spread' if self.spread else ''} {self.expiry[5:]}"


@dataclass
class RiskReview:
    approved: bool
    qty: float = 0
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    risk_dollars: float = 0.0
    fee: float = 0.0
    notes: list[str] = field(default_factory=list)


class PaperBroker:
    """Simulated account. Fills instantly at the last price; persists to JSON."""

    def __init__(self, path: str, starting_cash: float):
        self.path = path
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.positions: dict[str, Position] = {}
        self.contracts: dict[str, ContractPosition] = {}
        self.options: dict[str, OptionPosition] = {}
        self.trades: list[dict] = []
        self.history: list[dict] = []  # equity curve: {t, equity, bench}
        self.day = str(date.today())
        self.day_start_equity = starting_cash
        self._load()

    # ---------------------------------------------------------- persistence
    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path) as f:
            d = json.load(f)
        self.cash = d["cash"]
        self.trades = d.get("trades", [])
        self.history = d.get("history", [])
        self.positions = {s: _build(Position, p) for s, p in d.get("positions", {}).items()}
        self.contracts = {t: _build(ContractPosition, p) for t, p in d.get("contracts", {}).items()}
        self.options = {t: _build(OptionPosition, p) for t, p in d.get("options", {}).items()}
        self.day = d.get("day", self.day)
        self.day_start_equity = d.get("day_start_equity", self.equity)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump({
                "cash": self.cash,
                "positions": {s: asdict(p) for s, p in self.positions.items()},
                "contracts": {t: asdict(c) for t, c in self.contracts.items()},
                "options": {t: asdict(o) for t, o in self.options.items()},
                "trades": self.trades[-1000:],
                "history": self.history[-3000:],
                "day": self.day,
                "day_start_equity": self.day_start_equity,
            }, f, indent=1)

    def reset(self) -> None:
        self.cash = self.starting_cash
        self.positions, self.contracts, self.options, self.trades, self.history = {}, {}, {}, [], []
        self.day_start_equity = self.starting_cash
        self.save()

    # ---------------------------------------------------------- accounting
    @property
    def equity(self) -> float:
        held = sum(p.last * p.qty if p.side == "LONG" else -p.last * p.qty for p in self.positions.values())
        bets = sum(c.value for c in self.contracts.values())
        opts = sum(o.value for o in self.options.values())
        return round(self.cash + held + bets + opts, 2)

    @property
    def contract_exposure(self) -> float:
        return sum(c.qty * c.price / 100 for c in self.contracts.values())

    @property
    def option_exposure(self) -> float:
        return sum(o.cost for o in self.options.values())

    def drawdown(self, since: str | None = None) -> float:
        """Worst peak-to-trough drop of the equity curve, in % (since a timestamp, e.g. season start)."""
        curve = [h["equity"] for h in self.history if not since or h["t"] >= since] + [self.equity]
        peak, worst = curve[0], 0.0
        for e in curve:
            peak = max(peak, e)
            if peak > 0:
                worst = max(worst, (peak - e) / peak * 100)
        return round(worst, 2)

    def roll_day(self) -> None:
        today = str(date.today())
        if today != self.day:
            self.day, self.day_start_equity = today, self.equity

    def snapshot(self, bench_price: float | None) -> None:
        """Add a point to the equity curve (benchmark price lets the GUI compare to SPY)."""
        self.history.append({"t": _now(), "equity": self.equity, "bench": bench_price})
        self.save()

    def mark(self, prices: dict[str, float]) -> list[dict]:
        """Update prices; close anything that hit its stop or target."""
        closed = []
        for sym, px in prices.items():
            p = self.positions.get(sym)
            if not p:
                continue
            p.last = px
            long = p.side == "LONG"
            if (long and px <= p.stop) or (not long and px >= p.stop):
                closed.append(self.close(sym, px, "stop hit"))
            elif (long and px >= p.target) or (not long and px <= p.target):
                closed.append(self.close(sym, px, "target hit"))
        return closed

    def open(self, symbol: str, side: str, review: RiskReview, votes: dict, sector: str = "") -> dict:
        cost = round(review.qty * review.entry, 2)
        self.cash = round(self.cash - cost if side == "LONG" else self.cash + cost, 2)
        self.positions[symbol] = Position(symbol, side, review.qty, review.entry, review.stop,
                                          review.target, _now(), review.entry, votes, sector)
        t = {"time": _now(), "symbol": symbol, "action": "BUY" if side == "LONG" else "SELL SHORT",
             "qty": review.qty, "price": review.entry, "stop": review.stop, "target": review.target, "kind": "stock"}
        self.trades.append(t)
        self.save()
        return t

    def close(self, symbol: str, price: float, why: str) -> dict:
        p = self.positions.pop(symbol)
        p.last = price
        self.cash = round(self.cash + (price * p.qty if p.side == "LONG" else -price * p.qty), 2)
        t = {"time": _now(), "symbol": symbol, "action": "SELL" if p.side == "LONG" else "BUY TO COVER",
             "qty": p.qty, "price": px(price), "pnl": p.pnl, "reason": why, "kind": "stock"}
        self.trades.append(t)
        self.save()
        return t

    # ---------------------------------------------------------- prediction markets
    def open_contract(self, c, side: str, review: RiskReview, votes: dict) -> dict:
        price = int(review.entry)
        self.cash -= review.qty * price / 100 + review.fee
        self.contracts[c.ticker] = ContractPosition(
            c.ticker, c.title, side, review.qty, price, review.fee, _now(), c.close_time, c.category,
            c.mark(side), votes)
        t = {"time": _now(), "symbol": c.ticker, "title": c.title, "action": f"BUY {side}",
             "qty": review.qty, "price": price, "fee": review.fee, "kind": "contract"}
        self.trades.append(t)
        self.save()
        return t

    def mark_contract(self, c) -> dict | None:
        """Update a contract's price; pay out if the market has settled."""
        pos = self.contracts.get(c.ticker)
        if not pos:
            return None
        if c.result in ("yes", "no"):
            won = c.result == pos.side.lower()
            pos.last = 100 if won else 0
            self.contracts.pop(c.ticker)
            self.cash += pos.value
            t = {"time": _now(), "symbol": c.ticker, "title": pos.title, "action": "SETTLED " + c.result.upper(),
                 "qty": pos.qty, "price": pos.last, "pnl": pos.pnl, "reason": "won" if won else "lost",
                 "kind": "contract"}
            self.trades.append(t)
            self.save()
            return t
        pos.last = c.mark(pos.side)
        return None

    # ---------------------------------------------------------- options
    def open_option(self, q, review: RiskReview, votes: dict, short=None) -> dict:
        """Buy `review.qty` contracts of option quote `q` (and sell `short` if it's a spread)."""
        net = round(q.ask - (short.bid if short else 0), 2)
        self.cash = round(self.cash - review.qty * 100 * net, 2)
        pos = OptionPosition(q.contract, q.underlying, q.kind, q.strike, q.expiry, review.qty, net, _now(), q.iv,
                             round(q.mid - (short.mid if short else 0), 2),
                             short.contract if short else "", short.strike if short else 0.0, votes)
        self.options[q.contract] = pos
        t = {"time": _now(), "symbol": q.underlying, "title": pos.label(), "action": f"BUY {q.kind.upper()}"
             + (" SPREAD" if short else ""), "qty": review.qty, "price": net, "cost": pos.cost, "kind": "option",
             "expiry": q.expiry, "strike": q.strike}
        self.trades.append(t)
        self.save()
        return t

    def close_option(self, contract: str, value: float, why: str) -> dict:
        o = self.options.pop(contract)
        o.last = max(0.0, value)
        self.cash = round(self.cash + o.value, 2)
        t = {"time": _now(), "symbol": o.underlying, "title": o.label(), "action": "SELL " + o.kind.upper()
             + (" SPREAD" if o.spread else ""), "qty": o.qty, "price": round(o.last, 2), "pnl": o.pnl,
             "reason": why, "kind": "option"}
        self.trades.append(t)
        self.save()
        return t

    def mark_option(self, contract: str, value: float | None, underlying_px: float | None, s,
                    today: date | None = None) -> dict | None:
        """Update an option's value; settle at expiry and apply the take-profit / stop / time exits."""
        o = self.options.get(contract)
        if not o:
            return None
        days = (date.fromisoformat(o.expiry) - (today or date.today())).days
        if days <= 0 and underlying_px is not None:
            return self.close_option(contract, o.intrinsic(underlying_px), "expired")
        if value is None:
            return None
        o.last = round(max(0.0, value), 2)
        if o.price > 0 and o.last >= o.price * (1 + s.options_take_profit_pct / 100):
            return self.close_option(contract, o.last, f"took profit (+{s.options_take_profit_pct:g}%)")
        if o.price > 0 and o.last <= o.price * (1 - s.options_stop_pct / 100):
            return self.close_option(contract, o.last, f"stopped out (-{s.options_stop_pct:g}%)")
        if days <= 3:
            return self.close_option(contract, o.last, "sold before expiry")
        return None

    def summary(self) -> dict:
        return {
            "cash": round(self.cash, 2),
            "equity": self.equity,
            "day_pnl": round(self.equity - self.day_start_equity, 2),
            "total_pnl": round(self.equity - self.starting_cash, 2),
            "positions": [{**asdict(p), "pnl": p.pnl} for p in self.positions.values()],
            "contracts": [{**asdict(c), "pnl": c.pnl, "value": round(c.value, 2)} for c in self.contracts.values()],
            "options": [{**asdict(o), "pnl": o.pnl, "value": round(o.value, 2), "cost": o.cost, "label": o.label(),
                         "spread": o.spread} for o in self.options.values()],
            "trades": self.trades[-50:][::-1],
            "history": self.history[-500:],
        }


# ---------------------------------------------------------------- risk rules
def _halted(broker: PaperBroker, s) -> str | None:
    broker.roll_day()
    loss = (broker.equity - broker.day_start_equity) / broker.day_start_equity * 100
    if loss <= -s.max_daily_loss_pct:
        return f"Desk is down {loss:.1f}% today - daily loss limit ({s.max_daily_loss_pct}%) hit, trading halted"
    return None


def max_position_pct(s, asset: str) -> float:
    return {"crypto": s.crypto_max_position_pct, "forex": s.forex_max_position_pct}.get(asset, s.max_position_pct)


def review_trade(broker: PaperBroker, symbol: str, side: str, price: float, atr: float, s,
                 sector: str = "", risk_on: bool = True, days_to_earnings: int | None = None,
                 asset: str = "stock") -> RiskReview:
    """The risk manager's rulebook for stocks, crypto and currencies. `s` is the Settings object."""
    notes: list[str] = []
    if why := _halted(broker, s):
        return RiskReview(False, notes=[why])
    if symbol in broker.positions:
        return RiskReview(False, notes=[f"Already holding {symbol}; no doubling up."])
    if len(broker.positions) >= s.max_open_positions:
        return RiskReview(False, notes=[f"Book is full ({s.max_open_positions} positions)."])
    if sector and sum(p.sector == sector for p in broker.positions.values()) >= s.max_per_sector:
        return RiskReview(False, notes=[f"Already {s.max_per_sector} {sector} positions - too concentrated."])
    if days_to_earnings is not None and 0 <= days_to_earnings <= s.earnings_blackout_days:
        return RiskReview(False, notes=[f"Earnings in {days_to_earnings} day(s) - gap risk, we wait."])
    if not atr or atr <= 0 or math.isnan(atr):
        return RiskReview(False, notes=["No volatility reading - can't size the trade."])

    stop_dist = 2 * atr
    sign = 1 if side == "LONG" else -1
    stop = px(price - sign * stop_dist)
    target = px(price + sign * 3 * atr)
    eq = broker.equity
    risk_pct = s.risk_per_trade_pct if risk_on else s.risk_per_trade_pct / 2
    risk_budget = eq * risk_pct / 100
    frac = s.fractional_shares
    # Fractional shares (as most brokers now offer) let small accounts hold any stock.
    step = 10 ** (8 if price >= 1000 else 4)  # finer steps for expensive coins (0.00000001 BTC)
    shares = (lambda q: math.floor(q * step) / step) if frac else (lambda q: float(int(q)))
    qty = shares(risk_budget / stop_dist)
    if not risk_on:
        notes.append("Market is in risk-off mode (SPY below its 200-day average) - half size.")
    notes.append(f"Risking {risk_pct:g}% of equity (${risk_budget:,.2f}) with a 2×ATR stop at {stop}.")

    cap_pct = max_position_pct(s, asset)
    cap = shares(eq * cap_pct / 100 / price)
    if qty > cap:
        qty = cap
        notes.append(f"Capped at {cap_pct:g}% of equity{' for ' + asset if asset != 'stock' else ''}.")
    if side == "LONG" and qty * price > broker.cash:
        qty = shares(broker.cash / price)
        notes.append("Trimmed to available cash.")
    if (frac and qty * price < s.min_order_dollars) or (not frac and qty < 1):
        return RiskReview(False, notes=notes + [f"Position is too small to place (${qty * price:,.2f})."])
    if not frac:
        qty = int(qty)

    notes.append(f"Target {target} (3×ATR, 1.5R).")
    return RiskReview(True, qty, px(price), stop, target, round(qty * stop_dist, 2), notes=notes)


def review_contract(broker: PaperBroker, c, side: str, prob_yes: float, s) -> RiskReview:
    """Rulebook for prediction markets: quarter-Kelly sizing with hard caps."""
    if why := _halted(broker, s):
        return RiskReview(False, notes=[why])
    if c.ticker in broker.contracts:
        return RiskReview(False, notes=["Already holding this market."])
    price = c.ask(side)
    p_win = (prob_yes if side == "YES" else 100 - prob_yes) / 100
    cost = price / 100
    kelly = (p_win - cost) / (1 - cost) if cost < 1 else 0
    if kelly <= 0:
        return RiskReview(False, notes=[f"No positive edge at {price}¢ - the price already reflects our view."])
    eq = broker.equity
    budget = min(eq * kelly / 4, eq * s.kalshi_max_pct / 100)
    room = eq * s.kalshi_total_pct / 100 - broker.contract_exposure
    notes = [f"Quarter-Kelly says {kelly / 4 * 100:.1f}% of equity; capped at {s.kalshi_max_pct}% per market."]
    if room < budget:
        budget = max(0.0, room)
        notes.append(f"Prediction-market book is near its {s.kalshi_total_pct}% limit.")
    qty = int(budget // cost)
    if qty < 1:
        return RiskReview(False, notes=notes + ["Size rounds to zero contracts."])
    fee = fee_dollars(price, qty)
    max_loss = qty * cost + fee
    notes.append(f"{qty} {side} @ {price}¢, fee ${fee:.2f}. Max loss ${max_loss:,.2f} if it goes the other way.")
    return RiskReview(True, qty, price, 0, 100, round(max_loss, 2), fee, notes)


def review_option(broker: PaperBroker, q, s, short=None) -> RiskReview:
    """Rulebook for options: buy-only, so the premium is the maximum loss. Hard caps per trade and in total."""
    if why := _halted(broker, s):
        return RiskReview(False, notes=[why])
    if any(o.underlying == q.underlying for o in broker.options.values()):
        return RiskReview(False, notes=[f"Already holding an option on {q.underlying}."])
    net = round(q.ask - (short.bid if short else 0), 2)
    if net <= 0:
        return RiskReview(False, notes=["No sensible price on this option right now."])
    per_contract = net * 100
    eq = broker.equity
    limit = eq * s.options_trade_pct / 100
    room = eq * s.options_total_pct / 100 - broker.option_exposure
    what = f"the ${q.strike:g}/${short.strike:g} {q.kind} spread" if short else f"the ${q.strike:g} {q.kind}"
    notes = [f"One contract of {what} ({q.expiry}) costs ${per_contract:,.2f} (100 shares × ${net:.2f})."]
    budget = min(limit, room, broker.cash)
    why = (f"only ${max(room, 0):,.2f} of room left in the options book ({s.options_total_pct:g}% of equity in total)"
           if room < limit else f"our limit is ${limit:,.2f} per option trade ({s.options_trade_pct:g}% of equity)")
    qty = int(budget // per_contract)
    if qty < 1:
        return RiskReview(False, notes=notes + [f"Too expensive: {why}."])
    cost = round(qty * per_contract, 2)
    notes.append(f"Buying {qty}. Max loss ${cost:,.2f} (the premium). Exits: +{s.options_take_profit_pct:g}%, "
                 f"-{s.options_stop_pct:g}%, or 3 days before expiry.")
    return RiskReview(True, qty, net, 0, 0, cost, notes=notes)
