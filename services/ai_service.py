import logging
import re
from datetime import datetime
from typing import Dict, List
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

MENU_IMAGE_URL = "https://eventio.africa/wp-content/uploads/2026/03/chowder.ng_.jpg"

MENU_TEXT = """Our *Signature Loaded Fries* menu:

1. *Shawarma Chicken Loaded Fries* — ₦6,500
   Grilled chicken shavings, garlic toum, creamy shawarma sauce

2. *Dirty Bacon & Cheese Fries* — ₦8,000
   Cheddar melt, bacon bits, mozzarella pull, smoky chipotle mayo & crunchy onions

3. *Suya Beef Loaded Fries* — ₦7,500
   Soft spicy suya beef strips, yaji crumble, fresh onions & tomato chunks, suya-butter drizzle

4. *Asun Sweet & Spicy Loaded Fries* — ₦9,000
   Peppery soft goat meat, sweet chili glaze, bell peppers and fire mayo

5. *Honey Mustard Chicken Fries* — ₦7,000
   Crispy chicken bites, honey-mustard glaze, sesame & herbs

6. *BBQ Pulled Beef Loaded Fries* — ₦8,500
   Slow-cooked shredded beef, tangy BBQ sauce, slaw & crispy shallots

7. *Chilli Pepper Prawn Loaded Fries* — ₦10,000
   Slow-cooked prawns in chilli pepper sauce, slaw & crispy shallots"""

# Confirmed working models on this API key (tested 2026-03-20)
GEMINI_MODELS = [
    "gemini-2.5-flash",       # ✅ primary — best quality
    "gemini-flash-latest",    # ✅ fallback 1 — alias to current stable flash
    "gemini-flash-lite-latest", # ✅ fallback 2 — lighter/faster
    "gemini-2.5-flash-lite",  # ✅ fallback 3 — pinned lite version
]


class AIService:
    """
    Handles conversational AI ordering for Chowder.ng WhatsApp bot.
    Uses Google Gemini with lazy runtime fallback — no API calls at startup.
    """

    def __init__(self, config, data_manager):
        load_dotenv()
        self.data_manager = data_manager
        self.menu_image_url = MENU_IMAGE_URL
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

            if self.gemini_api_key:
                masked = self.gemini_api_key[:8] + "..." + self.gemini_api_key[-4:]
                logger.info(f"Chowder.ng AIService — Gemini key found: {masked}")
            else:
                logger.error(
                    "GEMINI_API_KEY is missing. "
                    "Get a free key at https://aistudio.google.com/app/apikey"
                )
                return

            self._build_agents()

            if self._executors:
                self.ai_enabled = True
                logger.info(f"AIService ready. Models: {list(self._executors.keys())}")
            else:
                logger.error("AIService: No agents could be built.")

        except Exception as e:
            logger.error(f"AIService init error: {e}", exc_info=True)

    def _build_agents(self):
        """
        Build one AgentExecutor per model — pure object construction, zero API calls.
        Gemini is only contacted when a real message is processed.
        """
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
            """Get the full Chowder.ng menu with all item names and prices."""
            return MENU_TEXT
        return get_menu

    def _get_system_prompt(self) -> str:
        return f"""You are the official WhatsApp order assistant for Chowder.ng, a Nigerian food brand serving Signature Loaded Fries.

Your role is strictly to take and confirm food orders. You are professional, warm, and concise. You do not roleplay, invent information, or make promises you cannot keep.

WHAT YOU CAN DO:
- Show the menu and help customers choose what to order
- Take and confirm orders with a reference number
- Answer questions about menu items, prices, and ingredients
- Tell customers that payment is cash on delivery

WHAT YOU CANNOT DO — never attempt these, no exceptions:
- You cannot check order status, delivery progress, or driver location
- You cannot contact dispatch, drivers, or any internal team
- You cannot offer refunds, discounts, free items, or compensation
- You cannot access any previous orders or order history
- You cannot make promises about delivery time
- You cannot invent or assume any information not given to you in this conversation

---

ESCALATION SCRIPTS — use these exact responses for complaints:

If a customer says their order has not arrived or is delayed:
"Thank you for reaching out. I am sorry to hear your order has not arrived yet. As the ordering assistant, I am not able to check delivery status directly. Please contact our support team who can look into this for you:
📞 Call or WhatsApp: [support number]
They will be able to follow up with the delivery team on your behalf."

If a customer asks for a refund or compensation:
"I understand your frustration and I am sorry for the experience. Refund and compensation requests need to be handled by our support team directly — I am not able to process these here.
Please reach out to:
📞 Call or WhatsApp: [support number]
They will review your case and get back to you."

If a customer asks for an update on an order they already placed:
"I only handle new orders and cannot access order history or delivery updates. For updates on an existing order, please contact our support team:
📞 Call or WhatsApp: [support number]"

If a customer is upset or angry:
"I hear you and I am truly sorry you are having this experience. I want to make sure you get the right help — please contact our support team directly so they can resolve this for you:
📞 Call or WhatsApp: [support number]
I will stay here if you would like to place a new order."

---

THE MENU:
{MENU_TEXT}

HOW TO TAKE AN ORDER:
- Greet the customer professionally and ask what they would like to order
- When they mention an item by name or number, confirm it and ask if they want anything else
- If they order the same item multiple times, group as one line: item name x[qty] — ₦[total for that item]
- Once done, summarise the order plainly and ask for their delivery address
- When they give their location, send the order confirmation in this exact format:

Order Confirmed

Order Ref: [use the ref given in context]
Name: [use the name given in context, omit line if not provided]
Phone: [use the phone given in context, omit line if not provided]
Delivery Address: [their address]

Your Order:
[item name] x[qty] — ₦[price]

Total: ₦[total]
Payment: Cash on Delivery

Your order has been received and is being prepared. Thank you for choosing Chowder.ng.

---

TONE AND STYLE:
- Professional and clear at all times
- Warm but not overly casual or playful
- Occasional light Nigerian expressions are fine (e.g. "No wahala") but keep it measured
- Never use asterisks (*) for formatting — plain text only
- Never use bullet point asterisks
- Short and direct responses — do not over-explain
- Never fabricate facts, order history, delivery times, or internal processes
- If you do not know something, say so honestly and direct the customer to support"""

    def _invoke_with_fallback(self, input_data: dict) -> str:
        """Try each model in order. Remember the last working one to skip retrying failed ones."""
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
                    logger.info(f"Active model: {model_name}")
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
        session_id: str = None
    ) -> tuple[str, bool, str, str]:
        """Generate a conversational order response using the Gemini agent."""
        if not user_message or not isinstance(user_message, str):
            return "Hey! What would you like to order today? 😊", False, None, None

        # Generate order ref in code — never let the AI invent it
        import random
        order_ref = f"CHW{random.randint(1000, 9999)}"

        if not self.ai_enabled or not self._executors:
            logger.error(f"AI unavailable — ai_enabled={self.ai_enabled}, executors={list(self._executors.keys())}")
            return (
                "Sorry, our ordering system is having a moment 😅 Please try again shortly!",
                False, None, None
            )

        try:
            chat_history = []

            # Always inject customer context as first turn so the AI has it
            # regardless of whether this is the first or subsequent message
            real_phone = phone_number if phone_number and str(phone_number).strip() not in ("", "N/A", "None") else None
            real_name = user_name if user_name and user_name.strip() not in ("", "Guest", "Customer", "None") else None

            phone_line = f"Phone: {real_phone}" if real_phone else "Phone: [omit this line from confirmation]"
            name_line = f"Name: {real_name}" if real_name else "Name: [omit this line from confirmation]"

            context_note = (
                f"[SYSTEM — customer details, copy exactly, never invent:\n"
                f"Order Ref: {order_ref}\n"
                f"{name_line}\n"
                f"{phone_line}]"
            )
            chat_history.append(("human", context_note))
            chat_history.append(("ai", "Noted. I will use only these exact details and omit any line marked [omit]."))

            # Add conversation history after the context
            if conversation_history:
                for exchange in conversation_history[-8:]:
                    chat_history.append(("human", exchange.get("user", "")))
                    chat_history.append(("ai", exchange.get("assistant", "")))

            ai_response = self._invoke_with_fallback({
                "input": user_message,
                "chat_history": chat_history
            })

            logger.info(f"[{self.active_model}] [{session_id}]: {ai_response[:120]}")
            return ai_response, False, None, None

        except Exception as e:
            logger.error(f"All models failed [{session_id}]: {e}", exc_info=True)
            return (
                "Ah, something went wrong on our end 😅 "
                "Try again or send *menu* to see what we've got!",
                False, None, None
            )

    # ── Compatibility stubs ────────────────────────────────────────────────────

    def _is_swahili(self, message: str) -> bool:
        return False

    def _extract_location(self, message: str) -> str:
        return message.strip() if message and len(message.strip()) > 3 else None

    def _extract_name(self, message: str) -> str:
        match = re.search(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b', message or "")
        return match.group(0) if match else None