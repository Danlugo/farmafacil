"""Tests for services/media.py — WhatsApp media download and processing.

Covers: download_whatsapp_media (two-step flow, HTTP errors, network errors),
encode_image_for_vision (types, size, encoding), extract_text_from_document
(PDF, DOCX, unsupported type, import failures).
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from farmafacil.services.media import (
    MAX_IMAGE_BYTES,
    SUPPORTED_IMAGE_TYPES,
    _get_http_client,
    download_whatsapp_media,
    encode_image_for_vision,
    extract_text_from_document,
)


# ── download_whatsapp_media ────────────────────────────────────────────


class TestDownloadWhatsappMedia:
    """Test the two-step media download flow."""

    @pytest.mark.asyncio
    async def test_successful_download(self):
        """Two-step download: metadata → file bytes."""
        meta_response = MagicMock()
        meta_response.raise_for_status = MagicMock()
        meta_response.json.return_value = {
            "url": "https://media.whatsapp.net/file123",
            "mime_type": "image/jpeg",
        }

        file_response = MagicMock()
        file_response.raise_for_status = MagicMock()
        file_response.content = b"\xff\xd8\xff\xe0fake-jpeg"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[meta_response, file_response])

        with patch("farmafacil.services.media._get_http_client", return_value=mock_client):
            result = await download_whatsapp_media("media123")

        assert result is not None
        data, mime = result
        assert data == b"\xff\xd8\xff\xe0fake-jpeg"
        assert mime == "image/jpeg"

    @pytest.mark.asyncio
    async def test_missing_url_returns_none(self):
        """If metadata has no download URL, returns None."""
        meta_response = MagicMock()
        meta_response.raise_for_status = MagicMock()
        meta_response.json.return_value = {"mime_type": "image/png"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=meta_response)

        with patch("farmafacil.services.media._get_http_client", return_value=mock_client):
            result = await download_whatsapp_media("media_no_url")

        assert result is None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        """HTTP errors return None without crashing."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=mock_response,
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("farmafacil.services.media._get_http_client", return_value=mock_client):
            result = await download_whatsapp_media("missing_media")

        assert result is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        """Network failures return None."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.RequestError("timeout"))

        with patch("farmafacil.services.media._get_http_client", return_value=mock_client):
            result = await download_whatsapp_media("timeout_media")

        assert result is None

    @pytest.mark.asyncio
    async def test_default_mime_type(self):
        """Missing mime_type defaults to application/octet-stream."""
        meta_response = MagicMock()
        meta_response.raise_for_status = MagicMock()
        meta_response.json.return_value = {"url": "https://media.whatsapp.net/x"}

        file_response = MagicMock()
        file_response.raise_for_status = MagicMock()
        file_response.content = b"data"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[meta_response, file_response])

        with patch("farmafacil.services.media._get_http_client", return_value=mock_client):
            result = await download_whatsapp_media("no_mime")

        assert result is not None
        _, mime = result
        assert mime == "application/octet-stream"


# ── encode_image_for_vision ────────────────────────────────────────────


class TestEncodeImageForVision:
    """Test Anthropic Vision block encoding."""

    def test_jpeg_encoding(self):
        data = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        result = encode_image_for_vision(data, "image/jpeg")
        assert result is not None
        assert result["type"] == "image"
        assert result["source"]["media_type"] == "image/jpeg"
        # Verify the base64 round-trips
        decoded = base64.standard_b64decode(result["source"]["data"])
        assert decoded == data

    def test_png_encoding(self):
        data = b"\x89PNG" + b"\x00" * 50
        result = encode_image_for_vision(data, "image/png")
        assert result is not None
        assert result["source"]["media_type"] == "image/png"

    def test_webp_encoding(self):
        data = b"RIFF" + b"\x00" * 50
        result = encode_image_for_vision(data, "image/webp")
        assert result is not None

    def test_gif_encoding(self):
        data = b"GIF89a" + b"\x00" * 50
        result = encode_image_for_vision(data, "image/gif")
        assert result is not None

    def test_unsupported_type_returns_none(self):
        result = encode_image_for_vision(b"data", "image/bmp")
        assert result is None

    def test_oversized_image_returns_none(self):
        big_data = b"\x00" * (MAX_IMAGE_BYTES + 1)
        result = encode_image_for_vision(big_data, "image/jpeg")
        assert result is None

    def test_exactly_max_size_accepted(self):
        data = b"\x00" * MAX_IMAGE_BYTES
        result = encode_image_for_vision(data, "image/jpeg")
        assert result is not None


# ── extract_text_from_document ─────────────────────────────────────────


class TestExtractTextFromDocument:
    """Test document text extraction (PDF and DOCX)."""

    @pytest.mark.asyncio
    async def test_pdf_extraction(self):
        """Mock fitz (pymupdf) to return text from a fake PDF."""
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Losartan 50mg\nPrecio: Bs 25.00"

        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
        mock_doc.close = MagicMock()

        with patch.dict("sys.modules", {"fitz": MagicMock()}) as mock_modules:
            mock_modules["fitz"].open.return_value = mock_doc
            # Re-import to pick up the mock
            import importlib
            import farmafacil.services.media as media_mod
            result = await media_mod.extract_text_from_document(
                b"fake-pdf", "application/pdf",
            )

        # Result should contain the extracted text
        assert result is not None
        assert "Losartan" in result

    @pytest.mark.asyncio
    async def test_unsupported_type_returns_none(self):
        result = await extract_text_from_document(b"data", "text/plain")
        assert result is None

    @pytest.mark.asyncio
    async def test_extraction_exception_returns_none(self):
        """If extraction crashes, returns None rather than raising."""
        with patch(
            "farmafacil.services.media._extract_pdf_text",
            side_effect=RuntimeError("corrupt PDF"),
        ):
            result = await extract_text_from_document(
                b"corrupt", "application/pdf",
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_docx_mime_type_accepted(self):
        """DOCX mime type is recognized and dispatched."""
        with patch(
            "farmafacil.services.media._extract_docx_text",
            return_value="Document content",
        ):
            result = await extract_text_from_document(
                b"fake-docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        assert result == "Document content"

    @pytest.mark.asyncio
    async def test_msword_mime_type_accepted(self):
        """Legacy .doc mime type is also dispatched."""
        with patch(
            "farmafacil.services.media._extract_docx_text",
            return_value="Old doc content",
        ):
            result = await extract_text_from_document(
                b"fake-doc", "application/msword",
            )
        assert result == "Old doc content"


# ── HTTP client singleton ──────────────────────────────────────────────


class TestHttpClientSingleton:
    """Verify the module-level client singleton pattern."""

    def test_returns_async_client(self):
        import farmafacil.services.media as mod
        old = mod._http_client
        try:
            mod._http_client = None
            client = _get_http_client()
            assert isinstance(client, httpx.AsyncClient)
        finally:
            mod._http_client = old

    def test_returns_same_instance(self):
        import farmafacil.services.media as mod
        old = mod._http_client
        try:
            mod._http_client = None
            c1 = _get_http_client()
            c2 = _get_http_client()
            assert c1 is c2
        finally:
            mod._http_client = old


# ── Constants ──────────────────────────────────────────────────────────


class TestMediaConstants:
    """Verify module constants are sensible."""

    def test_supported_image_types(self):
        assert "image/jpeg" in SUPPORTED_IMAGE_TYPES
        assert "image/png" in SUPPORTED_IMAGE_TYPES

    def test_max_image_bytes_is_10mb(self):
        assert MAX_IMAGE_BYTES == 10 * 1024 * 1024
