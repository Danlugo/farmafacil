"""Tests for the ``POST /api/v1/chat`` relay endpoint (Chamo integration).

Covers:
- Request validation (sender_id, text required; sender_name optional).
- Proxy-mode plumbing: ``start_collecting`` / ``stop_collecting`` around
  the handler call, with correct JSON response structure.
- Text, image, and interactive-list response types.
- Error resilience: handler exceptions are caught and collected responses
  are still returned.
- Rate limiting (30/minute).
- Proxy-mode cleanup on exception (``finally`` block).
- Conversation logging: inbound + outbound messages are logged so that
  ``get_recent_history()`` provides AI context for follow-up questions.
"""

import random
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from farmafacil.api.app import create_app
from farmafacil.bot.whatsapp import (
    _response_collector,
    start_collecting,
    stop_collecting,
)
from farmafacil.db.session import async_session
from farmafacil.models.database import ConversationLog, User


def _unique_phone() -> str:
    """Generate a random phone number unlikely to collide."""
    return f"58412{random.randint(1000000, 9999999)}"


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def _cleanup_test_users():
    """Remove test users and conversation logs created during the test."""
    phones: list[str] = []
    yield phones
    if phones:
        async with async_session() as session:
            await session.execute(
                delete(ConversationLog).where(
                    ConversationLog.phone_number.in_(phones)
                )
            )
            await session.execute(
                delete(User).where(User.phone_number.in_(phones))
            )
            await session.commit()


# ── Unit tests for proxy-mode helpers ────────────────────────────────


class TestProxyMode:
    """Test the contextvars-based response collector."""

    def test_start_collecting_returns_empty_list(self):
        bucket = start_collecting()
        assert bucket == []
        assert _response_collector.get() is bucket
        stop_collecting()  # cleanup

    def test_stop_collecting_returns_bucket(self):
        bucket = start_collecting()
        bucket.append({"type": "text", "body": "hello"})
        result = stop_collecting()
        assert result == [{"type": "text", "body": "hello"}]
        assert _response_collector.get() is None

    def test_stop_collecting_without_start_returns_empty(self):
        # Ensure no leftover state
        _response_collector.set(None)
        result = stop_collecting()
        assert result == []

    def test_nested_calls_replace_bucket(self):
        """Second start_collecting replaces the first bucket."""
        bucket1 = start_collecting()
        bucket1.append({"type": "text", "body": "first"})
        bucket2 = start_collecting()
        assert _response_collector.get() is bucket2
        assert bucket2 == []
        stop_collecting()


# ── Integration tests for the /api/v1/chat endpoint ────────────────


class TestChatEndpoint:
    """Integration tests hitting the actual ASGI app."""

    @pytest.mark.asyncio
    async def test_basic_text_response(self, client, _cleanup_test_users):
        """Handler sends a text message → response includes it."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with patch(
            "farmafacil.api.routes.handle_incoming_message",
            new_callable=AsyncMock,
        ) as mock_handler:

            async def fake_handler(sender, message_text, **kwargs):
                bucket = _response_collector.get()
                if bucket is not None:
                    bucket.append({"type": "text", "body": "Hola! Soy FarmaFacil"})

            mock_handler.side_effect = fake_handler

            resp = await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "hola"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "responses" in data
        assert len(data["responses"]) == 1
        assert data["responses"][0]["type"] == "text"
        assert data["responses"][0]["body"] == "Hola! Soy FarmaFacil"

    @pytest.mark.asyncio
    async def test_multiple_responses(self, client, _cleanup_test_users):
        """Handler sends multiple messages → all returned in order."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with patch(
            "farmafacil.api.routes.handle_incoming_message",
            new_callable=AsyncMock,
        ) as mock_handler:

            async def fake_handler(sender, message_text, **kwargs):
                bucket = _response_collector.get()
                if bucket is not None:
                    bucket.append({"type": "text", "body": "Buscando..."})
                    bucket.append({
                        "type": "image",
                        "url": "https://example.com/drug.jpg",
                        "caption": "Losartan 50mg",
                    })
                    bucket.append({"type": "text", "body": "1 resultado encontrado"})

            mock_handler.side_effect = fake_handler

            resp = await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "losartan"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["responses"]) == 3
        assert data["responses"][0]["type"] == "text"
        assert data["responses"][1]["type"] == "image"
        assert data["responses"][1]["url"] == "https://example.com/drug.jpg"
        assert data["responses"][2]["body"] == "1 resultado encontrado"

    @pytest.mark.asyncio
    async def test_image_response_fields(self, client, _cleanup_test_users):
        """Image responses include url and optional caption."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with patch(
            "farmafacil.api.routes.handle_incoming_message",
            new_callable=AsyncMock,
        ) as mock_handler:

            async def fake_handler(sender, message_text, **kwargs):
                bucket = _response_collector.get()
                if bucket is not None:
                    bucket.append({
                        "type": "image",
                        "url": "https://cdn.example.com/img.png",
                        "caption": "Product image",
                    })

            mock_handler.side_effect = fake_handler

            resp = await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "aspirina"},
            )

        data = resp.json()
        img = data["responses"][0]
        assert img["type"] == "image"
        assert img["url"] == "https://cdn.example.com/img.png"
        assert img["caption"] == "Product image"

    @pytest.mark.asyncio
    async def test_list_response_fields(self, client, _cleanup_test_users):
        """Interactive list responses include body, button, rows."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with patch(
            "farmafacil.api.routes.handle_incoming_message",
            new_callable=AsyncMock,
        ) as mock_handler:

            async def fake_handler(sender, message_text, **kwargs):
                bucket = _response_collector.get()
                if bucket is not None:
                    bucket.append({
                        "type": "list",
                        "body": "¿Qué categoría buscas?",
                        "button": "Ver opciones",
                        "rows": [
                            {"id": "cat_1", "title": "Dolor"},
                            {"id": "cat_2", "title": "Fiebre"},
                        ],
                        "header": "Categorías",
                    })

            mock_handler.side_effect = fake_handler

            resp = await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "categorias"},
            )

        data = resp.json()
        lst = data["responses"][0]
        assert lst["type"] == "list"
        assert lst["body"] == "¿Qué categoría buscas?"
        assert lst["button"] == "Ver opciones"
        assert len(lst["rows"]) == 2
        assert lst["header"] == "Categorías"

    @pytest.mark.asyncio
    async def test_handler_exception_still_returns_collected(
        self, client, _cleanup_test_users
    ):
        """If handler raises, already-collected responses are still returned."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with patch(
            "farmafacil.api.routes.handle_incoming_message",
            new_callable=AsyncMock,
        ) as mock_handler:

            async def failing_handler(sender, message_text, **kwargs):
                bucket = _response_collector.get()
                if bucket is not None:
                    bucket.append({"type": "text", "body": "Buscando..."})
                raise RuntimeError("Simulated crash")

            mock_handler.side_effect = failing_handler

            resp = await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "test"},
            )

        # Should still get 200 with the partial response
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["responses"]) == 1
        assert data["responses"][0]["body"] == "Buscando..."

    @pytest.mark.asyncio
    async def test_handler_exception_cleans_up_proxy_mode(
        self, client, _cleanup_test_users
    ):
        """After handler exception, proxy mode is properly exited."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with patch(
            "farmafacil.api.routes.handle_incoming_message",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "test"},
            )

        # Proxy mode must be off after the request
        assert _response_collector.get() is None

    @pytest.mark.asyncio
    async def test_empty_response_when_handler_sends_nothing(
        self, client, _cleanup_test_users
    ):
        """Handler that sends no messages → empty responses list."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with patch(
            "farmafacil.api.routes.handle_incoming_message",
            new_callable=AsyncMock,
        ):
            resp = await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "silent"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["responses"] == []

    @pytest.mark.asyncio
    async def test_sender_name_is_optional(self, client, _cleanup_test_users):
        """Request without sender_name should still work."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with patch(
            "farmafacil.api.routes.handle_incoming_message",
            new_callable=AsyncMock,
        ):
            resp = await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "hola"},
            )

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_sender_name_accepted(self, client, _cleanup_test_users):
        """Request with sender_name is accepted."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with patch(
            "farmafacil.api.routes.handle_incoming_message",
            new_callable=AsyncMock,
        ):
            resp = await client.post(
                "/api/v1/chat",
                json={
                    "sender_id": phone,
                    "sender_name": "José Miguel",
                    "text": "hola",
                },
            )

        assert resp.status_code == 200


# ── Validation tests ──────────────────────────────────────────────────


class TestChatValidation:
    """Request validation — Pydantic model enforces constraints."""

    @pytest.mark.asyncio
    async def test_missing_sender_id(self, client):
        resp = await client.post(
            "/api/v1/chat",
            json={"text": "hola"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_text(self, client):
        resp = await client.post(
            "/api/v1/chat",
            json={"sender_id": "584120000000"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_text(self, client):
        resp = await client.post(
            "/api/v1/chat",
            json={"sender_id": "584120000000", "text": ""},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_sender_id_too_short(self, client):
        resp = await client.post(
            "/api/v1/chat",
            json={"sender_id": "12", "text": "hola"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_text_too_long(self, client):
        resp = await client.post(
            "/api/v1/chat",
            json={"sender_id": "584120000000", "text": "x" * 2001},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_body(self, client):
        resp = await client.post("/api/v1/chat", json={})
        assert resp.status_code == 422


# ── Proxy-mode intercept tests (send_* functions) ─────────────────────


class TestProxyModeIntercept:
    """Verify that whatsapp.py send_* functions respect proxy mode."""

    @pytest.mark.asyncio
    async def test_send_text_message_collected(self):
        """send_text_message appends to collector in proxy mode."""
        from farmafacil.bot.whatsapp import send_text_message

        start_collecting()
        result = await send_text_message("584120000000", "Test message")
        collected = stop_collecting()

        assert result == {"messages": [{"id": "proxy"}]}
        assert len(collected) == 1
        assert collected[0] == {"type": "text", "body": "Test message"}

    @pytest.mark.asyncio
    async def test_send_image_message_collected(self):
        """send_image_message appends to collector in proxy mode."""
        from farmafacil.bot.whatsapp import send_image_message

        start_collecting()
        result = await send_image_message(
            "584120000000",
            "https://example.com/img.jpg",
            caption="Drug photo",
        )
        collected = stop_collecting()

        assert result == {"messages": [{"id": "proxy"}]}
        assert len(collected) == 1
        assert collected[0]["type"] == "image"
        assert collected[0]["url"] == "https://example.com/img.jpg"
        assert collected[0]["caption"] == "Drug photo"

    @pytest.mark.asyncio
    async def test_send_image_message_no_caption(self):
        """send_image_message without caption still works."""
        from farmafacil.bot.whatsapp import send_image_message

        start_collecting()
        await send_image_message("584120000000", "https://example.com/img.jpg")
        collected = stop_collecting()

        assert collected[0]["type"] == "image"
        assert "caption" not in collected[0]

    @pytest.mark.asyncio
    async def test_send_interactive_list_collected(self):
        """send_interactive_list appends to collector in proxy mode."""
        from farmafacil.bot.whatsapp import send_interactive_list

        start_collecting()
        result = await send_interactive_list(
            to="584120000000",
            body="Choose one",
            button="Options",
            rows=[{"id": "r1", "title": "Row 1"}],
            header="Header",
            footer="Footer",
        )
        collected = stop_collecting()

        assert result == {"messages": [{"id": "proxy"}]}
        assert len(collected) == 1
        item = collected[0]
        assert item["type"] == "list"
        assert item["body"] == "Choose one"
        assert item["button"] == "Options"
        assert item["rows"] == [{"id": "r1", "title": "Row 1"}]
        assert item["header"] == "Header"
        assert item["footer"] == "Footer"

    @pytest.mark.asyncio
    async def test_send_interactive_list_minimal(self):
        """send_interactive_list without optional header/footer."""
        from farmafacil.bot.whatsapp import send_interactive_list

        start_collecting()
        await send_interactive_list(
            to="584120000000",
            body="Pick",
            button="Go",
            rows=[{"id": "x", "title": "X"}],
        )
        collected = stop_collecting()

        item = collected[0]
        assert "header" not in item
        assert "footer" not in item

    @pytest.mark.asyncio
    async def test_send_read_receipt_noop_in_proxy(self):
        """send_read_receipt is a no-op in proxy mode."""
        from farmafacil.bot.whatsapp import send_read_receipt

        start_collecting()
        await send_read_receipt("584120000000", "wamid.abc123")
        collected = stop_collecting()

        # Read receipts should NOT be collected
        assert collected == []

    @pytest.mark.asyncio
    async def test_multiple_sends_accumulate(self):
        """Multiple send_* calls accumulate in order."""
        from farmafacil.bot.whatsapp import (
            send_image_message,
            send_text_message,
        )

        start_collecting()
        await send_text_message("584120000000", "First")
        await send_image_message(
            "584120000000", "https://img.test/a.jpg", caption="Cap"
        )
        await send_text_message("584120000000", "Third")
        collected = stop_collecting()

        assert len(collected) == 3
        assert collected[0]["type"] == "text"
        assert collected[1]["type"] == "image"
        assert collected[2]["type"] == "text"
        assert collected[2]["body"] == "Third"


# ── Conversation logging for relay context ─────────────────────────────


class TestChatConversationLogging:
    """Verify that relay endpoints log messages to conversation_log.

    Without these logs, ``get_recent_history()`` returns empty for relay
    users and the AI classifier cannot understand follow-up questions
    like "which is the cheapest?" after a drug search.
    """

    @pytest.mark.asyncio
    async def test_text_chat_logs_inbound(self, client, _cleanup_test_users):
        """POST /api/v1/chat should log the user's text as inbound."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with patch(
            "farmafacil.api.routes.handle_incoming_message",
            new_callable=AsyncMock,
        ):
            resp = await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "omeprazol"},
            )

        assert resp.status_code == 200

        async with async_session() as session:
            result = await session.execute(
                select(ConversationLog)
                .where(
                    ConversationLog.phone_number == phone,
                    ConversationLog.direction == "inbound",
                )
            )
            logs = result.scalars().all()

        assert len(logs) == 1
        assert logs[0].message_text == "omeprazol"
        assert logs[0].message_type == "text"

    @pytest.mark.asyncio
    async def test_text_chat_logs_outbound(self, client, _cleanup_test_users):
        """POST /api/v1/chat should log bot text responses as outbound."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with patch(
            "farmafacil.api.routes.handle_incoming_message",
            new_callable=AsyncMock,
        ) as mock_handler:

            async def fake_handler(sender, message_text, **kwargs):
                bucket = _response_collector.get()
                if bucket is not None:
                    bucket.append({"type": "text", "body": "Buscando omeprazol..."})
                    bucket.append({"type": "text", "body": "3 resultados encontrados"})

            mock_handler.side_effect = fake_handler

            resp = await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "omeprazol"},
            )

        assert resp.status_code == 200

        async with async_session() as session:
            result = await session.execute(
                select(ConversationLog)
                .where(
                    ConversationLog.phone_number == phone,
                    ConversationLog.direction == "outbound",
                )
                .order_by(ConversationLog.id.asc())
            )
            logs = result.scalars().all()

        assert len(logs) == 2
        assert logs[0].message_text == "Buscando omeprazol..."
        assert logs[1].message_text == "3 resultados encontrados"

    @pytest.mark.asyncio
    async def test_text_chat_logs_image_caption_as_outbound(
        self, client, _cleanup_test_users,
    ):
        """Image responses with captions are logged for AI context."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with patch(
            "farmafacil.api.routes.handle_incoming_message",
            new_callable=AsyncMock,
        ) as mock_handler:

            async def fake_handler(sender, message_text, **kwargs):
                bucket = _response_collector.get()
                if bucket is not None:
                    bucket.append({
                        "type": "image",
                        "url": "https://example.com/img.jpg",
                        "caption": "Omeprazol 20mg - Bs. 15.50",
                    })

            mock_handler.side_effect = fake_handler

            resp = await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "omeprazol"},
            )

        assert resp.status_code == 200

        async with async_session() as session:
            result = await session.execute(
                select(ConversationLog)
                .where(
                    ConversationLog.phone_number == phone,
                    ConversationLog.direction == "outbound",
                )
            )
            logs = result.scalars().all()

        # Image caption should be logged as outbound text
        assert len(logs) == 1
        assert "Omeprazol 20mg" in logs[0].message_text

    @pytest.mark.asyncio
    async def test_text_chat_skips_empty_outbound(
        self, client, _cleanup_test_users,
    ):
        """Image responses without text or caption are NOT logged."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with patch(
            "farmafacil.api.routes.handle_incoming_message",
            new_callable=AsyncMock,
        ) as mock_handler:

            async def fake_handler(sender, message_text, **kwargs):
                bucket = _response_collector.get()
                if bucket is not None:
                    bucket.append({
                        "type": "image",
                        "url": "https://example.com/img.jpg",
                    })

            mock_handler.side_effect = fake_handler

            resp = await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "test"},
            )

        assert resp.status_code == 200

        async with async_session() as session:
            result = await session.execute(
                select(ConversationLog)
                .where(
                    ConversationLog.phone_number == phone,
                    ConversationLog.direction == "outbound",
                )
            )
            logs = result.scalars().all()

        assert len(logs) == 0

    @pytest.mark.asyncio
    async def test_inbound_log_failure_does_not_break_response(
        self, client, _cleanup_test_users,
    ):
        """If log_inbound raises, the chat endpoint still returns 200."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with (
            patch(
                "farmafacil.api.routes.handle_incoming_message",
                new_callable=AsyncMock,
            ) as mock_handler,
            patch(
                "farmafacil.api.routes.log_inbound",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB down"),
            ),
        ):

            async def fake_handler(sender, message_text, **kwargs):
                bucket = _response_collector.get()
                if bucket is not None:
                    bucket.append({"type": "text", "body": "Works fine"})

            mock_handler.side_effect = fake_handler

            resp = await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "test"},
            )

        assert resp.status_code == 200
        assert resp.json()["responses"][0]["body"] == "Works fine"

    @pytest.mark.asyncio
    async def test_outbound_log_failure_does_not_break_response(
        self, client, _cleanup_test_users,
    ):
        """If log_outbound raises, the chat endpoint still returns 200."""
        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with (
            patch(
                "farmafacil.api.routes.handle_incoming_message",
                new_callable=AsyncMock,
            ) as mock_handler,
            patch(
                "farmafacil.api.routes.log_outbound",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB down"),
            ),
        ):

            async def fake_handler(sender, message_text, **kwargs):
                bucket = _response_collector.get()
                if bucket is not None:
                    bucket.append({"type": "text", "body": "Still works"})

            mock_handler.side_effect = fake_handler

            resp = await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "test"},
            )

        assert resp.status_code == 200
        assert resp.json()["responses"][0]["body"] == "Still works"

    @pytest.mark.asyncio
    async def test_get_recent_history_returns_relay_messages(
        self, client, _cleanup_test_users,
    ):
        """After a relay chat, get_recent_history returns the conversation."""
        from farmafacil.services.conversation_log import get_recent_history

        phone = _unique_phone()
        _cleanup_test_users.append(phone)

        with patch(
            "farmafacil.api.routes.handle_incoming_message",
            new_callable=AsyncMock,
        ) as mock_handler:

            async def fake_handler(sender, message_text, **kwargs):
                bucket = _response_collector.get()
                if bucket is not None:
                    bucket.append({"type": "text", "body": "Hola! Soy FarmaFacil"})

            mock_handler.side_effect = fake_handler

            await client.post(
                "/api/v1/chat",
                json={"sender_id": phone, "text": "hola"},
            )

        # Now get_recent_history should return exactly the inbound + outbound
        history = await get_recent_history(phone)
        assert len(history) == 2

        roles = [m["role"] for m in history]
        assert "user" in roles
        assert "assistant" in roles

        # Inbound message should be present
        user_msgs = [m for m in history if m["role"] == "user"]
        assert any("hola" in m["content"] for m in user_msgs)

        # Outbound response should be present
        bot_msgs = [m for m in history if m["role"] == "assistant"]
        assert any("FarmaFacil" in m["content"] for m in bot_msgs)
