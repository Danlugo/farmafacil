"""WhatsApp Business API webhook endpoint."""

import logging

from fastapi import APIRouter, Query, Request, Response

from farmafacil.bot.handler import handle_incoming_message
from farmafacil.bot.whatsapp import send_text_message
from farmafacil.config import WHATSAPP_VERIFY_TOKEN
from farmafacil.services.conversation_log import is_duplicate_message, log_inbound

logger = logging.getLogger(__name__)

webhook_router = APIRouter()


@webhook_router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
) -> Response:
    """Handle WhatsApp webhook verification (GET).

    Meta sends a GET request with a challenge to verify the endpoint.

    Args:
        hub_mode: Should be "subscribe".
        hub_verify_token: Must match our WHATSAPP_VERIFY_TOKEN.
        hub_challenge: Challenge string to echo back.

    Returns:
        The challenge string if verification passes, 403 otherwise.
    """
    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning("Webhook verification failed: mode=%s", hub_mode)
    return Response(content="Forbidden", status_code=403)


@webhook_router.post("/webhook")
async def receive_webhook(request: Request) -> dict:
    """Handle incoming WhatsApp messages (POST).

    Meta sends a POST with message data when users send messages.

    Args:
        request: The incoming webhook request.

    Returns:
        Acknowledgement dict (200 OK).
    """
    body = await request.json()

    # Extract messages from the webhook payload
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            for message in value.get("messages", []):
                sender = message.get("from", "")
                msg_type = message.get("type", "")
                wa_id = message.get("id", "")

                # Deduplicate: WhatsApp retries webhooks on slow responses
                if wa_id and await is_duplicate_message(wa_id):
                    logger.info("Skipping duplicate message %s from %s", wa_id, sender)
                    continue

                if msg_type == "text":
                    text = message.get("text", {}).get("body", "")
                    logger.info("Received message from %s: %s", sender, text[:100])

                    # Log inbound message
                    await log_inbound(
                        phone_number=sender,
                        message_text=text,
                        message_type="text",
                        wa_message_id=wa_id,
                    )

                    await handle_incoming_message(sender, text, wa_message_id=wa_id)

                elif msg_type == "location":
                    loc = message.get("location", {})
                    lat = loc.get("latitude", "")
                    lng = loc.get("longitude", "")
                    logger.info("Received location from %s: %s, %s", sender, lat, lng)

                    await log_inbound(
                        phone_number=sender,
                        message_text=f"location:{lat},{lng}",
                        message_type="location",
                        wa_message_id=wa_id,
                    )

                    # TODO: handle location shares for onboarding

                elif msg_type == "image":
                    logger.info("Received image from %s", sender)
                    await log_inbound(
                        phone_number=sender,
                        message_text="[imagen]",
                        message_type="image",
                        wa_message_id=wa_id,
                    )
                    await send_text_message(
                        sender,
                        "\U0001f4f7 Recibimos tu imagen!\n\n"
                        "La funcion de reconocimiento de recetas y productos "
                        "por foto estara disponible pronto.\n\n"
                        "Por ahora, enviame el *nombre del medicamento* por texto.",
                    )

                else:
                    logger.info("Received %s message from %s", msg_type, sender)

                    await log_inbound(
                        phone_number=sender,
                        message_text=f"[{msg_type}]",
                        message_type=msg_type,
                        wa_message_id=wa_id,
                    )

    return {"status": "ok"}
