"""
ATHENA AI — Alerts Configuration Router
GET  /alerts/config
PUT  /alerts/config
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional

from models.database import get_db, User, AlertConfig
from routers.auth import get_current_user

router = APIRouter()


class AlertConfigRequest(BaseModel):
    min_score: int = 70
    notifications_enabled: bool = True
    markets: str = "FOREX,INDICES,CRYPTO,COMMODITY"
    directions: str = "LONG,SHORT"


@router.get("/config")
async def get_alert_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(AlertConfig).where(AlertConfig.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()
    return {
        "notifications_enabled": current_user.notifications_enabled,
        "min_score": config.min_score if config else 70,
        "markets": config.markets if config else "FOREX,INDICES,CRYPTO,COMMODITY",
        "directions": config.directions if config else "LONG,SHORT",
    }


@router.put("/config")
async def update_alert_config(
    req: AlertConfigRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # Update user notifications flag
    current_user.notifications_enabled = req.notifications_enabled
    current_user.min_score_alert = req.min_score
    db.add(current_user)

    # Upsert AlertConfig
    result = await db.execute(
        select(AlertConfig).where(AlertConfig.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()
    if config:
        config.min_score = req.min_score
        config.markets = req.markets
        config.directions = req.directions
    else:
        config = AlertConfig(
            user_id=current_user.id,
            min_score=req.min_score,
            markets=req.markets,
            directions=req.directions,
        )
        db.add(config)

    await db.commit()
    return {"status": "ok", "message": "Alert config updated"}
