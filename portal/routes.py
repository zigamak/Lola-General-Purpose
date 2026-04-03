"""
portal/routes.py
Flask blueprint for the Lola merchant portal.

Register in app.py:
    from portal.routes import portal_bp, init_portal
    init_portal(config)
    app.register_blueprint(portal_bp)
"""
import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from db_manager import DBManager

logger = logging.getLogger(__name__)

portal_bp = Blueprint("portal", __name__, url_prefix="/portal")

_config = None
_db = None


def init_portal(config):
    global _config, _db
    _config = config
    _db = DBManager(config)


# ── Dashboard ──────────────────────────────────────────────────────────────────

@portal_bp.route("/")
def dashboard():
    stats = {
        "total_customers": 0,
        "total_orders": 0,
        "paid_orders": 0,
        "total_messages": 0,
        "total_revenue": 0,
        "recent_orders": [],
        "error": None,
    }
    try:
        row = _db._execute("SELECT COUNT(*) as c FROM customers", fetch='one')
        stats["total_customers"] = row['c'] if row else 0

        row = _db._execute("SELECT COUNT(*) as c FROM orders", fetch='one')
        stats["total_orders"] = row['c'] if row else 0

        row = _db._execute("SELECT COUNT(*) as c FROM orders WHERE payment_status = 'paid'", fetch='one')
        stats["paid_orders"] = row['c'] if row else 0

        row = _db._execute("SELECT COUNT(*) as c FROM conversations", fetch='one')
        stats["total_messages"] = row['c'] if row else 0

        row = _db._execute("SELECT COALESCE(SUM(total),0) as c FROM orders WHERE payment_status='paid'", fetch='one')
        stats["total_revenue"] = row['c'] if row else 0

        recent = _db._execute(
            """SELECT o.order_ref, o.total, o.status, o.payment_status, o.created_at,
                      c.name, c.phone_number
               FROM orders o
               LEFT JOIN customers c ON o.customer_id = c.id
               ORDER BY o.created_at DESC LIMIT 5""",
            fetch='all'
        )
        stats["recent_orders"] = [dict(r) for r in recent] if recent else []

    except Exception as e:
        logger.error(f"Dashboard DB error: {e}")
        stats["error"] = str(e)

    return render_template("dashboard.html", stats=stats)


# ── Conversations ──────────────────────────────────────────────────────────────

@portal_bp.route("/conversations")
def conversations():
    rows = _db._execute(
        """SELECT c.id, c.phone_number, c.name, c.created_at,
                  COUNT(cv.id) as message_count,
                  MAX(cv.created_at) as last_message
           FROM customers c
           LEFT JOIN conversations cv ON c.id = cv.customer_id
           GROUP BY c.id, c.phone_number, c.name, c.created_at
           ORDER BY last_message DESC NULLS LAST""",
        fetch='all'
    )
    customers = [dict(r) for r in rows] if rows else []
    return render_template("conversations.html", customers=customers)


@portal_bp.route("/conversations/<phone>")
def conversation_detail(phone):
    customer = _db._execute(
        "SELECT * FROM customers WHERE phone_number = %s", (phone,), fetch='one'
    )
    if not customer:
        flash("Customer not found", "error")
        return redirect(url_for('portal.conversations'))

    messages = _db._execute(
        """SELECT cv.role, cv.message, cv.created_at, cv.order_id
           FROM conversations cv
           WHERE cv.customer_id = %s
           ORDER BY cv.created_at ASC""",
        (customer['id'],),
        fetch='all'
    )
    messages = [dict(m) for m in messages] if messages else []
    return render_template("conversation_detail.html",
                           customer=dict(customer), messages=messages)


# ── Orders ─────────────────────────────────────────────────────────────────────

@portal_bp.route("/orders")
def orders():
    status_filter = request.args.get("status", "")
    if status_filter:
        rows = _db._execute(
            """SELECT o.*, c.name, c.phone_number
               FROM orders o LEFT JOIN customers c ON o.customer_id = c.id
               WHERE o.status = %s ORDER BY o.created_at DESC""",
            (status_filter,), fetch='all'
        )
    else:
        rows = _db._execute(
            """SELECT o.*, c.name, c.phone_number
               FROM orders o LEFT JOIN customers c ON o.customer_id = c.id
               ORDER BY o.created_at DESC""",
            fetch='all'
        )
    orders_list = [dict(r) for r in rows] if rows else []
    return render_template("orders.html", orders=orders_list, status_filter=status_filter)


@portal_bp.route("/orders/<order_ref>")
def order_detail(order_ref):
    order = _db._execute(
        """SELECT o.*, c.name, c.phone_number
           FROM orders o LEFT JOIN customers c ON o.customer_id = c.id
           WHERE o.order_ref = %s""",
        (order_ref,), fetch='one'
    )
    if not order:
        flash("Order not found", "error")
        return redirect(url_for('portal.orders'))

    items = _db._execute(
        "SELECT * FROM order_items WHERE order_id = %s", (order['id'],), fetch='all'
    )
    items = [dict(i) for i in items] if items else []
    return render_template("order_detail.html",
                           order=dict(order), items=items)


@portal_bp.route("/orders/<order_ref>/status", methods=["POST"])
def update_order_status(order_ref):
    new_status = request.form.get("status")
    allowed = ["pending", "payment_sent", "paid", "preparing", "on_the_way", "delivered", "cancelled"]
    if new_status not in allowed:
        flash("Invalid status", "error")
        return redirect(url_for('portal.order_detail', order_ref=order_ref))

    _db._execute(
        "UPDATE orders SET status = %s, updated_at = NOW() WHERE order_ref = %s",
        (new_status, order_ref)
    )
    flash(f"Order status updated to {new_status}", "success")
    return redirect(url_for('portal.order_detail', order_ref=order_ref))


# ── Products ───────────────────────────────────────────────────────────────────

@portal_bp.route("/products")
def products():
    rows = _db._execute(
        "SELECT * FROM products ORDER BY category, name", fetch='all'
    )
    products_list = [dict(r) for r in rows] if rows else []

    # Group by category for the template
    from collections import defaultdict
    grouped = defaultdict(list)
    for p in products_list:
        grouped[p['category']].append(p)

    return render_template("products.html",
                           products=products_list,
                           grouped=grouped)


@portal_bp.route("/products/add", methods=["POST"])
def add_product():
    name        = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    price       = request.form.get("price", 0)
    category    = request.form.get("category", "").strip()

    if not name or not price or not category:
        flash("Name, price and category are required", "error")
        return redirect(url_for('portal.products'))

    try:
        _db._execute(
            "INSERT INTO products (name, description, price, category) VALUES (%s, %s, %s, %s)",
            (name, description, int(price), category)
        )
        flash(f'"{name}" added successfully', "success")
    except Exception as e:
        flash(f"Error adding product: {e}", "error")

    return redirect(url_for('portal.products'))


@portal_bp.route("/products/<int:product_id>/edit", methods=["POST"])
def edit_product(product_id):
    name        = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    price       = request.form.get("price", 0)
    category    = request.form.get("category", "").strip()
    is_available = request.form.get("is_available") == "true"

    try:
        _db._execute(
            """UPDATE products
               SET name=%s, description=%s, price=%s, category=%s,
                   is_available=%s, updated_at=NOW()
               WHERE id=%s""",
            (name, description, int(price), category, is_available, product_id)
        )
        flash(f'"{name}" updated successfully', "success")
    except Exception as e:
        flash(f"Error updating product: {e}", "error")

    return redirect(url_for('portal.products'))


@portal_bp.route("/products/<int:product_id>/delete", methods=["POST"])
def delete_product(product_id):
    try:
        _db._execute("DELETE FROM products WHERE id = %s", (product_id,))
        flash("Product deleted", "success")
    except Exception as e:
        flash(f"Error deleting product: {e}", "error")
    return redirect(url_for('portal.products'))


@portal_bp.route("/products/<int:product_id>/toggle", methods=["POST"])
def toggle_product(product_id):
    _db._execute(
        "UPDATE products SET is_available = NOT is_available, updated_at=NOW() WHERE id=%s",
        (product_id,)
    )
    return jsonify({"ok": True})