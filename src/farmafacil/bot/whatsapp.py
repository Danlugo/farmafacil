"""WhatsApp Business Cloud API client for sending messages."""

import logging

import httpx

from farmafacil.config import WHATSAPP_API_TOKEN, WHATSAPP_API_URL
from farmafacil.services.conversation_log import log_outbound

logger = logging.getLogger(__name__)


async def _send_message(to: str, payload: dict, log_text: str) -> dict | None:
    """Send a message payload via WhatsApp Business API.

    Args:
        to: Recipient phone number.
        payload: Full WhatsApp API payload.
        log_text: Text to log for the conversation record.

    Returns:
        API response dict on success, None on failure.
    """
    headers = {
        "Authorization": f"Bearer {WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(WHATSAPP_API_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            msg_id = data.get("messages", [{}])[0].get("id")
            logger.info("WhatsApp message sent to %s: %s", to, msg_id)
            await log_outbound(to, log_text)
            return data
    except httpx.HTTPStatusError as exc:
        logger.error(
            "WhatsApp API error %s: %s",
            exc.response.status_code,
            exc.response.text,
        )
        return None
    except httpx.RequestError as exc:
        logger.error("WhatsApp request failed: %s", exc)
        return None


async def send_text_message(to: str, text: str) -> dict | None:
    """Send a text message via WhatsApp Business API.

    Args:
        to: Recipient phone number with country code.
        text: Message text to send.

    Returns:
        API response dict on success, None on failure.
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    return await _send_message(to, payload, text)


async def send_image_message(
    to: str, image_url: str, caption: str | None = None
) -> dict | None:
    """Send an image message via WhatsApp Business API.

    Args:
        to: Recipient phone number with country code.
        image_url: Public URL of the image.
        caption: Optional caption text below the image.

    Returns:
        API response dict on success, None on failure.
    """
    image_payload: dict = {"link": image_url}
    if caption:
        image_payload["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": image_payload,
    }
    log_text = f"[image] {caption or image_url}"
    return await _send_message(to, payload, log_text)
