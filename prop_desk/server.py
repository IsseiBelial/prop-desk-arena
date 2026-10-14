"""Web server: serves the trading-floor GUI and streams desk events over a WebSocket.

Run:  python -m prop_desk.server      then open http://localhost:8000
"""
from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agents import personas as P
from .config import settings
from .desk import Arena

logging.basicConfig(level=logging.INFO)
STATIC = os.path.join(os.path.dirname(__file__), "static")

@asynccontextmanager
async def lifespan(app):
    if settings.autorun_minutes > 0 or settings.schedule_et:
        asyncio.create_task(_autorun())
    yield


app = FastAPI(title="Prop Desk Arena", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

clients: set[WebSocket] = set()
history: deque[dict] = deque(maxlen=400)  # replayed to browsers that join mid-session


async def broadcast(event: dict) -> None:
    history.append(event)
    for ws in list(clients):
        try:
            await ws.send_json(event)
        except Exception:
            clients.discard(ws)


arena = Arena(settings, broadcast)


class RunRequest(BaseModel):
    symbols: list[str] | None = None


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/state")
async def state():
    return {
        "mode": arena.mode,
        "running": arena.running,
        "agents": [p.public() for p in P.ALL],
        "factions": [f.public() for f in arena.factions],
        "standings": arena.standings(),
        "season": arena.season_info(),
        "portfolios": {f.id: f.broker.summary() for f in arena.factions},
        "cost_usd": arena.cost_usd,
        "autorun_minutes": settings.autorun_minutes,
        "schedule_et": settings.schedule_et,
        "settings": {
            "watchlist": settings.watchlist,
            "threshold": settings.approve_threshold,
            "quorum": settings.min_agreeing_traders,
            "risk_per_trade_pct": settings.risk_per_trade_pct,
            "max_position_pct": settings.max_position_pct,
            "max_open_positions": settings.max_open_positions,
            "max_daily_loss_pct": settings.max_daily_loss_pct,
            "allow_shorts": settings.allow_shorts,
            "min_families": settings.min_families,
            "kalshi": settings.kalshi_enabled,
            "kalshi_min_edge": settings.kalshi_min_edge_cents,
            "starting_cash": settings.starting_cash,
            "assets": settings.assets,
            "crypto": settings.crypto,
            "forex": settings.forex,
            "options_total_pct": settings.options_total_pct,
            "options_trade_pct": settings.options_trade_pct,
        },
    }


@app.get("/api/sessions")
async def sessions():
    """Saved session transcripts, newest first."""
    files = sorted(glob.glob(os.path.join(settings.sessions_dir, "*.jsonl")), reverse=True)[:50]
    return [os.path.basename(f).removesuffix(".jsonl") for f in files]


@app.get("/api/sessions/{name}")
async def session_transcript(name: str):
    path = os.path.join(settings.sessions_dir, os.path.basename(name) + ".jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f]


@app.post("/api/session")
async def run_session(req: RunRequest):
    if arena.running:
        return {"started": False, "reason": "A session is already running"}
    history.clear()
    asyncio.create_task(arena.run_session(req.symbols))
    return {"started": True}


@app.post("/api/reset")
async def reset():
    if arena.running:
        return {"ok": False, "reason": "Wait for the session to finish"}
    for f in arena.factions:  # start over: money, track records and season history
        f.broker.reset()
        f.scorecard.reset()
    arena.season.reset()
    await arena.publish()
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        for e in list(history):
            await ws.send_json({**e, "replay": True})
        while True:
            await ws.receive_text()  # keep-alive; the browser doesn't send commands here
    except WebSocketDisconnect:
        clients.discard(ws)


def _market_open() -> bool:
    now = datetime.now(ZoneInfo("America/New_York"))
    return now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) < (16, 0)


def _next_scheduled(now: datetime) -> datetime:
    """Next weekday run time from PROP_DESK_SCHEDULE_ET (US-Eastern)."""
    times = sorted(tuple(map(int, t.split(":"))) for t in settings.schedule_et)
    day = now
    for _ in range(8):
        for h, m in times:
            t = day.replace(hour=h, minute=m, second=0, microsecond=0)
            if t > now and t.weekday() < 5:
                return t
        day = (day + timedelta(days=1)).replace(hour=0, minute=0)
    raise ValueError("no schedule")


async def _autorun():
    """Run sessions on a schedule: fixed ET times, or every N minutes during market hours."""
    while True:
        if settings.schedule_et:
            now = datetime.now(ZoneInfo("America/New_York"))
            await asyncio.sleep(max(1, (_next_scheduled(now) - now).total_seconds()))
        else:
            await asyncio.sleep(settings.autorun_minutes * 60)
            if settings.market_hours_only and not _market_open():
                continue
        if arena.running:
            continue
        history.clear()
        await arena.run_session()


def main():
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
