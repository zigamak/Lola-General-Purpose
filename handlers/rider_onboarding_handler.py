"""
handlers/rider_onboarding_handler.py
─────────────────────────────────────
Step-by-step Telegram KYC onboarding for new riders.

Trigger: user sends /register in any private chat with the bot.

Collected fields (in order):
  name → email → phone → hall → room number → course

Hall options (case-insensitive button or typed match):
  Peter Hall | John Hall | Joseph Hall | Daniel Hall
"""

import logging
import re

logger = logging.getLogger(__name__)

HALLS = ["Peter Hall", "John Hall", "Joseph Hall", "Daniel Hall"]

# Steps in order
STEP_ORDER = ["name", "email", "phone", "hall", "room", "course"]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[\d\s\-]{7,15}$")


class RiderOnboardingHandler:
    """
    Manages rider KYC onboarding conversations.

    State is held in-process (dict keyed by telegram_id).
    On completion the data is flushed to the `riders` table
    and the in-process state is cleared.
    """

    def __init__(self, config, db_manager, telegram_service):
        self.config   = config
        self.db       = db_manager
        self.telegram = telegram_service
        # {telegram_id: {"step": str, "data": {field: value}}}
        self._sessions: dict = {}
        logger.info("RiderOnboardingHandler initialised.")

    # ── Public API ─────────────────────────────────────────────────────────────

    def is_onboarding(self, telegram_id: str) -> bool:
        """True when this chat_id has an active onboarding conversation."""
        return str(telegram_id) in self._sessions

    def start(self, telegram_id: str, user_name: str = ""):
        """
        Begin (or restart) the onboarding flow.
        Called when the user sends /register.
        """
        tid = str(telegram_id)

        existing = self.db.get_rider_by_telegram_id(tid)
        if existing and existing.get("onboarding_complete"):
            self.telegram.create_text_message(
                tid,
                f"Hi {existing.get('name', 'there')}! You're already registered as a rider.\n\n"
                "You'll be notified when orders are available in the rider group.",
            )
            return

        # Pre-fill name from Telegram display name if available
        self._sessions[tid] = {
            "step": "name",
            "data": {"_telegram_name": user_name or ""},
        }

        name_hint = f" (or press Enter to use '{user_name}')" if user_name else ""
        self.telegram.create_text_message(
            tid,
            f"Welcome! Let's get you set up as a Lola rider.\n\n"
            f"Step 1 of 6 — What's your *full name*?{name_hint}",
        )

    def handle(self, telegram_id: str, text: str):
        """
        Route the incoming message to the correct step handler.
        Call only after confirming is_onboarding() returns True.
        """
        tid = str(telegram_id)
        session = self._sessions.get(tid)
        if not session:
            return

        step = session["step"]
        handler = getattr(self, f"_step_{step}", None)
        if handler:
            handler(tid, session, text.strip())

    # ── Step handlers ──────────────────────────────────────────────────────────

    def _step_name(self, tid: str, session: dict, text: str):
        # Allow blank to reuse Telegram display name
        name = text or session["data"].get("_telegram_name", "")
        if not name:
            self.telegram.create_text_message(tid, "Please tell me your full name.")
            return

        session["data"]["name"] = name
        session["step"] = "email"
        self.telegram.create_text_message(
            tid,
            f"Nice to meet you, {name}!\n\n"
            "Step 2 of 6 — What's your *email address*?",
        )

    def _step_email(self, tid: str, session: dict, text: str):
        if not _EMAIL_RE.match(text.lower()):
            self.telegram.create_text_message(
                tid, "That doesn't look like a valid email. Please try again."
            )
            return

        session["data"]["email"] = text.lower()
        session["step"] = "phone"
        self.telegram.create_text_message(
            tid,
            "Step 3 of 6 — What's your *phone number*? (e.g. 08012345678)",
        )

    def _step_phone(self, tid: str, session: dict, text: str):
        phone = re.sub(r"[\s\-]", "", text)
        if not _PHONE_RE.match(phone) or len(phone) < 10:
            self.telegram.create_text_message(
                tid, "Please enter a valid phone number (at least 10 digits)."
            )
            return

        session["data"]["phone"] = phone
        session["step"] = "hall"
        self._ask_hall(tid)

    def _step_hall(self, tid: str, session: dict, text: str):
        # Accept button callback ("hall_Peter Hall") or typed match
        if text.startswith("hall_"):
            selected = text[len("hall_"):]
        else:
            # Fuzzy match — check if typed text contains a hall name
            selected = None
            for h in HALLS:
                if h.lower() in text.lower() or text.lower() in h.lower():
                    selected = h
                    break

        if not selected or selected not in HALLS:
            self._ask_hall(tid, retry=True)
            return

        session["data"]["hall"] = selected
        session["step"] = "room"
        self.telegram.create_text_message(
            tid,
            f"Step 5 of 6 — What's your *room number* in {selected}?",
        )

    def _step_room(self, tid: str, session: dict, text: str):
        if not text:
            self.telegram.create_text_message(tid, "Please enter your room number.")
            return

        session["data"]["room_number"] = text
        session["step"] = "course"
        self.telegram.create_text_message(
            tid,
            "Step 6 of 6 — What *course* are you studying? (e.g. Computer Science)",
        )

    def _step_course(self, tid: str, session: dict, text: str):
        if not text:
            self.telegram.create_text_message(tid, "Please enter your course of study.")
            return

        session["data"]["course"] = text
        session["step"] = "done"
        self._complete(tid, session)

    # ── Completion ─────────────────────────────────────────────────────────────

    def _complete(self, tid: str, session: dict):
        data = session["data"]

        success = self.db.save_rider_onboarding(
            telegram_id  = tid,
            name         = data["name"],
            email        = data.get("email", ""),
            phone        = data["phone"],
            hall         = data["hall"],
            room_number  = data["room_number"],
            course       = data["course"],
        )

        del self._sessions[tid]

        if success:
            summary = (
                "✅ *Registration complete!*\n\n"
                f"Name:   {data['name']}\n"
                f"Email:  {data.get('email', '—')}\n"
                f"Phone:  {data['phone']}\n"
                f"Hall:   {data['hall']}\n"
                f"Room:   {data['room_number']}\n"
                f"Course: {data['course']}\n\n"
                "You've been added to the rider pool. You can now accept delivery "
                "orders when they're posted to the rider group. Good luck! 🛵"
            )
        else:
            summary = (
                "Something went wrong saving your details. "
                "Please try again by sending /register."
            )

        self.telegram.create_text_message(tid, summary)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _ask_hall(self, tid: str, retry: bool = False):
        prompt = (
            "Please choose your hall from the options below:"
            if retry
            else "Step 4 of 6 — Which *hall* do you live in?"
        )
        buttons = [
            {"type": "reply", "reply": {"id": f"hall_{h}", "title": h}}
            for h in HALLS
        ]
        self.telegram.send_button_message(tid, prompt, buttons)
