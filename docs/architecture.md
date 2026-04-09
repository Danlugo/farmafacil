# FarmaFacil — System Architecture

> Last Updated: 2026-03-31

## Component Overview

```
User (WhatsApp)
      |
      | HTTPS (Meta Webhooks)
      v
  [ngrok tunnel]
      |
      v
+---------------------------+
|   FastAPI Application     |
|   (uvicorn, port 8000)    |
|                           |
|  +---------------------+  |
|  |  WhatsApp Webhook   |  |
|  |  /webhook GET/POST  |  |
|  +---------------------+  |
|           |                |
|           v                |
|  +---------------------+  |
|  |   Bot Handler       |  |
|  |  (handler.py)       |  |
|  +---------------------+  |
|     |       |      |       |
|     v       v      v       |
|  Intent  Search  Users    |
|  Service Service Service  |
|     |       |              |
|     v       v              |
|  [Claude  [Algolia API]  [VTEX API] |
|   Haiku]                  |
|     |       |              |
|     v       v              |
|  +---------------------+  |
|  |   PostgreSQL / SQLite|  |
|  +---------------------+  |
+---------------------------+
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
7. If onboarding complete:
   a. Check DB keyword cache (instant, free)
   b. If ambiguous → AI role-based classification (classify_with_ai)
8. Route by intent.action:
   - greeting   → send welcome-back message
   - help        → send HELP_MESSAGE
   - drug_search → search_drug() → format results → send text + image
   - question    → try store lookup, else AI responder (role-based)
   - unknown     → AI responder (role-based)
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

## External Services

| Service | Purpose | Auth |
|---------|---------|------|
| Farmatodo Algolia | Product search index | Static API key (public) |
| Farmacias SAAS VTEX | Product search (Intelligent Search API) | No auth required |
| Locatel VTEX | Product search (Intelligent Search API) | No auth required |
| WhatsApp Business Cloud API | Send/receive messages | WHATSAPP_API_TOKEN (System User) |
| OpenStreetMap Nominatim | Geocode Venezuelan zones | No key — User-Agent header |
| Anthropic Claude Haiku | Intent detection fallback | ANTHROPIC_API_KEY |
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
| product_cache | DEPRECATED — replaced by products/product_prices/search_queries |

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

## Startup Sequence

On application start (`lifespan` in `app.py`):
1. `init_db()` — create all tables via SQLAlchemy
2. `seed_intents()` — insert default keywords if table is empty
3. `seed_settings()` — insert default app settings
4. `backfill_stores()` — fetch Farmatodo store locations (all 18 cities), upsert

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
