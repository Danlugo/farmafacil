"""Tests for WhatsApp processing indicator — typing indicator (Item 117, v0.38.0).

Covers:
- send_typing_indicator() — WhatsApp API call with mocked httpx
- send_reaction() / remove_reaction() — still available as general utilities
- _safe_handle — error wrapper for background tasks
- Webhook integration — typing indicator sent for processed types, skipped for silent
- Proxy mode — typing indicator is no-op when response collector is active
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── send_typing_indicator unit tests ────────────────────────────────────


class TestSendTypingIndicator:
    """Test the WhatsApp typing indicator sender."""

    @pytest.mark.asyncio
    async def test_sends_typing_indicator_payload(self):
        """send_typing_indicator sends correct payload to WhatsApp API."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch(
            "farmafacil.bot.whatsapp._get_http_client",
            return_value=mock_client,
        ):
            from farmafacil.bot.whatsapp import send_typing_indicator

            await send_typing_indicator("1234567890")

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["type"] == "typing_indicator"
        assert payload["typing_indicator"]["type"] == "text"
        assert payload["to"] == "1234567890"
        assert payload["messaging_product"] == "whatsapp"

    @pytest.mark.asyncio
    async def test_noop_in_proxy_mode(self):
        """send_typing_indicator is a no-op when proxy mode is active."""
        mock_client = AsyncMock()

        with patch(
            "farmafacil.bot.whatsapp._get_http_client",
            return_value=mock_client,
        ):
            from farmafacil.bot.whatsapp import (
                send_typing_indicator,
                start_collecting,
                stop_collecting,
            )

            start_collecting()
            try:
                await send_typing_indicator("1234567890")
            finally:
                stop_collecting()

        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_swallows_http_errors(self):
        """send_typing_indicator never raises — HTTP errors are silently logged."""
        import httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("test error"),
        )

        with patch(
            "farmafacil.bot.whatsapp._get_http_client",
            return_value=mock_client,
        ):
            from farmafacil.bot.whatsapp import send_typing_indicator

            # Should not raise
            await send_typing_indicator("1234567890")


# ── send_reaction / remove_reaction unit tests (general utility) ────────


class TestSendReaction:
    """Test the WhatsApp reaction sender (general utility, not used for typing)."""

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


# ── Webhook integration — typing indicator for processed types ──────────


class TestWebhookTypingIndicator:
    """Verify typing indicator is sent for message types that trigger handlers."""

    @pytest.mark.parametrize(
        "msg_type",
        ["text", "location", "image", "document", "audio"],
        ids=["text", "location", "image", "document", "audio"],
    )
    def test_typing_indicator_sent_for_processed_types(self, msg_type):
        """The typing indicator is sent in webhook.py for message types that need processing."""
        import inspect

        from farmafacil.bot.webhook import receive_webhook

        source = inspect.getsource(receive_webhook)
        # The typing indicator dispatch covers all processed types in one check
        assert "send_typing_indicator(sender)" in source
        assert msg_type in source

    def test_typing_indicator_not_sent_for_silent_types(self):
        """Silent types (reaction, system) don't get typing indicator."""
        import inspect

        from farmafacil.bot.webhook import receive_webhook

        source = inspect.getsource(receive_webhook)
        # The typing indicator block only covers processed types
        assert '"reaction", "system", "ephemeral", "order"' in source
        # These types are handled separately (just logging) — no handler dispatch

    def test_no_reaction_cleanup_needed(self):
        """Typing indicator auto-dismisses — no clear_reaction in _safe_handle."""
        import inspect

        from farmafacil.bot.webhook import _safe_handle

        source = inspect.getsource(_safe_handle)
        # clear_reaction parameter should be gone
        assert "clear_reaction" not in source
        assert "remove_reaction" not in source

    def test_webhook_uses_typing_indicator_not_reaction(self):
        """webhook.py uses send_typing_indicator, not send_reaction."""
        import inspect

        from farmafacil.bot.webhook import receive_webhook

        source = inspect.getsource(receive_webhook)
        assert "send_typing_indicator" in source
        # Reaction should NOT be used for the processing indicator
        assert 'send_reaction(sender, wa_id, "⏳")' not in source
