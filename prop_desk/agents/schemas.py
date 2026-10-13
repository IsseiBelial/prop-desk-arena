"""Structured outputs the agents must return (validated JSON, never free text)."""
from typing import Literal

from pydantic import BaseModel, Field

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
    conviction: int = Field(description="0-100. 80+ = would put your own book on it; under 40 = weak; 0 for PASS.")
    headline: str = Field(description="One punchy sentence, the way you'd say it across the desk.")
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
    verdict: str = Field(description="One punchy sentence.")
    arguments: list[str]
    what_would_prove_me_wrong: str


class ContractOpinion(BaseModel):
    """A trader's vote on a prediction market."""
    stance: Literal["YES", "NO", "PASS"]
    probability: int = Field(description="Your estimate, 0-100, that the event resolves YES.")
    conviction: int = Field(description="0-100. 80+ = very confident; under 40 = weak; 0 for PASS.")
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
    fair_probability: int = Field(description="Your estimate, 0-100, that the market resolves YES.")
    thesis: str
    evidence: list[str]
    risks: list[str]


class MarketSlate(BaseModel):
    """What the research agent brings back from a prediction-market hunt."""
    summary: str
    ideas: list[MarketIdea]
