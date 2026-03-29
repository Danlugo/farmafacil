"""Handle incoming WhatsApp messages and dispatch responses."""

import logging

from farmafacil.bot.formatter import format_search_results
from farmafacil.bot.whatsapp import send_text_message
from farmafacil.services.geocode import geocode_zone
from farmafacil.services.search import search_drug
from farmafacil.services.users import get_or_create_user, update_user_location

logger = logging.getLogger(__name__)

GREETING_WORDS = {
    "hola", "hi", "hello", "hey", "buenos dias", "buenas tardes",
    "buenas noches", "buenas", "saludos",
}

LOCATION_CHANGE_WORDS = {
    "cambiar ubicacion", "cambiar ubicación", "cambiar zona",
    "nueva ubicacion", "nueva ubicación", "otra zona", "moverme",
}

WELCOME_MESSAGE = (
    "\U0001f48a *Bienvenido a FarmaFacil!*\n\n"
    "Busco medicamentos en farmacias de Venezuela por ti.\n\n"
    "Para empezar, necesito saber tu ubicacion.\n"
    "Dime tu zona o barrio (ej: _La Boyera_, _Chacao_, _Altamira_)"
)

LOCATION_ASK_MESSAGE = (
    "Dime tu nueva zona o barrio.\n\n"
    "Ejemplo: _La Boyera_, _Chacao_, _Maracaibo_"
)

LOCATION_NOT_FOUND_MESSAGE = (
    "No logre ubicar esa zona en Venezuela.\n"
    "Intenta con el nombre de tu barrio o urbanizacion.\n\n"
    "Ejemplos: _La Boyera_, _El Cafetal_, _Chacao_, _Altamira_, _Maracaibo_"
)

# In-memory flag for users who just requested a location change.
# Maps phone_number → True when waiting for new zone.
_awaiting_location: set[str] = set()


async def handle_incoming_message(sender: str, message_text: str) -> None:
    """Process an incoming WhatsApp message and send a response.

    Args:
        sender: Phone number of the sender (with country code).
        message_text: The text content of the message.
    """
    text = message_text.strip()
    if not text:
        return

    text_lower = text.lower()

    # Get or create user
    user = await get_or_create_user(sender)

    # Check for greetings
    if text_lower in GREETING_WORDS:
        if user.latitude is not None:
            await send_text_message(
                sender,
                f"\U0001f48a *Hola de nuevo!* Buscando en *{user.zone_name}*.\n\n"
                "Envia el nombre de un medicamento para buscar.\n"
                "Escribe _cambiar zona_ para cambiar tu ubicacion.",
            )
        else:
            await send_text_message(sender, WELCOME_MESSAGE)
        return

    # Check for location change request
    if text_lower in LOCATION_CHANGE_WORDS:
        _awaiting_location.add(sender)
        await send_text_message(sender, LOCATION_ASK_MESSAGE)
        return

    # If user has no location OR explicitly asked to change, geocode the message
    if user.latitude is None or sender in _awaiting_location:
        location = await geocode_zone(text)
        if location:
            _awaiting_location.discard(sender)
            user = await update_user_location(
                phone_number=sender,
                latitude=location["lat"],
                longitude=location["lng"],
                zone_name=location["zone_name"],
                city_code=location["city"],
            )
            await send_text_message(
                sender,
                f"\u2705 Ubicacion guardada: *{user.zone_name}*\n\n"
                "Ahora envia el nombre de un medicamento para buscar.\n"
                "Ejemplo: _losartan_ o _acetaminofen_",
            )
        else:
            await send_text_message(sender, LOCATION_NOT_FOUND_MESSAGE)
        return

    # User has location — treat as drug search (no geocode call)
    logger.info(
        "Drug search from %s (%s): '%s'",
        sender,
        user.zone_name,
        text,
    )
    response = await search_drug(
        query=text,
        city_code=user.city_code,
        latitude=user.latitude,
        longitude=user.longitude,
        zone_name=user.zone_name,
    )
    reply = format_search_results(response)
    await send_text_message(sender, reply)
