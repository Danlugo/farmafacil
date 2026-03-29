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
SCRAPER_USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
)
