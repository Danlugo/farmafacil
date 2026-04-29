"""Tests for v0.19.0 — services.location.

Covers:
- Hash + normalization (cache key stability)
- Confidence + name-match validation (the Daniel-class bug)
- Forward / reverse cache hit + miss flows (Nominatim mocked)
- Admin helpers (set_user_location, set_pharmacy_location)
- Cache cleanup
"""

from unittest.mock import patch

import pytest

from farmafacil.db.session import async_session
from farmafacil.models.database import GeocodeCache, PharmacyLocation, User
from farmafacil.services.location import (
    DEFAULT_MIN_CONFIDENCE,
    LocationResult,
    _confidence_from_importance,
    _forward_key,
    _name_matches_query,
    _normalize,
    _reverse_key,
    cleanup_expired_cache,
    geocode_health,
    resolve,
    reverse,
    set_pharmacy_location,
    set_user_location,
)


# ── Hashing / normalization ──────────────────────────────────────────


class TestNormalize:
    def test_lowercases(self):
        assert _normalize("La Boyera") == "la boyera"

    def test_strips_accents(self):
        assert _normalize("Mérida") == "merida"

    def test_collapses_whitespace(self):
        assert _normalize("  La   Boyera  ") == "la boyera"


class TestForwardKey:
    def test_same_query_same_hash(self):
        _, h1 = _forward_key("La Boyera")
        _, h2 = _forward_key("la boyera")
        _, h3 = _forward_key("LA  BOYERA")
        assert h1 == h2 == h3

    def test_different_queries_different_hash(self):
        _, h1 = _forward_key("La Boyera")
        _, h2 = _forward_key("La Hoyadita")
        assert h1 != h2

    def test_forward_and_reverse_keys_disjoint(self):
        # Even if "10.5,-66.9" matched a forward query somehow, the
        # source prefix in the hash means the keys never collide.
        _, fh = _forward_key("10.5,-66.9")
        _, rh = _reverse_key(10.5, -66.9)
        assert fh != rh


class TestReverseKey:
    def test_close_coords_share_key(self):
        # Within 4-decimal rounding (~10m) reverse queries collapse.
        # Both lat and lng must round to the same 4-decimal value.
        _, h1 = _reverse_key(10.42581, -66.84221)
        _, h2 = _reverse_key(10.42583, -66.84219)
        assert h1 == h2  # both round to 10.4258, -66.8422

    def test_far_coords_distinct_key(self):
        _, h1 = _reverse_key(10.4258, -66.8422)  # La Boyera
        _, h2 = _reverse_key(10.3555, -66.8467)  # La Hoyadita
        assert h1 != h2


# ── Confidence + name-match validation ───────────────────────────────


class TestConfidenceFromImportance:
    def test_clamps_to_unit_interval(self):
        assert _confidence_from_importance(1.5) == 1.0
        assert _confidence_from_importance(-0.2) == 0.0

    def test_passes_normal_values(self):
        assert _confidence_from_importance(0.5) == 0.5

    def test_handles_missing(self):
        assert _confidence_from_importance(None) == 0.0
        assert _confidence_from_importance("oops") == 0.0


class TestNameMatchesQuery:
    """The validation guard that would have caught Daniel's bug."""

    def test_la_boyera_matches_la_boyera_display(self):
        assert _name_matches_query(
            "La Boyera",
            "La Boyera, Caracas, Parroquia El Hatillo, Venezuela",
        ) is True

    def test_la_boyera_does_NOT_match_la_hoyadita(self):
        # The proximate Daniel bug
        assert _name_matches_query(
            "La Boyera",
            "La Hoyadita, Caracas, Parroquia Nuestra Señora del Rosario",
        ) is False

    def test_accent_insensitive(self):
        assert _name_matches_query("Mérida", "Merida, Estado Mérida") is True

    def test_empty_query_returns_false(self):
        assert _name_matches_query("", "anything") is False

    def test_missing_display_returns_false(self):
        assert _name_matches_query("La Boyera", None) is False
        assert _name_matches_query("La Boyera", "") is False


# ── resolve() with mocked Nominatim ──────────────────────────────────


class TestResolveCacheBehavior:
    """resolve() should hit cache on the second call and skip Nominatim."""

    @pytest.mark.asyncio
    async def test_first_call_miss_second_call_hit(self):
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(GeocodeCache))
            await session.commit()

        nominatim_payload = [{
            "lat": "10.4258",
            "lon": "-66.8422",
            "display_name": "La Boyera, Caracas, Parroquia El Hatillo, Venezuela",
            "importance": 0.6,
            "name": "La Boyera",
            "address": {"city": "Caracas", "state": "Miranda"},
        }]

        async def fake_search(query):
            fake_search.calls += 1
            return nominatim_payload
        fake_search.calls = 0

        with patch("farmafacil.services.location._nominatim_search", side_effect=fake_search):
            r1 = await resolve("La Boyera")
            r2 = await resolve("La Boyera")
            r3 = await resolve("la  boyera")  # normalized to same key

        assert fake_search.calls == 1
        assert r1 is not None and r1.source == "forward"
        assert r2 is not None and r2.source == "cache"
        assert r3 is not None and r3.source == "cache"
        # All three must point to the same coords
        assert r1.lat == r2.lat == r3.lat == 10.4258


class TestResolveValidation:
    """The Daniel-class regression — low confidence + bad name = surfaced."""

    @pytest.mark.asyncio
    async def test_low_confidence_with_name_mismatch_is_returned_with_low_confidence(self):
        # The signal is: caller can read .confidence and decide how to react.
        # We don't return None unconditionally — the value is still "best
        # we have" and the bot's onboarding flow asks the user to confirm.
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(GeocodeCache))
            await session.commit()

        bad_payload = [{
            "lat": "10.3555",
            "lon": "-66.8467",
            "display_name": "La Hoyadita, Caracas, Parroquia Nuestra Señora del Rosario",
            "importance": 0.15,
            "name": "La Hoyadita",
            "address": {"state": "Miranda"},
        }]

        with patch(
            "farmafacil.services.location._nominatim_search",
            return_value=bad_payload,
        ):
            r = await resolve("La Boyera")

        assert r is not None
        assert r.confidence < DEFAULT_MIN_CONFIDENCE
        assert "Hoyadita" in r.display_name
        # display_name doesn't include "boyera", so name match fails
        assert _name_matches_query("La Boyera", r.display_name) is False

    @pytest.mark.asyncio
    async def test_alternatives_populated(self):
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(GeocodeCache))
            await session.commit()

        payload = [
            {"lat": "10.4258", "lon": "-66.8422", "display_name": "La Boyera A",
             "importance": 0.6, "name": "La Boyera",
             "address": {"city": "Caracas", "state": "Miranda"}},
            {"lat": "10.4280", "lon": "-66.8402", "display_name": "La Boyera B",
             "importance": 0.5, "address": {}},
            {"lat": "10.4274", "lon": "-66.8396", "display_name": "La Boyera C",
             "importance": 0.4, "address": {}},
        ]

        with patch(
            "farmafacil.services.location._nominatim_search",
            return_value=payload,
        ):
            r = await resolve("La Boyera")

        assert r is not None
        assert len(r.alternatives) == 2
        assert r.alternatives[0]["display_name"] == "La Boyera B"


class TestResolveEmpty:
    @pytest.mark.asyncio
    async def test_empty_query_returns_none(self):
        assert await resolve("") is None
        assert await resolve("   ") is None

    @pytest.mark.asyncio
    async def test_no_results_returns_none(self):
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(GeocodeCache))
            await session.commit()

        with patch(
            "farmafacil.services.location._nominatim_search",
            return_value=[],
        ):
            assert await resolve("Pharmacy On Mars") is None


# ── reverse() ─────────────────────────────────────────────────────────


class TestReverse:
    @pytest.mark.asyncio
    async def test_extracts_zone_from_suburb(self):
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(GeocodeCache))
            await session.commit()

        payload = {
            "display_name": "Av Principal, La Boyera, Caracas, Miranda",
            "importance": 0.5,
            "address": {
                "country_code": "ve",
                "suburb": "La Boyera",
                "state": "Miranda",
            },
        }
        with patch(
            "farmafacil.services.location._nominatim_reverse",
            return_value=payload,
        ):
            r = await reverse(10.4258, -66.8422)
        assert r is not None
        assert r.zone_name == "La Boyera"
        assert r.source == "reverse"

    @pytest.mark.asyncio
    async def test_cache_hit_on_repeat(self):
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(GeocodeCache))
            await session.commit()

        payload = {
            "display_name": "Las Mercedes, Caracas",
            "importance": 0.4,
            "address": {"country_code": "ve", "suburb": "Las Mercedes"},
        }

        async def fake_reverse(lat, lng):
            fake_reverse.calls += 1
            return payload
        fake_reverse.calls = 0

        with patch(
            "farmafacil.services.location._nominatim_reverse",
            side_effect=fake_reverse,
        ):
            r1 = await reverse(10.4856, -66.8634)
            r2 = await reverse(10.4856, -66.8634)

        assert fake_reverse.calls == 1
        assert r1.source == "reverse" and r2.source == "cache"


# ── set_user_location admin helper ───────────────────────────────────


class TestSetUserLocation:
    @pytest.mark.asyncio
    async def test_updates_existing_user(self):
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(GeocodeCache))
            await session.execute(sa_delete(User))
            session.add(User(
                phone_number="+99999999",
                name="TestUser",
                latitude=0.0,
                longitude=0.0,
                display_preference="grid",
            ))
            await session.commit()

        good_payload = [{
            "lat": "10.4258",
            "lon": "-66.8422",
            "display_name": "La Boyera, Caracas",
            "importance": 0.6,
            "name": "La Boyera",
            "address": {"city": "Caracas", "state": "Miranda"},
        }]

        with patch(
            "farmafacil.services.location._nominatim_search",
            return_value=good_payload,
        ):
            out = await set_user_location("+99999999", "La Boyera")

        assert out["ok"] is True
        assert abs(out["lat"] - 10.4258) < 0.001
        assert out["zone_name"] == "La Boyera"

        # Verify persisted
        async with async_session() as session:
            result = await session.execute(
                User.__table__.select().where(User.phone_number == "+99999999")
            )
            row = result.first()
            assert abs(row.latitude - 10.4258) < 0.001

        # Cleanup
        async with async_session() as session:
            await session.execute(sa_delete(User).where(User.phone_number == "+99999999"))
            await session.commit()

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        good_payload = [{
            "lat": "10.4258", "lon": "-66.8422",
            "display_name": "La Boyera", "importance": 0.6,
            "name": "La Boyera", "address": {},
        }]
        with patch(
            "farmafacil.services.location._nominatim_search",
            return_value=good_payload,
        ):
            out = await set_user_location("+nonexistent", "La Boyera")
        assert out["ok"] is False
        assert out["reason"] == "user_not_found"

    @pytest.mark.asyncio
    async def test_geocode_failure(self):
        with patch(
            "farmafacil.services.location._nominatim_search",
            return_value=[],
        ):
            out = await set_user_location("+anything", "GibberishPlace")
        assert out["ok"] is False
        assert out["reason"] == "geocode_failed"


# ── set_pharmacy_location admin helper ───────────────────────────────


class TestSetPharmacyLocation:
    @pytest.mark.asyncio
    async def test_manual_lat_lng_override(self):
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(PharmacyLocation).where(
                PharmacyLocation.external_id == "test-manual-1"
            ))
            session.add(PharmacyLocation(
                external_id="test-manual-1",
                pharmacy_chain="Independiente",
                name="TestPharmacy",
                name_lower="testpharmacy",
                city_code="CCS",
                latitude=0.0,
                longitude=0.0,
                is_active=True,
            ))
            await session.commit()
            result = await session.execute(
                PharmacyLocation.__table__.select().where(
                    PharmacyLocation.external_id == "test-manual-1"
                )
            )
            row = result.first()
            pharmacy_id = row.id

        out = await set_pharmacy_location(pharmacy_id, lat=10.4258, lng=-66.8422)
        assert out["ok"] is True
        assert out["lat"] == 10.4258
        assert out["confidence"] == 1.0  # manual override

        async with async_session() as session:
            await session.execute(sa_delete(PharmacyLocation).where(
                PharmacyLocation.id == pharmacy_id
            ))
            await session.commit()

    @pytest.mark.asyncio
    async def test_query_and_coords_both_rejected(self):
        out = await set_pharmacy_location(1, query="X", lat=10.0, lng=-66.0)
        assert out["ok"] is False
        assert "query_xor_coords" in out["reason"]

    @pytest.mark.asyncio
    async def test_neither_query_nor_coords_rejected(self):
        out = await set_pharmacy_location(1)
        assert out["ok"] is False

    @pytest.mark.asyncio
    async def test_unknown_pharmacy_id(self):
        out = await set_pharmacy_location(999_999_999, lat=10.0, lng=-66.0)
        assert out["ok"] is False
        assert out["reason"] == "pharmacy_not_found"


# ── geocode_health + cleanup ─────────────────────────────────────────


class TestGeocodeHealth:
    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_keys(self):
        stats = await geocode_health(days=7)
        assert "total_rows" in stats
        assert "fetched_last_n_days" in stats
        assert "forward_rows" in stats
        assert "reverse_rows" in stats
        assert "low_confidence_rows" in stats
        assert "ttl_days" in stats


class TestSetPharmacyLocationBounds:
    """v0.19.0 review finding #4 — reject out-of-Venezuela coordinates."""

    @pytest.mark.asyncio
    async def test_null_island_rejected(self):
        out = await set_pharmacy_location(1, lat=0.0, lng=0.0)
        assert out["ok"] is False
        assert out["reason"] == "coords_out_of_bounds"

    @pytest.mark.asyncio
    async def test_bogota_coords_rejected(self):
        # Bogotá, Colombia — outside VE bbox
        out = await set_pharmacy_location(1, lat=4.65, lng=-74.05)
        assert out["ok"] is False
        assert out["reason"] == "coords_out_of_bounds"

    @pytest.mark.asyncio
    async def test_madrid_rejected(self):
        out = await set_pharmacy_location(1, lat=40.41, lng=-3.70)
        assert out["ok"] is False
        assert out["reason"] == "coords_out_of_bounds"


class TestCachePutRace:
    """Regression for v0.19.0 review finding #2 — concurrent _cache_put."""

    @pytest.mark.asyncio
    async def test_concurrent_writes_do_not_raise(self):
        """If two coroutines write the same query_hash, neither raises."""
        import asyncio
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(GeocodeCache))
            await session.commit()

        payload = [{
            "lat": "10.4258", "lon": "-66.8422",
            "display_name": "La Boyera, Caracas",
            "importance": 0.6, "name": "La Boyera",
            "address": {"city": "Caracas", "state": "Miranda"},
        }]

        with patch(
            "farmafacil.services.location._nominatim_search",
            return_value=payload,
        ):
            # Fire several concurrent resolves of the same query.
            results = await asyncio.gather(
                resolve("La Boyera"),
                resolve("La Boyera"),
                resolve("La Boyera"),
                return_exceptions=True,
            )

        # No exceptions, all return a usable result.
        for r in results:
            assert not isinstance(r, BaseException), r
            assert r is not None

        # Exactly one row in the cache.
        async with async_session() as session:
            count_result = await session.execute(
                GeocodeCache.__table__.select()
            )
            rows = count_result.fetchall()
            assert len(rows) == 1


class TestCleanupExpiredCache:
    @pytest.mark.asyncio
    async def test_purges_old_rows(self):
        from datetime import datetime, timedelta
        from sqlalchemy import delete as sa_delete

        async with async_session() as session:
            await session.execute(sa_delete(GeocodeCache))
            old = GeocodeCache(
                query_hash="old123",
                query_text="oldplace",
                source="forward",
                latitude=0.0,
                longitude=0.0,
                fetched_at=datetime.utcnow() - timedelta(days=200),
            )
            new = GeocodeCache(
                query_hash="new456",
                query_text="newplace",
                source="forward",
                latitude=0.0,
                longitude=0.0,
                fetched_at=datetime.utcnow(),
            )
            session.add(old)
            session.add(new)
            await session.commit()

        deleted = await cleanup_expired_cache(older_than_days=90)
        assert deleted >= 1

        async with async_session() as session:
            still = (
                await session.execute(
                    GeocodeCache.__table__.select().where(
                        GeocodeCache.query_hash == "new456"
                    )
                )
            ).first()
            gone = (
                await session.execute(
                    GeocodeCache.__table__.select().where(
                        GeocodeCache.query_hash == "old123"
                    )
                )
            ).first()
            assert still is not None
            assert gone is None
