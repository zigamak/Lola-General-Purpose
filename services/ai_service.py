import logging
import re
import random
from typing import Dict, List, Tuple
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

MENU_IMAGE_URL = "https://eventio.africa/wp-content/uploads/2026/03/chowder.ng_.jpg"

MENU_TEXT = """
MAKINDE KITCHEN — Full Menu

RICE & GRAINS
- Jollof Rice — ₦2,500 (party-style smoky jollof, cooked to order)
- Fried Rice — ₦2,800 (mixed veggies, liver and prawns)
- Coconut Rice — ₦3,000 (fragrant coconut base with assorted protein)
- White Rice + Stew — ₦2,000 (plain rice with rich tomato beef stew)

SWALLOWS
- Pounded Yam — ₦1,500
- Eba (Garri) — ₦800 (regular or yellow, soft or firm on request)
- Amala — ₦1,200 (dark yam flour swallow)
- Wheat (Semovita) — ₦1,000

SOUPS
- Egusi Soup — ₦2,500 (ground melon with assorted meat and stockfish)
- Efo Riro — ₦2,500 (rich Yoruba vegetable soup with assorted)
- Banga Soup — ₦3,000 (Delta-style palm nut soup with catfish)
- Oha Soup — ₦2,800 (Igbo-style oha leaf soup with cocoyam)
- Okro Soup — ₦2,200 (fresh cut okro with assorted meat)

SMALL CHOPS & SNACKS
- Small Chops Platter — ₦5,500 (puff puff, spring rolls, samosa, peppered gizzard — serves 4)
- Puff Puff (10 pcs) — ₦1,000
- Spring Rolls (5 pcs) — ₦1,500
- Peppered Gizzard — ₦2,000 (100g grilled and peppered chicken gizzard)
- Moin Moin — ₦700 (steamed bean pudding)

PROTEINS (Add-ons)
- Chicken (1 piece) — ₦1,500 (grilled or fried)
- Beef (large cut) — ₦1,500 (peppered or plain)
- Fish (1 piece) — ₦2,000 (catfish or titus)
- Goat Meat (3 pcs) — ₦2,500
- Shrimp (50g) — ₦2,000

DRINKS
- Zobo (500ml) — ₦800
- Kunu (500ml) — ₦700
- Chapman (can) — ₦1,200
- Bottled Water (75cl) — ₦300

DELIVERY
- Free delivery for orders above ₦5,000
- ₦500 flat delivery fee for orders below ₦5,000

OPERATING HOURS
- Monday to Saturday: 10am - 9pm
- Sunday: 12pm - 7pm
"""

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
]


class AIService:
    """
    Conversational AI ordering service for Makinde Kitchen.
    Uses Google Gemini with lazy runtime fallback — no API calls at startup.

    Payment trigger: when the AI is ready to send a payment link it appends
    [PAYMENT_READY:amount=XXXXX] at the END of its response. The ai_handler
    intercepts this tag, strips it, generates the Paystack link, and sends
    the payment message separately.
    """

    def __init__(self, config, data_manager):
        load_dotenv()
        self.data_manager = data_manager  # available for future order saving
        self.active_model = None
        self._executors = {}
        self.ai_enabled = False

        try:
            if isinstance(config, dict):
                self.gemini_api_key = config.get("gemini_api_key") or config.get("google_api_key")
            else:
                self.gemini_api_key = (
                    getattr(config, 'GEMINI_API_KEY', None) or
                    getattr(config, 'GOOGLE_API_KEY', None)
                )

            if not self.gemini_api_key:
                logger.error("GEMINI_API_KEY is missing.")
                return

            masked = self.gemini_api_key[:8] + "..." + self.gemini_api_key[-4:]
            logger.info(f"Makinde Kitchen AIService — Gemini key: {masked}")

            self._build_agents()

            if self._executors:
                self.ai_enabled = True
                logger.info(f"AIService ready. Models: {list(self._executors.keys())}")
            else:
                logger.error("AIService: No agents could be built.")

        except Exception as e:
            logger.error(f"AIService init error: {e}", exc_info=True)

    # ── Agent construction ────────────────────────────────────────────────────

    def _build_agents(self):
        """Build one AgentExecutor per model — pure object construction, zero API calls."""
        tools = [self._create_menu_tool()]
        prompt = ChatPromptTemplate.from_messages([
            ("system", self._get_system_prompt()),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        for model_name in GEMINI_MODELS:
            try:
                llm = ChatGoogleGenerativeAI(
                    model=model_name,
                    temperature=0.7,
                    google_api_key=self.gemini_api_key,
                    convert_system_message_to_human=False,
                )
                agent = create_tool_calling_agent(llm, tools, prompt)
                self._executors[model_name] = AgentExecutor(
                    agent=agent,
                    tools=tools,
                    verbose=False,
                    handle_parsing_errors=True,
                    max_iterations=3
                )
                logger.info(f"Agent built for: {model_name}")
            except Exception as e:
                logger.warning(f"Could not build agent for '{model_name}': {e}")

    def _create_menu_tool(self):
        @tool
        def get_menu() -> str:
            """Get the full Makinde Kitchen menu with all items and prices."""
            return MENU_TEXT
        return get_menu

    def _get_system_prompt(self) -> str:
        return f"""You are Lola, the official WhatsApp ordering assistant for Makinde Kitchen — a Lagos-based Nigerian comfort food brand.

Your only job is to help customers browse the menu, place food orders, collect their delivery details, and trigger payment. You are warm, professional, and concise.

---

THE MENU:
{MENU_TEXT}

---

ORDER FLOW — follow these steps in order:

STEP 1 — MENU & BROWSING
When a customer first messages or asks to see the menu, show the categories:
  1. Rice & Grains
  2. Swallows
  3. Soups
  4. Small Chops & Snacks
  5. Proteins (Add-ons)
  6. Drinks
Let them pick a category or just tell you what they want.

STEP 2 — TAKING THE ORDER
- Confirm each item and quantity as the customer selects
- Ask if they want anything else
- When they say they are done or have nothing to add, go straight to STEP 3
- Do NOT show an order summary here, do NOT ask them to reply YES or EDIT

STEP 3 — DELIVERY ADDRESS
Ask ONLY for their delivery address. Nothing else. Just:
"What is your delivery address?"

STEP 4 — PAYMENT TRIGGER
Once you have the delivery address, send the final order summary and trigger payment.
Use this EXACT format — each item MUST be on its own separate line with a line break between them:

Order Summary

Order Ref: [use the ref from context]
Name: [customer name if provided, else omit this line]
Phone: [customer phone if provided, else omit this line]
Delivery Address: [their address]

Items:
[item name] x[qty] — ₦[subtotal for that item]
[item name] x[qty] — ₦[subtotal for that item]
[item name] x[qty] — ₦[subtotal for that item]

Subtotal: ₦[subtotal]
Delivery: ₦[500 or Free]
Total: ₦[grand total]

A payment link will be sent to you now. Please complete payment within 10 minutes to confirm your order.
[PAYMENT_READY:amount=[grand total in kobo, i.e. multiply naira by 100]]

CRITICAL FORMATTING RULES FOR THE ORDER SUMMARY:
- Every single item MUST be on its own line — never combine two items on one line
- Put a newline character after every item line without exception
- The amount in [PAYMENT_READY:amount=] must be in KOBO (multiply the naira total by 100)
- Example: ₦3,500 total = [PAYMENT_READY:amount=350000]
- Always place the tag on the very last line with nothing after it
- Never show the tag text to the customer — it is stripped by the system before sending

---

DELIVERY RULES:
- Orders above ₦5,000: free delivery
- Orders ₦5,000 and below: ₦500 flat delivery fee
- Add the delivery fee to the total before putting it in the payment tag

OPERATING HOURS:
- Monday to Saturday: 10am - 9pm
- Sunday: 12pm - 7pm
- If a customer messages outside hours, acknowledge it warmly, take their pre-order, and let them know it will be processed when the kitchen opens

---

FAQ RESPONSES — handle these automatically:

If asked about delivery areas: "We deliver across Lagos — Surulere, Lagos Island, VI, Yaba, Lekki and surrounding areas."
If asked about minimum order: "No minimum order! Delivery is free for orders above ₦5,000. Below that, a flat ₦500 delivery fee applies."
If asked about payment: "We accept card and bank transfer via Paystack — completely safe and instant."
If asked about allergies or customisation: "Of course! Just tell me your preferences — no pepper, extra sauce, no onions, more protein — and we will make it happen."
If asked about advance orders: "Yes! Tell me your preferred date and time. Please order at least 3 hours in advance."
If asked to order for someone else: "No problem! Just give me the delivery address and recipient name."

---

COMPLAINT / ESCALATION:
If a customer reports a missing order, asks for a refund, or is upset:
"I am sorry to hear that. For issues with existing orders, please contact our support team directly:
WhatsApp/Call: +2348000000000
They will resolve this for you as quickly as possible."

---

TONE:
- Warm, clear, and professional
- Light Nigerian expressions are fine (e.g. "No wahala") but keep it measured
- Plain text only — no markdown asterisks for bold, no bullet dashes in the order summary
- Short responses — do not over-explain
- Never invent prices, delivery times, or order history
- If unsure, be honest and direct the customer to support"""

    # ── Inference ─────────────────────────────────────────────────────────────

    def _invoke_with_fallback(self, input_data: dict) -> str:
        """Try each model in order. Stick to the last working one."""
        model_order = GEMINI_MODELS[:]
        if self.active_model and self.active_model in model_order:
            model_order.remove(self.active_model)
            model_order.insert(0, self.active_model)

        for model_name in model_order:
            executor = self._executors.get(model_name)
            if not executor:
                continue
            try:
                result = executor.invoke(input_data)
                ai_response = result.get("output", "").strip()
                if not ai_response:
                    raise ValueError("Empty response")
                if self.active_model != model_name:
                    logger.info(f"Active model switched to: {model_name}")
                    self.active_model = model_name
                return ai_response
            except Exception as e:
                logger.warning(f"'{model_name}' failed: {str(e)[:80]}. Trying next...")
                continue

        raise RuntimeError("All Gemini model fallbacks exhausted.")

    def generate_order_response(
        self,
        user_message: str,
        conversation_history: List[Dict] = None,
        phone_number: str = None,
        user_name: str = None,
        session_id: str = None,
        order_ref: str = None,
    ) -> Tuple[str, bool, str, str]:
        """
        Generate a conversational order response.

        Returns:
            Tuple of (ai_response, payment_triggered, order_ref, raw_response)
            - ai_response: cleaned text to send to the customer (payment tag stripped)
            - payment_triggered: True if [PAYMENT_READY:amount=...] was found
            - order_ref: the order ref injected into context
            - raw_response: full response including tag (for handler to parse amount)
        """
        if not user_message or not isinstance(user_message, str):
            return "Hey! What would you like to order today? 😊", False, None, None

        if not self.ai_enabled or not self._executors:
            return (
                "Sorry, our ordering system is having a moment. Please try again shortly!",
                False, None, None
            )

        # Use passed ref or generate a new one
        if not order_ref:
            order_ref = f"MK{random.randint(10000, 99999)}"

        try:
            chat_history = []

            # Inject customer context as the first turn so the AI always has it
            real_phone = phone_number if phone_number and str(phone_number).strip() not in ("", "N/A", "None") else None
            real_name = user_name if user_name and user_name.strip() not in ("", "Guest", "Customer", "None") else None

            phone_line = f"Phone: {real_phone}" if real_phone else "Phone: [omit from summary]"
            name_line = f"Name: {real_name}" if real_name else "Name: [omit from summary]"

            context_note = (
                f"[SYSTEM — use these exact details in the order summary, never invent them:\n"
                f"Order Ref: {order_ref}\n"
                f"{name_line}\n"
                f"{phone_line}]"
            )
            chat_history.append(("human", context_note))
            chat_history.append(("ai", "Understood. I will use only these exact details and omit any line marked [omit]."))

            # Append conversation history
            if conversation_history:
                for exchange in conversation_history[-8:]:
                    chat_history.append(("human", exchange.get("user", "")))
                    chat_history.append(("ai", exchange.get("assistant", "")))

            raw_response = self._invoke_with_fallback({
                "input": user_message,
                "chat_history": chat_history
            })

            # Detect and strip the payment trigger tag
            payment_triggered = False
            clean_response = raw_response

            payment_match = re.search(r'\[PAYMENT_READY:amount=(\d+)\]', raw_response)
            if payment_match:
                payment_triggered = True
                # Strip the tag from the customer-facing message
                clean_response = re.sub(r'\[PAYMENT_READY:amount=\d+\]', '', raw_response).strip()

            logger.info(
                f"[{self.active_model}] [{session_id}] payment_triggered={payment_triggered}: "
                f"{clean_response[:100]}"
            )
            return clean_response, payment_triggered, order_ref, raw_response

        except Exception as e:
            logger.error(f"All models failed [{session_id}]: {e}", exc_info=True)
            return (
                "Something went wrong on our end. Try again or send *menu* to browse!",
                False, None, None
            )