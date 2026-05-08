"""Tests for v0.19.0 — admin chat tools for the location pipeline.

These tests exercise the 5 new tools registered in admin_chat.TOOLS:
- geocode_query
- geocode_reverse
- set_user_location
- set_pharmacy_location
- geocode_health

Each tool is invoked via its registered callable so we also catch any
schema/registration drift (the TOOL_REGISTRY ↔ tool function pairing).
"""

from unittest.mock import patch

import pytest

from farmafacil.db.session import async_session
from farmafacil.models.database import GeocodeCache, PharmacyLocation, User
from farmafacil.services.admin_chat import TOOLS


# ── Registry sanity ──────────────────────────────────────────────────


class TestToolsRegistered:
    """The 5 new tools must be present and callable."""

    @pytest.mark.parametrize(
        "tool_name",
        [
            "geocode_query",
            "geocode_reverse",
            "set_user_location",
            "set_pharmacy_location",
            "geocode_health",
        ],
    )
    def test_tool_present_in_registry(self, tool_name):
        assert tool_name in TOOLS
        desc, fn = TOOLS[tool_name]
        assert isinstance(desc, str) and len(desc) > 0
        assert callable(fn)


# ── geocode_query ────────────────────────────────────────────────────


class TestGeocodeQueryTool:
    @pytest.mark.asyncio
    async def test_returns_resolved_string(self):
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

        _, fn = TOOLS["geocode_query"]
        with patch(
            "farmafacil.services.location._nominatim_search",
            return_value=payload,
        ):
            out = await fn({"text": "La Boyera"})

        assert "La Boyera" in out
        assert "10.4258" in out
        assert "confidence" in out

    @pytest.mark.asyncio
    async def test_missing_text_returns_error(self):
        _, fn = TOOLS["geocode_query"]
        out = await fn({})
        assert "Falta" in out or "missing" in out.lower()

    @pytest.mark.asyncio
    async def test_no_result_message(self):
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(GeocodeCache))
            await session.commit()

        _, fn = TOOLS["geocode_query"]
        with patch(
            "farmafacil.services.location._nominatim_search",
            return_value=[],
        ):
            out = await fn({"text": "MisspelledNonexistent"})
        assert "Sin resultados" in out


# ── geocode_reverse ──────────────────────────────────────────────────


class TestGeocodeReverseTool:
    @pytest.mark.asyncio
    async def test_returns_zone_name(self):
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(GeocodeCache))
            await session.commit()

        payload = {
            "display_name": "La Boyera, Caracas, Miranda",
            "importance": 0.5,
            "address": {
                "country_code": "ve",
                "suburb": "La Boyera",
                "state": "Miranda",
            },
        }
        _, fn = TOOLS["geocode_reverse"]
        with patch(
            "farmafacil.services.location._nominatim_reverse",
            return_value=payload,
        ):
            out = await fn({"lat": 10.4258, "lng": -66.8422})

        assert "La Boyera" in out

    @pytest.mark.asyncio
    async def test_missing_args_rejected(self):
        _, fn = TOOLS["geocode_reverse"]
        assert "inválidos" in (await fn({})).lower() or "Faltan" in await fn({})

    @pytest.mark.asyncio
    async def test_invalid_lat_rejected(self):
        _, fn = TOOLS["geocode_reverse"]
        out = await fn({"lat": "not-a-number", "lng": -66.0})
        assert "inválidos" in out.lower() or "Faltan" in out


# ── set_user_location ────────────────────────────────────────────────


class TestSetUserLocationTool:
    @pytest.mark.asyncio
    async def test_updates_existing_user_via_tool(self):
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(GeocodeCache))
            await session.execute(
                sa_delete(User).where(User.phone_number == "+88888888")
            )
            session.add(User(
                phone_number="+88888888",
                name="ToolUser",
                latitude=0.0,
                longitude=0.0,
                display_preference="grid",
            ))
            await session.commit()

        payload = [{
            "lat": "10.4258", "lon": "-66.8422",
            "display_name": "La Boyera, Caracas",
            "importance": 0.6, "name": "La Boyera",
            "address": {"city": "Caracas", "state": "Miranda"},
        }]
        _, fn = TOOLS["set_user_location"]
        with patch(
            "farmafacil.services.location._nominatim_search",
            return_value=payload,
        ):
            out = await fn({"phone": "+88888888", "query": "La Boyera"})

        assert out.startswith("✅")
        assert "ToolUser" in out

        async with async_session() as session:
            await session.execute(
                sa_delete(User).where(User.phone_number == "+88888888")
            )
            await session.commit()

    @pytest.mark.asyncio
    async def test_low_confidence_warning(self):
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(GeocodeCache))
            await session.execute(
                sa_delete(User).where(User.phone_number == "+87878787")
            )
            session.add(User(
                phone_number="+87878787",
                name="LowConf",
                latitude=0.0,
                longitude=0.0,
                display_preference="grid",
            ))
            await session.commit()

        payload = [{
            "lat": "10.3", "lon": "-66.8",
            "display_name": "Some random place far away",
            "importance": 0.1, "name": "Random",
            "address": {"state": "Miranda"},
        }]
        _, fn = TOOLS["set_user_location"]
        with patch(
            "farmafacil.services.location._nominatim_search",
            return_value=payload,
        ):
            out = await fn({"phone": "+87878787", "query": "AnythingObscure"})

        assert "baja confianza" in out

        async with async_session() as session:
            await session.execute(
                sa_delete(User).where(User.phone_number == "+87878787")
            )
            await session.commit()

    @pytest.mark.asyncio
    async def test_missing_args(self):
        _, fn = TOOLS["set_user_location"]
        assert "Faltan" in await fn({"phone": "+1"})  # no query
        assert "Faltan" in await fn({"query": "X"})  # no phone


# ── set_pharmacy_location ────────────────────────────────────────────


class TestSetPharmacyLocationTool:
    @pytest.mark.asyncio
    async def test_manual_coords_succeeds(self):
        from sqlalchemy import delete as sa_delete
        async with async_session() as session:
            await session.execute(sa_delete(PharmacyLocation).where(
                PharmacyLocation.external_id == "tool-test-1"
            ))
            session.add(PharmacyLocation(
                external_id="tool-test-1",
                pharmacy_chain="Independiente",
                name="ToolTest",
                name_lower="tooltest",
                city_code="CCS",
                latitude=0.0,
                longitude=0.0,
                is_active=True,
            ))
            await session.commit()
            result = await session.execute(
                PharmacyLocation.__table__.select().where(
                    PharmacyLocation.external_id == "tool-test-1"
                )
            )
            pharmacy_id = result.first().id

        _, fn = TOOLS["set_pharmacy_location"]
        out = await fn({
            "pharmacy_id": pharmacy_id,
            "lat": 10.5,
            "lng": -66.9,
        })
        assert out.startswith("✅")
        assert "ToolTest" in out

        async with async_session() as session:
            await session.execute(sa_delete(PharmacyLocation).where(
                PharmacyLocation.id == pharmacy_id
            ))
            await session.commit()

    @pytest.mark.asyncio
    async def test_invalid_pharmacy_id(self):
        _, fn = TOOLS["set_pharmacy_location"]
        out = await fn({"pharmacy_id": "not-int", "lat": 10, "lng": -66})
        assert "inválido" in out

    @pytest.mark.asyncio
    async def test_both_query_and_coords_rejected(self):
        _, fn = TOOLS["set_pharmacy_location"]
        out = await fn({
            "pharmacy_id": 1, "query": "x", "lat": 10, "lng": -66,
        })
        assert "SOLO" in out or "no ambos" in out


# ── geocode_health ───────────────────────────────────────────────────


class TestGeocodeHealthTool:
    @pytest.mark.asyncio
    async def test_returns_stats_block(self):
        _, fn = TOOLS["geocode_health"]
        out = await fn({})
        assert "Geocode cache health" in out
        assert "total rows" in out
        assert "TTL" in out

    @pytest.mark.asyncio
    async def test_invalid_days_falls_back_to_default(self):
        _, fn = TOOLS["geocode_health"]
        # Should not raise — default to 7 days
        out = await fn({"days": "not-an-int"})
        assert "Geocode cache health" in out
