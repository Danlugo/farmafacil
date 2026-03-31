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
        """Single result is formatted with pharmacy name and price."""
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
        assert "Losartan 50mg Genven" in text
        assert "920" in text
        assert "Farmatodo" in text

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

    def test_format_truncates_at_max(self):
        """More than MAX_RESULTS_SHOWN results shows truncation message."""
        results = [
            DrugResult(
                drug_name=f"Drug {i}",
                pharmacy_name="Farmatodo",
                available=True,
                price_bs=Decimal("100"),
            )
            for i in range(12)
        ]
        response = SearchResponse(
            query="test",
            results=results,
            total=12,
            searched_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        assert "4 resultados mas" in text

    def test_format_sorted_by_price(self):
        """Results are sorted by price, lowest first."""
        response = SearchResponse(
            query="losartan",
            results=[
                DrugResult(
                    drug_name="Expensive Drug",
                    pharmacy_name="Farmatodo",
                    price_bs=Decimal("50"),
                    available=True,
                ),
                DrugResult(
                    drug_name="Cheap Drug",
                    pharmacy_name="Farmacias SAAS",
                    price_bs=Decimal("5"),
                    available=True,
                ),
            ],
            total=2,
            searched_pharmacies=["Farmatodo", "Farmacias SAAS"],
        )
        text = format_search_results(response)
        # Cheap Drug should appear before Expensive Drug
        cheap_pos = text.index("Cheap Drug")
        expensive_pos = text.index("Expensive Drug")
        assert cheap_pos < expensive_pos

    def test_format_multi_pharmacy(self):
        """Results from multiple pharmacies show pharmacy names."""
        response = SearchResponse(
            query="losartan",
            results=[
                DrugResult(
                    drug_name="Losartan A",
                    pharmacy_name="Farmatodo",
                    price_bs=Decimal("10"),
                    available=True,
                ),
                DrugResult(
                    drug_name="Losartan B",
                    pharmacy_name="Farmacias SAAS",
                    price_bs=Decimal("8"),
                    available=True,
                ),
            ],
            total=2,
            searched_pharmacies=["Farmatodo", "Farmacias SAAS"],
        )
        text = format_search_results(response)
        assert "Farmatodo" in text
        assert "Farmacias SAAS" in text


@pytest.mark.integration
class TestGeocode:
    """Test the geocode service (hits live Nominatim API)."""

    async def test_geocode_el_cafetal(self):
        """El Cafetal resolves to CCS."""
        result = await geocode_zone("El Cafetal")
        assert result is not None
        assert result["city"] == "CCS"
        assert result["lat"] == pytest.approx(10.45, abs=0.05)

    async def test_geocode_la_boyera(self):
        """La Boyera resolves to CCS."""
        result = await geocode_zone("La Boyera")
        assert result is not None
        assert result["city"] == "CCS"
        assert "Boyera" in result["zone_name"]

    async def test_geocode_chacao(self):
        """Chacao resolves correctly."""
        result = await geocode_zone("chacao")
        assert result is not None
        assert result["city"] == "CCS"

    async def test_geocode_maracaibo(self):
        """Maracaibo resolves to MCBO."""
        result = await geocode_zone("Maracaibo")
        assert result is not None
        assert result["city"] == "MCBO"

    async def test_geocode_unknown(self):
        """Nonsense zone returns None."""
        result = await geocode_zone("xyznotazone12345")
        assert result is None

    async def test_geocode_valencia(self):
        """Valencia resolves to VAL."""
        result = await geocode_zone("Valencia")
        assert result is not None
        assert result["city"] == "VAL"


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
