import numpy as np
import pytest

from prop_desk import forecaster
from prop_desk.forecaster import Forecast


class FakeKronos:
    """Stands in for Kronos: every symbol drifts the same way, with a little spread between paths."""
    name = model_id = "Kronos-fake"
    context = 400
    device = "cpu"

    def __init__(self, drift=0.006, n=20):
        self.drift, self.n, self.calls = drift, n, 0
        self.split = False  # True: half the paths rise and half fall, so they don't agree

    def load(self):
        return self

    def forecast(self, symbol, bars, horizon=5, asset="stock"):
        self.calls += 1
        px = float(bars["close"].iloc[-1])
        rng = np.random.default_rng(sum(map(ord, symbol)))
        drift = np.where(np.arange(self.n) % 2, -self.drift, self.drift)[:, None] if self.split else self.drift
        close = px * np.cumprod(1 + drift + rng.normal(0, 0.004, (self.n, horizon)), axis=1)
        o = np.concatenate([np.full((self.n, 1), px), close[:, :-1]], axis=1)
        hi, lo = np.maximum(o, close) * 1.002, np.minimum(o, close) * 0.998
        return Forecast(symbol, px, horizon, np.stack([o, hi, lo, close], axis=-1))


@pytest.fixture(autouse=True)
def fake_kronos(monkeypatch):
    """No test downloads or runs the real Kronos model."""
    engine = FakeKronos()
    monkeypatch.setattr(forecaster, "available", lambda: True)
    monkeypatch.setattr(forecaster, "make_engine", lambda s: engine)
    return engine
