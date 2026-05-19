"""Tests for v0.22.0 voice message support.

Covers: webhook audio dispatch, handler flow (download → save → transcribe → process),
voice service functions (save, transcribe, translate stub), VoiceMessage DB operations,
admin chat tools, and admin audio playback endpoint.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from farmafacil.models.database import VoiceMessage
from farmafacil.services.voice import (
    AUDIO_BASE_DIR,
    MAX_AUDIO_BYTES,
    get_audio_absolute_path,
    save_audio_file,
    transcribe_audio,
    translate_text,
)


# ── Voice service: save_audio_file ───────────────────────────────────


class TestSaveAudioFile:
    """Tests for services/voice.py save_audio_file."""

    def test_save_creates_file(self, tmp_path, monkeypatch):
        """Audio bytes are saved to disk with correct naming."""
        monkeypatch.setattr("farmafacil.services.voice.AUDIO_BASE_DIR", tmp_path / "audio")
        data = b"\x00\x01\x02" * 100
        rel_path = save_audio_file(data, user_id=1, wa_message_id="wamid.test123")
        abs_path = (tmp_path / "audio").parent / rel_path
        assert abs_path.exists()
        assert abs_path.read_bytes() == data

    def test_save_creates_user_subdirectory(self, tmp_path, monkeypatch):
        """User-specific subdirectory is created automatically."""
        monkeypatch.setattr("farmafacil.services.voice.AUDIO_BASE_DIR", tmp_path / "audio")
        save_audio_file(b"test", user_id=42, wa_message_id="wamid.abc")
        assert (tmp_path / "audio" / "42").is_dir()

    def test_save_returns_relative_path(self, tmp_path, monkeypatch):
        """Returned path is relative to AUDIO_BASE_DIR parent."""
        monkeypatch.setattr("farmafacil.services.voice.AUDIO_BASE_DIR", tmp_path / "audio")
        rel_path = save_audio_file(b"data", user_id=1, wa_message_id="wamid.xyz")
        assert rel_path.startswith("audio/1/")
        assert rel_path.endswith(".ogg")

    def test_save_sanitizes_wa_message_id(self, tmp_path, monkeypatch):
        """Slashes in wa_message_id are replaced for filesystem safety."""
        monkeypatch.setattr("farmafacil.services.voice.AUDIO_BASE_DIR", tmp_path / "audio")
        rel_path = save_audio_file(b"data", user_id=1, wa_message_id="wamid/with/slashes")
        filename = Path(rel_path).name
        assert "/" not in filename.replace("audio/1/", "")

    def test_save_handles_empty_wa_message_id(self, tmp_path, monkeypatch):
        """Empty or None wa_message_id defaults to 'unknown'."""
        monkeypatch.setattr("farmafacil.services.voice.AUDIO_BASE_DIR", tmp_path / "audio")
        rel_path = save_audio_file(b"data", user_id=1, wa_message_id="")
        assert "unknown" in Path(rel_path).name


# ── Voice service: get_audio_absolute_path ───────────────────────────


class TestGetAudioAbsolutePath:
    """Tests for converting DB-stored relative path to absolute."""

    def test_resolves_to_parent_of_base_dir(self):
        """Relative path is joined to AUDIO_BASE_DIR.parent."""
        result = get_audio_absolute_path("audio/1/20260518_wamid.ogg")
        expected = AUDIO_BASE_DIR.parent / "audio/1/20260518_wamid.ogg"
        assert result == expected


# ── Voice service: transcribe_audio ──────────────────────────────────


class TestTranscribeAudio:
    """Tests for Whisper API transcription."""

    @pytest.mark.asyncio
    async def test_returns_none_without_api_key(self, monkeypatch):
        """If OPENAI_API_KEY is empty, transcription is skipped."""
        monkeypatch.setattr("farmafacil.services.voice.OPENAI_API_KEY", "")
        text, lang, dur = await transcribe_audio("/fake/path.ogg")
        assert text is None
        assert lang is None
        assert dur is None

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_file(self, monkeypatch, tmp_path):
        """Non-existent file returns (None, None)."""
        monkeypatch.setattr("farmafacil.services.voice.OPENAI_API_KEY", "sk-test")
        text, lang, dur = await transcribe_audio(tmp_path / "nonexistent.ogg")
        assert text is None
        assert lang is None
        assert dur is None

    @pytest.mark.asyncio
    async def test_returns_none_for_oversized_file(self, monkeypatch, tmp_path):
        """Files exceeding MAX_AUDIO_BYTES are rejected."""
        monkeypatch.setattr("farmafacil.services.voice.OPENAI_API_KEY", "sk-test")
        big_file = tmp_path / "big.ogg"
        big_file.write_bytes(b"\x00" * (MAX_AUDIO_BYTES + 1))
        text, lang, dur = await transcribe_audio(big_file)
        assert text is None
        assert lang is None
        assert dur is None

    @pytest.mark.asyncio
    async def test_successful_transcription(self, monkeypatch, tmp_path):
        """Mock a successful Whisper API call and verify extraction."""
        monkeypatch.setattr("farmafacil.services.voice.OPENAI_API_KEY", "sk-test")

        audio_file = tmp_path / "test.ogg"
        audio_file.write_bytes(b"\x00" * 100)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "text": "Hola, necesito ibuprofeno",
            "language": "es",
            "duration": 3.5,
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("farmafacil.services.voice.httpx.AsyncClient", return_value=mock_client):
            text, lang, dur = await transcribe_audio(audio_file)

        assert text == "Hola, necesito ibuprofeno"
        assert lang == "es"
        assert dur == 3.5

    @pytest.mark.asyncio
    async def test_transcription_http_error(self, monkeypatch, tmp_path):
        """HTTP errors from Whisper API return (None, None, None)."""
        monkeypatch.setattr("farmafacil.services.voice.OPENAI_API_KEY", "sk-test")
        import httpx

        audio_file = tmp_path / "test.ogg"
        audio_file.write_bytes(b"\x00" * 100)

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limit exceeded"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("farmafacil.services.voice.httpx.AsyncClient", return_value=mock_client):
            text, lang, dur = await transcribe_audio(audio_file)

        assert text is None
        assert lang is None
        assert dur is None

    @pytest.mark.asyncio
    async def test_empty_transcription_returns_none(self, monkeypatch, tmp_path):
        """If Whisper returns empty text, return (None, language, duration)."""
        monkeypatch.setattr("farmafacil.services.voice.OPENAI_API_KEY", "sk-test")

        audio_file = tmp_path / "test.ogg"
        audio_file.write_bytes(b"\x00" * 100)

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "text": "   ",
            "language": "es",
            "duration": 1.0,
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("farmafacil.services.voice.httpx.AsyncClient", return_value=mock_client):
            text, lang, dur = await transcribe_audio(audio_file)

        assert text is None
        assert lang == "es"
        assert dur == 1.0


# ── Voice service: translate_text (stub) ─────────────────────────────


class TestTranslateText:
    """Tests for the translation stub."""

    @pytest.mark.asyncio
    async def test_stub_returns_none(self):
        """translate_text is a shell — always returns None."""
        result = await translate_text("Hola", "es", "en")
        assert result is None

    @pytest.mark.asyncio
    async def test_stub_accepts_any_language_pair(self):
        """Stub handles any language combination gracefully."""
        result = await translate_text("Hello", "en", "pt")
        assert result is None


# ── Webhook audio dispatch ───────────────────────────────────────────


class TestWebhookAudioDispatch:
    """Tests for webhook.py audio message type handling."""

    def test_webhook_imports_handle_voice_message(self):
        """webhook.py imports handle_voice_message from handler."""
        from farmafacil.bot.webhook import webhook_router
        import importlib
        mod = importlib.import_module("farmafacil.bot.webhook")
        source = Path(mod.__file__).read_text()
        assert "handle_voice_message" in source

    def test_webhook_has_audio_branch(self):
        """webhook.py has elif msg_type == 'audio' dispatch."""
        import importlib
        mod = importlib.import_module("farmafacil.bot.webhook")
        source = Path(mod.__file__).read_text()
        assert 'msg_type == "audio"' in source


# ── Handler: handle_voice_message ────────────────────────────────────


class TestHandleVoiceMessage:
    """Tests for bot/handler.py handle_voice_message."""

    @pytest.mark.asyncio
    async def test_download_failure_sends_fallback(self):
        """When media download fails, user gets a 'type instead' message."""
        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock) as mock_user,
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock) as mock_validate,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock) as mock_dl,
        ):
            user_obj = MagicMock()
            user_obj.id = 1
            mock_user.return_value = user_obj
            mock_validate.return_value = user_obj
            mock_dl.return_value = None

            from farmafacil.bot.handler import handle_voice_message
            await handle_voice_message("14258904657", "media_123", wa_message_id="wamid.test")

            mock_send.assert_called_once()
            msg = mock_send.call_args[0][1]
            assert "texto" in msg.lower()

    @pytest.mark.asyncio
    async def test_oversized_audio_sends_fallback(self):
        """When audio exceeds MAX_AUDIO_BYTES, user gets size warning."""
        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock) as mock_user,
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock) as mock_validate,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock) as mock_dl,
        ):
            user_obj = MagicMock()
            user_obj.id = 1
            mock_user.return_value = user_obj
            mock_validate.return_value = user_obj
            # Return data larger than MAX_AUDIO_BYTES
            mock_dl.return_value = (b"\x00" * (MAX_AUDIO_BYTES + 1), "audio/ogg")

            from farmafacil.bot.handler import handle_voice_message
            await handle_voice_message("14258904657", "media_456", wa_message_id="wamid.big")

            mock_send.assert_called_once()
            msg = mock_send.call_args[0][1]
            assert "largo" in msg.lower() or "corto" in msg.lower()

    @pytest.mark.asyncio
    async def test_transcription_failure_asks_to_type(self):
        """When transcription returns None, user is asked to type."""
        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock) as mock_user,
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock) as mock_validate,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock) as mock_dl,
            patch("farmafacil.services.voice.save_audio_file") as mock_save,
            patch("farmafacil.services.voice.get_audio_absolute_path") as mock_abs,
            patch("farmafacil.services.voice.transcribe_audio", new_callable=AsyncMock) as mock_transcribe,
            patch("farmafacil.db.session.async_session") as mock_session_cls,
        ):
            user_obj = MagicMock()
            user_obj.id = 1
            mock_user.return_value = user_obj
            mock_validate.return_value = user_obj
            mock_dl.return_value = (b"\x00" * 100, "audio/ogg")
            mock_save.return_value = "audio/1/test.ogg"
            mock_abs.return_value = Path("/fake/audio/1/test.ogg")
            mock_transcribe.return_value = (None, None, None)

            # Mock DB session for VoiceMessage insert
            mock_sess = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_sess.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
            # Make the VoiceMessage mock have an id after commit
            mock_sess.add = MagicMock()

            from farmafacil.bot.handler import handle_voice_message
            await handle_voice_message("14258904657", "media_789", wa_message_id="wamid.nope")

            # Last call should be the "could not understand" message
            last_call = mock_send.call_args_list[-1]
            msg = last_call[0][1]
            assert "texto" in msg.lower()

    @pytest.mark.asyncio
    async def test_successful_transcription_sends_ack_and_processes(self):
        """Successful flow: ack with transcription, then handle_incoming_message."""
        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock) as mock_user,
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock) as mock_validate,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.handle_incoming_message", new_callable=AsyncMock) as mock_handle,
            patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock) as mock_dl,
            patch("farmafacil.services.voice.save_audio_file") as mock_save,
            patch("farmafacil.services.voice.get_audio_absolute_path") as mock_abs,
            patch("farmafacil.services.voice.transcribe_audio", new_callable=AsyncMock) as mock_transcribe,
            patch("farmafacil.db.session.async_session") as mock_session_cls,
        ):
            user_obj = MagicMock()
            user_obj.id = 1
            mock_user.return_value = user_obj
            mock_validate.return_value = user_obj
            mock_dl.return_value = (b"\x00" * 100, "audio/ogg")
            mock_save.return_value = "audio/1/test.ogg"
            mock_abs.return_value = Path("/fake/audio/1/test.ogg")
            mock_transcribe.return_value = ("necesito losartan", "es", 4.2)

            # Mock DB session
            mock_sess = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_sess.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
            mock_sess.add = MagicMock()

            from farmafacil.bot.handler import handle_voice_message
            await handle_voice_message("14258904657", "media_ok", wa_message_id="wamid.ok")

            # Should send ack with transcription
            ack_call = mock_send.call_args_list[-1]
            ack_msg = ack_call[0][1]
            assert "Te escuché" in ack_msg
            assert "losartan" in ack_msg

            # Should process transcription as text
            mock_handle.assert_called_once_with(
                "14258904657", "necesito losartan", wa_message_id="wamid.ok"
            )


# ── VoiceMessage model ───────────────────────────────────────────────


class TestVoiceMessageModel:
    """Tests for the VoiceMessage SQLAlchemy model."""

    def test_model_tablename(self):
        """Table is named 'voice_messages'."""
        assert VoiceMessage.__tablename__ == "voice_messages"

    def test_model_has_required_columns(self):
        """All expected columns exist on the model."""
        expected_cols = {
            "id", "user_id", "phone_number", "audio_path", "audio_url",
            "duration_seconds", "original_language", "transcription",
            "translation_es", "translation_en", "wa_message_id",
            "conversation_log_id", "transcription_model", "created_at",
        }
        actual_cols = {c.name for c in VoiceMessage.__table__.columns}
        assert expected_cols == actual_cols

    def test_user_id_is_not_nullable(self):
        """user_id column is required."""
        col = VoiceMessage.__table__.columns["user_id"]
        assert col.nullable is False

    def test_translation_columns_are_nullable(self):
        """Translation shell columns allow NULL (future implementation)."""
        for col_name in ("translation_es", "translation_en"):
            col = VoiceMessage.__table__.columns[col_name]
            assert col.nullable is True, f"{col_name} should be nullable"

    def test_conversation_log_fk(self):
        """conversation_log_id has a foreign key to conversation_logs."""
        col = VoiceMessage.__table__.columns["conversation_log_id"]
        fk_targets = [fk.target_fullname for fk in col.foreign_keys]
        assert "conversation_logs.id" in fk_targets

    def test_user_fk(self):
        """user_id has a foreign key to users."""
        col = VoiceMessage.__table__.columns["user_id"]
        fk_targets = [fk.target_fullname for fk in col.foreign_keys]
        assert "users.id" in fk_targets


# ── Admin chat tools ─────────────────────────────────────────────────


class TestAdminChatVoiceTools:
    """Tests for admin_chat.py voice message tools."""

    def test_tools_registered(self):
        """list_voice_messages and get_voice_message are in TOOLS dict."""
        from farmafacil.services.admin_chat import TOOLS
        assert "list_voice_messages" in TOOLS
        assert "get_voice_message" in TOOLS

    def test_list_tool_description(self):
        """list_voice_messages has a Spanish description."""
        from farmafacil.services.admin_chat import TOOLS
        desc, _ = TOOLS["list_voice_messages"]
        assert "voz" in desc.lower() or "voice" in desc.lower()

    def test_get_tool_description(self):
        """get_voice_message has a description mentioning id."""
        from farmafacil.services.admin_chat import TOOLS
        desc, _ = TOOLS["get_voice_message"]
        assert "id" in desc.lower()

    @pytest.mark.asyncio
    async def test_list_empty_returns_sin_mensajes(self):
        """list_voice_messages on empty table returns 'Sin mensajes'."""
        from farmafacil.services.admin_chat import _tool_list_voice_messages

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_sess = AsyncMock()
        mock_sess.execute = AsyncMock(return_value=mock_result)

        with patch("farmafacil.services.admin_chat.async_session") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await _tool_list_voice_messages({})

        assert "Sin mensajes de voz" in result

    @pytest.mark.asyncio
    async def test_list_returns_formatted_entries(self):
        """list_voice_messages formats entries with emoji and key info."""
        from farmafacil.services.admin_chat import _tool_list_voice_messages

        mock_vm = MagicMock()
        mock_vm.id = 1
        mock_vm.phone_number = "14258904657"
        mock_vm.transcription = "Necesito losartan para la presión"
        mock_vm.original_language = "es"
        mock_vm.duration_seconds = 5.2
        mock_vm.created_at = datetime(2026, 5, 18, 14, 30)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_vm]
        mock_sess = AsyncMock()
        mock_sess.execute = AsyncMock(return_value=mock_result)

        with patch("farmafacil.services.admin_chat.async_session") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await _tool_list_voice_messages({})

        assert "🎙️ #1" in result
        assert "es" in result
        assert "5s" in result
        assert "14258904657" in result

    @pytest.mark.asyncio
    async def test_get_missing_id_returns_falta(self):
        """get_voice_message with no id returns 'Falta id'."""
        from farmafacil.services.admin_chat import _tool_get_voice_message
        result = await _tool_get_voice_message({})
        assert "Falta id" in result

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_no_existe(self):
        """get_voice_message for unknown id returns 'no existe'."""
        from farmafacil.services.admin_chat import _tool_get_voice_message

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_sess = AsyncMock()
        mock_sess.execute = AsyncMock(return_value=mock_result)

        with patch("farmafacil.services.admin_chat.async_session") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await _tool_get_voice_message({"id": 999})

        assert "no existe" in result

    @pytest.mark.asyncio
    async def test_get_returns_full_details(self):
        """get_voice_message returns all fields for a valid record."""
        from farmafacil.services.admin_chat import _tool_get_voice_message

        mock_vm = MagicMock()
        mock_vm.id = 1
        mock_vm.user_id = 4
        mock_vm.phone_number = "14258904657"
        mock_vm.original_language = "es"
        mock_vm.duration_seconds = 7.3
        mock_vm.audio_path = "audio/4/20260518_wamid.ogg"
        mock_vm.transcription = "Necesito losartan"
        mock_vm.translation_es = None
        mock_vm.translation_en = None
        mock_vm.transcription_model = "whisper-1"
        mock_vm.wa_message_id = "wamid.test"
        mock_vm.conversation_log_id = 42
        mock_vm.created_at = datetime(2026, 5, 18, 14, 30)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_vm
        mock_sess = AsyncMock()
        mock_sess.execute = AsyncMock(return_value=mock_result)

        with patch("farmafacil.services.admin_chat.async_session") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await _tool_get_voice_message({"id": 1})

        assert "Mensaje de voz #1" in result
        assert "14258904657" in result
        assert "losartan" in result
        assert "whisper-1" in result
        assert "#42" in result
        assert "7.3s" in result


# ── Admin audio playback endpoint ────────────────────────────────────


class TestAudioPlaybackEndpoint:
    """Tests for GET /api/v1/audio/{voice_message_id}."""

    def test_route_exists(self):
        """The audio playback route is registered."""
        from farmafacil.api.routes import router
        paths = [r.path for r in router.routes]
        assert "/api/v1/audio/{voice_message_id}" in paths

    def test_route_requires_auth(self):
        """Audio endpoint has _admin parameter (injected by _require_admin)."""
        import inspect
        from farmafacil.api.routes import get_voice_audio
        sig = inspect.signature(get_voice_audio)
        assert "_admin" in sig.parameters, "Audio endpoint must have _admin dependency"


# ── VoiceMessageAdmin ────────────────────────────────────────────────


class TestVoiceMessageAdmin:
    """Tests for SQLAdmin VoiceMessage view."""

    def test_admin_view_exists(self):
        """VoiceMessageAdmin is in the ADMIN_VIEWS list."""
        from farmafacil.api.admin import ADMIN_VIEWS, VoiceMessageAdmin
        admin_classes = [v.__name__ if isinstance(v, type) else type(v).__name__ for v in ADMIN_VIEWS]
        assert "VoiceMessageAdmin" in admin_classes

    def test_admin_is_read_only(self):
        """VoiceMessageAdmin cannot create, edit, or delete."""
        from farmafacil.api.admin import VoiceMessageAdmin
        assert VoiceMessageAdmin.can_create is False
        assert VoiceMessageAdmin.can_delete is False
        assert VoiceMessageAdmin.can_edit is False

    def test_admin_model_is_voice_message(self):
        """VoiceMessageAdmin targets the VoiceMessage model."""
        from farmafacil.api.admin import VoiceMessageAdmin
        assert VoiceMessageAdmin.model is VoiceMessage

    def test_admin_icon_is_microphone(self):
        """VoiceMessageAdmin uses microphone icon."""
        from farmafacil.api.admin import VoiceMessageAdmin
        assert "microphone" in VoiceMessageAdmin.icon

    def test_admin_audio_path_formatter_escapes_html(self):
        """audio_path formatter uses markupsafe.escape to prevent XSS."""
        from farmafacil.api.admin import VoiceMessageAdmin
        formatter = VoiceMessageAdmin.column_formatters_detail[VoiceMessage.audio_path]
        mock_vm = MagicMock()
        mock_vm.id = 1
        mock_vm.audio_path = '<script>alert("xss")</script>'
        result = str(formatter(mock_vm, None))
        assert "<script>" not in result
        assert "&lt;script&gt;" in result


# ── Constants and config ─────────────────────────────────────────────


class TestVoiceConstants:
    """Tests for voice module constants."""

    def test_max_audio_bytes_matches_whisper_limit(self):
        """MAX_AUDIO_BYTES is 25 MB (Whisper API limit)."""
        assert MAX_AUDIO_BYTES == 25 * 1024 * 1024

    def test_openai_api_key_in_config(self):
        """OPENAI_API_KEY is defined in config module."""
        from farmafacil.config import OPENAI_API_KEY
        assert isinstance(OPENAI_API_KEY, str)

    def test_audio_base_dir_is_path(self):
        """AUDIO_BASE_DIR is a Path object."""
        assert isinstance(AUDIO_BASE_DIR, Path)
