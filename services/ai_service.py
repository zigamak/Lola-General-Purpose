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

MENU_IMAGE_URL = "https://eventio.africa/wp-content/uploads/2026/04/lola-general-purpose.jpg"

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
- ₦500 flat delivery fee for orders ₦5,000 and below

OPERATING HOURS
- Monday to Saturday: 10am - 9pm
- Sunday: 12pm - 7pm
"""

GEMINI_MODELS = [
    "gemini-2.5-flash",

]

# Injected at runtime by AIHandler so the order status tool can query the DB
_db_instance = None


def set_db(db):
    """Called by AIHandler after init to give the AI tools DB access."""
    global _db_instance
    _db_instance = db


class AIService:
    """
    Conversational AI ordering service for Makinde Kitchen.
    Uses Google Gemini with lazy runtime fallback.

    Payment trigger: AI appends [PAYMENT_READY:amount=XXXXX] (in kobo)
    at the end of its response when ready to collect payment.
    AIHandler intercepts this tag, strips it, and generates the Paystack link.
    """

    def __init__(self, config, data_manager):
        load_dotenv()
        self.data_manager = data_manager
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
        """Build one AgentExecutor per model — no API calls at startup."""
        tools = [self._create_menu_tool(), self._create_order_status_tool()]
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
                    max_iterations=4
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

    def _create_order_status_tool(self):
        @tool
        def check_order_status(phone_number: str) -> str:
            """
            Check the status of the most recent order for a customer.
            Use this when a customer asks about their order, delivery status,
            or says something like 'where is my order', 'order update', 'has my food shipped'.
            Pass the customer's phone number exactly as provided in the system context.
            """
            if not _db_instance:
                return "Order status unavailable right now."
            try:
                row = _db_instance._execute(
                    """SELECT o.order_ref, o.status, o.payment_status, o.total,
                              o.delivery_address, o.created_at
                       FROM orders o
                       JOIN customers c ON o.customer_id = c.id
                       WHERE c.phone_number = %s
                       ORDER BY o.created_at DESC
                       LIMIT 1""",
                    (phone_number,),
                    fetch='one'
                )
                if not row:
                    return "No orders found for this customer."

                status_labels = {
                    "pending":      "Order received, awaiting payment",
                    "payment_sent": "Payment link sent — awaiting payment",
                    "paid":         "Payment confirmed",
                    "preparing":    "Your order is being prepared in the kitchen",
                    "on_the_way":   "Your order is on its way to you",
                    "delivered":    "Order delivered",
                    "cancelled":    "Order cancelled",
                }
                payment_labels = {
                    "unpaid": "Payment not yet received",
                    "paid":   "Payment confirmed",
                    "failed": "Payment failed",
                }

                status_text  = status_labels.get(row['status'], row['status'])
                payment_text = payment_labels.get(row['payment_status'], row['payment_status'])
                total        = f"₦{row['total']:,}"
                created      = row['created_at'].strftime('%d %b %Y, %H:%M') if row['created_at'] else "—"

                return (
                    f"Order Ref: {row['order_ref']}\n"
                    f"Status: {status_text}\n"
                    f"Payment: {payment_text}\n"
                    f"Total: {total}\n"
                    f"Delivery Address: {row['delivery_address'] or 'Not recorded'}\n"
                    f"Placed: {created}"
                )
            except Exception as e:
                logger.error(f"check_order_status tool error: {e}")
                return "Could not retrieve order status right now."

        return check_order_status

    def _get_system_prompt(self) -> str:
        return f"""You are Lola, the official WhatsApp ordering assistant for Makinde Kitchen — a Lagos-based Nigerian comfort food brand.

Your job is to take food orders, help customers check their order status, and handle common questions. You are warm, professional, and concise.

---

THE MENU:
{MENU_TEXT}

---

SESSION AWARENESS — VERY IMPORTANT:
You will be told in the system context whether this is a NEW SESSION or a RETURNING SESSION.

If the context says RETURNING SESSION:
- Do NOT show the menu or welcome message
- Greet them warmly and ask: "Welcome back! Would you like to check on your order or place a new one?"
- If they ask for an order update, use the check_order_status tool with their phone number
- If they want to order again, proceed with the normal order flow

If the context says NEW SESSION:
- Greet them and show the menu categories as normal

---

ORDER FLOW — follow these steps in order:

STEP 1 — MENU & BROWSING
Show the categories:
  1. Rice & Grains
  2. Swallows
  3. Soups
  4. Small Chops & Snacks
  5. Proteins (Add-ons)
  6. Drinks
Let them pick a category or just tell you what they want.

STEP 2 — TAKING THE ORDER
- Confirm each item and quantity as the customer selects
- After each addition ask "Anything else?" or "Would you like to add anything?"
- When they say they are done, show a clear order summary like this:

Here is your order so far:
[item name] x[qty] — ₦[subtotal]
[item name] x[qty] — ₦[subtotal]

Subtotal: ₦[subtotal]

Is that all, or would you like to add anything else?

- Only move to STEP 3 when they confirm they are done (e.g. "that's all", "no", "done", "proceed")
- If they want to change something, update the order and show the summary again

STEP 3 — DELIVERY ADDRESS
Ask ONLY for their delivery address:
"What is your delivery address?"

STEP 4 — PAYMENT TRIGGER
Once you have the delivery address, send the final order summary and trigger payment.
Use this EXACT format — each item MUST be on its own separate line:

Order Summary

Order Ref: [use the ref from context]
Name: [customer name if provided, else omit this line]
Phone: [customer phone if provided, else omit this line]
Delivery Address: [their address]

Items:
[item name] x[qty] — ₦[subtotal for that item]
[item name] x[qty] — ₦[subtotal for that item]

Subtotal: ₦[subtotal]
Delivery: ₦[500 or Free]
Total: ₦[grand total]

A payment link will be sent to you now. Please complete payment within 10 minutes to confirm your order.
[ORDER_ITEMS:name=[item name],qty=[qty],price=[unit price in naira],subtotal=[subtotal in naira];name=[item name],qty=[qty],price=[unit price in naira],subtotal=[subtotal in naira]]
[PAYMENT_READY:amount=[grand total in kobo — multiply naira by 100]]

CRITICAL FORMATTING RULES:
- Every item MUST be on its own line — never run two items together
- The amount in [PAYMENT_READY:amount=] must be in KOBO (naira x 100)
- Example: ₦3,500 = [PAYMENT_READY:amount=350000]
- [ORDER_ITEMS] must list every item separated by semicolons, all on one line
- Example with 2 items: [ORDER_ITEMS:name=Jollof Rice,qty=2,price=2500,subtotal=5000;name=Zobo (500ml),qty=1,price=800,subtotal=800]
- Place [ORDER_ITEMS] on the second to last line, [PAYMENT_READY] on the very last line
- Never show either tag to the customer — both are stripped by the system

---

ORDER STATUS CHECKS:
When a customer asks about their order ("where is my order", "has my food left", "order update", "track my order"):
- Use the check_order_status tool with their phone number from context
- Present the result clearly and warmly
- If the status is "preparing" or "on_the_way", reassure them
- If payment is still unpaid, let them know and offer to resend the payment link

---

DELIVERY RULES:
- Orders above ₦5,000: free delivery
- Orders ₦5,000 and below: ₦500 flat delivery fee

OPERATING HOURS:
- Monday to Saturday: 10am - 9pm
- Sunday: 12pm - 7pm
- Outside hours: acknowledge warmly, take a pre-order, confirm it will be processed when kitchen opens

---

FAQ:
If asked about delivery areas: "We deliver across Lagos — Surulere, Lagos Island, VI, Yaba, Lekki and surrounding areas."
If asked about minimum order: "No minimum! Delivery is free for orders above ₦5,000. Below that, a flat ₦500 delivery fee applies."
If asked about payment: "We accept card and bank transfer via Paystack — safe and instant."
If asked about customisation: "Of course! Tell me your preferences — no pepper, extra sauce, no onions — and we will make it happen."
If asked about advance orders: "Yes! Tell me your preferred date and time. Please order at least 3 hours in advance."

---

COMPLAINT / ESCALATION:
If a customer reports a missing order, refund, or is upset:
"I am sorry to hear that. Please contact our support team directly:
WhatsApp/Call: +2348000000000
They will resolve this for you as quickly as possible."

---

TONE:
- Warm, clear, professional
- Light Nigerian expressions are fine (e.g. "No wahala") but keep it measured
- Plain text only — no markdown asterisks, no bullet dashes in order summary
- Short responses — do not over-explain
- Never invent prices, delivery times, or order history"""

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
        is_returning: bool = False,
    ) -> Tuple[str, bool, str, str]:
        """
        Generate a conversational order response.

        Returns:
            (clean_response, payment_triggered, order_ref, raw_response)
        """
        if not user_message or not isinstance(user_message, str):
            return "Hey! What would you like to order today?", False, None, None

        if not self.ai_enabled or not self._executors:
            return (
                "Sorry, our ordering system is having a moment. Please try again shortly!",
                False, None, None
            )

        if not order_ref:
            order_ref = f"MK{random.randint(10000, 99999)}"

        try:
            chat_history = []

            # Customer context — always injected as first turn
            real_phone = phone_number if phone_number and str(phone_number).strip() not in ("", "N/A", "None") else None
            real_name  = user_name if user_name and user_name.strip() not in ("", "Guest", "Customer", "None") else None

            phone_line = f"Phone: {real_phone}" if real_phone else "Phone: [omit from summary]"
            name_line  = f"Name: {real_name}"  if real_name  else "Name: [omit from summary]"
            session_line = "RETURNING SESSION" if is_returning else "NEW SESSION"

            context_note = (
                f"[SYSTEM — customer details, copy exactly, never invent:\n"
                f"Order Ref: {order_ref}\n"
                f"{name_line}\n"
                f"{phone_line}\n"
                f"Session: {session_line}]"
            )
            chat_history.append(("human", context_note))
            chat_history.append(("ai", "Understood. I will use only these exact details, omit lines marked [omit], and follow the session type correctly."))

            # Append conversation history
            if conversation_history:
                for exchange in conversation_history[-8:]:
                    chat_history.append(("human", exchange.get("user", "")))
                    chat_history.append(("ai",    exchange.get("assistant", "")))

            raw_response = self._invoke_with_fallback({
                "input":        user_message,
                "chat_history": chat_history,
            })

            # Detect and strip both tags from customer-facing response
            payment_triggered = False
            clean_response    = raw_response

            payment_match = re.search(r'\[PAYMENT_READY:amount=(\d+)\]', raw_response)
            if payment_match:
                payment_triggered = True
                # Strip both tags — customer never sees them
                clean_response = re.sub(r'\[ORDER_ITEMS:[^\]]+\]', '', clean_response).strip()
                clean_response = re.sub(r'\[PAYMENT_READY:amount=\d+\]', '', clean_response).strip()

            logger.info(
                f"[{self.active_model}] [{session_id}] returning={is_returning} "
                f"payment={payment_triggered}: {clean_response[:100]}"
            )
            return clean_response, payment_triggered, order_ref, raw_response

        except Exception as e:
            logger.error(f"All models failed [{session_id}]: {e}", exc_info=True)
            return (
                "Something went wrong on our end. Try again or send menu to browse!",
                False, None, None
            )