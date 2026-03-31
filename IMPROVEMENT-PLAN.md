# FarmaFacil — Improvement Plan

Tracks planned improvements, new features, and technical debt. Items are prioritized P0–P3 and executed via `/farmafacil-update`.

**Status legend:** PENDING | IN PROGRESS | DONE | DEFERRED

---

## P1 — High

### Item 1: Farmacias SAAS Scraper (VTEX GraphQL)

- **Status:** PENDING
- **Added:** 2026-03-30
- **Problem:** FarmaFacil only searches Farmatodo. Farmacias SAAS is a major Venezuelan pharmacy chain with an online catalog at farmaciasaas.com powered by VTEX, exposing a GraphQL API for product search.
- **Suggested solution:** Create `src/farmafacil/scrapers/saas.py` subclassing `BaseScraper`. Use the VTEX Intelligent Search GraphQL endpoint (`/api/io/_v/api/intelligent-search/product_search/`) to query products. Extract product name, price, image URL, availability, and store locations. Consider creating a shared `VTEXScraper` base class since Locatel also uses VTEX.
- **Affected files:** `src/farmafacil/scrapers/saas.py` (new), `src/farmafacil/scrapers/vtex_base.py` (new, optional shared base), `src/farmafacil/services/search.py` (register scraper), `tests/test_saas_scraper.py` (new)
- **Effort:** Medium (3–4 hours)
- **Notes:** VTEX GraphQL API is publicly accessible for product search. Need to reverse-engineer the exact query parameters and response schema from farmaciasaas.com.

### Item 2: Locatel Scraper (VTEX GraphQL)

- **Status:** PENDING
- **Added:** 2026-03-30
- **Problem:** Locatel is one of Venezuela's largest pharmacy/retail chains with an online catalog at locatel.com.ve, also powered by VTEX. Adding Locatel significantly expands drug availability coverage.
- **Suggested solution:** Create `src/farmafacil/scrapers/locatel.py` subclassing `BaseScraper` (or `VTEXScraper` if Item 1 creates the shared base). Use the same VTEX Intelligent Search GraphQL pattern. Locatel sells more than drugs (home goods, personal care), so filtering to pharmacy/health categories may be needed.
- **Affected files:** `src/farmafacil/scrapers/locatel.py` (new), `src/farmafacil/services/search.py` (register scraper), `tests/test_locatel_scraper.py` (new)
- **Effort:** Medium (2–3 hours, less if VTEXScraper base exists from Item 1)
- **Notes:** Locatel uses the same VTEX platform as SAAS. Implement Item 1 first to establish the VTEX pattern, then Locatel becomes a thin adapter.

---

## P2 — Medium

*(No items yet)*

---

## P3 — Low

### Item 3: Farmacias XANA Scraper

- **Status:** DEFERRED
- **Added:** 2026-03-30
- **Problem:** Farmacias XANA is a Venezuelan pharmacy chain, but they have no online catalog or public API. Without a digital presence, there is no data source to scrape.
- **Suggested solution:** Monitor for any future online presence. If they launch a website or app, revisit. Alternatively, explore if any third-party aggregator includes XANA inventory.
- **Affected files:** TBD
- **Effort:** Unknown
- **Notes:** Physical-only chain as of March 2026. No website, no app store presence. Deferred until a data source becomes available.

---

## Completed

*(No items completed yet)*
