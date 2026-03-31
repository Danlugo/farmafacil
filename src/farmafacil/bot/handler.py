"""Handle incoming WhatsApp messages and dispatch responses."""

import logging

from farmafacil.bot.formatter import format_search_results
from farmafacil.bot.whatsapp import send_text_message
from farmafacil.services.geocode import geocode_zone
from farmafacil.services.intent import HELP_MESSAGE, classify_intent
from farmafacil.services.search import search_drug
from farmafacil.services.users import get_or_create_user, update_user_location

logger = logging.getLogger(__name__)

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

LOCATION_REQUIRED_MESSAGE = (
    "Primero necesito saber tu ubicacion para buscarte "
    "medicamentos en farmacias cercanas.\n\n"
    "Dime tu zona o barrio (ej: _La Boyera_, _Chacao_, _Altamira_)"
)

# In-memory set: users who have been asked for location and we're waiting for their reply.
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

    # Get or create user
    user = await get_or_create_user(sender)

    # ── State: Awaiting location (user was explicitly asked for zone) ──
    if sender in _awaiting_location:
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

    # ── State: New user with no location ──
    if user.latitude is None:
        # Classify intent to handle greetings/help, but for everything else
        # ask for location first — do NOT try to geocode random messages.
        intent = await classify_intent(text)
        if intent.action == "greeting":
            _awaiting_location.add(sender)
            await send_text_message(sender, WELCOME_MESSAGE)
        elif intent.action == "help":
            await send_text_message(sender, HELP_MESSAGE)
        else:
            # They sent a drug name or question, but we have no location yet.
            _awaiting_location.add(sender)
            await send_text_message(sender, LOCATION_REQUIRED_MESSAGE)
        return

    # ── State: Existing user with location — classify intent ──
    intent = await classify_intent(text)

    if intent.action == "greeting":
        await send_text_message(
            sender,
            f"\U0001f48a *Hola de nuevo!* Buscando en *{user.zone_name}*.\n\n"
            "Envia el nombre de un medicamento para buscar.\n"
            "Escribe _cambiar zona_ para cambiar tu ubicacion.",
        )

    elif intent.action == "location_change":
        _awaiting_location.add(sender)
        await send_text_message(sender, LOCATION_ASK_MESSAGE)

    elif intent.action == "help":
        await send_text_message(sender, HELP_MESSAGE)

    elif intent.action == "drug_search":
        query = intent.drug_query or text
        logger.info("Drug search from %s (%s): '%s'", sender, user.zone_name, query)
        response = await search_drug(
            query=query,
            city_code=user.city_code,
            latitude=user.latitude,
            longitude=user.longitude,
            zone_name=user.zone_name,
        )
        reply = format_search_results(response)
        await send_text_message(sender, reply)

    elif intent.action == "question" and intent.response_text:
        await send_text_message(sender, intent.response_text)

    else:
        await send_text_message(
            sender,
            "No estoy seguro de lo que necesitas.\n"
            "Envia el nombre de un medicamento y te busco donde esta disponible.\n\n"
            "Escribe _ayuda_ para ver las instrucciones.",
        )
