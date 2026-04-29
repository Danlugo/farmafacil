# FarmaFacil — WhatsApp Bot Conversation Flow

> Last Updated: 2026-04-08

## Overview

Every incoming message follows this entry path:

```
POST /webhook
  → log_inbound()
  → handle_incoming_message(sender, text)
      → get_or_create_user(sender)
      → validate_user_profile(user)
      → send_read_receipt(sender, wa_message_id)  ← fire-and-forget
      → route by onboarding_step or intent
```

**Read receipt:** A read receipt (`status: "read"`) is sent as fire-and-forget via `asyncio.create_task()` immediately after user validation. This marks the message with blue check marks and triggers the typing indicator bubble. Uses WhatsApp Cloud API v22.0 messages endpoint. Non-blocking — failures are silently logged.

---

## Onboarding Flow

New users go through a 4-step wizard. Each step is stored in `users.onboarding_step`.

### Step 1: Welcome (`step = "welcome"`)

Triggered on the very first message from any phone number.

**Bot sends:**
> Hola! Soy FarmaFacil
> Te ayudo a encontrar productos en farmacias de Venezuela.
> Como te llamas?

**Side effect:** Step advances to `awaiting_name`.

---

### Step 2: Name Collection (`step = "awaiting_name"`)

**Always uses LLM** to distinguish real names from greetings.

| User sends | Bot behavior |
|-----------|-------------|
| "hola" / greeting without a name | Re-asks for name |
| "Maria" | Saves name, advances to awaiting_location |
| "Soy Jose de Chacao" | Saves name=Jose, geocodes Chacao, skips to awaiting_preference |
| "losartan" | Rejects as non-name, re-asks |

Name validation (`_is_valid_name`):
- Must be >= 2 characters
- Must not be a common non-name word (hola, si, no, ayuda, losartan, etc.)
- Must not be all digits
- Must be <= 4 words (rejects sentences)

---

### Step 3: Location Collection (`step = "awaiting_location"`)

**Bot sends:**
> Mucho gusto Maria! En que zona o barrio estas?
> Ejemplo: La Boyera, Chacao, Maracaibo

The bot accepts location in **two forms**:

1. **Typed zone name** — calls `geocode_zone()` against OpenStreetMap Nominatim (`/search`). On success, the city code is derived and stored.
2. **WhatsApp location pin** (Item 24, v0.13.0) — the user taps the paperclip → *Location* → *Send your current location*. The webhook dispatches the `location` message type to `handle_location_message()`, which calls `reverse_geocode()` against Nominatim's `/reverse` endpoint at zoom=14 (neighbourhood level). Falls back through `suburb → neighbourhood → village → town → city → county → state` for the zone name, and validates `country_code == "ve"` to reject coordinates outside Venezuela.

| User sends | Bot behavior |
|-----------|-------------|
| "La Boyera" | Forward geocode → saves lat/lng/zone_name/city_code, advances |
| "Chacao" | Forward geocode → saves, advances |
| "xyz123" | Forward geocode fails → re-asks with examples |
| 📍 location pin (Venezuela) | Reverse geocode → saves lat/lng/zone_name/city_code, advances |
| 📍 location pin (outside VE) | Reverse geocode rejects → "no pude ubicar tu zona" + re-asks |
| 📍 malformed coordinates | Webhook sends "no pude leer las coordenadas" + re-asks |

On success, step advances to `awaiting_preference`.

**Location pin also works post-onboarding.** A fully-onboarded user who shares their location pin triggers a "cambiar zona" — the new coordinates replace the old ones and the bot replies "✅ Zona actualizada a *X*" without touching `display_preference`. `handle_location_message` snapshots the prior `onboarding_step` before calling `update_user_location` (which unconditionally sets it to `awaiting_preference`) to decide between "still onboarding → ask for preference" and "already onboarded → acknowledge zone update".

---

### Step 4: Display Preference (`step = "awaiting_preference"`)

**Bot sends:**
> Como prefieres ver los resultados?
> 1. Imagen grande — un producto a la vez con detalles
> 2. Galeria — varios productos en una imagen
> Responde 1 o 2

| User sends | Mapped to |
|-----------|-----------|
| "1", "imagen grande", "imagen", "detalle" | `detail` |
| "2", "galeria", "grid", "grilla", "varios" | `grid` |
| anything else | Re-asks |

On success, `onboarding_step` is set to `null` (complete).

---

## Profile Validation and Auto-Repair

`validate_user_profile()` is called on **every message** after user load. It catches inconsistent profile states (e.g., a bug reset onboarding_step to null but location was never saved).

| Current state | Missing data | Auto-repair to |
|--------------|-------------|---------------|
| step = null | no name | awaiting_name |
| step = null | no location | awaiting_location |
| step = null | no display_preference | awaiting_preference |
| step = awaiting_preference | no name | awaiting_name |
| step = awaiting_preference | no location | awaiting_location |
| step = awaiting_location | no name | awaiting_name |

When a repair happens, a WARNING is logged with the old and new states.

---

## Post-Onboarding Flow

Once onboarding is complete (`step = null`), incoming messages follow this pipeline:

### 1. Keyword Cache Check

The DB `intent_keywords` table is checked first (in-memory cache, 5-minute TTL). Exact lowercase match against:

| Keyword action | Bot response |
|---------------|-------------|
| `location_change` | Asks for new zone, sets step to `awaiting_location` |
| `preference_change` | Asks for preference, sets step to `awaiting_preference` |
| `name_change` | Asks for name, sets step to `awaiting_name` |
| `farewell` | Sends canned response text |

Default keywords loaded at startup include: "cambiar zona", "cambiar preferencia", "cambiar nombre", "ayuda", "hola", etc.

### 2. Intent Classification

If no keyword match, `classify_intent()` runs:

1. **Keyword heuristic:** Short messages (1-8 words) with no question markers → `drug_search`
2. **LLM fallback:** Longer or ambiguous messages → Claude Haiku

LLM can also extract profile data mid-conversation:
- If LLM detects a new name → auto-updates `users.name`
- If LLM detects a new location → geocodes and auto-updates coordinates

### 3. Intent Routing

| Intent action | Bot behavior |
|--------------|-------------|
| `greeting` | If `app_settings.category_menu_enabled == "true"` (default), sends a WhatsApp interactive list with 5 category quick-replies (see [Category Menu Flow](#category-menu-flow)); otherwise sends the legacy welcome-back text with zone + preference. |
| `help` | Sends full help menu with command list |
| `drug_search` | Runs drug search, sends results text + image |
| `clarify_needed` | Sends a clarifying question and stashes the original query (see [Clarification Flow](#clarification-flow-for-vague-categories)) |
| `question` | Tries store lookup; if not a store, sends LLM-generated answer |
| `unknown` | Prompts user to send a drug name |

---

## Drug Recommendation Policy (v0.14.2, Item 37)

**Liability guardrails** — enforced via AI role rules (highest priority) and system prompt:

| Scenario | Bot behavior |
|----------|-------------|
| User describes symptoms, no product name | Empathize → explain cannot recommend drugs → offer to search what doctor prescribed → optionally suggest non-drug products |
| User names a specific product (with or without symptoms) | Search that product, no medical judgment on appropriateness |
| User asks "what should I take for X" | Decline → route to doctor/pharmacist |
| User volunteers a medication they take | Share general interaction/side-effect info (public knowledge) → always end with "consulta con tu médico" |
| Non-drug products (skincare, vitamins, baby, hygiene, household) | Can freely recommend and search |

**Enforced by:** `no_drug_recommendations` rule (sort_order 1, highest priority), rewritten `no_diagnosis` rule, `symptom_acknowledgment` skill, `drug_interaction_info` skill, system prompt liability warning.

**Admin override:** If `ai_roles.locked_by_admin = True`, the startup seed sync will NOT overwrite the role's prompt/rules/skills — allows manual policy edits via SQLAdmin without them being reverted on the next deploy.

---

## Drug Search Flow

When intent is `drug_search`:

1. Check user has location (if not → prompt, set step to `awaiting_location`)
2. **Symptom acknowledgment (v0.14.2 policy):** If the AI included a conversational response, send it as a text message BEFORE the search results. **The bot NEVER recommends specific drugs for symptoms** — when a user describes symptoms without naming a product, the AI responds with empathy, explains it cannot recommend medications (liability), and offers to search for whatever their doctor has prescribed. The bot CAN recommend non-drug products (skincare, vitamins, baby, hygiene). If the user volunteers a medication they already take, the bot can share general interaction/side-effect info but always routes to "consulta con tu médico".
3. **Drug interaction check:** If the user has known medications in their memory (`user_memories`), extract them via `extract_medications_from_memory()`, then query the RxNorm/RxNav API via `check_interactions()`. If interactions are detected, send a ⚠️ warning message before search results.
4. Call `search_drug(query, city_code, lat, lng, zone_name)`
3. Format results as text via `format_search_results()`
4. Send text message
5. If results exist, send image based on preference:
   - `detail`: Send individual product images (top 3) with rich captions
   - `grid`: Generate a stacked product image (up to 8 products) via Pillow at 1080×N px, save as JPEG (quality ladder starting at q=92 subsampling=0, falls back to progressively smaller tiers if the file exceeds the 4.5 MB WhatsApp safety budget), send, unlink temp file in a `try/finally` block. Farmatodo product photos (`lh*.googleusercontent.com`) are URL-upgraded to `=s1200` before download so the grid renders from a high-resolution source instead of the 512 px default thumbnail. VTEX URLs (SAAS / Locatel) are left unchanged because they already serve the full original on plain `/arquivos/ids/{id}/...` paths. Mild `UnsharpMask(radius=1, percent=50, threshold=2)` applied after LANCZOS resize to compensate for WhatsApp's JPEG recompression.

Product image captions include:
- Discount badge (if applicable)
- Brand and drug name
- Price in Bolivares (with strikethrough original price if discounted)
- Per-unit price
- Prescription requirement
- Number of stores in stock
- Nearest store name and distance

### Scraper Failure vs No Results

`search_drug()` tracks which scrapers raise exceptions during concurrent execution (`asyncio.gather`) and populates `SearchResponse.failed_pharmacies`. The formatter then differentiates three zero-result scenarios:

| State | Message |
|-------|---------|
| All queried scrapers failed | `⚠️ No pudimos conectar con {names} ahora mismo. Intenta de nuevo en unos minutos.` |
| Partial failure (some empty, some errored) | `No encontramos *{query}*. ⚠️ Ademas, no pudimos conectar con {names}. Intenta de nuevo en unos minutos.` |
| All succeeded, zero results | `No encontramos resultados para *{query}*. Intenta con otro nombre o revisa la ortografia.` |

When results DO exist but some scrapers failed, the header shows `⚠️ No pudimos conectar con {names} — resultados parciales.` so users know the view is partial.

`(cache)` and `(catalogo)` suffixes on `searched_pharmacies` are observability labels added by the cache/catalog paths — they are stripped when deciding whether "all queried" scrapers failed, so cache hits never trigger a connection-error message.

---

## Clarification Flow (for vague categories)

When a user asks for a CATEGORY that comes in multiple form factors (e.g., "medicinas para la memoria", "algo para dormir", "vitaminas") instead of naming a specific product, the AI classifier returns `action: clarify_needed` with a `CLARIFY_QUESTION` and `CLARIFY_CONTEXT`. The handler then:

1. Stashes the original vague query in `users.awaiting_clarification_context` (VARCHAR 300)
2. Sends the clarifying question (e.g., "¿Pastillas o bebibles? ¿Adulto o niño?")
3. Returns without running a search

On the **next** incoming message from that user, the handler detects the stashed context (before running intent classification) and:

1. Merges the original context with the new reply: `"medicinas para la memoria" + "pastillas adulto"` → `"medicinas para la memoria pastillas adulto"`
2. Clears `awaiting_clarification_context` atomically
3. Dispatches directly to `_handle_drug_search()`
4. Updates user memory with the chosen preference so the question isn't repeated

### Escape hatches

| User sends | Behavior |
|-----------|----------|
| `cancelar`, `cancela`, `olvidalo`, `nada`, `no` | Clears the context and sends a cancellation confirmation — no search |
| `/bug ...` or `/comentario ...` | The feedback command runs normally and the stashed context is intentionally preserved (bug report doesn't cancel the clarification) |

**Rules for when to clarify (see classification prompt):**

- ✅ Use `clarify_needed` for generic categories: "medicinas para la memoria", "algo para dormir", "vitaminas", "suplementos", "algo para el cabello", "condones", "anticonceptivos", "lentes de contacto", "kit dental", "productos de higiene íntima"
- ❌ Never use `clarify_needed` when the user names a specific product, brand, or ingredient: "omeprazol", "protector solar", "aspirina", "Trojan ultradelgado"
- ❌ Never use `clarify_needed` in mid-onboarding (the check is gated on `step is None`)
- 💡 **Why this matters (v0.17.2):** clarifying first means no pharmacy API calls (Farmatodo Algolia + 2 VTEX) before we know what to search. Personal-care categories (condones, anticonceptivos, lentes, kit dental, higiene íntima) were added in Item 44 after Jose's "necesito condones" test produced 37 generic results then crashed the turn on a Farmatodo `/stores/nearby` 409.

If the LLM returns `ACTION: clarify_needed` without a `CLARIFY_QUESTION`, the parser defensively degrades to `drug_search` so the user is never left hanging.

---

## Category Menu Flow

When a fully-onboarded user sends a bare greeting in hybrid mode (intent = `greeting`, onboarding_step = None), the bot shows a WhatsApp interactive list with 5 category quick-replies instead of the legacy welcome-back text. Added in v0.13.2 (Item 29).

### Categories

| Reply ID | Display title |
|---|---|
| `cat_medicamentos` | Medicamentos |
| `cat_cuidado_personal` | Cuidado Personal |
| `cat_belleza` | Belleza |
| `cat_alimentos` | Alimentos |
| `cat_hogar` | Articulos Hogar |

Defined in `src/farmafacil/bot/handler.py::CATEGORIES`. Adding or removing a row is a single-line edit. WhatsApp caps interactive lists at 10 rows per section — stay under the limit.

### Flow

1. User sends "hola" (or similar bare greeting). `classify_intent` returns `action=greeting`.
2. Handler reads `app_settings.category_menu_enabled`. If the value is the literal string `"true"`, it calls `_send_category_list(sender, display_name)` which dispatches a WhatsApp `type=interactive` list payload. Any other value falls back to the legacy `MSG_RETURNING` text.
3. User taps a row in the WhatsApp list UI. The webhook receives `msg_type=interactive` with `interactive.list_reply.id` and `.title`. `webhook.py` routes to `handle_list_reply(sender, reply_id)`.
4. `handle_list_reply` validates the reply id against `_CATEGORY_BY_ID`, stashes the category in `users.awaiting_category_search` (VARCHAR 50), and sends the canned prompt `"🛍 {category} - ¿Qué producto buscas? ..."`. Unknown reply ids are logged and dropped silently.
5. On the **next** free-text message from that user, the `awaiting_category_search` branch at the top of `handle_incoming_message` fires (runs AFTER `/bug` + clarification escape hatches, BEFORE onboarding so the guard is `step is None`):
   - Clears the stash atomically BEFORE dispatch (fail-safe pattern from Item 31)
   - Calls `_handle_drug_search(sender, user, text, ...)` with the raw user text (the category is **not** merged into the query — it's UX scaffolding, not a search filter)
   - Updates user memory with `[{category}] {text}` for future context

### Kill switch

New `app_setting` `category_menu_enabled` (default `"true"`). Flip to `"false"` via the admin UI or a direct DB update to fall back to the legacy `MSG_RETURNING` text without redeploying. A misconfigured value (empty string, garbage) also falls back to the legacy path — never catastrophic.

### Escape hatches

| User sends | Behavior |
|---|---|
| `cancelar`, `olvidalo`, `nada`, `no`, ... | Clears `awaiting_category_search` and sends a cancellation message — no search |
| `/bug ...` or `/comentario ...` | Feedback command runs normally; the stash is intentionally preserved so the user can resume after reporting |
| Another list tap mid-flow | The second pick overwrites the first — last pick wins |

### What the category does NOT do

Category selection is **not** a scraper-side filter in v0.13.2. The drug search runs against all pharmacies unchanged. Adding actual category filtering (Farmatodo Algolia `facetFilters`, VTEX `categoryId`) is deferred to a future item — see IMPROVEMENT-PLAN.md.

---

## Store Lookup

When a question comes in, the bot first tries to identify a pharmacy store name:

Patterns detected:
- "donde queda TEPUY" → extracts "TEPUY"
- "donde esta farmacia Bello Monte" → extracts "Bello Monte"
- "TEPUY" (1-2 words, short) → tries as store name directly

Lookup is case-insensitive against `pharmacy_locations.name_lower`. If found, returns address + Google Maps link.

---

## Change Commands

Any registered user can update their profile at any time:

| User types | Effect |
|-----------|--------|
| "cambiar zona" | Enters awaiting_location step |
| "cambiar preferencia" | Enters awaiting_preference step |
| "cambiar nombre" | Enters awaiting_name step |

These are managed via the `intent_keywords` table and can be extended via `POST /api/v1/intents`.

---

## Search Feedback

After every drug search, the bot asks "¿Te sirvió? (sí/no)". The flow uses `onboarding_step` to track state:

| Step | User sends | Bot behavior |
|------|-----------|-------------|
| `awaiting_feedback` | "sí", "si", "yes", "👍", "1" | Records positive feedback, thanks user |
| `awaiting_feedback` | "no", "nop", "nope", "👎", "0" | Records negative feedback, asks follow-up |
| `awaiting_feedback` | anything else (incl. "gracias", "ok", "bien") | Clears step, processes as normal message |
| `awaiting_feedback_detail` | any text | Records detail, thanks user |

The positive/negative match sets are intentionally tight — ambiguous words like `gracias`, `ok`, `bien`, `perfecto` are common farewells and must NOT auto-record feedback (regression from Item 28: user Jose Lugo got the "thanks for feedback" message immediately after typing "gracias").

Feedback is stored in `search_logs.feedback` (yes/no) and `search_logs.feedback_detail` (free text).

---

## User Feedback Commands (`/bug`, `/comentario`)

Users can submit bug reports or comments at any time via slash commands. The command is intercepted early in `handle_incoming_message()` — before onboarding state handling — so it also functions as an **escape hatch** from stuck states.

| User sends | Bot behavior |
|-----------|-------------|
| `/bug <texto>` | Creates `user_feedback` row (type=bug), replies with case ID |
| `/comentario <texto>` | Creates row (type=comentario), replies with case ID |
| `/commentario <texto>` | Typo alias, normalized to `comentario` |
| `/bug` (bare) | Asks user to include the report text after the command |

**Confirmation format:** `✅ ¡Gracias! Tu reporte ha sido registrado. 📋 Caso #{id}. Nuestro equipo lo revisará pronto.`

**Escape hatch behavior:** If the user is in `awaiting_feedback` or `awaiting_feedback_detail` state, the step is cleared BEFORE the `create_feedback()` call. Even if the DB write fails, the user is freed from the stuck state and sees an error message they can act on.

Submissions are reviewed via the SQLAdmin dashboard at `/admin/user-feedback/` — reviewers can only edit `reviewed`, `reviewer_notes`, and `reviewed_at`. Each row links back to the latest inbound `conversation_logs` entry for context.

---

## Response Mode

The bot supports two response modes, controlled globally via `app_settings.response_mode` and overridable per-user via `users.response_mode`:

| Mode | Behavior |
|------|----------|
| `hybrid` (default) | Keywords + preset answers first, LLM for complex questions |
| `ai_only` | Everything goes through AI classifier — no keyword routing |

Resolution: user override → global setting → fallback to `hybrid`.

---

## Chat Debug Mode

When enabled, the bot appends a debug footer to every AI-powered response showing:

```
---
🔧 DEBUG
ai model: claude-haiku-4-5-20251001
ai role: pharmacy_advisor
tokens: 142 in / 87 out
total questions: 23
total success: 8
```

Controlled via `app_settings.chat_debug` (global) and `users.chat_debug` (per-user override).

| Setting | Values |
|---------|--------|
| Global | `enabled` / `disabled` (default: disabled) |
| Per-user | `enabled` / `disabled` / NULL (NULL = use global) |

Resolution: user override → global setting → fallback to `disabled`.

---

## Admin Chat Mode (v0.14.0, Item 35)

A secondary chat interface for users who also operate the app. Unlike the pharmacy advisor, the admin AI is hardcoded to Claude Opus and has tool-call access to the application's database and a sandboxed slice of its source tree.

### Security invariant

`users.chat_admin` is editable ONLY from the SQLAdmin dashboard. No chat command, tool call, or AI prompt can flip this flag — the `set_user_setting` tool's whitelist explicitly excludes it. If an admin loses dashboard access, chat admin is gone.

### Activation flow

```
User sends: /admin
  ↓
handler.py::_handle_admin_toggle
  ↓
is_chat_admin(sender)?
  ├─ False → MSG_ADMIN_DENIED ("no tienes permiso...")
  └─ True  → set_admin_mode(sender, True) + send MSG_ADMIN_WELCOME
             (commands list + sample prompts)
```

From that moment, every free-text message routes through `_handle_admin_turn` → `run_admin_turn` (Opus tool loop) → `execute_tool` dispatch.

### Slash commands

| Command | Handler | Effect |
|---------|---------|--------|
| `/admin` | `_handle_admin_toggle` | Toggle admin mode (gated by `chat_admin`) |
| `/admin off`, `turn off admin`, `apagar admin`, `admin off` | `_handle_admin_off` | Leave admin mode |
| `/models` | `_handle_model_commands` | Show current default model + aliases |
| `/model <alias>` | `_handle_model_commands` | Set `default_model` app_setting (haiku / sonnet / opus) |
| `/bug <text>` | `_handle_bug_command` | Escape hatch — works even inside admin mode |
| `/stats` | `_handle_stats_command` | Personal usage stats |

### Dispatch order

Inside `handle_incoming_message`:

1. `/bug` + `/comentario` intercepts (always first — escape hatch)
2. `/admin off` phrases
3. `/admin` toggle
4. If `user.admin_mode_active` → `_handle_model_commands` → `_handle_admin_turn`
5. Normal onboarding / intent / drug search pipeline

The `/bug` escape hatch is deliberately placed BEFORE the admin dispatch so an admin who ends up in a broken admin state can always self-report the issue from chat.

### Tool registry (services/admin_chat.py)

Each tool is an async function returning a short text string. The LLM emits `ACTION: TOOL_CALL / TOOL: <name> / ARGS: {...json...}`, the tool result is fed back as a user turn, and the loop continues (max `MAX_ADMIN_STEPS`) until the LLM returns `ACTION: FINAL / RESPONSE: ...`.

| Domain | Tools |
|--------|-------|
| Feedback | `list_feedback`, `get_feedback`, `update_feedback`, `report_issue` |
| Conversation logs | `list_conversation_logs`, `get_conversation_log` |
| AI roles | `list_ai_roles`, `get_ai_role`, `update_ai_role`, `add_ai_rule`, `update_ai_rule`, `delete_ai_rule`, `add_ai_skill`, `update_ai_skill`, `delete_ai_skill` |
| Users | `list_users`, `get_user`, `get_user_memory`, `set_user_memory`, `clear_user_memory`, `set_user_setting` (whitelisted fields only) |
| Pharmacies / Products | `list_pharmacies`, `get_pharmacy`, `set_pharmacy_active`, `list_products`, `get_product` |
| Analytics | `recent_searches`, `counts` |
| App settings | `list_app_settings`, `get_app_setting`, `set_app_setting`, `get_default_model`, `set_default_model` |
| Code introspection | `read_code`, `list_code` (sandboxed allowlist) |

### `report_issue` — admin-to-backlog bridge

When the admin flags a bug, idea, or issue during a session, `report_issue` writes a row into `user_feedback` with `feedback_type=f"admin_{bug|idea|issue}"`. The dev-side `/farmafacil-review` skill (and related triage skills) can filter the backlog with `WHERE feedback_type LIKE 'admin_%'` to distinguish admin-flagged items from end-user submissions. The caller's `admin_user_id` is injected by `execute_tool` (stripping any LLM-supplied value first — security hardening) so the audit trail always points to the right admin.

### Code introspection sandbox

The `read_code` and `list_code` tools let the admin AI explain its own source when asked "cómo funciona X" type questions. Paths must pass `_is_allowed_path`:

- Absolute paths rejected (`/`, `~`)
- Post-`resolve()` must be inside `PROJECT_ROOT`
- Hidden files (`.env`, `.git`, etc.) rejected
- Forbidden suffixes: `.db`, `.sqlite`, `.pyc`, `.pyo`, `.so`
- Forbidden names: `.env*`, `credentials.json`, `farmafacil.db`
- Must be either inside `src/farmafacil/`, `tests/`, or `docs/`, OR one of: `CLAUDE.md`, `IMPROVEMENT-PLAN.md`, `README.md`, `pyproject.toml`, `MEMORY.md`
- Reads capped at 64 KiB; listings capped at 100 entries

### Logging & token tracking

Admin replies are logged with `conversation_logs.message_type="admin_out"` so they're easy to filter from regular user traffic. Token usage flows into a dedicated bucket:

| Column | Price |
|--------|-------|
| `tokens_in_admin` / `tokens_out_admin` | $15 / $75 per MTok (Opus) |
| `calls_admin` | Count of admin turns |

Plus matching `global_*_admin` aggregates. The `/admin/user-stats/{id}` dashboard and `/api/v1/stats` endpoint render an Admin card separately so admin cost never pollutes user-facing cost metrics.

### Kill switch

If an admin ends up in a stuck admin-mode state, any of these work:

1. Send `/bug <text>` — escape hatch routes around admin dispatch
2. Send `/admin` (the toggle — second tap turns it off)
3. Send `turn off admin`, `apagar admin`, or `admin off`
4. An operator with dashboard access flips `admin_mode_active` to `False` in the UserAdmin view
