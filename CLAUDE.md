# Lola — Multi-Vendor Order Bot

A production-grade multi-vendor food ordering bot supporting both Telegram and WhatsApp. Built with Python/Flask, Google Gemini (via LangChain) for conversational AI, Paystack for payments, and PostgreSQL for persistence.

---

## Project Structure

```
Lola-General-Purpose/
├── app.py                  # Flask app, blueprint registration, service initialization
├── run.py                  # Entry point: env validation, logging setup, starts Flask
├── config.py               # Config class — all env vars loaded here
├── db_manager.py           # All PostgreSQL operations (single DBManager class)
├── message_processor.py    # Central message router (platform-agnostic, state-based)
├── telegram_webhook.py     # Telegram webhook route + rider/customer routing
├── payment_webhook.py      # Paystack webhook handler (charge.success → order flow)
│
├── handlers/
│   ├── base_handler.py     # Base class: platform detection, vendor list sender
│   ├── greeting_handler.py # "start" state — fetches and shows vendor list
│   ├── vendor_handler.py   # "vendor_selection" state — loads menu into session
│   ├── ai_handler.py       # "ai_chat" state — Gemini conversation + payment trigger
│   ├── delivery_handler.py # Rider button callbacks (accept/picked/delivered/unavailable)
│   ├── webhook_handler.py  # WhatsApp webhook (Meta Cloud API)
│   └── faq_handler.py      # FAQ (not heavily used)
│
├── services/
│   ├── telegram_service.py    # Telegram Bot API wrapper (drop-in for WhatsAppService)
│   ├── whatsapp_service.py    # Meta WhatsApp Cloud API wrapper
│   ├── ai_service.py          # Gemini agent with dynamic per-vendor system prompt
│   ├── notification_service.py# Post-payment notifications (vendor/customer/rider)
│   ├── payment_service.py     # Paystack payment link generation
│   ├── lead_tracker.py        # Lead tracking (unused in current flow)
│   └── location_service.py    # Google Maps (unused in current flow)
│
├── utils/
│   ├── session_manager.py  # In-memory sessions with timeout logic
│   ├── whatsapp_utils.py   # WhatsApp-specific helpers
│   └── helpers.py          # Generic utilities
│
├── models/
│   └── session_state.py    # Session state dataclass
│
├── portal/
│   └── routes.py           # Vendor dashboard (view orders, manage products)
│
├── data/                   # JSON fallback storage (not primary)
├── data.sql                # PostgreSQL schema
└── requirements.txt        # Python dependencies
```

---

## Running the Bot

```bash
python run.py
python run.py --debug --port 8000
```

**Health check:** `GET /health`

**Entry flow in `run.py`:**
1. Validate required env vars
2. Set up logging
3. Import Flask app → register blueprints
4. Initialize services (SessionManager, WhatsAppService, TelegramService, DBManager, AIService)
5. Register Telegram webhook with Telegram's API via `setWebhook`

---

## How Telegram Bots Work

### 1. Webhook Entry Point — `telegram_webhook.py`

All Telegram updates arrive at `POST /telegram/webhook`. The handler:

1. Calls `TelegramService.process_incoming_payload()` to parse the raw Telegram Update into a standardized dict: `{wa_id, message_id, text}` (same shape as WhatsApp payloads — this is how platform-agnosticism is achieved)
2. Determines if the message came from the **rider group** or a **known rider in private chat**
3. If it's a delivery callback (`accept_*`, `picked_*`, `delivered_*`, `unavailable_*`) → routes to `DeliveryHandler`
4. Everything else → routes to `MessageProcessor`

### 2. Platform Detection

Platform is inferred from `session_id` format in `MessageProcessor`:
- Numeric ID with fewer than 15 digits → **Telegram** (chat_id)
- Longer numeric string → **WhatsApp** (phone number)

### 3. Message Routing — `message_processor.py`

State machine with three states:

| State | Handler |
|---|---|
| `start` (or reset words like "menu", "start") | `GreetingHandler` |
| `vendor_selection` | `VendorHandler` |
| `ai_chat` | `AIHandler` |

### 4. Conversation Flow

```
Customer: /start
    → GreetingHandler → sends vendor list as inline keyboard buttons

Customer: taps "Makinde Kitchen"  (callback: vendor_3)
    → VendorHandler → loads vendor + menu from DB into session
    → sets current_state = "ai_chat"
    → AIHandler sends vendor welcome message + menu image

Customer: "2x Jollof Rice, delivery to Lekki"
    → AIHandler → AIService.generate_order_response()
    → AI computes total, appends tags: [ORDER_ITEMS:...] [PAYMENT_READY:amount=550000]
    → AIHandler strips tags, saves order to DB, generates Paystack link
    → sends payment link to customer

Customer: pays via Paystack
    → Paystack POSTs to /paystack/webhook
    → payment_webhook creates delivery record + PIN
    → NotificationService notifies vendor + posts to rider group

Rider: taps [Accept] in group
    → telegram_webhook detects rider group callback
    → DeliveryHandler._handle_accept() → assigns rider, notifies customer

Rider: taps [Picked Up] → [Delivered]
    → DeliveryHandler updates DB status, notifies customer each step
```

### 5. TelegramService — `services/telegram_service.py`

Drop-in replacement for `WhatsAppService`. Key behaviors:
- `process_incoming_payload()` — normalizes Telegram Update into `{wa_id, message_id, text}`. Handles both regular messages and callback queries; auto-calls `answerCallbackQuery` to dismiss Telegram's loading state.
- Button conversion: handlers pass WhatsApp-style button dicts → `TelegramService` converts to `InlineKeyboardMarkup`
- `create_list_message()` — converts WhatsApp list-picker format to Telegram inline buttons
- `register_webhook(url)` — calls Telegram `setWebhook` at startup

### 6. Rider Delivery Callbacks — `handlers/delivery_handler.py`

Callback data format: `{action}_{order_ref}` (e.g. `accept_MK00042`)

| Callback | Action |
|---|---|
| `accept_*` | Assign rider, send PIN privately, notify customer |
| `picked_*` | Mark picked up, notify customer |
| `delivered_*` | Mark delivered, close order, notify customer |
| `unavailable_*` | Cancel order (vendor out of stock), notify customer |

### 7. AI Conversation — `services/ai_service.py`

- Uses `gemini-2.5-flash` via LangChain `ChatGoogleGenerativeAI`
- System prompt is built **per request** (not at startup) from vendor-specific context injected by `AIHandler`: vendor name, menu, delivery fee, free delivery threshold, opening hours, delivery areas, support contact
- AI signals payment readiness by appending to its response:
  ```
  [ORDER_ITEMS:name=X,qty=Y,price=Z,subtotal=W;...]
  [PAYMENT_READY:amount=XXXXXX]   ← amount in kobo
  ```
- `AIHandler._process_message()` detects and strips these tags before sending text to customer

---

## Key Configuration — `config.py`

All loaded from environment variables (`.env` file).

**Required:**
```
TELEGRAM_BOT_TOKEN
GEMINI_API_KEY
DB_URL                    # PostgreSQL connection string
WHATSAPP_ACCESS_TOKEN
WHATSAPP_PHONE_NUMBER_ID
VERIFY_TOKEN              # WhatsApp webhook verification
PAYSTACK_SECRET_KEY
CALLBACK_BASE_URL         # Public HTTPS URL (ngrok or Render)
```

**Optional:**
```
RIDER_GROUP_CHAT_ID       # Telegram group ID for rider notifications (negative int)
PAYSTACK_PUBLIC_KEY
PAYSTACK_WEBHOOK_SECRET
Maps_API_KEY
SUPABASE_URL
SUPABASE_SERVICE_KEY
SUBACCOUNT_CODE           # Paystack split payment
BUSINESS_NAME
BUSINESS_SUPPORT_PHONE
SESSION_TIMEOUT           # Seconds (default: 3000)
```

---

## Session State — `utils/session_manager.py`

In-memory sessions keyed by `session_id` (chat_id for Telegram, phone for WhatsApp).

**Timeout:** 50 min regular, 200 min after payment. On timeout: reset state, preserve `user_name`, `delivery_address`, `account_number`.

**Key session fields:**
```python
current_state        # "start" | "vendor_selection" | "ai_chat"
platform             # "telegram" | "whatsapp"
user_name
selected_vendor_id
selected_vendor_name
vendor_menu          # formatted product list
vendor_delivery_fee
vendor_free_min      # free delivery threshold (naira)
vendor_hours
vendor_areas
vendor_ref_prefix    # e.g. "MK" → order refs like "MK00042"
order_ref
db_order_id
payment_pending
payment_amount_kobo
conversation_history # list of {user, assistant, timestamp} dicts
```

---

## Database — `db_manager.py` / `data.sql`

PostgreSQL. Single `DBManager` class with a persistent connection (`_get_conn()` auto-reconnects).

**Core tables:** `vendors`, `customers`, `products`, `orders`, `order_items`, `deliveries`, `payments`, `conversations`, `riders`

**Key relationships:**
- `orders` → `customers`, `vendors`
- `order_items` → `orders`, `products` (product_id nullable — AI can order items not in DB)
- `deliveries` → `orders`, `riders`
- `payments` → `orders`

**Order status flow:** `payment_sent` → `paid` → `preparing` → `on_the_way` → `delivered` (or `cancelled`)

**Delivery status flow:** `pending` → `accepted` → `picked_up` → `delivered` (or `cancelled`)

---

## Notification Flow — `services/notification_service.py`

Triggered by `payment_webhook.py` after Paystack `charge.success`:

1. Notify vendor via Telegram (personal chat) — order details + PIN
2. Notify vendor via WhatsApp (if number set)
3. Post to rider Telegram group — order summary + [Accept] [Unavailable] buttons
4. Notify customer — "Order confirmed, finding rider"

After rider actions: customer is notified at each step (accepted, picked up, delivered).

---

## Webhooks

| Route | Purpose |
|---|---|
| `POST /telegram/webhook` | All Telegram updates |
| `POST /webhook` | WhatsApp messages (Meta Cloud API) |
| `GET /webhook` | WhatsApp webhook verification |
| `POST /paystack/webhook` | Paystack payment events |
| `GET /health` | Health check |

---

## Dependencies (key)

```
Flask==2.3.2
langchain, langchain-google-genai  # AI orchestration
google-generativeai                # Gemini API
psycopg2-binary                    # PostgreSQL
requests                           # HTTP calls (Telegram/Paystack APIs)
supabase                           # Optional Supabase client
schedule                           # Scheduled tasks (not yet active)
```

---

## Architecture Notes

- **Platform agnosticism:** `TelegramService` and `WhatsAppService` share the same interface. `MessageProcessor` and all handlers are unaware of the underlying platform — they call `messaging_service.send_text()`, `send_button_message()`, etc. `TelegramService` handles the format conversion internally.
- **Dynamic AI prompts:** The system prompt is rebuilt per request, injecting live vendor context. One `AIService` instance serves all vendors.
- **No background scheduler active:** `DeliveryHandler.handle_timeout()` and `SessionManager.cleanup_expired_sessions()` exist but are not automatically triggered. A cron job or APScheduler could be added.
- **Rider routing is additive, not forked:** The same Telegram webhook serves customers and riders. Rider group callbacks are detected first; all other messages fall through to normal customer routing.
- **Payment tags are the AI/business logic boundary:** AI never calls the DB or payment APIs directly — it signals intent via `[PAYMENT_READY]` tags, and `AIHandler` handles everything downstream.
