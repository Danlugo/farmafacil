# FarmaFacil — Adding a New Pharmacy Scraper

> Last Updated: 2026-03-30

## Overview

All pharmacy scrapers implement the `BaseScraper` abstract class and are registered in `ACTIVE_SCRAPERS`. The search service queries all active scrapers and stores results in the product catalog.

---

## BaseScraper Interface

File: `src/farmafacil/scrapers/base.py`

```python
class BaseScraper(abc.ABC):

    @property
    @abc.abstractmethod
    def pharmacy_name(self) -> str:
        """Human-readable pharmacy name (e.g., 'Farmatodo')."""
        ...

    @abc.abstractmethod
    async def search(
        self, query: str, city: str | None = None, max_results: int = 10
    ) -> list[DrugResult]:
        """Search for a drug and return results.

        Args:
            query: Drug name to search (e.g., "losartan").
            city: Optional city name for localized pricing (e.g., "caracas").
            max_results: Max results to return.

        Returns:
            List of DrugResult objects.
        """
        ...
```

The `DrugResult` schema is defined in `src/farmafacil/models/schemas.py`. Key fields:

| Field | Type | Notes |
|-------|------|-------|
| `drug_name` | str | Product display name |
| `pharmacy_name` | str | Must match `pharmacy_name` property |
| `price_bs` | Decimal \| None | Best (current/offer) price in Bolivares |
| `full_price_bs` | Decimal \| None | Original price before discount (if applicable) |
| `discount_pct` | str \| None | Discount text (e.g., "20%") |
| `available` | bool | Whether the drug is in stock |
| `url` | str \| None | Product page URL |
| `requires_prescription` | bool | True if Rx required |
| `image_url` | str \| None | Product image URL |
| `brand` | str \| None | Manufacturer/brand name |
| `drug_class` | str \| None | Pharmacological class |
| `stores_in_stock` | int | Number of stores with stock |
| `stores_with_stock_ids` | list[int] | Store IDs from the pharmacy's system |

---

## Step-by-Step Guide

### Step 1: Create the scraper file

Create `src/farmafacil/scrapers/new_pharmacy.py`.

```python
"""NewPharmacy drug search via their API."""

import logging
import httpx
from farmafacil.config import SCRAPER_TIMEOUT
from farmafacil.models.schemas import DrugResult
from farmafacil.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

NEW_PHARMACY_API_URL = "https://api.newpharmacy.com/search"


class NewPharmacyScraper(BaseScraper):

    @property
    def pharmacy_name(self) -> str:
        return "NewPharmacy"

    async def search(
        self, query: str, city: str | None = None, max_results: int = 10
    ) -> list[DrugResult]:
        try:
            async with httpx.AsyncClient(timeout=SCRAPER_TIMEOUT) as client:
                response = await client.get(
                    NEW_PHARMACY_API_URL,
                    params={"q": query, "limit": max_results},
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("NewPharmacy search timed out for query: %s", query)
            return []
        except httpx.HTTPStatusError as exc:
            logger.warning("NewPharmacy returned HTTP %s", exc.response.status_code)
            return []

        data = response.json()
        return [self._hit_to_result(hit) for hit in data.get("products", [])]

    def _hit_to_result(self, hit: dict) -> DrugResult:
        return DrugResult(
            drug_name=hit.get("name", "Unknown"),
            pharmacy_name=self.pharmacy_name,
            price_bs=hit.get("price"),
            available=hit.get("inStock", False),
            url=hit.get("url"),
            image_url=hit.get("imageUrl"),
            brand=hit.get("brand"),
        )
```

### Step 1b: VTEX shortcut (for VTEX-powered pharmacies)

If the pharmacy runs on VTEX (e.g., Farmacias SAAS, Locatel), subclass `VTEXScraper` instead. All HTTP and parsing logic is handled by the base class — you only need to set `base_url` and `pharmacy_name`:

```python
"""NewVTEXPharmacy drug search via VTEX Intelligent Search API."""

from farmafacil.scrapers.vtex import VTEXScraper

class NewVTEXPharmacyScraper(VTEXScraper):
    base_url = "https://www.newvtexpharmacy.com"

    @property
    def pharmacy_name(self) -> str:
        return "NewVTEXPharmacy"
```

See `src/farmafacil/scrapers/saas.py` for a real example.

### Step 2: Register in ACTIVE_SCRAPERS

File: `src/farmafacil/services/search.py`

```python
from farmafacil.scrapers.farmatodo import FarmatodoScraper
from farmafacil.scrapers.saas import SAASScraper
from farmafacil.scrapers.new_pharmacy import NewPharmacyScraper

ACTIVE_SCRAPERS: list[BaseScraper] = [
    FarmatodoScraper(),
    SAASScraper(),
    NewPharmacyScraper(),
]
```

The search service iterates `ACTIVE_SCRAPERS` on every cache miss and merges all results.

### Step 3: Add store locations (optional)

If the pharmacy has physical locations and exposes a store API, add a backfill function (see `src/farmafacil/services/store_backfill.py` for the Farmatodo pattern). Store locations are saved to `pharmacy_locations` and used to show the nearest store to each user.

### Step 4: Write tests

Create `tests/test_new_pharmacy_scraper.py`. Minimum tests:

- Happy path: mock the API, verify `DrugResult` fields are populated correctly
- Timeout: verify returns empty list (no exception raised)
- HTTP error: verify returns empty list with warning logged
- City filtering: verify city parameter is passed when applicable

---

## How Results Are Stored

After a successful scrape, `save_search_results()` in `product_cache.py` upserts:

1. **Product** record — by `(external_id, pharmacy_chain)`. External ID is derived from the URL slug or `pharmacy_name:drug_name` as fallback.
2. **ProductPrice** record — by `(product_id, city_code)`. Updates price, stock, and `refreshed_at`.
3. **SearchQuery** record — maps the normalized query + city to the ordered product ID list.

This means results from multiple scrapers for the same query are all stored and served together. Each pharmacy's products are tracked separately with their own `pharmacy_chain` value.

---

## City Code Mapping

The geocode service maps Venezuelan locations to Farmatodo city codes (e.g., `CCS`, `MCBO`). These same codes are passed to the scraper's `city` parameter after geocoding.

If the new pharmacy uses different city identifiers, add a mapping in the scraper's `search()` method before building the API request.

See `src/farmafacil/scrapers/farmatodo.py` → `CITY_CODES` for the Farmatodo mapping.
