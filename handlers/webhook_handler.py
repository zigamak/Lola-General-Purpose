import json
import logging
from message_processor import MessageProcessor

logger = logging.getLogger(__name__)


class WebhookHandler:
    """Handles incoming WhatsApp webhook requests."""

    def __init__(self, config, message_processor: MessageProcessor):
        self.config = config
        self.message_processor = message_processor
        self._processed_ids = set()  # Deduplication cache

    # ── Verification ──────────────────────────────────────────────────────────

    def verify_webhook(self, request):
        """Handle WhatsApp webhook verification (GET)."""
        mode      = request.args.get("hub.mode")
        token     = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if mode == "subscribe" and token == self.config.VERIFY_TOKEN:
            logger.info("WhatsApp webhook verified successfully.")
            return challenge, 200

        logger.error("Webhook verification failed — token mismatch or wrong mode.")
        return "Verification failed", 403

    # ── Incoming messages ─────────────────────────────────────────────────────

    def handle_webhook(self, request):
        """Handle incoming WhatsApp messages (POST)."""
        try:
            data = request.get_json()
            if not data:
                logger.error("Webhook POST received with no JSON body.")
                return {"status": "error", "message": "No data received"}, 400

            logger.debug(f"Webhook payload: {json.dumps(data, indent=2)}")

            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value    = change.get("value", {})
                    messages = value.get("messages", [])

                    if not messages:
                        continue

                    message      = messages[0]
                    phone_number = message.get("from")

                    if not phone_number:
                        logger.error("Message has no 'from' field — skipping.")
                        continue

                    # ── Deduplication ─────────────────────────────────────────
                    message_id = message.get("id", "")
                    if message_id and message_id in self._processed_ids:
                        logger.info(f"Duplicate webhook ignored: {message_id}")
                        continue

                    if message_id:
                        self._processed_ids.add(message_id)
                        # Keep set bounded — trim to last 500 when it hits 1000
                        if len(self._processed_ids) > 1000:
                            self._processed_ids = set(list(self._processed_ids)[-500:])

                    # ── Extract sender name ───────────────────────────────────
                    user_name = None
                    contacts  = value.get("contacts", [])
                    if contacts:
                        user_name = contacts[0].get("profile", {}).get("name")

                    # ── Extract message data ──────────────────────────────────
                    message_data = self._extract_message_data(message)

                    if message_data:
                        # Attach the raw message_id so downstream services
                        # (e.g. WhatsApp typing indicator) can reference it
                        if message_id:
                            message_data["message_id"] = message_id
                        logger.info(
                            f"Message from {phone_number} ({user_name or 'Unknown'}): "
                            f"{str(message_data)[:80]}"
                        )
                        # process_message handles sending internally —
                        # do NOT use the return value to send again
                        self.message_processor.process_message(
                            message_data, phone_number, user_name
                        )
                    else:
                        logger.warning(
                            f"Could not extract message data for {phone_number} "
                            f"(type: {message.get('type')}) — skipping."
                        )

            # Always return 200 immediately so WhatsApp doesn't retry
            return {"status": "success"}, 200

        except Exception as e:
            logger.error(f"Error processing webhook: {e}", exc_info=True)
            # Still return 200 to prevent WhatsApp retries causing duplicate messages
            return {"status": "ok"}, 200

    # ── Message extraction ────────────────────────────────────────────────────

    def _extract_message_data(self, message):
        """Extract structured data from different WhatsApp message types."""
        message_type = message.get("type")

        if message_type == "text":
            return {"type": "text", "text": message["text"]["body"]}

        elif message_type == "button":
            return {"type": "text", "text": message["button"]["payload"]}

        elif message_type == "interactive":
            interactive = message.get("interactive", {})
            itype       = interactive.get("type")
            if itype == "button_reply":
                return {"type": "text", "text": interactive["button_reply"]["id"]}
            elif itype == "list_reply":
                return {"type": "text", "text": interactive["list_reply"]["id"]}

        elif message_type == "location":
            loc = message.get("location", {})
            return {
                "type":      "location",
                "latitude":  loc.get("latitude"),
                "longitude": loc.get("longitude"),
                "name":      loc.get("name"),
                "address":   loc.get("address"),
            }

        return None