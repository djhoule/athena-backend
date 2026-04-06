"""
ATHENA AI — Performance Stats Router
GET /trades/stats  → win rate, total R, breakdown par marché/grade
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional
from datetime import datetime, timezone, timedelta

from models.database import get_db, Trade, MarketType

router = APIRouter()


def _streak(outcomes: list[str]) -> tuple[int, int]:
    """Returns (current_streak, best_streak). Positive = wins, negative = losses."""
    if not outcomes:
        return 0, 0

    best = 0
    current = 0
    for o in outcomes:
        is_win = o in ("WIN_TP1", "WIN_TP2")
        if is_win:
            current = max(current + 1, 1)
        elif o == "LOSS":
            current = min(current - 1, -1)
        else:
            current = 0
        if abs(current) > abs(best):
            best = current

    return current, best


@router.get("/stats")
async def get_performance_stats(
    days: int = Query(90, description="Période en jours (défaut 90)"),
    market_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns aggregated performance statistics for resolved trades.
    Only counts WIN_TP1, WIN_TP2, and LOSS outcomes (not PENDING/EXPIRED).
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Base query
    query = select(Trade).where(Trade.created_at >= since)
    if market_type:
        try:
            query = query.where(Trade.market_type == MarketType(market_type.upper()))
        except ValueError:
            pass

    result = await db.execute(query.order_by(Trade.created_at))
    all_trades = result.scalars().all()

    # Split by outcome category
    resolved   = [t for t in all_trades if t.outcome in ("WIN_TP1", "WIN_TP2", "LOSS")]
    wins       = [t for t in resolved if t.outcome in ("WIN_TP1", "WIN_TP2")]
    tp2_wins   = [t for t in resolved if t.outcome == "WIN_TP2"]
    tp1_wins   = [t for t in resolved if t.outcome == "WIN_TP1"]
    losses     = [t for t in resolved if t.outcome == "LOSS"]
    expired    = [t for t in all_trades if t.outcome == "EXPIRED"]
    pending    = [t for t in all_trades if t.outcome == "PENDING"]

    total_r    = sum(t.pnl_r for t in resolved if t.pnl_r is not None)
    win_rate   = (len(wins) / len(resolved) * 100) if resolved else 0.0
    avg_r      = (total_r / len(resolved)) if resolved else 0.0

    # Streak (chronological resolved trades)
    outcome_sequence = [t.outcome for t in resolved]
    current_streak, best_streak = _streak(outcome_sequence)

    # Breakdown by market
    by_market = {}
    for mtype in ("FOREX", "INDICES", "CRYPTO", "COMMODITY"):
        m_resolved = [t for t in resolved if t.market_type.value == mtype]
        m_wins     = [t for t in m_resolved if t.outcome in ("WIN_TP1", "WIN_TP2")]
        m_r        = sum(t.pnl_r for t in m_resolved if t.pnl_r is not None)
        by_market[mtype] = {
            "trades":   len(m_resolved),
            "wins":     len(m_wins),
            "losses":   len(m_resolved) - len(m_wins),
            "win_rate": round(len(m_wins) / len(m_resolved) * 100, 1) if m_resolved else 0.0,
            "total_r":  round(m_r, 2),
        }

    # Breakdown by grade
    by_grade = {}
    for grade in ("A", "B"):
        g_resolved = [t for t in resolved if t.grade.value == grade]
        g_wins     = [t for t in g_resolved if t.outcome in ("WIN_TP1", "WIN_TP2")]
        g_r        = sum(t.pnl_r for t in g_resolved if t.pnl_r is not None)
        by_grade[grade] = {
            "trades":   len(g_resolved),
            "wins":     len(g_wins),
            "losses":   len(g_resolved) - len(g_wins),
            "win_rate": round(len(g_wins) / len(g_resolved) * 100, 1) if g_resolved else 0.0,
            "total_r":  round(g_r, 2),
        }

    return {
        "period_days":      days,
        "total_trades":     len(all_trades),
        "resolved_trades":  len(resolved),
        "pending_trades":   len(pending),
        "expired_trades":   len(expired),
        "win_rate":         round(win_rate, 1),
        "wins":             len(wins),
        "wins_tp1":         len(tp1_wins),
        "wins_tp2":         len(tp2_wins),
        "losses":           len(losses),
        "total_r":          round(total_r, 2),
        "avg_r_per_trade":  round(avg_r, 2),
        "current_streak":   current_streak,
        "best_streak":      best_streak,
        "by_market":        by_market,
        "by_grade":         by_grade,
    }
