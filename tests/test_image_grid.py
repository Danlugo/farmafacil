"""Tests for the image grid service — _unique_product_images and generate_product_grid."""

import os
import tempfile
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _cleanup_grid_tempfiles():
    """Remove any tempfiles created by generate_product_grid during the test.

    generate_product_grid writes to ``tempfile.NamedTemporaryFile(delete=False)``
    — in production the caller (bot/handler.py) unlinks the file after
    upload. Tests that call generate_product_grid directly skip that
    cleanup, so we sweep the tempdir for ``.jpg`` files created during
    the test run and unlink anything newer than the test start time.
    """
    start = _now_mtime()
    yield
    tmpdir = tempfile.gettempdir()
    for name in os.listdir(tmpdir):
        if not name.endswith(".jpg"):
            continue
        path = os.path.join(tmpdir, name)
        try:
            if os.path.getmtime(path) >= start:
                os.unlink(path)
        except OSError:
            pass


def _now_mtime() -> float:
    """Return the current mtime-style timestamp (same units as os.path.getmtime)."""
    import time
    return time.time()

from farmafacil.models.schemas import DrugResult
from farmafacil.services.image_grid import (
    CANVAS_WIDTH,
    IMAGE_SIZE,
    TARGET_SRC_SIZE,
    _render_card,
    _unique_product_images,
    _upgrade_image_url,
    generate_product_grid,
)


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
        assert result.endswith(".jpg")

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
        assert result.endswith(".jpg")

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


# ── TestUpgradeImageUrl ─────────────────────────────────────────────────────


class TestUpgradeImageUrl:
    """Unit tests for _upgrade_image_url — URL rewrite helper (pure function)."""

    def test_empty_string_returned_unchanged(self):
        """Empty string input is returned as-is."""
        assert _upgrade_image_url("") == ""

    def test_none_input_returned_unchanged(self):
        """None input is returned as None without raising."""
        assert _upgrade_image_url(None) is None  # type: ignore[arg-type]

    def test_google_cdn_plain_gets_size_suffix(self):
        """A plain lh3.googleusercontent.com URL gets ``=s{TARGET_SRC_SIZE}`` appended."""
        url = "https://lh3.googleusercontent.com/abc123"
        upgraded = _upgrade_image_url(url)
        assert upgraded == f"https://lh3.googleusercontent.com/abc123=s{TARGET_SRC_SIZE}"

    def test_google_cdn_existing_s_suffix_replaced(self):
        """An existing ``=s512`` suffix is replaced — not stacked."""
        url = "https://lh3.googleusercontent.com/abc123=s512"
        upgraded = _upgrade_image_url(url)
        assert upgraded == f"https://lh3.googleusercontent.com/abc123=s{TARGET_SRC_SIZE}"
        # Sanity: no double suffix
        assert upgraded.count("=s") == 1

    def test_google_cdn_existing_wh_suffix_replaced(self):
        """An existing ``=w400-h400`` suffix is replaced by ``=s{TARGET_SRC_SIZE}``."""
        url = "https://lh3.googleusercontent.com/abc123=w400-h400"
        upgraded = _upgrade_image_url(url)
        assert upgraded == f"https://lh3.googleusercontent.com/abc123=s{TARGET_SRC_SIZE}"

    def test_google_cdn_any_subdomain_upgraded(self):
        """lh4/lh5/lh6 subdomains are also matched."""
        for sub in ("lh3", "lh4", "lh5", "lh6"):
            url = f"https://{sub}.googleusercontent.com/xyz"
            upgraded = _upgrade_image_url(url)
            assert upgraded == f"https://{sub}.googleusercontent.com/xyz=s{TARGET_SRC_SIZE}"

    def test_vtex_url_unchanged(self):
        """VTEX image URLs are left alone — VTEX serves max-res on plain URLs."""
        url = "https://farmaciasaas.vtexassets.com/arquivos/ids/212136/foo.jpg?v=1"
        assert _upgrade_image_url(url) == url

    def test_locatel_vtex_url_unchanged(self):
        """Locatel VTEX image URLs are unchanged."""
        url = "https://locatelvenezuela.vtexassets.com/arquivos/ids/171057/2091116.jpg"
        assert _upgrade_image_url(url) == url

    def test_unknown_host_unchanged(self):
        """Non-CDN URLs from unknown hosts are passed through untouched."""
        url = "https://example.com/some/path/image.jpg"
        assert _upgrade_image_url(url) == url

    def test_google_cdn_with_qualifier_suffix_replaced(self):
        """A qualified suffix like ``=s512-c-k`` is still matched and replaced."""
        url = "https://lh3.googleusercontent.com/abc123=s512-c-k"
        upgraded = _upgrade_image_url(url)
        assert upgraded == f"https://lh3.googleusercontent.com/abc123=s{TARGET_SRC_SIZE}"

    def test_google_cdn_query_string_preserved(self):
        """A URL with ``?query=...`` keeps its query string intact and valid."""
        url = "https://lh3.googleusercontent.com/abc123?foo=bar"
        upgraded = _upgrade_image_url(url)
        # Query string is preserved AFTER the size directive, producing
        # a valid URL with exactly one `?` and no mangled segments.
        assert upgraded.count("?") == 1
        assert "foo=bar" in upgraded
        assert f"=s{TARGET_SRC_SIZE}" in upgraded
        assert upgraded == (
            f"https://lh3.googleusercontent.com/abc123=s{TARGET_SRC_SIZE}?foo=bar"
        )

    def test_google_cdn_fragment_preserved(self):
        """A URL with ``#fragment`` keeps its fragment intact and valid."""
        url = "https://lh3.googleusercontent.com/abc123#section"
        upgraded = _upgrade_image_url(url)
        assert upgraded.count("#") == 1
        assert "section" in upgraded
        assert upgraded == (
            f"https://lh3.googleusercontent.com/abc123=s{TARGET_SRC_SIZE}#section"
        )

    def test_google_cdn_size_with_query_replaced(self):
        """A URL like ``=s512?foo=bar`` has the size replaced and query preserved."""
        url = "https://lh3.googleusercontent.com/abc123=s512?foo=bar"
        upgraded = _upgrade_image_url(url)
        assert upgraded == (
            f"https://lh3.googleusercontent.com/abc123=s{TARGET_SRC_SIZE}?foo=bar"
        )
        # No stacked or mangled directives
        assert upgraded.count("=s") == 1


# ── TestDownloadImageUpgradesUrl ────────────────────────────────────────────


class TestDownloadImageUpgradesUrl:
    """Verify _download_image routes URLs through _upgrade_image_url."""

    async def test_google_cdn_url_upgraded_before_fetch(self):
        """The actual HTTP GET is issued against the upgraded URL."""
        from unittest.mock import MagicMock

        from farmafacil.services import image_grid as grid_mod

        captured: dict[str, str] = {}

        class FakeResponse:
            # 1x1 red PNG
            content = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00"
                b"\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT"
                b"\x08\xd7c\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\xe2!\xbc3\x00"
                b"\x00\x00\x00IEND\xaeB`\x82"
            )

            def raise_for_status(self) -> None:
                return None

        class FakeClient:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc) -> None:
                return None

            async def get(self, url: str, **_kwargs):
                captured["url"] = url
                return FakeResponse()

        with patch.object(grid_mod.httpx, "AsyncClient", FakeClient):
            # Stub Image.open so we don't depend on the 1x1 PNG bytes surviving
            fake_img = MagicMock()
            fake_img.convert = MagicMock(return_value="OK")
            with patch.object(grid_mod.Image, "open", return_value=fake_img):
                result = await grid_mod._download_image(
                    "https://lh3.googleusercontent.com/abc123=s512"
                )

        assert result == "OK"
        assert captured["url"] == (
            f"https://lh3.googleusercontent.com/abc123=s{TARGET_SRC_SIZE}"
        )


# ── TestRenderCard ──────────────────────────────────────────────────────────


class TestRenderCard:
    """Unit tests for _render_card — resize + UnsharpMask pipeline."""

    def test_output_size_matches_image_size_constant(self):
        """Any source size is resized to exactly IMAGE_SIZE x IMAGE_SIZE."""
        from PIL import Image

        src = Image.new("RGBA", (300, 450), color=(0, 128, 255, 255))
        out = _render_card(src)
        assert out.size == (IMAGE_SIZE, IMAGE_SIZE)

    def test_large_source_is_downscaled_to_image_size(self):
        """A 2000x2000 source is downscaled — never larger than IMAGE_SIZE."""
        from PIL import Image

        src = Image.new("RGBA", (2000, 2000), color=(255, 0, 0, 255))
        out = _render_card(src)
        assert out.size == (IMAGE_SIZE, IMAGE_SIZE)

    def test_unsharp_mask_applied_to_output(self):
        """UnsharpMask filter is invoked as part of the render pipeline.

        We patch PIL.Image.Image.filter and confirm it's called with
        an UnsharpMask instance. This is the behavioural contract the
        rest of the grid depends on for perceived sharpness.
        """
        from PIL import Image, ImageFilter

        from farmafacil.services import image_grid as grid_mod

        src = Image.new("RGBA", (500, 500), color=(10, 20, 30, 255))

        called_with: list[object] = []
        original_filter = Image.Image.filter

        def spy_filter(self, f):
            called_with.append(f)
            return original_filter(self, f)

        with patch.object(grid_mod.Image.Image, "filter", spy_filter):
            grid_mod._render_card(src)

        assert len(called_with) == 1
        assert isinstance(called_with[0], ImageFilter.UnsharpMask)


# ── TestGridOutputSpec ──────────────────────────────────────────────────────


class TestGridOutputSpec:
    """Integration-ish tests confirming the output JPEG matches the v0.14.1 spec."""

    async def test_output_is_jpeg_with_canvas_width(self, tmp_path):
        """Output file is a valid JPEG at the CANVAS_WIDTH target resolution."""
        from PIL import Image

        fake_image = Image.new("RGBA", (800, 800), color=(128, 64, 200, 255))

        results = [
            make_result("Drug A", "Farmatodo", image_url="https://example.com/a.jpg"),
        ]
        with patch(
            "farmafacil.services.image_grid._download_image",
            new=AsyncMock(return_value=fake_image),
        ):
            out_path = await generate_product_grid(results)

        assert out_path is not None
        assert out_path.endswith(".jpg")

        out = Image.open(out_path)
        assert out.format == "JPEG"
        assert out.width == CANVAS_WIDTH
        # Height = 2*margin + 1*image + 0*gap for a single card
        assert out.height >= IMAGE_SIZE

    async def test_multi_card_height_matches_formula(self):
        """For N cards, canvas height = 2*margin + n*image + (n-1)*gap."""
        from PIL import Image

        from farmafacil.services.image_grid import CARD_GAP, CARD_MARGIN

        fake_image = Image.new("RGBA", (600, 600), color=(200, 200, 50, 255))

        results = [
            make_result(
                f"Drug {i}", "Farmatodo", image_url=f"https://example.com/{i}.jpg"
            )
            for i in range(3)
        ]
        with patch(
            "farmafacil.services.image_grid._download_image",
            new=AsyncMock(return_value=fake_image),
        ):
            out_path = await generate_product_grid(results, max_products=3)

        out = Image.open(out_path)
        expected_h = 2 * CARD_MARGIN + 3 * IMAGE_SIZE + 2 * CARD_GAP
        assert out.height == expected_h
        assert out.width == CANVAS_WIDTH

    async def test_max_cards_output_under_whatsapp_limit(self):
        """8-card worst-case output stays under the WhatsApp 5 MB limit.

        Uses a real noisy source image (random-looking packaging-style
        content) to stress JPEG compression, not a flat solid color
        which would compress unrealistically well.
        """
        from PIL import Image

        from farmafacil.services.image_grid import WHATSAPP_MAX_BYTES

        # Build a high-entropy source so JPEG can't trivially compress it.
        # Use a PIL-native noise pattern: random-ish gradient blocks.
        src = Image.new("RGB", (1200, 1200))
        pixels = src.load()
        for y in range(1200):
            for x in range(0, 1200, 8):  # sparse to keep test fast
                pixels[x, y] = ((x * 7 + y * 13) % 256, (x * 11) % 256, (y * 17) % 256)
        src = src.convert("RGBA")

        results = [
            make_result(
                f"Drug {i}", "Farmatodo", image_url=f"https://example.com/{i}.jpg"
            )
            for i in range(8)
        ]
        with patch(
            "farmafacil.services.image_grid._download_image",
            new=AsyncMock(return_value=src),
        ):
            out_path = await generate_product_grid(results, max_products=8)

        assert out_path is not None
        size = os.path.getsize(out_path)
        assert size <= WHATSAPP_MAX_BYTES, (
            f"8-card output {size} bytes exceeds WhatsApp budget "
            f"{WHATSAPP_MAX_BYTES}"
        )

    async def test_quality_ladder_falls_back_when_over_budget(self):
        """When the first tier blows the budget, the next tier is tried."""
        from PIL import Image

        from farmafacil.services import image_grid as grid_mod

        fake_image = Image.new("RGBA", (500, 500), color=(123, 45, 67, 255))
        results = [
            make_result("Drug A", "Farmatodo", image_url="https://example.com/a.jpg"),
        ]

        # Force the first tier to "fail" the budget by setting the limit
        # to 1 byte. The ladder should walk through every tier and take
        # the final one.
        with patch.object(grid_mod, "WHATSAPP_MAX_BYTES", 1):
            with patch(
                "farmafacil.services.image_grid._download_image",
                new=AsyncMock(return_value=fake_image),
            ):
                out_path = await generate_product_grid(results)

        assert out_path is not None
        assert out_path.endswith(".jpg")
        # Output is still a valid JPEG even after walking the whole ladder
        out = Image.open(out_path)
        assert out.format == "JPEG"
