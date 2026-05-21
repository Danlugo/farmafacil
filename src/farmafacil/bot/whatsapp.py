"""WhatsApp Business Cloud API client for sending messages.

Supports a **proxy mode** for the ``/api/v1/chat`` endpoint: when the
``_response_collector`` context-variable holds a list, outbound messages
are appended to that list as dicts instead of being sent to the WhatsApp
API.  This lets external callers (e.g. a Chamo group-relay bot) run the
full handler logic and receive the responses as JSON.
"""

import contextvars
import logging
import os
from typing import Any

import httpx

from farmafacil.config import WHATSAPP_API_TOKEN, WHATSAPP_API_URL, WHATSAPP_PHONE_NUMBER_ID
from farmafacil.services.conversation_log import log_outbound

# ── Proxy-mode response collector ────────────────────────────────────────
# When set to a list, send_* functions append structured dicts instead of
# calling the WhatsApp Business API.  See ``collect_responses()`` below.
_response_collector: contextvars.ContextVar[list[dict[str, Any]] | None] = (
    contextvars.ContextVar("_response_collector", default=None)
)


def start_collecting() -> list[dict[str, Any]]:
    """Enter proxy mode: outbound messages are collected, not sent.

    Returns the list that will accumulate response dicts.
    """
    bucket: list[dict[str, Any]] = []
    _response_collector.set(bucket)
    return bucket


def stop_collecting() -> list[dict[str, Any]]:
    """Exit proxy mode and return the collected responses."""
    bucket = _response_collector.get() or []
    _response_collector.set(None)
    return bucket

logger = logging.getLogger(__name__)

MEDIA_UPLOAD_URL = f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_NUMBER_ID}/media"

# ── Module-level httpx client singleton ─────────────────────────────────
# A single AsyncClient reuses the underlying connection pool (TCP keepalive,
# TLS session resumption) across all WhatsApp API calls, avoiding per-call
# TLS handshakes to graph.facebook.com.  The client is safe for concurrent
# use from multiple asyncio tasks.
# (Item 78, v0.25.0 — was creating a new client per call.)
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return the module-level async httpx client, creating it lazily.

    Uses a 30 s default timeout — long enough for the WhatsApp Media upload
    endpoint (large image files) while still failing fast on stalled connections.
    """
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


async def _send_message(to: str, payload: dict, log_text: str) -> dict | None:
    """Send a message payload via WhatsApp Business API."""
    headers = {
        "Authorization": f"Bearer {WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        client = _get_http_client()
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

    In proxy mode this is a no-op — read receipts are meaningless when
    the message didn't come from WhatsApp.

    Args:
        to: Recipient phone number.
        message_id: The WhatsApp message ID to mark as read.
    """
    if not message_id:
        return
    if _response_collector.get() is not None:
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
        client = _get_http_client()
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.debug(
            "Read receipt failed for %s (non-critical): %s", to, exc,
        )


async def send_text_message(to: str, text: str) -> dict | None:
    """Send a text message via WhatsApp Business API.

    In proxy mode (``_response_collector`` is set), appends the message to
    the collector list instead of calling the API.
    """
    bucket = _response_collector.get()
    if bucket is not None:
        bucket.append({"type": "text", "body": text})
        return {"messages": [{"id": "proxy"}]}

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
    # ── Proxy mode: collect as structured dict ──────────────────────
    bucket = _response_collector.get()
    if bucket is not None:
        entry: dict[str, Any] = {
            "type": "list",
            "body": body,
            "button": button,
            "rows": rows,
        }
        if header:
            entry["header"] = header
        if footer:
            entry["footer"] = footer
        bucket.append(entry)
        return {"messages": [{"id": "proxy"}]}

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
    """Send an image message via public URL.

    In proxy mode, appends an image entry with the public ``url`` so the
    relay bot can forward it.
    """
    bucket = _response_collector.get()
    if bucket is not None:
        entry: dict[str, Any] = {"type": "image", "url": image_url}
        if caption:
            entry["caption"] = caption
        bucket.append(entry)
        return {"messages": [{"id": "proxy"}]}

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


_EXT_MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


async def _upload_media(file_path: str, mime_type: str | None = None) -> str | None:
    """Upload a local file to WhatsApp Media API and return the media ID.

    Args:
        file_path: Path to the local file.
        mime_type: MIME type of the file. If None, inferred from the
            file extension (``.jpg`` → ``image/jpeg``, etc.). Defaults
            to ``image/png`` when the extension is unknown so older
            callers keep working.

    Returns:
        Media ID string or None on failure.
    """
    if mime_type is None:
        ext = os.path.splitext(file_path)[1].lower()
        mime_type = _EXT_MIME_MAP.get(ext, "image/png")

    headers = {"Authorization": f"Bearer {WHATSAPP_API_TOKEN}"}

    try:
        client = _get_http_client()
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

    The temporary file at ``file_path`` is deleted after the upload attempt
    regardless of success or failure, so callers do not need to manage
    cleanup themselves.  (Item 85, v0.25.0)

    **Not intercepted in proxy mode** — this function uploads via the
    WhatsApp Media API (producing a ``media_id``), which relay bots
    cannot use.  The chat relay API callers receive the text summary
    that follows the grid image instead.  Use ``send_image_message``
    with a public URL for relay-compatible image flows.

    Args:
        to: Recipient phone number.
        file_path: Path to local image file (e.g. a tempfile produced by
            the caller).
        caption: Optional caption.

    Returns:
        API response dict or None on failure.
    """
    try:
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
    finally:
        # Always remove the temp file — it has been uploaded (or failed) and
        # the local copy is no longer needed.  Silently ignore missing files
        # (already cleaned up by a prior call, OS temp cleanup, etc.).
        try:
            os.unlink(file_path)
            logger.debug("Cleaned up temp image file: %s", file_path)
        except OSError:
            pass
