"""Integration tests for ``handle_incoming_message`` — Item 26 (v0.13.1).

Coverage audit (2026-04-11)
---------------------------
Existing dedicated handler tests:
- ``test_location_sharing.py``     — location pin onboarding + zone updates
- ``test_feedback_suppression.py`` — ``_should_ask_feedback`` + feedback flow
- ``test_clarification.py``        — clarify_needed flow + refiner
- ``test_nearest_store.py``        — nearest_store AI-only route
- ``test_symptom_typing.py``       — read receipt + symptom response fields
- ``test_user_feedback.py``        — ``/bug`` / ``/comentario`` command parser
  + state clearing
- ``test_user_validation.py``      — ``validate_user_profile`` helper
- ``test_ai_role_scope.py``        — role prompt copy

This file targets ``handle_incoming_message`` paths that none of the above
cover directly:

- Rigid onboarding states (welcome, awaiting_name, awaiting_location,
  awaiting_preference) — happy and failure paths.
- Empty-message early return.
- ``/stats`` command branches (debug on/off).
- Hybrid-mode keyword cache routing (``location_change``, ``name_change``,
  ``preference_change``, ``farewell``).
- Hybrid-mode intent routing for ``greeting``, ``help``, and the fallback
  ``unknown`` branch.
- ``awaiting_feedback_detail`` final step.
- ``awaiting_feedback`` fall-through (text is not yes/no).

All tests patch the handler module's imported names so no WhatsApp calls,
no LLM calls, and no HTTP requests are made.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, select

from farmafacil.bot import handler
from farmafacil.bot.handler import handle_incoming_message
from farmafacil.db.session import async_session
from farmafacil.models.database import User
from farmafacil.services.intent import Intent
from farmafacil.services.users import get_or_create_user

# ---------------------------------------------------------------------------
# Shared fixture — isolate test users before and after every test.
# ---------------------------------------------------------------------------

TEST_PHONES = {
    f"5491999000{i:03d}" for i in range(30)
}


@pytest.fixture(autouse=True)
async def _cleanup_handler_test_users():
    """Wipe handler test phones before and after each test."""
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
    last_search_log_id: int | None = None,
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
        row.last_search_log_id = last_search_log_id
        await session.commit()


# ---------------------------------------------------------------------------
# Empty message early return
# ---------------------------------------------------------------------------


class TestEmptyMessage:
    """A whitespace-only message must not trigger any side effects."""

    @pytest.mark.asyncio
    async def test_empty_message_returns_silently(self):
        phone = "5491999000001"
        with patch.object(handler, "send_text_message", new=AsyncMock()) as mock_send, \
             patch.object(handler, "get_or_create_user", new=AsyncMock()) as mock_user:
            await handle_incoming_message(phone, "   ")

        mock_send.assert_not_awaited()
        mock_user.assert_not_awaited()


# ---------------------------------------------------------------------------
# Onboarding — welcome → awaiting_name transition
# ---------------------------------------------------------------------------


class TestOnboardingWelcome:
    """``welcome`` step advances to ``awaiting_name`` and sends MSG_WELCOME."""

    @pytest.mark.asyncio
    async def test_welcome_step_advances_and_sends_welcome(self):
        phone = "5491999000002"
        await _seed_user(phone, step="welcome")

        with patch.object(handler, "send_text_message", new=AsyncMock()) as mock_send:
            await handle_incoming_message(phone, "hola")

        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step == "awaiting_name"
        mock_send.assert_awaited_once()
        sent_text = mock_send.await_args.args[1]
        assert "FarmaFacil" in sent_text
        assert "Como te llamas" in sent_text


# ---------------------------------------------------------------------------
# Onboarding — awaiting_name
# ---------------------------------------------------------------------------


class TestOnboardingAwaitingName:
    """Tests for the ``awaiting_name`` branch."""

    @pytest.mark.asyncio
    async def test_greeting_reasked_for_name(self):
        """A bare greeting (no name detected) re-asks for the name."""
        phone = "5491999000003"
        await _seed_user(phone, step="awaiting_name")

        ai_result = MagicMock()
        ai_result.action = "greeting"
        ai_result.detected_name = None
        ai_result.detected_location = None
        ai_result.input_tokens = 5
        ai_result.output_tokens = 10

        with patch.object(
            handler, "classify_with_ai", new=AsyncMock(return_value=ai_result),
        ), patch.object(
            handler, "increment_token_usage", new=AsyncMock(),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "hola")

        refreshed = await _fetch_user(phone)
        assert refreshed.name is None
        assert refreshed.onboarding_step == "awaiting_name"
        sent = mock_send.await_args.args[1]
        assert "Dime tu nombre" in sent

    @pytest.mark.asyncio
    async def test_valid_name_persists_and_asks_location(self):
        phone = "5491999000004"
        await _seed_user(phone, step="awaiting_name")

        ai_result = MagicMock()
        ai_result.action = "provide_name"
        ai_result.detected_name = "Maria"
        ai_result.detected_location = None
        ai_result.input_tokens = 0
        ai_result.output_tokens = 0

        with patch.object(
            handler, "classify_with_ai", new=AsyncMock(return_value=ai_result),
        ), patch.object(
            handler, "increment_token_usage", new=AsyncMock(),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "me llamo Maria")

        refreshed = await _fetch_user(phone)
        assert refreshed.name == "Maria"
        sent = mock_send.await_args.args[1]
        assert "Maria" in sent
        assert "zona o barrio" in sent

    @pytest.mark.asyncio
    async def test_invalid_name_rejected(self):
        """Common non-name words (e.g., 'si') are rejected by ``_is_valid_name``."""
        phone = "5491999000005"
        await _seed_user(phone, step="awaiting_name")

        ai_result = MagicMock()
        ai_result.action = "provide_name"
        ai_result.detected_name = "si"  # not a real name
        ai_result.detected_location = None
        ai_result.input_tokens = 0
        ai_result.output_tokens = 0

        with patch.object(
            handler, "classify_with_ai", new=AsyncMock(return_value=ai_result),
        ), patch.object(
            handler, "increment_token_usage", new=AsyncMock(),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "si")

        refreshed = await _fetch_user(phone)
        assert refreshed.name is None
        sent = mock_send.await_args.args[1]
        assert "No logre entender tu nombre" in sent

    @pytest.mark.asyncio
    async def test_name_with_location_completes_onboarding(self):
        """When the AI extracts both name and location, onboarding is complete."""
        phone = "5491999000006"
        await _seed_user(phone, step="awaiting_name")

        ai_result = MagicMock()
        ai_result.action = "provide_name"
        ai_result.detected_name = "Carlos"
        ai_result.detected_location = "Chacao"
        ai_result.input_tokens = 0
        ai_result.output_tokens = 0

        geocoded = {
            "lat": 10.49,
            "lng": -66.85,
            "zone_name": "Chacao",
            "city": "CCS",
        }

        with patch.object(
            handler, "classify_with_ai", new=AsyncMock(return_value=ai_result),
        ), patch.object(
            handler, "increment_token_usage", new=AsyncMock(),
        ), patch.object(
            handler, "geocode_zone", new=AsyncMock(return_value=geocoded),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "soy Carlos de Chacao")

        refreshed = await _fetch_user(phone)
        assert refreshed.name == "Carlos"
        assert refreshed.zone_name == "Chacao"
        assert refreshed.city_code == "CCS"
        assert refreshed.onboarding_step is None
        sent = mock_send.await_args.args[1]
        assert "Listo" in sent or "Carlos" in sent


# ---------------------------------------------------------------------------
# Onboarding — awaiting_location (text)
# ---------------------------------------------------------------------------


class TestOnboardingAwaitingLocation:
    """Tests for the ``awaiting_location`` branch (typed zone, not pin)."""

    @pytest.mark.asyncio
    async def test_geocode_success_completes_onboarding(self):
        phone = "5491999000007"
        await _seed_user(phone, name="Maria", step="awaiting_location")

        intent = Intent(action="unknown", detected_location="La Boyera")
        geocoded = {
            "lat": 10.48,
            "lng": -66.87,
            "zone_name": "La Boyera",
            "city": "CCS",
        }

        with patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch.object(
            handler, "increment_token_usage", new=AsyncMock(),
        ), patch.object(
            handler, "geocode_zone", new=AsyncMock(return_value=geocoded),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "La Boyera")

        refreshed = await _fetch_user(phone)
        assert refreshed.zone_name == "La Boyera"
        assert refreshed.city_code == "CCS"
        assert refreshed.onboarding_step is None
        sent = mock_send.await_args.args[1]
        assert "Listo" in sent or "Maria" in sent

    @pytest.mark.asyncio
    async def test_geocode_failure_reasks(self):
        phone = "5491999000008"
        await _seed_user(phone, name="Maria", step="awaiting_location")

        intent = Intent(action="unknown", detected_location=None)

        with patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch.object(
            handler, "increment_token_usage", new=AsyncMock(),
        ), patch.object(
            handler, "geocode_zone", new=AsyncMock(return_value=None),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "xyz123")

        refreshed = await _fetch_user(phone)
        assert refreshed.zone_name is None
        assert refreshed.onboarding_step == "awaiting_location"
        sent = mock_send.await_args.args[1]
        assert "No pude encontrar" in sent or "zona" in sent.lower()


# ---------------------------------------------------------------------------
# Onboarding — awaiting_preference
# ---------------------------------------------------------------------------


class TestOnboardingAwaitingPreferenceLegacy:
    """Legacy users stuck in ``awaiting_preference`` get cleared (v0.15.2)."""

    @pytest.mark.asyncio
    async def test_legacy_preference_step_cleared(self):
        """A user stuck in awaiting_preference has step cleared to None."""
        phone = "5491999000009"
        await _seed_user(
            phone,
            name="Maria",
            step="awaiting_preference",
            latitude=10.48,
            longitude=-66.87,
            zone_name="La Boyera",
            city_code="CCS",
        )

        with patch.object(handler, "send_text_message", new=AsyncMock()), \
             patch.object(handler, "classify_intent", new=AsyncMock(
                 return_value=Intent(action="greeting"),
             )):
            await handle_incoming_message(phone, "hola")

        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step is None


# ---------------------------------------------------------------------------
# awaiting_feedback_detail — final "tell me more" step
# ---------------------------------------------------------------------------


class TestAwaitingFeedbackDetail:
    """User is in ``awaiting_feedback_detail`` after answering 'no'."""

    @pytest.mark.asyncio
    async def test_detail_recorded_and_thanks_sent(self):
        phone = "5491999000011"
        await _seed_user(
            phone,
            name="Maria",
            step="awaiting_feedback_detail",
            latitude=10.48,
            longitude=-66.87,
            zone_name="La Boyera",
            city_code="CCS",
            last_search_log_id=999,
        )

        with patch.object(
            handler, "record_feedback_detail", new=AsyncMock(),
        ) as mock_record, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "no encontré lo que buscaba")

        mock_record.assert_awaited_once()
        args = mock_record.await_args.args
        assert args[0] == 999
        assert args[1] == "no encontré lo que buscaba"

        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step is None
        mock_send.assert_awaited_once()


# ---------------------------------------------------------------------------
# awaiting_feedback fall-through
# ---------------------------------------------------------------------------


class TestAwaitingFeedbackFallthrough:
    """Message in ``awaiting_feedback`` that is NOT yes/no clears the state
    and processes the message through the normal hybrid flow."""

    @pytest.mark.asyncio
    async def test_non_yes_no_clears_state_and_falls_through(self):
        phone = "5491999000012"
        await _seed_user(
            phone,
            name="Maria",
            step="awaiting_feedback",
            latitude=10.48,
            longitude=-66.87,
            zone_name="La Boyera",
            city_code="CCS",
            last_search_log_id=777,
        )

        intent = Intent(action="help")

        with patch.object(
            handler, "parse_feedback", return_value=None,
        ), patch.object(
            handler, "_get_keyword_cache", new=AsyncMock(return_value={}),
        ), patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch.object(
            handler, "increment_token_usage", new=AsyncMock(),
        ), patch.object(
            handler, "get_setting", new=AsyncMock(return_value="hybrid"),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "ayuda")

        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step is None
        # ``help`` action sends HELP_MESSAGE
        sent = mock_send.await_args.args[1]
        assert "FarmaFacil" in sent


# ---------------------------------------------------------------------------
# /stats command
# ---------------------------------------------------------------------------


class TestStatsCommand:
    """The ``/stats`` command is gated on chat_debug being enabled."""

    @pytest.mark.asyncio
    async def test_stats_blocked_when_debug_off(self):
        phone = "5491999000013"
        await _seed_user(
            phone,
            name="Maria",
            latitude=10.48,
            longitude=-66.87,
            zone_name="La Boyera",
            city_code="CCS",
        )

        with patch.object(
            handler, "get_setting", new=AsyncMock(return_value="hybrid"),
        ), patch.object(
            handler, "resolve_chat_debug", return_value=False,
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "/stats")

        sent = mock_send.await_args.args[1]
        assert "no esta disponible" in sent.lower()

    @pytest.mark.asyncio
    async def test_stats_rendered_when_debug_on(self):
        phone = "5491999000014"
        await _seed_user(
            phone,
            name="Maria",
            latitude=10.48,
            longitude=-66.87,
            zone_name="La Boyera",
            city_code="CCS",
        )

        fake_stats = {
            "total_questions": 7,
            "total_success": 3,
            "last_tokens_in": 100,
            "last_tokens_out": 50,
            "total_tokens_in": 1000,
            "total_tokens_out": 500,
            "tokens_in_haiku": 800,
            "tokens_out_haiku": 400,
            "tokens_in_sonnet": 200,
            "tokens_out_sonnet": 100,
            "calls_haiku": 5,
            "calls_sonnet": 2,
            "global_tokens_in": 10000,
            "global_tokens_out": 5000,
            "global_tokens_in_haiku": 8000,
            "global_tokens_out_haiku": 4000,
            "global_tokens_in_sonnet": 2000,
            "global_tokens_out_sonnet": 1000,
            "global_calls_haiku": 50,
            "global_calls_sonnet": 20,
        }

        with patch.object(
            handler, "get_setting", new=AsyncMock(return_value="hybrid"),
        ), patch.object(
            handler, "resolve_chat_debug", return_value=True,
        ), patch.object(
            handler, "get_user_stats", new=AsyncMock(return_value=fake_stats),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "/stats")

        sent = mock_send.await_args.args[1]
        assert "Stats" in sent
        assert "Maria" in sent
        assert "preguntas: 7" in sent
        assert "haiku" in sent.lower()
        assert "sonnet" in sent.lower()


# ---------------------------------------------------------------------------
# Hybrid keyword cache routing
# ---------------------------------------------------------------------------


class TestHybridKeywordRouting:
    """Hybrid mode checks ``_get_keyword_cache()`` before running classify_intent."""

    @pytest.mark.asyncio
    async def test_location_change_keyword_sets_awaiting_location(self):
        phone = "5491999000015"
        await _seed_user(
            phone,
            name="Maria",
            latitude=10.48,
            longitude=-66.87,
            zone_name="La Boyera",
            city_code="CCS",
        )
        cache = {"cambiar zona": ("location_change", None)}

        with patch.object(
            handler, "get_setting", new=AsyncMock(return_value="hybrid"),
        ), patch.object(
            handler, "resolve_chat_debug", return_value=False,
        ), patch.object(
            handler, "_get_keyword_cache", new=AsyncMock(return_value=cache),
        ), patch.object(
            handler, "classify_intent", new=AsyncMock(),
        ) as mock_classify, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "cambiar zona")

        mock_classify.assert_not_awaited()  # keyword route short-circuits
        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step == "awaiting_location"
        mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_name_change_keyword_sets_awaiting_name(self):
        phone = "5491999000016"
        await _seed_user(
            phone,
            name="Maria",
            latitude=10.48,
            longitude=-66.87,
            zone_name="La Boyera",
            city_code="CCS",
        )
        cache = {"cambiar nombre": ("name_change", None)}

        with patch.object(
            handler, "get_setting", new=AsyncMock(return_value="hybrid"),
        ), patch.object(
            handler, "resolve_chat_debug", return_value=False,
        ), patch.object(
            handler, "_get_keyword_cache", new=AsyncMock(return_value=cache),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "cambiar nombre")

        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step == "awaiting_name"

    @pytest.mark.asyncio
    async def test_farewell_keyword_sends_response_verbatim(self):
        phone = "5491999000017"
        await _seed_user(
            phone,
            name="Maria",
            latitude=10.48,
            longitude=-66.87,
            zone_name="La Boyera",
            city_code="CCS",
        )
        cache = {"adios": ("farewell", "Hasta luego Maria!")}

        with patch.object(
            handler, "get_setting", new=AsyncMock(return_value="hybrid"),
        ), patch.object(
            handler, "resolve_chat_debug", return_value=False,
        ), patch.object(
            handler, "_get_keyword_cache", new=AsyncMock(return_value=cache),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "adios")

        sent = mock_send.await_args.args[1]
        assert sent == "Hasta luego Maria!"


# ---------------------------------------------------------------------------
# Hybrid intent routing — greeting / help / unknown fallback
# ---------------------------------------------------------------------------


class TestHybridIntentRouting:
    """Hybrid-mode intent routing after keyword cache miss."""

    @pytest.mark.asyncio
    async def test_greeting_intent_sends_returning_message(self):
        phone = "5491999000018"
        await _seed_user(
            phone,
            name="Maria",
            latitude=10.48,
            longitude=-66.87,
            zone_name="La Boyera",
            city_code="CCS",
        )
        intent = Intent(action="greeting")

        with patch.object(
            handler, "get_setting", new=AsyncMock(return_value="hybrid"),
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
        ) as mock_send:
            await handle_incoming_message(phone, "hola")

        sent = mock_send.await_args.args[1]
        assert "Maria" in sent
        assert "La Boyera" in sent

    @pytest.mark.asyncio
    async def test_help_intent_sends_help_message(self):
        phone = "5491999000019"
        await _seed_user(
            phone,
            name="Maria",
            latitude=10.48,
            longitude=-66.87,
            zone_name="La Boyera",
            city_code="CCS",
        )
        intent = Intent(action="help")

        with patch.object(
            handler, "get_setting", new=AsyncMock(return_value="hybrid"),
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
        ) as mock_send:
            await handle_incoming_message(phone, "ayuda")

        sent = mock_send.await_args.args[1]
        assert "FarmaFacil" in sent
        assert "Buscar producto" in sent

    @pytest.mark.asyncio
    async def test_drug_search_without_location_prompts_and_awaits_location(self):
        """Drug-search intent on an onboarded user who somehow has no lat/lng
        (e.g. after 'cambiar zona') prompts for the missing location and
        transitions back to ``awaiting_location``."""
        phone = "5491999000020"
        await _seed_user(phone, name="Maria")  # no location
        intent = Intent(action="drug_search", drug_query="losartan")

        with patch.object(
            handler, "get_setting", new=AsyncMock(return_value="hybrid"),
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
        ) as mock_send:
            await handle_incoming_message(phone, "losartan")

        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step == "awaiting_location"
        sent = mock_send.await_args.args[1]
        # MSG_NEED_LOCATION template
        assert "zona" in sent.lower() or "ubicacion" in sent.lower()

    @pytest.mark.asyncio
    async def test_unknown_intent_falls_back_to_ai_responder(self):
        """Unknown intent action -> ``generate_response`` fallback."""
        phone = "5491999000021"
        await _seed_user(
            phone,
            name="Maria",
            latitude=10.48,
            longitude=-66.87,
            zone_name="La Boyera",
            city_code="CCS",
        )
        intent = Intent(action="unknown")

        ai_fallback = MagicMock()
        ai_fallback.text = "No estoy seguro pero puedo ayudarte con..."
        ai_fallback.role_used = "pharmacy_advisor"
        ai_fallback.input_tokens = 20
        ai_fallback.output_tokens = 40

        with patch.object(
            handler, "get_setting", new=AsyncMock(return_value="hybrid"),
        ), patch.object(
            handler, "resolve_chat_debug", return_value=False,
        ), patch.object(
            handler, "_get_keyword_cache", new=AsyncMock(return_value={}),
        ), patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch.object(
            handler, "generate_response", new=AsyncMock(return_value=ai_fallback),
        ) as mock_gen, patch.object(
            handler, "increment_token_usage", new=AsyncMock(),
        ), patch.object(
            handler, "_update_memory_safe", new=AsyncMock(),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "como estas?")

        mock_gen.assert_awaited_once()
        sent = mock_send.await_args.args[1]
        assert "No estoy seguro" in sent
