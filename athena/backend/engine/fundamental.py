"""
ATHENA AI — Fundamental Analysis Engine
Sources:
- ForexFactory RSS  → Calendrier économique (requests sync)
- FXStreet RSS      → News Forex temps réel (requests sync)
- Scoring sentiment → Basé sur mots-clés bullish/bearish + surprises économiques
"""
import asyncio
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

FOREX_FACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
FXSTREET_RSS_URL  = "https://www.fxstreet.com/rss/news"

SYMBOL_CURRENCIES = {
    "EURUSD": ["EUR", "USD"], "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"], "USDCHF": ["USD", "CHF"],
    "AUDUSD": ["AUD", "USD"], "NZDUSD": ["NZD", "USD"],
    "USDCAD": ["USD", "CAD"], "GBPJPY": ["GBP", "JPY"],
    "EURJPY": ["EUR", "JPY"], "EURGBP": ["EUR", "GBP"],
    "BTC/USDT": ["USD"], "ETH/USDT": ["USD"],
    "BNB/USDT": ["USD"], "SOL/USDT": ["USD"], "XRP/USDT": ["USD"],
    "SPY": ["USD"], "QQQ": ["USD"], "DIA": ["USD"],
    "IWM": ["USD"], "EWG": ["EUR"],
    "GC=F": ["USD"], "CL=F": ["USD"], "SI=F": ["USD"],
}

# Keywords for simple sentiment scoring
# ─────────────────────────────────────────────────────────────────────────────
# SEASONALITY MAP — historical monthly bias per asset
# Source: multi-year statistical averages (forex seasonality research)
# bias: "bullish" | "bearish" | "neutral"  /  strength: "fort" | "modéré" | "faible"
# ─────────────────────────────────────────────────────────────────────────────

SEASONALITY_MAP: Dict[str, Dict[int, Dict[str, str]]] = {
    "EURUSD": {
        1:  {"bias": "bullish",  "strength": "modéré",  "note": "Janvier : USD faible post-fêtes, EUR/USD hausse en moyenne"},
        2:  {"bias": "neutral",  "strength": "faible",  "note": "Février : saisonnalité neutre sur EUR/USD"},
        3:  {"bias": "bearish",  "strength": "modéré",  "note": "Mars : USD tend à se renforcer, pression baissière sur EUR/USD"},
        4:  {"bias": "neutral",  "strength": "faible",  "note": "Avril : mois mixte sur EUR/USD"},
        5:  {"bias": "bearish",  "strength": "faible",  "note": "Mai : 'Sell in May' affecte légèrement EUR/USD"},
        6:  {"bias": "bearish",  "strength": "modéré",  "note": "Juin : Risk-off estival, USD fort historiquement"},
        7:  {"bias": "neutral",  "strength": "faible",  "note": "Juillet : volumes réduits, saisonnalité neutre"},
        8:  {"bias": "neutral",  "strength": "faible",  "note": "Août : faible liquidité, mouvements erratiques"},
        9:  {"bias": "bearish",  "strength": "modéré",  "note": "Septembre : USD fort historiquement ce mois"},
        10: {"bias": "neutral",  "strength": "faible",  "note": "Octobre : début de retournement possible"},
        11: {"bias": "bullish",  "strength": "modéré",  "note": "Novembre : rebond EUR/USD historiquement"},
        12: {"bias": "neutral",  "strength": "faible",  "note": "Décembre : thin markets, faible signal saisonnier"},
    },
    "GBPUSD": {
        1:  {"bias": "bullish",  "strength": "modéré",  "note": "Janvier : GBP/USD hausse historiquement en début d'année"},
        2:  {"bias": "neutral",  "strength": "faible",  "note": "Février : saisonnalité neutre"},
        3:  {"bias": "bearish",  "strength": "faible",  "note": "Mars : légère pression baissière sur GBP"},
        4:  {"bias": "bullish",  "strength": "faible",  "note": "Avril : rebond GBP fréquent"},
        5:  {"bias": "bearish",  "strength": "faible",  "note": "Mai : 'Sell in May' légère pression"},
        6:  {"bias": "bearish",  "strength": "modéré",  "note": "Juin : USD tend à dominer"},
        7:  {"bias": "neutral",  "strength": "faible",  "note": "Juillet : été calme"},
        8:  {"bias": "neutral",  "strength": "faible",  "note": "Août : faible liquidité"},
        9:  {"bias": "bearish",  "strength": "modéré",  "note": "Septembre : USD fort, GBP/USD sous pression"},
        10: {"bias": "neutral",  "strength": "faible",  "note": "Octobre : volatilité accrue"},
        11: {"bias": "bullish",  "strength": "faible",  "note": "Novembre : reprise GBP modérée"},
        12: {"bias": "neutral",  "strength": "faible",  "note": "Décembre : fin d'année, thin market"},
    },
    "USDJPY": {
        1:  {"bias": "bearish",  "strength": "modéré",  "note": "Janvier : JPY fort (rapatriement fin d'année fiscale)"},
        2:  {"bias": "neutral",  "strength": "faible",  "note": "Février : neutre"},
        3:  {"bias": "neutral",  "strength": "faible",  "note": "Mars : fin d'année fiscale japonaise, JPY volatile"},
        4:  {"bias": "bullish",  "strength": "modéré",  "note": "Avril : début année fiscale, flux sortants du Japon"},
        5:  {"bias": "bullish",  "strength": "faible",  "note": "Mai : USD/JPY tend à monter"},
        6:  {"bias": "neutral",  "strength": "faible",  "note": "Juin : neutre"},
        7:  {"bias": "neutral",  "strength": "faible",  "note": "Juillet : saison estivale calme"},
        8:  {"bias": "bearish",  "strength": "modéré",  "note": "Août : risk-off estival, JPY safe haven fort"},
        9:  {"bias": "bullish",  "strength": "modéré",  "note": "Septembre : USD fort historiquement"},
        10: {"bias": "neutral",  "strength": "faible",  "note": "Octobre : neutre"},
        11: {"bias": "bullish",  "strength": "faible",  "note": "Novembre : USD fort Q4"},
        12: {"bias": "neutral",  "strength": "faible",  "note": "Décembre : volatilité fin d'année"},
    },
    "AUDUSD": {
        1:  {"bias": "bullish",  "strength": "modéré",  "note": "Janvier : AUD/USD historiquement haussier (appétit au risque)"},
        2:  {"bias": "neutral",  "strength": "faible",  "note": "Février : neutre"},
        3:  {"bias": "bearish",  "strength": "faible",  "note": "Mars : USD se renforce"},
        4:  {"bias": "bullish",  "strength": "faible",  "note": "Avril : rebond commodity currencies"},
        5:  {"bias": "bearish",  "strength": "modéré",  "note": "Mai : risk-off, AUD affecté"},
        6:  {"bias": "bearish",  "strength": "modéré",  "note": "Juin : pression baissière saisonnière"},
        7:  {"bias": "neutral",  "strength": "faible",  "note": "Juillet : été calme"},
        8:  {"bias": "bearish",  "strength": "faible",  "note": "Août : risque faible"},
        9:  {"bias": "bearish",  "strength": "modéré",  "note": "Septembre : USD fort, AUD sous pression"},
        10: {"bias": "neutral",  "strength": "faible",  "note": "Octobre : rebond possible"},
        11: {"bias": "bullish",  "strength": "faible",  "note": "Novembre : reprise risk-on"},
        12: {"bias": "neutral",  "strength": "faible",  "note": "Décembre : thin market"},
    },
    "GC=F": {  # Gold
        1:  {"bias": "bullish",  "strength": "fort",    "note": "Janvier : or historiquement très haussier (safe haven + demande Asie)"},
        2:  {"bias": "neutral",  "strength": "faible",  "note": "Février : consolidation après le rally de janvier"},
        3:  {"bias": "bearish",  "strength": "modéré",  "note": "Mars : pression baissière saisonnière sur l'or"},
        4:  {"bias": "neutral",  "strength": "faible",  "note": "Avril : période mixte"},
        5:  {"bias": "neutral",  "strength": "faible",  "note": "Mai : neutre"},
        6:  {"bias": "bullish",  "strength": "faible",  "note": "Juin : légère tendance haussière"},
        7:  {"bias": "bearish",  "strength": "faible",  "note": "Juillet : été calme, pression légère"},
        8:  {"bias": "bullish",  "strength": "modéré",  "note": "Août : demande bijouterie Inde (fêtes) + risk-off"},
        9:  {"bias": "bullish",  "strength": "fort",    "note": "Septembre : meilleur mois historique pour l'or"},
        10: {"bias": "bullish",  "strength": "modéré",  "note": "Octobre : or fort en Q4 historiquement"},
        11: {"bias": "bullish",  "strength": "modéré",  "note": "Novembre : demande bijouterie + safe haven"},
        12: {"bias": "neutral",  "strength": "faible",  "note": "Décembre : consolidation de fin d'année"},
    },
    "CL=F": {  # Crude Oil
        1:  {"bias": "neutral",  "strength": "faible",  "note": "Janvier : pétrole mixte en début d'année"},
        2:  {"bias": "bullish",  "strength": "faible",  "note": "Février : début de hausse saisonnière"},
        3:  {"bias": "bullish",  "strength": "modéré",  "note": "Mars : hausse saisonnière (passage à l'été)"},
        4:  {"bias": "bullish",  "strength": "fort",    "note": "Avril : driving season US — pétrole fort"},
        5:  {"bias": "bullish",  "strength": "fort",    "note": "Mai : pic demande estivale"},
        6:  {"bias": "bullish",  "strength": "modéré",  "note": "Juin : demande estivale soutenue"},
        7:  {"bias": "neutral",  "strength": "faible",  "note": "Juillet : plateau estival"},
        8:  {"bias": "bearish",  "strength": "faible",  "note": "Août : début de déclin saisonnier"},
        9:  {"bias": "bearish",  "strength": "modéré",  "note": "Septembre : fin driving season, pression baissière"},
        10: {"bias": "bearish",  "strength": "modéré",  "note": "Octobre : demande réduite"},
        11: {"bias": "bearish",  "strength": "faible",  "note": "Novembre : hiver, demande chauffage partielle"},
        12: {"bias": "neutral",  "strength": "faible",  "note": "Décembre : neutre, marché attentiste"},
    },
    "BTC/USDT": {
        1:  {"bias": "bullish",  "strength": "fort",    "note": "Janvier : 'January effect' crypto — historiquement très haussier"},
        2:  {"bias": "bullish",  "strength": "modéré",  "note": "Février : continuation du rally Q1"},
        3:  {"bias": "bullish",  "strength": "modéré",  "note": "Mars : Q1 fort historiquement pour BTC"},
        4:  {"bias": "neutral",  "strength": "faible",  "note": "Avril : consolidation possible"},
        5:  {"bias": "bearish",  "strength": "modéré",  "note": "Mai : 'Sell in May' crypto — début de déclin historique"},
        6:  {"bias": "bearish",  "strength": "modéré",  "note": "Juin : pression baissière saisonnière"},
        7:  {"bias": "neutral",  "strength": "faible",  "note": "Juillet : rebond possible mais instable"},
        8:  {"bias": "neutral",  "strength": "faible",  "note": "Août : volatilité sans direction claire"},
        9:  {"bias": "bearish",  "strength": "fort",    "note": "Septembre : pire mois historique pour BTC (avg -7%)"},
        10: {"bias": "bullish",  "strength": "fort",    "note": "'Uptober' — Octobre historiquement très haussier pour BTC"},
        11: {"bias": "bullish",  "strength": "fort",    "note": "Novembre : rally de fin d'année, BTC fort Q4"},
        12: {"bias": "bullish",  "strength": "modéré",  "note": "Décembre : continuation Q4 rally mais attention au profit-taking"},
    },
    "ETH/USDT": {
        1:  {"bias": "bullish",  "strength": "fort",    "note": "Janvier : ETH suit BTC — très fort en début d'année"},
        2:  {"bias": "bullish",  "strength": "modéré",  "note": "Février : momentum Q1 crypto"},
        3:  {"bias": "bullish",  "strength": "modéré",  "note": "Mars : Q1 fort pour ETH"},
        4:  {"bias": "neutral",  "strength": "faible",  "note": "Avril : consolidation"},
        5:  {"bias": "bearish",  "strength": "modéré",  "note": "Mai : 'Sell in May' impacte ETH fortement"},
        6:  {"bias": "bearish",  "strength": "modéré",  "note": "Juin : pression baissière"},
        7:  {"bias": "neutral",  "strength": "faible",  "note": "Juillet : rebond instable"},
        8:  {"bias": "neutral",  "strength": "faible",  "note": "Août : mixte"},
        9:  {"bias": "bearish",  "strength": "fort",    "note": "Septembre : mois baissier historique pour ETH"},
        10: {"bias": "bullish",  "strength": "fort",    "note": "Octobre : fort rebond crypto Q4"},
        11: {"bias": "bullish",  "strength": "fort",    "note": "Novembre : continuation Q4 bull run"},
        12: {"bias": "bullish",  "strength": "modéré",  "note": "Décembre : Q4 fort, attention profit-taking fin mois"},
    },
    "SPY": {
        1:  {"bias": "bullish",  "strength": "modéré",  "note": "Janvier : 'January effect' — afflux de capitaux institutionnels"},
        2:  {"bias": "neutral",  "strength": "faible",  "note": "Février : consolidation après janvier"},
        3:  {"bias": "bullish",  "strength": "faible",  "note": "Mars : marché généralement positif"},
        4:  {"bias": "bullish",  "strength": "modéré",  "note": "Avril : fort historiquement avant 'sell in May'"},
        5:  {"bias": "neutral",  "strength": "faible",  "note": "Mai : 'Sell in May' — début de période faible"},
        6:  {"bias": "neutral",  "strength": "faible",  "note": "Juin : période estivale neutre"},
        7:  {"bias": "bullish",  "strength": "faible",  "note": "Juillet : rebond estival fréquent"},
        8:  {"bias": "neutral",  "strength": "faible",  "note": "Août : faible liquidité, risque de correction"},
        9:  {"bias": "bearish",  "strength": "fort",    "note": "Septembre : pire mois historique pour le S&P500"},
        10: {"bias": "neutral",  "strength": "faible",  "note": "Octobre : volatile mais souvent point de retournement"},
        11: {"bias": "bullish",  "strength": "fort",    "note": "Novembre : très fort — rally de fin d'année"},
        12: {"bias": "bullish",  "strength": "modéré",  "note": "Décembre : 'Santa Claus rally' fréquent"},
    },
    "QQQ": {
        1:  {"bias": "bullish",  "strength": "modéré",  "note": "Janvier : tech fort en début d'année"},
        2:  {"bias": "neutral",  "strength": "faible",  "note": "Février : neutre"},
        3:  {"bias": "bullish",  "strength": "faible",  "note": "Mars : modérément haussier"},
        4:  {"bias": "bullish",  "strength": "modéré",  "note": "Avril : tech fort avant la pause estivale"},
        5:  {"bias": "neutral",  "strength": "faible",  "note": "Mai : 'Sell in May' début"},
        6:  {"bias": "neutral",  "strength": "faible",  "note": "Juin : neutre"},
        7:  {"bias": "bullish",  "strength": "faible",  "note": "Juillet : rebond tech fréquent"},
        8:  {"bias": "neutral",  "strength": "faible",  "note": "Août : neutre/risque"},
        9:  {"bias": "bearish",  "strength": "fort",    "note": "Septembre : tech sous forte pression saisonnière"},
        10: {"bias": "neutral",  "strength": "faible",  "note": "Octobre : retournement possible"},
        11: {"bias": "bullish",  "strength": "fort",    "note": "Novembre : tech très fort en Q4"},
        12: {"bias": "bullish",  "strength": "modéré",  "note": "Décembre : continuation Q4"},
    },
}

# Fallback pour les symboles non mappés
_DEFAULT_SEASONALITY = {"bias": "neutral", "strength": "faible", "note": "Pas de données saisonnières spécifiques pour cet actif"}

BULLISH_WORDS = [
    "bullish", "rally", "surge", "rise", "gain", "strong", "beat",
    "better than expected", "outperform", "above forecast", "hawkish",
    "higher", "upside", "recovery", "boost", "optimism", "positive",
    "exceeds", "tops", "growth", "momentum", "breakout",
]
BEARISH_WORDS = [
    "bearish", "drop", "fall", "decline", "weak", "miss", "disappoint",
    "below forecast", "underperform", "dovish", "lower", "downside",
    "recession", "slowdown", "concern", "risk", "negative", "sell-off",
    "plunge", "collapse", "warning", "fear", "uncertainty",
]


# ─────────────────────────────────────────────────────────────────────────────
# FOREX FACTORY — Economic Calendar
# ─────────────────────────────────────────────────────────────────────────────

def country_to_currency(country: str) -> str:
    mapping = {
        "USD": "USD", "United States": "USD", "US": "USD",
        "EUR": "EUR", "Euro Zone": "EUR", "European Union": "EUR",
        "GBP": "GBP", "United Kingdom": "GBP", "UK": "GBP",
        "JPY": "JPY", "Japan": "JPY",
        "CHF": "CHF", "Switzerland": "CHF",
        "AUD": "AUD", "Australia": "AUD",
        "NZD": "NZD", "New Zealand": "NZD",
        "CAD": "CAD", "Canada": "CAD",
    }
    return mapping.get(country, country)


async def fetch_forex_factory_events() -> List[Dict]:
    """Parse ForexFactory XML calendar using sync requests in thread."""
    try:
        def _fetch():
            resp = requests.get(
                FOREX_FACTORY_URL,
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            return resp.content

        content = await asyncio.to_thread(_fetch)

        # Parse XML — handle encoding issues
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            # Try fixing encoding
            text = content.decode("windows-1252", errors="replace")
            root = ET.fromstring(text.encode("utf-8"))

        events = []
        for event in root.findall("event"):
            def get(tag):
                el = event.find(tag)
                if el is None:
                    return ""
                # Handle CDATA
                return (el.text or "").strip()

            title    = get("title")
            country  = get("country")
            date_str = get("date")
            time_str = get("time")
            impact   = get("impact")
            forecast = get("forecast")
            previous = get("previous")
            actual   = get("actual")

            # Parse datetime
            event_dt = None
            try:
                dt_str = f"{date_str} {time_str}".strip()
                # Try multiple formats
                for fmt in ["%m-%d-%Y %I:%M%p", "%m-%d-%Y %I%p", "%m-%d-%Y"]:
                    try:
                        event_dt = datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

            events.append({
                "title":    title,
                "country":  country,
                "currency": country_to_currency(country),
                "date":     event_dt,
                "impact":   impact.lower() if impact else "low",
                "forecast": forecast,
                "previous": previous,
                "actual":   actual,
            })

        logger.info(f"ForexFactory: {len(events)} events loaded")
        return events

    except Exception as e:
        logger.error(f"ForexFactory fetch error: {e}")
        return []


def get_upcoming_events(events: List[Dict], currencies: List[str], hours_ahead: int = 24) -> List[Dict]:
    """High-impact events in next N hours for given currencies."""
    now    = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours_ahead)
    return [
        ev for ev in events
        if ev["date"] and ev["currency"] in currencies
        and ev["impact"] == "high"
        and now <= ev["date"] <= cutoff
    ]


def get_recent_surprises(events: List[Dict], currencies: List[str], hours_back: int = 48) -> List[Dict]:
    """Events with actual data released in last N hours."""
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_back)
    return [
        ev for ev in events
        if ev["date"] and ev["actual"] != ""
        and ev["currency"] in currencies
        and ev["impact"] in ("high", "medium")
        and cutoff <= ev["date"] <= now
    ]


def score_economic_surprise(surprises: List[Dict]) -> float:
    """
    Score economic surprises: actual vs forecast.
    Returns net score between -1.0 (bearish) and +1.0 (bullish).
    """
    if not surprises:
        return 0.0

    scores = []
    for ev in surprises:
        try:
            actual   = float(ev["actual"].replace("%", "").replace("K", "").replace("M", "").strip())
            forecast = float(ev["forecast"].replace("%", "").replace("K", "").replace("M", "").strip())
            if forecast != 0:
                surprise = (actual - forecast) / abs(forecast)
                scores.append(surprise)
        except (ValueError, AttributeError):
            continue

    return sum(scores) / len(scores) if scores else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# FXSTREET RSS — News Sentiment
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_fxstreet_headlines(currencies: List[str]) -> List[str]:
    """Fetch FXStreet RSS headlines filtered by relevant currencies."""
    try:
        def _fetch():
            resp = requests.get(
                FXSTREET_RSS_URL,
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            return resp.content

        content = await asyncio.to_thread(_fetch)
        root    = ET.fromstring(content)
        channel = root.find("channel")
        if channel is None:
            return []

        headlines = []
        currency_keywords = [c.upper() for c in currencies]

        for item in channel.findall("item"):
            title_el = item.find("title")
            if title_el is None or not title_el.text:
                continue
            title = title_el.text.strip()

            # Filter: only headlines mentioning relevant currencies
            title_upper = title.upper()
            if any(kw in title_upper for kw in currency_keywords):
                headlines.append(title)

        logger.info(f"FXStreet: {len(headlines)} relevant headlines for {currencies}")
        return headlines[:30]

    except Exception as e:
        logger.error(f"FXStreet fetch error: {e}")
        return []


def analyze_sentiment_keywords(headlines: List[str]) -> Dict[str, float]:
    """
    Simple but effective keyword-based sentiment analysis.
    No ML model needed — fast and reliable.
    """
    if not headlines:
        return {"positive": 0.33, "negative": 0.33, "neutral": 0.34, "net": 0.0}

    bullish_count = 0
    bearish_count = 0
    neutral_count = 0

    for headline in headlines:
        h = headline.lower()
        b_score = sum(1 for w in BULLISH_WORDS if w in h)
        s_score = sum(1 for w in BEARISH_WORDS if w in h)

        if b_score > s_score:
            bullish_count += 1
        elif s_score > b_score:
            bearish_count += 1
        else:
            neutral_count += 1

    total = bullish_count + bearish_count + neutral_count
    if total == 0:
        return {"positive": 0.33, "negative": 0.33, "neutral": 0.34, "net": 0.0}

    positive = bullish_count / total
    negative = bearish_count / total
    neutral  = neutral_count / total
    net      = positive - negative

    return {
        "positive": round(positive, 3),
        "negative": round(negative, 3),
        "neutral":  round(neutral, 3),
        "net":      round(net, 3),
    }


def get_seasonality(symbol: str, month: Optional[int] = None) -> Dict[str, str]:
    """Returns seasonal bias for symbol in the given month (default: current month)."""
    if month is None:
        month = datetime.now(timezone.utc).month
    symbol_map = SEASONALITY_MAP.get(symbol)
    if symbol_map:
        return symbol_map.get(month, _DEFAULT_SEASONALITY)
    return _DEFAULT_SEASONALITY


# ─────────────────────────────────────────────────────────────────────────────
# MAIN FUNDAMENTAL ANALYSIS FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

async def get_fundamental_signals(
    symbol: str,
    market_type: str,
    all_events: List[Dict]
) -> Dict[str, Any]:
    """
    Returns all fundamental signals for a given symbol.
    all_events: pre-fetched ForexFactory events (shared across all symbols).
    """
    currencies = SYMBOL_CURRENCIES.get(symbol, ["USD"])

    # ── Economic Calendar ────────────────────────────────────────────────────
    upcoming_high_impact = get_upcoming_events(all_events, currencies, hours_ahead=24)
    recent_surprises     = get_recent_surprises(all_events, currencies, hours_back=48)
    surprise_score       = score_economic_surprise(recent_surprises)

    # ── News Sentiment (FXStreet) ────────────────────────────────────────────
    headlines = await fetch_fxstreet_headlines(currencies)
    sentiment = analyze_sentiment_keywords(headlines)

    # Blend economic surprise into sentiment
    if surprise_score != 0.0:
        blended_net = (sentiment["net"] * 0.6) + (surprise_score * 0.4)
        sentiment["net"] = round(blended_net, 3)

    seasonality = get_seasonality(symbol)

    return {
        "upcoming_high_impact_events": upcoming_high_impact,
        "recent_surprises":            recent_surprises,
        "surprise_score":              surprise_score,
        "sentiment":                   sentiment,
        "headline_count":              len(headlines),
        "headlines":                   headlines[:10],   # top 10 headlines for analysis display
        "seasonality":                 seasonality,
    }
