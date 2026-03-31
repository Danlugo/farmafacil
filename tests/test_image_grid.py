"""Tests for the image grid service — _unique_product_images and generate_product_grid."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from farmafacil.models.schemas import DrugResult
from farmafacil.services.image_grid import _unique_product_images, generate_product_grid


# ── Fixtures ────────────────────────────────────────────────────────────────


def make_result(
    drug_name: str,
    pharmacy_name: str,
    image_url: str | None = None,
    price_bs: Decimal | None = None,
    available: bool = True,
) -> DrugResult:
    """Helper to build a DrugResult with minimal boilerplate."""
    return DrugResult(
        drug_name=drug_name,
        pharmacy_name=pharmacy_name,
        price_bs=price_bs,
        available=available,
        image_url=image_url,
    )


# ── TestUniqueProductImages ─────────────────────────────────────────────────


class TestUniqueProductImages:
    """Unit tests for _unique_product_images (pure function — no I/O)."""

    def test_single_product_with_image_returned(self):
        """Single product with an image URL is returned."""
        results = [
            make_result("Losartan 50mg", "Farmatodo", image_url="https://example.com/img1.jpg"),
        ]
        unique = _unique_product_images(results, max_products=8)
        assert len(unique) == 1
        assert unique[0].drug_name == "Losartan 50mg"

    def test_product_without_image_is_skipped(self):
        """Products with no image_url are excluded from the result."""
        results = [
            make_result("DrugNoImage", "Farmatodo", image_url=None),
            make_result("DrugWithImage", "Farmatodo", image_url="https://example.com/img.jpg"),
        ]
        unique = _unique_product_images(results, max_products=8)
        assert len(unique) == 1
        assert unique[0].drug_name == "DrugWithImage"

    def test_all_products_without_images_returns_empty(self):
        """When no product has an image URL, the result is an empty list."""
        results = [
            make_result("Drug A", "Farmatodo", image_url=None),
            make_result("Drug B", "Farmatodo", image_url=None),
        ]
        unique = _unique_product_images(results, max_products=8)
        assert unique == []

    def test_duplicate_image_url_across_products_deduped(self):
        """Two different products sharing the same image URL produce only one entry.

        The first product in interleaved order claims the URL; the second is skipped.
        Chain names are sorted alphabetically for round-robin, so "Farmacias SAAS"
        comes before "Farmatodo" — Drug B (SAAS) is emitted first and claims the URL.
        """
        shared_url = "https://example.com/shared.jpg"
        results = [
            make_result("Drug A", "Farmatodo", image_url=shared_url),
            make_result("Drug B", "Farmacias SAAS", image_url=shared_url),
        ]
        unique = _unique_product_images(results, max_products=8)
        assert len(unique) == 1
        # "Farmacias SAAS" < "Farmatodo" alphabetically → Drug B wins the URL
        assert unique[0].drug_name == "Drug B"

    def test_same_product_different_pharmacies_one_image(self):
        """Same drug from two pharmacies yields at most one image entry."""
        results = [
            make_result(
                "Losartan 50mg",
                "Farmatodo",
                image_url="https://farmatodo.com/losartan.jpg",
                price_bs=Decimal("900"),
            ),
            make_result(
                "Losartan 50mg",
                "Farmacias SAAS",
                image_url="https://saas.com/losartan.jpg",
                price_bs=Decimal("800"),
            ),
        ]
        unique = _unique_product_images(results, max_products=8)
        # The formatter groups them into one product group — only one image picked
        assert len(unique) == 1

    def test_max_products_limits_output(self):
        """max_products caps the number of returned images."""
        results = [
            make_result(f"Drug {i}", "Farmatodo", image_url=f"https://example.com/img{i}.jpg")
            for i in range(10)
        ]
        unique = _unique_product_images(results, max_products=5)
        assert len(unique) == 5

    def test_max_products_zero_returns_empty(self):
        """max_products=0 always returns an empty list."""
        results = [
            make_result("Drug A", "Farmatodo", image_url="https://example.com/a.jpg"),
        ]
        unique = _unique_product_images(results, max_products=0)
        assert unique == []

    def test_order_matches_formatter_order(self):
        """Image order matches _group_by_product interleaving order.

        Chain names are sorted alphabetically for round-robin:
        "Farmacias SAAS" < "Farmatodo" → SAAS goes first each round.
        SAAS has: Drug B, Drug D.  Farmatodo has: Drug A, Drug C.
        Interleaved order: Drug B, Drug A, Drug D, Drug C.
        """
        results = [
            make_result("Drug A", "Farmatodo", image_url="https://example.com/a.jpg"),
            make_result("Drug B", "Farmacias SAAS", image_url="https://example.com/b.jpg"),
            make_result("Drug C", "Farmatodo", image_url="https://example.com/c.jpg"),
            make_result("Drug D", "Farmacias SAAS", image_url="https://example.com/d.jpg"),
        ]
        unique = _unique_product_images(results, max_products=8)
        names = [r.drug_name for r in unique]
        # "Farmacias SAAS" sorts before "Farmatodo" alphabetically
        assert names == ["Drug B", "Drug A", "Drug D", "Drug C"]

    def test_empty_results_returns_empty(self):
        """Empty input produces an empty list."""
        unique = _unique_product_images([], max_products=8)
        assert unique == []

    def test_fallback_to_second_pharmacy_image_when_first_has_no_image(self):
        """If the primary (cheapest) pharmacy has no image, the next with an image is used."""
        results = [
            make_result(
                "Losartan 50mg",
                "CheapButNoImage",
                image_url=None,
                price_bs=Decimal("100"),
            ),
            make_result(
                "Losartan 50mg",
                "ExpensiveButHasImage",
                image_url="https://example.com/losartan.jpg",
                price_bs=Decimal("999"),
            ),
        ]
        unique = _unique_product_images(results, max_products=8)
        # Should pick the second pharmacy's image since the first has none
        assert len(unique) == 1
        assert unique[0].image_url == "https://example.com/losartan.jpg"


# ── TestGenerateProductGrid ─────────────────────────────────────────────────


class TestGenerateProductGrid:
    """Tests for generate_product_grid — mocks _download_image to avoid network I/O."""

    async def test_returns_none_when_no_products_have_images(self):
        """Returns None if no product has an image URL."""
        results = [
            make_result("Drug A", "Farmatodo", image_url=None),
        ]
        result = await generate_product_grid(results)
        assert result is None

    async def test_returns_none_when_all_downloads_fail(self):
        """Returns None if _download_image returns None for every product."""
        results = [
            make_result("Drug A", "Farmatodo", image_url="https://example.com/a.jpg"),
        ]
        with patch(
            "farmafacil.services.image_grid._download_image",
            new=AsyncMock(return_value=None),
        ):
            result = await generate_product_grid(results)
        assert result is None

    async def test_returns_none_for_empty_results(self):
        """Returns None if the results list is empty."""
        result = await generate_product_grid([])
        assert result is None

    async def test_returns_file_path_on_success(self, tmp_path):
        """Returns a file path string when at least one image downloads successfully."""
        from PIL import Image

        # Create a real small image in memory to return from the mock
        fake_image = Image.new("RGBA", (100, 100), color=(255, 0, 0, 255))

        results = [
            make_result("Drug A", "Farmatodo", image_url="https://example.com/a.jpg"),
        ]
        with patch(
            "farmafacil.services.image_grid._download_image",
            new=AsyncMock(return_value=fake_image),
        ):
            result = await generate_product_grid(results)

        assert result is not None
        assert isinstance(result, str)
        assert result.endswith(".png")

    async def test_partial_download_failure_still_generates_grid(self):
        """If some images fail to download, the grid is still generated with the successful ones."""
        from PIL import Image

        fake_image = Image.new("RGBA", (100, 100), color=(0, 255, 0, 255))

        results = [
            make_result("Drug A", "Farmatodo", image_url="https://example.com/a.jpg"),
            make_result("Drug B", "Farmatodo", image_url="https://example.com/b.jpg"),
        ]

        call_count = 0

        async def mock_download(url: str):
            nonlocal call_count
            call_count += 1
            # First succeeds, second fails
            return fake_image if call_count == 1 else None

        with patch("farmafacil.services.image_grid._download_image", new=mock_download):
            result = await generate_product_grid(results)

        # Grid generated with the one successful image
        assert result is not None
        assert result.endswith(".png")

    async def test_max_products_respected_by_grid(self):
        """generate_product_grid respects the max_products parameter."""
        from PIL import Image

        fake_image = Image.new("RGBA", (50, 50), color=(0, 0, 255, 255))
        download_calls: list[str] = []

        async def mock_download(url: str):
            download_calls.append(url)
            return fake_image

        results = [
            make_result(f"Drug {i}", "Farmatodo", image_url=f"https://example.com/{i}.jpg")
            for i in range(10)
        ]

        with patch("farmafacil.services.image_grid._download_image", new=mock_download):
            result = await generate_product_grid(results, max_products=3)

        assert result is not None
        # Only 3 images should have been downloaded
        assert len(download_calls) == 3
