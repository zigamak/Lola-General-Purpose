import logging
from handlers.greeting_handler import GreetingHandler
from handlers.ai_handler import AIHandler

logger = logging.getLogger(__name__)

class MessageProcessor:
    """Message processor for Chowder.ng WhatsApp order bot."""

    def __init__(self, config, session_manager, data_manager, whatsapp_service):
        self.config = config
        self.session_manager = session_manager
        self.data_manager = data_manager
        self.whatsapp_service = whatsapp_service

        self.greeting_handler = GreetingHandler(config, session_manager, data_manager, whatsapp_service)
        self.ai_handler = AIHandler(config, session_manager, data_manager, whatsapp_service)

        logger.info("MessageProcessor initialized for Chowder.ng order bot.")

    def process_message(self, message_data, session_id, user_name):
        """Main method to process incoming WhatsApp messages."""
        try:
            state = self.session_manager.get_session_state(session_id)
            self.session_manager.update_session_activity(session_id)

            if isinstance(message_data, dict):
                message = message_data.get("text", "")
            else:
                message = message_data

            original_message = message
            message = message.strip().lower() if message else ""

            self._update_user_info(state, session_id, user_name)
            self.session_manager.update_session_state(session_id, state)

            response = self._route_to_handler(state, message, original_message, session_id, user_name)
            return response

        except Exception as e:
            logger.error(f"Session {session_id}: Error processing message: {e}", exc_info=True)
            state = self.session_manager.get_session_state(session_id)
            state["current_state"] = "ai_chat"
            state["current_handler"] = "ai_handler"
            self.session_manager.update_session_state(session_id, state)
            return self.whatsapp_service.create_text_message(
                session_id,
                "⚠️ Something went wrong on our end. Send *menu* to see what we've got or just tell us what you'd like to order! 🍟"
            )

    def _update_user_info(self, state, session_id, user_name):
        """Update user information in session state."""
        if user_name and not state.get("user_name"):
            state["user_name"] = user_name
        if not state.get("user_name"):
            state["user_name"] = "Guest"
        state["phone_number"] = session_id

    def _route_to_handler(self, state, message, original_message, session_id, user_name):
        """Route messages to the appropriate handler."""
        current_handler_name = state.get("current_handler", "ai_handler")
        current_state = state.get("current_state", "start")

        if "user_name" not in state:
            state["user_name"] = user_name or "Guest"

        try:
            # Global trigger words — always start/restart the order chat
            trigger_words = ["menu", "start", "hello", "hi", "order", "hey"]
            if message in trigger_words:
                logger.info(f"Session {session_id}: Trigger word '{message}' — starting order chat.")
                return self._start_order_chat(state, session_id, user_name, original_message)

            # New session — go straight to order chat
            if current_state == "start" or not current_handler_name:
                logger.info(f"Session {session_id}: New session — starting order chat.")
                return self._start_order_chat(state, session_id, user_name, original_message)

            # Route to AI handler for all order conversation
            if current_handler_name == "ai_handler":
                if current_state == "ai_chat":
                    return self.ai_handler.handle_ai_chat_state(state, message, original_message, session_id)
                else:
                    logger.info(f"Session {session_id}: Unhandled state '{current_state}' — defaulting to order chat.")
                    return self._start_order_chat(state, session_id, user_name, original_message)

            # Fallback
            logger.info(f"Session {session_id}: Unknown handler '{current_handler_name}' — redirecting to order chat.")
            return self._start_order_chat(state, session_id, user_name, original_message)

        except Exception as e:
            logger.error(f"Session {session_id}: Error in message routing: {e}", exc_info=True)
            return self._start_order_chat(state, session_id, user_name, original_message)

    def _start_order_chat(self, state, session_id, user_name, original_message=None):
        """Start or restart the conversational order flow."""
        state["current_state"] = "ai_chat"
        state["current_handler"] = "ai_handler"
        state["user_name"] = user_name or "Guest"
        self.session_manager.update_session_state(session_id, state)

        logger.info(f"Session {session_id}: Starting order chat for '{user_name}' — msg: '{original_message}'.")
        return self.ai_handler._handle_start(state, session_id, original_message)

    def cleanup_expired_resources(self):
        """Clean up expired sessions."""
        try:
            self.session_manager.cleanup_expired_sessions()
            logger.info("Resource cleanup completed.")
        except Exception as e:
            logger.error(f"Error in resource cleanup: {e}", exc_info=True)