"""
estimate_tokens.py
Estimates Gemini 2.5 Flash token usage and cost from saved conversations in the DB.

Pricing (Gemini 2.5 Flash — text):
  Input:  $0.30 per 1M tokens
  Output: $2.50 per 1M tokens

Usage:
    python estimate_tokens.py

Requires DB_URL in .env
"""

import os
import sys
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DB_URL")

# ── Gemini 2.5 Flash pricing (per 1M tokens) ──────────────────────────────────
INPUT_PRICE_PER_M  = 0.30   # $ per 1M input tokens
OUTPUT_PRICE_PER_M = 2.50   # $ per 1M output tokens

# ── Token estimation ──────────────────────────────────────────────────────────
# Gemini uses ~4 characters per token on average for English/mixed text.
# Nigerian food names and Pidgin English are slightly longer so we use 3.8.
CHARS_PER_TOKEN = 3.8

# System prompt is sent on every AI call — estimate its size once
SYSTEM_PROMPT_CHARS = 4200  # approximate length of the system prompt in ai_service.py

# Context window overhead per turn (role tokens, formatting etc)
OVERHEAD_PER_TURN = 10  # tokens


def chars_to_tokens(chars: int) -> int:
    return max(1, round(chars / CHARS_PER_TOKEN))


def estimate_cost(input_tokens: int, output_tokens: int) -> dict:
    input_cost  = (input_tokens  / 1_000_000) * INPUT_PRICE_PER_M
    output_cost = (output_tokens / 1_000_000) * OUTPUT_PRICE_PER_M
    return {
        "input_cost":  input_cost,
        "output_cost": output_cost,
        "total_cost":  input_cost + output_cost,
    }


def main():
    if not DB_URL:
        print("ERROR: DB_URL not set in .env")
        sys.exit(1)

    print("Connecting to database...")
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception as e:
        print(f"Could not connect: {e}")
        sys.exit(1)

    # ── Fetch all conversations grouped by customer ───────────────────────────
    cur.execute("""
        SELECT
            cv.customer_id,
            c.phone_number,
            c.name,
            cv.role,
            cv.message,
            cv.created_at
        FROM conversations cv
        LEFT JOIN customers c ON cv.customer_id = c.id
        ORDER BY cv.customer_id, cv.created_at ASC
    """)
    rows = cur.fetchall()

    # ── Summary stats ─────────────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) as c FROM conversations")
    total_messages = cur.fetchone()['c']

    cur.execute("SELECT COUNT(*) as c FROM conversations WHERE role = 'user'")
    user_messages = cur.fetchone()['c']

    cur.execute("SELECT COUNT(*) as c FROM conversations WHERE role = 'assistant'")
    bot_messages = cur.fetchone()['c']

    cur.execute("SELECT COUNT(DISTINCT customer_id) as c FROM conversations")
    unique_customers = cur.fetchone()['c']

    cur.close()
    conn.close()

    if not rows:
        print("No conversations found in the database.")
        return

    # ── Group messages by customer session ────────────────────────────────────
    # Each time a user sends a message that gets an AI response,
    # the AI receives: system prompt + conversation history so far + new message
    # We simulate this accumulation per customer

    from collections import defaultdict
    customer_messages = defaultdict(list)
    for row in rows:
        customer_messages[row['customer_id']].append(dict(row))

    total_input_tokens  = 0
    total_output_tokens = 0
    total_ai_calls      = 0
    system_prompt_tokens = chars_to_tokens(SYSTEM_PROMPT_CHARS)

    for customer_id, messages in customer_messages.items():
        # Build up conversation history as AI sees it
        history_chars = 0

        for i, msg in enumerate(messages):
            msg_chars = len(msg['message'])
            msg_tokens = chars_to_tokens(msg_chars)

            if msg['role'] == 'user':
                # This user message triggers an AI call
                # Input = system prompt + all history so far + this message
                history_tokens = chars_to_tokens(history_chars)
                input_tokens = (
                    system_prompt_tokens +
                    history_tokens +
                    msg_tokens +
                    OVERHEAD_PER_TURN
                )
                total_input_tokens += input_tokens
                total_ai_calls     += 1

                # Add to history
                history_chars += msg_chars

            elif msg['role'] == 'assistant':
                # Output tokens = the bot response
                total_output_tokens += msg_tokens + OVERHEAD_PER_TURN

                # Add to history (next AI call will include this)
                history_chars += msg_chars

    # ── Calculate costs ───────────────────────────────────────────────────────
    costs = estimate_cost(total_input_tokens, total_output_tokens)

    # ── Print report ──────────────────────────────────────────────────────────
    print()
    print("=" * 58)
    print("  Gemini 2.5 Flash — Token Usage & Cost Estimate")
    print("=" * 58)
    print()
    print("  DATABASE SUMMARY")
    print(f"  Total messages saved:    {total_messages:,}")
    print(f"  User messages:           {user_messages:,}")
    print(f"  Bot messages:            {bot_messages:,}")
    print(f"  Unique customers:        {unique_customers:,}")
    print(f"  Total AI calls:          {total_ai_calls:,}")
    print()
    print("  TOKEN ESTIMATE")
    print(f"  System prompt (per call):{system_prompt_tokens:>10,} tokens")
    print(f"  Total input tokens:      {total_input_tokens:>10,} tokens")
    print(f"  Total output tokens:     {total_output_tokens:>10,} tokens")
    print(f"  Total tokens:            {total_input_tokens + total_output_tokens:>10,} tokens")
    print()
    print("  COST ESTIMATE (Gemini 2.5 Flash text pricing)")
    print(f"  Input  @ $0.30/1M:       ${costs['input_cost']:>10.6f}")
    print(f"  Output @ $2.50/1M:       ${costs['output_cost']:>10.6f}")
    print(f"  Total estimated cost:    ${costs['total_cost']:>10.6f}")
    print()

    # ── Per-customer breakdown ────────────────────────────────────────────────
    print("  PER CUSTOMER BREAKDOWN")
    print(f"  {'Phone':<20} {'Messages':>8} {'AI Calls':>9} {'Est. Cost':>12}")
    print(f"  {'-'*20} {'-'*8} {'-'*9} {'-'*12}")

    customer_costs = []
    for customer_id, messages in customer_messages.items():
        phone    = messages[0]['phone_number'] or str(customer_id)
        name     = messages[0].get('name') or ''
        n_msgs   = len(messages)
        n_calls  = sum(1 for m in messages if m['role'] == 'user')

        # Rough per-customer cost
        cust_chars_in  = sum(len(m['message']) for m in messages if m['role'] == 'user')
        cust_chars_out = sum(len(m['message']) for m in messages if m['role'] == 'assistant')
        cust_in_tok    = chars_to_tokens(cust_chars_in)  + (n_calls * system_prompt_tokens)
        cust_out_tok   = chars_to_tokens(cust_chars_out)
        cust_cost      = estimate_cost(cust_in_tok, cust_out_tok)['total_cost']

        customer_costs.append((phone, name, n_msgs, n_calls, cust_cost))

    # Sort by cost descending
    customer_costs.sort(key=lambda x: x[4], reverse=True)

    for phone, name, n_msgs, n_calls, cust_cost in customer_costs:
        label = f"{phone}" + (f" ({name})" if name else "")
        print(f"  {label:<20} {n_msgs:>8} {n_calls:>9} ${cust_cost:>11.6f}")

    print()
    print("  NOTE: Estimates based on ~3.8 chars/token average.")
    print("  Actual usage may vary ±15% depending on message content.")
    print("  Pricing: input $0.30/1M, output $2.50/1M tokens.")
    print("=" * 58)
    print()


if __name__ == "__main__":
    main()