"""
ATHENA AI — Push Notifications (Expo Push API + Discord Webhook)
"""
import httpx
import logging
from typing import List, Dict
from sqlalchemy import select

from models.database import AsyncSessionLocal, User

logger = logging.getLogger(__name__)

EXPO_PUSH_URL    = "https://exp.host/--/api/v2/push/send"
DISCORD_WEBHOOK  = "https://discord.com/api/webhooks/1490683766811660401/Zc1nEtFY2gv_-6nxssIqhX5jkw6ao3O5qC1lZecRRRT4q8e8w17QqLL1GWxYPwxA5p1C"


async def send_discord_alert(trades: List[Dict]):
    """Send a Discord message for every Grade A trade found."""
    grade_a = [t for t in trades if t.get("grade") == "A"]
    if not grade_a:
        return

    lines = []
    for t in grade_a:
        dir_emoji  = "📈" if t["direction"] == "LONG" else "📉"
        mtf        = " ✅ MTF" if t.get("mtf_confirmed") else ""
        conf       = t.get("confluence_count", 0)
        lines.append(
            f"{dir_emoji} **{t['symbol']}** {t['direction']}{mtf}\n"
            f"> Score: **{t['score_total']:.0f}/100** | R:R **{t['risk_reward']:.1f}** | "
            f"⚡ {conf} confluences | Entry: `{t['entry']}`"
        )

    content = "🏆 **ATHENA AI — Grade A Setup(s) détecté(s)**\n\n" + "\n\n".join(lines)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(DISCORD_WEBHOOK, json={"content": content})
            if resp.status_code not in (200, 204):
                logger.warning(f"Discord webhook failed: {resp.status_code} {resp.text}")
            else:
                logger.info(f"Discord alert sent for {len(grade_a)} Grade A trade(s)")
    except Exception as e:
        logger.error(f"Discord webhook error: {e}")


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
