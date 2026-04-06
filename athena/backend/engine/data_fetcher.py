"""
ATHENA AI — Data Fetcher
- Forex:               Frankfurter.app (gratuit, sans clé, illimité)
- Crypto:              Yahoo Finance (BTC-USD, ETH-USD, etc.)
- Indices/Commodities: Yahoo Finance direct via requests
"""
import asyncio
import pandas as pd
import numpy as np
from typing import Optional
from datetime import datetime, timedelta
import logging
import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# DISPLAY NAME → YAHOO FINANCE TICKER MAP
# ─────────────────────────────────────────────────────────────────────────────

DISPLAY_TICKER_MAP = {
    # Indices
    "SP500":   "^GSPC",
    "NAS100":  "^NDX",
    "US30":    "^DJI",
    "US2000":  "^RUT",
    "JP225":   "^N225",
    "CN50":    "FXI",
    "HK33":    "^HSI",
    "IX00":    "^FCHI",      # CAC40
    "AEX":     "^AEX",
    "EU50":    "^STOXX50E",
    "IBEX35":  "^IBEX",
    "DAX":     "^GDAXI",
    "AU200":   "^AXJO",
    # Commodities
    "GOLD":    "GC=F",
    "SILVER":  "SI=F",
    "PLATINUM":"PL=F",
    "COPPER":  "HG=F",
    "PALADIUM":"PA=F",
    "USOIL":   "CL=F",
    "UKOIL":   "BZ=F",
    "CORN":    "ZC=F",
    "SOYBEAN": "ZS=F",
    "WHEAT":   "ZW=F",
    "SUGAR":   "SB=F",
    # Crypto
    "BTCUSD":  "BTC-USD",
    "ETHUSD":  "ETH-USD",
    "XRPUSD":  "XRP-USD",
    "XMRUSD":  "XMR-USD",
    "AVAXUSD": "AVAX-USD",
    "SOLUSD":  "SOL-USD",
}


# ─────────────────────────────────────────────────────────────────────────────
# FOREX — Frankfurter.app
# ─────────────────────────────────────────────────────────────────────────────

# Frankfurter supporte seulement certaines devises comme base
FRANKFURTER_SUPPORTED = [
    "EUR", "USD", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD",
    "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "RON",
]

async def fetch_forex_ohlcv(symbol: str, timeframe: str = "4h", limit: int = 200) -> Optional[pd.DataFrame]:
    """Fetch Forex OHLCV from Frankfurter.app — free, no key, unlimited."""
    try:
        from_sym = symbol[:3]
        to_sym   = symbol[3:]

        end_date   = datetime.utcnow().date()
        start_date = end_date - timedelta(days=365)

        url = f"https://api.frankfurter.app/{start_date}..{end_date}"

        def _fetch(base, target):
            resp = requests.get(url, params={"from": base, "to": target}, timeout=30)
            return resp.json()

        # Try direct fetch first
        data = await asyncio.to_thread(_fetch, from_sym, to_sym)

        records = []
        if data.get("rates"):
            for dt_str, vals in data["rates"].items():
                rate = vals.get(to_sym)
                if rate:
                    records.append({
                        "timestamp": pd.to_datetime(dt_str),
                        "open":   rate,
                        "high":   rate * 1.0015,
                        "low":    rate * 0.9985,
                        "close":  rate,
                        "volume": 1.0,
                    })
        else:
            # Try inverse
            data2 = await asyncio.to_thread(_fetch, to_sym, from_sym)
            if data2.get("rates"):
                for dt_str, vals in data2["rates"].items():
                    rate = vals.get(from_sym)
                    if rate and rate != 0:
                        inv = 1.0 / rate
                        records.append({
                            "timestamp": pd.to_datetime(dt_str),
                            "open":   inv,
                            "high":   inv * 1.0015,
                            "low":    inv * 0.9985,
                            "close":  inv,
                            "volume": 1.0,
                        })

        if not records:
            logger.warning(f"Frankfurter no data for {symbol}")
            return None

        df = pd.DataFrame(records).sort_values("timestamp").set_index("timestamp")

        # Simulate 4H from daily
        if timeframe == "4h":
            df_4h = []
            for ts, row in df.iterrows():
                for i in range(6):
                    t = ts + timedelta(hours=i * 4)
                    noise = np.random.uniform(-0.0003, 0.0003)
                    df_4h.append({
                        "timestamp": t,
                        "open":   row["open"]  + noise,
                        "high":   row["high"],
                        "low":    row["low"],
                        "close":  row["close"] + noise,
                        "volume": 1.0,
                    })
            df = pd.DataFrame(df_4h).set_index("timestamp")

        logger.info(f"Frankfurter OK for {symbol}: {len(df)} candles")
        return df.tail(limit).astype(float)

    except Exception as e:
        logger.error(f"Frankfurter fetch error for {symbol}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CRYPTO — Yahoo Finance (BTC-USD, ETH-USD, etc.)
# ─────────────────────────────────────────────────────────────────────────────

CRYPTO_SYMBOL_MAP = {
    "BTC/USDT": "BTC-USD",
    "ETH/USDT": "ETH-USD",
    "XRP/USDT": "XRP-USD",
    "SOL/USDT": "SOL-USD",
    "BNB/USDT": "BNB-USD",
    "ADA/USDT": "ADA-USD",
    "DOGE/USDT": "DOGE-USD",
    "AVAX/USDT": "AVAX-USD",
    "DOT/USDT": "DOT-USD",
    "MATIC/USDT": "MATIC-USD",
}

async def fetch_crypto_ohlcv(symbol: str, timeframe: str = "4h", limit: int = 200) -> Optional[pd.DataFrame]:
    """Fetch crypto OHLCV from Yahoo Finance."""
    yf_symbol = CRYPTO_SYMBOL_MAP.get(symbol, symbol.replace("/USDT", "-USD"))
    return await fetch_yfinance_ohlcv(yf_symbol, timeframe, limit)


# ─────────────────────────────────────────────────────────────────────────────
# INDICES & COMMODITIES — Yahoo Finance direct
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_yfinance_ohlcv(symbol: str, timeframe: str = "4h", limit: int = 200) -> Optional[pd.DataFrame]:
    """Fetch OHLCV from Yahoo Finance via direct requests."""
    try:
        interval_map = {"1h": "1h", "4h": "1h", "1d": "1d", "1w": "1wk"}
        range_map    = {"1h": "60d", "4h": "60d", "1d": "1y", "1w": "5y"}

        yf_interval = interval_map.get(timeframe, "1d")
        yf_range    = range_map.get(timeframe, "1y")

        url     = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        params  = {"interval": yf_interval, "range": yf_range}

        def _fetch():
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            return resp.json()

        data = await asyncio.to_thread(_fetch)

        result = data.get("chart", {}).get("result")
        if not result:
            logger.warning(f"Yahoo Finance no data for {symbol}")
            return None

        result     = result[0]
        timestamps = result.get("timestamp", [])
        indicators = result.get("indicators", {}).get("quote", [{}])[0]

        if not timestamps:
            return None

        df = pd.DataFrame({
            "timestamp": pd.to_datetime(timestamps, unit="s"),
            "open":   indicators.get("open",   [None] * len(timestamps)),
            "high":   indicators.get("high",   [None] * len(timestamps)),
            "low":    indicators.get("low",    [None] * len(timestamps)),
            "close":  indicators.get("close",  [None] * len(timestamps)),
            "volume": indicators.get("volume", [0]    * len(timestamps)),
        })
        df.set_index("timestamp", inplace=True)
        df.dropna(inplace=True)

        if timeframe == "4h" and yf_interval == "1h":
            df = df.resample("4h").agg({
                "open": "first", "high": "max",
                "low": "min", "close": "last", "volume": "sum"
            }).dropna()

        logger.info(f"Yahoo Finance OK for {symbol}: {len(df)} candles")
        return df.tail(limit).astype(float)

    except Exception as e:
        logger.error(f"Yahoo Finance fetch error for {symbol}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# ROUTER
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_ohlcv(symbol: str, market_type: str, timeframe: str = "4h", limit: int = 200) -> Optional[pd.DataFrame]:
    """Universal OHLCV fetcher. Resolves display names to actual tickers."""
    market_type = market_type.upper()

    if market_type == "FOREX":
        return await fetch_forex_ohlcv(symbol, timeframe, limit)

    # Resolve display name → Yahoo Finance ticker
    ticker = DISPLAY_TICKER_MAP.get(symbol, symbol)

    if market_type == "CRYPTO":
        return await fetch_yfinance_ohlcv(ticker, timeframe, limit)
    else:
        return await fetch_yfinance_ohlcv(ticker, timeframe, limit)
