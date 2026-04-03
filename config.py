import os
from dotenv import load_dotenv
import logging

load_dotenv()

class Config:
    def __init__(self):
        # Database configuration
        self.DB_URL = os.getenv('DB_URL')
        self.DB_HOST = os.getenv('DB_HOST')
        self.DB_PORT = os.getenv('DB_PORT')
        self.DB_NAME = os.getenv('DB_NAME')
        self.DB_USER = os.getenv('DB_USER')
        self.DB_PASSWORD = os.getenv('DB_PASSWORD')
        self.DB_SSLMODE = os.getenv('DB_SSLMODE')

        # WhatsApp configuration
        self.WHATSAPP_ACCESS_TOKEN = os.getenv('WHATSAPP_ACCESS_TOKEN')
        self.WHATSAPP_PHONE_NUMBER_ID = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
        self.VERIFY_TOKEN = os.getenv('VERIFY_TOKEN')
        self.APP_SECRET = os.getenv('APP_SECRET')

        # Payment configuration
        self.PAYSTACK_SECRET_KEY = os.getenv('PAYSTACK_SECRET_KEY')
        self.PAYSTACK_PUBLIC_KEY = os.getenv('PAYSTACK_PUBLIC_KEY')
        self.PAYSTACK_WEBHOOK_SECRET = os.getenv('PAYSTACK_WEBHOOK_SECRET', 'paystack_webhook_secret_placeholder')
        self.SUBACCOUNT_CODE = "ACCT_iwv6csej0ra4k7g"
        self.SUBACCOUNT_PERCENTAGE = 1
        self.MERCHANT_PHONE_NUMBER = "2347082345056"
        self.MERCHANT_ID = os.getenv('MERCHANT_ID', '20')

        # Other services
        self.Maps_API_KEY = os.getenv('Maps_API_KEY')

        # Gemini AI configuration
        self.GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

        # Feature flags
        self.ENABLE_AI_FEATURES = os.getenv('ENABLE_AI_FEATURES', 'false').lower() == 'true'
        self.ENABLE_LOCATION_FEATURES = os.getenv('ENABLE_LOCATION_FEATURES', 'false').lower() == 'true'

        # Flask configuration
        self.FLASK_ENV = os.getenv('FLASK_ENV', 'development')
        self.FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
        self.APP_PORT = int(os.getenv('APP_PORT', 5000))
        self.CALLBACK_BASE_URL = os.getenv('CALLBACK_BASE_URL')

        # File paths
        self.DATA_DIR = os.getenv('DATA_DIR', 'data')
        self.LOGS_DIR = os.getenv('LOGS_DIR', 'logs')
        self.SUPABASE_URL = os.getenv('SUPABASE_URL')
        self.SUPABASE_SERVICE_KEY = os.getenv('SUPABASE_SERVICE_KEY')

        self.ORDER_DETAILS_FILE = os.getenv('ORDER_DETAILS_FILE', os.path.join(self.DATA_DIR, 'orders.json'))
        self.ENQUIRY_DETAILS_FILE = os.getenv('ENQUIRY_DETAILS_FILE', os.path.join(self.DATA_DIR, 'enquiries.json'))
        self.COMPLAINT_DETAILS_FILE = os.getenv('COMPLAINT_DETAILS_FILE', os.path.join(self.DATA_DIR, 'complaints.json'))
        self.LEAD_TRACKER_DATA_FILE = os.getenv('LEAD_TRACKER_DATA_FILE', os.path.join(self.DATA_DIR, 'leads.json'))
        self.PRODUCTS_FILE = os.getenv('PRODUCTS_FILE', os.path.join(self.DATA_DIR, 'products.json'))

        # Session configuration
        self.SESSION_TIMEOUT = int(os.getenv('SESSION_TIMEOUT', 3600))

        # Business details — Makinde Kitchen demo
        self.BUSINESS_NAME = os.getenv('BUSINESS_NAME', 'Makinde Kitchen')
        self.BUSINESS_SUPPORT_PHONE = os.getenv('BUSINESS_SUPPORT_PHONE', '+2348000000000')
        self.BUSINESS_EMAIL = os.getenv('BUSINESS_EMAIL', 'orders@makindekitchen.com')


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )