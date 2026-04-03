import hashlib
import hmac
import logging

from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

payment_webhook_bp = Blueprint("payment_webhook", __name__)

_config = None
_session_manager = None
_whatsapp_service = None
_db_manager = None


def init_payment_webhook(config, session_manager, whatsapp_service, db_manager=None):
    global _config, _session_manager, _whatsapp_service, _db_manager
    _config = config
    _session_manager = session_manager
    _whatsapp_service = whatsapp_service
    _db_manager = db_manager
    logger.info("PaymentWebhook initialised.")


# ── Webhook route (Paystack calls this automatically after payment) ────────────

@payment_webhook_bp.route("/paystack/webhook", methods=["POST"])
def paystack_webhook():
    raw_body = request.data

    # Verify Paystack signature using PAYSTACK_SECRET_KEY (same key as the API)
    paystack_secret = getattr(_config, 'PAYSTACK_SECRET_KEY', '') or ''
    signature = request.headers.get("x-paystack-signature", "")

    if paystack_secret:
        # FIX: was `hmac.new(...)` which doesn't exist — correct call is `hmac.new`
        # from the stdlib which is exposed as the module-level `hmac.new` function.
        # The correct API is: hmac.new(key_bytes, msg_bytes, digestmod)
        expected = hmac.new(
            paystack_secret.encode("utf-8"),
            raw_body,
            hashlib.sha512
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            logger.warning("Paystack webhook: invalid signature — rejected.")
            return jsonify({"status": "error"}), 400
    else:
        logger.warning("Paystack webhook: PAYSTACK_SECRET_KEY not set — skipping signature check.")

    try:
        payload = request.get_json(force=True, silent=True) or {}
    except Exception as e:
        logger.error(f"Paystack webhook: bad JSON: {e}")
        return jsonify({"status": "ok"}), 200  # always 200 so Paystack does not retry

    event = payload.get("event")
    data = payload.get("data", {})
    logger.info(f"Paystack webhook: event={event}, ref={data.get('reference')}")

    if event == "charge.success":
        try:
            _handle_charge_success(data)
        except Exception as e:
            logger.error(f"Error handling charge.success: {e}", exc_info=True)
            # still return 200 — Paystack must not retry

    return jsonify({"status": "ok"}), 200


# ── Core payment confirmation logic ───────────────────────────────────────────

def _handle_charge_success(data: dict):
    reference    = data.get("reference", "")
    amount_kobo  = data.get("amount", 0)
    amount_naira = amount_kobo // 100
    metadata     = data.get("metadata", {})

    # FIX: Paystack returns metadata exactly as submitted.  ai_handler now passes
    # customer_phone inside the metadata dict, so this lookup will succeed without
    # a DB roundtrip.  The DB fallback below is kept as a safety net.
    customer_phone = metadata.get("customer_phone", "")

    logger.info(f"Confirmed payment: ref={reference}, NGN{amount_naira}, phone={customer_phone}")

    if not reference:
        logger.warning("charge.success: no reference — skipping.")
        return

    # 1. Update database
    if _db_manager:
        _db_manager.update_order_payment(
            order_ref=reference,
            payment_status="paid",
            payment_ref=reference,
            status="preparing",
        )
        logger.info(f"DB updated: {reference} -> paid / preparing")

    # 2. Resolve phone from DB if not in metadata (safety net)
    if not customer_phone and _db_manager:
        customer_phone = _get_phone_from_order(reference)

    if not customer_phone:
        logger.warning(f"No phone found for ref {reference} — cannot send WhatsApp.")
        return

    # 3. Update in-memory session
    _mark_session_paid(customer_phone, reference)

    # 4. Send WhatsApp confirmation
    _send_confirmation(customer_phone, reference, amount_naira)


def _get_phone_from_order(order_ref: str) -> str:
    try:
        order = _db_manager.get_order_by_ref(order_ref)
        if not order:
            return ""
        row = _db_manager._execute(
            "SELECT phone_number FROM customers WHERE id = %s",
            (order["customer_id"],),
            fetch="one",
        )
        return row["phone_number"] if row else ""
    except Exception as e:
        logger.error(f"_get_phone_from_order error: {e}")
        return ""


def _mark_session_paid(phone: str, reference: str):
    if not _session_manager:
        return
    try:
        state = _session_manager.get_session_state(phone)
        if state is not None:
            state["payment_pending"]   = False
            state["payment_confirmed"] = True
            state["payment_ref"]       = reference
            _session_manager.update_session_state(phone, state)
    except Exception as e:
        logger.warning(f"Could not update session for {phone}: {e}")


def _send_confirmation(phone: str, reference: str, amount_naira: int):
    """
    Send a WhatsApp payment confirmation.

    FIX: build the payload dict manually and pass it to send_message() once.
    Do NOT call create_text_message() here — that method sends internally AND
    returns the API response dict.  If you then pass that response dict to
    send_message() it will fail with "Missing required fields: ['to', 'type']"
    because the response has no 'to'/'type' keys (it's the WhatsApp API reply,
    not a sendable payload).
    """
    if not _whatsapp_service:
        logger.error("_whatsapp_service not initialised.")
        return

    message = (
        f"Payment confirmed! Thank you 🎉\n\n"
        f"Order Ref: {reference}\n"
        f"Amount Paid: NGN{amount_naira:,}\n\n"
        f"Your order is now being prepared in our kitchen 🍛\n"
        f"We will notify you once it is on its way.\n\n"
        f"Thank you for choosing Makinde Kitchen!"
    )
    try:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": str(phone),
            "type": "text",
            "text": {"body": message},
        }
        _whatsapp_service.send_message(payload)
        logger.info(f"Confirmation sent to {phone} for ref {reference}.")
    except Exception as e:
        logger.error(f"Failed to send confirmation to {phone}: {e}", exc_info=True)


# ── Manual fallback: call from ai_handler when user says "I've paid" ──────────

PAYMENT_KEYWORDS = {
    "i paid", "i have paid", "i've paid", "payment done", "payment made",
    "i sent the money", "done paying", "paid already", "transfer done",
    "i completed payment", "payment complete", "payment confirmed",
    "i just paid", "money sent", "i transferred",
}


def is_payment_claim(message: str) -> bool:
    msg = message.lower().strip()
    return any(kw in msg for kw in PAYMENT_KEYWORDS)


def handle_manual_payment_check(phone: str, order_ref: str) -> str:
    """
    Verify payment with Paystack manually.
    Returns a reply string — caller is responsible for sending it.
    """
    if not _config or not order_ref:
        return "I could not find your order. Please contact support if you have already paid."

    from services.payment_service import PaymentService
    payment_service = PaymentService(_config)

    logger.info(f"Manual payment check: phone={phone}, ref={order_ref}")
    verified, payment_data = payment_service.verify_payment_detailed(order_ref)

    if verified:
        amount_kobo  = payment_data.get("amount", 0)
        amount_naira = amount_kobo // 100

        if _db_manager:
            _db_manager.update_order_payment(
                order_ref=order_ref,
                payment_status="paid",
                payment_ref=payment_data.get("reference", order_ref),
                status="preparing",
            )

        _mark_session_paid(phone, order_ref)
        logger.info(f"Manual check: verified ref={order_ref}")

        return (
            f"Payment confirmed! Thank you 🎉\n\n"
            f"Order Ref: {order_ref}\n"
            f"Amount Paid: NGN{amount_naira:,}\n\n"
            f"Your order is now being prepared in our kitchen 🍛\n"
            f"We will update you when it is on its way!"
        )
    else:
        paystack_status = (payment_data or {}).get("status", "unknown")
        logger.warning(f"Manual check: not verified, ref={order_ref}, status={paystack_status}")

        if paystack_status in ("abandoned", "failed"):
            return (
                f"It looks like the payment for order {order_ref} did not go through.\n\n"
                f"Would you like me to send you a new payment link?"
            )
        return (
            f"I checked and your payment for order {order_ref} has not been confirmed yet.\n\n"
            f"Please complete the payment using the link sent earlier, "
            f"or let me know if you need a new one."
        )