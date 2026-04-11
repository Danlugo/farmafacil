"""Tests for the clarify_needed flow (Item 31).

Covers:
- _parse_structured_response: clarify_needed action with CLARIFY_QUESTION/CLARIFY_CONTEXT
- Intent and AiResponse dataclasses carry clarify fields
- Handler source-inspection: clarify branches are wired into both modes
- Handler integration: vague query → clarify question (state stashed),
  next reply → refined drug_search, cancelar resets state
- Escape hatch: /bug and cancel words both clear the clarification state
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import User
from farmafacil.services.ai_responder import AiResponse, _parse_structured_response
from farmafacil.services.intent import Intent
from farmafacil.services.users import set_awaiting_clarification, set_onboarding_step


# ── Parser tests ──────────────────────────────────────────────────────


class TestParseClarifyNeeded:
    """Verify _parse_structured_response handles the clarify_needed action."""

    def test_clarify_needed_full_fields(self):
        reply = (
            "ACTION: clarify_needed\n"
            "CLARIFY_QUESTION: ¿Prefieres pastillas o bebibles? ¿Es para adulto o niño?\n"
            "CLARIFY_CONTEXT: medicinas para la memoria"
        )
        result = _parse_structured_response(reply)
        assert result.action == "clarify_needed"
        assert result.clarify_question is not None
        assert "pastillas" in result.clarify_question.lower()
        assert result.clarify_context == "medicinas para la memoria"

    def test_clarify_needed_without_question_degrades_to_drug_search(self):
        """Defensive: if the LLM forgets CLARIFY_QUESTION, fall back to drug_search."""
        reply = "ACTION: clarify_needed\nCLARIFY_CONTEXT: vitaminas"
        result = _parse_structured_response(reply)
        # Degraded to drug_search so the user is not left hanging
        assert result.action == "drug_search"

    def test_clarify_needed_is_valid_action(self):
        """clarify_needed must be in the valid actions list so it isn't coerced."""
        reply = (
            "ACTION: clarify_needed\n"
            "CLARIFY_QUESTION: ¿Qué tipo de vitamina?\n"
            "CLARIFY_CONTEXT: vitaminas"
        )
        result = _parse_structured_response(reply)
        assert result.action == "clarify_needed"

    def test_specific_drug_not_clarified(self):
        """A specific drug name should pass through as drug_search, not clarify."""
        reply = "ACTION: drug_search\nDRUG: Omeprazol"
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.clarify_question is None
        assert result.clarify_context is None


# ── Dataclass tests ───────────────────────────────────────────────────


class TestClarifyDataclassFields:
    """AiResponse and Intent must expose clarify_question / clarify_context."""

    def test_airesponse_has_clarify_fields(self):
        resp = AiResponse(text="", role_used="")
        assert hasattr(resp, "clarify_question")
        assert hasattr(resp, "clarify_context")
        assert resp.clarify_question is None
        assert resp.clarify_context is None

    def test_airesponse_clarify_fields_populatable(self):
        resp = AiResponse(
            text="",
            role_used="",
            action="clarify_needed",
            clarify_question="¿Pastillas o gotas?",
            clarify_context="algo para dormir",
        )
        assert resp.clarify_question == "¿Pastillas o gotas?"
        assert resp.clarify_context == "algo para dormir"

    def test_intent_has_clarify_fields(self):
        intent = Intent(action="clarify_needed")
        assert hasattr(intent, "clarify_question")
        assert hasattr(intent, "clarify_context")
        assert intent.clarify_question is None
        assert intent.clarify_context is None

    def test_intent_clarify_fields_populatable(self):
        intent = Intent(
            action="clarify_needed",
            clarify_question="¿Para adulto o niño?",
            clarify_context="medicinas para la memoria",
        )
        assert intent.clarify_question == "¿Para adulto o niño?"
        assert intent.clarify_context == "medicinas para la memoria"


# ── Prompt-content tests ──────────────────────────────────────────────


class TestClarifyPrompt:
    """Verify the classification prompt instructs the AI on clarify_needed."""

    def test_prompt_mentions_clarify_needed_action(self):
        import inspect
        from farmafacil.services.ai_responder import classify_with_ai

        source = inspect.getsource(classify_with_ai)
        assert "clarify_needed" in source

    def test_prompt_mentions_vague_category_examples(self):
        import inspect
        from farmafacil.services.ai_responder import classify_with_ai

        source = inspect.getsource(classify_with_ai).lower()
        # Prompt should mention at least one of the canonical vague examples
        assert "memoria" in source or "vitaminas" in source or "dormir" in source

    def test_prompt_instructs_not_to_clarify_specific_drugs(self):
        import inspect
        from farmafacil.services.ai_responder import classify_with_ai

        source = inspect.getsource(classify_with_ai).lower()
        # Prompt should warn NOT to clarify specific products
        assert "no" in source and ("específico" in source or "directo" in source)


# ── Service: set_awaiting_clarification ───────────────────────────────


class TestSetAwaitingClarification:
    """The service helper should set and clear the column atomically."""

    @pytest.mark.asyncio
    async def test_set_and_clear_context(self):
        phone = "+58414cl001"
        async with async_session() as session:
            user = User(
                phone_number=phone,
                name="Test Clarify",
                latitude=10.5,
                longitude=-66.9,
                zone_name="Chacao",
                city_code="CCS",
                display_preference="grid",
            )
            session.add(user)
            await session.commit()
        # Explicitly clear onboarding step (model default is "awaiting_name")
        await set_onboarding_step(phone, None)

        await set_awaiting_clarification(phone, "medicinas para la memoria")
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.phone_number == phone)
            )
            user = result.scalar_one()
            assert user.awaiting_clarification_context == "medicinas para la memoria"

        await set_awaiting_clarification(phone, None)
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.phone_number == phone)
            )
            user = result.scalar_one()
            assert user.awaiting_clarification_context is None


# ── Handler source-inspection tests ───────────────────────────────────


class TestHandlerClarifyWiring:
    """Verify the handler source wires the clarify flow in both modes."""

    def test_handler_imports_set_awaiting_clarification(self):
        import inspect
        from farmafacil.bot import handler

        source = inspect.getsource(handler)
        assert "set_awaiting_clarification" in source

    def test_handler_references_awaiting_clarification_context(self):
        import inspect
        from farmafacil.bot.handler import handle_incoming_message

        source = inspect.getsource(handle_incoming_message)
        assert "awaiting_clarification_context" in source

    def test_handler_has_clarify_needed_branch(self):
        """Both AI-only and hybrid paths should check clarify_needed."""
        import inspect
        from farmafacil.bot.handler import handle_incoming_message

        source = inspect.getsource(handle_incoming_message)
        # Must appear at least twice: once for ai_only, once for hybrid
        assert source.count("clarify_needed") >= 2

    def test_handler_has_cancel_escape_hatch(self):
        import inspect
        from farmafacil.bot import handler

        source = inspect.getsource(handler)
        assert "_CLARIFY_CANCEL_WORDS" in source
        assert "cancelar" in source


# ── Integration tests: handler end-to-end with mocked scrapers/WA ─────


@pytest.fixture
async def clarify_user():
    """Create a fully-onboarded user for clarification integration tests."""
    phone = "+58414cl999"
    async with async_session() as session:
        # Clean up any residue from a previous run
        result = await session.execute(
            select(User).where(User.phone_number == phone)
        )
        existing = result.scalar_one_or_none()
        if existing:
            await session.delete(existing)
            await session.commit()

        user = User(
            phone_number=phone,
            name="Clarify Tester",
            latitude=10.5,
            longitude=-66.9,
            zone_name="Chacao",
            city_code="CCS",
            display_preference="grid",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    # Explicitly clear onboarding step (the column default is "awaiting_name")
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


@pytest.mark.asyncio
async def test_vague_query_asks_clarify_and_stashes_context(clarify_user):
    """Vague query → bot sends clarify question, stashes context, no scraper call."""
    from farmafacil.bot import handler

    mock_ai = AiResponse(
        text="",
        role_used="pharmacy_advisor",
        action="clarify_needed",
        clarify_question="¿Pastillas o bebibles? ¿Adulto o niño?",
        clarify_context="medicinas para la memoria",
        input_tokens=50,
        output_tokens=30,
    )

    sent_messages: list[str] = []

    async def fake_send_text(phone, text):
        sent_messages.append(text)
        return True

    with patch.object(handler, "classify_with_ai", AsyncMock(return_value=mock_ai)), \
         patch.object(handler, "send_text_message", new=AsyncMock(side_effect=fake_send_text)), \
         patch.object(handler, "search_drug", AsyncMock()) as mock_search, \
         patch.object(handler, "get_setting", AsyncMock(return_value="ai_only")):
        await handler.handle_incoming_message(
            clarify_user, "medicinas para la memoria", wa_message_id=""
        )

    # Scraper must NOT have been called
    mock_search.assert_not_called()

    # Clarify question should have been sent
    assert any("Pastillas" in m or "bebibles" in m for m in sent_messages)

    # Context should be stashed in the DB
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == clarify_user)
        )
        user = result.scalar_one()
        assert user.awaiting_clarification_context == "medicinas para la memoria"


@pytest.mark.asyncio
async def test_clarify_answer_merges_and_dispatches_search(clarify_user):
    """After clarify question, next reply should merge into a refined search."""
    from farmafacil.bot import handler
    from farmafacil.models.schemas import SearchResponse

    # Pre-stash the clarification context (simulates previous turn)
    await set_awaiting_clarification(clarify_user, "medicinas para la memoria")

    fake_response = SearchResponse(
        query="medicinas para la memoria pastillas",
        results=[],
        total=0,
        searched_pharmacies=["Farmatodo"],
    )

    sent_messages: list[str] = []

    async def fake_send_text(phone, text):
        sent_messages.append(text)
        return True

    search_calls: list[str] = []

    async def fake_search(**kwargs):
        search_calls.append(kwargs.get("query", ""))
        return fake_response

    with patch.object(handler, "send_text_message", new=AsyncMock(side_effect=fake_send_text)), \
         patch.object(handler, "search_drug", new=AsyncMock(side_effect=fake_search)), \
         patch.object(handler, "get_setting", AsyncMock(return_value="hybrid")), \
         patch.object(handler, "_send_grid_image", AsyncMock()), \
         patch.object(handler, "_send_detail_images", AsyncMock()):
        await handler.handle_incoming_message(
            clarify_user, "pastillas para adulto", wa_message_id=""
        )

    # Search should have been called with the MERGED query
    assert len(search_calls) == 1, f"Expected 1 search, got {len(search_calls)}: {search_calls}"
    merged = search_calls[0]
    assert "medicinas para la memoria" in merged
    assert "pastillas" in merged.lower()

    # Context should have been cleared
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == clarify_user)
        )
        user = result.scalar_one()
        assert user.awaiting_clarification_context is None


@pytest.mark.asyncio
async def test_cancel_word_aborts_clarification(clarify_user):
    """Typing 'cancelar' clears the state and confirms cancellation."""
    from farmafacil.bot import handler

    await set_awaiting_clarification(clarify_user, "medicinas para la memoria")

    sent_messages: list[str] = []

    async def fake_send_text(phone, text):
        sent_messages.append(text)
        return True

    with patch.object(handler, "send_text_message", new=AsyncMock(side_effect=fake_send_text)), \
         patch.object(handler, "search_drug", AsyncMock()) as mock_search, \
         patch.object(handler, "get_setting", AsyncMock(return_value="hybrid")):
        await handler.handle_incoming_message(
            clarify_user, "cancelar", wa_message_id=""
        )

    mock_search.assert_not_called()
    assert any("cancel" in m.lower() for m in sent_messages)

    # Context cleared
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == clarify_user)
        )
        user = result.scalar_one()
        assert user.awaiting_clarification_context is None


@pytest.mark.asyncio
async def test_bug_command_clears_clarify_state(clarify_user):
    """/bug command should work even while awaiting_clarification_context is set."""
    from farmafacil.bot import handler

    await set_awaiting_clarification(clarify_user, "medicinas para la memoria")

    sent_messages: list[str] = []

    async def fake_send_text(phone, text):
        sent_messages.append(text)
        return True

    with patch.object(handler, "send_text_message", new=AsyncMock(side_effect=fake_send_text)), \
         patch.object(handler, "search_drug", AsyncMock()) as mock_search, \
         patch.object(handler, "get_setting", AsyncMock(return_value="hybrid")):
        await handler.handle_incoming_message(
            clarify_user, "/bug la busqueda anterior no sirvio", wa_message_id=""
        )

    mock_search.assert_not_called()
    # Bug command should confirm registration
    assert any("Caso" in m for m in sent_messages)


@pytest.mark.asyncio
async def test_specific_drug_skips_clarification(clarify_user):
    """A specific drug name should never trigger clarify_needed."""
    from farmafacil.bot import handler
    from farmafacil.models.schemas import SearchResponse

    mock_ai = AiResponse(
        text="",
        role_used="pharmacy_advisor",
        action="drug_search",
        drug_query="Omeprazol",
        input_tokens=30,
        output_tokens=10,
    )

    async def fake_send_text(phone, text):
        pass

    async def fake_search(**kwargs):
        return SearchResponse(
            query="Omeprazol", results=[], total=0,
            searched_pharmacies=["Farmatodo"],
        )

    with patch.object(handler, "classify_with_ai", AsyncMock(return_value=mock_ai)), \
         patch.object(handler, "send_text_message", new=AsyncMock(side_effect=fake_send_text)), \
         patch.object(handler, "search_drug", new=AsyncMock(side_effect=fake_search)), \
         patch.object(handler, "get_setting", AsyncMock(return_value="ai_only")), \
         patch.object(handler, "_send_grid_image", AsyncMock()), \
         patch.object(handler, "_send_detail_images", AsyncMock()):
        await handler.handle_incoming_message(
            clarify_user, "Omeprazol", wa_message_id=""
        )

    # No clarification context should have been stashed
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == clarify_user)
        )
        user = result.scalar_one()
        assert user.awaiting_clarification_context is None
