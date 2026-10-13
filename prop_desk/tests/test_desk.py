import asyncio
import os

import pytest

from prop_desk.factions import legion as legion_mod
from prop_desk.factions.ascendant import analog_forecast, pool
from prop_desk.factions.brood import drone_votes
from prop_desk.agents.schemas import (BearCase, ContractOpinion, DeskMemo, MarketIdea, MarketSlate, NewsDigest,
                                      Opinion, Pitch, StockIdea, StockSlate)
from prop_desk.broker import PaperBroker, review_contract, review_trade
from prop_desk.config import Settings
from prop_desk.consensus import tally
from prop_desk.desk import Arena
from prop_desk.indicators import compute_features
from prop_desk.kalshi import Contract, SyntheticKalshi, _num, edge_cents, fee_dollars
from prop_desk.market_data import SyntheticProvider
from prop_desk.scorecard import Scorecard


def op(stance, conv):
    return Opinion(stance=stance, conviction=conv, headline="h", reasoning="r", key_signals=[], concerns=[])


def contract(**kw):
    base = dict(ticker="T-1", title="Will it happen?", category="Economics", yes_bid=28, yes_ask=30,
                last_price=29, previous_price=29, volume=50000, open_interest=1000, close_time="2030-01-01T00:00")
    return Contract(**{**base, **kw})


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.ai_mode, s.data_source, s.kalshi_data, s.pace = "rules", "synthetic", "synthetic", 0
    s.data_dir = str(tmp_path)
    s.watchlist = ["AAPL", "MSFT", "XOM", "JPM"]
    s.candidates_per_session = 2
    s.alert_webhook = ""
    return s


# ------------------------------------------------------------- data + indicators
def test_features_are_complete_and_sane():
    p = SyntheticProvider()
    f = compute_features(p.bars("AAPL"), p.bars("SPY"))
    assert set(f) == {"price", "momentum", "ichimoku", "money_flow", "volatility"}
    assert 0 <= f["momentum"]["rsi14"] <= 100 and 0 <= f["money_flow"]["mfi14"] <= 100
    assert f["ichimoku"]["price_vs_cloud"] in {"above", "below", "inside"}
    assert f["volatility"]["atr14"] > 0


def test_synthetic_history_is_stable_between_calls():
    p = SyntheticProvider()
    a, b = p.bars("NVDA"), p.bars("NVDA")
    assert (a["close"].iloc[:-1] == b["close"].iloc[:-1]).all()
    assert 20 < a["close"].iloc[-1] < 1500


# ------------------------------------------------------------- consensus
def test_unanimous_long_is_approved():
    d = tally({a: op("LONG", 70) for a in "abcde"}, {}, 0.3, 3, False)
    assert d.approved and d.direction == "LONG" and d.score == 0.7


def test_one_loud_trader_cannot_force_a_trade():
    votes = {"a": op("LONG", 100), "b": op("LONG", 100), "c": op("PASS", 0), "d": op("PASS", 0), "e": op("PASS", 0)}
    d = tally(votes, {}, 0.3, 3, False)
    assert d.score == 0.4 and not d.approved and "need 3" in d.reason


def test_three_chart_traders_alone_cannot_approve():
    fams = {"fundamental": "fundamental", "momentum": "price", "cloud": "price", "flow": "price", "news": "news"}
    votes = {"momentum": op("LONG", 90), "cloud": op("LONG", 90), "flow": op("LONG", 90),
             "fundamental": op("PASS", 0), "news": op("PASS", 0)}
    assert not tally(votes, {}, 0.3, 3, families=fams, min_families=2).approved
    votes["fundamental"] = op("LONG", 60)
    assert tally(votes, {}, 0.3, 3, families=fams, min_families=2).approved


def test_shorts_blocked_when_disabled():
    d = tally({a: op("SHORT", 80) for a in "abcde"}, {}, 0.3, 3, False)
    assert d.direction == "SHORT" and not d.approved
    assert tally({a: op("SHORT", 80) for a in "abcde"}, {}, 0.3, 3, True).approved


def test_track_record_weights_change_the_score():
    votes = {"good": op("LONG", 80), "bad": op("SHORT", 80)}
    assert tally(votes, {"good": 1.5, "bad": 0.5}, 0.3, 1).approved
    assert not tally(votes, {}, 0.3, 1).approved


# ------------------------------------------------------------- risk
def test_risk_sizes_by_atr_and_caps_position(settings):
    b = PaperBroker(os.path.join(settings.data_dir, "t", "ledger.json"), 100_000)
    r = review_trade(b, "AAPL", "LONG", price=100.0, atr=2.0, s=settings)
    # 1% of 100k = $1,000 risk / $4 stop distance = 250 shares, but 10% cap = 100 shares
    assert r.approved and r.qty == 100 and r.stop == 96.0 and r.target == 106.0


def test_risk_off_halves_size(settings):
    b = PaperBroker(os.path.join(settings.data_dir, "t", "ledger.json"), 100_000)
    r = review_trade(b, "AAPL", "LONG", 100.0, 10.0, settings, risk_on=False)
    assert r.qty == 25  # $500 / $20 stop distance


def test_risk_vetoes(settings):
    b = PaperBroker(os.path.join(settings.data_dir, "t", "ledger.json"), 100_000)
    r = review_trade(b, "AAPL", "LONG", 100.0, 2.0, settings, sector="Tech")
    b.open("AAPL", "LONG", r, {}, "Tech")
    assert not review_trade(b, "AAPL", "LONG", 100.0, 2.0, settings).approved  # no doubling up
    b.open("MSFT", "LONG", review_trade(b, "MSFT", "LONG", 100.0, 2.0, settings), {}, "Tech")
    assert "concentrated" in review_trade(b, "NVDA", "LONG", 100.0, 2.0, settings, sector="Tech").notes[0]
    assert "Earnings" in review_trade(b, "XOM", "LONG", 100.0, 2.0, settings, days_to_earnings=1).notes[0]
    b.mark({"AAPL": 50.0})  # stop hit -> closed
    assert "AAPL" not in b.positions
    b.day_start_equity = b.equity / 0.96  # pretend we're down 4% today
    assert "daily loss" in review_trade(b, "JPM", "LONG", 100.0, 2.0, settings).notes[0]


def test_ledger_persists(settings):
    b = PaperBroker(os.path.join(settings.data_dir, "t", "ledger.json"), 100_000)
    b.open("AAPL", "LONG", review_trade(b, "AAPL", "LONG", 100.0, 2.0, settings), {})
    b.snapshot(500.0)
    again = PaperBroker(os.path.join(settings.data_dir, "t", "ledger.json"), 100_000)
    assert "AAPL" in again.positions and again.cash == b.cash and len(again.history) == 1


# ------------------------------------------------------------- prediction markets
def test_kalshi_fee_and_edge():
    assert fee_dollars(50, 100) == 1.75  # 0.07 * 100 * 0.5 * 0.5
    assert fee_dollars(50, 1) == 0.02  # rounds up to the cent
    assert edge_cents(40, "YES", 30) == pytest.approx(10 - 1.47, abs=0.01)
    assert edge_cents(40, "NO", 70) < 0


def test_kalshi_price_formats():
    assert _num({"yes_bid": 28}, "yes_bid") == 28
    assert _num({"yes_bid_dollars": "0.2800"}, "yes_bid") == 28


def test_contract_sizing_and_settlement(settings):
    b = PaperBroker(os.path.join(settings.data_dir, "t", "ledger.json"), 100_000)
    c = contract()
    assert not review_contract(b, c, "YES", 25, settings).approved  # no edge
    r = review_contract(b, c, "YES", 60, settings)
    assert r.approved and r.qty * 0.30 <= 1000  # capped at 1% of equity
    b.open_contract(c, "YES", r, {})
    cash_after_buy = b.cash
    t = b.mark_contract(contract(result="yes", status="settled"))
    assert t["reason"] == "won" and b.cash == pytest.approx(cash_after_buy + r.qty)
    assert not b.contracts


def test_synthetic_markets_settle_later():
    k = SyntheticKalshi()
    m = k.markets()
    assert m and all(1 <= c.yes_bid < c.yes_ask <= 99 for c in m)
    again = k.market(m[0].ticker)
    assert again.ticker == m[0].ticker and again.title == m[0].title


# ------------------------------------------------------------- scorecard
def test_scorecard_grades_calls_and_weights(tmp_path):
    sc = Scorecard(str(tmp_path / "s.json"), horizon_days=0)
    for _ in range(6):
        sc.record("stock", "AAPL", "AAPL", 100.0, {"good": "LONG", "bad": "SHORT", "meh": "PASS"}, traded=True)
    for call in sc.pending("stock"):
        sc.resolve_stock(call, 110.0)
    sc.save()
    st = sc.stats()
    assert st["good"]["accuracy"] == 1.0 and st["bad"]["accuracy"] == 0.0 and st["meh"]["calls"] == 0
    assert st["good"]["streak"] == 6 and st["good"]["edge"] == pytest.approx(60.0)
    assert sc.weight("good", 1.0) > 1.0 > sc.weight("bad", 1.0)
    assert Scorecard(str(tmp_path / "s.json")).stats()["good"]["calls"] == 6  # persisted


# ------------------------------------------------------------- faction models
def test_log_odds_pooling():
    assert pool({"a": 0.5, "b": 0.5}, {}) == pytest.approx(0.5)
    assert pool({"a": 0.7, "b": 0.7}, {}) > 0.7  # agreeing independent evidence compounds
    assert pool({"a": 0.9, "b": 0.1}, {"a": 3, "b": 1}) > 0.5  # weights matter


def test_analog_forecast_is_a_probability():
    p, er = analog_forecast(SyntheticProvider().bars("AAPL").close.to_numpy())
    assert 0.15 <= p <= 0.85 and -50 < er < 50


def test_every_drone_votes():
    prov = SyntheticProvider()
    snap = prov.snapshot("MSFT")
    votes = drone_votes(compute_features(snap.bars, prov.bars("SPY")), snap)
    from prop_desk.agents.personas import DRONES
    assert set(votes) == {d for d, *_ in DRONES} and set(votes.values()) <= {-1, 0, 1}


# ------------------------------------------------------------- full sessions
def run(arena, symbols=None):
    events = []

    async def emit(e):
        events.append(e)

    arena._emit = emit
    asyncio.run(arena.run_session(symbols))
    return events


def test_arena_session_runs_all_three_factions(settings):
    a = Arena(settings, None)
    ev = run(a)
    assert not [e for e in ev if e["type"] == "error"]
    assert [e["faction"] for e in ev if e["type"] == "turn"] == ["legion", "ascendant", "brood"]
    assert any(e["type"] == "model" for e in ev) and any(e["type"] == "swarm_vote" for e in ev)
    assert any(e["type"] == "message" and e["agent"] == "bear" for e in ev)  # devil's advocate spoke
    standings = [e for e in ev if e["type"] == "standings"][-1]["factions"]
    assert {r["id"] for r in standings} == {"legion", "ascendant", "brood"}
    for f in a.factions:  # separate books, every idea recorded as a faction call
        assert os.path.exists(os.path.join(settings.data_dir, f.id, "ledger.json"))
        assert f.scorecard.calls and all(f.id in c["votes"] for c in f.scorecard.calls)
        assert f.broker.history
    assert os.listdir(settings.sessions_dir)  # transcript saved


def test_calls_get_graded_next_session(settings):
    settings.call_horizon_days = 0
    a = Arena(settings, None)
    run(a)
    run(a)
    standings = a.standings()
    assert any(r["units"] for r in standings)
    graded = sum(1 for f in a.factions for c in f.scorecard.calls if c["outcome"])
    assert graded > 0


def test_legion_goes_for_gas_when_no_stock_trade(settings):
    settings.factions = ["legion"]
    settings.min_agreeing_traders = 6  # impossible -> no stock trade
    ev = run(Arena(settings, None))
    assert any(e["type"] == "phase" and e["phase"] == "markets" for e in ev)
    assert not any(e["type"] == "error" for e in ev)


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
            BearCase: BearCase(verdict="AI bear", arguments=["a"], what_would_prove_me_wrong="w"),
            Opinion: op("LONG", 80),
            ContractOpinion: ContractOpinion(stance="YES", probability=70, conviction=80, headline="h",
                                             reasoning="r", key_signals=[], concerns=[]),
            DeskMemo: DeskMemo(summary="AI memo", what_would_change_our_mind="x"),
        }[schema]


class FakeResearch:
    """Stands in for the Agent SDK research agent."""
    fail = False

    def __init__(self, persona, *a, **k):
        self.persona, self.cost_usd = persona, 0.25

    async def hunt(self, task, ctx, schema):
        if FakeResearch.fail:
            raise RuntimeError("agent crashed")
        if schema is StockSlate:
            sym = sorted(ctx.screen)[0]
            return StockSlate(summary="AI hunt", ideas=[StockIdea(symbol=sym, direction="LONG", thesis="AI thesis",
                                                                  catalysts=["c"], risks=["r"])])
        t = next(iter(ctx.contracts))
        return MarketSlate(summary="AI markets", ideas=[MarketIdea(ticker=t, side="YES", fair_probability=95,
                                                                   thesis="AI edge", evidence=["e"], risks=["r"])])


@pytest.fixture
def fake_research(monkeypatch):
    FakeResearch.fail = False
    monkeypatch.setattr(legion_mod, "ResearchAgent", FakeResearch)
    return FakeResearch


def legion_with(settings, brain):
    settings.factions = ["legion"]
    a = Arena(settings, None)
    a.faction("legion").brain = brain
    return a, a.faction("legion")


def test_claude_path_uses_model_outputs(settings, fake_research):
    a, legion = legion_with(settings, FakeBrain())
    ev = run(a)
    texts = [e["text"] for e in ev if e["type"] == "message"]
    assert "AI hunt" in " ".join(texts) and any("AI memo" in t for t in texts) and any("AI bear" in t for t in texts)
    assert legion.brain.calls.count("Opinion") == 10  # 1 idea x 5 traders x 2 rounds
    assert [e for e in ev if e["type"] == "order"]  # unanimous 80 -> trade placed
    assert a.cost_usd == 0.25  # research agent spend is counted


def test_claude_markets_round_trades_and_settles(settings, fake_research, monkeypatch):
    settings.min_agreeing_traders = 6
    a, legion = legion_with(settings, FakeBrain())
    ev = run(a)
    orders = [e for e in ev if e["type"] == "order"]
    assert orders and orders[0]["kind"] == "contract"
    ticker = orders[0]["symbol"]
    settled = SyntheticKalshi().market(ticker)
    settled.result, settled.status = "yes", "settled"
    monkeypatch.setattr(a.kalshi, "market", lambda t: settled)
    run(a)
    assert any(t.get("action") == "SETTLED YES" for t in legion.broker.trades)
    assert [c for c in legion.scorecard.calls if c["kind"] == "contract" and c["outcome"] == "yes"]


def test_failures_fall_back_to_rules(settings, fake_research):
    fake_research.fail = True
    a, _ = legion_with(settings, FakeBrain(fail=True))
    ev = run(a)
    assert any(e["type"] == "notice" for e in ev)
    assert not any(e["type"] == "error" for e in ev)
    assert len([e for e in ev if e["type"] == "tally" and not e["symbol"].startswith("SIM")]) == 4


# ------------------------------------------------------------- Claude backends
def test_backend_detection(monkeypatch):
    from prop_desk.agents import brain
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(brain.os.path, "isdir", lambda p: False)
    monkeypatch.setattr(brain, "plan_logged_in", lambda: False)
    assert brain.detect_backend("auto") is None
    assert brain.detect_backend("rules") is None
    monkeypatch.setattr(brain, "plan_logged_in", lambda: True)
    assert brain.detect_backend("auto") == "plan"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert brain.detect_backend("auto") == "api"  # an API key wins
    assert brain.detect_backend("plan") == "plan" and brain.detect_backend("claude") == "api"


def test_plan_brain_returns_validated_output(monkeypatch):
    import claude_agent_sdk

    from prop_desk.agents.brain import PlanBrain

    class FakeResult:
        def __init__(self, out):
            self.structured_output, self.total_cost_usd, self.is_error, self.subtype = out, 0.01, False, "success"

    seen = {}

    async def fake_query(prompt, options):
        seen["options"] = options
        yield FakeResult({"verdict": "v", "arguments": ["a"], "what_would_prove_me_wrong": "w"})

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(claude_agent_sdk, "ResultMessage", FakeResult)
    b = PlanBrain("claude-opus-5-5", "low")
    out = asyncio.run(b.think("system", "prompt", BearCase))
    assert isinstance(out, BearCase) and out.verdict == "v" and b.cost_usd == 0.01
    o = seen["options"]
    assert o.tools == [] and o.setting_sources == [] and o.output_format["type"] == "json_schema"
