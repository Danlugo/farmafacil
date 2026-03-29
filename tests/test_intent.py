"""Tests for the intent detection service."""

from farmafacil.services.intent import classify_intent_keywords


class TestKeywordIntent:
    """Test keyword-based intent classification (no LLM)."""

    def test_greeting_hola(self):
        intent = classify_intent_keywords("hola")
        assert intent is not None
        assert intent.action == "greeting"

    def test_greeting_buenas(self):
        intent = classify_intent_keywords("buenas")
        assert intent is not None
        assert intent.action == "greeting"

    def test_location_change(self):
        intent = classify_intent_keywords("cambiar zona")
        assert intent is not None
        assert intent.action == "location_change"

    def test_location_change_accent(self):
        intent = classify_intent_keywords("cambiar ubicación")
        assert intent is not None
        assert intent.action == "location_change"

    def test_help(self):
        intent = classify_intent_keywords("ayuda")
        assert intent is not None
        assert intent.action == "help"
        assert intent.response_text is not None

    def test_short_drug_name(self):
        """Short text without question marks → drug search."""
        intent = classify_intent_keywords("losartan")
        assert intent is not None
        assert intent.action == "drug_search"
        assert intent.drug_query == "losartan"

    def test_drug_name_with_dose(self):
        """Drug name with dosage → drug search."""
        intent = classify_intent_keywords("losartan 50mg")
        assert intent is not None
        assert intent.action == "drug_search"
        assert intent.drug_query == "losartan 50mg"

    def test_drug_name_longer(self):
        """Multi-word drug name → drug search."""
        intent = classify_intent_keywords("losartan potasico 50mg tabletas")
        assert intent is not None
        assert intent.action == "drug_search"

    def test_question_returns_none(self):
        """Question should return None (needs LLM)."""
        intent = classify_intent_keywords("cuanto cuesta el delivery?")
        assert intent is None

    def test_conversational_question_returns_none(self):
        """Conversational query should return None (needs LLM)."""
        intent = classify_intent_keywords("tienen algo para el dolor de cabeza?")
        assert intent is None

    def test_symptom_description_returns_none(self):
        """Symptom description with question starter → None (needs LLM)."""
        intent = classify_intent_keywords("que puedo tomar para la gripe")
        assert intent is None

    def test_long_drug_name_still_classified(self):
        """Up to 8 words without question → drug search."""
        intent = classify_intent_keywords("acetaminofen 500mg caja por 20 tabletas recubiertas")
        assert intent is not None
        assert intent.action == "drug_search"

    def test_very_long_text_returns_none(self):
        """Very long text → None (needs LLM)."""
        intent = classify_intent_keywords(
            "hola buenos dias necesito saber si tienen disponible algun medicamento "
            "para la presion arterial alta porque mi mama lo necesita urgente"
        )
        assert intent is None
