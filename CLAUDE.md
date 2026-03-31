# FarmaFacil

Venezuela pharmacy drug finder with WhatsApp integration.

## Quick Start (Local Dev)

```bash
pip install -e ".[dev]"
cp .env.example .env
uvicorn farmafacil.api.app:app --reload
pytest
```

## Production Deployment (10.0.0.114)

```bash
# SSH to server
ssh -i ~/.ssh/id_ed25519 dgonzalez@10.0.0.114

# Deploy
cd ~/workspace/farmafacil && git pull
docker compose build --no-cache
docker compose down && docker compose up -d

# Verify
curl http://localhost:8100/health
docker compose logs -f app
```

**Server:** 10.0.0.114 (Linux Mint, 32GB RAM, Docker 29.1)
**Ports:** App=8100, Postgres=5433, ngrok=4040
**ngrok URL:** https://amparo-chromophoric-christia.ngrok-free.dev

## Architecture

- **FastAPI** backend with async endpoints
- **SQLAlchemy 2.0** async ORM (SQLite dev / PostgreSQL prod)
- **Farmatodo Algolia API** for drug search (no HTML scraping)
- **WhatsApp Business Cloud API** (Meta) for bot
- **Claude Haiku** for intent detection fallback
- **OpenStreetMap Nominatim** for geocoding zones
- **Pillow** for product grid image generation

## Key Paths

| Path | Purpose |
|------|---------|
| `src/farmafacil/api/` | FastAPI routes, app factory |
| `src/farmafacil/bot/` | WhatsApp webhook, handler, formatter |
| `src/farmafacil/scrapers/` | Pharmacy scrapers (Farmatodo via Algolia) |
| `src/farmafacil/services/` | Business logic, intent, geocode, cache, stores |
| `src/farmafacil/models/` | Pydantic schemas + SQLAlchemy ORM |
| `src/farmafacil/db/` | Database session, seed data |
| `tests/` | pytest test suite (80 tests) |
| `docs/` | Project documentation (see below) |

## Database Tables

| Table | Purpose |
|-------|---------|
| `users` | Phone, name, location, display preference, onboarding step |
| `intent_keywords` | Bot keyword→action mappings (admin-editable) |
| `pharmacy_locations` | Physical store locations (generic, multi-chain) |
| `products` | Permanent product catalog (never deleted, only upserted) |
| `product_prices` | Per-location pricing with refresh timestamps (FK → products) |
| `search_queries` | Maps search query + city to product IDs for cache lookups |
| `product_cache` | DEPRECATED — legacy cache (replaced by products/product_prices/search_queries) |
| `app_settings` | Admin-editable config (cache TTL, etc.) |
| `conversation_logs` | Every inbound/outbound WhatsApp message |
| `search_logs` | Search analytics |

## API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Health check |
| `GET /api/v1/search?q=losartan` | Drug search |
| `GET /api/v1/users` | View users |
| `GET /api/v1/conversations` | View message logs |
| `GET /api/v1/intents` | View/manage intent keywords |
| `POST /api/v1/intents` | Add intent keyword |
| `GET /webhook` | WhatsApp verification |
| `POST /webhook` | WhatsApp incoming messages |

## Documentation

| Doc | Purpose |
|-----|---------|
| `docs/architecture.md` | System components, bot flow, DB schema, product catalog design |
| `docs/api-reference.md` | All API endpoints, request/response formats, admin dashboard |
| `docs/deployment.md` | Local dev, Docker production, ngrok, env vars, WhatsApp token |
| `docs/bot-flow.md` | Onboarding steps, intent detection, drug search, store lookup |
| `docs/adding-pharmacies.md` | BaseScraper interface, step-by-step guide, product catalog integration |
| `docs/troubleshooting.md` | WhatsApp token, ngrok styling, profile corruption, geocoding, LLM errors |

## Adding a New Pharmacy Scraper

See `docs/adding-pharmacies.md` for the full guide. Summary:

1. Create `src/farmafacil/scrapers/new_pharmacy.py`
2. Subclass `BaseScraper`, implement `pharmacy_name` and `search()`
3. Register in `src/farmafacil/services/search.py` → `ACTIVE_SCRAPERS`
4. Add tests in `tests/test_new_pharmacy_scraper.py`
