"""Generate product grid images for WhatsApp — clean multi-pharmacy cards."""

import io
import logging
import tempfile
from collections import defaultdict
from decimal import Decimal

import httpx
from PIL import Image, ImageDraw, ImageFont

from farmafacil.models.schemas import DrugResult

logger = logging.getLogger(__name__)

# Grid layout constants — optimized for WhatsApp (2-col, phone-friendly)
CARD_WIDTH = 600
CARD_HEIGHT = 780
CARD_PADDING = 24
IMAGE_SIZE = 540  # 90% of card width
GRID_COLS = 2
GRID_GAP = 18
GRID_MARGIN = 24

# Colors — neutral multi-pharmacy theme
BG_COLOR = (245, 245, 245)
CARD_BG = (255, 255, 255)
TEXT_COLOR = (33, 33, 33)
PRICE_COLOR = (27, 94, 32)
OLD_PRICE_COLOR = (158, 158, 158)
DISCOUNT_BG = (76, 175, 80)
DISCOUNT_TEXT = (255, 255, 255)
BRAND_COLOR = (117, 117, 117)
STOCK_COLOR = (76, 175, 80)
NO_STOCK_COLOR = (244, 67, 54)
BORDER_COLOR = (224, 224, 224)
DISTANCE_COLOR = (63, 81, 181)

# Pharmacy badge colors — each chain gets a distinct color
PHARMACY_COLORS: dict[str, tuple[int, int, int]] = {
    "Farmatodo": (255, 193, 7),       # Yellow
    "Farmacias SAAS": (63, 81, 181),  # Blue
    "Locatel": (0, 150, 136),         # Teal
    "Farmahorro": (233, 30, 99),      # Pink
}
PHARMACY_DEFAULT_COLOR = (97, 97, 97)  # Grey fallback


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


def _text_width(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
    """Get the pixel width of rendered text."""
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except AttributeError:
        return int(font.getlength(text))


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


def _truncate_text(
    text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int
) -> str:
    """Truncate text with ellipsis to fit within max_width."""
    if not text:
        return ""
    if _text_width(text, font) <= max_width:
        return text
    while len(text) > 3:
        text = text[:-1]
        if _text_width(text + "...", font) <= max_width:
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
    """Draw a single product card onto the canvas.

    Layout (top to bottom):
    - Discount badge (top-left) + Pharmacy badge (top-right)
    - Product image (centered)
    - Product name (bold, 1-2 lines)
    - Price (green, bold) + old price (strikethrough)
    - Distance to nearest store (if available)
    - Stock indicator
    """
    font_pharmacy = _get_font(20, bold=True)
    font_name = _get_font(26, bold=True)
    font_price = _get_font(36, bold=True)
    font_old_price = _get_font(22)
    font_discount = _get_font(20, bold=True)
    font_detail = _get_font(22)

    text_area = CARD_WIDTH - 2 * CARD_PADDING

    # Card background
    draw.rounded_rectangle(
        [x, y, x + CARD_WIDTH, y + CARD_HEIGHT],
        radius=12,
        fill=CARD_BG,
        outline=BORDER_COLOR,
        width=1,
    )

    # ── Pharmacy badge (top-right) ──
    pharmacy_name = result.pharmacy_name or ""
    if pharmacy_name:
        badge_color = PHARMACY_COLORS.get(pharmacy_name, PHARMACY_DEFAULT_COLOR)
        pw = _text_width(pharmacy_name, font_pharmacy)
        badge_x = x + CARD_WIDTH - pw - 24
        draw.rounded_rectangle(
            [badge_x - 8, y + 10, x + CARD_WIDTH - 10, y + 36],
            radius=12,
            fill=badge_color,
        )
        draw.text((badge_x, y + 11), pharmacy_name, fill=(255, 255, 255), font=font_pharmacy)

    # ── Discount badge (top-left) ──
    if result.discount_pct:
        badge_text = f" {result.discount_pct} "
        dw = _text_width(badge_text, font_discount)
        draw.rounded_rectangle(
            [x + 10, y + 10, x + dw + 20, y + 36],
            radius=12,
            fill=DISCOUNT_BG,
        )
        draw.text((x + 14, y + 11), badge_text, fill=DISCOUNT_TEXT, font=font_discount)

    # ── Product image (centered, 90% of card width) ──
    img_x = x + (CARD_WIDTH - IMAGE_SIZE) // 2
    img_y = y + 44
    if product_img:
        resized = product_img.resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.LANCZOS)
        canvas.paste(resized, (img_x, img_y), resized if resized.mode == "RGBA" else None)
    else:
        draw.rectangle(
            [img_x, img_y, img_x + IMAGE_SIZE, img_y + IMAGE_SIZE],
            fill=(240, 240, 240),
            outline=BORDER_COLOR,
        )
        draw.text(
            (img_x + 200, img_y + 240), "Sin imagen", fill=OLD_PRICE_COLOR, font=font_detail
        )

    # ── Text area below image ──
    ty = img_y + IMAGE_SIZE + 16

    # Product name (up to 2 lines)
    name = result.drug_name or ""
    line1 = _truncate_text(name, font_name, text_area)
    draw.text((x + CARD_PADDING, ty), line1, fill=TEXT_COLOR, font=font_name)
    ty += 34

    if len(line1) < len(name) and not line1.endswith("..."):
        line2 = _truncate_text(name[len(line1):].strip(), font_name, text_area)
        draw.text((x + CARD_PADDING, ty), line2, fill=TEXT_COLOR, font=font_name)
        ty += 34

    ty += 8

    # ── Price ──
    if result.price_bs is not None:
        price_text = f"Bs. {result.price_bs:,.2f}"
        draw.text((x + CARD_PADDING, ty), price_text, fill=PRICE_COLOR, font=font_price)

        # Old price (strikethrough)
        if result.full_price_bs and result.full_price_bs != result.price_bs:
            px_end = _text_width(price_text, font_price)
            old_text = f"Bs. {result.full_price_bs:,.2f}"
            old_x = x + CARD_PADDING + px_end + 12
            draw.text((old_x, ty + 8), old_text, fill=OLD_PRICE_COLOR, font=font_old_price)
            old_w = _text_width(old_text, font_old_price)
            strike_y = ty + 20
            draw.line(
                [(old_x, strike_y), (old_x + old_w, strike_y)],
                fill=OLD_PRICE_COLOR,
                width=2,
            )

        ty += 46

    # ── Distance to nearest store ──
    if result.nearby_stores:
        closest = result.nearby_stores[0]
        dist_text = f"\U0001f4cd {closest.distance_km:.1f} km — {closest.store_name}"
        dist_text = _truncate_text(dist_text, font_detail, text_area)
        draw.text((x + CARD_PADDING, ty), dist_text, fill=DISTANCE_COLOR, font=font_detail)
        ty += 30

    # ── Stock status ──
    if result.available:
        if result.stores_in_stock > 0:
            stock_text = f"\u2713 {result.stores_in_stock} tiendas"
        else:
            stock_text = "\u2713 Disponible"
        draw.text((x + CARD_PADDING, ty), stock_text, fill=STOCK_COLOR, font=font_detail)
    else:
        draw.text(
            (x + CARD_PADDING, ty), "\u2717 Sin stock", fill=NO_STOCK_COLOR, font=font_detail
        )


def _interleave_for_grid(
    results: list[DrugResult], max_products: int
) -> list[DrugResult]:
    """Interleave results round-robin across pharmacies for the grid image.

    Prioritizes available items, sorted by price within each pharmacy.
    """
    by_pharmacy: dict[str, list[DrugResult]] = defaultdict(list)
    for r in results:
        by_pharmacy[r.pharmacy_name].append(r)

    for name in by_pharmacy:
        available = [r for r in by_pharmacy[name] if r.available]
        unavailable = [r for r in by_pharmacy[name] if not r.available]
        available.sort(
            key=lambda r: r.price_bs if r.price_bs is not None else Decimal("999999")
        )
        unavailable.sort(
            key=lambda r: r.price_bs if r.price_bs is not None else Decimal("999999")
        )
        by_pharmacy[name] = available + unavailable

    interleaved: list[DrugResult] = []
    pharmacy_names = sorted(by_pharmacy.keys())
    indices = {name: 0 for name in pharmacy_names}

    while len(interleaved) < max_products:
        added = False
        for name in pharmacy_names:
            if len(interleaved) >= max_products:
                break
            idx = indices[name]
            if idx < len(by_pharmacy[name]):
                interleaved.append(by_pharmacy[name][idx])
                indices[name] = idx + 1
                added = True
        if not added:
            break

    if not interleaved:
        return []
    return interleaved


async def generate_product_grid(
    results: list[DrugResult], max_products: int = 4
) -> str | None:
    """Generate a product grid image from search results.

    Results are interleaved across pharmacies (round-robin) so every chain
    is represented, with the best-priced items from each shown first.

    Args:
        results: Drug search results to display.
        max_products: Maximum products to show (default 4, max 6).

    Returns:
        Path to the generated temporary image file, or None on failure.
    """
    products = _interleave_for_grid(results, max_products)

    if not products:
        return None

    n = len(products)
    cols = min(n, GRID_COLS)
    rows = (n + cols - 1) // cols

    canvas_w = 2 * GRID_MARGIN + cols * CARD_WIDTH + (cols - 1) * GRID_GAP
    canvas_h = 2 * GRID_MARGIN + rows * CARD_HEIGHT + (rows - 1) * GRID_GAP

    canvas = Image.new("RGB", (canvas_w, canvas_h), BG_COLOR)
    draw = ImageDraw.Draw(canvas)

    # Download all product images
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
    logger.info(
        "Generated product grid: %s (%dx%d, %d products)",
        tmp.name, canvas_w, canvas_h, n,
    )
    return tmp.name
