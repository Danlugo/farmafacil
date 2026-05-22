"""Tests for POST /api/v1/chat/voice — voice message relay endpoint.

The endpoint accepts multipart/form-data with sender_id, sender_name, and
audio file.  It saves the audio, transcribes via Whisper, stores a
VoiceMessage record, then runs the handler in proxy mode.

Conversation logging: transcription text is logged as inbound and bot
responses as outbound, so ``get_recent_history()`` provides AI context
for follow-up questions from voice relay users.

Note: The voice endpoint uses lazy imports for services (voice, users) inside
the function body, so mocks must target the *source* module paths:
  - farmafacil.services.voice.{save_audio_file, transcribe_audio, ...}
  - farmafacil.services.users.{get_or_create_user, validate_user_profile}
Module-level imports (async_session, handle_incoming_message, start/stop_collecting)
are patched on farmafacil.api.routes as usual.
"""

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from farmafacil.api.app import create_app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    app = create_app()
    return TestClient(app)


def _fake_audio_file(size: int = 1024) -> tuple[str, io.BytesIO, str]:
    """Create a fake audio file for upload."""
    return ("voice.ogg", io.BytesIO(b"\x00" * size), "audio/ogg")


class TestChatVoiceEndpoint:
    """Test the /api/v1/chat/voice endpoint."""

    @patch("farmafacil.api.routes.stop_collecting", return_value=[
        {"type": "text", "body": "🎙️ Te escuché: _hola_"},
        {"type": "text", "body": "Hola! Bienvenido a FarmaFacil."},
    ])
    @patch("farmafacil.api.routes.start_collecting")
    @patch("farmafacil.api.routes.handle_incoming_message", new_callable=AsyncMock)
    @patch("farmafacil.bot.whatsapp.send_text_message", new_callable=AsyncMock)
    @patch("farmafacil.services.voice.transcribe_audio", new_callable=AsyncMock, return_value=("hola", "es", 1.5))
    @patch("farmafacil.services.voice.save_audio_file", return_value="audio/1/20260521_relay_abc.ogg")
    @patch("farmafacil.services.voice.get_audio_absolute_path")
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    @patch("farmafacil.api.routes.async_session")
    def test_successful_voice_transcription(
        self, mock_session, mock_get_user, mock_validate, mock_abs_path,
        mock_save, mock_transcribe, mock_send_text, mock_handler,
        mock_start, mock_stop, client,
    ):
        """Happy path: audio uploaded → transcribed → handler runs → response returned."""
        user_mock = MagicMock(id=1, name="Daniel")
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock
        mock_abs_path.return_value = MagicMock()

        # Mock the DB session context for VoiceMessage save
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_ctx)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        session_ctx.add = MagicMock()
        session_ctx.commit = AsyncMock()
        session_ctx.refresh = AsyncMock(side_effect=lambda vm: setattr(vm, "id", 42))
        mock_session.return_value = session_ctx

        response = client.post(
            "/api/v1/chat/voice",
            data={"sender_id": "584127006823", "sender_name": "Daniel"},
            files={"audio": _fake_audio_file()},
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["responses"]) == 2
        assert body["responses"][0]["type"] == "text"
        assert "Te escuché" in body["responses"][0]["body"]

        # Verify handler was called with transcription text
        mock_handler.assert_called_once()
        assert mock_handler.call_args.kwargs["message_text"] == "hola"

    @patch("farmafacil.services.voice.transcribe_audio", new_callable=AsyncMock, return_value=(None, None, None))
    @patch("farmafacil.services.voice.save_audio_file", return_value="audio/1/20260521_relay_abc.ogg")
    @patch("farmafacil.services.voice.get_audio_absolute_path")
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    @patch("farmafacil.api.routes.async_session")
    def test_transcription_failure_returns_error_message(
        self, mock_session, mock_get_user, mock_validate, mock_abs_path,
        mock_save, mock_transcribe, client,
    ):
        """When Whisper fails, return a friendly error response (not HTTP error)."""
        user_mock = MagicMock(id=1)
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock
        mock_abs_path.return_value = MagicMock()

        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_ctx)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        session_ctx.add = MagicMock()
        session_ctx.commit = AsyncMock()
        session_ctx.refresh = AsyncMock(side_effect=lambda vm: setattr(vm, "id", 99))
        mock_session.return_value = session_ctx

        response = client.post(
            "/api/v1/chat/voice",
            data={"sender_id": "584127006823"},
            files={"audio": _fake_audio_file()},
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["responses"]) == 1
        assert "No pude entender" in body["responses"][0]["body"]

    def test_missing_audio_returns_422(self, client):
        """Missing audio file should return 422 validation error."""
        response = client.post(
            "/api/v1/chat/voice",
            data={"sender_id": "584127006823"},
        )
        assert response.status_code == 422

    def test_missing_sender_id_returns_422(self, client):
        """Missing sender_id should return 422 validation error."""
        response = client.post(
            "/api/v1/chat/voice",
            files={"audio": _fake_audio_file()},
        )
        assert response.status_code == 422

    def test_short_sender_id_returns_422(self, client):
        """sender_id shorter than 5 chars should be rejected."""
        response = client.post(
            "/api/v1/chat/voice",
            data={"sender_id": "123"},
            files={"audio": _fake_audio_file()},
        )
        assert response.status_code == 422

    @patch("farmafacil.services.voice.MAX_AUDIO_BYTES", 100)
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    def test_oversized_audio_returns_413(self, mock_get_user, mock_validate, client):
        """Audio exceeding MAX_AUDIO_BYTES should return 413."""
        response = client.post(
            "/api/v1/chat/voice",
            data={"sender_id": "584127006823"},
            files={"audio": ("big.ogg", io.BytesIO(b"\x00" * 200), "audio/ogg")},
        )
        assert response.status_code == 413

    @patch("farmafacil.services.voice.save_audio_file", return_value="audio/1/20260521_relay_abc.ogg")
    @patch("farmafacil.services.voice.get_audio_absolute_path")
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    @patch("farmafacil.api.routes.async_session")
    def test_db_failure_cleans_up_orphan_audio(
        self, mock_session, mock_get_user, mock_validate, mock_abs_path,
        mock_save, client,
    ):
        """DB failure during VoiceMessage save returns 500 and cleans up audio file."""
        user_mock = MagicMock(id=1)
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock

        # Track unlink calls on the path mock
        path_mock = MagicMock()
        mock_abs_path.return_value = path_mock

        # Make the session context raise on commit
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_ctx)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        session_ctx.add = MagicMock()
        session_ctx.commit = AsyncMock(side_effect=RuntimeError("DB connection lost"))
        mock_session.return_value = session_ctx

        response = client.post(
            "/api/v1/chat/voice",
            data={"sender_id": "584127006823"},
            files={"audio": _fake_audio_file()},
        )

        assert response.status_code == 500
        # Verify orphan audio file was cleaned up
        path_mock.unlink.assert_called_once_with(missing_ok=True)

    @patch("farmafacil.services.voice.transcribe_audio", new_callable=AsyncMock, return_value=(None, None, None))
    @patch("farmafacil.services.voice.save_audio_file", return_value="audio/1/20260521_relay_abc.ogg")
    @patch("farmafacil.services.voice.get_audio_absolute_path")
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    @patch("farmafacil.api.routes.async_session")
    def test_transcription_failure_cleans_up_audio(
        self, mock_session, mock_get_user, mock_validate, mock_abs_path,
        mock_save, mock_transcribe, client,
    ):
        """Empty transcription returns friendly message and deletes audio file."""
        user_mock = MagicMock(id=1)
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock

        path_mock = MagicMock()
        mock_abs_path.return_value = path_mock

        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_ctx)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        session_ctx.add = MagicMock()
        session_ctx.commit = AsyncMock()
        session_ctx.refresh = AsyncMock(side_effect=lambda vm: setattr(vm, "id", 99))
        mock_session.return_value = session_ctx

        response = client.post(
            "/api/v1/chat/voice",
            data={"sender_id": "584127006823"},
            files={"audio": _fake_audio_file()},
        )

        assert response.status_code == 200
        body = response.json()
        assert "No pude entender" in body["responses"][0]["body"]
        # Verify audio file was cleaned up after empty transcription
        path_mock.unlink.assert_called_once_with(missing_ok=True)


class TestChatVoiceConversationLogging:
    """Verify voice relay logs transcription to conversation_log."""

    @patch("farmafacil.api.routes.stop_collecting", return_value=[
        {"type": "text", "body": "🎙️ Te escuché: _hola_"},
        {"type": "text", "body": "Hola! Bienvenido a FarmaFacil."},
    ])
    @patch("farmafacil.api.routes.start_collecting")
    @patch("farmafacil.api.routes.handle_incoming_message", new_callable=AsyncMock)
    @patch("farmafacil.bot.whatsapp.send_text_message", new_callable=AsyncMock)
    @patch("farmafacil.services.voice.transcribe_audio", new_callable=AsyncMock, return_value=("hola", "es", 1.5))
    @patch("farmafacil.services.voice.save_audio_file", return_value="audio/1/test.ogg")
    @patch("farmafacil.services.voice.get_audio_absolute_path")
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    @patch("farmafacil.api.routes.async_session")
    @patch("farmafacil.api.routes.log_outbound", new_callable=AsyncMock)
    @patch("farmafacil.api.routes.log_inbound", new_callable=AsyncMock)
    def test_voice_logs_transcription_as_inbound(
        self, mock_log_inbound, mock_log_outbound, mock_session,
        mock_get_user, mock_validate, mock_abs_path, mock_save,
        mock_transcribe, mock_send_text, mock_handler,
        mock_start, mock_stop, client,
    ):
        """Voice endpoint logs the transcription text as inbound."""
        user_mock = MagicMock(id=1, name="Daniel")
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock
        mock_abs_path.return_value = MagicMock()

        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_ctx)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        session_ctx.add = MagicMock()
        session_ctx.commit = AsyncMock()
        session_ctx.refresh = AsyncMock(side_effect=lambda vm: setattr(vm, "id", 42))
        mock_session.return_value = session_ctx

        response = client.post(
            "/api/v1/chat/voice",
            data={"sender_id": "584127006823", "sender_name": "Daniel"},
            files={"audio": _fake_audio_file()},
        )

        assert response.status_code == 200

        # Verify inbound log was called with the transcription
        mock_log_inbound.assert_called_once_with("584127006823", "hola")

    @patch("farmafacil.api.routes.stop_collecting", return_value=[
        {"type": "text", "body": "🎙️ Te escuché: _hola_"},
        {"type": "text", "body": "Hola! Bienvenido."},
    ])
    @patch("farmafacil.api.routes.start_collecting")
    @patch("farmafacil.api.routes.handle_incoming_message", new_callable=AsyncMock)
    @patch("farmafacil.bot.whatsapp.send_text_message", new_callable=AsyncMock)
    @patch("farmafacil.services.voice.transcribe_audio", new_callable=AsyncMock, return_value=("hola", "es", 1.5))
    @patch("farmafacil.services.voice.save_audio_file", return_value="audio/1/test.ogg")
    @patch("farmafacil.services.voice.get_audio_absolute_path")
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    @patch("farmafacil.api.routes.async_session")
    @patch("farmafacil.api.routes.log_outbound", new_callable=AsyncMock)
    @patch("farmafacil.api.routes.log_inbound", new_callable=AsyncMock)
    def test_voice_logs_outbound_skips_ack(
        self, mock_log_inbound, mock_log_outbound, mock_session,
        mock_get_user, mock_validate, mock_abs_path, mock_save,
        mock_transcribe, mock_send_text, mock_handler,
        mock_start, mock_stop, client,
    ):
        """Voice ack ('Te escuché') is skipped; only real responses logged."""
        user_mock = MagicMock(id=1, name="Daniel")
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock
        mock_abs_path.return_value = MagicMock()

        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_ctx)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        session_ctx.add = MagicMock()
        session_ctx.commit = AsyncMock()
        session_ctx.refresh = AsyncMock(side_effect=lambda vm: setattr(vm, "id", 42))
        mock_session.return_value = session_ctx

        response = client.post(
            "/api/v1/chat/voice",
            data={"sender_id": "584127006823"},
            files={"audio": _fake_audio_file()},
        )

        assert response.status_code == 200

        # Voice ack is skipped, only the real response is logged
        assert mock_log_outbound.call_count == 1
        mock_log_outbound.assert_called_once_with("584127006823", "Hola! Bienvenido.")

    @patch("farmafacil.services.voice.transcribe_audio", new_callable=AsyncMock, return_value=(None, None, None))
    @patch("farmafacil.services.voice.save_audio_file", return_value="audio/1/test.ogg")
    @patch("farmafacil.services.voice.get_audio_absolute_path")
    @patch("farmafacil.services.users.validate_user_profile", new_callable=AsyncMock)
    @patch("farmafacil.services.users.get_or_create_user", new_callable=AsyncMock)
    @patch("farmafacil.api.routes.async_session")
    @patch("farmafacil.api.routes.log_inbound", new_callable=AsyncMock)
    def test_voice_no_inbound_log_on_empty_transcription(
        self, mock_log_inbound, mock_session, mock_get_user, mock_validate,
        mock_abs_path, mock_save, mock_transcribe, client,
    ):
        """Empty transcription should NOT log inbound (no useful text)."""
        user_mock = MagicMock(id=1)
        mock_get_user.return_value = user_mock
        mock_validate.return_value = user_mock

        path_mock = MagicMock()
        mock_abs_path.return_value = path_mock

        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_ctx)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        session_ctx.add = MagicMock()
        session_ctx.commit = AsyncMock()
        session_ctx.refresh = AsyncMock(side_effect=lambda vm: setattr(vm, "id", 99))
        mock_session.return_value = session_ctx

        response = client.post(
            "/api/v1/chat/voice",
            data={"sender_id": "584127006823"},
            files={"audio": _fake_audio_file()},
        )

        assert response.status_code == 200
        # Empty transcription returns early before step 7 (logging)
        mock_log_inbound.assert_not_called()
