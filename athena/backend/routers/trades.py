"""
ATHENA AI — Trades Router
GET /trades/top     → Top active trades (2-3)
GET /trades/history → All past trades
GET /trades/{id}    → Single trade detail
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
import json

from models.database import get_db, Trade, MarketType, TradeDirection, UserTradeAction
from routers.auth import get_current_user
from models.database import User

router = APIRouter()


# ── Pydantic Response Schemas ─────────────────────────────────────────────────

class ReasoningItem(BaseModel):
    score: float
    reason: str


class TradeResponse(BaseModel):
    id: int
    symbol: str
    market_type: str
    direction: str
    grade: str
    score_total: float
    score_rsi: float
    score_macd: float
    score_ema: float
    score_sr: float
    score_trend: float = 0.0
    score_bollinger: float = 0.0
    score_candle: float = 0.0
    score_ichimoku: float = 0.0
    score_volume: float = 0.0
    score_calendar: float
    score_sentiment: float
    confluence_count: int = 0
    outcome: str = "PENDING"
    outcome_price: Optional[float] = None
    outcome_at: Optional[datetime] = None
    pnl_r: Optional[float] = None
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward: float
    current_price: float
    timeframe: str
    reasoning: dict
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_with_reasoning(cls, trade: Trade):
        data = {
            "id": trade.id,
            "symbol": trade.symbol,
            "market_type": trade.market_type.value,
            "direction": trade.direction.value,
            "grade": trade.grade.value,
            "score_total": trade.score_total,
            "score_rsi": trade.score_rsi,
            "score_macd": trade.score_macd,
            "score_ema": trade.score_ema,
            "score_sr": trade.score_sr,
            "score_trend": getattr(trade, "score_trend", 0.0),
            "score_bollinger": getattr(trade, "score_bollinger", 0.0),
            "score_candle":    getattr(trade, "score_candle",   0.0),
            "score_ichimoku":  getattr(trade, "score_ichimoku", 0.0),
            "score_volume":    getattr(trade, "score_volume",   0.0),
            "score_calendar": trade.score_calendar,
            "score_sentiment": trade.score_sentiment,
            "confluence_count": getattr(trade, "confluence_count", 0),
            "outcome":          getattr(trade, "outcome", "PENDING"),
            "outcome_price":    getattr(trade, "outcome_price", None),
            "outcome_at":       getattr(trade, "outcome_at", None),
            "pnl_r":            getattr(trade, "pnl_r", None),
            "entry_price": trade.entry_price,
            "stop_loss": trade.stop_loss,
            "take_profit_1": trade.take_profit_1,
            "take_profit_2": trade.take_profit_2,
            "risk_reward": trade.risk_reward,
            "current_price": trade.current_price,
            "timeframe": trade.timeframe,
            "reasoning": json.loads(trade.reasoning) if trade.reasoning else {},
            "is_active": trade.is_active,
            "created_at": trade.created_at,
        }
        return data


@router.get("/top", response_model=List[dict])
async def get_top_trades(db: AsyncSession = Depends(get_db)):
    """Returns the current top 2-3 active high-probability trades."""
    result = await db.execute(
        select(Trade)
        .where(Trade.is_active == True)
        .order_by(desc(Trade.score_total))
        .limit(10)
    )
    trades = result.scalars().all()
    return [TradeResponse.from_orm_with_reasoning(t) for t in trades]


@router.get("/history", response_model=List[dict])
async def get_trade_history(
    limit: int = Query(50, le=200),
    market_type: Optional[str] = None,
    direction: Optional[str] = None,
    min_grade: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Returns historical trades with optional filters."""
    query = select(Trade).order_by(desc(Trade.created_at)).limit(limit)

    if market_type:
        query = query.where(Trade.market_type == MarketType(market_type.upper()))
    if direction:
        query = query.where(Trade.direction == TradeDirection(direction.upper()))
    if min_grade == "A":
        query = query.where(Trade.score_total >= 80)
    elif min_grade == "B":
        query = query.where(Trade.score_total >= 65)

    result = await db.execute(query)
    trades = result.scalars().all()
    return [TradeResponse.from_orm_with_reasoning(t) for t in trades]


@router.post("/{trade_id}/action", response_model=dict)
async def record_trade_action(
    trade_id: int,
    action: str = Query(..., regex="^(TAKEN|PASSED)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record whether the user took or passed this trade. Upserts if already recorded."""
    from sqlalchemy import and_
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    # Upsert: update action if the user already acted on this trade
    result = await db.execute(
        select(UserTradeAction).where(
            and_(UserTradeAction.user_id == current_user.id,
                 UserTradeAction.trade_id == trade_id)
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.action = action
        from datetime import datetime, timezone
        existing.acted_at = datetime.now(timezone.utc)
    else:
        db.add(UserTradeAction(user_id=current_user.id, trade_id=trade_id, action=action))

    await db.commit()
    return {"trade_id": trade_id, "action": action, "status": "ok"}


@router.delete("/reset", response_model=dict)
async def reset_all_trades(db: AsyncSession = Depends(get_db)):
    """Deletes ALL trades from the database. Use for testing/reset only."""
    from sqlalchemy import delete
    await db.execute(delete(Trade))
    await db.commit()
    return {"status": "ok", "message": "All trades deleted."}


@router.get("/live-prices", response_model=dict)
async def get_live_prices(db: AsyncSession = Depends(get_db)):
    """Returns current market prices for all active trades (for real-time refresh)."""
    from engine.data_fetcher import fetch_current_prices

    result = await db.execute(
        select(Trade.symbol, Trade.market_type)
        .where(Trade.is_active == True)
        .distinct()
    )
    rows = result.all()
    if not rows:
        return {"prices": {}}

    symbol_pairs = [(row.symbol, row.market_type.value) for row in rows]
    prices = await fetch_current_prices(symbol_pairs)
    return {"prices": prices}


@router.get("/{trade_id}", response_model=dict)
async def get_trade(trade_id: int, db: AsyncSession = Depends(get_db)):
    """Returns full detail of a single trade."""
    result = await db.execute(select(Trade).where(Trade.id == trade_id))
    trade = result.scalar_one_or_none()
    if not trade:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Trade not found")
    return TradeResponse.from_orm_with_reasoning(trade)
