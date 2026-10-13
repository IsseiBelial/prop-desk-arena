"""Thin wrapper around the Claude API used by every agent."""
from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import BaseModel

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

# $ per million tokens (input, output) - used for the cost readout in the GUI.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
# Models that accept server-side refusal fallbacks in their "default" form.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class ClaudeBrain:
    def __init__(self, model: str, effort: str = "medium"):
        import anthropic

        self.client = anthropic.AsyncAnthropic()
        self.model = model
        self.effort = effort
        self.input_tokens = 0
        self.output_tokens = 0

    @property
    def cost_usd(self) -> float:
        pin, pout = PRICES.get(self.model, (4.0, 20.0))
        return (self.input_tokens * pin + self.output_tokens * pout) / 1e6

    async def think(self, system: str, prompt: str, schema: type[T]) -> T | None:
        """Ask Claude for a schema-validated answer. Returns None if it declines."""
        extra = {}
        if self.model in FALLBACK_MODELS:
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        resp = await self.client.beta.messages.parse(
            model=self.model,
            max_tokens=16000,
            # The persona prompt is identical every session, so cache it.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
            output_config={"effort": self.effort},
            **extra,
        )
        u = resp.usage
        # Cached reads bill at ~0.1x and cache writes at ~1.25x of the input rate.
        self.input_tokens += int(
            u.input_tokens + 0.1 * (u.cache_read_input_tokens or 0) + 1.25 * (u.cache_creation_input_tokens or 0)
        )
        self.output_tokens += u.output_tokens
        if resp.stop_reason == "refusal":
            log.warning("model declined: %s", resp.stop_details)
            return None
        return resp.parsed_output


def has_credentials() -> bool:
    """True if the Anthropic SDK can find credentials (env var or `ant auth login`)."""
    import os

    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return True
    return os.path.isdir(os.path.expanduser("~/.config/anthropic"))
