"""Paper-trading ledger and the risk manager's hard rules.

Nothing in this file calls an AI. Position sizing, stops and limits are plain
code so they can't be talked out of by a persuasive agent.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Position:
    symbol: str
    side: str  # LONG | SHORT
    qty: int
    entry: float
    stop: float
    target: float
    opened_at: str
    last: float = 0.0
    votes: dict = field(default_factory=dict)

    @property
    def pnl(self) -> float:
        sign = 1 if self.side == "LONG" else -1
        return round((self.last - self.entry) * self.qty * sign, 2)


@dataclass
class RiskReview:
    approved: bool
    qty: int = 0
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    risk_dollars: float = 0.0
    notes: list[str] = field(default_factory=list)


class PaperBroker:
    """Simulated account. Fills instantly at the last price; persists to JSON."""

    def __init__(self, path: str, starting_cash: float):
        self.path = path
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.positions: dict[str, Position] = {}
        self.trades: list[dict] = []
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
        self.positions = {s: Position(**p) for s, p in d.get("positions", {}).items()}
        self.day = d.get("day", self.day)
        self.day_start_equity = d.get("day_start_equity", self.equity)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump({
                "cash": self.cash,
                "positions": {s: asdict(p) for s, p in self.positions.items()},
                "trades": self.trades[-500:],
                "day": self.day,
                "day_start_equity": self.day_start_equity,
            }, f, indent=1)

    def reset(self) -> None:
        self.cash = self.starting_cash
        self.positions, self.trades = {}, []
        self.day_start_equity = self.starting_cash
        self.save()

    # ---------------------------------------------------------- accounting
    @property
    def equity(self) -> float:
        held = sum(p.last * p.qty if p.side == "LONG" else -p.last * p.qty for p in self.positions.values())
        return round(self.cash + held, 2)

    def roll_day(self) -> None:
        today = str(date.today())
        if today != self.day:
            self.day, self.day_start_equity = today, self.equity

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

    def open(self, symbol: str, side: str, review: RiskReview, votes: dict) -> dict:
        cost = review.qty * review.entry
        self.cash += -cost if side == "LONG" else cost
        self.positions[symbol] = Position(symbol, side, review.qty, review.entry, review.stop,
                                          review.target, _now(), review.entry, votes)
        t = {"time": _now(), "symbol": symbol, "action": "BUY" if side == "LONG" else "SELL SHORT",
             "qty": review.qty, "price": review.entry, "stop": review.stop, "target": review.target}
        self.trades.append(t)
        self.save()
        return t

    def close(self, symbol: str, price: float, why: str) -> dict:
        p = self.positions.pop(symbol)
        p.last = price
        self.cash += price * p.qty if p.side == "LONG" else -price * p.qty
        t = {"time": _now(), "symbol": symbol, "action": "SELL" if p.side == "LONG" else "BUY TO COVER",
             "qty": p.qty, "price": round(price, 2), "pnl": p.pnl, "reason": why}
        self.trades.append(t)
        self.save()
        return t

    def summary(self) -> dict:
        return {
            "cash": round(self.cash, 2),
            "equity": self.equity,
            "day_pnl": round(self.equity - self.day_start_equity, 2),
            "total_pnl": round(self.equity - self.starting_cash, 2),
            "positions": [{**asdict(p), "pnl": p.pnl} for p in self.positions.values()],
            "trades": self.trades[-50:][::-1],
        }


def review_trade(broker: PaperBroker, symbol: str, side: str, price: float, atr: float, s) -> RiskReview:
    """The risk manager's rulebook. `s` is the Settings object."""
    notes: list[str] = []
    broker.roll_day()
    eq = broker.equity
    day_loss_pct = (eq - broker.day_start_equity) / broker.day_start_equity * 100
    if day_loss_pct <= -s.max_daily_loss_pct:
        return RiskReview(False, notes=[f"Desk is down {day_loss_pct:.1f}% today - daily loss limit "
                                        f"({s.max_daily_loss_pct}%) hit, trading halted"])
    if symbol in broker.positions:
        return RiskReview(False, notes=[f"Already holding {symbol}; no doubling up"])
    if len(broker.positions) >= s.max_open_positions:
        return RiskReview(False, notes=[f"Book is full ({s.max_open_positions} positions)"])
    if not atr or atr <= 0 or math.isnan(atr):
        return RiskReview(False, notes=["No volatility reading - can't size the trade"])

    stop_dist = 2 * atr
    sign = 1 if side == "LONG" else -1
    stop = round(price - sign * stop_dist, 2)
    target = round(price + sign * 3 * atr, 2)
    risk_budget = eq * s.risk_per_trade_pct / 100
    qty = int(risk_budget // stop_dist)
    notes.append(f"Risking {s.risk_per_trade_pct}% of equity (${risk_budget:,.0f}) with a 2×ATR stop at {stop}.")

    cap = int(eq * s.max_position_pct / 100 // price)
    if qty > cap:
        qty = cap
        notes.append(f"Capped at {s.max_position_pct}% of equity.")
    if side == "LONG" and qty * price > broker.cash:
        qty = int(broker.cash // price)
        notes.append("Trimmed to available cash.")
    if qty < 1:
        return RiskReview(False, notes=notes + ["Position size rounds to zero shares"])

    notes.append(f"Target {target} (3×ATR, 1.5R).")
    return RiskReview(True, qty, round(price, 2), stop, target, round(qty * stop_dist, 2), notes)
