"""Tests for symptom acknowledgment and typing indicator features."""

import pytest

from farmafacil.bot.whatsapp import send_read_receipt
from farmafacil.services.ai_responder import _parse_structured_response, AiResponse


# ── Symptom response parsing ──────────────────────────────────────────


class TestSymptomResponseParsing:
    """Verify _parse_structured_response correctly extracts RESPONSE alongside DRUG."""

    def test_symptom_with_response_and_drug(self):
        """AI should return both DRUG and RESPONSE for symptom messages."""
        reply = (
            "ACTION: drug_search\n"
            "DRUG: Omeprazol\n"
            "RESPONSE: Entiendo que tienes acidez estomacal. "
            "Te voy a buscar Omeprazol que es lo más común para eso. "
            "Recuerda que si es frecuente, consulta con tu médico."
        )
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.drug_query == "Omeprazol"
        assert result.text  # RESPONSE should be non-empty
        assert "acidez" in result.text.lower() or "omeprazol" in result.text.lower()

    def test_drug_search_without_symptom_no_response(self):
        """Direct product search should NOT have a RESPONSE."""
        reply = "ACTION: drug_search\nDRUG: Losartan 50mg"
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.drug_query == "Losartan 50mg"
        assert result.text == ""

    def test_symptom_headache(self):
        """Headache symptom should map to Acetaminofén with response."""
        reply = (
            "ACTION: drug_search\n"
            "DRUG: Acetaminofén\n"
            "RESPONSE: Entiendo que te duele la cabeza. "
            "Te busco Acetaminofén que es lo más usado para el dolor de cabeza. "
            "Si persiste, consulta con tu médico."
        )
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.drug_query == "Acetaminofén"
        assert len(result.text) > 10  # Must be a real response

    def test_symptom_allergy(self):
        """Allergy symptom should include conversational response."""
        reply = (
            "ACTION: drug_search\n"
            "DRUG: Loratadina\n"
            "RESPONSE: Veo que tienes síntomas de alergia. "
            "Te busco Loratadina, es un antihistamínico muy efectivo. "
            "Si los síntomas no mejoran, visita al médico."
        )
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.drug_query == "Loratadina"
        assert result.text != ""

    def test_greeting_no_drug(self):
        """Greeting should have no drug query."""
        reply = "ACTION: greeting\nRESPONSE: ¡Hola! ¿En qué te puedo ayudar?"
        result = _parse_structured_response(reply)
        assert result.action == "greeting"
        assert result.drug_query is None
        assert result.text == "¡Hola! ¿En qué te puedo ayudar?"

    def test_response_field_preserves_full_text(self):
        """RESPONSE field should preserve the complete conversational text."""
        long_response = (
            "Entiendo que tienes acidez estomacal. Te voy a buscar Omeprazol, "
            "que es un inhibidor de la bomba de protones muy efectivo para la acidez. "
            "Si los síntomas persisten más de 2 semanas, te recomiendo consultar "
            "con un gastroenterólogo."
        )
        reply = f"ACTION: drug_search\nDRUG: Omeprazol\nRESPONSE: {long_response}"
        result = _parse_structured_response(reply)
        assert result.text == long_response


# ── Seed data: symptom_translation skill ──────────────────────────────


class TestSymptomAcknowledgmentSkill:
    """Verify the seed data for symptom_acknowledgment skill (v0.14.2 — replaces symptom_translation)."""

    def test_skill_content_includes_acknowledge(self):
        """Skill should instruct AI to acknowledge symptoms with empathy."""
        from farmafacil.db.seed import DEFAULT_ROLES

        pharmacy_role = next(r for r in DEFAULT_ROLES if r["name"] == "pharmacy_advisor")
        symptom_skill = next(
            s for s in pharmacy_role["skills"] if s["name"] == "symptom_acknowledgment"
        )
        content = symptom_skill["content"].lower()
        assert "reconoce" in content or "empatía" in content

    def test_skill_content_does_not_recommend_drugs(self):
        """Skill must NOT instruct AI to suggest specific drugs for symptoms."""
        from farmafacil.db.seed import DEFAULT_ROLES

        pharmacy_role = next(r for r in DEFAULT_ROLES if r["name"] == "pharmacy_advisor")
        symptom_skill = next(
            s for s in pharmacy_role["skills"] if s["name"] == "symptom_acknowledgment"
        )
        content = symptom_skill["content"]
        # Must NOT have symptom→drug mapping tables
        assert "→ Aspirina" not in content
        assert "→ Losartán" not in content
        assert "→ Metformina" not in content

    def test_skill_content_mentions_doctor(self):
        """Skill should always route to doctor/pharmacist."""
        from farmafacil.db.seed import DEFAULT_ROLES

        pharmacy_role = next(r for r in DEFAULT_ROLES if r["name"] == "pharmacy_advisor")
        symptom_skill = next(
            s for s in pharmacy_role["skills"] if s["name"] == "symptom_acknowledgment"
        )
        content = symptom_skill["content"].lower()
        assert "médico" in content

    def test_skill_description_updated(self):
        """Skill description should reflect the no-recommendation policy."""
        from farmafacil.db.seed import DEFAULT_ROLES

        pharmacy_role = next(r for r in DEFAULT_ROLES if r["name"] == "pharmacy_advisor")
        symptom_skill = next(
            s for s in pharmacy_role["skills"] if s["name"] == "symptom_acknowledgment"
        )
        desc = symptom_skill["description"].lower()
        assert "acknowledge" in desc or "never recommend" in desc


# ── Classification instructions ───────────────────────────────────────


class TestClassificationInstructions:
    """Verify AI responder classification instructions handle symptoms."""

    def test_classify_instructions_mention_symptoms(self):
        """Classification prompt should include symptom handling rules."""
        import inspect
        from farmafacil.services.ai_responder import classify_with_ai

        source = inspect.getsource(classify_with_ai)
        assert "SÍNTOMAS" in source or "síntomas" in source

    def test_classify_instructions_require_response_for_symptoms(self):
        """Classification prompt should require RESPONSE for symptom messages."""
        import inspect
        from farmafacil.services.ai_responder import classify_with_ai

        source = inspect.getsource(classify_with_ai)
        assert "RESPONSE" in source
        # Should instruct to include both DRUG and RESPONSE for symptoms
        assert "drug_search" in source.lower()


# ── Typing indicator ──────────────────────────────────────────────────


class TestReadReceipt:
    """Verify read receipt is imported and called in handler."""

    def test_read_receipt_imported_in_handler(self):
        """Handler should import send_read_receipt."""
        import inspect
        from farmafacil.bot import handler

        source = inspect.getsource(handler)
        assert "send_read_receipt" in source

    def test_read_receipt_called_early_in_handler(self):
        """Read receipt should be called near the top of handle_incoming_message."""
        import inspect
        from farmafacil.bot.handler import handle_incoming_message

        source = inspect.getsource(handle_incoming_message)
        lines = source.split("\n")
        # Find the line with send_read_receipt
        receipt_line = None
        for i, line in enumerate(lines):
            if "send_read_receipt" in line and "import" not in line:
                receipt_line = i
                break
        assert receipt_line is not None, "send_read_receipt not found in handler"
        # Should be within the first 25 lines of the function (after docstring)
        assert receipt_line < 25, f"Read receipt at line {receipt_line}, should be early"

    def test_read_receipt_function_exists(self):
        """send_read_receipt should be a callable async function."""
        import inspect
        assert callable(send_read_receipt)
        assert inspect.iscoroutinefunction(send_read_receipt)

    def test_read_receipt_is_fire_and_forget(self):
        """Read receipt should be called via asyncio.create_task (non-blocking)."""
        import inspect
        from farmafacil.bot.handler import handle_incoming_message

        source = inspect.getsource(handle_incoming_message)
        assert "asyncio.create_task" in source
        assert "send_read_receipt" in source

    def test_handler_accepts_wa_message_id(self):
        """handle_incoming_message should accept wa_message_id parameter."""
        import inspect
        from farmafacil.bot.handler import handle_incoming_message

        sig = inspect.signature(handle_incoming_message)
        assert "wa_message_id" in sig.parameters


# ── Handler: symptom response before search ───────────────────────────


class TestHandlerSymptomFlow:
    """Verify handler sends symptom response before drug search."""

    def test_ai_only_checks_ai_result_text(self):
        """AI-only mode should check ai_result.text before drug search."""
        import inspect
        from farmafacil.bot.handler import handle_incoming_message

        source = inspect.getsource(handle_incoming_message)
        # In AI-only section, after drug_search detection, should check ai_result.text
        assert "ai_result.text" in source

    def test_hybrid_checks_intent_response_text(self):
        """Hybrid mode should check intent.response_text before drug search."""
        import inspect
        from farmafacil.bot.handler import handle_incoming_message

        source = inspect.getsource(handle_incoming_message)
        assert "intent.response_text" in source

    def test_intent_dataclass_has_response_text(self):
        """Intent should have a response_text field."""
        from farmafacil.services.intent import Intent

        intent = Intent(action="drug_search", drug_query="Omeprazol")
        assert hasattr(intent, "response_text")
        assert intent.response_text is None  # Default

    def test_intent_response_text_populated(self):
        """Intent response_text should be populatable."""
        from farmafacil.services.intent import Intent

        intent = Intent(
            action="drug_search",
            drug_query="Omeprazol",
            response_text="Entiendo que tienes acidez. Te busco Omeprazol.",
        )
        assert intent.response_text is not None
        assert "acidez" in intent.response_text
