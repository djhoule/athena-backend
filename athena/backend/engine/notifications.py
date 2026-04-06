"""
ATHENA AI — Push Notifications (Expo Push API)
"""
import httpx
import logging
from typing import List, Dict
from sqlalchemy import select

from models.database import AsyncSessionLocal, User

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


async def send_expo_notification(token: str, title: str, body: str, data: Dict = None):
    """Send a single push notification via Expo."""
    if not token or not token.startswith("ExponentPushToken"):
        return

    payload = {
        "to": token,
        "title": title,
        "body": body,
        "data": data or {},
        "sound": "default",
        "priority": "high",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(EXPO_PUSH_URL, json=payload)
            if resp.status_code != 200:
                logger.warning(f"Expo push failed: {resp.text}")
    except Exception as e:
        logger.error(f"Push notification error: {e}")


async def send_trade_alerts(trades: List[Dict]):
    """Send push notifications to all users for new top trades."""
    if not trades:
        return

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(
                    User.notifications_enabled == True,
                    User.expo_push_token != None
                )
            )
            users = result.scalars().all()

        for user in users:
            # Filter trades by user's min score preference
            qualifying = [t for t in trades if t["score_total"] >= user.min_score_alert]
            if not qualifying:
                continue

            top = qualifying[0]
            direction_emoji = "📈" if top["direction"] == "LONG" else "📉"
            grade_emoji = "🏆" if top["grade"] == "A" else "⭐"

            title = f"{grade_emoji} Athena — Nouveau Setup {top['grade']}"
            body = (
                f"{direction_emoji} {top['symbol']} {top['direction']} "
                f"| Score: {top['score_total']:.0f}/100 "
                f"| R:R {top['risk_reward']:.1f}"
            )

            await send_expo_notification(
                token=user.expo_push_token,
                title=title,
                body=body,
                data={"trade_symbol": top["symbol"], "direction": top["direction"]}
            )

        logger.info(f"Notifications sent to {len(users)} users")
    except Exception as e:
        logger.error(f"send_trade_alerts error: {e}")
