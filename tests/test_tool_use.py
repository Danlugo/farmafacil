"""Tests for AI-only tool_use architecture (Item 105, v0.30.0).

In AI-only mode the bot sends Anthropic tool definitions to the API and
the model decides which tool to call — no text-based classification,
no if/elif routing chain.  These tests verify:

1. Tool definitions structure (valid Anthropic format)
2. classify_with_tools() response parsing
3. _dispatch_tool_use() routing for each tool type
4. Fallback behaviour on API errors
5. Handler integration (end-to-end AI-only mode with tool_use)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from farmafacil.services.ai_responder import (
    TOOL_DEFINITIONS,
    TOOL_USE_INSTRUCTIONS,
    ToolUseResult,
    classify_with_tools,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockUser:
    """Minimal User stand-in for handler tests."""

    def __init__(self, **overrides):
        self.id = 1
        self.name = "TestUser"
        self.phone_number = "5559930001"
        self.latitude = 10.43
        self.longitude = -66.86
        self.zone_name = "La Boyera"
        self.city_code = "CCS"
        self.display_preference = "grid"
        self.response_mode = None
        self.chat_debug = None
        self.onboarding_step = None
        self.last_search_query = "losartan"
        self.last_search_log_id = 42
        self.awaiting_clarification_context = None
        self.awaiting_category_search = None
        self.chat_admin = False
        self.admin_mode_active = False
        for key, val in overrides.items():
            setattr(self, key, val)


def _make_tool_use_response(tool_name: str, tool_input: dict):
    """Build a mock Anthropic response with a tool_use content block."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = tool_name
    tool_block.input = tool_input

    usage = MagicMock()
    usage.input_tokens = 150
    usage.output_tokens = 30

    response = MagicMock()
    response.content = [tool_block]
    response.usage = usage
    response.stop_reason = "tool_use"
    return response


def _make_text_response(text: str):
    """Build a mock Anthropic response with a text content block (no tool)."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text

    usage = MagicMock()
    usage.input_tokens = 100
    usage.output_tokens = 40

    response = MagicMock()
    response.content = [text_block]
    response.usage = usage
    response.stop_reason = "end_turn"
    return response


# ===========================================================================
# 1. Tool definition structure tests
# ===========================================================================

class TestToolDefinitions:
    """Verify TOOL_DEFINITIONS are valid Anthropic tool schemas."""

    def test_tool_definitions_is_list(self):
        assert isinstance(TOOL_DEFINITIONS, list)
        assert len(TOOL_DEFINITIONS) == 11  # 8 original + change_name + lookup_store + get_cheapest

    def test_each_tool_has_required_fields(self):
        for tool in TOOL_DEFINITIONS:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool {tool.get('name')} missing 'description'"
            assert "input_schema" in tool, f"Tool {tool['name']} missing 'input_schema'"
            schema = tool["input_schema"]
            assert schema.get("type") == "object", (
                f"Tool {tool['name']} input_schema type must be 'object'"
            )
            assert "properties" in schema, (
                f"Tool {tool['name']} input_schema missing 'properties'"
            )

    def test_tool_names_are_unique(self):
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert len(names) == len(set(names)), f"Duplicate tool names: {names}"

    def test_expected_tool_names(self):
        names = {t["name"] for t in TOOL_DEFINITIONS}
        expected = {
            "search_drug", "change_location", "find_nearest_stores",
            "view_similar", "ask_clarification", "report_emergency",
            "show_help", "general_reply", "change_name", "lookup_store",
            "get_cheapest",
        }
        assert names == expected

    def test_search_drug_has_query_required(self):
        search = next(t for t in TOOL_DEFINITIONS if t["name"] == "search_drug")
        assert "query" in search["input_schema"]["properties"]
        assert "query" in search["input_schema"].get("required", [])

    def test_ask_clarification_has_required_fields(self):
        clarify = next(t for t in TOOL_DEFINITIONS if t["name"] == "ask_clarification")
        required = clarify["input_schema"].get("required", [])
        assert "question" in required
        assert "context" in required

    def test_tool_use_instructions_is_nonempty(self):
        assert isinstance(TOOL_USE_INSTRUCTIONS, str)
        assert len(TOOL_USE_INSTRUCTIONS) > 50


# ===========================================================================
# 2. classify_with_tools() parsing tests
# ===========================================================================

class TestClassifyWithTools:
    """Test classify_with_tools() response parsing."""

    @pytest.mark.asyncio
    async def test_returns_tool_name_and_input(self):
        """When model calls a tool, ToolUseResult has the tool name and args."""
        mock_response = _make_tool_use_response(
            "search_drug", {"query": "losartan", "best_price": False},
        )
        with (
            patch("farmafacil.services.ai_responder.get_role", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.ai_responder.get_memory", new=AsyncMock(return_value="")),
            patch("farmafacil.services.ai_responder._get_user_profile", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.ai_responder.resolve_user_model", new=AsyncMock(return_value="claude-haiku-3")),
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "test-key"),
            patch("farmafacil.services.ai_responder._get_client") as mock_client,
        ):
            mock_client.return_value.messages.create = AsyncMock(return_value=mock_response)
            result = await classify_with_tools("losartan", user_id=1, user_name="Test")

        assert isinstance(result, ToolUseResult)
        assert result.tool_name == "search_drug"
        assert result.tool_input["query"] == "losartan"
        assert result.input_tokens == 150
        assert result.output_tokens == 30
        assert result.model == "claude-haiku-3"

    @pytest.mark.asyncio
    async def test_text_response_becomes_general_reply(self):
        """When model returns text without calling a tool, treat as general_reply."""
        mock_response = _make_text_response("Hola! ¿En qué puedo ayudarte?")
        with (
            patch("farmafacil.services.ai_responder.get_role", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.ai_responder.get_memory", new=AsyncMock(return_value="")),
            patch("farmafacil.services.ai_responder._get_user_profile", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.ai_responder.resolve_user_model", new=AsyncMock(return_value="claude-haiku-3")),
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "test-key"),
            patch("farmafacil.services.ai_responder._get_client") as mock_client,
        ):
            mock_client.return_value.messages.create = AsyncMock(return_value=mock_response)
            result = await classify_with_tools("hola", user_id=1, user_name="Test")

        assert result.tool_name == "general_reply"
        assert "ayudarte" in result.tool_input["message"]
        assert result.response_text == "Hola! ¿En qué puedo ayudarte?"

    @pytest.mark.asyncio
    async def test_no_api_key_fallback(self):
        """Without ANTHROPIC_API_KEY, falls back to search_drug."""
        with (
            patch("farmafacil.services.ai_responder.get_role", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.ai_responder.get_memory", new=AsyncMock(return_value="")),
            patch("farmafacil.services.ai_responder._get_user_profile", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", ""),
        ):
            result = await classify_with_tools("losartan", user_id=1, user_name="Test")

        assert result.tool_name == "search_drug"
        assert result.tool_input["query"] == "losartan"

    @pytest.mark.asyncio
    async def test_api_error_fallback(self):
        """On API error, falls back to search_drug."""
        from anthropic import APIConnectionError

        with (
            patch("farmafacil.services.ai_responder.get_role", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.ai_responder.get_memory", new=AsyncMock(return_value="")),
            patch("farmafacil.services.ai_responder._get_user_profile", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.ai_responder.resolve_user_model", new=AsyncMock(return_value="claude-haiku-3")),
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "test-key"),
            patch("farmafacil.services.ai_responder._get_client") as mock_client,
        ):
            mock_client.return_value.messages.create = AsyncMock(
                side_effect=APIConnectionError(request=MagicMock()),
            )
            result = await classify_with_tools("losartan", user_id=1, user_name="Test")

        assert result.tool_name == "search_drug"
        assert result.tool_input["query"] == "losartan"

    @pytest.mark.asyncio
    async def test_change_location_tool(self):
        """Model calls change_location tool."""
        mock_response = _make_tool_use_response(
            "change_location", {"location": "Baruta"},
        )
        with (
            patch("farmafacil.services.ai_responder.get_role", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.ai_responder.get_memory", new=AsyncMock(return_value="")),
            patch("farmafacil.services.ai_responder._get_user_profile", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.ai_responder.resolve_user_model", new=AsyncMock(return_value="claude-haiku-3")),
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "test-key"),
            patch("farmafacil.services.ai_responder._get_client") as mock_client,
        ):
            mock_client.return_value.messages.create = AsyncMock(return_value=mock_response)
            result = await classify_with_tools("vivo en Baruta", user_id=1, user_name="Test")

        assert result.tool_name == "change_location"
        assert result.tool_input["location"] == "Baruta"

    @pytest.mark.asyncio
    async def test_emergency_tool(self):
        """Model calls report_emergency tool."""
        mock_response = _make_tool_use_response(
            "report_emergency",
            {"message": "🚨 Llama al 911 inmediatamente."},
        )
        with (
            patch("farmafacil.services.ai_responder.get_role", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.ai_responder.get_memory", new=AsyncMock(return_value="")),
            patch("farmafacil.services.ai_responder._get_user_profile", new=AsyncMock(return_value=None)),
            patch("farmafacil.services.ai_responder.resolve_user_model", new=AsyncMock(return_value="claude-haiku-3")),
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "test-key"),
            patch("farmafacil.services.ai_responder._get_client") as mock_client,
        ):
            mock_client.return_value.messages.create = AsyncMock(return_value=mock_response)
            result = await classify_with_tools("no puedo respirar", user_id=1, user_name="Test")

        assert result.tool_name == "report_emergency"
        assert "911" in result.tool_input["message"]


# ===========================================================================
# 3. _dispatch_tool_use() routing tests
# ===========================================================================

class TestDispatchToolUse:
    """Test that _dispatch_tool_use routes each tool to the correct handler."""

    @pytest.mark.asyncio
    async def test_search_drug_dispatches(self):
        """search_drug tool calls _handle_drug_search."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="search_drug",
            tool_input={"query": "losartan"},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()

        with (
            patch("farmafacil.bot.handler._handle_drug_search", new=AsyncMock()) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()),
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="losartan", debug_on=False,
            )

        mock_search.assert_awaited_once()
        call_kwargs = mock_search.await_args
        assert call_kwargs.args[2] == "losartan"  # query

    @pytest.mark.asyncio
    async def test_search_drug_with_best_price(self):
        """search_drug with best_price=true passes the flag."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="search_drug",
            tool_input={"query": "losartan", "best_price": True},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()

        with (
            patch("farmafacil.bot.handler._handle_drug_search", new=AsyncMock()) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()),
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="losartan mas barato", debug_on=False,
            )

        mock_search.assert_awaited_once()
        assert mock_search.await_args.kwargs["best_price"] is True

    @pytest.mark.asyncio
    async def test_search_drug_with_preamble(self):
        """search_drug with preamble sends text before search."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="search_drug",
            tool_input={
                "query": "aspirina",
                "preamble": "Entiendo que tienes dolor de cabeza.",
            },
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()

        with (
            patch("farmafacil.bot.handler._handle_drug_search", new=AsyncMock()),
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="busca aspirina", debug_on=False,
            )

        # Preamble should be sent before the search
        first_msg = mock_send.await_args_list[0].args[1]
        assert "dolor de cabeza" in first_msg

    @pytest.mark.asyncio
    async def test_search_drug_with_temp_location(self):
        """search_drug with location geocodes and passes temp_location."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="search_drug",
            tool_input={"query": "losartan", "location": "Chacao"},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()
        geo = {"lat": 10.50, "lng": -66.85, "zone_name": "Chacao", "city": "CCS"}

        with (
            patch("farmafacil.bot.handler._handle_drug_search", new=AsyncMock()) as mock_search,
            patch("farmafacil.bot.handler.geocode_zone", new=AsyncMock(return_value=geo)),
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()),
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="busca losartan en Chacao", debug_on=False,
            )

        mock_search.assert_awaited_once()
        assert mock_search.await_args.kwargs["temp_location"] == geo

    @pytest.mark.asyncio
    async def test_search_drug_no_location_prompts(self):
        """search_drug with no user location asks for location."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="search_drug",
            tool_input={"query": "losartan"},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()
        user.latitude = None
        user.longitude = None

        with (
            patch("farmafacil.bot.handler.set_onboarding_step", new=AsyncMock()) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="losartan", debug_on=False,
            )

        mock_step.assert_awaited_once_with("5559930001", "awaiting_location")

    @pytest.mark.asyncio
    async def test_change_location_dispatches(self):
        """change_location tool calls _handle_location_change."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="change_location",
            tool_input={"location": "Baruta"},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()

        with (
            patch("farmafacil.bot.handler._handle_location_change", new=AsyncMock()) as mock_loc,
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="vivo en Baruta", debug_on=False,
            )

        mock_loc.assert_awaited_once_with("5559930001", "Baruta")

    @pytest.mark.asyncio
    async def test_change_location_no_location(self):
        """change_location without location falls back to two-step prompt."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="change_location",
            tool_input={},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()

        with (
            patch("farmafacil.bot.handler._handle_location_change", new=AsyncMock()) as mock_loc,
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="cambiar zona", debug_on=False,
            )

        mock_loc.assert_awaited_once_with("5559930001", None)

    @pytest.mark.asyncio
    async def test_find_nearest_stores_dispatches(self):
        """find_nearest_stores tool calls _handle_nearest_store."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="find_nearest_stores",
            tool_input={},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()

        with (
            patch("farmafacil.bot.handler._handle_nearest_store", new=AsyncMock()) as mock_store,
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()),
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="farmacias cercanas", debug_on=False,
            )

        mock_store.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_view_similar_dispatches(self):
        """view_similar tool calls _handle_view_similar."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="view_similar",
            tool_input={},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()

        with (
            patch("farmafacil.bot.handler._handle_view_similar", new=AsyncMock()) as mock_similar,
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="ver similares", debug_on=False,
            )

        mock_similar.assert_awaited_once_with("5559930001", user)

    @pytest.mark.asyncio
    async def test_ask_clarification_dispatches(self):
        """ask_clarification tool sets context and sends question."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="ask_clarification",
            tool_input={
                "question": "¿Qué tipo de dolor?",
                "context": "dolor",
            },
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()

        with (
            patch("farmafacil.bot.handler.set_awaiting_clarification", new=AsyncMock()) as mock_clarify,
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
            patch("farmafacil.bot.handler._update_memory_safe", new=AsyncMock()),
            patch("farmafacil.bot.handler._build_debug", new=AsyncMock(return_value="")),
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="dolor", debug_on=False,
            )

        mock_clarify.assert_awaited_once_with("5559930001", "dolor")
        sent = mock_send.await_args.args[1]
        assert "tipo de dolor" in sent

    @pytest.mark.asyncio
    async def test_report_emergency_dispatches(self):
        """report_emergency tool sends emergency message."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="report_emergency",
            tool_input={"message": "🚨 Llama al 911 inmediatamente."},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()

        with (
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
            patch("farmafacil.bot.handler._build_debug", new=AsyncMock(return_value="")),
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="no puedo respirar", debug_on=False,
            )

        sent = mock_send.await_args.args[1]
        assert "911" in sent

    @pytest.mark.asyncio
    async def test_report_emergency_default_message(self):
        """report_emergency with no message uses default."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="report_emergency",
            tool_input={},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()

        with (
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
            patch("farmafacil.bot.handler._build_debug", new=AsyncMock(return_value="")),
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="emergencia", debug_on=False,
            )

        sent = mock_send.await_args.args[1]
        assert "911" in sent
        assert "emergencia" in sent.lower()

    @pytest.mark.asyncio
    async def test_show_help_dispatches(self):
        """show_help tool sends the HELP_MESSAGE."""
        from farmafacil.bot.handler import _dispatch_tool_use
        from farmafacil.services.intent import HELP_MESSAGE

        tool_result = ToolUseResult(
            tool_name="show_help",
            tool_input={},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()

        with (
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="ayuda", debug_on=False,
            )

        mock_send.assert_awaited_once_with("5559930001", HELP_MESSAGE)

    @pytest.mark.asyncio
    async def test_general_reply_dispatches(self):
        """general_reply tool sends the AI's conversational response."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="general_reply",
            tool_input={"message": "¡Hola! ¿Qué producto buscas hoy?"},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()

        with (
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
            patch("farmafacil.bot.handler._update_memory_safe", new=AsyncMock()),
            patch("farmafacil.bot.handler._build_debug", new=AsyncMock(return_value="")),
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="hola", debug_on=False,
            )

        sent = mock_send.await_args.args[1]
        assert "producto buscas" in sent

    @pytest.mark.asyncio
    async def test_general_reply_fallback_to_generate(self):
        """general_reply with empty message generates a full AI response."""
        from farmafacil.bot.handler import _dispatch_tool_use
        from farmafacil.services.ai_responder import AiResponse

        tool_result = ToolUseResult(
            tool_name="general_reply",
            tool_input={},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()

        mock_generate = AiResponse(
            text="Soy FarmaFacil, ¿en qué puedo ayudarte?",
            role_used="pharmacy_advisor",
            input_tokens=50, output_tokens=20, model="haiku",
        )

        with (
            patch("farmafacil.bot.handler.generate_response", new=AsyncMock(return_value=mock_generate)),
            patch("farmafacil.bot.handler.increment_token_usage", new=AsyncMock()),
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
            patch("farmafacil.bot.handler._update_memory_safe", new=AsyncMock()),
            patch("farmafacil.bot.handler._build_debug", new=AsyncMock(return_value="")),
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="hola", debug_on=False,
            )

        sent = mock_send.await_args.args[1]
        assert "FarmaFacil" in sent

    @pytest.mark.asyncio
    async def test_unknown_tool_falls_to_general_reply(self):
        """Unknown tool name falls through to general_reply path."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="nonexistent_tool",
            tool_input={"message": "Algo salió bien igual."},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        user = MockUser()

        with (
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
            patch("farmafacil.bot.handler._update_memory_safe", new=AsyncMock()),
            patch("farmafacil.bot.handler._build_debug", new=AsyncMock(return_value="")),
        ):
            await _dispatch_tool_use(
                "5559930001", user, "TestUser", tool_result,
                text="algo raro", debug_on=False,
            )

        # Should still send a message (not crash)
        mock_send.assert_awaited()


# ===========================================================================
# 4. End-to-end handler integration test
# ===========================================================================

class TestHandlerToolUseIntegration:
    """Test that handle_incoming_message in ai_only mode uses tool_use."""

    @pytest.mark.asyncio
    async def test_ai_only_mode_uses_classify_with_tools(self):
        """AI-only mode calls classify_with_tools, not classify_with_ai."""
        tool_result = ToolUseResult(
            tool_name="search_drug",
            tool_input={"query": "losartan"},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )
        mock_search_response = MagicMock()
        mock_search_response.results = []
        mock_search_response.total = 0
        mock_search_response.failed_pharmacies = []

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new=AsyncMock(return_value=MockUser())),
            patch("farmafacil.bot.handler.validate_user_profile", new=AsyncMock(return_value=MockUser())),
            patch("farmafacil.bot.handler.send_read_receipt", new=AsyncMock()),
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()),
            patch("farmafacil.bot.handler.send_image_message", new=AsyncMock()),
            patch("farmafacil.bot.handler.classify_with_tools", new=AsyncMock(return_value=tool_result)) as mock_tools,
            patch("farmafacil.bot.handler.classify_with_ai") as mock_ai,
            patch("farmafacil.bot.handler.increment_token_usage", new=AsyncMock()),
            patch("farmafacil.bot.handler.search_drug", new=AsyncMock(return_value=mock_search_response)),
            patch("farmafacil.bot.handler.log_search", new=AsyncMock(return_value=1)),
            patch("farmafacil.bot.handler.update_last_search", new=AsyncMock()),
            patch("farmafacil.bot.handler.set_onboarding_step", new=AsyncMock()),
            patch("farmafacil.bot.handler._update_memory_safe", new=AsyncMock()),
            patch("farmafacil.bot.handler.get_setting", new=AsyncMock(return_value="ai_only")),
            patch("farmafacil.bot.handler.resolve_response_mode", return_value="ai_only"),
            patch("farmafacil.bot.handler.resolve_chat_debug", return_value=False),
            patch("farmafacil.bot.handler.get_memory", new=AsyncMock(return_value="")),
            patch("farmafacil.bot.handler.extract_medications_from_memory", return_value=[]),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message("5559930001", "losartan")

        # classify_with_tools was called
        mock_tools.assert_awaited_once()
        # classify_with_ai was NOT called
        mock_ai.assert_not_called()

    @pytest.mark.asyncio
    async def test_ai_only_emergency_sends_message(self):
        """AI-only mode with report_emergency sends emergency text."""
        tool_result = ToolUseResult(
            tool_name="report_emergency",
            tool_input={"message": "🚨 Llama al 911."},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new=AsyncMock(return_value=MockUser())),
            patch("farmafacil.bot.handler.validate_user_profile", new=AsyncMock(return_value=MockUser())),
            patch("farmafacil.bot.handler.send_read_receipt", new=AsyncMock()),
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
            patch("farmafacil.bot.handler.classify_with_tools", new=AsyncMock(return_value=tool_result)),
            patch("farmafacil.bot.handler.increment_token_usage", new=AsyncMock()),
            patch("farmafacil.bot.handler.get_setting", new=AsyncMock(return_value="ai_only")),
            patch("farmafacil.bot.handler.resolve_response_mode", return_value="ai_only"),
            patch("farmafacil.bot.handler.resolve_chat_debug", return_value=False),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message("5559930001", "no puedo respirar")

        # Check that 911 message was sent
        sent_messages = [call.args[1] for call in mock_send.await_args_list]
        assert any("911" in msg for msg in sent_messages)


# ===========================================================================
# 5. New tool definitions tests (Items 106-107, v0.31.0)
# ===========================================================================


class TestNewToolDefinitions:
    """Test that change_name and lookup_store tools have valid schemas."""

    def test_change_name_tool_exists(self):
        names = {t["name"] for t in TOOL_DEFINITIONS}
        assert "change_name" in names

    def test_change_name_has_name_property(self):
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "change_name")
        assert "name" in tool["input_schema"]["properties"]

    def test_lookup_store_tool_exists(self):
        names = {t["name"] for t in TOOL_DEFINITIONS}
        assert "lookup_store" in names

    def test_lookup_store_has_store_name_required(self):
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "lookup_store")
        assert "store_name" in tool["input_schema"]["properties"]
        assert "store_name" in tool["input_schema"].get("required", [])

    def test_lookup_store_has_chain_property(self):
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "lookup_store")
        assert "chain" in tool["input_schema"]["properties"]


# ===========================================================================
# 6. _dispatch_tool_use() for new tools (Items 106-107, v0.31.0)
# ===========================================================================


class TestDispatchNewTools:
    """Test dispatch routing for change_name and lookup_store tools."""

    @pytest.mark.asyncio
    async def test_change_name_with_name(self):
        """change_name with a name calls update_user_name and confirms."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="change_name",
            tool_input={"name": "Pedro"},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )

        with (
            patch("farmafacil.bot.handler.update_user_name", new=AsyncMock()) as mock_update,
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
        ):
            await _dispatch_tool_use(
                "5559930001", MockUser(), "TestUser", tool_result,
                text="me llamo Pedro", debug_on=False,
            )

        mock_update.assert_awaited_once_with("5559930001", "Pedro")
        sent = mock_send.await_args.args[1]
        assert "Pedro" in sent

    @pytest.mark.asyncio
    async def test_change_name_empty_prompts(self):
        """change_name without a name prompts user."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="change_name",
            tool_input={},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )

        with (
            patch("farmafacil.bot.handler.set_onboarding_step", new=AsyncMock()) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
        ):
            await _dispatch_tool_use(
                "5559930001", MockUser(), "TestUser", tool_result,
                text="cambiar nombre", debug_on=False,
            )

        mock_step.assert_awaited_once_with("5559930001", "awaiting_name")

    @pytest.mark.asyncio
    async def test_change_name_invalid(self):
        """change_name with invalid name (digits) prompts user."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="change_name",
            tool_input={"name": "12345"},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )

        with (
            patch("farmafacil.bot.handler.set_onboarding_step", new=AsyncMock()) as mock_step,
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
        ):
            await _dispatch_tool_use(
                "5559930001", MockUser(), "TestUser", tool_result,
                text="me llamo 12345", debug_on=False,
            )

        mock_step.assert_awaited_once_with("5559930001", "awaiting_name")
        sent = mock_send.await_args.args[1]
        assert "nombre" in sent.lower()

    @pytest.mark.asyncio
    async def test_lookup_store_found(self):
        """lookup_store with a known store shows info."""
        from farmafacil.bot.handler import _dispatch_tool_use

        mock_store = MagicMock()
        mock_store.pharmacy_chain = "Farmatodo"
        mock_store.name = "TEPUY"
        mock_store.address = "Av. Principal"
        mock_store.city_code = "CCS"
        mock_store.latitude = 10.43
        mock_store.longitude = -66.86

        tool_result = ToolUseResult(
            tool_name="lookup_store",
            tool_input={"store_name": "TEPUY"},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )

        with (
            patch("farmafacil.bot.handler.lookup_store", new=AsyncMock(return_value=mock_store)),
            patch("farmafacil.bot.handler.format_store_info", return_value="🏥 *Farmatodo TEPUY*\n📍 Av. Principal"),
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
        ):
            await _dispatch_tool_use(
                "5559930001", MockUser(), "TestUser", tool_result,
                text="donde queda TEPUY", debug_on=False,
            )

        sent = mock_send.await_args.args[1]
        assert "TEPUY" in sent

    @pytest.mark.asyncio
    async def test_lookup_store_not_found(self):
        """lookup_store with unknown store shows friendly message."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="lookup_store",
            tool_input={"store_name": "INVENTADA"},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )

        with (
            patch("farmafacil.bot.handler.lookup_store", new=AsyncMock(return_value=None)),
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
        ):
            await _dispatch_tool_use(
                "5559930001", MockUser(), "TestUser", tool_result,
                text="donde queda INVENTADA", debug_on=False,
            )

        sent = mock_send.await_args.args[1]
        assert "No encontré" in sent
        assert "INVENTADA" in sent

    @pytest.mark.asyncio
    async def test_lookup_store_with_chain(self):
        """lookup_store passes chain filter when provided."""
        from farmafacil.bot.handler import _dispatch_tool_use

        tool_result = ToolUseResult(
            tool_name="lookup_store",
            tool_input={"store_name": "La Boyera", "chain": "Farmatodo"},
            response_text="",
            input_tokens=100, output_tokens=30, model="haiku",
        )

        with (
            patch("farmafacil.bot.handler.lookup_store", new=AsyncMock(return_value=None)) as mock_lookup,
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()),
        ):
            await _dispatch_tool_use(
                "5559930001", MockUser(), "TestUser", tool_result,
                text="farmacia farmatodo la boyera", debug_on=False,
            )

        mock_lookup.assert_awaited_once_with("La Boyera", chain="Farmatodo")


# ===========================================================================
# 7. AI search result validation tests (Item 108, v0.31.0)
# ===========================================================================


class TestValidateSearchResults:
    """Test validate_search_results() AI filter."""

    @pytest.mark.asyncio
    async def test_returns_all_when_no_api_key(self):
        """No API key — returns results unchanged."""
        from farmafacil.services.ai_responder import validate_search_results

        results = [MagicMock(drug_name="Losartan 50mg", drug_class="ANTIHIPERTENSIVOS", brand="Genfar")]
        with patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", ""):
            filtered, in_tok, out_tok, model = await validate_search_results("losartan", results)

        assert filtered is results
        assert in_tok == 0
        assert model == ""

    @pytest.mark.asyncio
    async def test_returns_all_when_empty_results(self):
        """Empty results list — returns as-is."""
        from farmafacil.services.ai_responder import validate_search_results

        filtered, in_tok, out_tok, model = await validate_search_results("losartan", [])
        assert filtered == []
        assert in_tok == 0
        assert model == ""

    @pytest.mark.asyncio
    async def test_filters_irrelevant_products(self):
        """AI removes products that don't match the query."""
        from farmafacil.services.ai_responder import validate_search_results

        results = [
            MagicMock(drug_name="Crema Queloides", drug_class="DERMATOLOGICOS", brand="Lab X"),
            MagicMock(drug_name="Desodorante Dove Crema", drug_class="DESODORANTES", brand="Dove"),
            MagicMock(drug_name="Crema Cicatricure", drug_class="DERMATOLOGICOS", brand="Genomma"),
        ]

        # Mock AI response: keep indices 0 and 2, remove 1
        filter_block = MagicMock()
        filter_block.type = "tool_use"
        filter_block.name = "filter_results"
        filter_block.input = {"keep_indices": [0, 2], "note": "Removed deodorant"}

        usage = MagicMock()
        usage.input_tokens = 200
        usage.output_tokens = 40

        mock_response = MagicMock()
        mock_response.content = [filter_block]
        mock_response.usage = usage

        with (
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "test-key"),
            patch("farmafacil.services.ai_responder.resolve_user_model", new=AsyncMock(return_value="claude-haiku-3")),
            patch("farmafacil.services.ai_responder._get_client") as mock_client,
        ):
            mock_client.return_value.messages.create = AsyncMock(return_value=mock_response)
            filtered, in_tok, out_tok, model = await validate_search_results(
                "crema para queloides", results,
            )

        assert len(filtered) == 2
        assert filtered[0].drug_name == "Crema Queloides"
        assert filtered[1].drug_name == "Crema Cicatricure"
        assert in_tok == 200
        assert out_tok == 40
        assert model == "claude-haiku-3"

    @pytest.mark.asyncio
    async def test_keeps_all_when_ai_says_all_relevant(self):
        """AI keeps everything — no filtering."""
        from farmafacil.services.ai_responder import validate_search_results

        results = [
            MagicMock(drug_name="Losartan 50mg", drug_class="ANTIHIPERTENSIVOS", brand="A"),
            MagicMock(drug_name="Losartan 100mg", drug_class="ANTIHIPERTENSIVOS", brand="B"),
        ]

        filter_block = MagicMock()
        filter_block.type = "tool_use"
        filter_block.name = "filter_results"
        filter_block.input = {"keep_indices": [0, 1], "note": ""}

        usage = MagicMock()
        usage.input_tokens = 150
        usage.output_tokens = 20

        mock_response = MagicMock()
        mock_response.content = [filter_block]
        mock_response.usage = usage

        with (
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "test-key"),
            patch("farmafacil.services.ai_responder.resolve_user_model", new=AsyncMock(return_value="claude-haiku-3")),
            patch("farmafacil.services.ai_responder._get_client") as mock_client,
        ):
            mock_client.return_value.messages.create = AsyncMock(return_value=mock_response)
            filtered, in_tok, out_tok, model = await validate_search_results("losartan", results)

        assert len(filtered) == 2
        assert filtered is results  # same object — no filtering needed
        assert model == "claude-haiku-3"

    @pytest.mark.asyncio
    async def test_safety_net_returns_originals_when_all_removed(self):
        """If AI removes ALL results, return originals as safety net."""
        from farmafacil.services.ai_responder import validate_search_results

        results = [
            MagicMock(drug_name="Something", drug_class="X", brand="Y"),
        ]

        filter_block = MagicMock()
        filter_block.type = "tool_use"
        filter_block.name = "filter_results"
        filter_block.input = {"keep_indices": [], "note": "Nothing relevant"}

        usage = MagicMock()
        usage.input_tokens = 100
        usage.output_tokens = 20

        mock_response = MagicMock()
        mock_response.content = [filter_block]
        mock_response.usage = usage

        with (
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "test-key"),
            patch("farmafacil.services.ai_responder.resolve_user_model", new=AsyncMock(return_value="claude-haiku-3")),
            patch("farmafacil.services.ai_responder._get_client") as mock_client,
        ):
            mock_client.return_value.messages.create = AsyncMock(return_value=mock_response)
            filtered, in_tok, out_tok, model = await validate_search_results("x", results)

        assert filtered is results  # safety net — return originals
        assert model == "claude-haiku-3"

    @pytest.mark.asyncio
    async def test_api_error_returns_originals(self):
        """On API error, return all results unfiltered."""
        from farmafacil.services.ai_responder import validate_search_results
        from anthropic import APIConnectionError

        results = [
            MagicMock(drug_name="Losartan", drug_class="X", brand="Y"),
        ]

        with (
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "test-key"),
            patch("farmafacil.services.ai_responder.resolve_user_model", new=AsyncMock(return_value="claude-haiku-3")),
            patch("farmafacil.services.ai_responder._get_client") as mock_client,
        ):
            mock_client.return_value.messages.create = AsyncMock(
                side_effect=APIConnectionError(request=MagicMock()),
            )
            filtered, in_tok, out_tok, model = await validate_search_results("losartan", results)

        assert filtered is results
        assert in_tok == 0
        assert model == ""

    @pytest.mark.asyncio
    async def test_invalid_indices_are_filtered(self):
        """Indices out of range are silently ignored."""
        from farmafacil.services.ai_responder import validate_search_results

        results = [
            MagicMock(drug_name="A", drug_class="X", brand="Y"),
            MagicMock(drug_name="B", drug_class="X", brand="Y"),
        ]

        filter_block = MagicMock()
        filter_block.type = "tool_use"
        filter_block.name = "filter_results"
        filter_block.input = {"keep_indices": [0, 5, -1, 1], "note": ""}  # 5 and -1 are invalid

        usage = MagicMock()
        usage.input_tokens = 100
        usage.output_tokens = 20

        mock_response = MagicMock()
        mock_response.content = [filter_block]
        mock_response.usage = usage

        with (
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "test-key"),
            patch("farmafacil.services.ai_responder.resolve_user_model", new=AsyncMock(return_value="claude-haiku-3")),
            patch("farmafacil.services.ai_responder._get_client") as mock_client,
        ):
            mock_client.return_value.messages.create = AsyncMock(return_value=mock_response)
            filtered, in_tok, out_tok, model = await validate_search_results("test", results)

        # Only valid indices 0 and 1 kept — same as all results
        assert len(filtered) == 2
        assert model == "claude-haiku-3"

    @pytest.mark.asyncio
    async def test_duplicate_indices_are_deduplicated(self):
        """AI returns duplicate indices — they are deduplicated."""
        from farmafacil.services.ai_responder import validate_search_results

        results = [
            MagicMock(drug_name="A", drug_class="X", brand="Y"),
            MagicMock(drug_name="B", drug_class="X", brand="Y"),
            MagicMock(drug_name="C", drug_class="X", brand="Y"),
        ]

        filter_block = MagicMock()
        filter_block.type = "tool_use"
        filter_block.name = "filter_results"
        filter_block.input = {"keep_indices": [0, 0, 1], "note": ""}

        usage = MagicMock()
        usage.input_tokens = 100
        usage.output_tokens = 20

        mock_response = MagicMock()
        mock_response.content = [filter_block]
        mock_response.usage = usage

        with (
            patch("farmafacil.services.ai_responder.ANTHROPIC_API_KEY", "test-key"),
            patch("farmafacil.services.ai_responder.resolve_user_model", new=AsyncMock(return_value="claude-haiku-3")),
            patch("farmafacil.services.ai_responder._get_client") as mock_client,
        ):
            mock_client.return_value.messages.create = AsyncMock(return_value=mock_response)
            filtered, in_tok, out_tok, model = await validate_search_results("test", results)

        # [0, 0, 1] deduplicated to [0, 1] — 2 unique results, not 3
        assert len(filtered) == 2
        assert filtered[0].drug_name == "A"
        assert filtered[1].drug_name == "B"


# ---------------------------------------------------------------------------
# Phase 19 — Item 109: get_cheapest tool
# ---------------------------------------------------------------------------

class TestGetCheapestTool:
    """Tests for get_cheapest tool definition and dispatch."""

    def test_get_cheapest_tool_exists(self):
        """get_cheapest tool is defined in TOOL_DEFINITIONS."""
        names = {t["name"] for t in TOOL_DEFINITIONS}
        assert "get_cheapest" in names

    def test_get_cheapest_has_empty_required(self):
        """get_cheapest takes no required parameters."""
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "get_cheapest")
        assert tool["input_schema"]["required"] == []

    @pytest.mark.asyncio
    async def test_dispatch_get_cheapest_with_last_search(self):
        """get_cheapest re-runs last search with best_price=True."""
        user = MockUser(last_search_query="losartan")
        tool_result = ToolUseResult(
            tool_name="get_cheapest",
            tool_input={},
            response_text="",
        )

        with (
            patch("farmafacil.bot.handler._handle_drug_search", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
        ):
            from farmafacil.bot.handler import _dispatch_tool_use
            await _dispatch_tool_use("1234", user, "Test", tool_result, text="el más barato")

        mock_search.assert_awaited_once()
        call_kwargs = mock_search.call_args
        assert mock_search.call_args.kwargs["best_price"] is True

    @pytest.mark.asyncio
    async def test_dispatch_get_cheapest_no_last_search(self):
        """get_cheapest with no previous search tells user to search first."""
        user = MockUser(last_search_query=None)
        tool_result = ToolUseResult(
            tool_name="get_cheapest",
            tool_input={},
            response_text="",
        )

        with (
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
        ):
            from farmafacil.bot.handler import _dispatch_tool_use
            await _dispatch_tool_use("1234", user, "Test", tool_result, text="el más barato")

        mock_send.assert_awaited_once()
        msg = mock_send.call_args[0][1]
        assert "búsqueda reciente" in msg

    @pytest.mark.asyncio
    async def test_dispatch_get_cheapest_success_delegates_memory(self):
        """get_cheapest success path delegates memory update to _handle_drug_search."""
        user = MockUser(last_search_query="losartan")
        tool_result = ToolUseResult(
            tool_name="get_cheapest",
            tool_input={},
            response_text="",
        )

        with (
            patch("farmafacil.bot.handler._handle_drug_search", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock) as mock_mem,
        ):
            from farmafacil.bot.handler import _dispatch_tool_use
            await _dispatch_tool_use("1234", user, "Test", tool_result, text="el más barato")

        # _handle_drug_search was called (it handles memory internally)
        mock_search.assert_awaited_once()
        # _update_memory_safe NOT called directly — delegated to sub-handler
        mock_mem.assert_not_awaited()


# ---------------------------------------------------------------------------
# Phase 19 — Item 110: find_nearest_stores limit parameter
# ---------------------------------------------------------------------------

class TestFindNearestStoresLimit:
    """Tests for find_nearest_stores limit parameter."""

    def test_find_nearest_has_limit_property(self):
        """find_nearest_stores tool has a limit property."""
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "find_nearest_stores")
        assert "limit" in tool["input_schema"]["properties"]

    @pytest.mark.asyncio
    async def test_dispatch_nearest_stores_with_limit_1(self):
        """find_nearest_stores with limit=1 passes max_stores=1."""
        user = MockUser()
        tool_result = ToolUseResult(
            tool_name="find_nearest_stores",
            tool_input={"limit": 1},
            response_text="",
        )

        with (
            patch("farmafacil.bot.handler._handle_nearest_store", new_callable=AsyncMock) as mock_store,
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
        ):
            from farmafacil.bot.handler import _dispatch_tool_use
            await _dispatch_tool_use("1234", user, "Test", tool_result, text="la más cercana")

        mock_store.assert_awaited_once()
        assert mock_store.call_args.kwargs.get("max_stores") == 1

    @pytest.mark.asyncio
    async def test_dispatch_nearest_stores_default_limit(self):
        """find_nearest_stores with no limit uses default 5."""
        user = MockUser()
        tool_result = ToolUseResult(
            tool_name="find_nearest_stores",
            tool_input={},
            response_text="",
        )

        with (
            patch("farmafacil.bot.handler._handle_nearest_store", new_callable=AsyncMock) as mock_store,
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
        ):
            from farmafacil.bot.handler import _dispatch_tool_use
            await _dispatch_tool_use("1234", user, "Test", tool_result, text="farmacias cercanas")

        mock_store.assert_awaited_once()
        assert mock_store.call_args.kwargs.get("max_stores") == 5

    @pytest.mark.asyncio
    async def test_dispatch_nearest_stores_invalid_limit(self):
        """Invalid limit (negative, string) falls back to 5."""
        user = MockUser()
        tool_result = ToolUseResult(
            tool_name="find_nearest_stores",
            tool_input={"limit": -1},
            response_text="",
        )

        with (
            patch("farmafacil.bot.handler._handle_nearest_store", new_callable=AsyncMock) as mock_store,
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=user),
        ):
            from farmafacil.bot.handler import _dispatch_tool_use
            await _dispatch_tool_use("1234", user, "Test", tool_result, text="farmacias cercanas")

        mock_store.assert_awaited_once()
        assert mock_store.call_args.kwargs.get("max_stores") == 5


# ---------------------------------------------------------------------------
# Phase 19 — Item 111: Memory updates on all dispatch branches
# ---------------------------------------------------------------------------

class TestDispatchMemoryUpdates:
    """Verify _update_memory_safe is called across all dispatch branches."""

    @pytest.mark.asyncio
    async def test_change_location_updates_memory(self):
        """change_location dispatch calls _update_memory_safe."""
        user = MockUser()
        tool_result = ToolUseResult(
            tool_name="change_location",
            tool_input={"location": "Chacao"},
            response_text="",
        )

        with (
            patch("farmafacil.bot.handler._handle_location_change", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock) as mock_mem,
        ):
            from farmafacil.bot.handler import _dispatch_tool_use
            await _dispatch_tool_use("1234", user, "Test", tool_result, text="me mudé a Chacao")

        mock_mem.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lookup_store_updates_memory(self):
        """lookup_store dispatch calls _update_memory_safe."""
        user = MockUser()
        tool_result = ToolUseResult(
            tool_name="lookup_store",
            tool_input={"store_name": "TEPUY"},
            response_text="",
        )

        mock_store = MagicMock()
        mock_store.pharmacy_chain = "Farmatodo"
        mock_store.name = "TEPUY"
        mock_store.address = "Av. Baralt"
        mock_store.latitude = 10.5
        mock_store.longitude = -66.9
        mock_store.city = "Caracas"
        mock_store.state = "Miranda"

        with (
            patch("farmafacil.bot.handler.lookup_store", new_callable=AsyncMock, return_value=mock_store),
            patch("farmafacil.bot.handler.format_store_info", return_value="Farmatodo TEPUY - Av. Baralt"),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock) as mock_mem,
        ):
            from farmafacil.bot.handler import _dispatch_tool_use
            await _dispatch_tool_use("1234", user, "Test", tool_result, text="donde queda tepuy")

        mock_mem.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_report_emergency_updates_memory(self):
        """report_emergency dispatch calls _update_memory_safe."""
        user = MockUser()
        tool_result = ToolUseResult(
            tool_name="report_emergency",
            tool_input={"message": "Llama al 911"},
            response_text="",
        )

        with (
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock) as mock_mem,
        ):
            from farmafacil.bot.handler import _dispatch_tool_use
            await _dispatch_tool_use("1234", user, "Test", tool_result, text="me estoy ahogando")

        mock_mem.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_show_help_updates_memory(self):
        """show_help dispatch calls _update_memory_safe."""
        user = MockUser()
        tool_result = ToolUseResult(
            tool_name="show_help",
            tool_input={},
            response_text="",
        )

        with (
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock) as mock_mem,
        ):
            from farmafacil.bot.handler import _dispatch_tool_use
            await _dispatch_tool_use("1234", user, "Test", tool_result, text="ayuda")

        mock_mem.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_view_similar_updates_memory(self):
        """view_similar dispatch calls _update_memory_safe."""
        user = MockUser(last_search_query="losartan")
        tool_result = ToolUseResult(
            tool_name="view_similar",
            tool_input={},
            response_text="",
        )

        with (
            patch("farmafacil.bot.handler._handle_view_similar", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock) as mock_mem,
        ):
            from farmafacil.bot.handler import _dispatch_tool_use
            await _dispatch_tool_use("1234", user, "Test", tool_result, text="ver similares")

        mock_mem.assert_awaited_once()
