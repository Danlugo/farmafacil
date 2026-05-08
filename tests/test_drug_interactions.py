"""Tests for drug interaction service — name normalization, memory extraction, formatting."""

import pytest

from farmafacil.services.drug_interactions import (
    DrugInteraction,
    InteractionResult,
    _normalize_drug_name,
    extract_medications_from_memory,
    format_interaction_warning,
)


# ── Drug name normalization ───────────────────────────────────────────


class TestNormalizeDrugName:
    """Test Spanish→English drug name normalization."""

    def test_spanish_to_english(self):
        assert _normalize_drug_name("aspirina") == "aspirin"

    def test_spanish_with_accent(self):
        assert _normalize_drug_name("acetaminofén") == "acetaminophen"

    def test_strip_dosage_mg(self):
        assert _normalize_drug_name("losartan 50mg") == "losartan"

    def test_strip_dosage_with_space(self):
        assert _normalize_drug_name("omeprazol 20 mg") == "omeprazole"

    def test_strip_tabletas(self):
        assert _normalize_drug_name("ibuprofeno 400mg tabletas") == "ibuprofen"

    def test_english_passthrough(self):
        assert _normalize_drug_name("warfarin") == "warfarin"

    def test_case_insensitive(self):
        assert _normalize_drug_name("ASPIRINA") == "aspirin"

    def test_whitespace_stripped(self):
        assert _normalize_drug_name("  aspirina  ") == "aspirin"

    def test_unknown_drug_passthrough(self):
        assert _normalize_drug_name("xyzmedication") == "xyzmedication"

    def test_warfarina_spanish(self):
        assert _normalize_drug_name("warfarina") == "warfarin"

    def test_clopidogrel_same(self):
        assert _normalize_drug_name("clopidogrel") == "clopidogrel"


# ── Memory medication extraction ──────────────────────────────────────


class TestExtractMedicationsFromMemory:
    """Test extraction of medication names from user memory text."""

    def test_empty_memory(self):
        assert extract_medications_from_memory("") == []

    def test_none_memory(self):
        assert extract_medications_from_memory(None) == []

    def test_finds_spanish_drug(self):
        memory = "El usuario toma warfarina diariamente para su condición cardíaca"
        meds = extract_medications_from_memory(memory)
        assert "warfarina" in meds

    def test_finds_english_drug(self):
        memory = "User takes metformin for diabetes"
        meds = extract_medications_from_memory(memory)
        assert "metformin" in meds

    def test_finds_multiple_drugs(self):
        memory = "Toma losartan para la presión y metformina para diabetes"
        meds = extract_medications_from_memory(memory)
        assert "losartan" in meds
        assert "metformina" in meds

    def test_no_drugs_in_memory(self):
        memory = "El usuario vive en Caracas y tiene 2 hijos"
        meds = extract_medications_from_memory(memory)
        assert len(meds) == 0

    def test_no_duplicates(self):
        memory = "Toma aspirina. Compra aspirina frecuentemente."
        meds = extract_medications_from_memory(memory)
        assert meds.count("aspirina") == 1


# ── Interaction warning formatting ────────────────────────────────────


class TestFormatInteractionWarning:
    """Test formatting of interaction warnings."""

    def test_no_interactions_returns_empty(self):
        result = InteractionResult(
            has_interactions=False, interactions=[], drugs_checked=["aspirin"]
        )
        assert format_interaction_warning(result) == ""

    def test_single_interaction(self):
        result = InteractionResult(
            has_interactions=True,
            interactions=[
                DrugInteraction(
                    drug_a="Aspirin",
                    drug_b="Warfarin",
                    description="Increased risk of bleeding",
                    severity="high",
                )
            ],
            drugs_checked=["aspirin", "warfarin"],
        )
        warning = format_interaction_warning(result)
        assert "Aspirin" in warning
        assert "Warfarin" in warning
        assert "Alerta" in warning
        assert "medico" in warning.lower()

    def test_limits_to_three_interactions(self):
        interactions = [
            DrugInteraction(
                drug_a=f"Drug{i}", drug_b=f"Drug{i+1}",
                description=f"Interaction {i}", severity="moderate",
            )
            for i in range(5)
        ]
        result = InteractionResult(
            has_interactions=True,
            interactions=interactions,
            drugs_checked=["d1", "d2", "d3"],
        )
        warning = format_interaction_warning(result)
        # Should show at most 3 bullet points
        assert warning.count("\u2022") <= 3

    def test_truncates_long_descriptions(self):
        long_desc = "x" * 300
        result = InteractionResult(
            has_interactions=True,
            interactions=[
                DrugInteraction(
                    drug_a="A", drug_b="B",
                    description=long_desc, severity="high",
                )
            ],
            drugs_checked=["a", "b"],
        )
        warning = format_interaction_warning(result)
        # Description should be truncated
        assert len(warning) < 500


# ── InteractionResult dataclass ───────────────────────────────────────


class TestInteractionResult:
    """Test InteractionResult dataclass defaults."""

    def test_default_no_error(self):
        result = InteractionResult(
            has_interactions=False, interactions=[], drugs_checked=[]
        )
        assert result.error is None

    def test_with_error(self):
        result = InteractionResult(
            has_interactions=False, interactions=[], drugs_checked=[],
            error="API unavailable",
        )
        assert result.error == "API unavailable"


# ── RxNorm API (integration tests — require network) ─────────────────


class TestRxNormLookup:
    """Integration tests for RxNorm API calls."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_lookup_aspirin(self):
        from farmafacil.services.drug_interactions import lookup_rxcui
        rxcui = await lookup_rxcui("aspirin")
        assert rxcui is not None
        assert rxcui.isdigit()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_lookup_spanish_name(self):
        from farmafacil.services.drug_interactions import lookup_rxcui
        rxcui = await lookup_rxcui("aspirina")
        assert rxcui is not None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_aspirin_warfarin_interaction(self):
        from farmafacil.services.drug_interactions import check_interactions
        result = await check_interactions(["aspirina", "warfarina"])
        assert result.has_interactions is True
        assert len(result.interactions) > 0
