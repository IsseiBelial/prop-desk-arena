"""Web server: serves the trading-floor GUI and streams desk events over a WebSocket.

Run:  python -m prop_desk.server      then open http://localhost:8000
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agents import personas as P
from .config import settings
from .desk import Desk

logging.basicConfig(level=logging.INFO)
STATIC = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="Prop Desk")
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


desk = Desk(settings, broadcast)


class RunRequest(BaseModel):
    symbols: list[str] | None = None


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/state")
async def state():
    return {
        "mode": desk.mode,
        "running": desk.running,
        "agents": [p.public() for p in P.ALL],
        "portfolio": desk.broker.summary(),
        "settings": {
            "watchlist": settings.watchlist,
            "threshold": settings.approve_threshold,
            "quorum": settings.min_agreeing_traders,
            "risk_per_trade_pct": settings.risk_per_trade_pct,
            "max_position_pct": settings.max_position_pct,
            "max_open_positions": settings.max_open_positions,
            "max_daily_loss_pct": settings.max_daily_loss_pct,
            "allow_shorts": settings.allow_shorts,
        },
    }


@app.post("/api/session")
async def run_session(req: RunRequest):
    if desk.running:
        return {"started": False, "reason": "A session is already running"}
    history.clear()
    asyncio.create_task(desk.run_session(req.symbols))
    return {"started": True}


@app.post("/api/reset")
async def reset():
    if desk.running:
        return {"ok": False, "reason": "Wait for the session to finish"}
    desk.broker.reset()
    await broadcast({"type": "portfolio", **desk.broker.summary()})
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


def main():
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
