"""
ATHENA AI — Push Notifications (Expo Push API + Discord Webhook)
"""
import httpx
import logging
from typing import List, Dict
from sqlalchemy import select

from models.database import AsyncSessionLocal, User

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def _fmt_price(price: float, symbol: str) -> str:
    """Format price based on asset type."""
    if "JPY" in symbol or "MXN" in symbol:
        return f"{price:.3f}"
    if price > 10000:
        return f"{price:.2f}"
    if price > 100:
        return f"{price:.2f}"
    if price > 1:
        return f"{price:.4f}"
    return f"{price:.5f}"


async def send_discord_alert(trades: List[Dict]):
    """Send a Discord embed for every Grade A trade found."""
    from config import settings
    webhook_url = settings.DISCORD_WEBHOOK_URL
    if not webhook_url:
        logger.debug("DISCORD_WEBHOOK_URL not set — skipping Discord alert")
        return

    grade_a = [t for t in trades if t.get("grade") == "A"]
    if not grade_a:
        return

    embeds = []
    for t in grade_a:
        is_long   = t["direction"] == "LONG"
        dir_emoji = "📈" if is_long else "📉"
        mtf_note  = "✅ Confirmation 1D + 4H" if t.get("mtf_confirmed") else "📊 1D seulement"
        color     = 0x00D68F if is_long else 0xE94560   # green / red
        sym       = t["symbol"]
        conf      = t.get("confluence_count", 0)
        tf        = t.get("timeframe", "1D").upper()
        mtype     = t.get("market_type", "")

        fp = lambda p: _fmt_price(p, sym)

        # Risk distance for quick read
        risk_dist = abs(t["entry"] - t["stop_loss"])
        rr        = t.get("risk_reward", 0.0)

        embed = {
            "title": f"{dir_emoji}  {sym}  {t['direction']}  —  Grade {t['grade']}",
            "color": color,
            "fields": [
                # Row 1: price levels
                {"name": "🎯 Entry",     "value": f"`{fp(t['entry'])}`",         "inline": True},
                {"name": "🛑 Stop Loss", "value": f"`{fp(t['stop_loss'])}`",     "inline": True},
                {"name": "📏 Distance",  "value": f"`{fp(risk_dist)}`",          "inline": True},
                # Row 2: take profits
                {"name": "✅ TP1",       "value": f"`{fp(t['take_profit_1'])}`", "inline": True},
                {"name": "🚀 TP2",       "value": f"`{fp(t['take_profit_2'])}`", "inline": True},
                {"name": "⚖️ R:R",       "value": f"`1 : {rr:.2f}`",            "inline": True},
                # Row 3: score info
                {"name": "📊 Score",     "value": f"**{t['score_total']:.0f} / 100**", "inline": True},
                {"name": "⚡ Confluences","value": f"**{conf}**",                "inline": True},
                {"name": "🕐 Timeframe", "value": f"`{tf}` · {mtype}",          "inline": True},
            ],
            "footer": {"text": mtf_note},
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        }
        embeds.append(embed)

    payload = {
        "content": "🏆 **ATHENA AI — Grade A Setup(s) détecté(s)**",
        "embeds": embeds[:10],   # Discord max 10 embeds per message
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
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
