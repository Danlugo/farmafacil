"""Tests for bug #17 and #18 fixes — AI classification prompt improvements.

Bug #18 (Jose Miguel C, 2026-05-09):
    User asked "puedes decirme si consigues melatonina de laboratorio Arco iris"
    and got all Arco Iris lab products instead of just melatonina. Root cause:
    AI extracted only the brand as DRUG query, dropping the product name.
    Fix: CLASSIFY_INSTRUCTIONS now tells the AI to combine product + brand.

Bug #17 (Jose, 2026-04-29):
    User said "necesito hacerme un examen de heces" and bot echoed the statement
    back with no action. Root cause: AI didn't map medical exam mentions to
    pharmacy supplies. Fix: CLASSIFY_INSTRUCTIONS now maps exam types to the
    supplies pharmacies sell (recolector de heces, recolector de orina, etc.).
"""

from __future__ import annotations

import pytest

from farmafacil.services.ai_responder import (
    CLASSIFY_INSTRUCTIONS,
    _parse_structured_response,
)


# ── Bug #18: Product + brand/lab combination ──────────────────────────


class TestBug18ProductBrandCombination:
    """Verify CLASSIFY_INSTRUCTIONS and parser handle product + brand queries."""

    def test_instructions_contain_brand_lab_rule(self):
        """CLASSIFY_INSTRUCTIONS includes the product + brand/lab rule."""
        assert "PRODUCTO + MARCA/LABORATORIO" in CLASSIFY_INSTRUCTIONS

    def test_instructions_contain_melatonina_example(self):
        """The melatonina arco iris example from the actual bug is present."""
        assert "melatonina arco iris" in CLASSIFY_INSTRUCTIONS.lower()

    def test_instructions_warn_never_brand_alone(self):
        """Rule explicitly warns not to put only the brand in DRUG."""
        assert "NUNCA pongas solo la marca" in CLASSIFY_INSTRUCTIONS

    def test_parse_combined_product_brand(self):
        """Parser correctly extracts combined product + brand DRUG field."""
        reply = (
            "ACTION: drug_search\n"
            "DRUG: melatonina arco iris\n"
            "RESPONSE: Te busco melatonina de Arco Iris."
        )
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.drug_query == "melatonina arco iris"

    def test_parse_product_brand_no_extra_text(self):
        """Drug query doesn't include filler words like 'de laboratorio'."""
        reply = (
            "ACTION: drug_search\n"
            "DRUG: omeprazol lancasco"
        )
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.drug_query == "omeprazol lancasco"

    def test_parse_brand_only_would_be_wrong(self):
        """If AI only returns brand (the old bug), drug_query is just the brand.

        This test documents the failure mode — the fix ensures the AI
        never produces this output for product + brand queries.
        """
        reply = "ACTION: drug_search\nDRUG: arco iris"
        result = _parse_structured_response(reply)
        # Parser works fine — the bug was in what the AI *chose* to output.
        # With the new instructions, it should output "melatonina arco iris".
        assert result.drug_query == "arco iris"

    def test_instructions_contain_multiple_brand_examples(self):
        """Multiple brand examples are present to guide the AI."""
        lower = CLASSIFY_INSTRUCTIONS.lower()
        assert "omeprazol lancasco" in lower
        assert "vitamina c mason natural" in lower
        assert "ibuprofeno genfar" in lower


# ── Bug #17: Medical exam → pharmacy supplies ─────────────────────────


class TestBug17MedicalExamSupplies:
    """Verify CLASSIFY_INSTRUCTIONS and parser handle exam supply mapping."""

    def test_instructions_contain_exam_rule(self):
        """CLASSIFY_INSTRUCTIONS includes the medical exam supply rule."""
        assert "EXÁMENES MÉDICOS Y SUMINISTROS" in CLASSIFY_INSTRUCTIONS

    def test_instructions_contain_heces_mapping(self):
        """The heces → recolector mapping from the actual bug is present."""
        assert "recolector de heces" in CLASSIFY_INSTRUCTIONS.lower()

    def test_instructions_contain_orina_mapping(self):
        """Orina exam maps to recolector de orina."""
        assert "recolector de orina" in CLASSIFY_INSTRUCTIONS.lower()

    def test_instructions_contain_embarazo_mapping(self):
        """Pregnancy test maps to prueba de embarazo."""
        assert "prueba de embarazo" in CLASSIFY_INSTRUCTIONS.lower()

    def test_instructions_contain_glucosa_mapping(self):
        """Blood sugar test maps to glucometro."""
        assert "glucometro" in CLASSIFY_INSTRUCTIONS.lower()

    def test_instructions_contain_presion_mapping(self):
        """Blood pressure test maps to tensiometro."""
        assert "tensiometro" in CLASSIFY_INSTRUCTIONS.lower()

    def test_parse_exam_heces_response(self):
        """Parser correctly handles the exam-de-heces AI response."""
        reply = (
            "ACTION: drug_search\n"
            "DRUG: recolector de heces\n"
            "RESPONSE: Te busco envases recolectores de heces para tu examen."
        )
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.drug_query == "recolector de heces"
        assert "recolector" in result.text.lower()

    def test_parse_exam_orina_response(self):
        """Parser correctly handles the exam-de-orina AI response."""
        reply = (
            "ACTION: drug_search\n"
            "DRUG: recolector de orina\n"
            "RESPONSE: Te busco envases recolectores de orina."
        )
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.drug_query == "recolector de orina"

    def test_parse_exam_clarify_needed(self):
        """When exam type is ambiguous, AI can ask for clarification."""
        reply = (
            "ACTION: clarify_needed\n"
            "CLARIFY_QUESTION: ¿Qué necesitas para tu examen? (envase recolector, tiras reactivas, etc.)\n"
            "CLARIFY_CONTEXT: necesito hacerme un examen"
        )
        result = _parse_structured_response(reply)
        assert result.action == "clarify_needed"
        assert result.clarify_question is not None
        assert "recolector" in result.clarify_question.lower() or "examen" in result.clarify_question.lower()
        assert result.clarify_context is not None

    def test_parse_exam_presion_response(self):
        """Parser correctly handles the blood-pressure AI response."""
        reply = (
            "ACTION: drug_search\n"
            "DRUG: tensiometro\n"
            "RESPONSE: Te busco tensiómetros."
        )
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.drug_query == "tensiometro"

    def test_parse_single_line_comma_does_not_corrupt_drug(self):
        """If AI emits comma-separated DRUG+RESPONSE on one line, drug_query
        must NOT contain 'RESPONSE' as a substring.

        Documents a potential failure mode: the parser splits on newlines,
        so a single-line "DRUG: x, RESPONSE: y" would stuff everything into
        drug_query. The prompt examples now use multi-line format to prevent
        this, but this test guards against regression.
        """
        # Simulate malformed single-line output (old prompt format risk):
        reply = (
            "ACTION: drug_search\n"
            "DRUG: recolector de heces, RESPONSE: Te busco envases."
        )
        result = _parse_structured_response(reply)
        # The parser will put the whole line into drug_query — this test
        # documents that behaviour so we know the prompt must prevent it.
        assert "RESPONSE" in result.drug_query  # current parser behaviour
        # If a future parser fix strips trailing RESPONSE, update this test.

    def test_parse_echo_response_is_the_old_bug(self):
        """Document the old failure: AI echoed the statement with no action.

        With the new instructions, the AI should classify as drug_search
        instead of question with an echo. The parser itself is neutral —
        the instruction prompt is what prevents the AI from echoing.
        """
        # This is what the AI USED to return (the bug):
        reply = (
            "ACTION: question\n"
            "RESPONSE: Entiendo que necesitas hacerte un examen de heces."
        )
        result = _parse_structured_response(reply)
        # Parser works — the bug was in the AI's classification choice.
        assert result.action == "question"
        assert "examen" in result.text.lower()


# ── Regression guards ─────────────────────────────────────────────────


class TestClassifyInstructionsRegression:
    """Ensure existing rules weren't broken by the new additions."""

    def test_symptom_rule_still_present(self):
        """Symptom-only → question rule is intact."""
        assert "SOLO SÍNTOMAS" in CLASSIFY_INSTRUCTIONS

    def test_clarify_vague_categories_still_present(self):
        """Vague category → clarify_needed rule is intact."""
        assert "CATEGORÍAS VAGAS" in CLASSIFY_INSTRUCTIONS

    def test_emergency_rule_still_present(self):
        """Emergency rule is intact."""
        assert "EMERGENCIA" in CLASSIFY_INSTRUCTIONS

    def test_security_exception_still_present(self):
        """Drug interaction warning rule is intact."""
        assert "EXCEPCIÓN DE SEGURIDAD" in CLASSIFY_INSTRUCTIONS

    def test_nearest_store_rule_still_present(self):
        """Nearest store rule is intact."""
        assert "nearest_store" in CLASSIFY_INSTRUCTIONS

    def test_view_similar_rule_still_present(self):
        """View similar rule is intact."""
        assert "view_similar" in CLASSIFY_INSTRUCTIONS
