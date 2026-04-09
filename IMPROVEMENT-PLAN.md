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

- **Status:** PENDING
- **Added:** 2026-03-30
- **Problem:** Farmahorro (farmahorro.com.ve) is a major Venezuelan pharmacy chain with an active online product catalog and delivery service. They were reportedly absorbed by DRONENA/XANA but still operate independently with their own website. Adding Farmahorro expands coverage to the popular/mass-market segment.
- **Suggested solution:** Create `src/farmafacil/scrapers/farmahorro.py` subclassing `BaseScraper`. Investigate farmahorro.com.ve to identify the underlying tech stack and product search API. Extract product name, price, image URL, availability. They also partner with delivery platforms (PedidosYa, Coconecta) which may expose additional APIs.
- **Affected files:** `src/farmafacil/scrapers/farmahorro.py` (new), `src/farmafacil/services/search.py` (register scraper), `tests/test_farmahorro_scraper.py` (new)
- **Effort:** Medium (3–4 hours — need to reverse-engineer their catalog API first)
- **Notes:** Farmahorro has an active website with product catalog and delivery (Mon–Fri, 8AM–4PM). Tech stack unknown — needs investigation. Social media: @farmahorrove on Instagram/X.

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
