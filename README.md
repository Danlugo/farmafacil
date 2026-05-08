# FarmaFacil

A WhatsApp-based pharmacy drug finder for Venezuela. Users text a drug name or describe symptoms, and the bot searches multiple pharmacy chains in real time, returning availability, pricing, and nearby store locations.

## What It Does

- **Drug search** — Queries Farmatodo, Farmacias SAAS, and Locatel simultaneously, returning product cards with prices in Bolivares and stock status
- **Symptom understanding** — Users can describe what they feel (e.g., "me duele la cabeza") and the bot suggests appropriate OTC products
- **Nearby pharmacies** — Finds the closest stores using OpenStreetMap data (1,500+ locations including independent pharmacies)
- **Conversational AI** — Claude-powered intent detection handles greetings, follow-ups, clarification questions, and location changes naturally in Spanish
- **Image recognition** — Users can photograph a prescription or drug box, and the bot extracts product names via Vision API
- **Admin dashboard** — Web UI for managing users, AI roles, keywords, feedback, and app settings

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0 (async) |
| Database | SQLite (dev), PostgreSQL 16 (production) |
| Bot | WhatsApp Business Cloud API (Meta Graph API v22.0) |
| AI | Anthropic Claude (Haiku for users, Opus for admin) |
| Pharmacy data | Farmatodo Algolia API, SAAS/Locatel VTEX APIs |
| Geocoding | OpenStreetMap Nominatim + Overpass API |
| Admin UI | SQLAdmin |
| Deployment | Docker Compose |

## Project Structure

```
src/farmafacil/
├── api/           # FastAPI routes, admin dashboard, rate limiting
├── bot/           # WhatsApp webhook, message handler, response formatter
├── scrapers/      # Pharmacy chain integrations (Farmatodo, SAAS, Locatel)
├── services/      # Business logic — search, AI, geocoding, scheduling
├── models/        # SQLAlchemy ORM models + Pydantic schemas
└── db/            # Database session and seed data
```

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env with your API keys (WhatsApp, Anthropic, admin credentials)

# Run
uvicorn farmafacil.api.app:app --reload

# Test
pytest
```

## Docker Deployment

The application is deployed using Docker Compose with two services: the app and a PostgreSQL 16 database.

### Build and Run

```bash
# Build and start all services
docker compose up -d --build

# Rebuild from scratch (no cache)
docker compose build --no-cache
docker compose down && docker compose up -d

# View logs
docker compose logs -f app

# Stop
docker compose down
```

### Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `app` | Custom (Python 3.12-slim) | 8000 | FastAPI application |
| `db` | postgres:16-alpine | 5432 | PostgreSQL database |

### Architecture

- **Multi-stage build** — dependencies are installed in a builder stage, producing a slim runtime image without build tools
- **Non-root user** — the app runs as a dedicated `farmafacil` user inside the container
- **Health checks** — both the app (`/health` endpoint, 30s interval) and the database (`pg_isready`) have built-in health checks
- **Persistent volumes** — `pgdata` for PostgreSQL data and `app_data` for application files survive container restarts
- **Dependency ordering** — the app waits for the database health check to pass before starting

### Updating a Running Deployment

```bash
git pull
docker compose build --no-cache
docker compose down && docker compose up -d

# Verify
curl http://localhost:8000/health
docker compose logs --tail=20 app
```

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Required | Purpose |
|----------|----------|---------|
| `WHATSAPP_API_TOKEN` | Yes | Meta Graph API token |
| `WHATSAPP_PHONE_NUMBER_ID` | Yes | WhatsApp Business phone ID |
| `ANTHROPIC_API_KEY` | Yes | Claude API for AI features |
| `ADMIN_PASSWORD` | Yes | Admin dashboard login |
| `ADMIN_SECRET_KEY` | Yes | Session signing key |
| `DATABASE_URL` | No | Defaults to SQLite for local dev |

## License

Private — all rights reserved.
