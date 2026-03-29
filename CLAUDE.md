# FarmaFacil

Venezuela pharmacy drug finder with WhatsApp integration.

## Quick Start

```bash
# Install dependencies
pip install -e ".[dev]"

# Copy env and edit as needed
cp .env.example .env

# Run the API (SQLite by default, no Docker needed)
uvicorn farmafacil.api.app:app --reload

# Run tests
pytest
```

## Architecture

- **FastAPI** backend with async endpoints
- **SQLAlchemy 2.0** async ORM (SQLite dev / PostgreSQL prod)
- **Scraper pattern**: `BaseScraper` → per-pharmacy implementations (Farmatodo first)
- **Service layer**: `services/search.py` orchestrates all scrapers

## Key Paths

| Path | Purpose |
|------|---------|
| `src/farmafacil/api/` | FastAPI routes and app factory |
| `src/farmafacil/scrapers/` | Pharmacy website scrapers |
| `src/farmafacil/services/` | Business logic |
| `src/farmafacil/models/` | Pydantic schemas + SQLAlchemy ORM |
| `src/farmafacil/db/` | Database session and engine |
| `tests/` | pytest test suite |

## Database

Default: SQLite (`farmafacil.db` in project root).
To switch to PostgreSQL: set `DATABASE_URL=postgresql+asyncpg://...` in `.env` and install `pip install asyncpg`.

## Adding a New Pharmacy Scraper

1. Create `src/farmafacil/scrapers/new_pharmacy.py`
2. Subclass `BaseScraper`, implement `pharmacy_name` and `search()`
3. Register in `src/farmafacil/services/search.py` → `ACTIVE_SCRAPERS`
4. Add tests in `tests/test_new_pharmacy_scraper.py`
