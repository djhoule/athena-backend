"""
ATHENA AI — Scoring Engine + Trade Builder  (v2)
=================================================
Composite score (0-100):
  - RSI                12 pts
  - MACD               12 pts
  - EMA Stack          15 pts
  - Support/Resistance 20 pts
  - Trend + ADX        10 pts   ← NEW (was unused)
  - Bollinger Bands     8 pts   ← NEW
  - Fundamental Cal.   12 pts
  - News Sentiment     11 pts
  ─────────────────────────────
  Total               100 pts

Gate rules (ALL must pass):
  1. score_total >= 72
  2. confluence_count >= 3  (signals aligned with direction)
  3. R:R >= 2.0

Grade:
  A = score >= 82
  B = score >= 72
"""
import json
from typing import Dict, Any, Optional, Tuple, List
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# INDIVIDUAL SIGNAL SCORERS
# ─────────────────────────────────────────────────────────────────────────────

def score_rsi(rsi: Dict) -> Tuple[float, str, str]:
    """Returns (score 0-14, direction bias, reason) — +2 bonus for divergence"""
    val = rsi["value"]
    score = 0.0
    bias = "neutral"
    reasons = []

    if rsi["extreme_oversold"]:
        score = 12.0
        bias = "long"
        reasons.append(f"RSI extrême {val:.1f} — zone de retournement haussier puissant")
    elif rsi["oversold"]:
        score = 9.0
        bias = "long"
        reasons.append(f"RSI {val:.1f} en zone de survente — setup LONG favorable")
        if rsi["rising"]:
            score = 11.0
            reasons.append("RSI qui remonte = confirmation du retournement")
    elif rsi["extreme_overbought"]:
        score = 12.0
        bias = "short"
        reasons.append(f"RSI extrême {val:.1f} — zone de retournement baissier puissant")
    elif rsi["overbought"]:
        score = 9.0
        bias = "short"
        reasons.append(f"RSI {val:.1f} en zone de surachat — setup SHORT favorable")
        if not rsi["rising"]:
            score = 11.0
            reasons.append("RSI qui baisse = confirmation du retournement")
    elif 45 <= val <= 55:
        score = 2.0
        bias = "neutral"
        reasons.append(f"RSI {val:.1f} — neutre, pas de signal directionnel")
    else:
        score = 4.0
        bias = "long" if val < 50 else "short"
        reasons.append(f"RSI {val:.1f} — légèrement {'survendu' if val < 50 else 'suracheté'}")

    # Divergence bonus (+2 pts) — one of the most reliable reversal signals
    if rsi.get("bullish_divergence") and bias != "short":
        score = min(score + 2.0, 14.0)
        bias = "long"
        reasons.append("Divergence RSI haussière détectée — retournement probable")
    elif rsi.get("bearish_divergence") and bias != "long":
        score = min(score + 2.0, 14.0)
        bias = "short"
        reasons.append("Divergence RSI baissière détectée — retournement probable")

    # Stochastic RSI bonus (+1 pt for crossover confirmation in extreme zone)
    if rsi.get("stoch_bull_cross") and bias != "short":
        score = min(score + 1.0, 14.0)
        bias = "long"
        reasons.append(f"StochRSI croisement haussier en zone survente ({rsi.get('stoch_k', 0):.0f})")
    elif rsi.get("stoch_bear_cross") and bias != "long":
        score = min(score + 1.0, 14.0)
        bias = "short"
        reasons.append(f"StochRSI croisement baissier en zone surachat ({rsi.get('stoch_k', 100):.0f})")

    return score, bias, " | ".join(reasons)


def score_candle_patterns(candles: Dict) -> Tuple[float, str, str]:
    """
    Returns (score 0-12, direction bias, reason).
    Candlestick patterns are direct price action signals — very high weight.
    """
    if not candles or not candles.get("patterns"):
        return 2.0, "neutral", "Pas de pattern de bougie significatif"

    patterns = candles["patterns"]
    bias_str = candles.get("bias", "neutral")
    b_votes  = candles.get("bullish_votes", 0)
    s_votes  = candles.get("bearish_votes", 0)

    bias = "long" if bias_str == "bullish" else ("short" if bias_str == "bearish" else "neutral")

    # Score based on votes (max 6 votes = 12 pts)
    votes = max(b_votes, s_votes)
    score = min(votes * 2.0, 12.0)

    # Minimum 4 pts if any pattern detected
    score = max(score, 4.0) if patterns else 2.0

    label = " + ".join(patterns[:2])
    direction_label = "haussier" if bias == "long" else ("baissier" if bias == "short" else "neutre")
    reason = f"Pattern bougie {direction_label}: {label}" if label else "Signal bougie neutre"

    return score, bias, reason


def score_macd(macd: Dict) -> Tuple[float, str, str]:
    """Returns (score 0-12, direction bias, reason)"""
    score = 0.0
    bias = "neutral"
    reasons = []

    if macd["bullish_cross"]:
        score = 10.0
        bias = "long"
        reasons.append("MACD crossover haussier confirmé")
        if macd["above_zero"]:
            score = 12.0
            reasons.append("+ crossover au-dessus de zéro = momentum fort")
    elif macd["bearish_cross"]:
        score = 10.0
        bias = "short"
        reasons.append("MACD crossover baissier confirmé")
        if not macd["above_zero"]:
            score = 12.0
            reasons.append("+ crossover en dessous de zéro = momentum fort")
    elif macd["histogram_growing"] and macd["histogram"] > 0:
        score = 6.0
        bias = "long"
        reasons.append("MACD histogramme en expansion haussière")
    elif macd["histogram_growing"] and macd["histogram"] < 0:
        score = 6.0
        bias = "short"
        reasons.append("MACD histogramme en expansion baissière")
    elif macd["above_zero"]:
        score = 4.0
        bias = "long"
        reasons.append("MACD positif — tendance haussière en cours")
    else:
        score = 4.0
        bias = "short"
        reasons.append("MACD négatif — tendance baissière en cours")

    return score, bias, " | ".join(reasons)


def score_ema_stack(ema: Dict) -> Tuple[float, str, str]:
    """Returns (score 0-15, direction bias, reason)"""
    score = 0.0
    bias = "neutral"
    reasons = []

    if ema["bullish_stack"]:
        score = 15.0
        bias = "long"
        reasons.append("EMA Stack parfait (Prix > EMA20 > EMA50 > EMA200) — tendance haussière solide")
    elif ema["bearish_stack"]:
        score = 15.0
        bias = "short"
        reasons.append("EMA Stack inversé (Prix < EMA20 < EMA50 < EMA200) — tendance baissière solide")
    elif ema["price_above_200"]:
        score = 7.0
        bias = "long"
        reasons.append("Prix au-dessus de l'EMA200 — bias haussier long terme")
    else:
        score = 7.0
        bias = "short"
        reasons.append("Prix sous l'EMA200 — bias baissier long terme")

    return score, bias, " | ".join(reasons)


def score_support_resistance(sr: Dict) -> Tuple[float, str, str]:
    """
    Returns (score 0-20, direction bias, reason).

    Scoring logic:
    - 52-week levels are the most significant (absolute extremes)
    - Regular S/R gets a base score + touch bonus + recency bonus
      Touch count bonus: 3-4 touches → +3pts / 5+ touches → +5pts
      Recently tested   → +2pts (level retested in last 15 candles = fresh)
    """
    score = 0.0
    bias  = "neutral"
    reasons: List[str] = []

    # ── 52-week levels (absolute priority) ───────────────────────────────────
    if sr["testing_52w_low"]:
        score += 16.0
        bias   = "long"
        reasons.append("Prix au plus bas 52 semaines — support majeur absolu")
    elif sr["testing_52w_high"]:
        score += 12.0
        bias   = "short"
        reasons.append("Prix au plus haut 52 semaines — résistance majeure absolue")

    # ── Regular S/R testing ───────────────────────────────────────────────────
    if sr.get("testing_support") and bias != "short":
        touches = sr.get("nearest_support_touches", 1)
        recent  = sr.get("support_recently_tested", False)

        base_score = 10.0
        touch_bonus  = 5.0 if touches >= 5 else (3.0 if touches >= 3 else 0.0)
        recency_bonus = 2.0 if recent else 0.0

        score += base_score + touch_bonus + recency_bonus
        bias   = "long"

        touch_label = f"testé {touches}× — niveau fort" if touches >= 3 else f"testé {touches}×"
        recent_label = " | retesté récemment ✓" if recent else ""
        reasons.append(
            f"Support clé à {sr['nearest_support']:.5f} ({touch_label}{recent_label})"
        )

    elif sr.get("testing_resistance") and bias != "long":
        touches = sr.get("nearest_resistance_touches", 1)
        recent  = sr.get("resistance_recently_tested", False)

        base_score = 10.0
        touch_bonus   = 5.0 if touches >= 5 else (3.0 if touches >= 3 else 0.0)
        recency_bonus = 2.0 if recent else 0.0

        score += base_score + touch_bonus + recency_bonus
        bias   = "short"

        touch_label  = f"testé {touches}× — niveau fort" if touches >= 3 else f"testé {touches}×"
        recent_label = " | retesté récemment ✓" if recent else ""
        reasons.append(
            f"Résistance clé à {sr['nearest_resistance']:.5f} ({touch_label}{recent_label})"
        )

    elif sr.get("nearest_support") and sr.get("nearest_resistance"):
        reasons.append(
            f"Support à {sr['nearest_support']:.5f} | Résistance à {sr['nearest_resistance']:.5f}"
        )
        score += 3.0

    # ── Pivot Point bonus (+3 pts) — institutional level confirmation ─────────
    pivot_hit = sr.get("pivot_level_hit")
    pivot_b   = sr.get("pivot_bias", "neutral")
    if pivot_hit:
        score += 3.0
        label = "support pivot" if pivot_b == "long" else ("résistance pivot" if pivot_b == "short" else "pivot central")
        reasons.append(f"Prix au {label} ({pivot_hit}) — niveau institutionnel clé")
        if bias == "neutral":
            bias = pivot_b

    return min(score, 20.0), bias, " | ".join(reasons)


def score_trend(trend: Dict) -> Tuple[float, str, str]:
    """
    Returns (score 0-10, direction bias, reason).
    Uses uptrend/downtrend flags + ADX strength.
    """
    score = 0.0
    bias = "neutral"
    reasons = []

    adx = trend.get("adx", 0.0)
    is_trending = trend.get("trending", False)  # ADX > 25

    if trend["uptrend"]:
        bias = "long"
        if is_trending:
            score = 10.0
            reasons.append(f"Tendance haussière confirmée (HH/HL) + ADX {adx:.0f} — trend fort")
        else:
            score = 6.0
            reasons.append(f"Tendance haussière (HH/HL), ADX {adx:.0f} — trend modéré")
    elif trend["downtrend"]:
        bias = "short"
        if is_trending:
            score = 10.0
            reasons.append(f"Tendance baissière confirmée (LH/LL) + ADX {adx:.0f} — trend fort")
        else:
            score = 6.0
            reasons.append(f"Tendance baissière (LH/LL), ADX {adx:.0f} — trend modéré")
    else:
        score = 2.0
        bias = "neutral"
        reasons.append(f"Pas de tendance claire, ADX {adx:.0f} — marché en range")

    return score, bias, " | ".join(reasons)


def score_bollinger(bb: Dict) -> Tuple[float, str, str]:
    """
    Returns (score 0-8, direction bias, reason).
    Rewards price at extremes of Bollinger Bands.
    """
    score = 0.0
    bias = "neutral"
    reasons = []

    pct_b = bb.get("pct_b", 0.5)

    if bb["at_lower_band"]:
        score = 8.0
        bias = "long"
        reasons.append(f"Prix en bas des Bollinger Bands (%B={pct_b:.2f}) — zone de rebond")
    elif bb["at_upper_band"]:
        score = 8.0
        bias = "short"
        reasons.append(f"Prix en haut des Bollinger Bands (%B={pct_b:.2f}) — zone de rejet")
    elif bb.get("squeeze"):
        score = 5.0
        bias = "neutral"
        reasons.append("Bollinger Squeeze — expansion de volatilité imminente")
    elif pct_b < 0.3:
        score = 4.0
        bias = "long"
        reasons.append(f"Prix dans moitié basse des Bollinger (%B={pct_b:.2f})")
    elif pct_b > 0.7:
        score = 4.0
        bias = "short"
        reasons.append(f"Prix dans moitié haute des Bollinger (%B={pct_b:.2f})")
    else:
        score = 2.0
        bias = "neutral"
        reasons.append(f"Prix au milieu des Bollinger Bands (%B={pct_b:.2f}) — neutre")

    return score, bias, " | ".join(reasons)


def score_volume(vol: Dict) -> Tuple[float, str, str]:
    """
    Returns (score 0-8, direction bias, reason).
    Only meaningful for CRYPTO / INDICES / COMMODITIES.
    FOREX (Frankfurter fake volume) returns a neutral 4pts with no directional bias.
    """
    if not vol or not vol.get("has_real_volume", False):
        return 4.0, "neutral", "Pas de données de volume réel (FOREX) — score neutre"

    ratio      = vol.get("ratio", 1.0)
    obv_rising = vol.get("obv_rising", True)
    bias       = "long" if obv_rising else "short"
    obv_label  = "OBV haussier (accumulation)" if obv_rising else "OBV baissier (distribution)"

    if ratio > 2.0:
        score  = 8.0
        reason = f"Volume x{ratio:.1f} la moyenne — forte conviction institutionnelle"
    elif ratio > 1.5:
        score  = 6.0
        reason = f"Volume x{ratio:.1f} la moyenne — signal confirmé par le volume"
    elif ratio > 1.0:
        score  = 4.0
        reason = f"Volume légèrement au-dessus de la moyenne ({ratio:.1f}x)"
    elif ratio > 0.7:
        score  = 3.0
        bias   = "neutral"
        reason = f"Volume normal ({ratio:.1f}x moyenne)"
    else:
        score  = 1.0
        bias   = "neutral"
        reason = f"⚠️ Volume faible ({ratio:.1f}x moyenne) — breakout peu fiable"

    return score, bias, f"{reason} | {obv_label}"


def score_ichimoku(ichi: Dict) -> Tuple[float, str, str]:
    """
    Returns (score 0-15, direction bias, reason).
    Ichimoku is one of the most complete institutional indicators — it encodes
    trend, momentum, support/resistance, and time all in one system.
    Scoring (max 11 bullish/bearish points → normalized to 15):
      +4  Price above/below cloud
      +2  Cloud color (bullish/bearish)
      +3  TK Cross OR +1 price vs Kijun
      +2  Chikou confirmation
    """
    if not ichi or not ichi.get("valid"):
        return 3.0, "neutral", "Ichimoku: données insuffisantes (< 52 bougies)"

    bullish_pts = 0
    bearish_pts = 0
    reasons: List[str] = []

    # ── 1. Price vs Cloud (most important: +4) ────────────────────────────────
    if ichi["price_above_cloud"]:
        bullish_pts += 4
        reasons.append(f"Prix au-dessus du nuage (cloud_top={ichi['cloud_top']:.4f})")
    elif ichi["price_below_cloud"]:
        bearish_pts += 4
        reasons.append(f"Prix sous le nuage (cloud_bottom={ichi['cloud_bottom']:.4f})")
    else:
        reasons.append("Prix dans le nuage — indécision")

    # ── 2. Cloud color (+2) ────────────────────────────────────────────────────
    if ichi["cloud_bullish"]:
        bullish_pts += 2
        reasons.append("Nuage vert ↗")
    else:
        bearish_pts += 2
        reasons.append("Nuage rouge ↘")

    # ── 3. TK Cross (+3) or Price vs Kijun (+1) ───────────────────────────────
    if ichi["tk_cross_bullish"]:
        bullish_pts += 3
        reasons.append("✅ Croisement TK haussier")
    elif ichi["tk_cross_bearish"]:
        bearish_pts += 3
        reasons.append("✅ Croisement TK baissier")
    elif ichi["price_above_kijun"]:
        bullish_pts += 1
        reasons.append(f"Prix > Kijun ({ichi['kijun']:.4f})")
    else:
        bearish_pts += 1
        reasons.append(f"Prix < Kijun ({ichi['kijun']:.4f})")

    # ── 4. Chikou Span (+2) ────────────────────────────────────────────────────
    if ichi["chikou_bullish"]:
        bullish_pts += 2
        reasons.append("Chikou haussier ✓")
    elif ichi["chikou_bearish"]:
        bearish_pts += 2
        reasons.append("Chikou baissier ✓")

    # ── Resolve score & bias ──────────────────────────────────────────────────
    if bullish_pts > bearish_pts:
        bias  = "long"
        score = min(round(bullish_pts / 11 * 15, 1), 15.0)
    elif bearish_pts > bullish_pts:
        bias  = "short"
        score = min(round(bearish_pts / 11 * 15, 1), 15.0)
    else:
        bias  = "neutral"
        score = 3.0

    score = max(score, 3.0)
    return score, bias, " | ".join(reasons[:3])


def score_calendar(fundamental: Dict) -> Tuple[float, str, str]:
    """
    Returns (score 0-12, direction bias, reason).
    Note: upcoming high-impact events are vetoed upstream — this function
    only scores the quality of the economic context (surprises + clear calendar).
    """
    surprises = fundamental.get("recent_surprises", [])

    if surprises:
        surprise_score = fundamental.get("surprise_score", 0.0)
        titles = [e["title"] for e in surprises[:2]]
        if surprise_score > 0.1:
            return 10.0, "long", f"Surprise économique positive récente: {', '.join(titles)}"
        elif surprise_score < -0.1:
            return 10.0, "short", f"Surprise économique négative récente: {', '.join(titles)}"
        return 7.0, "neutral", f"Données économiques récentes: {', '.join(titles)}"

    # Clear calendar — green light but not inflated
    return 8.0, "neutral", "Calendrier clair — pas d'événement majeur imminent"


def score_sentiment(fundamental: Dict) -> Tuple[float, str, str]:
    """Returns (score 0-11, direction bias, reason)"""
    sentiment = fundamental.get("sentiment", {})
    net = sentiment.get("net", 0.0)
    positive = sentiment.get("positive", 0.33)
    negative = sentiment.get("negative", 0.33)

    if fundamental.get("headline_count", 0) == 0:
        return 5.0, "neutral", "Pas de données news disponibles — score neutre"

    if net > 0.3:
        score = 11.0
        bias = "long"
        reason = f"Sentiment très haussier ({positive*100:.0f}% positif)"
    elif net > 0.1:
        score = 8.0
        bias = "long"
        reason = f"Sentiment haussier ({positive*100:.0f}% positif)"
    elif net < -0.3:
        score = 11.0
        bias = "short"
        reason = f"Sentiment très baissier ({negative*100:.0f}% négatif)"
    elif net < -0.1:
        score = 8.0
        bias = "short"
        reason = f"Sentiment baissier ({negative*100:.0f}% négatif)"
    else:
        score = 5.0
        bias = "neutral"
        reason = f"Sentiment neutre (positif: {positive*100:.0f}% / négatif: {negative*100:.0f}%)"

    return score, bias, reason


# ─────────────────────────────────────────────────────────────────────────────
# DIRECTION RESOLVER
# ─────────────────────────────────────────────────────────────────────────────

def resolve_direction(biases: Dict[str, str]) -> Tuple[str, int, List[str]]:
    """
    Determines direction and counts aligned confluences.
    Returns (direction, confluence_count, aligned_signal_names).
    Only counts non-neutral biases toward confluence.
    """
    long_votes:  List[str] = [name for name, b in biases.items() if b == "long"]
    short_votes: List[str] = [name for name, b in biases.items() if b == "short"]

    if len(long_votes) >= len(short_votes):
        direction = "LONG"
        return direction, len(long_votes), long_votes
    else:
        direction = "SHORT"
        return direction, len(short_votes), short_votes


# ─────────────────────────────────────────────────────────────────────────────
# CONFLICT PENALTY
# ─────────────────────────────────────────────────────────────────────────────

def calculate_conflict_penalty(
    direction: str,
    biases: Dict[str, str],
    scores: Dict[str, float],
) -> Tuple[float, List[str], str]:
    """
    Penalises signals that actively vote AGAINST the chosen direction.
    Deduction = 50% of each opposing signal's score.

    Neutral signals are ignored — only explicit opposite votes are penalised.
    Returns (penalty_points, conflicting_signal_names, note_string).
    """
    opposite = "short" if direction == "LONG" else "long"
    penalty  = 0.0
    conflicting: List[str] = []
    details: List[str] = []

    for signal, bias in biases.items():
        if bias == opposite:
            deduction = round(scores.get(signal, 0) * 0.5, 1)
            penalty  += deduction
            conflicting.append(signal)
            details.append(f"{_SIGNAL_LABELS.get(signal, signal)} (-{deduction}pts)")

    note = (
        f"{', '.join(details)} contredisent la direction {direction} → -{round(penalty, 1)}pts"
        if details else ""
    )
    return round(penalty, 1), conflicting, note


# ─────────────────────────────────────────────────────────────────────────────
# TRADE BUILDER — Entry / SL / TP
# ─────────────────────────────────────────────────────────────────────────────

def build_trade_levels(
    direction: str,
    current_price: float,
    atr: float,
    sr: Dict,
    min_rr: float = 2.0
) -> Optional[Dict]:
    """
    Calculates Entry, SL, TP1, TP2 based on ATR and S/R levels.
    Returns None if R:R is insufficient.
    """
    atr_multiplier_sl = 1.5
    sl_distance = atr * atr_multiplier_sl

    if direction == "LONG":
        entry = current_price
        if sr.get("nearest_support") and sr["nearest_support"] > entry - sl_distance * 2:
            stop_loss = sr["nearest_support"] - atr * 0.3
        else:
            stop_loss = entry - sl_distance

        risk = entry - stop_loss
        tp1 = entry + risk * 1.5
        tp2_base = entry + risk * 3.0

        if sr.get("nearest_resistance") and sr["nearest_resistance"] > tp1:
            sr_target = sr["nearest_resistance"]
            sr_rr = (sr_target - entry) / max(risk, 0.0001)
            if 2.0 <= sr_rr <= 5.0:
                tp2 = sr_target
            else:
                tp2 = tp2_base
        else:
            tp2 = tp2_base

    else:  # SHORT
        entry = current_price
        if sr.get("nearest_resistance") and sr["nearest_resistance"] < entry + sl_distance * 2:
            stop_loss = sr["nearest_resistance"] + atr * 0.3
        else:
            stop_loss = entry + sl_distance

        risk = stop_loss - entry
        tp1 = entry - risk * 1.5
        tp2_base = entry - risk * 3.0

        if sr.get("nearest_support") and sr["nearest_support"] < tp1:
            sr_target = sr["nearest_support"]
            sr_rr = (entry - sr_target) / max(risk, 0.0001)
            if 2.0 <= sr_rr <= 5.0:
                tp2 = sr_target
            else:
                tp2 = tp2_base
        else:
            tp2 = tp2_base

    actual_rr = (abs(tp2 - entry)) / max(abs(entry - stop_loss), 0.0001)
    if actual_rr < min_rr:
        return None

    return {
        "entry": round(entry, 6),
        "stop_loss": round(stop_loss, 6),
        "take_profit_1": round(tp1, 6),
        "take_profit_2": round(tp2, 6),
        "risk_reward": round(actual_rr, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ANALYSIS SUMMARY BUILDER
# ─────────────────────────────────────────────────────────────────────────────

_SIGNAL_LABELS = {
    "rsi":      "RSI",
    "macd":     "MACD",
    "ema":      "EMA Stack",
    "sr":       "Support/Résistance",
    "trend":    "Tendance",
    "bollinger":"Bollinger",
    "candles":  "Pattern Bougie",
    "volume":   "Volume",
    "ichimoku": "Ichimoku Cloud",
}


def build_analysis_summary(
    direction: str,
    symbol: str,
    confluence_signals: List[str],
    confluence_count: int,
    score_total: float,
    fundamental: Dict,
) -> str:
    """Builds a human-readable analysis summary sentence."""
    signal_names = [_SIGNAL_LABELS.get(s, s) for s in confluence_signals]
    signals_str = ", ".join(signal_names) if signal_names else "aucun"

    seasonality = fundamental.get("seasonality", {})
    season_note = seasonality.get("note", "")
    season_bias = seasonality.get("bias", "neutral")

    season_part = ""
    if season_note:
        season_part = f" Saisonnalité: {season_note}."

    cal_events = fundamental.get("upcoming_high_impact_events", [])
    cal_part = ""
    if cal_events:
        cal_part = f" ⚠️ Attention: {len(cal_events)} événement(s) macro dans 24h."
    else:
        cal_part = " Calendrier macro clair."

    return (
        f"Setup {direction} sur {symbol} avec {confluence_count} confluences alignées "
        f"({signals_str}). Score: {score_total:.0f}/100.{season_part}{cal_part}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# COMPOSITE SCORER — Main Function
# ─────────────────────────────────────────────────────────────────────────────

def calculate_score(
    technical: Dict[str, Any],
    fundamental: Dict[str, Any],
    min_score: int = 72,
    min_rr: float = 2.0,
    min_confluence: int = 4,
    symbol: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Calculates composite score and builds trade if all gates pass.
    Returns None if setup is not tradeable.

    Gates:
      1. score_total >= min_score (72)
      2. confluence_count >= min_confluence (3)
      3. R:R >= min_rr (2.0)
    """
    if not technical:
        return None

    # ── Gate 0: veto dur — événement HIGH-impact dans les 4 prochaines heures ─
    # (était 24h → bloquait tout. Réduit à 4h : trop dangereux juste avant un NFP/FOMC)
    upcoming_hard = fundamental.get("upcoming_high_impact_events", [])
    if upcoming_hard:
        names = ", ".join(e["title"] for e in upcoming_hard[:3])
        logger.info(f"[{symbol}] Veto macro <4h: {names}")
        return None

    # ── Score each component ─────────────────────────────────────────────────
    rsi_score,    rsi_bias,    rsi_reason    = score_rsi(technical["rsi"])
    macd_score,   macd_bias,   macd_reason   = score_macd(technical["macd"])
    ema_score,    ema_bias,    ema_reason    = score_ema_stack(technical["ema"])
    sr_score,     sr_bias,     sr_reason     = score_support_resistance(technical["support_resistance"])
    trend_score,  trend_bias,  trend_reason  = score_trend(technical.get("trend", {}))
    bb_score,     bb_bias,     bb_reason     = score_bollinger(technical.get("bollinger", {}))
    vol_score,    vol_bias,    vol_reason    = score_volume(technical.get("volume", {}))
    cal_score,    cal_bias,    cal_reason    = score_calendar(fundamental)
    sent_score,   sent_bias,   sent_reason   = score_sentiment(fundamental)
    candle_score, candle_bias, candle_reason = score_candle_patterns(technical.get("candle_patterns", {}))
    ichi_score,   ichi_bias,   ichi_reason   = score_ichimoku(technical.get("ichimoku", {}))

    total = (
        rsi_score + macd_score + ema_score + sr_score
        + trend_score + bb_score + vol_score + cal_score + sent_score
        + candle_score + ichi_score
    )

    # ── Soft penalty: HIGH-impact event in 4-24h (-10 pts, trade still valid) ─
    upcoming_soft = fundamental.get("upcoming_soft_events", [])
    macro_penalty = 0.0
    if upcoming_soft:
        macro_penalty = 10.0
        names_soft = ", ".join(e["title"] for e in upcoming_soft[:2])
        logger.debug(f"[{symbol}] Soft macro penalty -10pts: {names_soft}")
    total -= macro_penalty

    # ── Determine direction + confluence ─────────────────────────────────────
    biases = {
        "rsi": rsi_bias, "macd": macd_bias, "ema": ema_bias, "sr": sr_bias,
        "trend": trend_bias, "bollinger": bb_bias,
        **({"candles":   candle_bias} if candle_bias != "neutral" else {}),
        **({"ichimoku":  ichi_bias}   if ichi_bias   != "neutral" else {}),
        # Volume only added to biases when real data exists (avoids penalising FOREX)
        **({"volume": vol_bias} if technical.get("volume", {}).get("has_real_volume") else {}),
    }
    direction, confluence_count, confluence_signals = resolve_direction(biases)

    # ── Conflict penalty ──────────────────────────────────────────────────────
    # Signals voting against the chosen direction reduce the final score by 50%
    # of their individual score. A strong opposing EMA Stack = -7.5pts; a weak
    # opposing Bollinger = -1pt. This makes contradictions costly but not fatal
    # for weak conflicting signals.
    signal_scores = {
        "rsi": rsi_score, "macd": macd_score, "ema": ema_score,
        "sr": sr_score, "trend": trend_score, "bollinger": bb_score,
        "candles": candle_score, "volume": vol_score, "ichimoku": ichi_score,
    }
    conflict_penalty, conflicting_signals, conflict_note = calculate_conflict_penalty(
        direction, biases, signal_scores
    )
    adjusted_total = total - conflict_penalty

    if conflict_penalty > 0:
        logger.debug(f"[{symbol}] Conflict penalty: -{conflict_penalty}pts ({conflict_note})")

    # ── Gate 1: minimum score (after penalty) ────────────────────────────────
    if adjusted_total < min_score:
        return None

    # ── Gate 2: minimum confluence ────────────────────────────────────────────
    if confluence_count < min_confluence:
        logger.debug(f"Rejected {symbol}: only {confluence_count}/{min_confluence} confluences")
        return None

    # ── Grade (based on adjusted score) ──────────────────────────────────────
    if adjusted_total >= 82:
        grade = "A"
    else:
        grade = "B"

    # ── Gate 3: build trade levels (includes R:R check) ──────────────────────
    levels = build_trade_levels(
        direction=direction,
        current_price=technical["current_price"],
        atr=technical["atr"],
        sr=technical["support_resistance"],
        min_rr=min_rr,
    )

    if levels is None:
        return None  # R:R insufficient

    # ── Build full reasoning + analysis ──────────────────────────────────────
    seasonality = fundamental.get("seasonality", {})
    headlines   = fundamental.get("headlines", [])

    analysis_summary = build_analysis_summary(
        direction=direction,
        symbol=symbol,
        confluence_signals=confluence_signals,
        confluence_count=confluence_count,
        score_total=adjusted_total,
        fundamental=fundamental,
    )

    # ── Build fundamental context block ──────────────────────────────────────
    upcoming_events   = fundamental.get("upcoming_high_impact_events", [])
    recent_surprises  = fundamental.get("recent_surprises", [])
    surprise_score_val = fundamental.get("surprise_score", 0.0)
    sentiment_data    = fundamental.get("sentiment", {})

    # Format upcoming events for display
    upcoming_formatted = [
        f"{e['title']} ({e['currency']}) — {e['date'].strftime('%d/%m %Hh') if e.get('date') else '?'}"
        for e in upcoming_events[:3]
    ]

    # Format recent surprises
    surprises_formatted = []
    for e in recent_surprises[:3]:
        actual   = e.get("actual", "")
        forecast = e.get("forecast", "")
        if actual and forecast:
            surprises_formatted.append(
                f"{e['title']}: {actual} vs {forecast} attendu"
            )
        else:
            surprises_formatted.append(e["title"])

    sentiment_label = (
        "Très haussier" if sentiment_data.get("net", 0) > 0.3 else
        "Haussier"      if sentiment_data.get("net", 0) > 0.1 else
        "Très baissier" if sentiment_data.get("net", 0) < -0.3 else
        "Baissier"      if sentiment_data.get("net", 0) < -0.1 else
        "Neutre"
    )

    fundamental_context = {
        "sentiment_label":      sentiment_label,
        "sentiment_net":        round(sentiment_data.get("net", 0.0), 3),
        "headline_count":       fundamental.get("headline_count", 0),
        "surprise_score":       round(surprise_score_val, 3),
        "recent_surprises":     surprises_formatted,
        "upcoming_events":      upcoming_formatted,
        "calendar_clear":       len(upcoming_events) == 0,
    }

    reasoning = json.dumps({
        "rsi":               {"score": rsi_score,    "reason": rsi_reason},
        "macd":              {"score": macd_score,   "reason": macd_reason},
        "ema":               {"score": ema_score,    "reason": ema_reason},
        "support_resistance":{"score": sr_score,     "reason": sr_reason},
        "trend":             {"score": trend_score,  "reason": trend_reason},
        "bollinger":         {"score": bb_score,     "reason": bb_reason},
        "candle_patterns":   {"score": candle_score, "reason": candle_reason,
                              "patterns": technical.get("candle_patterns", {}).get("patterns", [])},
        "ichimoku":          {"score": ichi_score,   "reason": ichi_reason,
                              "cloud_top": technical.get("ichimoku", {}).get("cloud_top"),
                              "cloud_bottom": technical.get("ichimoku", {}).get("cloud_bottom"),
                              "tenkan": technical.get("ichimoku", {}).get("tenkan"),
                              "kijun": technical.get("ichimoku", {}).get("kijun"),
                              "above_cloud": technical.get("ichimoku", {}).get("price_above_cloud", False),
                              "below_cloud": technical.get("ichimoku", {}).get("price_below_cloud", False),
                              "market_regime": technical.get("market_regime", "UNKNOWN")},
        "volume":            {"score": vol_score,    "reason": vol_reason},
        "calendar":          {"score": cal_score,    "reason": cal_reason},
        "sentiment":         {"score": sent_score,   "reason": sent_reason},
        "confluence": {
            "count":   confluence_count,
            "signals": confluence_signals,
            "summary": f"{confluence_count}/6 signaux techniques alignés {direction}",
        },
        "seasonality": {
            "note":     seasonality.get("note", ""),
            "bias":     seasonality.get("bias", "neutral"),
            "strength": seasonality.get("strength", "faible"),
        },
        "news_headlines":     headlines,
        "analysis_summary":   analysis_summary,
        "fundamental_context": fundamental_context,
        "conflict_penalty": {
            "points":   conflict_penalty,
            "signals":  conflicting_signals,
            "note":     conflict_note,
        },
    }, ensure_ascii=False)

    return {
        "score_total":      round(adjusted_total, 1),
        "score_rsi":        rsi_score,
        "score_macd":       macd_score,
        "score_ema":        ema_score,
        "score_sr":         sr_score,
        "score_trend":      trend_score,
        "score_bollinger":  bb_score,
        "score_candle":     candle_score,
        "score_ichimoku":   ichi_score,
        "score_volume":     vol_score,
        "score_calendar":   cal_score,
        "score_sentiment":  sent_score,
        "confluence_count": confluence_count,
        "direction":        direction,
        "grade":            grade,
        "reasoning":        reasoning,
        **levels,
    }
