"""Who works on the desk.

Each trader has:
  * a Claude system prompt describing their discipline, and
  * a rule-based `heuristic` used when Claude isn't connected (offline/demo),
    which also documents in code what the discipline looks at.

To add a trader: write a heuristic, add a Persona to TRADERS. That's it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..market_data import Snapshot

# heuristic(features, snapshot) -> (score in [-1, 1], signals, concerns)
Heuristic = Callable[[dict, Snapshot], tuple[float, list[str], list[str]]]


@dataclass
class Persona:
    id: str
    name: str
    role: str
    kind: str  # researcher | trader | risk | chair
    color: str
    initials: str
    bio: str
    sees: list[str] = field(default_factory=list)  # which feature groups they get
    weight: float = 1.0
    heuristic: Heuristic | None = None
    # Which kind of evidence the trader uses. A trade needs agreement from at least
    # two different families, so three price-chart traders can't approve it alone.
    family: str = ""
    look: str = ""  # character accessory drawn in the GUI
    faction: str = "legion"  # legion | ascendant | brood | neutral
    style: str = "human"  # how the GUI draws them: human | seer | drone | queen

    def public(self) -> dict:
        keys = ("id", "name", "role", "kind", "color", "initials", "bio", "weight", "family", "look", "faction", "style")
        return {k: getattr(self, k) for k in keys}


SHARED_RULES = """You sit on the trading floor of a small proprietary trading firm. Researchers
pitch trade ideas; five specialist traders vote; a risk manager sizes the trade;
the trade is only placed if the floor reaches consensus.

The desk trades stocks (including fast-moving smaller names), crypto (symbols like BTC-USD,
in US dollars) and currency pairs (symbols like EURUSD=X: the price of one euro in dollars).
Every view you get says which asset class it is. If your discipline doesn't apply to the asset -
company fundamentals for a coin or a currency, for example - PASS and say so.

How you work:
- Judge the pitch ONLY through your own discipline. Other traders cover the other angles;
  the firm wants independent views, not five copies of the same opinion.
- Use only the data you are given. Never invent prices, figures, or news.
- PASS is a respectable answer when your discipline has no edge here.
- Conviction is 0-100: 80+ means you would put your own book on it; under 40 means weak.
- Keep "headline" to one punchy sentence, the way you'd say it across the desk.
- Keep "reasoning" to 2-4 sentences and cite the specific numbers that drive your view."""


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


# ---------------------------------------------------------------- heuristics

def fundamental_view(f: dict, s: Snapshot):
    d = s.fundamentals or {}
    score, sig, con = 0.0, [], []

    def chk(cond, pts, good, bad):
        nonlocal score
        if cond is None:
            return
        if cond:
            score += pts
            sig.append(good)
        else:
            score -= pts * 0.6
            con.append(bad)

    g = lambda k: d.get(k)  # noqa: E731
    chk(None if g("peg") is None else g("peg") < 1.5, 0.25, f"PEG {g('peg')} is reasonable", f"PEG {g('peg')} is rich")
    chk(None if g("revenue_growth_pct") is None else g("revenue_growth_pct") > 8, 0.2,
        f"revenue growing {g('revenue_growth_pct')}%", f"revenue growth only {g('revenue_growth_pct')}%")
    chk(None if g("profit_margin_pct") is None else g("profit_margin_pct") > 15, 0.15,
        f"{g('profit_margin_pct')}% net margin", f"thin {g('profit_margin_pct')}% margin")
    chk(None if g("roe_pct") is None else g("roe_pct") > 15, 0.15, f"ROE {g('roe_pct')}%", f"ROE just {g('roe_pct')}%")
    chk(None if g("debt_to_equity") is None else g("debt_to_equity") < 120, 0.1,
        f"manageable leverage (D/E {g('debt_to_equity')})", f"heavy leverage (D/E {g('debt_to_equity')})")
    chk(None if g("fcf_yield_pct") is None else g("fcf_yield_pct") > 2.5, 0.15,
        f"{g('fcf_yield_pct')}% FCF yield", f"weak {g('fcf_yield_pct')}% FCF yield")
    if g("pe") and g("forward_pe") and g("forward_pe") < g("pe") * 0.85:
        score += 0.1
        sig.append(f"forward P/E {g('forward_pe')} well below trailing {g('pe')} - earnings expected to grow")
    return _clip(score), sig, con


def momentum_view(f: dict, s: Snapshot):
    m = f["momentum"]
    score, sig, con = 0.0, [], []
    for k, w, label in (("return_3m_pct", 0.25, "3-month"), ("return_6m_pct", 0.2, "6-month")):
        v = m.get(k)
        if v is not None:
            score += w if v > 0 else -w
            (sig if v > 0 else con).append(f"{label} return {v:+.1f}%")
    if m["price_vs_sma50_pct"] > 0:
        score += 0.15
        sig.append(f"price {m['price_vs_sma50_pct']:+.1f}% vs 50-day average")
    else:
        score -= 0.15
        con.append(f"below 50-day average ({m['price_vs_sma50_pct']:+.1f}%)")
    score += 0.1 if m["macd_above_signal"] else -0.1
    (sig if m["macd_above_signal"] else con).append("MACD " + ("above" if m["macd_above_signal"] else "below") + " signal")
    rsi = m["rsi14"]
    if rsi > 75:
        score -= 0.15
        con.append(f"RSI {rsi} - stretched, chasing here is risky")
    elif rsi > 50:
        score += 0.1
        sig.append(f"RSI {rsi} in the bullish zone")
    elif rsi < 30:
        con.append(f"RSI {rsi} - oversold, momentum is broken")
    if m["pct_from_52w_high"] > -5:
        score += 0.15
        sig.append(f"within {abs(m['pct_from_52w_high']):.1f}% of 52-week high")
    return _clip(score), sig, con


def cloud_view(f: dict, s: Snapshot):
    i = f["ichimoku"]
    score, sig, con = 0.0, [], []
    pos = i["price_vs_cloud"]
    if pos == "inside":
        con.append("price is inside the cloud - no trend, no trade")
        return 0.0, sig, con
    sign = 1 if pos == "above" else -1
    score += 0.35 * sign
    (sig if sign > 0 else con).append(f"price {pos} the cloud ({i['cloud_bottom']}-{i['cloud_top']})")
    for cond, w, label in (
        (i["future_cloud"] == "bullish", 0.2, f"future cloud is {i['future_cloud']}"),
        (i["tenkan_above_kijun"], 0.2, "Tenkan " + ("above" if i["tenkan_above_kijun"] else "below") + " Kijun"),
        (i["chikou_above_price_26_ago"], 0.15, "Chikou " + ("clear of" if i["chikou_above_price_26_ago"] else "under") + " past price"),
    ):
        score += w if cond else -w
        (sig if cond == (sign > 0) else con).append(label)
    if i["tk_cross_last_3_bars"] != "none":
        score += 0.15 if i["tk_cross_last_3_bars"] == "bullish" else -0.15
        sig.append(f"fresh {i['tk_cross_last_3_bars']} TK cross")
    if i["cloud_thickness_pct"] is not None and i["cloud_thickness_pct"] < 1:
        con.append("thin cloud - weak support/resistance")
        score *= 0.8
    return _clip(score), sig, con


def money_flow_view(f: dict, s: Snapshot):
    m = f["money_flow"]
    score, sig, con = 0.0, [], []
    mfi = m["mfi14"]
    if mfi is not None:
        if mfi > 80:
            score -= 0.15
            con.append(f"MFI {mfi} - overbought, distribution risk")
        elif mfi > 55:
            score += 0.2
            sig.append(f"MFI {mfi} - money flowing in")
        elif mfi < 40:
            score -= 0.2
            con.append(f"MFI {mfi} - money flowing out")
    if m["cmf20"] is not None:
        score += 0.2 if m["cmf20"] > 0.05 else -0.2 if m["cmf20"] < -0.05 else 0
        if abs(m["cmf20"]) > 0.05:
            (sig if m["cmf20"] > 0 else con).append(f"Chaikin money flow {m['cmf20']:+.2f}")
    if m["obv_trend_20d"] is not None:
        score += 0.15 if m["obv_trend_20d"] > 0.1 else -0.15 if m["obv_trend_20d"] < -0.1 else 0
        if abs(m["obv_trend_20d"]) > 0.1:
            (sig if m["obv_trend_20d"] > 0 else con).append("OBV " + ("accumulating" if m["obv_trend_20d"] > 0 else "distributing"))
    if m["up_down_volume_ratio_20d"]:
        r = m["up_down_volume_ratio_20d"]
        if r > 1.2:
            score += 0.15
            sig.append(f"up-volume {r}x down-volume")
        elif r < 0.8:
            score -= 0.15
            con.append(f"down-volume dominates ({r}x)")
    rs = m["relative_strength_vs_spy_3m_pct"]
    if rs is not None:
        score += 0.25 if rs > 0 else -0.25
        (sig if rs > 0 else con).append(f"{rs:+.1f}% vs SPY over 3 months")
    return _clip(score), sig, con


_POS = ("beat", "beats", "raise", "raises", "upgrade", "buyback", "wins", "record", "surge", "approval", "strong",
        "rallies", "lifts", "inflows", "adds")
_NEG = ("miss", "misses", "cut", "cuts", "downgrade", "probe", "lawsuit", "short seller", "recall", "weak", "plunge", "investigation",
        "hack", "slides", "falls", "sell-off")


def news_view(f: dict, s: Snapshot):
    score, sig, con = 0.0, [], []
    for h in s.news[:8]:
        t = h.title.lower()
        p = sum(w in t for w in _POS)
        n = sum(w in t for w in _NEG)
        if p > n:
            score += 0.3
            sig.append(h.title)
        elif n > p:
            score -= 0.3
            con.append(h.title)
    if not s.news:
        con.append("no fresh headlines - nothing to trade on")
    return _clip(score), sig, con


# ---------------------------------------------------------------- the desk

RESEARCHERS = [
    Persona("scout", "Priya Nair", "Quant Researcher", "researcher", "#38bdf8", "PN",
            "Hunts the watchlist every session and pitches the names with the strongest setups.",
            sees=["momentum", "ichimoku", "money_flow", "volatility", "fundamentals"], look="goggles"),
    Persona("newshound", "Leo Brandt", "News Researcher", "researcher", "#a78bfa", "LB",
            "Reads the tape and the wires; briefs the floor on what's moving each name.",
            sees=["news"], look="newspaper"),
    Persona("bear", "Bruno Kodiak", "Devil's Advocate", "researcher", "#c08457", "BK",
            "Paid to hate every idea. Makes the strongest case against each pitch before anyone votes.",
            sees=["momentum", "ichimoku", "money_flow", "volatility", "fundamentals", "news"], look="bear"),
    Persona("oracle", "Nova Lin", "Prediction Markets Researcher", "researcher", "#2dd4bf", "NL",
            "When stocks offer nothing, she scans Kalshi for markets the crowd has mispriced.",
            look="visor"),
]

TRADERS = [
    Persona("fundamental", "Ruth Okafor", "Fundamental Trader", "trader", "#34d399", "RO",
            "Valuation, growth, margins, balance sheet. Won't buy a story she can't see in the numbers.",
            sees=["fundamentals"], heuristic=fundamental_view, family="fundamental", look="glasses"),
    Persona("momentum", "Dash Reyes", "Momentum Trader", "trader", "#f59e0b", "DR",
            "Buys strength, sells weakness. Trend, RSI, MACD, 52-week highs.",
            sees=["momentum"], heuristic=momentum_view, family="price", look="headband"),
    Persona("cloud", "Kenji Sato", "Ichimoku Cloud Trader", "trader", "#f472b6", "KS",
            "Trades only what the Ichimoku cloud confirms: price vs cloud, Tenkan/Kijun, Chikou.",
            sees=["ichimoku"], heuristic=cloud_view, family="price", look="cloudhat"),
    Persona("flow", "Vivian Cole", "Money Flow & Strength Trader", "trader", "#60a5fa", "VC",
            "Follows the money: MFI, Chaikin flow, OBV, volume, relative strength vs the market.",
            sees=["money_flow"], heuristic=money_flow_view, family="price", look="badge"),
    Persona("news", "Max Byline", "News & Catalyst Trader", "trader", "#fb7185", "MB",
            "Trades headlines and catalysts. Fast to act, fast to fold if the story changes.",
            sees=["news"], heuristic=news_view, weight=0.8, family="news", look="headset"),
]

RISK = Persona("risk", "Ingrid Hale", "Quartermaster (Risk)", "risk", "#facc15", "IH",
               "Neutral referee. Sizes every faction's trades with the same hard limits, enforced in code.",
               sees=["volatility"], look="helmet", faction="neutral")

CHAIR = Persona("chair", "Sam Whitford", "Commander, Iron Legion", "chair", "#e5e7eb", "SW",
                "Chairs the Council, calls the vote, and writes the desk memo.", look="crown")

LEGION = RESEARCHERS + TRADERS + [CHAIR]

# ---------------------------------------------------------------- The Ascendant
# Hyper-advanced: every seer is a quantitative model computed in code; the Exarch
# pools their probabilities. (Models live in factions/ascendant.py.)
ASCENDANT = [
    Persona("vael", "Seer Vael", "Analog Forecaster", "trader", "#fde047", "VA",
            "Finds the past moments that looked most like today and asks what happened next.",
            faction="ascendant", style="seer", look="halo"),
    Persona("kyth", "Augur Kyth", "Regime Augur", "trader", "#67e8f9", "KY",
            "Reads the whole market's tide - trend and breadth - and how strongly this stock rides it.",
            faction="ascendant", style="seer", look="halo"),
    Persona("oriel", "Prism Oriel", "Volatility Prism", "trader", "#c4b5fd", "OR",
            "Only respects returns that are large relative to their noise.",
            faction="ascendant", style="seer", look="halo"),
    Persona("lumen", "Lumen Sar", "Cross-Sectional Ranker", "trader", "#fdba74", "LS",
            "Ranks every stock against every other on momentum, value and quality.",
            faction="ascendant", style="seer", look="halo"),
    Persona("thalor", "Exarch Thalor", "Exarch of the Convergence", "chair", "#fbbf24", "ET",
            "Pools the seers' probabilities into one. Acts only when the Convergence is clear.",
            faction="ascendant", style="seer", look="crown"),
]

# ---------------------------------------------------------------- The Brood
# Hive mind: two dozen single-signal drones vote independently; the Queen merges them.
# Drones that are wrong more often lose influence - the swarm evolves.
DRONES = [
    ("d_rsi_bounce", "Bounce Drone", "buys oversold RSI"), ("d_rsi_trend", "Pulse Drone", "RSI in the bull zone"),
    ("d_macd", "Cross Drone", "MACD above signal"), ("d_sma20", "Skitter Drone", "above 20-day average"),
    ("d_sma50", "Crawler Drone", "above 50-day average"), ("d_golden", "Gilded Drone", "50-day above 200-day"),
    ("d_breakout", "Breach Drone", "new 20-day high"), ("d_breakdown", "Sink Drone", "new 20-day low"),
    ("d_52w", "Apex Drone", "near 52-week high"), ("d_gap", "Leap Drone", "gapped up today"),
    ("d_volume", "Swell Drone", "volume surge on an up day"), ("d_obv", "Burrow Drone", "OBV accumulating"),
    ("d_cmf", "Siphon Drone", "Chaikin money flow positive"), ("d_mfi", "Gorge Drone", "MFI money inflow"),
    ("d_cloud", "Mist Drone", "above the Ichimoku cloud"), ("d_tk", "Fang Drone", "Tenkan above Kijun"),
    ("d_rs", "Stalker Drone", "beating SPY"), ("d_reversal", "Snap Drone", "fades sharp 5-day drops"),
    ("d_higher_lows", "Climber Drone", "higher lows"), ("d_squeeze", "Coil Drone", "volatility squeeze"),
    ("d_value", "Grazer Drone", "cheap on PEG"), ("d_growth", "Brood-Feeder Drone", "earnings growing"),
    ("d_news", "Whisper Drone", "headline sentiment"), ("d_trend3m", "Tide Drone", "3-month uptrend"),
]
_DRONE_COLORS = ["#d946ef", "#a855f7", "#c026d3", "#e879f9", "#9333ea", "#f0abfc"]
BROOD = [Persona(i, n, "Drone · " + d, "trader", _DRONE_COLORS[k % 6], n[:2].upper(),
                 f"A single-minded drone: it only knows '{d}'.", faction="brood", style="drone")
         for k, (i, n, d) in enumerate(DRONES)] + [
    Persona("vexa", "Hive Queen Vexa", "Queen of the Brood", "chair", "#f472b6", "HQ",
            "Feels every drone at once. Many small signals, one will.", faction="brood", style="queen"),
]

ALL = LEGION + ASCENDANT + BROOD + [RISK]
BY_ID = {p.id: p for p in ALL}

DISCIPLINE_PROMPTS = {
    "fundamental": "You are a fundamental trader. You care about valuation (P/E, forward P/E, PEG), growth, margins, return on equity, leverage and free cash flow. Price charts are not your job.",
    "momentum": "You are a momentum trader. You buy strength and short weakness using trailing returns, moving averages, RSI, MACD and proximity to 52-week highs/lows. You avoid chasing extremely overbought moves.",
    "cloud": "You are an Ichimoku Kinko Hyo (cloud) trader. You only take trades the cloud confirms: price relative to the Kumo, future cloud color, Tenkan/Kijun relationship and crosses, and the Chikou span. Price inside the cloud means no trade.",
    "flow": "You are a money-flow and relative-strength trader. You read MFI, Chaikin Money Flow, OBV trend, up/down volume and performance versus SPY to judge whether institutions are accumulating or distributing.",
    "news": "You are a news and catalyst trader. You trade on headlines, earnings, guidance, analyst actions and regulatory events. Stale or absent news means no edge. You think about whether news is already priced in.",
}


CONTRACT_PROMPTS = {
    "fundamental": "For prediction markets you are the base-rate analyst: start from how often events like this happen historically and the hard data on the underlying (economic releases, prices, schedules).",
    "momentum": "For prediction markets you read how the contract's own price has been moving - crowds that are repricing quickly often keep going - and whether the move is overdone.",
    "cloud": "You trade Ichimoku charts. A prediction-market contract has no meaningful cloud. Unless the market is about an asset's price and the chart data clearly answers it, PASS and say why.",
    "flow": "For prediction markets you read liquidity: volume, open interest and the bid/ask spread. Thin markets can be badly mispriced but are hard to trust; deep markets are usually efficient.",
    "news": "For prediction markets you weigh the latest news and scheduled catalysts that decide the outcome, and whether the price already reflects them.",
}


# ---------------------------------------------------------------- prediction-market heuristics
# Each returns (probability_yes or None to pass, signals, concerns).

def contract_base_rate(c):
    # Favourite-longshot bias: long shots tend to be overpriced, favourites underpriced.
    if c.implied <= 15:
        return c.implied - 5, [f"long shot at {c.implied}¢ - long shots are usually overpriced"], []
    if c.implied >= 85:
        return min(99, c.implied + 4), [f"heavy favourite at {c.implied}¢ - favourites tend to be underpriced"], []
    return None, [], ["no base-rate edge in the middle of the range"]


def contract_momentum(c):
    move = c.last_price - c.previous_price
    if abs(move) < 6:
        return None, [], [f"price barely moved ({move:+d}¢)"]
    est = c.implied + (4 if move > 0 else -4)
    return max(1, min(99, est)), [f"contract repricing {move:+d}¢ - crowd still catching up"], []


def contract_cloud(c):
    return None, [], ["no chart, no cloud, no trade"]


def contract_flow(c):
    spread = c.yes_ask - c.yes_bid
    if c.volume < 20000 or spread > 4:
        return None, [], [f"thin market (volume {c.volume:,}, spread {spread}¢) - can't trust the price"]
    return c.implied, [f"deep market (volume {c.volume:,}) - price is probably fair"], []


def contract_news(c):
    return None, [], ["no headlines on this one in rules mode"]


CONTRACT_HEURISTICS = {"fundamental": contract_base_rate, "momentum": contract_momentum,
                       "cloud": contract_cloud, "flow": contract_flow, "news": contract_news}
