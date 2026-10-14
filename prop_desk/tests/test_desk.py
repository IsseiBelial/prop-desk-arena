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


def test_arena_session_runs_all_four_factions(settings):
    a = Arena(settings, None)
    ev = run(a)
    assert not [e for e in ev if e["type"] == "error"]
    assert [e["faction"] for e in ev if e["type"] == "turn"] == ["legion", "ascendant", "brood", "syndicate"]
    assert any(e["type"] == "model" for e in ev) and any(e["type"] == "swarm_vote" for e in ev)
    assert any(e["type"] == "message" and e["agent"] == "bear" for e in ev)  # devil's advocate spoke
    assert any(e["type"] == "posture" for e in ev)  # the Syndicate's Lookout read the market
    standings = [e for e in ev if e["type"] == "standings"][-1]["factions"]
    assert {r["id"] for r in standings} == {"legion", "ascendant", "brood", "syndicate"}
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


# ------------------------------------------------------------- small accounts + seasons
def test_fractional_shares_for_small_accounts(settings):
    b = PaperBroker(os.path.join(settings.data_dir, "t", "ledger.json"), 100)
    r = review_trade(b, "AAPL", "LONG", 230.0, 6.0, settings)
    assert r.approved and 0 < r.qty < 1 and r.qty * 230 <= 10.0  # capped at 10% of $100
    b.open("AAPL", "LONG", r, {})
    b.mark({"AAPL": 260.0})  # target hit -> closed at a profit
    assert "AAPL" not in b.positions and b.cash > 100
    settings.fractional_shares = False
    assert not review_trade(b, "MSFT", "LONG", 230.0, 6.0, settings).approved  # a whole share won't fit


def test_season_rollover_reinvests(tmp_path):
    from datetime import datetime, timedelta, timezone

    from prop_desk.season import Season
    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
    s = Season(str(tmp_path / "season.json"), days=30)
    assert s.tick({"legion": 100.0, "brood": 100.0}, now=t0) is None and s.number == 1
    assert s.day(t0 + timedelta(days=9)) == 10
    assert s.tick({"legion": 112.0, "brood": 95.0}, now=t0 + timedelta(days=29)) is None  # not over yet
    closed = s.tick({"legion": 120.0, "brood": 90.0}, now=t0 + timedelta(days=30))
    assert closed["winner"] == "legion" and closed["results"][0]["return_pct"] == 20.0
    assert s.number == 2 and s.start_equity == {"legion": 120.0, "brood": 90.0}  # profits/losses carried
    assert Season(str(tmp_path / "season.json"), 30).history[0]["winner"] == "legion"  # persisted


def test_arena_standings_rank_by_risk_adjusted_score(settings):
    a = Arena(settings, None)
    run(a)
    rows = a.standings()
    assert all({"season_return_pct", "max_drawdown_pct", "score"} <= set(r) for r in rows)
    assert [r["score"] for r in rows] == sorted((r["score"] for r in rows), reverse=True)
    assert a.season_info()["number"] == 1 and a.season_info()["day"] == 1


# ------------------------------------------------------------- more instruments
from datetime import date, timedelta  # noqa: E402

from prop_desk import universe  # noqa: E402
from prop_desk.broker import review_option  # noqa: E402
from prop_desk.options import SyntheticOptions, bs_price, pick  # noqa: E402
from prop_desk.season import Season, risk_adjusted  # noqa: E402


def test_universe_lanes_and_names(settings):
    assert universe.asset_class("BTC-USD") == "crypto" and universe.asset_class("EURUSD=X") == "forex"
    assert universe.display("EURUSD=X") == "EUR/USD" and universe.display("BTC-USD") == "BTC"
    lanes = universe.build(settings, "synthetic")  # offline: lively names come from the fallback list
    assert {lanes[s] for s in lanes} == {"stock", "lively", "crypto", "forex"}
    assert sum(v == "lively" for v in lanes.values()) == settings.lively_count
    settings.assets = ["stocks", "crypto"]
    assert set(universe.build(settings, "synthetic").values()) == {"stock", "crypto"}


def test_crypto_and_forex_data_behave():
    p = SyntheticProvider()
    spy = p.bars("SPY")
    fx, btc = p.snapshot("EURUSD=X"), p.snapshot("BTC-USD")
    assert fx.asset == "forex" and 0.5 < fx.price < 2 and (fx.bars["volume"] == 0).all()
    f = compute_features(fx.bars, spy)  # no volume: volume readings are None, nothing crashes
    assert f["money_flow"]["obv_trend_20d"] is None and f["money_flow"]["volume_20d_vs_50d"] is None
    assert f["volatility"]["atr_pct"] < 2  # currencies move far less than stocks
    assert btc.asset == "crypto" and btc.price > 1000 and "pe" not in btc.fundamentals
    assert not any("earnings" in h.title for h in btc.news)


def test_sizing_for_coins_and_currencies(settings):
    b = PaperBroker(os.path.join(settings.data_dir, "t", "ledger.json"), 100)
    btc = review_trade(b, "BTC-USD", "LONG", 65000.0, 3900.0, settings, asset="crypto")
    assert btc.approved and btc.qty < 0.001 and btc.qty * 65000 <= 10.0  # fractions of a coin
    doge = review_trade(b, "DOGE-USD", "LONG", 0.1523, 0.009, settings, asset="crypto")
    assert doge.approved and doge.stop == 0.1343  # not rounded to $0.13
    eur = review_trade(b, "EURUSD=X", "LONG", 1.0794, 0.0087, settings, asset="forex")
    assert eur.approved and eur.stop == 1.062 and round(eur.qty * 1.0794) == 50  # 50% cap for currencies


def test_option_pricing_and_choice():
    call, put = bs_price(100, 100, 0.25, 0.3, "call"), bs_price(100, 100, 0.25, 0.3, "put")
    assert abs((call - put) - (100 - 100 * 2.718281828 ** (-0.04 * 0.25))) < 0.01  # put-call parity
    today = date(2026, 10, 14)
    quotes = SyntheticOptions().chain("XYZ", 50.0, 0.5, "call", today=today)
    long_leg, short_leg = pick(quotes, 50.0, "call", budget=10_000, min_days=14, max_days=45, today=today)
    assert long_leg and short_leg is None and 14 <= long_leg.days(today) <= 45 and long_leg.strike >= 50
    long_leg, short_leg = pick(quotes, 50.0, "call", budget=20, min_days=14, max_days=45, today=today)
    assert short_leg and short_leg.strike > long_leg.strike and short_leg.expiry == long_leg.expiry  # spread


def test_options_are_capped_and_settle(settings):
    today = date.today()
    quotes = SyntheticOptions().chain("XYZ", 50.0, 0.5, "call")
    q, _ = pick(quotes, 50.0, "call", 10_000, 14, 45)
    small = PaperBroker(os.path.join(settings.data_dir, "s", "ledger.json"), 100)
    veto = review_option(small, q, settings)
    assert not veto.approved and "Too expensive" in veto.notes[-1]  # one contract = 100 shares
    b = PaperBroker(os.path.join(settings.data_dir, "b", "ledger.json"), 5000)
    rv = review_option(b, q, settings)
    assert rv.approved and rv.risk_dollars <= 500  # 10% of equity per option trade
    b.open_option(q, rv, {})
    assert b.option_exposure == rv.risk_dollars and not review_option(b, q, settings).approved  # one per stock
    exp = date.fromisoformat(q.expiry)
    t = b.mark_option(q.contract, None, q.strike + 5, settings, today=exp)  # expires $5 in the money
    assert t["reason"] == "expired" and t["price"] == 5.0 and not b.options
    b.open_option(q, rv, {})
    t = b.mark_option(q.contract, q.ask * 2.5, 60.0, settings, today=today)  # doubled -> take profit
    assert t["reason"].startswith("took profit") and t["pnl"] > 0


def test_risk_adjusted_scoring(tmp_path):
    assert risk_adjusted(8, 2) > risk_adjusted(10, 15)  # steady beats lucky
    assert risk_adjusted(1, 0.1) == 0.5  # drawdown floored at 2%
    assert risk_adjusted(-3, 10) == -3 < risk_adjusted(0, 0) == 0
    b = PaperBroker(str(tmp_path / "l.json"), 100)
    for e in (100, 110, 88, 105):
        b.history.append({"t": "2026-10-0%dT00:00:00+00:00" % (len(b.history) + 1), "equity": e})
    assert b.drawdown() == 20.0  # 110 -> 88
    s = Season(str(tmp_path / "season.json"), days=1)
    from datetime import datetime, timezone
    t0 = datetime(2026, 11, 2, tzinfo=timezone.utc)
    s.tick({"steady": 100.0, "lucky": 100.0}, now=t0)
    closed = s.tick({"steady": 108.0, "lucky": 110.0}, now=t0 + timedelta(days=1),
                    drawdowns={"steady": 2.0, "lucky": 15.0})
    assert closed["winner"] == "steady" and closed["results"][0]["score"] == 4.0


def test_short_blocked_council_can_buy_puts():
    d = tally({"a": op("SHORT", 80), "b": op("SHORT", 80), "c": op("SHORT", 70)}, {}, 0.3, 3)
    assert not d.approved and d.blocked_short


def test_map_shows_every_lane(settings):
    ev = run(Arena(settings, None))
    lanes = {m["lane"] for m in [e for e in ev if e["type"] == "map"][0]["minerals"]}
    assert lanes == {"stock", "lively", "crypto", "forex"}


# ------------------------------------------------------------- the Syndicate (setup hunters)
import numpy as np
import pandas as pd

from prop_desk.factions import syndicate as syn
from prop_desk.market_data import Headline, Snapshot


def path(*legs, start=100.0):
    """Closes that walk through waypoints: path((n_days, target), ...)."""
    out = [start]
    for n, target in legs:
        out += list(np.linspace(out[-1], target, n + 1)[1:])
    return np.array(out)


def frame(close, vol=1_000_000, spread=0.004, opens=None):
    close = np.asarray(close, float)
    vol = np.broadcast_to(np.asarray(vol, float), close.shape).copy()
    o = close if opens is None else opens
    idx = pd.bdate_range(end="2026-10-13", periods=len(close))
    return pd.DataFrame({"open": o, "high": np.maximum(o, close) * (1 + spread), "low": np.minimum(o, close) * (1 - spread),
                         "close": close, "volume": vol}, index=idx)


FLAT_SPY = frame(path((259, 101)))


def test_momentum_burst_grades_a_breakout_from_a_quiet_base():
    c = path((200, 60), (30, 60.5))  # uptrend, then a tight 30-day base
    b = frame(c, 1_000_000)
    b.loc[b.index[-1], ["open", "high", "low", "close", "volume"]] = [60.6, 63.9, 60.4, 63.7, 2_500_000]
    s = syn.momentum_burst(b, gate=5)
    assert s["grade"] in ("A", "B") and "4% breakout" in s["detail"] and s["stop"] == 60.4
    assert syn.momentum_burst(frame(c), gate=5)["grade"] is None  # a quiet day is no burst


def test_exhaustion_hammer_after_a_pullback():
    c = path((150, 70), (60, 100), (10, 88))  # strong run, then a 12% pullback
    b = frame(c, 1_000_000)
    b.loc[b.index[-1], ["open", "high", "low", "close", "volume"]] = [87.0, 88.4, 83.0, 88.0, 1_800_000]
    snap = Snapshot("HAM", b, {"market_cap_b": 50})
    s = syn.exhaustion_hammer(snap, gate=5)
    assert s["grade"] is not None and "pullback" in s["detail"]
    b.loc[b.index[-1], ["open", "low"]] = [83.5, 83.0]  # a big body is not a hammer
    assert syn.exhaustion_hammer(Snapshot("HAM", b, {}), gate=5)["grade"] is None


def test_vcp_finds_shrinking_contractions():
    c = path((199, 100), (12, 80), (12, 99), (10, 90), (10, 99), (8, 95.5), (8, 98.5), start=50)
    s = syn.vcp(frame(c, np.linspace(1.5e6, 0.6e6, len(c)), spread=0.008), FLAT_SPY)
    assert "contractions" in s["detail"] and s["score"] > 50, s
    assert syn.vcp(frame(path((259, 60))), FLAT_SPY)["grade"] is None  # a downtrend is not stage 2


def test_gap_and_drift():
    c = path((240, 100), (19, 100))
    opens = c.copy()
    b = frame(c, 1_000_000, opens=opens)
    i = len(c) - 1  # gap today: +8% open on 4x volume, closing near the high
    b.iloc[i, :] = [108.0, 112.5, 107.5, 112.0, 4_000_000]
    snap = Snapshot("GAP", b, {"market_cap_b": 5}, [Headline("GAP beats earnings, raises guidance", "wire", "")])
    s = syn.gap_drift(snap)
    assert s["grade"] is not None and "guidance raise" in s["detail"]
    assert syn.gap_drift(Snapshot("FLAT", frame(c), {}))["grade"] is None


def test_growth_leader_needs_earnings_and_leadership():
    c = path((259, 200), start=100)  # doubled while SPY went nowhere
    strong = Snapshot("LDR", frame(c), {"earnings_growth_pct": 55, "revenue_growth_pct": 30})
    assert syn.growth_leader(strong, frame(path((259, 103))))["grade"] in ("A", "B")
    weak = Snapshot("LAG", frame(c), {"earnings_growth_pct": 2, "revenue_growth_pct": 1})
    assert syn.growth_leader(weak, frame(path((259, 103))))["grade"] in (None, "C")


def test_distribution_days_and_posture():
    c = path((240, 120))
    vol = np.full(len(c) + 20, 1e6)
    tail = []
    for k in range(20):  # down 0.5% on higher volume every other day
        tail.append(tail[-1] * (0.995 if k % 2 == 0 else 1.001) if tail else c[-1] * 0.995)
        vol[len(c) + k] = 1.2e6 if k % 2 == 0 else 1e6
    spy = frame(np.concatenate([c, tail]), vol)
    dd = syn.distribution_days(spy)
    assert dd["d25"] >= 6 and dd["risk"] == "severe"
    assert syn.market_posture(spy, None)["state"] == "red"
    calm = syn.market_posture(frame(path((259, 130))), {"raw": 0.6, "bearish": False, "date": "x", "ma8": .6, "ma200": .6})
    assert calm["state"] == "green" and "60%" in calm["detail"]


def test_follow_through_day():
    c = list(path((200, 120)))
    c += [118, 116.5, 115, 113.5, 112]  # a correction, 5 down days
    c += [112.5, 113, 113.4]  # rally attempt days 1-3
    c += [113.4 * 1.017, 115.6, 116]  # day 4: +1.7% on higher volume
    vol = np.full(len(c), 1e6)
    vol[-3] = 1.6e6
    ftd = syn.follow_through(frame(c, vol))
    assert ftd["state"] == "follow-through" and ftd["day"] == 4


def test_circuit_breaker():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 14, 6, tzinfo=timezone.utc)  # 18 hours after yesterday's noon trades
    t = lambda h, pnl: {"time": f"2026-10-1{h}T12:00:00+00:00", "pnl": pnl}  # noqa: E731
    assert syn.circuit_breaker([], 100, now) is None
    assert syn.circuit_breaker([t(2, 1.0), t(3, 0.5)], 100, now) is None
    assert "cooldown" in syn.circuit_breaker([t(3, -0.2), t(3, -0.3)], 100, now)
    assert "revenge" in syn.circuit_breaker([t(3, 0.4), t(3, -0.3)], 100, now)
    assert "today" in syn.circuit_breaker([{"time": "2026-10-14T05:00:00+00:00", "pnl": -2.5}], 100, now)
    later = datetime(2026, 10, 15, 6, tzinfo=timezone.utc)
    assert syn.circuit_breaker([t(3, -0.2), t(3, -0.3)], 100, later) is None  # cooldown over


def test_syndicate_only_hunts_stocks_and_records_calls(settings):
    settings.factions = ["syndicate"]
    a = Arena(settings, None)
    ev = run(a)
    assert not [e for e in ev if e["type"] == "error"]
    f = a.faction("syndicate")
    fld_lanes = [e for e in ev if e["type"] == "map"][0]["minerals"]
    coins = [m["symbol"] for m in fld_lanes if m["lane"] in ("crypto", "forex")]
    assert coins and not [e for e in ev if e["type"] == "candidate" and e["symbol"] in coins]
    for c in f.scorecard.calls:
        assert set(c["votes"]) == set(syn.HUNTERS) | {"syndicate"}


def test_per_faction_model(settings, monkeypatch):
    monkeypatch.setenv("PROP_DESK_MODEL_LEGION", "claude-sonnet-5-5")
    settings.model = "claude-opus-5-5"
    assert settings.model_for("legion") == "claude-sonnet-5-5" and settings.model_for("brood") == "claude-opus-5-5"
