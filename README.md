# Lola — Multi-Vendor Ordering Bot: Project Context Prompt

Paste this at the start of any new conversation so Claude understands the full project without needing to re-upload files.

---

## Project Overview

**Lola** is a multi-vendor food ordering bot that runs on both **WhatsApp** and **Telegram**. It is built with Python/Flask, uses **Google Gemini** (via LangChain) as the AI engine, **Paystack** for payments, and **PostgreSQL** for persistence. The codebase is fully platform-agnostic — the same handlers and AI service work on both platforms by swapping the messaging service instance.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | Flask |
| AI / LLM | Google Gemini (`gemini-2.5-flash`) via LangChain |
| Messaging — WhatsApp | `WhatsAppService` (Meta Cloud API) |
| Messaging — Telegram | `TelegramService` (Telegram Bot API) |
| Payments | Paystack |
| Database | PostgreSQL via `psycopg2` |
| Session storage | In-memory `SessionManager` (keyed by chat_id / phone number) |
| Config | `config.py` with env vars via `python-dotenv` |

---

## File Structure

```
project/
│
├── app.py                        # Flask app entry point
├── telegram_webhook.py           # Telegram webhook route → MessageProcessor
├── whatsapp_webhook.py           # WhatsApp webhook route → MessageProcessor
├── payment_webhook.py            # Paystack webhook + manual payment check helpers
├── config.py                     # All env vars (DB_URL, GEMINI_API_KEY, etc.)
├── db_manager.py                 # All PostgreSQL operations (single class: DBManager)
├── message_processor.py          # Central router — receives every message, routes to handler
│
├── handlers/
│   ├── base_handler.py           # Base class all handlers extend
│   ├── greeting_handler.py       # First contact — fetches vendors, sends button list
│   ├── vendor_handler.py         # Handles vendor button tap, loads menu into session
│   └── ai_handler.py             # Conversational order flow via AIService
│
└── services/
    ├── ai_service.py             # Gemini agent — dynamic system prompt per vendor
    ├── telegram_service.py       # Telegram API wrapper (drop-in for WhatsAppService)
    ├── whatsapp_service.py       # Meta WhatsApp Cloud API wrapper
    └── payment_service.py        # Paystack link generation + email helpers
```

---

## Database Schema

### Core tables

**`vendors`** — one row per vendor/restaurant
- `id`, `name`, `description`, `type` (varchar: restaurant/pharmacy/grocery/etc.)
- `logo_url`, `menu_image_url` — image shown to customer on selection
- `telegram_chat_id`, `whatsapp_number` — for vendor notifications
- `zone`, `delivery_fee` (naira), `free_delivery_min` (naira)
- `opening_hours`, `delivery_areas`, `support_contact`
- `order_ref_prefix` (e.g. `MK`, `CC`, `MT`) — prefixes order refs
- `is_active` (boolean)

**`customers`** — one row per phone/chat_id
- `id`, `phone_number` (unique), `name`, `platform` (whatsapp/telegram)

**`products`** — one row per menu item
- `id`, `vendor_id` (FK → vendors), `name`, `description`, `category`, `price` (naira), `is_available`

**`orders`**
- `id`, `order_ref`, `customer_id` (FK), `vendor_id` (FK), `delivery_address`
- `subtotal`, `delivery_fee`, `total` (all naira)
- `status` (payment_sent / paid / preparing / on_the_way / delivered / cancelled)
- `payment_status` (unpaid / paid / failed), `payment_ref`
- `platform` (whatsapp/telegram)

**`order_items`** — line items for each order
- `order_id` (FK), `product_id` (FK, optional), `name`, `price`, `quantity`, `subtotal`

### Supporting tables

**`payments`** — logs every Paystack event
- `order_id`, `order_ref`, `amount` (kobo), `payment_ref`, `gateway`, `status`, `webhook_payload` (jsonb)

**`deliveries`** — rider tracking per order
- `order_id`, `rider_name`, `rider_phone`
- `status` (pending / accepted / picked_up / delivered)
- `pin` (4-digit), `accepted_at`, `picked_up_at`, `delivered_at`, `timeout_at`

**`notifications`** — log of every outbound message sent
- `order_id`, `recipient_type` (customer/vendor/rider), `platform`, `chat_id`, `message`, `status`

**`conversations`** — full chat history
- `customer_id` (FK), `order_id` (FK, optional), `role` (user/assistant), `message`

---

## Message Flow

```
Telegram/WhatsApp webhook
    → MessageProcessor.process_message()
        → _route_to_handler()
            │
            ├── state = "start" or reset trigger ("menu", "start")
            │       → _start_fresh() → GreetingHandler.generate_initial_greeting()
            │               → fetches vendors from DB
            │               → sends welcome text + vendor buttons
            │               → state = "vendor_selection"
            │
            ├── state = "vendor_selection"
            │       → VendorHandler.handle_vendor_selection()
            │               → parses "vendor_N" from button tap
            │               → loads vendor + products from DB
            │               → builds menu text via db.format_menu_text()
            │               → stores vendor context in session state
            │               → sends menu image + welcome text
            │               → state = "ai_chat"
            │
            └── state = "ai_chat"
                    → AIHandler.handle_ai_chat_state()
                            → checks for payment claim first (manual "I've paid")
                            → calls AIService.generate_order_response()
                            → if [PAYMENT_READY] tag detected:
                                    → saves order to DB
                                    → logs payment record
                                    → generates Paystack link
                                    → sends payment message
                            → else: sends AI response as plain text
```

---

## Session State Keys

Every session is a dict stored in `SessionManager` keyed by `session_id` (chat_id or phone number).

```python
state = {
    # Core
    "current_state":        str,   # "start" | "vendor_selection" | "ai_chat"
    "current_handler":      str,   # "greeting_handler" | "ai_handler"
    "platform":             str,   # "whatsapp" | "telegram"
    "user_name":            str,
    "phone_number":         str,   # same as session_id
    "welcome_sent":         bool,
    "is_returning":         bool,
    "conversation_history": list,  # last 10 exchanges [{user, assistant, timestamp}]

    # Vendor (populated by VendorHandler)
    "selected_vendor_id":   int,
    "selected_vendor_name": str,
    "menu_image_url":       str,
    "vendor_menu":          str,   # formatted menu text injected into AI prompt
    "vendor_delivery_fee":  int,   # naira
    "vendor_free_min":      int,   # naira — threshold for free delivery
    "vendor_hours":         str,
    "vendor_areas":         str,
    "vendor_support":       str,
    "vendor_ref_prefix":    str,   # e.g. "MK"

    # Order (populated during AI chat)
    "order_ref":            str,   # e.g. "MK12345"
    "db_order_id":          int,   # orders.id after DB insert
    "delivery_address":     str,
    "payment_pending":      bool,
    "payment_ref":          str,
    "payment_amount_kobo":  int,
}
```

---

## AI System Prompt Design

`AIService._build_system_prompt()` is called **per request** (not at startup) so it can inject the current vendor's data dynamically. Parameters injected:

- `vendor_name` — replaces all hardcoded restaurant names
- `vendor_menu` — formatted product list from DB via `db.format_menu_text(vendor_id)`
- `delivery_fee`, `free_delivery_min` — vendor-specific delivery rules
- `opening_hours`, `delivery_areas`, `support_contact` — vendor-specific FAQ answers

The AI uses two LangChain tools:
- `get_menu()` — returns vendor menu text (closure over current vendor's menu)
- `check_order_status(phone_number)` — queries `orders` + `customers` tables via `_db_instance`

### Payment trigger protocol

When the AI is ready to collect payment it appends two tags to its response:
```
[ORDER_ITEMS:name=X,qty=Y,price=Z,subtotal=W;name=...]
[PAYMENT_READY:amount=XXXXXX]   ← amount in KOBO
```
`AIHandler._process_message()` detects these, strips them from the customer-facing message, and calls `_handle_payment_trigger()` which saves the order, logs the payment, and generates the Paystack link.

---

## Key Design Decisions

1. **Platform detection** — `session_id` is numeric-only for Telegram, phone number format for WhatsApp. `BaseHandler.get_platform()` uses this to decide button format.

2. **WhatsApp button limit** — WhatsApp supports max 3 reply buttons per message. `send_vendor_list()` in `BaseHandler` paginates vendors into groups of 3 for WhatsApp; Telegram gets all vendors in one inline keyboard.

3. **Menu is DB-driven** — no hardcoded menu anywhere. `db.format_menu_text(vendor_id)` builds the menu string from the `products` table grouped by category.

4. **Welcome is sent once by VendorHandler** — `ai_handler._handle_start()` only sets up session state (order_ref, flags). It does NOT send messages, to prevent double-sending.

5. **`_start_fresh()` always goes to GreetingHandler** — never directly to AIHandler. A fresh session always starts with vendor selection.

6. **Vendor context flows via session state** — AIService has no DB access for vendor data. Everything it needs (menu, delivery rules, hours) is passed in as parameters from AIHandler, which reads from session state.

---

## Adding a New Vendor

1. Insert into `vendors` table with correct `order_ref_prefix`, `menu_image_url`, etc.
2. Insert products into `products` table with the correct `vendor_id`
3. No code changes needed — the bot picks it up automatically

## Adding a New Handler

1. Create `handlers/my_handler.py` extending `BaseHandler`
2. Add a new `current_state` value (e.g. `"my_state"`)
3. Add routing branch in `MessageProcessor._route_to_handler()`
4. Instantiate handler in `MessageProcessor.__init__()`

## Adding a New DB Operation

All DB work goes in `DBManager` in `db_manager.py`. Use `self._execute(query, params, fetch='one'|'all'|None)` — it handles connection, commit, rollback, and logging automatically.