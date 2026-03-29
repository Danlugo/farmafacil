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
    assert result.available is True


def test_drug_result_with_price():
    """DrugResult stores Decimal prices correctly."""
    result = DrugResult(
        drug_name="Losartan 50mg",
        pharmacy_name="Farmatodo",
        price=Decimal("5.99"),
        available=True,
    )
    assert result.price == Decimal("5.99")


def test_search_request_validation():
    """SearchRequest enforces min_length on query."""
    req = SearchRequest(query="losartan")
    assert req.query == "losartan"


def test_search_response_structure():
    """SearchResponse aggregates results correctly."""
    resp = SearchResponse(
        query="losartan",
        results=[
            DrugResult(drug_name="Losartan 50mg", pharmacy_name="Farmatodo", available=True),
        ],
        total=1,
        searched_pharmacies=["Farmatodo"],
    )
    assert resp.total == 1
    assert len(resp.results) == 1
    assert resp.searched_pharmacies == ["Farmatodo"]
