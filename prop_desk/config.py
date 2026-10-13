"""Desk settings. Everything can be overridden with environment variables."""
import os
from dataclasses import dataclass, field


def _env_list(name: str, default: str) -> list[str]:
    return [s.strip().upper() for s in os.getenv(name, default).split(",") if s.strip()]


@dataclass
class Settings:
    # --- AI ---
    # "auto": use Claude when an API key is present, otherwise run the
    # built-in rule-based agents so the desk still works offline.
    ai_mode: str = os.getenv("PROP_DESK_AI", "auto")  # auto | claude | rules
    model: str = os.getenv("PROP_DESK_MODEL", "claude-opus-5-5")
    effort: str = os.getenv("PROP_DESK_EFFORT", "medium")  # low | medium | high

    # --- Market data ---
    data_source: str = os.getenv("PROP_DESK_DATA", "auto")  # auto | yahoo | synthetic
    watchlist: list[str] = field(default_factory=lambda: _env_list(
        "PROP_DESK_WATCHLIST",
        "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AMD,JPM,XOM,LLY,COST",
    ))
    benchmark: str = os.getenv("PROP_DESK_BENCHMARK", "SPY")
    candidates_per_session: int = int(os.getenv("PROP_DESK_CANDIDATES", "3"))

    # --- Execution ---
    # Orders go to an internal paper ledger. There is intentionally no live
    # brokerage connection yet - see README "Going live".
    starting_cash: float = float(os.getenv("PROP_DESK_CASH", "100000"))
    ledger_path: str = os.getenv("PROP_DESK_LEDGER", os.path.join(os.path.dirname(__file__), "data", "ledger.json"))
    # Seconds between messages in rules mode so humans can follow the floor.
    pace: float = float(os.getenv("PROP_DESK_PACE", "0.6"))

    # --- Consensus ---
    approve_threshold: float = float(os.getenv("PROP_DESK_THRESHOLD", "0.30"))
    min_agreeing_traders: int = int(os.getenv("PROP_DESK_QUORUM", "3"))

    # --- Risk (enforced in code, not by the AI) ---
    risk_per_trade_pct: float = float(os.getenv("PROP_DESK_RISK_PCT", "1.0"))
    max_position_pct: float = float(os.getenv("PROP_DESK_MAX_POS_PCT", "10"))
    max_open_positions: int = int(os.getenv("PROP_DESK_MAX_POSITIONS", "8"))
    max_daily_loss_pct: float = float(os.getenv("PROP_DESK_MAX_DAILY_LOSS_PCT", "3"))
    allow_shorts: bool = os.getenv("PROP_DESK_ALLOW_SHORTS", "false").lower() == "true"


settings = Settings()
