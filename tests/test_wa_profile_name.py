"""Tests for WhatsApp profile name auto-detection during onboarding.

Item 126 (v0.46.0): When a new user messages the bot, extract the
contact display name from the WhatsApp webhook ``contacts`` array and
pre-fill the user's name.  This lets the bot greet the user by name
(``MSG_WELCOME_NAMED``) and skip the "¿Cómo te llamas?" step.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from farmafacil.services.users import _extract_first_name


# ── Unit tests: _extract_first_name ─────────────────────────────────────


class TestExtractFirstName:
    """Test the helper that extracts a clean first name from WA profiles."""

    @pytest.mark.parametrize(
        "full_name,expected",
        [
            ("Johnny Alejandro Gonzalez lugo", "Johnny"),
            ("Maria", "Maria"),
            ("jose", "Jose"),
            ("CARLOS EDUARDO", "Carlos"),
            ("  Ana Maria  ", "Ana"),
            ("María José García", "María"),
            ("José", "José"),
            ("Ñoño Perez", "Ñoño"),
            ("Über Cool", "Über"),
        ],
        ids=[
            "full-name", "single-name", "lowercase", "uppercase",
            "whitespace", "accented", "single-accented", "ñ-name", "umlaut",
        ],
    )
    def test_valid_names(self, full_name: str, expected: str):
        assert _extract_first_name(full_name) == expected

    @pytest.mark.parametrize(
        "full_name",
        [
            "",
            "   ",
            "A",
            "X",
            "123456789",
            "🎉🎊",
            "+584127006823",
            "42",
            "😀 emoji name",
            "A" * 25,  # too long after extraction of first word > 20 chars
        ],
        ids=[
            "empty", "whitespace-only", "single-char-A", "single-char-X",
            "all-digits", "emojis", "phone-number", "two-digits",
            "emoji-prefix", "too-long",
        ],
    )
    def test_invalid_names_return_empty(self, full_name: str):
        assert _extract_first_name(full_name) == ""

    def test_strips_numbers_from_name(self):
        """Names like 'Juan123' should extract 'Juan'."""
        assert _extract_first_name("Juan123 Perez") == "Juan"

    def test_strips_punctuation(self):
        """Names with punctuation should be cleaned."""
        assert _extract_first_name("Maria! Garcia") == "Maria"


# ── Unit tests: get_or_create_user with name pre-fill ───────────────────


class TestGetOrCreateUserPreFill:
    """Test that get_or_create_user pre-fills name from WA profile.

    Uses a real in-memory SQLite database to verify the actual User
    record created by ``get_or_create_user``.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "profile_name,expected_step,expected_name",
        [
            ("Johnny Alejandro Gonzalez lugo", "welcome_named", "Johnny"),
            ("Maria", "welcome_named", "Maria"),
            ("", "welcome", ""),
            ("🎉🎊", "welcome", ""),
            ("A", "welcome", ""),  # too short
        ],
        ids=[
            "full-name-prefilled", "single-name-prefilled",
            "empty-welcome", "emoji-welcome", "too-short-welcome",
        ],
    )
    async def test_new_user_onboarding_step(
        self, profile_name: str, expected_step: str, expected_name: str,
    ):
        """New user gets correct onboarding step based on profile name."""
        from farmafacil.services.users import get_or_create_user

        # Use a unique phone per test case
        phone = f"584129{abs(hash(profile_name)) % 100000:05d}"

        user = await get_or_create_user(phone, wa_profile_name=profile_name)
        assert user.onboarding_step == expected_step
        assert (user.name or "") == expected_name

    @pytest.mark.asyncio
    async def test_existing_user_not_affected_by_profile_name(self):
        """Existing users are returned as-is — profile name doesn't overwrite."""
        from farmafacil.services.users import get_or_create_user

        phone = "584129999904"

        # Create user first time
        user1 = await get_or_create_user(phone, wa_profile_name="Daniel")
        assert user1.name == "Daniel"
        assert user1.onboarding_step == "welcome_named"

        # Second call with different profile name — should NOT overwrite
        user2 = await get_or_create_user(phone, wa_profile_name="OtherName")
        assert user2.name == "Daniel"  # unchanged


# ── Integration tests: welcome_named onboarding flow ────────────────────


class TestWelcomeNamedFlow:
    """Test the welcome_named onboarding step in the message handler."""

    @pytest.mark.asyncio
    async def test_welcome_named_sends_greeting_and_processes_location(self):
        """User with pre-filled name gets greeted and location is processed."""
        user = MagicMock()
        user.id = 99
        user.name = "Johnny"
        user.onboarding_step = "welcome_named"
        user.phone_number = "584120000010"
        user.latitude = None
        user.longitude = None
        user.zone_name = None
        user.city_code = None
        user.display_preference = "grid"
        user.admin_mode_active = False
        user.chat_admin = False
        user.awaiting_clarification_context = None
        user.awaiting_category_search = None
        user.chat_debug = None
        user.last_search_query = None

        validated_user = MagicMock()
        validated_user.id = 99
        validated_user.name = "Johnny"
        validated_user.onboarding_step = "welcome_named"
        validated_user.phone_number = "584120000010"
        validated_user.latitude = None
        validated_user.zone_name = None
        validated_user.display_preference = "grid"
        validated_user.admin_mode_active = False
        validated_user.chat_admin = False
        validated_user.awaiting_clarification_context = None
        validated_user.awaiting_category_search = None
        validated_user.chat_debug = None
        validated_user.last_search_query = None

        from farmafacil.bot.messages import MSG_WELCOME_NAMED

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=validated_user),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_set_step,
            patch("farmafacil.bot.handler.classify_intent", new_callable=AsyncMock) as mock_classify,
            patch("farmafacil.bot.handler._resolve_location", new_callable=AsyncMock) as mock_resolve,
        ):
            from farmafacil.services.location import LocationResult
            mock_classify.return_value = MagicMock(
                detected_location="Los Naranjos",
                action="greeting",
            )
            mock_resolve.return_value = LocationResult(
                lat=10.45, lng=-66.85,
                zone_name="Los Naranjos", city_code="CCS",
                confidence=0.9, display_name="Los Naranjos, Caracas",
                source="forward",
                alternatives=[],
            )

            # Mock update_user_location to return updated user
            updated_user = MagicMock()
            updated_user.name = "Johnny"
            updated_user.zone_name = "Los Naranjos"
            with patch("farmafacil.bot.handler.update_user_location", new_callable=AsyncMock, return_value=updated_user):
                from farmafacil.bot.handler import handle_incoming_message
                await handle_incoming_message(
                    "584120000010", "Vivo en Los Naranjos",
                    wa_message_id="wamid_test",
                )

            # First call: MSG_WELCOME_NAMED greeting
            calls = mock_send.call_args_list
            assert len(calls) >= 1
            first_msg = calls[0][0][1]
            assert "Johnny" in first_msg
            assert "FarmaFacil" in first_msg

            # Step was set to awaiting_location
            set_step_calls = mock_set_step.call_args_list
            assert any(
                call[0] == ("584120000010", "awaiting_location")
                for call in set_step_calls
            )

    @pytest.mark.asyncio
    async def test_welcome_named_bare_greeting_shows_welcome_then_location_prompt(self):
        """Pre-filled user sending 'hola' sees welcome + location not found."""
        user = MagicMock()
        user.id = 100
        user.name = "Maria"
        user.onboarding_step = "welcome_named"
        user.phone_number = "584120000011"
        user.latitude = None
        user.longitude = None
        user.zone_name = None
        user.city_code = None
        user.display_preference = "grid"
        user.admin_mode_active = False
        user.chat_admin = False
        user.awaiting_clarification_context = None
        user.awaiting_category_search = None
        user.chat_debug = None
        user.last_search_query = None

        from farmafacil.bot.messages import MSG_WELCOME_NAMED, MSG_LOCATION_NOT_FOUND

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent", new_callable=AsyncMock) as mock_classify,
            patch("farmafacil.bot.handler._resolve_location", new_callable=AsyncMock, return_value=None),
        ):
            mock_classify.return_value = MagicMock(
                detected_location=None,
                action="greeting",
            )

            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(
                "584120000011", "hola",
                wa_message_id="wamid_test2",
            )

            calls = mock_send.call_args_list
            # First call: welcome named greeting
            assert "Maria" in calls[0][0][1]
            assert "FarmaFacil" in calls[0][0][1]
            # Second call: location not found (since "hola" isn't a location)
            assert calls[1][0][1] == MSG_LOCATION_NOT_FOUND


# ── Webhook profile name extraction tests ────────────────────────────────


class TestWebhookProfileExtraction:
    """Test that the webhook extracts and passes wa_profile_name."""

    @pytest.mark.asyncio
    async def test_webhook_extracts_profile_name_from_contacts(self):
        """Verify the webhook passes wa_profile_name to handle_incoming_message."""
        from farmafacil.bot.webhook import receive_webhook

        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "contacts": [{
                            "profile": {"name": "Johnny Alejandro Gonzalez lugo"},
                            "wa_id": "584120000020",
                        }],
                        "messages": [{
                            "from": "584120000020",
                            "id": "wamid_test_profile",
                            "type": "text",
                            "text": {"body": "hola"},
                        }],
                    },
                }],
            }],
        }
        raw = json.dumps(payload).encode()

        mock_request = AsyncMock()
        mock_request.body = AsyncMock(return_value=raw)
        mock_request.headers = {"X-Hub-Signature-256": ""}

        with (
            patch("farmafacil.bot.webhook._verify_signature", return_value=True),
            patch("farmafacil.bot.webhook.is_duplicate_message", new_callable=AsyncMock, return_value=False),
            patch("farmafacil.bot.webhook.send_reaction", new_callable=AsyncMock),
            patch("farmafacil.bot.webhook._log_inbound_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.webhook.handle_incoming_message", new_callable=AsyncMock) as mock_handler,
            patch("farmafacil.bot.webhook._fire_and_forget") as mock_fire,
        ):
            await receive_webhook(mock_request)

            # _fire_and_forget is patched (no-op), so verify that the
            # handler coroutine was constructed with the right kwargs
            # (i.e., the dispatch call site is wired correctly).
            assert mock_handler.called
            call_kwargs = mock_handler.call_args
            # handle_incoming_message is called as positional + keyword
            assert call_kwargs[1].get("wa_profile_name") == "Johnny Alejandro Gonzalez lugo"

    @pytest.mark.asyncio
    async def test_webhook_handles_missing_contacts_gracefully(self):
        """Webhook works when contacts array is missing (wa_profile_name='')."""
        from farmafacil.bot.webhook import receive_webhook

        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "584120000021",
                            "id": "wamid_no_contacts",
                            "type": "text",
                            "text": {"body": "hola"},
                        }],
                    },
                }],
            }],
        }
        raw = json.dumps(payload).encode()

        mock_request = AsyncMock()
        mock_request.body = AsyncMock(return_value=raw)
        mock_request.headers = {"X-Hub-Signature-256": ""}

        with (
            patch("farmafacil.bot.webhook._verify_signature", return_value=True),
            patch("farmafacil.bot.webhook.is_duplicate_message", new_callable=AsyncMock, return_value=False),
            patch("farmafacil.bot.webhook.send_reaction", new_callable=AsyncMock),
            patch("farmafacil.bot.webhook._log_inbound_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.webhook.handle_incoming_message", new_callable=AsyncMock) as mock_handler,
            patch("farmafacil.bot.webhook._fire_and_forget") as mock_fire,
        ):
            await receive_webhook(mock_request)

            assert mock_handler.called
            call_kwargs = mock_handler.call_args
            assert call_kwargs[1].get("wa_profile_name") == ""

    @pytest.mark.asyncio
    async def test_webhook_handles_null_profile_name(self):
        """Explicit JSON null in profile.name is coerced to empty string."""
        from farmafacil.bot.webhook import receive_webhook

        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "contacts": [{
                            "profile": {"name": None},
                            "wa_id": "584120000022",
                        }],
                        "messages": [{
                            "from": "584120000022",
                            "id": "wamid_null_name",
                            "type": "text",
                            "text": {"body": "hola"},
                        }],
                    },
                }],
            }],
        }
        raw = json.dumps(payload).encode()

        mock_request = AsyncMock()
        mock_request.body = AsyncMock(return_value=raw)
        mock_request.headers = {"X-Hub-Signature-256": ""}

        with (
            patch("farmafacil.bot.webhook._verify_signature", return_value=True),
            patch("farmafacil.bot.webhook.is_duplicate_message", new_callable=AsyncMock, return_value=False),
            patch("farmafacil.bot.webhook.send_reaction", new_callable=AsyncMock),
            patch("farmafacil.bot.webhook._log_inbound_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.webhook.handle_incoming_message", new_callable=AsyncMock) as mock_handler,
            patch("farmafacil.bot.webhook._fire_and_forget") as mock_fire,
        ):
            await receive_webhook(mock_request)

            # Explicit null must be coerced to "", not passed as None
            assert mock_handler.called
            call_kwargs = mock_handler.call_args
            assert call_kwargs[1].get("wa_profile_name") == ""


# ── Relay endpoint profile name tests ────────────────────────────────────


class TestRelayProfileName:
    """Test that the Chamo relay passes sender_name as wa_profile_name."""

    @pytest.mark.asyncio
    async def test_chat_relay_passes_sender_name(self):
        """POST /api/v1/chat passes body.sender_name to handler."""
        from httpx import ASGITransport, AsyncClient
        from farmafacil.api.app import app

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock) as mock_get_user,
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock) as mock_validate,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.api.routes.log_inbound", new_callable=AsyncMock),
            patch("farmafacil.api.routes.log_outbound", new_callable=AsyncMock),
        ):
            user = MagicMock()
            user.id = 200
            user.name = None
            user.onboarding_step = "welcome"
            user.phone_number = "584120000030"
            user.latitude = None
            user.zone_name = None
            user.display_preference = "grid"
            user.admin_mode_active = False
            user.chat_admin = False
            user.awaiting_clarification_context = None
            user.awaiting_category_search = None
            user.chat_debug = None
            user.last_search_query = None
            mock_get_user.return_value = user
            mock_validate.return_value = user

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/chat",
                    json={
                        "sender_id": "584120000030",
                        "sender_name": "Johnny Test",
                        "text": "hola",
                    },
                )

            assert response.status_code == 200
            # get_or_create_user should have received the profile name
            mock_get_user.assert_called_once()
            call_kwargs = mock_get_user.call_args
            assert call_kwargs[1].get("wa_profile_name") == "Johnny Test"


# ── Message constant tests ──────────────────────────────────────────────


class TestMessageConstants:
    """Test that MSG_WELCOME_NAMED is properly defined and formatted."""

    def test_msg_welcome_named_contains_placeholder(self):
        from farmafacil.bot.messages import MSG_WELCOME_NAMED
        assert "{name}" in MSG_WELCOME_NAMED

    def test_msg_welcome_named_format(self):
        from farmafacil.bot.messages import MSG_WELCOME_NAMED
        result = MSG_WELCOME_NAMED.format(name="Johnny")
        assert "Johnny" in result
        assert "FarmaFacil" in result
        assert "zona" in result.lower() or "barrio" in result.lower()

    def test_msg_welcome_named_asks_for_location(self):
        """Welcome named message should ask for location, not name."""
        from farmafacil.bot.messages import MSG_WELCOME_NAMED
        result = MSG_WELCOME_NAMED.format(name="Test")
        assert "llamas" not in result.lower()  # should NOT ask for name
        assert "zona" in result.lower() or "barrio" in result.lower()
