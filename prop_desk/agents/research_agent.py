"""Research agents built on the Claude Agent SDK.

Researchers get an open-ended job ("find the best trades today"), so they run as
real agents: Claude decides which tools to call, in what order, and when it has
enough. Their tools are an in-process MCP server over the desk's own data, plus
web search for news. Everything they can touch is read-only.

The Council's votes stay on plain structured API calls (see brain.py) - a vote
on a fixed set of facts doesn't need an agent loop, and is faster and cheaper
without one.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)
Emit = Callable[[dict], Awaitable[None]]


@dataclass
class DeskContext:
    """Read-only view of the desk's data that research tools can query."""
    reports: dict[str, dict] = field(default_factory=dict)  # symbol -> compact report
    screen: dict[str, float] = field(default_factory=dict)  # symbol -> setup score
    contracts: dict[str, dict] = field(default_factory=dict)  # ticker -> contract dict
    holdings: list[str] = field(default_factory=list)


def _text(obj) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(obj, default=str)}]}


def build_tools(ctx: DeskContext):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool("screen_watchlist", "Technical setup score (-1 bearish .. +1 bullish) for everything on the "
          "watchlist (stocks, crypto like BTC-USD, currency pairs like EURUSD=X), plus what the desk already holds.", {})
    async def screen_watchlist(args):
        board = sorted(ctx.screen.items(), key=lambda kv: kv[1], reverse=True)
        return _text({"scores": dict(board), "already_held": ctx.holdings})

    @tool("symbol_report", "Full research report for one symbol (stock, coin or currency pair): price, momentum, Ichimoku cloud, money flow, "
          "volatility, fundamentals and recent headlines.", {"symbol": str})
    async def symbol_report(args):
        r = ctx.reports.get(str(args.get("symbol", "")).upper())
        if not r:
            return {**_text({"error": "not on the watchlist", "watchlist": sorted(ctx.reports)}), "is_error": True}
        return _text(r)

    @tool("prediction_markets", "Open Kalshi prediction markets: ticker, question, YES bid/ask in cents "
          "(= implied % chance), volume and closing time.", {})
    async def prediction_markets(args):
        keys = ("ticker", "title", "category", "yes_bid", "yes_ask", "last_price", "previous_price",
                "volume", "close_time")
        return _text([{k: c[k] for k in keys} for c in ctx.contracts.values()])

    @tool("market_detail", "Full detail and resolution rules for one prediction market.", {"ticker": str})
    async def market_detail(args):
        c = ctx.contracts.get(str(args.get("ticker", "")))
        if not c:
            return {**_text({"error": "unknown ticker"}), "is_error": True}
        return _text(c)

    return create_sdk_mcp_server("desk", tools=[screen_watchlist, symbol_report, prediction_markets, market_detail])


_SYSTEM = """You are {name}, {role} at a small proprietary trading firm. You hunt for trade ideas and
bring the best ones to the Council of specialist traders, who vote on them. A devil's advocate will
attack every idea you bring, so only bring ideas you can defend.

Rules:
- Use the desk tools for prices, indicators, fundamentals and markets. Never invent numbers.
- Use web search for current news and for facts that decide prediction markets.
- Be selective: it is fine to return fewer ideas, or none, if nothing is good.
- Keep theses to 2-3 sentences and cite the specific evidence."""


class ResearchAgent:
    def __init__(self, persona, model: str, effort: str, emit: Emit, budget_usd: float = 1.0):
        self.p = persona
        self.model = model
        self.effort = effort
        self.emit = emit
        self.budget = budget_usd
        self.cost_usd = 0.0

    async def hunt(self, task: str, ctx: DeskContext, schema: type[BaseModel]) -> BaseModel | None:
        from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock,
                                      ToolUseBlock, query)

        desk_tools = ["screen_watchlist", "symbol_report", "prediction_markets", "market_detail"]
        workdir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "agent")
        os.makedirs(workdir, exist_ok=True)
        options = ClaudeAgentOptions(
            model=self.model,
            effort=self.effort,
            system_prompt=_SYSTEM.format(name=self.p.name, role=self.p.role),
            mcp_servers={"desk": build_tools(ctx)},
            tools=["WebSearch", "WebFetch"],  # no shell, no file editing
            allowed_tools=["WebSearch", "WebFetch"] + [f"mcp__desk__{t}" for t in desk_tools],
            permission_mode="dontAsk",  # anything not allowed above is refused, never prompted
            setting_sources=[],  # don't load the user's Claude Code settings or CLAUDE.md
            cwd=workdir,
            max_turns=16,
            max_budget_usd=self.budget,
            output_format={"type": "json_schema", "schema": schema.model_json_schema()},
        )
        result = None
        async for msg in query(prompt=task, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock) and block.name != "StructuredOutput":
                        await self.emit({"type": "tool", "agent": self.p.id,
                                         "tool": block.name.removeprefix("mcp__desk__"), "input": block.input})
                    elif isinstance(block, TextBlock) and block.text.strip():
                        await self.emit({"type": "message", "agent": self.p.id, "kind": "thinking",
                                         "text": block.text.strip()[:400]})
            elif isinstance(msg, ResultMessage):
                self.cost_usd += msg.total_cost_usd or 0.0
                if msg.is_error:
                    raise RuntimeError(f"research agent ended with {msg.subtype}")
                result = msg.structured_output
        if result is None:
            return None
        try:
            return schema.model_validate(result)
        except ValidationError as e:
            log.warning("research output didn't match schema: %s", e)
            return None
