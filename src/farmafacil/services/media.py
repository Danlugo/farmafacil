"""WhatsApp media download and processing.

Handles downloading images and documents from WhatsApp Cloud API,
encoding images for Claude Vision, and extracting text from PDFs/DOCX.
"""

import base64
import io
import logging

import httpx

from farmafacil.config import WHATSAPP_API_TOKEN

logger = logging.getLogger(__name__)

# WhatsApp Cloud API media endpoint
_GRAPH_BASE = "https://graph.facebook.com/v22.0"

# Supported image types for Claude Vision
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# Max image size (bytes) — WhatsApp compresses images but we cap at 10MB
MAX_IMAGE_BYTES = 10 * 1024 * 1024


async def download_whatsapp_media(media_id: str) -> tuple[bytes, str] | None:
    """Download media from WhatsApp Cloud API.

    Two-step process:
    1. GET /{media_id} → returns JSON with download URL
    2. GET {url} with auth → returns file bytes

    Args:
        media_id: WhatsApp media ID from the webhook payload.

    Returns:
        Tuple of (file_bytes, mime_type) or None on failure.
    """
    headers = {"Authorization": f"Bearer {WHATSAPP_API_TOKEN}"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1: Get download URL
            meta_resp = await client.get(
                f"{_GRAPH_BASE}/{media_id}",
                headers=headers,
            )
            meta_resp.raise_for_status()
            meta = meta_resp.json()
            url = meta.get("url")
            mime_type = meta.get("mime_type", "application/octet-stream")

            if not url:
                logger.error("No download URL in media metadata for %s", media_id)
                return None

            # Step 2: Download the file
            file_resp = await client.get(url, headers=headers)
            file_resp.raise_for_status()
            data = file_resp.content

            logger.info(
                "Downloaded media %s: %s, %d bytes",
                media_id, mime_type, len(data),
            )
            return data, mime_type

    except httpx.HTTPStatusError as exc:
        logger.error(
            "Media download HTTP error for %s: %s",
            media_id, exc.response.status_code,
        )
    except httpx.RequestError as exc:
        logger.error("Media download network error for %s: %s", media_id, exc)

    return None


def encode_image_for_vision(
    data: bytes, mime_type: str,
) -> dict | None:
    """Encode image bytes as an Anthropic Vision content block.

    Args:
        data: Raw image bytes.
        mime_type: MIME type (must be in SUPPORTED_IMAGE_TYPES).

    Returns:
        Anthropic image content block dict, or None if unsupported/too large.
    """
    if mime_type not in SUPPORTED_IMAGE_TYPES:
        logger.warning("Unsupported image type for Vision: %s", mime_type)
        return None

    if len(data) > MAX_IMAGE_BYTES:
        logger.warning("Image too large for Vision: %d bytes", len(data))
        return None

    b64 = base64.standard_b64encode(data).decode("ascii")

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": mime_type,
            "data": b64,
        },
    }


async def extract_text_from_document(
    data: bytes, mime_type: str,
) -> str | None:
    """Extract text content from a PDF or DOCX document.

    Args:
        data: Raw document bytes.
        mime_type: MIME type of the document.

    Returns:
        Extracted text or None if unsupported/failed.
    """
    try:
        if mime_type == "application/pdf":
            return _extract_pdf_text(data)
        if mime_type in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        ):
            return _extract_docx_text(data)
    except Exception as exc:
        logger.error("Document text extraction failed (%s): %s", mime_type, exc)

    return None


def _extract_pdf_text(data: bytes) -> str | None:
    """Extract text from PDF bytes using pymupdf (fitz)."""
    try:
        import fitz  # pymupdf
    except ImportError:
        logger.warning("pymupdf not installed — cannot extract PDF text")
        return None

    try:
        doc = fitz.open(stream=data, filetype="pdf")
        pages = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        text = "\n\n".join(pages).strip()
        # Cap at 50k chars to avoid blowing up the context
        if len(text) > 50_000:
            text = text[:50_000] + "\n\n[... truncado a 50,000 caracteres]"
        return text or None
    except Exception as exc:
        logger.error("PDF extraction error: %s", exc)
        return None


def _extract_docx_text(data: bytes) -> str | None:
    """Extract text from DOCX bytes using python-docx."""
    try:
        from docx import Document
    except ImportError:
        logger.warning("python-docx not installed — cannot extract DOCX text")
        return None

    try:
        doc = Document(io.BytesIO(data))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n\n".join(paragraphs).strip()
        if len(text) > 50_000:
            text = text[:50_000] + "\n\n[... truncado a 50,000 caracteres]"
        return text or None
    except Exception as exc:
        logger.error("DOCX extraction error: %s", exc)
        return None
