import os
import logging
from flask import Flask, request, jsonify, redirect, url_for
from dotenv import load_dotenv

from config import Config, configure_logging
from handlers.webhook_handler import WebhookHandler
from handlers.greeting_handler import GreetingHandler
from handlers.ai_handler import AIHandler
from utils.session_manager import SessionManager
from services.whatsapp_service import WhatsAppService
from services.telegram_service import TelegramService
from message_processor import MessageProcessor
from payment_webhook import payment_webhook_bp, init_payment_webhook
from db_manager import DBManager
from portal.routes import portal_bp, init_portal
from telegram_webhook import telegram_bp, init_telegram_webhook

load_dotenv()
configure_logging()

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "lola-demo-secret-change-in-production")
config = Config()

# ── Core services ─────────────────────────────────────────────────────────────
try:
    session_manager  = SessionManager(config.SESSION_TIMEOUT)
    whatsapp_service = WhatsAppService(config)
    db_manager       = DBManager(config)
    logger.info("Core services initialised — Makinde Kitchen (Lola Demo Bot)")
except Exception as e:
    logger.error(f"Failed to initialise core services: {e}", exc_info=True)
    exit(1)

# ── Handlers ──────────────────────────────────────────────────────────────────
try:
    greeting_handler  = GreetingHandler(config, session_manager, None, whatsapp_service)
    ai_handler        = AIHandler(config, session_manager, None, whatsapp_service)
    message_processor = MessageProcessor(config, session_manager, None, whatsapp_service)
    logger.info("Handlers and message processor initialised.")
except Exception as e:
    logger.error(f"Failed to initialise handlers: {e}", exc_info=True)
    exit(1)

# ── Webhook handler ───────────────────────────────────────────────────────────
try:
    webhook_handler = WebhookHandler(config, message_processor)
    logger.info("WebhookHandler initialised.")
except Exception as e:
    logger.error(f"Failed to initialise WebhookHandler: {e}", exc_info=True)
    exit(1)

# ── Paystack payment webhook ──────────────────────────────────────────────────
init_payment_webhook(config, session_manager, whatsapp_service, db_manager)
app.register_blueprint(payment_webhook_bp)
logger.info("Paystack payment webhook registered at /paystack/webhook")

# ── Telegram bot (initialised before portal so we can pass telegram_service) ──
telegram_service = None
try:
    telegram_service              = TelegramService(config)
    telegram_message_processor    = MessageProcessor(config, session_manager, None, telegram_service)
    init_telegram_webhook(config, session_manager, telegram_service, telegram_message_processor)
    app.register_blueprint(telegram_bp)
    telegram_service.register_webhook(f"{config.CALLBACK_BASE_URL}/telegram/webhook")
    logger.info("Telegram bot registered at /telegram/webhook")
except Exception as e:
    logger.error(f"Failed to initialise Telegram bot: {e}", exc_info=True)

# ── Merchant portal ───────────────────────────────────────────────────────────
# Payment success callback URL is built dynamically from CALLBACK_BASE_URL so it
# works on any environment (local dev, staging, production) without code changes.
# Set CALLBACK_BASE_URL=https://afyabot-7w4j.onrender.com in your .env / Render env vars.
init_portal(
    config,
    whatsapp_service=whatsapp_service,
    telegram_service=telegram_service,   # None-safe — portal handles missing service gracefully
)
app.register_blueprint(portal_bp)
logger.info("Merchant portal registered at /portal")
logger.info(f"Payment callback URL: {config.CALLBACK_BASE_URL}/portal/payment/success")

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect(url_for("portal.dashboard"))

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    return webhook_handler.verify_webhook(request)

@app.route("/webhook", methods=["POST"])
def webhook():
    return webhook_handler.handle_webhook(request)

@app.route("/health", methods=["GET"])
def health_check():
    try:
        return jsonify({
            "status":         "healthy",
            "service":        "Lola — Makinde Kitchen Demo Bot",
            "ai_service":     "enabled" if ai_handler.ai_enabled else "disabled",
            "active_sessions": len(session_manager._sessions) if hasattr(session_manager, '_sessions') else 0,
            "telegram":       "enabled" if config.TELEGRAM_BOT_TOKEN else "disabled",
            "payment_callback": f"{config.CALLBACK_BASE_URL}/portal/payment/success",
        }), 200
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

@app.route("/api/cleanup", methods=["POST"])
def manual_cleanup():
    try:
        message_processor.cleanup_expired_resources()
        return jsonify({"status": "success", "message": "Cleanup completed"}), 200
    except Exception as e:
        logger.error(f"Cleanup error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(error):
    return jsonify({"status": "error", "message": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}", exc_info=True)
    return jsonify({"status": "error", "message": "Internal server error"}), 500

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting Lola — Makinde Kitchen Demo Bot")
    logger.info(f"Webhook:          {config.CALLBACK_BASE_URL}/webhook")
    logger.info(f"Payment webhook:  {config.CALLBACK_BASE_URL}/paystack/webhook")
    logger.info(f"Payment callback: {config.CALLBACK_BASE_URL}/portal/payment/success")
    logger.info(f"Telegram webhook: {config.CALLBACK_BASE_URL}/telegram/webhook")
    logger.info(f"Portal:           {config.CALLBACK_BASE_URL}/portal")
    logger.info(f"Health:           {config.CALLBACK_BASE_URL}/health")
    logger.info(f"AI: {'enabled' if ai_handler.ai_enabled else 'DISABLED — check GEMINI_API_KEY'}")
    app.run(debug=config.FLASK_DEBUG, host="0.0.0.0", port=config.APP_PORT)