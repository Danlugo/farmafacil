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
the candidate in ``_pending_location_confirm`` and sets the onboarding step to
``awaiting_location_confirm`` instead of auto-accepting.  The user is prompted
"¿es correcto?".  Responding "sí" saves the location; "no" discards it and
re-asks; anything else is treated as a new location attempt.
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

    These are pure in-memory operations — no DB, no async.
    """

    def setup_method(self):
        """Clear the global stash before every test."""
        _pending_location_confirm.clear()

    def test_stash_stores_result(self):
        """_stash_location_confirm must store the result keyed by sender."""
        result = {"lat": 10.48, "lng": -66.87, "zone_name": "La Boyera", "city_code": "CCS"}
        _stash_location_confirm("5491111111111", result)
        assert "5491111111111" in _pending_location_confirm
        assert _pending_location_confirm["5491111111111"] == result

    def test_pop_returns_stored_result(self):
        """_pop_location_confirm must return the stashed dict."""
        result = {"lat": 10.48, "lng": -66.87, "zone_name": "La Boyera", "city_code": "CCS"}
        _stash_location_confirm("5491111111112", result)
        popped = _pop_location_confirm("5491111111112")
        assert popped == result

    def test_pop_removes_entry(self):
        """After pop, the sender key must no longer be in the stash."""
        result = {"lat": 10.48, "lng": -66.87, "zone_name": "La Boyera", "city_code": "CCS"}
        _stash_location_confirm("5491111111113", result)
        _pop_location_confirm("5491111111113")
        assert "5491111111113" not in _pending_location_confirm

    def test_pop_second_call_returns_none(self):
        """Calling pop twice for the same sender must return None on the second call."""
        result = {"lat": 10.48, "lng": -66.87, "zone_name": "La Boyera", "city_code": "CCS"}
        _stash_location_confirm("5491111111114", result)
        _pop_location_confirm("5491111111114")
        assert _pop_location_confirm("5491111111114") is None

    def test_pop_unknown_sender_returns_none(self):
        """Pop for a sender that was never stashed must return None."""
        assert _pop_location_confirm("9999999999999") is None

    def test_stash_overwrites_previous_entry(self):
        """A second stash for the same sender overwrites the first."""
        result_old = {"lat": 1.0, "lng": 1.0, "zone_name": "OldZone", "city_code": "CCS"}
        result_new = {"lat": 2.0, "lng": 2.0, "zone_name": "NewZone", "city_code": "MAR"}
        _stash_location_confirm("5491111111115", result_old)
        _stash_location_confirm("5491111111115", result_new)
        assert _pop_location_confirm("5491111111115") == result_new


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
    """Integration tests for the new ``awaiting_location_confirm`` handler step.

    All external calls (WhatsApp, LLM, geocoder) are mocked so tests run
    without network access or a real Nominatim endpoint.
    """

    # -----------------------------------------------------------------------
    # Test: low-confidence geocode → stash + set awaiting_location_confirm
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_low_confidence_geocode_stashes_and_asks_confirmation(self):
        """When the geocode result is low-confidence, the handler must:
        - set onboarding_step to 'awaiting_location_confirm'
        - NOT call update_user_location yet
        - ask the user to confirm ('¿es correcto?')
        """
        phone = "5493399010000"
        await _seed_user(phone, name="Ana", step="awaiting_location")

        intent = Intent(action="unknown", detected_location="La Boyera")

        with patch.object(
            handler, "classify_intent", new=AsyncMock(return_value=intent),
        ), patch(
            "farmafacil.services.location.resolve",
            new=AsyncMock(return_value=_LOW_CONF_RESULT),
        ), patch(
            "farmafacil.services.location._name_matches_query",
            return_value=False,  # forces the low-confidence branch
        ), patch.object(
            handler, "update_user_location", new=AsyncMock(),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "La Boyera")

        # Location must NOT have been saved yet
        mock_update_loc.assert_not_awaited()

        # Step must advance to awaiting_location_confirm
        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step == "awaiting_location_confirm"

        # Must ask for confirmation
        sent_text = mock_send.await_args.args[1]
        assert "¿es correcto?" in sent_text or "es correcto" in sent_text.lower()

    # -----------------------------------------------------------------------
    # Test: user responds "sí" → location saved, onboarding complete
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_si_response_saves_location_and_completes_onboarding(self):
        """Responding 'sí' to the confirmation prompt must save the stashed
        location and set onboarding_step to None (complete)."""
        phone = "5493399010001"
        await _seed_user(phone, name="Ana", step="awaiting_location_confirm")

        # Pre-stash a candidate
        candidate = {
            "lat": _LOW_CONF_RESULT.lat,
            "lng": _LOW_CONF_RESULT.lng,
            "zone_name": _LOW_CONF_RESULT.zone_name,
            "city_code": _LOW_CONF_RESULT.city_code,
        }
        _stash_location_confirm(phone, candidate)

        # Build a mock User returned by update_user_location
        mock_user = MagicMock()
        mock_user.name = "Ana"

        with patch.object(
            handler, "update_user_location", new=AsyncMock(return_value=mock_user),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "sí")

        # Location must have been saved with the stashed coordinates
        mock_update_loc.assert_awaited_once_with(
            phone,
            candidate["lat"],
            candidate["lng"],
            candidate["zone_name"],
            candidate["city_code"],
        )

        # Confirmation message must include success indicator
        sent_text = mock_send.await_args.args[1]
        assert "Listo" in sent_text or "Ana" in sent_text

    @pytest.mark.asyncio
    async def test_si_variants_all_confirm(self):
        """Multiple affirmative answers ('si', 'yes', 'ok', '1') must all
        trigger location save."""
        affirmative_answers = ["si", "sí", "yes", "ok", "1", "correcto"]

        for answer in affirmative_answers:
            phone = f"5493399010002"
            # Re-create user and stash for each iteration
            await _seed_user(phone, name="Test", step="awaiting_location_confirm")
            candidate = {
                "lat": 10.48, "lng": -66.87,
                "zone_name": "La Boyera", "city_code": "CCS",
            }
            _stash_location_confirm(phone, candidate)

            mock_user = MagicMock()
            mock_user.name = "Test"

            with patch.object(
                handler, "update_user_location", new=AsyncMock(return_value=mock_user),
            ) as mock_update_loc, patch.object(
                handler, "send_text_message", new=AsyncMock(),
            ):
                await handle_incoming_message(phone, answer)

            mock_update_loc.assert_awaited_once(), f"'{answer}' should have confirmed"

    # -----------------------------------------------------------------------
    # Test: user responds "no" → step goes back to awaiting_location
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_no_response_discards_candidate_and_reasks(self):
        """Responding 'no' must discard the stashed candidate, set step back
        to 'awaiting_location', and ask the user to re-enter their zone."""
        phone = "5493399010003"
        await _seed_user(phone, name="Ana", step="awaiting_location_confirm")

        candidate = {
            "lat": _LOW_CONF_RESULT.lat,
            "lng": _LOW_CONF_RESULT.lng,
            "zone_name": _LOW_CONF_RESULT.zone_name,
            "city_code": _LOW_CONF_RESULT.city_code,
        }
        _stash_location_confirm(phone, candidate)

        with patch.object(
            handler, "update_user_location", new=AsyncMock(),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "no")

        # Location must NOT have been saved
        mock_update_loc.assert_not_awaited()

        # Step must be reset to awaiting_location
        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step == "awaiting_location"

        # Stash must be cleared
        assert _pop_location_confirm(phone) is None

        # Must re-ask for location with guidance
        sent_text = mock_send.await_args.args[1]
        assert "zona" in sent_text.lower() or "ubicaci" in sent_text.lower() or "Sin problema" in sent_text

    @pytest.mark.asyncio
    async def test_no_variants_all_deny(self):
        """Multiple denial answers ('nop', 'nope', 'nel', '0') must all
        discard the candidate and reset to awaiting_location."""
        denial_answers = ["no", "n", "nop", "nope", "nel", "0"]

        for answer in denial_answers:
            phone = "5493399010004"
            await _seed_user(phone, name="Test", step="awaiting_location_confirm")
            candidate = {
                "lat": 10.48, "lng": -66.87,
                "zone_name": "La Boyera", "city_code": "CCS",
            }
            _stash_location_confirm(phone, candidate)

            with patch.object(
                handler, "update_user_location", new=AsyncMock(),
            ) as mock_update_loc, patch.object(
                handler, "send_text_message", new=AsyncMock(),
            ):
                await handle_incoming_message(phone, answer)

            mock_update_loc.assert_not_awaited(), f"'{answer}' should have denied"

            refreshed = await _fetch_user(phone)
            assert refreshed.onboarding_step == "awaiting_location", (
                f"'{answer}' should reset step to awaiting_location"
            )

    # -----------------------------------------------------------------------
    # Test: unrecognized response → treated as new location input
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_unrecognized_response_treated_as_new_location_high_confidence(self):
        """An unrecognized answer (e.g. typing a new zone) must be passed
        through the geocoder.  If the new geocode is high-confidence, the
        location is saved and onboarding completes."""
        phone = "5493399010005"
        await _seed_user(phone, name="Ana", step="awaiting_location_confirm")

        # Stash an old low-confidence candidate
        _stash_location_confirm(phone, {
            "lat": _LOW_CONF_RESULT.lat,
            "lng": _LOW_CONF_RESULT.lng,
            "zone_name": _LOW_CONF_RESULT.zone_name,
            "city_code": _LOW_CONF_RESULT.city_code,
        })

        mock_user = MagicMock()
        mock_user.name = "Ana"

        with patch(
            "farmafacil.services.location.resolve",
            new=AsyncMock(return_value=_HIGH_CONF_RESULT),
        ), patch(
            "farmafacil.services.location._name_matches_query",
            return_value=True,  # high confidence + name matches → auto-accept
        ), patch.object(
            handler, "update_user_location", new=AsyncMock(return_value=mock_user),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ):
            await handle_incoming_message(phone, "Chacao")

        # Geocode must have been attempted on the new text
        mock_update_loc.assert_awaited_once()

        # The stash must have been cleared
        assert _pop_location_confirm(phone) is None

    @pytest.mark.asyncio
    async def test_unrecognized_response_treated_as_new_location_low_confidence(self):
        """An unrecognized answer that itself geocodes to low-confidence must
        re-stash the new candidate and stay in awaiting_location_confirm."""
        phone = "5493399010006"
        await _seed_user(phone, name="Ana", step="awaiting_location_confirm")

        _stash_location_confirm(phone, {
            "lat": _LOW_CONF_RESULT.lat,
            "lng": _LOW_CONF_RESULT.lng,
            "zone_name": _LOW_CONF_RESULT.zone_name,
            "city_code": _LOW_CONF_RESULT.city_code,
        })

        # New geocode also low-confidence
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
            "farmafacil.services.location.resolve",
            new=AsyncMock(return_value=new_low_result),
        ), patch(
            "farmafacil.services.location._name_matches_query",
            return_value=False,
        ), patch.object(
            handler, "update_user_location", new=AsyncMock(),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "Somewhere obscure")

        # Location must NOT have been saved
        mock_update_loc.assert_not_awaited()

        # Step must remain at awaiting_location_confirm with a new stash
        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step == "awaiting_location_confirm"

        # Must re-ask for confirmation with the new candidate
        sent_text = mock_send.await_args.args[1]
        assert "¿es correcto?" in sent_text or "es correcto" in sent_text.lower()

    @pytest.mark.asyncio
    async def test_unrecognized_response_geocode_not_found(self):
        """An unrecognized answer that geocodes to None must send the
        'not found' message."""
        phone = "5493399010007"
        await _seed_user(phone, name="Ana", step="awaiting_location_confirm")

        _stash_location_confirm(phone, {
            "lat": 10.48, "lng": -66.87,
            "zone_name": "La Boyera", "city_code": "CCS",
        })

        with patch(
            "farmafacil.services.location.resolve",
            new=AsyncMock(return_value=None),  # geocoder found nothing
        ), patch.object(
            handler, "update_user_location", new=AsyncMock(),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "xyzabc123unknown")

        mock_update_loc.assert_not_awaited()
        sent_text = mock_send.await_args.args[1]
        # MSG_LOCATION_NOT_FOUND contains "No logré ubicar"
        assert "No logré ubicar" in sent_text or "zona" in sent_text.lower()

    # -----------------------------------------------------------------------
    # Test: stash expired (popped returns None) — sí response edge case
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_si_response_when_stash_expired_reasks_location(self):
        """If the user confirms but the stash has already been popped (e.g.
        TTL expired or duplicate message), the handler must gracefully reset
        to awaiting_location and prompt the user to try again."""
        phone = "5493399010008"
        await _seed_user(phone, name="Ana", step="awaiting_location_confirm")

        # Do NOT stash anything — stash is empty (simulates expiry)

        with patch.object(
            handler, "update_user_location", new=AsyncMock(),
        ) as mock_update_loc, patch.object(
            handler, "send_text_message", new=AsyncMock(),
        ) as mock_send:
            await handle_incoming_message(phone, "sí")

        # Location must NOT be saved
        mock_update_loc.assert_not_awaited()

        # Step must fall back to awaiting_location
        refreshed = await _fetch_user(phone)
        assert refreshed.onboarding_step == "awaiting_location"

        # Must inform the user that confirmation expired
        sent_text = mock_send.await_args.args[1]
        assert "venció" in sent_text or "zona" in sent_text.lower() or "de nuevo" in sent_text
