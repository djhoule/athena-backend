"""
ATHENA AI — Configuration
"""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost/athena"

    # ── Auth ──────────────────────────────────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production-use-32-char-minimum"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 jours

    # ── Market Data APIs ──────────────────────────────────────────────────────
    POLYGON_API_KEY: str = ""
    ALPHA_VANTAGE_KEY: str = ""
    BINANCE_API_KEY: str = ""

    # ── Fundamental Data ──────────────────────────────────────────────────────
    NEWS_API_KEY: str = ""
    TRADING_ECONOMICS_KEY: str = ""

    # ── Push Notifications ────────────────────────────────────────────────────
    EXPO_ACCESS_TOKEN: str = ""

    # ── Scan Parameters ───────────────────────────────────────────────────────
    SCAN_INTERVAL_MINUTES: int = 15
    MIN_SCORE_THRESHOLD: int = 72
    MAX_TRADES_OUTPUT: int = 10
    MIN_RISK_REWARD: float = 2.0

    # ── Watchlist ─────────────────────────────────────────────────────────────

    FOREX_PAIRS: List[str] = [
        # Majeurs — core
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
        "AUDUSD", "NZDUSD", "USDCAD",
        # Croisées — diversification
        "EURJPY", "GBPJPY", "EURGBP",
        "AUDJPY", "CADJPY", "EURAUD",
        "GBPAUD", "AUDCAD", "NZDJPY",
    ]

    INDICES: List[str] = [
        # Core US — via ETF Yahoo Finance
        "SPY",      # S&P 500
        "QQQ",      # NASDAQ 100
        "DIA",      # Dow Jones US30
        "IWM",      # Russell 2000 (US2000)
        # International
        "EWJ",      # Japan JP225
        "EWG",      # Germany DAX
        "EWU",      # UK FTSE
        "EWZ",      # Brazil
        "EEM",      # Emerging Markets
    ]

    CRYPTO_PAIRS: List[str] = [
        # Core
        "BTC/USDT",
        "ETH/USDT",
        # Diversification
        "XRP/USDT",
        "SOL/USDT",
        "BNB/USDT",
    ]

    COMMODITIES: List[str] = [
        # Core
        "GC=F",     # Gold (XAUUSD)
        "SI=F",     # Silver (XAGUSD)
        "CL=F",     # Crude Oil WTI (USOIL)
        # Diversification
        "BZ=F",     # Brent Oil (UKOIL)
        "NG=F",     # Natural Gas
        "PL=F",     # Platinum
        "HG=F",     # Copper
    ]

    class Config:
        env_file = ".env"


settings = Settings()
