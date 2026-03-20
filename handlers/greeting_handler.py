from handlers.base_handler import BaseHandler
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class GreetingHandler(BaseHandler):
<<<<<<< HEAD
    """Greeting handler for BEDC Support Bot - redirects to LLM-powered AI chat."""

    def handle_greeting_state(self, state: Dict, message: str, original_message: str, session_id: str) -> Dict[str, Any]:
        """Handle greeting state messages - redirect to AI chat."""
        logger.info(f"GreetingHandler: Redirecting to AI chat for session {session_id}")
        return self._redirect_to_ai_chat(state, session_id, original_message)

    def generate_initial_greeting(self, state: Dict, session_id: str, user_name: Optional[str] = None) -> Dict[str, Any]:
        """Generate initial greeting and redirect to AI chat."""
        logger.info(f"Session {session_id}: Generating initial greeting for '{user_name}'")
        return self._redirect_to_ai_chat(state, session_id)

    def handle_back_to_main(self, state: Dict, session_id: str, message: str = "") -> Dict[str, Any]:
        """Handle back to main navigation - redirect to AI chat."""
        logger.info(f"Session {session_id}: Returning to main")
        return self._redirect_to_ai_chat(state, session_id, message)

    def _redirect_to_ai_chat(self, state: Dict, session_id: str, user_message: str = "") -> Dict[str, Any]:
        """Redirect user to LLM-powered AI chat with proper state management."""
        
        # Check if user wants FAQ
        if user_message and user_message.lower() in ['faq', 'faqs', 'questions', 'help']:
            state["current_state"] = "faq"
            state["current_handler"] = "faq_handler"
            state["conversation_history"] = []
            
            self.session_manager.update_session_state(session_id, state)
            
            return {
                "redirect": "faq_handler",
                "redirect_message": "show_categories",
                "additional_message": None
            }
        
        # Set up state for AI chat
        state["current_state"] = "ai_chat"
        state["current_handler"] = "ai_handler"
        state["conversation_history"] = []
        
        # Preserve user info
=======
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

>>>>>>> ce5cffb (Chowder.ng)
        if not state.get("user_name"):
            state["user_name"] = "Customer"
        if not state.get("phone_number"):
            state["phone_number"] = session_id
<<<<<<< HEAD
        
        # Clear any temporary states
        if "fault_data" in state:
            del state["fault_data"]
        if "billing_checked" in state:
            del state["billing_checked"]
            
        self.session_manager.update_session_state(session_id, state)
        
        # Return redirect instruction with the user's message if any
        return {
            "redirect": "ai_handler", 
            "redirect_message": user_message if user_message else "initial_greeting",
            "additional_message": None
=======

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
>>>>>>> ce5cffb (Chowder.ng)
        }