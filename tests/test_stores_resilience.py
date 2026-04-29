"""Tests for Item 44 — Farmatodo store-enrichment resilience.

Production crashed 2026-04-28 23:31:59 with:
    httpx.HTTPStatusError: Client error '409 Conflict' for url
    'https://api-transactional.farmatodo.com/route/r/VE/v1/stores/nearby?...'

The original `except httpx.RequestError` clause did NOT catch HTTPStatusError
(separate subclass), so the 409 propagated up to `_handle_drug_search`, the
webhook returned 500, and the user got only the "Te busco condones ahora..."
ack with no follow-up.

These tests lock in:
1. HTTPStatusError (4xx/5xx) → log warning, return [] (search continues)
2. RequestError (network) → log error, return [] (existing behavior preserved)
3. 200 OK → returns parsed stores (existing happy path)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from farmafacil.services.stores import get_nearby_stores


def _make_status_error(status_code: int) -> httpx.HTTPStatusError:
    """Build an HTTPStatusError that mimics what raise_for_status emits."""
    request = httpx.Request("GET", "https://api-transactional.farmatodo.com/x")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{status_code}'", request=request, response=response,
    )


class TestGetNearbyStoresResilience:
    """get_nearby_stores must degrade gracefully on Farmatodo failures."""

    @pytest.mark.asyncio
    async def test_409_conflict_returns_empty_list(self):
        """A 409 from /stores/nearby must NOT crash — returns [] so search continues."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(side_effect=_make_status_error(409))

        with patch("farmafacil.services.stores.httpx.AsyncClient") as mc:
            instance = mc.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_response)
            result = await get_nearby_stores("CCS", 10.4395, -66.8389)

        assert result == []

    @pytest.mark.asyncio
    async def test_500_server_error_returns_empty_list(self):
        """A 5xx must also be swallowed — same degraded path as 4xx."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(side_effect=_make_status_error(500))

        with patch("farmafacil.services.stores.httpx.AsyncClient") as mc:
            instance = mc.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_response)
            result = await get_nearby_stores("CCS", 10.4395, -66.8389)

        assert result == []

    @pytest.mark.asyncio
    async def test_request_error_still_returns_empty_list(self):
        """Network-layer error (DNS, timeout) — existing behavior must hold."""
        with patch("farmafacil.services.stores.httpx.AsyncClient") as mc:
            instance = mc.return_value.__aenter__.return_value
            instance.get = AsyncMock(side_effect=httpx.ConnectError("DNS"))
            result = await get_nearby_stores("CCS", 10.4395, -66.8389)

        assert result == []

    @pytest.mark.asyncio
    async def test_200_ok_returns_parsed_stores(self):
        """Happy path — 200 with valid payload returns Store objects."""
        payload = {
            "nearbyStores": [
                {
                    "id": 100,
                    "name": "TEPUY",
                    "city": "CCS",
                    "latitude": 10.45,
                    "longitude": -66.85,
                    "address": "Av Libertador",
                    "distanceInKm": 99,  # bogus value — should be recalculated
                },
                {
                    "id": 101,
                    "name": "CHUAO",
                    "city": "CCS",
                    "latitude": 10.46,
                    "longitude": -66.86,
                    "address": "Av Principal",
                    "distanceInKm": 99,
                },
            ],
        }
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=payload)

        with patch("farmafacil.services.stores.httpx.AsyncClient") as mc:
            instance = mc.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_response)
            result = await get_nearby_stores("CCS", 10.4395, -66.8389)

        assert len(result) == 2
        # Distance must be recalculated via haversine, not the bogus 99
        assert all(s.distance_km < 10 for s in result)
        # Sorted nearest-first
        assert result[0].distance_km <= result[1].distance_km

    @pytest.mark.asyncio
    async def test_409_logs_warning_with_status_code(self, caplog):
        """The warning log must include the status code for diagnosability."""
        import logging

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(side_effect=_make_status_error(409))

        with caplog.at_level(logging.WARNING, logger="farmafacil.services.stores"):
            with patch("farmafacil.services.stores.httpx.AsyncClient") as mc:
                instance = mc.return_value.__aenter__.return_value
                instance.get = AsyncMock(return_value=mock_response)
                await get_nearby_stores("CCS", 10.4395, -66.8389)

        # Use getMessage() — it always returns the interpolated string.
        # `r.message` is only populated after a Formatter has run, which is
        # implementation-dependent across pytest versions / Python versions.
        assert any("409" in r.getMessage() for r in caplog.records)
        assert any("CCS" in r.getMessage() for r in caplog.records)
