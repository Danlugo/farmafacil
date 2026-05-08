"""Tests for user suggestion collection (/sugerencia command)."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import User, UserSuggestion
from farmafacil.services.user_suggestions import (
    MAX_MESSAGE_LENGTH,
    create_suggestion,
    parse_suggestion_command,
)


def _uphone() -> str:
    """Generate a unique phone number to avoid UNIQUE constraint collisions."""
    return f"+5841sug{uuid.uuid4().hex[:8]}"


class TestParseSuggestionCommand:
    """Test command parsing from raw user messages."""

    def test_sugerencia_with_body(self):
        result = parse_suggestion_command("/sugerencia filtrar por precio")
        assert result == "filtrar por precio"

    def test_bare_command_returns_empty(self):
        result = parse_suggestion_command("/sugerencia")
        assert result == ""

    def test_case_insensitive(self):
        result = parse_suggestion_command("/SUGERENCIA modo oscuro por favor")
        assert result == "modo oscuro por favor"

    def test_leading_whitespace_tolerated(self):
        result = parse_suggestion_command("  /sugerencia  mas filtros  ")
        assert result == "mas filtros"

    def test_non_command_returns_none(self):
        assert parse_suggestion_command("losartan") is None

    def test_mid_message_not_matched(self):
        """Command must be at the start of the message."""
        assert parse_suggestion_command("quiero /sugerencia algo") is None

    def test_no_separator_not_matched(self):
        """/sugerenciaXYZ should not be parsed."""
        assert parse_suggestion_command("/sugerenciaabc") is None

    def test_empty_string(self):
        assert parse_suggestion_command("") is None

    def test_none_input(self):
        assert parse_suggestion_command(None) is None

    def test_whitespace_only(self):
        assert parse_suggestion_command("   ") is None


class TestCreateSuggestion:
    """Test the create_suggestion service function."""

    @pytest.mark.asyncio
    async def test_create_returns_positive_id(self):
        phone = _uphone()
        async with async_session() as session:
            user = User(phone_number=phone, name="TestSug", onboarding_step=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        case_id = await create_suggestion(
            user_id=user_id,
            phone_number=phone,
            message="me gustaria poder filtrar por precio",
        )
        assert isinstance(case_id, int)
        assert case_id > 0

    @pytest.mark.asyncio
    async def test_record_persisted_correctly(self):
        phone = _uphone()
        async with async_session() as session:
            user = User(phone_number=phone, name="TestSug2", onboarding_step=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        case_id = await create_suggestion(
            user_id=user_id,
            phone_number=phone,
            message="agregar modo oscuro",
        )

        async with async_session() as session:
            result = await session.execute(
                select(UserSuggestion).where(UserSuggestion.id == case_id)
            )
            record = result.scalar_one()
            assert record.message == "agregar modo oscuro"
            assert record.user_id == user_id
            assert record.phone_number == phone
            assert record.reviewed is False
            assert record.admin_notes is None

    @pytest.mark.asyncio
    async def test_empty_message_raises(self):
        with pytest.raises(ValueError, match="empty"):
            await create_suggestion(
                user_id=1,
                phone_number=_uphone(),
                message="   ",
            )

    @pytest.mark.asyncio
    async def test_oversize_message_truncated(self):
        phone = _uphone()
        async with async_session() as session:
            user = User(phone_number=phone, name="TestSug3", onboarding_step=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        long_msg = "y" * (MAX_MESSAGE_LENGTH + 500)
        case_id = await create_suggestion(
            user_id=user_id,
            phone_number=phone,
            message=long_msg,
        )

        async with async_session() as session:
            result = await session.execute(
                select(UserSuggestion).where(UserSuggestion.id == case_id)
            )
            record = result.scalar_one()
            assert len(record.message) == MAX_MESSAGE_LENGTH


class TestSuggestionCommandInHandler:
    """Test handler-level integration: /sugerencia command."""

    @pytest.mark.asyncio
    async def test_sugerencia_saves_and_confirms(self):
        """A /sugerencia command saves and sends confirmation with case ID."""

        class MockUser:
            id = 50
            name = "Test"
            phone_number = "+58414sgh01"
            latitude = 10.43
            longitude = -66.86
            zone_name = "La Boyera"
            city_code = "CCS"
            display_preference = "grid"
            response_mode = None
            chat_debug = None
            onboarding_step = None
            last_search_query = None
            last_search_log_id = None

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
            patch(
                "farmafacil.bot.handler.create_suggestion",
                new=AsyncMock(return_value=42),
            ),
        ):
            from farmafacil.bot.handler import handle_incoming_message

            await handle_incoming_message(
                "+58414sgh01", "/sugerencia agregar favoritos"
            )

        all_messages = " ".join(str(call) for call in mock_send.call_args_list)
        assert "42" in all_messages
        assert "Sugerencia" in all_messages

    @pytest.mark.asyncio
    async def test_sugerencia_empty_body_shows_help(self):
        """A bare /sugerencia with no body shows the empty message."""

        class MockUser:
            id = 51
            name = "Test"
            phone_number = "+58414sgh02"
            latitude = 10.43
            longitude = -66.86
            zone_name = "La Boyera"
            city_code = "CCS"
            display_preference = "grid"
            response_mode = None
            chat_debug = None
            onboarding_step = None
            last_search_query = None
            last_search_log_id = None

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
        ):
            from farmafacil.bot.handler import handle_incoming_message

            await handle_incoming_message("+58414sgh02", "/sugerencia")

        all_messages = " ".join(str(call) for call in mock_send.call_args_list)
        assert "sugerencia" in all_messages.lower()
        assert "/sugerencia" in all_messages

    @pytest.mark.asyncio
    async def test_sugerencia_db_error_shows_error_message(self):
        """If create_suggestion fails, user gets error message."""

        class MockUser:
            id = 52
            name = "Test"
            phone_number = "+58414sgh03"
            latitude = 10.43
            longitude = -66.86
            zone_name = "La Boyera"
            city_code = "CCS"
            display_preference = "grid"
            response_mode = None
            chat_debug = None
            onboarding_step = None
            last_search_query = None
            last_search_log_id = None

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
            patch(
                "farmafacil.bot.handler.create_suggestion",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
        ):
            from farmafacil.bot.handler import handle_incoming_message

            await handle_incoming_message(
                "+58414sgh03", "/sugerencia agregar favoritos"
            )

        all_messages = " ".join(str(call) for call in mock_send.call_args_list)
        assert "no pude registrar" in all_messages

    @pytest.mark.asyncio
    async def test_sugerencia_clears_stuck_feedback_state(self):
        """/sugerencia also acts as escape hatch from stuck feedback state."""

        class MockUser:
            id = 53
            name = "Test"
            phone_number = "+58414sgh04"
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
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()),
            patch("farmafacil.bot.handler.set_onboarding_step", new=mock_set_step),
            patch(
                "farmafacil.bot.handler.create_suggestion",
                new=AsyncMock(return_value=99),
            ),
        ):
            from farmafacil.bot.handler import handle_incoming_message

            await handle_incoming_message(
                "+58414sgh04", "/sugerencia poder ver historial"
            )

        assert mock_set_step.await_count == 1
        args, _ = mock_set_step.await_args
        assert args[1] is None


class TestAdminChatSuggestionTools:
    """Test admin chat tools for suggestion management."""

    @pytest.mark.asyncio
    async def test_list_suggestions_returns_string(self):
        """list_suggestions returns a string without crashing."""
        from farmafacil.services.admin_chat import _tool_list_suggestions

        result = await _tool_list_suggestions({})
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_list_suggestions_returns_records(self):
        phone = _uphone()
        async with async_session() as session:
            user = User(phone_number=phone, name="AdminTest", onboarding_step=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            sug = UserSuggestion(
                user_id=user.id,
                phone_number=phone,
                message="agregar notificaciones push",
            )
            session.add(sug)
            await session.commit()
            await session.refresh(sug)
            sug_id = sug.id

        from farmafacil.services.admin_chat import _tool_list_suggestions

        result = await _tool_list_suggestions({"limit": 50})
        assert f"#{sug_id}" in result
        assert "notificaciones" in result

    @pytest.mark.asyncio
    async def test_get_suggestion_found(self):
        phone = _uphone()
        async with async_session() as session:
            user = User(phone_number=phone, name="AdminTest2", onboarding_step=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            sug = UserSuggestion(
                user_id=user.id,
                phone_number=phone,
                message="modo offline",
            )
            session.add(sug)
            await session.commit()
            await session.refresh(sug)
            sug_id = sug.id

        from farmafacil.services.admin_chat import _tool_get_suggestion

        result = await _tool_get_suggestion({"id": sug_id})
        assert f"#{sug_id}" in result
        assert "modo offline" in result
        assert phone in result

    @pytest.mark.asyncio
    async def test_get_suggestion_not_found(self):
        from farmafacil.services.admin_chat import _tool_get_suggestion

        result = await _tool_get_suggestion({"id": 99999})
        assert "no existe" in result

    @pytest.mark.asyncio
    async def test_update_suggestion_reviewed(self):
        phone = _uphone()
        async with async_session() as session:
            user = User(phone_number=phone, name="AdminTest3", onboarding_step=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            sug = UserSuggestion(
                user_id=user.id,
                phone_number=phone,
                message="mejor diseño",
            )
            session.add(sug)
            await session.commit()
            await session.refresh(sug)
            sug_id = sug.id

        from farmafacil.services.admin_chat import _tool_update_suggestion

        result = await _tool_update_suggestion({
            "id": sug_id,
            "reviewed": True,
            "admin_notes": "Buena idea, la priorizamos",
        })
        assert "actualizada" in result

        async with async_session() as session:
            r = await session.execute(
                select(UserSuggestion).where(UserSuggestion.id == sug_id)
            )
            updated = r.scalar_one()
            assert updated.reviewed is True
            assert updated.admin_notes == "Buena idea, la priorizamos"

    @pytest.mark.asyncio
    async def test_update_suggestion_sets_reviewed_at(self):
        """When marking reviewed=True, reviewed_at timestamp is set."""
        phone = _uphone()
        async with async_session() as session:
            user = User(phone_number=phone, name="ReviewAt", onboarding_step=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            sug = UserSuggestion(
                user_id=user.id,
                phone_number=phone,
                message="test reviewed_at",
            )
            session.add(sug)
            await session.commit()
            await session.refresh(sug)
            sug_id = sug.id
            assert sug.reviewed_at is None

        from farmafacil.services.admin_chat import _tool_update_suggestion

        await _tool_update_suggestion({"id": sug_id, "reviewed": True})

        async with async_session() as session:
            r = await session.execute(
                select(UserSuggestion).where(UserSuggestion.id == sug_id)
            )
            updated = r.scalar_one()
            assert updated.reviewed is True
            assert updated.reviewed_at is not None

    @pytest.mark.asyncio
    async def test_update_suggestion_not_found(self):
        from farmafacil.services.admin_chat import _tool_update_suggestion

        result = await _tool_update_suggestion({"id": 99999, "reviewed": True})
        assert "no existe" in result

    @pytest.mark.asyncio
    async def test_update_suggestion_nothing_to_update(self):
        from farmafacil.services.admin_chat import _tool_update_suggestion

        result = await _tool_update_suggestion({"id": 1})
        assert "Nada que actualizar" in result

    @pytest.mark.asyncio
    async def test_list_suggestions_filter_reviewed(self):
        """Filter by reviewed=False returns only unreviewed suggestions."""
        phone = _uphone()
        async with async_session() as session:
            user = User(phone_number=phone, name="AdminTest4", onboarding_step=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            sug_open = UserSuggestion(
                user_id=user.id,
                phone_number=phone,
                message="pendiente filtro test",
            )
            session.add(sug_open)
            await session.commit()
            await session.refresh(sug_open)
            open_id = sug_open.id

        from farmafacil.services.admin_chat import _tool_list_suggestions

        result = await _tool_list_suggestions({"reviewed": False})
        assert f"#{open_id}" in result
        assert "•" in result


class TestSuggestionModel:
    """Test the UserSuggestion ORM model."""

    def test_repr(self):
        sug = UserSuggestion(id=7, message="test", user_id=1, phone_number="+58")
        assert repr(sug) == "#7 [sugerencia]"

    @pytest.mark.asyncio
    async def test_defaults(self):
        phone = _uphone()
        async with async_session() as session:
            user = User(phone_number=phone, name="ModelTest", onboarding_step=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            sug = UserSuggestion(
                user_id=user.id,
                phone_number=phone,
                message="test defaults",
            )
            session.add(sug)
            await session.commit()
            await session.refresh(sug)
            assert sug.reviewed is False
            assert sug.admin_notes is None
            assert sug.created_at is not None


class TestSuggestionInSeed:
    """Verify the suggestion_tools skill is present in the app_admin seed."""

    def test_suggestion_tools_skill_in_admin_role(self):
        from farmafacil.db.seed import DEFAULT_ROLES

        admin_role = next(r for r in DEFAULT_ROLES if r["name"] == "app_admin")
        skill_names = [s["name"] for s in admin_role["skills"]]
        assert "suggestion_tools" in skill_names

    def test_suggestion_tools_content_mentions_tools(self):
        from farmafacil.db.seed import DEFAULT_ROLES

        admin_role = next(r for r in DEFAULT_ROLES if r["name"] == "app_admin")
        skill = next(s for s in admin_role["skills"] if s["name"] == "suggestion_tools")
        assert "list_suggestions" in skill["content"]
        assert "get_suggestion" in skill["content"]
        assert "update_suggestion" in skill["content"]
