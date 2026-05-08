"""Tests for the intent detection service."""

import pytest

from farmafacil.services.intent import classify_intent_keywords


class TestKeywordIntent:
    """Test keyword-based intent classification."""

    async def test_greeting_hola(self):
        intent = await classify_intent_keywords("hola")
        assert intent is not None
        assert intent.action == "greeting"

    async def test_greeting_buenas(self):
        intent = await classify_intent_keywords("buenas")
        assert intent is not None
        assert intent.action == "greeting"

    async def test_location_change(self):
        intent = await classify_intent_keywords("cambiar zona")
        assert intent is not None
        assert intent.action == "location_change"

    async def test_location_change_accent(self):
        intent = await classify_intent_keywords("cambiar ubicación")
        assert intent is not None
        assert intent.action == "location_change"

    async def test_help(self):
        intent = await classify_intent_keywords("ayuda")
        assert intent is not None
        assert intent.action == "help"

    async def test_farewell(self):
        intent = await classify_intent_keywords("gracias")
        assert intent is not None
        assert intent.action == "farewell"
        assert intent.response_text is not None

    async def test_short_drug_name(self):
        """Short text without question marks → drug search."""
        intent = await classify_intent_keywords("losartan")
        assert intent is not None
        assert intent.action == "drug_search"
        assert intent.drug_query == "losartan"

    async def test_drug_name_with_dose(self):
        intent = await classify_intent_keywords("losartan 50mg")
        assert intent is not None
        assert intent.action == "drug_search"

    async def test_drug_name_longer(self):
        intent = await classify_intent_keywords("losartan potasico 50mg tabletas")
        assert intent is not None
        assert intent.action == "drug_search"

    async def test_question_returns_none(self):
        """Question should return None (needs LLM)."""
        intent = await classify_intent_keywords("cuanto cuesta el delivery?")
        assert intent is None

    async def test_conversational_question_returns_none(self):
        intent = await classify_intent_keywords("tienen algo para el dolor de cabeza?")
        assert intent is None

    async def test_symptom_description_returns_none(self):
        intent = await classify_intent_keywords("que puedo tomar para la gripe")
        assert intent is None

    async def test_long_drug_name_still_classified(self):
        intent = await classify_intent_keywords("acetaminofen 500mg caja por 20 tabletas recubiertas")
        assert intent is not None
        assert intent.action == "drug_search"

    async def test_very_long_text_returns_none(self):
        intent = await classify_intent_keywords(
            "hola buenos dias necesito saber si tienen disponible algun medicamento "
            "para la presion arterial alta porque mi mama lo necesita urgente"
        )
        assert intent is None

    async def test_full_product_name_with_special_chars_classified_as_drug_search(self):
        """Specific product name with special chars (+ sign) is a drug search."""
        intent = await classify_intent_keywords("RESVERATROL NAD+VID CAP 125MG X60 HERB")
        assert intent is not None
        assert intent.action == "drug_search"

    async def test_full_product_name_preserved_in_drug_query(self):
        """Full product name is returned verbatim in drug_query, not simplified."""
        full_name = "RESVERATROL NAD+VID CAP 125MG X60 HERB"
        intent = await classify_intent_keywords(full_name)
        assert intent is not None
        assert intent.drug_query == full_name

    async def test_product_name_preserves_original_casing(self):
        """drug_query preserves the original casing sent by the user."""
        original = "Losartan Potasico 50mg Biumak Caja x30"
        intent = await classify_intent_keywords(original)
        assert intent is not None
        assert intent.drug_query == original

    async def test_product_name_at_word_boundary_8_words(self):
        """An 8-word drug name (the upper boundary) is classified as drug_search."""
        # Exactly 8 words — at the boundary of the second heuristic (len(words) <= 8)
        drug_name = "Atorvastatina 20mg Genven Caja por 30 Tabletas Recubiertas"
        intent = await classify_intent_keywords(drug_name)
        assert intent is not None
        assert intent.action == "drug_search"

    async def test_product_name_over_8_words_returns_none(self):
        """More than 8 words without question markers falls through to LLM (returns None)."""
        # 9 words — exceeds the keyword heuristic boundary
        long_text = "necesito encontrar el medicamento losartan 50mg potasico tabletas caja"
        intent = await classify_intent_keywords(long_text)
        # 9 words, no question marker → None (ambiguous, needs LLM)
        assert intent is None

    async def test_drug_search_with_plus_sign_not_treated_as_question(self):
        """A product name containing '+' is not confused with a question."""
        intent = await classify_intent_keywords("Vitamina C + Zinc 500mg")
        assert intent is not None
        assert intent.action == "drug_search"

    async def test_keyword_match_returns_action_from_db(self):
        """Exact keyword match returns action without entering drug_search heuristics."""
        intent = await classify_intent_keywords("ayuda")
        assert intent is not None
        assert intent.action == "help"

    async def test_question_with_question_mark_returns_none(self):
        """Any text with '?' is passed to LLM regardless of word count."""
        # Short (2 words) but has a question mark → should NOT be classified as drug search
        intent = await classify_intent_keywords("losartan?")
        assert intent is None

    async def test_question_starting_with_tienen_returns_none(self):
        """Text starting with 'tienen' is treated as a question even without '?'."""
        intent = await classify_intent_keywords("tienen losartan")
        assert intent is None

    async def test_view_similar_keyword(self):
        """'ver similares' is classified as view_similar action."""
        intent = await classify_intent_keywords("ver similares")
        assert intent is not None
        assert intent.action == "view_similar"

    async def test_similares_keyword(self):
        """'similares' alone is classified as view_similar action."""
        intent = await classify_intent_keywords("similares")
        assert intent is not None
        assert intent.action == "view_similar"

    async def test_ver_otros_keyword(self):
        """'ver otros' is classified as view_similar action."""
        intent = await classify_intent_keywords("ver otros")
        assert intent is not None
        assert intent.action == "view_similar"
