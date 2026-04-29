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
    DISCORD_WEBHOOK_URL: str = ""

    # ── Scan Parameters ───────────────────────────────────────────────────────
    SCAN_INTERVAL_MINUTES: int = 15
    MIN_SCORE_THRESHOLD: int = 90
    MIN_CONFLUENCE: int = 3
    MAX_TRADES_OUTPUT: int = 10
    MIN_RISK_REWARD: float = 2.0

    # ── Watchlist ─────────────────────────────────────────────────────────────

    FOREX_PAIRS: List[str] = [
        "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY", "USDCAD", "USDCHF",
        "EURAUD", "EURNZD", "EURCAD", "EURCHF", "EURJPY", "EURGBP",
        "GBPAUD", "GBPNZD", "GBPCAD", "GBPJPY", "GBPCHF",
        "AUDNZD", "AUDCAD", "AUDJPY", "AUDCHF",
        "NZDJPY", "NZDCAD", "NZDCHF",
        "CADCHF", "CADJPY", "CHFJPY",
        "USDMXN",
    ]

    INDICES: List[str] = [
        "SP500", "NAS100", "US30", "US2000",
        "JP225", "CN50", "HK33", "IX00",
        "AEX", "EU50", "IBEX35", "DAX", "AU200",
    ]

    CRYPTO_PAIRS: List[str] = [
        "BTCUSD", "ETHUSD", "XRPUSD", "XMRUSD", "AVAXUSD", "SOLUSD",
    ]

    COMMODITIES: List[str] = [
        "GOLD", "SILVER", "PLATINUM", "COPPER", "PALADIUM",
        "USOIL", "UKOIL", "CORN", "SOYBEAN", "WHEAT", "SUGAR",
    ]

    class Config:
        env_file = ".env"


settings = Settings()
