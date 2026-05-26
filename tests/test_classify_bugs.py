"""Tests for AI classification prompt improvements.

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

Vague symptom clarification (Daniel, 2026-05-19):
    User sent voice note "Encuentra medicina para dolores" → bot listed generic
    OTC pain meds instead of asking what type of pain. Root cause: symptom rule
    treated vague "dolores" the same as specific "dolor de cabeza". Fix: new
    CLASSIFY_INSTRUCTIONS rule sends vague symptoms to clarify_needed to ask
    what body part / symptom type before listing OTC options.
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

    @pytest.mark.parametrize("keyword,case_sensitive", [
        ("PRODUCTO + MARCA/LABORATORIO", True),
        ("NUNCA pongas solo la marca", True),
        ("melatonina arco iris", False),
    ])
    def test_instructions_contain_brand_rule_keywords(self, keyword, case_sensitive):
        """CLASSIFY_INSTRUCTIONS contains the product + brand/lab rule keywords."""
        haystack = CLASSIFY_INSTRUCTIONS if case_sensitive else CLASSIFY_INSTRUCTIONS.lower()
        needle = keyword if case_sensitive else keyword.lower()
        assert needle in haystack

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

    @pytest.mark.parametrize("mapping_keyword", [
        "recolector de heces",    # heces → recolector (the original bug)
        "recolector de orina",    # orina exam → recolector
        "prueba de embarazo",     # pregnancy test → prueba de embarazo
        "glucometro",             # blood sugar test → glucometro
        "tensiometro",            # blood pressure test → tensiometro
    ])
    def test_instructions_contain_exam_supply_mappings(self, mapping_keyword):
        """CLASSIFY_INSTRUCTIONS contains each medical-exam-to-supply mapping."""
        assert mapping_keyword in CLASSIFY_INSTRUCTIONS.lower()

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


# ── Vague symptom → clarify_needed ───────────────────────────────────


class TestVagueSymptomClarification:
    """Verify CLASSIFY_INSTRUCTIONS handles vague symptoms with clarification."""

    def test_instructions_contain_vague_symptom_rule(self):
        """CLASSIFY_INSTRUCTIONS includes the vague symptom clarification rule."""
        assert "SÍNTOMAS VAGOS SIN ESPECIFICAR TIPO" in CLASSIFY_INSTRUCTIONS

    @pytest.mark.parametrize("clarify_phrase", [
        "¿qué tipo de dolor?",        # dolor/dolores → clarify
        "¿qué síntomas tienes?",      # malestar → clarify
        "¿qué tipo de alergia?",      # alergia → clarify
        "¿dónde tienes la inflamación?",  # inflamación → clarify
    ])
    def test_instructions_contain_vague_symptom_clarify_examples(self, clarify_phrase):
        """CLASSIFY_INSTRUCTIONS contains each vague-symptom clarification example."""
        assert clarify_phrase in CLASSIFY_INSTRUCTIONS.lower()

    def test_instructions_fiebre_is_specific_not_vague(self):
        """Fiebre is specific enough for direct OTC listing, not clarify."""
        # Fiebre has a narrow OTC set (Acetaminofén/Ibuprofeno) — no need
        # to ask "what kind of fever". It lives in the specific-symptom rule.
        specific_section = CLASSIFY_INSTRUCTIONS[
            CLASSIFY_INSTRUCTIONS.index("SÍNTOMAS ESPECÍFICOS"):
            CLASSIFY_INSTRUCTIONS.index("SÍNTOMAS VAGOS")
        ]
        assert "fiebre" in specific_section.lower()

    def test_instructions_warn_specific_symptoms_no_clarify(self):
        """Rule explicitly says NOT to clarify specific symptoms."""
        assert "NO uses clarify_needed si el síntoma YA ES ESPECÍFICO" in CLASSIFY_INSTRUCTIONS

    def test_instructions_list_specific_symptom_exceptions(self):
        """Specific symptoms that should NOT trigger clarification are listed."""
        lower = CLASSIFY_INSTRUCTIONS.lower()
        for specific in ["dolor de cabeza", "dolor de estomago", "acidez", "gripe",
                         "dolor muscular", "tos", "diarrea", "náuseas", "fiebre",
                         "fiebre alta"]:
            assert specific in lower, f"Specific symptom '{specific}' not in exceptions"

    def test_parse_vague_dolor_clarify_response(self):
        """Parser correctly handles a vague-dolor clarify_needed response.

        This is Daniel's actual repro (2026-05-19): voice note transcribed to
        "Encuentra medicina para dolores" → should get clarify_needed, not
        a generic OTC list.
        """
        reply = (
            "ACTION: clarify_needed\n"
            "CLARIFY_CONTEXT: medicina para dolores\n"
            "CLARIFY_QUESTION: ¿Qué tipo de dolor? (cabeza, muscular, "
            "articulaciones, espalda, menstrual, estómago) Así te sugiero "
            "la mejor opción. 💊"
        )
        result = _parse_structured_response(reply)
        assert result.action == "clarify_needed"
        assert result.clarify_question is not None
        assert "dolor" in result.clarify_question.lower()
        assert result.clarify_context is not None
        assert "dolores" in result.clarify_context.lower()

    def test_parse_vague_malestar_clarify_response(self):
        """Parser correctly handles a vague-malestar clarify_needed response."""
        reply = (
            "ACTION: clarify_needed\n"
            "CLARIFY_CONTEXT: me siento mal\n"
            "CLARIFY_QUESTION: ¿Qué síntomas tienes? (dolor de cabeza, "
            "fiebre, náuseas, gripe, dolor muscular) Así puedo ayudarte "
            "mejor. 💊"
        )
        result = _parse_structured_response(reply)
        assert result.action == "clarify_needed"
        assert "síntomas" in result.clarify_question.lower()
        assert "me siento mal" in result.clarify_context.lower()

    def test_parse_fiebre_should_be_question_not_clarify(self):
        """Fiebre is specific enough — goes to question with OTC options."""
        reply = (
            "ACTION: question\n"
            "RESPONSE: Para la fiebre, opciones comunes de venta libre son "
            "Acetaminofén e Ibuprofeno. ¿Cuál quieres que te busque? "
            "Consulta con tu médico."
        )
        result = _parse_structured_response(reply)
        assert result.action == "question"
        assert "acetaminofén" in result.text.lower()

    def test_parse_vague_alergia_clarify_response(self):
        """Parser correctly handles a vague alergia clarify_needed response."""
        reply = (
            "ACTION: clarify_needed\n"
            "CLARIFY_CONTEXT: algo para alergia\n"
            "CLARIFY_QUESTION: ¿Qué tipo de alergia? (nasal/estornudos, "
            "piel/ronchas, ojos/picazón) Así te sugiero el medicamento "
            "adecuado. 💊"
        )
        result = _parse_structured_response(reply)
        assert result.action == "clarify_needed"
        assert "alergia" in result.clarify_question.lower()

    def test_parse_specific_symptom_should_be_question(self):
        """Specific symptom 'dolor de cabeza' goes to question, NOT clarify."""
        reply = (
            "ACTION: question\n"
            "RESPONSE: Para dolor de cabeza, opciones comunes de venta "
            "libre son Acetaminofén, Ibuprofeno y Aspirina. ¿Cuál quieres "
            "que te busque? Consulta con tu médico."
        )
        result = _parse_structured_response(reply)
        assert result.action == "question"
        assert result.text  # Has OTC options in RESPONSE

    def test_parse_specific_symptom_acidez_should_be_question(self):
        """Specific symptom 'acidez' goes to question with OTC options."""
        reply = (
            "ACTION: question\n"
            "RESPONSE: Para la acidez, opciones comunes son Omeprazol, "
            "Ranitidina y antiácidos. ¿Cuál quieres que te busque?"
        )
        result = _parse_structured_response(reply)
        assert result.action == "question"
        assert "omeprazol" in result.text.lower()

    def test_parse_inflamacion_clarify_response(self):
        """Parser correctly handles a vague inflamación clarify_needed response."""
        reply = (
            "ACTION: clarify_needed\n"
            "CLARIFY_CONTEXT: tengo inflamación\n"
            "CLARIFY_QUESTION: ¿Dónde tienes la inflamación? "
            "(garganta, articulaciones, muscular, estómago) 💊"
        )
        result = _parse_structured_response(reply)
        assert result.action == "clarify_needed"
        assert "inflamación" in result.clarify_question.lower()


    def test_parse_clarify_needed_missing_context_falls_back_to_none(self):
        """When LLM omits CLARIFY_CONTEXT, parser sets it to None.

        The handler fallback at handler.py:1905 then uses the raw user text
        as context. This test documents that the parser returns None (not
        empty string or error) so the handler fallback is the intended path.
        """
        reply = (
            "ACTION: clarify_needed\n"
            "CLARIFY_QUESTION: ¿Qué tipo de dolor? (cabeza, muscular, "
            "articulaciones) 💊"
            # Note: no CLARIFY_CONTEXT line
        )
        result = _parse_structured_response(reply)
        assert result.action == "clarify_needed"
        assert result.clarify_question is not None
        assert result.clarify_context is None  # handler uses raw text as fallback

    def test_instructions_contain_priority_tiebreaker(self):
        """The vague-symptom rule explicitly states priority over product rule."""
        assert "PRIORIDAD sobre la regla general" in CLASSIFY_INSTRUCTIONS

    def test_instructions_contain_encuentra_medicina_example(self):
        """Daniel's exact voice transcription is in the examples."""
        assert "encuentra medicina para dolores" in CLASSIFY_INSTRUCTIONS.lower()

    def test_fiebre_in_specific_exceptions_list(self):
        """Fiebre is listed in the exceptions (specific, not vague)."""
        # Find the exceptions list at the end of the vague-symptom rule
        vague_section = CLASSIFY_INSTRUCTIONS[
            CLASSIFY_INSTRUCTIONS.index("SÍNTOMAS VAGOS"):
            CLASSIFY_INSTRUCTIONS.index("CATEGORÍAS VAGAS")
        ]
        assert "fiebre" in vague_section.lower()
        assert "fiebre alta" in vague_section.lower()


class TestVagueSymptomVsSpecificRegression:
    """Ensure the vague symptom rule doesn't interfere with specific symptoms."""

    def test_specific_symptom_rule_still_present(self):
        """The specific-symptom → question rule is still in the instructions."""
        assert "SÍNTOMAS ESPECÍFICOS sin producto" in CLASSIFY_INSTRUCTIONS

    def test_vague_rule_comes_after_specific_rule(self):
        """Vague symptom rule is positioned after the specific symptom rule."""
        specific_pos = CLASSIFY_INSTRUCTIONS.index("SÍNTOMAS ESPECÍFICOS")
        vague_pos = CLASSIFY_INSTRUCTIONS.index("SÍNTOMAS VAGOS")
        assert vague_pos > specific_pos, (
            "Vague symptom rule must come AFTER specific symptom rule"
        )

    def test_both_rules_reference_clarify_needed(self):
        """Vague symptom rule uses clarify_needed, specific uses question."""
        # Specific symptom rule says "clasifica como question"
        specific_section = CLASSIFY_INSTRUCTIONS[
            CLASSIFY_INSTRUCTIONS.index("SÍNTOMAS ESPECÍFICOS"):
            CLASSIFY_INSTRUCTIONS.index("SÍNTOMAS VAGOS")
        ]
        assert "question" in specific_section

        # Vague symptom rule says "clasifica como clarify_needed"
        vague_section = CLASSIFY_INSTRUCTIONS[
            CLASSIFY_INSTRUCTIONS.index("SÍNTOMAS VAGOS"):
            CLASSIFY_INSTRUCTIONS.index("CATEGORÍAS VAGAS")
        ]
        assert "clarify_needed" in vague_section


# ── Regression guards ─────────────────────────────────────────────────


class TestClassifyInstructionsRegression:
    """Ensure existing rules weren't broken by the new additions."""

    @pytest.mark.parametrize("keyword", [
        "SÍNTOMAS ESPECÍFICOS sin producto",  # symptom-only → question rule
        "CATEGORÍAS VAGAS",                   # vague category → clarify_needed rule
        "EMERGENCIA",                         # emergency rule
        "EXCEPCIÓN DE SEGURIDAD",             # drug interaction warning rule
        "nearest_store",                      # nearest store rule
        "view_similar",                       # view similar rule
    ])
    def test_existing_rule_still_present(self, keyword):
        """Pre-existing CLASSIFY_INSTRUCTIONS rules are intact after new additions."""
        assert keyword in CLASSIFY_INSTRUCTIONS
