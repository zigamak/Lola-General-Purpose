import hashlib
import hmac
import json
import logging
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

# Blueprint — mount this in app.py with app.register_blueprint(payment_webhook_bp)
payment_webhook_bp = Blueprint("payment_webhook", __name__)

# These are set via init_payment_webhook() called from app.py
_config = None
_session_manager = None
_whatsapp_service = None


def init_payment_webhook(config, session_manager, whatsapp_service):
    """Call this once in app.py after initialising services."""
    global _config, _session_manager, _whatsapp_service
    _config = config
    _session_manager = session_manager
    _whatsapp_service = whatsapp_service
    logger.info("PaymentWebhook initialised.")


@payment_webhook_bp.route("/paystack/webhook", methods=["POST"])
def paystack_webhook():
    """
    Receives Paystack webhook events.
    Verifies the HMAC signature, then handles charge.success.
    """
    # ── 1. Verify Paystack signature ──────────────────────────────────────────
    paystack_secret = getattr(_config, 'PAYSTACK_WEBHOOK_SECRET', '') or ''
    signature = request.headers.get("x-paystack-signature", "")

    if paystack_secret and paystack_secret != 'paystack_webhook_secret_placeholder':
        expected = hmac.new(
            paystack_secret.encode("utf-8"),
            request.data,
            hashlib.sha512
        ).hexdigest()

        if not hmac.compare_digest(expected, signature):
            logger.warning("Paystack webhook: invalid signature — rejected.")
            return jsonify({"status": "error", "message": "Invalid signature"}), 400
    else:
        logger.warning("Paystack webhook: PAYSTACK_WEBHOOK_SECRET not set — skipping signature check (dev mode).")

    # ── 2. Parse event ────────────────────────────────────────────────────────
    try:
        payload = request.get_json(force=True)
    except Exception as e:
        logger.error(f"Paystack webhook: could not parse JSON body: {e}")
        return jsonify({"status": "error", "message": "Bad JSON"}), 400

    event = payload.get("event")
    data = payload.get("data", {})

    logger.info(f"Paystack webhook received: event={event}, ref={data.get('reference')}")

    # ── 3. Handle charge.success ──────────────────────────────────────────────
    if event == "charge.success":
        _handle_charge_success(data)

    # Always return 200 to Paystack so it doesn't retry
    return jsonify({"status": "ok"}), 200


def _handle_charge_success(data: dict):
    """
    Called when a payment is confirmed.
    Finds the session by order reference and sends a WhatsApp confirmation.
    """
    reference = data.get("reference", "")
    amount_kobo = data.get("amount", 0)
    amount_naira = amount_kobo // 100
    customer_info = data.get("customer", {})
    customer_phone = data.get("metadata", {}).get("customer_phone", "")

    logger.info(f"Payment confirmed: ref={reference}, amount=₦{amount_naira}, phone={customer_phone}")

    if not customer_phone:
        logger.warning(f"charge.success for {reference}: no customer_phone in metadata — cannot send WhatsApp.")
        return

    if not _whatsapp_service:
        logger.error("charge.success: _whatsapp_service not initialised.")
        return

    # ── Find session by payment_ref ───────────────────────────────────────────
    session_id = _find_session_by_ref(reference, customer_phone)

    # Update session state to mark payment complete
    if session_id and _session_manager:
        try:
            state = _session_manager.get_session_state(session_id)
            state["payment_pending"] = False
            state["payment_confirmed"] = True
            state["payment_ref"] = reference
            _session_manager.update_session_state(session_id, state)
        except Exception as e:
            logger.error(f"Could not update session state for {session_id}: {e}")

    # ── Send WhatsApp confirmation ─────────────────────────────────────────────
    confirmation_message = (
        f"Payment confirmed! Thank you. 🎉\n\n"
        f"Order Ref: {reference}\n"
        f"Amount Paid: ₦{amount_naira:,}\n\n"
        f"Your order has been sent to the kitchen and is being prepared. "
        f"You will receive a notification once it is on its way to you.\n\n"
        f"Thank you for choosing Makinde Kitchen! 🍛"
    )

    try:
        _whatsapp_service.send_message(
            _whatsapp_service.create_text_message(customer_phone, confirmation_message)
        )
        logger.info(f"Payment confirmation sent to {customer_phone} for ref {reference}.")
    except Exception as e:
        logger.error(f"Failed to send payment confirmation to {customer_phone}: {e}", exc_info=True)


def _find_session_by_ref(reference: str, customer_phone: str) -> str:
    """
    Try to find the session ID for this payment reference.
    First tries the customer phone (most reliable), then scans all sessions.
    Returns session_id string or None.
    """
    if not _session_manager:
        return customer_phone  # best guess

    # Phone number is usually the session ID in WhatsApp bots
    try:
        state = _session_manager.get_session_state(customer_phone)
        if state.get("payment_ref") == reference or state.get("order_ref") == reference:
            return customer_phone
    except Exception:
        pass

    # Fallback: scan all sessions (works for small session counts)
    try:
        if hasattr(_session_manager, '_sessions'):
            for sid, session_data in _session_manager._sessions.items():
                s = session_data.get("state", {})
                if s.get("payment_ref") == reference or s.get("order_ref") == reference:
                    return sid
    except Exception as e:
        logger.warning(f"Session scan failed: {e}")

    # Last resort — use phone number directly as session ID
    logger.warning(f"Could not find session for ref {reference}, using phone {customer_phone} as session ID.")
    return customer_phone