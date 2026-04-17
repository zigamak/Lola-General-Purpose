import logging
from typing import Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class DeliveryHandler:
    """
    Handles rider button taps from the Telegram rider group.

    Callback data format:
        accept_{order_ref}       — rider accepts the order
        picked_{order_ref}       — rider collected from vendor
        delivered_{order_ref}    — rider delivered to customer
        unavailable_{order_ref}  — vendor is out of stock

    All callbacks come through telegram_webhook.py which detects
    that the sender is in the rider group and routes here instead
    of MessageProcessor.
    """

    def __init__(self, config, db_manager, notification_service, session_manager=None):
        self.config               = config
        self.db                   = db_manager
        self.notification         = notification_service
        self.session_manager      = session_manager
        logger.info("DeliveryHandler initialised.")

    # ── Main entry point ───────────────────────────────────────────────────────

    def handle_callback(
        self,
        callback_data: str,
        rider_telegram_id: str,
        rider_name: str,
    ) -> dict:
        """
        Called by telegram_webhook for any callback that matches a delivery action.
        Returns a dict with status for logging.
        """
        logger.info(f"DeliveryHandler: callback='{callback_data}' from rider={rider_telegram_id}")

        action, order_ref = self._parse_callback(callback_data)
        if not action or not order_ref:
            logger.warning(f"DeliveryHandler: unrecognised callback '{callback_data}'")
            return {"status": "ignored"}

        if action == "accept":
            return self._handle_accept(order_ref, rider_telegram_id, rider_name)
        if action == "picked":
            return self._handle_picked_up(order_ref, rider_telegram_id, rider_name)
        if action == "delivered":
            return self._handle_delivered(order_ref, rider_telegram_id, rider_name)
        if action == "unavailable":
            return self._handle_unavailable(order_ref)

        return {"status": "ignored"}

    # ── Accept ─────────────────────────────────────────────────────────────────

    def _handle_accept(
        self,
        order_ref: str,
        rider_telegram_id: str,
        rider_name: str,
    ) -> dict:
        """First rider to tap Accept gets the order."""

        # Check order is still available (not already accepted)
        delivery = self._get_delivery(order_ref)
        if not delivery:
            logger.warning(f"Accept: no delivery record for ref={order_ref}")
            return {"status": "not_found"}

        if delivery['status'] != 'pending':
            # Another rider already accepted — notify the late rider privately
            logger.info(f"Accept: order {order_ref} already {delivery['status']} — ignoring late tap from {rider_telegram_id}.")
            try:
                self.notification.telegram.create_text_message(
                    rider_telegram_id,
                    f"Sorry {rider_name}, order {order_ref} was already taken by another rider."
                )
            except Exception:
                pass
            return {"status": "already_taken"}

        order = self.db.get_order_by_ref(order_ref)
        if not order:
            return {"status": "not_found"}

        # Get or create rider record
        rider_id = self._upsert_rider(rider_telegram_id, rider_name)

        # Update delivery status
        self.db.update_delivery_status(
            order_id=order['id'],
            status='accepted',
            rider_name=rider_name,
            rider_phone=None,
        )
        self.db._execute(
            "UPDATE deliveries SET rider_telegram_id = %s, rider_id = %s WHERE order_id = %s",
            (rider_telegram_id, rider_id, order['id'])
        )

        # Edit group message
        group_message_id = delivery.get('group_message_id')
        self.notification.edit_rider_group_message(
            group_message_id=group_message_id,
            order_ref=order_ref,
            rider_name=rider_name,
            status='accepted',
        )

        # Send accepted notice to group + PIN privately to rider
        pin = delivery.get('pin', '????')
        self._send_rider_details_to_group(order, rider_name, pin, rider_telegram_id)

        # Notify customer
        customer_phone, platform = self._get_customer_contact(order)
        if customer_phone:
            self.notification.notify_customer_rider_accepted(
                customer_phone=customer_phone,
                platform=platform,
                rider_name=rider_name,
                order_ref=order_ref,
            )

        logger.info(f"Order {order_ref} accepted by rider {rider_name} ({rider_telegram_id})")
        return {"status": "accepted", "order_ref": order_ref, "rider": rider_name}

    # ── Picked up ──────────────────────────────────────────────────────────────

    def _handle_picked_up(
        self,
        order_ref: str,
        rider_telegram_id: str,
        rider_name: str,
    ) -> dict:
        delivery = self._get_delivery(order_ref)
        if not delivery:
            return {"status": "not_found"}

        # Only the assigned rider can mark picked up
        if delivery.get('rider_telegram_id') != rider_telegram_id:
            logger.warning(f"Picked up: wrong rider {rider_telegram_id} for {order_ref}")
            try:
                self.notification.telegram.create_text_message(
                    rider_telegram_id,
                    f"This order ({order_ref}) is not assigned to you."
                )
            except Exception:
                pass
            return {"status": "not_your_order"}

        order = self.db.get_order_by_ref(order_ref)
        if not order:
            return {"status": "not_found"}

        self.db.update_delivery_status(
            order_id=order['id'],
            status='picked_up',
        )

        # Edit group message
        self.notification.edit_rider_group_message(
            group_message_id=delivery.get('group_message_id'),
            order_ref=order_ref,
            rider_name=rider_name,
            status='picked_up',
        )

        # Notify customer
        customer_phone, platform = self._get_customer_contact(order)
        if customer_phone:
            self.notification.notify_customer_picked_up(
                customer_phone=customer_phone,
                platform=platform,
                rider_name=rider_name,
                order_ref=order_ref,
            )

        logger.info(f"Order {order_ref} picked up by {rider_name}")
        return {"status": "picked_up", "order_ref": order_ref}

    # ── Delivered ──────────────────────────────────────────────────────────────

    def _handle_delivered(
        self,
        order_ref: str,
        rider_telegram_id: str,
        rider_name: str,
    ) -> dict:
        delivery = self._get_delivery(order_ref)
        if not delivery:
            return {"status": "not_found"}

        if delivery.get('rider_telegram_id') != rider_telegram_id:
            logger.warning(f"Delivered: wrong rider {rider_telegram_id} for {order_ref}")
            try:
                self.notification.telegram.create_text_message(
                    rider_telegram_id,
                    f"This order ({order_ref}) is not assigned to you."
                )
            except Exception:
                pass
            return {"status": "not_your_order"}

        order = self.db.get_order_by_ref(order_ref)
        if not order:
            return {"status": "not_found"}

        self.db.update_delivery_status(
            order_id=order['id'],
            status='delivered',
        )

        # Update order status to delivered
        self.db._execute(
            "UPDATE orders SET status = 'delivered', updated_at = NOW() WHERE order_ref = %s",
            (order_ref,)
        )

        # Edit group message
        self.notification.edit_rider_group_message(
            group_message_id=delivery.get('group_message_id'),
            order_ref=order_ref,
            rider_name=rider_name,
            status='delivered',
        )

        # Notify customer
        customer_phone, platform = self._get_customer_contact(order)
        if customer_phone:
            self.notification.notify_customer_delivered(
                customer_phone=customer_phone,
                platform=platform,
                order_ref=order_ref,
            )

        logger.info(f"Order {order_ref} delivered by {rider_name}")
        return {"status": "delivered", "order_ref": order_ref}

    # ── Unavailable ────────────────────────────────────────────────────────────

    def _handle_unavailable(self, order_ref: str) -> dict:
        """Vendor is out of stock — cancel order and notify customer."""
        order = self.db.get_order_by_ref(order_ref)
        if not order:
            return {"status": "not_found"}

        self.db._execute(
            "UPDATE orders SET status = 'cancelled', updated_at = NOW() WHERE order_ref = %s",
            (order_ref,)
        )

        # Notify customer
        customer_phone, platform = self._get_customer_contact(order)
        if customer_phone:
            text = (
                f"We're sorry — the vendor is unable to fulfil your order ({order_ref}) "
                f"right now.\n\n"
                f"You will receive a full refund within 24 hours. "
                f"Please contact support if you have any questions."
            )
            self.notification._send_to_customer(customer_phone, platform, text)

        logger.info(f"Order {order_ref} marked unavailable.")
        return {"status": "unavailable", "order_ref": order_ref}

    # ── Timeout handler ────────────────────────────────────────────────────────

    def handle_timeout(self, order_ref: str):
        """
        Called when no rider accepts within 15 minutes.
        Resets delivery to pending and re-posts to rider group.
        Can be triggered by a scheduled job or APScheduler.
        """
        order = self.db.get_order_by_ref(order_ref)
        if not order:
            return

        delivery = self._get_delivery(order_ref)
        if not delivery or delivery['status'] != 'pending':
            # Already accepted or resolved — no action needed
            return

        logger.info(f"Timeout: no rider accepted {order_ref} in 15 min — re-posting.")

        # Notify customer
        customer_phone, platform = self._get_customer_contact(order)
        if customer_phone:
            self.notification.notify_customer_finding_rider(
                customer_phone=customer_phone,
                platform=platform,
                order_ref=order_ref,
            )

        # Re-post to rider group
        self.notification.repost_to_rider_group(order_ref)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _parse_callback(self, callback_data: str):
        """
        Parse 'action_ORDERREF' format.
        Returns (action, order_ref) or (None, None).
        """
        if not callback_data:
            return None, None
        parts = callback_data.split('_', 1)
        if len(parts) == 2:
            return parts[0].lower(), parts[1]
        return None, None

    def is_delivery_callback(self, callback_data: str) -> bool:
        """Returns True if this callback belongs to DeliveryHandler."""
        action, _ = self._parse_callback(callback_data)
        return action in ('accept', 'picked', 'delivered', 'unavailable')

    def _get_delivery(self, order_ref: str) -> Optional[dict]:
        """Get the most recent delivery record for an order ref."""
        order = self.db.get_order_by_ref(order_ref)
        if not order:
            return None
        row = self.db._execute(
            """
            SELECT id, status, pin, rider_telegram_id, rider_name,
                   group_message_id, timeout_at
            FROM deliveries
            WHERE order_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (order['id'],),
            fetch='one'
        )
        return dict(row) if row else None

    def _get_customer_contact(self, order: dict):
        """Returns (phone_number, platform) for the customer on this order."""
        try:
            row = self.db._execute(
                "SELECT phone_number, platform FROM customers WHERE id = %s",
                (order['customer_id'],),
                fetch='one'
            )
            if row:
                return row['phone_number'], row.get('platform', 'whatsapp')
        except Exception as e:
            logger.error(f"_get_customer_contact error: {e}")
        return None, 'whatsapp'

    def _upsert_rider(self, telegram_id: str, name: str) -> Optional[int]:
        """Insert rider if not exists, return rider id."""
        try:
            row = self.db._execute(
                """
                INSERT INTO riders (telegram_id, name)
                VALUES (%s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET name = COALESCE(EXCLUDED.name, riders.name)
                RETURNING id
                """,
                (telegram_id, name),
                fetch='one'
            )
            return row['id'] if row else None
        except Exception as e:
            logger.error(f"_upsert_rider error: {e}")
            return None

    def _send_rider_details_to_group(
        self, order: dict, rider_name: str, pin: str, rider_telegram_id: str = None
    ):
        """
        After a rider accepts:
          - Post a short "accepted" notice to the group (no PIN)
          - Send full order details + PIN privately to the rider
        """
        vendor = None
        if order.get('vendor_id'):
            try:
                vendor = self.db.get_vendor_by_id(order['vendor_id'])
            except Exception:
                pass

        vendor_name   = vendor['name'] if vendor else "Vendor"
        order_ref     = order['order_ref']
        address       = order.get('delivery_address') or 'Check with vendor'

        # ── 1. Group message — no PIN, just status ─────────────────────────
        group_chat_id = vendor.get('rider_group_chat_id') if vendor else None
        if not group_chat_id:
            group_chat_id = getattr(self.config, 'RIDER_GROUP_CHAT_ID', None)

        if group_chat_id:
            group_text = (
                f"✅ Order {order_ref} accepted by {rider_name}\n"
                f"Vendor: {vendor_name}\n"
                f"Deliver to: {address}"
            )
            try:
                self.notification.telegram.create_text_message(group_chat_id, group_text)
                logger.info(f"Group notified: order {order_ref} accepted by {rider_name}")
            except Exception as e:
                logger.error(f"Group accept notice failed: {e}")

        # ── 2. Private message to rider — full details + PIN + buttons ─────
        if rider_telegram_id:
            private_text = (
                f"Order {order_ref} — assigned to you 🛵\n\n"
                f"Vendor: {vendor_name}\n"
                f"Deliver to: {address}\n\n"
                f"PIN: {pin}\n"
                f"(Show this PIN to the vendor before collecting)\n\n"
                f"Tap Picked Up once you have collected the order."
            )
            buttons = [
                {
                    "type":  "reply",
                    "reply": {"id": f"picked_{order_ref}", "title": "Picked Up"}
                },
                {
                    "type":  "reply",
                    "reply": {"id": f"delivered_{order_ref}", "title": "Delivered"}
                },
            ]
            try:
                self.notification.telegram.send_button_message(
                    rider_telegram_id, private_text, buttons
                )
                logger.info(f"PIN sent privately to rider {rider_telegram_id} for order {order_ref}")
            except Exception as e:
                logger.error(f"Private rider message failed: {e}")