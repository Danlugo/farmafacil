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

### Item 2: Locatel Scraper (VTEX GraphQL)

- **Status:** PENDING
- **Added:** 2026-03-30
- **Problem:** Locatel is one of Venezuela's largest pharmacy/retail chains with an online catalog at locatel.com.ve, also powered by VTEX. Adding Locatel significantly expands drug availability coverage.
- **Suggested solution:** Create `src/farmafacil/scrapers/locatel.py` subclassing `BaseScraper` (or `VTEXScraper` if Item 1 creates the shared base). Use the same VTEX Intelligent Search GraphQL pattern. Locatel sells more than drugs (home goods, personal care), so filtering to pharmacy/health categories may be needed.
- **Affected files:** `src/farmafacil/scrapers/locatel.py` (new), `src/farmafacil/services/search.py` (register scraper), `tests/test_locatel_scraper.py` (new)
- **Effort:** Medium (2–3 hours, less if VTEXScraper base exists from Item 1)
- **Notes:** Locatel uses the same VTEX platform as SAAS. Implement Item 1 first to establish the VTEX pattern, then Locatel becomes a thin adapter.

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
