import hashlib
import hmac
import logging

from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

payment_webhook_bp = Blueprint("payment_webhook", __name__)

_config               = None
_session_manager      = None
_whatsapp_service     = None
_db_manager           = None
_notification_service = None

PAYMENT_CALLBACK_URL = "https://afyabot-7w4j.onrender.com/portal/payment/success"


def init_payment_webhook(
    config,
    session_manager,
    whatsapp_service,
    db_manager=None,
    notification_service=None,
):
    global _config, _session_manager, _whatsapp_service, _db_manager, _notification_service
    _config               = config
    _session_manager      = session_manager
    _whatsapp_service     = whatsapp_service
    _db_manager           = db_manager
    _notification_service = notification_service
    logger.info("PaymentWebhook initialised.")


# ── Webhook route ─────────────────────────────────────────────────────────────

@payment_webhook_bp.route("/paystack/webhook", methods=["POST"])
def paystack_webhook():
    raw_body = request.data

    paystack_secret = getattr(_config, 'PAYSTACK_SECRET_KEY', '') or ''
    signature       = request.headers.get("x-paystack-signature", "")

    if paystack_secret:
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
        return jsonify({"status": "ok"}), 200

    event = payload.get("event")
    data  = payload.get("data", {})
    logger.info(f"Paystack webhook: event={event}, ref={data.get('reference')}")

    if event == "charge.success":
        try:
            _handle_charge_success(data)
        except Exception as e:
            logger.error(f"Error handling charge.success: {e}", exc_info=True)

    return jsonify({"status": "ok"}), 200


# ── Core payment confirmation ─────────────────────────────────────────────────

def _handle_charge_success(data: dict):
    reference    = data.get("reference", "")
    amount_kobo  = data.get("amount", 0)
    amount_naira = amount_kobo // 100
    metadata     = data.get("metadata", {})

    customer_phone    = metadata.get("customer_phone", "")
    vendor_id         = metadata.get("vendor_id")
    customer_platform = metadata.get("channel", "whatsapp")

    logger.info(f"Confirmed payment: ref={reference}, NGN{amount_naira}, phone={customer_phone}")

    if not reference:
        logger.warning("charge.success: no reference — skipping.")
        return

    # 1. Update DB
    if _db_manager:
        _db_manager.update_order_payment(
            order_ref=reference,
            payment_status="paid",
            payment_ref=reference,
            status="preparing",
        )
        # Log payment record
        order = _db_manager.get_order_by_ref(reference)
        if order:
            _db_manager.log_payment(
                order_id=order['id'],
                order_ref=reference,
                amount=amount_kobo,
                payment_ref=reference,
                gateway='paystack',
                status='success',
                webhook_payload=data,
            )

        logger.info(f"DB updated: {reference} -> paid / preparing")

    # 2. Resolve phone from DB if not in metadata
    if not customer_phone and _db_manager:
        customer_phone = _get_phone_from_order(reference)

    if not customer_phone:
        logger.warning(f"No phone found for ref {reference} — cannot notify customer.")
        return

    # 3. Update in-memory session
    _mark_session_paid(customer_phone, reference)

    # 4. Fire all delivery notifications via NotificationService
    if _notification_service:
        try:
            _notification_service.handle_order_confirmed(
                order_ref=reference,
                amount_naira=amount_naira,
                customer_phone=customer_phone,
                customer_platform=customer_platform,
                vendor_id=int(vendor_id) if vendor_id else None,
            )
        except Exception as e:
            logger.error(f"NotificationService.handle_order_confirmed failed: {e}", exc_info=True)
            # Fall back to basic confirmation so customer is not left in the dark
            # Get vendor name for fallback message
        _vendor_name = "our kitchen"
        if _db_manager:
            try:
                _order = _db_manager.get_order_by_ref(reference)
                if _order and _order.get("vendor_id"):
                    _vendor = _db_manager.get_vendor_by_id(_order["vendor_id"])
                    if _vendor:
                        _vendor_name = _vendor["name"]
            except Exception:
                pass
        _send_basic_confirmation(customer_phone, customer_platform, reference, amount_naira, _vendor_name)
    else:
        # NotificationService not injected — send basic confirmation only
        _vendor_name = "our kitchen"
        if _db_manager:
            try:
                _order = _db_manager.get_order_by_ref(reference)
                if _order and _order.get("vendor_id"):
                    _vendor = _db_manager.get_vendor_by_id(_order["vendor_id"])
                    if _vendor:
                        _vendor_name = _vendor["name"]
            except Exception:
                pass
        _send_basic_confirmation(customer_phone, customer_platform, reference, amount_naira, _vendor_name)


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


def _send_basic_confirmation(
    phone: str,
    platform: str,
    reference: str,
    amount_naira: int,
    vendor_name: str = "our kitchen",
):
    """Fallback confirmation when NotificationService is unavailable."""
    message = (
        f"Payment confirmed! Thank you 🎉\n\n"
        f"Order Ref: {reference}\n"
        f"Amount Paid: ₦{amount_naira:,}\n\n"
        f"Your order is now being prepared by {vendor_name}.\n"
        f"We will notify you once a rider is on the way."
    )
    try:
        if platform == 'telegram':
            from services.telegram_service import TelegramService
            # telegram_service is not directly injected here — log and skip
            logger.warning("Basic confirmation: Telegram service not available in fallback.")
            return
        if _whatsapp_service:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type":    "individual",
                "to":                str(phone),
                "type":              "text",
                "text":              {"body": message},
            }
            _whatsapp_service.send_message(payload)
            logger.info(f"Basic confirmation sent to {phone}")
    except Exception as e:
        logger.error(f"_send_basic_confirmation failed for {phone}: {e}")


# ── Manual payment check ──────────────────────────────────────────────────────

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

        # Trigger full notification flow if available
        if _notification_service:
            try:
                order = _db_manager.get_order_by_ref(order_ref) if _db_manager else None
                customer_platform = 'whatsapp'
                vendor_id = None
                if order:
                    vendor_id = order.get('vendor_id')
                    # Detect platform from phone format
                    customer_platform = 'telegram' if str(phone).isdigit() and len(str(phone)) < 15 else 'whatsapp'

                _notification_service.handle_order_confirmed(
                    order_ref=order_ref,
                    amount_naira=amount_naira,
                    customer_phone=phone,
                    customer_platform=customer_platform,
                    vendor_id=vendor_id,
                )
            except Exception as e:
                logger.error(f"Manual check: NotificationService failed: {e}")

        logger.info(f"Manual check: verified ref={order_ref}")
        return (
            f"Payment confirmed! Thank you 🎉\n\n"
            f"Order Ref: {order_ref}\n"
            f"Amount Paid: ₦{amount_naira:,}\n\n"
            f"Your order is now being prepared.\n"
            f"We will update you when a rider is on the way!"
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