"""
portal/routes.py
Flask blueprint for the Lola merchant portal.

Register in app.py:
    from portal.routes import portal_bp, init_portal
    init_portal(config, whatsapp_service=..., telegram_service=..., notification_service=...)
    app.register_blueprint(portal_bp)
"""
import logging
import requests as _requests
from functools import wraps
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, session
)
from db_manager import DBManager

logger = logging.getLogger(__name__)

portal_bp = Blueprint("portal", __name__, url_prefix="/portal")

_config               = None
_db                   = None
_whatsapp_service     = None
_telegram_service     = None
_notification_service = None


def init_portal(
    config,
    whatsapp_service=None,
    telegram_service=None,
    notification_service=None,
):
    global _config, _db, _whatsapp_service, _telegram_service, _notification_service
    _config               = config
    _db                   = DBManager(config)
    _whatsapp_service     = whatsapp_service
    _telegram_service     = telegram_service
    _notification_service = notification_service


# ── Vendor session helpers ──────────────────────────────────────────────────────

def get_current_vendor():
    """Return the vendor dict stored in session, or None."""
    return session.get("vendor")


def require_vendor(f):
    """Decorator: redirect to vendor select if no vendor in session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_current_vendor():
            flash("Please select a vendor to continue.", "error")
            return redirect(url_for("portal.vendor_select"))
        return f(*args, **kwargs)
    return decorated


def _get_all_vendors():
    """Fetch all active vendors from DB."""
    try:
        rows = _db._execute(
            "SELECT id, name, description, type, zone, order_ref_prefix FROM vendors WHERE is_active = TRUE ORDER BY name",
            fetch='all'
        )
        return [dict(r) for r in rows] if rows else []
    except Exception as e:
        logger.error(f"_get_all_vendors error: {e}")
        return []


# ── Vendor Select / Login ───────────────────────────────────────────────────────

@portal_bp.route("/", methods=["GET"])
def index():
    """Root: redirect to dashboard if vendor in session, else vendor select."""
    if get_current_vendor():
        return redirect(url_for("portal.dashboard"))
    return redirect(url_for("portal.vendor_select"))


@portal_bp.route("/select", methods=["GET", "POST"])
def vendor_select():
    """Simple vendor selection 'login' — no password, just pick a vendor."""
    if request.method == "POST":
        vendor_id = request.form.get("vendor_id")
        if not vendor_id:
            flash("Please select a vendor.", "error")
            return redirect(url_for("portal.vendor_select"))

        try:
            row = _db._execute(
                "SELECT id, name, description, type, zone, order_ref_prefix, logo_url FROM vendors WHERE id = %s AND is_active = TRUE",
                (int(vendor_id),),
                fetch='one'
            )
            if not row:
                flash("Vendor not found or inactive.", "error")
                return redirect(url_for("portal.vendor_select"))

            session["vendor"] = dict(row)
            session.permanent = True
            flash(f"Welcome to {row['name']}!", "success")
            return redirect(url_for("portal.dashboard"))

        except Exception as e:
            logger.error(f"vendor_select POST error: {e}")
            flash("Something went wrong. Please try again.", "error")
            return redirect(url_for("portal.vendor_select"))

    vendors = _get_all_vendors()
    return render_template("vendor_select.html", vendors=vendors)


@portal_bp.route("/logout")
def logout():
    """Clear vendor session and return to select screen."""
    vendor_name = (get_current_vendor() or {}).get("name", "")
    session.pop("vendor", None)
    flash(f"Switched out of {vendor_name}." if vendor_name else "Logged out.", "success")
    return redirect(url_for("portal.vendor_select"))


# ── Dashboard ──────────────────────────────────────────────────────────────────

@portal_bp.route("/dashboard")
@require_vendor
def dashboard():
    vendor = get_current_vendor()
    vendor_id = vendor["id"]

    stats = {
        "total_customers": 0,
        "total_orders":    0,
        "paid_orders":     0,
        "total_messages":  0,
        "total_revenue":   0,
        "recent_orders":   [],
        "error":           None,
        "vendor":          vendor,
    }
    try:
        # Customers who placed orders with this vendor
        row = _db._execute(
            "SELECT COUNT(DISTINCT customer_id) as c FROM orders WHERE vendor_id = %s",
            (vendor_id,), fetch='one'
        )
        stats["total_customers"] = row['c'] if row else 0

        row = _db._execute(
            "SELECT COUNT(*) as c FROM orders WHERE vendor_id = %s",
            (vendor_id,), fetch='one'
        )
        stats["total_orders"] = row['c'] if row else 0

        row = _db._execute(
            "SELECT COUNT(*) as c FROM orders WHERE vendor_id = %s AND payment_status = 'paid'",
            (vendor_id,), fetch='one'
        )
        stats["paid_orders"] = row['c'] if row else 0

        row = _db._execute(
            """SELECT COUNT(cv.id) as c FROM conversations cv
               JOIN customers cu ON cv.customer_id = cu.id
               JOIN orders o ON o.customer_id = cu.id
               WHERE o.vendor_id = %s""",
            (vendor_id,), fetch='one'
        )
        stats["total_messages"] = row['c'] if row else 0

        row = _db._execute(
            "SELECT COALESCE(SUM(total),0) as c FROM orders WHERE vendor_id = %s AND payment_status='paid'",
            (vendor_id,), fetch='one'
        )
        stats["total_revenue"] = row['c'] if row else 0

        recent = _db._execute(
            """SELECT o.order_ref, o.total, o.status, o.payment_status, o.created_at,
                      c.name, c.phone_number
               FROM orders o
               LEFT JOIN customers c ON o.customer_id = c.id
               WHERE o.vendor_id = %s
               ORDER BY o.created_at DESC LIMIT 5""",
            (vendor_id,),
            fetch='all'
        )
        stats["recent_orders"] = [dict(r) for r in recent] if recent else []

    except Exception as e:
        logger.error(f"Dashboard DB error: {e}")
        stats["error"] = str(e)

    return render_template("dashboard.html", stats=stats, vendor=vendor)


# ── Conversations ──────────────────────────────────────────────────────────────

@portal_bp.route("/conversations")
@require_vendor
def conversations():
    vendor = get_current_vendor()
    vendor_id = vendor["id"]

    rows = _db._execute(
        """SELECT c.id, c.phone_number, c.name, c.created_at,
                  COUNT(cv.id) as message_count,
                  MAX(cv.created_at) as last_message
           FROM customers c
           LEFT JOIN conversations cv ON c.id = cv.customer_id
           WHERE c.id IN (SELECT DISTINCT customer_id FROM orders WHERE vendor_id = %s)
           GROUP BY c.id, c.phone_number, c.name, c.created_at
           ORDER BY last_message DESC NULLS LAST""",
        (vendor_id,),
        fetch='all'
    )
    customers = [dict(r) for r in rows] if rows else []
    return render_template("conversations.html", customers=customers, vendor=vendor)


@portal_bp.route("/conversations/<phone>")
@require_vendor
def conversation_detail(phone):
    vendor = get_current_vendor()

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
                           customer=dict(customer), messages=messages, vendor=vendor)


# ── Orders ─────────────────────────────────────────────────────────────────────

@portal_bp.route("/orders")
@require_vendor
def orders():
    vendor = get_current_vendor()
    vendor_id = vendor["id"]
    status_filter = request.args.get("status", "")

    if status_filter:
        rows = _db._execute(
            """SELECT o.*, c.name, c.phone_number
               FROM orders o LEFT JOIN customers c ON o.customer_id = c.id
               WHERE o.vendor_id = %s AND o.status = %s
               ORDER BY o.created_at DESC""",
            (vendor_id, status_filter), fetch='all'
        )
    else:
        rows = _db._execute(
            """SELECT o.*, c.name, c.phone_number
               FROM orders o LEFT JOIN customers c ON o.customer_id = c.id
               WHERE o.vendor_id = %s
               ORDER BY o.created_at DESC""",
            (vendor_id,), fetch='all'
        )
    orders_list = [dict(r) for r in rows] if rows else []
    return render_template("orders.html", orders=orders_list,
                           status_filter=status_filter, vendor=vendor)


@portal_bp.route("/orders/<order_ref>")
@require_vendor
def order_detail(order_ref):
    vendor = get_current_vendor()
    vendor_id = vendor["id"]

    order = _db._execute(
        """SELECT o.*, c.name, c.phone_number
           FROM orders o LEFT JOIN customers c ON o.customer_id = c.id
           WHERE o.order_ref = %s AND o.vendor_id = %s""",
        (order_ref, vendor_id), fetch='one'
    )
    if not order:
        flash("Order not found", "error")
        return redirect(url_for('portal.orders'))

    items = _db._execute(
        "SELECT * FROM order_items WHERE order_id = %s", (order['id'],), fetch='all'
    )
    items = [dict(i) for i in items] if items else []
    return render_template("order_detail.html", order=dict(order),
                           items=items, vendor=vendor)


@portal_bp.route("/orders/<order_ref>/status", methods=["POST"])
@require_vendor
def update_order_status(order_ref):
    vendor = get_current_vendor()
    vendor_id = vendor["id"]

    new_status = request.form.get("status")
    allowed = ["pending", "payment_sent", "paid", "preparing", "on_the_way", "delivered", "cancelled"]
    if new_status not in allowed:
        flash("Invalid status", "error")
        return redirect(url_for('portal.order_detail', order_ref=order_ref))

    _db._execute(
        "UPDATE orders SET status = %s, updated_at = NOW() WHERE order_ref = %s AND vendor_id = %s",
        (new_status, order_ref, vendor_id)
    )
    flash(f"Order status updated to {new_status}", "success")
    return redirect(url_for('portal.order_detail', order_ref=order_ref))


# ── Products ───────────────────────────────────────────────────────────────────

@portal_bp.route("/products")
@require_vendor
def products():
    vendor = get_current_vendor()
    vendor_id = vendor["id"]

    rows = _db._execute(
        "SELECT * FROM products WHERE vendor_id = %s ORDER BY category, name",
        (vendor_id,), fetch='all'
    )
    products_list = [dict(r) for r in rows] if rows else []

    from collections import defaultdict
    grouped = defaultdict(list)
    for p in products_list:
        grouped[p['category']].append(p)

    return render_template("products.html", products=products_list,
                           grouped=grouped, vendor=vendor)


@portal_bp.route("/products/add", methods=["POST"])
@require_vendor
def add_product():
    vendor = get_current_vendor()
    vendor_id = vendor["id"]

    name        = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    price       = request.form.get("price", 0)
    category    = request.form.get("category", "").strip()

    if not name or not price or not category:
        flash("Name, price and category are required", "error")
        return redirect(url_for('portal.products'))

    try:
        _db._execute(
            "INSERT INTO products (vendor_id, name, description, price, category) VALUES (%s, %s, %s, %s, %s)",
            (vendor_id, name, description, int(price), category)
        )
        flash(f'"{name}" added successfully', "success")
    except Exception as e:
        flash(f"Error adding product: {e}", "error")

    return redirect(url_for('portal.products'))


@portal_bp.route("/products/<int:product_id>/edit", methods=["POST"])
@require_vendor
def edit_product(product_id):
    vendor = get_current_vendor()
    vendor_id = vendor["id"]

    name         = request.form.get("name", "").strip()
    description  = request.form.get("description", "").strip()
    price        = request.form.get("price", 0)
    category     = request.form.get("category", "").strip()
    is_available = request.form.get("is_available") == "true"

    try:
        _db._execute(
            """UPDATE products
               SET name=%s, description=%s, price=%s, category=%s,
                   is_available=%s, updated_at=NOW()
               WHERE id=%s AND vendor_id=%s""",
            (name, description, int(price), category, is_available, product_id, vendor_id)
        )
        flash(f'"{name}" updated successfully', "success")
    except Exception as e:
        flash(f"Error updating product: {e}", "error")

    return redirect(url_for('portal.products'))


@portal_bp.route("/products/<int:product_id>/delete", methods=["POST"])
@require_vendor
def delete_product(product_id):
    vendor = get_current_vendor()
    vendor_id = vendor["id"]

    try:
        _db._execute(
            "DELETE FROM products WHERE id = %s AND vendor_id = %s",
            (product_id, vendor_id)
        )
        flash("Product deleted", "success")
    except Exception as e:
        flash(f"Error deleting product: {e}", "error")
    return redirect(url_for('portal.products'))


@portal_bp.route("/products/<int:product_id>/toggle", methods=["POST"])
@require_vendor
def toggle_product(product_id):
    vendor = get_current_vendor()
    vendor_id = vendor["id"]

    _db._execute(
        "UPDATE products SET is_available = NOT is_available, updated_at=NOW() WHERE id=%s AND vendor_id=%s",
        (product_id, vendor_id)
    )
    return jsonify({"ok": True})


# ── Payment success callback ───────────────────────────────────────────────────

@portal_bp.route("/payment/success")
def payment_success():
    reference = request.args.get("reference", "").strip()

    ctx = {
        "success":        False,
        "reference":      reference,
        "amount_naira":   0,
        "customer_name":  None,
        "phone_display":  None,
        "channel":        "telegram",
        "vendor_name":    "Lola",
        "vendor_support": None,
        "error_message":  None,
    }

    if not reference:
        ctx["error_message"] = "No payment reference provided."
        return render_template("payment_success.html", **ctx)

    try:
        paystack_secret = getattr(_config, "PAYSTACK_SECRET_KEY", "") or ""
        resp = _requests.get(
            f"https://api.paystack.co/transaction/verify/{reference}",
            headers={"Authorization": f"Bearer {paystack_secret}"},
            timeout=10,
        )
        resp.raise_for_status()
        pdata = resp.json().get("data", {})
    except Exception as e:
        logger.error(f"payment_success: Paystack verify failed: {e}")
        ctx["error_message"] = (
            "Could not reach payment provider. "
            "If you completed payment, please message us with your reference."
        )
        return render_template("payment_success.html", **ctx)

    paystack_status = pdata.get("status", "")
    if paystack_status != "success":
        ctx["error_message"] = (
            f"Payment status is '{paystack_status}'. "
            "If you completed the payment, please send us your reference."
        )
        return render_template("payment_success.html", **ctx)

    amount_kobo       = pdata.get("amount", 0)
    amount_naira      = amount_kobo // 100
    metadata          = pdata.get("metadata") or {}
    customer_phone    = metadata.get("customer_phone", "")
    vendor_id         = metadata.get("vendor_id")
    customer_platform = metadata.get("channel", "telegram")

    ctx["amount_naira"] = amount_naira
    ctx["success"]      = True
    ctx["channel"]      = customer_platform

    if _db:
        try:
            _db.update_order_payment(
                order_ref=reference,
                payment_status="paid",
                payment_ref=reference,
                status="preparing",
            )
            order = _db.get_order_by_ref(reference)
            if order:
                _db.log_payment(
                    order_id=order['id'],
                    order_ref=reference,
                    amount=amount_kobo,
                    payment_ref=reference,
                    gateway='paystack',
                    status='success',
                    webhook_payload=pdata,
                )
                if not vendor_id and order.get('vendor_id'):
                    vendor_id = order['vendor_id']
                if not customer_platform and order.get('platform'):
                    customer_platform = order['platform']
            logger.info(f"payment_success: DB updated for ref={reference}")
        except Exception as e:
            logger.error(f"payment_success: DB update failed: {e}")

    customer_name = None
    if not customer_phone and _db:
        try:
            order = _db.get_order_by_ref(reference)
            if order:
                row = _db._execute(
                    "SELECT phone_number, name, platform FROM customers WHERE id = %s",
                    (order["customer_id"],),
                    fetch="one",
                )
                if row:
                    customer_phone    = row["phone_number"]
                    customer_name     = row["name"]
                    customer_platform = row.get("platform", customer_platform)
        except Exception as e:
            logger.error(f"payment_success: customer lookup failed: {e}")

    if not customer_name and customer_phone and _db:
        try:
            row = _db._execute(
                "SELECT name FROM customers WHERE phone_number = %s",
                (customer_phone,), fetch="one",
            )
            if row:
                customer_name = row["name"]
        except Exception:
            pass

    ctx["customer_name"] = customer_name
    if customer_phone:
        p = str(customer_phone)
        ctx["phone_display"] = p[:4] + " *** *** " + p[-4:] if len(p) >= 8 else p

    if _db and vendor_id:
        try:
            vendor_row = _db._execute(
                "SELECT name, support_contact FROM vendors WHERE id = %s",
                (int(vendor_id),), fetch="one",
            )
            if vendor_row:
                ctx["vendor_name"]    = vendor_row["name"]
                ctx["vendor_support"] = vendor_row["support_contact"]
        except Exception as e:
            logger.error(f"payment_success: vendor lookup failed: {e}")

    if customer_phone:
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
                logger.error(f"payment_success: NotificationService failed: {e}", exc_info=True)
                _send_fallback_confirmation(
                    customer_phone, customer_platform, reference,
                    amount_naira, customer_name, vendor_id
                )
        else:
            _send_fallback_confirmation(
                customer_phone, customer_platform, reference,
                amount_naira, customer_name, vendor_id
            )

    return render_template("payment_success.html", **ctx)


@portal_bp.route("/payment/test")
def payment_test():
    return render_template("payment_success.html",
        success=False,
        reference="TEST123",
        amount_naira=0,
        customer_name=None,
        phone_display=None,
        channel="telegram",
        vendor_name="Lola",
        vendor_support=None,
        error_message="This is a test render",
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _send_fallback_confirmation(
    customer_phone: str,
    platform: str,
    reference: str,
    amount_naira: int,
    customer_name: str = None,
    vendor_id=None,
):
    vendor_name = "our kitchen"
    if _db and vendor_id:
        try:
            vendor = _db.get_vendor_by_id(int(vendor_id))
            if vendor:
                vendor_name = vendor["name"]
        except Exception:
            pass

    name_greeting = f"Hi {customer_name}! " if customer_name else ""
    msg = (
        f"{name_greeting}Payment confirmed! Thank you 🎉\n\n"
        f"Order Ref: {reference}\n"
        f"Amount Paid: ₦{amount_naira:,}\n\n"
        f"Your order is now being prepared by {vendor_name}.\n"
        f"We will notify you once a rider is on the way."
    )

    try:
        if platform == "telegram" and _telegram_service:
            _telegram_service.create_text_message(str(customer_phone), msg)
        elif _whatsapp_service:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type":    "individual",
                "to":                str(customer_phone),
                "type":              "text",
                "text":              {"body": msg},
            }
            _whatsapp_service.send_message(payload)
    except Exception as e:
        logger.error(f"_send_fallback_confirmation failed: {e}")