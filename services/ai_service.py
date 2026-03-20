import logging
import os
import re
from typing import Dict, List, Any
from datetime import datetime
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
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


class AIService:
    """
    Handles conversational AI ordering for Chowder.ng WhatsApp bot.
    Customers chat naturally to browse the menu, place orders, and get confirmations.
    """

    def __init__(self, config, data_manager):
        load_dotenv()
        self.data_manager = data_manager
        self.menu_image_url = MENU_IMAGE_URL
        self.agent_executor = None
        self.llm = None

        try:
            if isinstance(config, dict):
                self.openai_api_key = config.get("openai_api_key")
            else:
                self.openai_api_key = getattr(config, 'OPENAI_API_KEY', None)

            self.ai_enabled = bool(self.openai_api_key)
            logger.info(f"Chowder.ng AIService - OpenAI Key: {'set' if self.openai_api_key else 'missing'}")

            if self.ai_enabled:
                self.llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7, api_key=self.openai_api_key)

                tools = [self._create_menu_tool()]

                prompt = ChatPromptTemplate.from_messages([
                    ("system", self._get_system_prompt()),
                    MessagesPlaceholder(variable_name="chat_history", optional=True),
                    ("human", "{input}"),
                    MessagesPlaceholder(variable_name="agent_scratchpad"),
                ])

                agent = create_tool_calling_agent(self.llm, tools, prompt)
                self.agent_executor = AgentExecutor(
                    agent=agent,
                    tools=tools,
                    verbose=True,
                    handle_parsing_errors=True,
                    max_iterations=3
                )
                logger.info("Chowder.ng conversational order agent initialized.")
            else:
                logger.warning("AI disabled — missing OpenAI API key.")

        except Exception as e:
            logger.error(f"AIService init error: {e}", exc_info=True)
            self.ai_enabled = False

    def _create_menu_tool(self):
        """Tool that returns the full menu when the agent needs to reference it."""
        @tool
        def get_menu() -> str:
            """Get the full Chowder.ng menu with all item names and prices."""
            return MENU_TEXT

        return get_menu

    def _get_system_prompt(self) -> str:
        return f"""You are the friendly WhatsApp order-taking assistant for *Chowder.ng* 🍟 — a Nigerian food brand serving Signature Loaded Fries.

Your job is to take food orders conversationally. Be warm, casual, and fun — like a great waiter who loves the food.

The menu:
{MENU_TEXT}

How to handle the conversation:
- When someone greets you or says they want to order, welcome them warmly and show the full menu.
- When they mention an item (by name OR by number), confirm it enthusiastically and ask if they want anything else.
- Once they say they're done, summarise their full order with each item and price, then show the total. Ask for their delivery location/address.
- When they send their location, confirm the full order one more time with the total, their delivery address, and tell them it's confirmed and being processed. Generate a short order reference like CHW followed by 4 digits (e.g. CHW2847).
- If someone asks about a specific item, describe it warmly from the menu.
- If someone asks something totally unrelated to food/ordering, gently steer them back.
- Use natural Nigerian-friendly expressions where it fits (e.g. "No wahala!", "We go sort you out!", "Your order don land! 🔥").
- Use emojis naturally but not excessively 🍟🔥😋

Important rules:
- Keep track of everything they order across the whole conversation.
- Calculate totals correctly from the menu prices.
- Never invent items or prices not on the menu.
- Always be warm and make Chowder.ng feel like a place they'll want to order from again."""

    def generate_order_response(
        self,
        user_message: str,
        conversation_history: List[Dict] = None,
        phone_number: str = None,
        user_name: str = None,
        session_id: str = None
    ) -> tuple[str, bool, str, str]:
        """
        Generate a conversational order response using the AI agent.
        Returns: (response_text, needs_info, placeholder1, placeholder2)
        Signature is compatible with the original generate_medical_response.
        """
        if not user_message or not isinstance(user_message, str):
            return "Hey! What would you like to order today? 😊", False, None, None

        if not self.ai_enabled or not self.agent_executor:
            return (
                "Sorry, our ordering system is having a moment 😅 "
                "Please try again shortly or call us directly!",
                False, None, None
            )

        try:
            # Build chat history for conversational context
            chat_history = []
            if conversation_history:
                for exchange in conversation_history[-8:]:
                    chat_history.append(("human", exchange.get("user", "")))
                    chat_history.append(("ai", exchange.get("assistant", "")))

            result = self.agent_executor.invoke({
                "input": user_message,
                "chat_history": chat_history
            })

            ai_response = result.get("output", "").strip()

            if not ai_response:
                raise ValueError("Empty response from agent")

            logger.info(f"Chowder.ng response [{session_id}]: {ai_response[:120]}")
            return ai_response, False, None, None

        except Exception as e:
            logger.error(f"Error generating order response for session {session_id}: {e}", exc_info=True)
            return (
                "Ah, something went wrong on our end 😅 "
                "Try again or send *menu* to see what we've got!",
                False, None, None
            )

    # ── Compatibility stubs so nothing else in the codebase breaks ────────────

    def _is_swahili(self, message: str) -> bool:
        return False

    def _extract_location(self, message: str) -> str:
        return message.strip() if message and len(message.strip()) > 3 else None

    def _extract_name(self, message: str) -> str:
        match = re.search(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b', message or "")
        return match.group(0) if match else None