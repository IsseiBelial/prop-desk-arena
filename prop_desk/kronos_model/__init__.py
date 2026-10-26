"""Kronos, a foundation model for financial candlesticks (K-lines).

Vendored from https://github.com/shiyu-coder/Kronos (MIT License, see LICENSE in this
folder), commit 67b630e (2026-04-13). Paper: https://arxiv.org/abs/2508.02739

Only the imports at the top of kronos.py were changed (package-relative import,
tqdm optional). The trained weights are downloaded from the Hugging Face Hub
(NeoQuasar/Kronos-small etc.) the first time the Kronos Watch runs.
Needs PyTorch: pip install -r prop_desk/requirements-kronos.txt
"""
from .kronos import Kronos, KronosPredictor, KronosTokenizer

__all__ = ["Kronos", "KronosPredictor", "KronosTokenizer"]
