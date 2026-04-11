"""Tests for the category quick-reply menu (Item 29, v0.13.2).

Covers:
- Shape of the WhatsApp interactive list payload sent by
  ``_send_category_list`` / ``send_interactive_list``.
- Hybrid-mode greeting branch — with the kill switch on vs. off, and
  still-onboarding users bypassing the menu.
- ``handle_list_reply`` — valid category pick, unknown id, state update.
- ``handle_incoming_message`` awaiting_category_search branch — freeform
  product -> drug search, empty query + cancel words, fail-safe state
  clearing before dispatch.
- Kill-switch integration: flipping ``category_menu_enabled`` to
  ``"false"`` via the real settings service falls back to the legacy
  MSG_RETURNING text.

All WhatsApp / LLM / scraper collaborators are patched — no network I/O.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, select, update

from farmafacil.bot import handler
from farmafacil.bot.handler import (
    CATEGORIES,
    _CATEGORY_BY_ID,
    handle_incoming_message,
    handle_list_reply,
)
from farmafacil.db.session import async_session
from farmafacil.models.database import AppSetting, User
from farmafacil.services.intent import Intent
from farmafacil.services.users import get_or_create_user

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_PHONES = {f"5491999001{i:03d}" for i in range(20)}


@pytest.fixture(autouse=True)
async def _cleanup_category_test_users():
    """Wipe category-test phones before and after each test."""
    async with async_session() as session:
        await session.execute(
            delete(User).where(User.phone_number.in_(TEST_PHONES))
        )
        await session.commit()
    yield
    async with async_session() as session:
        await session.execute(
            delete(User).where(User.phone_number.in_(TEST_PHONES))
        )
        await session.commit()


async def _fetch_user(phone: str) -> User:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone)
        )
        return result.scalar_one()


async def _seed_user(
    phone: str,
    *,
    name: str | None = None,
    step: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    zone_name: str | None = None,
    city_code: str | None = None,
    display_preference: str = "grid",
    awaiting_category_search: str | None = None,
) -> None:
    """Create + mutate a user in one session (avoids detached-object bug)."""
    await get_or_create_user(phone)
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone)
        )
        row = result.scalar_one()
        row.name = name
        row.onboarding_step = step
        row.latitude = latitude
        row.longitude = longitude
        row.zone_name = zone_name
        row.city_code = city_code
        if display_preference is not None:
            row.display_preference = display_preference
        row.awaiting_category_search = awaiting_category_search
        await session.commit()


def _onboarded_defaults() -> dict:
    return {
        "name": "Maria",
        "latitude": 10.48,
        "longitude": -66.87,
        "zone_name": "La Boyera",
        "city_code": "CCS",
    }


# ---------------------------------------------------------------------------
# Category list payload shape
# ---------------------------------------------------------------------------


class TestCategoryListPayload:
    """The rendered interactive list message must be well-formed."""

    def test_categories_constant_has_5_rows(self):
        assert len(CATEGORIES) == 5
        # All IDs unique, all titles non-empty, all titles <= 24 chars
        # (WhatsApp hard cap on row titles).
        ids = [row_id for row_id, _ in CATEGORIES]
        assert len(set(ids)) == 5
        for row_id, title in CATEGORIES:
            assert row_id.startswith("cat_")
            assert title
            assert len(title) <= 24
        assert _CATEGORY_BY_ID["cat_medicamentos"] == "Medicamentos"

    @pytest.mark.asyncio
    async def test_send_category_list_payload_shape(self):
        """``_send_category_list`` builds a valid WhatsApp list payload."""
        phone = "5491999001000"
        with patch.object(
            handler, "send_interactive_list", new=AsyncMock(),
        ) as mock_list:
            await handler._send_category_list(phone, "Maria")

        mock_list.assert_awaited_once()
        kwargs = mock_list.await_args.kwargs
        assert kwargs["to"] == phone
        assert "Maria" in kwargs["body"]
        assert kwargs["button"]
        rows = kwargs["rows"]
        assert len(rows) == 5
        assert rows[0]["id"] == "cat_medicamentos"
        assert rows[0]["title"] == "Medicamentos"
        # Every row must have id + title
        for row in rows:
            assert "id" in row and "title" in row


# ---------------------------------------------------------------------------
# Greeting routes to category list (hybrid mode)
# ---------------------------------------------------------------------------


class TestGreetingRoutesCategoryList:
    """Hybrid-mode greeting branch obeys the kill switch."""

    @pytest.mark.asyncio
    async def test_onboarded_greeting_with_setting_on_sends_list(self):
        """Fully onboarded user + greeting intent + setting=true → list, not text."""
        phone = "5491999001001"
        await _seed_user(phone, **_onboarded_defaults())
        intent = Intent(action="greeting")

        async def fake_get_setting(key: str) -> str:
            return {
                "response_mode": "hybrid",
                "chat_debug": "disabled",
                "category_menu_enabled": "true",
            }.get(key, "")

        with patch.object(
            handler, "get_setting", new=AsyncMock(side_effect=fake_get_setting),
        ), patch.object(
            handler, "resolve_chat_debug", return_value=False,
        ), patch.object(
            handler, "_get_keyword_cache", new=AsyncMock(return_value={}),
        ), patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch.object(
            handler, "increment_token_usage", new=AsyncMock(),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_text, patch.object(
            handler, "send_interactive_list", new=AsyncMock(),
        ) as mock_list:
            await handle_incoming_message(phone, "hola")

        mock_list.assert_awaited_once()
        # Legacy MSG_RETURNING text NOT sent
        mock_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_onboarded_greeting_with_setting_off_sends_legacy_text(self):
        """Setting=false falls back to the pre-v0.13.2 MSG_RETURNING path."""
        phone = "5491999001002"
        await _seed_user(phone, **_onboarded_defaults())
        intent = Intent(action="greeting")

        async def fake_get_setting(key: str) -> str:
            return {
                "response_mode": "hybrid",
                "chat_debug": "disabled",
                "category_menu_enabled": "false",
            }.get(key, "")

        with patch.object(
            handler, "get_setting", new=AsyncMock(side_effect=fake_get_setting),
        ), patch.object(
            handler, "resolve_chat_debug", return_value=False,
        ), patch.object(
            handler, "_get_keyword_cache", new=AsyncMock(return_value={}),
        ), patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch.object(
            handler, "increment_token_usage", new=AsyncMock(),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_text, patch.object(
            handler, "send_interactive_list", new=AsyncMock(),
        ) as mock_list:
            await handle_incoming_message(phone, "hola")

        mock_list.assert_not_awaited()
        mock_text.assert_awaited_once()
        sent = mock_text.await_args.args[1]
        assert "Maria" in sent
        assert "La Boyera" in sent

    @pytest.mark.asyncio
    async def test_onboarding_user_greeting_bypasses_menu(self):
        """Users still in onboarding never see the category menu — the
        rigid onboarding branch takes precedence. The explicit test here
        is for a ``welcome`` step user whose first message is a greeting.
        """
        phone = "5491999001003"
        await _seed_user(phone, step="welcome")

        with patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_text, patch.object(
            handler, "send_interactive_list", new=AsyncMock(),
        ) as mock_list:
            await handle_incoming_message(phone, "hola")

        mock_list.assert_not_awaited()
        mock_text.assert_awaited_once()
        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step == "awaiting_name"


# ---------------------------------------------------------------------------
# handle_list_reply
# ---------------------------------------------------------------------------


class TestHandleListReply:
    """``handle_list_reply`` stashes category + sends a canned prompt."""

    @pytest.mark.asyncio
    async def test_valid_category_pick_stashes_and_prompts(self):
        phone = "5491999001004"
        await _seed_user(phone, **_onboarded_defaults())

        with patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_text:
            await handle_list_reply(phone, "cat_medicamentos")

        refreshed = await _fetch_user(phone)
        assert refreshed.awaiting_category_search == "Medicamentos"
        # Onboarding step should NOT be touched
        assert refreshed.onboarding_step is None

        mock_text.assert_awaited_once()
        prompt = mock_text.await_args.args[1]
        assert "Medicamentos" in prompt
        assert "producto" in prompt.lower()

    @pytest.mark.asyncio
    async def test_unknown_reply_id_logs_and_noops(self):
        phone = "5491999001005"
        await _seed_user(phone, **_onboarded_defaults())

        with patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_text:
            await handle_list_reply(phone, "cat_garbage_unknown")

        mock_text.assert_not_awaited()
        refreshed = await _fetch_user(phone)
        # State untouched
        assert refreshed.awaiting_category_search is None

    @pytest.mark.asyncio
    async def test_list_reply_replacing_existing_category_overwrites(self):
        """Tapping a second category after the first replaces the stash."""
        phone = "5491999001006"
        await _seed_user(
            phone, **_onboarded_defaults(),
            awaiting_category_search="Medicamentos",
        )

        with patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ):
            await handle_list_reply(phone, "cat_belleza")

        refreshed = await _fetch_user(phone)
        assert refreshed.awaiting_category_search == "Belleza"


# ---------------------------------------------------------------------------
# awaiting_category_search freeform branch
# ---------------------------------------------------------------------------


class TestAwaitingCategorySearchFreeform:
    """Next message after a category pick routes through drug search."""

    @pytest.mark.asyncio
    async def test_freeform_dispatches_drug_search_and_clears_state(self):
        """'aspirina' after picking Medicamentos → drug search with
        state cleared BEFORE dispatch (fail-safe pattern)."""
        phone = "5491999001007"
        await _seed_user(
            phone, **_onboarded_defaults(),
            awaiting_category_search="Medicamentos",
        )

        # Spy on state-clearing: capture the column value at the moment
        # _handle_drug_search is called.
        captured_state: dict = {}

        async def capture_state(*args, **kwargs):
            row = await _fetch_user(phone)
            captured_state["awaiting"] = row.awaiting_category_search

        with patch.object(
            handler, "get_setting", new=AsyncMock(return_value="disabled"),
        ), patch.object(
            handler, "resolve_chat_debug", return_value=False,
        ), patch.object(
            handler, "_handle_drug_search",
            new=AsyncMock(side_effect=capture_state),
        ) as mock_search, patch.object(
            handler, "_update_memory_safe", new=AsyncMock(),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "aspirina")

        mock_search.assert_awaited_once()
        args = mock_search.await_args.args
        assert args[0] == phone  # sender
        assert args[2] == "aspirina"  # query (raw, not merged)

        # State must have been cleared BEFORE _handle_drug_search fired
        assert captured_state["awaiting"] is None

        # And still cleared afterward
        refreshed = await _fetch_user(phone)
        assert refreshed.awaiting_category_search is None

    @pytest.mark.asyncio
    async def test_cancel_word_clears_state_without_searching(self):
        phone = "5491999001008"
        await _seed_user(
            phone, **_onboarded_defaults(),
            awaiting_category_search="Belleza",
        )

        with patch.object(
            handler, "_handle_drug_search", new=AsyncMock(),
        ) as mock_search, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_text:
            await handle_incoming_message(phone, "cancelar")

        mock_search.assert_not_awaited()
        mock_text.assert_awaited_once()
        sent = mock_text.await_args.args[1]
        assert "cancel" in sent.lower()

        refreshed = await _fetch_user(phone)
        assert refreshed.awaiting_category_search is None

    @pytest.mark.asyncio
    async def test_bug_command_escapes_category_state(self):
        """/bug during awaiting_category_search still works as an escape
        hatch and reports the case — the feedback command branch runs
        BEFORE the category branch by design."""
        phone = "5491999001010"
        await _seed_user(
            phone, **_onboarded_defaults(),
            awaiting_category_search="Medicamentos",
        )

        with patch.object(
            handler, "create_feedback", new=AsyncMock(return_value=42),
        ), patch.object(
            handler, "_handle_drug_search", new=AsyncMock(),
        ) as mock_search, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_text:
            await handle_incoming_message(
                phone, "/bug la categoria no funciona"
            )

        mock_search.assert_not_awaited()
        mock_text.assert_awaited_once()
        sent = mock_text.await_args.args[1]
        assert "Caso" in sent or "#42" in sent


# ---------------------------------------------------------------------------
# Kill switch integration against the real settings service
# ---------------------------------------------------------------------------


class TestKillSwitchIntegration:
    """Flipping ``category_menu_enabled`` via the DB disables the menu."""

    @pytest.mark.asyncio
    async def test_setting_false_disables_menu(self):
        """With the real (un-mocked) ``get_setting`` returning ``"false"``,
        the greeting branch falls through to the legacy MSG_RETURNING text.
        """
        phone = "5491999001011"
        await _seed_user(phone, **_onboarded_defaults())
        intent = Intent(action="greeting")

        # Write the kill switch directly to the DB
        async with async_session() as session:
            existing = await session.execute(
                select(AppSetting).where(
                    AppSetting.key == "category_menu_enabled"
                )
            )
            row = existing.scalar_one_or_none()
            if row is None:
                session.add(
                    AppSetting(
                        key="category_menu_enabled",
                        value="false",
                        description="test override",
                    )
                )
            else:
                await session.execute(
                    update(AppSetting)
                    .where(AppSetting.key == "category_menu_enabled")
                    .values(value="false")
                )
            await session.commit()

        try:
            with patch.object(
                handler, "resolve_chat_debug", return_value=False,
            ), patch.object(
                handler, "_get_keyword_cache", new=AsyncMock(return_value={}),
            ), patch.object(
                handler, "classify_intent", new=AsyncMock(return_value=intent),
            ), patch.object(
                handler, "increment_token_usage", new=AsyncMock(),
            ), patch.object(
                handler, "send_text_message", new=AsyncMock(),
            ) as mock_text, patch.object(
                handler, "send_interactive_list", new=AsyncMock(),
            ) as mock_list:
                await handle_incoming_message(phone, "hola")

            mock_list.assert_not_awaited()
            mock_text.assert_awaited_once()
            sent = mock_text.await_args.args[1]
            assert "Maria" in sent
        finally:
            # Restore the default so other tests run with the menu on
            async with async_session() as session:
                await session.execute(
                    update(AppSetting)
                    .where(AppSetting.key == "category_menu_enabled")
                    .values(value="true")
                )
                await session.commit()
