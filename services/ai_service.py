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
        return f"""You are the friendly WhatsApp order-taking assistant for Chowder.ng — a Nigerian food brand serving Signature Loaded Fries.

Your job is to take food orders conversationally over WhatsApp. Be warm, casual, and fun.

The menu:
{MENU_TEXT}

How to handle the conversation:
- When someone wants to order, ask what they would like.
- When they mention an item by name OR by number, confirm it and ask if they want anything else.
- If they order the same item multiple times or say a quantity (e.g. "2 of the suya"), group it as one line with quantity x price = subtotal.
- Once they are done ordering, summarise their order plainly then show the total. Ask for their delivery location/address.
- When they send their location, send the final confirmation using EXACTLY this format:

Order Confirmed! 🎉

Order Ref: [use the order ref given to you in context, never invent one]
Name: [use ONLY the exact name from the customer info given — never write "Chowder Customer" or any placeholder]
Phone: [use ONLY the exact phone from the customer info given — if it says "not provided", leave this line out completely]
Delivery Address: [their location]

Your Order:
[item name] x[qty] — ₦[unit price x qty]

Total: ₦[total]

Payment: Cash on Delivery 💵

Your order is being processed and will be with you shortly. No wahala! 🔥

CRITICAL: Never invent, guess or use placeholder values for Name, Phone or Order Ref. Use only what was given to you.

- If someone orders 1 of an item, write it as: Shawarma Chicken Loaded Fries x1 — ₦6,500
- If someone orders 3 of the same item, write it as: Shawarma Chicken Loaded Fries x3 — ₦19,500
- Never repeat the same item on multiple lines. Always group same items into one line with the correct quantity and total price.
- If someone asks about a specific item, describe it warmly from the menu.
- If someone asks something unrelated to food, gently steer them back.
- Use natural Nigerian expressions where it fits (e.g. "No wahala!", "We go sort you out!").
- Use emojis naturally but not excessively.

FORMATTING RULES — follow strictly:
- Never use asterisks (*) anywhere in your response.
- Never use bullet points or dashes as list markers.
- Write everything as plain text. WhatsApp will display it cleanly.
- Never repeat the same item on multiple lines in the order summary.

Calculation rules:
- Calculate totals correctly from the menu prices above.
- Never invent items or prices not on the menu.
- Always be warm — make Chowder.ng feel like a place they will want to order from again."""

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