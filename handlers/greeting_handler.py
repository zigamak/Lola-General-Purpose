import logging
import sys
from typing import Dict, Any, Optional, List

from .base_handler import BaseHandler
from db_manager import DBManager

logger = logging.getLogger(__name__)
handler = logging.StreamHandler(stream=sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
handler.stream.reconfigure(encoding='utf-8')
logger.addHandler(handler)
logger.setLevel(logging.INFO)


class GreetingHandler(BaseHandler):
    """
    Greeting handler for the Lola multi-vendor bot.
    Platform-agnostic — works on both WhatsApp and Telegram.

    On first contact (or reset), fetches all active vendors from the DB
    and presents them as interactive buttons for the customer to choose from.
    State is set to 'vendor_selection'; VendorHandler picks up from there.
    """

    def __init__(self, config, session_manager, data_manager, messaging_service):
        super().__init__(config, session_manager, data_manager, messaging_service)
        self.db = DBManager(config)

    # ── Public entry points ────────────────────────────────────────────────────

    def handle_greeting_state(
        self, state: Dict, message: str, original_message: str, session_id: str
    ) -> Dict[str, Any]:
        """Handle greeting state — show vendor list."""
        self.logger.info(f"GreetingHandler: session {session_id} — showing vendor list.")
        return self._send_vendor_selection(state, session_id)

    def generate_initial_greeting(
        self, state: Dict, session_id: str, user_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Initial greeting for a new session — show vendor list."""
        self.logger.info(f"GreetingHandler: initial greeting for {session_id}, user '{user_name}'.")
        return self._send_vendor_selection(state, session_id)

    def handle_back_to_main(
        self, state: Dict, session_id: str, message: str = ""
    ) -> Dict[str, Any]:
        """Back to main — re-show vendor list."""
        self.logger.info(f"GreetingHandler: back to main for {session_id}.")
        return self._send_vendor_selection(state, session_id, additional_message=message)

    # ── Core ───────────────────────────────────────────────────────────────────

    def _send_vendor_selection(
        self,
        state: Dict,
        session_id: str,
        additional_message: str = ""
    ) -> Dict[str, Any]:
        """
        Fetch active vendors from DB and send as interactive buttons.
        Sets state to 'vendor_selection' so VendorHandler can process the tap.
        """
        user_name = state.get("user_name", "there")

        # Detect platform and store in state
        platform = self.get_platform(session_id)
        state["platform"] = platform

        # Transition to vendor selection state
        state["current_state"]        = "vendor_selection"
        state["current_handler"]      = "greeting_handler"
        state["conversation_history"] = []

        if not state.get("user_name"):
            state["user_name"] = "Guest"
        if not state.get("phone_number"):
            state["phone_number"] = session_id

        self.session_manager.update_session_state(session_id, state)

        # Fetch vendors
        vendors: List[Dict] = []
        try:
            vendors = self.db.get_all_vendors()
        except Exception as e:
            self.logger.error(f"GreetingHandler: could not fetch vendors for {session_id}: {e}")

        # Welcome message
        greeting_name = f", {user_name}" if user_name and user_name not in ("Guest", "there", "") else ""
        welcome_text = (
            f"Hi{greeting_name}! 👋 Welcome to Lola.\n\n"
            "Who would you like to order from today?"
        )

        if additional_message:
            welcome_text = f"{additional_message}\n\n{welcome_text}"

        self.messaging_service.send_text(session_id, welcome_text)

        if vendors:
            self.send_vendor_list(session_id, vendors, platform)
        else:
            self.messaging_service.send_text(
                session_id,
                "We are setting things up — no vendors are available just yet. "
                "Please try again shortly!"
            )

        return {
            "status":  "vendor_list_sent",
            "vendors": len(vendors),
        }