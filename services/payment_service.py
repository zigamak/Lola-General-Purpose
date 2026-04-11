import os
import logging
import datetime
import random
import requests
from utils.helpers import parse_name

logger = logging.getLogger(__name__)

# Payment success page — Paystack redirects customers here after completing payment.
# Built as a constant so it's easy to see and change in one place.
# The path must match the route registered in portal/routes.py:
#   @portal_bp.route("/payment/success")  →  /portal/payment/success
PAYMENT_SUCCESS_PATH = "/portal/payment/success"


class PaymentService:
    """Handles payment processing and verification."""

    def __init__(self, config):
        self.config = config
        self.paystack_secret_key = config.PAYSTACK_SECRET_KEY
        self.paystack_public_key = config.PAYSTACK_PUBLIC_KEY

        # Strip trailing slash so we never get double-slashes in the URL
        base = (config.CALLBACK_BASE_URL or "").rstrip("/")
        self.callback_base_url = base

        # Full callback URL Paystack will redirect customers to after payment.
        # e.g. https://afyabot-7w4j.onrender.com/portal/payment/success
        self.payment_callback_url = f"{base}{PAYMENT_SUCCESS_PATH}"

        logger.info(f"PaymentService initialised — callback: {self.payment_callback_url}")

    def generate_order_id(self):
        """Generate a unique order ID."""
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        random_num = random.randint(1000, 9999)
        return f"ORDER-{timestamp}-{random_num}"

    def calculate_cart_total(self, cart):
        """Calculate total amount in kobo (Paystack uses kobo)."""
        total = 0
        for item, details in cart.items():
            subtotal = details["price"] * details["quantity"]
            total += subtotal
        return total * 100  # Convert to kobo

    def generate_customer_email(self, phone_number, user_name):
        """Generate a customer email from name and phone."""
        first_name, last_name = parse_name(user_name)

        clean_first_name = ''.join(c.lower() for c in first_name if c.isalnum())
        clean_phone = phone_number.replace('+', '').replace('-', '').replace(' ', '')[-4:]

        return f"{clean_first_name}{clean_phone}@lola.com"

    def create_payment_link(
        self,
        amount,
        email,
        reference,
        customer_name,
        customer_phone,
        metadata=None,
        subaccount_code=None,
        split_percentage=None,
    ):
        """
        Create a Paystack payment link with optional subaccount splitting.

        After the customer pays, Paystack redirects them to:
            {CALLBACK_BASE_URL}/portal/payment/success?reference=<reference>

        That page verifies the payment, updates the DB, and sends a WhatsApp
        or Telegram confirmation message automatically.
        """
        url = "https://api.paystack.co/transaction/initialize"
        headers = {
            "Authorization": f"Bearer {self.paystack_secret_key}",
            "Content-Type": "application/json",
        }

        first_name, last_name = parse_name(customer_name)

        data = {
            "amount": amount,           # Amount in kobo
            "email": email,
            "reference": reference,
            # ── KEY FIX: was self.callback_base_url (bare domain) ──
            # Now points to the full payment success route so Paystack
            # appends ?reference=<ref> and lands on the right page.
            "callback_url": self.payment_callback_url,
            "customer": {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "phone": customer_phone,
            },
            "metadata": {
                "customer_name":  customer_name,
                "customer_phone": customer_phone,
                "first_name":     first_name,
                "last_name":      last_name,
                **(metadata or {}),
            },
        }

        # Add subaccount splitting if provided
        if subaccount_code and split_percentage:
            data["subaccount"] = subaccount_code
            data["transaction_charge"] = int(amount * (split_percentage / 100))
            data["bearer"] = "account"
            logger.info(f"Subaccount split: code={subaccount_code}, {split_percentage}%")

        try:
            logger.info(
                f"Creating payment link — ref={reference}, amount={amount}, "
                f"email={email}, callback={self.payment_callback_url}"
            )
            response = requests.post(url, json=data, headers=headers)
            response.raise_for_status()
            result = response.json()
            logger.debug(f"Paystack init response for {reference}: {result}")

            if result["status"]:
                auth_url = result["data"]["authorization_url"]
                logger.info(
                    f"Payment link created for {first_name} {last_name} "
                    f"({customer_phone}) → {auth_url}"
                )
                return auth_url
            else:
                logger.error(
                    f"Paystack link creation failed for {reference}: "
                    f"{result.get('message', 'Unknown error')} — {result}"
                )
                return None

        except requests.exceptions.HTTPError as http_err:
            logger.error(
                f"HTTP error creating payment link for {reference}: {http_err} — "
                f"{http_err.response.text}",
                exc_info=True,
            )
            return None
        except requests.RequestException as e:
            logger.error(f"Network error creating payment link for {reference}: {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Unexpected error creating payment link for {reference}: {e}", exc_info=True)
            return None

    def verify_payment(self, reference):
        """Simple payment verification — returns status string."""
        verified, _ = self.verify_payment_detailed(reference)
        return "success" if verified else "failed"

    def verify_payment_detailed(self, reference):
        """Detailed payment verification — returns (bool, dict)."""
        url = f"https://api.paystack.co/transaction/verify/{reference}"
        headers = {
            "Authorization": f"Bearer {self.paystack_secret_key}",
            "Content-Type": "application/json",
        }

        try:
            logger.info(f"Verifying payment for reference: {reference}")
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            result = response.json()
            logger.debug(f"Paystack verification response for {reference}: {result}")

            if result["status"] and result["data"]["status"] == "success":
                payment_data = {
                    "amount":                result["data"]["amount"],
                    "currency":              result["data"]["currency"],
                    "reference":             result["data"]["reference"],
                    "status":                result["data"]["status"],
                    "gateway_response":      result["data"]["gateway_response"],
                    "paid_at":               result["data"]["paid_at"],
                    "channel":               result["data"]["channel"],
                    "fees":                  result["data"].get("fees", 0),
                    "authorization":         result["data"].get("authorization", {}),
                    "customer":              result["data"].get("customer", {}),
                    "transaction_date":      result["data"].get("transaction_date"),
                    "verification_timestamp": datetime.datetime.now().isoformat(),
                    "full_response":         result,
                }
                logger.info(f"Payment verified for {reference}: {result['data']['status']}")
                return True, payment_data
            else:
                current_status = (
                    result["data"]["status"] if "data" in result else "API_ERROR_NO_DATA"
                )
                logger.warning(
                    f"Payment not successful for {reference}. "
                    f"Paystack status: {current_status} — {result}"
                )
                return False, result.get("data", {})

        except requests.exceptions.HTTPError as http_err:
            logger.error(
                f"HTTP error verifying payment for {reference}: {http_err} — "
                f"{http_err.response.text}",
                exc_info=True,
            )
            return False, {"error": f"HTTPError: {http_err.response.text}"}
        except requests.RequestException as e:
            logger.error(f"Network error verifying payment for {reference}: {e}", exc_info=True)
            return False, {"error": f"RequestException: {e}"}
        except Exception as e:
            logger.error(f"Unexpected error verifying payment for {reference}: {e}", exc_info=True)
            return False, {"error": f"Unexpected Error: {e}"}