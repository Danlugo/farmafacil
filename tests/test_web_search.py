"""Tests for services/web_search.py — Brave Search API wrapper.

Covers: missing API key, successful search, empty results, rate limit,
auth error, generic HTTP error, network error, result formatting.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from farmafacil.services.web_search import (
    BRAVE_API_URL,
    MAX_RESULTS,
    TIMEOUT,
    web_search,
)


class TestWebSearchNoApiKey:
    """Behaviour when BRAVE_SEARCH_API_KEY is missing."""

    @pytest.mark.asyncio
    async def test_returns_unavailable_message(self, monkeypatch):
        monkeypatch.setattr("farmafacil.services.web_search.BRAVE_SEARCH_API_KEY", "")
        result = await web_search("test query")
        assert "no disponible" in result.lower()

    @pytest.mark.asyncio
    async def test_no_http_call_when_no_key(self, monkeypatch):
        monkeypatch.setattr("farmafacil.services.web_search.BRAVE_SEARCH_API_KEY", "")
        with patch("farmafacil.services.web_search.httpx.AsyncClient") as mock_cls:
            await web_search("test")
            mock_cls.assert_not_called()


class TestWebSearchSuccess:
    """Verify successful search result formatting."""

    @pytest.mark.asyncio
    async def test_formats_results(self, monkeypatch):
        monkeypatch.setattr("farmafacil.services.web_search.BRAVE_SEARCH_API_KEY", "sk-test")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "web": {
                "results": [
                    {
                        "title": "Farmatodo Venezuela",
                        "url": "https://farmatodo.com.ve",
                        "description": "Farmacia online",
                    },
                    {
                        "title": "Locatel",
                        "url": "https://locatel.com.ve",
                        "description": "Tienda de salud",
                    },
                ]
            }
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("farmafacil.services.web_search.httpx.AsyncClient", return_value=mock_client):
            result = await web_search("farmacias Venezuela")

        assert "Farmatodo Venezuela" in result
        assert "Locatel" in result
        assert "farmacias Venezuela" in result
        assert "1." in result
        assert "2." in result

    @pytest.mark.asyncio
    async def test_empty_results(self, monkeypatch):
        monkeypatch.setattr("farmafacil.services.web_search.BRAVE_SEARCH_API_KEY", "sk-test")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"web": {"results": []}}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("farmafacil.services.web_search.httpx.AsyncClient", return_value=mock_client):
            result = await web_search("xyz nonexistent")

        assert "no se encontraron" in result.lower()


class TestWebSearchErrors:
    """Verify error handling for various HTTP failure modes."""

    @pytest.mark.asyncio
    async def test_rate_limit_429(self, monkeypatch):
        monkeypatch.setattr("farmafacil.services.web_search.BRAVE_SEARCH_API_KEY", "sk-test")

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429", request=MagicMock(), response=mock_response,
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("farmafacil.services.web_search.httpx.AsyncClient", return_value=mock_client):
            result = await web_search("test")

        assert "rate limit" in result.lower()

    @pytest.mark.asyncio
    async def test_auth_error_401(self, monkeypatch):
        monkeypatch.setattr("farmafacil.services.web_search.BRAVE_SEARCH_API_KEY", "sk-test")

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=mock_response,
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("farmafacil.services.web_search.httpx.AsyncClient", return_value=mock_client):
            result = await web_search("test")

        assert "inválida" in result.lower() or "expirada" in result.lower()

    @pytest.mark.asyncio
    async def test_generic_http_error(self, monkeypatch):
        monkeypatch.setattr("farmafacil.services.web_search.BRAVE_SEARCH_API_KEY", "sk-test")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=mock_response,
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("farmafacil.services.web_search.httpx.AsyncClient", return_value=mock_client):
            result = await web_search("test")

        assert "HTTP 500" in result

    @pytest.mark.asyncio
    async def test_network_error(self, monkeypatch):
        monkeypatch.setattr("farmafacil.services.web_search.BRAVE_SEARCH_API_KEY", "sk-test")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.RequestError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("farmafacil.services.web_search.httpx.AsyncClient", return_value=mock_client):
            result = await web_search("test")

        assert "conexión" in result.lower()


class TestWebSearchConstants:
    """Verify module constants."""

    def test_brave_api_url(self):
        assert "brave.com" in BRAVE_API_URL

    def test_max_results_is_reasonable(self):
        assert 1 <= MAX_RESULTS <= 20

    def test_timeout_is_positive(self):
        assert TIMEOUT > 0
