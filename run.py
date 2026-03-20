#!/usr/bin/env python3
"""
Chowder.ng WhatsApp Order Bot — Runner Script
"""

import os
import sys
import logging
from pathlib import Path


def check_environment():
    """Check if all required environment variables and files are present."""
    print("🔍 Checking environment setup...")

    if not os.path.exists('.env'):
        print("❌ ERROR: .env file not found!")
        print("   Create a .env file with your configuration.")
        return False

    from dotenv import load_dotenv
    load_dotenv()

    required_vars = [
        'WHATSAPP_ACCESS_TOKEN',
        'WHATSAPP_PHONE_NUMBER_ID',
        'VERIFY_TOKEN',
    ]

    missing_vars = [v for v in required_vars if not os.getenv(v)]
    if missing_vars:
        print("❌ ERROR: Missing required environment variables:")
        for var in missing_vars:
            print(f"   - {var}")
        return False

    # Check Gemini key
    gemini_key = os.getenv('GEMINI_API_KEY')
    if gemini_key:
        print("✅ Gemini AI key found — AI ordering will be enabled!")
    else:
        print("⚠️  WARNING: GEMINI_API_KEY not set — AI features will be disabled.")
        print("   Get a free key at: https://aistudio.google.com/app/apikey")

    # Check directory structure
    for directory in ['handlers', 'services', 'utils']:
        if not os.path.exists(directory):
            print(f"❌ ERROR: Directory '{directory}' not found!")
            return False
        init_file = os.path.join(directory, '__init__.py')
        if not os.path.exists(init_file):
            print(f"⚠️  Creating missing {init_file}...")
            Path(init_file).touch()

    print("✅ Environment check passed!")
    return True


def setup_logging(debug=False):
    """Setup logging configuration."""
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("chowder_bot.log"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def print_startup_info(port):
    """Print startup information."""
    print("\n" + "=" * 65)
    print("🍟  Chowder.ng WhatsApp Order Bot")
    print("=" * 65)
    print("🤖 What this bot does:")
    print("   • Welcomes customers and shows the Signature Loaded Fries menu")
    print("   • Takes orders conversationally (by name or number)")
    print("   • Calculates totals and collects delivery location")
    print("   • Confirms orders with a CHW reference number")
    print()
    print("📋 Local development steps:")
    print("   1. Make sure ngrok is running:")
    print(f"      ngrok http {port}")
    print("   2. Copy the ngrok HTTPS URL")
    print("   3. Go to Meta Developer Console → WhatsApp → Configuration")
    print("   4. Set Webhook URL to:  https://<ngrok-url>/webhook")
    print("   5. Set Verify Token to: 123456  (must match VERIFY_TOKEN in .env)")
    print("   6. Click 'Verify and Save'")
    print()
    print("🔗 Local URLs:")
    print(f"   Webhook:      http://localhost:{port}/webhook")
    print(f"   Health check: http://localhost:{port}/health")
    print("=" * 65 + "\n")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Chowder.ng WhatsApp Order Bot')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    parser.add_argument('--port', type=int, default=8000, help='Port to run on (default: 8000)')
    parser.add_argument('--host', type=str, default='127.0.0.1', help='Host to bind to')
    parser.add_argument('--no-check', action='store_true', help='Skip environment checks')
    parser.add_argument('--production', action='store_true', help='Print production gunicorn command and exit')
    args = parser.parse_args()

    setup_logging(debug=args.debug)
    logger = logging.getLogger(__name__)

    try:
        if not args.no_check:
            if not check_environment():
                logger.error("Environment check failed. Fix the issues above and retry.")
                sys.exit(1)

        print_startup_info(args.port)

        if args.production:
            print("🚀 PRODUCTION — run with gunicorn:")
            print(f"   gunicorn -w 4 -k gevent --timeout 120 --preload -b 0.0.0.0:{args.port} app:app")
            return

        logger.info("Starting Chowder.ng order bot...")
        from app import app
        app.run(
            host=args.host,
            port=args.port,
            debug=args.debug,
            use_reloader=False
        )

    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
        print("\n👋 Chowder.ng bot stopped. Goodbye!")

    except ImportError as e:
        logger.error(f"Import error: {e}")
        print("❌ Could not import required modules.")
        print("   Run: pip install -r requirements.txt")
        sys.exit(1)

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()