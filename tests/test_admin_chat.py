"""Tests for the AI Chat Admin role (Item 35, v0.14.0).

Covers:
- Admin mode gating (chat_admin flag, /admin toggle, /admin off phrases)
- /models and /model <alias> commands
- admin_chat tool registry (report_issue, set_user_setting whitelist,
  set_app_setting, get_default_model, counts, read_code path guards)
- build_tools_manifest + parse_tool_args + execute_tool dispatch
- run_admin_turn loop: FINAL path, TOOL_CALL path, step budget, error paths
- Conversation logging classification ("admin_out")

NO tests hit the real Anthropic API — run_admin_turn is patched to return
a scripted reply. NO tests hit the real search pipeline. All DB access
hits the local SQLite DB created by conftest's init_db fixture.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from farmafacil.bot.handler import (
    MSG_ADMIN_DENIED,
    MSG_ADMIN_NOT_ACTIVE,
    MSG_ADMIN_OFF,
    MSG_ADMIN_WELCOME,
    _handle_admin_off,
    _handle_admin_toggle,
    _handle_admin_turn,
    _handle_model_commands,
    handle_incoming_message,
)
from farmafacil.db.seed import seed_ai_roles
from farmafacil.db.session import async_session
from farmafacil.models.database import (
    AppSetting,
    ConversationLog,
    User,
    UserFeedback,
)
from farmafacil.services.admin_chat import (
    PROJECT_ROOT,
    TOOLS,
    _is_allowed_path,
    build_tools_manifest,
    execute_tool,
    parse_tool_args,
)
from farmafacil.services.ai_responder import (
    AdminTurnResult,
    _parse_admin_action,
    run_admin_turn,
)
from farmafacil.services.users import (
    get_or_create_user,
    is_chat_admin,
    set_admin_mode,
)
from sqlalchemy import delete, select, update


# Dedicated phone block for admin tests so we don't collide with the
# existing test phones (5491999000001-021 used by test_handler, 001000-019
# by test_category_menu). Format: 5491998AD####.
ADMIN_PHONE_BASE = "5491998AD"


def _phone(n: int) -> str:
    return f"{ADMIN_PHONE_BASE}{n:04d}"


@pytest.fixture(autouse=True)
async def _cleanup_admin_phones():
    """Nuke admin test rows after each test to keep the test DB clean."""
    yield
    async with async_session() as session:
        # Delete dependent tables first (feedback, conv logs) then users
        phones = await session.execute(
            select(User.phone_number, User.id)
            .where(User.phone_number.like(f"{ADMIN_PHONE_BASE}%"))
        )
        rows = phones.all()
        user_ids = [r.id for r in rows]
        phone_set = [r.phone_number for r in rows]
        if user_ids:
            await session.execute(
                delete(UserFeedback).where(UserFeedback.user_id.in_(user_ids))
            )
        if phone_set:
            await session.execute(
                delete(ConversationLog)
                .where(ConversationLog.phone_number.in_(phone_set))
            )
            await session.execute(
                delete(User).where(User.phone_number.in_(phone_set))
            )
        await session.commit()


async def _make_admin_user(phone: str, chat_admin: bool = True) -> User:
    """Helper — create a user and flip the chat_admin flag via raw UPDATE
    (chat_admin is UI-only, there's no service helper by design)."""
    user = await get_or_create_user(phone)
    if chat_admin:
        async with async_session() as session:
            await session.execute(
                update(User).where(User.id == user.id).values(chat_admin=True)
            )
            await session.commit()
        # Re-read so callers see the flag
        user = await get_or_create_user(phone)
    return user


# ─────────────────────────────────────────────────────────────────────────
# Admin mode gating
# ─────────────────────────────────────────────────────────────────────────


class TestAdminModeGating:
    """The /admin command must respect users.chat_admin (UI-only flag)."""

    @pytest.mark.asyncio
    async def test_admin_denied_when_chat_admin_false(self):
        phone = _phone(1)
        user = await _make_admin_user(phone, chat_admin=False)
        mock_send = AsyncMock()
        with patch("farmafacil.bot.handler.send_text_message", new=mock_send):
            await _handle_admin_toggle(phone, user)
        mock_send.assert_awaited_once_with(phone, MSG_ADMIN_DENIED)
        # DB flag still off
        refreshed = await get_or_create_user(phone)
        assert refreshed.admin_mode_active is False

    @pytest.mark.asyncio
    async def test_admin_toggle_on_when_chat_admin_true(self):
        phone = _phone(2)
        user = await _make_admin_user(phone, chat_admin=True)
        mock_send = AsyncMock()
        with patch("farmafacil.bot.handler.send_text_message", new=mock_send):
            await _handle_admin_toggle(phone, user)
        mock_send.assert_awaited_once_with(phone, MSG_ADMIN_WELCOME)
        refreshed = await get_or_create_user(phone)
        assert refreshed.admin_mode_active is True

    @pytest.mark.asyncio
    async def test_admin_toggle_off_when_already_on(self):
        phone = _phone(3)
        await _make_admin_user(phone, chat_admin=True)
        await set_admin_mode(phone, True)
        user = await get_or_create_user(phone)
        assert user.admin_mode_active is True
        mock_send = AsyncMock()
        with patch("farmafacil.bot.handler.send_text_message", new=mock_send):
            await _handle_admin_toggle(phone, user)
        mock_send.assert_awaited_once_with(phone, MSG_ADMIN_OFF)
        refreshed = await get_or_create_user(phone)
        assert refreshed.admin_mode_active is False

    @pytest.mark.asyncio
    async def test_admin_welcome_lists_commands_and_samples(self):
        """MSG_ADMIN_WELCOME must include every slash command + samples
        so admins never have to guess what's available."""
        for cmd in ["/admin", "/models", "/model", "/stats", "/bug"]:
            assert cmd in MSG_ADMIN_WELCOME
        # Spot-check some sample operations
        assert "feedbacks" in MSG_ADMIN_WELCOME.lower()
        assert "farmacia" in MSG_ADMIN_WELCOME.lower()
        assert "default_model" in MSG_ADMIN_WELCOME

    @pytest.mark.asyncio
    async def test_admin_off_phrase_turns_off(self):
        phone = _phone(4)
        await _make_admin_user(phone, chat_admin=True)
        await set_admin_mode(phone, True)
        user = await get_or_create_user(phone)
        mock_send = AsyncMock()
        with patch("farmafacil.bot.handler.send_text_message", new=mock_send):
            await _handle_admin_off(phone, user)
        mock_send.assert_awaited_once_with(phone, MSG_ADMIN_OFF)
        refreshed = await get_or_create_user(phone)
        assert refreshed.admin_mode_active is False

    @pytest.mark.asyncio
    async def test_admin_off_phrase_noop_when_not_active(self):
        phone = _phone(5)
        user = await _make_admin_user(phone, chat_admin=True)
        assert user.admin_mode_active is False
        mock_send = AsyncMock()
        with patch("farmafacil.bot.handler.send_text_message", new=mock_send):
            await _handle_admin_off(phone, user)
        mock_send.assert_awaited_once_with(phone, MSG_ADMIN_NOT_ACTIVE)

    @pytest.mark.asyncio
    async def test_is_chat_admin_helper(self):
        phone_a = _phone(6)
        phone_b = _phone(7)
        await _make_admin_user(phone_a, chat_admin=True)
        await _make_admin_user(phone_b, chat_admin=False)
        assert await is_chat_admin(phone_a) is True
        assert await is_chat_admin(phone_b) is False
        assert await is_chat_admin("5491000000000") is False  # unknown


# ─────────────────────────────────────────────────────────────────────────
# /models and /model commands
# ─────────────────────────────────────────────────────────────────────────


class TestModelCommands:
    """Test /models and /model <alias> commands."""

    @pytest.mark.asyncio
    async def test_models_shows_current_and_aliases(self):
        phone = _phone(8)
        captured: list[str] = []

        async def fake_send(to, text):
            captured.append(text)

        with (
            patch("farmafacil.bot.handler.send_text_message", side_effect=fake_send),
            patch("farmafacil.bot.handler.log_outbound_conv", new=AsyncMock()),
        ):
            handled = await _handle_model_commands(phone, "/models")
        assert handled is True
        assert len(captured) == 1
        body = captured[0]
        assert "Default actual" in body
        assert "haiku" in body and "sonnet" in body and "opus" in body

    @pytest.mark.asyncio
    async def test_model_alias_valid_updates_setting(self):
        phone = _phone(9)
        captured: list[str] = []

        async def fake_send(to, text):
            captured.append(text)

        with (
            patch("farmafacil.bot.handler.send_text_message", side_effect=fake_send),
            patch("farmafacil.bot.handler.log_outbound_conv", new=AsyncMock()),
        ):
            handled = await _handle_model_commands(phone, "/model sonnet")
        assert handled is True
        assert "sonnet" in captured[0]
        # Assert the DB setting actually changed
        from farmafacil.services.settings import get_default_model
        assert await get_default_model() == "sonnet"
        # Cleanup: put it back
        from farmafacil.services.settings import set_default_model
        await set_default_model("haiku")

    @pytest.mark.asyncio
    async def test_model_alias_invalid_shows_error(self):
        phone = _phone(10)
        captured: list[str] = []

        async def fake_send(to, text):
            captured.append(text)

        with (
            patch("farmafacil.bot.handler.send_text_message", side_effect=fake_send),
            patch("farmafacil.bot.handler.log_outbound_conv", new=AsyncMock()),
        ):
            handled = await _handle_model_commands(phone, "/model gpt5")
        assert handled is True
        assert captured[0].startswith("\u274c")

    @pytest.mark.asyncio
    async def test_non_model_command_returns_false(self):
        phone = _phone(11)
        with (
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()),
            patch("farmafacil.bot.handler.log_outbound_conv", new=AsyncMock()),
        ):
            handled = await _handle_model_commands(phone, "hola que tal")
        assert handled is False


# ─────────────────────────────────────────────────────────────────────────
# Tool registry: manifest + parse_args + dispatch
# ─────────────────────────────────────────────────────────────────────────


class TestToolRegistry:

    def test_manifest_lists_every_registered_tool(self):
        manifest = build_tools_manifest()
        assert "HERRAMIENTAS DISPONIBLES" in manifest
        for name in TOOLS:
            assert name in manifest, f"Tool '{name}' missing from manifest"

    def test_manifest_includes_report_issue(self):
        """report_issue is the dev-backlog handoff — must be visible."""
        assert "report_issue" in build_tools_manifest()

    def test_parse_tool_args_valid_json(self):
        assert parse_tool_args('{"id": 42}') == {"id": 42}

    def test_parse_tool_args_empty(self):
        assert parse_tool_args("") == {}
        assert parse_tool_args("   ") == {}

    def test_parse_tool_args_garbage_returns_empty_dict(self):
        assert parse_tool_args("not json") == {}
        assert parse_tool_args("[1,2,3]") == {}

    @pytest.mark.asyncio
    async def test_execute_tool_unknown(self):
        result = await execute_tool("nope", {}, admin_user_id=1)
        assert "desconocida" in result.lower()

    @pytest.mark.asyncio
    async def test_execute_tool_injects_admin_user_id(self):
        """execute_tool must add _admin_user_id to args so report_issue
        can attribute the row — and the sentinel must NOT leak when
        admin_user_id is None."""
        captured: dict = {}

        async def fake_tool(args):
            captured.update(args)
            return "ok"

        with patch.dict(TOOLS, {"_test": ("test", fake_tool)}):
            await execute_tool("_test", {"x": 1}, admin_user_id=42)
        assert captured == {"x": 1, "_admin_user_id": 42}

        captured.clear()
        with patch.dict(TOOLS, {"_test": ("test", fake_tool)}):
            await execute_tool("_test", {"x": 1}, admin_user_id=None)
        assert captured == {"x": 1}

    @pytest.mark.asyncio
    async def test_execute_tool_strips_llm_spoofed_admin_user_id(self):
        """An LLM-supplied `_admin_user_id` in ARGS must NEVER override the
        caller-injected value (security: audit trail spoofing prevention)."""
        captured: dict = {}

        async def fake_tool(args):
            captured.update(args)
            return "ok"

        # LLM puts 9999 in ARGS trying to impersonate another admin.
        # Caller (handler) passes admin_user_id=42. Result must be 42.
        with patch.dict(TOOLS, {"_test": ("test", fake_tool)}):
            await execute_tool(
                "_test",
                {"x": 1, "_admin_user_id": 9999},
                admin_user_id=42,
            )
        assert captured["_admin_user_id"] == 42
        assert captured["x"] == 1

        # Also: when caller passes None, the LLM's value must still be stripped
        # (not leaked as the sentinel — stripping is absolute).
        captured.clear()
        with patch.dict(TOOLS, {"_test": ("test", fake_tool)}):
            await execute_tool(
                "_test",
                {"x": 1, "_admin_user_id": 9999},
                admin_user_id=None,
            )
        assert "_admin_user_id" not in captured
        assert captured == {"x": 1}

    @pytest.mark.asyncio
    async def test_execute_tool_catches_exception(self):
        async def boom(_):
            raise RuntimeError("kaboom")

        with patch.dict(TOOLS, {"_boom": ("test", boom)}):
            result = await execute_tool("_boom", {}, admin_user_id=1)
        assert "kaboom" in result


# ─────────────────────────────────────────────────────────────────────────
# report_issue tool — feedback backlog handoff
# ─────────────────────────────────────────────────────────────────────────


class TestReportIssueTool:
    """report_issue must write UserFeedback rows with admin_ prefix so
    /farmafacil-review can filter on feedback_type LIKE 'admin_%'."""

    @pytest.mark.asyncio
    async def test_bug_writes_admin_bug_row(self):
        phone = _phone(12)
        user = await _make_admin_user(phone, chat_admin=True)
        result = await execute_tool(
            "report_issue",
            {"type": "bug", "message": "Farmatodo Algolia is throwing 500s"},
            admin_user_id=user.id,
        )
        assert "admin_bug" in result
        async with async_session() as session:
            row = (await session.execute(
                select(UserFeedback)
                .where(UserFeedback.user_id == user.id)
                .order_by(UserFeedback.id.desc())
                .limit(1)
            )).scalar_one()
        assert row.feedback_type == "admin_bug"
        assert "Farmatodo" in row.message

    @pytest.mark.asyncio
    async def test_idea_writes_admin_idea_row(self):
        phone = _phone(13)
        user = await _make_admin_user(phone, chat_admin=True)
        await execute_tool(
            "report_issue",
            {"type": "idea", "message": "Add Mercadolibre as 4th scraper"},
            admin_user_id=user.id,
        )
        async with async_session() as session:
            row = (await session.execute(
                select(UserFeedback).where(UserFeedback.user_id == user.id)
            )).scalar_one()
        assert row.feedback_type == "admin_idea"

    @pytest.mark.asyncio
    async def test_unknown_type_falls_back_to_issue(self):
        phone = _phone(14)
        user = await _make_admin_user(phone, chat_admin=True)
        await execute_tool(
            "report_issue",
            {"type": "banana", "message": "some issue"},
            admin_user_id=user.id,
        )
        async with async_session() as session:
            row = (await session.execute(
                select(UserFeedback).where(UserFeedback.user_id == user.id)
            )).scalar_one()
        assert row.feedback_type == "admin_issue"

    @pytest.mark.asyncio
    async def test_empty_message_rejected(self):
        phone = _phone(15)
        user = await _make_admin_user(phone, chat_admin=True)
        result = await execute_tool(
            "report_issue",
            {"type": "bug", "message": "   "},
            admin_user_id=user.id,
        )
        assert "Falta mensaje" in result

    @pytest.mark.asyncio
    async def test_without_admin_context_rejected(self):
        result = await execute_tool(
            "report_issue",
            {"type": "bug", "message": "hi"},
            admin_user_id=None,
        )
        assert "contexto de admin" in result


# ─────────────────────────────────────────────────────────────────────────
# set_user_setting whitelist — SECURITY BOUNDARY
# ─────────────────────────────────────────────────────────────────────────


class TestUserSettableFieldsWhitelist:
    """chat_admin MUST NOT be editable from chat — security requirement."""

    @pytest.mark.asyncio
    async def test_chat_admin_field_rejected(self):
        phone = _phone(16)
        user = await _make_admin_user(phone, chat_admin=False)
        result = await execute_tool(
            "set_user_setting",
            {"user_ref": user.id, "field": "chat_admin", "value": True},
            admin_user_id=user.id,
        )
        assert "no permitido" in result.lower()
        refreshed = await get_or_create_user(phone)
        assert refreshed.chat_admin is False  # STILL false

    @pytest.mark.asyncio
    async def test_tokens_field_rejected(self):
        phone = _phone(17)
        user = await _make_admin_user(phone, chat_admin=True)
        result = await execute_tool(
            "set_user_setting",
            {"user_ref": user.id, "field": "total_tokens_in", "value": 999},
            admin_user_id=user.id,
        )
        assert "no permitido" in result.lower()

    @pytest.mark.asyncio
    async def test_valid_field_accepted(self):
        phone = _phone(18)
        user = await _make_admin_user(phone, chat_admin=True)
        await execute_tool(
            "set_user_setting",
            {"user_ref": user.id, "field": "name", "value": "Admin Dan"},
            admin_user_id=user.id,
        )
        refreshed = await get_or_create_user(phone)
        assert refreshed.name == "Admin Dan"


# ─────────────────────────────────────────────────────────────────────────
# App setting tool
# ─────────────────────────────────────────────────────────────────────────


class TestAppSettingTool:

    @pytest.mark.asyncio
    async def test_set_app_setting_upsert(self):
        key = "__test_admin_setting"
        # Ensure clean start
        async with async_session() as session:
            await session.execute(
                delete(AppSetting).where(AppSetting.key == key)
            )
            await session.commit()
        result = await execute_tool(
            "set_app_setting", {"key": key, "value": "hello"}, admin_user_id=1,
        )
        assert "hello" in result
        async with async_session() as session:
            row = (await session.execute(
                select(AppSetting).where(AppSetting.key == key)
            )).scalar_one()
        assert row.value == "hello"
        # Update existing
        await execute_tool(
            "set_app_setting", {"key": key, "value": "world"}, admin_user_id=1,
        )
        async with async_session() as session:
            row = (await session.execute(
                select(AppSetting).where(AppSetting.key == key)
            )).scalar_one()
        assert row.value == "world"
        # Cleanup
        async with async_session() as session:
            await session.execute(
                delete(AppSetting).where(AppSetting.key == key)
            )
            await session.commit()

    @pytest.mark.asyncio
    async def test_get_default_model_lists_aliases(self):
        result = await execute_tool("get_default_model", {}, admin_user_id=1)
        assert "haiku" in result
        assert "sonnet" in result
        assert "opus" in result


# ─────────────────────────────────────────────────────────────────────────
# Code introspection path guards
# ─────────────────────────────────────────────────────────────────────────


class TestCodeIntrospectionSecurity:
    """read_code and list_code MUST stay inside the allowlist."""

    def test_env_rejected(self):
        ok, reason = _is_allowed_path(".env")
        assert ok is False

    def test_db_file_rejected(self):
        ok, reason = _is_allowed_path("farmafacil.db")
        assert ok is False

    def test_absolute_path_rejected(self):
        ok, _ = _is_allowed_path("/etc/passwd")
        assert ok is False

    def test_dotdot_escape_rejected(self):
        ok, _ = _is_allowed_path("src/../../../etc/passwd")
        assert ok is False

    def test_hidden_file_rejected(self):
        ok, _ = _is_allowed_path("src/farmafacil/.secret")
        assert ok is False

    def test_src_farmafacil_allowed(self):
        ok, _ = _is_allowed_path("src/farmafacil/bot/handler.py")
        assert ok is True

    def test_claude_md_allowed(self):
        ok, _ = _is_allowed_path("CLAUDE.md")
        assert ok is True

    def test_tests_allowed(self):
        ok, _ = _is_allowed_path("tests/test_admin_chat.py")
        assert ok is True

    def test_node_modules_rejected(self):
        ok, _ = _is_allowed_path("node_modules/foo/index.js")
        assert ok is False

    @pytest.mark.asyncio
    async def test_read_code_blocked_path(self):
        result = await execute_tool(
            "read_code", {"path": ".env"}, admin_user_id=1,
        )
        assert "denegada" in result

    @pytest.mark.asyncio
    async def test_read_code_allowed_file(self):
        result = await execute_tool(
            "read_code",
            {"path": "src/farmafacil/bot/handler.py"},
            admin_user_id=1,
        )
        assert "handler.py" in result or "===" in result


# ─────────────────────────────────────────────────────────────────────────
# run_admin_turn loop
# ─────────────────────────────────────────────────────────────────────────


def _mock_anthropic_response(text: str, in_toks: int = 10, out_toks: int = 20):
    """Build a fake Anthropic messages.create() response."""
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(input_tokens=in_toks, output_tokens=out_toks)
    return response


class TestParseAdminAction:

    def test_final_with_response(self):
        action, fields = _parse_admin_action(
            "ACTION: FINAL\nRESPONSE: Hola mundo"
        )
        assert action == "FINAL"
        assert fields["RESPONSE"] == "Hola mundo"

    def test_tool_call_with_args(self):
        action, fields = _parse_admin_action(
            'ACTION: TOOL_CALL\nTOOL: counts\nARGS: {}'
        )
        assert action == "TOOL_CALL"
        assert fields["TOOL"] == "counts"
        assert fields["ARGS"] == "{}"


class TestRunAdminTurn:

    @pytest.mark.asyncio
    async def test_final_response_short_circuits(self):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _mock_anthropic_response(
            "ACTION: FINAL\nRESPONSE: Todo OK admin!"
        )
        with (
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "sk-test"),
            patch("farmafacil.services.ai_responder.anthropic.Anthropic",
                  return_value=fake_client),
        ):
            result = await run_admin_turn(
                "ping", "sys prompt", None, admin_user_id=1,
            )
        assert "Todo OK admin!" in result.text
        assert result.input_tokens == 10
        assert result.output_tokens == 20
        assert result.steps == 1
        assert result.tools_used == []
        # Exactly one API call
        assert fake_client.messages.create.call_count == 1

    @pytest.mark.asyncio
    async def test_tool_call_then_final(self):
        """First reply is TOOL_CALL, second is FINAL — 2 API calls."""
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = [
            _mock_anthropic_response(
                'ACTION: TOOL_CALL\nTOOL: counts\nARGS: {}',
                in_toks=5, out_toks=6,
            ),
            _mock_anthropic_response(
                "ACTION: FINAL\nRESPONSE: Tenemos 42 usuarios.",
                in_toks=7, out_toks=8,
            ),
        ]
        fake_tool = AsyncMock(return_value="usuarios: 42\nfeedback: 0")
        with (
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "sk-test"),
            patch("farmafacil.services.ai_responder.anthropic.Anthropic",
                  return_value=fake_client),
            patch("farmafacil.services.admin_chat.execute_tool", new=fake_tool),
        ):
            result = await run_admin_turn(
                "cuantos usuarios hay?", "sys prompt", None, admin_user_id=42,
            )
        assert "42 usuarios" in result.text
        assert result.steps == 2
        assert result.tools_used == ["counts"]
        assert result.input_tokens == 12
        assert result.output_tokens == 14
        # execute_tool called with admin_user_id=42
        fake_tool.assert_awaited_once_with("counts", {}, admin_user_id=42)

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_fallback(self):
        with patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", ""):
            result = await run_admin_turn(
                "hello", "sys prompt", None, admin_user_id=1,
            )
        assert "ANTHROPIC_API_KEY" in result.text
        assert result.input_tokens == 0
        assert result.output_tokens == 0

    @pytest.mark.asyncio
    async def test_history_elements_validated_no_sentinel_leaks(self):
        """Malformed history elements must be dropped and the Anthropic API
        must never receive any sentinel fields like `_admin_user_id`."""
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _mock_anthropic_response(
            "ACTION: FINAL\nRESPONSE: ok"
        )
        # Mix valid messages with malformed / sentinel entries
        history = [
            {"role": "user", "content": "previous question"},
            {"role": "assistant", "content": "previous answer"},
            {"_admin_user_id": 1},               # sentinel dict — must drop
            {"role": "system", "content": "bad"},  # wrong role — must drop
            {"role": "user"},                      # missing content — must drop
            "not a dict",                          # garbage — must drop
            {"role": "user", "content": 12345},    # non-str content — must drop
        ]
        with (
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "sk-test"),
            patch("farmafacil.services.ai_responder.anthropic.Anthropic",
                  return_value=fake_client),
        ):
            await run_admin_turn(
                "next question", "sys prompt", history, admin_user_id=42,
            )
        # Capture the messages array sent to the Anthropic API
        call_kwargs = fake_client.messages.create.call_args.kwargs
        sent_messages = call_kwargs["messages"]
        # Exactly 3 messages: 2 valid history + 1 current user message
        assert len(sent_messages) == 3
        # No sentinel fields anywhere
        for m in sent_messages:
            assert set(m.keys()) == {"role", "content"}
            assert m["role"] in ("user", "assistant")
            assert isinstance(m["content"], str)
            assert "_admin_user_id" not in m["content"]
        # The first two preserve the valid history items
        assert sent_messages[0] == {"role": "user", "content": "previous question"}
        assert sent_messages[1] == {"role": "assistant", "content": "previous answer"}
        assert sent_messages[2] == {"role": "user", "content": "next question"}

    @pytest.mark.asyncio
    async def test_step_budget_exhausted(self):
        """Every reply is a TOOL_CALL — should hit MAX_ADMIN_STEPS cap."""
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _mock_anthropic_response(
            'ACTION: TOOL_CALL\nTOOL: counts\nARGS: {}',
            in_toks=1, out_toks=1,
        )
        fake_tool = AsyncMock(return_value="usuarios: 1")
        with (
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "sk-test"),
            patch("farmafacil.services.ai_responder.anthropic.Anthropic",
                  return_value=fake_client),
            patch("farmafacil.services.admin_chat.execute_tool", new=fake_tool),
            patch("farmafacil.services.ai_responder.MAX_ADMIN_STEPS", 3),
        ):
            result = await run_admin_turn(
                "loop forever", "sys prompt", None, admin_user_id=1,
            )
        assert "límite" in result.text or "limite" in result.text
        assert result.steps == 3
        assert len(result.tools_used) == 3


# ─────────────────────────────────────────────────────────────────────────
# Handler integration — full dispatch through handle_incoming_message
# ─────────────────────────────────────────────────────────────────────────


class TestHandlerAdminDispatch:
    """handle_incoming_message routes admin traffic correctly."""

    @pytest.mark.asyncio
    async def test_slash_admin_toggles_via_handler(self):
        phone = _phone(19)
        await _make_admin_user(phone, chat_admin=True)
        captured: list[str] = []

        async def fake_send(to, text):
            captured.append(text)

        with (
            patch("farmafacil.bot.handler.send_text_message", side_effect=fake_send),
            patch("farmafacil.bot.handler.log_outbound_conv", new=AsyncMock()),
            patch("farmafacil.bot.handler.send_read_receipt", new=AsyncMock()),
        ):
            await handle_incoming_message(phone, "/admin")
        # Welcome message was sent
        assert any("Modo Admin ACTIVADO" in m for m in captured)
        refreshed = await get_or_create_user(phone)
        assert refreshed.admin_mode_active is True

    @pytest.mark.asyncio
    async def test_slash_admin_denied_via_handler(self):
        phone = _phone(20)
        await _make_admin_user(phone, chat_admin=False)
        captured: list[str] = []

        async def fake_send(to, text):
            captured.append(text)

        with (
            patch("farmafacil.bot.handler.send_text_message", side_effect=fake_send),
            patch("farmafacil.bot.handler.log_outbound_conv", new=AsyncMock()),
            patch("farmafacil.bot.handler.send_read_receipt", new=AsyncMock()),
        ):
            await handle_incoming_message(phone, "/admin")
        assert any("permisos de admin" in m for m in captured)

    @pytest.mark.asyncio
    async def test_admin_free_text_routed_to_admin_turn(self):
        """When admin_mode_active=True, free-text messages go through
        _handle_admin_turn, NOT the normal drug-search pipeline."""
        phone = _phone(21)
        await _make_admin_user(phone, chat_admin=True)
        await set_admin_mode(phone, True)

        # Seed the app_admin role so _handle_admin_turn can load it
        await seed_ai_roles()

        fake_admin_turn = AsyncMock(return_value=AdminTurnResult(
            text="Yo soy admin AI, hay 5 usuarios.",
            input_tokens=10, output_tokens=20, steps=1, tools_used=["counts"],
        ))
        captured_replies: list[str] = []

        async def fake_send(to, text):
            captured_replies.append(text)

        with (
            patch("farmafacil.bot.handler.send_text_message", side_effect=fake_send),
            patch("farmafacil.bot.handler.log_outbound_conv", new=AsyncMock()),
            patch("farmafacil.bot.handler.send_read_receipt", new=AsyncMock()),
            patch("farmafacil.bot.handler.run_admin_turn", new=fake_admin_turn),
            patch("farmafacil.bot.handler.classify_intent") as mock_intent,
            patch("farmafacil.bot.handler._handle_drug_search") as mock_search,
        ):
            await handle_incoming_message(phone, "cuantos usuarios hay")

        # Admin AI was invoked
        fake_admin_turn.assert_awaited_once()
        # Normal drug-search pipeline was NOT invoked
        mock_intent.assert_not_called()
        mock_search.assert_not_called()
        # The admin AI's reply reached the user
        assert any("soy admin AI" in m for m in captured_replies)

    @pytest.mark.asyncio
    async def test_admin_reply_logged_as_admin_out(self):
        """Outbound admin reply must be logged with message_type='admin_out'
        so dashboards can filter admin conversations separately."""
        phone = _phone(22)
        await _make_admin_user(phone, chat_admin=True)
        await set_admin_mode(phone, True)
        await seed_ai_roles()

        fake_admin_turn = AsyncMock(return_value=AdminTurnResult(
            text="hola admin",
            input_tokens=5, output_tokens=10, steps=1,
        ))
        captured_log_calls: list[tuple[str, str, str]] = []

        async def fake_log(phone_arg, text_arg, message_type="text"):
            captured_log_calls.append((phone_arg, text_arg, message_type))

        with (
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()),
            patch("farmafacil.bot.handler.log_outbound_conv", side_effect=fake_log),
            patch("farmafacil.bot.handler.send_read_receipt", new=AsyncMock()),
            patch("farmafacil.bot.handler.run_admin_turn", new=fake_admin_turn),
        ):
            await handle_incoming_message(phone, "hola")

        # At least one call must be tagged admin_out
        admin_out = [c for c in captured_log_calls if c[2] == "admin_out"]
        assert len(admin_out) >= 1, f"No admin_out log — got {captured_log_calls}"

    @pytest.mark.asyncio
    async def test_bug_escape_hatch_still_works_while_admin_active(self):
        """Even with admin_mode_active=True, /bug must route to feedback,
        not to the admin AI. Safety: never trap an admin in a broken state."""
        phone = _phone(23)
        await _make_admin_user(phone, chat_admin=True)
        await set_admin_mode(phone, True)

        fake_admin_turn = AsyncMock()
        with (
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()),
            patch("farmafacil.bot.handler.log_outbound_conv", new=AsyncMock()),
            patch("farmafacil.bot.handler.send_read_receipt", new=AsyncMock()),
            patch("farmafacil.bot.handler.run_admin_turn", new=fake_admin_turn),
            patch(
                "farmafacil.bot.handler.create_feedback",
                new=AsyncMock(return_value=999),
            ),
        ):
            await handle_incoming_message(phone, "/bug algo esta roto")

        # Admin AI must NOT have been called
        fake_admin_turn.assert_not_called()
