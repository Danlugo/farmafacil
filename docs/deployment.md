# FarmaFacil — Deployment Guide

> Last Updated: 2026-03-30

## Local Development

See `CLAUDE.md` Quick Start section for the 3-command setup. This guide covers deeper configuration.

### Prerequisites

- Python 3.12+
- SQLite (dev default — no installation needed)
- `ngrok` account (for WhatsApp webhook testing)

### Setup

```bash
pip install -e ".[dev]"
cp .env.example .env
# Edit .env — add ANTHROPIC_API_KEY and WhatsApp credentials
uvicorn farmafacil.api.app:app --reload
```

The app defaults to SQLite at `./farmafacil.db`. No database setup required.

---

## Environment Variables

All variables live in `.env` (copy from `.env.example`).

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `DATABASE_URL` | SQLite path | No | Full SQLAlchemy URL. Use `postgresql+asyncpg://...` for production |
| `API_HOST` | `0.0.0.0` | No | Bind host |
| `API_PORT` | `8000` | No | Bind port |
| `LOG_LEVEL` | `INFO` | No | Python log level (DEBUG/INFO/WARNING/ERROR) |
| `SCRAPER_TIMEOUT` | `30` | No | HTTP timeout (seconds) for Algolia requests |
| `WHATSAPP_PHONE_NUMBER_ID` | *(empty)* | Yes | Meta Business phone number ID |
| `WHATSAPP_API_TOKEN` | *(empty)* | Yes | System User permanent token |
| `WHATSAPP_VERIFY_TOKEN` | `farmafacil_verify_2026` | Yes | Webhook verification token (set in Meta dashboard) |
| `ANTHROPIC_API_KEY` | *(empty)* | Yes | Claude Haiku API key |
| `LLM_MODEL` | `claude-haiku-4-5-20251001` | No | Anthropic model ID |
| `ADMIN_USERNAME` | `admin` | No | Admin dashboard username |
| `ADMIN_PASSWORD` | `LetsGoChiguires` | Yes (change) | Admin dashboard password |
| `ADMIN_SECRET_KEY` | *(insecure default)* | Yes (change) | Session signing key |

> **Security:** Change `ADMIN_PASSWORD` and `ADMIN_SECRET_KEY` before any deployment.

---

## Production Server

**Server:** `10.0.0.116` (Linux Mint, 32GB RAM, Docker 29.1)

| Service | Port | Notes |
|---------|------|-------|
| App (Docker) | `8100` → container `8000` | Exposed via `docker compose` |
| PostgreSQL (Docker) | `5433` → container `5432` | Data in `pgdata` named volume |
| ngrok agent | `4040` | Admin UI for tunnel inspection |

### SSH Access

```bash
ssh -i ~/.ssh/id_ed25519 dgonzalez@10.0.0.116
```

---

## Docker Deployment

The production stack uses Docker Compose with PostgreSQL.

### First Deployment

```bash
ssh -i ~/.ssh/id_ed25519 dgonzalez@10.0.0.116
cd ~/workspace/farmafacil
cp .env.example .env
# Edit .env — add real WhatsApp token, Anthropic key, admin credentials
docker compose build --no-cache
docker compose up -d
curl http://localhost:8100/health
```

### Updating the App

```bash
ssh -i ~/.ssh/id_ed25519 dgonzalez@10.0.0.116
cd ~/workspace/farmafacil && git pull
docker compose build --no-cache
docker compose down && docker compose up -d
curl http://localhost:8100/health
docker compose logs -f app
```

### Useful Docker Commands

```bash
docker compose logs -f app        # Stream app logs
docker compose logs -f db         # Stream DB logs
docker compose ps                  # Check service status
docker compose down                # Stop all services
docker compose exec app bash       # Shell into app container
docker compose exec db psql -U farmafacil farmafacil  # DB shell
```

### Volume Management

| Volume | Contents |
|--------|---------|
| `pgdata` | PostgreSQL data files |
| `app_data` | SQLite DB (unused in production with Postgres) |

Volumes persist across `docker compose down`. To wipe data:
```bash
docker compose down -v   # Removes volumes too — all data lost
```

---

## ngrok Configuration

ngrok exposes the local app to Meta's webhook servers.

### Start ngrok

```bash
ngrok http 8100
# or run as a service — check /etc/systemd/system/ngrok.service
```

The current public URL is: `https://amparo-chromophoric-christia.ngrok-free.dev`

ngrok admin UI: `http://10.0.0.116:4040`

### Update Webhook URL in Meta

When the ngrok URL changes (free plan rotates on restart):

1. Go to [Meta Business Manager](https://developers.facebook.com) → App → WhatsApp → Configuration
2. Set webhook URL to: `https://<ngrok-url>/webhook`
3. Set Verify Token to match `WHATSAPP_VERIFY_TOKEN` in `.env`
4. Subscribe to `messages` webhook field

---

## Updating the WhatsApp API Token

The app uses a **System User permanent token** — it does not expire like standard user tokens.

If rotation is needed:
1. Go to Meta Business Manager → Settings → System Users
2. Select the FarmaFacil system user
3. Generate a new token (select `whatsapp_business_messaging` permission)
4. Update `WHATSAPP_API_TOKEN` in `.env` on the server
5. Restart the app: `docker compose restart app`

---

## Database Notes

- **Local dev:** SQLite at `./farmafacil.db` (auto-created on first run)
- **Production:** PostgreSQL via Docker Compose (auto-connected via `DATABASE_URL` env override in `docker-compose.yml`)
- Schema is created automatically via `init_db()` on startup (SQLAlchemy `create_all`)
- No migration tool — schema changes require a manual `docker compose down -v` + rebuild for destructive changes

---

## Health Verification

```bash
curl http://localhost:8100/health
# Expected: {"status":"ok","version":"0.1.0"}
```

Check recent WhatsApp messages:
```bash
curl http://localhost:8100/api/v1/conversations?limit=10
```
