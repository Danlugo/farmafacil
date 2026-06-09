"""Tests for FarmaBien and Farmarket store backfill functions.

Validates that _backfill_farmabien_stores() and _backfill_farmarket_stores()
correctly parse store data from their respective HTML pages and upsert
into pharmacy_locations. Also tests the _map_ve_state_to_city() helper.
"""

import hashlib
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from farmafacil.services.store_backfill import (
    _backfill_farmabien_stores,
    _backfill_farmarket_stores,
    _map_ve_state_to_city,
)


# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------

FARMABIEN_STORE_JSON = json.dumps([
    {
        "id": 41,
        "country": "VE",
        "nickname": "ALTOS DE BARINAS",
        "address": "Av. Prolongación Av. 23 de Enero",
        "phone": "0274-5551234",
        "mobile": "0414-9507075",
        "latitude": 8.61574,
        "longitude": -70.2452,
        "locality": "Barinas",
        "state": "Barinas",
        "store": "Farmacia FarmaÉxito Alto Barinas, C.A.",
    },
    {
        "id": 95,
        "country": "VE",
        "nickname": "ARAPUEY",
        "address": "Mérida Arapuey",
        "phone": "",
        "mobile": "",
        "latitude": 9.26073,
        "longitude": -70.9506,
        "locality": "Arapuey",
        "state": "Mérida",
        "store": "Farmacia Arapuey,C.A.",
    },
    {
        "id": 200,
        "country": "CO",
        "nickname": "BOGOTA CENTRO",
        "address": "Calle 123",
        "phone": "",
        "mobile": "",
        "latitude": 4.6,
        "longitude": -74.1,
        "locality": "Bogotá",
        "state": "Cundinamarca",
        "store": "Farmacia Bogotá",
    },
])

FARMABIEN_HTML_INLINE = (
    '<html><body>'
    '"defaultStores":' + FARMABIEN_STORE_JSON + ','
    '</body></html>'
)

# Multiline variant — JSON array spans multiple lines (re.DOTALL needed)
FARMABIEN_HTML_MULTILINE = (
    '<html><body>\n'
    '"defaultStores":[\n'
    + ",\n".join(
        json.dumps(s, ensure_ascii=False)
        for s in json.loads(FARMABIEN_STORE_JSON)
    )
    + '\n],\n'
    '</body></html>'
)

# Escaped variant — realistic Next.js RSC payload (\" around all JSON keys/values)
FARMABIEN_HTML_ESCAPED = (
    '<html><body><script>self.__next_f.push([1,"'
    + '\\"defaultStores\\":'
    + FARMABIEN_STORE_JSON.replace('"', '\\"')
    + ',"])</script></body></html>'
)


FARMARKET_HTML = """
<html><body>
<div class="store-item">
  <h5>La Trinidad</h5>
  <p>Direción: <span>Av. Gonzalez Rincones, Quinta Cafea</span></p>
  <p>Teléfono: <span>0212-945-17-30</span></p>
  <a href="https://www.google.com/maps/dir//Farmarket+La+Trinidad/@10.4340416,-66.8628845,16z/">Ir a google Maps</a>
</div>
<div class="store-item">
  <h5>El Cafetal</h5>
  <p>Direción: <span>Av Boulevard con Avenida Santa Ana</span></p>
  <p>Teléfono: <span></span></p>
  <a href="https://www.google.com/maps/dir//Farmarket+El+Cafetal/@10.4613131,-66.8294831,16z/">Ir a google Maps</a>
</div>
<div class="store-item">
  <h5>Prados del Este</h5>
  <p>Direción: <span>Av. Amazonas con Calle Estanque</span></p>
  <p>Teléfono: <span>0424-152-19-60</span></p>
  <a href="https://www.google.com/maps/dir//Farmarket+Prados/@10.4449875,-66.8907519,16z/">Ir a google Maps</a>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# Helper to create mock DB session
# ---------------------------------------------------------------------------

def _make_mock_session(existing_record=None):
    """Build an async mock session that tracks add() calls.

    Args:
        existing_record: If provided, scalar_one_or_none returns this object
            (simulates an existing DB record). Defaults to None (no existing).
    """
    session = AsyncMock()
    session.added = []

    def track_add(obj):
        session.added.append(obj)

    session.add = MagicMock(side_effect=track_add)

    # scalar_one_or_none is a SYNC method on SQLAlchemy Result — use MagicMock
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = existing_record
    session.execute = AsyncMock(return_value=result_mock)

    return session


# ---------------------------------------------------------------------------
# Tests: _map_ve_state_to_city
# ---------------------------------------------------------------------------

class TestMapVeStateToCity:
    """Venezuelan state → city code mapping."""

    @pytest.mark.parametrize("state,locality,expected", [
        ("Distrito Capital", "", "CCS"),
        ("Miranda", "", "CCS"),
        ("Mérida", "", "MER"),
        ("merida", "", "MER"),
        ("Táchira", "", "SAC"),
        ("Zulia", "", "MCBO"),
        ("Lara", "", "BAR"),
        ("Barinas", "", "BAR"),
        ("Anzoátegui", "", "PDM"),
        ("Trujillo", "", "MER"),
        ("Portuguesa", "", "BAR"),
        ("Yaracuy", "", "BAR"),
        ("Unknown State", "", "CCS"),
        ("Zulia", "Maracaibo", "MCBO"),
        ("Zulia", "Ciudad Ojeda", "MCBO"),
        ("Zulia", "San Francisco", "MCBO"),
    ], ids=[
        "distrito_capital", "miranda", "merida_accent", "merida_no_accent",
        "tachira", "zulia", "lara", "barinas", "anzoategui", "trujillo",
        "portuguesa", "yaracuy", "unknown_defaults_ccs",
        "zulia_maracaibo", "zulia_ciudad_ojeda", "zulia_san_francisco",
    ])
    def test_state_mapping(self, state: str, locality: str, expected: str):
        assert _map_ve_state_to_city(state, locality) == expected


# ---------------------------------------------------------------------------
# Tests: _backfill_farmabien_stores
# ---------------------------------------------------------------------------

class TestBackfillFarmabienStores:
    """FarmaBien store backfill from Next.js page."""

    async def test_happy_path_inserts_ve_stores_only(self):
        """Should insert Venezuelan stores and skip Colombian ones."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = (
            '<html>"defaultStores":' + FARMABIEN_STORE_JSON + ',</html>'
        )
        mock_response.raise_for_status = MagicMock()

        mock_session = _make_mock_session()

        with patch("farmafacil.services.store_backfill.httpx.AsyncClient") as mock_client_cls, \
             patch("farmafacil.services.store_backfill.async_session") as mock_session_ctx:

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _backfill_farmabien_stores()

        # 2 VE stores inserted, 1 CO filtered out
        assert result == 2
        assert len(mock_session.added) == 2

        # Check first store
        store1 = mock_session.added[0]
        assert store1.pharmacy_chain == "FarmaBien"
        assert store1.external_id == "41"
        assert store1.name == "ALTOS DE BARINAS"
        assert store1.latitude == 8.61574
        assert store1.longitude == -70.2452
        assert store1.city_code == "BAR"
        assert store1.phone == "0274-5551234"  # phone preferred over mobile

        # Second store has no phone/mobile
        store2 = mock_session.added[1]
        assert store2.name == "ARAPUEY"
        assert store2.city_code == "MER"
        assert store2.phone is None

    async def test_http_error_returns_zero(self):
        """Network failure should log and return 0, not crash."""
        with patch("farmafacil.services.store_backfill.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _backfill_farmabien_stores()
        assert result == 0

    async def test_missing_default_stores_returns_zero(self):
        """Page without defaultStores JSON should return 0."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>No stores here</body></html>"
        mock_response.raise_for_status = MagicMock()

        with patch("farmafacil.services.store_backfill.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _backfill_farmabien_stores()
        assert result == 0

    async def test_existing_store_updates_instead_of_inserting(self):
        """Existing store should be updated, not duplicated."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        # Single VE store
        single_store = json.dumps([{
            "id": 41, "country": "VE", "nickname": "TEST",
            "address": "New Address", "phone": "0212-1234567", "mobile": "",
            "latitude": 8.6, "longitude": -70.2, "locality": "Barinas",
            "state": "Barinas", "store": "Test",
        }])
        mock_response.text = f'"defaultStores":{single_store},'
        mock_response.raise_for_status = MagicMock()

        # Simulate existing record
        existing = MagicMock()
        existing.address = "Old Address"
        existing.latitude = 8.5
        existing.longitude = -70.1
        existing.phone = None

        mock_session = _make_mock_session(existing_record=existing)

        with patch("farmafacil.services.store_backfill.httpx.AsyncClient") as mock_client_cls, \
             patch("farmafacil.services.store_backfill.async_session") as mock_session_ctx:

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _backfill_farmabien_stores()

        assert result == 0  # No new inserts
        assert existing.address == "New Address"  # Updated
        assert existing.latitude == 8.6
        assert existing.phone == "0212-1234567"

    async def test_store_without_id_is_skipped(self):
        """Stores missing an 'id' field should be skipped."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        stores_no_id = json.dumps([{
            "country": "VE", "nickname": "NO ID",
            "address": "Addr", "phone": "", "mobile": "",
            "latitude": 8.0, "longitude": -70.0,
            "locality": "X", "state": "Mérida",
        }])
        mock_response.text = f'"defaultStores":{stores_no_id},'
        mock_response.raise_for_status = MagicMock()

        mock_session = _make_mock_session()

        with patch("farmafacil.services.store_backfill.httpx.AsyncClient") as mock_client_cls, \
             patch("farmafacil.services.store_backfill.async_session") as mock_session_ctx:

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _backfill_farmabien_stores()

        assert result == 0
        assert len(mock_session.added) == 0

    async def test_nickname_fallback_to_store_name(self):
        """When nickname is empty, store name should be used."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        stores = json.dumps([{
            "id": 99, "country": "VE", "nickname": "",
            "address": "Addr", "phone": "", "mobile": "0414-1234567",
            "latitude": 10.0, "longitude": -66.0,
            "locality": "Caracas", "state": "Distrito Capital",
            "store": "Farmacia Gran Venezuela",
        }])
        mock_response.text = f'"defaultStores":{stores},'
        mock_response.raise_for_status = MagicMock()

        mock_session = _make_mock_session()

        with patch("farmafacil.services.store_backfill.httpx.AsyncClient") as mock_client_cls, \
             patch("farmafacil.services.store_backfill.async_session") as mock_session_ctx:

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _backfill_farmabien_stores()

        assert result == 1
        assert mock_session.added[0].name == "Farmacia Gran Venezuela"
        assert mock_session.added[0].phone == "0414-1234567"  # Falls back to mobile

    async def test_multiline_json_parsed_with_dotall(self):
        """JSON array spanning multiple lines must be parsed (re.DOTALL)."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = FARMABIEN_HTML_MULTILINE
        mock_response.raise_for_status = MagicMock()

        mock_session = _make_mock_session()

        with patch("farmafacil.services.store_backfill.httpx.AsyncClient") as mock_client_cls, \
             patch("farmafacil.services.store_backfill.async_session") as mock_session_ctx:

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _backfill_farmabien_stores()

        # 2 VE stores (CO filtered), even with multiline JSON
        assert result == 2

    async def test_escaped_nextjs_rsc_format(self):
        """Realistic Next.js RSC payload with escaped quotes must parse."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = FARMABIEN_HTML_ESCAPED
        mock_response.raise_for_status = MagicMock()

        mock_session = _make_mock_session()

        with patch("farmafacil.services.store_backfill.httpx.AsyncClient") as mock_client_cls, \
             patch("farmafacil.services.store_backfill.async_session") as mock_session_ctx:

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _backfill_farmabien_stores()

        # 2 VE stores from escaped JSON, CO filtered out
        assert result == 2
        assert mock_session.added[0].pharmacy_chain == "FarmaBien"


# ---------------------------------------------------------------------------
# Tests: _backfill_farmarket_stores
# ---------------------------------------------------------------------------

class TestBackfillFarmarketStores:
    """Farmarket store backfill from static HTML page."""

    async def test_happy_path_extracts_stores(self):
        """Should parse stores from Google Maps links in HTML."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = FARMARKET_HTML
        mock_response.raise_for_status = MagicMock()

        mock_session = _make_mock_session()

        with patch("farmafacil.services.store_backfill.httpx.AsyncClient") as mock_client_cls, \
             patch("farmafacil.services.store_backfill.async_session") as mock_session_ctx:

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _backfill_farmarket_stores()

        assert result == 3
        assert len(mock_session.added) == 3

        # All stores should be in Caracas
        for store in mock_session.added:
            assert store.pharmacy_chain == "Farmarket"
            assert store.city_code == "CCS"
            assert store.latitude is not None
            assert store.longitude is not None

        # Check first store name
        names = [s.name for s in mock_session.added]
        assert "La Trinidad" in names

    async def test_http_error_returns_zero(self):
        """Network failure should log and return 0."""
        with patch("farmafacil.services.store_backfill.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                side_effect=httpx.TimeoutException("Timed out")
            )
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _backfill_farmarket_stores()
        assert result == 0

    async def test_empty_page_returns_zero(self):
        """Page without Google Maps links should return 0."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>Coming soon</body></html>"
        mock_response.raise_for_status = MagicMock()

        mock_session = _make_mock_session()

        with patch("farmafacil.services.store_backfill.httpx.AsyncClient") as mock_client_cls, \
             patch("farmafacil.services.store_backfill.async_session") as mock_session_ctx:

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _backfill_farmarket_stores()
        assert result == 0

    async def test_duplicate_coordinates_deduplicated(self):
        """Same store appearing twice (same coords) should be deduplicated."""
        html = """
        <html><body>
        <div class="store-item">
          <h5>Store A</h5>
          <p>Direción: <span>Address A</span></p>
          <a href="https://www.google.com/maps/dir//X/@10.434,-66.862,16z/">Ir a google Maps</a>
        </div>
        <div class="store-item">
          <h5>Store A Duplicate</h5>
          <p>Direción: <span>Address A again</span></p>
          <a href="https://www.google.com/maps/dir//X/@10.434,-66.862,16z/">Ir a google Maps</a>
        </div>
        </body></html>
        """
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = html
        mock_response.raise_for_status = MagicMock()

        mock_session = _make_mock_session()

        with patch("farmafacil.services.store_backfill.httpx.AsyncClient") as mock_client_cls, \
             patch("farmafacil.services.store_backfill.async_session") as mock_session_ctx:

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _backfill_farmarket_stores()

        # Only 1 store should be inserted, not 2
        assert result == 1

    async def test_ext_id_is_deterministic(self):
        """ext_id must be identical across runs (no Python hash randomization)."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = FARMARKET_HTML
        mock_response.raise_for_status = MagicMock()

        mock_session = _make_mock_session()

        with patch("farmafacil.services.store_backfill.httpx.AsyncClient") as mock_client_cls, \
             patch("farmafacil.services.store_backfill.async_session") as mock_session_ctx:

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await _backfill_farmarket_stores()

        # Verify first store has a deterministic hashlib-based ext_id
        store = mock_session.added[0]
        expected = f"fm-{hashlib.md5(f'{store.name}{store.latitude}{store.longitude}'.encode()).hexdigest()[:8]}"
        assert store.external_id == expected
        assert store.external_id.startswith("fm-")
        assert len(store.external_id) == 11  # "fm-" + 8 hex chars
