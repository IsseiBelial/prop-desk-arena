"""Pre-flight check: is this computer ready to run the Arena?

    python -m prop_desk.check

Prints one line per check with OK / WARN / FAIL and what to do about it.
Makes no Claude calls and places no trades.
"""
from __future__ import annotations

import sys

RESULTS: list[str] = []


def line(status: str, what: str, fix: str = "") -> None:
    mark = {"OK": "[ OK ]", "WARN": "[WARN]", "FAIL": "[FAIL]"}[status]
    print(f"{mark} {what}" + (f"\n       -> {fix}" if fix else ""), flush=True)
    RESULTS.append(status)


def extra_checks(settings) -> None:
    """Lively-stock screener, crypto, currencies and option chains (all from Yahoo)."""
    import yfinance as yf

    from .market_data import YahooProvider
    from .universe import screen_lively
    assets = set(settings.assets)
    if "lively" in assets:
        found = screen_lively(settings.lively_count, settings.lively_min_price,
                              settings.lively_min_dollar_volume, set(settings.watchlist))
        line("OK" if found else "WARN", f"Lively-stock screener: {', '.join(found) or 'nothing found'}",
             "" if found else "The Arena would use its built-in list of lively stocks instead")
    for kind, syms in (("crypto", settings.crypto), ("forex", settings.forex)):
        if kind in assets and syms:
            px = YahooProvider().bars(syms[0], days=5)["close"].iloc[-1]
            line("OK", f"Live {kind}: {syms[0]} {px:,.4f}")
    if "options" in assets:
        n = len(yf.Ticker(settings.watchlist[0]).options or [])
        line("OK" if n else "WARN", f"Option chains: {n} expiry dates for {settings.watchlist[0]}",
             "" if n else "Options would be priced with a model instead of live quotes")


def main() -> int:
    import logging
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # its own retry chatter isn't useful here
    print("Prop Desk Arena - pre-flight check\n")

    v = sys.version_info
    if v >= (3, 10):
        line("OK", f"Python {v.major}.{v.minor}")
    else:
        line("FAIL", f"Python {v.major}.{v.minor} is too old", "Install Python 3.12 from python.org")
        return 1

    missing = []
    for mod in ("anthropic", "claude_agent_sdk", "fastapi", "uvicorn", "pandas", "numpy", "pydantic", "yfinance"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        line("FAIL", f"Missing packages: {', '.join(missing)}", "Run: pip install -r prop_desk/requirements.txt")
        return 1
    line("OK", "Python packages installed")

    try:
        from zoneinfo import ZoneInfo
        ZoneInfo("America/New_York")
        line("OK", "Timezone data (US-Eastern schedule)")
    except Exception:
        line("FAIL", "No timezone data", "Run: pip install tzdata")

    from .config import ENV_FILE, settings
    import os
    line("OK" if os.path.exists(ENV_FILE) else "WARN", f"Settings file: {ENV_FILE}",
         "" if os.path.exists(ENV_FILE) else "Copy prop_desk/arena.env.example to prop_desk/arena.env and edit it")
    print(f"       starting cash ${settings.starting_cash:g} per faction, {settings.season_days}-day seasons")
    print("       factions: " + ", ".join(f"{f} ({settings.model_for(f)})" for f in settings.factions))
    if settings.schedule_et:
        try:
            times = [tuple(map(int, t.split(":"))) for t in settings.schedule_et]
            assert all(0 <= h < 24 and 0 <= m < 60 for h, m in times)
            line("OK", f"Schedule (US-Eastern, weekdays): {', '.join(settings.schedule_et)}")
        except Exception:
            line("FAIL", f"Can't read PROP_DESK_SCHEDULE_ET={','.join(settings.schedule_et)}",
                 'Use 24-hour times separated by commas, e.g. 09:45,15:30')
    elif settings.autorun_minutes:
        line("OK", f"Runs every {settings.autorun_minutes} minutes during market hours")
    else:
        line("WARN", "No schedule set - rounds only run when you press R",
             "Set PROP_DESK_SCHEDULE_ET in arena.env, e.g. 09:45,15:30")

    from .agents.brain import detect_backend, has_api_key, plan_logged_in
    backend = detect_backend(settings.ai_mode)
    if backend == "plan":
        line("OK", "Claude: using your Claude plan login (Agent SDK)")
    elif backend == "api":
        line("OK", "Claude: using an API key")
    elif settings.ai_mode == "rules":
        line("WARN", "Claude is switched off (PROP_DESK_AI=rules) - factions use built-in rules only")
    else:
        line("FAIL", "Claude: no login and no API key found",
             "Install Claude Code and run: claude auth login   (or set ANTHROPIC_API_KEY in arena.env)")
    if backend == "plan" and not plan_logged_in():
        line("FAIL", "PROP_DESK_AI=plan but Claude Code isn't signed in", "Run: claude auth login")
    if backend == "api" and not has_api_key():
        line("FAIL", "PROP_DESK_AI=api but no API key found", "Set ANTHROPIC_API_KEY in arena.env")

    try:
        from .market_data import YahooProvider
        bars = YahooProvider().bars(settings.benchmark, days=5)
        line("OK", f"Live stock data: {settings.benchmark} last close ${bars['close'].iloc[-1]:,.2f}")
        yahoo_ok = True
    except Exception as e:
        yahoo_ok = False
        line("FAIL", f"Can't reach Yahoo Finance ({type(e).__name__})",
             "Check the internet connection, firewall or antivirus; the app would fall back to fake prices")

    print(f"       trading: {', '.join(settings.assets)}")
    if yahoo_ok:
        try:
            extra_checks(settings)
        except Exception as e:
            line("WARN", f"Couldn't check the extra instruments ({type(e).__name__})",
                 "Crypto, currencies and options may fall back to simulated prices")

    if "syndicate" in settings.factions:
        try:
            from .factions.syndicate import fetch_breadth
            b = fetch_breadth()
            line("OK", f"S&P 500 breadth (for the Syndicate): {b['raw']:.0%} above the 200-day on {b['date']}")
        except Exception as e:
            line("WARN", f"Can't reach the breadth data ({type(e).__name__})",
                 "The Syndicate's Lookout would read the market from SPY alone. Check access to tradermonty.github.io")

    if settings.kalshi_enabled:
        try:
            from .kalshi import KalshiProvider
            n = len(KalshiProvider().markets(limit=5))
            line("OK", f"Live Kalshi data: {n} liquid markets found")
        except Exception as e:
            line("WARN", f"Can't reach Kalshi ({type(e).__name__})",
                 "Prediction markets would use simulated prices. Check the connection, or set PROP_DESK_KALSHI=false")

    print()
    if "FAIL" in RESULTS:
        print("Not ready yet - fix the [FAIL] lines above, then run this check again.")
        return 1
    print("Ready. Start the Arena with the launcher (run_arena.bat on Windows, ./run_arena.sh on Mac/Linux).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
