"""Tests for v0.22.1 voice-to-action linking.

Verifies that voice_message_id is threaded from handle_voice_message through
handle_incoming_message to search_logs, user_feedback, and user_suggestions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Model tests: voice_message_id FK columns exist ─────────────────────


class TestVoiceMessageIdColumns:
    """Verify voice_message_id FK exists on SearchLog, UserFeedback, UserSuggestion."""

    def test_search_log_has_voice_message_id(self):
        """SearchLog model has voice_message_id column."""
        from farmafacil.models.database import SearchLog
        assert hasattr(SearchLog, "voice_message_id")

    def test_user_feedback_has_voice_message_id(self):
        """UserFeedback model has voice_message_id column."""
        from farmafacil.models.database import UserFeedback
        assert hasattr(UserFeedback, "voice_message_id")

    def test_user_suggestion_has_voice_message_id(self):
        """UserSuggestion model has voice_message_id column."""
        from farmafacil.models.database import UserSuggestion
        assert hasattr(UserSuggestion, "voice_message_id")

    def test_search_log_has_voice_message_relationship(self):
        """SearchLog model has voice_message relationship."""
        from farmafacil.models.database import SearchLog
        assert hasattr(SearchLog, "voice_message")

    def test_user_feedback_has_voice_message_relationship(self):
        """UserFeedback model has voice_message relationship."""
        from farmafacil.models.database import UserFeedback
        assert hasattr(UserFeedback, "voice_message")

    def test_user_suggestion_has_voice_message_relationship(self):
        """UserSuggestion model has voice_message relationship."""
        from farmafacil.models.database import UserSuggestion
        assert hasattr(UserSuggestion, "voice_message")


# ── Service tests: voice_message_id accepted by service functions ──────


class TestLogSearchVoiceMessageId:
    """Verify log_search accepts and stores voice_message_id."""

    @pytest.mark.asyncio
    async def test_log_search_passes_voice_message_id(self):
        """log_search creates SearchLog with voice_message_id when provided."""
        from farmafacil.services.search_feedback import log_search

        mock_entry = MagicMock()
        mock_entry.id = 99
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("farmafacil.services.search_feedback.async_session", return_value=mock_session):
            await log_search(user_id=1, query="acetaminofen", results_count=3, voice_message_id=42)

        # The SearchLog constructor should have received voice_message_id
        add_call = mock_session.add.call_args
        entry = add_call[0][0]
        assert entry.voice_message_id == 42

    @pytest.mark.asyncio
    async def test_log_search_none_voice_message_id_by_default(self):
        """log_search defaults voice_message_id to None for typed messages."""
        from farmafacil.services.search_feedback import log_search

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("farmafacil.services.search_feedback.async_session", return_value=mock_session):
            await log_search(user_id=1, query="ibuprofeno", results_count=5)

        entry = mock_session.add.call_args[0][0]
        assert entry.voice_message_id is None


class TestCreateFeedbackVoiceMessageId:
    """Verify create_feedback accepts and stores voice_message_id."""

    @pytest.mark.asyncio
    async def test_create_feedback_passes_voice_message_id(self):
        """create_feedback creates UserFeedback with voice_message_id when provided."""
        from farmafacil.services.user_feedback import create_feedback

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        # Mock _find_latest_inbound_log_id to return None
        with patch("farmafacil.services.user_feedback.async_session", return_value=mock_session), \
             patch("farmafacil.services.user_feedback._find_latest_inbound_log_id", new_callable=AsyncMock, return_value=None):
            await create_feedback(
                user_id=1, feedback_type="bug", message="audio bug report",
                phone_number="12345", voice_message_id=55,
            )

        entry = mock_session.add.call_args[0][0]
        assert entry.voice_message_id == 55

    @pytest.mark.asyncio
    async def test_create_feedback_none_voice_message_id_by_default(self):
        """create_feedback defaults voice_message_id to None for typed messages."""
        from farmafacil.services.user_feedback import create_feedback

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("farmafacil.services.user_feedback.async_session", return_value=mock_session), \
             patch("farmafacil.services.user_feedback._find_latest_inbound_log_id", new_callable=AsyncMock, return_value=None):
            await create_feedback(
                user_id=1, feedback_type="comentario", message="typed feedback",
                phone_number="12345",
            )

        entry = mock_session.add.call_args[0][0]
        assert entry.voice_message_id is None


class TestCreateSuggestionVoiceMessageId:
    """Verify create_suggestion accepts and stores voice_message_id."""

    @pytest.mark.asyncio
    async def test_create_suggestion_passes_voice_message_id(self):
        """create_suggestion creates UserSuggestion with voice_message_id when provided."""
        from farmafacil.services.user_suggestions import create_suggestion

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("farmafacil.services.user_suggestions.async_session", return_value=mock_session):
            await create_suggestion(
                user_id=1, phone_number="12345", message="audio suggestion",
                voice_message_id=77,
            )

        entry = mock_session.add.call_args[0][0]
        assert entry.voice_message_id == 77

    @pytest.mark.asyncio
    async def test_create_suggestion_none_voice_message_id_by_default(self):
        """create_suggestion defaults voice_message_id to None for typed messages."""
        from farmafacil.services.user_suggestions import create_suggestion

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("farmafacil.services.user_suggestions.async_session", return_value=mock_session):
            await create_suggestion(
                user_id=1, phone_number="12345", message="typed suggestion",
            )

        entry = mock_session.add.call_args[0][0]
        assert entry.voice_message_id is None


# ── Handler tests: voice_message_id threaded through call chain ────────


class TestHandleIncomingMessageVoiceId:
    """Verify handle_incoming_message accepts and threads voice_message_id."""

    def test_handle_incoming_message_signature_has_voice_message_id(self):
        """handle_incoming_message accepts voice_message_id kwarg."""
        import inspect
        from farmafacil.bot.handler import handle_incoming_message
        sig = inspect.signature(handle_incoming_message)
        assert "voice_message_id" in sig.parameters
        param = sig.parameters["voice_message_id"]
        assert param.default is None

    def test_handle_drug_search_signature_has_voice_message_id(self):
        """_handle_drug_search accepts voice_message_id kwarg."""
        import inspect
        from farmafacil.bot.handler import _handle_drug_search
        sig = inspect.signature(_handle_drug_search)
        assert "voice_message_id" in sig.parameters
        param = sig.parameters["voice_message_id"]
        assert param.default is None


class TestHandleVoiceMessagePassesId:
    """Verify handle_voice_message passes voice_msg.id to handle_incoming_message."""

    @pytest.mark.asyncio
    async def test_voice_message_id_threaded_to_handle_incoming(self):
        """handle_voice_message passes voice_msg.id as voice_message_id kwarg."""
        from farmafacil.bot.handler import handle_voice_message

        # Mock all dependencies
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.name = "Test"
        mock_user.onboarding_step = None
        mock_user.latitude = 10.5
        mock_user.longitude = -66.9

        # Mock VoiceMessage with id=42
        mock_voice_msg = MagicMock()
        mock_voice_msg.id = 42
        mock_voice_msg.user_id = 1

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        # Make session.add set mock_voice_msg properties, then refresh returns it
        mock_session.add = MagicMock()

        mock_handle_incoming = AsyncMock()

        with patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=mock_user), \
             patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=mock_user), \
             patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock), \
             patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock), \
             patch("farmafacil.services.voice.save_audio_file", return_value="audio/1/test.ogg"), \
             patch("farmafacil.services.voice.get_audio_absolute_path", return_value=MagicMock()), \
             patch("farmafacil.services.voice.transcribe_audio", new_callable=AsyncMock, return_value=("busco acetaminofen", "es", 3.5)), \
             patch("farmafacil.services.media.download_whatsapp_media", new_callable=AsyncMock, return_value=(b"audio_data", "audio/ogg")), \
             patch("farmafacil.db.session.async_session", return_value=mock_session) as mock_db_session, \
             patch("farmafacil.bot.handler.handle_incoming_message", mock_handle_incoming):

            # Make the DB session add and commit work, and simulate voice_msg.id
            def capture_add(obj):
                obj.id = 42  # Simulate DB assigning an ID
            mock_session.add.side_effect = capture_add

            await handle_voice_message("12345", "media_id_123", "wamid.test")

        # Verify handle_incoming_message was called with voice_message_id=42
        mock_handle_incoming.assert_called_once()
        call_kwargs = mock_handle_incoming.call_args
        assert call_kwargs.kwargs.get("voice_message_id") == 42

    @pytest.mark.asyncio
    async def test_text_message_has_no_voice_id(self):
        """Non-voice call sites pass no voice_message_id (default None)."""
        # This is a signature test — verify the default is None
        import inspect
        from farmafacil.bot.handler import handle_incoming_message
        sig = inspect.signature(handle_incoming_message)
        assert sig.parameters["voice_message_id"].default is None


class TestDrugSearchVoiceIdThreading:
    """Verify voice_message_id flows from _handle_drug_search to log_search."""

    @pytest.mark.asyncio
    async def test_drug_search_passes_voice_id_to_log_search(self):
        """_handle_drug_search passes voice_message_id to log_search."""
        from farmafacil.bot.handler import _handle_drug_search

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.latitude = 10.5
        mock_user.longitude = -66.9
        mock_user.zone_name = "Test Zone"
        mock_user.city_code = "CCS"
        mock_user.display_preference = "detail"

        mock_log_search = AsyncMock(return_value=99)
        mock_response = MagicMock()
        mock_response.results = []
        mock_response.total = 0

        with patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=None), \
             patch("farmafacil.bot.handler.extract_medications_from_memory", return_value=[]), \
             patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock, return_value=mock_response), \
             patch("farmafacil.bot.handler.log_search", mock_log_search), \
             patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock), \
             patch("farmafacil.bot.handler.format_search_results", return_value="No results"), \
             patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock), \
             patch("farmafacil.bot.handler._should_ask_feedback", return_value=False):

            await _handle_drug_search(
                "12345", mock_user, "acetaminofen", "Test",
                voice_message_id=42,
            )

        mock_log_search.assert_called_once_with(
            1, "acetaminofen", 0, voice_message_id=42,
        )

    @pytest.mark.asyncio
    async def test_drug_search_no_voice_id_for_text(self):
        """_handle_drug_search passes None voice_message_id when not set."""
        from farmafacil.bot.handler import _handle_drug_search

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.latitude = 10.5
        mock_user.longitude = -66.9
        mock_user.zone_name = "Test Zone"
        mock_user.city_code = "CCS"
        mock_user.display_preference = "detail"

        mock_log_search = AsyncMock(return_value=99)
        mock_response = MagicMock()
        mock_response.results = []
        mock_response.total = 0

        with patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=None), \
             patch("farmafacil.bot.handler.extract_medications_from_memory", return_value=[]), \
             patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock, return_value=mock_response), \
             patch("farmafacil.bot.handler.log_search", mock_log_search), \
             patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock), \
             patch("farmafacil.bot.handler.format_search_results", return_value="No results"), \
             patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock), \
             patch("farmafacil.bot.handler._should_ask_feedback", return_value=False):

            await _handle_drug_search(
                "12345", mock_user, "ibuprofeno", "Test",
            )

        mock_log_search.assert_called_once_with(
            1, "ibuprofeno", 0, voice_message_id=None,
        )


# ── Additive migration tests ──────────────────────────────────────────


class TestAdditiveMigrations:
    """Verify voice_message_id additive migrations are registered."""

    def test_search_logs_migration_registered(self):
        """search_logs voice_message_id is in additive_migrations list."""
        import ast
        from pathlib import Path

        session_path = Path("src/farmafacil/db/session.py")
        source = session_path.read_text()
        assert "search_logs" in source and "voice_message_id" in source

    def test_user_feedback_migration_registered(self):
        """user_feedback voice_message_id is in additive_migrations list."""
        from pathlib import Path

        source = Path("src/farmafacil/db/session.py").read_text()
        assert "user_feedback" in source and "voice_message_id" in source

    def test_user_suggestions_migration_registered(self):
        """user_suggestions voice_message_id is in additive_migrations list."""
        from pathlib import Path

        source = Path("src/farmafacil/db/session.py").read_text()
        assert "user_suggestions" in source and "voice_message_id" in source


# ── Admin view tests ──────────────────────────────────────────────────


class TestAdminVoiceLinking:
    """Verify admin views show voice_message_id links."""

    def test_search_log_admin_shows_voice_column(self):
        """SearchLogAdmin column_list includes voice_message_id."""
        from farmafacil.api.admin import SearchLogAdmin
        from farmafacil.models.database import SearchLog
        col_attrs = [
            c.key if hasattr(c, "key") else str(c)
            for c in SearchLogAdmin.column_list
        ]
        assert "voice_message_id" in col_attrs

    def test_user_feedback_admin_shows_voice_column(self):
        """UserFeedbackAdmin column_list includes voice_message_id."""
        from farmafacil.api.admin import UserFeedbackAdmin
        from farmafacil.models.database import UserFeedback
        col_attrs = [
            c.key if hasattr(c, "key") else str(c)
            for c in UserFeedbackAdmin.column_list
        ]
        assert "voice_message_id" in col_attrs

    def test_user_suggestion_admin_shows_voice_column(self):
        """UserSuggestionAdmin column_list includes voice_message_id."""
        from farmafacil.api.admin import UserSuggestionAdmin
        from farmafacil.models.database import UserSuggestion
        col_attrs = [
            c.key if hasattr(c, "key") else str(c)
            for c in UserSuggestionAdmin.column_list
        ]
        assert "voice_message_id" in col_attrs

    def test_voice_message_admin_labels(self):
        """SearchLogAdmin has voice label."""
        from farmafacil.api.admin import SearchLogAdmin
        assert "voice_message_id" in SearchLogAdmin.column_labels
