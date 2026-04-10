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

The bot calls `geocode_zone()` via OpenStreetMap Nominatim. On success, the city code is derived and stored.

| User sends | Bot behavior |
|-----------|-------------|
| "La Boyera" | Geocodes → saves lat/lng/zone_name/city_code, advances |
| "Chacao" | Geocodes → saves, advances |
| "xyz123" | Geocoding fails → re-asks with examples |

On success, step advances to `awaiting_preference`.

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
| `greeting` | Sends welcome-back message with current zone and preference |
| `help` | Sends full help menu with command list |
| `drug_search` | Runs drug search, sends results text + image |
| `question` | Tries store lookup; if not a store, sends LLM-generated answer |
| `unknown` | Prompts user to send a drug name |

---

## Drug Search Flow

When intent is `drug_search`:

1. Check user has location (if not → prompt, set step to `awaiting_location`)
2. **Symptom acknowledgment:** If the AI included a conversational response (e.g., "Entiendo que tienes acidez. Te busco Omeprazol..."), send it as a text message BEFORE the search results. This happens when users describe symptoms instead of naming a specific product.
3. **Drug interaction check:** If the user has known medications in their memory (`user_memories`), extract them via `extract_medications_from_memory()`, then query the RxNorm/RxNav API via `check_interactions()`. If interactions are detected, send a ⚠️ warning message before search results.
4. Call `search_drug(query, city_code, lat, lng, zone_name)`
3. Format results as text via `format_search_results()`
4. Send text message
5. If results exist, send image based on preference:
   - `detail`: Send individual product images (top 3) with rich captions
   - `grid`: Generate a product grid image (up to 6 products) via Pillow, send, delete temp file

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
