"""Tests for WhatsApp processing indicator — ⏳ reaction (Item 117, v0.40.0).

The WhatsApp Cloud API does NOT support a ``typing_indicator`` message type.
Instead, we react to the incoming message with ⏳ when processing starts and
remove the reaction in ``_safe_handle``'s ``finally`` block once the handler
completes (or fails).  Edge-case paths that skip ``_safe_handle`` remove it
inline.

Covers:
- _safe_handle — reaction cleanup via clear_reaction flag
- Webhook integration — ⏳ reaction sent for processed types, cleaned up on edge cases
- Proxy mode — send_reaction is a no-op when response collector is active
- send_reaction / remove_reaction — unit tests (general utilities)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── _safe_handle tests ──────────────────────────────────────────────────


class TestSafeHandle:
    """Test the _safe_handle background task error wrapper."""

    @pytest.mark.asyncio
    async def test_handler_success(self):
        """_safe_handle completes normally on success."""
        async def success_handler():
            pass

        from farmafacil.bot.webhook import _safe_handle

        # Should not raise
        await _safe_handle(success_handler(), "1234567890", "wamid.test123")

    @pytest.mark.asyncio
    async def test_handler_failure_logged_not_raised(self):
        """_safe_handle catches exceptions and logs, does not re-raise."""
        async def failing_handler():
            raise RuntimeError("boom")

        from farmafacil.bot.webhook import _safe_handle

        # Should not raise
        await _safe_handle(failing_handler(), "1234567890", "wamid.test123")

    @pytest.mark.asyncio
    async def test_cancelled_error_reraised(self):
        """_safe_handle re-raises CancelledError for clean shutdown."""
        async def cancelled_handler():
            raise asyncio.CancelledError()

        from farmafacil.bot.webhook import _safe_handle

        with pytest.raises(asyncio.CancelledError):
            await _safe_handle(cancelled_handler(), "1234567890", "wamid.test123")

    @pytest.mark.asyncio
    async def test_clear_reaction_on_success(self):
        """When clear_reaction=True, remove_reaction is called after success."""
        async def success_handler():
            pass

        with patch(
            "farmafacil.bot.webhook.remove_reaction", new_callable=AsyncMock,
        ) as mock_remove:
            from farmafacil.bot.webhook import _safe_handle

            await _safe_handle(
                success_handler(), "1234567890", "wamid.test123",
                clear_reaction=True,
            )

        mock_remove.assert_awaited_once_with("1234567890", "wamid.test123")

    @pytest.mark.asyncio
    async def test_clear_reaction_on_failure(self):
        """When clear_reaction=True, remove_reaction is still called after failure."""
        async def failing_handler():
            raise RuntimeError("boom")

        with patch(
            "farmafacil.bot.webhook.remove_reaction", new_callable=AsyncMock,
        ) as mock_remove:
            from farmafacil.bot.webhook import _safe_handle

            await _safe_handle(
                failing_handler(), "1234567890", "wamid.test123",
                clear_reaction=True,
            )

        mock_remove.assert_awaited_once_with("1234567890", "wamid.test123")

    @pytest.mark.asyncio
    async def test_clear_reaction_on_cancelled(self):
        """When clear_reaction=True, remove_reaction is called even on CancelledError."""
        async def cancelled_handler():
            raise asyncio.CancelledError()

        with patch(
            "farmafacil.bot.webhook.remove_reaction", new_callable=AsyncMock,
        ) as mock_remove:
            from farmafacil.bot.webhook import _safe_handle

            with pytest.raises(asyncio.CancelledError):
                await _safe_handle(
                    cancelled_handler(), "1234567890", "wamid.test123",
                    clear_reaction=True,
                )

        mock_remove.assert_awaited_once_with("1234567890", "wamid.test123")

    @pytest.mark.asyncio
    async def test_no_clear_reaction_by_default(self):
        """When clear_reaction is not set, remove_reaction is NOT called."""
        async def success_handler():
            pass

        with patch(
            "farmafacil.bot.webhook.remove_reaction", new_callable=AsyncMock,
        ) as mock_remove:
            from farmafacil.bot.webhook import _safe_handle

            await _safe_handle(success_handler(), "1234567890", "wamid.test123")

        mock_remove.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clear_reaction_error_swallowed(self):
        """If remove_reaction raises, the error is swallowed (best-effort cleanup)."""
        async def success_handler():
            pass

        with patch(
            "farmafacil.bot.webhook.remove_reaction", new_callable=AsyncMock,
            side_effect=RuntimeError("API down"),
        ):
            from farmafacil.bot.webhook import _safe_handle

            # Should not raise even though remove_reaction fails
            await _safe_handle(
                success_handler(), "1234567890", "wamid.test123",
                clear_reaction=True,
            )

    @pytest.mark.asyncio
    async def test_clear_reaction_cancelled_error_swallowed(self):
        """If remove_reaction raises CancelledError, it's caught (BaseException guard)."""
        async def success_handler():
            pass

        with patch(
            "farmafacil.bot.webhook.remove_reaction", new_callable=AsyncMock,
            side_effect=asyncio.CancelledError(),
        ):
            from farmafacil.bot.webhook import _safe_handle

            # Should not raise — CancelledError from cleanup is swallowed
            await _safe_handle(
                success_handler(), "1234567890", "wamid.test123",
                clear_reaction=True,
            )


# ── _log_inbound_safe tests ──────────────────────────────────────────────


class TestLogInboundSafe:
    """Test the best-effort inbound log wrapper."""

    @pytest.mark.asyncio
    async def test_delegates_to_log_inbound(self):
        """_log_inbound_safe calls log_inbound with the same kwargs."""
        with patch(
            "farmafacil.bot.webhook.log_inbound", new_callable=AsyncMock,
        ) as mock_log:
            from farmafacil.bot.webhook import _log_inbound_safe

            await _log_inbound_safe(
                phone_number="123",
                message_text="hello",
                message_type="text",
                wa_message_id="wamid.1",
            )

        mock_log.assert_awaited_once_with(
            phone_number="123",
            message_text="hello",
            message_type="text",
            wa_message_id="wamid.1",
        )

    @pytest.mark.asyncio
    async def test_swallows_db_errors(self):
        """_log_inbound_safe catches DB errors — never raises."""
        with patch(
            "farmafacil.bot.webhook.log_inbound", new_callable=AsyncMock,
            side_effect=RuntimeError("DB connection refused"),
        ):
            from farmafacil.bot.webhook import _log_inbound_safe

            # Should not raise
            await _log_inbound_safe(
                phone_number="123",
                message_text="hello",
                message_type="text",
                wa_message_id="wamid.1",
            )


# ── send_reaction / remove_reaction unit tests ──────────────────────────


class TestSendReaction:
    """Test the WhatsApp reaction sender."""

    @pytest.mark.asyncio
    async def test_sends_reaction_payload(self):
        """send_reaction sends correct payload to WhatsApp API."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch(
            "farmafacil.bot.whatsapp._get_http_client",
            return_value=mock_client,
        ):
            from farmafacil.bot.whatsapp import send_reaction

            await send_reaction("1234567890", "wamid.test123", "⏳")

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["type"] == "reaction"
        assert payload["reaction"]["message_id"] == "wamid.test123"
        assert payload["reaction"]["emoji"] == "⏳"
        assert payload["to"] == "1234567890"

    @pytest.mark.asyncio
    async def test_remove_reaction_sends_empty_emoji(self):
        """remove_reaction sends empty emoji string to clear the reaction."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch(
            "farmafacil.bot.whatsapp._get_http_client",
            return_value=mock_client,
        ):
            from farmafacil.bot.whatsapp import remove_reaction

            await remove_reaction("1234567890", "wamid.test123")

        payload = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1].get("json")
        assert payload["reaction"]["emoji"] == ""

    @pytest.mark.asyncio
    async def test_noop_when_no_message_id(self):
        """send_reaction is a no-op when message_id is empty."""
        mock_client = AsyncMock()

        with patch(
            "farmafacil.bot.whatsapp._get_http_client",
            return_value=mock_client,
        ):
            from farmafacil.bot.whatsapp import send_reaction

            await send_reaction("1234567890", "", "⏳")

        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_in_proxy_mode(self):
        """send_reaction is a no-op when proxy mode (response collector) is active."""
        mock_client = AsyncMock()

        with patch(
            "farmafacil.bot.whatsapp._get_http_client",
            return_value=mock_client,
        ):
            from farmafacil.bot.whatsapp import send_reaction, start_collecting, stop_collecting

            start_collecting()
            try:
                await send_reaction("1234567890", "wamid.test123", "⏳")
            finally:
                stop_collecting()

        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_swallows_http_errors(self):
        """send_reaction never raises — HTTP errors are silently logged."""
        import httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("test error"),
        )

        with patch(
            "farmafacil.bot.whatsapp._get_http_client",
            return_value=mock_client,
        ):
            from farmafacil.bot.whatsapp import send_reaction

            # Should not raise
            await send_reaction("1234567890", "wamid.test123", "⏳")


# ── Webhook integration — reaction for processed types ───────────────────


class TestWebhookProcessingReaction:
    """Verify ⏳ reaction is sent for processed types and cleaned up properly."""

    @pytest.mark.parametrize(
        "msg_type",
        ["text", "location", "image", "document", "audio"],
        ids=["text", "location", "image", "document", "audio"],
    )
    def test_reaction_sent_for_processed_types(self, msg_type):
        """The ⏳ reaction is sent in webhook.py for message types that trigger handlers."""
        import inspect

        from farmafacil.bot.webhook import receive_webhook

        source = inspect.getsource(receive_webhook)
        assert 'send_reaction(sender, wa_id, "⏳")' in source
        assert msg_type in source

    def test_reaction_not_sent_for_silent_types(self):
        """Silent types (reaction, system) don't get ⏳ reaction."""
        import inspect

        from farmafacil.bot.webhook import receive_webhook

        source = inspect.getsource(receive_webhook)
        # These types are handled separately (just logging) — no handler dispatch
        assert '"reaction", "system", "ephemeral", "order"' in source

    def test_safe_handle_has_clear_reaction_flag(self):
        """_safe_handle accepts clear_reaction keyword argument."""
        import inspect

        from farmafacil.bot.webhook import _safe_handle

        sig = inspect.signature(_safe_handle)
        assert "clear_reaction" in sig.parameters

    def test_safe_handle_has_remove_reaction_cleanup(self):
        """_safe_handle's finally block calls remove_reaction."""
        import inspect

        from farmafacil.bot.webhook import _safe_handle

        source = inspect.getsource(_safe_handle)
        assert "remove_reaction" in source
        assert "clear_reaction" in source

    def test_webhook_uses_reaction_not_typing_indicator(self):
        """webhook.py uses send_reaction, not send_typing_indicator."""
        import inspect

        from farmafacil.bot.webhook import receive_webhook

        source = inspect.getsource(receive_webhook)
        assert "send_reaction" in source
        assert "send_typing_indicator" not in source

    @pytest.mark.parametrize(
        "edge_case",
        [
            "Image from %s has no media_id",
            "Document from %s has no media_id",
            "Audio from %s has no media_id",
            "Unhandled interactive type from %s",
        ],
        ids=[
            "image_no_media_id",
            "document_no_media_id",
            "audio_no_media_id",
            "unhandled_interactive",
        ],
    )
    def test_edge_cases_have_reaction_cleanup(self, edge_case):
        """Edge-case paths (no media_id, unhandled interactive) call remove_reaction inline."""
        import inspect

        from farmafacil.bot.webhook import receive_webhook

        source = inspect.getsource(receive_webhook)
        # The edge case warning is followed by remove_reaction cleanup
        assert edge_case.split("%s")[0].strip() in source
        assert "remove_reaction(sender, wa_id)" in source

    def test_all_safe_handle_calls_have_clear_reaction(self):
        """All _safe_handle calls for processed types pass clear_reaction=True."""
        import inspect

        from farmafacil.bot.webhook import receive_webhook

        source = inspect.getsource(receive_webhook)
        # Count _safe_handle calls with clear_reaction=True — should be
        # at least 7 (text, location×2, interactive, image, document, audio)
        cr_count = source.count("clear_reaction=True")
        assert cr_count >= 7, f"Expected ≥7, got {cr_count}"

    def test_unsupported_type_no_clear_reaction(self):
        """Unsupported type path (sticker, etc.) does NOT set clear_reaction — no reaction was sent."""
        import inspect

        from farmafacil.bot.webhook import receive_webhook

        source = inspect.getsource(receive_webhook)
        # Find the "unsupported" block — it should NOT have clear_reaction=True
        # because no ⏳ reaction was sent for those types
        unsupported_idx = source.find("Unsupported type")
        assert unsupported_idx > 0
        # The _safe_handle call after "unsupported" should NOT have clear_reaction
        block_after = source[unsupported_idx:unsupported_idx + 400]
        assert "clear_reaction=True" not in block_after

    def test_reaction_types_use_log_inbound_safe(self):
        """Reaction-handled types use _log_inbound_safe (not raw log_inbound) to prevent stuck ⏳."""
        import inspect

        from farmafacil.bot.webhook import receive_webhook

        source = inspect.getsource(receive_webhook)
        # Count _log_inbound_safe calls — should be 6 (text, location,
        # interactive, image, document, audio)
        safe_count = source.count("_log_inbound_safe")
        assert safe_count >= 6, f"Expected ≥6, got {safe_count}"

    def test_safe_handle_finally_catches_base_exception(self):
        """_safe_handle's finally block catches BaseException, not just Exception."""
        import inspect

        from farmafacil.bot.webhook import _safe_handle

        source = inspect.getsource(_safe_handle)
        assert "except BaseException:" in source
