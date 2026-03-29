"""Tests for the WhatsApp bot formatter, webhook, and geocode."""

from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from farmafacil.api.app import create_app
from farmafacil.bot.formatter import format_search_results
from farmafacil.models.schemas import DrugResult, NearbyStore, SearchResponse
from farmafacil.services.geocode import geocode_zone


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestFormatter:
    """Test WhatsApp message formatting."""

    def test_format_no_results(self):
        """Empty results produce a helpful message."""
        response = SearchResponse(
            query="xyznonexistent",
            results=[],
            total=0,
            searched_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        assert "xyznonexistent" in text
        assert "No encontramos" in text

    def test_format_single_result(self):
        """Single result is formatted correctly."""
        response = SearchResponse(
            query="losartan",
            results=[
                DrugResult(
                    drug_name="Losartan 50mg Genven",
                    pharmacy_name="Farmatodo",
                    price_bs=Decimal("920"),
                    available=True,
                    stores_in_stock=42,
                    requires_prescription=True,
                ),
            ],
            total=1,
            searched_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        assert "1" in text
        assert "Losartan 50mg Genven" in text
        assert "920" in text

    def test_format_with_nearby_stores(self):
        """Results with nearby stores show store names and distances."""
        response = SearchResponse(
            query="losartan",
            zone="El Cafetal",
            results=[
                DrugResult(
                    drug_name="Losartan 50mg",
                    pharmacy_name="Farmatodo",
                    price_bs=Decimal("920"),
                    available=True,
                    nearby_stores=[
                        NearbyStore(
                            store_name="TEPUY",
                            address="Las Mercedes",
                            distance_km=0.5,
                            price_bs=Decimal("920"),
                        ),
                        NearbyStore(
                            store_name="CHUAO",
                            address="Chuao",
                            distance_km=1.9,
                            price_bs=Decimal("920"),
                        ),
                    ],
                ),
            ],
            total=1,
            searched_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        assert "El Cafetal" in text
        assert "TEPUY" in text
        assert "0.5 km" in text
        assert "CHUAO" in text

    def test_format_unavailable(self):
        """Unavailable drug shows correct icon."""
        response = SearchResponse(
            query="test",
            results=[
                DrugResult(
                    drug_name="Test Drug",
                    pharmacy_name="Farmatodo",
                    available=False,
                ),
            ],
            total=1,
            searched_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        assert "\u274c" in text
        assert "Sin stock" in text

    def test_format_truncates_at_five(self):
        """More than 5 results shows truncation message."""
        results = [
            DrugResult(
                drug_name=f"Drug {i}",
                pharmacy_name="Farmatodo",
                available=True,
                price_bs=Decimal("100"),
            )
            for i in range(8)
        ]
        response = SearchResponse(
            query="test",
            results=results,
            total=8,
            searched_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        assert "3 resultados mas" in text


class TestGeocode:
    """Test the geocode service."""

    def test_geocode_el_cafetal(self):
        """El Cafetal resolves to CCS."""
        result = geocode_zone("El Cafetal")
        assert result is not None
        assert result["city"] == "CCS"
        assert result["zone_name"] == "El Cafetal"
        assert result["lat"] == pytest.approx(10.4558, abs=0.01)

    def test_geocode_chacao(self):
        """Chacao resolves correctly."""
        result = geocode_zone("chacao")
        assert result is not None
        assert result["city"] == "CCS"

    def test_geocode_maracaibo(self):
        """Maracaibo resolves to MCBO."""
        result = geocode_zone("Maracaibo")
        assert result is not None
        assert result["city"] == "MCBO"

    def test_geocode_unknown(self):
        """Unknown zone returns None."""
        result = geocode_zone("xyznotazone")
        assert result is None

    def test_geocode_case_insensitive(self):
        """Geocode is case insensitive."""
        result = geocode_zone("EL CAFETAL")
        assert result is not None
        assert result["city"] == "CCS"

    def test_geocode_partial_match(self):
        """Partial zone names match."""
        result = geocode_zone("cafetal")
        assert result is not None
        assert result["city"] == "CCS"


class TestWebhook:
    """Test the webhook verification endpoint."""

    async def test_webhook_verify_success(self, client):
        """Valid verification request returns challenge."""
        response = await client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "farmafacil_verify_2026",
                "hub.challenge": "test_challenge_123",
            },
        )
        assert response.status_code == 200
        assert response.text == "test_challenge_123"

    async def test_webhook_verify_bad_token(self, client):
        """Invalid token returns 403."""
        response = await client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong_token",
                "hub.challenge": "test",
            },
        )
        assert response.status_code == 403

    async def test_webhook_post_ack(self, client):
        """POST webhook returns 200 OK."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "123"},
                                "messages": [],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }
        response = await client.post("/webhook", json=payload)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
