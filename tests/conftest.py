"""Shared test fixtures for FarmaFacil."""

import pytest


@pytest.fixture
def sample_drug_name() -> str:
    """Common drug name for testing searches."""
    return "losartan"


@pytest.fixture
def sample_farmatodo_html() -> str:
    """Minimal Farmatodo search result HTML for testing the parser."""
    return """
    <div class="product-item">
        <h2 class="product-name">Losartan 50mg</h2>
        <span class="price">$5.99</span>
        <span class="availability">Disponible</span>
    </div>
    """
