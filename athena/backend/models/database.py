"""
ATHENA AI — Database Models & Init (SQLAlchemy async)
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Float, Integer, Boolean, DateTime, ForeignKey, Text, Enum
from datetime import datetime, timezone
from typing import Optional, List
import enum

from config import settings

engine = create_async_engine(
    "postgresql+asyncpg://postgres.yqjzrmmolbbgnipqrszt:Lafeuille100%25@aws-0-us-west-2.pooler.supabase.com:5432/postgres",
    connect_args={"ssl": "require", "statement_cache_size": 0},
    echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class TradeDirection(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradeGrade(str, enum.Enum):
    A = "A"
    B = "B"
    C = "C"


class TradeOutcome(str, enum.Enum):
    PENDING  = "PENDING"   # pas encore déterminé
    WIN_TP1  = "WIN_TP1"   # TP1 touché (+1.5R)
    WIN_TP2  = "WIN_TP2"   # TP2 touché (R:R complet)
    LOSS     = "LOSS"      # SL touché (-1R)
    EXPIRED  = "EXPIRED"   # expiré sans résultat


class MarketType(str, enum.Enum):
    FOREX = "FOREX"
    INDICES = "INDICES"
    CRYPTO = "CRYPTO"
    COMMODITY = "COMMODITY"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    expo_push_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    min_score_alert: Mapped[int] = mapped_column(Integer, default=70)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    alerts: Mapped[List["AlertConfig"]] = relationship("AlertConfig", back_populates="user")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    market_type: Mapped[MarketType] = mapped_column(Enum(MarketType))
    direction: Mapped[TradeDirection] = mapped_column(Enum(TradeDirection))
    grade: Mapped[TradeGrade] = mapped_column(Enum(TradeGrade))

    # Score breakdown
    score_total: Mapped[float] = mapped_column(Float)
    score_rsi: Mapped[float] = mapped_column(Float)
    score_macd: Mapped[float] = mapped_column(Float)
    score_ema: Mapped[float] = mapped_column(Float)
    score_sr: Mapped[float] = mapped_column(Float)
    score_trend: Mapped[float] = mapped_column(Float, default=0.0)
    score_bollinger: Mapped[float] = mapped_column(Float, default=0.0)
    score_candle: Mapped[float] = mapped_column(Float, default=0.0)
    score_volume: Mapped[float] = mapped_column(Float, default=0.0)
    score_calendar: Mapped[float] = mapped_column(Float)
    score_sentiment: Mapped[float] = mapped_column(Float)
    confluence_count: Mapped[int] = mapped_column(Integer, default=0)

    # Price levels
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit_1: Mapped[float] = mapped_column(Float)
    take_profit_2: Mapped[float] = mapped_column(Float)
    risk_reward: Mapped[float] = mapped_column(Float)

    # Context
    current_price: Mapped[float] = mapped_column(Float)
    timeframe: Mapped[str] = mapped_column(String(10))
    reasoning: Mapped[str] = mapped_column(Text)  # JSON string of signal descriptions

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Outcome tracking
    outcome: Mapped[str] = mapped_column(String(20), default="PENDING")
    outcome_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    outcome_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    pnl_r: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # ex: +2.5, -1.0


class UserTradeAction(Base):
    __tablename__ = "user_trade_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    trade_id: Mapped[int] = mapped_column(Integer, ForeignKey("trades.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(10))  # "TAKEN" | "PASSED"
    acted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AlertConfig(Base):
    __tablename__ = "alert_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    min_score: Mapped[int] = mapped_column(Integer, default=70)
    markets: Mapped[str] = mapped_column(String(255), default="FOREX,INDICES,CRYPTO,COMMODITY")
    directions: Mapped[str] = mapped_column(String(50), default="LONG,SHORT")

    user: Mapped["User"] = relationship("User", back_populates="alerts")


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Safe migration: add new columns if they don't exist yet
        for col, col_type in [("score_candle", "FLOAT DEFAULT 0.0")]:
            try:
                await conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE trades ADD COLUMN IF NOT EXISTS {col} {col_type}"
                    )
                )
            except Exception:
                pass
