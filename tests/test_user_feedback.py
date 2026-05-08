"""Tests for user feedback collection (/bug, /comentario commands)."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import ConversationLog, User, UserFeedback
from farmafacil.services.user_feedback import (
    MAX_MESSAGE_LENGTH,
    create_feedback,
    parse_feedback_command,
)


class TestParseFeedbackCommand:
    """Test command parsing from raw user messages."""

    def test_bug_with_body(self):
        result = parse_feedback_command("/bug no encuentro losartan")
        assert result == ("bug", "no encuentro losartan")

    def test_comentario_with_body(self):
        result = parse_feedback_command("/comentario la app está lenta")
        assert result == ("comentario", "la app está lenta")

    def test_commentario_typo_normalized(self):
        """Common typo /commentario should map to comentario."""
        result = parse_feedback_command("/commentario sugerencia: modo oscuro")
        assert result == ("comentario", "sugerencia: modo oscuro")

    def test_bug_bare_command_returns_empty_body(self):
        result = parse_feedback_command("/bug")
        assert result == ("bug", "")

    def test_comentario_bare_command_returns_empty_body(self):
        result = parse_feedback_command("/comentario")
        assert result == ("comentario", "")

    def test_case_insensitive_prefix(self):
        result = parse_feedback_command("/BUG mayúsculas funcionan")
        assert result == ("bug", "mayúsculas funcionan")

    def test_leading_whitespace_tolerated(self):
        result = parse_feedback_command("  /bug  with spaces  ")
        assert result == ("bug", "with spaces")

    def test_non_command_returns_none(self):
        assert parse_feedback_command("losartan") is None

    def test_mention_not_a_command(self):
        """Command must be at the start — /bug in the middle doesn't count."""
        assert parse_feedback_command("necesito /bug algo") is None

    def test_no_separator_not_matched(self):
        """'/bugabc' should not be parsed as a bug command."""
        assert parse_feedback_command("/bugabc") is None

    def test_empty_string(self):
        assert parse_feedback_command("") is None

    def test_none_like(self):
        assert parse_feedback_command("   ") is None


class TestCreateFeedback:
    """Test the create_feedback service function."""

    @pytest.mark.asyncio
    async def test_create_bug_returns_id(self):
        """create_feedback should return a positive ID."""
        async with async_session() as session:
            user = User(phone_number="+58414fb001", name="Test User", onboarding_step=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        case_id = await create_feedback(
            user_id=user_id,
            feedback_type="bug",
            message="el bot no responde rapido",
            phone_number="+58414fb001",
        )
        assert isinstance(case_id, int)
        assert case_id > 0

    @pytest.mark.asyncio
    async def test_create_comentario_persists_record(self):
        """Comentario is stored with correct type and message."""
        async with async_session() as session:
            user = User(phone_number="+58414fb002", name="Test", onboarding_step=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        case_id = await create_feedback(
            user_id=user_id,
            feedback_type="comentario",
            message="Me gustaría ver precios en USD",
            phone_number="+58414fb002",
        )

        async with async_session() as session:
            result = await session.execute(
                select(UserFeedback).where(UserFeedback.id == case_id)
            )
            record = result.scalar_one()
            assert record.feedback_type == "comentario"
            assert record.message == "Me gustaría ver precios en USD"
            assert record.user_id == user_id
            assert record.reviewed is False

    @pytest.mark.asyncio
    async def test_invalid_type_raises(self):
        """Non-allowed feedback_type must raise ValueError."""
        with pytest.raises(ValueError, match="Invalid feedback_type"):
            await create_feedback(
                user_id=1,
                feedback_type="complaint",
                message="hola",
                phone_number="+58414fb003",
            )

    @pytest.mark.asyncio
    async def test_empty_message_raises(self):
        with pytest.raises(ValueError, match="empty"):
            await create_feedback(
                user_id=1,
                feedback_type="bug",
                message="   ",
                phone_number="+58414fb004",
            )

    @pytest.mark.asyncio
    async def test_oversize_message_truncated(self):
        """Messages longer than MAX_MESSAGE_LENGTH are truncated, not rejected."""
        async with async_session() as session:
            user = User(phone_number="+58414fb005", name="Test", onboarding_step=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        long_msg = "x" * (MAX_MESSAGE_LENGTH + 500)
        case_id = await create_feedback(
            user_id=user_id,
            feedback_type="bug",
            message=long_msg,
            phone_number="+58414fb005",
        )

        async with async_session() as session:
            result = await session.execute(
                select(UserFeedback).where(UserFeedback.id == case_id)
            )
            record = result.scalar_one()
            assert len(record.message) == MAX_MESSAGE_LENGTH

    @pytest.mark.asyncio
    async def test_links_to_latest_inbound_log(self):
        """When a conversation log exists for the phone, it's linked."""
        phone = "+58414fb006"
        async with async_session() as session:
            user = User(phone_number=phone, name="Test", onboarding_step=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

            # Create two inbound logs + one outbound — latest inbound should link
            log_old = ConversationLog(
                phone_number=phone, direction="inbound", message_text="hola",
            )
            session.add(log_old)
            await session.commit()
            await session.refresh(log_old)

            log_out = ConversationLog(
                phone_number=phone, direction="outbound", message_text="hi",
            )
            session.add(log_out)
            await session.commit()

            log_new = ConversationLog(
                phone_number=phone,
                direction="inbound",
                message_text="/bug no me funciona",
            )
            session.add(log_new)
            await session.commit()
            await session.refresh(log_new)
            expected_log_id = log_new.id

        case_id = await create_feedback(
            user_id=user_id,
            feedback_type="bug",
            message="no me funciona",
            phone_number=phone,
        )

        async with async_session() as session:
            result = await session.execute(
                select(UserFeedback).where(UserFeedback.id == case_id)
            )
            record = result.scalar_one()
            assert record.conversation_log_id == expected_log_id

    @pytest.mark.asyncio
    async def test_no_inbound_log_allowed(self):
        """If there's no prior inbound log, conversation_log_id stays NULL."""
        async with async_session() as session:
            user = User(phone_number="+58414fb007", name="Test", onboarding_step=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        case_id = await create_feedback(
            user_id=user_id,
            feedback_type="comentario",
            message="first message ever",
            phone_number="+58414fb007",
        )

        async with async_session() as session:
            result = await session.execute(
                select(UserFeedback).where(UserFeedback.id == case_id)
            )
            record = result.scalar_one()
            assert record.conversation_log_id is None


class TestFeedbackCommandInHandler:
    """Test handler-level integration: /bug command escape hatch."""

    @pytest.mark.asyncio
    async def test_bug_clears_awaiting_feedback_state(self):
        """A /bug command while stuck in awaiting_feedback must clear the state.

        Regression for Item 28 code review: if create_feedback raises, the
        state-clearing must happen BEFORE the DB call so the user is never
        left stuck in awaiting_feedback.
        """

        class MockUser:
            id = 42
            name = "Test"
            phone_number = "+58414fbh01"
            latitude = 10.43
            longitude = -66.86
            zone_name = "La Boyera"
            city_code = "CCS"
            display_preference = "grid"
            response_mode = None
            chat_debug = None
            onboarding_step = "awaiting_feedback"
            last_search_query = None
            last_search_log_id = None

        mock_set_step = AsyncMock()

        with (
            patch(
                "farmafacil.bot.handler.get_or_create_user",
                new=AsyncMock(return_value=MockUser()),
            ),
            patch(
                "farmafacil.bot.handler.validate_user_profile",
                new=AsyncMock(return_value=MockUser()),
            ),
            patch("farmafacil.bot.handler.send_read_receipt", new=AsyncMock()),
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
            patch("farmafacil.bot.handler.set_onboarding_step", new=mock_set_step),
            patch(
                "farmafacil.bot.handler.create_feedback",
                new=AsyncMock(return_value=777),
            ),
        ):
            from farmafacil.bot.handler import handle_incoming_message

            await handle_incoming_message(
                "+58414fbh01", "/bug el bot se quedó trabado"
            )

        # State must have been cleared exactly once with None
        assert mock_set_step.await_count == 1
        args, kwargs = mock_set_step.await_args
        assert args[1] is None

        # Confirmation message with case ID must have been sent
        all_messages = [str(call) for call in mock_send.call_args_list]
        joined = " ".join(all_messages)
        assert "777" in joined
        assert "Caso" in joined

    @pytest.mark.asyncio
    async def test_bug_clears_state_even_when_create_fails(self):
        """If create_feedback raises, state is still cleared (escape hatch)."""

        class MockUser:
            id = 43
            name = "Test"
            phone_number = "+58414fbh02"
            latitude = 10.43
            longitude = -66.86
            zone_name = "La Boyera"
            city_code = "CCS"
            display_preference = "grid"
            response_mode = None
            chat_debug = None
            onboarding_step = "awaiting_feedback"
            last_search_query = None
            last_search_log_id = None

        mock_set_step = AsyncMock()

        with (
            patch(
                "farmafacil.bot.handler.get_or_create_user",
                new=AsyncMock(return_value=MockUser()),
            ),
            patch(
                "farmafacil.bot.handler.validate_user_profile",
                new=AsyncMock(return_value=MockUser()),
            ),
            patch("farmafacil.bot.handler.send_read_receipt", new=AsyncMock()),
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
            patch("farmafacil.bot.handler.set_onboarding_step", new=mock_set_step),
            patch(
                "farmafacil.bot.handler.create_feedback",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
        ):
            from farmafacil.bot.handler import handle_incoming_message

            await handle_incoming_message(
                "+58414fbh02", "/bug algo no funciona"
            )

        # State was cleared BEFORE the failing create_feedback call
        assert mock_set_step.await_count == 1
        args, kwargs = mock_set_step.await_args
        assert args[1] is None

        # User got the error message (not the success message)
        all_messages = " ".join(str(call) for call in mock_send.call_args_list)
        assert "no pude registrar" in all_messages
