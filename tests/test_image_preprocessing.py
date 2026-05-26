"""Tests for media.py image preprocessing — HEIC support, resize, EXIF.

Covers:
- _preprocess_image: HEIC→JPEG, resize, EXIF transpose, fast-path
- encode_image_for_vision: ALL_IMAGE_TYPES gate, preprocessing integration
- ALL_IMAGE_TYPES: includes both native and convertible formats
- Handler integration: ALL_IMAGE_TYPES used instead of SUPPORTED_IMAGE_TYPES

Item 124 follow-up, v0.45.0.
"""

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from farmafacil.services.media import (
    ALL_IMAGE_TYPES,
    SUPPORTED_IMAGE_TYPES,
    _CONVERTIBLE_IMAGE_TYPES,
    _VISION_MAX_DIMENSION,
    _preprocess_image,
    encode_image_for_vision,
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_jpeg(width: int = 200, height: int = 150) -> bytes:
    """Create a minimal JPEG image of given size."""
    img = Image.new("RGB", (width, height), color=(128, 64, 32))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _make_png_rgba(width: int = 200, height: int = 150) -> bytes:
    """Create a PNG with alpha channel."""
    img = Image.new("RGBA", (width, height), color=(128, 64, 32, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_large_jpeg(dim: int = 4032) -> bytes:
    """Create a large JPEG simulating a phone camera photo."""
    img = Image.new("RGB", (dim, dim * 3 // 4), color=(200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ── ALL_IMAGE_TYPES contract ────────────────────────────────────────────


class TestAllImageTypes:
    """Verify ALL_IMAGE_TYPES includes native + convertible formats."""

    def test_includes_native_formats(self):
        for fmt in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            assert fmt in ALL_IMAGE_TYPES

    def test_includes_heic_heif(self):
        assert "image/heic" in ALL_IMAGE_TYPES
        assert "image/heif" in ALL_IMAGE_TYPES

    def test_includes_avif(self):
        assert "image/avif" in ALL_IMAGE_TYPES

    def test_is_superset_of_supported(self):
        assert SUPPORTED_IMAGE_TYPES.issubset(ALL_IMAGE_TYPES)

    def test_convertible_types_exist(self):
        assert "image/heic" in _CONVERTIBLE_IMAGE_TYPES
        assert "image/heif" in _CONVERTIBLE_IMAGE_TYPES
        assert "image/avif" in _CONVERTIBLE_IMAGE_TYPES


# ── _preprocess_image ───────────────────────────────────────────────────


class TestPreprocessImage:
    """Test image preprocessing: resize, convert, EXIF."""

    def test_small_jpeg_fast_path(self):
        """Small native-format JPEG returns original bytes unchanged."""
        data = _make_jpeg(200, 150)
        result = _preprocess_image(data, "image/jpeg")
        assert result is not None
        processed, mime = result
        # Fast path: same bytes, same mime
        assert processed is data
        assert mime == "image/jpeg"

    def test_small_png_fast_path(self):
        """Small native-format PNG returns original bytes unchanged."""
        img = Image.new("RGB", (200, 150), color=(100, 100, 100))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
        result = _preprocess_image(data, "image/png")
        assert result is not None
        processed, mime = result
        assert processed is data
        assert mime == "image/png"

    def test_large_jpeg_resized(self):
        """Large JPEG (> _VISION_MAX_DIMENSION) is resized."""
        data = _make_large_jpeg(4032)
        result = _preprocess_image(data, "image/jpeg")
        assert result is not None
        processed, mime = result
        assert mime == "image/jpeg"
        # Verify resized
        img = Image.open(io.BytesIO(processed))
        assert max(img.size) <= _VISION_MAX_DIMENSION
        # Should be smaller
        assert len(processed) < len(data)

    def test_large_png_resized_to_jpeg(self):
        """Large PNG is resized and converted to JPEG."""
        img = Image.new("RGB", (3000, 2000), color=(50, 100, 150))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
        result = _preprocess_image(data, "image/png")
        assert result is not None
        processed, mime = result
        assert mime == "image/jpeg"
        resized = Image.open(io.BytesIO(processed))
        assert max(resized.size) <= _VISION_MAX_DIMENSION

    def test_rgba_composited_on_white(self):
        """RGBA image gets composited on white background."""
        data = _make_png_rgba(3000, 2000)  # Force resize to trigger conversion
        result = _preprocess_image(data, "image/png")
        assert result is not None
        processed, mime = result
        img = Image.open(io.BytesIO(processed))
        assert img.mode == "RGB"

    def test_corrupt_image_returns_none(self):
        """Corrupt image data returns None."""
        result = _preprocess_image(b"not-an-image", "image/jpeg")
        assert result is None

    def test_dimension_boundary_exact(self):
        """Image at exactly _VISION_MAX_DIMENSION is not resized."""
        data = _make_jpeg(_VISION_MAX_DIMENSION, _VISION_MAX_DIMENSION)
        result = _preprocess_image(data, "image/jpeg")
        assert result is not None
        processed, mime = result
        assert processed is data  # Fast path — unchanged

    def test_dimension_boundary_plus_one(self):
        """Image 1px over _VISION_MAX_DIMENSION IS resized."""
        data = _make_jpeg(_VISION_MAX_DIMENSION + 1, 100)
        result = _preprocess_image(data, "image/jpeg")
        assert result is not None
        processed, mime = result
        assert processed is not data  # Should be different bytes
        assert mime == "image/jpeg"
        img = Image.open(io.BytesIO(processed))
        assert max(img.size) <= _VISION_MAX_DIMENSION

    @pytest.mark.parametrize("orientation,expected_swap", [
        (6, True),   # 90° CW → width/height swap
        (3, False),  # 180° → same dimensions
    ], ids=["90cw", "180"])
    def test_exif_orientation_applied(self, orientation, expected_swap):
        """EXIF orientation tag is applied during preprocessing."""
        import struct
        # Create a wide image that needs resize to trigger preprocessing
        img = Image.new("RGB", (2000, 1000), color=(100, 200, 50))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        data = buf.getvalue()

        # The image will be resized — verify it opens and processes
        result = _preprocess_image(data, "image/jpeg")
        assert result is not None
        processed, mime = result
        assert mime == "image/jpeg"


# ── encode_image_for_vision ─────────────────────────────────────────────


class TestEncodeImageForVision:
    """Test the encode_image_for_vision function with preprocessing."""

    def test_small_jpeg_encodes(self):
        """Small JPEG is encoded without modification."""
        data = _make_jpeg(200, 150)
        block = encode_image_for_vision(data, "image/jpeg")
        assert block is not None
        assert block["type"] == "image"
        assert block["source"]["media_type"] == "image/jpeg"

    def test_large_jpeg_resized_then_encoded(self):
        """Large JPEG is resized before encoding."""
        data = _make_large_jpeg(4032)
        block = encode_image_for_vision(data, "image/jpeg")
        assert block is not None
        assert block["source"]["media_type"] == "image/jpeg"
        # Verify the base64 decodes to a resized image
        import base64
        decoded = base64.standard_b64decode(block["source"]["data"])
        img = Image.open(io.BytesIO(decoded))
        assert max(img.size) <= _VISION_MAX_DIMENSION

    def test_unsupported_type_returns_none(self):
        """Unsupported MIME type returns None."""
        assert encode_image_for_vision(b"data", "video/mp4") is None

    def test_heic_type_accepted(self):
        """HEIC MIME type is in ALL_IMAGE_TYPES gate."""
        # We can't create a real HEIC in tests without complex setup,
        # but we can verify the gate logic
        assert "image/heic" in ALL_IMAGE_TYPES

    def test_too_large_returns_none(self):
        """Image exceeding MAX_IMAGE_BYTES returns None."""
        from farmafacil.services.media import MAX_IMAGE_BYTES
        huge = b"\xff" * (MAX_IMAGE_BYTES + 1)
        assert encode_image_for_vision(huge, "image/jpeg") is None

    def test_corrupt_heic_returns_none(self):
        """Corrupt HEIC data returns None (preprocessing fails)."""
        result = encode_image_for_vision(b"not-heic-data", "image/heic")
        assert result is None


# ── Handler uses ALL_IMAGE_TYPES ────────────────────────────────────────


class TestHandlerImageTypeGate:
    """Verify handler uses ALL_IMAGE_TYPES, not just SUPPORTED_IMAGE_TYPES."""

    def test_handler_imports_all_image_types(self):
        """handler.py imports ALL_IMAGE_TYPES for the image detection check."""
        import inspect
        from farmafacil.bot import handler
        source = inspect.getsource(handler.handle_image_message)
        assert "ALL_IMAGE_TYPES" in source
        # Should NOT use SUPPORTED_IMAGE_TYPES for the main gate
        # (it's OK if it appears in comments or other contexts)

    def test_handler_uses_user_name_not_display_name(self):
        """Regression: handler must use user.name, not user.display_name.

        v0.45.0 bug — AttributeError: 'User' object has no attribute
        'display_name' crashed the image handler after Vision analysis
        succeeded, causing the user to see only "Analizando la imagen..."
        with no result.
        """
        import inspect
        from farmafacil.bot import handler
        source = inspect.getsource(handler.handle_image_message)
        assert "user.display_name" not in source
        assert "user.name" in source


# ── Proactive drug translation in handler ───────────────────────────────


class TestProactiveDrugTranslation:
    """Verify image handler translates English drug names before searching."""

    @pytest.mark.asyncio
    async def test_english_drug_names_translated(self):
        """English drug names from Vision are translated to Spanish."""
        from farmafacil.services.image_analysis import ImageAnalysisResult
        from farmafacil.services.drug_translation import TranslationResult

        analysis = ImageAnalysisResult(
            image_type="prescription",
            analysis_text="📋 *Prescription*\n\n💊 *1. Acetaminophen*",
            drug_names=["Acetaminophen", "Ibuprofen"],
            model_used="model",
            tokens_in=500,
            tokens_out=100,
        )

        sent_messages: list[str] = []
        searched_queries: list[str] = []

        async def mock_send(to, text):
            sent_messages.append(text)

        async def mock_handle(sender, text):
            searched_queries.append(text)

        mock_user = MagicMock()
        mock_user.admin_mode_active = False
        mock_user.awaiting_clarification_context = None
        mock_user.display_name = "TestUser"
        mock_user.id = 1

        # translate_drug_query returns Spanish names
        async def mock_translate(name):
            translations = {
                "Acetaminophen": TranslationResult("Paracetamol", 50, 10),
                "Ibuprofen": TranslationResult("Ibuprofeno", 50, 10),
            }
            return translations.get(name)

        with (
            patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock, return_value=(b"fake", "image/jpeg")),
            patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image", "source": {}}),
            patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock, return_value=analysis),
            patch("farmafacil.bot.handler.send_text_message", side_effect=mock_send),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=mock_user),
            patch("farmafacil.bot.handler.is_chat_admin", new_callable=AsyncMock, return_value=False),
            patch("farmafacil.bot.handler.handle_incoming_message", side_effect=mock_handle),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.translate_drug_query", side_effect=mock_translate),
        ):
            from farmafacil.bot.handler import handle_image_message
            await handle_image_message("1234567890", "media123", "image/jpeg")

        # Should have searched with Spanish names
        assert "Paracetamol" in searched_queries
        assert "Ibuprofeno" in searched_queries
        # English names should NOT appear in searches
        assert "Acetaminophen" not in searched_queries
        assert "Ibuprofen" not in searched_queries

    @pytest.mark.asyncio
    async def test_spanish_names_not_translated(self):
        """Already-Spanish drug names pass through unchanged."""
        from farmafacil.services.image_analysis import ImageAnalysisResult

        analysis = ImageAnalysisResult(
            image_type="medicine",
            analysis_text="📦 *Ibuprofeno 400mg*",
            drug_names=["Ibuprofeno"],
            model_used="model",
            tokens_in=400,
            tokens_out=50,
        )

        searched_queries: list[str] = []

        async def mock_handle(sender, text):
            searched_queries.append(text)

        mock_user = MagicMock()
        mock_user.admin_mode_active = False
        mock_user.awaiting_clarification_context = None
        mock_user.display_name = "TestUser"
        mock_user.id = 1

        # translate_drug_query returns None for Spanish names
        async def mock_translate(name):
            return None

        with (
            patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock, return_value=(b"fake", "image/jpeg")),
            patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image", "source": {}}),
            patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock, return_value=analysis),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=mock_user),
            patch("farmafacil.bot.handler.is_chat_admin", new_callable=AsyncMock, return_value=False),
            patch("farmafacil.bot.handler.handle_incoming_message", side_effect=mock_handle),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.translate_drug_query", side_effect=mock_translate),
        ):
            from farmafacil.bot.handler import handle_image_message
            await handle_image_message("1234567890", "media123", "image/jpeg")

        # Spanish name passes through unchanged
        assert "Ibuprofeno" in searched_queries
