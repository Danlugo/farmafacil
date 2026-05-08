"""Generate product images for WhatsApp — clean image-only cards.

v0.14.1 — sharpness overhaul (Item 36)
======================================
The grid used to be soft/blurry on phones because:

1. Canvas was 500 px wide / cards 460 px — on a modern phone WhatsApp
   scales that up to the display width so the user saw a ~2x upscale
   applied by the phone itself (perceived blur).
2. Farmatodo product photos come from ``lh3.googleusercontent.com``
   and default to 512 px — that was upscaled to 460 (tight, acceptable)
   but with ZERO margin left for WhatsApp's own recompression pass.
3. Saved as PNG, which WhatsApp re-encodes to JPEG anyway.

Fix:
- Canvas → 1080 px wide, cards → 1000 px (matches what VTEX natively
  serves — no upscaling from the source).
- ``_upgrade_image_url`` rewrites Google-CDN URLs to request ``=s1200``
  (1200 px) instead of the default thumbnail so Farmatodo images arrive
  at a real high-res size.
- Mild ``UnsharpMask`` after LANCZOS resize to compensate for WhatsApp's
  lossy JPEG recompression.
- Save as JPEG quality 92 with ``subsampling=0`` (full chroma) — smaller
  file than PNG for photos and what WhatsApp will transcode to anyway.
"""

import io
import logging
import os
import re
import tempfile

import httpx
from PIL import Image, ImageDraw, ImageFilter, UnidentifiedImageError

from farmafacil.bot.formatter import _group_by_product
from farmafacil.models.schemas import DrugResult

logger = logging.getLogger(__name__)

# Layout: high-DPI canvas, stacked product images only.
# These sizes target ~1080 px phone displays so WhatsApp shows the image
# at its natural resolution instead of upscaling a small canvas.
CANVAS_WIDTH = 1080
IMAGE_SIZE = 1000
CARD_MARGIN = 40
CARD_GAP = 24
CARD_RADIUS = 24

# JPEG output — WhatsApp re-encodes to JPEG on ingest, so sending JPEG
# directly gives the best fidelity at the smallest file size. We start
# at q=92 with full chroma (subsampling=0) for maximum sharpness, then
# fall back to smaller settings if the output exceeds the WhatsApp 5 MB
# image limit. Fallback ladder is evaluated in order; the first save
# under ``WHATSAPP_MAX_BYTES`` wins.
JPEG_QUALITY_LADDER: tuple[tuple[int, int], ...] = (
    (92, 0),   # q=92, subsampling=0 (4:4:4, no chroma loss) — ideal
    (88, 1),   # q=88, subsampling=1 (4:2:2) — small file size drop
    (85, 2),   # q=85, subsampling=2 (4:2:0) — standard web JPEG
    (78, 2),   # last resort — still looks OK, ~3-4x smaller than tier 0
)
# WhatsApp Cloud API hard limit for ``type: image`` is 5 MB. Budget with
# a safety margin so we never hit the rejection.
WHATSAPP_MAX_BYTES = 4_500_000

# Target source resolution for URL upgrades. 1200 px is a comfortable
# margin above the 1000 px card size so LANCZOS has real pixels to work
# with instead of guessing.
TARGET_SRC_SIZE = 1200

# Colors
BG_COLOR = (245, 245, 245)
BORDER_COLOR = (224, 224, 224)

# Google CDN (lh3/lh4/lh5/lh6.googleusercontent.com) serves sized variants
# via a trailing ``=s{size}`` or ``=w{w}-h{h}`` path segment. The regex
# matches an existing size suffix (optional leading ``=``) so we can swap
# it for ``=s{TARGET_SRC_SIZE}`` without stacking directives.
_GOOGLE_CDN_SIZE_RE = re.compile(r"=(?:s\d+|w\d+-h\d+)(?:-[a-zA-Z0-9]+)*$")
_GOOGLE_CDN_HOST_RE = re.compile(r"^https?://lh\d+\.googleusercontent\.com/")


def _upgrade_image_url(url: str | None) -> str | None:
    """Rewrite known image-CDN URLs to request a high-resolution variant.

    Currently handles:
    - ``lh*.googleusercontent.com`` (Farmatodo product photos) — appends
      or replaces the ``=s{size}`` suffix with ``=s{TARGET_SRC_SIZE}``.
    - ``*.vtexassets.com`` (Farmacias SAAS + Locatel) — left as-is, VTEX
      already serves the full original (1000–1080 px) when no ``-WxH``
      suffix is present.

    Unknown hosts and empty/None input are returned unchanged so the
    helper is always safe to call. Query strings and fragment
    identifiers are preserved and kept separate from the size directive
    to avoid producing malformed URLs.

    Args:
        url: Original image URL from a scraper (or None/empty).

    Returns:
        Possibly-rewritten URL pointing to a higher-resolution variant.
    """
    if not url:
        return url
    if not _GOOGLE_CDN_HOST_RE.match(url):
        return url
    # Preserve any query string / fragment so we don't append the size
    # directive in the wrong place (Google CDN product URLs in the wild
    # don't carry them, but be defensive against future callers).
    path, sep_q, query = url.partition("?")
    path, sep_f, fragment = path.partition("#")
    stripped = _GOOGLE_CDN_SIZE_RE.sub("", path)
    rebuilt = f"{stripped}=s{TARGET_SRC_SIZE}"
    if sep_f:
        rebuilt += f"#{fragment}"
    if sep_q:
        rebuilt += f"?{query}"
    return rebuilt


async def _download_image(url: str) -> Image.Image | None:
    """Download a product image from URL.

    The URL is first passed through :func:`_upgrade_image_url` so known
    CDNs return a high-resolution variant instead of their default
    thumbnail. Errors at any stage log a warning and return None so the
    caller can skip the failed card without aborting the whole grid.
    """
    fetch_url = _upgrade_image_url(url)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(fetch_url, follow_redirects=True)
            resp.raise_for_status()
            return Image.open(io.BytesIO(resp.content)).convert("RGBA")
    except httpx.HTTPError as exc:
        logger.warning("Failed to download image %s: %s", fetch_url[:80], exc)
        return None
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning(
            "Failed to decode image %s: %s", fetch_url[:80], exc, exc_info=True,
        )
        return None


def _unique_product_images(
    results: list[DrugResult], max_products: int
) -> list[DrugResult]:
    """Get unique products in the same order as the text formatter.

    Uses the formatter's _group_by_product to ensure images match the
    text message order. Deduplicates by image URL so the same photo
    never appears twice.
    """
    groups = _group_by_product(results)
    unique: list[DrugResult] = []
    seen_urls: set[str] = set()

    for _name, pharmacy_results in groups[:max_products]:
        # Pick the first result with an image from this product group
        for r in pharmacy_results:
            if r.image_url and r.image_url not in seen_urls:
                seen_urls.add(r.image_url)
                unique.append(r)
                break

    return unique


def _render_card(img: Image.Image) -> Image.Image:
    """Resize a source image to IMAGE_SIZE and apply mild sharpening.

    Pure function (no I/O) extracted from :func:`generate_product_grid`
    so it can be unit-tested without the full grid pipeline.

    Args:
        img: Source image (any size, any mode).

    Returns:
        RGBA image exactly ``IMAGE_SIZE``x``IMAGE_SIZE`` with a mild
        UnsharpMask applied to compensate for WhatsApp recompression.
    """
    resized = img.resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.LANCZOS)
    # Mild UnsharpMask — enough to survive WhatsApp's JPEG recompression
    # without introducing visible halos. Values chosen empirically:
    # radius=1 (single-pixel edge), percent=50 (moderate boost),
    # threshold=2 (skip flat areas so noise isn't amplified).
    return resized.filter(ImageFilter.UnsharpMask(radius=1, percent=50, threshold=2))


async def generate_product_grid(
    results: list[DrugResult], max_products: int = 8
) -> str | None:
    """Generate a stacked product image for WhatsApp — images only, no text.

    Uses the same product grouping and order as the text formatter.
    Deduplicates by image URL so no photo appears twice.

    Args:
        results: Drug search results to display.
        max_products: Maximum products to show (default 8, matches text).

    Returns:
        Path to the generated temporary image file (``.jpg``), or None
        on failure.
    """
    products = _unique_product_images(results, max_products)

    if not products:
        return None

    # Download all product images, skip failures
    cards: list[Image.Image] = []
    for p in products:
        if p.image_url:
            img = await _download_image(p.image_url)
            if img:
                cards.append(img)

    if not cards:
        return None

    n = len(cards)
    canvas_h = 2 * CARD_MARGIN + n * IMAGE_SIZE + (n - 1) * CARD_GAP

    canvas = Image.new("RGB", (CANVAS_WIDTH, canvas_h), BG_COLOR)

    for i, img in enumerate(cards):
        cy = CARD_MARGIN + i * (IMAGE_SIZE + CARD_GAP)
        cx = CARD_MARGIN

        sharpened = _render_card(img)

        # Create rounded mask
        mask = Image.new("L", (IMAGE_SIZE, IMAGE_SIZE), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle(
            [0, 0, IMAGE_SIZE, IMAGE_SIZE], radius=CARD_RADIUS, fill=255
        )

        # White card background
        card_bg = Image.new("RGBA", (IMAGE_SIZE, IMAGE_SIZE), (255, 255, 255, 255))
        card_bg.paste(
            sharpened, (0, 0), sharpened if sharpened.mode == "RGBA" else None
        )
        card_rgb = card_bg.convert("RGB")

        canvas.paste(card_rgb, (cx, cy), mask)

        # Draw border
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(
            [cx, cy, cx + IMAGE_SIZE, cy + IMAGE_SIZE],
            radius=CARD_RADIUS,
            outline=BORDER_COLOR,
            width=1,
        )

    # Save as JPEG — WhatsApp transcodes PNGs to JPEG anyway, so sending
    # JPEG directly gives us control over quality and a much smaller
    # file. We walk a quality ladder and take the first tier whose
    # output fits under the WhatsApp 5 MB limit (with safety margin).
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()  # we only need the path — re-open by name for saving
    final_quality = JPEG_QUALITY_LADDER[-1][0]
    final_subsampling = JPEG_QUALITY_LADDER[-1][1]
    final_size = 0
    for quality, subsampling in JPEG_QUALITY_LADDER:
        canvas.save(
            tmp.name,
            "JPEG",
            quality=quality,
            subsampling=subsampling,
            optimize=True,
            progressive=True,
        )
        final_size = os.path.getsize(tmp.name)
        final_quality = quality
        final_subsampling = subsampling
        if final_size <= WHATSAPP_MAX_BYTES:
            break
        logger.warning(
            "Grid JPEG %d bytes at q=%d ss=%d — trying next tier",
            final_size, quality, subsampling,
        )
    logger.info(
        "Generated product images: %s (%dx%d, %d products, %d bytes, q=%d ss=%d)",
        tmp.name,
        CANVAS_WIDTH,
        canvas_h,
        n,
        final_size,
        final_quality,
        final_subsampling,
    )
    return tmp.name
