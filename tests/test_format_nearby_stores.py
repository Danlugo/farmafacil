"""Tests for v0.18.0 Items 45 + 46 — format_nearby_stores rich-attribute display.

The formatter must:
- Drop the chain prefix when chain == "Independiente" (per user spec).
- Show zone_name when present in the location line.
- Show 24h flag as a moon icon (compact) instead of the raw hours string.
- Show truncated hours for partial-day pharmacies.
- Show phone + website only when populated.
"""

from farmafacil.bot.formatter import (
    HOURS_DISPLAY_MAXLEN,
    URL_DISPLAY_MAXLEN,
    _short_hours,
    _short_url,
    format_nearby_stores,
)


def _make_store(**overrides) -> dict:
    base = {
        "store_name": "Farmatodo TEPUY",
        "address": "AV RIO DE JANEIRO",
        "distance_km": 5.6,
        "pharmacy_chain": "Farmatodo",
        "zone_name": None,
        "opening_hours": None,
        "is_24h": False,
        "phone": None,
        "website": None,
    }
    base.update(overrides)
    return base


# ── _short_hours ─────────────────────────────────────────────────────


class TestShortHours:
    def test_short_string_passthrough(self):
        assert _short_hours("Mo-Fr 08:00-20:00") == "Mo-Fr 08:00-20:00"

    def test_long_string_truncated_at_first_segment(self):
        full = "Mo-Fr 08:00-20:00; Sa 09:00-18:00; Su 10:00-14:00"
        result = _short_hours(full)
        assert "Mo-Fr 08:00-20:00" in result
        assert "Su 10:00-14:00" not in result

    def test_truncation_marker_for_excess(self):
        result = _short_hours("Mo-Fr 08:00-20:00" * 5)
        assert result.endswith("...")
        assert len(result) <= HOURS_DISPLAY_MAXLEN


# ── _short_url ───────────────────────────────────────────────────────


class TestShortUrl:
    def test_strips_https_prefix(self):
        assert _short_url("https://farmatodo.com.ve") == "farmatodo.com.ve"

    def test_strips_http_prefix(self):
        assert _short_url("http://example.com") == "example.com"

    def test_strips_trailing_slash(self):
        assert _short_url("https://example.com/") == "example.com"

    def test_long_url_truncated(self):
        url = "https://" + "x" * 200 + ".com"
        result = _short_url(url)
        assert len(result) <= URL_DISPLAY_MAXLEN
        assert result.endswith("...")


# ── format_nearby_stores — rich attributes ───────────────────────────


class TestFormatNearbyStoresAttributes:
    def test_chain_prefix_for_real_chain(self):
        stores = [_make_store(pharmacy_chain="Farmatodo", store_name="TEPUY")]
        out = format_nearby_stores(stores)
        assert "Farmatodo TEPUY" in out

    def test_no_chain_prefix_for_independiente(self):
        stores = [_make_store(
            pharmacy_chain="Independiente",
            store_name="Farmacia Los Naranjos",
        )]
        out = format_nearby_stores(stores)
        # Per Item 46 spec: never show "(Independiente)" or any chain marker
        assert "Independiente" not in out
        assert "Farmacia Los Naranjos" in out

    def test_zone_name_appears_in_location_line(self):
        stores = [_make_store(zone_name="Las Mercedes", distance_km=2.4)]
        out = format_nearby_stores(stores)
        assert "Las Mercedes" in out
        assert "2.4 km" in out

    def test_24h_flag_displayed_as_moon_icon(self):
        stores = [_make_store(is_24h=True, opening_hours="24/7")]
        out = format_nearby_stores(stores)
        assert "24 horas" in out
        # The full raw "24/7" string should NOT appear — compact form wins.
        assert "24/7" not in out

    def test_partial_hours_displayed_when_not_24h(self):
        stores = [_make_store(opening_hours="Mo-Fr 08:00-20:00", is_24h=False)]
        out = format_nearby_stores(stores)
        assert "08:00-20:00" in out

    def test_phone_displayed_when_present(self):
        stores = [_make_store(phone="+58-212-555-0100")]
        out = format_nearby_stores(stores)
        assert "+58-212-555-0100" in out

    def test_website_displayed_when_present(self):
        stores = [_make_store(website="https://farmatodo.com.ve")]
        out = format_nearby_stores(stores)
        assert "farmatodo.com.ve" in out

    def test_optional_fields_omitted_when_missing(self):
        # Bare store with no rich attributes — none of the icons should appear.
        stores = [_make_store()]
        out = format_nearby_stores(stores)
        assert "24 horas" not in out
        assert "📞" not in out
        assert "🌐" not in out

    def test_distance_label_always_present(self):
        stores = [_make_store(distance_km=0.4)]
        out = format_nearby_stores(stores)
        assert "0.4 km" in out

    def test_full_combination(self):
        """Every attribute populated — verify the whole block renders sanely."""
        stores = [_make_store(
            store_name="TEPUY",
            pharmacy_chain="Farmatodo",
            zone_name="Las Mercedes",
            distance_km=5.6,
            opening_hours="Mo-Su 07:00-23:00",
            is_24h=False,
            phone="+58-212-555-0100",
            website="https://farmatodo.com.ve",
        )]
        out = format_nearby_stores(stores, zone_name="Los Naranjos")

        # Header references the user's zone (separate from the store's zone).
        assert "Los Naranjos" in out
        # The store rendering has all the attributes.
        assert "Farmatodo TEPUY" in out
        assert "Las Mercedes" in out
        assert "5.6 km" in out
        assert "07:00-23:00" in out
        assert "+58-212-555-0100" in out
        assert "farmatodo.com.ve" in out
