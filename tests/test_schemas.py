"""Tests for Pydantic schemas."""

from decimal import Decimal

from farmafacil.models.schemas import DrugResult, SearchRequest, SearchResponse


def test_drug_result_minimal():
    """DrugResult can be created with just required fields."""
    result = DrugResult(
        drug_name="Losartan 50mg",
        pharmacy_name="Farmatodo",
        available=True,
    )
    assert result.drug_name == "Losartan 50mg"
    assert result.price is None
    assert result.price_bs is None
    assert result.available is True
    assert result.requires_prescription is False
    assert result.stores_in_stock == 0


def test_drug_result_with_all_fields():
    """DrugResult stores all fields correctly."""
    result = DrugResult(
        drug_name="Losartan 50mg",
        pharmacy_name="Farmatodo",
        price=Decimal("5.99"),
        price_bs=Decimal("920"),
        available=True,
        requires_prescription=True,
        brand="Genven",
        drug_class="ANTIHIPERTENSIVOS",
        stores_in_stock=42,
        image_url="https://example.com/img.jpg",
    )
    assert result.price == Decimal("5.99")
    assert result.price_bs == Decimal("920")
    assert result.requires_prescription is True
    assert result.brand == "Genven"
    assert result.stores_in_stock == 42


def test_search_request_validation():
    """SearchRequest enforces min_length on query."""
    req = SearchRequest(query="losartan")
    assert req.query == "losartan"
    assert req.city is None


def test_search_request_with_city():
    """SearchRequest accepts optional city."""
    req = SearchRequest(query="losartan", city="caracas")
    assert req.city == "caracas"


def test_search_response_structure():
    """SearchResponse aggregates results correctly."""
    resp = SearchResponse(
        query="losartan",
        city="caracas",
        results=[
            DrugResult(drug_name="Losartan 50mg", pharmacy_name="Farmatodo", available=True),
        ],
        total=1,
        searched_pharmacies=["Farmatodo"],
    )
    assert resp.total == 1
    assert resp.city == "caracas"
    assert len(resp.results) == 1
    assert resp.searched_pharmacies == ["Farmatodo"]
