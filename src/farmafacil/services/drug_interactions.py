"""Drug interaction checking via RxNorm/RxNav API.

Provides real-time drug interaction detection using the NIH RxNav API.
No authentication required. Rate limit: 20 requests/second.

Flow:
1. Convert drug names to RxCUI identifiers via RxNorm lookup
2. Check for interactions between RxCUIs via the Interaction API
3. Return structured interaction data with severity and descriptions
"""

import logging
import re
import unicodedata
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

RXNORM_BASE = "https://rxnav.nlm.nih.gov/REST"
REQUEST_TIMEOUT = 10

# Common Spanish→English drug name mappings for RxNorm lookup
_SPANISH_TO_ENGLISH: dict[str, str] = {
    "aspirina": "aspirin",
    "acetaminofen": "acetaminophen",
    "acetaminofén": "acetaminophen",
    "ibuprofeno": "ibuprofen",
    "omeprazol": "omeprazole",
    "losartan": "losartan",
    "losartán": "losartan",
    "enalapril": "enalapril",
    "amlodipino": "amlodipine",
    "metformina": "metformin",
    "loratadina": "loratadine",
    "cetirizina": "cetirizine",
    "diclofenac": "diclofenac",
    "warfarina": "warfarin",
    "clopidogrel": "clopidogrel",
    "ranitidina": "ranitidine",
    "amoxicilina": "amoxicillin",
    "azitromicina": "azithromycin",
    "ciprofloxacina": "ciprofloxacin",
    "prednisona": "prednisone",
    "dexametasona": "dexamethasone",
    "furosemida": "furosemide",
    "hidroclorotiazida": "hydrochlorothiazide",
    "atorvastatina": "atorvastatin",
    "simvastatina": "simvastatin",
    "insulina": "insulin",
    "levotiroxina": "levothyroxine",
    "alprazolam": "alprazolam",
    "clonazepam": "clonazepam",
    "fluoxetina": "fluoxetine",
    "sertralina": "sertraline",
}

# Deduplicated set of all known drug names (Spanish keys + English-only values)
_ALL_DRUG_NAMES: set[str] = set(_SPANISH_TO_ENGLISH.keys()) | set(
    _SPANISH_TO_ENGLISH.values()
)


@dataclass
class DrugInteraction:
    """A detected drug interaction between two substances."""

    drug_a: str
    drug_b: str
    description: str
    severity: str  # "N/A", or extracted from description


@dataclass
class InteractionResult:
    """Result of an interaction check."""

    has_interactions: bool
    interactions: list[DrugInteraction]
    drugs_checked: list[str]
    error: str | None = None


def _normalize_drug_name(name: str) -> str:
    """Normalize a drug name for RxNorm lookup.

    Strips dosage info, converts Spanish names to English equivalents,
    and lowercases for consistent matching.

    Args:
        name: Raw drug name (possibly Spanish, with dosage).

    Returns:
        Normalized English drug name for API lookup.
    """
    # Lowercase and strip
    name = name.lower().strip()

    # Remove common dosage patterns (50mg, 100 mg, etc.) and form words
    name = re.sub(r"\d+\s*(?:mg|ml|g|mcg|ui)", "", name)
    name = re.sub(
        r"\b(?:tabletas?|capsulas?|comprimidos?|gotas|jarabe|crema|gel)\b", "", name
    )
    name = name.strip()

    # Check Spanish→English mapping
    if name in _SPANISH_TO_ENGLISH:
        return _SPANISH_TO_ENGLISH[name]

    # Try without accents
    normalized = "".join(
        c for c in unicodedata.normalize("NFD", name)
        if unicodedata.category(c) != "Mn"
    )
    if normalized in _SPANISH_TO_ENGLISH:
        return _SPANISH_TO_ENGLISH[normalized]

    # Return as-is (many drug names are the same in Spanish/English)
    return name


async def lookup_rxcui(
    drug_name: str,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Look up the RxCUI identifier for a drug name.

    Args:
        drug_name: Drug name (English or Spanish).
        client: Optional shared httpx client (avoids opening a new connection per call).

    Returns:
        RxCUI string or None if not found.
    """
    english_name = _normalize_drug_name(drug_name)
    url = f"{RXNORM_BASE}/rxcui.json"
    params = {"name": english_name, "search": 2}  # search=2 is approximate

    try:
        if client is not None:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        else:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as _client:
                resp = await _client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

        # Extract RxCUI from response
        id_group = data.get("idGroup", {})
        rxnorm_id = id_group.get("rxnormId")
        if rxnorm_id:
            logger.debug("RxCUI for '%s' → %s", english_name, rxnorm_id)
            return rxnorm_id[0] if isinstance(rxnorm_id, list) else rxnorm_id

        logger.debug("No RxCUI found for '%s'", english_name)
        return None

    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
        logger.warning("RxNorm lookup failed for '%s': %s", drug_name, exc)
        return None


async def check_interactions(drug_names: list[str]) -> InteractionResult:
    """Check for interactions between a list of drugs.

    Looks up RxCUI for each drug, then queries the RxNav interaction API.

    Args:
        drug_names: List of drug names to check (Spanish or English).

    Returns:
        InteractionResult with any detected interactions.
    """
    if len(drug_names) < 2:
        # Return raw names — no resolution attempted
        return InteractionResult(
            has_interactions=False,
            interactions=[],
            drugs_checked=drug_names,
        )

    # Step 1: Look up RxCUIs for all drugs using a shared HTTP client
    rxcui_map: dict[str, str] = {}  # drug_name → rxcui
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for name in drug_names:
            rxcui = await lookup_rxcui(name, client=client)
            if rxcui:
                rxcui_map[name] = rxcui

    if len(rxcui_map) < 2:
        logger.info(
            "Only %d of %d drugs resolved to RxCUI — skipping interaction check",
            len(rxcui_map), len(drug_names),
        )
        return InteractionResult(
            has_interactions=False,
            interactions=[],
            drugs_checked=list(rxcui_map.keys()),
        )

    # Step 2: Query interaction API
    # RxNav list API expects RxCUIs joined by "+" — see https://rxnav.nlm.nih.gov/InteractionAPIs.html
    rxcui_list = "+".join(rxcui_map.values())
    url = f"{RXNORM_BASE}/interaction/list.json"
    params = {"rxcuis": rxcui_list}

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        # Step 3: Parse interactions
        interactions: list[DrugInteraction] = []
        for group in data.get("fullInteractionTypeGroup", []):
            for interaction_type in group.get("fullInteractionType", []):
                for pair in interaction_type.get("interactionPair", []):
                    description = pair.get("description", "")
                    severity = pair.get("severity", "N/A")

                    # Extract drug names from the interaction pair
                    concepts = pair.get("interactionConcept", [])
                    drug_a = (
                        concepts[0].get("minConceptItem", {}).get("name", "Unknown")
                        if len(concepts) > 0
                        else "Unknown"
                    )
                    drug_b = (
                        concepts[1].get("minConceptItem", {}).get("name", "Unknown")
                        if len(concepts) > 1
                        else "Unknown"
                    )

                    interactions.append(DrugInteraction(
                        drug_a=drug_a,
                        drug_b=drug_b,
                        description=description,
                        severity=severity,
                    ))

        logger.info(
            "Interaction check: %d drugs, %d interactions found",
            len(rxcui_map), len(interactions),
        )

        return InteractionResult(
            has_interactions=len(interactions) > 0,
            interactions=interactions,
            drugs_checked=list(rxcui_map.keys()),
        )

    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
        logger.warning("RxNav interaction check failed: %s", exc)
        return InteractionResult(
            has_interactions=False,
            interactions=[],
            drugs_checked=list(rxcui_map.keys()),
            error="Interaction API unavailable",
        )


def format_interaction_warning(result: InteractionResult) -> str:
    """Format interaction results into a Spanish warning message.

    Args:
        result: InteractionResult from check_interactions.

    Returns:
        Formatted warning string, or empty string if no interactions.
    """
    if not result.has_interactions:
        return ""

    lines = ["\u26a0\ufe0f *Alerta de interaccion:*\n"]
    for ix in result.interactions[:3]:  # Limit to 3 most important
        lines.append(
            f"\u2022 *{ix.drug_a}* + *{ix.drug_b}*: {ix.description[:200]}"
        )

    lines.append(
        "\n\U0001f6a8 *Consulta con tu medico o farmaceutico antes de combinar estos medicamentos.*"
    )
    return "\n".join(lines)


def extract_medications_from_memory(memory_text: str | None) -> list[str]:
    """Extract known medications from a user's memory text.

    Scans the memory for mentions of drug names that the user takes.
    Uses the deduplicated set of all known drug names to avoid returning
    both Spanish and English forms for the same drug.

    Args:
        memory_text: The user's memory text from user_memories table.

    Returns:
        List of medication names found in the memory.
    """
    if not memory_text:
        return []

    memory_lower = memory_text.lower()
    found: list[str] = []

    # Check all known drug names (deduplicated set avoids Spanish+English dupes)
    for drug in sorted(_ALL_DRUG_NAMES):
        if drug in memory_lower and drug not in found:
            found.append(drug)

    return found
