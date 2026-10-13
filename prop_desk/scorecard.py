"""'Who called it?' - every trader's track record.

Every idea the council votes on becomes a *call*, traded or not. Calls are graded
later:
  * stocks: after `horizon_days`, up >1% = "up", down >1% = "down", else "flat"
  * prediction markets: when the market settles YES or NO
A directional vote that matched the outcome is a hit. PASS votes aren't graded.

Track records feed back into vote weights, so traders who are right more often
get more say (shrunk toward normal weight until they have enough calls).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

_HIT = {("LONG", "up"), ("SHORT", "down"), ("YES", "yes"), ("NO", "no")}


class Scorecard:
    def __init__(self, path: str, horizon_days: int = 5):
        self.path = path
        self.horizon = horizon_days
        self.calls: list[dict] = []
        if os.path.exists(path):
            with open(path) as f:
                self.calls = json.load(f)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.calls[-3000:], f)

    def record(self, kind: str, key: str, title: str, price: float, votes: dict[str, str],
               traded: bool, due: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        self.calls.append({
            "kind": kind, "key": key, "title": title, "price": price, "votes": votes, "traded": traded,
            "made": now.isoformat(timespec="minutes"),
            "due": due or (now + timedelta(days=self.horizon)).isoformat(timespec="minutes"),
            "outcome": None, "move_pct": None,
        })
        self.save()

    def pending(self, kind: str, due_only: bool = True) -> list[dict]:
        """Ungraded calls. Markets can settle early, so callers may check them before `due`."""
        now = datetime.now(timezone.utc).isoformat()
        return [c for c in self.calls if c["kind"] == kind and c["outcome"] is None
                and (not due_only or c["due"] <= now)]

    def resolve_stock(self, call: dict, price_now: float) -> None:
        move = (price_now / call["price"] - 1) * 100
        call["move_pct"] = round(move, 2)
        call["outcome"] = "up" if move > 1 else "down" if move < -1 else "flat"

    def resolve_contract(self, call: dict, result: str) -> None:
        call["outcome"] = result
        # Move from the YES side's perspective, in cents of payout.
        call["move_pct"] = round((100 if result == "yes" else 0) - call["price"], 2)

    def stats(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for c in sorted((c for c in self.calls if c["outcome"]), key=lambda c: c["made"]):
            for agent, stance in c["votes"].items():
                s = out.setdefault(agent, {"calls": 0, "hits": 0, "passes": 0, "edge": 0.0, "recent": []})
                if stance == "PASS":
                    s["passes"] += 1
                    continue
                if c["outcome"] == "flat":
                    continue
                hit = (stance, c["outcome"]) in _HIT
                s["calls"] += 1
                s["hits"] += hit
                # Return (in %) had you followed only this trader. For contracts the
                # move is in cents per $1 contract, which is also a percentage.
                sign = 1 if stance in ("LONG", "YES") else -1
                s["edge"] += sign * (c["move_pct"] or 0)
                s["recent"] = (s["recent"] + ["W" if hit else "L"])[-10:]
        for s in out.values():
            s["accuracy"] = round(s["hits"] / s["calls"], 3) if s["calls"] else None
            s["edge"] = round(s["edge"], 2)
            streak = 0
            for r in reversed(s["recent"]):
                if streak == 0 or (r == "W") == (streak > 0):
                    streak += 1 if r == "W" else -1
                else:
                    break
            s["streak"] = streak
        return out

    def weight(self, agent: str, base: float) -> float:
        s = self.stats().get(agent)
        if not s or not s["calls"]:
            return base
        n = s["calls"]
        w = base * (1 + (s["accuracy"] - 0.5) * 2 * n / (n + 10))
        return round(max(0.5 * base, min(1.5 * base, w)), 3)
