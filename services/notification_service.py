import logging
import random
import string
from typing import Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Centralises all post-payment notifications for the Lola delivery flow.

    Triggered by payment_webhook after Paystack confirms payment.

    Responsibilities:
      1. Notify vendor via Telegram (always)
      2. Notify vendor via WhatsApp (if whatsapp_number set)
      3. Post order to rider group with Accept / Unavailable buttons
      4. Notify customer that order is confirmed and rider is being found
      5. Handle rider Accept / Picked Up / Delivered / Unavailable callbacks
      6. Handle 15-minute rider timeout — re-post to group
    """

    def __init__(self, config, db_manager, telegram_service, whatsapp_service=None):
        self.config            = config
        self.db                = db_manager
        self.telegram          = telegram_service
        self.whatsapp          = whatsapp_service
        # rider_group_chat_id is read per-vendor from the DB, not from config
        logger.info("NotificationService initialised.")

    # ── Post-payment entry point ───────────────────────────────────────────────

    def handle_order_confirmed(
        self,
        order_ref: str,
        amount_naira: int,
        customer_phone: str,
        customer_platform: str = 'whatsapp',
        vendor_id: int = None,
    ):
        """
        Called by payment_webhook after charge.success.
        Fires all notifications in sequence.
        """
        # Load order + vendor details
        order = self.db.get_order_by_ref(order_ref)
        if not order:
            logger.error(f"NotificationService: order not found for ref={order_ref}")
            return

        vendor = None
        if vendor_id:
            vendor = self.db.get_vendor_by_id(vendor_id)
        elif order.get('vendor_id'):
            vendor = self.db.get_vendor_by_id(order['vendor_id'])

        # Load order items for the notification messages
        items = self._get_order_items(order['id'])

        # Generate 4-digit PIN for delivery
        pin = self._generate_pin()

        # Create delivery record
        timeout_at = datetime.now() + timedelta(minutes=15)
        delivery_id = self.db.create_delivery(
            order_id=order['id'],
            pin=pin,
            timeout_at=timeout_at,
        )

        logger.info(f"NotificationService: delivery_id={delivery_id}, pin={pin}, ref={order_ref}")

        # 1. Notify vendor — Telegram
        if vendor and vendor.get('telegram_chat_id'):
            self._notify_vendor_telegram(vendor, order, items, pin)

        # 2. Notify vendor — WhatsApp
        if vendor and vendor.get('whatsapp_number') and self.whatsapp:
            self._notify_vendor_whatsapp(vendor, order, items, pin)

        # 3. Post to rider group — read chat id from vendor record
        group_chat_id = vendor.get('rider_group_chat_id') if vendor else None
        if not group_chat_id:
            # Fallback to config env var if vendor record has no group set
            group_chat_id = getattr(self.config, 'RIDER_GROUP_CHAT_ID', None)
        if group_chat_id:
            self._post_to_rider_group(group_chat_id, order, items, vendor, delivery_id)
        else:
            logger.warning(f"NotificationService: no rider_group_chat_id for vendor — skipping rider group post.")

        # 4. Notify customer
        self._notify_customer_confirmed(customer_phone, customer_platform, order_ref, amount_naira, vendor)

    # ── Vendor notifications ───────────────────────────────────────────────────

    def _notify_vendor_telegram(self, vendor: dict, order: dict, items: list, pin: str):
        """Send order details + PIN to vendor's personal Telegram."""
        text = self._build_vendor_message(order, items, pin)
        try:
            self.telegram.create_text_message(vendor['telegram_chat_id'], text)
            self.db.log_notification(
                order_id=order['id'],
                recipient_type='vendor',
                platform='telegram',
                chat_id=vendor['telegram_chat_id'],
                message=text,
                status='sent',
            )
            logger.info(f"Vendor Telegram notified: {vendor['telegram_chat_id']}")
        except Exception as e:
            logger.error(f"Vendor Telegram notification failed: {e}")
            self.db.log_notification(
                order_id=order['id'],
                recipient_type='vendor',
                platform='telegram',
                chat_id=vendor['telegram_chat_id'],
                message=text,
                status='failed',
            )

    def _notify_vendor_whatsapp(self, vendor: dict, order: dict, items: list, pin: str):
        """Send order details + PIN to vendor's WhatsApp number."""
        text = self._build_vendor_message(order, items, pin)
        try:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type":    "individual",
                "to":                str(vendor['whatsapp_number']),
                "type":              "text",
                "text":              {"body": text},
            }
            self.whatsapp.send_message(payload)
            self.db.log_notification(
                order_id=order['id'],
                recipient_type='vendor',
                platform='whatsapp',
                chat_id=vendor['whatsapp_number'],
                message=text,
                status='sent',
            )
            logger.info(f"Vendor WhatsApp notified: {vendor['whatsapp_number']}")
        except Exception as e:
            logger.error(f"Vendor WhatsApp notification failed: {e}")

    def _build_vendor_message(self, order: dict, items: list, pin: str) -> str:
        items_text = "\n".join(
            f"  {item['name']} x{item['quantity']} — ₦{item['subtotal']:,}"
            for item in items
        ) or "  (no items recorded)"

        return (
            f"New Order Received! 🛎️\n\n"
            f"Order Ref: {order['order_ref']}\n"
            f"Delivery Address: {order.get('delivery_address') or 'Not provided'}\n\n"
            f"Items:\n{items_text}\n\n"
            f"Total: ₦{order['total']:,}\n"
            f"Payment: CONFIRMED ✅\n\n"
            f"Rider PIN: {pin}\n"
            f"(Rider will show you this PIN before collecting)"
        )

    # ── Rider group ────────────────────────────────────────────────────────────

    def _post_to_rider_group(
        self,
        group_chat_id: str,
        order: dict,
        items: list,
        vendor: dict,
        delivery_id: int,
    ):
        """Post order card to rider group with Accept and Unavailable buttons."""
        order_ref   = order['order_ref']
        vendor_name = vendor['name'] if vendor else "Vendor"
        address     = order.get('delivery_address') or 'Not provided'

        items_text = "\n".join(
            f"  {item['name']} x{item['quantity']}"
            for item in items
        ) or "  (see vendor for details)"

        text = (
            f"New Delivery 🛵\n\n"
            f"Ref: {order_ref}\n"
            f"Vendor: {vendor_name}\n"
            f"Deliver to: {address}\n\n"
            f"Items:\n{items_text}\n\n"
            f"Tap Accept to take this order."
        )

        buttons = [
            {
                "type":  "reply",
                "reply": {
                    "id":    f"accept_{order_ref}",
                    "title": "Accept"
                }
            },
            {
                "type":  "reply",
                "reply": {
                    "id":    f"unavailable_{order_ref}",
                    "title": "Unavailable"
                }
            },
        ]

        try:
            result = self.telegram.send_button_message(group_chat_id, text, buttons)
            # Store the group message_id so we can edit it later
            if result and result.get('result'):
                group_message_id = str(result['result']['message_id'])
                self.db._execute(
                    "UPDATE deliveries SET group_message_id = %s WHERE id = %s",
                    (group_message_id, delivery_id)
                )
                logger.info(f"Rider group posted: message_id={group_message_id}, ref={order_ref}")

            self.db.log_notification(
                order_id=order['id'],
                recipient_type='rider',
                platform='telegram',
                chat_id=group_chat_id,
                message=text,
                status='sent',
            )
        except Exception as e:
            logger.error(f"Rider group post failed: {e}")

    # ── Customer notifications ─────────────────────────────────────────────────

    def _notify_customer_confirmed(
        self,
        customer_phone: str,
        platform: str,
        order_ref: str,
        amount_naira: int,
        vendor: dict,
    ):
        """Tell customer their payment is confirmed and we're finding a rider."""
        vendor_name = vendor['name'] if vendor else "the kitchen"
        text = (
            f"Payment confirmed! Thank you 🎉\n\n"
            f"Order Ref: {order_ref}\n"
            f"Amount Paid: ₦{amount_naira:,}\n\n"
            f"Your order has been sent to {vendor_name} and we are finding a rider for you.\n"
            f"We will update you as soon as a rider accepts.\n\n"
            f"Reply 'order update' anytime to check your status."
        )
        self._send_to_customer(customer_phone, platform, text)

    def notify_customer_rider_accepted(
        self,
        customer_phone: str,
        platform: str,
        rider_name: str,
        order_ref: str,
    ):
        text = (
            f"Great news! 🛵\n\n"
            f"Rider: {rider_name}\n"
            f"is heading to pick up your order ({order_ref}).\n\n"
            f"They will collect from the vendor shortly."
        )
        self._send_to_customer(customer_phone, platform, text)

    def notify_customer_picked_up(
        self,
        customer_phone: str,
        platform: str,
        rider_name: str,
        order_ref: str,
    ):
        text = (
            f"Your order is on the way! 🚀\n\n"
            f"Rider {rider_name} has picked up your order ({order_ref}) "
            f"and is heading to you now.\n\n"
            f"Should be with you shortly!"
        )
        self._send_to_customer(customer_phone, platform, text)

    def notify_customer_delivered(
        self,
        customer_phone: str,
        platform: str,
        order_ref: str,
    ):
        text = (
            f"Order delivered! 🎉\n\n"
            f"Your order ({order_ref}) has been delivered.\n"
            f"Enjoy your meal!\n\n"
            f"Thank you for ordering with Lola. Come back anytime!"
        )
        self._send_to_customer(customer_phone, platform, text)

    def notify_customer_finding_rider(
        self,
        customer_phone: str,
        platform: str,
        order_ref: str,
    ):
        """Called on 15-min timeout — reassure customer while re-posting to group."""
        text = (
            f"We are still finding a rider for your order ({order_ref}).\n\n"
            f"Please bear with us — we will notify you as soon as one accepts."
        )
        self._send_to_customer(customer_phone, platform, text)

    def _send_to_customer(self, customer_phone: str, platform: str, text: str):
        try:
            if platform == 'telegram':
                self.telegram.create_text_message(customer_phone, text)
            else:
                if not self.whatsapp:
                    logger.warning("WhatsApp service not available for customer notification.")
                    return
                payload = {
                    "messaging_product": "whatsapp",
                    "recipient_type":    "individual",
                    "to":                str(customer_phone),
                    "type":              "text",
                    "text":              {"body": text},
                }
                self.whatsapp.send_message(payload)
            logger.info(f"Customer notified: {customer_phone} via {platform}")
        except Exception as e:
            logger.error(f"Customer notification failed ({customer_phone}): {e}")

    # ── Rider group message editing ────────────────────────────────────────────

    def edit_rider_group_message(
        self,
        group_message_id: str,
        order_ref: str,
        rider_name: str,
        status: str,  # 'accepted' | 'picked_up' | 'delivered'
        group_chat_id: str = None,
    ):
        """Edit the original rider group message to reflect current status."""
        if not group_chat_id:
            group_chat_id = getattr(self.config, 'RIDER_GROUP_CHAT_ID', None)
        if not group_chat_id or not group_message_id:
            return

        status_text = {
            'accepted':  f"✅ Accepted by {rider_name}",
            'picked_up': f"🛵 Picked up by {rider_name} — in transit",
            'delivered': f"🎉 Delivered by {rider_name}",
        }.get(status, status)

        text = f"Order {order_ref}\n{status_text}"

        try:
            self.telegram._post("editMessageText", {
                "chat_id":    str(group_chat_id),
                "message_id": int(group_message_id),
                "text":       text,
            })
            logger.info(f"Rider group message edited: ref={order_ref}, status={status}")
        except Exception as e:
            logger.error(f"Could not edit rider group message: {e}")

    # ── Re-post on timeout ─────────────────────────────────────────────────────

    def repost_to_rider_group(self, order_ref: str):
        """
        Called by timeout handler when no rider accepted within 15 minutes.
        Re-fetches order and re-posts to rider group.
        """
        order = self.db.get_order_by_ref(order_ref)
        if not order:
            logger.error(f"repost_to_rider_group: order not found for ref={order_ref}")
            return

        vendor = None
        if order.get('vendor_id'):
            vendor = self.db.get_vendor_by_id(order['vendor_id'])

        items = self._get_order_items(order['id'])

        # Get current delivery record
        delivery = self.db._execute(
            "SELECT id FROM deliveries WHERE order_id = %s ORDER BY created_at DESC LIMIT 1",
            (order['id'],),
            fetch='one'
        )
        if not delivery:
            logger.error(f"repost_to_rider_group: no delivery record for order_id={order['id']}")
            return

        group_chat_id = vendor.get('rider_group_chat_id') if vendor else None
        if not group_chat_id:
            group_chat_id = getattr(self.config, 'RIDER_GROUP_CHAT_ID', None)
        if group_chat_id:
            self._post_to_rider_group(group_chat_id, order, items, vendor, delivery['id'])
            logger.info(f"Order {order_ref} re-posted to rider group after timeout.")

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _generate_pin(self) -> str:
        return ''.join(random.choices(string.digits, k=4))

    def _get_order_items(self, order_id: int) -> list:
        rows = self.db._execute(
            "SELECT name, quantity, subtotal FROM order_items WHERE order_id = %s",
            (order_id,),
            fetch='all'
        )
        return [dict(r) for r in rows] if rows else []