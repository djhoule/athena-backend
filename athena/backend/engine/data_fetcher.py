"""
ATHENA AI — Data Fetcher (v5+)
==============================
Stooq removed (now requires API key for all requests).

Sources:
  FOREX       : Yahoo Finance v8 chart API  (EURUSD=X format)
  INDICES     : Yahoo Finance v8 chart API  (^GSPC, ^GDAXI, ^NDX…)
  COMMODITIES : Yahoo Finance v8 chart API  (GC=F, CL=F, ZC=F…)
  CRYPTO      : Binance public API           (no key, 1D / 4H / 1H)

Yahoo Finance v8 endpoint:
  https://query1.finance.yahoo.com/v8/finance/chart/{symbol}
  ?interval={1d|1h|1wk}&range={2y|60d|10y}&includePrePost=false

Session management:
  - Persistent httpx.AsyncClient across requests (preserves cookies)
  - Crumb fetched once on first use, refreshed on 401
  - Rate limiter: 1 req/sec via asyncio.Lock (avoids Yahoo rate-limit)

Cache TTL by timeframe:
  1D  → 4 hours   (daily candle doesn't change until close)
  4H  → 1 hour
  1H  → 15 min
  1W  → 12 hours
"""
import asyncio
import json
import time
import logging
import pandas as pd
import numpy as np
from typing import Optional, Dict, List, Tuple
import httpx

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# IN-MEMORY OHLCV CACHE  (TTL depends on timeframe)
# ─────────────────────────────────────────────────────────────────────────────

_ohlcv_cache: dict = {}

_CACHE_TTL: dict = {
    "1d": 14400,   # 4 hours  — daily candle doesn't change until close
    "4h":  3600,   # 1 hour   — 4H candle changes every 4 hours
    "1h":   900,   # 15 min
    "1w": 43200,   # 12 hours
}
_DEFAULT_TTL = 3600


def _cache_get(key: str, timeframe: str = "1d") -> Optional[pd.DataFrame]:
    entry = _ohlcv_cache.get(key)
    ttl = _CACHE_TTL.get(timeframe, _DEFAULT_TTL)
    if entry and (time.time() - entry["ts"]) < ttl:
        return entry["df"]
    return None


def _cache_set(key: str, df: pd.DataFrame):
    _ohlcv_cache[key] = {"df": df, "ts": time.time()}


# ─────────────────────────────────────────────────────────────────────────────
# SYMBOL MAPS
# ─────────────────────────────────────────────────────────────────────────────

# FOREX: just append "=X" (EURUSD → EURUSD=X)
def _yf_forex(symbol: str) -> str:
    return f"{symbol}=X"

# Athena display name → Yahoo Finance symbol (indices)
YAHOO_INDICES_MAP: dict = {
    "SP500":   "^GSPC",
    "NAS100":  "^NDX",
    "US30":    "^DJI",
    "US2000":  "^RUT",
    "JP225":   "^N225",
    "CN50":    "000300.SS",    # CSI 300
    "HK33":    "^HSI",
    "IX00":    "^FCHI",        # CAC 40
    "AEX":     "^AEX",
    "EU50":    "^STOXX50E",
    "IBEX35":  "^IBEX",
    "DAX":     "^GDAXI",
    "AU200":   "^AXJO",
}

# Athena display name → Yahoo Finance symbol (commodities)
YAHOO_COMMODITY_MAP: dict = {
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
}

# Athena crypto display name → Binance symbol
BINANCE_SYMBOL_MAP: dict = {
    "BTCUSD":  "BTCUSDT",
    "ETHUSD":  "ETHUSDT",
    "XRPUSD":  "XRPUSDT",
    "SOLUSD":  "SOLUSDT",
    "AVAXUSD": "AVAXUSDT",
    "XMRUSD":  "XMRUSDT",
}

# Yahoo interval codes  (4H is not natively supported — fetch 1H + resample)
YAHOO_INTERVAL: dict = {
    "1h": "1h",
    "4h": "1h",    # fetch 1h → resample to 4H
    "1d": "1d",
    "1w": "1wk",
}

# Yahoo range string per effective timeframe
YAHOO_RANGE: dict = {
    "1h":  "60d",   # 60 days of hourly
    "4h":  "60d",   # need hourly to resample
    "1d":  "2y",    # 2 years of daily
    "1w":  "10y",
}

# Binance interval codes
BINANCE_INTERVAL: dict = {
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
}


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HTTP HEADERS
# ─────────────────────────────────────────────────────────────────────────────

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":           "application/json, text/html, */*;q=0.8",
    "Accept-Language":  "en-US,en;q=0.9",
    "Accept-Encoding":  "gzip, deflate, br",
    "Connection":       "keep-alive",
}


# ─────────────────────────────────────────────────────────────────────────────
# YAHOO FINANCE CLIENT  (persistent connection pool + rate limiter)
# ─────────────────────────────────────────────────────────────────────────────
# The v8/chart endpoint does NOT require crumb or cookies — it's open read access.
# Crumb is only needed for the v7/download CSV endpoint (which we don't use).
# We keep a persistent httpx client for connection reuse and add a 1.1s rate
# limiter to avoid Yahoo's rate-limit (typically ~60 req/min from a single IP).

_yf_client:    Optional[httpx.AsyncClient] = None
_yf_rate_lock  = asyncio.Lock()
_yf_last_call: float = 0.0
_YF_MIN_INTERVAL: float = 1.1   # seconds between Yahoo requests

# Binance concurrency limit
_BINANCE_SEM = asyncio.Semaphore(10)


async def _get_yf_client() -> httpx.AsyncClient:
    """Returns the shared Yahoo Finance httpx client, creating it if needed."""
    global _yf_client
    if _yf_client is None:
        _yf_client = httpx.AsyncClient(
            headers=_HTTP_HEADERS,
            follow_redirects=True,
            timeout=20.0,
        )
    return _yf_client


async def _yf_get(url: str) -> Optional[str]:
    """Rate-limited GET using the persistent Yahoo Finance client."""
    global _yf_last_call

    # Enforce 1.1s minimum between Yahoo requests
    async with _yf_rate_lock:
        now  = time.time()
        wait = _YF_MIN_INTERVAL - (now - _yf_last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _yf_last_call = time.time()

    client = await _get_yf_client()

    for attempt in range(3):
        try:
            resp = await client.get(url)

            if resp.status_code == 200:
                return resp.text

            if resp.status_code == 429:
                # Hard rate-limit — back off longer
                backoff = 15 * (attempt + 1)
                logger.warning(f"Yahoo 429 — backing off {backoff}s")
                await asyncio.sleep(backoff)
                continue

            logger.warning(f"Yahoo HTTP {resp.status_code} for {url}")
            break

        except Exception as exc:
            wait_s = 2 ** attempt
            if attempt < 2:
                logger.warning(f"Yahoo attempt {attempt + 1} failed: {exc} — retry in {wait_s}s")
                await asyncio.sleep(wait_s)
            else:
                logger.error(f"Yahoo all attempts failed ({url}): {exc}")

    return None


async def _http_get(url: str, timeout: float = 20.0) -> Optional[str]:
    """Simple one-shot async GET (used for Binance)."""
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                headers=_HTTP_HEADERS,
                timeout=timeout,
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.text
                logger.warning(f"HTTP {resp.status_code} for {url}")
        except Exception as exc:
            wait = 2 ** attempt
            if attempt < 2:
                logger.warning(f"HTTP attempt {attempt + 1} failed ({url}): {exc} — retry in {wait}s")
                await asyncio.sleep(wait)
            else:
                logger.error(f"HTTP all attempts failed ({url}): {exc}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# YAHOO FINANCE FETCHER  (FOREX / Indices / Commodities)
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_yahoo(
    symbol: str,
    interval: str = "1d",
    limit: int = 500,
) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV from Yahoo Finance v8 chart API.
    Returns DataFrame sorted ascending with [open, high, low, close, volume].

    interval: one of "1d", "1h", "4h", "1w"
    Note: 4h → fetches 1h data, caller resamples.
    """
    yf_interval = YAHOO_INTERVAL.get(interval, "1d")
    yf_range    = YAHOO_RANGE.get(interval, "2y")

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval={yf_interval}&range={yf_range}&includePrePost=false"
    )

    text = await _yf_get(url)
    if not text:
        logger.warning(f"Yahoo: empty response for {symbol} [{interval}]")
        return None

    try:
        data = json.loads(text)

        chart = data.get("chart", {})
        result = chart.get("result")
        if not result:
            err = chart.get("error", {})
            logger.warning(f"Yahoo: no result for {symbol} — {err}")
            return None

        r = result[0]
        timestamps = r.get("timestamp")
        if not timestamps:
            logger.warning(f"Yahoo: no timestamps for {symbol}")
            return None

        quote = r.get("indicators", {}).get("quote", [{}])[0]
        opens  = quote.get("open",   [])
        highs  = quote.get("high",   [])
        lows   = quote.get("low",    [])
        closes = quote.get("close",  [])
        vols   = quote.get("volume", [None] * len(timestamps))

        df = pd.DataFrame({
            "timestamp": pd.to_datetime(timestamps, unit="s"),
            "open":   [o if o is not None else np.nan for o in opens],
            "high":   [h if h is not None else np.nan for h in highs],
            "low":    [l if l is not None else np.nan for l in lows],
            "close":  [c if c is not None else np.nan for c in closes],
            "volume": [v if v is not None else 0.0    for v in vols],
        })

        df = df.set_index("timestamp")
        df.index.name = "timestamp"
        df = df.dropna(subset=["open", "high", "low", "close"])
        df = df.sort_index()

        if len(df) < 30:
            logger.warning(f"Yahoo: only {len(df)} candles for {symbol} [{interval}]")
            return None

        logger.info(f"Yahoo OK {symbol} [{interval}]: {len(df)} candles")
        return df.tail(limit).astype(float)

    except Exception as exc:
        logger.error(f"Yahoo parse error for {symbol}: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# BINANCE FETCHER  (Crypto only)
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_binance(
    symbol: str,
    timeframe: str = "1d",
    limit: int = 500,
) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV from Binance public API (no API key required).
    Endpoint: GET /api/v3/klines
    """
    interval = BINANCE_INTERVAL.get(timeframe, "1d")
    url = (
        f"https://api.binance.com/api/v3/klines"
        f"?symbol={symbol}&interval={interval}&limit={min(limit, 1000)}"
    )
    async with _BINANCE_SEM:
        text = await _http_get(url)

    if not text:
        logger.warning(f"Binance: empty response for {symbol}")
        return None

    try:
        data = json.loads(text)

        if isinstance(data, dict) and data.get("code"):
            logger.warning(f"Binance error for {symbol}: {data.get('msg')}")
            return None

        if not data:
            return None

        df = pd.DataFrame(data, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore",
        ])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.set_index("timestamp")
        df.index.name = "timestamp"
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        df = df.sort_index()

        if len(df) < 30:
            logger.warning(f"Binance: only {len(df)} candles for {symbol}")
            return None

        logger.info(f"Binance OK {symbol} [{timeframe}]: {len(df)} candles")
        return df

    except Exception as exc:
        logger.error(f"Binance parse error for {symbol}: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# RESAMPLE HELPER  (hourly → 4H)
# ─────────────────────────────────────────────────────────────────────────────

def _resample_to_4h(df: pd.DataFrame, limit: int) -> Optional[pd.DataFrame]:
    """Resample hourly OHLCV to 4-hour candles."""
    try:
        df4 = df.resample("4h").agg({
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }).dropna(subset=["open", "close"])

        if len(df4) < 20:
            return None

        return df4.tail(limit).astype(float)

    except Exception as exc:
        logger.error(f"Resample 4h error: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSAL ROUTER  (with caching)
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_ohlcv(
    symbol: str,
    market_type: str,
    timeframe: str = "1d",
    limit: int = 300,
) -> Optional[pd.DataFrame]:
    """
    Universal OHLCV fetcher with timeframe-aware caching.

    Routes:
      CRYPTO    → Binance public API
      FOREX     → Yahoo Finance v8 (EURUSD → EURUSD=X)
      INDICES   → Yahoo Finance v8 (SP500 → ^GSPC)
      COMMODITY → Yahoo Finance v8 (GOLD → GC=F)
    """
    market_type = market_type.upper()
    cache_key   = f"{symbol}_{market_type}_{timeframe}_{limit}"

    cached = _cache_get(cache_key, timeframe)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return cached

    df: Optional[pd.DataFrame] = None

    # ── CRYPTO: Binance ────────────────────────────────────────────────────────
    if market_type == "CRYPTO":
        binance_sym = BINANCE_SYMBOL_MAP.get(symbol)
        if binance_sym:
            df = await fetch_binance(binance_sym, timeframe, limit)
        else:
            logger.warning(f"No Binance mapping for: {symbol}")

    # ── FOREX: Yahoo Finance ───────────────────────────────────────────────────
    elif market_type == "FOREX":
        yf_sym    = _yf_forex(symbol)
        fetch_lim = limit * 4 if timeframe == "4h" else limit
        df        = await fetch_yahoo(yf_sym, timeframe, fetch_lim)
        if df is not None and timeframe == "4h":
            df = _resample_to_4h(df, limit)

    # ── INDICES: Yahoo Finance ─────────────────────────────────────────────────
    elif market_type == "INDICES":
        yf_sym    = YAHOO_INDICES_MAP.get(symbol, symbol)
        fetch_lim = limit * 4 if timeframe == "4h" else limit
        df        = await fetch_yahoo(yf_sym, timeframe, fetch_lim)
        if df is not None and timeframe == "4h":
            df = _resample_to_4h(df, limit)

    # ── COMMODITY: Yahoo Finance ───────────────────────────────────────────────
    else:
        yf_sym    = YAHOO_COMMODITY_MAP.get(symbol, f"{symbol}=F")
        fetch_lim = limit * 4 if timeframe == "4h" else limit
        df        = await fetch_yahoo(yf_sym, timeframe, fetch_lim)
        if df is not None and timeframe == "4h":
            df = _resample_to_4h(df, limit)

    if df is not None:
        _cache_set(cache_key, df)
    else:
        logger.debug(f"No data returned for {symbol} [{market_type}] [{timeframe}]")

    return df


# ─────────────────────────────────────────────────────────────────────────────
# BATCH CURRENT-PRICE FETCHER  (for live price refresh)
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_current_prices(
    symbols: List[Tuple[str, str]]  # [(athena_symbol, market_type), ...]
) -> Dict[str, float]:
    """
    Batch-fetch latest prices for a list of (symbol, market_type) pairs.
    - 1 Yahoo Finance v7/quote request for all FOREX/INDICES/COMMODITY symbols
    - 1 Binance ticker/price request for all CRYPTO symbols
    Returns {athena_symbol: price}.
    """
    import urllib.parse
    results: Dict[str, float] = {}

    yahoo_pairs = [(s, m) for s, m in symbols if m.upper() != "CRYPTO"]
    crypto_pairs = [(s, m) for s, m in symbols if m.upper() == "CRYPTO"]

    # ── Yahoo batch quote ─────────────────────────────────────────────────────
    if yahoo_pairs:
        yf_to_athena: Dict[str, str] = {}
        for athena_sym, mtype in yahoo_pairs:
            mt = mtype.upper()
            if mt == "FOREX":
                yf_sym = _yf_forex(athena_sym)
            elif mt == "INDICES":
                yf_sym = YAHOO_INDICES_MAP.get(athena_sym, athena_sym)
            else:
                yf_sym = YAHOO_COMMODITY_MAP.get(athena_sym, f"{athena_sym}=F")
            yf_to_athena[yf_sym] = athena_sym

        syms_param = ",".join(yf_to_athena.keys())
        url = (
            f"https://query1.finance.yahoo.com/v7/finance/quote"
            f"?symbols={syms_param}&fields=regularMarketPrice"
        )
        text = await _yf_get(url)
        if text:
            try:
                data = json.loads(text)
                quote_list = data.get("quoteResponse", {}).get("result", [])
                for item in quote_list:
                    yf_sym = item.get("symbol", "")
                    price  = item.get("regularMarketPrice")
                    if price is not None and yf_sym in yf_to_athena:
                        results[yf_to_athena[yf_sym]] = float(price)
            except Exception as exc:
                logger.error(f"Yahoo quote batch parse error: {exc}")

    # ── Binance batch ticker ──────────────────────────────────────────────────
    if crypto_pairs:
        binance_to_athena: Dict[str, str] = {}
        for athena_sym, _ in crypto_pairs:
            b_sym = BINANCE_SYMBOL_MAP.get(athena_sym)
            if b_sym:
                binance_to_athena[b_sym] = athena_sym

        if binance_to_athena:
            syms_json = json.dumps(list(binance_to_athena.keys()))
            url = (
                f"https://api.binance.com/api/v3/ticker/price"
                f"?symbols={urllib.parse.quote(syms_json)}"
            )
            text = await _http_get(url)
            if text:
                try:
                    data = json.loads(text)
                    for item in data:
                        b_sym = item.get("symbol", "")
                        price = item.get("price")
                        if price is not None and b_sym in binance_to_athena:
                            results[binance_to_athena[b_sym]] = float(price)
                except Exception as exc:
                    logger.error(f"Binance price batch parse error: {exc}")

    logger.info(f"fetch_current_prices: {len(results)}/{len(symbols)} prices fetched")
    return results
