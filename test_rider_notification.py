#!/usr/bin/env python3
"""
test_rider_notification.py
──────────────────────────
Simulates a completed order and fires the full notification flow:
  - Vendor Telegram notification
  - Rider group post with Accept / Unavailable buttons
  - Customer confirmation

Run from project root:
    python test_rider_notification.py

Optional flags:
    --vendor-id 2        use a specific vendor (default: first active vendor)
    --phone 1764691432   customer Telegram chat_id (default: from .env TEST_CHAT_ID)
    --amount 2500        order total in naira (default: 2500)
    --real               use a real order ref (creates DB record). Default: dry run with fake ref
"""

import os
import sys
import argparse
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Test Lola rider notification flow")
    parser.add_argument('--vendor-id', type=int, default=None, help='Vendor id to use')
    parser.add_argument('--phone',     type=str, default=None, help='Customer Telegram chat_id')
    parser.add_argument('--amount',    type=int, default=2500,  help='Order total in naira')
    parser.add_argument('--real',      action='store_true',     help='Create real DB records')
    args = parser.parse_args()

    # ── Setup ──────────────────────────────────────────────────────────────────
    from config import Config
    from db_manager import DBManager
    from services.telegram_service import TelegramService
    from services.notification_service import NotificationService

    config  = Config()
    db      = DBManager(config)
    telegram = TelegramService(config)

    # WhatsApp service optional — only needed if vendor has whatsapp_number
    whatsapp = None
    try:
        from services.whatsapp_service import WhatsAppService
        whatsapp = WhatsAppService(config)
    except Exception:
        print("WhatsApp service not available — skipping vendor WhatsApp notification.")

    notification = NotificationService(config, db, telegram, whatsapp)

    # ── Pick vendor ────────────────────────────────────────────────────────────
    vendors = db.get_all_vendors()
    if not vendors:
        print("ERROR: No active vendors in DB. Run seed_vendors.sql first.")
        sys.exit(1)

    vendor = next((v for v in vendors if v['id'] == args.vendor_id), None) if args.vendor_id else vendors[0]
    if not vendor:
        print(f"ERROR: Vendor id {args.vendor_id} not found.")
        sys.exit(1)

    print(f"\nVendor:  {vendor['name']} (id={vendor['id']})")
    print(f"Group:   {vendor.get('rider_group_chat_id') or 'NOT SET — update vendors table'}")

    # ── Customer phone ─────────────────────────────────────────────────────────
    customer_phone = args.phone or os.getenv('TEST_CHAT_ID') or os.getenv('TELEGRAM_TEST_CHAT_ID')
    if not customer_phone:
        print("\nERROR: No customer phone provided.")
        print("  Use --phone <your_telegram_chat_id>")
        print("  Or set TEST_CHAT_ID=<your_chat_id> in .env")
        sys.exit(1)

    print(f"Customer: {customer_phone} (Telegram)")
    print(f"Amount:   ₦{args.amount:,}")

    # ── Build test order data ──────────────────────────────────────────────────
    prefix    = vendor.get('order_ref_prefix', 'TST')
    order_ref = f"{prefix}{random.randint(10000, 99999)}"

    sample_items = [
        {'name': 'Jollof Rice',    'quantity': 1, 'subtotal': 2500},
        {'name': 'Zobo (500ml)',   'quantity': 1, 'subtotal': 800},
    ]

    print(f"\nOrder ref: {order_ref}")
    print("Items:")
    for item in sample_items:
        print(f"  {item['name']} x{item['quantity']} — ₦{item['subtotal']:,}")

    # ── Create DB records if --real ────────────────────────────────────────────
    order_id = None
    if args.real:
        print("\nCreating real DB records...")
        order_id = db.create_order(
            order_ref=order_ref,
            phone_number=customer_phone,
            delivery_address="Test Address, Lagos",
            subtotal=args.amount - vendor.get('delivery_fee', 500),
            delivery_fee=vendor.get('delivery_fee', 500),
            total=args.amount,
            customer_name="Test Customer",
            vendor_id=vendor['id'],
            platform='telegram',
        )
        if order_id:
            db.save_order_items(order_id, [
                {'name': i['name'], 'price': i['subtotal'], 'quantity': i['quantity'], 'subtotal': i['subtotal']}
                for i in sample_items
            ])
            db.update_order_payment(order_ref=order_ref, payment_status='paid', status='preparing')
            print(f"Order created: id={order_id}, ref={order_ref}")
        else:
            print("WARNING: Could not create order in DB — continuing with mock data.")
    else:
        print("\n[DRY RUN] No DB records created. Use --real to create actual records.")

    # ── Fire notification ──────────────────────────────────────────────────────
    print("\nFiring notifications...")

    if args.real and order_id:
        # Use the real flow
        notification.handle_order_confirmed(
            order_ref=order_ref,
            amount_naira=args.amount,
            customer_phone=customer_phone,
            customer_platform='telegram',
            vendor_id=vendor['id'],
        )
    else:
        # Mock the order dict and fire each notification directly
        mock_order = {
            'id':               0,
            'order_ref':        order_ref,
            'customer_id':      0,
            'vendor_id':        vendor['id'],
            'delivery_address': 'Test Address, Lagos',
            'total':            args.amount,
        }

        pin           = str(random.randint(1000, 9999))
        group_chat_id = vendor.get('rider_group_chat_id')

        print(f"\n  PIN (for test): {pin}")

        # Vendor Telegram
        if vendor.get('telegram_chat_id'):
            print(f"  Sending vendor Telegram to {vendor['telegram_chat_id']}...")
            notification._notify_vendor_telegram(vendor, mock_order, sample_items, pin)
        else:
            print("  Vendor telegram_chat_id not set — skipping vendor notification.")

        # Rider group
        if group_chat_id:
            print(f"  Posting to rider group {group_chat_id}...")

            # Need a fake delivery_id — use 0 for dry run
            class FakeDelivery:
                pass

            # Directly call _post_to_rider_group
            notification._post_to_rider_group(group_chat_id, mock_order, sample_items, vendor, 0)
        else:
            print("  rider_group_chat_id not set in vendors table — skipping group post.")
            print("  Run this SQL to fix:")
            print(f"    UPDATE vendors SET rider_group_chat_id = '-5232735234' WHERE id = {vendor['id']};")

        # Customer
        print(f"  Sending customer confirmation to {customer_phone}...")
        notification._notify_customer_confirmed(
            customer_phone=customer_phone,
            platform='telegram',
            order_ref=order_ref,
            amount_naira=args.amount,
            vendor=vendor,
        )

    print("\nDone. Check your Telegram.")


if __name__ == "__main__":
    main()