"""What the factions are allowed to trade.

  stocks  - the core watchlist of large caps
  lively  - fast-moving stocks that still trade heavily (found with Yahoo's screeners):
            listed on a major exchange, at least $2 a share and $5M traded a day.
            No penny stocks, nothing over-the-counter.
  crypto  - major coins (BTC, ETH...), priced in USD
  forex   - major currency pairs quoted in USD (EUR/USD, GBP/USD...), so P&L is in dollars
Options are not a separate universe: they're bought on the stocks above (see options.py).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

CRYPTO = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD"]
FOREX = ["EURUSD=X", "GBPUSD=X", "AUDUSD=X", "NZDUSD=X"]
# Used when the live screener isn't reachable: liquid, volatile, exchange-listed names.
LIVELY_FALLBACK = ["PLTR", "SOFI", "HOOD", "RIVN", "COIN", "MARA", "AFRM", "RKLB", "IONQ", "UPST", "SNAP", "DKNG"]
_LISTED = {"NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS", "NAS", "NYS"}  # Nasdaq, NYSE, NYSE American/Arca, Cboe


def asset_class(symbol: str) -> str:
    if symbol.endswith("=X"):
        return "forex"
    if symbol.endswith("-USD"):
        return "crypto"
    return "stock"


def display(symbol: str) -> str:
    """BTC-USD -> BTC, EURUSD=X -> EUR/USD."""
    if symbol.endswith("=X"):
        pair = symbol[:-2]
        return f"{pair[:3]}/{pair[3:]}"
    if symbol.endswith("-USD"):
        return symbol[:-4]
    return symbol


def screen_lively(n: int, min_price: float, min_dollar_volume: float, exclude: set[str]) -> list[str]:
    """Liquid fast movers from Yahoo's predefined screeners."""
    import yfinance as yf

    picks: list[str] = []
    for query in ("most_actives", "day_gainers", "small_cap_gainers", "aggressive_small_caps"):
        try:
            quotes = yf.screen(query, count=50).get("quotes", [])
        except Exception as e:
            log.warning("screener %s failed: %s", query, e)
            continue
        for q in quotes:
            sym, price = q.get("symbol", ""), q.get("regularMarketPrice") or 0
            dollar_vol = price * (q.get("averageDailyVolume3Month") or q.get("regularMarketVolume") or 0)
            if (q.get("quoteType") == "EQUITY" and q.get("exchange") in _LISTED and price >= min_price
                    and dollar_vol >= min_dollar_volume and sym not in exclude and sym not in picks
                    and "." not in sym):
                picks.append(sym)
        if len(picks) >= n:
            break
    return picks[:n]


def build(settings, provider_name: str) -> dict[str, str]:
    """{symbol: lane} for this session. Lane is stock | lively | crypto | forex."""
    lanes: dict[str, str] = {}
    assets = set(settings.assets)
    if "stocks" in assets:
        lanes.update({s: "stock" for s in settings.watchlist})
    if "lively" in assets and settings.lively_count > 0:
        found = []
        if provider_name == "yahoo":
            found = screen_lively(settings.lively_count, settings.lively_min_price,
                                  settings.lively_min_dollar_volume, set(lanes))
        if not found:
            found = [s for s in LIVELY_FALLBACK if s not in lanes][: settings.lively_count]
        lanes.update({s: "lively" for s in found})
    if "crypto" in assets:
        lanes.update({s: "crypto" for s in settings.crypto})
    if "forex" in assets:
        lanes.update({s: "forex" for s in settings.forex})
    return lanes
