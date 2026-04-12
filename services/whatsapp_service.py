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


class WhatsAppService:
    """Service for sending and handling WhatsApp messages."""

    def __init__(self, config):
        self.config = config
        self.base_url = f"https://graph.facebook.com/v17.0/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
        self.headers = {
            "Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        logger.debug("WhatsAppService initialized with phone_number_id: %s", config.WHATSAPP_PHONE_NUMBER_ID)

    # ── Shared interface method ───────────────────────────────────────────────

    def send_text(self, to: str, text: str) -> Optional[Dict]:
        """
        Unified send method used by platform-agnostic handlers.
        Delegates to create_text_message internally.
        """
        return self.create_text_message(to, text)

    # ── Typing indicator ──────────────────────────────────────────────────────

    def send_typing_indicator(self, message_id: str) -> Optional[Dict]:
        """
        Mark an incoming message as read and show the three-dot typing indicator
        to the user. Call this immediately after receiving a message and before
        processing the response.

        The indicator is dismissed automatically when you send a reply or after
        25 seconds — whichever comes first.
        """
        try:
            if not message_id:
                logger.error("Cannot send typing indicator: message_id is required")
                return None

            payload = {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": str(message_id),
                "typing_indicator": {
                    "type": "text"
                }
            }

            response = requests.post(self.base_url, json=payload, headers=self.headers)
            response.raise_for_status()
            result = response.json()
            logger.debug("Typing indicator sent for message_id: %s", message_id)
            return result

        except requests.exceptions.HTTPError as http_err:
            logger.error(
                "HTTP error sending typing indicator for %s: %s - Response: %s",
                message_id, http_err,
                response.text if 'response' in locals() else "No response"
            )
            return None
        except requests.RequestException as e:
            logger.error("Request error sending typing indicator for %s: %s", message_id, e)
            return None
        except Exception as e:
            logger.error("Unexpected error sending typing indicator for %s: %s", message_id, e, exc_info=True)
            return None

    # ── Core send ─────────────────────────────────────────────────────────────

    def send_message(self, payload: Dict) -> Optional[Dict]:
        """Send a message to the WhatsApp Business API."""
        try:
            if not payload or not isinstance(payload, dict):
                logger.error("Payload is None or not a dictionary: %s", payload)
                return None

            required_fields = ["to", "type", "messaging_product"]
            missing_fields = [field for field in required_fields if field not in payload]
            if missing_fields:
                logger.error("Missing required fields in payload: %s - Payload: %s", missing_fields, payload)
                return None

            if payload["messaging_product"] != "whatsapp":
                logger.error("Invalid messaging_product: %s", payload["messaging_product"])
                return None

            logger.info("Sending WhatsApp payload to %s: %s", payload.get("to"), payload)

            response = requests.post(self.base_url, json=payload, headers=self.headers)
            response.raise_for_status()
            response_data = response.json()
            logger.info("WhatsApp API response: %s - %s", response.status_code, response_data)

            if not response_data.get("messaging_product") == "whatsapp":
                logger.error("Invalid response format: %s", response_data)
                return None

            return response_data
        except requests.exceptions.HTTPError as http_err:
            logger.error("HTTP error sending WhatsApp message: %s - Response: %s", http_err, response.text if 'response' in locals() else "No response")
            return None
        except requests.RequestException as e:
            logger.error("Request error sending WhatsApp message: %s", e)
            return None
        except Exception as e:
            logger.error("Unexpected error in send_message: %s", e, exc_info=True)
            return None

    def process_incoming_payload(self, payload: Dict) -> Optional[Dict]:
        """Process an incoming WhatsApp webhook payload and extract relevant data."""
        try:
            if not payload or not isinstance(payload, dict):
                logger.error("Invalid incoming payload: %s", payload)
                return None

            if payload.get("messaging_product") == "whatsapp" and "contacts" in payload and "messages" in payload:
                contacts = payload.get("contacts", [])
                messages = payload.get("messages", [])
                wa_id = contacts[0].get("wa_id") or contacts[0].get("input") if contacts else None
                message_id = messages[0].get("id") if messages else None
                logger.info("Processed API response - wa_id: %s, message_id: %s", wa_id, message_id)
                return {"wa_id": wa_id, "message_id": message_id}

            if "object" in payload and payload["object"] == "whatsapp_business_account":
                entries = payload.get("entry", [])
                if not entries:
                    return None

                changes = entries[0].get("changes", [])
                if not changes:
                    return None

                value    = changes[0].get("value", {})
                messages = value.get("messages", [])
                contacts = value.get("contacts", [])

                if not messages or not contacts:
                    return None

                wa_id         = contacts[0].get("wa_id") or value.get("metadata", {}).get("phone_number_id")
                message_text  = messages[0].get("text", {}).get("body") if messages[0].get("type") == "text" else None
                message_id    = messages[0].get("id")

                logger.info("Processed incoming message - wa_id: %s, message_id: %s, text: %s", wa_id, message_id, message_text)
                return {"wa_id": wa_id, "message_id": message_id, "text": message_text}

            logger.error("Unrecognized payload format: %s", payload)
            return None
        except Exception as e:
            logger.error("Error processing incoming payload: %s", e, exc_info=True)
            return None

    def create_text_message(self, to: str, text: str) -> Optional[Dict]:
        """Create and send a text message."""
        try:
            if not to or not text:
                logger.error("Invalid parameters: to='%s', text='%s'", to, text)
                return None

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": str(to),
                "type": "text",
                "text": {"body": str(text)}
            }
            return self.send_message(payload)
        except Exception as e:
            logger.error("Error creating text message for %s: %s", to, e, exc_info=True)
            return None

    def create_button_message_payload(self, to: str, text: str, buttons: List[Dict]) -> Optional[Dict]:
        """Creates a button message payload without sending."""
        try:
            if not to or not text or not buttons:
                return None

            if not isinstance(buttons, list) or len(buttons) > 3:
                logger.error("Invalid buttons format or too many buttons: %s", buttons)
                return None

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": str(to),
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": str(text)},
                    "action": {"buttons": buttons}
                }
            }
            return payload
        except Exception as e:
            logger.error("Error creating button payload for %s: %s", to, e, exc_info=True)
            return None

    def send_button_message(self, to: str, text: str, buttons: List[Dict]) -> Optional[Dict]:
        """Sends a button message."""
        payload = self.create_button_message_payload(to, text, buttons)
        if not payload:
            return self.create_text_message(to, text)
        return self.send_message(payload)

    def create_button_message(self, to: str, text: str, buttons: List[Dict]) -> Optional[Dict]:
        """Alias for send_button_message for compatibility."""
        return self.send_button_message(to, text, buttons)

    def create_list_message(self, to: str, text: str, button_text: str, sections: List[Dict]) -> Optional[Dict]:
        """Create and send a list message."""
        try:
            if not to or not text or not button_text or not sections:
                return None

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": str(to),
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "body": {"text": str(text)},
                    "action": {"button": str(button_text), "sections": sections}
                }
            }
            return self.send_message(payload)
        except Exception as e:
            logger.error("Error creating list message for %s: %s", to, e, exc_info=True)
            return self.create_text_message(to, text)

    def create_image_message(self, to: str, image_url: str, caption: str = "") -> Optional[Dict]:
        """Creates an image message payload without sending."""
        try:
            if not to or not image_url:
                return None

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": str(to),
                "type": "image",
                "image": {"link": image_url}
            }
            if caption:
                payload["image"]["caption"] = str(caption)

            return payload
        except Exception as e:
            logger.error("Error creating image message payload for %s: %s", to, e, exc_info=True)
            return None

    def send_image_message(self, to: str, image_url: str, caption: str = "") -> Optional[Dict]:
        """Sends an image message with an optional caption."""
        try:
            payload = self.create_image_message(to, image_url, caption)
            if not payload:
                return None
            return self.send_message(payload)
        except Exception as e:
            logger.error("Error sending image message for %s: %s", to, e, exc_info=True)
            return None

    def send_image_with_buttons(self, to: str, image_url: str, text: str, buttons: List[Dict], button_prompt: str = "") -> Optional[Dict]:
        """Sends an image message followed by a button message."""
        try:
            image_response = self.send_image_message(to, image_url, caption=text)
            if not image_response:
                return None
            button_text = button_prompt if button_prompt else text
            return self.send_button_message(to, button_text, buttons)
        except Exception as e:
            logger.error("Error sending image with buttons for %s: %s", to, e, exc_info=True)
            return self.create_text_message(to, text)

    def send_timeout_message(self, session_id: str) -> Optional[Dict]:
        """Send timeout message to user."""
        try:
            if not session_id:
                return None
            return self.create_text_message(
                session_id,
                "Your session has timed out due to inactivity. Please send a message to start a new interaction."
            )
        except Exception as e:
            logger.error("Error sending timeout message for %s: %s", session_id, e, exc_info=True)
            return None

    def send_template_message(self, to: str, template_name: str, language_code: str, components: List[Dict]) -> Optional[Dict]:
        """Sends a WhatsApp template message."""
        try:
            if not to or not template_name or not language_code or not components:
                return None

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": str(to),
                "type": "template",
                "template": {
                    "name": str(template_name),
                    "language": {"code": str(language_code)},
                    "components": components
                }
            }
            return self.send_message(payload)
        except Exception as e:
            logger.error("Error creating template message for %s: %s", to, e, exc_info=True)
            return self.create_text_message(to, "⚠️ Error sending template message. Please contact support.")

    def validate_contact(self, phone_number: str) -> Optional[Dict]:
        """Validate a phone number using the WhatsApp Business API."""
        try:
            if not phone_number:
                return None
            payload = {
                "messaging_product": "whatsapp",
                "contacts": [{"phone_number": str(phone_number)}]
            }
            url = f"https://graph.facebook.com/v17.0/{self.config.WHATSAPP_PHONE_NUMBER_ID}/contacts"
            response = requests.post(url, json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Error validating contact %s: %s", phone_number, e, exc_info=True)
            return None