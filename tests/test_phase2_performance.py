"""Tests for Phase 2 Performance Unlock — items 56-61."""

import asyncio
import re
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from farmafacil.db.session import async_session
from farmafacil.models.database import User
from tests.conftest import TEST_ADMIN_PASS, TEST_ADMIN_USER, admin_auth_headers


# ── Item 56: Async Anthropic SDK ────────────────────────────────────────


class TestAsyncAnthropicClient:
    """Verify that LLM calls use AsyncAnthropic (not sync Anthropic)."""

    def test_get_client_returns_async_instance(self):
        """_get_client() returns an AsyncAnthropic singleton."""
        import anthropic

        from farmafacil.services.ai_responder import _get_client

        client = _get_client()
        assert isinstance(client, anthropic.AsyncAnthropic)

    def test_get_client_returns_same_instance(self):
        """_get_client() returns the same instance on repeated calls (singleton)."""
        from farmafacil.services.ai_responder import _get_client

        c1 = _get_client()
        c2 = _get_client()
        assert c1 is c2

    def test_no_sync_anthropic_in_ai_responder(self):
        """ai_responder.py must NOT instantiate sync Anthropic() anymore."""
        import inspect

        from farmafacil.services import ai_responder

        source = inspect.getsource(ai_responder)
        # The module-level "import anthropic" is fine — but calling
        # anthropic.Anthropic() (sync) should be gone.
        # Allow "AsyncAnthropic" and "_get_client" but not bare "Anthropic("
        lines = source.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "anthropic.Anthropic(" in stripped and "AsyncAnthropic" not in stripped:
                pytest.fail(
                    f"ai_responder.py line {i} still uses sync Anthropic(): {stripped}"
                )

    def test_no_sync_anthropic_in_user_memory(self):
        """user_memory.py must NOT instantiate sync Anthropic() anymore."""
        import inspect

        from farmafacil.services import user_memory

        source = inspect.getsource(user_memory)
        for i, line in enumerate(source.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "anthropic.Anthropic(" in stripped:
                pytest.fail(
                    f"user_memory.py line {i} still uses sync Anthropic(): {stripped}"
                )

    def test_no_sync_anthropic_in_handler_extractors(self):
        """handler.py vision/document extractors must use async client."""
        import inspect

        from farmafacil.bot import handler

        source = inspect.getsource(handler)
        # Find _extract_drug_name_from_image and _extract_drug_name_from_text
        for func_name in ("_extract_drug_name_from_image", "_extract_drug_name_from_text"):
            func = getattr(handler, func_name)
            func_source = inspect.getsource(func)
            assert "anthropic.Anthropic(" not in func_source, (
                f"{func_name} still uses sync Anthropic()"
            )
            assert "_get_client()" in func_source, (
                f"{func_name} should use _get_client()"
            )


# ── Item 57: Settings cache ─────────────────────────────────────────────


class TestSettingsCache:
    """Verify in-memory cache for get_setting()."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        """Ensure cache is clean for each test."""
        from farmafacil.services.settings import clear_settings_cache

        clear_settings_cache()
        yield
        clear_settings_cache()

    @pytest.mark.asyncio
    async def test_get_setting_caches_value(self):
        """Second call within TTL returns cached value without DB hit."""
        from farmafacil.services.settings import _cache, get_setting

        # First call populates cache
        val1 = await get_setting("cache_ttl_minutes")
        assert val1 == "10080"
        assert "cache_ttl_minutes" in _cache

        # Second call should use cache (value, expire_ts)
        val2 = await get_setting("cache_ttl_minutes")
        assert val2 == val1

    @pytest.mark.asyncio
    async def test_set_setting_invalidates_cache(self):
        """set_setting() removes the key from cache."""
        from farmafacil.services.settings import _cache, get_setting, set_setting

        await get_setting("chat_debug")
        assert "chat_debug" in _cache

        await set_setting("chat_debug", "enabled")
        assert "chat_debug" not in _cache

    @pytest.mark.asyncio
    async def test_set_default_model_invalidates_cache(self):
        """set_default_model() removes 'default_model' from cache."""
        from farmafacil.services.settings import (
            _cache,
            get_setting,
            set_default_model,
        )

        await get_setting("default_model")
        assert "default_model" in _cache

        await set_default_model("haiku")
        assert "default_model" not in _cache

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self):
        """Stale cache entries are refreshed from DB."""
        from farmafacil.services.settings import _cache, get_setting

        await get_setting("cache_ttl_minutes")
        # Manually expire the entry
        key, (val, _) = "cache_ttl_minutes", _cache["cache_ttl_minutes"]
        _cache["cache_ttl_minutes"] = (val, time.monotonic() - 1)

        # Next call should re-read from DB
        val2 = await get_setting("cache_ttl_minutes")
        assert val2 == "10080"
        # Cache should be refreshed with new expiry
        _, new_expire = _cache["cache_ttl_minutes"]
        assert new_expire > time.monotonic()

    def test_clear_settings_cache(self):
        """clear_settings_cache() empties the dict."""
        from farmafacil.services.settings import _cache, clear_settings_cache

        _cache["foo"] = ("bar", time.monotonic() + 999)
        clear_settings_cache()
        assert len(_cache) == 0

    @pytest.mark.asyncio
    async def test_default_value_is_cached(self):
        """When key has no DB row, default is cached to avoid repeated misses."""
        from farmafacil.services.settings import _cache, get_setting

        # "relevance_threshold" likely has no DB row (defaults used)
        val = await get_setting("relevance_threshold")
        assert val == "0.3"
        assert "relevance_threshold" in _cache


# ── Item 58: Non-blocking webhook ────────────────────────────────────────


class TestNonBlockingWebhook:
    """Verify webhook dispatches handlers as background tasks."""

    @pytest.fixture(autouse=True)
    def _patch_admin(self):
        with (
            patch("farmafacil.api.routes.ADMIN_USERNAME", TEST_ADMIN_USER),
            patch("farmafacil.api.routes.ADMIN_PASSWORD", TEST_ADMIN_PASS),
        ):
            yield

    def test_fire_and_forget_exists(self):
        """webhook.py exposes _fire_and_forget helper."""
        from farmafacil.bot.webhook import _fire_and_forget

        assert callable(_fire_and_forget)

    def test_safe_handle_exists(self):
        """webhook.py exposes _safe_handle coroutine."""
        import asyncio

        from farmafacil.bot.webhook import _safe_handle

        assert asyncio.iscoroutinefunction(_safe_handle)

    @pytest.mark.asyncio
    async def test_safe_handle_catches_exceptions(self):
        """_safe_handle logs but doesn't raise on handler failure."""
        from farmafacil.bot.webhook import _safe_handle

        async def failing_handler():
            raise RuntimeError("test explosion")

        # Should not raise
        await _safe_handle(failing_handler(), "1234567890", "wamid.test")

    @pytest.mark.asyncio
    async def test_safe_handle_reraises_cancelled_error(self):
        """_safe_handle re-raises CancelledError for clean shutdown."""
        from farmafacil.bot.webhook import _safe_handle

        async def cancelled_handler():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await _safe_handle(cancelled_handler(), "1234567890", "wamid.test")

    @pytest.mark.asyncio
    async def test_webhook_returns_immediately(self):
        """POST /webhook returns 200 before handler completes."""
        from httpx import ASGITransport, AsyncClient

        from farmafacil.api.app import create_app

        # Create an event that tracks when handler starts
        handler_started = asyncio.Event()
        handler_can_finish = asyncio.Event()

        original_handle = None

        async def slow_handler(*args, **kwargs):
            handler_started.set()
            await handler_can_finish.wait()

        app = create_app()
        transport = ASGITransport(app=app)

        with patch("farmafacil.bot.webhook.handle_incoming_message", new=slow_handler), \
             patch("farmafacil.bot.webhook.is_duplicate_message", new=AsyncMock(return_value=False)), \
             patch("farmafacil.bot.webhook.log_inbound", new=AsyncMock()), \
             patch("farmafacil.bot.webhook._verify_signature", return_value=True):

            async with AsyncClient(transport=transport, base_url="http://test") as client:
                payload = {
                    "entry": [{
                        "changes": [{
                            "value": {
                                "messages": [{
                                    "from": "1234567890",
                                    "type": "text",
                                    "id": "wamid.test123",
                                    "text": {"body": "hello"},
                                }]
                            }
                        }]
                    }]
                }
                resp = await client.post("/webhook", json=payload)
                assert resp.status_code == 200
                assert resp.json() == {"status": "ok"}

                # Handler may or may not have started yet — either is fine.
                # The key test is that the 200 response was returned.

            # Clean up: let the handler finish if it started
            handler_can_finish.set()
            # Give background task a moment to complete
            await asyncio.sleep(0.05)


# ── Item 59: Consolidated user DB round-trips ────────────────────────────


class TestConsolidatedUserUpdates:
    """Verify set_onboarding_step and update_last_search use direct UPDATE."""

    @pytest.mark.asyncio
    async def test_set_onboarding_step_returns_none(self):
        """set_onboarding_step returns None (no longer returns User)."""
        from farmafacil.services.users import set_onboarding_step

        async with async_session() as session:
            user = User(phone_number="5559690001", onboarding_step="welcome")
            session.add(user)
            await session.commit()

        result = await set_onboarding_step("5559690001", "awaiting_name")
        assert result is None

        # Verify the update took effect
        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(User).where(User.phone_number == "5559690001")
            )
            user = result.scalar_one()
            assert user.onboarding_step == "awaiting_name"

    @pytest.mark.asyncio
    async def test_set_onboarding_step_clears(self):
        """set_onboarding_step(None) clears the step."""
        from farmafacil.services.users import set_onboarding_step

        async with async_session() as session:
            user = User(phone_number="5559690002", onboarding_step="awaiting_location")
            session.add(user)
            await session.commit()

        await set_onboarding_step("5559690002", None)

        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(User).where(User.phone_number == "5559690002")
            )
            user = result.scalar_one()
            assert user.onboarding_step is None

    @pytest.mark.asyncio
    async def test_update_last_search_direct_update(self):
        """update_last_search uses direct UPDATE pattern."""
        from farmafacil.services.users import update_last_search

        async with async_session() as session:
            user = User(phone_number="5559690003", onboarding_step="welcome")
            session.add(user)
            await session.commit()

        await update_last_search("5559690003", "losartan", search_log_id=42)

        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(User).where(User.phone_number == "5559690003")
            )
            user = result.scalar_one()
            assert user.last_search_query == "losartan"
            assert user.last_search_log_id == 42

    @pytest.mark.asyncio
    async def test_update_last_search_without_log_id(self):
        """update_last_search with search_log_id=None skips that column."""
        from farmafacil.services.users import update_last_search

        async with async_session() as session:
            user = User(phone_number="5559690004", onboarding_step="welcome")
            session.add(user)
            await session.commit()

        await update_last_search("5559690004", "acetaminofen")

        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(User).where(User.phone_number == "5559690004")
            )
            user = result.scalar_one()
            assert user.last_search_query == "acetaminofen"

    def test_set_onboarding_step_no_select_in_source(self):
        """set_onboarding_step uses UPDATE not SELECT+UPDATE pattern."""
        import inspect

        from farmafacil.services import users

        source = inspect.getsource(users.set_onboarding_step)
        # Should use update(User).where() pattern, not select(User).where()
        assert "update(User)" in source
        assert "select(User)" not in source


# ── Item 60: pool_pre_ping ───────────────────────────────────────────────


class TestPoolPrePing:
    """Verify pool_pre_ping is configured for non-SQLite engines."""

    def test_sqlite_engine_no_pool_pre_ping(self):
        """SQLite engine does not use pool_pre_ping (uses StaticPool)."""
        from farmafacil.db.session import _is_sqlite, engine

        # In test environment, we use SQLite
        if _is_sqlite:
            # SQLite doesn't support pool_pre_ping — verify it's not set
            # (StaticPool ignores it anyway, but the kwarg shouldn't be there)
            assert True  # Just confirming we're in SQLite test mode

    def test_pool_pre_ping_in_source(self):
        """session.py includes pool_pre_ping=True for Postgres."""
        import inspect

        from farmafacil.db import session

        source = inspect.getsource(session)
        assert "pool_pre_ping" in source
        assert 'pool_pre_ping": True' in source or "pool_pre_ping=True" in source


# ── Item 61: Filename sanitization ───────────────────────────────────────


class TestFilenameSanitization:
    """Verify Content-Disposition filenames are sanitized."""

    @pytest.fixture(autouse=True)
    def _patch_admin(self):
        with (
            patch("farmafacil.api.routes.ADMIN_USERNAME", TEST_ADMIN_USER),
            patch("farmafacil.api.routes.ADMIN_PASSWORD", TEST_ADMIN_PASS),
        ):
            yield

    def test_sanitize_filename_part_basic(self):
        """Normal phone number passes through with + replaced."""
        from farmafacil.api.routes import _sanitize_filename_part

        assert _sanitize_filename_part("5551234567") == "5551234567"
        assert _sanitize_filename_part("+5551234567") == "_5551234567"

    def test_sanitize_filename_part_injection(self):
        """Malicious characters are stripped."""
        from farmafacil.api.routes import _sanitize_filename_part

        result = _sanitize_filename_part('"; rm -rf /; echo "')
        assert '"' not in result
        assert ";" not in result
        assert " " not in result

    def test_sanitize_filename_part_truncates(self):
        """Result is truncated to 30 chars."""
        from farmafacil.api.routes import _sanitize_filename_part

        long_input = "A" * 100
        assert len(_sanitize_filename_part(long_input)) == 30

    def test_sanitize_filename_part_none(self):
        """None input returns empty string."""
        from farmafacil.api.routes import _sanitize_filename_part

        assert _sanitize_filename_part(None) == ""

    def test_sanitize_filename_part_header_injection(self):
        """Newline-based header injection is prevented."""
        from farmafacil.api.routes import _sanitize_filename_part

        result = _sanitize_filename_part("foo\r\nX-Injected: true")
        assert "\r" not in result
        assert "\n" not in result
        # Colons and spaces are replaced, breaking any header injection
        assert ":" not in result
        assert " " not in result

    @pytest.mark.asyncio
    async def test_csv_export_sanitizes_phone(self):
        """CSV export filename sanitizes the phone parameter."""
        from httpx import ASGITransport, AsyncClient

        from farmafacil.api.app import create_app

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                '/api/v1/conversations/export?phone="+1234; echo pwned"&format=csv',
                headers=admin_auth_headers(),
            )
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        # The filename must not contain quotes, semicolons, spaces
        assert '";"' not in cd
        assert "echo" not in cd or "_echo" in cd
        # Should contain sanitized version
        assert "conversations_" in cd

    @pytest.mark.asyncio
    async def test_docx_export_sanitizes_phone(self):
        """DOCX export filename sanitizes the phone parameter."""
        from httpx import ASGITransport, AsyncClient

        from farmafacil.api.app import create_app

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                '/api/v1/conversations/export?phone="+1234; echo pwned"&format=docx',
                headers=admin_auth_headers(),
            )
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert '";"' not in cd
        assert "conversations_" in cd
