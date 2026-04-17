#!/usr/bin/env python3
"""
Lola — Multi-Vendor Order Bot
Runner Script
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning, module="langchain_google_genai")


def check_environment():
    if not os.path.exists('.env'):
        print("ERROR: .env file not found.")
        return False

    from dotenv import load_dotenv
    load_dotenv()

    # Required
    required = {
        'WHATSAPP_ACCESS_TOKEN':    'WhatsApp messaging',
        'WHATSAPP_PHONE_NUMBER_ID': 'WhatsApp messaging',
        'VERIFY_TOKEN':             'WhatsApp webhook verification',
    }
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print("ERROR: Missing required environment variables:")
        for var in missing:
            print(f"   {var}  ({required[var]})")
        return False

    # Optional — warn but don't block
    optional = {
        'GEMINI_API_KEY':        'AI ordering (required for bot to work)',
        'PAYSTACK_SECRET_KEY':   'Payment links',
        'DB_URL':                'Conversation + order history',
        'TELEGRAM_BOT_TOKEN':    'Telegram bot',
        'RIDER_GROUP_CHAT_ID':   'Rider delivery notifications',
        'CALLBACK_BASE_URL':     'Paystack webhooks + Telegram registration',
    }
    for var, desc in optional.items():
        val = os.getenv(var)
        status = "✓" if val else "✗ not set"
        print(f"   {status}  {var:30s} {desc}")

    # Validate package folders exist
    for directory in ['handlers', 'services', 'utils', 'portal']:
        if not os.path.exists(directory):
            print(f"ERROR: Directory '{directory}' not found.")
            return False
        init_file = os.path.join(directory, '__init__.py')
        if not os.path.exists(init_file):
            Path(init_file).touch()

    return True


def setup_logging(debug=False):
    import io
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("lola.log", encoding='utf-8'),
            logging.StreamHandler(io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')),
        ]
    )
    for noisy in ("requests", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def print_startup_info(port):
    from dotenv import load_dotenv
    load_dotenv()
    base = os.getenv('CALLBACK_BASE_URL', f'http://localhost:{port}')

    print()
    print("=" * 55)
    print("  Lola — Multi-Vendor Order Bot")
    print("=" * 55)
    print()
    print("Local URLs:")
    print(f"  Health:           http://localhost:{port}/health")
    print(f"  Portal:           http://localhost:{port}/portal")
    print(f"  WhatsApp webhook: http://localhost:{port}/webhook")
    print(f"  Telegram webhook: http://localhost:{port}/telegram/webhook")
    print(f"  Paystack webhook: http://localhost:{port}/paystack/webhook")
    print()
    print("Production (ngrok / Render):")
    print(f"  WhatsApp webhook: {base}/webhook")
    print(f"  Telegram webhook: {base}/telegram/webhook")
    print(f"  Paystack webhook: {base}/paystack/webhook")
    print()
    print("WhatsApp setup:")
    print("  1. Run: ngrok http {port}")
    print("  2. Meta Developer Console → WhatsApp → Configuration")
    print(f"  3. Webhook URL: {base}/webhook")
    print(f"  4. Verify Token: match VERIFY_TOKEN in .env")
    print("=" * 55)
    print()


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Lola — Multi-Vendor Order Bot')
    parser.add_argument('--debug',      action='store_true', help='Enable debug logging')
    parser.add_argument('--port',       type=int, default=8000, help='Port (default: 8000)')
    parser.add_argument('--host',       type=str, default='127.0.0.1', help='Host to bind')
    parser.add_argument('--no-check',   action='store_true', help='Skip environment checks')
    parser.add_argument('--production', action='store_true', help='Print gunicorn command and exit')
    args = parser.parse_args()

    setup_logging(debug=args.debug)
    logger = logging.getLogger(__name__)

    try:
        if not args.no_check:
            print("\nChecking environment...")
            if not check_environment():
                logger.error("Environment check failed.")
                sys.exit(1)
            print("Environment check passed.\n")

        print_startup_info(args.port)

        if args.production:
            print("Production command:")
            print(f"  gunicorn -w 4 -k gevent --timeout 120 --preload -b 0.0.0.0:{args.port} app:app")
            return

        logger.info("Starting Lola...")
        from app import app
        app.run(
            host=args.host,
            port=args.port,
            debug=args.debug,
            use_reloader=False,
        )

    except KeyboardInterrupt:
        print("\nLola stopped.")

    except ImportError as e:
        logger.error(f"Import error: {e}")
        print(f"Import error: {e}")
        print("Run: pip install -r requirements.txt")
        sys.exit(1)

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        print(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()