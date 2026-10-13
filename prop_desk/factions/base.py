"""What every faction has in common.

Each faction is its own little firm: its own paper account, its own track record,
its own AI budget, and its own way of deciding. They all face the same market,
the same risk rules (enforced in code by the neutral Quartermaster) and the same
scoring, so the Arena is a fair fight between trading philosophies.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from ..agents import personas as P
from ..agents.brain import ClaudeBrain
from ..broker import PaperBroker, review_contract, review_trade
from ..indicators import ichimoku
from ..kalshi import Contract
from ..market_data import Snapshot
from ..scorecard import Scorecard

if TYPE_CHECKING:
    from ..desk import Arena

log = logging.getLogger(__name__)
SIGN = {"LONG": 1, "YES": 1, "SHORT": -1, "NO": -1, "PASS": 0}


@dataclass
class Field:
    """The battlefield for one session: market data every faction sees."""
    snaps: dict[str, Snapshot] = field(default_factory=dict)
    feats: dict[str, dict] = field(default_factory=dict)
    bench_bars: object = None
    bench_price: float | None = None
    risk_on: bool = True
    watchlist: list[str] = field(default_factory=list)
    contracts: list[Contract] = field(default_factory=list)
    focus: list[str] | None = None  # symbols the user asked for


class Faction:
    id = "base"
    name = "Faction"
    color = "#ffffff"
    motto = ""
    leader = "chair"

    def __init__(self, arena: "Arena"):
        self.arena = arena
        self.s = arena.s
        root = os.path.join(self.s.data_dir, self.id)
        self.broker = PaperBroker(os.path.join(root, "ledger.json"), self.s.starting_cash)
        self.scorecard = Scorecard(os.path.join(root, "scorecard.json"), self.s.call_horizon_days)
        self.brain = ClaudeBrain(self.s.model, self.s.effort) if arena.use_claude else None
        self.research_cost = 0.0

    # ------------------------------------------------------------ identity
    @property
    def units(self) -> list[P.Persona]:
        return [p for p in P.ALL if p.faction == self.id]

    @property
    def voters(self) -> list[P.Persona]:
        return [p for p in self.units if p.kind == "trader"]

    @property
    def cost_usd(self) -> float:
        return round((self.brain.cost_usd if self.brain else 0) + self.research_cost, 4)

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "color": self.color, "motto": self.motto, "leader": self.leader,
                "units": [u.id for u in self.units]}

    # ------------------------------------------------------------ messaging
    async def emit(self, e: dict) -> None:
        await self.arena.emit({"faction": self.id, **e})

    async def say(self, agent: str, text: str, kind: str = "chat", symbol: str | None = None, **extra):
        await self.emit({"type": "status", "agent": agent, "status": "speaking"})
        await self.emit({"type": "message", "agent": agent, "text": text, "kind": kind, "symbol": symbol, **extra})
        if not self.brain:
            await asyncio.sleep(self.s.pace)
        await self.emit({"type": "status", "agent": agent, "status": "idle"})

    async def phase(self, name: str, symbol: str | None = None):
        await self.emit({"type": "phase", "phase": name, "symbol": symbol})

    async def pause(self, k: float = 1.0):
        if not self.brain:
            await asyncio.sleep(self.s.pace * k)

    async def ask(self, persona: P.Persona, system: str, prompt: str, schema, fallback):
        """Ask Claude; on any failure use the rule-based fallback so the session continues."""
        if not self.brain:
            return fallback()
        await self.emit({"type": "status", "agent": persona.id, "status": "thinking"})
        try:
            out = await self.brain.think(system, prompt, schema)
            if out is not None:
                return out
            note = "Claude declined this request"
        except Exception as e:  # network, rate limit, bad key...
            log.exception("Claude call failed for %s", persona.id)
            note = f"Claude error: {type(e).__name__}"
        await self.emit({"type": "notice", "text": f"{persona.name}: {note} - using rule-based view"})
        return fallback()

    def weight(self, unit: str, base: float = 1.0) -> float:
        return self.scorecard.weight(unit, base)

    # ------------------------------------------------------------ bookkeeping (same for everyone)
    async def housekeep(self, fld: Field):
        for t in self.broker.mark({s: sn.price for s, sn in fld.snaps.items()}):
            await self.say("risk", f"{self.name}: closed {t['symbol']} - {t['reason']} at {t['price']} "
                                   f"(P&L ${t['pnl']:+,.2f}).", "risk")
            self.arena.alert(f"{self.name} closed {t['symbol']} ({t['reason']}) P&L ${t['pnl']:+,.2f}")
        loop = asyncio.get_running_loop()
        if self.arena.kalshi:
            for tk in list(self.broker.contracts):
                try:
                    c = await loop.run_in_executor(None, self.arena.kalshi.market, tk)
                except Exception as e:
                    await self.emit({"type": "notice", "text": f"Couldn't price {tk}: {e}"})
                    continue
                if t := self.broker.mark_contract(c):
                    await self.say("risk", f"{self.name}: \"{t['title']}\" settled {c.result.upper()} - "
                                           f"{t['reason']} (P&L ${t['pnl']:+,.2f}).", "risk")
                    self.arena.alert(f"{self.name}: {t['title']} settled {c.result.upper()}, P&L ${t['pnl']:+,.2f}")
        graded = 0
        for call in self.scorecard.pending("stock"):
            if call["key"] in fld.snaps:
                self.scorecard.resolve_stock(call, fld.snaps[call["key"]].price)
                graded += 1
        if self.arena.kalshi:
            for call in self.scorecard.pending("contract", due_only=False):
                try:
                    c = await loop.run_in_executor(None, self.arena.kalshi.market, call["key"])
                except Exception:
                    continue
                if c.result in ("yes", "no"):
                    self.scorecard.resolve_contract(call, c.result)
                    graded += 1
        if graded:
            self.scorecard.save()
        self.broker.snapshot(fld.bench_price)
        return graded

    def quick_view(self, sym: str, fld: Field) -> float:
        """Cheap code-only opinion (-1..+1) used for position reviews. Factions override."""
        return 0.0

    async def review_positions(self, fld: Field):
        for sym, pos in list(self.broker.positions.items()):
            if sym not in fld.snaps:
                continue
            score = self.quick_view(sym, fld)
            held = 1 if pos.side == "LONG" else -1
            if score * held < -0.2:
                t = self.broker.close(sym, fld.snaps[sym].price, f"{self.name} turned against it")
                await self.say(self.leader, f"We've turned on {sym} ({score:+.2f}). Pulling out at {t['price']}, "
                                            f"P&L ${t['pnl']:+,.2f}.", "decision", sym)
                await self.emit({"type": "retreat", "symbol": sym})
                self.arena.alert(f"{self.name} exited {sym}, P&L ${t['pnl']:+,.2f}")

    # ------------------------------------------------------------ execution (same rules for everyone)
    async def execute_stock(self, sym: str, direction: str, fld: Field, votes: dict) -> dict | None:
        snap, feats = fld.snaps[sym], fld.feats[sym]
        await self.phase("risk", sym)
        ne = snap.fundamentals.get("next_earnings")
        dte = (date.fromisoformat(str(ne)[:10]) - date.today()).days if ne else None
        rv = review_trade(self.broker, sym, direction, feats["price"], feats["volatility"]["atr14"], self.s,
                          sector=snap.fundamentals.get("sector") or "", risk_on=fld.risk_on, days_to_earnings=dte)
        verdict = (f"Signed off for {self.name}: {rv.qty} shares, stop {rv.stop}, target {rv.target}, "
                   f"max loss ${rv.risk_dollars:,.0f}." if rv.approved else f"VETO for {self.name}.")
        await self.say("risk", f"{verdict} {' '.join(rv.notes)}", "risk", sym, approved=rv.approved)
        if not rv.approved:
            return None
        await self.phase("execute", sym)
        order = self.broker.open(sym, direction, rv, votes, snap.fundamentals.get("sector") or "")
        await self.emit({"type": "order", **order})
        self.arena.alert(f"{self.name}: {order['action']} {order['qty']} {sym} @ {order['price']} "
                         f"(stop {order['stop']}, target {order['target']})")
        self.broker.snapshot(fld.bench_price)
        return order

    async def execute_contract(self, c: Contract, side: str, prob: float, votes: dict) -> dict | None:
        await self.phase("risk", c.ticker)
        rv = review_contract(self.broker, c, side, prob, self.s)
        await self.say("risk", (f"Signed off for {self.name}. " if rv.approved else f"VETO for {self.name}. ")
                       + " ".join(rv.notes), "risk", c.ticker, approved=rv.approved)
        if not rv.approved:
            return None
        await self.phase("execute", c.ticker)
        order = self.broker.open_contract(c, side, rv, votes)
        await self.emit({"type": "order", **order})
        self.arena.alert(f"{self.name}: bought {order['qty']} {side} on \"{c.title}\" @ {order['price']}¢")
        self.broker.snapshot(self.arena.bench_price)
        return order

    def stock_candidate(self, sym: str, fld: Field) -> dict:
        """The 'candidate' event for a stock: price, fundamentals, candles and the Ichimoku cloud."""
        snap, feats = fld.snaps[sym], fld.feats[sym]
        bars = snap.bars.tail(90)
        _, _, span_a, span_b = ichimoku(snap.bars)
        # The cloud under each bar is the spans computed 26 bars earlier.
        cloud = [[None if a != a else round(float(a), 2), None if b != b else round(float(b), 2)]
                 for a, b in zip(span_a.shift(26).tail(90), span_b.shift(26).tail(90))]
        return {"type": "candidate", "kind": "stock", "symbol": sym, "title": sym, "price": feats["price"],
                "features": feats, "fundamentals": snap.fundamentals, "cloud": cloud,
                "candles": [[str(i.date()), round(r.open, 2), round(r.high, 2), round(r.low, 2), round(r.close, 2)]
                            for i, r in bars.iterrows()]}

    def record(self, kind: str, key: str, title: str, price: float, unit_votes: dict[str, str],
               decision: str, traded: bool, due: str | None = None) -> None:
        """Every idea becomes a graded call - for each unit and for the faction itself."""
        self.scorecard.record(kind, key, title, price, {**unit_votes, self.id: decision}, traded, due)

    # ------------------------------------------------------------ the faction's turn
    async def stock_turn(self, fld: Field) -> bool:
        raise NotImplementedError

    async def gas_turn(self, fld: Field) -> bool:
        return False

    def standings(self) -> dict:
        st = self.scorecard.stats()
        me = st.get(self.id, {"calls": 0, "hits": 0, "accuracy": None, "edge": 0, "streak": 0, "recent": []})
        b = self.broker.summary()
        units = [{"agent": u.id, **st.get(u.id, {"calls": 0, "hits": 0, "accuracy": None, "edge": 0, "streak": 0,
                                                  "recent": [], "passes": 0}),
                  "weight": self.weight(u.id, u.weight)} for u in self.voters]
        return {
            "id": self.id, "name": self.name, "color": self.color,
            "calls": me["calls"], "hits": me["hits"], "misses": me["calls"] - me["hits"],
            "net": me["hits"] * 2 - me["calls"], "accuracy": me["accuracy"], "streak": me["streak"],
            "recent": me["recent"], "equity": b["equity"], "total_pnl": b["total_pnl"], "day_pnl": b["day_pnl"],
            "positions": len(b["positions"]), "contracts": len(b["contracts"]), "cost_usd": self.cost_usd,
            "units": units,
        }
