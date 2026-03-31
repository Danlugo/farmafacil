"""Generate product grid images for WhatsApp — mimics Farmatodo app cards."""

import io
import logging
import tempfile
from decimal import Decimal

import httpx
from PIL import Image, ImageDraw, ImageFont

from farmafacil.models.schemas import DrugResult

logger = logging.getLogger(__name__)

# Grid layout constants
CARD_WIDTH = 380
CARD_HEIGHT = 480
CARD_PADDING = 16
IMAGE_SIZE = 200
GRID_COLS = 3
GRID_GAP = 12
GRID_MARGIN = 20

# Colors (matching Farmatodo's yellow/white theme)
BG_COLOR = (245, 245, 245)
CARD_BG = (255, 255, 255)
TEXT_COLOR = (33, 33, 33)
PRICE_COLOR = (0, 0, 0)
OLD_PRICE_COLOR = (158, 158, 158)
DISCOUNT_BG = (76, 175, 80)
DISCOUNT_TEXT = (255, 255, 255)
BRAND_COLOR = (117, 117, 117)
STOCK_COLOR = (76, 175, 80)
NO_STOCK_COLOR = (244, 67, 54)
BORDER_COLOR = (224, 224, 224)


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Get a font, falling back to default if system fonts unavailable."""
    font_paths = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSText.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    if bold:
        font_paths = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/SFNSText-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ] + font_paths

    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


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


def _truncate_text(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int) -> str:
    """Truncate text with ellipsis to fit within max_width."""
    if not text:
        return ""
    try:
        bbox = font.getbbox(text)
        text_width = bbox[2] - bbox[0]
    except AttributeError:
        text_width = font.getlength(text)

    if text_width <= max_width:
        return text

    while len(text) > 3:
        text = text[:-1]
        try:
            bbox = font.getbbox(text + "...")
            w = bbox[2] - bbox[0]
        except AttributeError:
            w = font.getlength(text + "...")
        if w <= max_width:
            return text + "..."
    return text[:3] + "..."


def _draw_card(
    draw: ImageDraw.ImageDraw,
    result: DrugResult,
    product_img: Image.Image | None,
    canvas: Image.Image,
    x: int,
    y: int,
) -> None:
    """Draw a single product card onto the canvas."""
    font_brand = _get_font(16)
    font_name = _get_font(17, bold=True)
    font_price = _get_font(22, bold=True)
    font_old_price = _get_font(16)
    font_discount = _get_font(15, bold=True)
    font_stock = _get_font(14)
    font_store = _get_font(14)

    text_area = CARD_WIDTH - 2 * CARD_PADDING

    # Card background with rounded corners (approximate with rectangle)
    draw.rounded_rectangle(
        [x, y, x + CARD_WIDTH, y + CARD_HEIGHT],
        radius=12,
        fill=CARD_BG,
        outline=BORDER_COLOR,
        width=1,
    )

    # Discount badge (top-left)
    if result.discount_pct:
        badge_text = f" {result.discount_pct} "
        draw.rounded_rectangle(
            [x + 8, y + 8, x + 80, y + 32],
            radius=4,
            fill=DISCOUNT_BG,
        )
        draw.text((x + 12, y + 9), badge_text, fill=DISCOUNT_TEXT, font=font_discount)

    # Product image (centered)
    img_x = x + (CARD_WIDTH - IMAGE_SIZE) // 2
    img_y = y + 40
    if product_img:
        resized = product_img.resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.LANCZOS)
        # Paste with alpha mask for transparency
        canvas.paste(resized, (img_x, img_y), resized if resized.mode == "RGBA" else None)
    else:
        # Placeholder
        draw.rectangle(
            [img_x, img_y, img_x + IMAGE_SIZE, img_y + IMAGE_SIZE],
            fill=(240, 240, 240),
            outline=BORDER_COLOR,
        )
        draw.text(
            (img_x + 60, img_y + 90), "Sin imagen", fill=OLD_PRICE_COLOR, font=font_stock
        )

    # Text area starts below image
    ty = img_y + IMAGE_SIZE + 12

    # Brand
    if result.brand:
        brand_text = _truncate_text(result.brand, font_brand, text_area)
        draw.text((x + CARD_PADDING, ty), brand_text, fill=BRAND_COLOR, font=font_brand)
        ty += 22

    # Product name (up to 2 lines)
    name = result.drug_name or ""
    line1 = _truncate_text(name, font_name, text_area)
    draw.text((x + CARD_PADDING, ty), line1, fill=TEXT_COLOR, font=font_name)
    ty += 24

    # If name was truncated, show second line
    if len(line1) < len(name) and not line1.endswith("..."):
        line2 = _truncate_text(name[len(line1):].strip(), font_name, text_area)
        draw.text((x + CARD_PADDING, ty), line2, fill=TEXT_COLOR, font=font_name)
        ty += 24

    ty += 4

    # Price
    if result.price_bs is not None:
        price_text = f"Bs.{result.price_bs:,.2f}"
        draw.text((x + CARD_PADDING, ty), price_text, fill=PRICE_COLOR, font=font_price)

        # Old price (strikethrough)
        if result.full_price_bs and result.full_price_bs != result.price_bs:
            try:
                bbox = font_price.getbbox(price_text)
                px_end = bbox[2] - bbox[0]
            except AttributeError:
                px_end = int(font_price.getlength(price_text))

            old_text = f"Bs.{result.full_price_bs:,.2f}"
            old_x = x + CARD_PADDING + px_end + 8
            draw.text((old_x, ty + 4), old_text, fill=OLD_PRICE_COLOR, font=font_old_price)
            # Strikethrough line
            try:
                old_bbox = font_old_price.getbbox(old_text)
                old_w = old_bbox[2] - old_bbox[0]
            except AttributeError:
                old_w = int(font_old_price.getlength(old_text))
            strike_y = ty + 14
            draw.line([(old_x, strike_y), (old_x + old_w, strike_y)], fill=OLD_PRICE_COLOR, width=1)

        ty += 30

    # Unit price
    if result.unit_label:
        draw.text(
            (x + CARD_PADDING, ty), result.unit_label, fill=OLD_PRICE_COLOR, font=font_stock
        )
        ty += 20

    # Stock status
    ty += 4
    if result.available and result.stores_in_stock > 0:
        stock_text = f"✓ {result.stores_in_stock} tiendas"
        draw.text((x + CARD_PADDING, ty), stock_text, fill=STOCK_COLOR, font=font_stock)
    elif not result.available:
        draw.text((x + CARD_PADDING, ty), "✗ Sin stock", fill=NO_STOCK_COLOR, font=font_stock)

    # Nearest store
    if result.nearby_stores:
        ty += 18
        closest = result.nearby_stores[0]
        store_text = _truncate_text(
            f"📍 {closest.store_name} — {closest.distance_km:.1f}km",
            font_store,
            text_area,
        )
        draw.text((x + CARD_PADDING, ty), store_text, fill=BRAND_COLOR, font=font_store)


async def generate_product_grid(results: list[DrugResult], max_products: int = 6) -> str | None:
    """Generate a product grid image from search results.

    Args:
        results: Drug search results to display.
        max_products: Maximum products to show (default 6, max 9).

    Returns:
        Path to the generated temporary image file, or None on failure.
    """
    products = [r for r in results[:max_products] if r.available]
    if not products:
        products = results[:max_products]

    if not products:
        return None

    n = len(products)
    cols = min(n, GRID_COLS)
    rows = (n + cols - 1) // cols

    canvas_w = 2 * GRID_MARGIN + cols * CARD_WIDTH + (cols - 1) * GRID_GAP
    canvas_h = 2 * GRID_MARGIN + rows * CARD_HEIGHT + (rows - 1) * GRID_GAP

    canvas = Image.new("RGB", (canvas_w, canvas_h), BG_COLOR)
    draw = ImageDraw.Draw(canvas)

    # Download all product images in parallel-ish (sequential for simplicity)
    product_images: list[Image.Image | None] = []
    for p in products:
        if p.image_url:
            img = await _download_image(p.image_url)
            product_images.append(img)
        else:
            product_images.append(None)

    # Draw each card
    for i, (result, prod_img) in enumerate(zip(products, product_images)):
        col = i % cols
        row = i // cols
        cx = GRID_MARGIN + col * (CARD_WIDTH + GRID_GAP)
        cy = GRID_MARGIN + row * (CARD_HEIGHT + GRID_GAP)
        _draw_card(draw, result, prod_img, canvas, cx, cy)

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    canvas.save(tmp.name, "PNG", optimize=True)
    logger.info("Generated product grid: %s (%dx%d, %d products)", tmp.name, canvas_w, canvas_h, n)
    return tmp.name
