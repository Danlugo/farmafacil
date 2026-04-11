"""WhatsApp Business Cloud API client for sending messages."""

import logging
import os

import httpx

from farmafacil.config import WHATSAPP_API_TOKEN, WHATSAPP_API_URL, WHATSAPP_PHONE_NUMBER_ID
from farmafacil.services.conversation_log import log_outbound

logger = logging.getLogger(__name__)

MEDIA_UPLOAD_URL = f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_NUMBER_ID}/media"


async def _send_message(to: str, payload: dict, log_text: str) -> dict | None:
    """Send a message payload via WhatsApp Business API."""
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


async def send_read_receipt(to: str, message_id: str) -> None:
    """Mark a message as read, which triggers the typing indicator bubble.

    Uses the WhatsApp Cloud API messages endpoint with status=read.
    This marks the message as read (blue check marks) and shows the
    typing indicator to the user. Non-blocking — errors are silently logged.

    Args:
        to: Recipient phone number.
        message_id: The WhatsApp message ID to mark as read.
    """
    if not message_id:
        return

    headers = {
        "Authorization": f"Bearer {WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }
    url = f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.debug(
            "Read receipt failed for %s (non-critical): %s", to, exc,
        )


async def send_text_message(to: str, text: str) -> dict | None:
    """Send a text message via WhatsApp Business API."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    return await _send_message(to, payload, text)


async def send_interactive_list(
    to: str,
    body: str,
    button: str,
    rows: list[dict],
    header: str | None = None,
    footer: str | None = None,
    section_title: str = "Opciones",
) -> dict | None:
    """Send a WhatsApp interactive *list* message.

    Used by the category quick-reply menu (Item 29, v0.13.2). Each row is a
    dict with keys ``id`` (max 200 chars) and ``title`` (max 24 chars); an
    optional ``description`` field (max 72 chars) is forwarded if present.
    WhatsApp caps rows at 10 per section — the caller is responsible for
    staying under the limit.

    Args:
        to: Recipient WhatsApp phone number.
        body: Body text shown above the "View options" button (max ~1024 chars).
        button: Label on the button that opens the list (max 20 chars).
        rows: List of row dicts with ``id`` + ``title`` (and optional
            ``description``).
        header: Optional header text above the body.
        footer: Optional footer text below the button.
        section_title: Title for the single section in the list (max 24 chars).

    Returns:
        API response dict, or ``None`` on failure.
    """
    interactive: dict = {
        "type": "list",
        "body": {"text": body},
        "action": {
            "button": button,
            "sections": [
                {
                    "title": section_title,
                    "rows": rows,
                }
            ],
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header}
    if footer:
        interactive["footer"] = {"text": footer}

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    }
    # Log the body text so conversation_logs still have something readable
    # even though the real content is the rendered WhatsApp list.
    log_text = f"[interactive:list] {body}"
    return await _send_message(to, payload, log_text)


async def send_image_message(
    to: str, image_url: str, caption: str | None = None
) -> dict | None:
    """Send an image message via public URL."""
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


async def _upload_media(file_path: str, mime_type: str = "image/png") -> str | None:
    """Upload a local file to WhatsApp Media API and return the media ID.

    Args:
        file_path: Path to the local file.
        mime_type: MIME type of the file.

    Returns:
        Media ID string or None on failure.
    """
    headers = {"Authorization": f"Bearer {WHATSAPP_API_TOKEN}"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            with open(file_path, "rb") as f:
                response = await client.post(
                    MEDIA_UPLOAD_URL,
                    headers=headers,
                    data={"messaging_product": "whatsapp", "type": mime_type},
                    files={"file": (os.path.basename(file_path), f, mime_type)},
                )
                response.raise_for_status()
                media_id = response.json().get("id")
                logger.info("Uploaded media %s → %s", file_path, media_id)
                return media_id
    except httpx.HTTPStatusError as exc:
        logger.error("Media upload error %s: %s", exc.response.status_code, exc.response.text)
        return None
    except httpx.HTTPError as exc:
        logger.error("Media upload network error: %s", exc)
        return None
    except OSError as exc:
        logger.error("Media upload file error (%s): %s", file_path, exc)
        return None


async def send_local_image(
    to: str, file_path: str, caption: str | None = None
) -> dict | None:
    """Upload a local image and send it via WhatsApp.

    Args:
        to: Recipient phone number.
        file_path: Path to local image file.
        caption: Optional caption.

    Returns:
        API response dict or None on failure.
    """
    media_id = await _upload_media(file_path)
    if not media_id:
        return None

    image_payload: dict = {"id": media_id}
    if caption:
        image_payload["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": image_payload,
    }
    log_text = f"[product-grid] {caption or 'grid image'}"
    return await _send_message(to, payload, log_text)
