"""
ATHENA AI — Main FastAPI Application
"""
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from routers import trades, auth, alerts, stats
from engine.scanner import run_scan
from engine.outcome_checker import check_outcomes
from models.database import init_db

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler.add_job(run_scan, "interval", minutes=15, id="market_scan")
    scheduler.add_job(check_outcomes, "interval", minutes=30, id="outcome_checker")
    scheduler.start()
    await run_scan()
    yield
    scheduler.shutdown()


app = FastAPI(
    title="Athena AI — Trade Scanner",
    description="High-probability trade scanner: Forex, Indices, Crypto, Commodities",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,   prefix="/auth",   tags=["Auth"])
app.include_router(stats.router,  prefix="/trades", tags=["Stats"])   # must be before trades router
app.include_router(trades.router, prefix="/trades", tags=["Trades"])
app.include_router(alerts.router, prefix="/alerts", tags=["Alerts"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "Athena AI"}
