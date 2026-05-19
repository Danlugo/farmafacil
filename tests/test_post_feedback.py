"""Tests for post-feedback follow-up flows (v0.22.2).

After a drug search with results:
- YES feedback → offer to leave a suggestion (text or voice)
- NO feedback → offer to leave a bug report (text or voice)

Both flows support text and voice input, AI re-wording, and
skip via "no" response.  Each feature has an independent on/off
toggle in app_settings (post_feedback_suggestion / post_feedback_bug_report).

When in awaiting_post_suggestion or awaiting_post_bug states, smart
intent classification detects drug names and falls through to normal
search flow instead of saving them as suggestions/bugs.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from wtforms import SelectField

from farmafacil.services.intent import Intent

# ── Helpers ──────────────────────────────────────────────────────────────

SENDER = "14258904657"
SENDER_NAME = "Daniel"


def _make_user(
    step: str | None = None,
    last_search_log_id: int | None = 42,
):
    """Build a mock user with sensible defaults for feedback tests."""
    user = MagicMock()
    user.id = 1
    user.name = SENDER_NAME
    user.phone_number = SENDER
    user.onboarding_step = step
    user.last_search_log_id = last_search_log_id
    user.latitude = 10.43
    user.longitude = -66.85
    user.zone_name = "La Boyera"
    user.city_code = "CCS"
    user.response_mode = None
    user.chat_debug = None
    user.post_feedback_suggestion = None
    user.post_feedback_bug_report = None
    user.admin_mode_active = False
    user.awaiting_clarification = None
    user.awaiting_category_search = None
    user.last_search_query = None
    return user


_SETTING_DEFAULTS = {
    "post_feedback_suggestion": "false",
    "post_feedback_bug_report": "false",
    "response_mode": "hybrid",
    "chat_debug": "disabled",
    "category_menu_enabled": "true",
    "default_model": "haiku",
}


async def _setting_both_on(key: str) -> str:
    """Mock get_setting with both post-feedback features enabled globally."""
    if key in ("post_feedback_suggestion", "post_feedback_bug_report"):
        return "true"
    return _SETTING_DEFAULTS.get(key, "")


async def _setting_lookup(key: str) -> str:
    """Mock get_setting that returns realistic defaults (features OFF)."""
    return _SETTING_DEFAULTS.get(key, "")


# _setting_suggestion_off and _setting_bug_off are aliases for _setting_lookup
# since the global defaults are now "false" for both. Kept as named aliases
# for test readability — they make the intent explicit at the call site.
_setting_suggestion_off = _setting_lookup
_setting_bug_off = _setting_lookup


# ── YES → Suggestion offer flow ─────────────────────────────────────────


class TestYesFeedbackToSuggestionOffer:
    """When user says YES to '¿Te sirvió?', they get the suggestion offer."""

    @pytest.mark.asyncio
    async def test_yes_feedback_transitions_to_suggestion_offer(self):
        """YES feedback + feature ON (per-user) → state=awaiting_post_suggestion + offer."""
        user = _make_user(step="awaiting_feedback")
        user.post_feedback_suggestion = "true"  # per-user override ON

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.record_feedback", new_callable=AsyncMock) as mock_record,
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_setting", side_effect=_setting_lookup),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "sí")

            mock_record.assert_called_once_with(42, "yes")
            # Message sent BEFORE step transition (send-then-step pattern)
            sent_text = mock_send.call_args[0][1]
            assert "sugerencia" in sent_text.lower()
            assert "nota de voz" in sent_text.lower()
            mock_step.assert_called_once_with(SENDER, "awaiting_post_suggestion")

    @pytest.mark.asyncio
    async def test_yes_feedback_feature_off_sends_thanks(self):
        """YES feedback + feature OFF (global default false, no user override) → thanks."""
        user = _make_user(step="awaiting_feedback")
        # user.post_feedback_suggestion = None → falls through to global "false"

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.record_feedback", new_callable=AsyncMock) as mock_record,
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_setting", side_effect=_setting_suggestion_off),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "sí")

            mock_record.assert_called_once_with(42, "yes")
            mock_step.assert_called_once_with(SENDER, None)
            sent_text = mock_send.call_args[0][1]
            assert "gracias" in sent_text.lower()
            # Should NOT mention sugerencia
            assert "sugerencia" not in sent_text.lower()


class TestAwaitingPostSuggestion:
    """Tests for the awaiting_post_suggestion state."""

    @pytest.mark.asyncio
    async def test_no_skips_suggestion(self):
        """User says 'no' → clears state, sends skip message."""
        user = _make_user(step="awaiting_post_suggestion")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "no")

            mock_step.assert_called_once_with(SENDER, None)
            sent_text = mock_send.call_args[0][1]
            assert "gracias" in sent_text.lower()

    @pytest.mark.asyncio
    async def test_si_reprompts_for_content(self):
        """User says 'sí' → keeps state, re-prompts to send content."""
        user = _make_user(step="awaiting_post_suggestion")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "sí")

            # State should NOT be cleared — user still needs to type content
            mock_step.assert_not_called()
            sent_text = mock_send.call_args[0][1]
            assert "sugerencia" in sent_text.lower() or "voz" in sent_text.lower()

    @pytest.mark.asyncio
    async def test_text_creates_suggestion(self):
        """User sends actual text → reworded + saved as suggestion."""
        user = _make_user(step="awaiting_post_suggestion")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent_keywords", new_callable=AsyncMock, return_value=None),
            patch("farmafacil.bot.handler.reword_for_feedback", new_callable=AsyncMock, return_value="Agregar filtro por precio") as mock_reword,
            patch("farmafacil.bot.handler.create_suggestion", new_callable=AsyncMock, return_value=7) as mock_create,
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "seria chevere poder filtrar por precio")

            mock_reword.assert_called_once_with("seria chevere poder filtrar por precio", "sugerencia")
            mock_create.assert_called_once_with(
                user_id=1,
                phone_number=SENDER,
                message="Agregar filtro por precio",
                voice_message_id=None,
            )
            mock_step.assert_called_once_with(SENDER, None)
            sent_text = mock_send.call_args[0][1]
            assert "#7" in sent_text

    @pytest.mark.asyncio
    async def test_voice_transcription_creates_suggestion(self):
        """Voice message ID is threaded through to create_suggestion."""
        user = _make_user(step="awaiting_post_suggestion")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent_keywords", new_callable=AsyncMock, return_value=None),
            patch("farmafacil.bot.handler.reword_for_feedback", new_callable=AsyncMock, return_value="Mejorar búsqueda de genéricos"),
            patch("farmafacil.bot.handler.create_suggestion", new_callable=AsyncMock, return_value=8) as mock_create,
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(
                SENDER, "me gustaría que buscara genéricos",
                voice_message_id=55,
            )

            mock_create.assert_called_once_with(
                user_id=1,
                phone_number=SENDER,
                message="Mejorar búsqueda de genéricos",
                voice_message_id=55,
            )

    @pytest.mark.asyncio
    async def test_suggestion_save_error_handled(self):
        """If create_suggestion raises, user gets error msg and state clears."""
        user = _make_user(step="awaiting_post_suggestion")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent_keywords", new_callable=AsyncMock, return_value=None),
            patch("farmafacil.bot.handler.reword_for_feedback", new_callable=AsyncMock, return_value="test"),
            patch("farmafacil.bot.handler.create_suggestion", new_callable=AsyncMock, side_effect=Exception("DB error")),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "mi sugerencia")

            # State must still be cleared even on error
            mock_step.assert_called_once_with(SENDER, None)
            sent_text = mock_send.call_args[0][1]
            assert "no pude" in sent_text.lower() or "error" in sent_text.lower() or "inténtalo" in sent_text.lower()

    @pytest.mark.asyncio
    async def test_drug_name_falls_through_to_search(self):
        """Drug name like 'losartan' in suggestion state → normal search flow."""
        user = _make_user(step="awaiting_post_suggestion")

        drug_intent = Intent(action="drug_search", drug_query="losartan")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent_keywords", new_callable=AsyncMock, return_value=drug_intent),
            patch("farmafacil.bot.handler.reword_for_feedback", new_callable=AsyncMock) as mock_reword,
            patch("farmafacil.bot.handler.create_suggestion", new_callable=AsyncMock) as mock_create,
            # The fall-through hits normal flow — mock _handle_drug_search
            # to avoid needing full search pipeline mocking
            patch("farmafacil.bot.handler.classify_intent", new_callable=AsyncMock, return_value=Intent(action="drug_search", drug_query="losartan")),
            patch("farmafacil.bot.handler.get_setting", side_effect=_setting_lookup),
            patch("farmafacil.bot.handler._handle_drug_search", new_callable=AsyncMock) as mock_search,
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "losartan")

            # Should NOT save as suggestion
            mock_reword.assert_not_called()
            mock_create.assert_not_called()
            # State should be cleared (set to None for fall-through)
            mock_step.assert_any_call(SENDER, None)
            # Should have triggered a drug search
            mock_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_voice_drug_name_falls_through(self):
        """Voice note transcribed to drug name → search, not suggestion."""
        user = _make_user(step="awaiting_post_suggestion")

        drug_intent = Intent(action="drug_search", drug_query="acetaminofen")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent_keywords", new_callable=AsyncMock, return_value=drug_intent),
            patch("farmafacil.bot.handler.create_suggestion", new_callable=AsyncMock) as mock_create,
            patch("farmafacil.bot.handler.classify_intent", new_callable=AsyncMock, return_value=Intent(action="drug_search", drug_query="acetaminofen")),
            patch("farmafacil.bot.handler.get_setting", side_effect=_setting_lookup),
            patch("farmafacil.bot.handler._handle_drug_search", new_callable=AsyncMock) as mock_search,
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "acetaminofen", voice_message_id=70)

            # Must NOT save as suggestion
            mock_create.assert_not_called()
            mock_step.assert_any_call(SENDER, None)
            mock_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_classify_keywords_error_saves_as_suggestion(self):
        """If classify_intent_keywords raises, treat input as suggestion."""
        user = _make_user(step="awaiting_post_suggestion")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent_keywords", new_callable=AsyncMock, side_effect=Exception("DB cache error")),
            patch("farmafacil.bot.handler.reword_for_feedback", new_callable=AsyncMock, return_value="Saved anyway") as mock_reword,
            patch("farmafacil.bot.handler.create_suggestion", new_callable=AsyncMock, return_value=99) as mock_create,
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "losartan")

            # Should fall back to saving as suggestion (not crash/stuck)
            mock_reword.assert_called_once()
            mock_create.assert_called_once()
            mock_step.assert_called_once_with(SENDER, None)
            sent_text = mock_send.call_args[0][1]
            assert "#99" in sent_text


# ── NO → Bug report offer flow ──────────────────────────────────────────


class TestNoFeedbackToBugOffer:
    """When user says NO to '¿Te sirvió?', they get the bug report offer."""

    @pytest.mark.asyncio
    async def test_no_feedback_transitions_to_bug_offer(self):
        """NO feedback + feature ON (per-user) → state=awaiting_post_bug + offer."""
        user = _make_user(step="awaiting_feedback")
        user.post_feedback_bug_report = "true"  # per-user override ON

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.record_feedback", new_callable=AsyncMock) as mock_record,
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_setting", side_effect=_setting_lookup),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "no")

            mock_record.assert_called_once_with(42, "no")
            # Message sent BEFORE step transition (send-then-step pattern)
            sent_text = mock_send.call_args[0][1]
            assert "no funcionó" in sent_text.lower() or "nota de voz" in sent_text.lower()
            mock_step.assert_called_once_with(SENDER, "awaiting_post_bug")

    @pytest.mark.asyncio
    async def test_no_feedback_feature_off_goes_to_detail(self):
        """NO feedback + feature OFF (global default false, no user override) → detail."""
        user = _make_user(step="awaiting_feedback")
        # user.post_feedback_bug_report = None → falls through to global "false"

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.record_feedback", new_callable=AsyncMock) as mock_record,
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_setting", side_effect=_setting_bug_off),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "no")

            mock_record.assert_called_once_with(42, "no")
            mock_step.assert_called_once_with(SENDER, "awaiting_feedback_detail")
            sent_text = mock_send.call_args[0][1]
            # Should send the original "what went wrong?" message
            assert "buscabas" in sent_text.lower() or "estuvo mal" in sent_text.lower()


class TestAwaitingPostBug:
    """Tests for the awaiting_post_bug state."""

    @pytest.mark.asyncio
    async def test_no_skips_bug_report(self):
        """User says 'no' → clears state, sends skip message."""
        user = _make_user(step="awaiting_post_bug")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "no")

            mock_step.assert_called_once_with(SENDER, None)
            sent_text = mock_send.call_args[0][1]
            assert "gracias" in sent_text.lower()

    @pytest.mark.asyncio
    async def test_si_reprompts_for_content(self):
        """User says 'sí' → keeps state, re-prompts to send content."""
        user = _make_user(step="awaiting_post_bug")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "sí")

            mock_step.assert_not_called()
            sent_text = mock_send.call_args[0][1]
            assert "mensaje" in sent_text.lower() or "voz" in sent_text.lower()

    @pytest.mark.asyncio
    async def test_text_creates_bug_feedback(self):
        """User sends text → reworded + saved as bug in user_feedback."""
        user = _make_user(step="awaiting_post_bug")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent_keywords", new_callable=AsyncMock, return_value=None),
            patch("farmafacil.bot.handler.reword_for_feedback", new_callable=AsyncMock, return_value="La búsqueda de melatonina no mostró resultados") as mock_reword,
            patch("farmafacil.bot.handler.create_feedback", new_callable=AsyncMock, return_value=15) as mock_create,
            patch("farmafacil.bot.handler.record_feedback_detail", new_callable=AsyncMock) as mock_detail,
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "busque melatonina y no salio nada")

            mock_reword.assert_called_once_with("busque melatonina y no salio nada", "reporte de error")
            mock_create.assert_called_once_with(
                user_id=1,
                feedback_type="bug",
                message="La búsqueda de melatonina no mostró resultados",
                phone_number=SENDER,
                voice_message_id=None,
            )
            # Also records as feedback_detail on the search log
            mock_detail.assert_called_once_with(42, "La búsqueda de melatonina no mostró resultados")
            mock_step.assert_called_once_with(SENDER, None)
            sent_text = mock_send.call_args[0][1]
            assert "#15" in sent_text

    @pytest.mark.asyncio
    async def test_voice_transcription_creates_bug(self):
        """Voice message ID is threaded through to create_feedback."""
        user = _make_user(step="awaiting_post_bug")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent_keywords", new_callable=AsyncMock, return_value=None),
            patch("farmafacil.bot.handler.reword_for_feedback", new_callable=AsyncMock, return_value="No encontré resultados para aspirina"),
            patch("farmafacil.bot.handler.create_feedback", new_callable=AsyncMock, return_value=16) as mock_create,
            patch("farmafacil.bot.handler.record_feedback_detail", new_callable=AsyncMock),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(
                SENDER, "no me salieron resultados de aspirina",
                voice_message_id=60,
            )

            mock_create.assert_called_once_with(
                user_id=1,
                feedback_type="bug",
                message="No encontré resultados para aspirina",
                phone_number=SENDER,
                voice_message_id=60,
            )

    @pytest.mark.asyncio
    async def test_bug_save_error_handled(self):
        """If create_feedback raises, user gets error msg and state clears."""
        user = _make_user(step="awaiting_post_bug")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent_keywords", new_callable=AsyncMock, return_value=None),
            patch("farmafacil.bot.handler.reword_for_feedback", new_callable=AsyncMock, return_value="test"),
            patch("farmafacil.bot.handler.create_feedback", new_callable=AsyncMock, side_effect=Exception("DB error")),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "mi reporte de bug")

            mock_step.assert_called_once_with(SENDER, None)
            sent_text = mock_send.call_args[0][1]
            assert "no pude" in sent_text.lower() or "error" in sent_text.lower() or "inténtalo" in sent_text.lower()

    @pytest.mark.asyncio
    async def test_no_search_log_skips_detail_record(self):
        """If user has no last_search_log_id, feedback_detail is skipped."""
        user = _make_user(step="awaiting_post_bug", last_search_log_id=None)

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent_keywords", new_callable=AsyncMock, return_value=None),
            patch("farmafacil.bot.handler.reword_for_feedback", new_callable=AsyncMock, return_value="test bug"),
            patch("farmafacil.bot.handler.create_feedback", new_callable=AsyncMock, return_value=17),
            patch("farmafacil.bot.handler.record_feedback_detail", new_callable=AsyncMock) as mock_detail,
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "algo no funciono")

            mock_detail.assert_not_called()

    @pytest.mark.asyncio
    async def test_drug_name_falls_through_to_search(self):
        """Drug name like 'losartan' in bug state → normal search flow."""
        user = _make_user(step="awaiting_post_bug")

        drug_intent = Intent(action="drug_search", drug_query="losartan")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent_keywords", new_callable=AsyncMock, return_value=drug_intent),
            patch("farmafacil.bot.handler.reword_for_feedback", new_callable=AsyncMock) as mock_reword,
            patch("farmafacil.bot.handler.create_feedback", new_callable=AsyncMock) as mock_create,
            patch("farmafacil.bot.handler.classify_intent", new_callable=AsyncMock, return_value=Intent(action="drug_search", drug_query="losartan")),
            patch("farmafacil.bot.handler.get_setting", side_effect=_setting_lookup),
            patch("farmafacil.bot.handler._handle_drug_search", new_callable=AsyncMock) as mock_search,
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "losartan")

            # Should NOT save as bug
            mock_reword.assert_not_called()
            mock_create.assert_not_called()
            mock_step.assert_any_call(SENDER, None)
            mock_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_voice_drug_name_falls_through(self):
        """Voice note transcribed to drug name → search, not bug report."""
        user = _make_user(step="awaiting_post_bug")

        drug_intent = Intent(action="drug_search", drug_query="ibuprofeno")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent_keywords", new_callable=AsyncMock, return_value=drug_intent),
            patch("farmafacil.bot.handler.create_feedback", new_callable=AsyncMock) as mock_create,
            patch("farmafacil.bot.handler.classify_intent", new_callable=AsyncMock, return_value=Intent(action="drug_search", drug_query="ibuprofeno")),
            patch("farmafacil.bot.handler.get_setting", side_effect=_setting_lookup),
            patch("farmafacil.bot.handler._handle_drug_search", new_callable=AsyncMock) as mock_search,
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "ibuprofeno", voice_message_id=75)

            mock_create.assert_not_called()
            mock_step.assert_any_call(SENDER, None)
            mock_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_classify_keywords_error_saves_as_bug(self):
        """If classify_intent_keywords raises, treat input as bug report."""
        user = _make_user(step="awaiting_post_bug")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent_keywords", new_callable=AsyncMock, side_effect=Exception("DB cache error")),
            patch("farmafacil.bot.handler.reword_for_feedback", new_callable=AsyncMock, return_value="Saved anyway") as mock_reword,
            patch("farmafacil.bot.handler.create_feedback", new_callable=AsyncMock, return_value=99) as mock_create,
            patch("farmafacil.bot.handler.record_feedback_detail", new_callable=AsyncMock),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "ibuprofeno")

            # Should fall back to saving as bug (not crash/stuck)
            mock_reword.assert_called_once()
            mock_create.assert_called_once()
            mock_step.assert_called_once_with(SENDER, None)
            sent_text = mock_send.call_args[0][1]
            assert "#99" in sent_text

    @pytest.mark.asyncio
    async def test_non_drug_intent_still_saves_as_bug(self):
        """Non-drug intent (e.g., greeting) in bug state → saved as bug."""
        user = _make_user(step="awaiting_post_bug")

        # classify_intent_keywords returns a greeting, not drug_search
        greeting_intent = Intent(action="greeting", response_text="Hola")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.classify_intent_keywords", new_callable=AsyncMock, return_value=greeting_intent),
            patch("farmafacil.bot.handler.reword_for_feedback", new_callable=AsyncMock, return_value="No funciona la app") as mock_reword,
            patch("farmafacil.bot.handler.create_feedback", new_callable=AsyncMock, return_value=20) as mock_create,
            patch("farmafacil.bot.handler.record_feedback_detail", new_callable=AsyncMock),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "no funciona la app")

            # Non-drug intent → still treated as bug report
            mock_reword.assert_called_once()
            mock_create.assert_called_once()
            mock_step.assert_called_once_with(SENDER, None)
            sent_text = mock_send.call_args[0][1]
            assert "#20" in sent_text


# ── Escape hatch: /bug and /sugerencia clear new states ─────────────────


class TestEscapeHatchClearsNewStates:
    """Commands /bug and /sugerencia should clear the new feedback states."""

    @pytest.mark.asyncio
    async def test_bug_command_clears_awaiting_post_suggestion(self):
        """/bug clears awaiting_post_suggestion state."""
        user = _make_user(step="awaiting_post_suggestion")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.create_feedback", new_callable=AsyncMock, return_value=99),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "/bug la app no funciona")

            # First call clears the stuck state, second would be from /bug flow
            mock_step.assert_any_call(SENDER, None)

    @pytest.mark.asyncio
    async def test_sugerencia_command_clears_awaiting_post_bug(self):
        """/sugerencia clears awaiting_post_bug state."""
        user = _make_user(step="awaiting_post_bug")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.create_suggestion", new_callable=AsyncMock, return_value=99),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "/sugerencia agregar filtro")

            mock_step.assert_any_call(SENDER, None)


# ── AI re-wording tests ─────────────────────────────────────────────────


class TestRewordForFeedback:
    """Tests for the reword_for_feedback function."""

    @pytest.mark.asyncio
    async def test_reword_returns_cleaned_text(self):
        """Successful LLM call returns re-worded text."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Agregar filtro de precio")]
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 10

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with (
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "test-key"),
            patch("farmafacil.services.ai_responder.anthropic.Anthropic", return_value=mock_client),
            patch("farmafacil.services.ai_responder.resolve_user_model", new_callable=AsyncMock, return_value="claude-haiku-4-5-20251001"),
        ):
            from farmafacil.services.ai_responder import reword_for_feedback
            result = await reword_for_feedback("seria chevere que se pueda filtrar por precio")

            assert result == "Agregar filtro de precio"
            call_kwargs = mock_client.messages.create.call_args
            assert "sugerencia" in call_kwargs.kwargs["system"]

    @pytest.mark.asyncio
    async def test_reword_with_bug_type(self):
        """Passing 'reporte de error' changes the system prompt."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Error en búsqueda")]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with (
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "test-key"),
            patch("farmafacil.services.ai_responder.anthropic.Anthropic", return_value=mock_client),
            patch("farmafacil.services.ai_responder.resolve_user_model", new_callable=AsyncMock, return_value="claude-haiku-4-5-20251001"),
        ):
            from farmafacil.services.ai_responder import reword_for_feedback
            result = await reword_for_feedback("no me funciono la busqueda", "reporte de error")

            assert result == "Error en búsqueda"
            call_kwargs = mock_client.messages.create.call_args
            assert "reporte de error" in call_kwargs.kwargs["system"]

    @pytest.mark.asyncio
    async def test_reword_fallback_on_no_api_key(self):
        """Without API key, returns raw text stripped."""
        with patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", ""):
            from farmafacil.services.ai_responder import reword_for_feedback
            result = await reword_for_feedback("  mi sugerencia cruda  ")

            assert result == "mi sugerencia cruda"

    @pytest.mark.asyncio
    async def test_reword_fallback_on_api_error(self):
        """On API error, returns raw text."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API timeout")

        with (
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "test-key"),
            patch("farmafacil.services.ai_responder.anthropic.Anthropic", return_value=mock_client),
            patch("farmafacil.services.ai_responder.resolve_user_model", new_callable=AsyncMock, return_value="claude-haiku-4-5-20251001"),
        ):
            from farmafacil.services.ai_responder import reword_for_feedback
            result = await reword_for_feedback("mi texto original")

            assert result == "mi texto original"


# ── Legacy state compatibility ──────────────────────────────────────────


class TestLegacyFeedbackDetailState:
    """awaiting_feedback_detail still works for users stuck in it."""

    @pytest.mark.asyncio
    async def test_legacy_detail_still_records(self):
        """Users stuck in awaiting_feedback_detail can still submit."""
        user = _make_user(step="awaiting_feedback_detail")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.record_feedback_detail", new_callable=AsyncMock) as mock_detail,
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "la busqueda no funciono")

            mock_detail.assert_called_once_with(42, "la busqueda no funciono")
            mock_step.assert_called_once_with(SENDER, None)


# ── Admin dropdown includes new states ──────────────────────────────────


class TestAdminDropdownStates:
    """Admin form dropdown includes the new feedback follow-up states."""

    def test_onboarding_step_choices_include_new_states(self):
        from farmafacil.api.admin import USER_ONBOARDING_STEP_CHOICES

        step_values = [v for v, _ in USER_ONBOARDING_STEP_CHOICES]
        assert "awaiting_post_suggestion" in step_values
        assert "awaiting_post_bug" in step_values

    def test_legacy_detail_still_in_choices(self):
        from farmafacil.api.admin import USER_ONBOARDING_STEP_CHOICES

        step_values = [v for v, _ in USER_ONBOARDING_STEP_CHOICES]
        assert "awaiting_feedback_detail" in step_values


# ── Settings defaults exist ─────────────────────────────────────────────


class TestSettingsDefaults:
    """Verify post-feedback settings exist in DEFAULTS with proper values."""

    def test_suggestion_setting_in_defaults(self):
        from farmafacil.services.settings import DEFAULTS

        assert "post_feedback_suggestion" in DEFAULTS
        value, desc = DEFAULTS["post_feedback_suggestion"]
        assert value == "false"  # v0.22.5: default OFF, per-user override available
        assert "suggestion" in desc.lower() or "sugerencia" in desc.lower()

    def test_bug_report_setting_in_defaults(self):
        from farmafacil.services.settings import DEFAULTS

        assert "post_feedback_bug_report" in DEFAULTS
        value, desc = DEFAULTS["post_feedback_bug_report"]
        assert value == "false"  # v0.22.5: default OFF, per-user override available
        assert "bug" in desc.lower() or "report" in desc.lower()

    def test_settings_are_independent(self):
        """The two settings are distinct keys, not a shared toggle."""
        from farmafacil.services.settings import DEFAULTS

        assert "post_feedback_suggestion" != "post_feedback_bug_report"
        assert DEFAULTS["post_feedback_suggestion"] is not DEFAULTS["post_feedback_bug_report"]


# ── Per-user override resolution (v0.22.5) ────────────────────────────


class TestResolvePostFeedback:
    """Unit tests for resolve_post_feedback in settings.py."""

    def test_user_true_overrides_global_false(self):
        from farmafacil.services.settings import resolve_post_feedback

        assert resolve_post_feedback("true", "false") is True

    def test_user_false_overrides_global_true(self):
        from farmafacil.services.settings import resolve_post_feedback

        assert resolve_post_feedback("false", "true") is False

    def test_user_none_falls_through_to_global_true(self):
        from farmafacil.services.settings import resolve_post_feedback

        assert resolve_post_feedback(None, "true") is True

    def test_user_none_falls_through_to_global_false(self):
        from farmafacil.services.settings import resolve_post_feedback

        assert resolve_post_feedback(None, "false") is False

    def test_empty_string_treated_as_none(self):
        from farmafacil.services.settings import resolve_post_feedback

        assert resolve_post_feedback("", "true") is True

    def test_whitespace_treated_as_none(self):
        from farmafacil.services.settings import resolve_post_feedback

        assert resolve_post_feedback("   ", "false") is False

    def test_invalid_global_defaults_to_false(self):
        from farmafacil.services.settings import resolve_post_feedback

        assert resolve_post_feedback(None, "garbage") is False

    def test_case_insensitive_user(self):
        from farmafacil.services.settings import resolve_post_feedback

        assert resolve_post_feedback("TRUE", "false") is True
        assert resolve_post_feedback("True", "false") is True

    def test_case_insensitive_global(self):
        from farmafacil.services.settings import resolve_post_feedback

        assert resolve_post_feedback(None, "TRUE") is True


class TestPerUserSuggestionOverride:
    """Integration: per-user post_feedback_suggestion overrides global."""

    @pytest.mark.asyncio
    async def test_user_on_global_off_enables_suggestion(self):
        """user.post_feedback_suggestion='true', global='false' → offer shown."""
        user = _make_user(step="awaiting_feedback")
        user.post_feedback_suggestion = "true"

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.record_feedback", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_setting", side_effect=_setting_lookup),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "sí")

            mock_step.assert_called_once_with(SENDER, "awaiting_post_suggestion")
            sent_text = mock_send.call_args[0][1]
            assert "sugerencia" in sent_text.lower()

    @pytest.mark.asyncio
    async def test_user_off_global_on_disables_suggestion(self):
        """user.post_feedback_suggestion='false', global='true' → thanks only."""
        user = _make_user(step="awaiting_feedback")
        user.post_feedback_suggestion = "false"

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.record_feedback", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_setting", side_effect=_setting_both_on),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "sí")

            mock_step.assert_called_once_with(SENDER, None)
            sent_text = mock_send.call_args[0][1]
            assert "gracias" in sent_text.lower()
            assert "sugerencia" not in sent_text.lower()

    @pytest.mark.asyncio
    async def test_user_null_global_on_enables_suggestion(self):
        """user.post_feedback_suggestion=None, global='true' → offer shown."""
        user = _make_user(step="awaiting_feedback")
        # user.post_feedback_suggestion is already None from _make_user

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.record_feedback", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_setting", side_effect=_setting_both_on),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "sí")

            mock_step.assert_called_once_with(SENDER, "awaiting_post_suggestion")
            sent_text = mock_send.call_args[0][1]
            assert "sugerencia" in sent_text.lower()


class TestPerUserBugReportOverride:
    """Integration: per-user post_feedback_bug_report overrides global."""

    @pytest.mark.asyncio
    async def test_user_on_global_off_enables_bug_report(self):
        """user.post_feedback_bug_report='true', global='false' → offer shown."""
        user = _make_user(step="awaiting_feedback")
        user.post_feedback_bug_report = "true"

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.record_feedback", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_setting", side_effect=_setting_lookup),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "no")

            mock_step.assert_called_once_with(SENDER, "awaiting_post_bug")
            sent_text = mock_send.call_args[0][1]
            assert "nota de voz" in sent_text.lower() or "no funcionó" in sent_text.lower()

    @pytest.mark.asyncio
    async def test_user_off_global_on_disables_bug_report(self):
        """user.post_feedback_bug_report='false', global='true' → detail flow."""
        user = _make_user(step="awaiting_feedback")
        user.post_feedback_bug_report = "false"

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.record_feedback", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_setting", side_effect=_setting_both_on),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "no")

            mock_step.assert_called_once_with(SENDER, "awaiting_feedback_detail")
            sent_text = mock_send.call_args[0][1]
            assert "buscabas" in sent_text.lower() or "estuvo mal" in sent_text.lower()

    @pytest.mark.asyncio
    async def test_user_null_global_on_enables_bug_report(self):
        """user.post_feedback_bug_report=None, global='true' → offer shown."""
        user = _make_user(step="awaiting_feedback")

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.record_feedback", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_setting", side_effect=_setting_both_on),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message(SENDER, "no")

            mock_step.assert_called_once_with(SENDER, "awaiting_post_bug")


class TestAdminPostFeedbackDropdowns:
    """Admin form includes per-user post-feedback dropdowns."""

    def test_post_feedback_suggestion_in_form_overrides(self):
        from farmafacil.api.admin import UserAdmin
        assert UserAdmin.form_overrides["post_feedback_suggestion"] is SelectField

    def test_post_feedback_bug_report_in_form_overrides(self):
        from farmafacil.api.admin import UserAdmin
        assert UserAdmin.form_overrides["post_feedback_bug_report"] is SelectField

    def test_post_feedback_suggestion_choices(self):
        from farmafacil.api.admin import USER_POST_FEEDBACK_CHOICES
        values = {v for v, _ in USER_POST_FEEDBACK_CHOICES}
        assert "" in values  # "— use global —"
        assert "true" in values
        assert "false" in values

    def test_post_feedback_bug_report_choices(self):
        from farmafacil.api.admin import UserAdmin
        choices = UserAdmin.form_args["post_feedback_bug_report"]["choices"]
        values = {v for v, _ in choices}
        assert "" in values
        assert "true" in values
        assert "false" in values

    def test_coerce_is_nullable(self):
        from farmafacil.api.admin import UserAdmin
        for field in ("post_feedback_suggestion", "post_feedback_bug_report"):
            coerce = UserAdmin.form_args[field]["coerce"]
            assert coerce("") is None
            assert coerce("true") == "true"
            assert coerce("false") == "false"

    def test_post_feedback_in_column_list(self):
        from farmafacil.api.admin import UserAdmin
        col_names = {
            (col.key if hasattr(col, "key") else str(col))
            for col in UserAdmin.column_list
        }
        assert "post_feedback_suggestion" in col_names
        assert "post_feedback_bug_report" in col_names

    def test_post_feedback_in_form_columns(self):
        from farmafacil.api.admin import UserAdmin
        col_names = {
            (col.key if hasattr(col, "key") else str(col))
            for col in UserAdmin.form_columns
        }
        assert "post_feedback_suggestion" in col_names
        assert "post_feedback_bug_report" in col_names

    def test_post_feedback_in_column_labels(self):
        from farmafacil.api.admin import UserAdmin
        assert "post_feedback_suggestion" in UserAdmin.column_labels
        assert "post_feedback_bug_report" in UserAdmin.column_labels
