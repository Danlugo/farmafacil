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
- **Status:** PENDING
- **Priority:** P1
- **Effort:** Low (~15 min)
- **Problem:** `preference_change` keyword is seeded and recognized but no handler branch exists. User gets zero response. Feature deprecated since v0.15.2.
- **Fix:** Remove `preference_change` entries from `DEFAULT_INTENTS` in `seed.py`. Optionally add migration to deactivate existing rows in `intent_keywords`.
- **Files:** `src/farmafacil/db/seed.py`
- **Found by:** Product

### Item 63: Add Google Maps links to nearby-store results
- **Status:** PENDING
- **Priority:** P1
- **Effort:** Low (~15 min)
- **Problem:** `format_nearby_stores()` shows distance + address but no navigation link. `format_store_info()` already includes Maps links — inconsistency.
- **Fix:** Add `f"https://maps.google.com/?q={lat},{lng}"` per store in `format_nearby_stores()`. Data already available in store dict.
- **Files:** `src/farmafacil/bot/formatter.py`
- **Found by:** Product

### Item 64: Reply to unsupported message types
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Low (~15 min)
- **Problem:** Stickers, contacts, and other unsupported types get silent non-response. Users think bot is broken.
- **Fix:** Add fallback reply "No puedo procesar ese tipo de mensaje. Enviame texto, foto, documento, nota de voz o ubicacion." Exclude `reaction` and `status` types.
- **Files:** `src/farmafacil/bot/webhook.py`
- **Found by:** Product

### Item 65: Guard feedback prompt after empty nearby-store results
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Low (~10 min)
- **Problem:** "Te sirvio? (si/no)" appears even when 0 stores found — makes no sense.
- **Fix:** Add `if stores:` before `awaiting_feedback` + feedback prompt in `_handle_nearest_store()`.
- **Files:** `src/farmafacil/bot/handler.py`
- **Found by:** Product

### Item 66: Add WhatsApp message length guard
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Med (~1 hour)
- **Problem:** WhatsApp has 4096-char limit. Long search results with 8 products across 3 chains can exceed it, causing silent truncation.
- **Fix:** Count chars during formatting. Stop adding products at ~3800 chars, append "... y N productos mas."
- **Files:** `src/farmafacil/bot/formatter.py`
- **Found by:** Product

### Item 67: Fix Spanish register consistency (vos/tu mixing) and missing accents
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Low (~30 min)
- **Problem:** Some messages use voseo ("Elegi", "Podes", "Volves"), most use tuteo. Missing accents on "ubicacion", "Que estas buscando", etc.
- **Fix:** Standardize all MSG_ constants to tuteo. Audit and add proper diacritics.
- **Files:** `src/farmafacil/bot/handler.py` (MSG_ constants)
- **Found by:** Product

### Item 68: Expand HELP_MESSAGE with discoverable features
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Low (~15 min)
- **Problem:** HELP_MESSAGE omits voice notes, image/prescription photos, location pin sharing, "ver similares", "farmacias cercanas", `/stats`.
- **Fix:** Add feature discovery lines for each.
- **Files:** `src/farmafacil/services/intent.py`
- **Found by:** Product

---

## Phase 4 — Test Hardening (P1/P2)

### Item 69: Add tests for untested security-boundary services
- **Status:** PENDING
- **Priority:** P1
- **Effort:** Low (~2 hours)
- **Problem:** `file_manager.py` (path traversal guard), `web_search.py` (Brave API), `media.py` (size limits, Vision encoding) have zero test coverage. `file_manager.py` is a security boundary.
- **Fix:** Create `test_file_manager.py` (traversal, scope boundaries), `test_web_search.py` (mocked HTTP), `test_media.py` (oversized data, unsupported MIME).
- **Files:** `tests/test_file_manager.py` (new), `tests/test_web_search.py` (new), `tests/test_media.py` (new)
- **Found by:** Test Engineer

### Item 70: Fix 7 known flaky tests
- **Status:** PENDING
- **Priority:** P1
- **Effort:** Low (~2 hours)
- **Problem:** Root causes identified: `test_chat_debug` ×2 — no row cleanup for phone `5558812222`; `test_location_sharing` ×3 — unmocked Nominatim HTTP calls; `test_user_memory` ×1 — fixture `return` instead of `yield` (no teardown); `test_handler` ×1 — phone collision from incomplete cleanup.
- **Fix:** Add autouse cleanup fixtures; mock Nominatim in 3 tests; change fixture to `yield` + teardown.
- **Files:** `tests/test_chat_debug.py`, `tests/test_location_sharing.py`, `tests/test_user_memory.py`, `tests/test_handler.py`
- **Found by:** Test Engineer

### Item 71: Add HTTP-level tests for audio endpoint
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Med (~1 hour)
- **Problem:** `GET /api/v1/audio/{id}` is security-sensitive (serves user audio PII) but only checked structurally. No HTTP test verifies 401 for unauthenticated, 404 for missing, 403 for path traversal.
- **Fix:** Create tests with `AsyncClient`: no creds → 401, wrong creds → 401, missing record → 404, path escape → 403.
- **Files:** `tests/test_voice.py` or new `tests/test_audio_endpoint.py`
- **Found by:** Test Engineer

---

## Phase 5 — Structural Refactor (P1-P2, future)

### Item 72: Decompose handler.py into focused modules
- **Status:** PENDING
- **Priority:** P1
- **Effort:** High (~8 hours)
- **Problem:** 2,524-line monolith mixes 7 responsibilities. Single largest maintenance risk in the codebase.
- **Fix:** Extract: `bot/onboarding.py`, `bot/admin.py`, `bot/media_handler.py`, `bot/feedback.py`, `bot/search_handler.py`. Keep handler.py as ~150-line dispatcher. Pure structural refactor — existing 1,196 tests remain valid.
- **Files:** `src/farmafacil/bot/handler.py` → 5 new modules
- **Found by:** Code Quality, Architecture

### Item 73: Split admin_chat.py into domain modules
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Med (~3 hours)
- **Problem:** 1,785 lines, 40+ tool functions in a flat file.
- **Fix:** Split into `admin_chat/user_tools.py`, `admin_chat/feedback_tools.py`, `admin_chat/ai_tools.py`, `admin_chat/registry.py`.
- **Files:** `src/farmafacil/services/admin_chat.py` → package
- **Found by:** Architecture, Code Quality

### Item 74: Bulk product upsert
- **Status:** PENDING
- **Priority:** P2
- **Effort:** High (~4 hours)
- **Problem:** Row-by-row upsert: ~40 ORM operations per 10-result cache-miss search.
- **Fix:** Use `INSERT ... ON CONFLICT DO UPDATE`. Batch keyword sync. Collapse to ~4 statements.
- **Files:** `src/farmafacil/services/product_cache.py`
- **Found by:** Performance

### Item 75: Consolidate geocoding modules
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Med (~2 hours)
- **Problem:** `geocode.py` (legacy) and `location.py` (v0.19.0 authoritative) overlap. Three callers still use the old module.
- **Fix:** Migrate all callers to `location.py` equivalents. Deprecate and remove `geocode.py`.
- **Files:** `src/farmafacil/services/geocode.py`, `src/farmafacil/services/location.py`, callers
- **Found by:** Code Quality

### Item 76: Remove dead `image_grid.py` module
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Low (~15 min)
- **Problem:** Zero production imports since v0.15.2. Dead code with tests for a removed feature.
- **Fix:** Delete `services/image_grid.py` and `tests/test_image_grid.py`.
- **Files:** `src/farmafacil/services/image_grid.py`, `tests/test_image_grid.py`
- **Found by:** Code Quality

---

## Phase 6 — Infrastructure & Observability (P2-P3, future)

### Item 77: Add CSRF protection to admin dashboard
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Med (~2 hours)
- **Problem:** SQLAdmin cookie-based sessions with no CSRF token on form submissions.
- **Fix:** Add `SameSite=Strict` on session cookie. Consider `starlette_csrf` middleware.
- **Files:** `src/farmafacil/api/admin.py`, `src/farmafacil/api/app.py`
- **Found by:** Security

### Item 78: Create module-level httpx clients with connection pooling
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Med (~2 hours)
- **Problem:** New `httpx.AsyncClient()` created on every API call — no connection pooling, repeated TLS handshakes.
- **Fix:** Module-level clients initialized in lifespan, closed in shutdown.
- **Files:** `src/farmafacil/scrapers/farmatodo.py`, `src/farmafacil/scrapers/vtex.py`, `src/farmafacil/bot/whatsapp.py`
- **Found by:** Architecture

### Item 79: Add scheduler task timeout
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Med (~1 hour)
- **Problem:** Tasks run without timeout. OSM backfill can block scheduler loop for 27+ minutes.
- **Fix:** Wrap `_execute_task()` with `asyncio.wait_for(..., timeout=...)`.
- **Files:** `src/farmafacil/services/scheduler.py`
- **Found by:** Architecture, SRE

### Item 80: Fix rate limiting behind ngrok (X-Forwarded-For)
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Med (~1 hour)
- **Problem:** `get_remote_address` reads ngrok's internal IP — all traffic shares one rate-limit bucket.
- **Fix:** Custom `key_func` that reads `X-Forwarded-For` header when behind trusted proxy.
- **Files:** `src/farmafacil/api/limiter.py`
- **Found by:** Security, SRE

### Item 81: Add prompt injection delimiter defense
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Low (~15 min)
- **Problem:** User messages passed to Claude without delimiters. Crafted messages could attempt to override structured output format.
- **Fix:** Wrap user input in `<user_message>` XML tags in the messages list.
- **Files:** `src/farmafacil/services/ai_responder.py`
- **Found by:** Security

### Item 82: Enhance health check with DB connectivity
- **Status:** PENDING
- **Priority:** P3
- **Effort:** Low (~15 min)
- **Problem:** `/health` returns `{"status":"ok"}` without verifying DB. Docker marks container healthy even when Postgres is down.
- **Fix:** Add `SELECT 1` via engine. Return 503 on failure.
- **Files:** `src/farmafacil/api/routes.py`
- **Found by:** SRE

### Item 83: Add composite index on conversation_logs
- **Status:** PENDING
- **Priority:** P3
- **Effort:** Low (~15 min)
- **Problem:** No index on `(phone_number, created_at)` — the most common query pattern. Full table scan as logs grow.
- **Fix:** Add `Index("idx_convlog_phone_created", "phone_number", "created_at")` to model.
- **Files:** `src/farmafacil/models/database.py`
- **Found by:** SRE, Architecture

### Item 84: Update architecture docs
- **Status:** PENDING
- **Priority:** P3
- **Effort:** Med (~2 hours)
- **Problem:** `docs/architecture.md` last updated 2026-03-31. Missing voice messages, scheduler, admin chat, AI role system, 6+ tables, relevance filter details.
- **Fix:** Update component diagram, add missing tables, document scheduler, voice flow, admin chat, configuration layers.
- **Files:** `docs/architecture.md`
- **Found by:** Architecture

### Item 85: Clean up temp image files after send
- **Status:** PENDING
- **Priority:** P2
- **Effort:** Low (~15 min)
- **Problem:** `generate_product_grid` creates temp files with `delete=False`. No cleanup after `send_local_image`. Slowly fills `/tmp`.
- **Fix:** Add `os.unlink(tmp_path)` in try/finally after send.
- **Files:** `src/farmafacil/bot/handler.py` (caller site)
- **Found by:** SRE

---

## Summary

| Phase | Items | Priority | Total Effort | Status |
|-------|-------|----------|-------------|--------|
| 1 — Security Hotfix | 50-55 (6 items) | P0 | ~4 hours | PENDING |
| 2 — Performance Unlock | 56-61 (6 items) | P1 | ~10 hours | PENDING |
| 3 — UX Polish | 62-68 (7 items) | P1-P2 | ~3 hours | PENDING |
| 4 — Test Hardening | 69-71 (3 items) | P1-P2 | ~5 hours | PENDING |
| 5 — Structural Refactor | 72-76 (5 items) | P1-P2 | ~17 hours | PENDING |
| 6 — Infrastructure | 77-85 (9 items) | P2-P3 | ~10 hours | PENDING |
| **Total** | **36 items** | | **~49 hours** | |
