"""Tests for location prefix stripping (Item 120) and store info formatting (Item 121).

Item 120: Venezuelan place-type descriptors ("urbanización", "barrio", "sector")
are stripped before geocoding. "urbanización Los Naranjos" → "Los Naranjos".

Item 121: format_store_info() returns all available store details (phone,
website, hours, zone) — not just address and map link.
"""

import pytest

from farmafacil.services.location import _strip_location_prefix


# ===========================================================================
# Item 120 — Place-type descriptor prefix stripping
# ===========================================================================


class TestPlaceTypeDescriptorPrefixes:
    """Venezuelan place-type descriptors stripped before geocoding."""

    @pytest.mark.parametrize("text,expected", [
        # urbanización — most common case (with and without accent)
        ("urbanización Los Naranjos", "Los Naranjos"),
        ("urbanizacion Los Naranjos", "Los Naranjos"),
        ("Urbanización El Cafetal", "El Cafetal"),
        ("URBANIZACIÓN LA BOYERA", "LA BOYERA"),
        # urb. abbreviation
        ("urb. Los Naranjos", "Los Naranjos"),
        ("urb Los Naranjos", "Los Naranjos"),
        ("Urb. El Hatillo", "El Hatillo"),
        # barrio
        ("barrio La Cruz", "La Cruz"),
        ("Barrio Sucre", "Sucre"),
        # sector
        ("sector El Valle", "El Valle"),
        # conjunto / residencias
        ("conjunto residencial Los Pinos", "residencial Los Pinos"),
        ("residencias El Parque", "El Parque"),
        # conversational prefixes still work (regression check)
        ("en La Boyera", "La Boyera"),
        ("vivo en Altamira", "Altamira"),
        ("estoy en El Hatillo", "El Hatillo"),
        # plain name without prefix — unchanged
        ("Los Naranjos", "Los Naranjos"),
        ("El Hatillo", "El Hatillo"),
    ], ids=[
        "urbanización-accent", "urbanizacion-no-accent", "urbanización-cafetal",
        "urbanización-uppercase", "urb-dot", "urb-no-dot", "urb-dot-hatillo",
        "barrio-la-cruz", "barrio-sucre", "sector-el-valle",
        "conjunto-residencial", "residencias-parque",
        "conversational-en", "conversational-vivo-en", "conversational-estoy-en",
        "plain-los-naranjos", "plain-el-hatillo",
    ])
    def test_strip_place_type_prefix(self, text, expected):
        assert _strip_location_prefix(text) == expected

    def test_urbanizacion_alone_returns_original(self):
        """If stripping 'urbanización' leaves nothing useful, keep original."""
        assert _strip_location_prefix("urbanización") == "urbanización"

    def test_urbanizacion_short_remainder(self):
        """If remainder < 3 chars after stripping, keep original."""
        assert _strip_location_prefix("urbanización La") == "urbanización La"


# ===========================================================================
# Item 120 — _NAME_NOISE includes place-type descriptors
# ===========================================================================


class TestNameNoiseDescriptors:
    """Place-type descriptors in _NAME_NOISE don't break token-overlap check."""

    def test_urbanizacion_in_noise_set(self):
        from farmafacil.services.location import _NAME_NOISE
        assert "urbanizacion" in _NAME_NOISE

    def test_barrio_in_noise_set(self):
        from farmafacil.services.location import _NAME_NOISE
        assert "barrio" in _NAME_NOISE

    @pytest.mark.parametrize("descriptor", [
        "urbanizacion", "urb", "barrio", "sector", "conjunto", "residencias",
    ], ids=["urbanizacion", "urb", "barrio", "sector", "conjunto", "residencias"])
    def test_descriptor_in_noise(self, descriptor):
        from farmafacil.services.location import _NAME_NOISE
        assert descriptor in _NAME_NOISE


# ===========================================================================
# Item 121 — format_store_info() enriched output
# ===========================================================================


class TestFormatStoreInfo:
    """format_store_info() shows all available store details."""

    @staticmethod
    def _make_store(**overrides):
        """Build a minimal PharmacyLocation-like object for testing."""
        from unittest.mock import MagicMock
        store = MagicMock()
        defaults = dict(
            pharmacy_chain="Farmatodo",
            name="CHUAO",
            address="Avenida principal de Chuao, Municipio Baruta",
            zone_name="Chuao",
            city_code="CCS",
            opening_hours="Mo-Fr 08:00-20:00; Sa 09:00-18:00",
            is_24h=False,
            phone="+58 212-555-1234",
            website="https://www.farmatodo.com.ve",
            latitude=10.4823007,
            longitude=-66.8459645,
        )
        defaults.update(overrides)
        for key, val in defaults.items():
            setattr(store, key, val)
        return store

    def test_full_store_has_all_fields(self):
        """Store with all fields populated shows everything."""
        from farmafacil.services.store_backfill import format_store_info
        store = self._make_store()
        text = format_store_info(store)

        assert "Farmatodo CHUAO" in text
        assert "Avenida principal de Chuao" in text
        assert "Chuao" in text  # zone_name
        assert "CCS" in text
        assert "+58 212-555-1234" in text
        assert "farmatodo.com.ve" in text
        assert "maps.google.com" in text
        assert "Mo-Fr 08:00-20:00" in text

    def test_phone_shown_with_emoji(self):
        """Phone is displayed with 📞 emoji."""
        from farmafacil.services.store_backfill import format_store_info
        store = self._make_store()
        text = format_store_info(store)
        assert "\U0001f4de +58 212-555-1234" in text

    def test_website_shown_with_emoji(self):
        """Website is displayed with 🌐 emoji."""
        from farmafacil.services.store_backfill import format_store_info
        store = self._make_store()
        text = format_store_info(store)
        assert "\U0001f310 https://www.farmatodo.com.ve" in text

    def test_24h_shown_as_moon_emoji(self):
        """24h stores show 🌙 24 horas instead of raw hours."""
        from farmafacil.services.store_backfill import format_store_info
        store = self._make_store(is_24h=True)
        text = format_store_info(store)
        assert "\U0001f319 24 horas" in text
        # Should NOT show raw opening_hours when is_24h is True
        assert "Mo-Fr" not in text

    def test_hours_shown_when_not_24h(self):
        """Non-24h stores show opening hours with 🕐 emoji."""
        from farmafacil.services.store_backfill import format_store_info
        store = self._make_store(is_24h=False, opening_hours="Mo-Sa 08:00-20:00")
        text = format_store_info(store)
        assert "\U0001f550 Mo-Sa 08:00-20:00" in text

    def test_verbose_hours_truncated(self):
        """Long OSM opening_hours strings are truncated for WhatsApp."""
        from farmafacil.services.store_backfill import format_store_info
        long_hours = "Mo-Fr 08:00-20:00; Sa 09:00-18:00; Su 10:00-14:00; PH 10:00-14:00"
        store = self._make_store(is_24h=False, opening_hours=long_hours)
        text = format_store_info(store)
        # First segment kept, ellipsis added
        assert "Mo-Fr 08:00-20:00" in text
        assert "..." in text
        # Full verbose string should NOT appear
        assert "PH 10:00-14:00" not in text

    def test_missing_phone_no_crash(self):
        """Store without phone doesn't show phone line."""
        from farmafacil.services.store_backfill import format_store_info
        store = self._make_store(phone=None)
        text = format_store_info(store)
        assert "\U0001f4de" not in text

    def test_missing_website_no_crash(self):
        """Store without website doesn't show website line."""
        from farmafacil.services.store_backfill import format_store_info
        store = self._make_store(website=None)
        text = format_store_info(store)
        assert "\U0001f310" not in text

    def test_missing_hours_no_crash(self):
        """Store without hours doesn't show hours line."""
        from farmafacil.services.store_backfill import format_store_info
        store = self._make_store(is_24h=False, opening_hours=None)
        text = format_store_info(store)
        assert "\U0001f550" not in text
        assert "\U0001f319" not in text

    def test_duplicate_chain_in_name_not_repeated(self):
        """If store name starts with chain, chain is not repeated."""
        from farmafacil.services.store_backfill import format_store_info
        store = self._make_store(pharmacy_chain="Farmatodo", name="Farmatodo CHUAO")
        text = format_store_info(store)
        # Should show "Farmatodo CHUAO", not "Farmatodo Farmatodo CHUAO"
        assert "Farmatodo Farmatodo" not in text
        assert "Farmatodo CHUAO" in text

    def test_zone_name_shown(self):
        """Zone name shown between address and city."""
        from farmafacil.services.store_backfill import format_store_info
        store = self._make_store(zone_name="Las Mercedes")
        text = format_store_info(store)
        assert "Zona: Las Mercedes" in text

    def test_minimal_store_only_name(self):
        """Store with only name — no crash, no empty lines."""
        from farmafacil.services.store_backfill import format_store_info
        store = self._make_store(
            address=None, zone_name=None, city_code=None,
            opening_hours=None, is_24h=False, phone=None,
            website=None, latitude=None, longitude=None,
        )
        text = format_store_info(store)
        assert "Farmatodo CHUAO" in text
        lines = [l for l in text.split("\n") if l.strip()]
        assert len(lines) == 1  # Just the title
