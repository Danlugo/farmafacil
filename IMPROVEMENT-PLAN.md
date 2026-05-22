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
| **Total** | **51 items** | | **~60 hours** | |
