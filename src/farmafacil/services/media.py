"""WhatsApp media download and processing.

Handles downloading images and documents from WhatsApp Cloud API,
encoding images for Claude Vision, and extracting text from PDFs/DOCX.

v0.45.0 (Item 124): Added image preprocessing — HEIC/HEIF→JPEG
conversion (iPhone/Samsung photos), auto-resize large photos to
1568px (Anthropic Vision optimal), EXIF orientation correction.
"""

import base64
import io
import logging

import httpx
from PIL import Image

from farmafacil.config import WHATSAPP_API_TOKEN

logger = logging.getLogger(__name__)

# Register HEIC/HEIF opener so Pillow can read iPhone photos.
# Graceful: if pillow-heif is not installed, HEIC files simply won't open.
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    _HEIF_AVAILABLE = True
except ImportError:  # pragma: no cover
    _HEIF_AVAILABLE = False

# WhatsApp Cloud API media endpoint
_GRAPH_BASE = "https://graph.facebook.com/v22.0"

# Supported image types for Claude Vision (Anthropic-native formats).
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# Additional types we accept and convert to JPEG before Vision.
_CONVERTIBLE_IMAGE_TYPES = {"image/heic", "image/heif", "image/avif"}

# Union of all image types we can handle (native + convertible).
ALL_IMAGE_TYPES = SUPPORTED_IMAGE_TYPES | _CONVERTIBLE_IMAGE_TYPES

# Max image size (bytes) — WhatsApp compresses images but we cap at 10MB
MAX_IMAGE_BYTES = 10 * 1024 * 1024

# Anthropic Vision optimal max dimension — larger images are auto-resized
# by the API but cost more tokens in transit.  Pre-resizing saves bandwidth
# and token budget (~4× token reduction for a 4032px → 1568px photo).
_VISION_MAX_DIMENSION = 1568

# JPEG quality for pre-processed images (resize / HEIC conversion).
_JPEG_QUALITY = 85

# ── Module-level httpx client singleton ─────────────────────────────────
# A single AsyncClient reuses the underlying connection pool for all
# WhatsApp media download calls, avoiding per-download TLS handshakes to
# graph.facebook.com.
# (Item 78, v0.25.0 — was creating a new client per media download.)
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return the module-level async httpx client, creating it lazily."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


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
        client = _get_http_client()

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


def _preprocess_image(data: bytes, mime_type: str) -> tuple[bytes, str] | None:
    """Preprocess an image for Claude Vision: convert, resize, orient.

    Handles:
    - HEIC/HEIF/AVIF → JPEG conversion (iPhone/Samsung photos).
    - Large images → resize to ``_VISION_MAX_DIMENSION`` px longest side.
    - EXIF orientation → apply rotation so the image is upright.
    - PNG/WebP with transparency → composited on white background.

    Returns:
        ``(processed_bytes, final_mime_type)`` or ``None`` on error.
        If the image is already small enough and in a native format,
        returns the original bytes unchanged (zero-copy fast path).
    """
    needs_conversion = mime_type in _CONVERTIBLE_IMAGE_TYPES
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as exc:
        logger.warning("Cannot open image (%s): %s", mime_type, exc)
        return None

    # Apply EXIF orientation (phone photos often have rotation metadata).
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass  # No EXIF or rotation fails — continue with original

    w, h = img.size
    longest = max(w, h)
    needs_resize = longest > _VISION_MAX_DIMENSION

    # Fast path: native format, small enough, no conversion needed.
    if not needs_conversion and not needs_resize:
        return data, mime_type

    # Resize if needed (maintain aspect ratio).
    if needs_resize:
        ratio = _VISION_MAX_DIMENSION / longest
        new_w = int(w * ratio)
        new_h = int(h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        logger.info(
            "Resized image %dx%d → %dx%d for Vision",
            w, h, new_w, new_h,
        )

    # Convert to RGB (handles RGBA, palette, HEIC color spaces).
    if img.mode in ("RGBA", "P", "LA"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Encode as JPEG.
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    processed = buf.getvalue()

    logger.info(
        "Preprocessed image: %s %d bytes → JPEG %d bytes",
        mime_type, len(data), len(processed),
    )
    return processed, "image/jpeg"


def encode_image_for_vision(
    data: bytes, mime_type: str,
) -> dict | None:
    """Encode image bytes as an Anthropic Vision content block.

    Handles HEIC/HEIF/AVIF conversion and auto-resizes large phone
    photos (> 1568px) to save Vision API tokens.

    Args:
        data: Raw image bytes.
        mime_type: MIME type (must be in ``ALL_IMAGE_TYPES``).

    Returns:
        Anthropic image content block dict, or None if unsupported/too large.
    """
    if mime_type not in ALL_IMAGE_TYPES:
        logger.warning("Unsupported image type for Vision: %s", mime_type)
        return None

    if len(data) > MAX_IMAGE_BYTES:
        logger.warning("Image too large for Vision: %d bytes", len(data))
        return None

    # Preprocess: HEIC→JPEG, resize, EXIF orient.
    result = _preprocess_image(data, mime_type)
    if result is None:
        return None
    processed_data, final_mime = result

    b64 = base64.standard_b64encode(processed_data).decode("ascii")

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": final_mime,
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
