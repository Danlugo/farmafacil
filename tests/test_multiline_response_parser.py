"""Tests for multiline RESPONSE parsing in _parse_structured_response.

Bug: The parser used to capture only the first line of a RESPONSE field,
truncating multi-line AI output (bullet lists, disclaimers, etc.).  This
was especially visible for vague-symptom queries where the AI returns:

    RESPONSE: Para resfriado, las opciones mas comunes son:
    - Acetaminofen 500mg
    - Ibuprofeno 200mg
    ...

The old parser returned only the first line; the fix accumulates all lines
until the next known key.
"""

import pytest

from farmafacil.services.ai_responder import AiResponse, _parse_structured_response


class TestMultilineResponse:
    """_parse_structured_response must accumulate multiline RESPONSE values."""

    def test_multiline_bullet_list(self):
        """Symptom query → AI returns intro + bullet list + question."""
        reply = (
            "ACTION: question\n"
            "RESPONSE: Para resfriado comun, las opciones de venta libre mas comunes son:\n"
            "- Acetaminofen 500mg: reduce fiebre y dolor\n"
            "- Ibuprofeno 200mg: antiinflamatorio\n"
            "- Jarabe para la tos: calma la tos productiva\n"
            "Cual quieres que te busque? Consulta con tu medico."
        )
        result = _parse_structured_response(reply)
        assert result.action == "question"
        # Must contain ALL lines, not just the first
        assert "Acetaminofen" in result.text
        assert "Ibuprofeno" in result.text
        assert "Jarabe" in result.text
        assert "Consulta con tu medico" in result.text

    def test_multiline_numbered_list(self):
        """AI returns numbered list of recommendations."""
        reply = (
            "ACTION: question\n"
            "RESPONSE: Aqui tienes opciones para el dolor de cabeza:\n"
            "1. Acetaminofen\n"
            "2. Ibuprofeno\n"
            "3. Naproxeno\n"
            "Cual prefieres buscar?"
        )
        result = _parse_structured_response(reply)
        assert "1. Acetaminofen" in result.text
        assert "3. Naproxeno" in result.text
        assert "Cual prefieres" in result.text

    def test_multiline_with_emoji_lines(self):
        """AI uses emoji-styled bullets."""
        reply = (
            "ACTION: question\n"
            "RESPONSE: Para la gripe te recomiendo:\n"
            "  Acetaminofen para fiebre\n"
            "  Descongestionante nasal\n"
            "  Consulta medica si persiste"
        )
        result = _parse_structured_response(reply)
        assert "Acetaminofen" in result.text
        assert "Descongestionante" in result.text
        assert "persiste" in result.text

    def test_multiline_with_disclaimer(self):
        """RESPONSE spans multiple paragraphs with blank-ish lines."""
        reply = (
            "ACTION: question\n"
            "RESPONSE: Para el dolor de estomago existen varias opciones:\n"
            "- Omeprazol\n"
            "- Alka-Seltzer\n"
            "\n"
            "Importante: consulta a tu medico si los sintomas persisten mas de 3 dias."
        )
        result = _parse_structured_response(reply)
        assert "Omeprazol" in result.text
        assert "Alka-Seltzer" in result.text
        assert "consulta a tu medico" in result.text

    def test_single_line_response_still_works(self):
        """Single-line RESPONSE must still parse correctly (no regression)."""
        reply = (
            "ACTION: drug_search\n"
            "DRUG: ibuprofeno\n"
            "RESPONSE: Buscando ibuprofeno para ti."
        )
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.drug_query == "ibuprofeno"
        assert result.text == "Buscando ibuprofeno para ti."

    def test_multiline_response_followed_by_another_key(self):
        """RESPONSE ends when the next known key starts."""
        reply = (
            "ACTION: clarify_needed\n"
            "RESPONSE: No entendi bien tu solicitud.\n"
            "Podrias decirme de que area te refieres?\n"
            "CLARIFY_QUESTION: En que parte del cuerpo sientes el dolor?\n"
            "CLARIFY_CONTEXT: dolor"
        )
        result = _parse_structured_response(reply)
        assert result.action == "clarify_needed"
        # RESPONSE stops before CLARIFY_QUESTION
        assert "No entendi bien" in result.text
        assert "de que area" in result.text
        assert "En que parte" not in result.text
        # CLARIFY_QUESTION captured separately
        assert result.clarify_question == "En que parte del cuerpo sientes el dolor?"
        assert result.clarify_context == "dolor"

    def test_response_with_colons_in_value(self):
        """Colons inside response text must not start new fields."""
        reply = (
            "ACTION: question\n"
            "RESPONSE: Opciones:\n"
            "- Acetaminofen 500mg: para fiebre\n"
            "- Ibuprofeno 200mg: antiinflamatorio\n"
            "Nota: estas son sugerencias generales."
        )
        result = _parse_structured_response(reply)
        # Lines with colons but non-key prefixes stay in RESPONSE
        assert "Acetaminofen 500mg: para fiebre" in result.text
        assert "Ibuprofeno 200mg: antiinflamatorio" in result.text
        assert "Nota: estas son sugerencias" in result.text

    def test_drug_search_multiline_response(self):
        """drug_search action with multiline RESPONSE (acknowledgment text)."""
        reply = (
            "ACTION: drug_search\n"
            "DRUG: acetaminofen\n"
            "RESPONSE: Entendido, buscando Acetaminofen.\n"
            "Esta es una opcion comun para fiebre y dolor leve."
        )
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.drug_query == "acetaminofen"
        assert "Entendido" in result.text
        assert "opcion comun" in result.text

    def test_greeting_multiline(self):
        """Greeting with multiline welcome message."""
        reply = (
            "ACTION: greeting\n"
            "RESPONSE: Hola! Bienvenido a FarmaFacil.\n"
            "Puedo ayudarte a buscar medicamentos y farmacias cercanas.\n"
            "Que necesitas hoy?"
        )
        result = _parse_structured_response(reply)
        assert result.action == "greeting"
        assert "Bienvenido" in result.text
        assert "medicamentos" in result.text
        assert "Que necesitas" in result.text

    def test_empty_response_field(self):
        """RESPONSE with no content after the colon."""
        reply = "ACTION: drug_search\nDRUG: losartan\nRESPONSE:"
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.drug_query == "losartan"
        assert result.text == ""

    def test_multiline_preserves_structure(self):
        """The full multiline text is preserved including internal newlines."""
        reply = (
            "ACTION: question\n"
            "RESPONSE: Linea uno\n"
            "Linea dos\n"
            "Linea tres"
        )
        result = _parse_structured_response(reply)
        lines = result.text.split("\n")
        assert len(lines) == 3
        assert lines[0] == "Linea uno"
        assert lines[1] == "Linea dos"
        assert lines[2] == "Linea tres"
