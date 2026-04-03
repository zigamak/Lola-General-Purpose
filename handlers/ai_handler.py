import logging
import re
import random
from typing import Dict
from datetime import datetime

from .base_handler import BaseHandler
from services.ai_service import AIService
from services.payment_service import PaymentService

logger = logging.getLogger(__name__)

MENU_IMAGE_URL = "https://eventio.africa/wp-content/uploads/2026/04/lola-general-purpose.jpg"


class AIHandler(BaseHandler):
    """
    Conversational order handler for Makinde Kitchen.

    Flow:
      1. _handle_start()  — sends welcome text + menu image, no AI call
      2. handle_ai_chat_state() — all subsequent messages go to the AI agent
      3. If AI returns [PAYMENT_READY:amount=XXXX], intercepts it:
           - strips the tag from the customer-facing text
           - generates a Paystack payment link
           - sends a clean payment message with order details + link
    """

    def __init__(self, config, session_manager, data_manager, whatsapp_service):
        super().__init__(config, session_manager, data_manager, whatsapp_service)

        self.ai_service = AIService(config, data_manager)
        self.payment_service = PaymentService(config)
        self.ai_enabled = self.ai_service.ai_enabled

        if not self.ai_enabled:
            logger.warning("AIHandler: AI disabled — AIService could not be initialised.")
        else:
            logger.info("AIHandler: Makinde Kitchen order bot ready.")

    # ── Public entry points ───────────────────────────────────────────────────

    def handle_ai_chat_state(self, state: Dict, message: str, original_message: str, session_id: str) -> Dict:
        """Handle all incoming messages through the conversational AI agent."""
        logger.info(f"AIHandler: message from {session_id}: '{original_message[:80]}'")
        return self._process_message(state, session_id, original_message)

    def handle_ai_menu_state(self, state: Dict, message: str, original_message: str, session_id: str) -> Dict:
        """Menu state — treat as chat."""
        if message in ("ai_chat", "start_ai_chat", "initial_greeting"):
            return self._handle_start(state, session_id)
        if message in ("back_to_main", "menu"):
            return self.handle_back_to_main(state, session_id)
        return self._handle_start(state, session_id)

    # ── Welcome ───────────────────────────────────────────────────────────────

    def _handle_start(self, state: Dict, session_id: str, user_message: str = None) -> Dict:
        """
        Entry point — send short welcome text + menu image.
        Resets conversation history. No AI call here.
        """
        state["current_state"] = "ai_chat"
        state["current_handler"] = "ai_handler"
        state["conversation_history"] = []
        state["order_ref"] = f"MK{random.randint(10000, 99999)}"
        state["welcome_sent"] = True
        self.session_manager.update_session_state(session_id, state)

        user_name = state.get("user_name", "")
        greeting_name = f", {user_name}" if user_name and user_name not in ("Guest", "") else ""

        welcome_text = (
            f"Hi{greeting_name}! Welcome to Makinde Kitchen. 🍛\n\n"
            "Here's our menu — what would you like to order today?"
        )

        self.whatsapp_service.send_message(
            self.whatsapp_service.create_text_message(session_id, welcome_text)
        )

        try:
            self.whatsapp_service.send_image_message(
                session_id,
                MENU_IMAGE_URL,
                caption=""
            )
        except Exception as e:
            logger.warning(f"Could not send menu image for {session_id}: {e}")

        return {"status": "welcome_sent"}

    # ── Core message processing ───────────────────────────────────────────────

    def _process_message(self, state: Dict, session_id: str, user_message: str) -> Dict:
        """Send message to AI agent. Handle payment trigger if returned."""
        phone_number = state.get("phone_number", session_id)
        user_name = state.get("user_name", "Customer")
        conversation_history = state.get("conversation_history", [])
        order_ref = state.get("order_ref") or f"MK{random.randint(10000, 99999)}"

        if not self.ai_enabled:
            return self.whatsapp_service.create_text_message(
                session_id,
                "Sorry, our ordering system is currently unavailable. Please try again shortly!"
            )

        try:
            clean_response, payment_triggered, order_ref, raw_response = self.ai_service.generate_order_response(
                user_message=user_message,
                conversation_history=conversation_history,
                phone_number=phone_number,
                user_name=user_name,
                session_id=session_id,
                order_ref=order_ref,
            )

            # Persist order ref back to state
            state["order_ref"] = order_ref

            # Save to conversation history
            conversation_history.append({
                "user": user_message,
                "assistant": clean_response,
                "timestamp": datetime.now().isoformat()
            })
            if len(conversation_history) > 10:
                conversation_history = conversation_history[-10:]
            state["conversation_history"] = conversation_history
            self.session_manager.update_session_state(session_id, state)

            # ── Payment flow ──────────────────────────────────────────────────
            if payment_triggered and raw_response:
                return self._handle_payment_trigger(
                    state, session_id, clean_response, raw_response,
                    phone_number, user_name, order_ref
                )

            # ── Normal response ───────────────────────────────────────────────
            return self.whatsapp_service.create_text_message(session_id, clean_response)

        except Exception as e:
            logger.error(f"AIHandler error for {session_id}: {e}", exc_info=True)
            return self.whatsapp_service.create_text_message(
                session_id,
                "Something went wrong on our end. Please try again!"
            )

    # ── Payment trigger ───────────────────────────────────────────────────────

    def _handle_payment_trigger(
        self,
        state: Dict,
        session_id: str,
        clean_response: str,
        raw_response: str,
        phone_number: str,
        user_name: str,
        order_ref: str,
    ) -> Dict:
        """
        Called when the AI appends [PAYMENT_READY:amount=XXXX].
        1. Send the clean order summary message
        2. Generate a Paystack link
        3. Send the payment link message
        """
        # Extract amount in kobo from the raw tag
        amount_kobo = self._extract_payment_amount(raw_response)

        if not amount_kobo:
            logger.error(f"Payment tag found but could not parse amount for {session_id}. Raw: {raw_response[-200:]}")
            # Fall back to sending the summary without a link
            return self.whatsapp_service.create_text_message(session_id, clean_response)

        # 1. Send the order summary text
        self.whatsapp_service.send_message(
            self.whatsapp_service.create_text_message(session_id, clean_response)
        )

        # 2. Generate Paystack link
        try:
            customer_email = self.payment_service.generate_customer_email(phone_number, user_name)
            payment_url = self.payment_service.create_payment_link(
                amount=amount_kobo,
                email=customer_email,
                reference=order_ref,
                customer_name=user_name,
                customer_phone=phone_number,
                metadata={"order_ref": order_ref, "channel": "whatsapp"},
            )
        except Exception as e:
            logger.error(f"Paystack link generation failed for {session_id}: {e}", exc_info=True)
            payment_url = None

        # 3. Send payment message
        if payment_url:
            amount_naira = amount_kobo // 100
            payment_message = (
                f"To complete your order, please make payment here:\n\n"
                f"Amount: ₦{amount_naira:,}\n"
                f"Ref: {order_ref}\n\n"
                f"Pay here: {payment_url}\n\n"
                f"Please complete payment within 10 minutes. "
                f"Your order will be confirmed automatically once payment is received."
            )
        else:
            payment_message = (
                f"We could not generate your payment link right now. "
                f"Please contact us directly to complete your order:\n"
                f"Ref: {order_ref}"
            )

        # Mark that payment link has been sent in session state
        state["payment_pending"] = True
        state["payment_ref"] = order_ref
        state["payment_amount_kobo"] = amount_kobo
        self.session_manager.update_session_state(session_id, state)

        return self.whatsapp_service.create_text_message(session_id, payment_message)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_payment_amount(self, raw_response: str) -> int:
        """Extract kobo amount from [PAYMENT_READY:amount=XXXXX] tag."""
        match = re.search(r'\[PAYMENT_READY:amount=(\d+)\]', raw_response)
        if match:
            return int(match.group(1))
        return 0