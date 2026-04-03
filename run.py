#!/usr/bin/env python3
"""
Lola — Makinde Kitchen WhatsApp Order Bot
Runner Script
"""

import os
import sys

# Ensure project root is always on the path — must be before any local imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
import warnings
from pathlib import Path

# Suppress deprecation warning from old google-generativeai package
warnings.filterwarnings("ignore", category=FutureWarning, module="langchain_google_genai")


def check_environment():
    """Check required environment variables and folder structure."""
    print("Checking environment setup...")

    if not os.path.exists('.env'):
        print("ERROR: .env file not found!")
        print("Create a .env file with your configuration.")
        return False

    from dotenv import load_dotenv
    load_dotenv()

    required_vars = [
        'WHATSAPP_ACCESS_TOKEN',
        'WHATSAPP_PHONE_NUMBER_ID',
        'VERIFY_TOKEN',
    ]

    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        print("ERROR: Missing required environment variables:")
        for var in missing:
            print(f"   - {var}")
        return False

    # Optional but important
    if os.getenv('GEMINI_API_KEY'):
        print("Gemini AI key found — AI ordering enabled.")
    else:
        print("WARNING: GEMINI_API_KEY not set — AI features disabled.")
        print("Get a free key at: https://aistudio.google.com/app/apikey")

    if os.getenv('PAYSTACK_SECRET_KEY'):
        print("Paystack key found — payments enabled.")
    else:
        print("WARNING: PAYSTACK_SECRET_KEY not set — payment links will not generate.")

    if os.getenv('DB_URL'):
        print("DB_URL found — database saving enabled.")
    else:
        print("WARNING: DB_URL not set — conversations will not be saved.")

    # Ensure __init__.py exists in all package folders
    for directory in ['handlers', 'services', 'utils', 'portal']:
        if not os.path.exists(directory):
            print(f"ERROR: Directory '{directory}' not found!")
            return False
        init_file = os.path.join(directory, '__init__.py')
        if not os.path.exists(init_file):
            print(f"Creating missing {init_file}...")
            Path(init_file).touch()

    print("Environment check passed.")
    return True


def setup_logging(debug=False):
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("lola_bot.log"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def print_startup_info(port):
    print("\n" + "=" * 60)
    print("  Lola — Makinde Kitchen WhatsApp Order Bot")
    print("=" * 60)
    print()
    print("Local development setup:")
    print(f"  1. Run ngrok:   ngrok http {port}")
    print("  2. Copy the ngrok HTTPS URL")
    print("  3. Meta Developer Console -> WhatsApp -> Configuration")
    print("  4. Webhook URL:    https://<ngrok-url>/webhook")
    print("  5. Verify Token:   match VERIFY_TOKEN in .env")
    print("  6. Click Verify and Save")
    print()
    print("URLs:")
    print(f"  Bot webhook:   http://localhost:{port}/webhook")
    print(f"  Health check:  http://localhost:{port}/health")
    print(f"  Portal:        http://localhost:{port}/portal")
    print(f"  Pay webhook:   http://localhost:{port}/paystack/webhook")
    print("=" * 60 + "\n")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Lola — Makinde Kitchen WhatsApp Bot')
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
            if not check_environment():
                logger.error("Environment check failed.")
                sys.exit(1)

        print_startup_info(args.port)

        if args.production:
            print("Production — run with gunicorn:")
            print(f"  gunicorn -w 4 -k gevent --timeout 120 --preload -b 0.0.0.0:{args.port} app:app")
            return

        logger.info("Starting Lola — Makinde Kitchen bot...")
        from app import app
        app.run(
            host=args.host,
            port=args.port,
            debug=args.debug,
            use_reloader=False
        )

    except KeyboardInterrupt:
        logger.info("Bot stopped.")
        print("\nLola stopped. Goodbye!")

    except ImportError as e:
        logger.error(f"Import error: {e}")
        print("Could not import required modules.")
        print("Run: pip install -r requirements.txt")
        sys.exit(1)

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        print(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()