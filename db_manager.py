import logging
import psycopg2
import psycopg2.extras
from typing import Optional, List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class DBManager:
    """
    Handles all PostgreSQL database operations for the Makinde Kitchen bot.
    Saves every customer message and bot response, orders, and order items.
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

    # ── Customers ──────────────────────────────────────────────────────────────

    def upsert_customer(self, phone_number: str, name: str = None) -> Optional[int]:
        """
        Insert customer if not exists, update name if provided.
        Returns customer id.
        """
        try:
            if name:
                row = self._execute(
                    """
                    INSERT INTO customers (phone_number, name, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (phone_number)
                    DO UPDATE SET
                        name = COALESCE(EXCLUDED.name, customers.name),
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (phone_number, name),
                    fetch='one'
                )
            else:
                row = self._execute(
                    """
                    INSERT INTO customers (phone_number, updated_at)
                    VALUES (%s, NOW())
                    ON CONFLICT (phone_number)
                    DO UPDATE SET updated_at = NOW()
                    RETURNING id
                    """,
                    (phone_number,),
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
        order_id: int = None
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
            customer_id = self.upsert_customer(phone_number, customer_name)
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

    # ── Orders ─────────────────────────────────────────────────────────────────

    def create_order(
        self,
        order_ref: str,
        phone_number: str,
        delivery_address: str,
        subtotal: int,
        delivery_fee: int,
        total: int,
        customer_name: str = None
    ) -> Optional[int]:
        """
        Create a new order record.
        Returns the new order id.
        """
        try:
            customer_id = self.upsert_customer(phone_number, customer_name)
            if not customer_id:
                logger.warning(f"create_order: could not upsert customer {phone_number}")
                return None

            row = self._execute(
                """
                INSERT INTO orders
                    (order_ref, customer_id, delivery_address, subtotal, delivery_fee, total,
                     status, payment_status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'payment_sent', 'unpaid', NOW(), NOW())
                RETURNING id
                """,
                (order_ref, customer_id, delivery_address, subtotal, delivery_fee, total),
                fetch='one'
            )
            if row:
                logger.info(f"Order created: ref={order_ref}, id={row['id']}")
                return row['id']
        except Exception as e:
            logger.error(f"DBManager.create_order error: {e}")
        return None

    def save_order_items(self, order_id: int, items: List[Dict]) -> bool:
        """
        Save order line items.

        Each item dict should have:
            name, price (naira), quantity, subtotal (naira)
            product_id is optional
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
        """
        Update payment status on an order.
        Called from payment_webhook when Paystack confirms payment.
        """
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

    # ── Products ───────────────────────────────────────────────────────────────

    def get_all_products(self) -> List[Dict]:
        """Get all available products."""
        rows = self._execute(
            "SELECT * FROM products WHERE is_available = TRUE ORDER BY category, name",
            fetch='all'
        )
        return [dict(r) for r in rows] if rows else []

    def get_products_by_category(self, category: str) -> List[Dict]:
        """Get available products by category."""
        rows = self._execute(
            "SELECT * FROM products WHERE category = %s AND is_available = TRUE ORDER BY name",
            (category,),
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