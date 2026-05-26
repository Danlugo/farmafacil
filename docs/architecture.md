# FarmaFacil — System Architecture

> Last Updated: 2026-03-31

## Component Overview

```
User (WhatsApp)        Group (WhatsApp)
      |                      |
      | Meta Webhooks        | Baileys (Chamo bot)
      v                      v
  [ngrok tunnel]      [Chamo container]
      |                      |
      v                      v
+-----------------------------------+
|     FastAPI Application           |
|     (uvicorn, port 8000)          |
|                                   |
|  +-----------+  +-------------+   |
|  | /webhook  |  | /api/v1/chat|   |
|  | GET/POST  |  | POST        |   |
|  +-----------+  +-------------+   |
|       |               |           |
|       |    proxy mode: collect    |
|       |    responses as JSON      |
|       v               v          |
|  +---------------------+         |
|  |   Bot Handler       |         |
|  |  (handler.py)       |         |
|  +---------------------+         |
|     |       |      |              |
|     v       v      v              |
|  Intent  Search  Users            |
|  Service Service Service          |
|     |       |                     |
|     v       v                     |
|  [Claude  [Algolia/VTEX API]     |
|   Haiku]                          |
|     |       |                     |
|     v       v                     |
|  +---------------------+         |
|  |  PostgreSQL / SQLite |         |
|  +---------------------+         |
+-----------------------------------+
      |
      v
  [Nominatim]      [WhatsApp Cloud API]
  (geocoding)       (send messages/images)
```

## WhatsApp Bot Message Flow

```
1. User sends message on WhatsApp
2. Meta delivers POST to /webhook
3. webhook.py deduplicates by wa_message_id (skips retried webhooks)
4. Extracts sender + text, logs to conversation_logs
5. handler.py: get_or_create_user() + validate_user_profile()
6. If onboarding not complete → rigid step-by-step wizard
7. If onboarding complete → resolve response_mode (hybrid or ai_only):
   **AI-only mode** (tool_use, v0.30.0):
   a. classify_with_tools() sends user message + 8 tool schemas to Anthropic
   b. Model returns tool_use block (search_drug, change_location, etc.)
   c. _dispatch_tool_use() routes to existing handler helpers
   **Hybrid mode** (default):
   a. Check DB keyword cache (instant, free)
   b. If ambiguous → AI role-based classification (classify_with_ai)
   c. Route by intent.action (greeting, help, drug_search, question, etc.)
```

## AI Role System

```
Message → Intent Keywords (fast, free)
  ↓ (no keyword match / complex)
Message → Role Router (lightweight LLM: picks role from ai_roles)
  ↓
Selected Role → Assemble prompt:
  • role.system_prompt (from ai_roles table)
  • role's rules (from ai_role_rules, sorted by sort_order)
  • role's skills (from ai_role_skills)
  • client memory (from user_memories)
  ↓
Full LLM call → Response
  ↓
Auto-update client memory (non-blocking)
```

### AI Tables

| Table | Purpose | Analogy |
|-------|---------|---------|
| `ai_roles` | AI personas with system prompts | CLAUDE.md |
| `ai_role_rules` | Behavioral rules per role | rules/*.md |
| `ai_role_skills` | Capability definitions per role | Skills |
| `user_memories` | Per-user conversation context | Project memory |

### Default Roles

| Role | Slug | Purpose |
|------|------|---------|
| Asesor de Farmacia | `pharmacy_advisor` | Drug search, health questions, price comparison |
| Soporte de App | `app_support` | App help, feature explanations, troubleshooting |

All roles, rules, skills, and user memories are editable via `/admin`.
```

### Admin UI Convention (v0.34.0)

**Rule: Every field with a known set of valid values MUST use a SelectField dropdown — never a free-text input.**

Pattern for ModelView classes in `api/admin.py`:
1. Define choices as `list[tuple[str, str]]` constant (e.g., `PHARMACY_CHAIN_CHOICES`)
2. Add `form_overrides = {"field": SelectField}` on the ModelView
3. Add `form_args = {"field": {"choices": CHOICES, "coerce": str}}` (use `_coerce_optional_str` for nullable fields)
4. For key-value tables (like `AppSetting`) where valid values depend on the row, use `on_model_change` server-side validation with a `SETTING_VALUE_CHOICES` dict

Current dropdown fields: `IntentKeyword.action`, `PharmacyLocation.pharmacy_chain`, `PharmacyLocation.city_code`, `Product.pharmacy_chain`, `ScheduledTask.task_key`, `User.city_code`, `User.display_preference`, `User.response_mode`, `User.chat_debug`, `User.onboarding_step`, `User.post_feedback_*`.

## External Services

| Service | Purpose | Auth |
|---------|---------|------|
| Farmatodo Algolia | Product search index | Static API key (public) |
| Farmacias SAAS VTEX | Product search (Intelligent Search API) | No auth required |
| Locatel VTEX | Product search (Intelligent Search API) | No auth required |
| WhatsApp Business Cloud API | Send/receive messages | WHATSAPP_API_TOKEN (System User) |
| OpenStreetMap Nominatim | Geocode Venezuelan zones | No key — User-Agent header |
| Anthropic Claude Haiku | Intent detection fallback | ANTHROPIC_API_KEY |
| OpenAI Whisper API | Voice message transcription (v0.22.0) | OPENAI_API_KEY |
| NIH RxNorm/RxNav API | Drug interaction checking | No auth required |

## Database Schema

### users
Primary table for WhatsApp bot users.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| phone_number | VARCHAR(20) UNIQUE | WhatsApp number with country code |
| name | VARCHAR(100) NULL | User's display name |
| latitude / longitude | FLOAT NULL | Geocoded coordinates |
| zone_name | VARCHAR(100) NULL | Neighborhood (e.g., "El Cafetal") |
| city_code | VARCHAR(10) NULL | Farmatodo city code (e.g., "CCS") |
| display_preference | VARCHAR(20) | "grid" or "detail" (default: grid) |
| onboarding_step | VARCHAR(30) NULL | NULL = complete; else current step |
| created_at / updated_at | DATETIME | Timestamps |

### products
Permanent product catalog — never deleted, only upserted.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| external_id | VARCHAR(200) | Algolia objectID / URL slug |
| pharmacy_chain | VARCHAR(100) | e.g., "Farmatodo" |
| drug_name | VARCHAR(300) | Product display name |
| brand | VARCHAR(200) NULL | Manufacturer/brand |
| image_url | VARCHAR(500) NULL | Product image URL |
| drug_class | VARCHAR(100) NULL | Pharmacological class |
| requires_prescription | BOOLEAN | True if Rx required |
| unit_count / unit_label | INT / VARCHAR NULL | Pack size info |
| product_url | VARCHAR(500) NULL | Link to product page |

### product_prices
Per-city pricing, updated on each search refresh.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| product_id | INTEGER FK | → products.id (CASCADE DELETE) |
| city_code | VARCHAR(10) | Farmatodo city code |
| full_price_bs / offer_price_bs | NUMERIC(18,2) NULL | Prices in Bolivares |
| discount_pct | VARCHAR(20) NULL | Discount text (e.g., "20%") |
| in_stock | BOOLEAN | Current stock status |
| stores_in_stock_count | INTEGER | Number of stores with stock |
| stores_with_stock_ids | JSON NULL | Store ID list |
| refreshed_at | DATETIME | Last price update |

Unique constraint: `(product_id, city_code)`

### search_queries
Maps normalized search terms to product ID lists (cache lookup index).

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| query | VARCHAR(200) | Normalized (lowercase, stripped) |
| city_code | VARCHAR(10) NULL | City scope |
| product_ids | JSON NULL | Ordered list of product.id |
| searched_at | DATETIME | When this query was last executed |

Unique constraint: `(query, city_code)`

### intent_keywords
Admin-editable keyword-to-intent mappings. Cached in memory (5 min TTL).

| Column | Type | Description |
|--------|------|-------------|
| action | VARCHAR(50) | greeting, help, location_change, etc. |
| keyword | VARCHAR(200) UNIQUE | Exact lowercase match |
| response | TEXT NULL | Canned response text |
| is_active | BOOLEAN | Soft delete |

### pharmacy_locations
Physical store locations, backfilled from Farmatodo API on startup.

| Column | Type | Description |
|--------|------|-------------|
| external_id | VARCHAR(50) | Farmatodo store ID |
| pharmacy_chain | VARCHAR(100) | e.g., "Farmatodo" |
| name / name_lower | VARCHAR(100) | Store name + lowercase variant |
| city_code | VARCHAR(10) | Farmatodo city code |
| address | TEXT NULL | Street address |
| latitude / longitude | FLOAT NULL | GPS coordinates |

### Supporting Tables

| Table | Purpose |
|-------|---------|
| conversation_logs | Every inbound/outbound WhatsApp message (direction, type, wa_message_id) |
| search_logs | Analytics — query, result count, source (whatsapp/api) |
| app_settings | Admin-editable key/value config (cache TTL, etc.) |
| product_keywords | Inverted index of keyword tokens per product (FK → products CASCADE). Added in v0.12.6 (Item 30) so `find_cross_chain_matches` can run a single indexed SQL query (`WHERE keyword IN (...) GROUP BY product_id HAVING COUNT(DISTINCT keyword) = N`) instead of loading every product with keywords into memory. Populated automatically by `_upsert_product → _sync_product_keywords`; idempotent backfill in `init_db()` for existing deployments. |
| voice_messages | Voice notes: audio_path, transcription (Whisper), language, duration, FK→users + conversation_logs. Translation columns (`translation_es`, `translation_en`) are shell for future use (v0.22.0). Voice-to-action linking (v0.22.1): `voice_message_id` FK added to `search_logs`, `user_feedback`, `user_suggestions` to connect voice notes to the actions they triggered. |

## Product Catalog Design

On every cache miss, the search flow is:

```
Algolia API → DrugResult list
    → save_search_results()
        → upsert Product (by external_id + pharmacy_chain)
        → upsert ProductPrice (by product_id + city_code)
        → upsert SearchQuery (query + city → product_ids list)
```

On cache hit, `get_cached_results()`:
1. Looks up `search_queries` by (query, city_code)
2. Checks if `searched_at` is within the TTL (default: 60 min, set in `app_settings`)
3. Loads `products` + `product_prices` by ID, reconstructs `DrugResult` list

Products are never deleted. Prices refresh on every real search. This gives a growing historical catalog.

## Search Relevance Filter (Item 38, hardened in v0.20.1)

Pharmacy APIs (Algolia, VTEX) return products by text similarity, not medical relevance. Without filtering, query "Aspirina" can come back with "Aspirador Nasal Infantil" because both share the prefix "aspir-" under typo tolerance. `services/relevance.py` is the post-filter that gates results:

`compute_relevance(query, name, drug_class, description, brand)` returns 0.0–1.0 from four signals (in evaluation order):

1. **Token-overlap floor (Q6, v0.20.1)** — at least one normalized query token must appear as a *whole token* in the product `drug_name` OR `brand`. No overlap → score 0.0 regardless of category. This is the gate that stopped Algolia returning baby aspirators for "Aspirina".
2. **Token overlap (0.0–0.5)** — fraction of query tokens present in name (or brand, whichever is larger).
3. **Pharmaceutical category (+0.3 / +0.15 / +0.0)** — bonus from `drug_class`; non-pharma classes in `NON_PHARMA_CATEGORIES` (cleaning, food, diapers, etc.) get nothing.
4. **Active ingredient match (+0.2)** — query tokens minus form words ("pastillas", "jarabe", etc.) intersect with name/brand.

`is_relevant(...)` thresholds at `app_settings.relevance_threshold` (default 0.3). Called from two places:

- `services/search.py` — final filter on results before they're returned to the user (covers cached AND fresh data).
- `services/product_cache.py` — gates which product IDs get cached in `search_queries` per query string.

## Startup Sequence

On application start (`lifespan` in `app.py`):
1. `init_db()` — create all tables via SQLAlchemy
2. `seed_intents()` — insert default keywords if table is empty
3. `seed_settings()` — insert default app settings
4. `backfill_stores()` — fetch Farmatodo store locations (all 18 cities), upsert

## Performance Architecture (v0.24.0)

- **Async LLM client:** Module-level `AsyncAnthropic` singleton in `ai_responder._get_client()` — reuses httpx connection pool across all 8 LLM call sites, non-blocking `await` on event loop.
- **Settings cache:** In-memory `{key: (value, expire_ts)}` dict with 60s TTL in `settings.py`. Invalidated on writes (`set_setting`, `set_default_model`). Avoids 8+ DB round-trips per message.
- **Non-blocking webhook:** `webhook.py` returns 200 to Meta immediately; handler runs as `asyncio.create_task()` via `_fire_and_forget()` with `_safe_handle()` error wrapper. Dedup check + `log_inbound` remain synchronous.
- **Direct UPDATE:** `set_onboarding_step` and `update_last_search` use `update(User).where().values()` — no SELECT first.
- **Pool pre-ping:** Postgres engine uses `pool_pre_ping=True` to detect and replace stale connections transparently.
- **Filename sanitization:** `_sanitize_filename_part()` in routes.py strips non-alphanumeric chars from Content-Disposition filenames via allowlist regex.

## Chat Relay API (v0.27.0)

The `/api/v1/chat` endpoint enables external bots (e.g. Chamo) to relay WhatsApp group messages through the full FarmaFacil handler without using Meta's WhatsApp Business API test numbers.

**How it works:** A `contextvars.ContextVar` in `whatsapp.py` intercepts all outbound `send_*` calls. When proxy mode is active (via `start_collecting()`), messages are appended to a list as structured dicts instead of being sent to the WhatsApp Cloud API. The `/api/v1/chat` endpoint enters proxy mode, calls `handle_incoming_message()`, then returns all collected responses as JSON.

**Key files:**
- `bot/whatsapp.py` — `_response_collector` ContextVar, `start_collecting()`, `stop_collecting()`; intercepted: `send_text_message`, `send_image_message`, `send_interactive_list`, `send_read_receipt` (no-op)
- `api/routes.py` — `ChatRequest`, `ChatResponseItem`, `ChatResponse` models; `POST /api/v1/chat` endpoint
- `docs/chamo-farmafacil-skill.md` — complete integration instructions for the Chamo relay bot

**Design decisions:**
- Zero changes to `handler.py` — proxy mode is transparent to all 114+ `send_*` call sites
- `send_read_receipt` becomes a no-op in proxy mode (read receipts are meaningless for relay bots)
- `send_local_image` (product grid) is NOT intercepted — these use `media_id` uploads which relay bots cannot use; the text summary that follows contains equivalent info
- No auth required — matches the existing `/api/v1/search` endpoint; Chamo connects via localhost

## Supported Cities

Farmatodo city codes used throughout the system:

| Code | City |
|------|------|
| CCS | Caracas (default) |
| MCBO | Maracaibo |
| VAL | Valencia |
| BAR | Barquisimeto |
| MAT | Maracay |
| MER | Merida |
| PTO | Puerto Ordaz |
| SAC | San Cristobal |
| PDM | Puerto La Cruz |
| POR | Porlamar |
| PTC | Punto Fijo |
| GUAC | Guarenas/Guatire |
