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
    "pescados",
    "quesillos",
    "en frio",
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
    "cuidado personal",
    "cuidado especial",
    "jabones barra",
    # Baby products (non-medical)
    "panales",
    "cambio panal",
    "accesorios bebe",
    # Cosmetics / beauty
    "mascaras",
    "facial especializada",
    "nutritivas",
    # Deodorants / oral care — not pharmaceutical
    # NOTE: "cd adulto" is Farmatodo's internal code for adult
    # toothpaste/oral care — not pharmaceutical.
    "cd adulto",
    "desodorantes barra",
    "desodorantes locion/crema",
    # NOTE: "roblox" is Farmatodo's internal code for throat lozenges
    # (Bencidamina, Bucoxol), NOT the game — kept as pharma.
    # NOTE: "prot corporal" / "proteccion facial" are sunscreen —
    # legitimate pharmacy products users search for, kept as pharma.
    # NOTE: "jabones intimos" kept as pharma — intimate hygiene products
    # have medical claims and are commonly searched at pharmacies.
    # NOTE: "compresas" kept as pharma — medical compresses are
    # legitimate pharmacy items.
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

# ---------------------------------------------------------------------------
# Form groups — map each dosage form word to a canonical group name.
# When the user specifies a form in their query (e.g., "tabletas recubiertas")
# and the product name contains a *different* form group (e.g., "crema
# vaginal"), the product is heavily penalized (score → 0.0).
# Item 123, v0.44.0.
# ---------------------------------------------------------------------------
_FORM_GROUPS: dict[str, str] = {
    # Oral solid forms
    "tabletas": "oral_solid",
    "tabs": "oral_solid",
    "tab": "oral_solid",
    "comprimidos": "oral_solid",
    "comp": "oral_solid",
    "pastillas": "oral_solid",
    "capsulas": "oral_solid",
    "cap": "oral_solid",
    "sobres": "oral_solid",
    "granulado": "oral_solid",
    # Topical forms
    "crema": "topical",
    "pomada": "topical",
    "unguento": "topical",
    "gel": "topical",
    "locion": "topical",
    "emulsion": "topical",
    "parche": "topical",
    # Liquid oral forms
    "jarabe": "liquid_oral",
    "jbe": "liquid_oral",
    "gotas": "liquid_oral",
    "gts": "liquid_oral",
    "solucion": "liquid_oral",
    "sol": "liquid_oral",
    "elixir": "liquid_oral",
    "suspension": "liquid_oral",
    # Injectable forms
    "inyectable": "injectable",
    "ampolla": "injectable",
    # Inhaled forms
    "spray": "inhaled",
    # Rectal forms
    "supositorio": "rectal",
    # Powder (reconstitution) forms
    "polvo": "powder",
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


def _extract_form_groups(tokens: set[str]) -> set[str]:
    """Extract canonical form groups from a set of tokens.

    Returns the set of group names (e.g., {"oral_solid"}) found among
    the tokens.  Returns an empty set if no form words are present.
    """
    groups: set[str] = set()
    for token in tokens:
        group = _FORM_GROUPS.get(token)
        if group:
            groups.add(group)
    return groups


def compute_relevance(
    query: str,
    drug_name: str,
    drug_class: str | None = None,
    description: str | None = None,
    brand: str | None = None,
) -> float:
    """Score how relevant a product is to a search query.

    Returns a float in [0.0, 1.0].  The score is built from four signals,
    gated by a hard floor on token overlap (Q6 fix, v0.20.1):

    0. **Token-overlap floor (Q6, v0.20.1)**: at least one normalized query
       token must appear as a *whole token* in the product's ``drug_name``
       OR ``brand``. Without overlap the score is 0.0 — even when the
       pharmaceutical category alone would otherwise be a positive signal.
       This kills upstream-API fuzzy/prefix matches like Algolia returning
       "Aspirador Nasal" for query "Aspirina" (different whole tokens, no
       overlap), or "Tiotropio Spiriva" for the same query.

    1. **Token overlap** (0.0–0.5): fraction of query tokens found in the
       product name.  Uses normalized, accent-stripped tokens.

    2. **Pharmaceutical category** (+0.3 pharma / +0.15 unknown / +0.0
       non-pharma): bonus based on the product's ``drug_class``.

    3. **Active ingredient match** (0.0 or 0.2): bonus if query tokens
       (after stripping form words like "pastillas") appear in the product
       name — stronger signal that the product actually contains the
       searched ingredient.

    4. **Form-conflict penalty (Item 123, v0.44.0)**: if the user specifies
       a dosage form in their query (e.g., "tabletas") and the product name
       contains a *different* form group (e.g., "crema"), the score is set
       to 0.0.  Form words are grouped by equivalence (tabletas/tabs/comp →
       oral_solid, crema/pomada/gel → topical, etc.).  No penalty when the
       query or product has no form words.

    Args:
        query: User's search query (e.g., "paracetamol pastillas").
        drug_name: Product name from the pharmacy API.
        drug_class: Product category from the API (may be None).
        description: Product description (used for tie-breaking).
        brand: Product brand/manufacturer (used as a fallback target for
            the token-overlap floor — covers the bidirectional brand↔
            generic case where the brand field carries the recognizable
            name even though the drug_name is the generic compound).

    Returns:
        Relevance score between 0.0 and 1.0.
    """
    query_tokens = _tokenize(query)
    name_tokens = _tokenize(drug_name)

    if not query_tokens or not name_tokens:
        return 0.0

    # Signal 0 (Q6 floor): at least one query token must appear as a whole
    # token in the product name OR brand. Without it, return 0.0 even when
    # the drug_class is pharma — this stops Algolia/VTEX fuzzy hits
    # ("Aspirador Nasal" for "Aspirina") from squeaking through at exactly
    # the threshold via the category bonus alone.
    #
    # Q8 fix (v0.26.0): digit-only tokens (e.g. "500", "50") are excluded
    # from the floor check.  Dosage numbers like "500" appear across many
    # unrelated products ("Aspirina 500" vs "Vitamina C 500 Mg"), so they
    # must NOT satisfy the floor on their own.  They still participate in
    # the overlap *score* (Signal 1) once the floor is passed.
    #
    # Q9 fix (v0.29.0): FORM_WORDS (e.g. "crema", "gel", "jarabe") are
    # also excluded from the floor check.  These dosage-form descriptors
    # appear in thousands of unrelated products (toothpaste, deodorant,
    # wet wipes all contain "crema").  Searching "crema queloides" must
    # NOT let "Crema Dental Colgate" pass the floor just because "crema"
    # overlaps.  Form words still contribute to the overlap *score*
    # (Signal 1) once the floor is passed by a meaningful token.
    brand_tokens = _tokenize(brand) if brand else set()
    name_overlap = query_tokens & name_tokens
    brand_overlap = query_tokens & brand_tokens
    # Strip digit-only tokens and form words for the floor gate.
    # _NOISE_TOKENS are already removed by _tokenize() so they cannot
    # appear in overlap sets — only digits and FORM_WORDS need filtering.
    name_overlap_meaningful = {
        t for t in name_overlap if not t.isdigit() and t not in FORM_WORDS
    }
    brand_overlap_meaningful = {
        t for t in brand_overlap if not t.isdigit() and t not in FORM_WORDS
    }
    if not name_overlap_meaningful and not brand_overlap_meaningful:
        # A query consisting entirely of form words and/or digits (e.g.
        # "crema", "gel 500") intentionally returns 0.0.  Form words alone
        # do not identify a specific product — the AI classifier should
        # always pair them with an ingredient or condition name.
        return 0.0

    score = 0.0

    # Signal 1: Token overlap (0.0 – 0.5). Use whichever of name/brand
    # gives the larger overlap — typing the brand should score as well as
    # typing the generic name.
    if len(name_overlap) >= len(brand_overlap):
        effective_overlap = name_overlap
    else:
        effective_overlap = brand_overlap
    score += 0.5 * len(effective_overlap) / len(query_tokens)

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
    if ingredient_tokens and (
        (ingredient_tokens & name_tokens) or (ingredient_tokens & brand_tokens)
    ):
        score += 0.2

    # Signal 4: Form-conflict penalty (Item 123, v0.44.0)
    # When the user explicitly specifies a dosage form (e.g., "tabletas
    # recubiertas") and the product name contains a *different* form group
    # (e.g., "crema vaginal"), the product is disqualified (score → 0.0).
    # This prevents "Estrógenos Conjugados Crema Vaginal" from appearing
    # when the user searched for "tabletas recubiertas".
    #
    # Rules:
    # - No form in query → no penalty (user doesn't care about form)
    # - No form in product name → no penalty (ambiguous product)
    # - Same form group → no penalty (match)
    # - Different form groups with no intersection → score = 0.0
    query_forms = _extract_form_groups(query_tokens)
    if query_forms:
        # Intentionally name-only: brand fields rarely carry dosage-form
        # data, and checking brand could create false conflicts when the
        # brand name happens to contain a form word.
        product_forms = _extract_form_groups(name_tokens)
        if product_forms and not (query_forms & product_forms):
            return 0.0

    return min(score, 1.0)


def is_relevant(
    query: str,
    drug_name: str,
    drug_class: str | None = None,
    description: str | None = None,
    threshold: float = 0.3,
    brand: str | None = None,
) -> bool:
    """Check if a product meets the relevance threshold for a query.

    Args:
        query: User's search query.
        drug_name: Product name from the pharmacy API.
        drug_class: Product category.
        description: Product description.
        threshold: Minimum score to be considered relevant.
        brand: Optional brand/manufacturer field — see
            :func:`compute_relevance` for how it's used.

    Returns:
        True if the product is relevant to the query.
    """
    return (
        compute_relevance(query, drug_name, drug_class, description, brand=brand)
        >= threshold
    )


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
