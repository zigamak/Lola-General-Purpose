import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


class BaseHandler:
    """
    Base class for all message handlers.
    Uses self.messaging_service (aliased as whatsapp_service for
    backwards compatibility) so subclasses work with any platform.
    """

    def __init__(self, config, session_manager, data_manager, messaging_service):
        self.config          = config
        self.session_manager = session_manager
        self.data_manager    = data_manager

        # Primary reference — platform-agnostic name
        self.messaging_service = messaging_service

        # Backwards-compat alias so any handler that still references
        # self.whatsapp_service continues to work without changes
        self.whatsapp_service = messaging_service

        self.logger = logger

    def handle_back_to_main(self, state: Dict, session_id: str, message: str = "") -> Dict:
        """
        Handle returning to main conversation mode.
        Clears temporary state and redirects to conversational AI.
        """
        state["current_state"]   = "ai_chat"
        state["current_handler"] = "ai_handler"

        # Preserve essential user data
        user_name    = state.get("user_name", "Customer")
        phone_number = state.get("phone_number", session_id)

        # Clear temporary conversation state
        for key in ("fault_data", "billing_inquiry"):
            state.pop(key, None)

        state["conversation_history"] = []
        state["user_name"]            = user_name
        state["phone_number"]         = phone_number

        self.session_manager.update_session_state(session_id, state)
        self.logger.info(f"Session {session_id} returned to AI chat.")

        return {
            "redirect":           "ai_handler",
            "redirect_message":   "initial_greeting",
            "additional_message": message if message else "How can I help you today?",
        }