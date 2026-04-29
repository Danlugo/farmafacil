"""Tests for v0.19.2 / Item 49 — admin set_default_model actually takes effect.

Bug context (Daniel, 2026-04-29):
  The admin chat tool ``set_default_model("sonnet")`` correctly persisted
  ``app_settings.default_model = "sonnet"``, but every user-facing LLM call
  site hardcoded ``LLM_MODEL`` (the haiku constant) instead of resolving the
  current default. So the next user message kept using Haiku.

Fix:
  - New ``settings.resolve_user_model()`` returns the full Anthropic model
    id from the alias.
  - ``ai_responder.classify_with_ai`` / ``_call_llm`` /
    ``refine_clarified_query``, ``user_memory.auto_update_memory``, and the
    Vision/document drug-extraction helpers in ``bot.handler`` now call it.
  - ``AiResponse.model`` and ``Intent.model`` carry the resolved model so
    the handler can route token usage to the correct per-model bucket and
    the chat-debug footer can render the actual model used.
  - The admin path (``run_admin_turn``) is left HARDCODED to Opus.

These tests cover all three guarantees from the bug report:
  1. set_default_model writes the right key, resolver reads it back.
  2. Subsequent user-facing calls actually use the new model.
  3. Admin AI stays pinned to Opus regardless of default_model.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from farmafacil.config import LLM_MODEL, LLM_MODEL_ELEVATED, LLM_MODEL_OPUS
from farmafacil.services import settings as settings_svc


# ─────────────────────────────────────────────────────────────────────────
# Unit: resolver round-trips through the DB
# ─────────────────────────────────────────────────────────────────────────


class TestResolveUserModel:
    """resolve_user_model() must reflect the current app_settings.default_model."""

    @pytest.mark.asyncio
    async def test_resolver_default_is_haiku(self):
        """With no override (or fallback alias), the resolver returns Haiku.

        We restore haiku at the end of every other test, so this baseline
        check confirms the resolver maps haiku → LLM_MODEL.
        """
        await settings_svc.set_default_model("haiku")
        try:
            resolved = await settings_svc.resolve_user_model()
            assert resolved == LLM_MODEL
        finally:
            await settings_svc.set_default_model("haiku")

    @pytest.mark.asyncio
    async def test_resolver_after_set_sonnet(self):
        """set_default_model('sonnet') → resolver returns the Sonnet model id."""
        await settings_svc.set_default_model("sonnet")
        try:
            resolved = await settings_svc.resolve_user_model()
            assert resolved == LLM_MODEL_ELEVATED
            assert "sonnet" in resolved.lower()
        finally:
            await settings_svc.set_default_model("haiku")

    @pytest.mark.asyncio
    async def test_resolver_after_set_opus(self):
        """set_default_model('opus') → resolver returns the Opus model id."""
        await settings_svc.set_default_model("opus")
        try:
            resolved = await settings_svc.resolve_user_model()
            assert resolved == LLM_MODEL_OPUS
            assert "opus" in resolved.lower()
        finally:
            await settings_svc.set_default_model("haiku")

    @pytest.mark.asyncio
    async def test_set_default_model_persists_under_correct_key(self):
        """Regression — confirm the setter writes the same key the resolver reads.

        This was the suspected first bug class (case b) before investigation:
        if the setter wrote 'default_model' but the resolver read
        'user_default_model' (or vice versa), no amount of admin chat
        commands would change the user-facing model.
        """
        await settings_svc.set_default_model("sonnet")
        try:
            raw = await settings_svc.get_setting("default_model")
            assert raw == "sonnet"
            alias = await settings_svc.get_default_model()
            assert alias == "sonnet"
        finally:
            await settings_svc.set_default_model("haiku")


# ─────────────────────────────────────────────────────────────────────────
# Integration: each user-facing call site picks up the new default
# ─────────────────────────────────────────────────────────────────────────


class TestUserFacingCallSitesRespectDefault:
    """After set_default_model('sonnet'), every user-facing LLM call must
    pass the Sonnet model id to anthropic.messages.create."""

    @pytest.mark.asyncio
    async def test_classify_with_ai_uses_resolved_model(self):
        """The classify path that runs on EVERY user message — the one Daniel
        observed sticking on Haiku."""
        from farmafacil.services import ai_responder

        await settings_svc.set_default_model("sonnet")
        try:
            fake_message = MagicMock()
            fake_message.content = [MagicMock(text="ACTION: drug_search\nDRUG: aspirina")]
            fake_message.usage = MagicMock(input_tokens=10, output_tokens=5)

            fake_client = MagicMock()
            fake_client.messages.create.return_value = fake_message

            with (
                patch.object(ai_responder, "anthropic", MagicMock()) as mock_anthropic,
                patch.object(ai_responder, "ANTHROPIC_API_KEY", "sk-test"),
                patch.object(
                    ai_responder, "get_role",
                    new=AsyncMock(return_value=None),
                ),
                patch.object(
                    ai_responder, "get_memory",
                    new=AsyncMock(return_value=""),
                ),
                patch.object(
                    ai_responder, "_get_user_profile",
                    new=AsyncMock(return_value=None),
                ),
            ):
                mock_anthropic.Anthropic.return_value = fake_client
                result = await ai_responder.classify_with_ai(
                    "me duele la cabeza", user_id=1, user_name="Daniel",
                )

            # The single most important assertion: which model was actually used?
            assert fake_client.messages.create.called
            call_kwargs = fake_client.messages.create.call_args.kwargs
            assert call_kwargs["model"] == LLM_MODEL_ELEVATED, (
                f"After set_default_model('sonnet'), classify_with_ai must "
                f"use {LLM_MODEL_ELEVATED}, got {call_kwargs['model']!r} "
                f"(this is the v0.19.2 / Item 49 regression)"
            )
            # AiResponse must carry the resolved model so the handler can
            # route tokens to the correct per-model bucket.
            assert result.model == LLM_MODEL_ELEVATED
        finally:
            await settings_svc.set_default_model("haiku")

    @pytest.mark.asyncio
    async def test_call_llm_uses_resolved_model(self):
        """generate_response → _call_llm path."""
        from farmafacil.services import ai_responder

        await settings_svc.set_default_model("sonnet")
        try:
            fake_message = MagicMock()
            fake_message.content = [MagicMock(text="hola, soy un asistente")]
            fake_message.usage = MagicMock(input_tokens=20, output_tokens=10)

            fake_client = MagicMock()
            fake_client.messages.create.return_value = fake_message

            with (
                patch.object(ai_responder, "anthropic", MagicMock()) as mock_anthropic,
                patch.object(ai_responder, "ANTHROPIC_API_KEY", "sk-test"),
            ):
                mock_anthropic.Anthropic.return_value = fake_client
                text, tin, tout, model = await ai_responder._call_llm(
                    "system prompt", "hola", "Daniel",
                )

            assert text == "hola, soy un asistente"
            assert model == LLM_MODEL_ELEVATED
            call_kwargs = fake_client.messages.create.call_args.kwargs
            assert call_kwargs["model"] == LLM_MODEL_ELEVATED
        finally:
            await settings_svc.set_default_model("haiku")

    @pytest.mark.asyncio
    async def test_refine_clarified_query_uses_resolved_model(self):
        """The clarification refinement path."""
        from farmafacil.services import ai_responder

        await settings_svc.set_default_model("sonnet")
        try:
            fake_message = MagicMock()
            fake_message.content = [MagicMock(text="ginkgo gomitas adulto")]
            fake_message.usage = MagicMock(input_tokens=80, output_tokens=6)

            fake_client = MagicMock()
            fake_client.messages.create.return_value = fake_message

            with (
                patch.object(ai_responder, "anthropic", MagicMock()) as mock_anthropic,
                patch.object(ai_responder, "ANTHROPIC_API_KEY", "sk-test"),
            ):
                mock_anthropic.Anthropic.return_value = fake_client
                refined, tin, tout, model = await ai_responder.refine_clarified_query(
                    "medicinas para la memoria", "gomitas adulto",
                )

            assert refined == "ginkgo gomitas adulto"
            assert model == LLM_MODEL_ELEVATED
            call_kwargs = fake_client.messages.create.call_args.kwargs
            assert call_kwargs["model"] == LLM_MODEL_ELEVATED
        finally:
            await settings_svc.set_default_model("haiku")

    @pytest.mark.asyncio
    async def test_auto_update_memory_uses_resolved_model(self):
        """User memory updater also follows the default."""
        from farmafacil.services import user_memory

        await settings_svc.set_default_model("sonnet")
        try:
            fake_message = MagicMock()
            fake_message.content = [MagicMock(text="- usuario nuevo")]
            fake_message.usage = MagicMock(input_tokens=30, output_tokens=8)

            fake_client = MagicMock()
            fake_client.messages.create.return_value = fake_message

            with (
                patch.object(user_memory, "anthropic", MagicMock()) as mock_anthropic,
                patch.object(user_memory, "ANTHROPIC_API_KEY", "sk-test"),
                patch.object(
                    user_memory, "get_memory",
                    new=AsyncMock(return_value=""),
                ),
                patch.object(
                    user_memory, "_get_user_context",
                    new=AsyncMock(return_value="Name: Daniel"),
                ),
                patch.object(
                    user_memory, "update_memory", new=AsyncMock(),
                ),
            ):
                mock_anthropic.Anthropic.return_value = fake_client
                await user_memory.auto_update_memory(
                    user_id=1, user_name="Daniel",
                    user_message="me duele la cabeza",
                    bot_response="prueba aspirina",
                )

            assert fake_client.messages.create.called
            call_kwargs = fake_client.messages.create.call_args.kwargs
            assert call_kwargs["model"] == LLM_MODEL_ELEVATED
        finally:
            await settings_svc.set_default_model("haiku")


# ─────────────────────────────────────────────────────────────────────────
# Regression: admin path is pinned to Opus regardless of default_model
# ─────────────────────────────────────────────────────────────────────────


class TestAdminAlwaysOpus:
    """The admin AI must always use Opus — admin reasoning benefits from it,
    cost is tracked separately, and admin must NOT be affected by user-facing
    model changes."""

    @pytest.mark.asyncio
    async def test_admin_turn_uses_opus_when_default_is_haiku(self):
        from farmafacil.services import ai_responder

        await settings_svc.set_default_model("haiku")
        try:
            fake_message = MagicMock()
            fake_message.content = [MagicMock(text="ACTION: FINAL\nRESPONSE: ok")]
            fake_message.usage = MagicMock(input_tokens=15, output_tokens=4)

            fake_client = MagicMock()
            fake_client.messages.create.return_value = fake_message

            with (
                patch.object(ai_responder, "anthropic", MagicMock()) as mock_anthropic,
                patch.object(ai_responder, "ANTHROPIC_API_KEY", "sk-test"),
            ):
                mock_anthropic.Anthropic.return_value = fake_client
                result = await ai_responder.run_admin_turn(
                    "hola admin", "system prompt for admin",
                    history=[], admin_user_id=1,
                )

            assert result.text == "ok"
            call_kwargs = fake_client.messages.create.call_args.kwargs
            assert call_kwargs["model"] == LLM_MODEL_OPUS, (
                "Admin AI must ALWAYS use Opus, never the user-facing default. "
                "Even when default_model='haiku', admin must call Opus."
            )
        finally:
            await settings_svc.set_default_model("haiku")

    @pytest.mark.asyncio
    async def test_admin_turn_uses_opus_when_default_is_sonnet(self):
        """Same regression check, but with the default flipped to Sonnet."""
        from farmafacil.services import ai_responder

        await settings_svc.set_default_model("sonnet")
        try:
            fake_message = MagicMock()
            fake_message.content = [MagicMock(text="ACTION: FINAL\nRESPONSE: ok")]
            fake_message.usage = MagicMock(input_tokens=15, output_tokens=4)

            fake_client = MagicMock()
            fake_client.messages.create.return_value = fake_message

            with (
                patch.object(ai_responder, "anthropic", MagicMock()) as mock_anthropic,
                patch.object(ai_responder, "ANTHROPIC_API_KEY", "sk-test"),
            ):
                mock_anthropic.Anthropic.return_value = fake_client
                await ai_responder.run_admin_turn(
                    "hola admin", "system prompt for admin",
                    history=[], admin_user_id=1,
                )

            call_kwargs = fake_client.messages.create.call_args.kwargs
            assert call_kwargs["model"] == LLM_MODEL_OPUS, (
                "Admin AI must stay on Opus even when user default is Sonnet."
            )
        finally:
            await settings_svc.set_default_model("haiku")


# ─────────────────────────────────────────────────────────────────────────
# Integration: end-to-end flow — admin sets default → user message uses it
# ─────────────────────────────────────────────────────────────────────────


class TestEndToEndAdminThenUser:
    """Replays Daniel's exact bug repro at the service-layer boundary:
    admin tool → set_default_model → next user message resolves correctly."""

    @pytest.mark.asyncio
    async def test_admin_tool_sets_then_user_call_uses_new_model(self):
        from farmafacil.services import admin_chat, ai_responder

        # 1. Admin invokes set_default_model — same code path as Daniel's chat.
        result_text = await admin_chat._tool_set_default_model({"alias": "sonnet"})
        assert "sonnet" in result_text.lower()

        try:
            # 2. The next user-facing call (any AI role except app_admin)
            # must resolve to the Sonnet model.
            fake_message = MagicMock()
            fake_message.content = [MagicMock(text="ACTION: question\nRESPONSE: ok")]
            fake_message.usage = MagicMock(input_tokens=10, output_tokens=5)

            fake_client = MagicMock()
            fake_client.messages.create.return_value = fake_message

            with (
                patch.object(ai_responder, "anthropic", MagicMock()) as mock_anthropic,
                patch.object(ai_responder, "ANTHROPIC_API_KEY", "sk-test"),
                patch.object(
                    ai_responder, "get_role",
                    new=AsyncMock(return_value=None),
                ),
                patch.object(
                    ai_responder, "get_memory",
                    new=AsyncMock(return_value=""),
                ),
                patch.object(
                    ai_responder, "_get_user_profile",
                    new=AsyncMock(return_value=None),
                ),
            ):
                mock_anthropic.Anthropic.return_value = fake_client
                result = await ai_responder.classify_with_ai(
                    "me duele la cabeza", user_id=1, user_name="Daniel",
                )

            # The fix: the user message uses Sonnet, not Haiku.
            assert result.model == LLM_MODEL_ELEVATED
            call_kwargs = fake_client.messages.create.call_args.kwargs
            assert call_kwargs["model"] == LLM_MODEL_ELEVATED, (
                "End-to-end regression: admin set_default_model('sonnet') "
                "must change the model used by the next user message. "
                "(v0.19.2, Item 49 — Daniel's 2026-04-29 bug.)"
            )
        finally:
            # Restore default for downstream tests.
            await settings_svc.set_default_model("haiku")

    @pytest.mark.asyncio
    async def test_aliasresponse_carries_model_for_token_routing(self):
        """The handler routes per-model token buckets via the model name on
        AiResponse.model. If model_used is empty/wrong, tokens go to the
        wrong bucket and /stats becomes unreliable."""
        from farmafacil.services import ai_responder
        from farmafacil.services.users import _classify_model

        await settings_svc.set_default_model("sonnet")
        try:
            fake_message = MagicMock()
            fake_message.content = [MagicMock(text="ACTION: greeting\nRESPONSE: hola")]
            fake_message.usage = MagicMock(input_tokens=8, output_tokens=3)

            fake_client = MagicMock()
            fake_client.messages.create.return_value = fake_message

            with (
                patch.object(ai_responder, "anthropic", MagicMock()) as mock_anthropic,
                patch.object(ai_responder, "ANTHROPIC_API_KEY", "sk-test"),
                patch.object(
                    ai_responder, "get_role",
                    new=AsyncMock(return_value=None),
                ),
                patch.object(
                    ai_responder, "get_memory",
                    new=AsyncMock(return_value=""),
                ),
                patch.object(
                    ai_responder, "_get_user_profile",
                    new=AsyncMock(return_value=None),
                ),
            ):
                mock_anthropic.Anthropic.return_value = fake_client
                result = await ai_responder.classify_with_ai(
                    "hola", user_id=1, user_name="Daniel",
                )

            # _classify_model must place this in the sonnet bucket, not haiku.
            assert _classify_model(result.model) == "sonnet"
        finally:
            await settings_svc.set_default_model("haiku")
