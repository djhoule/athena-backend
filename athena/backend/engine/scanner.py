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
from engine.notifications import send_trade_alerts, send_discord_alert, calculate_lot_size

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
        min_confluence=settings.MIN_CONFLUENCE,
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
                if r.get("mtf_confirmed") and not seen[sym].get("mtf_confirmed"):
                    seen[sym] = r

        deduped = list(seen.values())

        # ── Correlation filter ────────────────────────────────────────────────
        # Prevent showing multiple correlated trades (same underlying risk).
        # Groups: US indices / EUR indices / crypto majors / energy / metals.
        # For FOREX: if two pairs share a currency moving the same way → keep top.
        CORR_GROUPS = [
            {"SP500", "NAS100", "US30", "US2000"},
            {"DAX", "EU50", "IBEX35", "AEX", "IX00"},
            {"JP225", "HK33", "AU200", "CN50"},
            {"BTCUSD", "ETHUSD"},
            {"USOIL", "UKOIL"},
            {"GOLD", "SILVER", "PLATINUM"},
        ]

        def forex_currency_exposures(symbol: str, direction: str):
            """Returns set of (currency, side) tuples for a FOREX pair."""
            if len(symbol) == 6:
                base, quote = symbol[:3], symbol[3:]
                if direction == "LONG":
                    return {(base, "up"), (quote, "down")}
                return {(base, "down"), (quote, "up")}
            return set()

        filtered: List[Dict] = []
        used_corr_groups: set = set()
        used_forex_exposures: set = set()

        for trade in deduped:
            sym = trade["symbol"]
            mtype = trade["market_type"]
            direction = trade["direction"]

            # Check non-FOREX correlation groups
            blocked = False
            for gi, group in enumerate(CORR_GROUPS):
                if sym in group:
                    key = (gi, direction)
                    if key in used_corr_groups:
                        logger.debug(f"Correlation filter: skipping {sym} ({direction}) — group already represented")
                        blocked = True
                        break
                    used_corr_groups.add(key)
                    break

            if blocked:
                continue

            # FOREX: check currency exposure overlap
            if mtype == "FOREX":
                exposures = forex_currency_exposures(sym, direction)
                overlap = exposures & used_forex_exposures
                if overlap:
                    logger.debug(f"Correlation filter: skipping {sym} ({direction}) — currency overlap {overlap}")
                    continue
                used_forex_exposures |= exposures

            filtered.append(trade)

        top_trades = filtered[:settings.MAX_TRADES_OUTPUT]

        # Keys of trades that qualify this scan
        top_keys = {(t["symbol"], t["direction"]) for t in top_trades}

        # ── Step 1: deactivate only trades that DROPPED OUT of the top list ─────
        # (previously we deactivated ALL then re-skipped duplicates → trades went
        #  invisible even though the same setup was still the best pick)
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select as sa_select, update as sa_update
            result = await session.execute(
                sa_select(Trade).where(Trade.is_active == True)
            )
            for active in result.scalars().all():
                if (active.symbol, active.direction.value) not in top_keys:
                    active.is_active = False
            await session.commit()

        if not top_trades:
            logger.info("No qualifying trades found this scan.")
            return

        # ── Step 2: upsert — re-activate existing PENDING or create new ─────────
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select as sa_select

            # Load existing PENDING trades as full objects (need to update them)
            result = await session.execute(
                sa_select(Trade).where(Trade.outcome == "PENDING")
            )
            pending_map: Dict[tuple, Trade] = {
                (row.symbol, row.direction.value): row
                for row in result.scalars().all()
            }

            new_count = updated_count = 0
            for t in top_trades:
                key   = (t["symbol"], t["direction"])
                now16 = datetime.now(timezone.utc) + timedelta(hours=16)

                # ── Calcul du lot size pour risquer 1 000 USD ────────────────
                lot = calculate_lot_size(
                    entry_price=t["entry"],
                    stop_loss=t["stop_loss"],
                    risk_usd=1000,
                    atr=t.get("atr"),
                )

                if key in pending_map:
                    # ── Re-activate + refresh existing PENDING trade ──────────
                    ex = pending_map[key]
                    ex.is_active        = True
                    ex.grade            = TradeGrade(t["grade"])
                    ex.score_total      = t["score_total"]
                    ex.score_rsi        = t["score_rsi"]
                    ex.score_macd       = t["score_macd"]
                    ex.score_ema        = t["score_ema"]
                    ex.score_sr         = t["score_sr"]
                    ex.score_trend      = t.get("score_trend", 0.0)
                    ex.score_bollinger  = t.get("score_bollinger", 0.0)
                    ex.score_candle     = t.get("score_candle", 0.0)
                    ex.score_ichimoku   = t.get("score_ichimoku", 0.0)
                    ex.score_volume     = t.get("score_volume", 0.0)
                    ex.score_calendar   = t["score_calendar"]
                    ex.score_sentiment  = t["score_sentiment"]
                    ex.confluence_count = t.get("confluence_count", 0)
                    ex.entry_price      = t["entry"]
                    ex.stop_loss        = t["stop_loss"]
                    ex.take_profit_1    = t["take_profit_1"]
                    ex.take_profit_2    = t["take_profit_2"]
                    ex.risk_reward      = t["risk_reward"]
                    ex.current_price    = t["entry"]
                    ex.timeframe        = t["timeframe"]
                    ex.reasoning        = t["reasoning"]
                    ex.expires_at       = now16
                    ex.lot_size         = lot   # mise à jour du lot size
                    updated_count += 1
                else:
                    # ── Brand-new setup → create Trade record ─────────────────
                    session.add(Trade(
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
                        score_candle=t.get("score_candle", 0.0),
                        score_ichimoku=t.get("score_ichimoku", 0.0),
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
                        lot_size=lot,        # Amélioration 1 — lot size
                        is_active=True,
                        expires_at=now16,
                        # discord_message_id laissé NULL → sera rempli après envoi Discord
                    ))
                    new_count += 1

            await session.commit()

            mtf_count = sum(1 for t in top_trades if t.get("mtf_confirmed"))
            logger.info(
                f"✅ {new_count} new + {updated_count} refreshed trades "
                f"({mtf_count} MTF confirmed): {[t['symbol'] for t in top_trades]}"
            )

        # ── Expo push (Grade A seulement) ─────────────────────────────────────
        await send_trade_alerts(top_trades)

        # ── Discord : un message par trade Grade A jamais encore notifié ──────
        # On interroge la DB pour avoir les objets ORM avec leurs id réels,
        # puis on filtre sur discord_message_id IS NULL (pas encore envoyé).
        # Cela couvre aussi les trades B qui seraient montés en Grade A depuis
        # le dernier scan, sans qu'on leur envoie un doublon.
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select as sa_select

            result = await session.execute(
                sa_select(Trade).where(
                    Trade.grade      == TradeGrade.A,
                    Trade.outcome    == "PENDING",
                    Trade.is_active  == True,
                    Trade.discord_message_id == None,
                )
            )
            new_grade_a_trades = result.scalars().all()

            if new_grade_a_trades:
                logger.info(
                    f"Discord : {len(new_grade_a_trades)} nouveau(x) Grade A "
                    f"à notifier : {[t.symbol for t in new_grade_a_trades]}"
                )
                # Envoi individuel — retourne {trade_id: discord_message_id}
                id_to_msg = await send_discord_alert(new_grade_a_trades)

                # Persistance des message_ids en DB
                for trade_obj in new_grade_a_trades:
                    msg_id = id_to_msg.get(trade_obj.id)
                    if msg_id:
                        trade_obj.discord_message_id = msg_id

                await session.commit()
                logger.info(
                    f"Discord message_ids enregistrés pour "
                    f"{len(id_to_msg)} trade(s)"
                )
            else:
                logger.debug("Discord : aucun nouveau Grade A à notifier ce scan.")

    except Exception as e:
        logger.error(f"run_scan error: {e}", exc_info=True)
