"""
ATHENA AI — Outcome Checker
============================
Vérifie si les trades PENDING ont touché leur SL ou TP.

Logique (candle par candle, ordre chronologique) :
  - LONG  : low  <= stop_loss     → LOSS  (-1R)
             high >= take_profit_2 → WIN_TP2 (R:R complet)
             high >= take_profit_1 → WIN_TP1 (+1.5R)
  - SHORT : high >= stop_loss     → LOSS  (-1R)
             low  <= take_profit_2 → WIN_TP2 (R:R complet)
             low  <= take_profit_1 → WIN_TP1 (+1.5R)

  Si SL et TP sont tous les deux touchés dans le même candle → LOSS (conservateur).
  Si expiré sans résultat                                    → EXPIRED (0R).

Appelé toutes les 30 minutes par APScheduler.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import AsyncSessionLocal, Trade, TradeOutcome
from engine.data_fetcher import fetch_ohlcv

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# OUTCOME DETERMINATION
# ─────────────────────────────────────────────────────────────────────────────

def _determine_outcome(
    trade: Trade,
    candles,  # pd.DataFrame with high/low columns, chronological
) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """
    Walks through candles chronologically to determine outcome.
    Returns (outcome, pnl_r, outcome_price) or (None, None, None) if still open.
    """
    is_long = trade.direction.value == "LONG"

    for _, row in candles.iterrows():
        high = float(row["high"])
        low  = float(row["low"])

        if is_long:
            if low <= trade.stop_loss:
                return "LOSS", -1.0, trade.stop_loss
            if high >= trade.take_profit_2:
                return "WIN_TP2", round(trade.risk_reward, 2), trade.take_profit_2
            if high >= trade.take_profit_1:
                return "WIN_TP1", 1.5, trade.take_profit_1
        else:  # SHORT
            if high >= trade.stop_loss:
                return "LOSS", -1.0, trade.stop_loss
            if low <= trade.take_profit_2:
                return "WIN_TP2", round(trade.risk_reward, 2), trade.take_profit_2
            if low <= trade.take_profit_1:
                return "WIN_TP1", 1.5, trade.take_profit_1

    return None, None, None  # still open


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CHECKER
# ─────────────────────────────────────────────────────────────────────────────

async def check_outcomes():
    """
    Fetches all PENDING trades and tries to resolve their outcome.
    Called every 30 minutes by the scheduler.
    """
    now = datetime.now(timezone.utc)
    resolved = 0
    errors   = 0

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Trade).where(Trade.outcome == "PENDING")
            )
            pending_trades = result.scalars().all()

        if not pending_trades:
            logger.info("Outcome checker: no PENDING trades to check.")
            return

        logger.info(f"Outcome checker: checking {len(pending_trades)} PENDING trades…")

        for trade in pending_trades:
            try:
                # Determine market_type string
                market_type = trade.market_type.value

                # Fetch OHLCV since trade creation (1H for intraday resolution)
                # Use 1H for crypto/indices, 1D for forex (data limitation)
                timeframe = "1d" if market_type == "FOREX" else "1h"
                df = await fetch_ohlcv(trade.symbol, market_type, timeframe=timeframe, limit=100)

                if df is None or df.empty:
                    logger.warning(f"No price data for {trade.symbol}, skipping.")
                    continue

                # Only keep candles after trade creation
                df_since = df[df.index > trade.created_at.replace(tzinfo=None)]
                if df_since.empty:
                    # If trade just created and no new candle yet, skip
                    if trade.expires_at and now > trade.expires_at:
                        # Expired with no data → EXPIRED
                        await _update_outcome(trade.id, "EXPIRED", 0.0, trade.entry_price)
                        resolved += 1
                    continue

                outcome, pnl_r, outcome_price = _determine_outcome(trade, df_since)

                if outcome:
                    await _update_outcome(trade.id, outcome, pnl_r, outcome_price)
                    resolved += 1
                    logger.info(f"  {trade.symbol} {trade.direction.value}: {outcome} ({pnl_r:+.1f}R)")
                elif trade.expires_at and now > trade.expires_at + timedelta(hours=4):
                    # Grace period: wait 4h after expiry before marking EXPIRED
                    await _update_outcome(trade.id, "EXPIRED", 0.0, trade.entry_price)
                    resolved += 1
                    logger.info(f"  {trade.symbol}: EXPIRED (no level hit)")

            except Exception as e:
                logger.error(f"Outcome check error for trade {trade.id} ({trade.symbol}): {e}")
                errors += 1

        logger.info(f"Outcome checker done: {resolved} resolved, {errors} errors.")

    except Exception as e:
        logger.error(f"check_outcomes fatal error: {e}", exc_info=True)


async def _update_outcome(
    trade_id: int,
    outcome: str,
    pnl_r: Optional[float],
    outcome_price: Optional[float],
):
    """Writes the outcome to the database."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Trade)
            .where(Trade.id == trade_id)
            .values(
                outcome=outcome,
                pnl_r=pnl_r,
                outcome_price=outcome_price,
                outcome_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
