"""
ATHENA AI — Data Fetcher (v3)
- FOREX      : Yahoo Finance (EURUSD=X) — vraies données OHLCV, intraday disponible
- CRYPTO     : Yahoo Finance (BTC-USD)
- INDICES    : Yahoo Finance (^GSPC, ^NDX, etc.)
- COMMODITIES: Yahoo Finance (GC=F, CL=F, etc.)

Améliorations v3 :
- Suppression Frankfurter (données OHLC simulées) → Yahoo Finance pour tout
- Retry avec backoff exponentiel (3 tentatives)
- Session HTTP partagée pour performance
"""
import asyncio
import pandas as pd
import numpy as np
from typing import Optional
from datetime import datetime, timedelta
import logging
import time

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# DISPLAY NAME → YAHOO FINANCE TICKER MAP
# ─────────────────────────────────────────────────────────────────────────────

DISPLAY_TICKER_MAP = {
    # Indices
    "SP500":    "^GSPC",
    "NAS100":   "^NDX",
    "US30":     "^DJI",
    "US2000":   "^RUT",
    "JP225":    "^N225",
    "CN50":     "FXI",
    "HK33":     "^HSI",
    "IX00":     "^FCHI",
    "AEX":      "^AEX",
    "EU50":     "^STOXX50E",
    "IBEX35":   "^IBEX",
    "DAX":      "^GDAXI",
    "AU200":    "^AXJO",
    # Commodities
    "GOLD":     "GC=F",
    "SILVER":   "SI=F",
    "PLATINUM": "PL=F",
    "COPPER":   "HG=F",
    "PALADIUM": "PA=F",
    "USOIL":    "CL=F",
    "UKOIL":    "BZ=F",
    "CORN":     "ZC=F",
    "SOYBEAN":  "ZS=F",
    "WHEAT":    "ZW=F",
    "SUGAR":    "SB=F",
    # Crypto
    "BTCUSD":   "BTC-USD",
    "ETHUSD":   "ETH-USD",
    "XRPUSD":   "XRP-USD",
    "XMRUSD":   "XMR-USD",
    "AVAXUSD":  "AVAX-USD",
    "SOLUSD":   "SOL-USD",
}

# FOREX pairs → Yahoo Finance uses "EURUSD=X" format
# All 6-char FOREX pairs get "=X" appended automatically in fetch_forex_ohlcv


# ─────────────────────────────────────────────────────────────────────────────
# RETRY WRAPPER
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_with_retry(fetch_fn, symbol: str, max_attempts: int = 3) -> Optional[pd.DataFrame]:
    """Calls fetch_fn() up to max_attempts times with exponential backoff."""
    for attempt in range(max_attempts):
        try:
            result = await asyncio.to_thread(fetch_fn)
            if result is not None and not result.empty:
                return result
            # Empty result — wait and retry
            if attempt < max_attempts - 1:
                await asyncio.sleep(2 ** attempt)
        except Exception as e:
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                logger.warning(f"Fetch attempt {attempt+1} failed for {symbol}: {e}. Retry in {wait}s")
                await asyncio.sleep(wait)
            else:
                logger.error(f"All {max_attempts} attempts failed for {symbol}: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# CORE YFINANCE FETCHER
# ─────────────────────────────────────────────────────────────────────────────

def _yfinance_fetch(symbol: str, yf_period: str, yf_interval: str) -> Optional[pd.DataFrame]:
    """Synchronous yfinance fetch — run via asyncio.to_thread."""
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=yf_period, interval=yf_interval, auto_adjust=True)
    return df


async def fetch_yfinance_ohlcv(symbol: str, timeframe: str = "1d", limit: int = 300) -> Optional[pd.DataFrame]:
    """Fetch OHLCV from Yahoo Finance with retry logic."""
    interval_map = {"1h": "1h", "4h": "1h", "1d": "1d", "1w": "1wk"}
    period_map   = {"1h": "60d", "4h": "60d", "1d": "2y", "1w": "5y"}

    yf_interval = interval_map.get(timeframe, "1d")
    yf_period   = period_map.get(timeframe, "2y")

    def _fetch():
        return _yfinance_fetch(symbol, yf_period, yf_interval)

    df = await _fetch_with_retry(_fetch, symbol)

    if df is None or df.empty:
        logger.warning(f"yfinance no data for {symbol}")
        return None

    # Normalize column names
    df.columns = [c.lower() for c in df.columns]
    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.warning(f"yfinance missing columns {missing} for {symbol}")
        return None

    df = df[required].copy()
    df.index.name = "timestamp"
    df.dropna(inplace=True)

    # Resample 1h → 4h
    if timeframe == "4h":
        df = df.resample("4h").agg({
            "open": "first", "high": "max",
            "low": "min", "close": "last", "volume": "sum"
        }).dropna()

    if len(df) < 30:
        logger.warning(f"yfinance insufficient candles for {symbol}: {len(df)}")
        return None

    logger.info(f"yfinance OK {symbol} [{timeframe}]: {len(df)} candles")
    return df.tail(limit).astype(float)


# ─────────────────────────────────────────────────────────────────────────────
# FOREX — Yahoo Finance (EURUSD=X format)
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_forex_ohlcv(symbol: str, timeframe: str = "1d", limit: int = 300) -> Optional[pd.DataFrame]:
    """
    Fetch Forex OHLCV from Yahoo Finance.
    Yahoo supports EURUSD=X, GBPUSD=X, USDJPY=X etc. with real OHLCV data.
    """
    yf_symbol = symbol + "=X"
    df = await fetch_yfinance_ohlcv(yf_symbol, timeframe, limit)

    if df is None:
        # Try reversed pair as fallback (some crosses need inversion)
        reversed_sym = symbol[3:] + symbol[:3] + "=X"
        logger.info(f"FOREX fallback: trying {reversed_sym}")
        df_rev = await fetch_yfinance_ohlcv(reversed_sym, timeframe, limit)
        if df_rev is not None:
            # Invert prices
            for col in ["open", "high", "low", "close"]:
                df_rev[col] = 1.0 / df_rev[col]
            # After inversion, high and low are swapped
            df_rev["high"], df_rev["low"] = df_rev["low"].copy(), df_rev["high"].copy()
            return df_rev

    return df


# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSAL ROUTER
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_ohlcv(symbol: str, market_type: str, timeframe: str = "1d", limit: int = 300) -> Optional[pd.DataFrame]:
    """Universal OHLCV fetcher. Resolves display names to Yahoo Finance tickers."""
    market_type = market_type.upper()

    if market_type == "FOREX":
        return await fetch_forex_ohlcv(symbol, timeframe, limit)

    # Resolve display name → Yahoo Finance ticker
    ticker = DISPLAY_TICKER_MAP.get(symbol, symbol)
    return await fetch_yfinance_ohlcv(ticker, timeframe, limit)
