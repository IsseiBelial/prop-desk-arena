"""How the Council turns five opinions into one decision.

score = sum(weight * conviction * direction) / sum(weight * 100)   -> range -1 .. +1
  direction is +1 for LONG/YES, -1 for SHORT/NO, 0 for PASS
  weight comes from each trader's track record ("Who called it?")

A trade is approved when ALL of these hold:
  1. |score| >= threshold                  (the room leans clearly one way)
  2. at least `quorum` traders voted that way      (not one loud trader)
  3. those traders use at least `min_families` different kinds of evidence -
     the momentum, cloud and money-flow traders all read the same price chart,
     so on their own they only count as one independent opinion
  4. the direction is allowed (shorting stocks is off by default)
The risk manager can still veto afterwards.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

_SIGN = {"LONG": 1, "YES": 1, "SHORT": -1, "NO": -1, "PASS": 0}


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


def tally(opinions: dict, weights: dict[str, float], threshold: float, quorum: int,
          allow_shorts: bool = False, families: dict[str, str] | None = None,
          min_families: int = 1, kind: str = "stock") -> Decision:
    total_w = sum(weights.get(a, 1.0) for a in opinions) or 1.0
    raw = sum(weights.get(a, 1.0) * max(0, min(100, o.conviction)) * _SIGN[o.stance] for a, o in opinions.items())
    score = round(raw / (total_w * 100), 3)
    up, down = ("LONG", "SHORT") if kind == "stock" else ("YES", "NO")
    direction = up if score >= 0 else down
    agreeing = [a for a, o in opinions.items() if o.stance == direction]
    dissenting = [a for a, o in opinions.items() if o.stance not in (direction, "PASS")]
    passing = [a for a, o in opinions.items() if o.stance == "PASS"]
    fams = {(families or {}).get(a, a) for a in agreeing}

    if abs(score) < threshold:
        approved, reason = False, f"No consensus: score {score:+.2f} is inside the ±{threshold:.2f} band"
    elif len(agreeing) < quorum:
        approved, reason = False, f"Only {len(agreeing)} of {len(opinions)} traders back {direction}; need {quorum}"
    elif len(fams) < min_families:
        approved, reason = False, (f"{len(agreeing)} traders back {direction} but they all read the same "
                                   f"evidence ({', '.join(sorted(fams))}); need {min_families} independent kinds")
    elif direction == "SHORT" and not allow_shorts:
        approved, reason = False, "Council wants to short, but shorting is disabled"
    else:
        approved, reason = True, f"{len(agreeing)} of {len(opinions)} traders back {direction} (score {score:+.2f})"
    return Decision(score, direction, agreeing, dissenting, passing, approved, reason)
