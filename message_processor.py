import logging
from handlers.greeting_handler import GreetingHandler
from handlers.ai_handler import AIHandler

logger = logging.getLogger(__name__)


class MessageProcessor:
    """Message processor for Makinde Kitchen / Lola WhatsApp order bot."""

    def __init__(self, config, session_manager, data_manager, whatsapp_service):
        self.config          = config
        self.session_manager = session_manager
        self.data_manager    = data_manager
        self.whatsapp_service = whatsapp_service

        self.greeting_handler = GreetingHandler(config, session_manager, data_manager, whatsapp_service)
        self.ai_handler       = AIHandler(config, session_manager, data_manager, whatsapp_service)

        logger.info("MessageProcessor initialised — Makinde Kitchen.")

    def process_message(self, message_data, session_id, user_name):
        """Main entry point for all incoming WhatsApp messages."""
        try:
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
            state["current_state"]   = "ai_chat"
            state["current_handler"] = "ai_handler"
            self.session_manager.update_session_state(session_id, state)
            return self.whatsapp_service.create_text_message(
                session_id,
                "Something went wrong on our end. Send 'menu' to start over or just tell us what you'd like to order!"
            )

    def _update_user_info(self, state, session_id, user_name):
        if user_name and not state.get("user_name"):
            state["user_name"] = user_name
        if not state.get("user_name"):
            state["user_name"] = "Guest"
        state["phone_number"] = session_id

    def _route_to_handler(self, state, message, original_message, session_id, user_name):
        current_handler = state.get("current_handler", "")
        current_state   = state.get("current_state", "start")
        welcome_sent    = state.get("welcome_sent", False)

        try:
            # Hard reset triggers — always restart fresh
            if message in ("menu", "start"):
                logger.info(f"Session {session_id}: Reset trigger '{message}'.")
                return self._start_fresh(state, session_id, user_name, original_message)

            # New session — first time this number has messaged
            if current_state == "start" or not current_handler:
                logger.info(f"Session {session_id}: New session.")
                return self._start_fresh(state, session_id, user_name, original_message)

            # Active session — route to AI
            if current_handler == "ai_handler" and current_state == "ai_chat":

                # Returning session with welcome already sent —
                # pass to AI with is_returning=True so it greets appropriately
                # instead of showing the menu again
                if welcome_sent and message in ("hi", "hello", "hey", "order update", "track my order", "where is my order"):
                    logger.info(f"Session {session_id}: Returning greeting — handing to AI as returning.")
                    return self.ai_handler._handle_returning(state, session_id, original_message)

                return self.ai_handler.handle_ai_chat_state(state, message, original_message, session_id)

            # Fallback
            logger.info(f"Session {session_id}: Unknown state '{current_state}' — resetting.")
            return self._start_fresh(state, session_id, user_name, original_message)

        except Exception as e:
            logger.error(f"Session {session_id}: Routing error: {e}", exc_info=True)
            return self._start_fresh(state, session_id, user_name, original_message)

    def _start_fresh(self, state, session_id, user_name, original_message=None):
        """Start a completely new session — show welcome + menu image."""
        state["current_state"]        = "ai_chat"
        state["current_handler"]      = "ai_handler"
        state["user_name"]            = user_name or "Guest"
        state["conversation_history"] = []
        state["is_returning"]         = False
        self.session_manager.update_session_state(session_id, state)
        logger.info(f"Session {session_id}: Fresh start for '{user_name}'.")
        return self.ai_handler._handle_start(state, session_id, original_message)

    def cleanup_expired_resources(self):
        try:
            self.session_manager.cleanup_expired_sessions()
            logger.info("Resource cleanup completed.")
        except Exception as e:
            logger.error(f"Error in resource cleanup: {e}", exc_info=True)