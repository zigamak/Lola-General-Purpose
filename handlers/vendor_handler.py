import logging
import sys
from typing import Dict, Any, Optional

from .base_handler import BaseHandler
from db_manager import DBManager

logger = logging.getLogger(__name__)
handler = logging.StreamHandler(stream=sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
handler.stream.reconfigure(encoding='utf-8')
logger.addHandler(handler)
logger.setLevel(logging.INFO)


class VendorHandler(BaseHandler):
    """
    Handles vendor selection after the customer taps a vendor button.

    Responsibilities:
      1. Parse vendor_N from button callback
      2. Load vendor details from DB
      3. Build menu text from vendor's products
      4. Store everything in session state
      5. Hand off to AIHandler._handle_start()
    """

    def __init__(self, config, session_manager, data_manager, messaging_service):
        super().__init__(config, session_manager, data_manager, messaging_service)
        self.db = DBManager(config)

    # ── Public entry point ─────────────────────────────────────────────────────

    def handle_vendor_selection(
        self,
        state: Dict,
        message: str,
        session_id: str,
        ai_handler=None
    ) -> Dict[str, Any]:
        """
        Called by MessageProcessor when current_state == 'vendor_selection'.

        message: the raw text from the button tap, e.g. 'vendor_3'
        """
        vendor_id = self._parse_vendor_id(message)

        if not vendor_id:
            # User typed free text instead of tapping a button — re-show vendor list
            self.logger.info(
                f"VendorHandler: non-button message '{message}' during vendor_selection "
                f"for {session_id} — re-showing vendor list."
            )
            return self._re_show_vendor_list(state, session_id)

        # Load vendor from DB
        vendor = None
        try:
            vendor = self.db.get_vendor_by_id(vendor_id)
        except Exception as e:
            self.logger.error(f"VendorHandler: DB error loading vendor {vendor_id}: {e}")

        if not vendor:
            self.logger.warning(f"VendorHandler: vendor_id={vendor_id} not found for {session_id}")
            self.messaging_service.send_text(
                session_id,
                "Sorry, that vendor is not available right now. Please choose another."
            )
            return self._re_show_vendor_list(state, session_id)

        # Build menu text from products
        menu_text = ""
        try:
            menu_text = self.db.format_menu_text(vendor_id)
        except Exception as e:
            self.logger.error(f"VendorHandler: could not build menu text for vendor {vendor_id}: {e}")
            menu_text = "Menu currently unavailable."

        # Store vendor details in session state
        state["selected_vendor_id"]   = vendor['id']
        state["selected_vendor_name"] = vendor['name']
        state["menu_image_url"]       = vendor.get('menu_image_url') or ""
        state["vendor_menu"]          = menu_text
        state["vendor_delivery_fee"]  = vendor.get('delivery_fee', 500)
        state["vendor_free_min"]      = vendor.get('free_delivery_min', 5000)
        state["vendor_hours"]         = vendor.get('opening_hours', 'Mon–Sat: 10am–9pm')
        state["vendor_areas"]         = vendor.get('delivery_areas', 'Our service area')
        state["vendor_support"]       = vendor.get('support_contact', '')
        state["vendor_ref_prefix"]    = vendor.get('order_ref_prefix', 'ORD')

        # Transition state to ai_chat
        state["current_state"]   = "ai_chat"
        state["current_handler"] = "ai_handler"
        state["welcome_sent"]    = True
        state["is_returning"]    = False

        self.session_manager.update_session_state(session_id, state)

        self.logger.info(
            f"VendorHandler: {session_id} selected vendor '{vendor['name']}' (id={vendor_id})."
        )

        # Send welcome prompt first -- one place only, never via _handle_start
        user_name     = state.get("user_name", "")
        greeting_name = f", {user_name}" if user_name and user_name not in ("Guest", "") else ""
        welcome_text  = (
            f"Hi{greeting_name}! Welcome to {vendor['name']}. 🍽️\n\n"
            "Here's our menu - what would you like to order today?"
        )
        self.messaging_service.send_text(session_id, welcome_text)

        # Send menu image if available, otherwise fall back to formatted text menu
        menu_image_url = vendor.get('menu_image_url', '')
        if menu_image_url:
            try:
                self.messaging_service.send_image_message(session_id, menu_image_url, caption="")
            except Exception as e:
                self.logger.warning(
                    f"Could not send menu image for {session_id}: {e} — sending text menu instead."
                )
                if menu_text and menu_text != "Menu currently unavailable.":
                    self.messaging_service.send_text(session_id, menu_text)
        else:
            # No image URL — send the DB product list as plain text
            self.logger.info(
                f"VendorHandler: no menu image for vendor {vendor_id} — sending text menu."
            )
            if menu_text and menu_text != "Menu currently unavailable.":
                self.messaging_service.send_text(session_id, menu_text)
            else:
                self.logger.warning(
                    f"VendorHandler: no menu image and no menu text for vendor {vendor_id}."
                )

        return {"status": "vendor_selected", "vendor": vendor['name']}

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _parse_vendor_id(self, message: str) -> Optional[int]:
        """
        Extract vendor id from 'vendor_N' button callback.
        Returns None if message doesn't match pattern.
        """
        msg = (message or "").strip().lower()
        if msg.startswith("vendor_"):
            try:
                return int(msg.split("_", 1)[1])
            except (ValueError, IndexError):
                pass
        return None

    def _re_show_vendor_list(self, state: Dict, session_id: str) -> Dict[str, Any]:
        """Re-fetch and re-send vendor list when selection is unclear."""
        platform = state.get("platform") or self.get_platform(session_id)
        try:
            vendors = self.db.get_all_vendors()
            if vendors:
                self.messaging_service.send_text(
                    session_id,
                    "Please tap one of the vendors below to continue:"
                )
                self.send_vendor_list(session_id, vendors, platform)
                return {"status": "vendor_list_reshown"}
        except Exception as e:
            self.logger.error(f"VendorHandler._re_show_vendor_list error: {e}")

        self.messaging_service.send_text(
            session_id,
            "Sorry, something went wrong. Send 'menu' to start over."
        )
        return {"status": "error"}