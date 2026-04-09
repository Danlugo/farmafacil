"""Tests for nearest pharmacy store feature (Item 27)."""

from unittest.mock import AsyncMock, patch

import pytest

from farmafacil.bot.formatter import format_nearby_stores
from farmafacil.services.ai_responder import _parse_structured_response
from farmafacil.services.store_locations import get_all_nearby_stores


# ── Formatter Tests ──────────────────────────────────────────────────────


class TestFormatNearbyStores:
    """Test WhatsApp formatting for nearby store results."""

    def test_formats_multiple_stores(self):
        """Multiple stores are formatted with numbering and distance."""
        stores = [
            {
                "store_name": "Farmatodo La Boyera",
                "address": "C.C. La Boyera, Nivel PB",
                "distance_km": 1.2,
                "pharmacy_chain": "Farmatodo",
            },
            {
                "store_name": "SAAS El Hatillo",
                "address": "Av. Principal El Hatillo",
                "distance_km": 3.5,
                "pharmacy_chain": "Farmacias SAAS",
            },
        ]
        result = format_nearby_stores(stores, zone_name="La Boyera")
        assert "Farmacias cercanas" in result
        assert "La Boyera" in result
        assert "1." in result
        assert "Farmatodo La Boyera" in result
        assert "1.2 km" in result
        assert "2." in result
        assert "SAAS El Hatillo" in result
        assert "3.5 km" in result

    def test_formats_with_address(self):
        """Store address is shown when available."""
        stores = [
            {
                "store_name": "Locatel Chacao",
                "address": "Av. Francisco de Miranda",
                "distance_km": 2.0,
                "pharmacy_chain": "Locatel",
            },
        ]
        result = format_nearby_stores(stores)
        assert "Av. Francisco de Miranda" in result

    def test_empty_stores_shows_message(self):
        """No stores returns a helpful message."""
        result = format_nearby_stores([], zone_name="Lejania")
        assert "No encontramos farmacias cercanas" in result
        assert "cambiar zona" in result

    def test_no_zone_name(self):
        """Works without zone_name."""
        stores = [
            {
                "store_name": "Test Store",
                "address": "",
                "distance_km": 1.0,
                "pharmacy_chain": "Farmatodo",
            },
        ]
        result = format_nearby_stores(stores, zone_name=None)
        assert "Farmacias cercanas" in result
        assert "cerca de" not in result

    def test_shows_pharmacy_chain(self):
        """Each store shows its pharmacy chain name."""
        stores = [
            {
                "store_name": "Store A",
                "address": "",
                "distance_km": 1.0,
                "pharmacy_chain": "Farmatodo",
            },
            {
                "store_name": "Store B",
                "address": "",
                "distance_km": 2.0,
                "pharmacy_chain": "Farmacias SAAS",
            },
        ]
        result = format_nearby_stores(stores)
        assert "Farmatodo" in result
        assert "Farmacias SAAS" in result

    def test_footer_has_search_prompt(self):
        """Footer prompts user to search for products."""
        stores = [
            {
                "store_name": "Test",
                "address": "",
                "distance_km": 1.0,
                "pharmacy_chain": "Farmatodo",
            },
        ]
        result = format_nearby_stores(stores)
        assert "producto" in result.lower()


# ── AI Classifier Tests ──────────────────────────────────────────────────


class TestNearestStoreClassification:
    """Test that AI classifier recognizes nearest_store action."""

    def test_parse_nearest_store_action(self):
        """Parser accepts nearest_store as a valid action."""
        reply = "ACTION: nearest_store\nRESPONSE: Te muestro las farmacias cercanas."
        result = _parse_structured_response(reply)
        assert result.action == "nearest_store"
        assert "farmacias cercanas" in result.text

    def test_parse_nearest_store_without_response(self):
        """Parser handles nearest_store with no RESPONSE line."""
        reply = "ACTION: nearest_store"
        result = _parse_structured_response(reply)
        assert result.action == "nearest_store"
        assert result.text == ""

    def test_nearest_store_not_confused_with_drug_search(self):
        """nearest_store action is distinct from drug_search."""
        reply = "ACTION: nearest_store"
        result = _parse_structured_response(reply)
        assert result.action != "drug_search"
        assert result.drug_query is None


# ── Store Location Service Tests ─────────────────────────────────────────


class TestGetAllNearbyStores:
    """Test the all-chain store query."""

    @pytest.mark.asyncio
    async def test_returns_stores_sorted_by_distance(self):
        """Stores are returned sorted by distance from the user."""
        stores = await get_all_nearby_stores(
            latitude=10.43, longitude=-66.86,
        )
        # Verify sorted by distance
        if len(stores) >= 2:
            for i in range(len(stores) - 1):
                assert stores[i]["distance_km"] <= stores[i + 1]["distance_km"]

    @pytest.mark.asyncio
    async def test_returns_dict_with_required_keys(self):
        """Each store dict has all required keys."""
        stores = await get_all_nearby_stores(
            latitude=10.43, longitude=-66.86,
        )
        for store in stores:
            assert "store_name" in store
            assert "address" in store
            assert "distance_km" in store
            assert "pharmacy_chain" in store

    @pytest.mark.asyncio
    async def test_max_stores_limit(self):
        """Respects max_stores parameter."""
        stores = await get_all_nearby_stores(
            latitude=10.43, longitude=-66.86,
            max_stores=2,
        )
        assert len(stores) <= 2

    @pytest.mark.asyncio
    async def test_max_distance_filter(self):
        """Only returns stores within max_distance_km."""
        stores = await get_all_nearby_stores(
            latitude=10.43, longitude=-66.86,
            max_distance_km=0.001,  # Extremely close — likely 0 results
        )
        for store in stores:
            assert store["distance_km"] <= 0.001

    @pytest.mark.asyncio
    async def test_empty_db_returns_empty(self):
        """Returns empty list when no pharmacy locations exist."""
        # Use a location far from any pharmacy
        stores = await get_all_nearby_stores(
            latitude=0.0, longitude=0.0,
            max_distance_km=1.0,
        )
        assert stores == []


# ── Integration-style Handler Tests ──────────────────────────────────────


class TestNearestStoreHandler:
    """Test the handler routing for nearest_store action."""

    @pytest.mark.asyncio
    async def test_ai_only_mode_routes_nearest_store(self):
        """In AI-only mode, nearest_store action calls _handle_nearest_store."""
        from farmafacil.services.ai_responder import AiResponse

        mock_ai = AiResponse(
            text="",
            role_used="pharmacy_advisor",
            action="nearest_store",
            input_tokens=100,
            output_tokens=50,
        )

        mock_stores = [
            {
                "store_name": "Farmatodo Test",
                "address": "Test Address",
                "distance_km": 1.0,
                "pharmacy_chain": "Farmatodo",
            },
        ]

        # Create a mock user
        class MockUser:
            id = 1
            name = "Daniel"
            phone_number = "5559927001"
            latitude = 10.43
            longitude = -66.86
            zone_name = "La Boyera"
            city_code = "CCS"
            display_preference = "grid"
            response_mode = None
            chat_debug = None
            onboarding_step = None
            last_search_query = None
            last_search_log_id = None

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new=AsyncMock(return_value=MockUser())),
            patch("farmafacil.bot.handler.validate_user_profile", new=AsyncMock(return_value=MockUser())),
            patch("farmafacil.bot.handler.send_read_receipt", new=AsyncMock()),
            patch("farmafacil.bot.handler.send_text_message", new=AsyncMock()) as mock_send,
            patch("farmafacil.bot.handler.classify_with_ai", new=AsyncMock(return_value=mock_ai)),
            patch("farmafacil.bot.handler.increment_token_usage", new=AsyncMock()),
            patch("farmafacil.bot.handler.get_all_nearby_stores", new=AsyncMock(return_value=mock_stores)),
            patch("farmafacil.bot.handler.set_onboarding_step", new=AsyncMock()),
            patch("farmafacil.bot.handler._update_memory_safe", new=AsyncMock()),
            patch("farmafacil.bot.handler.get_setting", new=AsyncMock(return_value="ai_only")),
            patch("farmafacil.bot.handler.resolve_response_mode", return_value="ai_only"),
            patch("farmafacil.bot.handler.resolve_chat_debug", return_value=False),
        ):
            from farmafacil.bot.handler import handle_incoming_message
            await handle_incoming_message("5559927001", "cual es la farmacia mas cercana")

        # Verify send_text_message was called with nearby stores format
        calls = mock_send.call_args_list
        store_msg = None
        for call in calls:
            if "Farmacias cercanas" in str(call) or "Farmatodo Test" in str(call):
                store_msg = call
                break
        assert store_msg is not None, (
            f"Expected nearby stores message, got: {[str(c) for c in calls]}"
        )
