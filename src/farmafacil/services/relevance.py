"""Search relevance scoring — filters out non-pharmaceutical junk from API results.

Pharmacy search APIs (VTEX, Algolia) return products by text similarity, not
medical relevance.  Searching "acetaminofen" can return shoe insoles;
"paracetamol pastillas" can return cleaning tablets.  This module provides a
fast heuristic scorer that rejects irrelevant results before they reach the
user.

The design is "learn once, serve fast": on the first search for a new query,
each API result is scored and only relevant product IDs are cached in
``search_queries``. Future searches hit the cache, which already contains
only clean results.
"""

import re
import unicodedata

# ---------------------------------------------------------------------------
# Non-pharmaceutical categories — products in these drug_class values are
# rejected regardless of name overlap.  Curated from production data.
# Compared case-insensitively.
# ---------------------------------------------------------------------------
NON_PHARMA_CATEGORIES: set[str] = {
    # Food & beverage
    "caramel/chupet/gom",
    "cereales adulto",
    "dulces,snacks sin azucar",
    "frutas en almibar",
    "galletas",
    "panes",
    "te/ infusion/jugos diet",
    "untables",
    "alimentos",
    # Household & cleaning
    "limpiadores/desinfect",
    "papel higienico",
    # Personal care — non-medical
    "champu cosmeticos",
    "esmaltes",
    "lociones/siliconas",
    "banos de crema",
    "portacosmeticos",
    "acces rostro/cuerpo",
    # Baby products (non-medical)
    "panales",
    # Cosmetics / beauty
    "mascaras",
    "facial especializada",
    "nutritivas",
    # NOTE: "roblox" is Farmatodo's internal code for throat lozenges
    # (Bencidamina, Bucoxol), NOT the game — kept as pharma.
    # NOTE: "prot corporal" / "proteccion facial" are sunscreen —
    # legitimate pharmacy products users search for, kept as pharma.
}

# ---------------------------------------------------------------------------
# Pharmaceutical form words — these appear in user queries as the desired
# dosage form ("pastillas", "jarabe") but are NOT the active ingredient.
# Stripped from the query before computing the ingredient-overlap signal.
# ---------------------------------------------------------------------------
FORM_WORDS: set[str] = {
    "pastillas",
    "tabletas",
    "capsulas",
    "comprimidos",
    "jarabe",
    "gotas",
    "crema",
    "gel",
    "sobres",
    "solucion",
    "inyectable",
    "suspension",
    "polvo",
    "spray",
    "parche",
    "ampolla",
    "supositorio",
    "pomada",
    "unguento",
    "locion",
    "emulsion",
    "elixir",
    "granulado",
    "tabs",
    "tab",
    "cap",
    "comp",
    "sol",
    "jbe",
    "gts",
}

# Short tokens that carry no ingredient signal and should be ignored in
# overlap scoring.
_NOISE_TOKENS: set[str] = {
    "de", "en", "la", "el", "las", "los", "con", "para", "por", "sin",
    "mas", "mg", "ml", "g", "un", "una", "x", "pack", "caja", "blister",
    "frasco", "sobre", "tubo",
}


def _strip_accents(text: str) -> str:
    """Remove diacritics so 'acetaminofén' matches 'acetaminofen'."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """Lowercase, strip accents, remove non-alphanumeric except +.

    The ``+`` character is preserved because some drug names use it
    (e.g., "NAD+VID").
    """
    text = _strip_accents(text.lower().strip())
    # Keep letters, digits, whitespace, and +
    text = re.sub(r"[^\w\s+]", " ", text)
    # Collapse multiple spaces
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(text: str) -> set[str]:
    """Normalize and split into a set of meaningful tokens."""
    tokens = set(normalize(text).split())
    return tokens - _NOISE_TOKENS


def compute_relevance(
    query: str,
    drug_name: str,
    drug_class: str | None = None,
    description: str | None = None,
) -> float:
    """Score how relevant a product is to a search query.

    Returns a float in [0.0, 1.0].  The score is built from three signals:

    1. **Token overlap** (0.0–0.5): fraction of query tokens found in the
       product name.  Uses normalized, accent-stripped tokens.

    2. **Pharmaceutical category** (+0.3 pharma / +0.15 unknown / +0.0
       non-pharma): bonus based on the product's ``drug_class``.

    3. **Active ingredient match** (0.0 or 0.2): bonus if query tokens
       (after stripping form words like "pastillas") appear in the product
       name — stronger signal that the product actually contains the
       searched ingredient.

    Args:
        query: User's search query (e.g., "paracetamol pastillas").
        drug_name: Product name from the pharmacy API.
        drug_class: Product category from the API (may be None).
        description: Product description (used for tie-breaking).

    Returns:
        Relevance score between 0.0 and 1.0.
    """
    query_tokens = _tokenize(query)
    name_tokens = _tokenize(drug_name)

    if not query_tokens or not name_tokens:
        return 0.0

    score = 0.0

    # Signal 1: Token overlap (0.0 – 0.5)
    overlap = query_tokens & name_tokens
    score += 0.5 * len(overlap) / len(query_tokens)

    # Signal 2: Pharmaceutical category (0.0 or 0.3)
    pharma = classify_pharmaceutical(drug_class)
    if pharma is True:
        score += 0.3
    elif pharma is None:
        # Unknown category — give partial credit
        score += 0.15

    # Signal 3: Active ingredient match (0.0 or 0.2)
    # Strip form words to isolate the active ingredient
    ingredient_tokens = query_tokens - FORM_WORDS
    if ingredient_tokens and ingredient_tokens & name_tokens:
        score += 0.2

    return min(score, 1.0)


def is_relevant(
    query: str,
    drug_name: str,
    drug_class: str | None = None,
    description: str | None = None,
    threshold: float = 0.3,
) -> bool:
    """Check if a product meets the relevance threshold for a query.

    Args:
        query: User's search query.
        drug_name: Product name from the pharmacy API.
        drug_class: Product category.
        description: Product description.
        threshold: Minimum score to be considered relevant.

    Returns:
        True if the product is relevant to the query.
    """
    return compute_relevance(query, drug_name, drug_class, description) >= threshold


def classify_pharmaceutical(drug_class: str | None) -> bool | None:
    """Classify whether a drug_class indicates a pharmaceutical product.

    Args:
        drug_class: Category string from the pharmacy API.

    Returns:
        True if pharmaceutical, False if known non-pharma, None if unknown.
    """
    if not drug_class:
        return None
    if drug_class.lower().strip() in NON_PHARMA_CATEGORIES:
        return False
    return True
