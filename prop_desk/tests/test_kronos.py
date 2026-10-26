import asyncio

import numpy as np
import pytest

from prop_desk import forecaster, kronos_backtest
from prop_desk.agents.schemas import DeskMemo, KronosReview
from prop_desk.desk import Arena
from prop_desk.factions import kronos as kr
from prop_desk.forecaster import Forecast
from prop_desk.market_data import SyntheticProvider
from prop_desk.tests.test_desk import run, settings  # noqa: F401  (fixture)


def paths(*closes, price=100.0, wick=0.0):
    """Forecast paths from lists of daily closes; highs/lows sit `wick` around the bodies."""
    c = np.array(closes, dtype=float)
    o = np.concatenate([np.full((len(c), 1), price), c[:, :-1]], axis=1)
    return Forecast("TEST", price, c.shape[1], np.stack([o, np.maximum(o, c) + wick, np.minimum(o, c) - wick, c], -1))


# ------------------------------------------------------------- reading the paths
def test_forecast_reads_the_spread_of_paths():
    fc = paths([101, 102, 104], [99, 98, 97], [100, 101, 103], [100, 100, 99])
    assert fc.p_up == 0.5
    assert fc.expected_pct == pytest.approx((4 - 3 + 3 - 1) / 4)
    lo, hi = fc.band_pct
    assert lo < 0 < hi and fc.vol_pct > 0


def test_walk_plays_the_real_stop_and_target():
    # Stop 2 below, target 3 above (100 -> 98 / 103).
    fc = paths([101, 103.5, 104],  # target on day 2: +1.5R
               [97.5, 99, 104],    # stopped on day 1, even though it ends far up: -1R
               [100.5, 101, 101],  # neither: marked to the last close, +0.5R
               price=100)
    w = fc.walk(2, 3, 1)
    assert w["win"] == pytest.approx(2 / 3) and w["stopped"] == pytest.approx(1 / 3)
    assert w["r"] == pytest.approx((1.5 - 1 + 0.5) / 3)
    # A day that touches both counts as stopped out (the cautious reading).
    both = paths([100, 100], price=100, wick=4).walk(2, 3, 1)
    assert both["stopped"] == 1 and both["r"] == -1
    # The same paths as a short.
    assert fc.walk(2, 3, -1)["stopped"] == pytest.approx(2 / 3)


def test_implausible_forecasts_are_ignored():
    calm = paths([101, 102, 103])
    wild = paths([110, 125, 140])
    assert not kr.implausible(calm, 2.0)
    assert kr.implausible(wild, 2.0)  # +40% in 3 days on a stock that moves ~2% a day


def test_wardens_rank_and_vote():
    up = paths(*[[101, 102, 103]] * 9, [99, 98, 97])  # 90% of paths up
    flat = paths([101, 100, 99], [99, 100, 101])
    fcs = {"A": up, "B": flat, "C": paths([99, 98, 96]), "D": paths([100, 101, 100.5])}
    rk = kr.ranks(fcs)
    assert rk["A"] == 1.0 and rk["C"] == 0.0
    v = kr.warden_votes(up, rk["A"], atr=1.0, min_prob=0.6, field=4)
    assert {u: s for u, (s, _, _) in v.items()} == {"ione": "LONG", "sol": "LONG", "venn": "LONG"}
    assert "#1 of 4" in v["sol"][2]
    v = kr.warden_votes(flat, rk["B"], atr=1.0, min_prob=0.6, field=4)
    assert v["ione"][0] == "PASS" and v["sol"][0] == "PASS"
    assert kr.warden_votes(up, None, 1.0, 0.6)["sol"][0] == "PASS"  # too few symbols to rank


# ------------------------------------------------------------- the real model code
def test_untrained_kronos_runs_and_is_repeatable():
    pytest.importorskip("torch")
    e = forecaster.KronosEngine.untrained(samples=3)
    bars = SyntheticProvider().bars("AAPL")
    a, b = e.forecast("AAPL", bars, 5), e.forecast("AAPL", bars, 5)
    assert a.paths.shape == (3, 5, 4) and np.isfinite(a.paths).all()
    assert (a.paths == b.paths).all()  # same history, same forecast
    assert len({tuple(p) for p in a.closes.round(6)}) == 3  # the paths are sampled separately, not copies
    with pytest.raises(ValueError):
        e.forecast("AAPL", bars.tail(30), 5)


def test_tokenizer_matches_the_model_size():
    assert forecaster.KronosEngine("NeoQuasar/Kronos-mini").tokenizer_id.endswith("Tokenizer-2k")
    small = forecaster.KronosEngine("NeoQuasar/Kronos-small")
    assert small.tokenizer_id.endswith("Tokenizer-base") and small.max_context == 512


# ------------------------------------------------------------- the faction
def kronos_arena(settings, brain=None):
    settings.factions = ["kronos"]
    a = Arena(settings, None)
    a.faction("kronos").brain = brain
    return a, a.faction("kronos")


def test_kronos_watch_trades_when_the_paths_agree(settings, fake_kronos):
    a, k = kronos_arena(settings)
    ev = run(a)
    assert not [e for e in ev if e["type"] == "error"]
    assert [e for e in ev if e["type"] == "forecast"] and [e for e in ev if e["type"] == "kvote"]
    assert [e for e in ev if e["type"] == "order" and e["kind"] == "stock"]
    for c in k.scorecard.calls:
        assert set(c["votes"]) == {"ione", "sol", "venn", "kronos"}  # no Claude in rules mode
    assert not [e for e in ev if e["type"] == "candidate" and e.get("kind") == "contract"]  # no gas: candles only


def test_kronos_watch_sits_out_without_pytorch(settings, monkeypatch):
    monkeypatch.setattr(forecaster, "available", lambda: False)
    a, k = kronos_arena(settings)
    ev = run(a)
    assert any("PyTorch" in e.get("text", "") for e in ev if e["type"] == "message")
    assert not k.scorecard.calls and not [e for e in ev if e["type"] == "order"]


def test_kronos_exits_when_the_paths_turn(settings, fake_kronos):
    a, k = kronos_arena(settings)
    run(a)
    held = set(k.broker.positions)
    assert held
    fake_kronos.drift = -0.006
    ev = run(a)
    assert any(e["type"] == "retreat" for e in ev)
    assert not held & set(k.broker.positions)


class ReviewBrain:
    """Stands in for Claude as the Interpreter."""
    cost_usd = 0.0

    def __init__(self, verdict):
        self.verdict, self.prompts = verdict, []

    async def think(self, system, prompt, schema):
        self.prompts.append(prompt)
        assert schema is KronosReview
        return KronosReview(verdict=self.verdict, view="SHORT" if self.verdict == "VETO" else "LONG",
                            headline="Earnings land inside the window." if self.verdict == "VETO" else "Clean.",
                            reasoning="r", concerns=[])


def test_claude_can_veto_but_not_start_a_trade(settings):
    a, k = kronos_arena(settings, ReviewBrain("VETO"))
    ev = run(a)
    assert not [e for e in ev if e["type"] == "order"]
    assert [e for e in ev if e["type"] == "verdict" and e["label"] == "VETOED"]
    assert "Headlines:" in k.brain.prompts[0] and "can't see" in k.brain.prompts[0]
    vetoed = [c for c in k.scorecard.calls if "cass" in c["votes"]]
    assert vetoed and all(c["votes"]["cass"] == "SHORT" and c["votes"]["kronos"] == "PASS" for c in vetoed)


def test_claude_confirms_and_the_trade_goes_ahead(settings):
    a, k = kronos_arena(settings, ReviewBrain("CONFIRM"))
    ev = run(a)
    assert [e for e in ev if e["type"] == "order"]
    assert all(c["votes"].get("cass") == "LONG" for c in k.scorecard.calls if c["traded"])


def test_claude_is_only_asked_when_kronos_wants_to_trade(settings, fake_kronos):
    fake_kronos.split = True  # half the paths up, half down: nothing to approve
    a, k = kronos_arena(settings, ReviewBrain("CONFIRM"))
    run(a)
    assert k.scorecard.calls and not k.brain.prompts


# ------------------------------------------------------------- backtest
def test_backtest_scores_kronos_against_baselines(tmp_path, fake_kronos):
    out = tmp_path / "bt.csv"
    p = SyntheticProvider()
    since = str(p.bars("AAPL").index[-60].date())
    r = kronos_backtest.run(["AAPL", "MSFT", "XOM", "JPM"], since, 10, 5, 4, 260, 0.6, engine=fake_kronos,
                            provider=p, out=str(out), log=lambda *_: None)
    assert r["forecasts"] == 4 * len(range(len(p.bars("AAPL", 1300)) - 60, len(p.bars("AAPL", 1300)) - 5, 10))
    for key in ("kronos", "always_long", "momentum"):
        assert r[key]["hit_rate"] is None or 0 <= r[key]["hit_rate"] <= 1
    assert r["kronos"]["calls"] == r["always_long"]["calls"]  # always-up forecasts = always long
    assert r["rank_ic"] is not None and out.exists()
