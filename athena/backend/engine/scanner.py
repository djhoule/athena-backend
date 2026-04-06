"""
ATHENA AI — Market Scanner (v2 — Multi-Timeframe)
==================================================
Logique MTF (Multi-TimeFrame):
  - FOREX      : 1D seulement (Frankfurter ne fournit que des cours journaliers)
  - CRYPTO / INDICES / COMMODITIES :
      1. Scan 1D  → détermine le bias directionnel
      2. Scan 4H  → doit confirmer la même direction
      3. Si les deux s'accordent → trade validé (score boosté +5%)
      4. Si désaccord           → trade rejeté

Cela garantit qu'on ne prend jamais un trade 4H contre la tendance journalière.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from config import settings
from engine.data_fetcher import fetch_ohlcv
from engine.technical import calculate_technicals
from engine.fundamental import fetch_forex_factory_events, get_fundamental_signals
from engine.scorer import calculate_score
from models.database import AsyncSessionLocal, Trade, MarketType, TradeDirection, TradeGrade
from engine.notifications import send_trade_alerts, send_discord_alert

logger = logging.getLogger(__name__)

WATCHLIST = (
    [(sym, "FOREX")     for sym in settings.FOREX_PAIRS] +
    [(sym, "INDICES")   for sym in settings.INDICES] +
    [(sym, "CRYPTO")    for sym in settings.CRYPTO_PAIRS] +   # 1D only (like FOREX)
    [(sym, "COMMODITY") for sym in settings.COMMODITIES]
)


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE TIMEFRAME SCAN (helper)
# ─────────────────────────────────────────────────────────────────────────────

async def _scan_tf(
    symbol: str,
    market_type: str,
    timeframe: str,
    fundamental: Dict,
) -> Optional[Dict[str, Any]]:
    """Scan a single symbol on a single timeframe. Returns scorer result or None."""
    df = await fetch_ohlcv(symbol, market_type, timeframe=timeframe, limit=300)
    if df is None or len(df) < 60:
        return None

    technical = calculate_technicals(df)
    if not technical:
        return None

    return calculate_score(
        technical, fundamental,
        min_score=settings.MIN_SCORE_THRESHOLD,
        min_rr=settings.MIN_RISK_REWARD,
        symbol=symbol,
    )


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-TIMEFRAME SCAN (main logic per symbol)
# ─────────────────────────────────────────────────────────────────────────────

async def scan_symbol_mtf(
    symbol: str,
    market_type: str,
    all_events: List[Dict],
) -> Optional[Dict[str, Any]]:
    """
    Multi-timeframe scan for a single symbol.

    FOREX  → 1D only  (Frankfurter données journalières uniquement)
    Others → 1D bias + 4H entry confirmation required
    """
    try:
        # Fetch fundamental once (shared across both timeframes)
        fundamental = await get_fundamental_signals(symbol, market_type, all_events)

        # ── 1D scan ───────────────────────────────────────────────────────────
        result_1d = await _scan_tf(symbol, market_type, "1d", fundamental)

        # ── FOREX + CRYPTO → 1D only ──────────────────────────────────────────
        if market_type in ("FOREX", "CRYPTO"):
            if result_1d is None:
                return None
            return {
                "symbol": symbol,
                "market_type": market_type,
                "timeframe": "1D",
                "mtf_confirmed": False,
                **result_1d,
            }

        # ── Others: require 4H agreement ──────────────────────────────────────
        if result_1d is None:
            return None  # 1D must pass first (trend filter)

        direction_1d = result_1d["direction"]

        result_4h = await _scan_tf(symbol, market_type, "4h", fundamental)

        if result_4h is None:
            # 4H alone doesn't qualify — still offer the 1D trade but unconfirmed
            # (lower priority, no score boost)
            return {
                "symbol": symbol,
                "market_type": market_type,
                "timeframe": "1D",
                "mtf_confirmed": False,
                **result_1d,
            }

        # Both 1D and 4H must agree on direction
        if result_4h["direction"] != direction_1d:
            logger.debug(
                f"{symbol}: MTF disagreement — 1D={direction_1d} vs 4H={result_4h['direction']}"
            )
            return None  # Conflicting timeframes → skip

        # ── MTF confirmed: use 4H levels + 5% score boost ─────────────────────
        boosted_score = min(round(result_4h["score_total"] * 1.05, 1), 100.0)

        # Enrich reasoning with MTF context
        reasoning_dict = json.loads(result_4h["reasoning"])
        reasoning_dict["mtf_confirmation"] = {
            "confirmed": True,
            "direction_1d": direction_1d,
            "score_1d": result_1d["score_total"],
            "score_4h": result_4h["score_total"],
            "note": (
                f"1D ({result_1d['score_total']:.0f}pts) et 4H ({result_4h['score_total']:.0f}pts) "
                f"alignés {direction_1d} — confirmation multi-timeframe"
            ),
        }
        # Append to analysis summary
        summary = reasoning_dict.get("analysis_summary", "")
        reasoning_dict["analysis_summary"] = summary + " ✅ Confirmation 1D+4H alignés."

        return {
            "symbol": symbol,
            "market_type": market_type,
            "timeframe": "4H+1D",
            "mtf_confirmed": True,
            **result_4h,
            "score_total": boosted_score,
            "reasoning": json.dumps(reasoning_dict, ensure_ascii=False),
        }

    except Exception as e:
        logger.error(f"scan_symbol_mtf error for {symbol}: {e}", exc_info=True)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SCAN — called every 15 minutes by APScheduler
# ─────────────────────────────────────────────────────────────────────────────

async def run_scan():
    """
    Main scan function — called every 15 minutes.
    Scans all watchlist symbols with MTF logic, saves top N trades to DB.
    """
    logger.info(f"🔍 Athena MTF scan started at {datetime.now(timezone.utc).isoformat()}")

    try:
        # Fetch economic calendar once (shared across all symbols)
        all_events = await fetch_forex_factory_events()

        # Scan all symbols concurrently
        tasks = [
            scan_symbol_mtf(symbol, market_type, all_events)
            for symbol, market_type in WATCHLIST
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter valid results — MTF-confirmed first, then 1D-only
        valid = [r for r in results if isinstance(r, dict)]

        # Sort: MTF confirmed first, then by score descending
        valid.sort(key=lambda x: (x.get("mtf_confirmed", False), x["score_total"]), reverse=True)

        # Deduplicate: keep best result per symbol
        seen: Dict[str, Dict] = {}
        for r in valid:
            sym = r["symbol"]
            if sym not in seen:
                seen[sym] = r
            else:
                # Prefer MTF confirmed over unconfirmed
                if r.get("mtf_confirmed") and not seen[sym].get("mtf_confirmed"):
                    seen[sym] = r

        top_trades = list(seen.values())[:settings.MAX_TRADES_OUTPUT]

        # Always deactivate old active trades first (avoids stale data with 0-scores)
        async with AsyncSessionLocal() as session:
            from sqlalchemy import update
            await session.execute(
                update(Trade).where(Trade.is_active == True).values(is_active=False)
            )
            await session.commit()

        if not top_trades:
            logger.info("No qualifying trades found this scan.")
            return

        # Save to database
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select

            # Load existing PENDING trades to avoid duplicates (same symbol+direction)
            existing = await session.execute(
                select(Trade.symbol, Trade.direction).where(Trade.outcome == "PENDING")
            )
            pending_keys = {(row.symbol, row.direction.value) for row in existing}

            new_trades = []
            for t in top_trades:
                key = (t["symbol"], t["direction"])
                if key in pending_keys:
                    logger.debug(f"Skipping duplicate PENDING: {t['symbol']} {t['direction']}")
                    continue
                trade = Trade(
                    symbol=t["symbol"],
                    market_type=MarketType(t["market_type"]),
                    direction=TradeDirection(t["direction"]),
                    grade=TradeGrade(t["grade"]),
                    score_total=t["score_total"],
                    score_rsi=t["score_rsi"],
                    score_macd=t["score_macd"],
                    score_ema=t["score_ema"],
                    score_sr=t["score_sr"],
                    score_trend=t.get("score_trend", 0.0),
                    score_bollinger=t.get("score_bollinger", 0.0),
                    score_volume=t.get("score_volume", 0.0),
                    score_calendar=t["score_calendar"],
                    score_sentiment=t["score_sentiment"],
                    confluence_count=t.get("confluence_count", 0),
                    entry_price=t["entry"],
                    stop_loss=t["stop_loss"],
                    take_profit_1=t["take_profit_1"],
                    take_profit_2=t["take_profit_2"],
                    risk_reward=t["risk_reward"],
                    current_price=t["entry"],
                    timeframe=t["timeframe"],
                    reasoning=t["reasoning"],
                    is_active=True,
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=16),
                )
                session.add(trade)
                new_trades.append(trade)

            await session.commit()

            mtf_count = sum(1 for t in top_trades if t.get("mtf_confirmed"))
            logger.info(
                f"✅ Saved {len(new_trades)} trades "
                f"({mtf_count} MTF confirmed): {[t['symbol'] for t in top_trades]}"
            )

        await send_trade_alerts(top_trades)
        await send_discord_alert(top_trades)

    except Exception as e:
        logger.error(f"run_scan error: {e}", exc_info=True)
