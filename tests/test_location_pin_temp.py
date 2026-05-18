"""Tests for location-pin-as-temp-search feature (v0.21.3).

When an already-onboarded user shares a GPS pin, the bot stashes it as
a temporary location for the next search instead of permanently updating
their profile.  Onboarding users still get a permanent save.
"""

from __future__ import annotations

import time as _time

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from farmafacil.bot.handler import (
    handle_location_message,
    _stash_temp_location,
    _pop_temp_location,
    _pending_temp_location,
    _PENDING_LOC_TTL_SECONDS,
)


# ── Stash/pop unit tests ─────────────────────────────────────────────


class TestTempLocationStash:
    """Unit tests for _stash_temp_location / _pop_temp_location."""

    def setup_method(self):
        _pending_temp_location.clear()

    def teardown_method(self):
        _pending_temp_location.clear()

    def test_stash_and_pop(self):
        """Stashed location can be popped once."""
        loc = {"lat": 10.5, "lng": -66.9, "zone_name": "Altamira", "city": "CCS"}
        _stash_temp_location("584121234567", loc)
        result = _pop_temp_location("584121234567")
        assert result == loc

    def test_pop_empty(self):
        """Pop returns None when nothing stashed."""
        assert _pop_temp_location("584121234567") is None

    def test_pop_consumes_entry(self):
        """Pop removes the entry — second pop returns None."""
        loc = {"lat": 10.5, "lng": -66.9, "zone_name": "Altamira", "city": "CCS"}
        _stash_temp_location("584121234567", loc)
        _pop_temp_location("584121234567")
        assert _pop_temp_location("584121234567") is None

    def test_expired_entry_returns_none(self):
        """Entries older than TTL are discarded."""
        loc = {"lat": 10.5, "lng": -66.9, "zone_name": "Altamira", "city": "CCS"}
        # Manually set an old timestamp
        _pending_temp_location["584121234567"] = (loc, _time.monotonic() - _PENDING_LOC_TTL_SECONDS - 1)
        assert _pop_temp_location("584121234567") is None

    def test_stash_overwrites_previous(self):
        """Second stash for same sender replaces the first."""
        loc1 = {"lat": 10.5, "lng": -66.9, "zone_name": "Altamira", "city": "CCS"}
        loc2 = {"lat": 10.4, "lng": -66.8, "zone_name": "Chacao", "city": "CCS"}
        _stash_temp_location("584121234567", loc1)
        _stash_temp_location("584121234567", loc2)
        result = _pop_temp_location("584121234567")
        assert result["zone_name"] == "Chacao"


# ── handle_location_message integration ───────────────────────────────


class TestLocationPinOnboarded:
    """Onboarded user shares GPS pin → stash, not persist."""

    @pytest.fixture
    def mock_user_onboarded(self):
        """Fully onboarded user (step=None, has name+location)."""
        user = MagicMock()
        user.id = 1
        user.name = "Daniel"
        user.latitude = 10.4378
        user.longitude = -66.8354
        user.zone_name = "La Tahona"
        user.city_code = "CCS"
        user.onboarding_step = None
        return user

    @pytest.fixture
    def reverse_geocode_result(self):
        return {"lat": 10.50, "lng": -66.90, "zone_name": "Altamira", "city": "CCS"}

    def setup_method(self):
        _pending_temp_location.clear()

    def teardown_method(self):
        _pending_temp_location.clear()

    @pytest.mark.asyncio
    async def test_onboarded_user_stashes_not_persists(
        self, mock_user_onboarded, reverse_geocode_result,
    ):
        """Onboarded user location pin → stash, NOT update_user_location."""
        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=mock_user_onboarded),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=mock_user_onboarded),
            patch("farmafacil.bot.handler.reverse_geocode", new_callable=AsyncMock, return_value=reverse_geocode_result),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.update_user_location", new_callable=AsyncMock) as mock_update,
        ):
            await handle_location_message("584121234567", 10.50, -66.90)

            # update_user_location must NOT be called
            mock_update.assert_not_called()

            # Temp location should be stashed
            stashed = _pop_temp_location("584121234567")
            assert stashed is not None
            assert stashed["zone_name"] == "Altamira"

            # User should get a temp-search message
            msg = mock_send.call_args.args[1]
            assert "Altamira" in msg
            assert "La Tahona" in msg

    @pytest.mark.asyncio
    async def test_onboarding_user_still_persists(self, reverse_geocode_result):
        """User still onboarding → location IS permanently saved."""
        onboarding_user = MagicMock()
        onboarding_user.id = 2
        onboarding_user.name = "Jose"
        onboarding_user.latitude = None
        onboarding_user.longitude = None
        onboarding_user.zone_name = None
        onboarding_user.city_code = None
        onboarding_user.onboarding_step = "awaiting_location"

        updated_user = MagicMock()
        updated_user.id = 2
        updated_user.name = "Jose"
        updated_user.zone_name = "Altamira"
        updated_user.city_code = "CCS"

        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=onboarding_user),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=onboarding_user),
            patch("farmafacil.bot.handler.reverse_geocode", new_callable=AsyncMock, return_value=reverse_geocode_result),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.update_user_location", new_callable=AsyncMock, return_value=updated_user) as mock_update,
        ):
            await handle_location_message("584129999999", 10.50, -66.90)

            # update_user_location MUST be called for onboarding
            mock_update.assert_called_once()

            # Nothing stashed (permanent save, not temp)
            assert _pop_temp_location("584129999999") is None

    @pytest.mark.asyncio
    async def test_reverse_geocode_fails_no_stash(self, mock_user_onboarded):
        """If reverse_geocode returns None, nothing is stashed."""
        with (
            patch("farmafacil.bot.handler.get_or_create_user", new_callable=AsyncMock, return_value=mock_user_onboarded),
            patch("farmafacil.bot.handler.validate_user_profile", new_callable=AsyncMock, return_value=mock_user_onboarded),
            patch("farmafacil.bot.handler.reverse_geocode", new_callable=AsyncMock, return_value=None),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.send_read_receipt", new_callable=AsyncMock),
        ):
            await handle_location_message("584121234567", 10.50, -66.90)
            assert _pop_temp_location("584121234567") is None
