"""Tests for temporary location search — Requirement 4.

When a user says "busca X cerca de Y", the system should search from
location Y without permanently changing the user's saved location.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from farmafacil.bot.handler import _handle_drug_search, _handle_nearest_store


# ── _handle_drug_search with temp_location ──────────────────────────


class TestDrugSearchTempLocation:
    """Verify _handle_drug_search uses temp_location when provided."""

    @pytest.fixture
    def mock_user(self):
        """User with saved La Tahona coordinates."""
        user = MagicMock()
        user.id = 1
        user.name = "Daniel"
        user.latitude = 10.4378
        user.longitude = -66.8354
        user.zone_name = "La Tahona"
        user.city_code = "CCS"
        return user

    @pytest.fixture
    def temp_chacao(self):
        """Temporary location override for Chacao."""
        return {
            "lat": 10.4965,
            "lng": -66.8559,
            "zone_name": "Chacao",
            "city": "CCS",
        }

    @pytest.mark.asyncio
    async def test_temp_location_passed_to_search(self, mock_user, temp_chacao):
        """search_drug receives temp location coords, not user's saved ones."""
        with (
            patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=""),
            patch("farmafacil.bot.handler.log_search", new_callable=AsyncMock, return_value=1),
            patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_search_results", return_value="mock reply"),
            patch("farmafacil.bot.handler._send_detail_images", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._should_ask_feedback", return_value=False),
        ):
            # Mock search response
            mock_response = MagicMock()
            mock_response.results = []
            mock_response.failed_pharmacies = []
            mock_search.return_value = mock_response

            await _handle_drug_search(
                "584121234567", mock_user, "Enalapril", "Daniel",
                temp_location=temp_chacao,
            )

            # search_drug should use Chacao coords, not La Tahona
            mock_search.assert_called_once()
            call_kwargs = mock_search.call_args
            assert call_kwargs.kwargs["latitude"] == temp_chacao["lat"]
            assert call_kwargs.kwargs["longitude"] == temp_chacao["lng"]
            assert call_kwargs.kwargs["zone_name"] == "Chacao"
            assert call_kwargs.kwargs["city_code"] == "CCS"

    @pytest.mark.asyncio
    async def test_no_temp_location_uses_user_coords(self, mock_user):
        """Without temp_location, search_drug uses user's saved coordinates."""
        with (
            patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=""),
            patch("farmafacil.bot.handler.log_search", new_callable=AsyncMock, return_value=1),
            patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_search_results", return_value="mock reply"),
            patch("farmafacil.bot.handler._send_detail_images", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._should_ask_feedback", return_value=False),
        ):
            mock_response = MagicMock()
            mock_response.results = []
            mock_response.failed_pharmacies = []
            mock_search.return_value = mock_response

            await _handle_drug_search(
                "584121234567", mock_user, "Enalapril", "Daniel",
            )

            call_kwargs = mock_search.call_args
            assert call_kwargs.kwargs["latitude"] == mock_user.latitude
            assert call_kwargs.kwargs["longitude"] == mock_user.longitude
            assert call_kwargs.kwargs["zone_name"] == "La Tahona"

    @pytest.mark.asyncio
    async def test_temp_location_sends_info_message(self, mock_user, temp_chacao):
        """When using temp location, bot tells user their saved location is preserved."""
        with (
            patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=""),
            patch("farmafacil.bot.handler.log_search", new_callable=AsyncMock, return_value=1),
            patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_search_results", return_value="mock reply"),
            patch("farmafacil.bot.handler._send_detail_images", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._should_ask_feedback", return_value=False),
        ):
            mock_response = MagicMock()
            mock_response.results = []
            mock_response.failed_pharmacies = []
            mock_search.return_value = mock_response

            await _handle_drug_search(
                "584121234567", mock_user, "Enalapril", "Daniel",
                temp_location=temp_chacao,
            )

            # First message should mention Chacao and that home location is preserved
            first_msg = mock_send.call_args_list[0].args[1]
            assert "Chacao" in first_msg
            assert "La Tahona" in first_msg

    @pytest.mark.asyncio
    async def test_user_profile_not_modified(self, mock_user, temp_chacao):
        """Using temp_location does NOT change user object attributes
        AND update_user_location is never called (critical safety property)."""
        original_lat = mock_user.latitude
        original_lng = mock_user.longitude
        original_zone = mock_user.zone_name

        with (
            patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=""),
            patch("farmafacil.bot.handler.log_search", new_callable=AsyncMock, return_value=1),
            patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_search_results", return_value="mock reply"),
            patch("farmafacil.bot.handler._send_detail_images", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._should_ask_feedback", return_value=False),
            patch("farmafacil.bot.handler.update_user_location", new_callable=AsyncMock) as mock_update_loc,
        ):
            mock_response = MagicMock()
            mock_response.results = []
            mock_response.failed_pharmacies = []
            mock_search.return_value = mock_response

            await _handle_drug_search(
                "584121234567", mock_user, "Enalapril", "Daniel",
                temp_location=temp_chacao,
            )

        # User object unchanged
        assert mock_user.latitude == original_lat
        assert mock_user.longitude == original_lng
        assert mock_user.zone_name == original_zone
        # DB update NEVER called — the critical safety invariant
        mock_update_loc.assert_not_called()

    @pytest.mark.asyncio
    async def test_city_code_key_fallback(self, mock_user):
        """temp_location dict with 'city_code' key (instead of 'city') still works."""
        temp_loc = {
            "lat": 10.50, "lng": -66.90, "zone_name": "Altamira",
            "city_code": "CCS",  # mirrors User model attr name, not geocode_zone key
        }
        with (
            patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=""),
            patch("farmafacil.bot.handler.log_search", new_callable=AsyncMock, return_value=1),
            patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_search_results", return_value="mock reply"),
            patch("farmafacil.bot.handler._send_detail_images", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._should_ask_feedback", return_value=False),
        ):
            mock_response = MagicMock()
            mock_response.results = []
            mock_response.failed_pharmacies = []
            mock_search.return_value = mock_response

            await _handle_drug_search(
                "584121234567", mock_user, "Enalapril", "Daniel",
                temp_location=temp_loc,
            )

            call_kwargs = mock_search.call_args
            assert call_kwargs.kwargs["city_code"] == "CCS"


# ── _handle_nearest_store with temp_location ────────────────────────


class TestNearestStoreTempLocation:
    """Verify _handle_nearest_store uses temp_location when provided."""

    @pytest.fixture
    def mock_user(self):
        user = MagicMock()
        user.id = 1
        user.name = "Daniel"
        user.latitude = 10.4378
        user.longitude = -66.8354
        user.zone_name = "La Tahona"
        user.city_code = "CCS"
        return user

    @pytest.mark.asyncio
    async def test_temp_location_passed_to_nearby_stores(self, mock_user):
        """get_all_nearby_stores receives temp coords."""
        temp_loc = {"lat": 10.50, "lng": -66.90, "zone_name": "Altamira", "city": "CCS"}
        with (
            patch("farmafacil.bot.handler.get_all_nearby_stores", new_callable=AsyncMock) as mock_stores,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_nearby_stores") as mock_format,
        ):
            mock_stores.return_value = []
            mock_format.return_value = "No stores found"

            await _handle_nearest_store(
                "584121234567", mock_user, "Daniel",
                temp_location=temp_loc,
            )

            mock_stores.assert_called_once_with(
                latitude=10.50, longitude=-66.90, max_stores=5,
            )

    @pytest.mark.asyncio
    async def test_no_temp_uses_user_coords(self, mock_user):
        """Without temp_location, uses user's saved coordinates."""
        with (
            patch("farmafacil.bot.handler.get_all_nearby_stores", new_callable=AsyncMock) as mock_stores,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_nearby_stores") as mock_format,
        ):
            mock_stores.return_value = []
            mock_format.return_value = "No stores found"

            await _handle_nearest_store(
                "584121234567", mock_user, "Daniel",
            )

            mock_stores.assert_called_once_with(
                latitude=mock_user.latitude, longitude=mock_user.longitude,
                max_stores=5,
            )

    @pytest.mark.asyncio
    async def test_format_uses_temp_zone_name(self, mock_user):
        """format_nearby_stores receives the temp zone_name, not user's saved one."""
        temp_loc = {"lat": 10.50, "lng": -66.90, "zone_name": "Altamira", "city": "CCS"}
        with (
            patch("farmafacil.bot.handler.get_all_nearby_stores", new_callable=AsyncMock) as mock_stores,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_nearby_stores") as mock_format,
        ):
            mock_stores.return_value = []
            mock_format.return_value = "Stores near Altamira"

            await _handle_nearest_store(
                "584121234567", mock_user, "Daniel",
                temp_location=temp_loc,
            )

            mock_format.assert_called_once_with([], zone_name="Altamira")


# ── Geocode failure fallback ──────────────────────────────────────


class TestGeocodeFallback:
    """When geocode_zone returns None, search falls back to user's home."""

    @pytest.fixture
    def mock_user(self):
        user = MagicMock()
        user.id = 1
        user.name = "Daniel"
        user.latitude = 10.4378
        user.longitude = -66.8354
        user.zone_name = "La Tahona"
        user.city_code = "CCS"
        return user

    @pytest.mark.asyncio
    async def test_geocode_fails_uses_home_coords(self, mock_user):
        """When geocode_zone returns None, _temp_location is None → home coords used."""
        with (
            patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=""),
            patch("farmafacil.bot.handler.log_search", new_callable=AsyncMock, return_value=1),
            patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_search_results", return_value="mock reply"),
            patch("farmafacil.bot.handler._send_detail_images", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._should_ask_feedback", return_value=False),
        ):
            mock_response = MagicMock()
            mock_response.results = []
            mock_response.failed_pharmacies = []
            mock_search.return_value = mock_response

            # Explicitly pass temp_location=None (simulates geocode failure)
            await _handle_drug_search(
                "584121234567", mock_user, "Enalapril", "Daniel",
                temp_location=None,
            )

            call_kwargs = mock_search.call_args
            assert call_kwargs.kwargs["latitude"] == mock_user.latitude
            assert call_kwargs.kwargs["longitude"] == mock_user.longitude
            assert call_kwargs.kwargs["zone_name"] == "La Tahona"
            assert call_kwargs.kwargs["city_code"] == "CCS"

    @pytest.mark.asyncio
    async def test_geocode_fails_no_info_message_sent(self, mock_user):
        """When temp_location is None, no 'searching from X' message is sent."""
        with (
            patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=""),
            patch("farmafacil.bot.handler.log_search", new_callable=AsyncMock, return_value=1),
            patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_search_results", return_value="mock reply"),
            patch("farmafacil.bot.handler._send_detail_images", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._should_ask_feedback", return_value=False),
        ):
            mock_response = MagicMock()
            mock_response.results = []
            mock_response.failed_pharmacies = []
            mock_search.return_value = mock_response

            await _handle_drug_search(
                "584121234567", mock_user, "Enalapril", "Daniel",
                temp_location=None,
            )

            # No "Buscando cerca de..." message — only the search reply
            # and the retry hint (since _should_ask_feedback returns False)
            for call in mock_send.call_args_list:
                msg = call.args[1]
                assert "ubicación guardada" not in msg
