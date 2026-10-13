import asyncio

import pytest

from prop_desk.agents.schemas import DeskMemo, NewsDigest, Opinion, Pitch
from prop_desk.broker import PaperBroker, review_trade
from prop_desk.config import Settings
from prop_desk.consensus import tally
from prop_desk.desk import Desk
from prop_desk.indicators import compute_features
from prop_desk.market_data import SyntheticProvider


def op(stance, conv):
    return Opinion(stance=stance, conviction=conv, headline="h", reasoning="r", key_signals=[], concerns=[])


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.ai_mode, s.data_source, s.pace = "rules", "synthetic", 0
    s.ledger_path = str(tmp_path / "ledger.json")
    s.watchlist = ["AAPL", "MSFT", "XOM", "JPM"]
    s.candidates_per_session = 2
    return s


# ------------------------------------------------------------- indicators
def test_features_are_complete_and_sane():
    p = SyntheticProvider()
    f = compute_features(p.bars("AAPL"), p.bars("SPY"))
    assert set(f) == {"price", "momentum", "ichimoku", "money_flow", "volatility"}
    assert 0 <= f["momentum"]["rsi14"] <= 100
    assert 0 <= f["money_flow"]["mfi14"] <= 100
    assert f["ichimoku"]["price_vs_cloud"] in {"above", "below", "inside"}
    assert f["volatility"]["atr14"] > 0
    assert f["money_flow"]["relative_strength_vs_spy_3m_pct"] is not None


# ------------------------------------------------------------- consensus
def test_unanimous_long_is_approved():
    d = tally({a: op("LONG", 70) for a in "abcde"}, {}, 0.3, 3, False)
    assert d.approved and d.direction == "LONG" and d.score == 0.7


def test_one_loud_trader_cannot_force_a_trade():
    votes = {"a": op("LONG", 100), "b": op("LONG", 100), "c": op("PASS", 0), "d": op("PASS", 0), "e": op("PASS", 0)}
    d = tally(votes, {}, 0.3, 3, False)
    assert d.score == 0.4 and not d.approved and "need 3" in d.reason


def test_split_floor_is_no_trade():
    votes = {"a": op("LONG", 80), "b": op("LONG", 60), "c": op("SHORT", 70), "d": op("SHORT", 60), "e": op("PASS", 0)}
    assert not tally(votes, {}, 0.3, 3, False).approved


def test_shorts_blocked_when_disabled():
    d = tally({a: op("SHORT", 80) for a in "abcde"}, {}, 0.3, 3, False)
    assert d.direction == "SHORT" and not d.approved
    assert tally({a: op("SHORT", 80) for a in "abcde"}, {}, 0.3, 3, True).approved


# ------------------------------------------------------------- risk
def test_risk_sizes_by_atr_and_caps_position(settings):
    b = PaperBroker(settings.ledger_path, 100_000)
    r = review_trade(b, "AAPL", "LONG", price=100.0, atr=2.0, s=settings)
    # 1% of 100k = $1,000 risk / $4 stop distance = 250 shares, but 10% cap = 100 shares
    assert r.approved and r.qty == 100 and r.stop == 96.0 and r.target == 106.0


def test_risk_vetoes_duplicates_and_daily_loss(settings):
    b = PaperBroker(settings.ledger_path, 100_000)
    r = review_trade(b, "AAPL", "LONG", 100.0, 2.0, settings)
    b.open("AAPL", "LONG", r, {})
    assert not review_trade(b, "AAPL", "LONG", 100.0, 2.0, settings).approved
    b.mark({"AAPL": 50.0})  # stop hit -> position closed at a loss
    assert "AAPL" not in b.positions
    b.day_start_equity = b.equity / 0.96  # pretend we're down 4% today
    assert "daily loss" in review_trade(b, "MSFT", "LONG", 100.0, 2.0, settings).notes[0]


def test_ledger_persists(settings):
    b = PaperBroker(settings.ledger_path, 100_000)
    b.open("AAPL", "LONG", review_trade(b, "AAPL", "LONG", 100.0, 2.0, settings), {})
    again = PaperBroker(settings.ledger_path, 100_000)
    assert "AAPL" in again.positions and again.cash == b.cash


# ------------------------------------------------------------- full sessions
def run(desk, symbols=None):
    events = []

    async def emit(e):
        events.append(e)

    desk.emit = emit
    asyncio.run(desk.run_session(symbols))
    return events


def test_offline_session_runs_end_to_end(settings):
    ev = run(Desk(settings, None))
    kinds = [e["type"] for e in ev]
    assert "error" not in kinds and kinds[-2] == "session_end"
    tallies = [e for e in ev if e["type"] == "tally"]
    assert len(tallies) == 4  # 2 candidates x 2 rounds
    assert len([e for e in ev if e["type"] == "vote"]) == 20  # 5 traders x 2 rounds x 2 candidates


class FakeBrain:
    """Stands in for Claude: returns canned structured outputs."""
    cost_usd = 0.0

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def think(self, system, prompt, schema):
        self.calls.append(schema.__name__)
        if self.fail:
            raise RuntimeError("API down")
        return {
            Pitch: Pitch(direction="LONG", thesis="AI thesis", catalysts=["c"], risks=["r"]),
            NewsDigest: NewsDigest(sentiment="bullish", summary="AI news", market_moving=[]),
            Opinion: op("LONG", 80),
            DeskMemo: DeskMemo(summary="AI memo", what_would_change_our_mind="x"),
        }[schema]


def test_claude_path_uses_model_outputs(settings):
    d = Desk(settings, None)
    d.brain = FakeBrain()
    ev = run(d, ["AAPL"])
    texts = [e["text"] for e in ev if e["type"] == "message"]
    assert any("AI thesis" in t for t in texts) and any("AI memo" in t for t in texts)
    assert d.brain.calls.count("Opinion") == 10
    assert [e for e in ev if e["type"] == "order"]  # unanimous 80 -> trade placed


def test_claude_outage_falls_back_to_rules(settings):
    d = Desk(settings, None)
    d.brain = FakeBrain(fail=True)
    ev = run(d, ["AAPL"])
    assert any(e["type"] == "notice" for e in ev)
    assert not any(e["type"] == "error" for e in ev)
    assert len([e for e in ev if e["type"] == "tally"]) == 2
