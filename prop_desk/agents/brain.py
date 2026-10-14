"""How agents talk to Claude.

Two backends, same interface (`await brain.think(system, prompt, Schema)`):
  * ApiBrain  - the Claude API with a Console API key (billed per token, or from your
                plan's monthly API credits)
  * PlanBrain - the Claude Agent SDK signed in with your Claude plan; usage counts
                against your plan's usage limits, no API key needed
`detect_backend` picks one: an API key wins if present, otherwise your Claude login.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from functools import lru_cache
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


class ApiBrain:
    backend = "api"

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


class PlanBrain:
    """Structured answers through the Agent SDK, using your Claude plan's login."""
    backend = "plan"

    def __init__(self, model: str, effort: str = "medium", max_parallel: int = 3):
        self.model = model
        self.effort = effort
        self.cost_usd = 0.0  # what the same usage would cost at API prices (for comparison)
        self.max_parallel = max_parallel  # each call starts a CLI process; cap how many run at once
        self._slots = None
        self.workdir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "agent")

    async def think(self, system: str, prompt: str, schema: type[T]) -> T | None:
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        os.makedirs(self.workdir, exist_ok=True)
        options = ClaudeAgentOptions(
            model=self.model,
            effort=self.effort,
            system_prompt=system,
            tools=[],  # a vote needs no tools
            permission_mode="dontAsk",
            setting_sources=[],  # don't load the user's Claude Code settings or CLAUDE.md
            cwd=self.workdir,
            max_turns=3,
            output_format={"type": "json_schema", "schema": schema.model_json_schema()},
        )
        if self._slots is None:
            self._slots = asyncio.Semaphore(self.max_parallel)
        result = None
        async with self._slots:
            async for msg in query(prompt=prompt, options=options):
                if isinstance(msg, ResultMessage):
                    self.cost_usd += msg.total_cost_usd or 0.0
                    if msg.is_error:
                        raise RuntimeError(f"Agent SDK call ended with {msg.subtype}")
                    result = msg.structured_output
        return schema.model_validate(result) if result is not None else None


ClaudeBrain = ApiBrain  # older name


def has_api_key() -> bool:
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return True
    return os.path.isdir(os.path.expanduser("~/.config/anthropic"))  # `ant auth login` profile


@lru_cache(maxsize=1)
def plan_logged_in() -> bool:
    """True if Claude Code (the Agent SDK's engine) is signed in with a Claude plan."""
    try:
        import claude_agent_sdk

        bundled = os.path.join(os.path.dirname(claude_agent_sdk.__file__), "_bundled")
        cli = next((os.path.join(bundled, n) for n in ("claude", "claude.exe")
                    if os.path.exists(os.path.join(bundled, n))), "claude")  # else Claude Code on PATH
        out = subprocess.run([cli, "auth", "status", "--json"], capture_output=True, text=True, timeout=30)
        return bool(json.loads(out.stdout or "{}").get("loggedIn"))
    except Exception as e:
        log.info("couldn't check Claude login: %s", e)
        return False


def detect_backend(mode: str) -> str | None:
    """mode: auto | api | plan | rules  ('claude' means api, for older configs)."""
    mode = {"claude": "api"}.get(mode, mode)
    if mode in ("api", "plan"):
        return mode
    if mode == "rules":
        return None
    if has_api_key():
        return "api"
    return "plan" if plan_logged_in() else None


def make_brain(backend: str | None, model: str, effort: str):
    if backend == "api":
        return ApiBrain(model, effort)
    if backend == "plan":
        return PlanBrain(model, effort)
    return None


def has_credentials() -> bool:
    return detect_backend("auto") is not None
