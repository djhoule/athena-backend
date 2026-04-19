"""
ATHENA AI — Push Notifications (Expo Push API + Discord Webhook)

Améliorations v2 :
  - calculate_lot_size() : calcule le nombre de lots pour risquer exactement 1 000 USD
  - send_discord_alert() : envoie UN message Discord par trade Grade A (nouveau seulement)
    → utilise ?wait=true pour récupérer le message_id et l'enregistrer en DB
  - edit_discord_message() : édite un message existant quand le statut change (WIN/LOSS/EXPIRED)
  - send_trade_alerts()   : push Expo — Grade A uniquement
"""
import httpx
import json
import logging
from typing import List, Dict, Optional
from sqlalchemy import select

from models.database import AsyncSessionLocal, User, Trade

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


# ─────────────────────────────────────────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_price(price: float, symbol: str) -> str:
    """Formate un prix selon l'actif."""
    if "JPY" in symbol or "MXN" in symbol:
        return f"{price:.3f}"
    if price > 10000:
        return f"{price:.2f}"
    if price > 100:
        return f"{price:.2f}"
    if price > 1:
        return f"{price:.4f}"
    return f"{price:.5f}"


def calculate_lot_size(
    entry_price: float,
    stop_loss: float,
    risk_usd: float = 1000,
    atr: Optional[float] = None,
) -> Optional[float]:
    """
    Calcule la taille de position (lot size) pour risquer exactement risk_usd par trade.

    Formule principale :
        lot_size = risk_usd / |entry_price - stop_loss|

    Fallback (si stop_loss absent ou nul) :
        stop_distance = ATR * 1.5
        lot_size = risk_usd / stop_distance

    Retourne None si le calcul est impossible.
    """
    # Cas principal : stop_loss valide
    if stop_loss and entry_price and stop_loss != entry_price:
        distance = abs(entry_price - stop_loss)
        if distance > 0:
            return round(risk_usd / distance, 2)

    # Fallback : utiliser ATR * 1.5 comme distance de stop par défaut
    if atr and atr > 0:
        sl_distance = atr * 1.5
        logger.debug(
            f"calculate_lot_size fallback: stop_loss invalide, "
            f"utilisation ATR*1.5 = {sl_distance:.5f}"
        )
        return round(risk_usd / sl_distance, 2)

    logger.warning("calculate_lot_size: impossible (stop_loss et ATR tous deux invalides)")
    return None


def _extract_webhook_parts(webhook_url: str):
    """Extrait (webhook_id, token) depuis une URL Discord webhook."""
    parts = webhook_url.rstrip("/").split("/")
    return parts[-2], parts[-1]   # (id, token)


def _build_trade_embed(trade: Trade, outcome: Optional[str] = None) -> dict:
    """
    Construit un embed Discord à partir d'un objet Trade ORM.
    Si `outcome` est fourni (WIN_TP1 / WIN_TP2 / LOSS / EXPIRED),
    ajoute un champ résultat et adapte la couleur.
    """
    is_long    = trade.direction.value == "LONG"
    dir_emoji  = "📈" if is_long else "📉"
    sym        = trade.symbol
    rr         = trade.risk_reward

    # Vérifier la confirmation MTF dans le reasoning JSON
    mtf_confirmed = False
    try:
        reasoning_dict = json.loads(trade.reasoning)
        mtf_confirmed  = reasoning_dict.get("mtf_confirmation", {}).get("confirmed", False)
    except Exception:
        pass

    mtf_note   = "✅ Confirmation 1D + 4H" if mtf_confirmed else "📊 1D seulement"
    risk_dist  = abs(trade.entry_price - trade.stop_loss)
    fp         = lambda p: _fmt_price(p, sym)

    # Couleur de base : vert LONG / rouge SHORT
    color = 0x00D68F if is_long else 0xE94560

    fields = [
        # Rangée 1 : niveaux de prix
        {"name": "🎯 Entry",      "value": f"`{fp(trade.entry_price)}`",   "inline": True},
        {"name": "🛑 Stop Loss",  "value": f"`{fp(trade.stop_loss)}`",     "inline": True},
        {"name": "📏 Distance",   "value": f"`{fp(risk_dist)}`",           "inline": True},
        # Rangée 2 : take profits
        {"name": "✅ TP1",        "value": f"`{fp(trade.take_profit_1)}`", "inline": True},
        {"name": "🚀 TP2",        "value": f"`{fp(trade.take_profit_2)}`", "inline": True},
        {"name": "⚖️ R:R",        "value": f"`1 : {rr:.2f}`",             "inline": True},
        # Rangée 3 : score + confluences + timeframe
        {"name": "📊 Score",      "value": f"**{trade.score_total:.0f} / 100**",        "inline": True},
        {"name": "⚡ Confluences", "value": f"**{trade.confluence_count}**",              "inline": True},
        {"name": "🕐 Timeframe",  "value": f"`{trade.timeframe.upper()}` · {trade.market_type.value}", "inline": True},
    ]

    # Rangée 4 : lot size (si calculé)
    if trade.lot_size is not None:
        fields.append({
            "name":   "💰 Lot Size (risque 1 000 USD)",
            "value":  f"**{trade.lot_size:.2f}** unités",
            "inline": False,
        })

    # Titre et couleur — adaptés si le trade est clôturé
    title = f"{dir_emoji}  {sym}  {trade.direction.value}  —  Grade {trade.grade.value}"

    if outcome and outcome != "PENDING":
        outcome_emoji = {
            "WIN_TP1": "✅",
            "WIN_TP2": "🏆",
            "LOSS":    "❌",
            "EXPIRED": "⏰",
        }.get(outcome, "🔄")

        pnl_label = {
            "WIN_TP1": "+1.5R",
            "WIN_TP2": f"+{rr:.1f}R",
            "LOSS":    "-1R",
            "EXPIRED": "0R",
        }.get(outcome, "?")

        fields.append({
            "name":   f"{outcome_emoji} Résultat",
            "value":  f"**{outcome}** — {pnl_label}",
            "inline": False,
        })

        # Recoulorer selon issue
        color = (
            0x00D68F if "WIN" in outcome else
            0xE94560 if outcome == "LOSS" else
            0x888888   # gris pour EXPIRED
        )
        title = f"[CLÔTURÉ] {title}"

    return {
        "title":     title,
        "color":     color,
        "fields":    fields,
        "footer":    {"text": mtf_note},
        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# DISCORD — ENVOI & ÉDITION
# ─────────────────────────────────────────────────────────────────────────────

async def send_discord_alert(trades: List[Trade]) -> Dict[int, str]:
    """
    Envoie UN message Discord par trade Grade A passé en paramètre.

    Seuls les trades sans discord_message_id (jamais notifiés) doivent être transmis ici.
    Utilise ?wait=true pour récupérer l'id du message créé.

    Retourne un dict {trade.id: discord_message_id} pour persistance en DB.
    """
    from config import settings
    webhook_url = settings.DISCORD_WEBHOOK_URL
    if not webhook_url:
        logger.debug("DISCORD_WEBHOOK_URL non configuré — Discord ignoré")
        return {}

    if not trades:
        return {}

    id_to_msg: Dict[int, str] = {}

    for trade in trades:
        embed   = _build_trade_embed(trade)
        payload = {
            "content": f"🏆 **ATHENA AI — Nouveau Setup Grade A : {trade.symbol}**",
            "embeds":  [embed],
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # ?wait=true → Discord retourne le message créé (avec son id)
                resp = await client.post(
                    webhook_url + "?wait=true",
                    json=payload,
                )
                if resp.status_code in (200, 204):
                    data       = resp.json()
                    message_id = data.get("id")
                    if message_id:
                        id_to_msg[trade.id] = message_id
                        logger.info(
                            f"Discord envoyé : {trade.symbol} Grade A "
                            f"(msg_id={message_id})"
                        )
                    else:
                        logger.warning(
                            f"Discord n'a pas retourné de message_id pour {trade.symbol}"
                        )
                else:
                    logger.warning(
                        f"Discord webhook failed pour {trade.symbol}: "
                        f"{resp.status_code} {resp.text}"
                    )
        except Exception as exc:
            logger.error(f"Discord webhook error pour {trade.symbol}: {exc}")

    return id_to_msg


async def edit_discord_message(
    webhook_url: str,
    message_id: str,
    trade: Trade,
    outcome: str,
) -> bool:
    """
    Édite un message Discord existant pour refléter le nouvel état d'un trade.
    Utilise PATCH /webhooks/{id}/{token}/messages/{message_id}.

    Appelé depuis outcome_checker quand un trade Grade A est résolu.
    Retourne True si l'édition a réussi.
    """
    try:
        webhook_id, webhook_token = _extract_webhook_parts(webhook_url)
        edit_url = (
            f"https://discord.com/api/webhooks/{webhook_id}/{webhook_token}"
            f"/messages/{message_id}"
        )

        embed   = _build_trade_embed(trade, outcome=outcome)
        payload = {
            "content": f"🔔 **ATHENA AI — Trade Clôturé : {trade.symbol}**",
            "embeds":  [embed],
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.patch(edit_url, json=payload)
            if resp.status_code in (200, 204):
                logger.info(
                    f"Discord message édité : {trade.symbol} "
                    f"(outcome={outcome}, msg_id={message_id})"
                )
                return True
            else:
                logger.warning(
                    f"Discord edit failed pour {trade.symbol}: "
                    f"{resp.status_code} {resp.text}"
                )
                return False

    except Exception as exc:
        logger.error(
            f"edit_discord_message error pour trade {trade.id} ({trade.symbol}): {exc}"
        )
        return False


# ─────────────────────────────────────────────────────────────────────────────
# EXPO PUSH NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────

async def send_expo_notification(token: str, title: str, body: str, data: Dict = None):
    """Envoie une push notification via l'API Expo."""
    if not token or not token.startswith("ExponentPushToken"):
        return

    payload = {
        "to":       token,
        "title":    title,
        "body":     body,
        "data":     data or {},
        "sound":    "default",
        "priority": "high",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(EXPO_PUSH_URL, json=payload)
            if resp.status_code != 200:
                logger.warning(f"Expo push failed: {resp.text}")
    except Exception as exc:
        logger.error(f"Push notification error: {exc}")


async def send_trade_alerts(trades: List[Dict]):
    """
    Envoie des push Expo aux utilisateurs — Grade A uniquement.
    Les trades B/C ne génèrent plus de notification mobile.
    """
    # Filtrer Grade A uniquement
    grade_a = [t for t in trades if t.get("grade") == "A"]
    if not grade_a:
        return

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(
                    User.notifications_enabled == True,
                    User.expo_push_token != None,
                )
            )
            users = result.scalars().all()

        for user in users:
            # Filtrer par score minimum configuré par l'utilisateur
            qualifying = [
                t for t in grade_a
                if t["score_total"] >= user.min_score_alert
            ]
            if not qualifying:
                continue

            top            = qualifying[0]
            direction_emoji = "📈" if top["direction"] == "LONG" else "📉"
            title          = "🏆 Athena — Nouveau Setup Grade A"
            body           = (
                f"{direction_emoji} {top['symbol']} {top['direction']} "
                f"| Score: {top['score_total']:.0f}/100 "
                f"| R:R {top['risk_reward']:.1f}"
            )

            await send_expo_notification(
                token=user.expo_push_token,
                title=title,
                body=body,
                data={"trade_symbol": top["symbol"], "direction": top["direction"]},
            )

        logger.info(f"Notifications Expo envoyées à {len(users)} utilisateur(s)")

    except Exception as exc:
        logger.error(f"send_trade_alerts error: {exc}")
