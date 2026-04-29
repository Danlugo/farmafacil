"""Tests for v0.18.0 Item 46 — OpenStreetMap pharmacy backfill.

Covers the pure-Python parsing/dedup/chain-detection logic. The async
``backfill_from_osm`` orchestrator is exercised end-to-end with a mocked
Overpass response in ``TestBackfillOrchestration``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from farmafacil.services.osm_backfill import (
    DUPLICATE_NAME_THRESHOLD,
    DUPLICATE_RADIUS_M,
    INDEPENDIENTE_CHAIN,
    detect_chain,
    fetch_osm_pharmacies,
    haversine_m,
    is_24h_from_hours,
    is_duplicate,
    name_similarity,
    parse_osm_element,
)


# ── parse_osm_element ─────────────────────────────────────────────────


class TestParseOsmElement:
    """Tag normalization, coordinate extraction, and rejection edge cases."""

    def test_node_with_full_tags(self):
        element = {
            "type": "node",
            "id": 12345,
            "lat": 10.4856,
            "lon": -66.8634,
            "tags": {
                "amenity": "pharmacy",
                "name": "Farmatodo Tepuy",
                "phone": "+58-212-555-0100",
                "website": "https://farmatodo.com.ve",
                "email": "info@farmatodo.com.ve",
                "opening_hours": "Mo-Su 08:00-22:00",
                "addr:street": "Av Rio de Janeiro",
                "addr:suburb": "Las Mercedes",
            },
        }
        row = parse_osm_element(element)
        assert row is not None
        assert row["name"] == "Farmatodo Tepuy"
        assert row["pharmacy_chain"] == "Farmatodo"
        assert row["latitude"] == 10.4856
        assert row["longitude"] == -66.8634
        assert row["phone"] == "+58-212-555-0100"
        assert row["website"] == "https://farmatodo.com.ve"
        assert row["email"] == "info@farmatodo.com.ve"
        assert row["opening_hours"] == "Mo-Su 08:00-22:00"
        assert row["is_24h"] is False
        assert row["external_id"] == "osm-node-12345"
        assert "Av Rio de Janeiro" in (row["address"] or "")

    def test_way_uses_center_for_coordinates(self):
        element = {
            "type": "way",
            "id": 999,
            "center": {"lat": 10.5, "lon": -66.9},
            "tags": {"amenity": "pharmacy", "name": "Farmacia Caracas"},
        }
        row = parse_osm_element(element)
        assert row is not None
        assert row["latitude"] == 10.5
        assert row["longitude"] == -66.9
        assert row["external_id"] == "osm-way-999"

    def test_relation_uses_center(self):
        element = {
            "type": "relation",
            "id": 7,
            "center": {"lat": 10.6, "lon": -71.6},
            "tags": {"amenity": "pharmacy", "name": "Farmacias SAAS Maracaibo"},
        }
        row = parse_osm_element(element)
        assert row is not None
        assert row["external_id"] == "osm-relation-7"
        assert row["pharmacy_chain"] == "Farmacias SAAS"

    def test_contact_namespace_phone_falls_back(self):
        element = {
            "type": "node", "id": 1, "lat": 10.5, "lon": -66.9,
            "tags": {"name": "X", "contact:phone": "+58-212-111-2222"},
        }
        row = parse_osm_element(element)
        assert row is not None
        assert row["phone"] == "+58-212-111-2222"

    def test_multi_number_phone_keeps_first_only(self):
        # OSM phone tags often pack multiple numbers — pharmacy_locations
        # phone column is VARCHAR(30), so keep the first only. Regression
        # for the v0.18.0 production crash on 2026-04-28.
        element = {
            "type": "node", "id": 1, "lat": 10.5, "lon": -66.9,
            "tags": {
                "name": "X",
                "phone": "+58 212-8724131; +58 414-1234567; +58 416-9876543",
            },
        }
        row = parse_osm_element(element)
        assert row is not None
        assert row["phone"] == "+58 212-8724131"
        assert len(row["phone"]) <= 30

    def test_phone_truncated_to_30_chars(self):
        element = {
            "type": "node", "id": 1, "lat": 10.5, "lon": -66.9,
            "tags": {"name": "X", "phone": "+58 212-555-6789-extension-12345-too-long"},
        }
        row = parse_osm_element(element)
        assert row is not None
        assert len(row["phone"]) <= 30

    def test_long_name_truncated_to_100_chars(self):
        element = {
            "type": "node", "id": 1, "lat": 10.5, "lon": -66.9,
            "tags": {"name": "Farmacia " + "x" * 200},
        }
        row = parse_osm_element(element)
        assert row is not None
        assert len(row["name"]) <= 100

    def test_long_opening_hours_truncated(self):
        element = {
            "type": "node", "id": 1, "lat": 10.5, "lon": -66.9,
            "tags": {
                "name": "X",
                "opening_hours": "Mo-Fr 08:00-20:00; " * 30,
            },
        }
        row = parse_osm_element(element)
        assert row is not None
        assert len(row["opening_hours"]) <= 255

    def test_canonical_phone_wins_over_contact_namespace(self):
        element = {
            "type": "node", "id": 1, "lat": 10.5, "lon": -66.9,
            "tags": {
                "name": "X",
                "phone": "+58-212-CANONICAL",
                "contact:phone": "+58-212-CONTACT",
            },
        }
        row = parse_osm_element(element)
        assert row is not None
        assert row["phone"] == "+58-212-CANONICAL"

    def test_rejects_missing_name(self):
        element = {
            "type": "node", "id": 1, "lat": 10.5, "lon": -66.9,
            "tags": {"amenity": "pharmacy"},
        }
        assert parse_osm_element(element) is None

    def test_rejects_missing_coordinates(self):
        element = {
            "type": "node", "id": 1,
            "tags": {"name": "Farmacia X"},
        }
        assert parse_osm_element(element) is None

    def test_rejects_outside_venezuela_bbox(self):
        # Bogotá, Colombia — should not be ingested as a Venezuelan pharmacy
        element = {
            "type": "node", "id": 1, "lat": 4.65, "lon": -74.05,
            "tags": {"name": "Drogueria Cafam"},
        }
        assert parse_osm_element(element) is None


# ── detect_chain ──────────────────────────────────────────────────────


class TestDetectChain:
    """Map OSM names/brands to our canonical chain values."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Farmatodo Tepuy", "Farmatodo"),
            ("FARMATODO LAS MERCEDES", "Farmatodo"),
            ("Farmacias SAAS La Candelaria", "Farmacias SAAS"),
            ("SAAS Adriatica", "Farmacias SAAS"),
            ("Locatel Santa Paula", "Locatel"),
            ("Farmarebajas Centro", "Farmarebajas"),
            ("Farmahorro Sabana Grande", "Farmahorro"),
            ("Farmacias Xana", "Farmacias XANA"),
        ],
    )
    def test_name_matches_known_chain(self, name, expected):
        assert detect_chain(name) == expected

    def test_brand_overrides_name_when_name_is_generic(self):
        assert detect_chain("Farmacia", brand="Farmatodo") == "Farmatodo"

    def test_operator_match_works_too(self):
        assert detect_chain("Sucursal", operator="Locatel C.A.") == "Locatel"

    def test_unknown_chain_returns_independiente(self):
        assert detect_chain("Farmacia Los Naranjos") == INDEPENDIENTE_CHAIN

    def test_empty_inputs_return_independiente(self):
        assert detect_chain("") == INDEPENDIENTE_CHAIN

    def test_case_insensitive(self):
        assert detect_chain("farmatodo") == "Farmatodo"
        assert detect_chain("FARMACIAS SAAS") == "Farmacias SAAS"


# ── is_24h_from_hours ─────────────────────────────────────────────────


class TestIs24hFromHours:
    """Detect 24-hour pharmacies across OSM authoring variants."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("24/7", True),
            ("24x7", True),
            ("24h", True),
            ("00:00-24:00", True),
            ("Mo-Su 00:00-24:00", True),
            ("Mo-Sun 00:00-24:00", True),
            ("Mo-Fr 08:00-20:00", False),
            ("Mo-Sa 09:00-21:00; Su 10:00-14:00", False),
            ("", False),
            (None, False),
            ("closed", False),
        ],
    )
    def test_known_variants(self, value, expected):
        assert is_24h_from_hours(value) is expected


# ── name_similarity / haversine / is_duplicate ────────────────────────


class TestNameSimilarity:
    def test_identical_names_similarity_one(self):
        assert name_similarity("Farmatodo Tepuy", "Farmatodo Tepuy") == 1.0

    def test_noise_tokens_dropped(self):
        # "Farmacia" / "de" are noise; "los naranjos" carries the signal.
        assert name_similarity(
            "Farmacia Los Naranjos", "Farmacia de Los Naranjos",
        ) == 1.0

    def test_unrelated_names(self):
        assert name_similarity("Farmatodo Tepuy", "Locatel Santa Paula") < 0.2

    def test_accent_insensitive(self):
        assert name_similarity("Farmacia Mérida", "Farmacia Merida") == 1.0

    def test_empty_returns_zero(self):
        assert name_similarity("", "Anything") == 0.0
        assert name_similarity("Farmacia", "Pharmacy") == 0.0  # both reduce to {}


class TestHaversineM:
    def test_zero_distance(self):
        assert haversine_m(10.5, -66.9, 10.5, -66.9) == 0.0

    def test_known_distance_caracas_to_maracaibo(self):
        # ~520 km between Caracas and Maracaibo — within 2% tolerance
        d = haversine_m(10.4806, -66.9036, 10.6427, -71.6125)
        assert 510_000 <= d <= 530_000


class TestIsDuplicate:
    """Loose dedup: within 100m AND name overlap ≥ 0.7."""

    def _make_existing(self, *, name="Farmatodo Tepuy", lat=10.4856, lon=-66.8634):
        existing = MagicMock()
        existing.name = name
        existing.latitude = lat
        existing.longitude = lon
        return existing

    def test_same_coords_same_name_is_duplicate(self):
        osm = {"name": "Farmatodo Tepuy", "latitude": 10.4856, "longitude": -66.8634}
        assert is_duplicate(osm, self._make_existing()) is True

    def test_far_apart_same_name_is_not_duplicate(self):
        # Two different Farmatodo Tepuys (hypothetical) 5 km apart.
        osm = {"name": "Farmatodo Tepuy", "latitude": 10.5, "longitude": -66.9}
        existing = self._make_existing(lat=10.55, lon=-66.95)
        assert is_duplicate(osm, existing) is False

    def test_close_but_unrelated_name_is_not_duplicate(self):
        # Two pharmacies on the same block — different stores.
        osm = {
            "name": "Locatel Santa Paula",
            "latitude": 10.4856, "longitude": -66.8634,
        }
        assert is_duplicate(osm, self._make_existing()) is False

    def test_existing_without_coords_is_not_duplicate(self):
        osm = {"name": "Farmatodo Tepuy", "latitude": 10.4856, "longitude": -66.8634}
        existing = self._make_existing(lat=None, lon=None)
        assert is_duplicate(osm, existing) is False


# ── fetch_osm_pharmacies (network) ────────────────────────────────────


class TestFetchOsmPharmacies:
    """Network-layer resilience — Overpass can be flaky and slow."""

    @pytest.mark.asyncio
    async def test_returns_elements_on_200(self):
        payload = {
            "elements": [
                {"type": "node", "id": 1, "lat": 10.5, "lon": -66.9,
                 "tags": {"name": "X"}}
            ],
        }
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=payload)

        with patch(
            "farmafacil.services.osm_backfill.httpx.AsyncClient",
        ) as mc:
            instance = mc.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=mock_response)
            result = await fetch_osm_pharmacies()

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_on_503(self):
        request = httpx.Request("POST", "https://overpass-api.de/api/interpreter")
        response = httpx.Response(503, request=request)
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "Server overloaded", request=request, response=response,
            )
        )

        with patch("farmafacil.services.osm_backfill.httpx.AsyncClient") as mc:
            instance = mc.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=mock_response)
            result = await fetch_osm_pharmacies()

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_request_error(self):
        with patch("farmafacil.services.osm_backfill.httpx.AsyncClient") as mc:
            instance = mc.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=httpx.ConnectError("DNS"))
            result = await fetch_osm_pharmacies()

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_invalid_json(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(side_effect=ValueError("not json"))

        with patch("farmafacil.services.osm_backfill.httpx.AsyncClient") as mc:
            instance = mc.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=mock_response)
            result = await fetch_osm_pharmacies()

        assert result == []


# ── Sanitizer (formatter helper) ─────────────────────────────────────


class TestSanitizeOsmText:
    """Strip control chars from OSM-sourced strings before WhatsApp display."""

    def test_strips_zero_width_space(self):
        from farmafacil.bot.formatter import _sanitize_osm_text
        assert _sanitize_osm_text("Farmacia​X") == "FarmaciaX"

    def test_strips_rtl_override(self):
        from farmafacil.bot.formatter import _sanitize_osm_text
        # U+202E (RIGHT-TO-LEFT OVERRIDE) is a known display-spoofing char
        assert _sanitize_osm_text("safe‮malicious") == "safemalicious"

    def test_strips_control_chars(self):
        from farmafacil.bot.formatter import _sanitize_osm_text
        assert _sanitize_osm_text("hello\x00world\x1f") == "helloworld"

    def test_passes_legitimate_unicode(self):
        from farmafacil.bot.formatter import _sanitize_osm_text
        assert _sanitize_osm_text("Farmacia Mérida") == "Farmacia Mérida"
        assert _sanitize_osm_text("Mo-Fr 08:00-20:00") == "Mo-Fr 08:00-20:00"

    def test_none_returns_none(self):
        from farmafacil.bot.formatter import _sanitize_osm_text
        assert _sanitize_osm_text(None) is None

    def test_only_control_chars_returns_none(self):
        """Empty-after-strip should not render as an empty string in chat."""
        from farmafacil.bot.formatter import _sanitize_osm_text
        assert _sanitize_osm_text("\x00\x01\x02") is None


# ── In-batch dedup (regression for code-review finding #1) ───────────


class TestInBatchDedup:
    """If OSM has two elements for the same physical pharmacy (e.g., a
    `node` and a `way`), the second must NOT insert a duplicate row.

    This is a regression test for the v0.18.0 code-review finding: prior
    versions of ``backfill_from_osm`` loaded ``existing_rows`` once and
    never extended it after inserts, so duplicates within the same batch
    escaped dedup.
    """

    @pytest.mark.asyncio
    async def test_duplicate_osm_elements_dedup_within_batch(self, monkeypatch):
        from farmafacil.services import osm_backfill

        # Two OSM elements for the SAME physical pharmacy (one node + one
        # way is a common OSM authoring pattern). Same coords, same name.
        elements = [
            {
                "type": "node", "id": 1,
                "lat": 10.5, "lon": -66.9,
                "tags": {"amenity": "pharmacy", "name": "Farmacia Demo"},
            },
            {
                "type": "way", "id": 999,
                "center": {"lat": 10.5, "lon": -66.9},
                "tags": {"amenity": "pharmacy", "name": "Farmacia Demo"},
            },
        ]

        async def fake_fetch():
            return elements

        monkeypatch.setattr(osm_backfill, "fetch_osm_pharmacies", fake_fetch)

        # Reset DB so we have a clean baseline.
        from farmafacil.db.session import async_session
        from farmafacil.models.database import PharmacyLocation
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(PharmacyLocation))
            await session.commit()

        summary = await osm_backfill.backfill_from_osm()

        # Of the two OSM elements, exactly one must be inserted; the second
        # should be caught by in-batch dedup.
        assert summary["inserted"] == 1, summary
        # The skipped/updated counter must account for the duplicate hit.
        assert summary["skipped"] + summary["updated"] == 1, summary


class TestConstants:
    """Lock the dedup thresholds — moving these is a deliberate decision."""

    def test_radius_is_100m(self):
        assert DUPLICATE_RADIUS_M == 100.0

    def test_name_threshold_is_0_7(self):
        assert DUPLICATE_NAME_THRESHOLD == 0.7
