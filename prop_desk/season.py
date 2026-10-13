"""Seasons: fixed-length contests between the factions.

Each faction starts a season with whatever equity it has. When the season ends
(after `days` calendar days), results are recorded and a new season begins from
each faction's ending equity - profits are reinvested and losses carried. The
winner of a season is the faction with the highest return over that season.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Season:
    def __init__(self, path: str, days: int):
        self.path = path
        self.days = days
        self.number = 0
        self.started: str | None = None
        self.start_equity: dict[str, float] = {}
        self.history: list[dict] = []
        if os.path.exists(path):
            with open(path) as f:
                d = json.load(f)
            self.number, self.started = d["number"], d["started"]
            self.start_equity, self.history = d["start_equity"], d.get("history", [])

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump({"number": self.number, "started": self.started, "start_equity": self.start_equity,
                       "history": self.history}, f, indent=1)

    def reset(self) -> None:
        self.number, self.started, self.start_equity, self.history = 0, None, {}, []
        self.save()

    # ------------------------------------------------------------ timeline
    @property
    def ends(self) -> datetime | None:
        return datetime.fromisoformat(self.started) + timedelta(days=self.days) if self.started else None

    def day(self, now: datetime | None = None) -> int:
        if not self.started:
            return 0
        return min(self.days, ((now or _now()) - datetime.fromisoformat(self.started)).days + 1)

    def _start(self, equities: dict[str, float], now: datetime) -> None:
        self.number += 1
        self.started = now.isoformat(timespec="seconds")
        self.start_equity = {k: round(v, 2) for k, v in equities.items()}

    def returns(self, equities: dict[str, float]) -> dict[str, float]:
        return {k: round((v / self.start_equity[k] - 1) * 100, 2) if self.start_equity.get(k) else 0.0
                for k, v in equities.items()}

    def tick(self, equities: dict[str, float], now: datetime | None = None) -> dict | None:
        """Start the first season, or close an expired one and start the next. Returns the closed season."""
        now = now or _now()
        closed = None
        if not self.started:
            self._start(equities, now)
        elif now >= self.ends:
            rets = self.returns(equities)
            results = sorted(({"faction": k, "start": self.start_equity.get(k, v), "end": round(v, 2),
                               "return_pct": rets[k]} for k, v in equities.items()),
                             key=lambda r: r["return_pct"], reverse=True)
            closed = {"number": self.number, "started": self.started, "ended": now.isoformat(timespec="seconds"),
                      "results": results, "winner": results[0]["faction"] if results else None}
            self.history.append(closed)
            self._start(equities, now)  # reinvest: next season starts from ending equity
        for k, v in equities.items():  # a faction added mid-season starts from its current equity
            self.start_equity.setdefault(k, round(v, 2))
        self.save()
        return closed

    def public(self, equities: dict[str, float]) -> dict:
        return {"number": self.number, "days": self.days, "day": self.day(), "started": self.started,
                "ends": self.ends.isoformat(timespec="seconds") if self.ends else None,
                "start_equity": self.start_equity, "returns": self.returns(equities) if self.started else {},
                "history": self.history[-12:]}
