import logging
from handlers.greeting_handler import GreetingHandler
from handlers.vendor_handler import VendorHandler
from handlers.ai_handler import AIHandler

logger = logging.getLogger(__name__)


class MessageProcessor:
    """
    Platform-agnostic message processor for the Lola multi-vendor bot.
    Works with any messaging service (WhatsApp, Telegram, etc.)
    as long as it implements the shared service interface.
    """

    def __init__(self, config, session_manager, data_manager, messaging_service):
        self.config            = config
        self.session_manager   = session_manager
        self.data_manager      = data_manager
        self.messaging_service = messaging_service

        self.greeting_handler = GreetingHandler(config, session_manager, data_manager, messaging_service)
        self.vendor_handler   = VendorHandler(config, session_manager, data_manager, messaging_service)
        self.ai_handler       = AIHandler(config, session_manager, data_manager, messaging_service)

        logger.info("MessageProcessor initialised — Lola multi-vendor bot.")

    def process_message(self, message_data, session_id, user_name):
        """Main entry point for all incoming messages."""
        try:
            msg_id = message_data.get("message_id") if isinstance(message_data, dict) else None
            if msg_id and hasattr(self.messaging_service, "send_typing_indicator"):
                self.messaging_service.send_typing_indicator(msg_id)

            state = self.session_manager.get_session_state(session_id)
            self.session_manager.update_session_activity(session_id)

            message = message_data.get("text", "") if isinstance(message_data, dict) else message_data
            original_message = message
            message = message.strip().lower() if message else ""

            self._update_user_info(state, session_id, user_name)
            self.session_manager.update_session_state(session_id, state)

            return self._route_to_handler(state, message, original_message, session_id, user_name)

        except Exception as e:
            logger.error(f"Session {session_id}: Error processing message: {e}", exc_info=True)
            state = self.session_manager.get_session_state(session_id)
            state["current_state"]   = "vendor_selection"
            state["current_handler"] = "greeting_handler"
            self.session_manager.update_session_state(session_id, state)
            return self.messaging_service.send_text(
                session_id,
                "Something went wrong on our end. Send 'menu' to start over!"
            )

    def _update_user_info(self, state, session_id, user_name):
        if user_name and not state.get("user_name"):
            state["user_name"] = user_name
        if not state.get("user_name"):
            state["user_name"] = "Guest"
        state["phone_number"] = session_id

        # Detect and store platform once
        if not state.get("platform"):
            state["platform"] = (
                'telegram'
                if str(session_id).lstrip('+').isdigit() and len(str(session_id)) < 15
                else 'whatsapp'
            )

    def _route_to_handler(self, state, message, original_message, session_id, user_name):
        current_handler = state.get("current_handler", "")
        current_state   = state.get("current_state", "start")
        welcome_sent    = state.get("welcome_sent", False)

        try:
            # Hard reset triggers — always restart fresh and show vendor list
            if message in ("menu", "start"):
                logger.info(f"Session {session_id}: Reset trigger '{message}'.")
                return self._start_fresh(state, session_id, user_name)

            # New session — never been here before
            if current_state == "start" or not current_handler:
                logger.info(f"Session {session_id}: New session.")
                return self._start_fresh(state, session_id, user_name)

            # ── Vendor selection state ─────────────────────────────────────
            # Customer is picking which vendor to order from
            if current_state == "vendor_selection":
                return self.vendor_handler.handle_vendor_selection(
                    state, original_message, session_id,
                    ai_handler=self.ai_handler
                )

            # ── Active AI chat ─────────────────────────────────────────────
            if current_handler == "ai_handler" and current_state == "ai_chat":
                if welcome_sent and message in (
                    "hi", "hello", "hey",
                    "order update", "track my order", "where is my order"
                ):
                    logger.info(f"Session {session_id}: Returning greeting — handing to AI.")
                    return self.ai_handler._handle_returning(state, session_id, original_message)

                return self.ai_handler.handle_ai_chat_state(
                    state, message, original_message, session_id
                )

            # Fallback — unknown state, restart
            logger.info(f"Session {session_id}: Unknown state '{current_state}' — resetting.")
            return self._start_fresh(state, session_id, user_name)

        except Exception as e:
            logger.error(f"Session {session_id}: Routing error: {e}", exc_info=True)
            return self._start_fresh(state, session_id, user_name)

    def _start_fresh(self, state, session_id, user_name, original_message=None):
        """
        Start a completely new session.
        Clears vendor + order state and shows the vendor list.
        """
        # Clear everything vendor/order related
        for key in ("selected_vendor_id", "selected_vendor_name", "menu_image_url",
                    "vendor_menu", "vendor_delivery_fee", "vendor_free_min",
                    "vendor_hours", "vendor_areas", "vendor_support", "vendor_ref_prefix",
                    "order_ref", "db_order_id", "payment_pending", "payment_ref",
                    "payment_amount_kobo", "delivery_address", "welcome_sent", "is_returning"):
            state.pop(key, None)

        state["current_state"]        = "vendor_selection"
        state["current_handler"]      = "greeting_handler"
        state["user_name"]            = user_name or "Guest"
        state["phone_number"]         = session_id
        state["conversation_history"] = []

        self.session_manager.update_session_state(session_id, state)
        logger.info(f"Session {session_id}: Fresh start for '{user_name}'.")

        return self.greeting_handler.generate_initial_greeting(
            state, session_id, user_name
        )

    def cleanup_expired_resources(self):
        try:
            self.session_manager.cleanup_expired_sessions()
            logger.info("Resource cleanup completed.")
        except Exception as e:
            logger.error(f"Error in resource cleanup: {e}", exc_info=True)