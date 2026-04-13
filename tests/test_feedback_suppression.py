"""Tests for Item 34: suppress ¿Te sirvió? prompt on zero-result / total-failure.

Background (v0.12.5, 2026-04-11):
    When a drug search returned zero results OR every scraper failed, the bot
    still sent the "¿Te sirvió? (sí/no)" feedback prompt. Asking users to rate
    empty results is a UX confusion signal — it teaches users that the feedback
    prompt means "did the bot understand you?" instead of "did these results
    help?". The fix suppresses the prompt in those cases and shows a retry hint
    instead.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from farmafacil.bot import handler
from farmafacil.bot.handler import (
    MSG_ASK_FEEDBACK,
    MSG_RETRY_DIFFERENT_NAME,
    _should_ask_feedback,
)
from farmafacil.db.session import async_session
from farmafacil.models.database import User
from farmafacil.models.schemas import DrugResult, SearchResponse
from farmafacil.services.users import set_onboarding_step


# ---------------------------------------------------------------------------
# Unit tests for _should_ask_feedback (pure logic, no async, no DB)
# ---------------------------------------------------------------------------


def _make_result(name: str = "Losartan 50mg") -> DrugResult:
    """Minimal DrugResult for feedback suppression tests."""
    return DrugResult(
        drug_name=name,
        pharmacy_name="Farmatodo",
        price_bs=Decimal("100"),
        available=True,
        last_checked=datetime.now(tz=UTC),
    )


class TestShouldAskFeedbackUnit:
    """Pure-logic tests for _should_ask_feedback — no DB, no mocks."""

    def test_with_results_and_no_failures_asks_feedback(self):
        """Happy path: results present, no scraper failed → ask."""
        response = SearchResponse(
            query="losartan",
            results=[_make_result()],
            total=1,
            searched_pharmacies=["Farmatodo", "SAAS", "Locatel"],
            failed_pharmacies=[],
        )
        assert _should_ask_feedback(response) is True

    def test_zero_results_does_not_ask_feedback(self):
        """Empty results → suppress. This is the core Item 34 fix."""
        response = SearchResponse(
            query="xyznonexistent",
            results=[],
            total=0,
            searched_pharmacies=["Farmatodo", "SAAS", "Locatel"],
            failed_pharmacies=[],
        )
        assert _should_ask_feedback(response) is False

    def test_zero_results_with_partial_failure_does_not_ask(self):
        """Zero results beats partial failure — still suppress."""
        response = SearchResponse(
            query="xyz",
            results=[],
            total=0,
            searched_pharmacies=["Farmatodo", "SAAS", "Locatel"],
            failed_pharmacies=["Farmatodo"],
        )
        assert _should_ask_feedback(response) is False

    def test_partial_failure_with_results_still_asks(self):
        """1 of 3 scrapers down but results came back → still ask.
        The user has real products to rate even if coverage was incomplete."""
        response = SearchResponse(
            query="losartan",
            results=[_make_result()],
            total=1,
            searched_pharmacies=["Farmatodo", "SAAS", "Locatel"],
            failed_pharmacies=["Farmatodo"],
        )
        assert _should_ask_feedback(response) is True

    def test_multiple_partial_failures_with_results_still_asks(self):
        """2 of 3 scrapers down but 1 returned products → still ask."""
        response = SearchResponse(
            query="losartan",
            results=[_make_result()],
            total=1,
            searched_pharmacies=["Farmatodo", "SAAS", "Locatel"],
            failed_pharmacies=["Farmatodo", "SAAS"],
        )
        assert _should_ask_feedback(response) is True

    def test_all_scrapers_failed_does_not_ask(self):
        """Total outage (every active scraper raised) → suppress.
        Defensive guard for future regressions where cached/partial data
        might leak through."""
        # Simulate: every registered scraper reported a failure. We fake a
        # result list to exercise the second branch of the helper, even
        # though in real usage total failure always means zero results.
        all_pharmacies = [s.pharmacy_name for s in handler.ACTIVE_SCRAPERS]
        response = SearchResponse(
            query="losartan",
            results=[_make_result()],  # Hypothetical leaked data
            total=1,
            searched_pharmacies=all_pharmacies,
            failed_pharmacies=all_pharmacies,
        )
        assert _should_ask_feedback(response) is False

    def test_empty_failed_pharmacies_list_asks(self):
        """Empty failed list + results → ask (normal happy path)."""
        response = SearchResponse(
            query="losartan",
            results=[_make_result()],
            total=1,
            searched_pharmacies=["Farmatodo"],
        )
        assert _should_ask_feedback(response) is True


# ---------------------------------------------------------------------------
# Integration tests for _handle_drug_search feedback suppression
# ---------------------------------------------------------------------------


@pytest.fixture
async def feedback_user():
    """Create a fully-onboarded user for feedback suppression integration tests."""
    phone = "+58414fb999"
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone)
        )
        existing = result.scalar_one_or_none()
        if existing:
            await session.delete(existing)
            await session.commit()

        user = User(
            phone_number=phone,
            name="Feedback Tester",
            latitude=10.5,
            longitude=-66.9,
            zone_name="Chacao",
            city_code="CCS",
            display_preference="grid",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    await set_onboarding_step(phone, None)
    yield phone

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone)
        )
        user = result.scalar_one_or_none()
        if user:
            await session.delete(user)
            await session.commit()


async def _fetch_user(phone: str) -> User:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone)
        )
        return result.scalar_one()


@pytest.mark.asyncio
async def test_zero_results_skips_feedback_prompt(feedback_user):
    """Item 34: zero-result search → NO ¿Te sirvió? prompt, retry hint instead."""
    user = await _fetch_user(feedback_user)

    empty_response = SearchResponse(
        query="xyznonexistent",
        results=[],
        total=0,
        searched_pharmacies=["Farmatodo", "SAAS", "Locatel"],
        failed_pharmacies=[],
    )

    sent_messages: list[str] = []

    async def fake_send(phone, text):
        sent_messages.append(text)
        return True

    with patch.object(handler, "send_text_message", new=AsyncMock(side_effect=fake_send)), \
         patch.object(handler, "search_drug", new=AsyncMock(return_value=empty_response)), \
         patch.object(handler, "log_search", new=AsyncMock(return_value=1)), \
         patch.object(handler, "update_last_search", new=AsyncMock()), \
         patch.object(handler, "_update_memory_safe", new=AsyncMock()), \
         patch.object(handler, "get_memory", new=AsyncMock(return_value="")), \
         patch.object(handler, "extract_medications_from_memory", return_value=[]), \
         patch.object(handler, "set_onboarding_step", new=AsyncMock()) as mock_step, \
         patch.object(handler, "_send_detail_images", new=AsyncMock()):
        await handler._handle_drug_search(
            feedback_user, user, "xyznonexistent", "Feedback Tester"
        )

    # The feedback prompt MUST NOT have been sent.
    assert MSG_ASK_FEEDBACK not in sent_messages, \
        f"¿Te sirvió? should be suppressed on zero results. Sent: {sent_messages}"

    # The retry hint MUST have been sent instead.
    assert MSG_RETRY_DIFFERENT_NAME in sent_messages, \
        f"Retry hint should replace ¿Te sirvió?. Sent: {sent_messages}"

    # The user must NOT have been put into awaiting_feedback state —
    # otherwise the next message would be misinterpreted as feedback.
    awaiting_feedback_calls = [
        call for call in mock_step.call_args_list
        if "awaiting_feedback" in str(call)
    ]
    assert not awaiting_feedback_calls, \
        f"User should not enter awaiting_feedback on zero results. Calls: {mock_step.call_args_list}"


@pytest.mark.asyncio
async def test_results_present_still_asks_feedback(feedback_user):
    """Happy path regression: non-empty results → ¿Te sirvió? is still sent."""
    user = await _fetch_user(feedback_user)

    good_response = SearchResponse(
        query="losartan",
        results=[_make_result("Losartán 50 mg x 30")],
        total=1,
        searched_pharmacies=["Farmatodo"],
        failed_pharmacies=[],
    )

    sent_messages: list[str] = []

    async def fake_send(phone, text):
        sent_messages.append(text)
        return True

    with patch.object(handler, "send_text_message", new=AsyncMock(side_effect=fake_send)), \
         patch.object(handler, "search_drug", new=AsyncMock(return_value=good_response)), \
         patch.object(handler, "log_search", new=AsyncMock(return_value=1)), \
         patch.object(handler, "update_last_search", new=AsyncMock()), \
         patch.object(handler, "_update_memory_safe", new=AsyncMock()), \
         patch.object(handler, "get_memory", new=AsyncMock(return_value="")), \
         patch.object(handler, "extract_medications_from_memory", return_value=[]), \
         patch.object(handler, "set_onboarding_step", new=AsyncMock()) as mock_step, \
         patch.object(handler, "_send_detail_images", new=AsyncMock()):
        await handler._handle_drug_search(
            feedback_user, user, "losartan", "Feedback Tester"
        )

    assert MSG_ASK_FEEDBACK in sent_messages, \
        f"¿Te sirvió? must be sent when results are present. Sent: {sent_messages}"
    assert MSG_RETRY_DIFFERENT_NAME not in sent_messages, \
        f"Retry hint must NOT be sent when results are present. Sent: {sent_messages}"

    # User should have been put into awaiting_feedback state
    awaiting_feedback_calls = [
        call for call in mock_step.call_args_list
        if "awaiting_feedback" in str(call)
    ]
    assert len(awaiting_feedback_calls) == 1, \
        f"User should enter awaiting_feedback exactly once. Calls: {mock_step.call_args_list}"


@pytest.mark.asyncio
async def test_partial_failure_with_results_asks_feedback(feedback_user):
    """Partial failure (1 of 3 scrapers down) + results → still ask.
    The user has real products to rate; coverage gap is already shown by
    the formatter's '⚠️ No pudimos conectar con X' warning."""
    user = await _fetch_user(feedback_user)

    partial_response = SearchResponse(
        query="losartan",
        results=[_make_result("Losartán 50 mg x 30")],
        total=1,
        searched_pharmacies=["Farmatodo", "SAAS", "Locatel"],
        failed_pharmacies=["Farmatodo"],  # 1 of 3 failed
    )

    sent_messages: list[str] = []

    async def fake_send(phone, text):
        sent_messages.append(text)
        return True

    with patch.object(handler, "send_text_message", new=AsyncMock(side_effect=fake_send)), \
         patch.object(handler, "search_drug", new=AsyncMock(return_value=partial_response)), \
         patch.object(handler, "log_search", new=AsyncMock(return_value=1)), \
         patch.object(handler, "update_last_search", new=AsyncMock()), \
         patch.object(handler, "_update_memory_safe", new=AsyncMock()), \
         patch.object(handler, "get_memory", new=AsyncMock(return_value="")), \
         patch.object(handler, "extract_medications_from_memory", return_value=[]), \
         patch.object(handler, "set_onboarding_step", new=AsyncMock()), \
         patch.object(handler, "_send_detail_images", new=AsyncMock()):
        await handler._handle_drug_search(
            feedback_user, user, "losartan", "Feedback Tester"
        )

    assert MSG_ASK_FEEDBACK in sent_messages, \
        "Partial failure with results should still ask for feedback."
    assert MSG_RETRY_DIFFERENT_NAME not in sent_messages


@pytest.mark.asyncio
async def test_total_failure_zero_results_skips_feedback(feedback_user):
    """Every scraper failed → zero results → NO feedback prompt.
    This is the 'total outage' case that v0.12.1's failed_pharmacies UI
    already warns about in the result header."""
    user = await _fetch_user(feedback_user)

    all_pharmacies = [s.pharmacy_name for s in handler.ACTIVE_SCRAPERS]
    outage_response = SearchResponse(
        query="losartan",
        results=[],
        total=0,
        searched_pharmacies=all_pharmacies,
        failed_pharmacies=all_pharmacies,
    )

    sent_messages: list[str] = []

    async def fake_send(phone, text):
        sent_messages.append(text)
        return True

    with patch.object(handler, "send_text_message", new=AsyncMock(side_effect=fake_send)), \
         patch.object(handler, "search_drug", new=AsyncMock(return_value=outage_response)), \
         patch.object(handler, "log_search", new=AsyncMock(return_value=1)), \
         patch.object(handler, "update_last_search", new=AsyncMock()), \
         patch.object(handler, "_update_memory_safe", new=AsyncMock()), \
         patch.object(handler, "get_memory", new=AsyncMock(return_value="")), \
         patch.object(handler, "extract_medications_from_memory", return_value=[]), \
         patch.object(handler, "set_onboarding_step", new=AsyncMock()) as mock_step, \
         patch.object(handler, "_send_detail_images", new=AsyncMock()):
        await handler._handle_drug_search(
            feedback_user, user, "losartan", "Feedback Tester"
        )

    assert MSG_ASK_FEEDBACK not in sent_messages, \
        "Total outage should suppress ¿Te sirvió?"
    assert MSG_RETRY_DIFFERENT_NAME in sent_messages, \
        "Retry hint should replace ¿Te sirvió? on total outage."

    awaiting_feedback_calls = [
        call for call in mock_step.call_args_list
        if "awaiting_feedback" in str(call)
    ]
    assert not awaiting_feedback_calls, \
        "User should not enter awaiting_feedback on total outage."
