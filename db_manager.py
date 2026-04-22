import logging
import psycopg2
import psycopg2.extras
from typing import Optional, List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class DBManager:
    """
    Handles all PostgreSQL database operations for the Lola multi-vendor bot.
    Supports vendors, products, orders, payments, deliveries, and notifications.
    """

    def __init__(self, config):
        self.db_url = getattr(config, 'DB_URL', None)
        self._conn = None

        if not self.db_url:
            logger.error("DBManager: DB_URL is not set in config. Database saving disabled.")
        else:
            self._test_connection()

    # ── Connection ─────────────────────────────────────────────────────────────

    def _get_conn(self):
        """Get or reestablish database connection."""
        try:
            if self._conn is None or self._conn.closed:
                self._conn = psycopg2.connect(self.db_url)
                self._conn.autocommit = False
            return self._conn
        except Exception as e:
            logger.error(f"DBManager: Could not connect to database: {e}")
            return None

    def _test_connection(self):
        conn = self._get_conn()
        if conn:
            logger.info("DBManager: Database connection successful.")
        else:
            logger.error("DBManager: Database connection failed.")

    def _execute(self, query: str, params: tuple = None, fetch: str = None):
        """
        Execute a query safely.
        fetch: None | 'one' | 'all'
        Returns fetched rows or None.
        """
        conn = self._get_conn()
        if not conn:
            return None
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params)
                conn.commit()
                if fetch == 'one':
                    return cur.fetchone()
                if fetch == 'all':
                    return cur.fetchall()
                return True
        except Exception as e:
            conn.rollback()
            logger.error(f"DBManager query error: {e} | Query: {query[:100]}")
            return None

    # ── Vendors ────────────────────────────────────────────────────────────────

    def get_all_vendors(self) -> List[Dict]:
        """Get all active vendors ordered by name."""
        rows = self._execute(
            """
            SELECT id, name, description, type, logo_url, menu_image_url,
                   zone, delivery_fee, free_delivery_min, opening_hours,
                   delivery_areas, support_contact, order_ref_prefix,
                   rider_group_chat_id, telegram_chat_id, whatsapp_number
            FROM vendors
            WHERE is_active = TRUE
            ORDER BY name
            """,
            fetch='all'
        )
        return [dict(r) for r in rows] if rows else []

    def get_vendor_by_id(self, vendor_id: int) -> Optional[Dict]:
        """Get a single vendor by id."""
        row = self._execute(
            """
            SELECT id, name, description, type, logo_url, menu_image_url,
                   telegram_chat_id, whatsapp_number, zone, delivery_fee,
                   free_delivery_min, opening_hours, delivery_areas,
                   support_contact, order_ref_prefix, rider_group_chat_id
            FROM vendors
            WHERE id = %s AND is_active = TRUE
            """,
            (vendor_id,),
            fetch='one'
        )
        return dict(row) if row else None

    # ── Customers ──────────────────────────────────────────────────────────────

    def upsert_customer(
        self,
        phone_number: str,
        name: str = None,
        platform: str = 'whatsapp'
    ) -> Optional[int]:
        """
        Insert customer if not exists, update name/platform if provided.
        Returns customer id.
        """
        try:
            if name:
                row = self._execute(
                    """
                    INSERT INTO customers (phone_number, name, platform, updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (phone_number)
                    DO UPDATE SET
                        name     = COALESCE(EXCLUDED.name, customers.name),
                        platform = COALESCE(EXCLUDED.platform, customers.platform),
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (phone_number, name, platform),
                    fetch='one'
                )
            else:
                row = self._execute(
                    """
                    INSERT INTO customers (phone_number, platform, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (phone_number)
                    DO UPDATE SET
                        platform = COALESCE(EXCLUDED.platform, customers.platform),
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (phone_number, platform),
                    fetch='one'
                )
            if row:
                return row['id']
        except Exception as e:
            logger.error(f"DBManager.upsert_customer error: {e}")
        return None

    def get_customer_id(self, phone_number: str) -> Optional[int]:
        """Get customer id by phone number."""
        row = self._execute(
            "SELECT id FROM customers WHERE phone_number = %s",
            (phone_number,),
            fetch='one'
        )
        return row['id'] if row else None

    # ── Conversations ──────────────────────────────────────────────────────────

    def save_message(
        self,
        phone_number: str,
        role: str,
        message: str,
        customer_name: str = None,
        order_id: int = None,
        platform: str = 'whatsapp'
    ):
        """
        Save a single message (user or assistant) to conversations table.
        Automatically upserts the customer.
        Silently skips if DB is not configured.
        """
        if not self.db_url:
            return
        if not phone_number or not message:
            return
        try:
            customer_id = self.upsert_customer(phone_number, customer_name, platform)
            if not customer_id:
                logger.warning(f"save_message: could not upsert customer {phone_number}")
                return

            self._execute(
                """
                INSERT INTO conversations (customer_id, order_id, role, message, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                """,
                (customer_id, order_id, role, message)
            )
            logger.debug(f"Saved [{role}] message for {phone_number}")
        except Exception as e:
            logger.error(f"DBManager.save_message error: {e}")

    def get_conversation_history(self, phone_number: str, limit: int = 50) -> List[Dict]:
        """Get recent conversation history for a customer."""
        try:
            customer_id = self.get_customer_id(phone_number)
            if not customer_id:
                return []
            rows = self._execute(
                """
                SELECT role, message, order_id, created_at
                FROM conversations
                WHERE customer_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (customer_id, limit),
                fetch='all'
            )
            return [dict(r) for r in rows] if rows else []
        except Exception as e:
            logger.error(f"DBManager.get_conversation_history error: {e}")
            return []

    # ── Products ───────────────────────────────────────────────────────────────

    def get_all_products(self, vendor_id: int = None) -> List[Dict]:
        """Get all available products, optionally filtered by vendor."""
        if vendor_id:
            rows = self._execute(
                """
                SELECT * FROM products
                WHERE is_available = TRUE AND vendor_id = %s
                ORDER BY category, name
                """,
                (vendor_id,),
                fetch='all'
            )
        else:
            rows = self._execute(
                "SELECT * FROM products WHERE is_available = TRUE ORDER BY category, name",
                fetch='all'
            )
        return [dict(r) for r in rows] if rows else []

    def get_products_by_category(self, category: str, vendor_id: int = None) -> List[Dict]:
        """Get available products by category, optionally filtered by vendor."""
        if vendor_id:
            rows = self._execute(
                """
                SELECT * FROM products
                WHERE category = %s AND is_available = TRUE AND vendor_id = %s
                ORDER BY name
                """,
                (category, vendor_id),
                fetch='all'
            )
        else:
            rows = self._execute(
                "SELECT * FROM products WHERE category = %s AND is_available = TRUE ORDER BY name",
                (category,),
                fetch='all'
            )
        return [dict(r) for r in rows] if rows else []

    def format_menu_text(self, vendor_id: int) -> str:
        """
        Build a formatted menu string from DB products for a given vendor.
        Used to inject into the AI system prompt.
        """
        products = self.get_all_products(vendor_id)
        if not products:
            return "Menu not available."

        # Group by category
        categories: Dict[str, List[Dict]] = {}
        for p in products:
            cat = p.get('category') or 'Other'
            categories.setdefault(cat, []).append(p)

        lines = []
        for cat, items in categories.items():
            lines.append(f"\n{cat.upper()}")
            for item in items:
                desc = f" ({item['description']})" if item.get('description') else ""
                lines.append(f"- {item['name']}{desc} — ₦{item['price']:,}")

        return "\n".join(lines)

    # ── Orders ─────────────────────────────────────────────────────────────────

    def create_order(
        self,
        order_ref: str,
        phone_number: str,
        delivery_address: str,
        subtotal: int,
        delivery_fee: int,
        total: int,
        customer_name: str = None,
        vendor_id: int = None,
        platform: str = 'whatsapp'
    ) -> Optional[int]:
        """
        Create a new order record.
        Returns the new order id.
        """
        try:
            customer_id = self.upsert_customer(phone_number, customer_name, platform)
            if not customer_id:
                logger.warning(f"create_order: could not upsert customer {phone_number}")
                return None

            row = self._execute(
                """
                INSERT INTO orders
                    (order_ref, customer_id, vendor_id, delivery_address,
                     subtotal, delivery_fee, total, status, payment_status,
                     platform, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'payment_sent', 'unpaid', %s, NOW(), NOW())
                RETURNING id
                """,
                (order_ref, customer_id, vendor_id, delivery_address,
                 subtotal, delivery_fee, total, platform),
                fetch='one'
            )
            if row:
                logger.info(f"Order created: ref={order_ref}, id={row['id']}, vendor_id={vendor_id}")
                return row['id']
        except Exception as e:
            logger.error(f"DBManager.create_order error: {e}")
        return None

    def save_order_items(self, order_id: int, items: List[Dict]) -> bool:
        """
        Save order line items.
        Each item dict should have: name, price (naira), quantity, subtotal (naira).
        product_id is optional.
        """
        try:
            conn = self._get_conn()
            if not conn:
                return False

            with conn.cursor() as cur:
                for item in items:
                    cur.execute(
                        """
                        INSERT INTO order_items
                            (order_id, product_id, name, price, quantity, subtotal, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW())
                        """,
                        (
                            order_id,
                            item.get('product_id'),
                            item['name'],
                            item['price'],
                            item['quantity'],
                            item['subtotal'],
                        )
                    )
            conn.commit()
            logger.info(f"Saved {len(items)} order items for order_id={order_id}")
            return True
        except Exception as e:
            if self._conn:
                self._conn.rollback()
            logger.error(f"DBManager.save_order_items error: {e}")
            return False

    def update_order_payment(
        self,
        order_ref: str,
        payment_status: str,
        payment_ref: str = None,
        status: str = None
    ) -> bool:
        """Update payment status on an order."""
        try:
            self._execute(
                """
                UPDATE orders
                SET payment_status = %s,
                    payment_ref = COALESCE(%s, payment_ref),
                    status = COALESCE(%s, status),
                    updated_at = NOW()
                WHERE order_ref = %s
                """,
                (payment_status, payment_ref, status, order_ref)
            )
            logger.info(f"Order {order_ref} updated: payment_status={payment_status}")
            return True
        except Exception as e:
            logger.error(f"DBManager.update_order_payment error: {e}")
            return False

    def get_order_by_ref(self, order_ref: str) -> Optional[Dict]:
        """Get order details by order reference."""
        row = self._execute(
            "SELECT * FROM orders WHERE order_ref = %s",
            (order_ref,),
            fetch='one'
        )
        return dict(row) if row else None

    # ── Payments ───────────────────────────────────────────────────────────────

    def log_payment(
        self,
        order_id: int,
        order_ref: str,
        amount: int,
        payment_ref: str = None,
        gateway: str = 'paystack',
        status: str = 'pending',
        webhook_payload: dict = None
    ) -> Optional[int]:
        """Log a payment record. Returns payment id."""
        try:
            import json
            payload_json = json.dumps(webhook_payload) if webhook_payload else None
            row = self._execute(
                """
                INSERT INTO payments
                    (order_id, order_ref, amount, payment_ref, gateway, status, webhook_payload, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
                RETURNING id
                """,
                (order_id, order_ref, amount, payment_ref, gateway, status, payload_json),
                fetch='one'
            )
            return row['id'] if row else None
        except Exception as e:
            logger.error(f"DBManager.log_payment error: {e}")
            return None

    # ── Deliveries ─────────────────────────────────────────────────────────────

    def create_delivery(
        self,
        order_id: int,
        pin: str,
        timeout_at: datetime = None
    ) -> Optional[int]:
        """Create a delivery record for an order. Returns delivery id."""
        try:
            row = self._execute(
                """
                INSERT INTO deliveries (order_id, pin, timeout_at, status, created_at)
                VALUES (%s, %s, %s, 'pending', NOW())
                RETURNING id
                """,
                (order_id, pin, timeout_at),
                fetch='one'
            )
            return row['id'] if row else None
        except Exception as e:
            logger.error(f"DBManager.create_delivery error: {e}")
            return None

    def update_delivery_status(
        self,
        order_id: int,
        status: str,
        rider_name: str = None,
        rider_phone: str = None
    ) -> bool:
        """Update delivery status. Status: pending | accepted | picked_up | delivered."""
        try:
            timestamp_col = {
                'accepted':  'accepted_at',
                'picked_up': 'picked_up_at',
                'delivered': 'delivered_at',
            }.get(status)

            if timestamp_col:
                self._execute(
                    f"""
                    UPDATE deliveries
                    SET status = %s,
                        rider_name  = COALESCE(%s, rider_name),
                        rider_phone = COALESCE(%s, rider_phone),
                        {timestamp_col} = NOW()
                    WHERE order_id = %s
                    """,
                    (status, rider_name, rider_phone, order_id)
                )
            else:
                self._execute(
                    """
                    UPDATE deliveries
                    SET status = %s,
                        rider_name  = COALESCE(%s, rider_name),
                        rider_phone = COALESCE(%s, rider_phone)
                    WHERE order_id = %s
                    """,
                    (status, rider_name, rider_phone, order_id)
                )
            logger.info(f"Delivery for order_id={order_id} updated to status={status}")
            return True
        except Exception as e:
            logger.error(f"DBManager.update_delivery_status error: {e}")
            return False

    # ── Notifications ──────────────────────────────────────────────────────────

    def log_notification(
        self,
        order_id: int,
        recipient_type: str,
        platform: str,
        chat_id: str,
        message: str,
        status: str = 'sent'
    ) -> Optional[int]:
        """Log an outgoing notification. Returns notification id."""
        try:
            row = self._execute(
                """
                INSERT INTO notifications
                    (order_id, recipient_type, platform, chat_id, message, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (order_id, recipient_type, platform, chat_id, message, status),
                fetch='one'
            )
            return row['id'] if row else None
        except Exception as e:
            logger.error(f"DBManager.log_notification error: {e}")
            return None

    # ── Riders ────────────────────────────────────────────────────────────────

    def get_rider_by_telegram_id(self, telegram_id: str) -> Optional[Dict]:
        """Get rider record by their personal Telegram chat_id."""
        row = self._execute(
            "SELECT * FROM riders WHERE telegram_id = %s AND is_active = TRUE",
            (telegram_id,),
            fetch='one'
        )
        return dict(row) if row else None

    def get_all_riders(self) -> List[Dict]:
        """Get all active riders."""
        rows = self._execute(
            "SELECT * FROM riders WHERE is_active = TRUE ORDER BY name",
            fetch='all'
        )
        return [dict(r) for r in rows] if rows else []

    def save_rider_onboarding(
        self,
        telegram_id: str,
        name: str,
        email: str,
        phone: str,
        hall: str,
        room_number: str,
        course: str,
    ) -> bool:
        """
        Upsert a rider's KYC details.
        Creates the row if it doesn't exist; updates it if the rider was
        auto-created when they first tapped Accept.
        """
        try:
            self._execute(
                """
                INSERT INTO riders
                    (telegram_id, name, phone_number, email, hall,
                     room_number, course, onboarding_complete, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, TRUE)
                ON CONFLICT (telegram_id)
                DO UPDATE SET
                    name                = EXCLUDED.name,
                    phone_number        = EXCLUDED.phone_number,
                    email               = EXCLUDED.email,
                    hall                = EXCLUDED.hall,
                    room_number         = EXCLUDED.room_number,
                    course              = EXCLUDED.course,
                    onboarding_complete = TRUE,
                    updated_at          = NOW()
                """,
                (telegram_id, name, phone, email, hall, room_number, course),
            )
            logger.info(f"Rider onboarding saved for telegram_id={telegram_id}")
            return True
        except Exception as e:
            logger.error(f"save_rider_onboarding error: {e}")
            return False

    def get_delivery_by_order_ref(self, order_ref: str) -> Optional[Dict]:
        """Get the most recent delivery record for an order reference."""
        row = self._execute(
            """
            SELECT d.*
            FROM deliveries d
            JOIN orders o ON d.order_id = o.id
            WHERE o.order_ref = %s
            ORDER BY d.created_at DESC
            LIMIT 1
            """,
            (order_ref,),
            fetch='one'
        )
        return dict(row) if row else None

    def get_vendor_with_rider_group(self, vendor_id: int) -> Optional[Dict]:
        """Get vendor including rider_group_chat_id."""
        row = self._execute(
            """
            SELECT id, name, telegram_chat_id, whatsapp_number,
                   rider_group_chat_id, delivery_fee, free_delivery_min
            FROM vendors
            WHERE id = %s AND is_active = TRUE
            """,
            (vendor_id,),
            fetch='one'
        )
        return dict(row) if row else None

    def assign_rider(self, delivery_id: int, rider_telegram_id: str, rider_id: int = None) -> bool:
        """Assign a rider to a delivery record."""
        try:
            self._execute(
                """
                UPDATE deliveries
                SET rider_telegram_id = %s,
                    rider_id          = COALESCE(%s, rider_id),
                    status            = 'accepted',
                    accepted_at       = NOW()
                WHERE id = %s
                """,
                (rider_telegram_id, rider_id, delivery_id)
            )
            return True
        except Exception as e:
            logger.error(f"DBManager.assign_rider error: {e}")
            return False

    def get_pending_deliveries_past_timeout(self) -> List[Dict]:
        """
        Get all deliveries that are still pending but past their timeout.
        Used by a scheduled job to trigger re-posting to the rider group.
        """
        rows = self._execute(
            """
            SELECT d.*, o.order_ref, o.vendor_id
            FROM deliveries d
            JOIN orders o ON d.order_id = o.id
            WHERE d.status = 'pending'
              AND d.timeout_at IS NOT NULL
              AND d.timeout_at < NOW()
            """,
            fetch='all'
        )
        return [dict(r) for r in rows] if rows else []

    # ── Cleanup ────────────────────────────────────────────────────────────────

    def close(self):
        """Close the database connection."""
        try:
            if self._conn and not self._conn.closed:
                self._conn.close()
                logger.info("DBManager: connection closed.")
        except Exception as e:
            logger.error(f"DBManager.close error: {e}")