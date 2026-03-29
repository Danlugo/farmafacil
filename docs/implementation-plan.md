# FarmaFacil — Phased Implementation Plan

## Context

The FarmaFacil spec (from JM's detailed analysis and group discussions) defines a 6-module platform for Venezuela's pharmacy market. The core vision is: **WhatsApp as primary entry point → multi-pharmacy drug search → order & pay → delivery tracking**. This plan breaks that vision into implementable code phases, each delivering a working increment.

**What we already have (Phase 0 — done):**
- FastAPI backend with `/api/v1/search` endpoints (GET + POST)
- Farmatodo scraper with HTML parser + price extraction
- SQLAlchemy async ORM (SQLite default, Postgres-ready)
- Pydantic schemas, 16 passing tests
- Repo: `github.com/Danlugo/farmafacil`

---

## Phase 1: Live Drug Search Engine (the core product)
**Goal:** A working drug search that actually returns real results from Farmatodo, with caching and geographic filtering.

### 1A. Fix Farmatodo Scraper Against Live Site
- **What:** Hit `farmatodo.com.ve` live, inspect actual HTML structure, fix CSS selectors
- **Files:** `src/farmafacil/scrapers/farmatodo.py`
- **How:** Use httpx to fetch a real search page, inspect with BeautifulSoup, update selectors. Farmatodo may use a JS-rendered SPA — if so, check if they have an underlying API (XHR/fetch calls) we can hit directly instead of scraping HTML.
- **Tests:** Integration test that hits live site (tagged `@pytest.mark.integration`)

### 1B. Add Scraper Result Caching
- **What:** Cache scraper results in SQLite so we don't hit Farmatodo on every search. TTL-based (e.g., 30 min).
- **Files:** `src/farmafacil/services/cache.py` (new), update `services/search.py`
- **How:** Before scraping, check `drug_listings` table for recent results. If found within TTL, return cached. Otherwise scrape, store, return.
- **DB change:** Add index on `(drug_name_normalized, pharmacy_id, scraped_at)` for fast lookups.

### 1C. Geographic Zone Filtering
- **What:** Filter results by city/zone (Caracas, Maracaibo, Valencia, etc.) since pharmacy availability varies by location.
- **Files:** Update `models/schemas.py` (add `zone` field), update `scrapers/farmatodo.py` (pass zone to search URL if supported), update `api/routes.py` (add `zone` query param)
- **DB change:** Add `zone` column to `drug_listings`

### 1D. BCV Exchange Rate Service
- **What:** Fetch the current BCV (Banco Central de Venezuela) exchange rate so we can show prices in both USD and Bolivares.
- **Files:** `src/farmafacil/services/exchange_rate.py` (new)
- **How:** Scrape or call BCV API for the official rate. Cache for 1 hour. Apply to price display.

**Deliverable:** `GET /api/v1/search?q=losartan&zone=caracas` returns real Farmatodo results with USD + Bs prices.

---

## Phase 2: WhatsApp Bot (the primary user interface)
**Goal:** Users can search for drugs via WhatsApp — the spec's "Phase 1" user experience.

### 2A. WhatsApp Business SDK Bot Scaffold
- **What:** Standalone WhatsApp bot using the WhatsApp Business Cloud API (Meta).
- **Files:** `src/farmafacil/bot/` (new directory): `__init__.py`, `handler.py`, `formatter.py`, `webhook.py`
- **Dependencies:** `python-whatsapp-cloud-api` or direct HTTP to Meta Graph API
- **How:** FastAPI webhook endpoint receives incoming messages → parses drug name → calls `search_drug()` → formats response → sends back via WhatsApp API.
- **Config:** `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN` env vars

### 2B. Conversational Flow
- **What:** The WhatsApp UX from the spec:
  ```
  User: "Hola, necesito Enalapril 10mg"
  Bot: "Encontré Enalapril 10mg en 3 farmacias cerca de ti:
    💊 Farmatodo Las Mercedes — Bs. 4.50 ✅ En stock
    💊 Locatel Chacao — $1.20 ✅ En stock
    ¿Cuál prefieres?"
  ```
- **Files:** `src/farmafacil/bot/conversation.py` (new) — state machine for multi-turn conversations
- **States:** `IDLE → SEARCHING → RESULTS_SHOWN → PHARMACY_SELECTED → ORDER_PLACED`

### 2C. Search Analytics
- **What:** Log every WhatsApp search to `search_logs` table for analytics (what drugs are people searching for most, what zones, etc.)
- **Files:** Update `services/search.py` to accept `source` param, write to DB

**Deliverable:** WhatsApp number that responds to drug search queries with formatted results.

---

## Phase 3: Multi-Pharmacy Expansion (Module 1 complete)
**Goal:** Search across multiple pharmacies, not just Farmatodo.

### 3A. Locatel Scraper
- **Files:** `src/farmafacil/scrapers/locatel.py` (new)
- **How:** Same pattern as Farmatodo — investigate `locatel.com.ve`, find search URL, parse results

### 3B. XANA / Drogueria Nena Scraper
- **Files:** `src/farmafacil/scrapers/xana.py` (new)
- **Note:** XANA is the first potential B2B client (JM has connection). May need different approach — could be API-based if they partner with us.

### 3C. Generic Pharmacy Scraper
- **What:** Small pharmacies that don't have websites — manual inventory upload via a simple form or CSV.
- **Files:** `src/farmafacil/api/pharmacy_admin.py` (new) — endpoints for pharmacy owners to upload/manage inventory
- **DB:** New table for pharmacy users/auth

### 3D. Result Ranking & Deduplication
- **What:** When multiple pharmacies return the same drug, rank by: price (lowest first), availability, proximity to user's zone.
- **Files:** `src/farmafacil/services/ranking.py` (new)

**Deliverable:** Multi-pharmacy search with ranked, deduplicated results.

---

## Phase 4: Order & Payment Engine (Module 3)
**Goal:** Users can place orders and pay through the platform. Escrow model.

### 4A. Order Model
- **DB tables:** `orders`, `order_items`, `order_status_history`
- **Files:** Update `models/database.py`, new `src/farmafacil/services/orders.py`
- **States:** `CREATED → PAYMENT_PENDING → PAID → PHARMACY_CONFIRMED → DISPATCHED → DELIVERED → COMPLETED`

### 4B. Pago Movil Integration
- **What:** Venezuela's dominant payment method. User pays via Pago Movil, confirms with reference number.
- **Files:** `src/farmafacil/services/payments/pago_movil.py` (new)
- **How:** Manual confirmation flow initially — user sends Pago Movil reference, admin verifies. Automated reconciliation later.

### 4C. Stripe Integration (USD payments)
- **What:** For international/diaspora users paying in USD.
- **Files:** `src/farmafacil/services/payments/stripe.py` (new)
- **Dependencies:** `stripe` SDK

### 4D. Escrow Service
- **What:** Hold funds until delivery is confirmed, then release to pharmacy (minus FarmaFacil fee) and delivery driver.
- **Files:** `src/farmafacil/services/payments/escrow.py` (new)
- **DB:** `escrow_transactions` table with states: `HELD → RELEASED_TO_PHARMACY → RELEASED_TO_DRIVER → FEE_COLLECTED`

### 4E. WhatsApp Order Flow
- **What:** Extend the bot to support: "¿Quieres que te lo lleven a casa?" → address → payment method → order confirmation → tracking updates.
- **Files:** Update `bot/conversation.py` with order states

**Deliverable:** End-to-end order + payment through WhatsApp with Pago Movil.

---

## Phase 5: Delivery Tracking (extends Module 1)
**Goal:** Track motorcycle delivery from pharmacy to customer.

### 5A. Delivery Driver Model
- **DB:** `delivery_drivers`, `delivery_assignments` tables
- **Files:** `src/farmafacil/services/delivery.py` (new)

### 5B. Driver Assignment Logic
- **What:** When an order is confirmed, assign to nearest available driver. Initially manual (admin assigns), later automated.

### 5C. Real-Time Status Updates via WhatsApp
- **What:** Send WhatsApp messages at each stage: "Tu pedido fue recogido", "El motorizado está en camino", "Entregado"
- **Files:** Update `bot/handler.py` with push notification flow

**Deliverable:** Order tracking through WhatsApp notifications.

---

## Phase 6: Pharmacy B2B Panel (Module 4)
**Goal:** Pharmacy partners can manage inventory, receive orders, view analytics.

### 6A. Pharmacy Auth & Onboarding
- **Files:** `src/farmafacil/api/auth.py` (new), `src/farmafacil/services/pharmacy_accounts.py`
- **How:** Simple API key or JWT auth for pharmacy partners

### 6B. Inventory Management API
- **Endpoints:** `POST /api/v1/pharmacy/inventory` (upload CSV or JSON), `GET /api/v1/pharmacy/orders`, `PATCH /api/v1/pharmacy/orders/{id}/confirm`
- **Files:** `src/farmafacil/api/pharmacy.py` (new)

### 6C. Pharmacy Analytics Dashboard
- **What:** Sales volume, top drugs, revenue, pending orders
- **Files:** `src/farmafacil/api/analytics.py` (new)

**Deliverable:** API-first B2B panel (frontend can be added later).

---

## Phase 7: Digital Prescriptions (Module 2)
**Goal:** Prescription upload, validation, and tracking.

### 7A. Prescription Upload
- **What:** User uploads photo of prescription via WhatsApp. Store in object storage.
- **Files:** `src/farmafacil/services/prescriptions.py` (new)
- **Storage:** Local filesystem initially, S3/R2 later

### 7B. Pharmacist Validation Queue
- **What:** Queue for pharmacists to validate prescriptions before dispensing.
- **DB:** `prescriptions` table with states: `UPLOADED → PENDING_REVIEW → APPROVED → REJECTED → DISPENSED`

**Deliverable:** Prescription upload via WhatsApp, pharmacist review queue.

---

## Phase 8: AI & Loyalty (Modules 5 & 6) — Future
- Medication history and refill alerts for chronic conditions
- Drug interaction chatbot (AI-powered)
- VitaPuntos cross-pharmacy loyalty program
- Platform financing for drug purchases (LE's differentiator)

---

## Implementation Priority Summary

| Phase | Module | What | Effort | Business Value |
|-------|--------|------|--------|----------------|
| **1** | Core | Live Farmatodo search + caching + exchange rate | 1-2 weeks | Foundation — nothing works without this |
| **2** | Core | WhatsApp bot | 1-2 weeks | Primary user interface — the whole value prop |
| **3** | Module 1 | Multi-pharmacy (Locatel, XANA) | 2-3 weeks | Competitive moat — no one else does this |
| **4** | Module 3 | Orders + Pago Movil + Escrow | 3-4 weeks | Revenue — this is where we make money |
| **5** | Delivery | Driver tracking | 2 weeks | User experience — order completion |
| **6** | Module 4 | Pharmacy B2B panel | 2-3 weeks | Partner onboarding — needed for XANA deal |
| **7** | Module 2 | Digital prescriptions | 2 weeks | Differentiator — no one else has this |
| **8** | Modules 5+6 | AI + Loyalty | Ongoing | Long-term retention |

## Recommended Next Step
**Start with Phase 1A** — hit the live Farmatodo site, inspect the actual HTML/API, and fix the scraper so we have real data flowing. Everything else depends on this working.

## Verification
- Phase 1: `pytest` + manual `curl` to search endpoint returns real Farmatodo results
- Phase 2: Send WhatsApp message to bot, receive drug search results
- Phase 3: Search returns results from multiple pharmacies
- Phase 4: Complete order flow via WhatsApp with Pago Movil confirmation
