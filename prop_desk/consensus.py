"""How the floor turns five opinions into one decision.

score = sum(weight * conviction * direction) / sum(weight * 100)   -> range -1 .. +1
  direction is +1 for LONG, -1 for SHORT, 0 for PASS

A trade is approved when ALL of these hold:
  1. |score| >= approve_threshold          (the room leans clearly one way)
  2. at least `min_agreeing_traders` voted that way  (not one loud trader)
  3. the direction is allowed (shorts are off by default)
The risk manager can still veto afterwards.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .agents.schemas import Opinion

_SIGN = {"LONG": 1, "SHORT": -1, "PASS": 0}


@dataclass
class Decision:
    score: float
    direction: str
    agreeing: list[str]
    dissenting: list[str]
    passing: list[str]
    approved: bool
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def tally(opinions: dict[str, Opinion], weights: dict[str, float], threshold: float,
          quorum: int, allow_shorts: bool) -> Decision:
    total_w = sum(weights.get(a, 1.0) for a in opinions) or 1.0
    raw = sum(weights.get(a, 1.0) * max(0, min(100, o.conviction)) * _SIGN[o.stance] for a, o in opinions.items())
    score = round(raw / (total_w * 100), 3)
    direction = "LONG" if score >= 0 else "SHORT"
    agreeing = [a for a, o in opinions.items() if o.stance == direction]
    dissenting = [a for a, o in opinions.items() if o.stance not in (direction, "PASS")]
    passing = [a for a, o in opinions.items() if o.stance == "PASS"]

    if abs(score) < threshold:
        approved, reason = False, f"No consensus: score {score:+.2f} is inside the ±{threshold:.2f} band"
    elif len(agreeing) < quorum:
        approved, reason = False, f"Only {len(agreeing)} of {len(opinions)} traders back {direction}; need {quorum}"
    elif direction == "SHORT" and not allow_shorts:
        approved, reason = False, "Floor wants to short, but shorting is disabled"
    else:
        approved, reason = True, f"{len(agreeing)} of {len(opinions)} traders back {direction} (score {score:+.2f})"
    return Decision(score, direction, agreeing, dissenting, passing, approved, reason)
