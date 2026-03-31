"""Generate product images for WhatsApp — image-first with text overlays."""

import io
import logging
import tempfile
from collections import defaultdict
from decimal import Decimal

import httpx
from PIL import Image, ImageDraw, ImageFont

from farmafacil.models.schemas import DrugResult

logger = logging.getLogger(__name__)

# ── Layout: single-column, image-dominant cards ──
# WhatsApp shows images at ~330px wide. A 500px canvas = only 1.5x shrink.
# Text at 36pt renders as ~24pt on screen = very readable.
CANVAS_WIDTH = 500
CARD_SIZE = 460  # Square card (image fills 90%)
CARD_MARGIN = 20
CARD_GAP = 16
CARD_RADIUS = 16

# Colors
BG_COLOR = (245, 245, 245)
CARD_BG = (255, 255, 255)
OVERLAY_BG = (0, 0, 0, 160)  # Semi-transparent black for text overlay
OVERLAY_TEXT = (255, 255, 255)
PRICE_COLOR_LIGHT = (130, 255, 130)  # Green on dark overlay
NO_STOCK_OVERLAY = (255, 100, 100)
STOCK_OVERLAY = (130, 255, 130)
BORDER_COLOR = (224, 224, 224)

# Pharmacy badge colors
PHARMACY_COLORS: dict[str, tuple[int, int, int, int]] = {
    "Farmatodo": (255, 165, 0, 220),
    "Farmacias SAAS": (63, 81, 181, 220),
    "Locatel": (0, 150, 136, 220),
    "Farmahorro": (233, 30, 99, 220),
}
PHARMACY_DEFAULT_COLOR = (97, 97, 97, 220)


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
    canvas: Image.Image,
    result: DrugResult,
    product_img: Image.Image | None,
    y: int,
) -> None:
    """Draw a product card — large image with text overlaid as watermarks.

    Layout:
    ┌──────────────────────┐
    │ [Farmatodo]     20%  │  ← pharmacy badge + discount (top)
    │                      │
    │     PRODUCT IMAGE    │  ← 90% of card space
    │     (fills card)     │
    │                      │
    │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │  ← dark overlay bar (bottom)
    │ ▓ Losartan 50mg    ▓ │
    │ ▓ Bs. 316.00       ▓ │
    │ ▓ ✓ 198 tiendas   ▓ │
    └──────────────────────┘
    """
    x = CARD_MARGIN
    card_w = CANVAS_WIDTH - 2 * CARD_MARGIN

    # Fonts — at 500px canvas with 1.5x shrink, these render large on phone
    font_pharmacy = _get_font(24, bold=True)
    font_discount = _get_font(22, bold=True)
    font_name = _get_font(28, bold=True)
    font_price = _get_font(40, bold=True)
    font_old_price = _get_font(24)
    font_detail = _get_font(22)

    # ── Create card as RGBA for overlay compositing ──
    card = Image.new("RGBA", (card_w, CARD_SIZE), (255, 255, 255, 255))

    # ── Product image (fills the card) ──
    if product_img:
        resized = product_img.resize((card_w, CARD_SIZE), Image.Resampling.LANCZOS)
        card.paste(resized, (0, 0), resized if resized.mode == "RGBA" else None)
    else:
        card_draw = ImageDraw.Draw(card)
        card_draw.rectangle([0, 0, card_w, CARD_SIZE], fill=(245, 245, 245, 255))
        card_draw.text(
            (card_w // 2 - 60, CARD_SIZE // 2 - 15),
            "Sin imagen",
            fill=(158, 158, 158, 255),
            font=font_name,
        )

    # ── Create overlay layer for text ──
    overlay = Image.new("RGBA", (card_w, CARD_SIZE), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)

    # ── Pharmacy badge (top-left) ──
    pharmacy_name = result.pharmacy_name or ""
    if pharmacy_name:
        badge_color = PHARMACY_COLORS.get(pharmacy_name, PHARMACY_DEFAULT_COLOR)
        pw = _text_width(pharmacy_name, font_pharmacy)
        ov_draw.rounded_rectangle(
            [12, 12, pw + 32, 48],
            radius=10,
            fill=badge_color,
        )
        ov_draw.text(
            (22, 14), pharmacy_name, fill=(255, 255, 255, 255), font=font_pharmacy
        )

    # ── Discount badge (top-right) ──
    if result.discount_pct:
        badge_text = result.discount_pct
        dw = _text_width(badge_text, font_discount)
        ov_draw.rounded_rectangle(
            [card_w - dw - 32, 12, card_w - 12, 48],
            radius=10,
            fill=(76, 175, 80, 220),
        )
        ov_draw.text(
            (card_w - dw - 22, 15),
            badge_text,
            fill=(255, 255, 255, 255),
            font=font_discount,
        )

    # ── Bottom overlay bar with product info ──
    overlay_h = 160
    overlay_top = CARD_SIZE - overlay_h
    ov_draw.rounded_rectangle(
        [0, overlay_top, card_w, CARD_SIZE],
        radius=0,
        fill=(0, 0, 0, 170),
    )

    text_max_w = card_w - 24
    ty = overlay_top + 10

    # Product name
    name = result.drug_name or ""
    name_line = _truncate_text(name, font_name, text_max_w)
    ov_draw.text((12, ty), name_line, fill=(255, 255, 255, 255), font=font_name)
    ty += 38

    # Price line
    if result.price_bs is not None:
        price_text = f"Bs. {result.price_bs:,.2f}"
        ov_draw.text((12, ty), price_text, fill=PRICE_COLOR_LIGHT, font=font_price)

        if result.full_price_bs and result.full_price_bs != result.price_bs:
            px_end = _text_width(price_text, font_price)
            old_text = f"Bs. {result.full_price_bs:,.2f}"
            old_x = 12 + px_end + 10
            ov_draw.text(
                (old_x, ty + 10),
                old_text,
                fill=(180, 180, 180, 255),
                font=font_old_price,
            )
            old_w = _text_width(old_text, font_old_price)
            strike_y = ty + 22
            ov_draw.line(
                [(old_x, strike_y), (old_x + old_w, strike_y)],
                fill=(180, 180, 180, 255),
                width=2,
            )
        ty += 52

    # Stock + distance on same line
    info_parts: list[str] = []
    if result.nearby_stores:
        closest = result.nearby_stores[0]
        info_parts.append(f"{closest.distance_km:.1f} km")
    if result.available:
        if result.stores_in_stock > 0:
            info_parts.append(f"\u2713 {result.stores_in_stock} tiendas")
        else:
            info_parts.append("\u2713 Disponible")
        stock_color = STOCK_OVERLAY
    else:
        info_parts.append("\u2717 Sin stock")
        stock_color = NO_STOCK_OVERLAY

    info_text = "  \u2022  ".join(info_parts)
    ov_draw.text((12, ty), info_text, fill=stock_color, font=font_detail)

    # ── Composite overlay onto card ──
    card = Image.alpha_composite(card, overlay)

    # ── Draw rounded border on main canvas and paste card ──
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        [x, y, x + card_w, y + CARD_SIZE],
        radius=CARD_RADIUS,
        outline=BORDER_COLOR,
        width=2,
    )

    # Convert card to RGB for pasting onto RGB canvas
    card_rgb = card.convert("RGB")

    # Create a rounded mask
    mask = Image.new("L", (card_w, CARD_SIZE), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        [0, 0, card_w, CARD_SIZE], radius=CARD_RADIUS, fill=255
    )
    canvas.paste(card_rgb, (x, y), mask)


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
    """Generate a product image list for WhatsApp.

    Each product is a large image card with text overlaid as watermarks.
    Canvas is narrow (500px) so WhatsApp only shrinks by ~1.5x, keeping
    text readable on phone screens.

    Args:
        results: Drug search results to display.
        max_products: Maximum products to show (default 4).

    Returns:
        Path to the generated temporary image file, or None on failure.
    """
    products = _interleave_for_grid(results, max_products)

    if not products:
        return None

    n = len(products)
    canvas_h = 2 * CARD_MARGIN + n * CARD_SIZE + (n - 1) * CARD_GAP

    canvas = Image.new("RGB", (CANVAS_WIDTH, canvas_h), BG_COLOR)

    # Download all product images
    product_images: list[Image.Image | None] = []
    for p in products:
        if p.image_url:
            img = await _download_image(p.image_url)
            product_images.append(img)
        else:
            product_images.append(None)

    # Draw each card vertically stacked
    for i, (result, prod_img) in enumerate(zip(products, product_images)):
        cy = CARD_MARGIN + i * (CARD_SIZE + CARD_GAP)
        _draw_card(canvas, result, prod_img, cy)

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    canvas.save(tmp.name, "PNG", optimize=True)
    logger.info(
        "Generated product grid: %s (%dx%d, %d products)",
        tmp.name,
        CANVAS_WIDTH,
        canvas_h,
        n,
    )
    return tmp.name
