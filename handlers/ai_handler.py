import logging
import re
import random
from typing import Dict
from datetime import datetime

from .base_handler import BaseHandler
from services.ai_service import AIService, set_db
from services.payment_service import PaymentService
from db_manager import DBManager

logger = logging.getLogger(__name__)

MENU_IMAGE_URL = "https://eventio.africa/wp-content/uploads/2026/04/lola-general-purpose.jpg"


class AIHandler(BaseHandler):
    """
    Conversational order handler for Makinde Kitchen.

    Flow:
      1. _handle_start()        — welcome + menu image (new session only)
      2. _handle_returning()    — "welcome back" message (returning session)
      3. handle_ai_chat_state() — all messages go to the AI agent
      4. Payment tag detected   — strips tag, generates Paystack link, sends payment message
    """

    def __init__(self, config, session_manager, data_manager, whatsapp_service):
        super().__init__(config, session_manager, data_manager, whatsapp_service)

        self.ai_service      = AIService(config, data_manager)
        self.payment_service = PaymentService(config)
        self.db              = DBManager(config)
        self.ai_enabled      = self.ai_service.ai_enabled

        # Give the AI's order status tool access to the DB
        set_db(self.db)

        if not self.ai_enabled:
            logger.warning("AIHandler: AI disabled — AIService could not be initialised.")
        else:
            logger.info("AIHandler: Makinde Kitchen order bot ready.")

    # ── Public entry points ───────────────────────────────────────────────────

    def handle_ai_chat_state(self, state: Dict, message: str, original_message: str, session_id: str) -> Dict:
        """Handle all incoming messages through the AI agent."""
        logger.info(f"AIHandler: message from {session_id}: '{original_message[:80]}'")
        return self._process_message(state, session_id, original_message)

    def handle_ai_menu_state(self, state: Dict, message: str, original_message: str, session_id: str) -> Dict:
        if message in ("ai_chat", "start_ai_chat", "initial_greeting"):
            return self._handle_start(state, session_id)
        if message in ("back_to_main", "menu"):
            return self.handle_back_to_main(state, session_id)
        return self._handle_start(state, session_id)

    # ── Welcome ───────────────────────────────────────────────────────────────

    def _handle_start(self, state: Dict, session_id: str, user_message: str = None) -> Dict:
        """
        New session entry point — send welcome text + menu image.
        Resets conversation history.
        """
        state["current_state"]        = "ai_chat"
        state["current_handler"]      = "ai_handler"
        state["conversation_history"] = []
        state["order_ref"]            = f"MK{random.randint(10000, 99999)}"
        state["welcome_sent"]         = True
        state["is_returning"]         = False
        self.session_manager.update_session_state(session_id, state)

        phone_number = state.get("phone_number", session_id)
        user_name    = state.get("user_name", "")
        greeting_name = f", {user_name}" if user_name and user_name not in ("Guest", "") else ""

        welcome_text = (
            f"Hi{greeting_name}! Welcome to Makinde Kitchen. 🍛\n\n"
            "Here's our menu — what would you like to order today?"
        )

        # Save the trigger message from the user (hi/hello/menu etc)
        if user_message:
            self.db.save_message(
                phone_number=phone_number,
                role='user',
                message=user_message,
                customer_name=user_name if user_name not in ("Guest", "") else None,
            )

        # Save the welcome response from the bot
        self.db.save_message(
            phone_number=phone_number,
            role='assistant',
            message=welcome_text,
            customer_name=user_name if user_name not in ("Guest", "") else None,
        )

        self.whatsapp_service.send_message(
            self.whatsapp_service.create_text_message(session_id, welcome_text)
        )

        try:
            self.whatsapp_service.send_image_message(session_id, MENU_IMAGE_URL, caption="")
        except Exception as e:
            logger.warning(f"Could not send menu image for {session_id}: {e}")

        return {"status": "welcome_sent"}

    def _handle_returning(self, state: Dict, session_id: str, original_message: str) -> Dict:
        """
        Returning session — don't show menu, pass message straight to AI
        with is_returning=True so it greets appropriately.
        """
        state["is_returning"] = True
        self.session_manager.update_session_state(session_id, state)
        return self._process_message(state, session_id, original_message, is_returning=True)

    # ── Core message processing ───────────────────────────────────────────────

    def _process_message(
        self,
        state: Dict,
        session_id: str,
        user_message: str,
        is_returning: bool = False,
    ) -> Dict:
        """Send message to AI agent. Handle payment trigger if returned."""
        phone_number         = state.get("phone_number", session_id)
        user_name            = state.get("user_name", "Customer")
        conversation_history = state.get("conversation_history", [])
        order_ref            = state.get("order_ref") or f"MK{random.randint(10000, 99999)}"
        is_returning         = is_returning or state.get("is_returning", False)

        if not self.ai_enabled:
            return self.whatsapp_service.create_text_message(
                session_id,
                "Sorry, our ordering system is currently unavailable. Please try again shortly!"
            )

        try:
            # Save user message to DB
            self.db.save_message(
                phone_number=phone_number,
                role='user',
                message=user_message,
                customer_name=user_name,
                order_id=state.get('db_order_id')
            )

            clean_response, payment_triggered, order_ref, raw_response = \
                self.ai_service.generate_order_response(
                    user_message=user_message,
                    conversation_history=conversation_history,
                    phone_number=phone_number,
                    user_name=user_name,
                    session_id=session_id,
                    order_ref=order_ref,
                    is_returning=is_returning,
                )

            state["order_ref"]   = order_ref
            state["is_returning"] = True  # all subsequent messages are returning

            # Update conversation history
            conversation_history.append({
                "user":      user_message,
                "assistant": clean_response,
                "timestamp": datetime.now().isoformat()
            })
            if len(conversation_history) > 10:
                conversation_history = conversation_history[-10:]
            state["conversation_history"] = conversation_history
            self.session_manager.update_session_state(session_id, state)

            # ── Payment flow ──────────────────────────────────────────────────
            if payment_triggered and raw_response:
                result = self._handle_payment_trigger(
                    state, session_id, clean_response, raw_response,
                    phone_number, user_name, order_ref
                )
                self.db.save_message(
                    phone_number=phone_number,
                    role='assistant',
                    message=clean_response,
                    order_id=state.get('db_order_id')
                )
                return result

            # ── Normal response ───────────────────────────────────────────────
            self.db.save_message(
                phone_number=phone_number,
                role='assistant',
                message=clean_response,
                order_id=state.get('db_order_id')
            )
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
        Intercepts [PAYMENT_READY:amount=XXXX].
        1. Sends order summary
        2. Saves order to DB
        3. Generates Paystack link
        4. Sends payment message
        """
        amount_kobo = self._extract_payment_amount(raw_response)

        if not amount_kobo:
            logger.error(f"Payment tag found but could not parse amount for {session_id}.")
            return self.whatsapp_service.create_text_message(session_id, clean_response)

        # 1. Send the order summary
        self.whatsapp_service.send_message(
            self.whatsapp_service.create_text_message(session_id, clean_response)
        )

        # 2. Save order to DB
        amount_naira = amount_kobo // 100
        delivery_fee = 0 if amount_naira > 5000 else 500
        subtotal     = amount_naira - delivery_fee

        db_order_id = self.db.create_order(
            order_ref=order_ref,
            phone_number=phone_number,
            delivery_address=state.get('delivery_address', ''),
            subtotal=subtotal,
            delivery_fee=delivery_fee,
            total=amount_naira,
            customer_name=user_name
        )
        if db_order_id:
            state['db_order_id'] = db_order_id
            self.session_manager.update_session_state(session_id, state)
            logger.info(f"Order saved to DB: id={db_order_id}, ref={order_ref}")

            # 2b. Parse and save order items
            items = self._parse_order_items(raw_response)
            if items:
                self.db.save_order_items(db_order_id, items)
                logger.info(f"Saved {len(items)} order items for order_id={db_order_id}")
            else:
                logger.warning(f"No ORDER_ITEMS tag found in response for {session_id}")

        # 3. Generate Paystack link
        payment_url = None
        try:
            customer_email = self.payment_service.generate_customer_email(phone_number, user_name)
            payment_url    = self.payment_service.create_payment_link(
                amount=amount_kobo,
                email=customer_email,
                reference=order_ref,
                customer_name=user_name,
                customer_phone=phone_number,
                metadata={"order_ref": order_ref, "channel": "whatsapp"},
            )
        except Exception as e:
            logger.error(f"Paystack link generation failed for {session_id}: {e}", exc_info=True)

        # 4. Send payment message
        if payment_url:
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
                f"We could not generate your payment link right now.\n"
                f"Please contact us to complete your order.\n"
                f"Ref: {order_ref}"
            )

        state["payment_pending"]     = True
        state["payment_ref"]         = order_ref
        state["payment_amount_kobo"] = amount_kobo
        self.session_manager.update_session_state(session_id, state)

        return self.whatsapp_service.create_text_message(session_id, payment_message)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_payment_amount(self, raw_response: str) -> int:
        match = re.search(r'\[PAYMENT_READY:amount=(\d+)\]', raw_response)
        return int(match.group(1)) if match else 0

    def _parse_order_items(self, raw_response: str) -> list:
        """
        Parse [ORDER_ITEMS:name=X,qty=Y,price=Z,subtotal=W;name=...] tag.
        Returns list of dicts ready for db.save_order_items().
        """
        match = re.search(r'\[ORDER_ITEMS:([^\]]+)\]', raw_response)
        if not match:
            return []

        items = []
        try:
            entries = match.group(1).split(';')
            for entry in entries:
                entry = entry.strip()
                if not entry:
                    continue
                fields = {}
                for part in entry.split(','):
                    if '=' in part:
                        key, _, val = part.partition('=')
                        fields[key.strip()] = val.strip()

                name     = fields.get('name', '').strip()
                qty      = int(fields.get('qty', 1))
                price    = int(fields.get('price', 0))
                subtotal = int(fields.get('subtotal', price * qty))

                if name and price > 0:
                    items.append({
                        'name':     name,
                        'price':    price,
                        'quantity': qty,
                        'subtotal': subtotal,
                    })
        except Exception as e:
            logger.error(f"_parse_order_items error: {e} | raw: {raw_response[-300:]}")

        return items