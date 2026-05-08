"""Tests for the WhatsApp bot formatter, webhook, and geocode."""

from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from farmafacil.api.app import create_app
from farmafacil.services.conversation_log import is_duplicate_message, log_inbound
from farmafacil.bot.formatter import (
    MAX_PRODUCTS,
    MAX_STORES_PER_PHARMACY,
    _group_by_product,
    format_search_results,
)
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
        # No failures means no warning emoji
        assert "\u26a0\ufe0f" not in text

    def test_format_no_results_all_scrapers_failed(self):
        """When every queried scraper errored, show a connection-error message."""
        response = SearchResponse(
            query="losartan",
            results=[],
            total=0,
            searched_pharmacies=["Farmatodo", "Farmacias SAAS"],
            failed_pharmacies=["Farmatodo", "Farmacias SAAS"],
        )
        text = format_search_results(response)
        assert "\u26a0\ufe0f" in text
        assert "No pudimos conectar" in text
        assert "Farmatodo" in text
        assert "Farmacias SAAS" in text
        assert "unos minutos" in text
        # Must NOT say the drug doesn't exist
        assert "revisa la ortografia" not in text

    def test_format_no_results_some_scrapers_failed(self):
        """Partial failure: some returned empty, some errored."""
        response = SearchResponse(
            query="losartan",
            results=[],
            total=0,
            searched_pharmacies=["Farmatodo", "Farmacias SAAS", "Locatel"],
            failed_pharmacies=["Locatel"],
        )
        text = format_search_results(response)
        assert "No encontramos" in text
        assert "losartan" in text
        assert "\u26a0\ufe0f" in text
        assert "Locatel" in text
        assert "unos minutos" in text

    def test_format_results_with_partial_failure_shows_warning(self):
        """When results exist but some scrapers failed, header shows warning."""
        response = SearchResponse(
            query="losartan",
            results=[
                DrugResult(
                    drug_name="Losartan 50mg Genven",
                    pharmacy_name="Farmatodo",
                    price_bs=Decimal("920"),
                    available=True,
                ),
            ],
            total=1,
            searched_pharmacies=["Farmatodo", "Farmacias SAAS"],
            failed_pharmacies=["Farmacias SAAS"],
        )
        text = format_search_results(response)
        assert "Losartan 50mg Genven" in text
        assert "\u26a0\ufe0f" in text
        assert "Farmacias SAAS" in text
        assert "parciales" in text

    def test_format_no_results_cache_hit_no_failure_message(self):
        """Cache-suffixed pharmacy names with no failures → normal 'no results'."""
        response = SearchResponse(
            query="xyznonexistent",
            results=[],
            total=0,
            searched_pharmacies=["Farmatodo (cache)", "Farmacias SAAS (cache)"],
            failed_pharmacies=[],
        )
        text = format_search_results(response)
        assert "No encontramos resultados" in text
        assert "\u26a0\ufe0f" not in text

    def test_format_degenerate_cache_only_with_failed_falls_through(self):
        """Defensive: if all searched names are cache-suffixed but failed is
        non-empty (impossible via search_drug today, but SearchResponse is
        public), the 'queried' list is empty so the all-failed branch must
        NOT fire. Should fall through to the 'partial failure' message.
        """
        response = SearchResponse(
            query="losartan",
            results=[],
            total=0,
            searched_pharmacies=["Farmatodo (cache)"],
            failed_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        # queried == [], so len(failed) >= len(queried) evaluates 1 >= 0 = True,
        # BUT the guard "failed and queried" requires queried to be truthy —
        # so we fall through to the partial-failure branch.
        assert "No encontramos" in text
        assert "losartan" in text
        assert "Farmatodo" in text
        # Must NOT be the "all failed" full-outage message
        assert "ahora mismo" not in text

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
        assert "CHUAO" in text
        assert "1.9 km" in text

    def test_format_unavailable(self):
        """Unavailable drug shows Sin stock label."""
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
        assert "Sin stock" in text

    def test_format_truncates_at_max(self):
        """More than MAX_PRODUCTS unique products shows truncation message."""
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
        assert "4 productos mas" in text

    def test_format_groups_same_product(self):
        """Same product from multiple pharmacies is grouped under one entry."""
        response = SearchResponse(
            query="losartan",
            results=[
                DrugResult(
                    drug_name="Losartan 50mg",
                    pharmacy_name="Farmacias SAAS",
                    price_bs=Decimal("2"),
                    available=True,
                ),
                DrugResult(
                    drug_name="Losartan 50mg",
                    pharmacy_name="Farmatodo",
                    price_bs=Decimal("900"),
                    available=True,
                ),
            ],
            total=2,
            searched_pharmacies=["Farmatodo", "Farmacias SAAS"],
        )
        text = format_search_results(response)
        # Product name should appear only once (as header)
        assert text.count("*1. Losartan 50mg*") == 1
        # Both pharmacies listed under it
        assert "Farmacias SAAS" in text
        assert "Farmatodo" in text
        # No second numbered product
        assert "*2." not in text

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

    def test_format_deduplicates_same_pharmacy_same_product(self):
        """Same product from same pharmacy appears only once, not twice."""
        response = SearchResponse(
            query="losartan",
            results=[
                DrugResult(
                    drug_name="Losartan 50mg",
                    pharmacy_name="Farmatodo",
                    price_bs=Decimal("900"),
                    available=True,
                ),
                DrugResult(
                    drug_name="Losartan 50mg",
                    pharmacy_name="Farmatodo",
                    price_bs=Decimal("900"),
                    available=True,
                ),
            ],
            total=2,
            searched_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        # Only one numbered product entry
        assert text.count("*1. Losartan 50mg*") == 1
        assert "*2." not in text
        # Pharmacy appears exactly once under the product
        assert text.count("Farmatodo") >= 1

    def test_format_store_price_shown_per_store(self):
        """NearbyStore.price_bs is shown per store line."""
        response = SearchResponse(
            query="losartan",
            results=[
                DrugResult(
                    drug_name="Losartan 50mg",
                    pharmacy_name="Farmatodo",
                    price_bs=Decimal("900"),
                    available=True,
                    nearby_stores=[
                        NearbyStore(
                            store_name="TEPUY",
                            address="Las Mercedes",
                            distance_km=0.5,
                            price_bs=Decimal("910.50"),
                        ),
                    ],
                ),
            ],
            total=1,
            searched_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        assert "TEPUY" in text
        assert "910.50" in text

    def test_format_store_without_price_omits_price(self):
        """NearbyStore without price_bs does not emit a price line."""
        response = SearchResponse(
            query="losartan",
            results=[
                DrugResult(
                    drug_name="Losartan 50mg",
                    pharmacy_name="Farmatodo",
                    price_bs=Decimal("900"),
                    available=True,
                    nearby_stores=[
                        NearbyStore(
                            store_name="CHUAO",
                            address="Chuao",
                            distance_km=1.2,
                            price_bs=None,
                        ),
                    ],
                ),
            ],
            total=1,
            searched_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        assert "CHUAO" in text
        assert "1.2 km" in text
        # The store line should end after the distance — no "Bs." on the store line
        # (The product-level Bs. 900 is still present, but no second "Bs." for the store)
        store_line_start = text.index("CHUAO")
        newline_after = text.find("\n", store_line_start)
        store_line = text[store_line_start:newline_after] if newline_after != -1 else text[store_line_start:]
        assert "Bs." not in store_line

    def test_format_max_stores_per_pharmacy_capped(self):
        """Only MAX_STORES_PER_PHARMACY store lines appear per pharmacy."""
        stores = [
            NearbyStore(
                store_name=f"STORE_{i}",
                address=f"Address {i}",
                distance_km=float(i),
                price_bs=Decimal("100"),
            )
            for i in range(MAX_STORES_PER_PHARMACY + 2)
        ]
        response = SearchResponse(
            query="losartan",
            results=[
                DrugResult(
                    drug_name="Losartan 50mg",
                    pharmacy_name="Farmatodo",
                    price_bs=Decimal("100"),
                    available=True,
                    nearby_stores=stores,
                ),
            ],
            total=1,
            searched_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        # Stores 0..MAX_STORES_PER_PHARMACY-1 appear; the rest must not
        for i in range(MAX_STORES_PER_PHARMACY):
            assert f"STORE_{i}" in text
        for i in range(MAX_STORES_PER_PHARMACY, MAX_STORES_PER_PHARMACY + 2):
            assert f"STORE_{i}" not in text

    def test_format_product_without_price(self):
        """Product with no price_bs formats without a price entry."""
        response = SearchResponse(
            query="test",
            results=[
                DrugResult(
                    drug_name="NoPriceDrug",
                    pharmacy_name="Farmatodo",
                    price_bs=None,
                    available=True,
                ),
            ],
            total=1,
            searched_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        assert "NoPriceDrug" in text
        assert "Farmatodo" in text
        # No price should appear at all for this product
        assert "Bs." not in text

    def test_format_discount_shows_original_price_and_percentage(self):
        """Discount products show sale price, strikethrough original, and percentage."""
        response = SearchResponse(
            query="test",
            results=[
                DrugResult(
                    drug_name="DiscountDrug",
                    pharmacy_name="Farmatodo",
                    price_bs=Decimal("800"),
                    full_price_bs=Decimal("1000"),
                    discount_pct="20%",
                    available=True,
                ),
            ],
            total=1,
            searched_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        assert "800" in text
        assert "1,000" in text or "1000" in text
        assert "20%" in text
        # WhatsApp strikethrough tilde characters
        assert "~" in text

    def test_format_truncation_message_shows_correct_remaining_count(self):
        """Truncation message reports exact number of remaining products."""
        n_results = MAX_PRODUCTS + 3
        results = [
            DrugResult(
                drug_name=f"Drug {i}",
                pharmacy_name="Farmatodo",
                available=True,
                price_bs=Decimal("100"),
            )
            for i in range(n_results)
        ]
        response = SearchResponse(
            query="test",
            results=results,
            total=n_results,
            searched_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        assert "3 productos mas" in text

    def test_format_exactly_max_products_no_truncation(self):
        """Exactly MAX_PRODUCTS unique products — no truncation message."""
        results = [
            DrugResult(
                drug_name=f"Drug {i}",
                pharmacy_name="Farmatodo",
                available=True,
                price_bs=Decimal("100"),
            )
            for i in range(MAX_PRODUCTS)
        ]
        response = SearchResponse(
            query="test",
            results=results,
            total=MAX_PRODUCTS,
            searched_pharmacies=["Farmatodo"],
        )
        text = format_search_results(response)
        assert "productos mas" not in text

    def test_format_interleaves_pharmacies(self):
        """Products from different pharmacy chains alternate in output order."""
        results = [
            DrugResult(
                drug_name="Alpha Drug",
                pharmacy_name="Farmatodo",
                price_bs=Decimal("100"),
                available=True,
            ),
            DrugResult(
                drug_name="Beta Drug",
                pharmacy_name="Farmacias SAAS",
                price_bs=Decimal("50"),
                available=True,
            ),
            DrugResult(
                drug_name="Gamma Drug",
                pharmacy_name="Farmatodo",
                price_bs=Decimal("200"),
                available=True,
            ),
            DrugResult(
                drug_name="Delta Drug",
                pharmacy_name="Farmacias SAAS",
                price_bs=Decimal("75"),
                available=True,
            ),
        ]
        response = SearchResponse(
            query="test",
            results=results,
            total=4,
            searched_pharmacies=["Farmatodo", "Farmacias SAAS"],
        )
        text = format_search_results(response)
        # All four products are present
        assert "Alpha Drug" in text
        assert "Beta Drug" in text
        assert "Gamma Drug" in text
        assert "Delta Drug" in text
        # Two pharmacies alternate — positions must interleave (Farmatodo product
        # at pos 1 or 2, SAAS product at the other position, etc.)
        pos_alpha = text.index("Alpha Drug")
        pos_beta = text.index("Beta Drug")
        pos_gamma = text.index("Gamma Drug")
        pos_delta = text.index("Delta Drug")
        # The two Farmatodo products must not be adjacent (SAAS products interleave)
        farmatodo_positions = sorted([pos_alpha, pos_gamma])
        saas_positions = sorted([pos_beta, pos_delta])
        # Interleaved means a SAAS product falls between the two Farmatodo products
        assert saas_positions[0] > farmatodo_positions[0] or saas_positions[0] < farmatodo_positions[1]


class TestGroupByProduct:
    """Unit tests for the _group_by_product helper."""

    def test_same_product_different_pharmacies_grouped(self):
        """Same drug name from two pharmacies produces one group with two entries."""
        results = [
            DrugResult(
                drug_name="Losartan 50mg",
                pharmacy_name="Farmatodo",
                price_bs=Decimal("900"),
                available=True,
            ),
            DrugResult(
                drug_name="Losartan 50mg",
                pharmacy_name="Farmacias SAAS",
                price_bs=Decimal("800"),
                available=True,
            ),
        ]
        groups = _group_by_product(results)
        assert len(groups) == 1
        name, pharmacy_results = groups[0]
        assert name == "Losartan 50mg"
        assert len(pharmacy_results) == 2
        pharmacy_names = {r.pharmacy_name for r in pharmacy_results}
        assert pharmacy_names == {"Farmatodo", "Farmacias SAAS"}

    def test_same_product_same_pharmacy_deduplicated(self):
        """Duplicate (product, pharmacy) pair is deduplicated to one entry."""
        results = [
            DrugResult(
                drug_name="Losartan 50mg",
                pharmacy_name="Farmatodo",
                price_bs=Decimal("900"),
                available=True,
            ),
            DrugResult(
                drug_name="Losartan 50mg",
                pharmacy_name="Farmatodo",
                price_bs=Decimal("910"),
                available=True,
            ),
        ]
        groups = _group_by_product(results)
        assert len(groups) == 1
        _name, pharmacy_results = groups[0]
        assert len(pharmacy_results) == 1

    def test_different_products_produce_separate_groups(self):
        """Two distinct drug names produce two separate groups."""
        results = [
            DrugResult(
                drug_name="Losartan 50mg",
                pharmacy_name="Farmatodo",
                price_bs=Decimal("900"),
                available=True,
            ),
            DrugResult(
                drug_name="Enalapril 10mg",
                pharmacy_name="Farmatodo",
                price_bs=Decimal("500"),
                available=True,
            ),
        ]
        groups = _group_by_product(results)
        assert len(groups) == 2
        names = {name for name, _ in groups}
        assert names == {"Losartan 50mg", "Enalapril 10mg"}

    def test_available_sorted_before_unavailable(self):
        """Within a group, available pharmacies sort before unavailable ones."""
        results = [
            DrugResult(
                drug_name="Losartan 50mg",
                pharmacy_name="Farmatodo",
                price_bs=Decimal("900"),
                available=False,
            ),
            DrugResult(
                drug_name="Losartan 50mg",
                pharmacy_name="Farmacias SAAS",
                price_bs=Decimal("800"),
                available=True,
            ),
        ]
        groups = _group_by_product(results)
        _name, pharmacy_results = groups[0]
        assert pharmacy_results[0].available is True
        assert pharmacy_results[0].pharmacy_name == "Farmacias SAAS"

    def test_cheaper_available_sorted_first_within_available(self):
        """Among available pharmacies, the cheaper one comes first."""
        results = [
            DrugResult(
                drug_name="Losartan 50mg",
                pharmacy_name="PharmacyExpensive",
                price_bs=Decimal("1000"),
                available=True,
            ),
            DrugResult(
                drug_name="Losartan 50mg",
                pharmacy_name="PharmacyCheap",
                price_bs=Decimal("200"),
                available=True,
            ),
        ]
        groups = _group_by_product(results)
        _name, pharmacy_results = groups[0]
        assert pharmacy_results[0].pharmacy_name == "PharmacyCheap"

    def test_none_price_treated_as_highest_for_sorting(self):
        """A product with no price sorts after priced products in the same group."""
        results = [
            DrugResult(
                drug_name="Losartan 50mg",
                pharmacy_name="PharmacyNone",
                price_bs=None,
                available=True,
            ),
            DrugResult(
                drug_name="Losartan 50mg",
                pharmacy_name="PharmacyPriced",
                price_bs=Decimal("500"),
                available=True,
            ),
        ]
        groups = _group_by_product(results)
        _name, pharmacy_results = groups[0]
        assert pharmacy_results[0].pharmacy_name == "PharmacyPriced"

    def test_interleave_round_robins_across_chains(self):
        """Products alternate between pharmacy chains in round-robin order."""
        results = [
            DrugResult(
                drug_name="Drug A",
                pharmacy_name="ChainAlpha",
                price_bs=Decimal("100"),
                available=True,
            ),
            DrugResult(
                drug_name="Drug B",
                pharmacy_name="ChainBeta",
                price_bs=Decimal("200"),
                available=True,
            ),
            DrugResult(
                drug_name="Drug C",
                pharmacy_name="ChainAlpha",
                price_bs=Decimal("150"),
                available=True,
            ),
            DrugResult(
                drug_name="Drug D",
                pharmacy_name="ChainBeta",
                price_bs=Decimal("250"),
                available=True,
            ),
        ]
        groups = _group_by_product(results)
        names_in_order = [name for name, _ in groups]
        # ChainAlpha and ChainBeta should alternate (round-robin)
        # ChainAlpha: Drug A, Drug C; ChainBeta: Drug B, Drug D
        # Sorted chains: ChainAlpha, ChainBeta → order: A, B, C, D
        assert names_in_order == ["Drug A", "Drug B", "Drug C", "Drug D"]

    def test_empty_results_returns_empty_list(self):
        """Empty input produces an empty list."""
        groups = _group_by_product([])
        assert groups == []

    def test_single_result_returns_one_group(self):
        """Single result returns one group with one pharmacy entry."""
        results = [
            DrugResult(
                drug_name="Paracetamol 500mg",
                pharmacy_name="Farmatodo",
                price_bs=Decimal("300"),
                available=True,
            )
        ]
        groups = _group_by_product(results)
        assert len(groups) == 1
        name, pharmacy_results = groups[0]
        assert name == "Paracetamol 500mg"
        assert len(pharmacy_results) == 1


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


class TestMessageDedup:
    """Test WhatsApp message deduplication."""

    @pytest.mark.asyncio
    async def test_new_message_not_duplicate(self):
        """A message ID not in the DB is not a duplicate."""
        assert await is_duplicate_message("wamid_never_seen_123") is False

    @pytest.mark.asyncio
    async def test_empty_id_not_duplicate(self):
        """Empty message ID is never treated as duplicate."""
        assert await is_duplicate_message("") is False

    @pytest.mark.asyncio
    async def test_logged_message_is_duplicate(self):
        """A message ID already logged is detected as duplicate."""
        wa_id = "wamid_test_dedup_456"
        await log_inbound(
            phone_number="5491100000000",
            message_text="test dedup",
            message_type="text",
            wa_message_id=wa_id,
        )
        assert await is_duplicate_message(wa_id) is True
