import logging
import os
import re
from typing import Dict, List, Any
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

GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash-002",
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

            # Log exactly what we got so we can see in Render logs
            if self.gemini_api_key:
                masked = self.gemini_api_key[:6] + "..." + self.gemini_api_key[-4:]
                logger.info(f"Chowder.ng AIService - Gemini key found: {masked}")
            else:
                logger.error(
                    "Chowder.ng AIService - GEMINI_API_KEY is missing or None. "
                    "Check your environment variables on Render."
                )
                return

            self._build_agents()

            if self._executors:
                self.ai_enabled = True
                logger.info(f"AIService ready. Agents built for: {list(self._executors.keys())}")
            else:
                logger.error("AIService: No agents could be built — check logs above for errors.")

        except Exception as e:
            logger.error(f"AIService init error: {e}", exc_info=True)

    def _build_agents(self):
        """
        Build AgentExecutor objects for each model — pure object construction,
        no API calls made here. Gemini is only contacted on first invoke().
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
                logger.info(f"Agent object built for model: {model_name}")
            except Exception as e:
                logger.warning(f"Could not build agent for '{model_name}': {e}")

    def _create_menu_tool(self):
        @tool
        def get_menu() -> str:
            """Get the full Chowder.ng menu with all item names and prices."""
            return MENU_TEXT
        return get_menu

    def _get_system_prompt(self) -> str:
        return f"""You are the friendly WhatsApp order-taking assistant for *Chowder.ng* — a Nigerian food brand serving Signature Loaded Fries.

Your job is to take food orders conversationally over WhatsApp. Be warm, casual, and fun — like a great waiter who genuinely loves the food.

The menu:
{MENU_TEXT}

How to handle the conversation:
- When someone greets you or says they want to order, welcome them warmly and show the full menu.
- When they mention an item by name OR by number (e.g. "I'll take 1" or "give me the suya one"), confirm it and ask if they want anything else.
- Once they are done ordering, summarise their full order with each item and price, then show the total. Ask for their delivery location/address.
- When they send their location, confirm the full order one more time with the total and delivery address, tell them it is confirmed and being processed. Generate a short order reference: CHW followed by 4 random digits (e.g. CHW3821).
- If someone asks about a specific item, describe it warmly from the menu.
- If someone asks something unrelated to food or ordering, gently steer them back.
- Use natural Nigerian-friendly expressions where it fits (e.g. "No wahala!", "We go sort you out!", "Your order don land!").
- Use emojis naturally but not excessively.

Important rules:
- Keep track of everything they order across the whole conversation.
- Calculate totals correctly using only the prices on the menu above.
- Never invent items or prices not on the menu.
- Always be warm — make Chowder.ng feel like a place they will want to order from again."""

    def _invoke_with_fallback(self, input_data: dict) -> str:
        """
        Try each model in order at runtime. Remembers the last working model.
        """
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
                    logger.info(f"Active model set to: {model_name}")
                    self.active_model = model_name
                return ai_response
            except Exception as e:
                logger.warning(f"Model '{model_name}' invoke failed: {e}. Trying next...")
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

        if not self.ai_enabled or not self._executors:
            logger.error(
                f"generate_order_response called but ai_enabled={self.ai_enabled}, "
                f"executors={list(self._executors.keys())}"
            )
            return (
                "Sorry, our ordering system is having a moment 😅 Please try again shortly!",
                False, None, None
            )

        try:
            chat_history = []
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
            logger.error(f"All models failed for session {session_id}: {e}", exc_info=True)
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