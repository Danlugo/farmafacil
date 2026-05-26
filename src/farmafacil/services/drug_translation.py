"""English-to-Spanish drug name translation via AI (Item 116, v0.37.0).

When a drug search returns zero results, the query may be an English drug
name (e.g., "amlodipine" instead of "amlodipino").  This module asks Claude
Haiku — with temperature=0 for deterministic, factual output — whether the
query is an English pharmaceutical name and, if so, what the Spanish INN
(International Nonproprietary Name) equivalent is.

The translation is ONLY called on the zero-result fallback path, so normal
Spanish searches incur zero extra latency or cost.
"""

import logging

from anthropic import APIConnectionError, APIError

from farmafacil.config import LLM_MODEL

logger = logging.getLogger(__name__)

# Minimal system prompt — keeps token count low (~60 tokens).
_SYSTEM_PROMPT = (
    "You are a pharmaceutical name translator. "
    "If the input is an English drug or medicine name, respond with ONLY "
    "the Spanish pharmaceutical equivalent (the INN name used in Latin America). "
    "If the input is already in Spanish, is not a drug name, or you are unsure, "
    "respond with ONLY the word NO. "
    "No explanations, no punctuation, no extra text."
)


class TranslationResult:
    """Holds the translated drug name and token usage for accounting."""

    __slots__ = ("name", "input_tokens", "output_tokens")

    def __init__(self, name: str, input_tokens: int, output_tokens: int) -> None:
        self.name = name
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


async def translate_drug_query(query: str) -> TranslationResult | None:
    """Ask Claude if *query* is an English drug name and return the Spanish equivalent.

    Uses temperature=0 for deterministic, factual output.  Returns ``None``
    if the query is already in Spanish, is not a drug name, or the API call
    fails.  Failures are logged but never propagated — the caller simply
    skips the retry.

    Args:
        query: The drug search query that returned zero results.

    Returns:
        :class:`TranslationResult` with the Spanish name and token counts,
        or ``None`` if no translation applies.
    """
    # Avoid calling the API for very short or obviously non-drug text
    stripped = query.strip()
    if not stripped or len(stripped) < 3 or len(stripped) > 100:
        return None

    try:
        # Lazy import to reuse the module-level singleton from ai_responder
        from farmafacil.services.ai_responder import _get_client

        client = _get_client()
        response = await client.messages.create(
            model=LLM_MODEL,
            max_tokens=60,
            temperature=0,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": stripped}],
        )

        # Guard against empty content (e.g. max_tokens hit with 0 output)
        if not response.content:
            logger.warning(
                "Drug translation: empty content for '%s' (stop_reason=%s)",
                stripped, getattr(response, "stop_reason", "unknown"),
            )
            return None

        result = response.content[0].text.strip().rstrip(".")

        # Track token usage for observability
        in_tokens = response.usage.input_tokens
        out_tokens = response.usage.output_tokens

        if result.upper() == "NO" or result.lower() == stripped.lower():
            logger.debug(
                "Drug translation: '%s' → no translation (tokens: %d/%d)",
                stripped, in_tokens, out_tokens,
            )
            return None

        logger.info(
            "Drug translation: '%s' → '%s' (tokens: %d/%d)",
            stripped, result, in_tokens, out_tokens,
        )
        return TranslationResult(result, in_tokens, out_tokens)

    except (APIConnectionError, APIError) as exc:
        logger.warning("Drug translation API error for '%s': %s", stripped, exc)
        return None
    except Exception as exc:
        logger.warning("Drug translation unexpected error for '%s': %s", stripped, exc)
        return None
