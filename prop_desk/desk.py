"""The Arena: three trading factions, one market, one scoreboard.

Each session:
  1. Scouting   - load prices, fundamentals, headlines and prediction markets once (the "map")
  2. Upkeep     - every faction settles bets, checks stops/targets and grades old calls
  3. Turns      - each faction reviews its positions, then trades stocks its own way;
                  a faction that made no stock trade may go after prediction markets
  4. Standings  - who is calling it right, and what it cost

Factions never share opinions or money. They share only the market and the
Quartermaster's risk rules, so their results are directly comparable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .agents.brain import has_credentials
from .config import Settings
from .factions import FACTIONS
from .factions.base import Field
from .indicators import compute_features
from .kalshi import make_kalshi
from .market_data import make_provider

log = logging.getLogger(__name__)
Emit = Callable[[dict], Awaitable[None]]


class Arena:
    def __init__(self, settings: Settings, emit: Emit | None):
        self.s = settings
        self._emit = emit
        self.use_claude = settings.ai_mode == "claude" or (settings.ai_mode == "auto" and has_credentials())
        self.provider = None
        self.kalshi = None
        self.bench_price: float | None = None
        self.factions = [FACTIONS[f](self) for f in settings.factions if f in FACTIONS]
        self.running = False
        self.transcript: list[dict] = []
        self.verdicts: dict[str, dict[str, str]] = {}

    @property
    def mode(self) -> str:
        return f"Claude ({self.s.model})" if self.use_claude else "Rules (offline)"

    @property
    def cost_usd(self) -> float:
        return round(sum(f.cost_usd for f in self.factions), 4)

    def faction(self, fid: str):
        return next(f for f in self.factions if f.id == fid)

    # ------------------------------------------------------------ plumbing
    async def emit(self, e: dict) -> None:
        e = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **e}
        self.transcript.append(e)
        if e.get("type") == "verdict" and e.get("faction"):
            self.verdicts.setdefault(e["symbol"], {})[e["faction"]] = e["label"]
        if self._emit:
            await self._emit(e)

    def alert(self, text: str) -> None:
        """Post to a Slack-compatible incoming webhook (Slack, Discord /slack, many others)."""
        if not self.s.alert_webhook:
            return

        def post():
            try:
                req = urllib.request.Request(self.s.alert_webhook, data=json.dumps({"text": text}).encode(),
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10).close()
            except Exception as e:
                log.warning("alert webhook failed: %s", e)

        asyncio.get_running_loop().run_in_executor(None, post)

    def standings(self) -> list[dict]:
        rows = [f.standings() for f in self.factions]
        rows.sort(key=lambda r: (r["net"], r["accuracy"] or 0, r["total_pnl"]), reverse=True)
        return rows

    async def publish(self):
        for f in self.factions:
            await self.emit({"type": "portfolio", "faction": f.id, **f.broker.summary()})
        await self.emit({"type": "standings", "factions": self.standings(), "cost_usd": self.cost_usd})

    # ------------------------------------------------------------ the map
    async def scout(self, symbols: list[str] | None) -> Field:
        loop = asyncio.get_running_loop()
        if self.provider is None:
            self.provider = await loop.run_in_executor(None, make_provider, self.s.data_source, self.s.benchmark)
        if self.kalshi is None and self.s.kalshi_enabled:
            self.kalshi = await loop.run_in_executor(None, make_kalshi, self.s.kalshi_data)
        await self.emit({"type": "session", "mode": self.mode, "data_source": self.provider.name,
                         "markets_source": self.kalshi.name if self.kalshi else "off"})

        bench = await loop.run_in_executor(None, self.provider.snapshot, self.s.benchmark)
        bf = compute_features(bench.bars)
        self.bench_price = bf["price"]
        fld = Field(bench_bars=bench.bars, bench_price=bf["price"], watchlist=symbols or self.s.watchlist,
                    risk_on=(bf["momentum"]["price_vs_sma200_pct"] or 0) > 0, focus=symbols)
        await self.emit({"type": "regime", "risk_on": fld.risk_on, "spy": bf["price"],
                         "spy_vs_200d": bf["momentum"]["price_vs_sma200_pct"]})

        held = [s for f in self.factions for s in f.broker.positions]
        pending = [c["key"] for f in self.factions for c in f.scorecard.pending("stock")]
        for sym in dict.fromkeys(fld.watchlist + held + pending):
            try:
                snap = await loop.run_in_executor(None, self.provider.snapshot, sym)
                fld.snaps[sym], fld.feats[sym] = snap, compute_features(snap.bars, bench.bars)
            except Exception as e:
                await self.emit({"type": "notice", "text": f"Skipping {sym}: {e}"})
        if self.kalshi and not symbols:
            try:
                fld.contracts = await loop.run_in_executor(None, self.kalshi.markets)
            except Exception as e:
                await self.emit({"type": "notice", "text": f"Couldn't load prediction markets: {e}"})

        await self.emit({"type": "map", "minerals": [
            {"symbol": s, "price": fld.feats[s]["price"], "change_1w": fld.feats[s]["momentum"]["return_1w_pct"],
             "sector": fld.snaps[s].fundamentals.get("sector")} for s in fld.watchlist if s in fld.feats],
            "gas": [{"ticker": c.ticker, "title": c.title, "implied": c.implied, "category": c.category}
                    for c in fld.contracts[:8]]})
        return fld

    # ------------------------------------------------------------ session
    async def run_session(self, symbols: list[str] | None = None):
        if self.running:
            return
        self.running = True
        self.transcript, self.verdicts = [], {}
        started = datetime.now(timezone.utc)
        try:
            await self.emit({"type": "phase", "phase": "housekeeping"})
            fld = await self.scout([s.upper() for s in symbols] if symbols else None)
            for f in self.factions:
                graded = await f.housekeep(fld)
                if graded:
                    await f.say(f.leader, f"{graded} old call(s) graded. Check the standings.", "chair")
            await self.publish()

            for f in self.factions:
                await self.emit({"type": "turn", "faction": f.id, "name": f.name, "motto": f.motto})
                await f.review_positions(fld)
                traded = await f.stock_turn(fld)
                if not traded and fld.contracts:
                    traded = await f.gas_turn(fld)
                await self.emit({"type": "turn_end", "faction": f.id, "traded": traded})
                await self.publish()

            for sym, labels in self.verdicts.items():
                if "APPROVED" in labels.values() and any(v != "APPROVED" for v in labels.values()):
                    await self.emit({"type": "contest", "symbol": sym, "verdicts": labels})
            await self.emit({"type": "session_end", "cost_usd": self.cost_usd,
                             "costs": {f.id: f.cost_usd for f in self.factions}})
        except Exception as e:
            log.exception("session failed")
            await self.emit({"type": "error", "text": f"Session failed: {e}"})
        finally:
            self.running = False
            await self.emit({"type": "phase", "phase": "idle"})
            self._save_transcript(started)

    def _save_transcript(self, started: datetime) -> None:
        try:
            os.makedirs(self.s.sessions_dir, exist_ok=True)
            path = os.path.join(self.s.sessions_dir, started.strftime("%Y%m%d-%H%M%S") + ".jsonl")
            with open(path, "w") as f:
                for e in self.transcript:
                    f.write(json.dumps(e, default=str) + "\n")
        except Exception as e:
            log.warning("couldn't save transcript: %s", e)
