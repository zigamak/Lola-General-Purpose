"""
telegram_webhook.py
───────────────────
Flask blueprint that receives Telegram updates and feeds them
into the existing MessageProcessor — identical pipeline to WhatsApp.

Register in app.py:
    from telegram_webhook import telegram_bp, init_telegram_webhook
    init_telegram_webhook(config, session_manager, telegram_service, message_processor)
    app.register_blueprint(telegram_bp)
"""

import logging
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

telegram_bp = Blueprint("telegram", __name__)

# Injected by init_telegram_webhook()
_config            = None
_session_manager   = None
_telegram_service  = None
_message_processor = None


def init_telegram_webhook(config, session_manager, telegram_service, message_processor):
    global _config, _session_manager, _telegram_service, _message_processor
    _config            = config
    _session_manager   = session_manager
    _telegram_service  = telegram_service
    _message_processor = message_processor
    logger.info("Telegram webhook initialised.")


@telegram_bp.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    """
    Receives every Telegram Update (message or button tap),
    extracts the text and chat_id, and passes them to MessageProcessor.
    """
    try:
        update = request.get_json(silent=True)
        if not update:
            logger.warning("Telegram webhook: empty or non-JSON body")
            return jsonify({"status": "ignored"}), 200  # always 200 to Telegram

        logger.debug("Telegram update received: %s", update)

        # Parse the incoming update
        parsed = _telegram_service.process_incoming_payload(update)
        if not parsed:
            # Could be a delivery receipt, edit, etc. — silently ignore
            return jsonify({"status": "ignored"}), 200

        chat_id    = parsed["wa_id"]      # Telegram chat_id acts as session key
        text       = parsed.get("text", "")

        # Resolve a display name from the update if available
        user_name = _extract_name(update)

        logger.info("Telegram message from %s (%s): %s", chat_id, user_name, text)

        # ── Feed into the shared MessageProcessor ─────────────────────────
        _message_processor.process_message(
            message_data={"text": text},
            session_id=chat_id,
            user_name=user_name,
        )

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.error("Telegram webhook error: %s", e, exc_info=True)
        # Always return 200 — a non-200 causes Telegram to retry aggressively
        return jsonify({"status": "error"}), 200


def _extract_name(update: dict) -> str:
    """Pull the user's first name from a Telegram Update (best-effort)."""
    try:
        # Regular message
        msg = update.get("message") or {}
        user = msg.get("from") or {}
        if not user:
            # Callback query
            cq = update.get("callback_query") or {}
            user = cq.get("from") or {}
        first = user.get("first_name", "")
        last  = user.get("last_name", "")
        return (first + " " + last).strip() or "Guest"
    except Exception:
        return "Guest"