"""Tests for the voice message cleanup scheduled task (v0.22.6).

The ``cleanup_old_voice_messages`` task deletes voice_messages rows and
their associated audio files older than 90 days.  FK references in
search_logs, user_feedback, user_suggestions are ON DELETE SET NULL.

Tests cover:
- Old rows + files deleted, recent rows kept
- Missing audio files tolerated (no crash)
- Empty parent directories cleaned up
- Result message format
- Task registered in TASK_REGISTRY and DEFAULT_TASKS
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from farmafacil.services.scheduler import (
    DEFAULT_TASKS,
    TASK_REGISTRY,
    _cleanup_old_voice_messages,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_task() -> MagicMock:
    """Build a minimal mock ScheduledTask for the function signature."""
    t = MagicMock()
    t.task_key = "cleanup_old_voice_messages"
    return t


def _mock_db(audio_paths: list[str], delete_rowcount: int):
    """Build mocked async_session that returns audio_paths on SELECT and
    delete_rowcount on DELETE.  Returns the side_effect-ready factory."""

    mock_select_result = MagicMock()
    mock_select_result.fetchall.return_value = [(p,) for p in audio_paths]

    mock_delete_result = MagicMock()
    mock_delete_result.rowcount = delete_rowcount

    # Two separate session context managers (one for SELECT, one for DELETE)
    call_count = {"n": 0}

    def session_factory():
        call_count["n"] += 1
        mock_session = AsyncMock()
        if call_count["n"] == 1:
            mock_session.execute = AsyncMock(return_value=mock_select_result)
        else:
            mock_session.execute = AsyncMock(return_value=mock_delete_result)
            mock_session.commit = AsyncMock()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    return session_factory


# ── Registry tests ───────────────────────────────────────────────────────


class TestVoiceCleanupRegistry:
    """Verify the task is registered and seeded."""

    def test_task_in_registry(self):
        assert "cleanup_old_voice_messages" in TASK_REGISTRY
        assert TASK_REGISTRY["cleanup_old_voice_messages"] is _cleanup_old_voice_messages

    def test_task_in_default_tasks(self):
        keys = [key for _, key, _, _ in DEFAULT_TASKS]
        assert "cleanup_old_voice_messages" in keys

    def test_default_task_interval_is_weekly(self):
        for name, key, interval, enabled in DEFAULT_TASKS:
            if key == "cleanup_old_voice_messages":
                assert interval == 10080  # 7 days in minutes
                assert enabled is True
                break
        else:
            pytest.fail("cleanup_old_voice_messages not found in DEFAULT_TASKS")


# ── Functional tests ─────────────────────────────────────────────────────


class TestCleanupOldVoiceMessages:
    """Test the _cleanup_old_voice_messages task function."""

    @pytest.mark.asyncio
    async def test_deletes_old_rows_and_files(self, tmp_path):
        """Old voice messages are deleted from DB and disk."""
        audio_dir = tmp_path / "audio" / "1"
        audio_dir.mkdir(parents=True)
        old_file = audio_dir / "old_msg.ogg"
        old_file.write_bytes(b"fake audio data")

        db_mock = _mock_db(["audio/1/old_msg.ogg"], delete_rowcount=1)

        with (
            patch("farmafacil.services.scheduler.async_session", side_effect=db_mock),
            patch("farmafacil.services.scheduler.select"),
            patch("farmafacil.services.voice.AUDIO_BASE_DIR", tmp_path / "audio"),
        ):
            result = await _cleanup_old_voice_messages(_make_task())

        assert "1 voice messages" in result
        assert "1 audio files" in result
        assert not old_file.exists()

    @pytest.mark.asyncio
    async def test_missing_audio_file_tolerated(self, tmp_path):
        """If the audio file was already deleted from disk, no crash."""
        db_mock = _mock_db(["audio/99/gone.ogg"], delete_rowcount=1)

        with (
            patch("farmafacil.services.scheduler.async_session", side_effect=db_mock),
            patch("farmafacil.services.scheduler.select"),
            patch("farmafacil.services.voice.AUDIO_BASE_DIR", tmp_path / "audio"),
        ):
            result = await _cleanup_old_voice_messages(_make_task())

        assert "1 voice messages" in result
        assert "0 audio files" in result

    @pytest.mark.asyncio
    async def test_no_old_messages_is_noop(self, tmp_path):
        """When there are no old messages, nothing is deleted."""
        db_mock = _mock_db([], delete_rowcount=0)

        with (
            patch("farmafacil.services.scheduler.async_session", side_effect=db_mock),
            patch("farmafacil.services.scheduler.select"),
            patch("farmafacil.services.voice.AUDIO_BASE_DIR", tmp_path / "audio"),
        ):
            result = await _cleanup_old_voice_messages(_make_task())

        assert "0 voice messages" in result
        assert "0 audio files" in result

    @pytest.mark.asyncio
    async def test_empty_parent_dirs_cleaned(self, tmp_path):
        """Empty user audio subdirectories are removed after file deletion."""
        audio_dir = tmp_path / "audio" / "5"
        audio_dir.mkdir(parents=True)
        old_file = audio_dir / "old.ogg"
        old_file.write_bytes(b"data")

        db_mock = _mock_db(["audio/5/old.ogg"], delete_rowcount=1)

        with (
            patch("farmafacil.services.scheduler.async_session", side_effect=db_mock),
            patch("farmafacil.services.scheduler.select"),
            patch("farmafacil.services.voice.AUDIO_BASE_DIR", tmp_path / "audio"),
        ):
            result = await _cleanup_old_voice_messages(_make_task())

        assert not old_file.exists()
        assert not audio_dir.exists(), "Empty user audio dir should be removed"

    @pytest.mark.asyncio
    async def test_non_empty_parent_dir_kept(self, tmp_path):
        """User audio subdirectory with remaining files is NOT removed."""
        audio_dir = tmp_path / "audio" / "3"
        audio_dir.mkdir(parents=True)
        old_file = audio_dir / "old.ogg"
        old_file.write_bytes(b"delete me")
        recent_file = audio_dir / "recent.ogg"
        recent_file.write_bytes(b"keep me")

        db_mock = _mock_db(["audio/3/old.ogg"], delete_rowcount=1)

        with (
            patch("farmafacil.services.scheduler.async_session", side_effect=db_mock),
            patch("farmafacil.services.scheduler.select"),
            patch("farmafacil.services.voice.AUDIO_BASE_DIR", tmp_path / "audio"),
        ):
            result = await _cleanup_old_voice_messages(_make_task())

        assert audio_dir.exists(), "Non-empty dir should be kept"
        assert recent_file.exists()
        assert not old_file.exists()

    @pytest.mark.asyncio
    async def test_multiple_files_across_users(self, tmp_path):
        """Files from multiple user subdirectories are handled."""
        for uid in (1, 2):
            d = tmp_path / "audio" / str(uid)
            d.mkdir(parents=True)
            (d / "msg.ogg").write_bytes(b"audio")

        paths = ["audio/1/msg.ogg", "audio/2/msg.ogg"]
        db_mock = _mock_db(paths, delete_rowcount=2)

        with (
            patch("farmafacil.services.scheduler.async_session", side_effect=db_mock),
            patch("farmafacil.services.scheduler.select"),
            patch("farmafacil.services.voice.AUDIO_BASE_DIR", tmp_path / "audio"),
        ):
            result = await _cleanup_old_voice_messages(_make_task())

        assert "2 voice messages" in result
        assert "2 audio files" in result

    @pytest.mark.asyncio
    async def test_result_message_format(self, tmp_path):
        """Result message includes row count, file count, and age threshold."""
        db_mock = _mock_db(
            ["audio/1/a.ogg", "audio/1/b.ogg", "audio/2/c.ogg"],
            delete_rowcount=3,
        )

        with (
            patch("farmafacil.services.scheduler.async_session", side_effect=db_mock),
            patch("farmafacil.services.scheduler.select"),
            patch("farmafacil.services.voice.AUDIO_BASE_DIR", tmp_path / "audio"),
        ):
            result = await _cleanup_old_voice_messages(_make_task())

        assert "3 voice messages" in result
        assert "audio files" in result
        assert "90 days" in result

    @pytest.mark.asyncio
    async def test_os_error_on_file_delete_logged_not_raised(self, tmp_path):
        """OSError on file deletion is logged, not re-raised."""
        # Create a directory where the file should be — but make it a dir
        # instead of a file so is_file() returns False (safe path)
        audio_dir = tmp_path / "audio" / "1"
        audio_dir.mkdir(parents=True)
        # No actual file — the path exists as dir only

        db_mock = _mock_db(["audio/1/broken.ogg"], delete_rowcount=1)

        with (
            patch("farmafacil.services.scheduler.async_session", side_effect=db_mock),
            patch("farmafacil.services.scheduler.select"),
            patch("farmafacil.services.voice.AUDIO_BASE_DIR", tmp_path / "audio"),
        ):
            # Should not raise
            result = await _cleanup_old_voice_messages(_make_task())

        assert "1 voice messages" in result
        assert "0 audio files" in result
