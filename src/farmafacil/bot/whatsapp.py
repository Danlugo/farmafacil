"""WhatsApp Business Cloud API client for sending messages."""

import logging

import httpx

from farmafacil.config import WHATSAPP_API_TOKEN, WHATSAPP_API_URL

logger = logging.getLogger(__name__)


async def send_text_message(to: str, text: str) -> dict | None:
    """Send a text message via WhatsApp Business API.

    Args:
        to: Recipient phone number with country code (e.g., "14257809707").
        text: Message text to send.

    Returns:
        API response dict on success, None on failure.
    """
    headers = {
        "Authorization": f"Bearer {WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(WHATSAPP_API_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            logger.info("WhatsApp message sent to %s: %s", to, data.get("messages", [{}])[0].get("id"))
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
