"""Structured outputs the agents must return (validated JSON, never free text)."""
from typing import Literal

from pydantic import BaseModel

Stance = Literal["LONG", "SHORT", "PASS"]


class Pitch(BaseModel):
    """A researcher's trade idea, brought to the floor."""
    direction: Literal["LONG", "SHORT"]
    thesis: str
    catalysts: list[str]
    risks: list[str]


class NewsDigest(BaseModel):
    sentiment: Literal["bullish", "bearish", "mixed", "quiet"]
    summary: str
    market_moving: list[str]


class Opinion(BaseModel):
    """A trader's vote on a pitch."""
    stance: Stance
    conviction: int  # 0-100
    headline: str  # one sentence the floor hears first
    reasoning: str
    key_signals: list[str]
    concerns: list[str]
    changed_mind: bool = False


class DeskMemo(BaseModel):
    """The Head of Desk's closing note on a decision."""
    summary: str
    what_would_change_our_mind: str
