"""Generate product images for WhatsApp — clean image-only cards."""

import io
import logging
import tempfile

import httpx
from PIL import Image, ImageDraw

from farmafacil.bot.formatter import _group_by_product
from farmafacil.models.schemas import DrugResult

logger = logging.getLogger(__name__)

# Layout: narrow canvas, stacked product images only
CANVAS_WIDTH = 500
IMAGE_SIZE = 460
CARD_MARGIN = 20
CARD_GAP = 12
CARD_RADIUS = 12

# Colors
BG_COLOR = (245, 245, 245)
BORDER_COLOR = (224, 224, 224)


async def _download_image(url: str) -> Image.Image | None:
    """Download a product image from URL."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            return Image.open(io.BytesIO(resp.content)).convert("RGBA")
    except Exception:
        logger.warning("Failed to download image: %s", url[:80], exc_info=True)
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
        Path to the generated temporary image file, or None on failure.
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

        # Resize to fit card
        resized = img.resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.LANCZOS)

        # Create rounded mask
        mask = Image.new("L", (IMAGE_SIZE, IMAGE_SIZE), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle(
            [0, 0, IMAGE_SIZE, IMAGE_SIZE], radius=CARD_RADIUS, fill=255
        )

        # White card background
        card_bg = Image.new("RGBA", (IMAGE_SIZE, IMAGE_SIZE), (255, 255, 255, 255))
        card_bg.paste(resized, (0, 0), resized if resized.mode == "RGBA" else None)
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

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    canvas.save(tmp.name, "PNG", optimize=True)
    logger.info(
        "Generated product images: %s (%dx%d, %d products)",
        tmp.name,
        CANVAS_WIDTH,
        canvas_h,
        n,
    )
    return tmp.name
