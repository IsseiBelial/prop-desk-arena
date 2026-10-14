"""Desk settings.

Set them in `prop_desk/arena.env` (copy `arena.env.example`; one KEY=value per line),
or as environment variables. Real environment variables win over the file.
"""
import os
from dataclasses import dataclass, field

ENV_FILE = os.getenv("PROP_DESK_ENV_FILE", os.path.join(os.path.dirname(__file__), "arena.env"))


def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(ENV_FILE)


def _env_list(name: str, default: str) -> list[str]:
    return [s.strip().upper() for s in os.getenv(name, default).split(",") if s.strip()]


@dataclass
class Settings:
    # --- AI ---
    # auto: an API key if one is set, otherwise your Claude plan login (Claude Code /
    # Agent SDK), otherwise the built-in rule-based agents so the desk still works offline.
    ai_mode: str = os.getenv("PROP_DESK_AI", "auto")  # auto | api | plan | rules
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
    starting_cash: float = float(os.getenv("PROP_DESK_CASH", "100"))  # per faction, at the first season
    fractional_shares: bool = os.getenv("PROP_DESK_FRACTIONAL", "true").lower() == "true"
    min_order_dollars: float = float(os.getenv("PROP_DESK_MIN_ORDER", "1"))
    # A season is a contest window. At the end, each faction keeps its ending equity
    # (profits reinvested, losses carried) and a new season starts from there.
    season_days: int = int(os.getenv("PROP_DESK_SEASON_DAYS", "30"))
    # Each faction keeps its own ledger and scorecard under data_dir/<faction>/.
    data_dir: str = os.getenv("PROP_DESK_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
    factions: list[str] = field(default_factory=lambda: [
        f.strip().lower() for f in os.getenv("PROP_DESK_FACTIONS", "legion,ascendant,brood").split(",") if f.strip()])
    # Seconds between messages in rules mode so humans can follow the floor.
    pace: float = float(os.getenv("PROP_DESK_PACE", "0.6"))

    # --- Consensus ---
    approve_threshold: float = float(os.getenv("PROP_DESK_THRESHOLD", "0.30"))
    min_agreeing_traders: int = int(os.getenv("PROP_DESK_QUORUM", "3"))
    # Agreeing traders must use at least this many kinds of evidence (price / fundamental / news).
    min_families: int = int(os.getenv("PROP_DESK_MIN_FAMILIES", "2"))
    # Spending cap for each Agent SDK research hunt.
    research_budget_usd: float = float(os.getenv("PROP_DESK_RESEARCH_BUDGET", "1.0"))

    # --- Risk (enforced in code, not by the AI) ---
    risk_per_trade_pct: float = float(os.getenv("PROP_DESK_RISK_PCT", "1.0"))
    max_position_pct: float = float(os.getenv("PROP_DESK_MAX_POS_PCT", "10"))
    max_open_positions: int = int(os.getenv("PROP_DESK_MAX_POSITIONS", "8"))
    max_daily_loss_pct: float = float(os.getenv("PROP_DESK_MAX_DAILY_LOSS_PCT", "3"))
    allow_shorts: bool = os.getenv("PROP_DESK_ALLOW_SHORTS", "false").lower() == "true"
    max_per_sector: int = int(os.getenv("PROP_DESK_MAX_PER_SECTOR", "2"))
    earnings_blackout_days: int = int(os.getenv("PROP_DESK_EARNINGS_BLACKOUT_DAYS", "3"))

    # --- Prediction markets (Kalshi) - only when no stock trade got done ---
    kalshi_enabled: bool = os.getenv("PROP_DESK_KALSHI", "true").lower() == "true"
    kalshi_data: str = os.getenv("PROP_DESK_KALSHI_DATA", "auto")  # auto | kalshi | synthetic
    kalshi_candidates: int = int(os.getenv("PROP_DESK_KALSHI_CANDIDATES", "2"))
    kalshi_min_edge_cents: float = float(os.getenv("PROP_DESK_KALSHI_MIN_EDGE", "5"))
    kalshi_quorum: int = int(os.getenv("PROP_DESK_KALSHI_QUORUM", "2"))
    kalshi_max_pct: float = float(os.getenv("PROP_DESK_KALSHI_MAX_PCT", "1"))
    kalshi_total_pct: float = float(os.getenv("PROP_DESK_KALSHI_TOTAL_PCT", "5"))

    # --- Track record ---
    call_horizon_days: int = int(os.getenv("PROP_DESK_CALL_HORIZON_DAYS", "5"))

    # --- Automation ---
    autorun_minutes: int = int(os.getenv("PROP_DESK_AUTORUN_MINUTES", "0"))  # 0 = off
    # Or run at fixed US-Eastern times on weekdays, e.g. "09:45,12:30,15:30".
    schedule_et: list[str] = field(default_factory=lambda: [
        t.strip() for t in os.getenv("PROP_DESK_SCHEDULE_ET", "").split(",") if t.strip()])
    market_hours_only: bool = os.getenv("PROP_DESK_MARKET_HOURS_ONLY", "true").lower() == "true"
    alert_webhook: str = os.getenv("PROP_DESK_ALERT_WEBHOOK", "")  # Slack-compatible incoming webhook


    @property
    def sessions_dir(self) -> str:
        return os.path.join(self.data_dir, "sessions")


settings = Settings()
