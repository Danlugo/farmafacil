"""Tests for POST /api/v1/chat/image — image relay endpoint.

Item 127 (v0.47.0): The endpoint accepts multipart/form-data with
sender_id, sender_name, caption, and image file.  It encodes the image
for Claude Vision, analyzes it (prescription / medicine photo), and
runs drug searches in proxy mode — returning all bot responses as JSON.

Note: The image endpoint uses lazy imports for services inside the
function body, so mocks must target the *source* module paths:
  - farmafacil.services.media.{encode_image_for_vision, ALL_IMAGE_TYPES}
  - farmafacil.services.image_analysis.analyze_image
  - farmafacil.services.users.{get_or_create_user, validate_user_profile}
Module-level imports (handle_incoming_message, start/stop_collecting)
are patched on farmafacil.api.routes as usual.
"""

import io
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from farmafacil.api.app import create_app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    app = create_app()
    return TestClient(app)


def _fake_image_file(
    size: int = 1024, mime: str = "image/jpeg", filename: str = "photo.jpg",
) -> tuple[str, io.BytesIO, str]:
    """Create a fake image file for upload."""
    return (filename, io.BytesIO(b"\xff\xd8\xff" + b"\x00" * size), mime)


@dataclass
class FakeAnalysisResult:
    """Minimal stand-in for ImageAnalysisResult."""
    image_type: str = "medicine"
    analysis_text: str = "Identificado: Losartan 50mg"
    drug_names: list[str] = field(default_factory=lambda: ["losartan"])
    model_used: str = "claude-haiku-4-5-20251001"
    tokens_in: int = 100
    tokens_out: int = 50


# Shared mock decorators — applied to every test in the class.
# The image endpoint uses lazy imports so mocks target source modules.
_COMMON_PATCHES = {
    "stop_collecting": [
        {"type": "text", "body": "\U0001f50d Analizando la imagen..."},
        {"type": "text", "body": "Identificado: Losartan 50mg"},
        {"type": "text", "body": "\U0001f50e Buscando *losartan*..."},
        {"type": "text", "body": "Resultados de búsqueda..."},
    ],
}


class TestChatImageEndpoint:
    """Test the /api/v1/chat/image endpoint."""

    @patch("farmafacil.api.routes.stop_collecting", return_value=_COMMON_PATCHES["stop_collecting"])
    @patch("farmafacil.api.routes.start_collecting")
    @patch("farmafacil.api.routes.handle_incoming_message", new_callable=AsyncMock)
    @patch("farmafacil.bot.whatsapp.send_text_message", new_callable=AsyncMock)
    @patch("farmafacil.services.drug_translation.translate_drug_query", new_callable=AsyncMock, return_value=None)
    @patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock)
    @patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image", "source": {"type": "base64"}})
    @patch("farmafacil.services.users.increment_token_usage", new_callable=AsyncMock)
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    def test_medicine_photo_happy_path(
        self, mock_get_user, mock_validate, mock_tokens,
        mock_encode, mock_analyze, mock_translate,
        mock_send_text, mock_handler, mock_start, mock_stop, client,
    ):
        """Happy path: medicine photo → Vision analysis → drug search."""
        user_mock = MagicMock(id=1, name="Daniel")
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock
        mock_analyze.return_value = FakeAnalysisResult()

        response = client.post(
            "/api/v1/chat/image",
            data={"sender_id": "584127006823", "sender_name": "Daniel"},
            files={"image": _fake_image_file()},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["responses"]) == len(_COMMON_PATCHES["stop_collecting"])
        mock_encode.assert_called_once()
        mock_analyze.assert_called_once()
        mock_handler.assert_called_once_with(
            "584127006823", "losartan", wa_profile_name="Daniel",
        )
        mock_tokens.assert_called_once_with(1, 100, 50, model="claude-haiku-4-5-20251001")

    @patch("farmafacil.api.routes.stop_collecting", return_value=[
        {"type": "text", "body": "\U0001f50d Analizando la imagen..."},
        {"type": "text", "body": "Receta: 1. Losartan 50mg, 2. Metformina"},
        {"type": "text", "body": "\U0001f50e *Buscando disponibilidad:*\n  1. losartan\n  2. metformina"},
        {"type": "text", "body": "Resultado losartan..."},
        {"type": "text", "body": "Resultado metformina..."},
    ])
    @patch("farmafacil.api.routes.start_collecting")
    @patch("farmafacil.api.routes.handle_incoming_message", new_callable=AsyncMock)
    @patch("farmafacil.bot.whatsapp.send_text_message", new_callable=AsyncMock)
    @patch("farmafacil.services.drug_translation.translate_drug_query", new_callable=AsyncMock, return_value=None)
    @patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock)
    @patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image"})
    @patch("farmafacil.services.users.increment_token_usage", new_callable=AsyncMock)
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    def test_prescription_photo_searches_multiple_drugs(
        self, mock_get_user, mock_validate, mock_tokens,
        mock_encode, mock_analyze, mock_translate,
        mock_send_text, mock_handler, mock_start, mock_stop, client,
    ):
        """Prescription photo: searches each extracted drug name."""
        user_mock = MagicMock(id=1, name="Daniel")
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock
        mock_analyze.return_value = FakeAnalysisResult(
            image_type="prescription",
            analysis_text="Receta: 1. Losartan 50mg, 2. Metformina",
            drug_names=["losartan", "metformina"],
        )

        response = client.post(
            "/api/v1/chat/image",
            data={"sender_id": "584127006823", "caption": "Mi receta"},
            files={"image": _fake_image_file()},
        )

        assert response.status_code == 200
        # Should search both drugs with wa_profile_name
        assert mock_handler.call_count == 2
        mock_handler.assert_any_call("584127006823", "losartan", wa_profile_name="")
        mock_handler.assert_any_call("584127006823", "metformina", wa_profile_name="")

    @patch("farmafacil.api.routes.stop_collecting", return_value=[
        {"type": "text", "body": "\U0001f50d Analizando la imagen..."},
        {"type": "text", "body": "No pude identificar..."},
    ])
    @patch("farmafacil.api.routes.start_collecting")
    @patch("farmafacil.bot.whatsapp.send_text_message", new_callable=AsyncMock)
    @patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock, return_value=None)
    @patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image"})
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    def test_unrecognized_image_returns_fallback(
        self, mock_get_user, mock_validate,
        mock_encode, mock_analyze, mock_send_text,
        mock_start, mock_stop, client,
    ):
        """When Vision can't identify the image, return helpful fallback."""
        user_mock = MagicMock(id=1, name="Daniel")
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock

        response = client.post(
            "/api/v1/chat/image",
            data={"sender_id": "584127006823"},
            files={"image": _fake_image_file()},
        )

        assert response.status_code == 200

    @patch("farmafacil.services.media.encode_image_for_vision", return_value=None)
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    def test_encode_failure_returns_error(
        self, mock_get_user, mock_validate, mock_encode, client,
    ):
        """When image encoding fails (too large, corrupt), return error text."""
        user_mock = MagicMock(id=1, name="Daniel")
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock

        response = client.post(
            "/api/v1/chat/image",
            data={"sender_id": "584127006823"},
            files={"image": _fake_image_file()},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["responses"]) == 1
        assert "demasiado grande" in data["responses"][0]["body"]

    def test_oversized_image_returns_413(self, client):
        """Images exceeding 10 MB are rejected with 413."""
        # 11 MB file
        response = client.post(
            "/api/v1/chat/image",
            data={"sender_id": "584127006823"},
            files={"image": _fake_image_file(size=11 * 1024 * 1024)},
        )

        assert response.status_code == 413

    def test_unsupported_mime_type_returns_415(self, client):
        """Non-image MIME types are rejected with 415."""
        response = client.post(
            "/api/v1/chat/image",
            data={"sender_id": "584127006823"},
            files={"image": ("file.txt", io.BytesIO(b"hello"), "text/plain")},
        )

        assert response.status_code == 415

    def test_missing_sender_id_returns_422(self, client):
        """Request without sender_id fails validation."""
        response = client.post(
            "/api/v1/chat/image",
            data={},
            files={"image": _fake_image_file()},
        )

        assert response.status_code == 422

    def test_missing_image_returns_422(self, client):
        """Request without image file fails validation."""
        response = client.post(
            "/api/v1/chat/image",
            data={"sender_id": "584127006823"},
        )

        assert response.status_code == 422

    @patch("farmafacil.api.routes.stop_collecting", return_value=[
        {"type": "text", "body": "\U0001f50d Analizando la imagen..."},
        {"type": "text", "body": "Identified: Ibuprofen 400mg"},
        {"type": "text", "body": "\U0001f50e Buscando *ibuprofeno*..."},
    ])
    @patch("farmafacil.api.routes.start_collecting")
    @patch("farmafacil.api.routes.handle_incoming_message", new_callable=AsyncMock)
    @patch("farmafacil.bot.whatsapp.send_text_message", new_callable=AsyncMock)
    @patch("farmafacil.services.drug_translation.translate_drug_query", new_callable=AsyncMock)
    @patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock)
    @patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image"})
    @patch("farmafacil.services.users.increment_token_usage", new_callable=AsyncMock)
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    def test_english_drug_name_translated_before_search(
        self, mock_get_user, mock_validate, mock_tokens,
        mock_encode, mock_analyze, mock_translate,
        mock_send_text, mock_handler, mock_start, mock_stop, client,
    ):
        """English drug names from Vision are translated to Spanish before searching."""
        user_mock = MagicMock(id=1, name="Daniel")
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock
        mock_analyze.return_value = FakeAnalysisResult(
            drug_names=["ibuprofen"],
        )
        # translate_drug_query returns a TranslationResult-like object.
        # MagicMock(name=...) is special in mock — use a simple namespace.
        tr_result = MagicMock()
        tr_result.name = "ibuprofeno"
        mock_translate.return_value = tr_result

        response = client.post(
            "/api/v1/chat/image",
            data={"sender_id": "584127006823"},
            files={"image": _fake_image_file()},
        )

        assert response.status_code == 200
        mock_translate.assert_called_once_with("ibuprofen")
        # Drug search should use the translated name with wa_profile_name
        mock_handler.assert_called_once_with(
            "584127006823", "ibuprofeno", wa_profile_name="",
        )

    @patch("farmafacil.api.routes.stop_collecting", return_value=[
        {"type": "text", "body": "\U0001f50d Analizando la imagen..."},
        {"type": "text", "body": "Identificado: Losartan"},
    ])
    @patch("farmafacil.api.routes.start_collecting")
    @patch("farmafacil.api.routes.handle_incoming_message", new_callable=AsyncMock)
    @patch("farmafacil.bot.whatsapp.send_text_message", new_callable=AsyncMock)
    @patch("farmafacil.services.drug_translation.translate_drug_query", new_callable=AsyncMock, return_value=None)
    @patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock)
    @patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image"})
    @patch("farmafacil.services.users.increment_token_usage", new_callable=AsyncMock)
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    def test_caption_passed_to_analyze(
        self, mock_get_user, mock_validate, mock_tokens,
        mock_encode, mock_analyze, mock_translate,
        mock_send_text, mock_handler, mock_start, mock_stop, client,
    ):
        """Caption text is forwarded to analyze_image for context."""
        user_mock = MagicMock(id=1, name="Daniel")
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock
        mock_analyze.return_value = FakeAnalysisResult()

        response = client.post(
            "/api/v1/chat/image",
            data={"sender_id": "584127006823", "caption": "esta es mi receta"},
            files={"image": _fake_image_file()},
        )

        assert response.status_code == 200
        mock_analyze.assert_called_once_with({"type": "image"}, "esta es mi receta")

    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    def test_wa_profile_name_passed_to_user_creation(
        self, mock_get_user, mock_validate, client,
    ):
        """sender_name is passed as wa_profile_name for onboarding pre-fill."""
        user_mock = MagicMock(id=1, name="Johnny")
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock

        # We need to mock encode to return None so it exits early
        with patch(
            "farmafacil.services.media.encode_image_for_vision", return_value=None,
        ):
            response = client.post(
                "/api/v1/chat/image",
                data={
                    "sender_id": "584127006823",
                    "sender_name": "Johnny Gonzalez",
                },
                files={"image": _fake_image_file()},
            )

        assert response.status_code == 200
        mock_get_user.assert_called_once_with(
            "584127006823", wa_profile_name="Johnny Gonzalez",
        )

    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock, side_effect=Exception("DB down"))
    def test_user_lookup_failure_returns_500(self, mock_get_user, client):
        """DB failure during user lookup returns 500."""
        response = client.post(
            "/api/v1/chat/image",
            data={"sender_id": "584127006823"},
            files={"image": _fake_image_file()},
        )

        assert response.status_code == 500

    @patch("farmafacil.api.routes.stop_collecting", return_value=[
        {"type": "text", "body": "\U0001f50d Analizando la imagen..."},
    ])
    @patch("farmafacil.api.routes.start_collecting")
    @patch("farmafacil.bot.whatsapp.send_text_message", new_callable=AsyncMock)
    @patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock, side_effect=Exception("Vision API down"))
    @patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image"})
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    def test_vision_api_failure_returns_collected(
        self, mock_get_user, mock_validate,
        mock_encode, mock_analyze, mock_send_text,
        mock_start, mock_stop, client,
    ):
        """When Vision API crashes, still return whatever was collected."""
        user_mock = MagicMock(id=1, name="Daniel")
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock

        response = client.post(
            "/api/v1/chat/image",
            data={"sender_id": "584127006823"},
            files={"image": _fake_image_file()},
        )

        # Should return 200 (not 500) with partial responses
        assert response.status_code == 200
        data = response.json()
        assert len(data["responses"]) >= 1

    @patch("farmafacil.api.routes.stop_collecting", return_value=[
        {"type": "text", "body": "\U0001f50d Analizando la imagen..."},
        {"type": "text", "body": "Receta analizada"},
        {"type": "text", "body": "No pude extraer nombres de medicamentos..."},
    ])
    @patch("farmafacil.api.routes.start_collecting")
    @patch("farmafacil.api.routes.handle_incoming_message", new_callable=AsyncMock)
    @patch("farmafacil.bot.whatsapp.send_text_message", new_callable=AsyncMock)
    @patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock)
    @patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image"})
    @patch("farmafacil.services.users.increment_token_usage", new_callable=AsyncMock)
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    def test_prescription_with_no_drug_names(
        self, mock_get_user, mock_validate, mock_tokens,
        mock_encode, mock_analyze,
        mock_send_text, mock_handler, mock_start, mock_stop, client,
    ):
        """Prescription recognized but no drug names extracted → helpful fallback."""
        user_mock = MagicMock(id=1, name="Daniel")
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock
        mock_analyze.return_value = FakeAnalysisResult(
            image_type="prescription",
            analysis_text="Receta analizada",
            drug_names=[],
        )

        response = client.post(
            "/api/v1/chat/image",
            data={"sender_id": "584127006823"},
            files={"image": _fake_image_file()},
        )

        assert response.status_code == 200
        mock_handler.assert_not_called()

    @patch("farmafacil.api.routes.stop_collecting", return_value=[
        {"type": "text", "body": "\U0001f50d Analizando la imagen..."},
        {"type": "text", "body": "No pude identificar el medicamento..."},
    ])
    @patch("farmafacil.api.routes.start_collecting")
    @patch("farmafacil.api.routes.handle_incoming_message", new_callable=AsyncMock)
    @patch("farmafacil.bot.whatsapp.send_text_message", new_callable=AsyncMock)
    @patch("farmafacil.services.image_analysis.analyze_image", new_callable=AsyncMock)
    @patch("farmafacil.services.media.encode_image_for_vision", return_value={"type": "image"})
    @patch("farmafacil.services.users.increment_token_usage", new_callable=AsyncMock)
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    def test_medicine_photo_with_no_drug_names(
        self, mock_get_user, mock_validate, mock_tokens,
        mock_encode, mock_analyze,
        mock_send_text, mock_handler, mock_start, mock_stop, client,
    ):
        """Medicine photo recognized but no drug name extracted → helpful fallback."""
        user_mock = MagicMock(id=1, name="Daniel")
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock
        mock_analyze.return_value = FakeAnalysisResult(
            image_type="medicine",
            analysis_text="",
            drug_names=[],
        )

        response = client.post(
            "/api/v1/chat/image",
            data={"sender_id": "584127006823"},
            files={"image": _fake_image_file()},
        )

        assert response.status_code == 200
        mock_handler.assert_not_called()
