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


class BearCase(BaseModel):
    """The devil's advocate's case against a pitch."""
    verdict: str  # one punchy sentence
    arguments: list[str]
    what_would_prove_me_wrong: str


class ContractOpinion(BaseModel):
    """A trader's vote on a prediction market."""
    stance: Literal["YES", "NO", "PASS"]
    probability: int  # your estimate (0-100) that the event resolves YES
    conviction: int  # 0-100
    headline: str
    reasoning: str
    key_signals: list[str]
    concerns: list[str]
    changed_mind: bool = False


class StockIdea(BaseModel):
    symbol: str
    direction: Literal["LONG", "SHORT"]
    thesis: str
    catalysts: list[str]
    risks: list[str]


class StockSlate(BaseModel):
    """What the research agent brings back from a stock hunt."""
    summary: str
    ideas: list[StockIdea]


class MarketIdea(BaseModel):
    ticker: str
    side: Literal["YES", "NO"]
    fair_probability: int  # researcher's estimate that it resolves YES
    thesis: str
    evidence: list[str]
    risks: list[str]


class MarketSlate(BaseModel):
    """What the research agent brings back from a prediction-market hunt."""
    summary: str
    ideas: list[MarketIdea]
