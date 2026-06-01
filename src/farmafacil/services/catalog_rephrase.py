"""Zero-result catalog rephrase via AI (Item 128, v0.48.0).

When a drug search returns zero results and the English→Spanish
translation fallback (Item 116) doesn't apply, the query may use
a colloquial or brand name that doesn't match how Farmatodo indexes
the product (e.g., "kinesiotape" → "cinta kinesiológica", "tylenol"
→ "acetaminofén", "curitas" → "vendas adhesivas").

This module asks Claude to rephrase the query the way a Venezuelan
pharmacy catalog (Farmatodo) would actually list the product.
"""

import logging

from anthropic import APIConnectionError, APIError

from farmafacil.config import LLM_MODEL

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a Venezuelan pharmacy catalog expert. "
    "A user searched for a product on Farmatodo Venezuela but got zero results. "
    "If you can identify what product they meant, respond with ONLY the name "
    "that a pharmacy like Farmatodo would use in their catalog. "
    "Use the generic or common catalog name, not a brand name. "
    "Examples: 'kinesiotape' → 'cinta kinesiológica', "
    "'tylenol' → 'acetaminofén', 'curitas' → 'vendas adhesivas', "
    "'agua oxigenada' → 'peróxido de hidrógeno'. "
    "If the input is already a standard pharmacy name, you don't know what "
    "it is, or you cannot improve the search term, respond with ONLY the "
    "word NO. "
    "No explanations, no punctuation, no extra text."
)


class RephraseResult:
    """Holds the rephrased catalog name and token usage for accounting."""

    __slots__ = ("name", "input_tokens", "output_tokens")

    def __init__(self, name: str, input_tokens: int, output_tokens: int) -> None:
        self.name = name
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


async def rephrase_for_catalog(query: str) -> RephraseResult | None:
    """Ask Claude to rephrase *query* as a pharmacy catalog search term.

    Uses temperature=0 for deterministic output.  Returns ``None`` if
    the query is already a good catalog term, cannot be rephrased, or
    the API call fails.  Failures are logged but never propagated.

    Args:
        query: The drug search query that returned zero results.

    Returns:
        :class:`RephraseResult` with the catalog name and token counts,
        or ``None`` if no rephrase applies.
    """
    stripped = query.strip()
    if not stripped or len(stripped) < 3 or len(stripped) > 100:
        return None

    try:
        from farmafacil.services.ai_responder import _get_client

        client = _get_client()
        response = await client.messages.create(
            model=LLM_MODEL,
            max_tokens=80,
            temperature=0,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": stripped}],
        )

        if not response.content:
            logger.warning(
                "Catalog rephrase: empty content for '%s' (stop_reason=%s)",
                stripped, getattr(response, "stop_reason", "unknown"),
            )
            return None

        # Take first line only (guard against multiline AI output)
        result = response.content[0].text.splitlines()[0].strip().rstrip(".")

        in_tokens = response.usage.input_tokens
        out_tokens = response.usage.output_tokens

        if result.lower() == "no" or result.lower() == stripped.lower():
            logger.debug(
                "Catalog rephrase: '%s' → no rephrase (tokens: %d/%d)",
                stripped, in_tokens, out_tokens,
            )
            return None

        logger.info(
            "Catalog rephrase: '%s' → '%s' (tokens: %d/%d)",
            stripped, result, in_tokens, out_tokens,
        )
        return RephraseResult(result, in_tokens, out_tokens)

    except (APIConnectionError, APIError) as exc:
        logger.warning("Catalog rephrase API error for '%s': %s", stripped, exc)
        return None
    except Exception as exc:
        logger.warning("Catalog rephrase unexpected error for '%s': %s", stripped, exc)
        return None
