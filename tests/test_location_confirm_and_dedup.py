"""Tests for two bug fixes introduced after v0.27.0.

Fix 1 — Product upsert deduplication (product_cache.py)
---------------------------------------------------------
The bulk INSERT ON CONFLICT crashed with
  "ON CONFLICT DO UPDATE command cannot affect row a second time"
when the search-results list contained duplicate (external_id, pharmacy_chain)
pairs.  A dedup block now removes duplicates before the DB insert, keeping the
last occurrence.  A similar guard was added for price_rows.

Fix 2 — Location confirmation flow (handler.py)
------------------------------------------------
When onboarding geocode returns a low-confidence result the handler now stashes
a list of numbered candidates in ``_pending_location_confirm`` and sets the
onboarding step to ``awaiting_location_confirm``.  The user sees a numbered
list (e.g. *1.* La Boyera, *2.* La Boyera del Sur, *3.* Otra ubicación)
and picks a number.  Typing an unrecognized string is treated as a new
location attempt.
"""

from decimal import Decimal
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, select

from farmafacil.bot import handler
from farmafacil.bot.handler import (
    _stash_location_confirm,
    _pop_location_confirm,
    _pending_location_confirm,
    handle_incoming_message,
)
from farmafacil.db.session import async_session
from farmafacil.models.database import Product, ProductKeyword, ProductPrice, SearchQuery, User
from farmafacil.models.schemas import DrugResult
from farmafacil.services.intent import Intent
from farmafacil.services.location import LocationResult, DEFAULT_MIN_CONFIDENCE
from farmafacil.services.product_cache import save_search_results
from farmafacil.services.users import get_or_create_user


# ---------------------------------------------------------------------------
# Helpers shared across both test classes
# ---------------------------------------------------------------------------

def _make_drug_result(**overrides) -> DrugResult:
    """Create a DrugResult with defaults for testing."""
    defaults = {
        "drug_name": "Losartan 50mg",
        "pharmacy_name": "Farmatodo",
        "price_bs": Decimal("15.50"),
        "full_price_bs": None,
        "discount_pct": None,
        "available": True,
        "url": "https://www.farmatodo.com.ve/losartan-50mg-999",
        "last_checked": datetime.now(tz=UTC),
        "requires_prescription": False,
        "image_url": None,
        "brand": "Glenmark",
        "drug_class": "Antihypertensives",
        "unit_label": "Tabletas",
        "unit_count": 30,
        "description": "Losartan potassium 50mg tablets",
        "stores_in_stock": 3,
        "stores_with_stock_ids": [1, 2, 3],
    }
    defaults.update(overrides)
    return DrugResult(**defaults)


# ---------------------------------------------------------------------------
# Phone numbers used by handler tests (must not collide with other test files)
# ---------------------------------------------------------------------------

_HANDLER_TEST_PHONES = {
    f"5493399010{i:03d}" for i in range(10)
}


@pytest.fixture(autouse=True)
async def _cleanup_catalog_tables():
    """Wipe catalog tables before every test so tests are independent."""
    async with async_session() as session:
        await session.execute(delete(ProductPrice))
        await session.execute(delete(SearchQuery))
        await session.execute(delete(ProductKeyword))
        await session.execute(delete(Product))
        await session.commit()
    yield
    # Also clean up handler test users created during handler tests
    async with async_session() as session:
        await session.execute(
            delete(User).where(User.phone_number.in_(_HANDLER_TEST_PHONES))
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Helper: seed a user in the DB for handler integration tests
# ---------------------------------------------------------------------------

async def _seed_user(
    phone: str,
    *,
    name: str | None = None,
    step: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    zone_name: str | None = None,
    city_code: str | None = None,
) -> None:
    """Create a user and set state in a single session."""
    await get_or_create_user(phone)
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone)
        )
        row = result.scalar_one()
        row.name = name
        row.onboarding_step = step
        row.latitude = latitude
        row.longitude = longitude
        row.zone_name = zone_name
        row.city_code = city_code
        await session.commit()


async def _fetch_user(phone: str) -> User:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone)
        )
        return result.scalar_one()


# ===========================================================================
# Fix 1: Product upsert deduplication
# ===========================================================================


class TestProductUpsertDeduplication:
    """Verify that duplicate (external_id, pharmacy_chain) entries in a single
    save_search_results call do NOT crash with the PostgreSQL/SQLite conflict
    constraint and that the last occurrence wins."""

    @pytest.mark.asyncio
    async def test_duplicate_external_id_same_chain_does_not_crash(self):
        """Two DrugResult entries with the same URL and pharmacy_name must not
        raise 'ON CONFLICT DO UPDATE command cannot affect row a second time'."""
        # Both entries share the same URL → same external_id, same pharmacy_chain
        drug_a = _make_drug_result(
            drug_name="Losartan 50mg",
            brand="BrandA",
            url="https://www.farmatodo.com.ve/losartan-50mg-999",
            pharmacy_name="Farmatodo",
        )
        drug_b = _make_drug_result(
            drug_name="Losartan 50mg",
            brand="BrandB",
            url="https://www.farmatodo.com.ve/losartan-50mg-999",
            pharmacy_name="Farmatodo",
        )

        # This must NOT raise — the dedup block removes the first occurrence
        await save_search_results("losartan", "CCS", [drug_a, drug_b])

        async with async_session() as session:
            products = (await session.execute(select(Product))).scalars().all()
            assert len(products) == 1, (
                "Duplicate entries must be deduplicated to a single DB row"
            )

    @pytest.mark.asyncio
    async def test_duplicate_dedup_keeps_last_occurrence(self):
        """When duplicates are present, the last entry in the list must win
        (its brand/data must be the one saved)."""
        drug_first = _make_drug_result(
            brand="FirstBrand",
            url="https://www.farmatodo.com.ve/losartan-50mg-999",
            pharmacy_name="Farmatodo",
        )
        drug_last = _make_drug_result(
            brand="LastBrand",
            url="https://www.farmatodo.com.ve/losartan-50mg-999",
            pharmacy_name="Farmatodo",
        )

        await save_search_results("losartan", "CCS", [drug_first, drug_last])

        async with async_session() as session:
            products = (await session.execute(select(Product))).scalars().all()
            assert len(products) == 1
            assert products[0].brand == "LastBrand", (
                "Dedup must keep the last occurrence in the input list"
            )

    @pytest.mark.asyncio
    async def test_three_duplicates_keeps_last(self):
        """Three entries for the same (external_id, pharmacy_chain) → one row,
        last brand wins."""
        shared_url = "https://www.farmatodo.com.ve/losartan-50mg-999"
        drugs = [
            _make_drug_result(brand="Alpha", url=shared_url, pharmacy_name="Farmatodo"),
            _make_drug_result(brand="Beta",  url=shared_url, pharmacy_name="Farmatodo"),
            _make_drug_result(brand="Gamma", url=shared_url, pharmacy_name="Farmatodo"),
        ]

        await save_search_results("losartan", "CCS", drugs)

        async with async_session() as session:
            products = (await session.execute(select(Product))).scalars().all()
            assert len(products) == 1
            assert products[0].brand == "Gamma"

    @pytest.mark.asyncio
    async def test_duplicates_across_different_chains_are_separate(self):
        """Same URL but different pharmacy_name are NOT duplicates — both
        should be stored as separate product rows."""
        shared_url = "https://www.farmatodo.com.ve/losartan-50mg-999"
        drug_farmatodo = _make_drug_result(
            brand="BrandX",
            url=shared_url,
            pharmacy_name="Farmatodo",
        )
        drug_locatel = _make_drug_result(
            brand="BrandX",
            url=shared_url,
            pharmacy_name="Locatel",
        )

        await save_search_results("losartan", "CCS", [drug_farmatodo, drug_locatel])

        async with async_session() as session:
            products = (await session.execute(select(Product))).scalars().all()
            chains = {p.pharmacy_chain for p in products}
            assert chains == {"Farmatodo", "Locatel"}, (
                "Different pharmacy_chain → separate products"
            )

    @pytest.mark.asyncio
    async def test_duplicate_price_rows_deduplicated(self):
        """When the same product ends up with duplicate price_rows for the same
        (product_id, city_code), the upsert must not crash and only one price
        record must exist per city."""
        # Inject duplicate entries at the product level which forces duplicate
        # price rows downstream.
        shared_url = "https://www.farmatodo.com.ve/losartan-50mg-999"
        drug_a = _make_drug_result(
            price_bs=Decimal("10.00"),
            url=shared_url,
            pharmacy_name="Farmatodo",
        )
        drug_b = _make_drug_result(
            price_bs=Decimal("12.00"),
            url=shared_url,
            pharmacy_name="Farmatodo",
        )

        # No crash expected
        await save_search_results("losartan", "CCS", [drug_a, drug_b])

        async with async_session() as session:
            prices = (await session.execute(select(ProductPrice))).scalars().all()
            # Only one price per (product_id, city_code)
            ccs_prices = [p for p in prices if p.city_code == "CCS"]
            assert len(ccs_prices) == 1

    @pytest.mark.asyncio
    async def test_no_duplicates_saves_all_distinct_products(self):
        """A list with no duplicates must save all products without dedup."""
        drugs = [
            _make_drug_result(
                drug_name="Losartan 50mg",
                url="https://www.farmatodo.com.ve/losartan-50mg-001",
                pharmacy_name="Farmatodo",
            ),
            _make_drug_result(
                drug_name="Losartan 100mg",
                url="https://www.farmatodo.com.ve/losartan-100mg-002",
                pharmacy_name="Farmatodo",
            ),
        ]

        await save_search_results("losartan", "CCS", drugs)

        async with async_session() as session:
            products = (await session.execute(select(Product))).scalars().all()
            assert len(products) == 2


# ===========================================================================
# Fix 2a: _stash_location_confirm / _pop_location_confirm unit tests
# ===========================================================================


class TestStashPopLocationConfirm:
    """Unit tests for the in-memory stash helpers.

    Since v0.29.3 the stash stores a ``list[dict]`` (numbered candidates)
    instead of a single dict.  These are pure in-memory operations — no
    DB, no async.
    """

    def setup_method(self):
        """Clear the global stash before every test."""
        _pending_location_confirm.clear()

    def test_stash_stores_candidates_list(self):
        """_stash_location_confirm must store a list of candidates."""
        candidates = [
            {"lat": 10.48, "lng": -66.87, "zone_name": "La Boyera", "city_code": "CCS",
             "display_name": "La Boyera, Caracas"},
        ]
        _stash_location_confirm("5491111111111", candidates)
        assert "5491111111111" in _pending_location_confirm
        assert _pending_location_confirm["5491111111111"] == candidates

    def test_pop_returns_stored_list(self):
        """_pop_location_confirm must return the stashed list."""
        candidates = [
            {"lat": 10.48, "lng": -66.87, "zone_name": "La Boyera", "city_code": "CCS",
             "display_name": "La Boyera, Caracas"},
        ]
        _stash_location_confirm("5491111111112", candidates)
        popped = _pop_location_confirm("5491111111112")
        assert popped == candidates

    def test_pop_removes_entry(self):
        """After pop, the sender key must no longer be in the stash."""
        candidates = [
            {"lat": 10.48, "lng": -66.87, "zone_name": "La Boyera", "city_code": "CCS",
             "display_name": "La Boyera, Caracas"},
        ]
        _stash_location_confirm("5491111111113", candidates)
        _pop_location_confirm("5491111111113")
        assert "5491111111113" not in _pending_location_confirm

    def test_pop_second_call_returns_none(self):
        """Calling pop twice for the same sender must return None."""
        candidates = [
            {"lat": 10.48, "lng": -66.87, "zone_name": "La Boyera", "city_code": "CCS",
             "display_name": "La Boyera, Caracas"},
        ]
        _stash_location_confirm("5491111111114", candidates)
        _pop_location_confirm("5491111111114")
        assert _pop_location_confirm("5491111111114") is None

    def test_pop_unknown_sender_returns_none(self):
        """Pop for a sender that was never stashed must return None."""
        assert _pop_location_confirm("9999999999999") is None

    def test_stash_overwrites_previous_entry(self):
        """A second stash for the same sender overwrites the first."""
        old = [{"lat": 1.0, "lng": 1.0, "zone_name": "Old", "city_code": "CCS",
                "display_name": "Old Place"}]
        new = [{"lat": 2.0, "lng": 2.0, "zone_name": "New", "city_code": "MAR",
                "display_name": "New Place"}]
        _stash_location_confirm("5491111111115", old)
        _stash_location_confirm("5491111111115", new)
        assert _pop_location_confirm("5491111111115") == new

    def test_stash_multiple_candidates(self):
        """Stash can hold multiple candidates for a single sender."""
        candidates = [
            {"lat": 10.48, "lng": -66.87, "zone_name": "La Boyera", "city_code": "CCS",
             "display_name": "La Boyera, Caracas"},
            {"lat": 10.50, "lng": -66.90, "zone_name": "El Cafetal", "city_code": "CCS",
             "display_name": "El Cafetal, Caracas"},
        ]
        _stash_location_confirm("5491111111116", candidates)
        popped = _pop_location_confirm("5491111111116")
        assert len(popped) == 2
        assert popped[0]["zone_name"] == "La Boyera"
        assert popped[1]["zone_name"] == "El Cafetal"


# ===========================================================================
# Fix 2b: handler integration tests — awaiting_location_confirm flow
# ===========================================================================

# Shared low-confidence LocationResult used across handler integration tests
_LOW_CONF_RESULT = LocationResult(
    lat=10.48,
    lng=-66.87,
    display_name="La Boyera, Caracas, Venezuela",
    confidence=DEFAULT_MIN_CONFIDENCE - 0.05,  # explicitly below threshold
    source="forward",
    city_code="CCS",
    zone_name="La Boyera",
    alternatives=[
        {
            "lat": 10.50,
            "lng": -66.90,
            "display_name": "La Boyera del Sur, Miranda, Venezuela",
            "confidence": 0.15,
            "city_code": "CCS",
            "zone_name": "La Boyera del Sur",
        },
    ],
)

_HIGH_CONF_RESULT = LocationResult(
    lat=10.49,
    lng=-66.85,
    display_name="Chacao, Caracas, Venezuela",
    confidence=DEFAULT_MIN_CONFIDENCE + 0.2,  # explicitly above threshold
    source="forward",
    city_code="CCS",
    zone_name="Chacao",
)


class TestAwaitingLocationConfirmFlow:
    """Integration tests for the numbered location alternatives flow.

    v0.29.3 replaced the old sí/no confirmation with a numbered list:
    the user sees candidates (1, 2, …) plus "Otra ubicación" as the
    last option.  Typing a number picks a candidate; typing text is
    treated as a new location attempt.

    All external calls (WhatsApp, LLM, geocoder) are mocked.
    """

    # ── Helper to build a candidates list matching the stash format ──

    @staticmethod
    def _candidates_from(result: LocationResult) -> list[dict]:
        """Mirror what _offer_location_alternatives builds."""
        candidates = [{
            "lat": result.lat,
            "lng": result.lng,
            "zone_name": result.zone_name,
            "city_code": result.city_code,
            "display_name": result.display_name,
        }]
        for alt in result.alternatives:
            candidates.append({
                "lat": alt["lat"],
                "lng": alt["lng"],
                "zone_name": alt.get("zone_name", "Unknown"),
                "city_code": alt.get("city_code", "CCS"),
                "display_name": alt["display_name"],
            })
        return candidates

    # -----------------------------------------------------------------------
    # Test: low-confidence geocode → stash + numbered alternatives message
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_low_confidence_geocode_shows_numbered_alternatives(self):
        """When the geocode result is low-confidence, the handler must:
        - set onboarding_step to 'awaiting_location_confirm'
        - NOT call update_user_location yet
        - send a numbered list of alternatives
        """
        phone = "5493399010000"
        await _seed_user(phone, name="Ana", step="awaiting_location")

        intent = Intent(action="unknown", detected_location="La Boyera")

        with patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch(
            "farmafacil.bot.handler._resolve_location",
            new=AsyncMock(return_value=_LOW_CONF_RESULT),
        ), patch(
            "farmafacil.bot.handler._name_matches_query",
            return_value=False,
        ), patch.object(
            handler, "update_user_location", new=AsyncMock(),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "La Boyera")

        mock_update_loc.assert_not_awaited()

        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step == "awaiting_location_confirm"

        # Must contain numbered alternatives and "Otra ubicación"
        sent_text = mock_send.await_args.args[1]
        assert "*1.*" in sent_text
        assert "Otra ubicación" in sent_text
        assert "Escribe el número" in sent_text

    # -----------------------------------------------------------------------
    # Test: user types "1" → picks first candidate, saves location
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_pick_number_1_saves_first_candidate(self):
        """Typing '1' must pick the first candidate and complete onboarding."""
        phone = "5493399010001"
        await _seed_user(phone, name="Ana", step="awaiting_location_confirm")

        candidates = self._candidates_from(_LOW_CONF_RESULT)
        _stash_location_confirm(phone, candidates)

        mock_user = MagicMock()
        mock_user.name = "Ana"

        with patch.object(
            handler, "update_user_location", new=AsyncMock(return_value=mock_user),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "1")

        # First candidate must be saved
        mock_update_loc.assert_awaited_once_with(
            phone,
            candidates[0]["lat"],
            candidates[0]["lng"],
            candidates[0]["zone_name"],
            candidates[0]["city_code"],
        )
        sent_text = mock_send.await_args.args[1]
        assert "Listo" in sent_text or "Ana" in sent_text

    @pytest.mark.asyncio
    async def test_pick_number_2_saves_second_candidate(self):
        """Typing '2' must pick the second candidate (first alternative)."""
        phone = "5493399010002"
        await _seed_user(phone, name="Ana", step="awaiting_location_confirm")

        candidates = self._candidates_from(_LOW_CONF_RESULT)
        assert len(candidates) >= 2, "Test requires at least 2 candidates"
        _stash_location_confirm(phone, candidates)

        mock_user = MagicMock()
        mock_user.name = "Ana"

        with patch.object(
            handler, "update_user_location", new=AsyncMock(return_value=mock_user),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "2")

        mock_update_loc.assert_awaited_once_with(
            phone,
            candidates[1]["lat"],
            candidates[1]["lng"],
            candidates[1]["zone_name"],
            candidates[1]["city_code"],
        )

    # -----------------------------------------------------------------------
    # Test: user picks "Otra ubicación" number → reset to awaiting_location
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_pick_otra_ubicacion_number_reasks(self):
        """Typing the 'Otra ubicación' number must reset to awaiting_location."""
        phone = "5493399010003"
        await _seed_user(phone, name="Ana", step="awaiting_location_confirm")

        candidates = self._candidates_from(_LOW_CONF_RESULT)
        _stash_location_confirm(phone, candidates)
        otra_num = str(len(candidates) + 1)  # "3" when 2 candidates

        with patch.object(
            handler, "update_user_location", new=AsyncMock(),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, otra_num)

        mock_update_loc.assert_not_awaited()

        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step == "awaiting_location"

        sent_text = mock_send.await_args.args[1]
        assert "Sin problema" in sent_text or "zona" in sent_text.lower()

    # -----------------------------------------------------------------------
    # Test: unrecognized text → treated as new location attempt
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_unrecognized_text_treated_as_new_location_high_confidence(self):
        """Typing a zone name instead of a number must re-geocode.
        If high-confidence, the location is saved and onboarding completes."""
        phone = "5493399010005"
        await _seed_user(phone, name="Ana", step="awaiting_location_confirm")

        candidates = self._candidates_from(_LOW_CONF_RESULT)
        _stash_location_confirm(phone, candidates)

        mock_user = MagicMock()
        mock_user.name = "Ana"

        with patch(
            "farmafacil.bot.handler._resolve_location",
            new=AsyncMock(return_value=_HIGH_CONF_RESULT),
        ), patch(
            "farmafacil.bot.handler._name_matches_query",
            return_value=True,
        ), patch.object(
            handler, "update_user_location", new=AsyncMock(return_value=mock_user),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "Chacao")

        mock_update_loc.assert_awaited_once()
        assert _pop_location_confirm(phone) is None

    @pytest.mark.asyncio
    async def test_unrecognized_text_low_confidence_shows_alternatives_again(self):
        """Typing a zone that geocodes to low-confidence must show
        numbered alternatives again (not the old sí/no prompt)."""
        phone = "5493399010006"
        await _seed_user(phone, name="Ana", step="awaiting_location_confirm")

        candidates = self._candidates_from(_LOW_CONF_RESULT)
        _stash_location_confirm(phone, candidates)

        new_low_result = LocationResult(
            lat=8.0,
            lng=-63.0,
            display_name="Somewhere, Venezuela",
            confidence=DEFAULT_MIN_CONFIDENCE - 0.1,
            source="forward",
            city_code="BAR",
            zone_name="Somewhere",
        )

        with patch(
            "farmafacil.bot.handler._resolve_location",
            new=AsyncMock(return_value=new_low_result),
        ), patch(
            "farmafacil.bot.handler._name_matches_query",
            return_value=False,
        ), patch.object(
            handler, "update_user_location", new=AsyncMock(),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "Somewhere obscure")

        mock_update_loc.assert_not_awaited()

        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step == "awaiting_location_confirm"

        # Must show numbered alternatives (not old "¿es correcto?")
        sent_text = mock_send.await_args.args[1]
        assert "*1.*" in sent_text
        assert "Otra ubicación" in sent_text

    @pytest.mark.asyncio
    async def test_unrecognized_text_geocode_not_found(self):
        """Typing text that geocodes to None must send MSG_LOCATION_NOT_FOUND."""
        phone = "5493399010007"
        await _seed_user(phone, name="Ana", step="awaiting_location_confirm")

        candidates = self._candidates_from(_LOW_CONF_RESULT)
        _stash_location_confirm(phone, candidates)

        with patch(
            "farmafacil.bot.handler._resolve_location",
            new=AsyncMock(return_value=None),
        ), patch.object(
            handler, "update_user_location", new=AsyncMock(),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "xyzabc123unknown")

        mock_update_loc.assert_not_awaited()
        sent_text = mock_send.await_args.args[1]
        assert "No logré ubicar" in sent_text or "zona" in sent_text.lower()

    # -----------------------------------------------------------------------
    # Test: stash expired → reset to awaiting_location
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_stash_expired_reasks_location(self):
        """If the stash has been popped (expiry or duplicate message),
        any input must reset to awaiting_location with a friendly message."""
        phone = "5493399010008"
        await _seed_user(phone, name="Ana", step="awaiting_location_confirm")

        # Do NOT stash anything — simulates expiry

        with patch.object(
            handler, "update_user_location", new=AsyncMock(),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "1")

        mock_update_loc.assert_not_awaited()

        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step == "awaiting_location"

        sent_text = mock_send.await_args.args[1]
        assert "venció" in sent_text or "zona" in sent_text.lower()

    # -----------------------------------------------------------------------
    # Test: single candidate → still shows numbered list with 1 + Otra
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_single_candidate_shows_two_options(self):
        """Even with only 1 Nominatim result, the user should see '1.' and
        '2. Otra ubicación'."""
        phone = "5493399010009"
        await _seed_user(phone, name="Ana", step="awaiting_location")

        single_result = LocationResult(
            lat=10.48,
            lng=-66.87,
            display_name="La Boyera, Caracas",
            confidence=DEFAULT_MIN_CONFIDENCE - 0.1,
            source="forward",
            city_code="CCS",
            zone_name="La Boyera",
            alternatives=[],  # no alternatives
        )

        intent = Intent(action="unknown", detected_location="La Boyera")

        with patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch(
            "farmafacil.bot.handler._resolve_location",
            new=AsyncMock(return_value=single_result),
        ), patch(
            "farmafacil.bot.handler._name_matches_query",
            return_value=False,
        ), patch.object(
            handler, "update_user_location", new=AsyncMock(),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "La Boyera")

        sent_text = mock_send.await_args.args[1]
        assert "*1.*" in sent_text
        assert "*2.* Otra ubicación" in sent_text
        # Must NOT have a *3.* option
        assert "*3.*" not in sent_text

    # -----------------------------------------------------------------------
    # Test: out-of-range number → treated as new location attempt
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_out_of_range_number_treated_as_new_location(self):
        """A number beyond the 'Otra' option must be treated as
        unrecognized text and re-geocoded."""
        phone = "5493399010004"
        await _seed_user(phone, name="Ana", step="awaiting_location_confirm")

        candidates = self._candidates_from(_LOW_CONF_RESULT)
        _stash_location_confirm(phone, candidates)

        with patch(
            "farmafacil.bot.handler._resolve_location",
            new=AsyncMock(return_value=None),
        ), patch.object(
            handler, "update_user_location", new=AsyncMock(),
        ), patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            # "99" is way beyond the valid range
            await handle_incoming_message(phone, "99")

        sent_text = mock_send.await_args.args[1]
        assert "No logré ubicar" in sent_text
