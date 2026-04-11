"""Tests for WhatsApp location sharing support (Item 24, v0.13.0).

Covers:
- ``reverse_geocode`` service: happy path, non-VE rejection, network
  failure, missing-address fallback.
- ``handle_location_message`` handler: onboarding completion, name-first
  reordering, already-onboarded zone update, reverse-geocode failure path.
- Webhook: location payload parsing, malformed coordinate guard.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from farmafacil.api.app import create_app
from farmafacil.bot.handler import handle_location_message
from farmafacil.db.session import async_session
from farmafacil.models.database import User
from farmafacil.services.geocode import reverse_geocode
from farmafacil.services.users import get_or_create_user
from sqlalchemy import delete


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def _cleanup_test_users():
    """Remove test phone numbers before and after each test."""
    test_phones = {
        "5491200000001",
        "5491200000002",
        "5491200000003",
        "5491200000004",
        "5491200000005",
        "5491200000006",
        "5491200000007",
    }
    async with async_session() as session:
        await session.execute(
            delete(User).where(User.phone_number.in_(test_phones))
        )
        await session.commit()
    yield
    async with async_session() as session:
        await session.execute(
            delete(User).where(User.phone_number.in_(test_phones))
        )
        await session.commit()


def _nominatim_reverse_payload(
    *,
    country_code: str = "ve",
    suburb: str | None = "La Boyera",
    city: str | None = "Caracas",
    state: str | None = "Distrito Capital",
) -> dict:
    """Build a realistic Nominatim reverse-geocode response."""
    address: dict = {"country_code": country_code}
    if suburb:
        address["suburb"] = suburb
    if city:
        address["city"] = city
    if state:
        address["state"] = state
    return {
        "lat": "10.4806",
        "lon": "-66.8794",
        "display_name": (
            f"{suburb or ''}, {city or ''}, {state or ''}, Venezuela"
        ).strip(", "),
        "address": address,
    }


class TestReverseGeocodeUnit:
    """Unit tests for ``reverse_geocode`` with mocked httpx."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_city_code_and_zone(self):
        """A Venezuelan coordinate resolves to a city code + zone name."""
        payload = _nominatim_reverse_payload(
            suburb="La Boyera", city="Caracas", state="Distrito Capital",
        )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=payload)

        with patch("farmafacil.services.geocode.httpx.AsyncClient") as mc:
            instance = mc.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_response)
            result = await reverse_geocode(10.4806, -66.8794)

        assert result is not None
        assert result["city"] == "CCS"
        assert result["zone_name"] == "La Boyera"
        assert result["lat"] == 10.4806
        assert result["lng"] == -66.8794

    @pytest.mark.asyncio
    async def test_non_venezuela_rejected(self):
        """Coordinates outside Venezuela return None (security guard)."""
        payload = {
            "address": {
                "country_code": "co",
                "city": "Bogotá",
                "state": "Cundinamarca",
            },
            "display_name": "Bogotá, Colombia",
        }
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=payload)

        with patch("farmafacil.services.geocode.httpx.AsyncClient") as mc:
            instance = mc.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_response)
            result = await reverse_geocode(4.7110, -74.0721)

        assert result is None

    @pytest.mark.asyncio
    async def test_network_failure_returns_none(self):
        """httpx.RequestError on Nominatim returns None."""
        with patch("farmafacil.services.geocode.httpx.AsyncClient") as mc:
            instance = mc.return_value.__aenter__.return_value
            instance.get = AsyncMock(
                side_effect=httpx.RequestError("connection refused")
            )
            result = await reverse_geocode(10.48, -66.87)

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_address_returns_none(self):
        """Nominatim response without an address key returns None."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"lat": "0", "lon": "0"})

        with patch("farmafacil.services.geocode.httpx.AsyncClient") as mc:
            instance = mc.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_response)
            result = await reverse_geocode(0.0, 0.0)

        assert result is None

    @pytest.mark.asyncio
    async def test_falls_back_to_state_when_suburb_missing(self):
        """Zone name uses state when suburb/city are not available."""
        payload = _nominatim_reverse_payload(
            suburb=None, city=None, state="Zulia",
        )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=payload)

        with patch("farmafacil.services.geocode.httpx.AsyncClient") as mc:
            instance = mc.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_response)
            result = await reverse_geocode(10.64, -71.61)

        assert result is not None
        # state maps to MCBO per STATE_TO_CITY_CODE
        assert result["city"] == "MCBO"
        assert result["zone_name"] == "Zulia"


class TestHandleLocationMessage:
    """Integration tests for the location-pin handler."""

    @pytest.mark.asyncio
    async def test_onboarding_location_advances_to_preference(self):
        """User in awaiting_location who shares a pin → preference step."""
        phone = "5491200000001"
        # Pre-populate the user at the awaiting_location step with a name
        await get_or_create_user(phone)
        async with async_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(User).where(User.phone_number == phone)
            )
            row = result.scalar_one()
            row.name = "Maria"
            row.onboarding_step = "awaiting_location"
            await session.commit()

        location = {
            "lat": 10.48,
            "lng": -66.87,
            "city": "CCS",
            "zone_name": "La Boyera",
        }

        with patch(
            "farmafacil.bot.handler.reverse_geocode",
            new=AsyncMock(return_value=location),
        ), patch(
            "farmafacil.bot.handler.send_text_message",
            new=AsyncMock(),
        ) as mock_send:
            await handle_location_message(phone, 10.48, -66.87)

        # Reload user — location saved, step advanced
        async with async_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(User).where(User.phone_number == phone)
            )
            refreshed = result.scalar_one()

        assert refreshed.latitude == 10.48
        assert refreshed.longitude == -66.87
        assert refreshed.city_code == "CCS"
        assert refreshed.zone_name == "La Boyera"
        assert refreshed.onboarding_step == "awaiting_preference"

        # A preference prompt was sent
        assert mock_send.await_count == 1
        sent_text = mock_send.await_args.args[1]
        assert "La Boyera" in sent_text
        assert "1" in sent_text and "2" in sent_text  # preference options

    @pytest.mark.asyncio
    async def test_location_before_name_asks_for_name(self):
        """A user who shares location before giving a name → awaiting_name."""
        phone = "5491200000002"
        await get_or_create_user(phone)  # fresh user, no name

        location = {
            "lat": 10.48,
            "lng": -66.87,
            "city": "CCS",
            "zone_name": "Chacao",
        }

        with patch(
            "farmafacil.bot.handler.reverse_geocode",
            new=AsyncMock(return_value=location),
        ), patch(
            "farmafacil.bot.handler.send_text_message",
            new=AsyncMock(),
        ) as mock_send:
            await handle_location_message(phone, 10.48, -66.87)

        async with async_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(User).where(User.phone_number == phone)
            )
            refreshed = result.scalar_one()

        assert refreshed.zone_name == "Chacao"
        assert refreshed.onboarding_step == "awaiting_name"
        sent_text = mock_send.await_args.args[1]
        assert "Chacao" in sent_text
        assert "Como te llamas" in sent_text

    @pytest.mark.asyncio
    async def test_onboarded_user_updates_zone(self):
        """A fully-onboarded user sharing a pin → zone updated + confirmation."""
        phone = "5491200000003"
        await get_or_create_user(phone)
        async with async_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(User).where(User.phone_number == phone)
            )
            row = result.scalar_one()
            row.name = "Jose"
            row.display_preference = "image"
            row.zone_name = "Antigua zona"
            row.city_code = "MCBO"
            row.latitude = 10.64
            row.longitude = -71.61
            row.onboarding_step = None
            await session.commit()

        new_location = {
            "lat": 10.48,
            "lng": -66.87,
            "city": "CCS",
            "zone_name": "Nueva zona",
        }

        with patch(
            "farmafacil.bot.handler.reverse_geocode",
            new=AsyncMock(return_value=new_location),
        ), patch(
            "farmafacil.bot.handler.send_text_message",
            new=AsyncMock(),
        ) as mock_send:
            await handle_location_message(phone, 10.48, -66.87)

        async with async_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(User).where(User.phone_number == phone)
            )
            refreshed = result.scalar_one()

        assert refreshed.zone_name == "Nueva zona"
        assert refreshed.city_code == "CCS"
        assert refreshed.onboarding_step is None
        # Display preference preserved
        assert refreshed.display_preference == "image"
        sent_text = mock_send.await_args.args[1]
        assert "Nueva zona" in sent_text
        assert "actualizada" in sent_text.lower()

    @pytest.mark.asyncio
    async def test_reverse_geocode_failure_sends_error(self):
        """When reverse_geocode returns None, the user gets the error hint."""
        phone = "5491200000004"
        await get_or_create_user(phone)
        async with async_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(User).where(User.phone_number == phone)
            )
            row = result.scalar_one()
            row.name = "Carlos"
            row.onboarding_step = "awaiting_location"
            await session.commit()

        with patch(
            "farmafacil.bot.handler.reverse_geocode",
            new=AsyncMock(return_value=None),
        ), patch(
            "farmafacil.bot.handler.send_text_message",
            new=AsyncMock(),
        ) as mock_send:
            await handle_location_message(phone, 1.0, 1.0)

        # User state untouched
        async with async_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(User).where(User.phone_number == phone)
            )
            refreshed = result.scalar_one()

        assert refreshed.zone_name is None
        assert refreshed.onboarding_step == "awaiting_location"
        sent_text = mock_send.await_args.args[1]
        assert "no pude" in sent_text.lower() or "No pude" in sent_text
        assert "zona" in sent_text.lower()


class TestWebhookLocationPayload:
    """Tests for the webhook location message parsing."""

    @pytest.mark.asyncio
    async def test_webhook_dispatches_location_to_handler(self, client):
        """POST /webhook with a location message calls handle_location_message."""
        phone = "5491200000005"
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "123"},
                                "messages": [
                                    {
                                        "from": phone,
                                        "id": "wamid_test_loc_001",
                                        "type": "location",
                                        "location": {
                                            "latitude": 10.48,
                                            "longitude": -66.87,
                                        },
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        with patch(
            "farmafacil.bot.webhook.handle_location_message",
            new=AsyncMock(),
        ) as mock_handler:
            response = await client.post("/webhook", json=payload)

        assert response.status_code == 200
        mock_handler.assert_awaited_once()
        args = mock_handler.await_args.args
        kwargs = mock_handler.await_args.kwargs
        assert args[0] == phone
        assert args[1] == 10.48
        assert args[2] == -66.87
        assert kwargs["wa_message_id"] == "wamid_test_loc_001"

    @pytest.mark.asyncio
    async def test_webhook_malformed_location_sends_error(self, client):
        """Missing latitude/longitude fields → error sent, handler NOT called."""
        phone = "5491200000006"
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "123"},
                                "messages": [
                                    {
                                        "from": phone,
                                        "id": "wamid_test_loc_002",
                                        "type": "location",
                                        "location": {
                                            "latitude": None,
                                            "longitude": None,
                                        },
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        with patch(
            "farmafacil.bot.webhook.handle_location_message",
            new=AsyncMock(),
        ) as mock_handler, patch(
            "farmafacil.bot.webhook.send_text_message",
            new=AsyncMock(),
        ) as mock_send:
            response = await client.post("/webhook", json=payload)

        assert response.status_code == 200
        mock_handler.assert_not_awaited()
        mock_send.assert_awaited_once()
