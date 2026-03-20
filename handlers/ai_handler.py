import logging
from typing import Dict
from .base_handler import BaseHandler
from datetime import datetime
from services.ai_service import AIService

logger = logging.getLogger(__name__)


class AIHandler(BaseHandler):
    """
    Conversational order handler for Chowder.ng.
    Every message goes straight to the AI agent — no rigid state machine.
    The AI handles the full flow: menu → order → total → location → confirmation.
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
        """Treat menu state the same as chat — let AI handle it."""
        if message in ("ai_chat", "start_ai_chat", "initial_greeting"):
            return self._handle_start(state, session_id, original_message)
        if message in ("back_to_main", "menu"):
            return self.handle_back_to_main(state, session_id)
        return self._handle_start(state, session_id, original_message)

    def _handle_start(self, state: Dict, session_id: str, user_message: str = None) -> Dict:
        """Entry point — set up state then pass to the AI."""
        state["current_state"] = "ai_chat"
        state["current_handler"] = "ai_handler"
        if "conversation_history" not in state:
            state["conversation_history"] = []
        self.session_manager.update_session_state(session_id, state)
        return self._process_message(state, session_id, user_message or "hi")

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

            # Save exchange to conversation history
            conversation_history.append({
                "user": user_message,
                "assistant": ai_response,
                "timestamp": datetime.now().isoformat()
            })

            # Keep last 10 exchanges to manage context size
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