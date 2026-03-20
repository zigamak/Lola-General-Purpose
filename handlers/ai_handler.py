import logging
from typing import Dict
from .base_handler import BaseHandler
from datetime import datetime
from services.ai_service import AIService

logger = logging.getLogger(__name__)

MENU_IMAGE_URL = "https://eventio.africa/wp-content/uploads/2026/03/chowder.ng_.jpg"


class AIHandler(BaseHandler):
    """
    Conversational order handler for Chowder.ng.
    Entry: short welcome text + menu image.
    After that: every message goes to the AI agent.
    """

    def __init__(self, config, session_manager, data_manager, whatsapp_service):
        super().__init__(config, session_manager, data_manager, whatsapp_service)

        self.ai_service = AIService(config, data_manager)
        self.ai_enabled = self.ai_service.ai_enabled

        if not self.ai_enabled:
            logger.warning("AIHandler: AI features disabled — AIService could not be initialized.")
        else:
            logger.info("AIHandler: Chowder.ng conversational order bot ready.")

    def handle_ai_chat_state(self, state: Dict, message: str, original_message: str, session_id: str) -> Dict:
        """Handle all incoming messages through the conversational AI agent."""
        logger.info(f"AIHandler: message from session {session_id}: '{original_message[:80]}'")
        return self._process_message(state, session_id, original_message)

    def handle_ai_menu_state(self, state: Dict, message: str, original_message: str, session_id: str) -> Dict:
        """Treat menu state the same as chat."""
        if message in ("ai_chat", "start_ai_chat", "initial_greeting"):
            return self._handle_start(state, session_id, original_message)
        if message in ("back_to_main", "menu"):
            return self.handle_back_to_main(state, session_id)
        return self._handle_start(state, session_id, original_message)

    def _handle_start(self, state: Dict, session_id: str, user_message: str = None) -> Dict:
        """
        Entry point — send short welcome text with user's name + menu image.
        No AI call here. AI only kicks in from next message onwards.
        """
        state["current_state"] = "ai_chat"
        state["current_handler"] = "ai_handler"
        state["conversation_history"] = []
        state["welcome_sent"] = True
        self.session_manager.update_session_state(session_id, state)

        user_name = state.get("user_name", "")
        greeting_name = f", {user_name}" if user_name and user_name != "Guest" else ""

        welcome_text = (
            f"👋 Welcome to Chowder.ng{greeting_name}! 🍟\n\n"
            "Here's our menu — what would you like to order?"
        )

        # 1. Send short welcome text
        self.whatsapp_service.send_message(
            self.whatsapp_service.create_text_message(session_id, welcome_text)
        )

        # 2. Send menu image
        try:
            self.whatsapp_service.send_image_message(
                session_id,
                MENU_IMAGE_URL,
                caption=""
            )
        except Exception as e:
            logger.warning(f"Could not send menu image for {session_id}: {e}")

        return {"status": "welcome_sent"}

    def _process_message(self, state: Dict, session_id: str, user_message: str) -> Dict:
        """Send the message to the AI agent and return its response."""
        phone_number = state.get("phone_number", session_id)
        user_name = state.get("user_name", "Customer")
        conversation_history = state.get("conversation_history", [])

        if not self.ai_enabled:
            return self.whatsapp_service.create_text_message(
                session_id,
                "Sorry, our ordering system is currently unavailable 😅 Please try again shortly!"
            )

        try:
            ai_response, _, _, _ = self.ai_service.generate_order_response(
                user_message,
                conversation_history,
                phone_number,
                user_name,
                session_id
            )

            conversation_history.append({
                "user": user_message,
                "assistant": ai_response,
                "timestamp": datetime.now().isoformat()
            })

            if len(conversation_history) > 10:
                conversation_history = conversation_history[-10:]

            state["conversation_history"] = conversation_history
            self.session_manager.update_session_state(session_id, state)

            return self.whatsapp_service.create_text_message(session_id, ai_response)

        except Exception as e:
            logger.error(f"AIHandler error for session {session_id}: {e}", exc_info=True)
            return self.whatsapp_service.create_text_message(
                session_id,
                "Something went wrong on our end 😅 Please try again!"
            )