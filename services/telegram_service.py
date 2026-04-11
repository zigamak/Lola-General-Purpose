import requests
import logging
import sys
import io
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)
handler = logging.StreamHandler(stream=sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
if sys.platform.startswith('win'):
    handler.stream = io.TextIOWrapper(handler.stream.buffer, encoding='utf-8', errors='replace')
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)


class TelegramService:
    """
    Telegram drop-in replacement for WhatsAppService.

    Mirrors every public method used by MessageProcessor, AIHandler,
    GreetingHandler, and payment_webhook so the rest of the codebase
    needs zero changes — just swap the service instance.

    Key difference from WhatsApp:
      - `to` is a Telegram chat_id (integer or string), NOT a phone number.
      - Buttons are InlineKeyboardMarkup (callback_data), not WhatsApp reply buttons.
      - No "messaging_product" field — Telegram has its own response shape.
    """

    BASE_URL_TEMPLATE = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, config):
        self.config = config
        self.token = config.TELEGRAM_BOT_TOKEN
        logger.debug("TelegramService initialised.")

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _url(self, method: str) -> str:
        return self.BASE_URL_TEMPLATE.format(token=self.token, method=method)

    def _post(self, method: str, payload: Dict) -> Optional[Dict]:
        """POST to any Telegram Bot API method. Returns the full API response."""
        try:
            response = requests.post(self._url(method), json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.error("Telegram API error (%s): %s", method, data)
                return None
            logger.debug("Telegram %s -> %s", method, data)
            return data
        except requests.exceptions.HTTPError as e:
            logger.error("HTTP error calling %s: %s — %s", method, e,
                         response.text if 'response' in locals() else "no response")
            return None
        except requests.RequestException as e:
            logger.error("Request error calling %s: %s", method, e)
            return None
        except Exception as e:
            logger.error("Unexpected error calling %s: %s", method, e, exc_info=True)
            return None

    # ── Shared interface method ───────────────────────────────────────────────

    def send_text(self, to: str, text: str) -> Optional[Dict]:
        """
        Unified send method used by platform-agnostic handlers.
        Delegates to create_text_message internally.
        """
        return self.create_text_message(to, text)

    # ── Core send ─────────────────────────────────────────────────────────────

    def send_message(self, payload: Dict) -> Optional[Dict]:
        """
        Generic send — expects a dict with at least `chat_id` and one of:
          `text`, `photo`, `caption`.

        This is the single choke-point that all other methods funnel through,
        matching WhatsAppService.send_message() semantics.
        """
        if not payload or not isinstance(payload, dict):
            logger.error("send_message: invalid payload: %s", payload)
            return None

        if "chat_id" not in payload:
            logger.error("send_message: missing chat_id in payload: %s", payload)
            return None

        # Decide which Telegram method to call based on payload shape
        if "photo" in payload:
            method = "sendPhoto"
        elif "text" in payload:
            method = "sendMessage"
        else:
            logger.error("send_message: cannot determine method from payload: %s", payload)
            return None

        return self._post(method, payload)

    # ── Text messages ─────────────────────────────────────────────────────────

    def create_text_message(self, to: str, text: str) -> Optional[Dict]:
        """Send a plain text message. Mirrors WhatsAppService.create_text_message."""
        if not to or not text:
            logger.error("create_text_message: missing to=%s or text=%s", to, text)
            return None

        payload = {
            "chat_id": str(to),
            "text": str(text),
            # parse_mode omitted intentionally — AI responses are plain text
        }
        return self.send_message(payload)

    # ── Button messages ────────────────────────────────────────────────────────
    # WhatsApp buttons → Telegram InlineKeyboardMarkup with callback_data.
    #
    # WhatsApp button shape:  {"type": "reply", "reply": {"id": "...", "title": "..."}}
    # This service converts that to Telegram's shape automatically.

    def _wa_buttons_to_inline_keyboard(self, buttons: List[Dict]) -> Dict:
        """Convert WhatsApp-style button list to Telegram InlineKeyboardMarkup."""
        keyboard = []
        for btn in buttons:
            if btn.get("type") == "reply":
                reply = btn.get("reply", {})
                keyboard.append([{
                    "text": reply.get("title", ""),
                    "callback_data": reply.get("id", reply.get("title", ""))
                }])
            else:
                # Already Telegram-style or unknown — pass through
                keyboard.append([btn])
        return {"inline_keyboard": keyboard}

    def create_button_message_payload(self, to: str, text: str, buttons: List[Dict]) -> Optional[Dict]:
        """Build (but do not send) a button message payload."""
        if not to or not text or not buttons:
            logger.error("create_button_message_payload: missing args")
            return None

        return {
            "chat_id": str(to),
            "text": str(text),
            "reply_markup": self._wa_buttons_to_inline_keyboard(buttons)
        }

    def send_button_message(self, to: str, text: str, buttons: List[Dict]) -> Optional[Dict]:
        """Send a message with inline buttons."""
        payload = self.create_button_message_payload(to, text, buttons)
        if not payload:
            # Graceful fallback — send as plain text
            return self.create_text_message(to, text)
        return self.send_message(payload)

    def create_button_message(self, to: str, text: str, buttons: List[Dict]) -> Optional[Dict]:
        """Alias for send_button_message — matches WhatsAppService signature."""
        return self.send_button_message(to, text, buttons)

    # ── List messages ──────────────────────────────────────────────────────────
    # WhatsApp has a native list picker; Telegram does not.
    # We render the sections as numbered inline buttons instead.

    def create_list_message(self, to: str, text: str, button_text: str,
                            sections: List[Dict]) -> Optional[Dict]:
        """
        Render a WhatsApp list as Telegram inline buttons.
        Each section row becomes a button; button_text becomes the header.
        """
        if not to or not text or not sections:
            return self.create_text_message(to, text)

        keyboard = []
        for section in sections:
            for row in section.get("rows", []):
                keyboard.append([{
                    "text": row.get("title", ""),
                    "callback_data": row.get("id", row.get("title", ""))
                }])

        payload = {
            "chat_id": str(to),
            "text": str(text),
            "reply_markup": {"inline_keyboard": keyboard}
        }
        return self.send_message(payload)

    # ── Image messages ─────────────────────────────────────────────────────────

    def create_image_message(self, to: str, image_url: str, caption: str = "") -> Optional[Dict]:
        """Build (but do not send) a photo payload."""
        if not to or not image_url:
            logger.error("create_image_message: missing to=%s or image_url=%s", to, image_url)
            return None

        payload = {
            "chat_id": str(to),
            "photo": image_url,
        }
        if caption:
            payload["caption"] = str(caption)

        return payload

    def send_image_message(self, to: str, image_url: str, caption: str = "") -> Optional[Dict]:
        """Send a photo with an optional caption."""
        payload = self.create_image_message(to, image_url, caption)
        if not payload:
            return None
        return self.send_message(payload)

    def send_image_with_buttons(self, to: str, image_url: str, text: str,
                                buttons: List[Dict], button_prompt: str = "") -> Optional[Dict]:
        """
        Send a photo then immediately send a button message below it.
        Mirrors WhatsAppService.send_image_with_buttons.
        """
        image_response = self.send_image_message(to, image_url, caption=text)
        if not image_response:
            logger.error("send_image_with_buttons: photo failed for %s", to)
            return None

        prompt = button_prompt if button_prompt else text
        return self.send_button_message(to, prompt, buttons)

    # ── Utility / compatibility methods ───────────────────────────────────────

    def send_timeout_message(self, session_id: str) -> Optional[Dict]:
        """Notify the user their session expired. Mirrors WhatsAppService.send_timeout_message."""
        return self.create_text_message(
            session_id,
            "Your session has timed out due to inactivity. "
            "Send any message to start a new conversation."
        )

    def send_template_message(self, to: str, template_name: str,
                              language_code: str, components: List[Dict]) -> Optional[Dict]:
        """
        Telegram has no template concept — render the template as plain text.
        Override this method if you have a richer implementation.
        """
        logger.warning(
            "send_template_message called on TelegramService — "
            "Telegram has no templates. Sending plain text fallback."
        )
        text = f"[{template_name}] Message (template not supported on Telegram)"
        return self.create_text_message(to, text)

    def validate_contact(self, phone_number: str) -> Optional[Dict]:
        """
        No equivalent in Telegram (no phone number exposure).
        Returns a stub so callers don't crash.
        """
        logger.info("validate_contact called — no-op on Telegram (chat_id used instead).")
        return {"chat_id": phone_number, "status": "unknown"}

    # ── Incoming webhook parsing ───────────────────────────────────────────────

    def process_incoming_payload(self, payload: Dict) -> Optional[Dict]:
        """
        Parse a Telegram Update object into the same shape that
        WhatsAppService.process_incoming_payload returns:
          {"wa_id": <chat_id>, "message_id": <message_id>, "text": <text>}

        Handles both regular messages and callback_query (button taps).
        """
        try:
            if not payload or not isinstance(payload, dict):
                logger.error("process_incoming_payload: invalid payload")
                return None

            # ── Callback query (user tapped an inline button) ──────────────
            if "callback_query" in payload:
                cq = payload["callback_query"]
                chat_id = str(cq["message"]["chat"]["id"])
                message_id = str(cq.get("id", ""))
                text = cq.get("data", "")  # callback_data = the button's id/title

                # Acknowledge the callback so Telegram removes the loading spinner
                self._post("answerCallbackQuery", {"callback_query_id": cq["id"]})

                logger.info("Callback query from %s: %s", chat_id, text)
                return {"wa_id": chat_id, "message_id": message_id, "text": text}

            # ── Regular message ────────────────────────────────────────────
            message = payload.get("message")
            if not message:
                logger.warning("process_incoming_payload: no message or callback_query in update")
                return None

            chat_id = str(message["chat"]["id"])
            message_id = str(message.get("message_id", ""))
            text = message.get("text", "")

            logger.info("Incoming message from %s (id=%s): %s", chat_id, message_id, text)
            return {"wa_id": chat_id, "message_id": message_id, "text": text}

        except Exception as e:
            logger.error("process_incoming_payload error: %s", e, exc_info=True)
            return None

    # ── Webhook registration ───────────────────────────────────────────────────

    def register_webhook(self, webhook_url: str) -> bool:
        """
        Tell Telegram where to deliver updates.
        Call this once at startup (or whenever your URL changes).

        Example:
            telegram_service.register_webhook("https://yourdomain.com/telegram/webhook")
        """
        data = self._post("setWebhook", {"url": webhook_url})
        if data and data.get("ok"):
            logger.info("Telegram webhook registered: %s", webhook_url)
            return True
        logger.error("Failed to register Telegram webhook: %s", data)
        return False

    def get_webhook_info(self) -> Optional[Dict]:
        """Return current webhook info from Telegram (useful for debugging)."""
        return self._post("getWebhookInfo", {})