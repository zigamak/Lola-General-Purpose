from .base_handler import BaseHandler
import logging
from typing import Dict, Any, List, Optional
import sys

logger = logging.getLogger(__name__)
handler = logging.StreamHandler(stream=sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
handler.stream.reconfigure(encoding='utf-8')
logger.addHandler(handler)
logger.setLevel(logging.INFO)


class GreetingHandler(BaseHandler):
    """
    Greeting handler for Chowder.ng WhatsApp order bot.
    Sends the menu image on first contact, then hands off to the AI order handler.
    """

    MENU_IMAGE_URL = "https://eventio.africa/wp-content/uploads/2026/03/chowder.ng_.jpg"

    def handle_greeting_state(self, state: Dict, message: str, original_message: str, session_id: str) -> Dict[str, Any]:
        """Handle greeting state — send menu image and redirect to AI order handler."""
        self.logger.info(f"GreetingHandler: session {session_id} — sending Chowder.ng welcome.")
        return self._send_welcome_and_redirect(state, session_id)

    def generate_initial_greeting(self, state: Dict, session_id: str, user_name: Optional[str] = None) -> Dict[str, Any]:
        """Generate initial greeting with menu image, then hand off to AI handler."""
        self.logger.info(f"GreetingHandler: initial greeting for session {session_id}, user '{user_name}'.")
        return self._send_welcome_and_redirect(state, session_id)

    def handle_back_to_main(self, state: Dict, session_id: str, message: str = "") -> Dict[str, Any]:
        """Back to main — resend welcome and redirect."""
        self.logger.info(f"GreetingHandler: back to main for session {session_id}.")
        return self._send_welcome_and_redirect(state, session_id, additional_message=message)

    def _send_welcome_and_redirect(
        self, state: Dict, session_id: str, additional_message: str = ""
    ) -> Dict[str, Any]:
        """
        Send the Chowder.ng menu image with a welcome caption,
        then redirect to the AI order handler for conversational ordering.
        """
        user_name = state.get("user_name", "there")

        # Transition state to ai_chat
        state["current_state"] = "ai_chat"
        state["current_handler"] = "ai_handler"
        state["conversation_history"] = []

        if not state.get("user_name"):
            state["user_name"] = "Guest"
        if not state.get("phone_number"):
            state["phone_number"] = session_id

        self.session_manager.update_session_state(session_id, state)

        # Send menu image first
        try:
            self.whatsapp_service.send_image_message(
                session_id,
                self.MENU_IMAGE_URL,
                caption=(
                    f"👋 Welcome to *Chowder.ng*, {user_name}! 🍟\n\n"
                    "Here's our Signature Loaded Fries menu.\n"
                    "Just tell me what you'd like and we'll sort you out! 😋"
                )
            )
        except Exception as e:
            self.logger.error(
                f"Session {session_id}: Could not send menu image: {e}. Continuing without image."
            )

        # Hand off to AI handler for conversational ordering
        return {
            "redirect": "ai_handler",
            "redirect_message": "initial_greeting",
            "additional_message": additional_message if additional_message else None
        }