import logging
import re
import random
from typing import Dict, List, Tuple, Optional
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

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
    Conversational AI ordering service — multi-vendor, platform-agnostic.
    Uses Google Gemini with lazy runtime model fallback.

    The system prompt and menu are built dynamically per request using
    vendor details passed in from session state, so the same AIService
    instance serves any vendor without restart.

    Payment trigger: AI appends [PAYMENT_READY:amount=XXXXX] (in kobo)
    at the end of its response when ready to collect payment.
    AIHandler intercepts this tag, strips it, and generates the Paystack link.
    """

    def __init__(self, config, data_manager):
        load_dotenv()
        self.data_manager = data_manager
        self.active_model = None
        self.ai_enabled   = False

        # We keep one LLM instance per model but rebuild the agent per
        # request (cheap — just wires prompt + tools) so the system prompt
        # can be dynamic without re-instantiating the LLM.
        self._llms: Dict[str, ChatGoogleGenerativeAI] = {}

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
            logger.info(f"Lola AIService — Gemini key: {masked}")

            self._build_llms()

            if self._llms:
                self.ai_enabled = True
                logger.info(f"AIService ready. Models: {list(self._llms.keys())}")
            else:
                logger.error("AIService: No LLMs could be built.")

        except Exception as e:
            logger.error(f"AIService init error: {e}", exc_info=True)

    # ── LLM construction ──────────────────────────────────────────────────────

    def _build_llms(self):
        """Instantiate one LLM per model — no API calls at startup."""
        for model_name in GEMINI_MODELS:
            try:
                self._llms[model_name] = ChatGoogleGenerativeAI(
                    model=model_name,
                    temperature=0.7,
                    google_api_key=self.gemini_api_key,
                    convert_system_message_to_human=False,
                )
                logger.info(f"LLM built for: {model_name}")
            except Exception as e:
                logger.warning(f"Could not build LLM for '{model_name}': {e}")

    def _build_executor(
        self,
        system_prompt: str,
        vendor_menu: str,
        model_name: str
    ) -> Optional[AgentExecutor]:
        """
        Build an AgentExecutor with a dynamic system prompt.
        Called fresh per request so vendor context is always current.
        """
        llm = self._llms.get(model_name)
        if not llm:
            return None

        tools = [
            self._create_menu_tool(vendor_menu),
            self._create_order_status_tool(),
        ]

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        try:
            agent = create_tool_calling_agent(llm, tools, prompt)
            return AgentExecutor(
                agent=agent,
                tools=tools,
                verbose=False,
                handle_parsing_errors=True,
                max_iterations=4,
            )
        except Exception as e:
            logger.warning(f"Could not build executor for '{model_name}': {e}")
            return None

    # ── Tools ─────────────────────────────────────────────────────────────────

    def _create_menu_tool(self, vendor_menu: str):
        """Menu tool — returns the vendor's menu text (closure over vendor_menu)."""
        menu_snapshot = vendor_menu  # captured at executor-build time

        @tool
        def get_menu() -> str:
            """Get the full menu with all items and prices for the current vendor."""
            return menu_snapshot if menu_snapshot else "Menu not available right now."

        return get_menu

    def _create_order_status_tool(self):
        @tool
        def check_order_status(phone_number: str) -> str:
            """
            Check the status of the most recent order for a customer.
            Use when a customer asks about their order, delivery, or says
            'where is my order', 'order update', 'has my food shipped'.
            Pass the customer's phone number exactly as provided in context.
            """
            if not _db_instance:
                return "Order status unavailable right now."
            try:
                row = _db_instance._execute(
                    """
                    SELECT o.order_ref, o.status, o.payment_status, o.total,
                           o.delivery_address, o.created_at,
                           v.name AS vendor_name
                    FROM orders o
                    JOIN customers c ON o.customer_id = c.id
                    LEFT JOIN vendors v ON o.vendor_id = v.id
                    WHERE c.phone_number = %s
                    ORDER BY o.created_at DESC
                    LIMIT 1
                    """,
                    (phone_number,),
                    fetch='one'
                )
                if not row:
                    return "No orders found for this customer."

                status_labels = {
                    "pending":      "Order received, awaiting payment",
                    "payment_sent": "Payment link sent — awaiting payment",
                    "paid":         "Payment confirmed",
                    "preparing":    "Your order is being prepared",
                    "on_the_way":   "Your order is on its way",
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
                vendor_name  = row.get('vendor_name') or "the vendor"

                return (
                    f"Order Ref: {row['order_ref']}\n"
                    f"Vendor: {vendor_name}\n"
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

    # ── System prompt ─────────────────────────────────────────────────────────

    def _build_system_prompt(
        self,
        vendor_name: str,
        vendor_menu: str,
        delivery_fee: int = 500,
        free_delivery_min: int = 5000,
        opening_hours: str = "",
        delivery_areas: str = "",
        support_contact: str = "",
    ) -> str:
        """
        Build a fully dynamic system prompt for the current vendor.
        All previously hardcoded values are now injected at call time.
        """
        hours_text   = opening_hours  or "Please check with us for current operating hours."
        areas_text   = delivery_areas or "Our service area."
        support_text = support_contact or "Please contact us for support."

        free_or_fee = (
            f"Free delivery for orders above ₦{free_delivery_min:,}. "
            f"₦{delivery_fee:,} flat delivery fee for orders ₦{free_delivery_min:,} and below."
        )

        return f"""You are Lola, the official ordering assistant for {vendor_name}.

Your job is to take orders, help customers check their order status, and handle common questions. You are warm, professional, and concise.

---

THE MENU:
{vendor_menu}

---

SESSION AWARENESS — VERY IMPORTANT:
You will be told in the system context whether this is a NEW SESSION or a RETURNING SESSION.

If the context says RETURNING SESSION:
- Do NOT show the menu or welcome message
- Greet them warmly and ask: "Welcome back! Would you like to check on your order or place a new one?"
- If they ask for an order update, use the check_order_status tool with their phone number
- If they want to order again, proceed with the normal order flow

If the context says NEW SESSION:
- If the customer's first message already contains a clear order (e.g. "I want yam and egg sauce"), skip showing categories and go straight to STEP 2 — confirm the items and proceed.
- If the customer's first message is a greeting or vague (e.g. "hi", "what do you have"), then show the menu categories.

---

ORDER FLOW — follow these steps in order:

STEP 1 — MENU & BROWSING
Only show categories if the customer has NOT already told you what they want.
If they have already named items in their message, skip directly to STEP 2.
Otherwise, show the available categories and invite them to choose.

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
Vendor: {vendor_name}
Delivery Address: [their address]

Items:
[item name] x[qty] — ₦[subtotal for that item]
[item name] x[qty] — ₦[subtotal for that item]

Subtotal: ₦[subtotal]
Delivery: ₦[{delivery_fee} or Free]
Total: ₦[grand total]

A payment link will be sent to you now. Please complete payment within 10 minutes to confirm your order.
[ORDER_ITEMS:name=[item name],qty=[qty],price=[unit price in naira],subtotal=[subtotal in naira];name=[item name],qty=[qty],price=[unit price in naira],subtotal=[subtotal in naira]]
[PAYMENT_READY:amount=[grand total in kobo — multiply naira by 100]]

CRITICAL FORMATTING RULES:
- Every item MUST be on its own line — never run two items together
- The amount in [PAYMENT_READY:amount=] must be in KOBO (naira x 100)
- Example: ₦3,500 = [PAYMENT_READY:amount=350000]
- [ORDER_ITEMS] must list every item separated by semicolons, all on one line
- Place [ORDER_ITEMS] on the second to last line, [PAYMENT_READY] on the very last line
- Never show either tag to the customer — both are stripped by the system

---

ORDER STATUS CHECKS:
When a customer asks about their order ("where is my order", "has my food left", "order update", "track my order"):
- Use the check_order_status tool with their phone number from context
- Present the result clearly and warmly
- If payment is still unpaid, let them know and offer to resend the payment link

---

DELIVERY RULES:
{free_or_fee}

OPERATING HOURS:
{hours_text}
- Outside hours: acknowledge warmly, take a pre-order, confirm it will be processed when we open

---

FAQ:
If asked about delivery areas: "We deliver across {areas_text}."
If asked about minimum order: "No minimum! {free_or_fee}"
If asked about payment: "We accept card and bank transfer via Paystack — safe and instant."
If asked about customisation: "Of course! Tell me your preferences and we will make it happen."
If asked about advance orders: "Yes! Tell me your preferred date and time. Please order at least 3 hours in advance."

---

COMPLAINT / ESCALATION:
If a customer reports a missing order, refund, or is upset:
"I am sorry to hear that. Please contact our support team directly: {support_text}
They will resolve this for you as quickly as possible."

---

CHANGING VENDOR:
If the customer says anything like "I want to order from someone else", "change vendor",
"go back to vendor list", "pick a different restaurant", or similar — respond warmly,
tell them you will take them back to the vendor list, and append [CHANGE_VENDOR] on the
very last line of your response (never show this tag to the customer).
Example response:
"No problem! Let me take you back so you can choose another vendor."
[CHANGE_VENDOR]

---

TONE:
- Warm, clear, professional
- Light Nigerian expressions are fine (e.g. "No wahala") but keep it measured
- Plain text only — no markdown asterisks, no bullet dashes in order summary
- Short responses — do not over-explain
- Never invent prices, delivery times, or order history"""

    # ── Inference ─────────────────────────────────────────────────────────────

    def _invoke_with_fallback(
        self,
        input_data: dict,
        system_prompt: str,
        vendor_menu: str,
    ) -> str:
        """
        Try each model in order. Build executor dynamically per attempt.
        Sticks to the last working model for efficiency.
        """
        model_order = GEMINI_MODELS[:]
        if self.active_model and self.active_model in model_order:
            model_order.remove(self.active_model)
            model_order.insert(0, self.active_model)

        for model_name in model_order:
            executor = self._build_executor(system_prompt, vendor_menu, model_name)
            if not executor:
                continue
            try:
                result      = executor.invoke(input_data)
                ai_response = result.get("output", "").strip()

                # Agent went silent after a tool call — nudge it for a text reply
                if not ai_response:
                    logger.warning(f"'{model_name}' returned empty output — nudging for text reply.")
                    nudge_data = dict(input_data)
                    nudge_data["input"] = (
                        "[SYSTEM: Your last turn produced no text. "
                        "Please reply to the customer now in plain text.]"
                    )
                    result      = executor.invoke(nudge_data)
                    ai_response = result.get("output", "").strip()

                if not ai_response:
                    raise ValueError("Empty response after nudge")

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
        # Vendor context — all come from session state via AIHandler
        vendor_name: str = "our kitchen",
        vendor_menu: str = "",
        vendor_delivery_fee: int = 500,
        vendor_free_min: int = 5000,
        vendor_hours: str = "",
        vendor_areas: str = "",
        vendor_support: str = "",
    ) -> Tuple[str, bool, str, str]:
        """
        Generate a conversational order response for the selected vendor.

        Returns:
            (clean_response, payment_triggered, order_ref, raw_response)
        """
        if not user_message or not isinstance(user_message, str):
            return "Hey! What would you like to order today?", False, None, None, False

        if not self.ai_enabled or not self._llms:
            return (
                "Sorry, our ordering system is having a moment. Please try again shortly!",
                False, None, None, False
            )

        if not order_ref:
            order_ref = f"ORD{random.randint(10000, 99999)}"

        # Build dynamic system prompt for this vendor
        system_prompt = self._build_system_prompt(
            vendor_name=vendor_name,
            vendor_menu=vendor_menu,
            delivery_fee=vendor_delivery_fee,
            free_delivery_min=vendor_free_min,
            opening_hours=vendor_hours,
            delivery_areas=vendor_areas,
            support_contact=vendor_support,
        )

        try:
            chat_history = []

            real_phone = (
                phone_number
                if phone_number and str(phone_number).strip() not in ("", "N/A", "None")
                else None
            )
            real_name = (
                user_name
                if user_name and user_name.strip() not in ("", "Guest", "Customer", "None")
                else None
            )

            phone_line   = f"Phone: {real_phone}" if real_phone else "Phone: [omit from summary]"
            name_line    = f"Name: {real_name}"   if real_name  else "Name: [omit from summary]"
            session_line = "RETURNING SESSION" if is_returning else "NEW SESSION"

            context_note = (
                f"[SYSTEM — customer details, copy exactly, never invent:\n"
                f"Order Ref: {order_ref}\n"
                f"{name_line}\n"
                f"{phone_line}\n"
                f"Vendor: {vendor_name}\n"
                f"Session: {session_line}]"
            )
            chat_history.append(("human", context_note))
            chat_history.append((
                "ai",
                "Understood. I will use only these exact details, omit lines marked [omit], "
                "and follow the session type correctly."
            ))

            if conversation_history:
                for exchange in conversation_history[-8:]:
                    chat_history.append(("human", exchange.get("user", "")))
                    chat_history.append(("ai",    exchange.get("assistant", "")))

            raw_response = self._invoke_with_fallback(
                input_data={
                    "input":        user_message,
                    "chat_history": chat_history,
                },
                system_prompt=system_prompt,
                vendor_menu=vendor_menu,
            )

            # Strip both tags from customer-facing response
            payment_triggered = False
            vendor_change     = False
            clean_response    = raw_response

            payment_match = re.search(r'\[PAYMENT_READY:amount=(\d+)\]', raw_response)
            if payment_match:
                payment_triggered = True
                clean_response = re.sub(r'\[ORDER_ITEMS:[^\]]+\]', '', clean_response).strip()
                clean_response = re.sub(r'\[PAYMENT_READY:amount=\d+\]', '', clean_response).strip()

            if '[CHANGE_VENDOR]' in raw_response:
                vendor_change  = True
                clean_response = clean_response.replace('[CHANGE_VENDOR]', '').strip()

            logger.info(
                f"[{self.active_model}] [{session_id}] vendor={vendor_name} "
                f"returning={is_returning} payment={payment_triggered} "
                f"vendor_change={vendor_change}: {clean_response[:100]}"
            )
            return clean_response, payment_triggered, order_ref, raw_response, vendor_change

        except Exception as e:
            logger.error(f"All models failed [{session_id}]: {e}", exc_info=True)
            return (
                "Something went wrong on our end. Try again or send 'menu' to browse!",
                False, None, None, False
            )