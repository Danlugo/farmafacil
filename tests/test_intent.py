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
