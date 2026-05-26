"""Tests for inline location change (Item 104, v0.29.4).

Users can permanently change their saved location in a single message
by including the destination directly — e.g. "vivo en Caracas",
"estoy en Los Naranjos", "cambiar ubicación a Baruta".

The AI classifier detects ``action="location_change"`` and extracts
``detected_location``.  The handler geocodes it inline and either:
- saves permanently (high confidence), or
- shows numbered alternatives (low confidence / ambiguous), or
- falls back to the two-step prompt when no location was extracted.

Both the AI-only and hybrid response modes are covered.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from farmafacil.bot import handler
from farmafacil.bot.handler import handle_incoming_message
from farmafacil.db.session import async_session
from farmafacil.models.database import User
from farmafacil.services.intent import Intent
from farmafacil.services.location import (
    DEFAULT_MIN_CONFIDENCE,
    LocationResult,
)
from farmafacil.services.users import get_or_create_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _seed_user(
    phone: str,
    *,
    name: str = "TestUser",
    zone_name: str = "El Cafetal",
    latitude: float = 10.45,
    longitude: float = -66.85,
    city_code: str = "CCS",
) -> None:
    """Create a fully-onboarded user (no onboarding_step)."""
    await get_or_create_user(phone)
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone)
        )
        row = result.scalar_one()
        row.name = name
        row.onboarding_step = None
        row.latitude = latitude
        row.longitude = longitude
        row.zone_name = zone_name
        row.city_code = city_code
        await session.commit()


async def _fetch_user(phone: str) -> User:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone)
        )
        return result.scalar_one()


# Shared LocationResult fixtures

_HIGH_CONF = LocationResult(
    lat=10.49,
    lng=-66.85,
    display_name="Baruta, Caracas, Venezuela",
    confidence=DEFAULT_MIN_CONFIDENCE + 0.2,
    source="forward",
    city_code="CCS",
    zone_name="Baruta",
)

_LOW_CONF = LocationResult(
    lat=10.48,
    lng=-66.87,
    display_name="Los Naranjos, Caracas, Venezuela",
    confidence=DEFAULT_MIN_CONFIDENCE - 0.05,
    source="forward",
    city_code="CCS",
    zone_name="Los Naranjos",
    alternatives=[
        {
            "lat": 10.50,
            "lng": -66.90,
            "display_name": "Los Naranjos del Cafetal, Miranda, Venezuela",
            "confidence": 0.15,
            "city_code": "CCS",
            "zone_name": "Los Naranjos del Cafetal",
        },
    ],
)


# ===========================================================================
# Hybrid mode tests (default response_mode)
# ===========================================================================


class TestInlineLocationChangeHybrid:
    """Tests for location_change via classify_intent (hybrid mode)."""

    @pytest.mark.asyncio
    async def test_high_confidence_saves_location(self):
        """'cambiar ubicación a Baruta' with high-confidence geocode
        should permanently update the user's location."""
        phone = "5499910400001"
        await _seed_user(phone, zone_name="El Cafetal")

        intent = Intent(
            action="location_change",
            detected_location="Baruta",
        )

        with patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch(
            "farmafacil.bot.handler._resolve_location",
            new=AsyncMock(return_value=_HIGH_CONF),
        ), patch(
            "farmafacil.bot.handler._name_matches_query",
            return_value=True,
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send, patch.object(
            handler, "send_read_receipt", new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "cambiar ubicación a Baruta")

        # User's location must be updated
        user = await _fetch_user(phone)
        assert user.zone_name == "Baruta"
        assert user.latitude == pytest.approx(10.49)

        # Confirmation message sent
        sent = mock_send.await_args_list[-1].args[1]
        assert "Baruta" in sent
        assert "actualizada" in sent

    @pytest.mark.asyncio
    async def test_low_confidence_shows_alternatives(self):
        """'vivo en Los Naranjos' with low-confidence geocode should show
        numbered alternatives instead of saving immediately."""
        phone = "5499910400002"
        await _seed_user(phone, zone_name="El Cafetal")

        intent = Intent(
            action="location_change",
            detected_location="Los Naranjos",
        )

        with patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch(
            "farmafacil.bot.handler._resolve_location",
            new=AsyncMock(return_value=_LOW_CONF),
        ), patch(
            "farmafacil.bot.handler._name_matches_query",
            return_value=False,
        ), patch.object(
            handler, "update_user_location", new=AsyncMock(),
        ) as mock_update, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send, patch.object(
            handler, "send_read_receipt", new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "vivo en Los Naranjos")

        # Location must NOT be updated yet
        mock_update.assert_not_awaited()

        # Numbered alternatives must be shown
        sent = mock_send.await_args_list[-1].args[1]
        assert "*1.*" in sent
        assert "Otra ubicación" in sent

        # Onboarding step should be awaiting_location_confirm
        user = await _fetch_user(phone)
        assert user.onboarding_step == "awaiting_location_confirm"

    @pytest.mark.asyncio
    async def test_geocode_not_found_sends_error(self):
        """'estoy en Xyzzyville' where Nominatim returns nothing should
        inform the user and NOT change location."""
        phone = "5499910400003"
        await _seed_user(phone, zone_name="El Cafetal")

        intent = Intent(
            action="location_change",
            detected_location="Xyzzyville",
        )

        with patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch(
            "farmafacil.bot.handler._resolve_location",
            new=AsyncMock(return_value=None),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send, patch.object(
            handler, "send_read_receipt", new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "estoy en Xyzzyville")

        # Location must remain unchanged
        user = await _fetch_user(phone)
        assert user.zone_name == "El Cafetal"

        # Error message sent
        sent = mock_send.await_args_list[-1].args[1]
        assert "ubicar" in sent.lower() or "encontr" in sent.lower()

    @pytest.mark.asyncio
    async def test_no_detected_location_falls_back_to_prompt(self):
        """'cambiar zona' without specifying WHERE should trigger the
        two-step prompt (same as before)."""
        phone = "5499910400004"
        await _seed_user(phone, zone_name="El Cafetal")

        intent = Intent(
            action="location_change",
            detected_location=None,
        )

        with patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send, patch.object(
            handler, "send_read_receipt", new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "cambiar zona")

        # Should set onboarding step to awaiting_location
        user = await _fetch_user(phone)
        assert user.onboarding_step == "awaiting_location"

        # Should prompt for location
        sent = mock_send.await_args_list[-1].args[1]
        assert "zona" in sent.lower() or "barrio" in sent.lower()

    @pytest.mark.asyncio
    async def test_location_not_overwritten_when_same(self):
        """'vivo en El Cafetal' when the user is already in El Cafetal
        should still process (AI may detect it as location_change)."""
        phone = "5499910400005"
        await _seed_user(phone, zone_name="El Cafetal")

        el_cafetal_result = LocationResult(
            lat=10.45,
            lng=-66.85,
            display_name="El Cafetal, Caracas, Venezuela",
            confidence=DEFAULT_MIN_CONFIDENCE + 0.3,
            source="forward",
            city_code="CCS",
            zone_name="El Cafetal",
        )

        intent = Intent(
            action="location_change",
            detected_location="El Cafetal",
        )

        with patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch(
            "farmafacil.bot.handler._resolve_location",
            new=AsyncMock(return_value=el_cafetal_result),
        ), patch(
            "farmafacil.bot.handler._name_matches_query",
            return_value=True,
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send, patch.object(
            handler, "send_read_receipt", new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "vivo en El Cafetal")

        # Should still confirm (even if same zone)
        sent = mock_send.await_args_list[-1].args[1]
        assert "El Cafetal" in sent
        assert "actualizada" in sent


# ===========================================================================
# AI-only mode tests
# ===========================================================================


class TestInlineLocationChangeAIOnly:
    """Tests for location_change via AI-only response mode (tool_use).

    AI-only mode now uses classify_with_tools which returns ToolUseResult
    with tool_name="change_location".  resolve_response_mode is a regular
    (non-async) function.
    """

    @pytest.mark.asyncio
    async def test_ai_only_high_confidence_saves(self):
        """In AI-only mode, change_location tool with location and
        high-confidence geocode should save permanently."""
        phone = "5499910400010"
        await _seed_user(phone, zone_name="El Cafetal")

        from farmafacil.services.ai_responder import ToolUseResult

        tool_result = ToolUseResult(
            tool_name="change_location",
            tool_input={"location": "Baruta"},
            response_text="",
            input_tokens=50,
            output_tokens=20,
            model="claude-haiku-test",
        )

        with patch(
            "farmafacil.bot.handler.get_setting",
            new=AsyncMock(return_value="ai_only"),
        ), patch(
            "farmafacil.bot.handler.resolve_response_mode",
            return_value="ai_only",
        ), patch(
            "farmafacil.bot.handler.resolve_chat_debug",
            return_value=False,
        ), patch(
            "farmafacil.bot.handler.classify_with_tools",
            new=AsyncMock(return_value=tool_result),
        ), patch(
            "farmafacil.bot.handler._resolve_location",
            new=AsyncMock(return_value=_HIGH_CONF),
        ), patch(
            "farmafacil.bot.handler._name_matches_query",
            return_value=True,
        ), patch(
            "farmafacil.bot.handler.send_text_message",
            new=AsyncMock(),
        ) as mock_send, patch(
            "farmafacil.bot.handler.send_read_receipt",
            new=AsyncMock(),
        ), patch(
            "farmafacil.bot.handler.increment_token_usage",
            new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "me mudé a Baruta")

        user = await _fetch_user(phone)
        assert user.zone_name == "Baruta"

        sent = mock_send.await_args_list[-1].args[1]
        assert "Baruta" in sent
        assert "actualizada" in sent

    @pytest.mark.asyncio
    async def test_ai_only_low_confidence_shows_alternatives(self):
        """In AI-only mode, low-confidence geocode should show
        numbered alternatives."""
        phone = "5499910400011"
        await _seed_user(phone, zone_name="El Cafetal")

        from farmafacil.services.ai_responder import ToolUseResult

        tool_result = ToolUseResult(
            tool_name="change_location",
            tool_input={"location": "Los Naranjos"},
            response_text="",
            input_tokens=50,
            output_tokens=20,
            model="claude-haiku-test",
        )

        with patch(
            "farmafacil.bot.handler.get_setting",
            new=AsyncMock(return_value="ai_only"),
        ), patch(
            "farmafacil.bot.handler.resolve_response_mode",
            return_value="ai_only",
        ), patch(
            "farmafacil.bot.handler.resolve_chat_debug",
            return_value=False,
        ), patch(
            "farmafacil.bot.handler.classify_with_tools",
            new=AsyncMock(return_value=tool_result),
        ), patch(
            "farmafacil.bot.handler._resolve_location",
            new=AsyncMock(return_value=_LOW_CONF),
        ), patch(
            "farmafacil.bot.handler._name_matches_query",
            return_value=False,
        ), patch(
            "farmafacil.bot.handler.update_user_location",
            new=AsyncMock(),
        ) as mock_update, patch(
            "farmafacil.bot.handler.send_text_message",
            new=AsyncMock(),
        ) as mock_send, patch(
            "farmafacil.bot.handler.send_read_receipt",
            new=AsyncMock(),
        ), patch(
            "farmafacil.bot.handler.increment_token_usage",
            new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "estoy en Los Naranjos")

        mock_update.assert_not_awaited()

        sent = mock_send.await_args_list[-1].args[1]
        assert "*1.*" in sent
        assert "Otra ubicación" in sent

    @pytest.mark.asyncio
    async def test_ai_only_no_location_falls_back(self):
        """In AI-only mode, change_location without location
        should prompt for location."""
        phone = "5499910400012"
        await _seed_user(phone, zone_name="El Cafetal")

        from farmafacil.services.ai_responder import ToolUseResult

        tool_result = ToolUseResult(
            tool_name="change_location",
            tool_input={},
            response_text="",
            input_tokens=50,
            output_tokens=20,
            model="claude-haiku-test",
        )

        with patch(
            "farmafacil.bot.handler.get_setting",
            new=AsyncMock(return_value="ai_only"),
        ), patch(
            "farmafacil.bot.handler.resolve_response_mode",
            return_value="ai_only",
        ), patch(
            "farmafacil.bot.handler.resolve_chat_debug",
            return_value=False,
        ), patch(
            "farmafacil.bot.handler.classify_with_tools",
            new=AsyncMock(return_value=tool_result),
        ), patch(
            "farmafacil.bot.handler.send_text_message",
            new=AsyncMock(),
        ) as mock_send, patch(
            "farmafacil.bot.handler.send_read_receipt",
            new=AsyncMock(),
        ), patch(
            "farmafacil.bot.handler.increment_token_usage",
            new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "quiero cambiar mi zona")

        user = await _fetch_user(phone)
        assert user.onboarding_step == "awaiting_location"

        sent = mock_send.await_args_list[-1].args[1]
        assert "zona" in sent.lower() or "barrio" in sent.lower()

    @pytest.mark.asyncio
    async def test_ai_only_geocode_not_found(self):
        """In AI-only mode, failed geocode should send error."""
        phone = "5499910400013"
        await _seed_user(phone, zone_name="El Cafetal")

        from farmafacil.services.ai_responder import ToolUseResult

        tool_result = ToolUseResult(
            tool_name="change_location",
            tool_input={"location": "Nowhereistan"},
            response_text="",
            input_tokens=50,
            output_tokens=20,
            model="claude-haiku-test",
        )

        with patch(
            "farmafacil.bot.handler.get_setting",
            new=AsyncMock(return_value="ai_only"),
        ), patch(
            "farmafacil.bot.handler.resolve_response_mode",
            return_value="ai_only",
        ), patch(
            "farmafacil.bot.handler.resolve_chat_debug",
            return_value=False,
        ), patch(
            "farmafacil.bot.handler.classify_with_tools",
            new=AsyncMock(return_value=tool_result),
        ), patch(
            "farmafacil.bot.handler._resolve_location",
            new=AsyncMock(return_value=None),
        ), patch(
            "farmafacil.bot.handler.send_text_message",
            new=AsyncMock(),
        ) as mock_send, patch(
            "farmafacil.bot.handler.send_read_receipt",
            new=AsyncMock(),
        ), patch(
            "farmafacil.bot.handler.increment_token_usage",
            new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "soy de Nowhereistan")

        user = await _fetch_user(phone)
        assert user.zone_name == "El Cafetal"  # unchanged

        sent = mock_send.await_args_list[-1].args[1]
        assert "ubicar" in sent.lower() or "encontr" in sent.lower()


# ===========================================================================
# AI classifier prompt coverage
# ===========================================================================


class TestClassifyInstructionsIncludesLocationChange:
    """Verify the AI classifier prompt includes location_change."""

    def test_location_change_in_action_list(self):
        """The ACTION format line must include location_change."""
        from farmafacil.services.ai_responder import CLASSIFY_INSTRUCTIONS

        assert "location_change" in CLASSIFY_INSTRUCTIONS

    def test_location_change_rule_present(self):
        """There must be a rule explaining when to use location_change."""
        from farmafacil.services.ai_responder import CLASSIFY_INSTRUCTIONS

        assert "CAMBIO DE UBICACIÓN" in CLASSIFY_INSTRUCTIONS

    def test_disambiguation_guidance_present(self):
        """The prompt must explain how to distinguish location_change
        from drug_search with a temp location."""
        from farmafacil.services.ai_responder import CLASSIFY_INSTRUCTIONS

        # Must mention that drug_search with LOCATION is temporary
        assert "búsqueda temporal" in CLASSIFY_INSTRUCTIONS.lower()


# ===========================================================================
# Alternatives pick-a-number for already-onboarded user
# ===========================================================================


class TestAlternativesPickForOnboardedUser:
    """When a fully-onboarded user triggers alternatives via inline
    location_change, picking a number should send the update confirmation
    (MSG_LOCATION_UPDATED), NOT the onboarding MSG_READY."""

    @pytest.mark.asyncio
    async def test_pick_number_sends_update_not_ready(self):
        """An onboarded user picking option 1 from alternatives should
        see 'actualizada' not 'Ya estás configurado'."""
        from farmafacil.bot.handler import (
            _stash_location_confirm,
            handle_incoming_message,
        )

        phone = "5499910400020"
        # Fully onboarded user with an existing location
        await _seed_user(
            phone, zone_name="El Cafetal",
            latitude=10.45, longitude=-66.85,
        )

        # Simulate: the user previously got alternatives shown
        # (step is awaiting_location_confirm, candidates stashed)
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.phone_number == phone)
            )
            row = result.scalar_one()
            row.onboarding_step = "awaiting_location_confirm"
            await session.commit()

        candidates = [
            {
                "lat": 10.49,
                "lng": -66.85,
                "zone_name": "Baruta",
                "city_code": "CCS",
                "display_name": "Baruta, Caracas, Venezuela",
            },
            {
                "lat": 10.50,
                "lng": -66.90,
                "zone_name": "Baruta del Sur",
                "city_code": "CCS",
                "display_name": "Baruta del Sur, Miranda, Venezuela",
            },
        ]
        _stash_location_confirm(phone, candidates)

        intent = Intent(action="unknown")

        with patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send, patch.object(
            handler, "send_read_receipt", new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "1")

        # Should save the picked location
        user = await _fetch_user(phone)
        assert user.zone_name == "Baruta"

        # Confirmation must say "actualizada", NOT "Ya estás configurado"
        sent = mock_send.await_args_list[-1].args[1]
        assert "actualizada" in sent
        assert "Baruta" in sent
        assert "Ya estás configurado" not in sent

    @pytest.mark.asyncio
    async def test_onboarding_pick_still_sends_ready(self):
        """A user in first-time onboarding (no latitude) picking option 1
        should still get the onboarding MSG_READY."""
        from farmafacil.bot.handler import (
            _stash_location_confirm,
            handle_incoming_message,
        )

        phone = "5499910400021"
        # User in onboarding — has name but NO location yet
        await get_or_create_user(phone)
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.phone_number == phone)
            )
            row = result.scalar_one()
            row.name = "Carolina"
            row.onboarding_step = "awaiting_location_confirm"
            row.latitude = None
            row.longitude = None
            row.zone_name = None
            await session.commit()

        candidates = [
            {
                "lat": 10.49,
                "lng": -66.85,
                "zone_name": "Baruta",
                "city_code": "CCS",
                "display_name": "Baruta, Caracas, Venezuela",
            },
        ]
        _stash_location_confirm(phone, candidates)

        intent = Intent(action="unknown")

        with patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send, patch.object(
            handler, "send_read_receipt", new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "1")

        user = await _fetch_user(phone)
        assert user.zone_name == "Baruta"

        sent = mock_send.await_args_list[-1].args[1]
        # Onboarding user should get MSG_READY
        assert "configurado" in sent.lower() or "listo" in sent.lower()


# ===========================================================================
# Helper function extraction test
# ===========================================================================


class TestHandleLocationChangeHelper:
    """Verify the _handle_location_change helper exists and is callable."""

    def test_helper_exists(self):
        """_handle_location_change should be importable from handler."""
        from farmafacil.bot.handler import _handle_location_change
        import inspect
        assert inspect.iscoroutinefunction(_handle_location_change)
