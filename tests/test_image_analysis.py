"""Tests for services/image_analysis.py — prescription reader & medicine identifier.

Covers:
- _parse_vision_response: parsing structured Vision responses
- analyze_image: mocked Claude API calls for prescription and medicine photos
- Handler integration: verify correct message flow for both image types
- Edge cases: unknown images, API failures, missing drug names

Item 124, v0.45.0.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from farmafacil.services.image_analysis import (
    MAX_DRUG_NAMES,
    ImageAnalysisResult,
    _parse_vision_response,
    analyze_image,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


def _make_image_block() -> dict:
    """Create a minimal Anthropic Vision image content block."""
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": "dGVzdA==",  # base64 of "test"
        },
    }


# ── Unit Tests: _parse_vision_response ───────────────────────────────────


class TestParseVisionResponse:
    """Parse the structured Vision response into ImageAnalysisResult."""

    def test_prescription_response(self):
        reply = (
            "TIPO: RECETA\n"
            "\n"
            "📋 *Receta Médica*\n"
            "\n"
            "💊 *1. Losartan*\n"
            "   Dosis: 50mg\n"
            "   Tomar: 1 vez al día\n"
            "\n"
            "💊 *2. Metformina*\n"
            "   Dosis: 850mg\n"
            "   Tomar: 2 veces al día con las comidas\n"
            "\n"
            "MEDICAMENTOS: Losartan, Metformina"
        )
        result = _parse_vision_response(reply, "claude-3-haiku", 500, 200)

        assert result.image_type == "prescription"
        assert "Losartan" in result.analysis_text
        assert "Metformina" in result.analysis_text
        assert result.drug_names == ["Losartan", "Metformina"]
        assert result.model_used == "claude-3-haiku"
        assert result.tokens_in == 500
        assert result.tokens_out == 200

    def test_medicine_response(self):
        reply = (
            "TIPO: MEDICAMENTO\n"
            "\n"
            "📦 *Losartan Potásico 50mg Tabletas*\n"
            "Principio activo: Losartan\n"
            "Presentación: Tabletas x 30\n"
            "\n"
            "MEDICAMENTOS: Losartan"
        )
        result = _parse_vision_response(reply, "claude-3-haiku", 400, 100)

        assert result.image_type == "medicine"
        assert "Losartan" in result.analysis_text
        assert result.drug_names == ["Losartan"]

    def test_unknown_response(self):
        reply = "TIPO: DESCONOCIDO"
        result = _parse_vision_response(reply, "claude-3-haiku", 300, 10)

        assert result.image_type == "unknown"
        assert result.drug_names == []
        assert result.analysis_text == ""

    def test_tipo_line_stripped_from_text(self):
        """The TIPO: line should not appear in analysis_text."""
        reply = (
            "TIPO: MEDICAMENTO\n"
            "📦 *Ibuprofeno 400mg*\n"
            "MEDICAMENTOS: Ibuprofeno"
        )
        result = _parse_vision_response(reply, "model", 0, 0)
        assert "TIPO:" not in result.analysis_text

    def test_medicamentos_line_stripped_from_text(self):
        """The MEDICAMENTOS: line should not appear in analysis_text."""
        reply = (
            "TIPO: MEDICAMENTO\n"
            "📦 *Ibuprofeno 400mg*\n"
            "MEDICAMENTOS: Ibuprofeno"
        )
        result = _parse_vision_response(reply, "model", 0, 0)
        assert "MEDICAMENTOS:" not in result.analysis_text

    def test_code_fences_stripped(self):
        """Markdown code fences should be removed."""
        reply = (
            "TIPO: RECETA\n"
            "```\n"
            "📋 *Receta Médica*\n"
            "```\n"
            "MEDICAMENTOS: Losartan"
        )
        result = _parse_vision_response(reply, "model", 0, 0)
        assert "```" not in result.analysis_text

    def test_max_drug_names_capped(self):
        """Drug names list is capped at MAX_DRUG_NAMES."""
        names = ", ".join(f"Drug{i}" for i in range(10))
        reply = f"TIPO: RECETA\nTexto\nMEDICAMENTOS: {names}"
        result = _parse_vision_response(reply, "model", 0, 0)
        assert len(result.drug_names) == MAX_DRUG_NAMES

    def test_empty_medicamentos_line(self):
        """MEDICAMENTOS: with no names → empty list."""
        reply = "TIPO: MEDICAMENTO\n📦 Algo\nMEDICAMENTOS:"
        result = _parse_vision_response(reply, "model", 0, 0)
        assert result.drug_names == []

    def test_prescription_type_variants(self):
        """TIPO line may have extra whitespace or casing."""
        for tipo in ["TIPO: RECETA", "TIPO:  RECETA", "tipo: receta"]:
            reply = f"{tipo}\nTexto\nMEDICAMENTOS: Losartan"
            result = _parse_vision_response(reply, "model", 0, 0)
            assert result.image_type == "prescription", f"Failed for: {tipo}"

    def test_missing_tipo_line(self):
        """If TIPO: is missing entirely, result is unknown."""
        reply = "📦 Some product\nMEDICAMENTOS: Something"
        result = _parse_vision_response(reply, "model", 0, 0)
        assert result.image_type == "unknown"

    def test_drug_names_whitespace_stripped(self):
        """Drug names should have leading/trailing whitespace removed."""
        reply = "TIPO: RECETA\nTexto\nMEDICAMENTOS:  Losartan ,  Metformina  "
        result = _parse_vision_response(reply, "model", 0, 0)
        assert result.drug_names == ["Losartan", "Metformina"]

    def test_empty_drug_names_filtered(self):
        """Empty strings from splitting should be filtered out."""
        reply = "TIPO: RECETA\nTexto\nMEDICAMENTOS: Losartan,,, Metformina,"
        result = _parse_vision_response(reply, "model", 0, 0)
        assert result.drug_names == ["Losartan", "Metformina"]


# ── Unit Tests: analyze_image ─────────────────────────────────────────────


class TestAnalyzeImage:
    """Test the analyze_image function with mocked Claude API."""

    @pytest.mark.asyncio
    async def test_prescription_analysis(self):
        """Prescription photo returns structured result."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=(
            "TIPO: RECETA\n"
            "\n"
            "📋 *Receta Médica*\n"
            "\n"
            "💊 *1. Losartan*\n"
            "   Dosis: 50mg\n"
            "\n"
            "MEDICAMENTOS: Losartan"
        ))]
        mock_response.usage = MagicMock(input_tokens=800, output_tokens=200)

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("farmafacil.services.image_analysis._get_client", return_value=mock_client),
            patch("farmafacil.services.image_analysis.resolve_user_model", return_value="claude-3-haiku-20240307"),
            patch("farmafacil.services.image_analysis.ANTHROPIC_API_KEY", "test-key"),
        ):
            result = await analyze_image(_make_image_block())

        assert result is not None
        assert result.image_type == "prescription"
        assert result.drug_names == ["Losartan"]
        assert result.tokens_in == 800
        assert result.tokens_out == 200

    @pytest.mark.asyncio
    async def test_medicine_analysis(self):
        """Medicine photo returns drug name for search."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=(
            "TIPO: MEDICAMENTO\n"
            "📦 *Ibuprofeno 400mg*\n"
            "MEDICAMENTOS: Ibuprofeno"
        ))]
        mock_response.usage = MagicMock(input_tokens=600, output_tokens=50)

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("farmafacil.services.image_analysis._get_client", return_value=mock_client),
            patch("farmafacil.services.image_analysis.resolve_user_model", return_value="claude-3-haiku-20240307"),
            patch("farmafacil.services.image_analysis.ANTHROPIC_API_KEY", "test-key"),
        ):
            result = await analyze_image(_make_image_block(), caption="ibuprofeno")

        assert result is not None
        assert result.image_type == "medicine"
        assert result.drug_names == ["Ibuprofeno"]

    @pytest.mark.asyncio
    async def test_unknown_image(self):
        """Unrecognized image returns unknown type."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="TIPO: DESCONOCIDO")]
        mock_response.usage = MagicMock(input_tokens=500, output_tokens=5)

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("farmafacil.services.image_analysis._get_client", return_value=mock_client),
            patch("farmafacil.services.image_analysis.resolve_user_model", return_value="claude-3-haiku-20240307"),
            patch("farmafacil.services.image_analysis.ANTHROPIC_API_KEY", "test-key"),
        ):
            result = await analyze_image(_make_image_block())

        assert result is not None
        assert result.image_type == "unknown"
        assert result.drug_names == []

    @pytest.mark.asyncio
    async def test_api_failure_returns_none(self):
        """API exceptions return None without crashing."""
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("API down"))

        with (
            patch("farmafacil.services.image_analysis._get_client", return_value=mock_client),
            patch("farmafacil.services.image_analysis.resolve_user_model", new_callable=AsyncMock, return_value="model"),
            patch("farmafacil.services.image_analysis.ANTHROPIC_API_KEY", "test-key"),
        ):
            result = await analyze_image(_make_image_block())

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_none(self):
        """Missing ANTHROPIC_API_KEY returns None."""
        with patch("farmafacil.services.image_analysis.ANTHROPIC_API_KEY", ""):
            result = await analyze_image(_make_image_block())
        assert result is None

    @pytest.mark.asyncio
    async def test_caption_included_in_prompt(self):
        """Caption text is appended to the user message."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="TIPO: DESCONOCIDO")]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=5)

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("farmafacil.services.image_analysis._get_client", return_value=mock_client),
            patch("farmafacil.services.image_analysis.resolve_user_model", new_callable=AsyncMock, return_value="model"),
            patch("farmafacil.services.image_analysis.ANTHROPIC_API_KEY", "test-key"),
        ):
            await analyze_image(_make_image_block(), caption="losartan")

        # Verify the caption was included in the API call
        call_args = mock_client.messages.create.call_args
        messages = call_args.kwargs["messages"]
        user_content = messages[0]["content"]
        # The text block should mention the caption
        text_block = next(b for b in user_content if b.get("type") == "text")
        assert "losartan" in text_block["text"]

    @pytest.mark.asyncio
    async def test_system_prompt_sent(self):
        """The system prompt should be included in the API call."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="TIPO: DESCONOCIDO")]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=5)

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("farmafacil.services.image_analysis._get_client", return_value=mock_client),
            patch("farmafacil.services.image_analysis.resolve_user_model", new_callable=AsyncMock, return_value="model"),
            patch("farmafacil.services.image_analysis.ANTHROPIC_API_KEY", "test-key"),
        ):
            await analyze_image(_make_image_block())

        call_args = mock_client.messages.create.call_args
        assert "system" in call_args.kwargs
        assert "RECETA" in call_args.kwargs["system"]


# ── Handler integration tests ─────────────────────────────────────────────


class TestHandleImagePrescription:
    """Test handler.handle_image_message for prescription photos."""

    @pytest.mark.asyncio
    async def test_prescription_sends_analysis_and_searches(self):
        """Prescription photo: sends analysis text, then searches each drug."""
        from farmafacil.services.image_analysis import ImageAnalysisResult

        analysis = ImageAnalysisResult(
            image_type="prescription",
            analysis_text="📋 *Receta Médica*\n\n💊 *1. Losartan*\n   Dosis: 50mg",
            drug_names=["Losartan", "Metformina"],
            model_used="claude-3-haiku",
            tokens_in=800,
            tokens_out=200,
        )

        sent_messages: list[str] = []
        searched_queries: list[str] = []

        async def mock_send(to, text):
            sent_messages.append(text)

        async def mock_handle(sender, text):
            searched_queries.append(text)

        mock_user = MagicMock()
        mock_user.admin_mode_active = False
        mock_user.awaiting_clarification_context = None
        mock_user.display_name = "TestUser"
        mock_user.id = 1

        with (
            patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock, return_value=(b"fake-jpeg", "image/jpeg")),
            patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image", "source": {}}),
            patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock, return_value=analysis) as mock_analyze,
            patch("farmafacil.bot.handler.send_text_message", side_effect=mock_send),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=mock_user),
            patch("farmafacil.bot.handler.is_chat_admin", new_callable=AsyncMock, return_value=False),
            patch("farmafacil.bot.handler.handle_incoming_message", side_effect=mock_handle),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
        ):
            from farmafacil.bot.handler import handle_image_message
            await handle_image_message("1234567890", "media123", "image/jpeg")

        # Should have sent: analyzing msg + analysis text + search list
        assert any("Analizando" in m for m in sent_messages)
        assert any("Receta Médica" in m for m in sent_messages)
        assert any("Buscando disponibilidad" in m for m in sent_messages)

        # Should have searched for both drugs
        assert "Losartan" in searched_queries
        assert "Metformina" in searched_queries

    @pytest.mark.asyncio
    async def test_prescription_no_drug_names_sends_fallback(self):
        """Prescription with no extractable drug names sends fallback message."""
        from farmafacil.services.image_analysis import ImageAnalysisResult

        analysis = ImageAnalysisResult(
            image_type="prescription",
            analysis_text="📋 *Receta Médica*\n\n⚠️ No pude leer los nombres",
            drug_names=[],
            model_used="model",
            tokens_in=500,
            tokens_out=100,
        )

        sent_messages: list[str] = []

        async def mock_send(to, text):
            sent_messages.append(text)

        mock_user = MagicMock()
        mock_user.admin_mode_active = False
        mock_user.awaiting_clarification_context = None
        mock_user.display_name = "TestUser"
        mock_user.id = 1

        with (
            patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock, return_value=(b"fake", "image/jpeg")),
            patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image", "source": {}}),
            patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock, return_value=analysis),
            patch("farmafacil.bot.handler.send_text_message", side_effect=mock_send),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=mock_user),
            patch("farmafacil.bot.handler.is_chat_admin", new_callable=AsyncMock, return_value=False),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
        ):
            from farmafacil.bot.handler import handle_image_message
            await handle_image_message("1234567890", "media123", "image/jpeg")

        assert any("nombre del producto por texto" in m for m in sent_messages)


class TestHandleImageMedicine:
    """Test handler.handle_image_message for medicine photos."""

    @pytest.mark.asyncio
    async def test_medicine_photo_auto_searches(self):
        """Medicine photo: identifies drug and auto-searches."""
        from farmafacil.services.image_analysis import ImageAnalysisResult

        analysis = ImageAnalysisResult(
            image_type="medicine",
            analysis_text="📦 *Ibuprofeno 400mg Tabletas*\nPrincipio activo: Ibuprofeno",
            drug_names=["Ibuprofeno"],
            model_used="model",
            tokens_in=600,
            tokens_out=50,
        )

        sent_messages: list[str] = []
        searched_queries: list[str] = []

        async def mock_send(to, text):
            sent_messages.append(text)

        async def mock_handle(sender, text):
            searched_queries.append(text)

        mock_user = MagicMock()
        mock_user.admin_mode_active = False
        mock_user.awaiting_clarification_context = None
        mock_user.display_name = "TestUser"
        mock_user.id = 1

        with (
            patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock, return_value=(b"fake", "image/jpeg")),
            patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image", "source": {}}),
            patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock, return_value=analysis),
            patch("farmafacil.bot.handler.send_text_message", side_effect=mock_send),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=mock_user),
            patch("farmafacil.bot.handler.is_chat_admin", new_callable=AsyncMock, return_value=False),
            patch("farmafacil.bot.handler.handle_incoming_message", side_effect=mock_handle),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
        ):
            from farmafacil.bot.handler import handle_image_message
            await handle_image_message("1234567890", "media123", "image/jpeg")

        # Should show analysis then search
        assert any("Ibuprofeno" in m for m in sent_messages)
        assert "Ibuprofeno" in searched_queries


class TestHandleImageEdgeCases:
    """Edge cases for image handling."""

    @pytest.mark.asyncio
    async def test_unknown_image_sends_fallback(self):
        """Unknown image type sends helpful fallback message."""
        from farmafacil.services.image_analysis import ImageAnalysisResult

        analysis = ImageAnalysisResult(image_type="unknown")

        sent_messages: list[str] = []

        async def mock_send(to, text):
            sent_messages.append(text)

        mock_user = MagicMock()
        mock_user.admin_mode_active = False
        mock_user.awaiting_clarification_context = None
        mock_user.display_name = "TestUser"
        mock_user.id = 1

        with (
            patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock, return_value=(b"fake", "image/jpeg")),
            patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image", "source": {}}),
            patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock, return_value=analysis),
            patch("farmafacil.bot.handler.send_text_message", side_effect=mock_send),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=mock_user),
            patch("farmafacil.bot.handler.is_chat_admin", new_callable=AsyncMock, return_value=False),
        ):
            from farmafacil.bot.handler import handle_image_message
            await handle_image_message("1234567890", "media123", "image/jpeg")

        assert any("receta ni un medicamento" in m for m in sent_messages)

    @pytest.mark.asyncio
    async def test_api_failure_sends_fallback(self):
        """When analyze_image returns None, send fallback."""
        sent_messages: list[str] = []

        async def mock_send(to, text):
            sent_messages.append(text)

        mock_user = MagicMock()
        mock_user.admin_mode_active = False
        mock_user.awaiting_clarification_context = None
        mock_user.display_name = "TestUser"
        mock_user.id = 1

        with (
            patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock, return_value=(b"fake", "image/jpeg")),
            patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image", "source": {}}),
            patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock, return_value=None),
            patch("farmafacil.bot.handler.send_text_message", side_effect=mock_send),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=mock_user),
            patch("farmafacil.bot.handler.is_chat_admin", new_callable=AsyncMock, return_value=False),
        ):
            from farmafacil.bot.handler import handle_image_message
            await handle_image_message("1234567890", "media123", "image/jpeg")

        assert any("receta ni un medicamento" in m for m in sent_messages)

    @pytest.mark.asyncio
    async def test_medicine_no_drug_names_sends_fallback(self):
        """Medicine type but no drug names extracted → fallback."""
        from farmafacil.services.image_analysis import ImageAnalysisResult

        analysis = ImageAnalysisResult(
            image_type="medicine",
            analysis_text="",
            drug_names=[],
        )

        sent_messages: list[str] = []

        async def mock_send(to, text):
            sent_messages.append(text)

        mock_user = MagicMock()
        mock_user.admin_mode_active = False
        mock_user.awaiting_clarification_context = None
        mock_user.display_name = "TestUser"
        mock_user.id = 1

        with (
            patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock, return_value=(b"fake", "image/jpeg")),
            patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image", "source": {}}),
            patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock, return_value=analysis),
            patch("farmafacil.bot.handler.send_text_message", side_effect=mock_send),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=mock_user),
            patch("farmafacil.bot.handler.is_chat_admin", new_callable=AsyncMock, return_value=False),
        ):
            from farmafacil.bot.handler import handle_image_message
            await handle_image_message("1234567890", "media123", "image/jpeg")

        assert any("identificar el medicamento" in m for m in sent_messages)

    @pytest.mark.asyncio
    async def test_download_failure_sends_fallback(self):
        """When media download fails, send fallback message."""
        sent_messages: list[str] = []

        async def mock_send(to, text):
            sent_messages.append(text)

        mock_user = MagicMock()
        mock_user.admin_mode_active = False
        mock_user.awaiting_clarification_context = None
        mock_user.display_name = "TestUser"
        mock_user.id = 1

        with (
            patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock, return_value=None),
            patch("farmafacil.bot.handler.send_text_message", side_effect=mock_send),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=mock_user),
        ):
            from farmafacil.bot.handler import handle_image_message
            await handle_image_message("1234567890", "media123", "image/jpeg")

        assert any("descargar" in m for m in sent_messages)

    @pytest.mark.asyncio
    async def test_image_too_large_sends_fallback(self):
        """When encode_image_for_vision returns None, send too-large fallback."""
        sent_messages: list[str] = []

        async def mock_send(to, text):
            sent_messages.append(text)

        mock_user = MagicMock()
        mock_user.admin_mode_active = False
        mock_user.awaiting_clarification_context = None
        mock_user.display_name = "TestUser"
        mock_user.id = 1

        with (
            patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock, return_value=(b"fake", "image/jpeg")),
            patch("farmafacil.services.media.encode_image_for_vision", return_value=None),
            patch("farmafacil.bot.handler.send_text_message", side_effect=mock_send),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=mock_user),
            patch("farmafacil.bot.handler.is_chat_admin", new_callable=AsyncMock, return_value=False),
        ):
            from farmafacil.bot.handler import handle_image_message
            await handle_image_message("1234567890", "media123", "image/jpeg")

        assert any("grande" in m or "compatible" in m for m in sent_messages)


# ── Constants / contract tests ────────────────────────────────────────────


class TestImageAnalysisConstants:
    """Verify module constants are sensible."""

    def test_max_drug_names_is_3(self):
        assert MAX_DRUG_NAMES == 3

    def test_max_tokens_accommodates_complex_prescriptions(self):
        from farmafacil.services.image_analysis import _MAX_TOKENS
        assert _MAX_TOKENS >= 2048

    def test_max_caption_length_exists(self):
        from farmafacil.services.image_analysis import _MAX_CAPTION_LEN
        assert _MAX_CAPTION_LEN == 200

    def test_result_defaults(self):
        result = ImageAnalysisResult()
        assert result.image_type == "unknown"
        assert result.analysis_text == ""
        assert result.drug_names == []
        assert result.model_used == ""
        assert result.tokens_in == 0
        assert result.tokens_out == 0

    @pytest.mark.asyncio
    async def test_caption_truncated_to_max_length(self):
        """Long captions are truncated to _MAX_CAPTION_LEN."""
        long_caption = "A" * 500  # Exceeds _MAX_CAPTION_LEN

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="TIPO: DESCONOCIDO")]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=5)
        mock_response.stop_reason = "end_turn"

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("farmafacil.services.image_analysis._get_client", return_value=mock_client),
            patch("farmafacil.services.image_analysis.resolve_user_model", new_callable=AsyncMock, return_value="model"),
            patch("farmafacil.services.image_analysis.ANTHROPIC_API_KEY", "test-key"),
        ):
            await analyze_image(_make_image_block(), caption=long_caption)

        call_args = mock_client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        text_block = [b for b in user_content if b.get("type") == "text"][0]
        # Caption in the prompt should be truncated to 200 chars
        assert "A" * 200 in text_block["text"]
        assert "A" * 201 not in text_block["text"]
