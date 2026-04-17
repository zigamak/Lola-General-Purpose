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

        # Backwards-compat alias
        self.whatsapp_service = messaging_service

        self.logger = logger

    # ── Platform detection ─────────────────────────────────────────────────────

    def get_platform(self, session_id: str) -> str:
        """
        Infer platform from session_id.
        Telegram chat_ids are purely numeric; WhatsApp uses phone numbers
        that start with a country code and may contain '+'.
        """
        return 'telegram' if str(session_id).lstrip('+').isdigit() and len(str(session_id)) < 15 else 'whatsapp'

    # ── Vendor list sender ─────────────────────────────────────────────────────

    def send_vendor_list(self, session_id: str, vendors: List[Dict], platform: str = None) -> None:
        """
        Send the list of active vendors as interactive buttons.

        WhatsApp supports max 3 reply buttons per message, so vendors are
        paginated into groups of 3.
        Telegram renders all vendors as a single inline keyboard.
        """
        if not vendors:
            self.messaging_service.send_text(
                session_id,
                "Sorry, no vendors are available right now. Please try again later."
            )
            return

        if platform is None:
            platform = self.get_platform(session_id)

        prompt = "Please choose a vendor to order from:"

        if platform == 'telegram':
            # All vendors in one inline keyboard — one button per row
            buttons = [
                {
                    "type":  "reply",
                    "reply": {
                        "id":    f"vendor_{v['id']}",
                        "title": v['name'][:20],          # Telegram button title limit
                    }
                }
                for v in vendors
            ]
            self.messaging_service.send_button_message(session_id, prompt, buttons)

        else:
            # WhatsApp — max 3 buttons per message, paginate
            chunks = [vendors[i:i + 3] for i in range(0, len(vendors), 3)]
            for idx, chunk in enumerate(chunks):
                buttons = [
                    {
                        "type":  "reply",
                        "reply": {
                            "id":    f"vendor_{v['id']}",
                            "title": v['name'][:20],      # WhatsApp button title limit
                        }
                    }
                    for v in chunk
                ]
                text = prompt if idx == 0 else "More vendors:"
                self.messaging_service.send_button_message(session_id, text, buttons)

    # ── Back to main ───────────────────────────────────────────────────────────

    def handle_back_to_main(self, state: Dict, session_id: str, message: str = "") -> Dict:
        """
        Handle returning to main conversation mode.
        Clears temporary state and returns user to vendor selection.
        """
        # Clear vendor + order state so they pick a vendor fresh
        for key in ("selected_vendor_id", "selected_vendor_name", "menu_image_url",
                    "vendor_products", "vendor_menu", "fault_data", "billing_inquiry",
                    "order_ref", "db_order_id", "payment_pending", "payment_ref",
                    "payment_amount_kobo", "delivery_address"):
            state.pop(key, None)

        state["current_state"]        = "vendor_selection"
        state["current_handler"]      = "greeting_handler"
        state["conversation_history"] = []

        # Preserve essentials
        state["user_name"]    = state.get("user_name", "Guest")
        state["phone_number"] = state.get("phone_number", session_id)

        self.session_manager.update_session_state(session_id, state)
        self.logger.info(f"Session {session_id} returned to vendor selection.")

        return {
            "redirect":           "greeting_handler",
            "redirect_message":   "vendor_list",
            "additional_message": message if message else None,
        }