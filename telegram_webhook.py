"""
telegram_webhook.py
───────────────────
Flask blueprint that receives Telegram updates and routes them to either:
  - DeliveryHandler  (rider button taps: accept_, picked_, delivered_, unavailable_)
  - MessageProcessor (all customer messages)

Register in app.py:
    from telegram_webhook import telegram_bp, init_telegram_webhook
    init_telegram_webhook(config, session_manager, telegram_service,
                          message_processor, delivery_handler)
    app.register_blueprint(telegram_bp)
"""

import logging
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

telegram_bp = Blueprint("telegram", __name__)

_config            = None
_session_manager   = None
_telegram_service  = None
_message_processor = None
_delivery_handler  = None
_rider_group_chat_id = None


def init_telegram_webhook(
    config,
    session_manager,
    telegram_service,
    message_processor,
    delivery_handler=None,
):
    global _config, _session_manager, _telegram_service
    global _message_processor, _delivery_handler, _rider_group_chat_id

    _config              = config
    _session_manager     = session_manager
    _telegram_service    = telegram_service
    _message_processor   = message_processor
    _delivery_handler    = delivery_handler
    # Try config first, fall back to DB (vendors.rider_group_chat_id)
    _rider_group_chat_id = str(getattr(config, 'RIDER_GROUP_CHAT_ID', '') or '')
    if not _rider_group_chat_id:
        try:
            from db_manager import DBManager
            db = DBManager(config)
            vendors = db.get_all_vendors()
            for v in vendors:
                gid = v.get('rider_group_chat_id')
                if gid:
                    _rider_group_chat_id = str(gid)
                    break
        except Exception as e:
            logger.warning(f"Could not load rider_group_chat_id from DB: {e}")

    logger.info(f"Telegram webhook initialised. Rider group: {_rider_group_chat_id or 'NOT SET'}")


@telegram_bp.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    try:
        update = request.get_json(silent=True)
        if not update:
            logger.warning("Telegram webhook: empty or non-JSON body")
            return jsonify({"status": "ignored"}), 200

        logger.debug("Telegram update received: %s", update)

        parsed = _telegram_service.process_incoming_payload(update)
        if not parsed:
            return jsonify({"status": "ignored"}), 200

        chat_id   = parsed["wa_id"]
        text      = parsed.get("text", "")
        user_name = _extract_name(update)

        logger.info("Telegram message from %s (%s): %s", chat_id, user_name, text)

        sender_id = str(_get_sender_id(update))

        # ── Rider group — only delivery callbacks handled, all else ignored ─
        if _is_from_rider_group(update):
            if (
                _delivery_handler
                and text
                and _delivery_handler.is_delivery_callback(text)
            ):
                logger.info(
                    "Delivery callback '%s' from rider %s (%s)",
                    text, sender_id, user_name
                )
                _delivery_handler.handle_callback(
                    callback_data=text,
                    rider_telegram_id=sender_id,
                    rider_name=user_name,
                )
            else:
                logger.debug("Ignored non-delivery message from rider group: '%s'", text)
            return jsonify({"status": "ok"}), 200

        # ── Private message from a known rider — route to DeliveryHandler ─
        # Rider tapped Picked Up / Delivered from their private chat
        if (
            _delivery_handler
            and text
            and _delivery_handler.is_delivery_callback(text)
            and _is_known_rider(sender_id)
        ):
            logger.info(
                "Private delivery callback '%s' from rider %s (%s)",
                text, sender_id, user_name
            )
            _delivery_handler.handle_callback(
                callback_data=text,
                rider_telegram_id=sender_id,
                rider_name=user_name,
            )
            return jsonify({"status": "ok"}), 200

        # ── Regular customer message — route to MessageProcessor ──────────
        _message_processor.process_message(
            message_data={"text": text},
            session_id=chat_id,
            user_name=user_name,
        )

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.error("Telegram webhook error: %s", e, exc_info=True)
        return jsonify({"status": "error"}), 200


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_known_rider(telegram_id: str) -> bool:
    """
    Returns True if this telegram_id exists in the riders table.
    Used to route private Picked Up / Delivered taps to DeliveryHandler
    instead of MessageProcessor.
    """
    if not telegram_id or not _config:
        return False
    try:
        from db_manager import DBManager
        db = DBManager(_config)
        rider = db.get_rider_by_telegram_id(telegram_id)
        return rider is not None
    except Exception as e:
        logger.warning(f"_is_known_rider check failed: {e}")
        return False


def _is_from_rider_group(update: dict) -> bool:
    """
    Returns True if the update originated from the rider group chat.
    Checks both regular messages and callback queries.
    """
    if not _rider_group_chat_id:
        return False
    try:
        # Callback query
        cq = update.get("callback_query")
        if cq:
            chat_id = str(cq.get("message", {}).get("chat", {}).get("id", ""))
            return chat_id == _rider_group_chat_id

        # Regular message
        msg = update.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        return chat_id == _rider_group_chat_id
    except Exception:
        return False


def _get_sender_id(update: dict) -> str:
    """Get the personal chat_id of whoever sent the update."""
    try:
        cq = update.get("callback_query")
        if cq:
            return str(cq.get("from", {}).get("id", ""))
        msg = update.get("message", {})
        return str(msg.get("from", {}).get("id", ""))
    except Exception:
        return ""


def _extract_name(update: dict) -> str:
    """Pull the user's display name from a Telegram Update (best-effort)."""
    try:
        msg  = update.get("message") or {}
        user = msg.get("from") or {}
        if not user:
            cq   = update.get("callback_query") or {}
            user = cq.get("from") or {}
        first = user.get("first_name", "")
        last  = user.get("last_name", "")
        return (first + " " + last).strip() or "Guest"
    except Exception:
        return "Guest"