"""
ATHENA AI — Technical Analysis Engine
Calculates: RSI, MACD, EMA Stack, Support & Resistance, ATR
Returns structured signals dict used by the scorer.
"""
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Tuple
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR CALCULATIONS
# ─────────────────────────────────────────────────────────────────────────────

def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_bollinger(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper_band, middle_band, lower_band)."""
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Returns ADX series (trend strength 0-100). > 25 = trending."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    # When +DM > -DM, keep +DM else 0
    plus_dm = plus_dm.where(plus_dm > minus_dm, 0.0)
    minus_dm = minus_dm.where(minus_dm > plus_dm, 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr14 = tr.ewm(com=period - 1, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(com=period - 1, min_periods=period).mean() / atr14.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(com=period - 1, min_periods=period).mean() / atr14.replace(0, np.nan)

    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.ewm(com=period - 1, min_periods=period).mean()
    return adx


def detect_candlestick_patterns(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Detects high-probability reversal candlestick patterns on the last 3 candles.
    Returns a dict of detected patterns with direction bias.
    """
    if len(df) < 3:
        return {"patterns": [], "bias": "neutral"}

    c0 = df.iloc[-1]  # current candle
    c1 = df.iloc[-2]  # previous candle
    c2 = df.iloc[-3]  # two candles ago

    o0, h0, l0, cl0 = float(c0["open"]), float(c0["high"]), float(c0["low"]), float(c0["close"])
    o1, h1, l1, cl1 = float(c1["open"]), float(c1["high"]), float(c1["low"]), float(c1["close"])
    o2, h2, l2, cl2 = float(c2["open"]), float(c2["high"]), float(c2["low"]), float(c2["close"])

    body0 = abs(cl0 - o0)
    body1 = abs(cl1 - o1)
    rng0  = h0 - l0
    rng1  = h1 - l1

    upper_wick0  = h0 - max(o0, cl0)
    lower_wick0  = min(o0, cl0) - l0
    upper_wick1  = h1 - max(o1, cl1)
    lower_wick1  = min(o1, cl1) - l1

    bullish0 = cl0 > o0
    bearish0 = cl0 < o0
    bullish1 = cl1 > o1
    bearish1 = cl1 < o1

    patterns = []
    votes = {"bullish": 0, "bearish": 0}

    # ── Bullish Engulfing ──────────────────────────────────────────────────
    if (bearish1 and bullish0 and
            o0 <= cl1 and cl0 >= o1 and body0 > body1 * 1.1):
        patterns.append("Bullish Engulfing")
        votes["bullish"] += 2

    # ── Bearish Engulfing ──────────────────────────────────────────────────
    if (bullish1 and bearish0 and
            o0 >= cl1 and cl0 <= o1 and body0 > body1 * 1.1):
        patterns.append("Bearish Engulfing")
        votes["bearish"] += 2

    # ── Hammer (bullish reversal at bottom) ───────────────────────────────
    if (rng0 > 0 and lower_wick0 >= body0 * 2 and
            upper_wick0 <= body0 * 0.5 and body0 > 0):
        patterns.append("Hammer")
        votes["bullish"] += 1

    # ── Shooting Star (bearish reversal at top) ───────────────────────────
    if (rng0 > 0 and upper_wick0 >= body0 * 2 and
            lower_wick0 <= body0 * 0.5 and body0 > 0):
        patterns.append("Shooting Star")
        votes["bearish"] += 1

    # ── Bullish Pin Bar (long lower wick, small body at top) ──────────────
    if (rng0 > 0 and lower_wick0 >= rng0 * 0.6 and
            body0 <= rng0 * 0.3):
        patterns.append("Bullish Pin Bar")
        votes["bullish"] += 2

    # ── Bearish Pin Bar (long upper wick, small body at bottom) ──────────
    if (rng0 > 0 and upper_wick0 >= rng0 * 0.6 and
            body0 <= rng0 * 0.3):
        patterns.append("Bearish Pin Bar")
        votes["bearish"] += 2

    # ── Doji (indecision — body < 10% of range) ───────────────────────────
    if rng0 > 0 and body0 <= rng0 * 0.1:
        patterns.append("Doji")
        # Doji alone is neutral — context matters

    # ── Morning Star (3-candle bullish reversal) ──────────────────────────
    if (bearish1 and body1 > 0 and
            body0 < rng0 * 0.3 and  # small middle body
            bullish0 and cl0 > (o2 + cl2) / 2):
        patterns.append("Morning Star")
        votes["bullish"] += 3

    # ── Evening Star (3-candle bearish reversal) ──────────────────────────
    if (bullish1 and body1 > 0 and
            body0 < rng0 * 0.3 and
            bearish0 and cl0 < (o2 + cl2) / 2):
        patterns.append("Evening Star")
        votes["bearish"] += 3

    bias = "bullish" if votes["bullish"] > votes["bearish"] else (
           "bearish" if votes["bearish"] > votes["bullish"] else "neutral")

    return {
        "patterns":      patterns,
        "bias":          bias,
        "bullish_votes": votes["bullish"],
        "bearish_votes": votes["bearish"],
    }


def detect_rsi_divergence(df: pd.DataFrame, rsi_series: pd.Series, lookback: int = 30) -> Dict[str, Any]:
    """
    Detects RSI divergence over the last `lookback` candles.
    Bullish divergence : price lower low, RSI higher low  → reversal signal
    Bearish divergence : price higher high, RSI lower high → reversal signal
    """
    if len(df) < lookback or len(rsi_series) < lookback:
        return {"bullish_divergence": False, "bearish_divergence": False}

    price = df["close"].tail(lookback).values
    rsi   = rsi_series.tail(lookback).values

    # Find swing lows (for bullish divergence)
    def find_swing_lows(arr, window=5):
        lows = []
        for i in range(window, len(arr) - window):
            if arr[i] == min(arr[i-window:i+window+1]):
                lows.append((i, arr[i]))
        return lows

    def find_swing_highs(arr, window=5):
        highs = []
        for i in range(window, len(arr) - window):
            if arr[i] == max(arr[i-window:i+window+1]):
                highs.append((i, arr[i]))
        return highs

    price_lows  = find_swing_lows(price)
    price_highs = find_swing_highs(price)
    rsi_lows    = find_swing_lows(rsi)
    rsi_highs   = find_swing_highs(rsi)

    bullish_div = False
    bearish_div = False

    # Bullish: last 2 price lows descending, last 2 RSI lows ascending
    if len(price_lows) >= 2 and len(rsi_lows) >= 2:
        pl1, pl2 = price_lows[-2], price_lows[-1]
        rl1, rl2 = rsi_lows[-2], rsi_lows[-1]
        if pl2[1] < pl1[1] and rl2[1] > rl1[1]:
            bullish_div = True

    # Bearish: last 2 price highs ascending, last 2 RSI highs descending
    if len(price_highs) >= 2 and len(rsi_highs) >= 2:
        ph1, ph2 = price_highs[-2], price_highs[-1]
        rh1, rh2 = rsi_highs[-2], rsi_highs[-1]
        if ph2[1] > ph1[1] and rh2[1] < rh1[1]:
            bearish_div = True

    return {
        "bullish_divergence": bullish_div,
        "bearish_divergence": bearish_div,
    }


def _count_touches(level: float, df: pd.DataFrame, pct: float = 0.005) -> int:
    """
    Count how many candles touched within pct% of the level.
    A level tested multiple times is exponentially more significant.
    """
    return int(((df["high"] - level).abs() / level <= pct).sum() +
               ((df["low"]  - level).abs() / level <= pct).sum())


def find_support_resistance(
    df: pd.DataFrame,
    lookback: int = 200,   # was 50 — now 1 month on 4H / ~10 months on 1D
    threshold: float = 0.003,
) -> Dict[str, Any]:
    """
    Identifies key S/R levels using swing highs/lows with touch counting.

    Improvements over v1:
    - lookback 50 → 200 (was only 8 days on 4H — now captures major monthly levels)
    - Touch count per level: more touches = stronger level
    - recently_tested flag: level touched in last 15 candles = fresh & more relevant
    - threshold 0.002 → 0.003 (reduces over-fragmentation on daily data)
    """
    df_look = df.tail(lookback)
    highs = df_look["high"]
    lows  = df_look["low"]
    close = float(df["close"].iloc[-1])

    # Find swing highs (local maxima — require 3-bar confirmation each side)
    resistance_levels = []
    for i in range(3, len(highs) - 3):
        if (highs.iloc[i] > highs.iloc[i-1] and highs.iloc[i] > highs.iloc[i-2] and
                highs.iloc[i] > highs.iloc[i-3] and
                highs.iloc[i] > highs.iloc[i+1] and highs.iloc[i] > highs.iloc[i+2] and
                highs.iloc[i] > highs.iloc[i+3]):
            resistance_levels.append(highs.iloc[i])

    # Find swing lows (local minima — require 3-bar confirmation each side)
    support_levels = []
    for i in range(3, len(lows) - 3):
        if (lows.iloc[i] < lows.iloc[i-1] and lows.iloc[i] < lows.iloc[i-2] and
                lows.iloc[i] < lows.iloc[i-3] and
                lows.iloc[i] < lows.iloc[i+1] and lows.iloc[i] < lows.iloc[i+2] and
                lows.iloc[i] < lows.iloc[i+3]):
            support_levels.append(lows.iloc[i])

    # Cluster nearby levels (merge levels within threshold% of each other)
    def cluster_levels(levels: List[float]) -> List[float]:
        if not levels:
            return []
        levels = sorted(set(levels))
        clustered = [levels[0]]
        for lvl in levels[1:]:
            if abs(lvl - clustered[-1]) / max(clustered[-1], 0.0001) > threshold:
                clustered.append(lvl)
            else:
                clustered[-1] = (clustered[-1] + lvl) / 2
        return clustered

    supports    = cluster_levels(support_levels)
    resistances = cluster_levels(resistance_levels)

    # Nearest levels to current price
    nearest_support    = max([s for s in supports    if s < close], default=None)
    nearest_resistance = min([r for r in resistances if r > close], default=None)

    # Touch counts — how many times each nearest level was revisited
    recent_df = df.tail(lookback)   # use full lookback for touch counting
    nearest_support_touches    = _count_touches(nearest_support,    recent_df) if nearest_support    else 0
    nearest_resistance_touches = _count_touches(nearest_resistance, recent_df) if nearest_resistance else 0

    # Recently tested = level touched in last 15 candles (fresh = more relevant)
    last15 = df.tail(15)
    support_recently_tested    = (
        nearest_support is not None and
        _count_touches(nearest_support, last15, pct=0.008) > 0
    )
    resistance_recently_tested = (
        nearest_resistance is not None and
        _count_touches(nearest_resistance, last15, pct=0.008) > 0
    )

    # 52-week high/low as major absolute levels
    week52_high = float(df["high"].tail(252).max())
    week52_low  = float(df["low"].tail(252).min())

    return {
        "supports":                     supports,
        "resistances":                  resistances,
        "nearest_support":              nearest_support,
        "nearest_resistance":           nearest_resistance,
        "nearest_support_touches":      nearest_support_touches,
        "nearest_resistance_touches":   nearest_resistance_touches,
        "support_recently_tested":      support_recently_tested,
        "resistance_recently_tested":   resistance_recently_tested,
        "week52_high":                  week52_high,
        "week52_low":                   week52_low,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN TECHNICAL ANALYSIS FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def calculate_technicals(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Runs all technical indicators on the OHLCV dataframe.
    Returns a structured dict of signals for the scorer.
    """
    if df is None or len(df) < 50:
        return {}

    close = df["close"]
    current_price = float(close.iloc[-1])

    # ── RSI ──────────────────────────────────────────────────────────────────
    rsi = calc_rsi(close)
    rsi_current = float(rsi.iloc[-1])
    rsi_prev = float(rsi.iloc[-2])

    # ── MACD ─────────────────────────────────────────────────────────────────
    macd_line, signal_line, histogram = calc_macd(close)
    macd_current = float(macd_line.iloc[-1])
    signal_current = float(signal_line.iloc[-1])
    hist_current = float(histogram.iloc[-1])
    hist_prev = float(histogram.iloc[-2])

    # Detect crossover (last 3 candles)
    macd_bullish_cross = (
        float(macd_line.iloc[-2]) < float(signal_line.iloc[-2]) and
        macd_current > signal_current
    )
    macd_bearish_cross = (
        float(macd_line.iloc[-2]) > float(signal_line.iloc[-2]) and
        macd_current < signal_current
    )

    # ── EMA Stack ────────────────────────────────────────────────────────────
    ema_20 = calc_ema(close, 20)
    ema_50 = calc_ema(close, 50)
    ema_200 = calc_ema(close, 200)

    ema_20_val = float(ema_20.iloc[-1])
    ema_50_val = float(ema_50.iloc[-1])
    ema_200_val = float(ema_200.iloc[-1])

    # EMA alignment check
    bullish_ema_stack = current_price > ema_20_val > ema_50_val > ema_200_val
    bearish_ema_stack = current_price < ema_20_val < ema_50_val < ema_200_val
    price_above_200 = current_price > ema_200_val

    # ── ATR ──────────────────────────────────────────────────────────────────
    atr = calc_atr(df)
    atr_current = float(atr.iloc[-1])

    # ── Volume ───────────────────────────────────────────────────────────────
    vol_series  = df["volume"]
    vol_current = float(vol_series.iloc[-1])
    vol_avg_20  = float(vol_series.rolling(20, min_periods=5).mean().iloc[-1])
    vol_ratio   = vol_current / max(vol_avg_20, 0.001)

    # OBV (On-Balance Volume) — cumulative buying vs selling pressure
    close_delta = close.diff()
    obv_delta   = np.where(close_delta > 0, vol_series, np.where(close_delta < 0, -vol_series, 0))
    obv         = pd.Series(obv_delta, index=close.index).cumsum()
    obv_rising  = float(obv.iloc[-1]) > float(obv.iloc[-10]) if len(obv) >= 10 else True

    # FOREX from Frankfurter has fake volume = 1.0 for every candle
    # Detect real volume: avg > 100 (any real market has much higher volume)
    has_real_volume = vol_avg_20 > 100

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb_upper, bb_mid, bb_lower = calc_bollinger(close)
    bb_upper_val = float(bb_upper.iloc[-1])
    bb_lower_val = float(bb_lower.iloc[-1])
    bb_mid_val   = float(bb_mid.iloc[-1])
    bb_width = (bb_upper_val - bb_lower_val) / max(bb_mid_val, 0.0001)
    # %B: 0 = at lower band, 1 = at upper band
    bb_pct_b = (current_price - bb_lower_val) / max(bb_upper_val - bb_lower_val, 0.0001)
    # Squeeze = narrow bands (< 2% width)
    bb_squeeze = bb_width < 0.02

    # ── ADX ───────────────────────────────────────────────────────────────────
    adx = calc_adx(df)
    adx_current = float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 0.0

    # ── Support & Resistance ─────────────────────────────────────────────────
    sr = find_support_resistance(df)

    # Is price testing a key level? (within 0.5% of nearest S/R)
    testing_support = False
    testing_resistance = False
    proximity_pct = 0.005  # 0.5%

    if sr["nearest_support"]:
        distance_to_support = abs(current_price - sr["nearest_support"]) / current_price
        testing_support = distance_to_support < proximity_pct

    if sr["nearest_resistance"]:
        distance_to_resistance = abs(current_price - sr["nearest_resistance"]) / current_price
        testing_resistance = distance_to_resistance < proximity_pct

    # Testing 52W high/low (major levels get extra weight)
    testing_52w_high = abs(current_price - sr["week52_high"]) / current_price < proximity_pct
    testing_52w_low = abs(current_price - sr["week52_low"]) / current_price < proximity_pct

    # ── Trend Direction ──────────────────────────────────────────────────────
    recent_highs = df["high"].tail(20).values
    recent_lows  = df["low"].tail(20).values
    uptrend   = recent_highs[-1] > recent_highs[-10] and recent_lows[-1] > recent_lows[-10]
    downtrend = recent_highs[-1] < recent_highs[-10] and recent_lows[-1] < recent_lows[-10]

    # ── Candlestick Patterns ─────────────────────────────────────────────────
    candle_patterns = detect_candlestick_patterns(df)

    # ── RSI Divergence ────────────────────────────────────────────────────────
    rsi_divergence = detect_rsi_divergence(df, rsi)

    return {
        "current_price": current_price,
        "atr": atr_current,

        "rsi": {
            "value": rsi_current,
            "prev": rsi_prev,
            "oversold": rsi_current < 35,
            "overbought": rsi_current > 65,
            "extreme_oversold": rsi_current < 25,
            "extreme_overbought": rsi_current > 75,
            "rising": rsi_current > rsi_prev,
            "bullish_divergence": rsi_divergence["bullish_divergence"],
            "bearish_divergence": rsi_divergence["bearish_divergence"],
        },

        "macd": {
            "macd": macd_current,
            "signal": signal_current,
            "histogram": hist_current,
            "histogram_prev": hist_prev,
            "bullish_cross": macd_bullish_cross,
            "bearish_cross": macd_bearish_cross,
            "above_zero": macd_current > 0,
            "histogram_growing": abs(hist_current) > abs(hist_prev),
        },

        "ema": {
            "ema_20": ema_20_val,
            "ema_50": ema_50_val,
            "ema_200": ema_200_val,
            "bullish_stack": bullish_ema_stack,
            "bearish_stack": bearish_ema_stack,
            "price_above_200": price_above_200,
        },

        "support_resistance": {
            **sr,
            "testing_support": testing_support,
            "testing_resistance": testing_resistance,
            "testing_52w_high": testing_52w_high,
            "testing_52w_low": testing_52w_low,
        },

        "volume": {
            "current":         vol_current,
            "avg_20":          vol_avg_20,
            "ratio":           round(vol_ratio, 2),
            "spike":           vol_ratio > 1.5,
            "obv_rising":      obv_rising,
            "has_real_volume": has_real_volume,
        },

        "trend": {
            "uptrend": uptrend,
            "downtrend": downtrend,
            "adx": adx_current,
            "trending": adx_current > 25,
        },

        "bollinger": {
            "upper": bb_upper_val,
            "lower": bb_lower_val,
            "middle": bb_mid_val,
            "pct_b": round(bb_pct_b, 3),
            "squeeze": bb_squeeze,
            "at_lower_band": bb_pct_b < 0.1,
            "at_upper_band": bb_pct_b > 0.9,
        },

        "candle_patterns": candle_patterns,
    }
