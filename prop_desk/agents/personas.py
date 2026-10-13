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

    def public(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "name", "role", "kind", "color", "initials", "bio", "weight")}


SHARED_RULES = """You sit on the trading floor of a small proprietary trading firm. Researchers
pitch trade ideas; five specialist traders vote; a risk manager sizes the trade;
the trade is only placed if the floor reaches consensus.

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


_POS = ("beat", "beats", "raise", "raises", "upgrade", "buyback", "wins", "record", "surge", "approval", "strong")
_NEG = ("miss", "misses", "cut", "cuts", "downgrade", "probe", "lawsuit", "short seller", "recall", "weak", "plunge", "investigation")


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
            "Screens the watchlist every session and pitches the names with the strongest setups.",
            sees=["momentum", "ichimoku", "money_flow", "volatility", "fundamentals"]),
    Persona("newshound", "Leo Brandt", "News Researcher", "researcher", "#a78bfa", "LB",
            "Reads the tape and the wires; briefs the floor on what's moving each name.",
            sees=["news"]),
]

TRADERS = [
    Persona("fundamental", "Ruth Okafor", "Fundamental Trader", "trader", "#34d399", "RO",
            "Valuation, growth, margins, balance sheet. Won't buy a story she can't see in the numbers.",
            sees=["fundamentals"], heuristic=fundamental_view),
    Persona("momentum", "Dash Reyes", "Momentum Trader", "trader", "#f59e0b", "DR",
            "Buys strength, sells weakness. Trend, RSI, MACD, 52-week highs.",
            sees=["momentum"], heuristic=momentum_view),
    Persona("cloud", "Kenji Sato", "Ichimoku Cloud Trader", "trader", "#f472b6", "KS",
            "Trades only what the Ichimoku cloud confirms: price vs cloud, Tenkan/Kijun, Chikou.",
            sees=["ichimoku"], heuristic=cloud_view),
    Persona("flow", "Vivian Cole", "Money Flow & Strength Trader", "trader", "#60a5fa", "VC",
            "Follows the money: MFI, Chaikin flow, OBV, volume, relative strength vs the market.",
            sees=["money_flow"], heuristic=money_flow_view),
    Persona("news", "Max Byline", "News & Catalyst Trader", "trader", "#fb7185", "MB",
            "Trades headlines and catalysts. Fast to act, fast to fold if the story changes.",
            sees=["news"], heuristic=news_view, weight=0.8),
]

RISK = Persona("risk", "Ingrid Hale", "Risk Manager", "risk", "#facc15", "IH",
               "Sizes every trade and has a hard veto. Her limits are enforced in code.",
               sees=["volatility"])

CHAIR = Persona("chair", "Sam Whitford", "Head of Desk", "chair", "#e5e7eb", "SW",
                "Runs the meeting, calls the vote, and writes the desk memo.")

ALL = RESEARCHERS + TRADERS + [RISK, CHAIR]
BY_ID = {p.id: p for p in ALL}

DISCIPLINE_PROMPTS = {
    "fundamental": "You are a fundamental trader. You care about valuation (P/E, forward P/E, PEG), growth, margins, return on equity, leverage and free cash flow. Price charts are not your job.",
    "momentum": "You are a momentum trader. You buy strength and short weakness using trailing returns, moving averages, RSI, MACD and proximity to 52-week highs/lows. You avoid chasing extremely overbought moves.",
    "cloud": "You are an Ichimoku Kinko Hyo (cloud) trader. You only take trades the cloud confirms: price relative to the Kumo, future cloud color, Tenkan/Kijun relationship and crosses, and the Chikou span. Price inside the cloud means no trade.",
    "flow": "You are a money-flow and relative-strength trader. You read MFI, Chaikin Money Flow, OBV trend, up/down volume and performance versus SPY to judge whether institutions are accumulating or distributing.",
    "news": "You are a news and catalyst trader. You trade on headlines, earnings, guidance, analyst actions and regulatory events. Stale or absent news means no edge. You think about whether news is already priced in.",
}
