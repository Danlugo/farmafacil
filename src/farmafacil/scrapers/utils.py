"""Shared utilities for pharmacy scrapers.

Common parsing helpers used across multiple scraper implementations:
- Venezuelan price parsing (dot thousands, comma decimal)
- Brand extraction from product names
"""

import re
from decimal import Decimal, InvalidOperation


def parse_ve_price(text: str) -> Decimal | None:
    """Parse a Venezuelan price string like '2.677,76' to Decimal.

    Venezuelan format uses dots as thousands separators and comma as
    decimal separator (e.g., ``5.114,82`` → ``Decimal("5114.82")``).

    Args:
        text: Raw price text from the page.

    Returns:
        Decimal price or None if unparseable.
    """
    if not text:
        return None
    try:
        cleaned = text.replace(".", "").replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def extract_brand(name: str) -> str | None:
    """Extract the brand/manufacturer from a product name.

    Many Venezuelan pharmacies format product names with a trailing
    parenthesized brand, e.g., ``IBUPROFENO 400MG X 10 (ELMOR)``.

    Args:
        name: Product name string.

    Returns:
        Brand name or None if no parenthesized suffix is found.
    """
    match = re.search(r"\(([^)]+)\)\s*$", name)
    return match.group(1).strip() if match else None
