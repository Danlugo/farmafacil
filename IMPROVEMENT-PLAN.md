# FarmaFacil Improvement Plan

> Generated from 8-agent architecture review on 2026-05-19 (v0.22.6)
> Items 1-49: completed in prior versions (not listed here)
> Items 50+: from the v0.22.6 full-spectrum review

---

## Phase 1 — Security Hotfix (P0)

### Item 50: Authenticate all PII-exposing API endpoints
- **Status:** ✅ DONE (2026-05-19, v0.23.0)
- **Priority:** P0
- **Effort:** Low (~30 min)
- **Problem:** `/api/v1/users`, `/api/v1/conversations`, `/api/v1/stats`, `/admin/conversations/*`, `/api/v1/conversations/export`, `POST /api/v1/scheduled-tasks/{id}/run` are reachable without auth via the public ngrok URL. They expose phone numbers, GPS coordinates, full chat history, and allow triggering scheduled tasks.
- **Fix:** Added `_admin: str = Depends(_require_admin)` to all 12 PII-exposing endpoints in `api/routes.py`. HTTP Basic auth required on every endpoint except `/health`, `/webhook`, and `/docs`.
- **Files:** `src/farmafacil/api/routes.py`
- **Found by:** Security, SRE, Architecture (3 agents independently)

### Item 51: Add WhatsApp webhook HMAC-SHA256 signature verification
- **Status:** ✅ DONE (2026-05-19, v0.23.0)
- **Priority:** P0
- **Effort:** Low (~2 hours)
- **Problem:** Meta sends `X-Hub-Signature-256` on every webhook POST. The app ignores it entirely. Anyone who discovers the ngrok URL can spoof messages from any phone number, including admin-flagged phones.
- **Fix:** Added `WHATSAPP_APP_SECRET` env var. New `_verify_signature()` in webhook.py computes HMAC-SHA256 and compares with `hmac.compare_digest()`. Returns 403 on mismatch. GET verify_token now uses `secrets.compare_digest()` and rejects unconfigured tokens. Dev mode: skips HMAC when secret not set (with per-request warning).
- **Files:** `src/farmafacil/bot/webhook.py`, `src/farmafacil/config.py`
- **Found by:** Security, SRE

### Item 52: Remove hardcoded secrets and rotate credentials
- **Status:** ✅ DONE (2026-05-19, v0.23.0)
- **Priority:** P0
- **Effort:** Low (~1 hour)
- **Problem:** `ALGOLIA_API_KEY="869a91..."`, `ALGOLIA_APP_ID="VCOJEYD2PO"`, `WHATSAPP_VERIFY_TOKEN="farmafacil_verify_2026"` hardcoded as defaults in `config.py`. `POSTGRES_PASSWORD: farmafacil` in `docker-compose.yml`. Real verify token in `.env.example`. All in git history since initial commit.
- **Fix:** Removed all hardcoded defaults (empty string + startup warnings). `docker-compose.yml` uses `${POSTGRES_PASSWORD}`. `.env.example` uses placeholders. Added Algolia keys to production/local `.env` files. App/Postgres ports bound to 127.0.0.1.
- **Files:** `src/farmafacil/config.py`, `docker-compose.yml`, `.env.example`
- **Found by:** Security, Code Quality, Architecture, SRE (4 agents)

### Item 53: Fix `_handle_admin_media` token counting bug
- **Status:** ✅ DONE (2026-05-19, v0.23.0)
- **Priority:** P0
- **Effort:** Low (~15 min)
- **Problem:** `_handle_admin_media` at `handler.py:944-950` passes `sender` (string phone number) as `user_id` (expects int), and references `.tokens_in`/`.tokens_out` which don't exist on `AdminTurnResult` (correct fields: `.input_tokens`/`.output_tokens`). Silently corrupts token accounting for admin image messages.
- **Fix:** Changed `sender` → `user.id`, `.tokens_in` → `.input_tokens`, `.tokens_out` → `.output_tokens`.
- **Files:** `src/farmafacil/bot/handler.py`
- **Found by:** Code Quality

### Item 54: Harden admin login against brute-force
- **Status:** ✅ DONE (2026-05-19, v0.23.0)
- **Priority:** P0
- **Effort:** Low (~30 min)
- **Problem:** `/admin/login` uses `==` comparison (timing-observable) and has no rate limiting or lockout. The `_require_admin` in routes.py correctly uses `secrets.compare_digest` but the SQLAdmin login does not.
- **Fix:** Replaced `==` with `hmac.compare_digest()` for both username and password. Added truthiness guards to prevent matching unconfigured empty credentials.
- **Files:** `src/farmafacil/api/admin.py`
- **Found by:** Security

### Item 55: Bind Postgres port to localhost only
- **Status:** ✅ DONE (2026-05-19, v0.23.0)
- **Priority:** P0
- **Effort:** Low (~5 min)
- **Problem:** `docker-compose.yml` exposes Postgres on `0.0.0.0` with default credentials. Database directly reachable from the LAN.
- **Fix:** Changed port binding to `127.0.0.1:5432:5432`. Also bound app port to `127.0.0.1:8000:8000` (all external traffic goes through ngrok).
- **Files:** `docker-compose.yml`
- **Found by:** Security

---

## Phase 2 — Performance Unlock (P1)

### Item 56: Switch to async Anthropic SDK
- **Status:** ✅ DONE (2026-05-19, v0.24.0)
- **Priority:** P1
- **Effort:** Med (~3 hours)
- **Problem:** `anthropic.Anthropic().messages.create()` is synchronous blocking I/O in async handlers. Freezes the entire event loop for 1-5s per LLM call. Two simultaneous users = one waits for the other's Claude call. Also creates a new client instance per call (8 sites) with repeated TLS handshake overhead.
- **Fix:** Module-level `_get_client()` returns lazy `AsyncAnthropic` singleton. All 8 call sites now use `await client.messages.create(...)`.
- **Files:** `ai_responder.py` (5 sites + singleton), `handler.py` (2 extractors), `user_memory.py` (1 site)
- **Found by:** Performance, SRE, Code Quality, Architecture (4 agents)

### Item 57: Add settings cache
- **Status:** ✅ DONE (2026-05-19, v0.24.0)
- **Priority:** P1
- **Effort:** Low (~1 hour)
- **Problem:** `get_setting()` opens a fresh DB session and SELECT per call. 8+ calls per drug-search message for values that change at most once per admin session.
- **Fix:** Module-level `_cache: dict[str, tuple[str, float]]` with 60s TTL via `time.monotonic()`. Explicit invalidation in `set_setting()` and `set_default_model()`. `clear_settings_cache()` exposed for tests.
- **Files:** `services/settings.py`
- **Found by:** Performance, Code Quality, Architecture (3 agents)

### Item 58: Make webhook processing non-blocking
- **Status:** ✅ DONE (2026-05-19, v0.24.0)
- **Priority:** P1
- **Effort:** Med (~2 hours)
- **Problem:** Full handler execution (AI + pharmacy APIs + WhatsApp sends) happens before returning 200 to Meta. Voice transcription or slow Algolia can exceed Meta's 5s retry window, causing duplicate webhooks.
- **Fix:** `_fire_and_forget()` wraps handler in `asyncio.create_task()`, `_safe_handle()` catches exceptions with logging and re-raises `CancelledError` for clean shutdown. `_background_tasks` set prevents GC. `log_inbound` + dedup check remain synchronous before 200.
- **Files:** `bot/webhook.py`
- **Found by:** SRE, Architecture

### Item 59: Consolidate user DB round-trips
- **Status:** ✅ DONE (2026-05-19, v0.24.0)
- **Priority:** P1
- **Effort:** Med (~3 hours)
- **Problem:** `set_onboarding_step`, `update_last_search`, etc. each re-SELECT the full User row before UPDATE. 10-12 user-table round-trips per drug search message.
- **Fix:** `set_onboarding_step` and `update_last_search` now use direct `update(User).where().values()` — no SELECT. `set_onboarding_step` returns `None` (callers never used return value).
- **Files:** `services/users.py`
- **Found by:** Performance, Code Quality

### Item 60: Add `pool_pre_ping=True` to Postgres engine
- **Status:** ✅ DONE (2026-05-19, v0.24.0)
- **Priority:** P1
- **Effort:** Low (~5 min)
- **Problem:** Idle connections beyond server `tcp_keepalives_idle` are silently dropped. Next query on stale connection raises `OperationalError`. Causes sporadic 500 errors.
- **Fix:** Added `pool_pre_ping=True` to Postgres engine kwargs alongside `pool_size=5, max_overflow=10`.
- **Files:** `db/session.py`
- **Found by:** SRE

### Item 61: Sanitize Content-Disposition filename in export endpoints
- **Status:** ✅ DONE (2026-05-19, v0.24.0)
- **Priority:** P1
- **Effort:** Low (~15 min)
- **Problem:** Export endpoints build filename from unsanitized `phone` parameter. Crafted values can inject response headers.
- **Fix:** `_sanitize_filename_part(value)` strips non-alphanumeric chars via allowlist regex `[A-Za-z0-9_\-]`, truncates to 30 chars. Applied to CSV and DOCX export filenames.
- **Files:** `api/routes.py`
- **Found by:** Security

---

## Phase 3 — UX Polish (P1/P2)

### Item 62: Remove dead "cambiar preferencia" keyword
- **Status:** ✅ DONE (v0.24.0, 2026-05-19)
- **Priority:** P1
- **Effort:** Low (~15 min)
- **Problem:** `preference_change` keyword is seeded and recognized but no handler branch exists. User gets zero response. Feature deprecated since v0.15.2.
- **Fix:** Remove `preference_change` entries from `DEFAULT_INTENTS` in `seed.py`. Optionally add migration to deactivate existing rows in `intent_keywords`.
- **Files:** `src/farmafacil/db/seed.py`
- **Found by:** Product

### Item 63: Add Google Maps links to nearby-store results
- **Status:** ✅ DONE (v0.24.0, 2026-05-19)
- **Priority:** P1
- **Effort:** Low (~15 min)
- **Problem:** `format_nearby_stores()` shows distance + address but no navigation link. `format_store_info()` already includes Maps links — inconsistency.
- **Fix:** Add `f"https://maps.google.com/?q={lat},{lng}"` per store in `format_nearby_stores()`. Data already available in store dict.
- **Files:** `src/farmafacil/bot/formatter.py`
- **Found by:** Product

### Item 64: Reply to unsupported message types
- **Status:** ✅ DONE (v0.24.0, 2026-05-19)
- **Priority:** P2
- **Effort:** Low (~15 min)
- **Problem:** Stickers, contacts, and other unsupported types get silent non-response. Users think bot is broken.
- **Fix:** Add fallback reply "No puedo procesar ese tipo de mensaje. Enviame texto, foto, documento, nota de voz o ubicacion." Exclude `reaction` and `status` types.
- **Files:** `src/farmafacil/bot/webhook.py`
- **Found by:** Product

### Item 65: Guard feedback prompt after empty nearby-store results
- **Status:** ✅ DONE (v0.24.0, 2026-05-19)
- **Priority:** P2
- **Effort:** Low (~10 min)
- **Problem:** "Te sirvio? (si/no)" appears even when 0 stores found — makes no sense.
- **Fix:** Add `if stores:` before `awaiting_feedback` + feedback prompt in `_handle_nearest_store()`.
- **Files:** `src/farmafacil/bot/handler.py`
- **Found by:** Product

### Item 66: Add WhatsApp message length guard
- **Status:** ✅ DONE (v0.24.0, 2026-05-19)
- **Priority:** P2
- **Effort:** Med (~1 hour)
- **Problem:** WhatsApp has 4096-char limit. Long search results with 8 products across 3 chains can exceed it, causing silent truncation.
- **Fix:** Count chars during formatting. Stop adding products at ~3800 chars, append "... y N productos mas."
- **Files:** `src/farmafacil/bot/formatter.py`
- **Found by:** Product

### Item 67: Fix Spanish register consistency (vos/tu mixing) and missing accents
- **Status:** ✅ DONE (v0.24.0, 2026-05-19)
- **Priority:** P2
- **Effort:** Low (~30 min)
- **Problem:** Some messages use voseo ("Elegi", "Podes", "Volves"), most use tuteo. Missing accents on "ubicacion", "Que estas buscando", etc.
- **Fix:** Standardize all MSG_ constants to tuteo. Audit and add proper diacritics.
- **Files:** `src/farmafacil/bot/handler.py` (MSG_ constants)
- **Found by:** Product

### Item 68: Expand HELP_MESSAGE with discoverable features
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P2
- **Effort:** Low (~15 min)
- **Problem:** HELP_MESSAGE omits voice notes, image/prescription photos, location pin sharing, "ver similares", "farmacias cercanas", `/stats`.
- **Fix:** Add feature discovery lines for each.
- **Files:** `src/farmafacil/services/intent.py`
- **Found by:** Product

---

## Phase 4 — Test Hardening (P1/P2)

### Item 69: Add tests for untested security-boundary services
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P1
- **Effort:** Low (~2 hours)
- **Problem:** `file_manager.py` (path traversal guard), `web_search.py` (Brave API), `media.py` (size limits, Vision encoding) have zero test coverage. `file_manager.py` is a security boundary.
- **Fix:** Create `test_file_manager.py` (traversal, scope boundaries), `test_web_search.py` (mocked HTTP), `test_media.py` (oversized data, unsupported MIME).
- **Files:** `tests/test_file_manager.py` (new), `tests/test_web_search.py` (new), `tests/test_media.py` (new)
- **Found by:** Test Engineer

### Item 70: Fix 7 known flaky tests
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P1
- **Effort:** Low (~2 hours)
- **Problem:** Root causes identified: `test_chat_debug` ×2 — no row cleanup for phone `5558812222`; `test_location_sharing` ×3 — unmocked Nominatim HTTP calls; `test_user_memory` ×1 — fixture `return` instead of `yield` (no teardown); `test_handler` ×1 — phone collision from incomplete cleanup.
- **Fix:** Add autouse cleanup fixtures; mock Nominatim in 3 tests; change fixture to `yield` + teardown.
- **Files:** `tests/test_chat_debug.py`, `tests/test_location_sharing.py`, `tests/test_user_memory.py`, `tests/test_handler.py`
- **Found by:** Test Engineer

### Item 71: Add HTTP-level tests for audio endpoint
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P2
- **Effort:** Med (~1 hour)
- **Problem:** `GET /api/v1/audio/{id}` is security-sensitive (serves user audio PII) but only checked structurally. No HTTP test verifies 401 for unauthenticated, 404 for missing, 403 for path traversal.
- **Fix:** Create tests with `AsyncClient`: no creds → 401, wrong creds → 401, missing record → 404, path escape → 403.
- **Files:** `tests/test_voice.py` or new `tests/test_audio_endpoint.py`
- **Found by:** Test Engineer

---

## Phase 5 — Structural Refactor (P1-P2, future)

### Item 72: Decompose handler.py into focused modules
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P1
- **Effort:** High (~8 hours)
- **Problem:** 2,524-line monolith mixes 7 responsibilities. Single largest maintenance risk in the codebase.
- **Fix:** Extract: `bot/onboarding.py`, `bot/admin.py`, `bot/media_handler.py`, `bot/feedback.py`, `bot/search_handler.py`. Keep handler.py as ~150-line dispatcher. Pure structural refactor — existing 1,196 tests remain valid.
- **Files:** `src/farmafacil/bot/handler.py` → 5 new modules
- **Found by:** Code Quality, Architecture

### Item 73: Split admin_chat.py into domain modules
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P2
- **Effort:** Med (~3 hours)
- **Problem:** 1,785 lines, 40+ tool functions in a flat file.
- **Fix:** Split into `admin_chat/user_tools.py`, `admin_chat/feedback_tools.py`, `admin_chat/ai_tools.py`, `admin_chat/registry.py`.
- **Files:** `src/farmafacil/services/admin_chat.py` → package
- **Found by:** Architecture, Code Quality

### Item 74: Bulk product upsert
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P2
- **Effort:** High (~4 hours)
- **Problem:** Row-by-row upsert: ~40 ORM operations per 10-result cache-miss search.
- **Fix:** Use `INSERT ... ON CONFLICT DO UPDATE`. Batch keyword sync. Collapse to ~4 statements.
- **Files:** `src/farmafacil/services/product_cache.py`
- **Found by:** Performance

### Item 75: Consolidate geocoding modules
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P2
- **Effort:** Med (~2 hours)
- **Problem:** `geocode.py` (legacy) and `location.py` (v0.19.0 authoritative) overlap. Three callers still use the old module.
- **Fix:** Migrate all callers to `location.py` equivalents. Deprecate and remove `geocode.py`.
- **Files:** `src/farmafacil/services/geocode.py`, `src/farmafacil/services/location.py`, callers
- **Found by:** Code Quality

### Item 76: Remove dead `image_grid.py` module
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P2
- **Effort:** Low (~15 min)
- **Problem:** Zero production imports since v0.15.2. Dead code with tests for a removed feature.
- **Fix:** Delete `services/image_grid.py` and `tests/test_image_grid.py`.
- **Files:** `src/farmafacil/services/image_grid.py`, `tests/test_image_grid.py`
- **Found by:** Code Quality

---

## Phase 6 — Infrastructure & Observability (P2-P3, future)

### Item 77: Add CSRF protection to admin dashboard
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P2
- **Effort:** Med (~2 hours)
- **Problem:** SQLAdmin cookie-based sessions with no CSRF token on form submissions.
- **Fix:** Add `SameSite=Strict` on session cookie. Consider `starlette_csrf` middleware.
- **Files:** `src/farmafacil/api/admin.py`, `src/farmafacil/api/app.py`
- **Found by:** Security

### Item 78: Create module-level httpx clients with connection pooling
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P2
- **Effort:** Med (~2 hours)
- **Problem:** New `httpx.AsyncClient()` created on every API call — no connection pooling, repeated TLS handshakes.
- **Fix:** Module-level clients initialized in lifespan, closed in shutdown.
- **Files:** `src/farmafacil/scrapers/farmatodo.py`, `src/farmafacil/scrapers/vtex.py`, `src/farmafacil/bot/whatsapp.py`
- **Found by:** Architecture

### Item 79: Add scheduler task timeout
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P2
- **Effort:** Med (~1 hour)
- **Problem:** Tasks run without timeout. OSM backfill can block scheduler loop for 27+ minutes.
- **Fix:** Wrap `_execute_task()` with `asyncio.wait_for(..., timeout=...)`.
- **Files:** `src/farmafacil/services/scheduler.py`
- **Found by:** Architecture, SRE

### Item 80: Fix rate limiting behind ngrok (X-Forwarded-For)
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P2
- **Effort:** Med (~1 hour)
- **Problem:** `get_remote_address` reads ngrok's internal IP — all traffic shares one rate-limit bucket.
- **Fix:** Custom `key_func` that reads `X-Forwarded-For` header when behind trusted proxy.
- **Files:** `src/farmafacil/api/limiter.py`
- **Found by:** Security, SRE

### Item 81: Add prompt injection delimiter defense
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P2
- **Effort:** Low (~15 min)
- **Problem:** User messages passed to Claude without delimiters. Crafted messages could attempt to override structured output format.
- **Fix:** Wrap user input in `<user_message>` XML tags in the messages list.
- **Files:** `src/farmafacil/services/ai_responder.py`
- **Found by:** Security

### Item 82: Enhance health check with DB connectivity
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P3
- **Effort:** Low (~15 min)
- **Problem:** `/health` returns `{"status":"ok"}` without verifying DB. Docker marks container healthy even when Postgres is down.
- **Fix:** Add `SELECT 1` via engine. Return 503 on failure.
- **Files:** `src/farmafacil/api/routes.py`
- **Found by:** SRE

### Item 83: Add composite index on conversation_logs
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P3
- **Effort:** Low (~15 min)
- **Problem:** No index on `(phone_number, created_at)` — the most common query pattern. Full table scan as logs grow.
- **Fix:** Add `Index("idx_convlog_phone_created", "phone_number", "created_at")` to model.
- **Files:** `src/farmafacil/models/database.py`
- **Found by:** SRE, Architecture

### Item 84: Update architecture docs
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P3
- **Effort:** Med (~2 hours)
- **Problem:** `docs/architecture.md` last updated 2026-03-31. Missing voice messages, scheduler, admin chat, AI role system, 6+ tables, relevance filter details.
- **Fix:** Update component diagram, add missing tables, document scheduler, voice flow, admin chat, configuration layers.
- **Files:** `docs/architecture.md`
- **Found by:** Architecture

### Item 85: Clean up temp image files after send
- **Status:** ✅ DONE (v0.25.0, 2026-05-20)
- **Priority:** P2
- **Effort:** Low (~15 min)
- **Problem:** `generate_product_grid` creates temp files with `delete=False`. No cleanup after `send_local_image`. Slowly fills `/tmp`.
- **Fix:** Add `os.unlink(tmp_path)` in try/finally after send.
- **Files:** `src/farmafacil/bot/handler.py` (caller site)
- **Found by:** SRE

---

---

## Phase 7 — Backlog Cleanup (v0.26.0)

### 86. UserMemory `__repr__` (Q4)
- **Priority:** P3 — Cosmetic
- **Problem:** UserMemory was the only model without a `__repr__`, making debugging opaque.
- **Status:** ✅ DONE (v0.26.0, 2026-05-20)
- **Solution:** Added `__repr__` with id, user_id, and 40-char text preview. File: `models/database.py`.

### 87. Flaky test isolation (Q5)
- **Priority:** P2 — Test reliability
- **Problem:** 12 tests failed intermittently due to orphaned data in persistent SQLite DB (UserMemory, SearchLog rows from prior runs) and missing FK enforcement (ON DELETE CASCADE silently ignored).
- **Status:** ✅ DONE (v0.26.0, 2026-05-20)
- **Solution:** Enabled `PRAGMA foreign_keys=ON` via SQLAlchemy `connect` event in `db/session.py`. Added `passive_deletes=True` to ORM relationships (UserMemory, ProductPrice, AiRoleRule, AiRoleSkill) so `session.delete()` defers to DB-level CASCADE. Fixed test fixtures: added cleanup/teardown in `test_user_memory.py`, `test_admin_stats.py`, `test_clarification.py`. Cleaned 15 orphaned UserMemory + 209 orphaned SearchLog rows from dev DB.

### 88. Curated drug-keyword library (Q7)
- **Priority:** P3 — Performance
- **Problem:** Common drug names (losartan, acetaminofen, etc.) fell through keyword cache and hit the AI classifier, wasting an LLM round-trip.
- **Status:** ✅ DONE (v0.26.0, 2026-05-20)
- **Solution:** Added 76 common drug names as `drug_search` keywords in `db/seed.py`. Updated `classify_intent_keywords` in `intent.py` to set `drug_query=text_lower` when a `drug_search` keyword matches, so the handler can pass it directly to the scraper.

### 89. Digit-overlap residual leak (Q8)
- **Priority:** P2 — Search quality
- **Problem:** "Aspirina 500" matched "Vitamina C 500 Mg" (score 0.75) because the digit token "500" satisfied the Signal 0 floor check alone.
- **Status:** ✅ DONE (v0.26.0, 2026-05-20)
- **Solution:** Digit-only tokens are excluded from the Signal 0 floor gate in `services/relevance.py`. They still contribute to the overlap *score* once the floor is passed by a meaningful (non-digit) token. Aspirina 500 → Vitamina C 500: 0.0. Aspirina 500 → Aspirina 500 mg Bayer: 1.0.

### 90. Settings cache thundering-herd (v0.24.0 review)
- **Priority:** P2 — Reliability
- **Problem:** Multiple concurrent `get_setting()` calls on cache miss all hit the DB simultaneously.
- **Status:** ✅ DONE (v0.26.0, 2026-05-20)
- **Solution:** Added `asyncio.Lock` with double-check pattern in `services/settings.py`. Fast path (cache hit) is lock-free; only cache misses acquire the lock.

### 91. AsyncAnthropic singleton doc (v0.24.0 review)
- **Priority:** P3 — Documentation
- **Problem:** `_get_client()` lazy init lacked thread-safety documentation.
- **Status:** ✅ DONE (v0.26.0, 2026-05-20)
- **Solution:** Added docstring noting asyncio single-threaded safety and threading caveat. File: `services/ai_responder.py`.

### 92. Background task set backpressure warning (v0.24.0 review)
- **Priority:** P2 — Observability
- **Problem:** `_background_tasks` set could grow unbounded with no visibility.
- **Status:** ✅ DONE (v0.26.0, 2026-05-20)
- **Solution:** Added `_MAX_BACKGROUND_TASKS=100` threshold with `logger.warning` in `bot/webhook.py` `_fire_and_forget()`.

### 93. Silent no-op UPDATE logging (v0.24.0 review)
- **Priority:** P3 — Observability
- **Problem:** `set_onboarding_step` and `update_last_search` silently succeeded even when no user matched the phone number.
- **Status:** ✅ DONE (v0.26.0, 2026-05-20)
- **Solution:** Added `result.rowcount == 0` warning log in `services/users.py` for both functions.

---

## Phase 8 — Group Relay Integration (v0.27.0)

### Item 94: Chat Relay API endpoint for Chamo group bot
- **Priority:** P1 — Feature
- **Problem:** WhatsApp Business API limits test phone numbers to 5. Need a way for more users to interact with FarmaFacil via a WhatsApp group, relayed by the Chamo bot (Baileys-based, no Meta API limits).
- **Status:** ✅ DONE (v0.27.0, 2026-05-21)
- **Solution:** Added `POST /api/v1/chat` endpoint with contextvars-based proxy mode in `whatsapp.py`. When proxy mode is active (`start_collecting()`), all `send_text_message`, `send_image_message`, `send_interactive_list` calls append to a list instead of calling WhatsApp API; `send_read_receipt` becomes a no-op. Zero changes to `handler.py` — all 114+ send_* call sites work transparently. Pydantic models: `ChatRequest` (sender_id, sender_name, text), `ChatResponseItem`, `ChatResponse`. Rate limit 30/min, no auth (matches `/api/v1/search`).
- **Files:** `src/farmafacil/bot/whatsapp.py`, `src/farmafacil/api/routes.py`, `tests/test_chat_endpoint.py` (26 tests), `docs/chamo-farmafacil-skill.md` (integration guide), `docs/api-reference.md`, `docs/architecture.md`
- **Tests:** 26 new tests covering proxy mode unit, endpoint integration, validation, error resilience

---

## Phase 9 — Relay Bug Fixes (v0.27.1)

### Item 95: Fix multiline AI response parser truncation
- **Priority:** P0 — Bug
- **Problem:** `_parse_structured_response()` in `ai_responder.py` only captured the first line of each field value. When the AI returned multiline RESPONSE content (bullet lists, disclaimers for vague symptom queries like "necesito medicinas para refriado"), everything after the first line was silently dropped. Users saw "las opciones más comunes son:" with an empty list.
- **Status:** ✅ DONE (v0.27.1, 2026-05-21)
- **Solution:** Rewrote `_parse_structured_response()` to use a multiline accumulation pattern (matching `_parse_admin_action()`). Lines are buffered under the current key until the next recognised key or end-of-string. KNOWN_KEYS frozenset for O(1) lookup.
- **Files:** `src/farmafacil/services/ai_responder.py`, `tests/test_multiline_response_parser.py` (11 new tests)
- **Tests:** 11 new tests covering bullet lists, numbered lists, emoji bullets, disclaimers, colons in values, multiline followed by another key, single-line regression, empty RESPONSE, structure preservation

### Item 96: Fix product upsert duplicate crash
- **Priority:** P1 — Bug
- **Problem:** Farmatodo + Locatel returning overlapping product IDs in the same search batch caused PostgreSQL `ON CONFLICT DO UPDATE command cannot affect row a second time` crash during bulk INSERT.
- **Status:** ✅ DONE (v0.27.1, 2026-05-21)
- **Solution:** Deduplicate `product_rows` by `(external_id, pharmacy_chain)` and `price_rows` by `(product_id, city_code)` before INSERT, keeping the last occurrence (freshest scraper result).
- **Files:** `src/farmafacil/services/product_cache.py`, `tests/test_location_confirm_and_dedup.py` (6 dedup tests)

### Item 97: Add location confirmation step for low-confidence geocode
- **Priority:** P2 — UX
- **Problem:** During onboarding, low-confidence geocode results were auto-accepted, confusing users who saw "couldn't recognise your location" immediately followed by "you're all set!".
- **Status:** ✅ DONE (v0.27.1, 2026-05-21)
- **Solution:** New `awaiting_location_confirm` onboarding step. Low-confidence results are stashed in `_pending_location_confirm` dict, user is prompted "📍 Encontré *{location}* — ¿es correcto?". Responds "sí" (save + continue), "no" (discard + re-ask), or anything else (treated as new location attempt).
- **Files:** `src/farmafacil/bot/handler.py`, `tests/test_location_confirm_and_dedup.py` (15 location confirm tests)

---

## Phase 10 — Voice Relay Support (v0.28.0)

### Item 98: Voice message relay endpoint
- **Priority:** P1 — Feature
- **Problem:** Chamo group relay only supported text messages. When users sent voice notes in the WhatsApp group, they were silently ignored. FarmaFacil already has full Whisper transcription support for direct messages, but no endpoint for external relay bots to forward raw audio.
- **Status:** ✅ DONE (v0.28.0, 2026-05-21)
- **Solution:** New `POST /api/v1/chat/voice` endpoint accepting multipart/form-data (sender_id, sender_name, audio file). Pipeline: size check → user lookup → save audio to disk → Whisper transcription → VoiceMessage DB record → proxy-mode handler (same as text chat endpoint). Rate limited to 30/min (Whisper is expensive). On Chamo side: `chamo-bot.ts` detects `audioMessage` in group messages, `group-relay-service.ts` downloads via Baileys `downloadMediaMessage`, POSTs FormData to `{apiUrl}/voice` with doubled timeout.
- **Files:** `src/farmafacil/api/routes.py` (new endpoint), `tests/test_chat_voice_endpoint.py` (6 new tests), `docs/chamo-farmafacil-skill.md` (updated), Chamo: `src/chamo-bot.ts`, `src/services/group-relay-service.ts`
- **Tests:** 6 new tests (happy path, transcription failure, missing audio 422, missing sender 422, short sender 422, oversized audio 413)

## Phase 11 — Relay Context Fix

### Item 99: Add conversation logging to relay endpoints
- **Priority:** P1 — Bug Fix
- **Problem:** After searching via the relay (e.g. "omeprazol"), follow-up questions like "cual es la mas barata" failed because the AI classifier had no conversation history. Relay endpoints bypassed `webhook.py` (where `log_inbound` lives) and proxy mode skipped `_send_message` (where `log_outbound` lives), so `get_recent_history()` returned empty for relay users.
- **Status:** ✅ DONE (v0.28.1, 2026-05-21)
- **Solution:** Added `log_inbound` call before handler and `_log_relay_responses` helper after handler in both `/api/v1/chat` and `/api/v1/chat/voice`. Inbound logged before handler (matching webhook pattern) so `get_recent_history()` inside `classify_with_ai()` sees prior messages. Voice-ack messages ("🎙️ Te escuché") are skipped to avoid noise in AI context window. Best-effort logging — failures never break the HTTP response.
- **Files:** `src/farmafacil/api/routes.py`, `tests/test_chat_endpoint.py` (7 new tests), `tests/test_chat_voice_endpoint.py` (3 new tests)
- **Tests:** 10 new tests (inbound/outbound logging, image caption logging, empty-item skip, voice ack skip, log failure resilience, integration test with get_recent_history, no inbound on empty transcription)

---

## Phase 12 — Search Relevance Fix (v0.29.0)

### Item 100: Form-word floor exclusion + non-pharma category audit (Q9)
- **Priority:** P1 — Bug Fix
- **Problem:** Searching "crema queloides" (keloid scar cream) returned wet wipes ("Toallas Humedas Mimlot Crema"), toothpaste ("Crema Dental Colgate"), deodorants, and eye cream — 9 of 10 results were irrelevant. Root cause: "crema" is a FORM_WORD (dosage form descriptor) but it still satisfied the Signal 0 token-overlap floor gate. Any product with "crema" in its name passed the floor, and missing non-pharma categories ("CAMBIO PANAL", "CD ADULTO", etc.) gave these products a +0.3 pharma bonus, pushing scores to 0.55 — well above the 0.3 threshold. Only the legitimate product "PR88 CREM FORM QS QUELOIDES 60G" should have passed.
- **Status:** ✅ DONE (v0.29.0, 2026-05-22)
- **Solution:** Two-part fix: (1) Exclude FORM_WORDS from Signal 0 floor check — same pattern as Q8 digit-only exclusion. Form words like "crema", "gel", "jarabe" appear in thousands of unrelated products and carry no ingredient signal. (2) Added 11 missing non-pharma categories to NON_PHARMA_CATEGORIES: "cambio panal", "cd adulto", "desodorantes barra", "desodorantes locion/crema", "cuidado personal", "cuidado especial", "jabones barra", "accesorios bebe", "pescados", "quesillos", "en frio". Code review: fixed dead-code union with _NOISE_TOKENS in floor gate, improved test isolation for jarabe and pomada tests, added form-word+digit edge case test.
- **Files:** `src/farmafacil/services/relevance.py`, `tests/test_relevance.py` (26 new tests)
- **Tests:** 26 new tests — 13 for form-word floor exclusion (exact production bug products, legitimate matches unbroken, edge cases), 13 for new non-pharma categories (all new categories + regression guard for legitimate pharma)

### Item 101: Zero-price display — show "Precio no disponible" for Bs. 0.00
- **Priority:** P2 — UX Fix
- **Problem:** Products with Bs. 0.00 (bad/missing price data from the pharmacy API) displayed "Bs. 0.00" to users, which is misleading — it implies the product is free when in reality the API returned no price. The queloides cream (PR88 CREM FORM QS QUELOIDES 60G from Farmacia SAAS) had Bs. 0.00 for Caracas.
- **Status:** ✅ DONE (v0.29.1, 2026-05-22)
- **Solution:** Three-surface fix: (1) `formatter._format_price()` — returns "Precio no disponible — ver en <url>" (or plain "Precio no disponible" without URL) when `price_bs == 0`. (2) `formatter.format_search_results()` — store-level price guard changed from `store.price_bs` (truthy) to `store.price_bs != 0` (explicit). (3) `handler._build_product_caption()` — image caption shows `_Precio no disponible_` with URL on a separate line. Code review: changed `not result.price_bs` to explicit `result.price_bs == 0` for Decimal clarity; added negative-price edge case test; moved handler import to module level.
- **Files:** `src/farmafacil/bot/formatter.py`, `src/farmafacil/bot/handler.py`, `tests/test_zero_price_display.py` (new, 18 tests)
- **Tests:** 18 new tests — 8 for _format_price (zero/None/normal/discount/negative), 4 for format_search_results store-level, 6 for _build_product_caption image captions

### Item 102: Strip conversational prefixes from onboarding geocode queries
- **Priority:** P1 — Bug Fix
- **Problem:** Users typing "En la Lagunita" during onboarding got "No logré ubicar esa zona" because Nominatim cannot parse the Spanish conversational prefix "En". The same happens with "en la Boyera", "por El Hatillo", "vivo en Altamira", etc. "La Lagunita" (without prefix) resolves fine. Reported by Carolina during test onboarding.
- **Status:** ✅ DONE (v0.29.2, 2026-05-22)
- **Solution:** Added `_strip_location_prefix()` in location.py with regex matching common Spanish conversational prefixes (en, por, cerca de, vivo en, soy de, estoy en/por). When `resolve()` gets 0 Nominatim results, it retries with the stripped query. 3-char minimum guard prevents false positives on phrases like "por favor". Stripped form also cached to prevent duplicate Nominatim calls. Code review: fixed fragile mock discriminator, moved cleaned computation inside retry block, added dual-cache for stripped form.
- **Files:** `src/farmafacil/services/location.py`, `tests/test_location.py` (15 new tests)
- **Tests:** 15 new tests — 12 for prefix stripping (all supported prefixes, plain names, edge cases, case insensitive), 3 for resolve retry (retry succeeds, no-retry, both-fail)

### Item 103: Numbered location alternatives for ambiguous geocoding
- **Priority:** P2 — UX Enhancement
- **Problem:** When onboarding geocode returns a low-confidence or name-mismatched result, the bot showed a single "¿es correcto?" sí/no prompt. This was confusing when Nominatim had multiple possible matches — the user could only accept or reject the top hit. User requested a numbered list showing all candidates so they can pick the right one.
- **Status:** ✅ DONE (v0.29.3, 2026-05-26)
- **Solution:** Replaced the sí/no confirmation with a numbered list of candidates. When geocoding is ambiguous, the bot shows "*1.* La Boyera, Caracas", "*2.* La Boyera del Sur, Miranda", "*3.* Otra ubicación" etc. The user types a number to select, or types a zone name to re-geocode. The `_pending_location_confirm` stash changed from `dict[str, dict]` to `dict[str, list[dict]]`. New `_offer_location_alternatives()` function builds the numbered message. The `awaiting_location_confirm` step was fully rewritten for number selection. `resolve()` alternatives now include `city_code` and `zone_name`. Code review: fixed step-reset race (BLOCKER), moved inline imports to top-level (MAJOR), documented intentional stricter confidence gate, fixed test patch targets.
- **Files:** `src/farmafacil/bot/handler.py`, `src/farmafacil/services/location.py`, `tests/test_location_confirm_and_dedup.py`, `tests/test_handler.py`
- **Tests:** 23 tests in test_location_confirm_and_dedup.py (7 stash unit + 6 dedup + 10 handler integration), 1 fix in test_handler.py

### Item 104: Inline location change — extract location from message
- **Priority:** P2 — UX Enhancement
- **Problem:** Currently, changing your saved location requires a two-step flow: type "cambiar zona" → bot prompts → type location. Users expect to change location in a single message like "cambiar de localización a Baruta", "vivo en Caracas", or "estoy en Los Naranjos". The system should extract the location from the message, geocode it, and save permanently (or show numbered alternatives if ambiguous).
- **Status:** ✅ DONE (v0.29.4, 2026-05-26)
- **Solution:** (1) Added `location_change` to AI classifier ACTION list in CLASSIFY_INSTRUCTIONS with detailed disambiguation rules — the AI now distinguishes "vivo en Chacao" (permanent location change) from "busca losartan en Chacao" (temporary search location). (2) Extracted `_handle_location_change()` helper in handler.py that geocodes inline: high confidence saves permanently with MSG_LOCATION_UPDATED, low confidence shows numbered alternatives (v0.29.3 UX), not found sends error, no location falls back to two-step prompt. Called from both AI-only and hybrid modes. (3) Fixed BLOCKER in `awaiting_location_confirm`: onboarded users picking from alternatives now get "ubicación actualizada" instead of the onboarding MSG_READY. Added MSG_LOCATION_UPDATED constant.
- **Files:** `src/farmafacil/services/ai_responder.py`, `src/farmafacil/bot/handler.py`, `src/farmafacil/bot/messages.py`, `tests/test_inline_location_change.py` (new, 15 tests)
- **Tests:** 15 tests — 5 hybrid mode (high/low confidence, not found, no location, same zone), 4 AI-only mode (high/low confidence, no location, not found), 3 classifier prompt checks, 2 alternatives pick-a-number (onboarded vs onboarding), 1 helper existence

---

## Phase 17 — AI-Only Tool-Use Architecture

### Item 105: Replace AI-only classify+route with Anthropic tool_use
- **Priority:** P1 — Architecture Improvement
- **Problem:** AI-only mode uses a fragile text-based classification pattern: the AI emits `ACTION: drug_search` as structured text, a parser extracts it, then a long if/elif chain in handler.py routes the action. This is error-prone — the AI's text response can be misclassified by the parser, the keyword dictionary approach is too rigid, and the routing chain duplicates logic. The user explicitly requested: "AI Should handle the intentions (never a router function) — give AI all the tools instead of putting a handler or router in front of it."
- **Status:** ✅ DONE (2026-05-26)
- **Fix:** Replaced classify→route with Anthropic's native `tool_use` API. 8 tool schemas (search_drug, change_location, find_nearest_stores, view_similar, ask_clarification, report_emergency, show_help, general_reply) sent via `classify_with_tools()`. New `_dispatch_tool_use()` maps tool calls to existing handler helpers. ToolUseResult dataclass parallels AiResponse for debug footer compatibility. Module-level `_KNOWN_TOOLS` frozenset with warning log for unknown tools. Hybrid mode unchanged.
- **Files:** `src/farmafacil/services/ai_responder.py` (TOOL_DEFINITIONS, TOOL_USE_INSTRUCTIONS, ToolUseResult, classify_with_tools), `src/farmafacil/bot/handler.py` (_KNOWN_TOOLS, _dispatch_tool_use, ai_only block replaced), `src/farmafacil/bot/debug.py` (docstring), `tests/test_tool_use.py` (new, 29 tests), `tests/test_clarification.py`, `tests/test_inline_location_change.py`, `tests/test_nearest_store.py` (updated mocks)

---

## Phase 18 — AI Tool Coverage + Result Validation

### Item 106: Add change_name tool to AI-only mode
- **Priority:** P2 — Feature Gap
- **Problem:** In AI-only mode, users saying "me llamo Pedro" or "cambiar nombre" fell through to general_reply because there was no tool for name changes. Hybrid mode handled it via keyword cache ("cambiar nombre" → awaiting_name).
- **Status:** ✅ DONE (2026-05-26)
- **Fix:** Added `change_name` tool to TOOL_DEFINITIONS with optional `name` property. Dispatch in `_dispatch_tool_use()` uses `_is_valid_name()` for validation, calls `update_user_name()` if valid, prompts with `awaiting_name` step if empty or invalid.
- **Files:** `src/farmafacil/services/ai_responder.py`, `src/farmafacil/bot/handler.py`, `tests/test_tool_use.py`

### Item 107: Add lookup_store tool to AI-only mode
- **Priority:** P2 — Feature Gap
- **Problem:** In AI-only mode, "donde queda TEPUY" fell to general_reply. Hybrid mode had `_try_store_lookup()` in the question action branch. AI-only had no tool to query the pharmacy_locations DB.
- **Status:** ✅ DONE (2026-05-26)
- **Fix:** Added `lookup_store` tool with `store_name` (required) and `chain` (optional) properties. Dispatch calls existing `lookup_store()` + `format_store_info()`. Shows Google Maps link on match, friendly "no encontré" message on miss.
- **Files:** `src/farmafacil/services/ai_responder.py`, `src/farmafacil/bot/handler.py`, `tests/test_tool_use.py`

### Item 108: AI validates search results before sending to user
- **Priority:** P1 — Search Quality
- **Problem:** Pharmacy APIs (Farmatodo Algolia) return fuzzy-matched results that can be irrelevant. The heuristic `relevance.py` filter catches keyword-level mismatches but not semantic ones (e.g. "crema para queloides" returning "Crema Desodorante Dove" because both contain "crema"). Users receive wrong products.
- **Status:** ✅ DONE (2026-05-26)
- **Fix:** New `validate_search_results()` in ai_responder.py. After heuristic filter + best-price, sends product list to AI with a `filter_results` tool. AI returns indices of relevant products. Safety nets: (1) if AI removes ALL → return originals, (2) on API error → return originals, (3) skip when ≤1 result or best_price already filtered. Uses Haiku (~$0.0001/search). Token usage tracked separately.
- **Files:** `src/farmafacil/services/ai_responder.py` (validate_search_results, _VALIDATION_SYSTEM_PROMPT, _VALIDATION_TOOL), `src/farmafacil/bot/handler.py` (integration in _handle_drug_search), `tests/test_tool_use.py`

---

### Phase 19 — AI Interaction Completeness

### 109. Get cheapest from last search (re-run with best_price)
- **Priority:** P2 — UX Gap
- **Problem:** When the user searches "losartan" and then asks "¿cuál es el más barato?", there's no tool to handle this follow-up. The `best_price` flag only works for new searches. The AI needs a tool that re-runs the last search query with `best_price=true` to return only the single cheapest result.
- **Status:** ✅ DONE (2026-05-26)
- **Fix:** New `get_cheapest` tool (no params). AI reads `user.last_search_query`, calls `_handle_drug_search(best_price=True)`. If no previous search, tells user to search first. 11 tools total.
- **Files:** `src/farmafacil/services/ai_responder.py`, `src/farmafacil/bot/handler.py`, `tests/test_tool_use.py`

### 110. Closest pharmacy — single result
- **Priority:** P2 — UX Gap
- **Problem:** `find_nearest_stores` always returns 5 stores. When user asks "¿cuál es la farmacia más cercana?", they expect a single answer. The AI needs the ability to limit the result count.
- **Status:** ✅ DONE (2026-05-26)
- **Fix:** Added optional `limit` parameter (int, 1-5, default 5) to `find_nearest_stores` tool schema. Validated in dispatch (type check, min 1, clamped to 5). Passed as `max_stores` to `_handle_nearest_store()` → `get_all_nearby_stores()`. AI sets `limit=1` for singular requests.
- **Files:** `src/farmafacil/services/ai_responder.py`, `src/farmafacil/bot/handler.py`, `tests/test_tool_use.py`, `tests/test_temp_location.py`

### 111. Memory updates for all AI tool dispatch branches
- **Priority:** P2 — AI Quality
- **Problem:** Several `_dispatch_tool_use()` branches don't call `_update_memory_safe()`: `lookup_store`, `report_emergency`, `show_help`, `view_similar`, `change_location`. The AI forgets these interactions on the next turn.
- **Status:** ✅ DONE (2026-05-26)
- **Fix:** Added `_update_memory_safe()` calls to all 5 branches. `get_cheapest` success path delegates memory to `_handle_drug_search` (documented with comment).
- **Files:** `src/farmafacil/bot/handler.py`, `tests/test_tool_use.py`
- **Code review fix:** `validate_search_results` exception branch returned 3-tuple instead of 4-tuple — fixed to `(results, 0, 0, "")` in ai_responder.py.

### 112. Rewrite DB skills/rules for tool_use mode
- **Priority:** P1 — AI Quality
- **Problem:** 10 of 14 pharmacy_advisor skills and 1 rule (`product_scope`) contained hybrid-mode classification instructions ("clasifica como drug_search", "ACTION: emergency", "DRUG:", "CLARIFY_NEEDED") that conflicted with tool_use mode where the AI calls tools directly. The AI received contradictory routing instructions.
- **Status:** ✅ DONE (2026-05-26)
- **Fix:** Rewrote all 11 seed entries to reference tool names: `search_drug`, `general_reply`, `ask_clarification`, `report_emergency`, `find_nearest_stores`, `get_cheapest`. Added call-syntax examples (e.g. `search_drug(query=..., preamble=...)`). Updated 6 test assertions and added 3 new contract tests (product_scope uses search_drug, emergency_redirect uses report_emergency, TOOL_USE_INSTRUCTIONS references general_reply).
- **Files:** `src/farmafacil/db/seed.py`, `tests/test_drug_liability.py`, `tests/test_ai_role_scope.py`
- **Code review fix:** Caught missed `product_scope` rule still using hybrid-mode format.

---

### Phase 21 — Admin UI Polish

### 113. Admin dropdown fields for constrained-value settings
- **Priority:** P2 — Admin UX
- **Problem:** Admin dashboard text inputs for fields with known valid values (intent action, pharmacy chain, city code, task key, response mode, etc.) allowed free-text entry, risking typos and invalid data. Example: typing "farmatodo" instead of "Farmatodo" for pharmacy_chain would silently create bad data.
- **Status:** ✅ DONE (2026-05-26)
- **Fix:** Replaced text inputs with SelectField dropdowns for 4 ModelViews: IntentKeywordAdmin (action — 13 choices), PharmacyLocationAdmin (pharmacy_chain — 4 chains + city_code — 20 cities), ProductAdmin (pharmacy_chain — 4 chains), ScheduledTaskAdmin (task_key — dynamic from TASK_REGISTRY). For AppSettingAdmin (key-value table where valid values depend on the key), added server-side `on_model_change` validation that rejects invalid values for 6 constrained keys (response_mode, chat_debug, category_menu_enabled, post_feedback_suggestion, post_feedback_bug_report, default_model). Free-text settings (cache_ttl_minutes, etc.) remain unrestricted.
- **Files:** `src/farmafacil/api/admin.py`, `tests/test_admin_user_form.py`

---

### Phase 22 — Admin Chat Capacity

### 114. Remove admin AI call limit
- **Priority:** P1 — Admin Functionality
- **Problem:** `MAX_ADMIN_STEPS = 5` in `ai_responder.py` capped the admin AI to only 5 LLM roundtrips per turn. Complex admin tasks (reports, multi-tool analyses, bulk queries) consistently hit this cap and returned "Se alcanzó el límite de pasos del admin" before completing. Additionally, `max_tokens=1024` per response was too low for detailed admin reasoning.
- **Status:** ✅ DONE (2026-05-26)
- **Fix:** Raised `MAX_ADMIN_STEPS` from 5 → 25 (enough for any real admin task while still preventing infinite loops). Raised `max_tokens` from 1024 → 4096 for admin AI responses. Step-budget-exhausted message now includes the actual limit number.
- **Files:** `src/farmafacil/services/ai_responder.py`

---

### Phase 23 — Admin UI Friendly Names

### 115. FK columns show human-readable names instead of bare IDs
- **Priority:** P2 — Admin UX
- **Problem:** Nine SQLAdmin views displayed raw integer IDs for foreign key columns (user_id, role_id, product_id, pharmacy_id, conversation_log_id). Admins had to memorize or cross-reference IDs to understand relationships. Example: UserMemory list showed "user_id: 4" instead of "Jose (14258904657)".
- **Status:** ✅ DONE (2026-05-26)
- **Fix:** Added `_fk_formatter()` DRY helper in admin.py that renders FK columns as clickable `<a>` links showing the related object's `__repr__` (XSS-safe via `markupsafe.escape`, URL-safe via `int()` cast). Applied to 9 admin views: UserMemoryAdmin, AiRoleRuleAdmin, AiRoleSkillAdmin, SearchLogAdmin, UserFeedbackAdmin, UserSuggestionAdmin, ProductPriceAdmin, DrugListingAdmin, VoiceMessageAdmin. Added `lazy="selectin"` to 9 ORM relationships for async-safe eager loading in admin list views. Added `viewonly=True` relationships with `foreign()` annotation for SearchLog.user and DrugListing.pharmacy (no formal FK constraint). Added `__repr__` to Pharmacy, Product, DrugListing models. Updated `column_labels` to use human-friendly names ("User" not "User ID"). Updated skill files (farmafacil-new-feature.md, farmafacil-update.md, farmafacil-review.md) with the friendly-name rule for future implementations. 27 new tests (10 parametrized formatter checks, 9 label checks, 3 helper unit tests, 5 model repr tests).
- **Files:** `src/farmafacil/models/database.py`, `src/farmafacil/api/admin.py`, `tests/test_admin_user_form.py`, `.claude/commands/farmafacil-new-feature.md`, `.claude/commands/farmafacil-update.md`, `.claude/commands/farmafacil-review.md`

---

### Phase 24 — English Drug Name Translation

### 116. AI-powered English-to-Spanish drug name translation
- **Priority:** P2 — Search UX
- **Problem:** Users sometimes search for medicines using English names (e.g., "amlodipine", "acetaminophen", "ibuprofen tablets") because they know them from international sources or packaging. Farmatodo's Algolia index uses Spanish names, so these searches return zero results. The system should detect English drug names and translate them to Spanish transparently.
- **Status:** ✅ DONE (2026-05-26, v0.37.0)
- **Solution:** Dual-path AI-powered translation:
  1. **Zero-result fallback** — new `drug_translation.py` service calls Claude Haiku (temperature=0) when a search returns 0 results. If the query is an English drug name, it gets the Spanish INN equivalent and retries the search. User sees "🌐 Traduje *amlodipine* → *amlodipino*" notification.
  2. **Proactive AI instructions** — updated `TOOL_USE_INSTRUCTIONS`, `CLASSIFY_INSTRUCTIONS`, and `search_drug` tool schema to tell the AI to translate English drug names to Spanish before searching.
  - `TranslationResult` class returns token counts for accurate billing. Guards: length bounds (3-100 chars), empty content, trailing punctuation, API errors. 28 tests.
- **Files:** `services/drug_translation.py` (new), `bot/handler.py`, `services/ai_responder.py`, `tests/test_drug_translation.py` (new)

---

### Phase 25 — Processing Indicator

### 117. WhatsApp "processing" reaction indicator
- **Priority:** P2 — UX Responsiveness
- **Problem:** Users send a message and have no visual feedback that the bot received it and is working on a response. Similar to iMessage's typing dots, the bot should show it is processing.
- **Status:** ✅ DONE (2026-05-26, v0.38.0)
- **Solution:** Added `send_typing_indicator()` in `whatsapp.py` using WhatsApp Cloud API's native `typing_indicator` message type (three-dot bubble, added Q1 2025). In `webhook.py`, typing indicator is sent synchronously for all processed message types (text, location, interactive, image, document, audio) before the handler background task is dispatched. The dots auto-dismiss when the bot sends its response (or after 25s). No manual cleanup needed. Also added general-purpose `send_reaction()` / `remove_reaction()` utilities. Proxy mode no-op. 19 tests in `test_processing_indicator.py`.
- **Files:** `bot/whatsapp.py` (+`send_typing_indicator`, `send_reaction`, `remove_reaction`), `bot/webhook.py` (typing indicator dispatch), `tests/test_processing_indicator.py` (new)

---

## Summary

| Phase | Items | Priority | Total Effort | Status |
|-------|-------|----------|-------------|--------|
| 1 — Security Hotfix | 50-55 (6 items) | P0 | ~4 hours | ✅ DONE (v0.23.0) |
| 2 — Performance Unlock | 56-61 (6 items) | P1 | ~10 hours | ✅ DONE (v0.24.0) |
| 3 — UX Polish | 62-68 (7 items) | P1-P2 | ~3 hours | ✅ DONE (v0.25.0) |
| 4 — Test Hardening | 69-71 (3 items) | P1-P2 | ~5 hours | ✅ DONE (v0.25.0) |
| 5 — Structural Refactor | 72-76 (5 items) | P1-P2 | ~17 hours | ✅ DONE (v0.25.0) |
| 6 — Infrastructure | 77-85 (8+1 N/A items) | P2-P3 | ~10 hours | ✅ DONE (v0.25.0) |
| 7 — Backlog Cleanup | 86-93 (8 items) | P2-P3 | ~2 hours | ✅ DONE (v0.26.0) |
| 8 — Group Relay | 94 (1 item) | P1 | ~3 hours | ✅ DONE (v0.27.0) |
| 9 — Relay Bug Fixes | 95-97 (3 items) | P0-P2 | ~2 hours | ✅ DONE (v0.27.1) |
| 10 — Voice Relay | 98 (1 item) | P1 | ~2 hours | ✅ DONE (v0.28.0) |
| 11 — Relay Context Fix | 99 (1 item) | P1 | ~1 hour | ✅ DONE (v0.28.1) |
| 12 — Search Relevance Fix | 100 (1 item) | P1 | ~1 hour | ✅ DONE (v0.29.0) |
| 13 — Zero-Price Display | 101 (1 item) | P2 | ~1 hour | ✅ DONE (v0.29.1) |
| 14 — Geocode Prefix Fix | 102 (1 item) | P1 | ~1 hour | ✅ DONE (v0.29.2) |
| 15 — Location Alternatives | 103 (1 item) | P2 | ~1 hour | ✅ DONE (v0.29.3) |
| 16 — Inline Location Change | 104 (1 item) | P2 | ~1 hour | ✅ DONE (v0.29.4) |
| 17 — AI-Only Tool-Use | 105 (1 item) | P1 | ~3 hours | ✅ DONE (v0.30.0) |
| 18 — AI Tool Coverage + Validation | 106-108 (3 items) | P1-P2 | ~3 hours | ✅ DONE (v0.31.0) |
| 19 — AI Interaction Completeness | 109-111 (3 items) | P2 | ~2 hours | ✅ DONE (v0.32.0) |
| 20 — Skill Tool-Use Alignment | 112 (1 item) | P1 | ~1 hour | ✅ DONE (v0.33.0) |
| 21 — Admin UI Polish | 113 (1 item) | P2 | ~1 hour | ✅ DONE (v0.34.0) |
| 22 — Admin Chat Capacity | 114 (1 item) | P1 | ~30 min | ✅ DONE (v0.35.0) |
| 23 — Admin UI Friendly Names | 115 (1 item) | P2 | ~2 hours | ✅ DONE (v0.36.0) |
| 24 — English Drug Name Translation | 116 (1 item) | P2 | ~2 hours | ✅ DONE (v0.37.0) |
| 25 — Processing Indicator | 117 (1 item) | P2 | ~1 hour | ✅ DONE (v0.38.0) |
| **Total** | **68 items** | | **~80 hours** | |
