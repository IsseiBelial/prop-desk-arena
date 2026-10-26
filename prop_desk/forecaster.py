"""Kronos forecasts: many possible next few days for one symbol.

Kronos (see kronos_model/) reads a symbol's daily candles and writes the next few
candles one at a time, the way a language model writes the next words. Ask it
several times and you get several different, plausible futures ("paths"). The
Kronos Watch reads the spread of those paths, not just their average:
  * how many paths end higher than today      -> P(up)
  * the typical and the bad outcomes          -> expected return, 10th / 90th percentile
  * each path's daily highs and lows          -> would the stop or the target be hit first?

Kronos's own KronosPredictor.predict() averages the paths into one line. Here they
are kept apart by sending N copies of the history as one batch with sample_count=1,
so every copy is sampled on its own.

Needs PyTorch (pip install -r prop_desk/requirements-kronos.txt). Without it the
Kronos Watch sits out and the other factions run as usual.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

TOKENIZERS = {"mini": "NeoQuasar/Kronos-Tokenizer-2k"}  # every other size uses Kronos-Tokenizer-base
MAX_CONTEXT = {"mini": 2048}  # small and base read at most 512 candles

# The published configs (config.json on the Hub), used only to build an untrained
# stand-in for tests when the trained weights can't be downloaded.
_SMALL = dict(d_model=512, n_heads=8, ff_dim=1024, n_layers=8, s1_bits=10, s2_bits=10, ffn_dropout_p=0.25,
              attn_dropout_p=0.1, resid_dropout_p=0.25, token_dropout_p=0.1, learn_te=True)
_TOKENIZER = dict(d_in=6, d_model=256, n_heads=4, ff_dim=512, n_enc_layers=4, n_dec_layers=4, ffn_dropout_p=0.0,
                  attn_dropout_p=0.0, resid_dropout_p=0.0, s1_bits=10, s2_bits=10, beta=0.05, gamma0=1.0,
                  gamma=1.1, zeta=0.05, group_size=4)


def available() -> bool:
    """True if PyTorch and the Kronos code can be imported."""
    try:
        import torch  # noqa: F401
        from .kronos_model import Kronos  # noqa: F401
        return True
    except Exception:
        return False


@dataclass
class Forecast:
    symbol: str
    price: float  # today's close
    horizon: int  # trading days ahead
    paths: np.ndarray  # (n_paths, horizon, 4): open, high, low, close

    @property
    def closes(self) -> np.ndarray:
        return self.paths[:, :, 3]

    @property
    def returns_pct(self) -> np.ndarray:
        """Each path's return from today's close to the last forecast day."""
        return (self.closes[:, -1] / self.price - 1) * 100

    @property
    def p_up(self) -> float:
        return float((self.returns_pct > 0).mean())

    @property
    def expected_pct(self) -> float:
        return float(self.returns_pct.mean())

    @property
    def median_pct(self) -> float:
        return float(np.median(self.returns_pct))

    @property
    def band_pct(self) -> tuple[float, float]:
        lo, hi = np.percentile(self.returns_pct, [10, 90])
        return float(lo), float(hi)

    @property
    def vol_pct(self) -> float:
        """Annualized volatility Kronos expects: the spread of its daily moves."""
        c = np.concatenate([np.full((len(self.closes), 1), self.price), self.closes], axis=1)
        r = np.diff(np.log(np.maximum(c, 1e-12)), axis=1)
        return float(r.std() * np.sqrt(252) * 100)

    def walk(self, stop_dist: float, target_dist: float, side: int = 1) -> dict:
        """Play the Quartermaster's real trade along every path.

        side=1 is a long (stop below, target above), -1 a short. Each day, a path's
        low (high for a short) is checked against the stop before its high against the
        target, so a day that touches both counts as stopped out. A path that hits
        neither is marked to its last close. Returns the share of paths that won, the
        share stopped out, and the average result in R (multiples of the risk taken).
        """
        o, h, l, c = (self.paths[:, :, k] for k in range(4))
        hi, lo = np.maximum.reduce([o, h, c]), np.minimum.reduce([o, l, c])  # Kronos can draw a high under the close
        stop, target = self.price - side * stop_dist, self.price + side * target_dist
        results, stops = [], 0
        for p in range(len(c)):
            r = None
            for d in range(self.horizon):
                if (side == 1 and lo[p, d] <= stop) or (side == -1 and hi[p, d] >= stop):
                    r, stops = -1.0, stops + 1
                    break
                if (side == 1 and hi[p, d] >= target) or (side == -1 and lo[p, d] <= target):
                    r = target_dist / stop_dist
                    break
            results.append(r if r is not None else side * (c[p, -1] - self.price) / stop_dist)
        res = np.array(results)
        return {"win": float((res > 0).mean()), "stopped": stops / len(res), "r": float(res.mean())}

    def public(self, history: pd.Series, max_paths: int = 30) -> dict:
        """For the GUI: recent closes plus every path's closes (rounded)."""
        d = 2 if self.price >= 10 else 4 if self.price >= 1 else 6
        lo, hi = self.band_pct
        return {"history": [round(float(x), d) for x in history.tail(30)],
                "paths": [[round(float(x), d) for x in p] for p in self.closes[:max_paths]],
                "p_up": round(self.p_up, 3), "expected_pct": round(self.expected_pct, 2),
                "p10_pct": round(lo, 2), "p90_pct": round(hi, 2), "horizon": self.horizon}


class KronosEngine:
    """Loads Kronos once and turns a symbol's daily bars into a Forecast."""

    def __init__(self, model_id: str = "NeoQuasar/Kronos-small", tokenizer_id: str | None = None,
                 device: str | None = None, samples: int = 30, temperature: float = 1.0, top_p: float = 0.9,
                 context: int = 400):
        size = model_id.rsplit("-", 1)[-1].lower()
        self.model_id = model_id
        self.tokenizer_id = tokenizer_id or TOKENIZERS.get(size, "NeoQuasar/Kronos-Tokenizer-base")
        self.max_context = MAX_CONTEXT.get(size, 512)
        self.context = min(context, self.max_context)
        self.device = None if device in (None, "", "auto") else device
        self.samples, self.temperature, self.top_p = samples, temperature, top_p
        self._predictor = None
        self._lock = threading.Lock()

    @classmethod
    def untrained(cls, samples: int = 8) -> "KronosEngine":
        """Same architecture as Kronos-small with random weights. For tests only: its forecasts mean nothing."""
        from .kronos_model import Kronos, KronosPredictor, KronosTokenizer

        e = cls(model_id="untrained", samples=samples, device="cpu")
        import torch
        torch.manual_seed(0)
        e._predictor = KronosPredictor(Kronos(**_SMALL).eval(), KronosTokenizer(**_TOKENIZER).eval(),
                                       device="cpu", max_context=e.max_context)
        return e

    @property
    def name(self) -> str:
        return self.model_id.split("/")[-1]

    def load(self):
        """Download (first run only, ~115 MB for Kronos-small) and load the model. Thread-safe."""
        with self._lock:
            if self._predictor is None:
                from .kronos_model import Kronos, KronosPredictor, KronosTokenizer

                tokenizer = KronosTokenizer.from_pretrained(self.tokenizer_id).eval()
                model = Kronos.from_pretrained(self.model_id).eval()  # eval(): no dropout while forecasting
                self._predictor = KronosPredictor(model, tokenizer, device=self.device, max_context=self.max_context)
                log.info("Kronos loaded: %s + %s on %s", self.model_id, self.tokenizer_id, self._predictor.device)
        return self._predictor

    def forecast(self, symbol: str, bars: pd.DataFrame, horizon: int = 5, asset: str = "stock") -> Forecast:
        """`bars`: daily open/high/low/close/volume, oldest first. Same input -> same forecast."""
        import torch

        pred = self.load()
        bars = bars.dropna(subset=["open", "high", "low", "close"]).tail(self.context)
        if len(bars) < 60:
            raise ValueError(f"only {len(bars)} days of history for {symbol}; Kronos needs at least 60")
        idx = pd.DatetimeIndex(bars.index)
        idx = idx.tz_localize(None) if idx.tz is not None else idx
        # Crypto trades every day; stocks and currencies on weekdays.
        future = pd.date_range(idx[-1], periods=horizon + 1, freq="D" if asset == "crypto" else "B")[1:]

        x = bars[["open", "high", "low", "close"]].astype(float).copy()
        x["volume"] = bars["volume"].fillna(0).astype(float) if "volume" in bars else 0.0
        x["amount"] = x["volume"] * x[["open", "high", "low", "close"]].mean(axis=1)
        # The same normalization as KronosPredictor.predict().
        v = x.to_numpy(np.float32)
        mean, std = v.mean(axis=0), v.std(axis=0)
        v = np.clip((v - mean) / (std + 1e-5), -pred.clip, pred.clip)
        from .kronos_model.kronos import calc_time_stamps

        xs = calc_time_stamps(pd.Series(idx)).to_numpy(np.float32)
        ys = calc_time_stamps(pd.Series(future)).to_numpy(np.float32)
        n = self.samples
        seed = int(hashlib.sha256(f"{symbol}|{idx[-1].date()}|{self.model_id}".encode()).hexdigest()[:8], 16)
        with self._lock:  # torch's sampler uses one global random generator
            torch.manual_seed(seed)
            out = pred.generate(np.repeat(v[None], n, 0), np.repeat(xs[None], n, 0), np.repeat(ys[None], n, 0),
                                horizon, self.temperature, 0, self.top_p, 1, False)
        out = out * (std + 1e-5) + mean
        return Forecast(symbol, float(bars["close"].iloc[-1]), horizon, out[:, :, :4].astype(float))


_engine: KronosEngine | None = None


def make_engine(s) -> KronosEngine:
    """One shared engine per process, configured from Settings."""
    global _engine
    if _engine is None or _engine.model_id != s.kronos_model:
        _engine = KronosEngine(s.kronos_model, s.kronos_tokenizer or None, s.kronos_device, s.kronos_samples,
                               s.kronos_temperature, s.kronos_top_p)
    return _engine
