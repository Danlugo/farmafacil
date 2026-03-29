"""Handle incoming WhatsApp messages and dispatch responses."""

import logging

from farmafacil.bot.formatter import format_search_results
from farmafacil.bot.whatsapp import send_text_message
from farmafacil.services.search import search_drug

logger = logging.getLogger(__name__)

GREETING_WORDS = {"hola", "hi", "hello", "hey", "buenos dias", "buenas tardes", "buenas noches"}

WELCOME_MESSAGE = (
    "Hola! Soy *FarmaFacil* \U0001f48a\n\n"
    "Busco medicamentos en farmacias de Venezuela por ti.\n\n"
    "Envia el nombre de un medicamento y te digo donde esta disponible y a que precio.\n\n"
    "Ejemplo: _losartan_ o _acetaminofen_"
)


async def handle_incoming_message(sender: str, message_text: str) -> None:
    """Process an incoming WhatsApp message and send a response.

    Args:
        sender: Phone number of the sender (with country code).
        message_text: The text content of the message.
    """
    text = message_text.strip()

    if not text:
        return

    # Check for greetings
    if text.lower() in GREETING_WORDS:
        await send_text_message(sender, WELCOME_MESSAGE)
        return

    # Treat everything else as a drug search query
    logger.info("Drug search from %s: '%s'", sender, text)
    response = await search_drug(text)
    reply = format_search_results(response)
    await send_text_message(sender, reply)
