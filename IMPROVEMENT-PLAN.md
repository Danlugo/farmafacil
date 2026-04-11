# FarmaFacil — Improvement Plan

Tracks planned improvements, new features, and technical debt. Items are prioritized P0–P3 and executed via `/farmafacil-update`.

**Status legend:** PENDING | IN PROGRESS | DONE | DEFERRED

---

## P1 — High

### Item 1: Farmacias SAAS Scraper (VTEX GraphQL)

- **Status:** DONE
- **Added:** 2026-03-30
- **Completed:** 2026-03-30
- **Problem:** FarmaFacil only searches Farmatodo. Farmacias SAAS is a major Venezuelan pharmacy chain with an online catalog at farmaciasaas.com powered by VTEX, exposing a GraphQL API for product search.
- **Solution implemented:** Created `VTEXScraper` base class (`src/farmafacil/scrapers/vtex.py`) and `SAASScraper` subclass (`src/farmafacil/scrapers/saas.py`). Uses the VTEX Intelligent Search REST endpoint. Registered in `ACTIVE_SCRAPERS`. The VTEX base class is reusable for Locatel (Item 2).
- **Files changed:** `src/farmafacil/scrapers/vtex.py` (new), `src/farmafacil/scrapers/saas.py` (new), `src/farmafacil/services/search.py` (modified), `tests/test_saas_scraper.py` (new, 13 unit + 2 integration tests), `pyproject.toml` (integration marker)
- **Notes:** VTEX API is public, no auth needed. Endpoint: `GET /api/io/_v/api/intelligent-search/product_search/?query=<drug>`. Returns product name, price, list price, discount, image, availability, brand, categories.

### Item 2: Locatel Scraper (VTEX)

- **Status:** DONE
- **Added:** 2026-03-30
- **Completed:** 2026-04-09
- **Problem:** Locatel is one of Venezuela's largest pharmacy/retail chains with an online catalog at locatel.com.ve, also powered by VTEX. Adding Locatel significantly expands drug availability coverage.
- **Solution implemented:** Created `LocatelScraper` subclass of `VTEXScraper` (~19 lines) with `base_url = "https://www.locatel.com.ve"`. Registered in `ACTIVE_SCRAPERS` (now 3 scrapers: Farmatodo, SAAS, Locatel). Added Locatel store backfill via VTEX pickup-points API (~8 stores across Caracas + Valencia). Renamed shared constants from SAAS-specific to VTEX-generic (`SAAS_GEO_CENTERS` → `VTEX_GEO_CENTERS`, `_map_saas_city` → `_map_vtex_city`).
- **Files created:** `src/farmafacil/scrapers/locatel.py` (new), `tests/test_locatel_scraper.py` (new — 14 unit + 2 integration tests)
- **Files modified:** `src/farmafacil/services/search.py` (register scraper), `src/farmafacil/services/store_backfill.py` (Locatel store backfill + renamed shared constants)
- **Notes:** Locatel VTEX API is public, no auth needed. Same endpoint pattern as SAAS. Product categories in Locatel are broader (Farmacia > MEDICAMENTOS) vs SAAS (Medicamentos > Cardiovascular > Antihipertensivos).

### Item 16: Per-Model Token Tracking & Call Counts

- **Status:** DONE
- **Added:** 2026-04-09
- **Completed:** 2026-04-09
- **Problem:** All LLM tokens are tracked as flat counters (`total_tokens_in`/`total_tokens_out`) with cost estimated at Haiku rates. When we switch to Sonnet/Opus for complex medical responses, cost estimates become inaccurate and we lose visibility into per-model usage patterns. Need per-model token tracking, call counts, and accurate cost calculation.
- **Solution implemented:** Added 6 per-model columns to User model (`tokens_in_haiku`, `tokens_out_haiku`, `calls_haiku`, `tokens_in_sonnet`, `tokens_out_sonnet`, `calls_sonnet`). Updated `increment_token_usage()` with `_classify_model()` helper to route tokens to correct per-model counters. Added `MODEL_PRICING` dict with Haiku/Sonnet/Opus rates. `estimate_cost()` now accepts model parameter. Global `/api/v1/stats` returns per-model breakdown with costs. Debug footer shows per-model call counts and accurate per-model global cost. Admin dashboard shows call counts.
- **Files modified:** `src/farmafacil/models/database.py` (6 columns), `src/farmafacil/services/users.py` (`_classify_model`, `increment_token_usage`), `src/farmafacil/services/chat_debug.py` (`MODEL_PRICING`, `estimate_cost`, `estimate_cost_breakdown`, `get_user_stats`, `build_debug_footer`), `src/farmafacil/bot/handler.py` (6 increment call sites + /stats command), `src/farmafacil/api/routes.py` (per-model stats), `src/farmafacil/api/admin.py` (call count columns), `tests/test_chat_debug.py` (16 new tests), `tests/test_usage_stats.py` (5 new tests)
- **Migration SQL:** `ALTER TABLE users ADD COLUMN tokens_in_haiku INTEGER DEFAULT 0; ALTER TABLE users ADD COLUMN tokens_out_haiku INTEGER DEFAULT 0; ALTER TABLE users ADD COLUMN calls_haiku INTEGER DEFAULT 0; ALTER TABLE users ADD COLUMN tokens_in_sonnet INTEGER DEFAULT 0; ALTER TABLE users ADD COLUMN tokens_out_sonnet INTEGER DEFAULT 0; ALTER TABLE users ADD COLUMN calls_sonnet INTEGER DEFAULT 0;`

### Item 4: Farmahorro Scraper

- **Status:** CANCELLED
- **Added:** 2026-03-30
- **Cancelled:** 2026-04-09
- **Problem:** Farmahorro (farmahorro.com.ve) was a major Venezuelan pharmacy chain. Adding Farmahorro would have expanded coverage to the popular/mass-market segment.
- **Cancellation reason:** Farmahorro was acquired by DRONENA (owners of Farmacias XANA) in September 2025. All 89 stores across 37 cities were permanently closed. The domain farmahorro.com.ve has broken DNS (SERVFAIL) — site is offline. The successor (Farmacias XANA) has no public e-commerce website or product search API (wxana.com returns 403). DRONENA is B2B-only (pharmaceutical distributor).
- **Notes:** Investigated 2026-04-09: DNS SERVFAIL on farmahorro.com.ve, wxana.com returns 403, xana.com returns 403. Wikipedia confirms dissolution in 2025. No viable API to scrape.
- **Replacement:** See Item 17 (Farmarebajas Scraper) — discovered during investigation as a viable alternative with a public WooCommerce Store API.

### Item 18: Concurrent Scraper Execution

- **Status:** DONE
- **Added:** 2026-04-10
- **Completed:** 2026-04-10
- **Priority:** P1
- **Problem:** Scrapers run sequentially in a `for` loop (`search.py:209`). With 3 scrapers at up to 30s timeout each, worst-case response time is ~90 seconds. Users wait unnecessarily long for drug search results.
- **Solution implemented:** Replaced sequential `for scraper in ACTIVE_SCRAPERS` loop with `asyncio.gather(*tasks, return_exceptions=True)`. All 3 scrapers now execute concurrently — worst-case response time drops from ~90s to ~30s (max of individual scraper times). Per-scraper errors are isolated: one failure doesn't cancel others. Snapshots `ACTIVE_SCRAPERS` into a local `scrapers` variable before gather for safe zip pairing.
- **Files modified:** `src/farmafacil/services/search.py` (sequential → concurrent with asyncio.gather)
- **Files created:** `tests/test_search_concurrent.py` (11 tests: concurrent timing, error isolation, cache bypass, empty list, empty scraper list)

### Item 19: Move Algolia Credentials to Env Vars

- **Status:** DONE
- **Added:** 2026-04-10
- **Completed:** 2026-04-09
- **Priority:** P1
- **Problem:** Farmatodo Algolia API key and app ID are hardcoded in `farmatodo.py:15-16` and committed to Git. Security risk — credentials should be in `.env` and loaded via config.
- **Solution implemented:** Moved `ALGOLIA_APP_ID`, `ALGOLIA_API_KEY`, and `ALGOLIA_INDEX` to `config.py` as env vars with defaults (public search-only key, same as visible in Farmatodo's frontend JS). Updated `farmatodo.py` to import from config. Added placeholders to `.env.example`. Added 3 config tests.
- **Files modified:** `src/farmafacil/config.py` (3 new env vars), `src/farmafacil/scrapers/farmatodo.py` (import from config), `.env.example` (Algolia placeholders), `tests/test_farmatodo_scraper.py` (3 new tests)

### Item 20: N+1 Query Fix in Product Cache

- **Status:** DONE (2026-04-10, no code change — already solved)
- **Added:** 2026-04-10
- **Priority:** P2
- **Problem:** `product_cache.py` loads products then loops through `product.prices` without eager loading — triggers 100+ individual SQL queries for 100 products. Causes unnecessary DB load and latency on cached searches.
- **Finding (2026-04-10):** The premise was wrong. `Product.prices` already has `lazy="selectin"` configured at `src/farmafacil/models/database.py:294-296`, which makes SQLAlchemy batch-load all prices in one `WHERE product_id IN (...)` query regardless of how many products were fetched. Verified with SQL echo against 20 products × 2 cities: `get_cached_results` emits 4 queries total (settings + search_query + products + selectin prices), `find_cached_products` emits 3, `find_cross_chain_matches` emits 2. None of these scale with product count. No code change needed.
- **Related issue discovered (logged separately as Item 30):** `find_cross_chain_matches()` pulls every product with non-null keywords into memory and filters in Python — not an N+1, but an unindexed scan that will become a problem as the catalog grows.

### Item 21: Differentiate Connection Errors from No Results

- **Status:** DONE
- **Added:** 2026-04-10
- **Completed:** 2026-04-09 (v0.12.1)
- **Priority:** P2
- **Problem:** When scrapers fail (timeout, HTTP 503, DNS failure), users see "No encontramos resultados" — same message as when the drug genuinely doesn't exist. Users blame the pharmacy, not the network.
- **Solution implemented:** Added `failed_pharmacies: list[str]` field to `SearchResponse`. `search_drug()` now captures the names of scrapers that raised exceptions in the `asyncio.gather` result loop and propagates them through the response. The formatter (`format_search_results`) branches on `response.total == 0`:
  - All queried scrapers failed → `⚠️ No pudimos conectar con {names} ahora mismo. Intenta de nuevo en unos minutos.`
  - Partial failure (some empty, some errored) → `No encontramos *{query}*. ⚠️ Ademas, no pudimos conectar con {names}.`
  - All succeeded, no results → original `No encontramos resultados... revisa la ortografia.`
  When results exist but some scrapers failed, the header shows a `⚠️ resultados parciales` warning line. Cache/catalog suffixes (`(cache)`, `(catalogo)`) are stripped via `endswith` when counting "real" queried pharmacies — immune to pharmacy names containing those substrings mid-string.
- **Files modified:** `src/farmafacil/models/schemas.py`, `src/farmafacil/services/search.py`, `src/farmafacil/bot/formatter.py`
- **Files created (tests):** 5 new tests in `tests/test_bot.py` (all-failed, partial-failed, results+partial, cache-hit no-failure, degenerate edge case), 4 new tests in `tests/test_search_concurrent.py` (`TestFailedPharmaciesTracking`)
- **Test count:** 447 → 456 (+9)

### Item 22: Remove Deprecated ProductCache Table

- **Status:** DONE (v0.12.6, 2026-04-11)
- **Added:** 2026-04-10
- **Priority:** P3
- **Problem:** `ProductCache` model in `database.py:241-246` was marked DEPRECATED (replaced by products/product_prices/search_queries) but still in schema and admin views. Dead code.
- **Solution shipped:** Removed `ProductCache` ORM class from `database.py`, removed `ProductCacheAdmin` view + import + registration from `api/admin.py`. Confirmed no other references in the codebase (the similarly-named `services/product_cache.py` module is unrelated — it is the product catalog service). The underlying `product_cache` SQL table still exists on production but is now orphaned — safe to drop manually on the next maintenance window.
- **Files modified:** `src/farmafacil/models/database.py`, `src/farmafacil/api/admin.py`

### Item 23: API Input Validation & Rate Limiting

- **Status:** DONE (v0.12.2, 2026-04-10)
- **Added:** 2026-04-10
- **Priority:** P2
- **Problem:** No validation on search query (empty strings, excessive length accepted). No rate limiting on any API endpoint. Could be abused or cause unnecessary scraper load.
- **Solution shipped:**
  - Added `slowapi>=0.1.9` dependency; shared `Limiter` instance in new `api/limiter.py` module (get_remote_address keyfunc).
  - Wired limiter into `create_app()` with `RateLimitExceeded` exception handler.
  - Added `Query(..., min_length=2, max_length=200)` on GET `/api/v1/search?q=`, `max_length=50` on `city`, `max_length=30` on `phone` filter params, `max_length=50` on `action` filter.
  - Added `max_length=50` to `SearchRequest.city`; tightened `IntentCreate` with `min_length`/`max_length` on action, keyword, response.
  - Applied per-endpoint rate limits: search 30/min, intents 30/min, stats/users/conversations/admin-stats 60/min. `/health` and `/webhook` remain unlimited (monitoring + Meta).
  - Fixed stored XSS in `/admin/user-stats/{id}` HTML template: all user-sourced values (name, phone, zone, city, search query, feedback) now HTML-escaped via `html.escape()`.
- **Files modified:** `pyproject.toml`, `src/farmafacil/api/limiter.py` (new), `src/farmafacil/api/app.py`, `src/farmafacil/api/routes.py`, `src/farmafacil/models/schemas.py`, `tests/test_rate_limiting.py` (new, 18 tests), `tests/test_admin_stats.py` (+2 XSS regression tests), `docs/api-reference.md`, `docs/deployment.md`.
- **Notes:** `get_remote_address` reads `request.client.host`; behind ngrok all external users share one bucket. Acceptable for LAN deployment; revisit if public. WhatsApp bot users are unaffected — the bot handler calls `search_drug()` directly, bypassing HTTP routes.

### Item 24: WhatsApp Location Sharing Support

- **Status:** DONE (v0.13.0, 2026-04-11)
- **Added:** 2026-04-10
- **Completed:** 2026-04-11
- **Priority:** P2
- **Problem:** `webhook.py:99` had a TODO — users who shared their GPS location pin during onboarding didn't advance. Only text-based city names worked. Many users naturally share their WhatsApp location instead of typing a city name.
- **Solution implemented:**
  - New `reverse_geocode(lat, lng)` service in `services/geocode.py` — calls Nominatim's `/reverse` endpoint at zoom=14 (neighbourhood level), guards against non-Venezuelan coordinates via `country_code != "ve"`, falls back through `suburb → neighbourhood → village → town → city → county → state` for a human-readable zone name, and reuses `_extract_city_code()` for the Farmatodo city code. Returns the same dict shape as `geocode_zone()` so both code paths share `update_user_location()`.
  - New `handle_location_message(sender, latitude, longitude, wa_message_id)` in `bot/handler.py` — calls `get_or_create_user` + `validate_user_profile`, snapshots the prior `onboarding_step` (before `update_user_location` clobbers it to `awaiting_preference`), reverse-geocodes, updates the user, then routes on state: no name → set `awaiting_name` and re-ask; still onboarding (prior step != None) → send `MSG_ASK_PREFERENCE`; already onboarded (prior step was None) → clear step + send "zona actualizada" acknowledgement. `prior_step` is the correct onboarding signal because `display_preference` has a non-nullable default of `"grid"`, so it cannot distinguish "explicitly picked" from "never asked".
  - `webhook.py` location-message branch: replaced TODO with float coercion (`try: lat = float(lat_raw); lng = float(lng_raw)` guarded by `TypeError, ValueError`). Malformed coords send `"No pude leer las coordenadas que compartiste"` and `continue`. Successful parse dispatches to `handle_location_message(sender, lat, lng, wa_message_id=wa_id)`.
- **Files modified:** `src/farmafacil/services/geocode.py`, `src/farmafacil/bot/handler.py`, `src/farmafacil/bot/webhook.py`
- **Files added:** `tests/test_location_sharing.py` (11 tests):
  - `TestReverseGeocodeUnit` (5): happy path → CCS + "La Boyera"; non-VE rejection (Bogotá); `httpx.RequestError` → `None`; missing `address` key → `None`; suburb/city missing → state fallback (Zulia → MCBO).
  - `TestHandleLocationMessage` (4): `awaiting_location` → `awaiting_preference` with preference prompt; no name → `awaiting_name` with "Cómo te llamas"; fully onboarded → zone updated + confirmation preserves `display_preference`; reverse_geocode `None` → error message sent + state untouched.
  - `TestWebhookLocationPayload` (2): POST `/webhook` with `type=location` dispatches to `handle_location_message` with (phone, lat, lng, wa_message_id=...); malformed `latitude=None` / `longitude=None` → error sent, handler NOT called.
- **Effort:** Medium (2-3h)
- **Notes:** Found + fixed a latent handler bug during test authoring: the original version checked `if user.display_preference is None` to decide between "continue onboarding" vs "acknowledge zone update", but the User ORM column is non-nullable with default `"grid"`, so every fresh user already looked "already picked a preference". Switched to checking the *prior* `onboarding_step` snapshot taken before `update_user_location`. Without this, a real user sharing their location during onboarding would have had their onboarding step cleared to None with no preference prompt sent.

### Item 25: Improve Bare Exception Handlers

- **Status:** DONE (v0.12.6, 2026-04-11)
- **Added:** 2026-04-10
- **Priority:** P3
- **Problem:** 12+ `except Exception:` blocks across the codebase caught all errors indiscriminately. Made debugging hard — timeout vs auth failure vs parsing error all looked the same in logs.
- **Solution shipped:** Replaced bare `except Exception:` with specific exception classes across 11 handlers:
  - `services/ai_responder.py` — direct imports `from anthropic import APIError, APIConnectionError`; 3 handlers now catch `(APIError, APIConnectionError)` with last-resort `Exception` for defensive logging. Direct imports (not `anthropic.APIError` via module) are immune to test MagicMock patching.
  - `services/user_memory.py` — same direct-import pattern; `auto_update_memory` catches `(APIError, APIConnectionError)` + `SQLAlchemyError` + last-resort `Exception` (non-blocking background task).
  - `services/store_backfill.py` — 3 handlers (Farmatodo, SAAS, Locatel) catch `httpx.HTTPError` + `(ValueError, KeyError, TypeError)` with specific error messages.
  - `services/users.py` — token-persist handler catches `SQLAlchemyError`.
  - `services/store_locations.py` — DB query handler catches `SQLAlchemyError`.
  - `services/image_grid.py` — `httpx.HTTPError` + `(UnidentifiedImageError, OSError, ValueError)`.
  - `bot/whatsapp.py` — read receipt handler catches `httpx.HTTPError`; media upload handler catches `httpx.HTTPError` + `OSError`.
  - `bot/handler.py` — `_update_memory_safe` keeps last-resort `Exception` (documented); feedback create handler catches `ValueError` + `SQLAlchemyError` + last-resort `Exception` (required for the `/bug` escape-hatch test `test_bug_clears_state_even_when_create_fails` which raises `RuntimeError`).
  - `scrapers/vtex.py` — VTEX product parse handler catches `(KeyError, ValueError, TypeError, IndexError)`.
  - `scrapers/farmatodo.py` — Decimal parse handler catches `(InvalidOperation, ValueError, TypeError)` (leverages the v0.12.4 hotfix that coerces `measurePum` to Decimal).
- **Files modified:** `src/farmafacil/services/ai_responder.py`, `src/farmafacil/services/user_memory.py`, `src/farmafacil/services/store_backfill.py`, `src/farmafacil/services/users.py`, `src/farmafacil/services/store_locations.py`, `src/farmafacil/services/image_grid.py`, `src/farmafacil/bot/whatsapp.py`, `src/farmafacil/bot/handler.py`, `src/farmafacil/scrapers/vtex.py`, `src/farmafacil/scrapers/farmatodo.py`

### Item 27: Nearest Pharmacy Direct Query

- **Status:** DONE
- **Added:** 2026-04-10
- **Completed:** 2026-04-10
- **Priority:** P2
- **Problem:** When user asks "cuál es la farmacia más cercana", the AI can't answer — it only knows about stores through drug searches. Store lookups are a side effect of `_enrich_with_nearby_stores()` during product search, with no standalone capability.
- **Solution implemented:** Added `nearest_store` action across the full stack: (1) `get_all_nearby_stores()` service queries all pharmacy chains from `pharmacy_locations` DB, calculates haversine distances, returns top 5 sorted by proximity. (2) `format_nearby_stores()` formats results for WhatsApp with store name, chain, distance, address. (3) AI classifier recognizes `nearest_store` action for questions about nearby pharmacies. (4) Handler routes `nearest_store` in both AI-only and hybrid modes, with location check and feedback prompt. (5) 8 keyword entries ("farmacia cercana", "donde comprar", etc.) for fast hybrid-mode routing without LLM. (6) `nearest_store` skill added to pharmacy_advisor role. (7) Fixed stale seed prompt to include Locatel (3rd chain).
- **Files modified:** `src/farmafacil/services/store_locations.py` (new function), `src/farmafacil/bot/formatter.py` (new function), `src/farmafacil/services/ai_responder.py` (action enum + rules), `src/farmafacil/bot/handler.py` (routing + handler), `src/farmafacil/db/seed.py` (keywords + skill + Locatel prompt fix), `src/farmafacil/services/intent.py` (docstring + help msg)
- **Files created:** `tests/test_nearest_store.py` (15 tests)

### Item 26: Handler.py Test Coverage

- **Status:** DONE (v0.13.1, 2026-04-11)
- **Added:** 2026-04-10
- **Completed:** 2026-04-11
- **Priority:** P2
- **Problem:** Main bot handler (`handler.py`, ~800 lines, ~15% of codebase) had no dedicated test file. Most critical untested module — onboarding, search routing, feedback flow, debug commands all go through it.
- **Solution implemented:**
  - Started with a full audit of existing handler coverage to avoid duplication. Inventoried 8 files that import `bot.handler` (test_location_sharing, test_feedback_suppression, test_clarification, test_nearest_store, test_symptom_typing, test_user_feedback, test_user_validation, test_ai_role_scope) and catalogued which handler branches each already exercises.
  - Created `tests/test_handler.py` (21 tests) focused on the gaps:
    - **`TestEmptyMessage`** (1): whitespace-only message short-circuits before DB / send.
    - **`TestOnboardingWelcome`** (1): `welcome` step → `awaiting_name` + MSG_WELCOME.
    - **`TestOnboardingAwaitingName`** (4): greeting re-asks; valid name persists + asks location; invalid name (`si`) rejected by `_is_valid_name`; name + location combined in one message skips to `awaiting_preference`.
    - **`TestOnboardingAwaitingLocation`** (2): geocode success advances to `awaiting_preference`; geocode failure re-asks.
    - **`TestOnboardingAwaitingPreference`** (2): `"1"` → `detail` + step cleared; garbage input re-asks.
    - **`TestAwaitingFeedbackDetail`** (1): user in `awaiting_feedback_detail` → `record_feedback_detail` called with last search log id + step cleared.
    - **`TestAwaitingFeedbackFallthrough`** (1): non-yes/no message in `awaiting_feedback` clears the stuck state and processes through normal intent routing.
    - **`TestStatsCommand`** (2): `/stats` blocked when chat_debug off; rendered with per-user + global haiku/sonnet breakdown when on.
    - **`TestHybridKeywordRouting`** (3): `location_change` → `awaiting_location` without classify_intent call; `name_change` → `awaiting_name`; `farewell` sends response verbatim.
    - **`TestHybridIntentRouting`** (4): greeting intent sends MSG_RETURNING; help intent sends HELP_MESSAGE; drug_search without location → prompt + `awaiting_location`; unknown action falls back to `generate_response`.
  - Shared fixture `_cleanup_handler_test_users` wipes a dedicated block of test phones (`5491999000001`-`5491999000021`) before and after each test. Shared helper `_seed_user` writes name/step/location/last_search_log_id in a single session re-query (avoids the SQLAlchemy 2 detached-object bug that bit Item 24).
  - All handler collaborators are patched via `patch.object(handler, "...")`: `send_text_message`, `classify_with_ai`, `classify_intent`, `geocode_zone`, `get_setting`, `resolve_chat_debug`, `get_user_stats`, `_get_keyword_cache`, `record_feedback_detail`, `parse_feedback`, `generate_response`, `increment_token_usage`, `_update_memory_safe`. Zero real LLM / HTTP / WhatsApp traffic.
- **Files added:** `tests/test_handler.py` (new, 21 tests)
- **Effort:** High (4-6h)
- **Notes:** Intentionally did NOT duplicate coverage already present in the 8 pre-existing files. Drug-search happy path, feedback suppression, clarification flow, nearest-store routing, `/bug` and `/comentario`, location pin onboarding, and `validate_user_profile` all stay in their existing homes. `test_handler.py` is strictly the "paths no one else tests" file.

### Item 17: 4th Pharmacy Scraper

- **Status:** DEFERRED
- **Added:** 2026-04-09
- **Problem:** With Farmahorro closed (Item 4), FarmaFacil needs a 4th pharmacy data source with physical stores in Venezuela (required for store-level availability and "nearest store" feature).
- **Investigation (2026-04-09) — all candidates:**

| Chain | Stores | API | Verdict |
|-------|--------|-----|---------|
| Farmarebajas (farmarebajas.com) | 0 — online-only | ✅ Public WooCommerce Store API (`/wp-json/wc/store/v1/products?search=<query>`) | ❌ No physical stores — can't show "nearest store" |
| FarmaBien (farmabien.com) | 122+ across VE (Mérida, Caracas, Maracaibo, Barinas, etc.) | ❌ Protected — `/api/search` returns 403 Forbidden | Best candidate but API is auth-protected; needs client-side auth reverse-engineering |
| Medicinas To Go (medicinastogo.com) | Unknown | ❌ No e-commerce — orders via email only | Not viable |
| Farmacias XANA (wxana.com) | ~9 in Caracas area | ❌ 403 Forbidden on all URLs | No public catalog at all |
| Farmahorro (farmahorro.com.ve) | 0 — closed Sept 2025 | ❌ DNS SERVFAIL | Acquired by DRONENA, all 89 stores closed |

- **Farmarebajas API details (verified, public, working — but no stores):**
  - Endpoint: `GET https://farmarebajas.com/wp-json/wc/store/v1/products?search=losartan&per_page=10`
  - Response: JSON array of product objects
  - Price: `prices.price` in cents (e.g., "197" = $1.97 USD), `prices.currency_code` = "USD"
  - Stock: `is_purchasable` boolean + `add_to_cart.text` present
  - Image: `images[0].thumbnail` (webp format, 250x250)
  - Brand: `brands[0].name` (e.g., "Lab. Calox")
  - Categories: `categories[].name` (e.g., "Antihipertensivos")
  - No auth required, no rate limiting observed
- **FarmaBien store locator:** `https://www.farmabien.com/tiendas` — 122+ stores across Mérida (15+), Caracas (10+), Barinas (7+), Maracaibo (6+), San Cristóbal (5+), Trujillo, Lara, Zulia, Anzoátegui
- **FarmaBien tech stack:** Next.js (App Router) + Payload CMS, search via `/productos?term=<query>`, client-side rendering (no SSR product data), API at `/api/search` protected (403)
- **Next step:** Revisit FarmaBien when time permits — reverse-engineer client-side auth flow (cookies, tokens, or session) to unlock `/api/search`
- **Effort:** High (6+ hours — auth reverse-engineering, fragile integration)

---

## P2 — Medium

### Item 15: Drug Interaction Detection — RxNorm API Integration

- **Status:** DONE
- **Added:** 2026-04-09
- **Completed:** 2026-04-09
- **Priority:** P2
- **Problem:** The AI currently uses hardcoded common interaction patterns (e.g., anticoagulants + aspirin) to warn users. This is limited and doesn't cover the full range of drug interactions. Users who mention existing medications they take should get accurate, real-time interaction checks — not just pattern-matched guesses from Haiku.
- **Solution implemented:** Integrated NIH RxNorm/RxNav API for real-time drug interaction detection. Created `drug_interactions.py` service with: Spanish→English drug name normalization (~30 common drugs), dosage/form stripping, accent handling, RxCUI lookup via RxNorm REST API, interaction checking via RxNav Interaction API, medication extraction from user memory, and Spanish warning message formatting. When a user searches for a product and has known medications in their memory, the system automatically checks for interactions and sends a ⚠️ warning before search results. Added `LLM_MODEL_ELEVATED` config for future model switching when interactions are detected.
- **Files changed:** `src/farmafacil/services/drug_interactions.py` (new — RxNorm client, normalization, interaction check, memory extraction, warning formatting), `src/farmafacil/bot/handler.py` (modified — interaction check in `_handle_drug_search`), `src/farmafacil/config.py` (modified — `LLM_MODEL_ELEVATED`), `tests/test_drug_interactions.py` (new — 24 unit + 3 integration tests)
- **Notes:** RxNorm API is free, no auth, 20 req/sec limit. OpenFDA and DrugBank remain as future enhancements. Model switching to Sonnet/Opus when interactions are detected is configured but not yet active — will be enabled after testing interaction detection in production.

### Item 5: AI Roles Management System

- **Status:** DONE
- **Added:** 2026-03-31
- **Completed:** 2026-03-31
- **Problem:** LLM system prompts were hardcoded in intent.py. No way to manage AI behavior from the admin UI. No per-user memory across sessions.
- **Solution implemented:** Built a 3-layer AI management system:
  1. **AI Roles** (`ai_roles` table) — personas with system prompts, editable via admin
  2. **AI Rules + Skills** (`ai_role_rules`, `ai_role_skills` tables) — behavioral guidelines and capability definitions per role
  3. **Client Memory** (`user_memories` table) — per-user AI memory, auto-updated after conversations
  - Added role router (`ai_router.py`) that selects the right role via lightweight LLM call
  - Added AI responder (`ai_responder.py`) that assembles prompt from role + rules + skills + memory
  - Added 4 admin views for managing roles, rules, skills, and user memories
  - Seeded 2 default roles: pharmacy_advisor (4 rules, 2 skills) and app_support (2 rules)
  - Simplified intent.py to keyword-only; LLM classification now goes through AI responder
- **Files created:** `services/ai_roles.py`, `services/ai_router.py`, `services/ai_responder.py`, `services/user_memory.py`, `tests/test_ai_roles.py`
- **Files modified:** `models/database.py`, `api/admin.py`, `api/app.py`, `bot/handler.py`, `services/intent.py`, `db/seed.py`

### Item 6: Response Mode Filters (Global + Per-User Override)

- **Status:** DONE
- **Added:** 2026-04-07
- **Completed:** 2026-04-07
- **Problem:** No way to control whether the bot uses hybrid mode (intent keywords + LLM) or AI-only mode. Admins need a global setting and per-user overrides.
- **Solution implemented:** Added `response_mode` app setting (global: hybrid/ai_only) and `response_mode` column on `users` table (per-user override: NULL=use global, hybrid, ai_only). In handler.py, resolve effective mode per user, then route accordingly. AI-only mode uses `classify_with_ai` for intent detection + drug search, bypassing keyword cache entirely. Invalid modes fall back to hybrid with a warning log.
- **Files modified:** `models/database.py` (User.response_mode column + __repr__ methods), `services/settings.py` (response_mode default + resolve_response_mode), `bot/handler.py` (ai_only routing + _handle_drug_search helper), `api/admin.py` (response_mode in User admin)
- **Files created:** `tests/test_response_mode.py` (11 tests)

### Item 7: Search Feedback Loop

- **Status:** DONE
- **Added:** 2026-04-08
- **Completed:** 2026-04-08
- **Problem:** No way to validate whether drug search results were useful. Need a feedback mechanism to measure success and improve.
- **Solution implemented:** After every drug search, bot asks "¿Te sirvió? (sí/no)". Positive feedback is logged. Negative feedback triggers follow-up "¿Qué buscabas exactamente?" and records the detail. All feedback stored in `search_logs.feedback` and `search_logs.feedback_detail`. Users can also skip feedback by sending a normal message (falls through to regular handling).
- **Files created:** `services/search_feedback.py`, `tests/test_search_feedback.py` (18 tests)
- **Files modified:** `models/database.py` (feedback/feedback_detail on SearchLog, last_search_log_id on User), `bot/handler.py` (feedback flow + MSG constants), `services/users.py` (update_last_search accepts search_log_id), `api/admin.py` (feedback column in SearchLog admin)

### Item 8: Chat Debug Mode

- **Status:** DONE
- **Added:** 2026-04-08
- **Completed:** 2026-04-08
- **Problem:** No way to see what the AI is doing behind the scenes. Need a debug mode that appends metadata (model, role, tokens, total questions, success rate) to every bot response for testing and diagnostics.
- **Solution implemented:** Added `chat_debug` global setting (enabled/disabled, default: disabled) and per-user `chat_debug` column override (NULL=use global, enabled, disabled). When enabled, appends debug footer to every AI-powered response showing: AI model, AI role, input/output tokens, total inbound questions, total positive feedback count. Token tracking added to `AiResponse` dataclass and both LLM call paths (`generate_response`, `classify_with_ai`).
- **Files created:** `services/chat_debug.py` (stats queries + footer builder), `tests/test_chat_debug.py` (22 tests)
- **Files modified:** `models/database.py` (User.chat_debug column), `services/settings.py` (chat_debug default + resolve_chat_debug), `services/ai_responder.py` (token tracking on AiResponse + both LLM paths), `bot/handler.py` (_build_debug helper + debug footer in all response paths), `api/admin.py` (chat_debug in UserAdmin)

### Item 9: Persistent Usage Stats Tracking

- **Status:** DONE
- **Added:** 2026-04-08
- **Completed:** 2026-04-08
- **Problem:** Token usage from LLM calls is captured per-request but never persisted — lost after debug footer is sent. No way to track cumulative token spend per user or globally.
- **Solution implemented:** Added `total_tokens_in` and `total_tokens_out` columns to User model with atomic SQL increments after every LLM call (6 exit points in handler.py). Added `GET /api/v1/stats` endpoint for global and per-user usage stats. Updated debug footer with cumulative token totals. Propagated tokens through Intent dataclass from AI classifier.
- **Files created:** `tests/test_usage_stats.py` (12 tests across 6 test classes)
- **Files modified:** `models/database.py` (User token columns), `services/users.py` (increment_token_usage), `services/intent.py` (Intent token fields), `bot/handler.py` (6 increment calls), `services/chat_debug.py` (stats + footer with totals), `api/routes.py` (stats endpoint), `api/admin.py` (token columns in admin)

### Item 10: Broaden AI Role Scope to All Pharmacy Products

- **Status:** DONE
- **Added:** 2026-04-08
- **Completed:** 2026-04-08
- **Problem:** The `pharmacy_advisor` AI role system prompt and skills are narrowly focused on "medicamentos" (medicines only), causing the bot to refuse searches for legitimate pharmacy products (skincare, vitamins, personal care, baby products, etc.). The pharmacy APIs return all product types but the AI refuses to search for non-medicine items.
- **Solution implemented:** Broadened system prompt, skills, rules, classification instructions, fallback prompt, and all user-facing messages from "medicamentos" to "productos de farmacia" (medicines, skincare, vitamins, personal care, baby, hygiene, etc.). Added `product_scope` rule that instructs AI to always search for pharmacy products and only refuse for non-pharmacy items. Updated production DB with matching changes.
- **Files created:** `tests/test_ai_role_scope.py` (18 tests across 5 test classes)
- **Files modified:** `db/seed.py` (system prompt, skills, rules, farewell responses), `services/ai_responder.py` (fallback prompt, classification instructions, error messages), `services/intent.py` (HELP_MESSAGE), `bot/handler.py` (MSG_WELCOME, MSG_READY, MSG_RETURNING, error message), `docs/bot-flow.md`

### Item 11: User Memory — Build from All Interactions

- **Status:** DONE
- **Added:** 2026-04-08
- **Completed:** 2026-04-08
- **Problem:** User memory (`user_memories` table) was only updated from `generate_response()` (conversational Q&A), missing drug searches and all other interaction types. Result: 0 memory rows after 88+ interactions across 3 users. Memory should build a common-sense profile of each user from all interactions — searches, questions, style, patterns.
- **Solution implemented:** Moved `auto_update_memory` calls from `ai_responder.py` to `handler.py` at 4 interaction points (AI-only responses, drug searches, questions, unknown fallbacks). Added `_get_user_context()` that feeds user profile + recent search history into the memory LLM. Broadened memory prompt to track search patterns, communication style, family/dependents, product preferences, and life context clues.
- **Files created:** `tests/test_user_memory.py` (14 tests across 5 test classes)
- **Files modified:** `services/user_memory.py` (context builder, broadened prompt), `bot/handler.py` (memory calls at 4 exit points), `services/ai_responder.py` (removed duplicate memory call)

### Item 12: Profile Authority Over Memory in AI Prompts

- **Status:** DONE
- **Added:** 2026-04-08
- **Completed:** 2026-04-08
- **Problem:** User memory could store stale profile data (old zone, old name) that contradicts the live user profile in the database. The AI prompt had no mechanism to resolve which data was authoritative.
- **Solution implemented:** Updated `assemble_prompt()` to inject live user profile (name, zone, city_code, preference) as an authoritative section labeled "User Profile (authoritative — always current)" before client memory (labeled "supplementary — may contain outdated info"). Added `_get_user_profile()` helper in `ai_responder.py`.
- **Files created:** None
- **Files modified:** `services/ai_roles.py` (assemble_prompt with user_profile), `services/ai_responder.py` (_get_user_profile, pass profile to assemble_prompt), `tests/test_ai_roles.py` (new profile tests)

### Item 14: Debug Footer — App Version + Global Token Totals

- **Status:** DONE
- **Added:** 2026-04-08
- **Completed:** 2026-04-08
- **Problem:** Debug footer was missing the app version and global token totals (across all users). Only per-user and per-call tokens were shown.
- **Solution implemented:** Added `app version:` line showing dynamic `__version__` and `global tokens:` line showing sum of all users' cumulative tokens. Renamed "total tokens" to "user tokens" for clarity. Added global token aggregation query in `get_user_stats()`.
- **Files modified:** `services/chat_debug.py` (version import, global query, footer format), `bot/handler.py` (pass global tokens), `tests/test_chat_debug.py` (3 new tests), `tests/test_usage_stats.py` (updated label assertions)

### Item 13: Symptom Acknowledgment + Typing Indicator

- **Status:** DONE
- **Added:** 2026-04-08
- **Completed:** 2026-04-08
- **Problem:** When users describe symptoms (e.g., "tengo acidez estomacal"), the bot immediately shows drug search results without acknowledging the symptom or explaining why a particular medicine was chosen. Also, no WhatsApp typing indicator ("...") bubble appears while the bot processes messages, making users unsure if the bot is working.
- **Solution implemented:** (1) Updated classification instructions in `ai_responder.py` to require AI include a conversational RESPONSE alongside DRUG for symptom-based queries. Updated handler.py to send symptom response text before drug search results in both AI-only and hybrid modes. Updated `symptom_translation` skill in seed.py with acknowledge-then-search flow. (2) Added `send_typing_indicator()` function using WhatsApp Cloud API v22.0 `status: "typing"` payload. Called at the top of `handle_incoming_message()` so the typing bubble appears immediately.
- **Files created:** `tests/test_symptom_typing.py` (19 tests across 6 test classes)
- **Files modified:** `bot/handler.py` (typing indicator call, symptom text before search in both modes), `bot/whatsapp.py` (send_typing_indicator function), `services/ai_responder.py` (classification REGLAS for symptoms), `db/seed.py` (symptom_translation skill content)

### Item 28: User Feedback Collection — /bug and /comentario Commands + Feedback Race Bug Fix

- **Status:** DONE (2026-04-09, v0.12.0)
- **Added:** 2026-04-09
- **Priority:** P2
- **Problem:** (1) No structured way for users to report bugs, issues, or suggestions through the bot. Feedback is lost in conversation logs with no review workflow. (2) Test user Jose Lugo received the "¿Te sirvió?" prompt after a search, but the "gracias por tu respuesta" message fired immediately — he never had time to type anything. Root cause: `_POSITIVE` set in `search_feedback.py` contained ambiguous words like `"gracias"`, `"ok"`, `"bien"`, `"perfecto"` that users send as farewells, which were misinterpreted as positive feedback.
- **Solution implemented:** (1) New `user_feedback` table linked to `users` and `conversation_logs`. New `/bug` and `/comentario` commands intercepted early in `handle_incoming_message()` (after read receipt, before onboarding state handling), extract the text after the command, store a case, and reply with DB-generated case ID (`Caso #{id}`). State (`awaiting_feedback`, `awaiting_feedback_detail`) is cleared BEFORE the DB call so the `/bug` command works as an escape hatch even if `create_feedback` fails. New `UserFeedbackAdmin` SQLAdmin view exposes only `reviewed`, `reviewer_notes`, `reviewed_at` as editable fields. (2) Tightened `_POSITIVE` to `{"sí", "si", "yes", "yep", "👍", "1"}` and `_NEGATIVE` to `{"no", "nop", "nope", "👎", "0"}` — removed ambiguous farewells.
- **Files created:** `src/farmafacil/services/user_feedback.py`, `tests/test_user_feedback.py` (21 tests)
- **Files modified:** `src/farmafacil/models/database.py` (UserFeedback model), `src/farmafacil/bot/handler.py` (command intercept + escape-hatch state clearing), `src/farmafacil/api/admin.py` (UserFeedbackAdmin), `src/farmafacil/services/search_feedback.py` (tightened sets), `tests/test_search_feedback.py` (regression tests for ambiguous words)

### Item 29: Category Quick-Reply Menu on Greeting

- **Status:** DONE (v0.13.2, 2026-04-11)
- **Added:** 2026-04-09
- **Priority:** P2
- **Origin:** Suggested by Jose Lugo (test user).
- **Product decisions (2026-04-11):**
  - **Category set:** 5 categories — `Medicamentos / Cuidado Personal / Belleza / Alimentos / Articulos Hogar`. Dropped `Higiene` (overlap with Cuidado Personal in practice) and `Equipos Ortopédicos` (too niche — effectively zero SKUs indexed). **No scraper-side category filtering** — category is UX scaffolding, not a search filter. The follow-up product query is dispatched through the normal drug-search pipeline exactly as if the user had typed it directly.
  - **Trigger audience:** Fully-onboarded users whose greeting intent fires in hybrid mode. Users still in onboarding, or whose first message is not a bare greeting, continue on their existing path unchanged.
  - **Freeform fallback:** After a category pick the bot stashes the category on the user, sends `"🛍 {category} - ¿Qué producto buscas?"`, and waits for the user's next text message — which is then dispatched as a normal drug search (no scraper-side category filter, no merging into the query). Cancel words (`cancelar`, `olvidalo`, `nada`...) clear the stash.
  - **Kill switch:** New `app_setting` `category_menu_enabled` (default `"true"`). Flip to `"false"` via the admin UI or a direct DB update to fall back to the legacy `MSG_RETURNING` text without redeploying.
- **Solution implemented:**
  - New `User.awaiting_category_search` VARCHAR(50) column + idempotent `init_db()` migration (SQLite PRAGMA + Postgres `ADD COLUMN IF NOT EXISTS`).
  - New `set_awaiting_category_search(phone, category)` atomic UPDATE helper in `services/users.py`.
  - New `category_menu_enabled` entry in `services/settings.py` DEFAULTS, auto-seeded on startup.
  - New `send_interactive_list(to, body, button, rows, header=, footer=, section_title=)` in `bot/whatsapp.py` that posts a WhatsApp `type=interactive` list message. Logs an `[interactive:list] {body}` line to `conversation_logs` so the history stays readable.
  - New `CATEGORIES` constant in `bot/handler.py` (5 tuples) + `_CATEGORY_BY_ID` reverse lookup. New `_send_category_list(sender, display_name)` thin wrapper around `send_interactive_list` driven off that constant — adding or removing a category is a one-line edit.
  - New public `handle_list_reply(sender, reply_id, wa_message_id)` — validates the reply id against `_CATEGORY_BY_ID`, stashes the category, sends the canned prompt. Unknown ids are logged and silently dropped so a stale or malformed payload never crashes the webhook.
  - New `awaiting_category_search` freeform branch at the top of `handle_incoming_message` — runs AFTER the `/bug` + clarification escape hatches (so those still work mid-category-flow), and BEFORE onboarding (so the guard is `step is None`). Clears the stash BEFORE dispatching the drug search so a downstream failure cannot trap the user (fail-safe pattern from Item 31).
  - Hybrid-mode greeting branch now reads `await get_setting("category_menu_enabled")` and, if `"true"`, calls `_send_category_list` instead of sending `MSG_RETURNING`. Any value other than the literal `"true"` falls back to the legacy text — a misconfigured setting is never catastrophic.
  - `bot/webhook.py` now handles `msg_type == "interactive"` — parses `interactive.list_reply.id`/`title`, logs a readable `[interactive:list_reply] {id} ({title})` line, and routes to `handle_list_reply`. `button_reply` is accepted defensively for future use.
- **Files created:**
  - `tests/test_category_menu.py` — 12 tests: `TestCategoryListPayload` (2 — constant shape + payload shape), `TestGreetingRoutesCategoryList` (3 — setting on/off/onboarding-user), `TestHandleListReply` (3 — valid pick, unknown id, replace existing), `TestAwaitingCategorySearchFreeform` (3 — dispatch + state clear BEFORE dispatch, cancel word, `/bug` escape hatch), `TestKillSwitchIntegration` (1 — real DB setting flip disables menu).
- **Files modified:**
  - `src/farmafacil/models/database.py` — `User.awaiting_category_search` column
  - `src/farmafacil/db/session.py` — additive migration entry
  - `src/farmafacil/services/users.py` — `set_awaiting_category_search` helper
  - `src/farmafacil/services/settings.py` — `category_menu_enabled` default
  - `src/farmafacil/bot/whatsapp.py` — `send_interactive_list`
  - `src/farmafacil/bot/handler.py` — `CATEGORIES`, `_send_category_list`, `handle_list_reply`, `awaiting_category_search` branch, greeting kill-switch
  - `src/farmafacil/bot/webhook.py` — `interactive` message type handler
  - `tests/test_nearest_store.py` — `MockUser.awaiting_category_search = None`
  - `docs/bot-flow.md` — new section on category menu
- **Deferred for a follow-up:** Actual scraper-side category filtering (Farmatodo Algolia `facetFilters`, VTEX `categoryId`) — see deferred Item 35.

### Item 31: Clarification Step for Vague Category Queries

- **Status:** DONE (v0.12.3, 2026-04-10)
- **Added:** 2026-04-10 (from Case #1 bug report: "I asked AI that I needed help with memory medicines. It should ask more questions for correct product display, Maybe if I want to take a drink or pills or have some preference and then search for available products")
- **Priority:** P1
- **Problem:** When users asked for a vague product CATEGORY (e.g., "medicinas para la memoria", "algo para dormir", "vitaminas"), the AI classifier jumped straight to picking one product and scraping, leaving the user with results that did not match their form-factor or age preference. There was no mechanism to ask a clarifying question before searching.
- **Solution:**
  - New `clarify_needed` action in the AI classification prompt and parser (`ai_responder.py`). Prompt now has an explicit rule with examples ("memoria", "dormir", "vitaminas") plus a counter-example list of things that should NEVER be clarified (specific product names, ingredients).
  - New `CLARIFY_QUESTION` and `CLARIFY_CONTEXT` output fields; parser defensively downgrades `clarify_needed` to `drug_search` if the question is missing.
  - New `users.awaiting_clarification_context: VARCHAR(300)` column stashes the original vague query while the bot waits for the answer.
  - New idempotent additive-migration helper in `db/session.py::init_db()` (PRAGMA check on SQLite, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` on Postgres) so existing deployments pick up the new column automatically on container restart — no manual DDL needed.
  - New `set_awaiting_clarification()` service in `users.py` (atomic UPDATE).
  - Handler pre-route block: if `awaiting_clarification_context` is set AND `step is None`, the next incoming message is merged with the stored context (`"{original_query} {answer}"`), the context is cleared BEFORE dispatching (fail-safe), and the refined query goes straight to `_handle_drug_search()`.
  - Escape hatches: the `/bug` and `/comentario` commands still intercept before the clarify block (bug reports always take priority). New `_CLARIFY_CANCEL_WORDS` set (`cancelar`, `cancela`, `olvidalo`, `nada`, `no`, etc.) clears the context and confirms cancellation.
  - Both `ai_only` and hybrid routing branches wire the `clarify_needed` action to stash context + send the question + update memory.
  - User preference is persisted to `user_memories` on clarification so the bot does not re-ask next time.
- **Affected files:** `src/farmafacil/models/database.py` (new column), `src/farmafacil/db/session.py` (migration helper), `src/farmafacil/services/users.py` (set_awaiting_clarification), `src/farmafacil/services/ai_responder.py` (prompt + parser + AiResponse fields), `src/farmafacil/services/intent.py` (Intent fields + classify_intent_ai propagation), `src/farmafacil/bot/handler.py` (pre-route block + clarify_needed branches + cancel set), `tests/test_clarification.py` (21 new tests), `tests/test_nearest_store.py` (MockUser updated), `docs/bot-flow.md` (new Clarification Flow section), `src/farmafacil/__init__.py` + `pyproject.toml` (version bump).
- **Tests:** 21 new. Parser: clarify_needed valid action, degrades without question, specific drugs skip clarification. Dataclasses: AiResponse + Intent expose fields. Prompt: mentions `clarify_needed`, vague examples, and warning against specific drugs. Service: `set_awaiting_clarification` roundtrip. Handler source: imports, branches in both modes, cancel set present. Integration: vague query → stash + question (scraper not called), answer → merged search + cleared context, `cancelar` → aborted, `/bug` → registers case, specific drug → skips clarification. Full suite: **497 passed** (was 476, +21). Migration idempotency verified against a pre-existing SQLite DB (column added on first run, no error on second run).
- **Effort:** Small (0.5 day)
- **Follow-up regression (v0.12.4, 2026-04-10):** Production test showed the naive string concatenation `"{context} {answer}"` produced a 15-word natural-language sentence that no scraper could match, AND simultaneously exposed a Farmatodo scraper crash on float `measurePum` values. Both fixed in **Item 33** (LLM query refiner) and the **v0.12.4 hotfix** below.

### Item 33: LLM Query Refinement After Clarification

- **Status:** DONE (v0.12.4, 2026-04-10)
- **Added:** 2026-04-10 (from live prod test of v0.12.3)
- **Priority:** P1
- **Problem:** After Item 31 shipped, a real user test produced this flow:
  1. User: `"Que me recomiendas para mejorar mi memoria?"`
  2. Bot (correctly): `"¿Prefieres pastillas, cápsulas, bebibles o gomitas? ¿Es para adulto o niño?"`
  3. User: `"es para mi, adulto, me gusta la idea de gomitas"`
  4. Handler merged naively: `"que recomiendas para mejorar la memoria es para mi, adulto, me gusta la idea de gomitas"` — 15 words, fed directly to scrapers
  5. All scrapers returned zero results (no product catalog matches 15-word sentences), so clarification produced *worse* results than no clarification at all.
- **Solution:** Added `refine_clarified_query(original_context, user_answer)` in `ai_responder.py`. Single-purpose Claude Haiku call with a dedicated system prompt that distills the vague-query + answer pair into a concrete 2-5 word Spanish search term (e.g., `"medicinas para la memoria"` + `"adulto, gomitas"` → `"ginkgo gomitas adulto"`). Prompt includes 7 canonical examples and strict rules ("2 a 5 palabras", "sin explicación", "en español"). Returns `(refined_query, input_tokens, output_tokens)` so the handler can increment token counters.
- **Fallback hierarchy:**
  1. No `ANTHROPIC_API_KEY` → return raw user answer (0 tokens, logged warning)
  2. LLM exception (API 500, network timeout) → return raw user answer (0 tokens, logged error)
  3. LLM returns empty text → return raw user answer (tokens still counted)
  4. LLM returns text → strip surrounding quotes/punctuation, return refined query
- **Handler wiring:** In the clarification resume block (`handler.py`), the merged-string concatenation was replaced with:
  ```python
  refined_query, r_in, r_out = await refine_clarified_query(pending_context, text)
  if r_in or r_out:
      await increment_token_usage(user.id, r_in, r_out, model=LLM_MODEL)
  ```
  The refined query is what gets dispatched to `_handle_drug_search()`. The original context is still cleared BEFORE dispatch (fail-safe preserved from Item 31).
- **Affected files:** `src/farmafacil/services/ai_responder.py` (new `_REFINER_SYSTEM_PROMPT`, new `refine_clarified_query()` function), `src/farmafacil/bot/handler.py` (import + wire refiner into clarification resume), `tests/test_clarification.py` (1 existing test renamed + updated, 2 new handler integration tests, 6 new refiner unit tests).
- **Tests added (8 new):**
  - `test_clarify_answer_refines_and_dispatches_search` (renamed from `..._merges_...`) — asserts scraper is called with the *refined* keyword, explicitly guards against the raw natural-language sentence leaking through.
  - `test_refiner_failure_falls_back_to_user_answer` — 0-token refiner result does NOT trigger `increment_token_usage`.
  - `TestRefineClarifiedQueryUnit` class (6 tests): happy path returns stripped LLM text; strips quotes/punctuation like `'"melatonina pastillas."'` → `"melatonina pastillas"`; empty LLM response falls back to user answer but counts tokens; LLM exception (`RuntimeError("API 500")`) falls back with 0 tokens; no API key falls back with 0 tokens; `_REFINER_SYSTEM_PROMPT` contains the "2-5 palabras" rule + at least one canonical example.
- **Test suite:** **509 passed** (was 497, +12 net: 13 new, 1 rename). Verified with `rm farmafacil.db && pytest -m "not integration" -q`.
- **Effort:** Small (2h, bundled with v0.12.4 hotfix)

### Hotfix v0.12.4: Farmatodo Scraper Decimal/float Crash (P0)

- **Status:** DONE (v0.12.4, 2026-04-10)
- **Added:** 2026-04-10 (discovered while investigating the Item 33 bug via prod logs)
- **Priority:** **P0** — every Farmatodo search with a product that had `measurePum` returned as a float was crashing the scraper.
- **Symptom:** The v0.12.3 prod test showed the clarification flow return zero results with a "⚠️ No pudimos conectar con Farmatodo ahora mismo" line (the failed-pharmacies warning from Item 21). The user's assumption was a network/API outage. **It was not.**
- **Root cause (from prod logs):**
  ```
  ERROR farmafacil.services.search: Scraper Farmatodo failed for query
    'que recomiendas para mejorar la memoria ...':
    unsupported operand type(s) for /: 'decimal.Decimal' and 'float'
    File "/usr/local/lib/python3.12/site-packages/farmafacil/scrapers/farmatodo.py",
    line 122, in _hit_to_result
  ```
  The Farmatodo Algolia index was returning 988 hits successfully — the crash happened while **parsing** them. At `farmatodo.py:122`, the scraper divided `best_price` (Decimal) by `hit.get("measurePum")`, and for some products (notably omega-3 gomitas) Algolia returned `measurePum` as a Python `float` (e.g. `60.0`) instead of an `int`. Python refuses `Decimal / float` with a `TypeError`, which bubbled up, triggered `asyncio.gather(return_exceptions=True)` to record the exception, and the whole Farmatodo result set was discarded as a "connection error."
- **Why it wasn't caught earlier:** The existing unit tests for `_hit_to_result` only used integer `measurePum` values (or no value at all). Python's strict Decimal arithmetic made the bug data-dependent — it only fired when at least one hit in the result set had a float unit count. Zero-result searches never exercised the division path.
- **Fix:** Coerce `measurePum` to `Decimal` before dividing, and track an `int` variant separately for the `DrugResult.unit_count: int | None` schema field to avoid passing a Decimal into a Pydantic int field.
  ```python
  unit_count_raw = hit.get("measurePum")
  unit_count_dec: Decimal | None = None
  unit_count_int: int | None = None
  if unit_count_raw is not None:
      try:
          unit_count_dec = Decimal(str(unit_count_raw))
          unit_count_int = int(unit_count_dec)
      except Exception:
          unit_count_dec = None
          unit_count_int = None
  if unit_count_dec is not None and unit_count_dec > 0 and best_price:
      unit_price = best_price / unit_count_dec
      unit_price_str = f"{unit_label} {unit_price:.2f}" if unit_label else None
  ```
  `Decimal(str(60.0))` → `Decimal("60.0")` which divides cleanly with other Decimals. `int(Decimal("60.0"))` → `60` for the schema field. Garbage strings and zero are caught by the guards.
- **Affected files:** `src/farmafacil/scrapers/farmatodo.py` (`_hit_to_result` lines 116-156), `tests/test_farmatodo_scraper.py` (5 new regression tests).
- **Regression tests added (5):**
  - `test_hit_to_result_measurePum_float_no_crash` — **the actual bug**: uses `"measurePum": 60.0` and asserts `result.unit_label == "c/u 20.00"` (1200 / 60.0).
  - `test_hit_to_result_measurePum_int` — int case (common) still works.
  - `test_hit_to_result_measurePum_missing` — no field, `unit_count=None`, `unit_label=None`.
  - `test_hit_to_result_measurePum_zero` — no `ZeroDivisionError`, `unit_label=None`.
  - `test_hit_to_result_measurePum_garbage_string` — non-numeric falls back, no crash.
- **Impact:** Every Farmatodo search that matched any product with a float `measurePum` (typically multi-unit items with decimal counts) returned zero results instead of the full product list. This was **silently** reported to users as a "connection error" for Farmatodo (the Item 21 failed-pharmacies UI, which correctly identifies scraper exceptions). Users experiencing this got half the pharmacy coverage they should have.
- **Deployment note:** Pure code fix, no schema change, no migration. Ship with Item 33.

### Item 34: Skip ¿Te sirvió? Prompt on Zero-Result / Partial-Failure Responses

- **Status:** DONE (v0.12.5, 2026-04-11)
- **Added:** 2026-04-10 (from same v0.12.3 prod test)
- **Completed:** 2026-04-11
- **Priority:** P2
- **Problem:** When a drug search returned zero results (or all scrapers failed), the bot still appended the `¿Te sirvió? (sí/no)` feedback prompt at the end of the message. This was nonsensical — there was nothing to rate — and it taught users that the feedback prompt meant "did the bot understand you?" instead of "did these results help?". The v0.12.3 prod test captured a screenshot where the clarification flow failed, returned "No encontramos resultados", and still asked "¿Te sirvió?" — a clear UX confusion signal.
- **Solution implemented:**
  - New pure-logic helper `_should_ask_feedback(response)` in `handler.py` that returns False when (a) `response.results` is empty, OR (b) `len(failed_pharmacies) >= len(ACTIVE_SCRAPERS)` (total outage guard). Partial failures (1 of 3 scrapers down but at least one returned products) still get the prompt — the user has real results to rate even if coverage was incomplete.
  - New `MSG_RETRY_DIFFERENT_NAME` constant: `"💡 Si no encontraste lo que buscabas, prueba con otro nombre o el principio activo. Ejemplo: acetaminofen en vez de tachipirin."` — shown instead of `¿Te sirvió?` when the prompt is suppressed, so the user still has a clear next action.
  - `_handle_drug_search` now gates the feedback prompt + `set_onboarding_step("awaiting_feedback")` on `_should_ask_feedback(response)`. On suppression, it sends `MSG_RETRY_DIFFERENT_NAME` and logs the skip with query + results count + failed scraper list for analytics.
  - `ACTIVE_SCRAPERS` was added to the handler imports so the helper can compare against the real active scraper count (not a hardcoded number).
- **Affected files:** `src/farmafacil/bot/handler.py` (`ACTIVE_SCRAPERS` import, new `_should_ask_feedback` helper, new `MSG_RETRY_DIFFERENT_NAME`, gated feedback block in `_handle_drug_search`), `tests/test_feedback_suppression.py` (new, 11 tests).
- **Tests added (11 new):**
  - `TestShouldAskFeedbackUnit` (7 pure-logic tests): happy path with results + no failures, zero results alone, zero results + partial failure, 1-of-3 partial failure + results, 2-of-3 partial failure + results, total outage, empty failed list with results.
  - 4 integration tests against `_handle_drug_search` with mocked `search_drug`/`send_text_message`/`set_onboarding_step`: zero-result skips prompt and sends retry hint and does NOT enter `awaiting_feedback`; results present still sends prompt + enters `awaiting_feedback` exactly once; partial failure with results still sends prompt; total failure + zero results skips prompt and sends retry hint.
- **Test suite:** **520 passed** (was 509, +11 new). Verified with `rm farmafacil.db && pytest -m "not integration" -q`.
- **Effort:** Small (1h)

### Item 30: `find_cross_chain_matches` Unindexed Full-Scan

- **Status:** DONE (v0.12.6, 2026-04-11)
- **Added:** 2026-04-10
- **Priority:** P3
- **Problem:** `src/farmafacil/services/product_cache.py:258-272` pulled **every** product with non-null `keywords` into memory and filtered in Python with `all(kw in product_kw_set for kw in query_keywords)`. Fine at the current catalog size but would become expensive at thousands of products — each cross-chain search walked the full table plus all prices via `lazy="selectin"`.
- **Solution shipped (option b — portable across SQLite + Postgres):**
  - New `product_keywords` inverted-index table: `(id PK, product_id FK CASCADE, keyword VARCHAR(100))` with indexes on both `product_id` and `keyword`, plus `UniqueConstraint(product_id, keyword)`. `Product.keywords` JSON column retained as the denormalized cache; the new table is strictly an index for `find_cross_chain_matches`.
  - `find_cross_chain_matches` rewritten to do a single indexed SQL query: `SELECT product_id FROM product_keywords WHERE keyword IN (...) GROUP BY product_id HAVING COUNT(DISTINCT keyword) = N`, then `SELECT products WHERE id IN (matching_ids)`. No more full-table-scan + in-memory filter.
  - New `_sync_product_keywords(session, product_id, keywords)` helper: deletes existing rows for the product and inserts one row per unique lowercase token. Called from `_upsert_product` at the end of both create and update branches so token churn from drug_name edits is handled idempotently in the same transaction.
  - Idempotent backfill via new `_backfill_product_keywords()` in `db/session.py::init_db()` — follows the additive-migration pattern from Item 31 (v0.12.3). No-op if `product_keywords` already has rows or if no products have keyword JSON data; otherwise walks every product with non-null `keywords` and populates one row per unique token inside a single transaction so a mid-backfill crash leaves the table empty for the next startup to retry. Handles existing production deployments at container startup with no manual DDL.
  - Inside the handler clarification flow the new table is populated automatically via the save_search_results → _upsert_product path; no handler changes needed.
- **Files modified:** `src/farmafacil/models/database.py` (new `ProductKeyword` class), `src/farmafacil/services/product_cache.py` (rewrite of `find_cross_chain_matches` + new `_sync_product_keywords` + wiring from `_upsert_product`), `src/farmafacil/db/session.py` (new `_backfill_product_keywords` called from `init_db`), `tests/test_product_catalog.py` (new `TestProductKeywordSync` class with 3 tests + new `TestFindCrossChainMatchesIndexed` class with 5 tests + cleanup fixture updated to delete `ProductKeyword` defensively).
- **Tests added (8 new):** `test_save_populates_product_keywords`, `test_upsert_replaces_stale_keywords`, `test_dedupes_repeated_tokens`, `test_finds_product_when_all_keywords_present`, `test_skips_product_missing_any_keyword`, `test_exclude_names_filters_out_duplicates`, `test_empty_query_returns_empty`, `test_returns_multiple_matches`.
- **Test suite:** **528 passed** (was 520, +8 new). Verified with `rm farmafacil.db && pytest -m "not integration" -q`.

---

## P3 — Low

### Item 3: Farmacias XANA / DRONENA Scraper

- **Status:** DEFERRED
- **Added:** 2026-03-30
- **Updated:** 2026-03-30
- **Problem:** Farmacias XANA is the retail brand of Droguería NENA (DRONENA), a major Venezuelan pharmaceutical distributor aggressively growing in the popular sector after absorbing Farmahorro. XANA has no public consumer-facing online catalog — only an Instagram presence (@xanafarmacia). DRONENA operates a B2B portal at odoo.dronena.com (Odoo ERP) which requires credentials.
- **Suggested solution:** Monitor for a consumer-facing catalog. Potential approaches: (1) If DRONENA opens their Odoo catalog publicly, use the Odoo REST API. (2) If XANA launches a website, scrape it. (3) Explore if Farmahorro's catalog (Item 4) includes XANA-branded locations since DRONENA absorbed them.
- **Affected files:** TBD
- **Effort:** Unknown
- **Notes:** DRONENA = Droguería NENA (parent/distributor). XANA = retail pharmacy brand. Absorbed Farmahorro but Farmahorro still operates independently. DRONENA uses Odoo ERP for B2B. No consumer API available as of March 2026. Active on social: @dronenave, @xanafarmacia.

---

## Completed

*(No items completed yet)*
