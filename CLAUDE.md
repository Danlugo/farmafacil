# FarmaFacil

Venezuela pharmacy drug finder with WhatsApp integration.

## Rules

- ALWAYS be truthful with the user. Provide facts, proof of work, and analysis. Never guess or assume.
- If something failed, say it failed. If you didn't verify something, say so.
- Include actual command output or logs as evidence of completed work.
- If you're uncertain about something, state that explicitly rather than presenting assumptions as facts.
- Never report a deployment, test run, or operation as successful without showing the actual output that proves it.

## Quick Start (Local Dev)

```bash
pip install -e ".[dev]"
cp .env.example .env
uvicorn farmafacil.api.app:app --reload
pytest
```

## Production Deployment (10.0.0.116)

```bash
# SSH to server
ssh -i ~/.ssh/id_ed25519 dgonzalez@10.0.0.116

# Deploy
cd ~/workspace/farmafacil && git pull
docker compose build --no-cache
docker compose down && docker compose up -d

# Verify
curl http://localhost:8100/health
docker compose logs -f app
```

**Server:** 10.0.0.116 (Linux Mint, 32GB RAM, Docker 29.1)
**Ports:** App=8100, Postgres=5433, ngrok=4040
**ngrok URL:** https://amparo-chromophoric-christia.ngrok-free.dev

## Architecture

- **FastAPI** backend with async endpoints
- **SQLAlchemy 2.0** async ORM (SQLite dev / PostgreSQL prod)
- **Farmatodo Algolia API** for drug search (no HTML scraping)
- **Farmacias SAAS VTEX API** for drug search (shared VTEXScraper base)
- **Locatel VTEX API** for drug search (shared VTEXScraper base)
- **WhatsApp Business Cloud API** (Meta) for bot
- **Claude Haiku** for AI role-based responses and intent classification
- **OpenStreetMap Nominatim** for geocoding zones
- **Pillow** for product grid image generation

## Key Paths

| Path | Purpose |
|------|---------|
| `src/farmafacil/api/` | FastAPI routes, app factory |
| `src/farmafacil/bot/` | WhatsApp webhook, handler, formatter |
| `src/farmafacil/scrapers/` | Pharmacy scrapers (Farmatodo via Algolia, SAAS via VTEX) |
| `src/farmafacil/services/` | Business logic, intent, AI roles/router/responder, geocode, cache, stores |
| `src/farmafacil/models/` | Pydantic schemas + SQLAlchemy ORM |
| `src/farmafacil/db/` | Database session, seed data |
| `tests/` | pytest test suite (539 tests) |
| `docs/` | Project documentation (see below) |

## Database Tables

| Table | Purpose |
|-------|---------|
| `users` | Phone, name, location, display preference, response mode override, chat debug override, last search log ID, cumulative token counters, per-model token/call counters (haiku, sonnet), onboarding step |
| `intent_keywords` | Bot keyword→action mappings (admin-editable) |
| `pharmacy_locations` | Physical store locations (generic, multi-chain) |
| `products` | Permanent product catalog (never deleted, only upserted) |
| `product_prices` | Per-location pricing with refresh timestamps (FK → products) |
| `product_keywords` | Inverted index of keyword tokens per product (FK → products CASCADE) for fast cross-chain matching (v0.12.6) |
| `search_queries` | Maps search query + city to product IDs for cache lookups |
| `app_settings` | Admin-editable config (cache TTL, response mode, etc.) |
| `conversation_logs` | Every inbound/outbound WhatsApp message |
| `search_logs` | Search analytics with user feedback (yes/no + detail) |
| `ai_roles` | AI personas with system prompts (admin-editable) |
| `ai_role_rules` | Behavioral rules per AI role (like rules/*.md) |
| `ai_role_skills` | Skill definitions per AI role (capabilities) |
| `user_memories` | Per-user AI memory (conversation context, preferences) |
| `user_feedback` | `/bug` and `/comentario` submissions — type, message, linked conversation log, reviewed flag |

## API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Health check |
| `GET /api/v1/search?q=losartan` | Drug search |
| `GET /api/v1/users` | View users |
| `GET /api/v1/conversations` | View message logs |
| `GET /api/v1/intents` | View/manage intent keywords |
| `POST /api/v1/intents` | Add intent keyword |
| `GET /api/v1/stats` | Usage stats (global or per-user) |
| `GET /admin/user-stats/{id}` | HTML stats dashboard per user |
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
2. Subclass `BaseScraper` (or `VTEXScraper` for VTEX-powered pharmacies), implement `pharmacy_name` and `search()`
3. Register in `src/farmafacil/services/search.py` → `ACTIVE_SCRAPERS`
4. Add tests in `tests/test_new_pharmacy_scraper.py`

**For VTEX pharmacies** (e.g., Locatel): subclass `VTEXScraper`, set `base_url`, and override `pharmacy_name`. See `src/farmafacil/scrapers/saas.py` as a minimal example.
