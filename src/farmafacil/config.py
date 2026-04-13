"""Application configuration loaded from environment variables."""

import os

from dotenv import load_dotenv

load_dotenv()

# Database — default is SQLite for local dev; swap to Postgres via env var
DATABASE_URL = os.getenv(
    "DATABASE_URL", "sqlite+aiosqlite:///farmafacil.db"
)

# API
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Scraper
SCRAPER_TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", "30"))

# Farmatodo Algolia API
ALGOLIA_APP_ID = os.getenv("ALGOLIA_APP_ID", "VCOJEYD2PO")
ALGOLIA_API_KEY = os.getenv("ALGOLIA_API_KEY", "869a91e98550dd668b8b1dc04bca9011")
ALGOLIA_INDEX = os.getenv("ALGOLIA_INDEX", "products-venezuela")
SCRAPER_USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
)

# WhatsApp Business API
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_API_TOKEN = os.getenv("WHATSAPP_API_TOKEN", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "farmafacil_verify_2026")
WHATSAPP_API_URL = f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"

# LLM (Claude Haiku for intent detection and conversational fallback)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")
LLM_MODEL_ELEVATED = os.getenv("LLM_MODEL_ELEVATED", "claude-sonnet-4-20250514")
LLM_MODEL_OPUS = os.getenv("LLM_MODEL_OPUS", "claude-opus-4-1-20250805")

# Aliases used by admin chat `/model <alias>` and the default_model app_setting.
# Keys must match `services.settings.VALID_MODEL_ALIASES`.
MODEL_ALIASES: dict[str, str] = {
    "haiku": LLM_MODEL,
    "sonnet": LLM_MODEL_ELEVATED,
    "opus": LLM_MODEL_OPUS,
}

# Web search (Brave API — admin chat tool)
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")

# Admin dashboard
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "LetsGoChiguires")
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "farmafacil-admin-secret-key-change-me")
