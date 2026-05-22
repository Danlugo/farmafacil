"""Tests for zero-price (Bs. 0.00) display handling.

When a pharmacy API returns Bs. 0.00 for a product, the price data is bad/missing.
Instead of showing "Bs. 0.00" (misleading — it's not free), we show
"Precio no disponible" and link to the product page so the user can check themselves.

Covers: formatter._format_price, formatter.format_search_results (store-level),
        handler._build_product_caption.
"""

from decimal import Decimal

import pytest

from farmafacil.bot.formatter import _format_price, format_search_results
from farmafacil.bot.handler import _build_product_caption
from farmafacil.models.schemas import DrugResult, NearbyStore, SearchResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    price_bs: Decimal | None = Decimal("100.00"),
    url: str | None = None,
    **kwargs,
) -> DrugResult:
    """Build a DrugResult with sensible defaults, overridable for each test."""
    defaults = dict(
        drug_name="PR88 CREM FORM QS QUELOIDES 60G",
        pharmacy_name="Farmacia SAAS",
        available=True,
    )
    defaults.update(kwargs)
    return DrugResult(price_bs=price_bs, url=url, **defaults)


def _make_search_response(results: list[DrugResult], query: str = "queloides") -> SearchResponse:
    """Wrap results in a minimal SearchResponse."""
    return SearchResponse(
        query=query,
        results=results,
        total=len(results),
        searched_pharmacies=["Farmacia SAAS"],
    )


# ===========================================================================
# formatter._format_price
# ===========================================================================

class TestFormatPriceZero:
    """_format_price handles Bs. 0.00 as missing/bad price data."""

    def test_zero_price_with_url_shows_link(self):
        """Bs. 0.00 + product URL → 'Precio no disponible — ver en <url>'."""
        result = _make_result(
            price_bs=Decimal("0.00"),
            url="https://www.farmaciasaas.com/product/17919/p",
        )
        text = _format_price(result)
        assert "Precio no disponible" in text
        assert "https://www.farmaciasaas.com/product/17919/p" in text
        assert "Bs." not in text

    def test_zero_price_without_url_shows_plain_message(self):
        """Bs. 0.00 without URL → 'Precio no disponible' (no link)."""
        result = _make_result(price_bs=Decimal("0.00"), url=None)
        text = _format_price(result)
        assert text == "Precio no disponible"

    def test_zero_price_decimal_zero(self):
        """Decimal('0') also triggers the zero-price path."""
        result = _make_result(price_bs=Decimal("0"), url=None)
        text = _format_price(result)
        assert text == "Precio no disponible"

    def test_none_price_returns_empty(self):
        """price_bs=None means no price info at all → empty string."""
        result = _make_result(price_bs=None)
        text = _format_price(result)
        assert text == ""

    def test_normal_price_formatted(self):
        """A normal non-zero price is formatted as Bs. X.XX."""
        result = _make_result(price_bs=Decimal("45.99"))
        text = _format_price(result)
        assert "Bs. 45.99" in text
        assert "Precio no disponible" not in text

    def test_normal_price_with_discount(self):
        """Normal price with full_price and discount shows all info."""
        result = _make_result(
            price_bs=Decimal("36.00"),
            full_price_bs=Decimal("45.00"),
            discount_pct="20%",
        )
        text = _format_price(result)
        assert "Bs. 36.00" in text
        assert "~Bs. 45.00~" in text
        assert "(20%)" in text

    def test_small_nonzero_price_not_treated_as_zero(self):
        """Bs. 0.01 is a valid price — not treated as missing."""
        result = _make_result(price_bs=Decimal("0.01"))
        text = _format_price(result)
        assert "Bs. 0.01" in text
        assert "Precio no disponible" not in text

    def test_negative_price_not_treated_as_zero(self):
        """Negative price (bad data) is not caught by the zero-price guard.

        It displays the raw value — a separate data quality issue, but not
        the zero-price path.
        """
        result = _make_result(price_bs=Decimal("-1.00"))
        text = _format_price(result)
        assert "Precio no disponible" not in text
        assert "Bs." in text


# ===========================================================================
# formatter.format_search_results — store-level zero price
# ===========================================================================

class TestFormatSearchResultsStoreZeroPrice:
    """Store-level Bs. 0.00 prices are hidden (not shown as free)."""

    def test_store_zero_price_hidden(self):
        """A store with price_bs=0 should not display a price line."""
        result = _make_result(
            price_bs=Decimal("0.00"),
            url="https://example.com/product",
            nearby_stores=[
                NearbyStore(
                    store_name="Sucursal Centro",
                    address="Av. Libertador",
                    distance_km=2.5,
                    price_bs=Decimal("0.00"),
                ),
            ],
        )
        response = _make_search_response([result])
        text = format_search_results(response)
        # The store line should show name and distance but NOT "Bs. 0.00"
        assert "Sucursal Centro" in text
        assert "2.5 km" in text
        assert "Bs. 0.00" not in text

    def test_store_none_price_hidden(self):
        """A store with price_bs=None should not display a price."""
        result = _make_result(
            price_bs=Decimal("50.00"),
            nearby_stores=[
                NearbyStore(
                    store_name="Sucursal Este",
                    address="Av. Francisco de Miranda",
                    distance_km=3.2,
                    price_bs=None,
                ),
            ],
        )
        response = _make_search_response([result])
        text = format_search_results(response)
        assert "Sucursal Este" in text
        assert "3.2 km" in text
        # No spurious price on the store line
        lines = text.split("\n")
        store_line = [l for l in lines if "Sucursal Este" in l][0]
        assert "Bs." not in store_line

    def test_store_normal_price_shown(self):
        """A store with a valid non-zero price shows it."""
        result = _make_result(
            price_bs=Decimal("50.00"),
            nearby_stores=[
                NearbyStore(
                    store_name="Sucursal Oeste",
                    address="Calle Bolívar",
                    distance_km=1.0,
                    price_bs=Decimal("48.50"),
                ),
            ],
        )
        response = _make_search_response([result])
        text = format_search_results(response)
        assert "Bs. 48.50" in text

    def test_product_level_zero_price_shows_message(self):
        """The product header shows 'Precio no disponible' for Bs. 0.00."""
        result = _make_result(
            price_bs=Decimal("0.00"),
            url="https://example.com/product",
        )
        response = _make_search_response([result])
        text = format_search_results(response)
        assert "Precio no disponible" in text
        assert "Bs. 0.00" not in text


# ===========================================================================
# handler._build_product_caption — zero price in image captions
# ===========================================================================

class TestBuildProductCaptionZeroPrice:
    """_build_product_caption handles Bs. 0.00 in WhatsApp image captions."""

    def test_zero_price_caption_shows_unavailable(self):
        """Caption for product with Bs. 0.00 shows 'Precio no disponible'."""
        result = _make_result(
            price_bs=Decimal("0.00"),
            url="https://www.farmaciasaas.com/product/17919/p",
            brand="Comialca",
        )
        caption = _build_product_caption(result)
        assert "_Precio no disponible_" in caption
        assert "Ver en:" in caption
        assert "https://www.farmaciasaas.com/product/17919/p" in caption
        assert "Bs. 0.00" not in caption

    def test_zero_price_caption_without_url(self):
        """Caption shows 'Precio no disponible' even without URL."""
        from farmafacil.bot.handler import _build_product_caption

        result = _make_result(price_bs=Decimal("0.00"), url=None, brand="Genérico")
        caption = _build_product_caption(result)
        assert "_Precio no disponible_" in caption
        # No URL line when url is None
        assert "Ver en:" not in caption

    def test_zero_price_caption_has_product_name(self):
        """Even with zero price, the product name is still displayed."""
        from farmafacil.bot.handler import _build_product_caption

        result = _make_result(
            price_bs=Decimal("0.00"),
            drug_name="PR88 CREM FORM QS QUELOIDES 60G",
        )
        caption = _build_product_caption(result)
        assert "PR88 CREM FORM QS QUELOIDES 60G" in caption

    def test_normal_price_caption(self):
        """Normal non-zero price in caption shows Bs. amount."""
        from farmafacil.bot.handler import _build_product_caption

        result = _make_result(
            price_bs=Decimal("45.99"),
            brand="Comialca",
        )
        caption = _build_product_caption(result)
        assert "Bs. 45.99" in caption
        assert "Precio no disponible" not in caption

    def test_normal_price_with_discount_caption(self):
        """Caption shows discounted and full price."""
        from farmafacil.bot.handler import _build_product_caption

        result = _make_result(
            price_bs=Decimal("36.00"),
            full_price_bs=Decimal("45.00"),
            discount_pct="20%",
            brand="Comialca",
        )
        caption = _build_product_caption(result)
        assert "Bs. 36.00" in caption
        assert "~Bs. 45.00~" in caption
        assert "20% DCTO" in caption

    def test_none_price_caption_no_price_line(self):
        """Caption with price_bs=None shows no price information at all."""
        from farmafacil.bot.handler import _build_product_caption

        result = _make_result(price_bs=None, brand="Genérico")
        caption = _build_product_caption(result)
        assert "Bs." not in caption
        assert "Precio no disponible" not in caption
