import os
import logging
from flask import Flask, request, jsonify, redirect, url_for
from dotenv import load_dotenv

from config import Config, configure_logging
from handlers.webhook_handler import WebhookHandler
from utils.session_manager import SessionManager
from services.whatsapp_service import WhatsAppService
from services.telegram_service import TelegramService
from services.notification_service import NotificationService
from message_processor import MessageProcessor
from handlers.delivery_handler import DeliveryHandler
from handlers.rider_onboarding_handler import RiderOnboardingHandler
from payment_webhook import payment_webhook_bp, init_payment_webhook
from db_manager import DBManager
from portal.routes import portal_bp, init_portal
from telegram_webhook import telegram_bp, init_telegram_webhook

load_dotenv()
configure_logging()

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "lola-secret-change-in-production")
config = Config()

# ── Core services ─────────────────────────────────────────────────────────────
try:
    session_manager  = SessionManager(config.SESSION_TIMEOUT)
    whatsapp_service = WhatsAppService(config)
    db_manager       = DBManager(config)
    logger.info("Core services initialised.")
except Exception as e:
    logger.error(f"Failed to initialise core services: {e}", exc_info=True)
    exit(1)

# ── Telegram service ──────────────────────────────────────────────────────────
telegram_service = None
try:
    telegram_service = TelegramService(config)
    logger.info("Telegram service initialised.")
except Exception as e:
    logger.error(f"Failed to initialise Telegram service: {e}", exc_info=True)

# ── Notification + delivery services ─────────────────────────────────────────
try:
    notification_service = NotificationService(
        config, db_manager, telegram_service, whatsapp_service
    )
    delivery_handler = DeliveryHandler(
        config, db_manager, notification_service, session_manager
    )
    rider_onboarding_handler = RiderOnboardingHandler(
        config, db_manager, telegram_service
    )
    logger.info("NotificationService, DeliveryHandler and RiderOnboardingHandler initialised.")
except Exception as e:
    logger.error(f"Failed to initialise notification/delivery services: {e}", exc_info=True)
    notification_service     = None
    delivery_handler         = None
    rider_onboarding_handler = None

# ── Message processors (one per platform) ────────────────────────────────────
try:
    whatsapp_processor = MessageProcessor(config, session_manager, None, whatsapp_service)
    logger.info("WhatsApp MessageProcessor initialised.")
except Exception as e:
    logger.error(f"Failed to initialise WhatsApp MessageProcessor: {e}", exc_info=True)
    exit(1)

telegram_processor = None
try:
    if telegram_service:
        telegram_processor = MessageProcessor(config, session_manager, None, telegram_service)
        logger.info("Telegram MessageProcessor initialised.")
except Exception as e:
    logger.error(f"Failed to initialise Telegram MessageProcessor: {e}", exc_info=True)

# ── WhatsApp webhook handler ──────────────────────────────────────────────────
try:
    webhook_handler = WebhookHandler(config, whatsapp_processor)
    logger.info("WhatsApp WebhookHandler initialised.")
except Exception as e:
    logger.error(f"Failed to initialise WebhookHandler: {e}", exc_info=True)
    exit(1)

# ── Register blueprints ───────────────────────────────────────────────────────

# Paystack
init_payment_webhook(
    config,
    session_manager,
    whatsapp_service,
    db_manager,
    notification_service,
)
app.register_blueprint(payment_webhook_bp)
logger.info("Paystack webhook registered at /paystack/webhook")

# Telegram
if telegram_service and telegram_processor:
    init_telegram_webhook(
        config,
        session_manager,
        telegram_service,
        telegram_processor,
        delivery_handler,
        rider_onboarding_handler,
    )
    app.register_blueprint(telegram_bp)
    telegram_service.register_webhook(f"{config.CALLBACK_BASE_URL}/telegram/webhook")
    logger.info("Telegram bot registered at /telegram/webhook")

# Portal
init_portal(
    config,
    whatsapp_service=whatsapp_service,
    telegram_service=telegram_service,
    notification_service=notification_service,
)
app.register_blueprint(portal_bp)
logger.info("Merchant portal registered at /portal")

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
        from handlers.ai_handler import AIHandler
        ai_enabled = False
        try:
            # Best-effort check without instantiating a new handler
            ai_enabled = whatsapp_processor.ai_handler.ai_enabled
        except Exception:
            pass

        return jsonify({
            "status":            "healthy",
            "service":           "Lola — Multi-Vendor Order Bot",
            "ai":                "enabled" if ai_enabled else "disabled",
            "telegram":          "enabled" if telegram_service else "disabled",
            "notifications":     "enabled" if notification_service else "disabled",
            "delivery_handler":  "enabled" if delivery_handler else "disabled",
            "active_sessions":   len(session_manager._sessions) if hasattr(session_manager, '_sessions') else 0,
            "payment_callback":  f"{config.CALLBACK_BASE_URL}/portal/payment/success",
        }), 200
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


@app.route("/api/cleanup", methods=["POST"])
def manual_cleanup():
    try:
        whatsapp_processor.cleanup_expired_resources()
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
    logger.info("Starting Lola — Multi-Vendor Order Bot")
    logger.info(f"Webhook:          {config.CALLBACK_BASE_URL}/webhook")
    logger.info(f"Telegram webhook: {config.CALLBACK_BASE_URL}/telegram/webhook")
    logger.info(f"Payment webhook:  {config.CALLBACK_BASE_URL}/paystack/webhook")
    logger.info(f"Payment callback: {config.CALLBACK_BASE_URL}/portal/payment/success")
    logger.info(f"Portal:           {config.CALLBACK_BASE_URL}/portal")
    logger.info(f"Health:           {config.CALLBACK_BASE_URL}/health")
    app.run(debug=config.FLASK_DEBUG, host="0.0.0.0", port=config.APP_PORT)