"""WhatsApp Business API webhook endpoint.

Meta retries webhooks when the POST doesn't return 200 within ~5 seconds.
To avoid duplicate processing, we return 200 immediately and dispatch the
handler logic to a background ``asyncio.Task`` (Item 58, v0.24.0).
The existing dedup guard (``is_duplicate_message``) prevents re-processing
if Meta retries before the task starts.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import secrets

from fastapi import APIRouter, Query, Request, Response

from farmafacil.bot.handler import (
    handle_image_message,
    handle_incoming_message,
    handle_list_reply,
    handle_location_message,
    handle_voice_message,
)
from farmafacil.bot.whatsapp import (
    remove_reaction,
    send_reaction,
    send_text_message,
)
from farmafacil.config import WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN
from farmafacil.services.conversation_log import is_duplicate_message, log_inbound

logger = logging.getLogger(__name__)

# Keep references to background tasks so they aren't GC'd before finishing.
# The set auto-prunes via the done callback.
_background_tasks: set[asyncio.Task] = set()


_MAX_BACKGROUND_TASKS = 100


def _fire_and_forget(coro) -> None:
    """Schedule a coroutine as a background task with error logging.

    Logs a warning if the set exceeds ``_MAX_BACKGROUND_TASKS`` — this
    signals backpressure (messages arriving faster than they're processed).
    """
    if len(_background_tasks) >= _MAX_BACKGROUND_TASKS:
        logger.warning(
            "Background task set has %d tasks — possible backpressure",
            len(_background_tasks),
        )
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

webhook_router = APIRouter()


async def _log_inbound_safe(
    *, phone_number: str, message_text: str, message_type: str, wa_message_id: str,
) -> None:
    """Best-effort inbound log — DB failures never block message handling.

    If ``log_inbound`` raises (e.g. DB unavailable), the error is logged
    but the caller continues.  This prevents a DB hiccup from leaving
    the ⏳ processing reaction stuck on the user's message.
    """
    try:
        await log_inbound(
            phone_number=phone_number,
            message_text=message_text,
            message_type=message_type,
            wa_message_id=wa_message_id,
        )
    except Exception:
        logger.error(
            "Failed to log inbound message from %s (wa_id=%s)",
            phone_number, wa_message_id, exc_info=True,
        )


async def _safe_handle(
    coro, sender: str, wa_id: str, *, clear_reaction: bool = False,
) -> None:
    """Await *coro* inside a try/except so background tasks never crash silently.

    Must be called from within a running asyncio event loop (via
    ``_fire_and_forget``).  ``CancelledError`` is re-raised so Uvicorn
    shutdown can cancel in-flight tasks cleanly.

    Args:
        coro: The handler coroutine to await.
        sender: WhatsApp phone number of the message sender.
        wa_id: WhatsApp message ID.
        clear_reaction: If True, remove the ⏳ processing reaction in
            a ``finally`` block after the handler completes (success or
            failure).  Set to True for message types that received a
            reaction via ``send_reaction`` before dispatching.
    """
    try:
        await coro
    except asyncio.CancelledError:
        logger.info(
            "Background handler cancelled for %s (wa_id=%s)", sender, wa_id,
        )
        raise
    except Exception:
        logger.error(
            "Background handler failed for %s (wa_id=%s)",
            sender, wa_id, exc_info=True,
        )
    finally:
        if clear_reaction:
            try:
                await remove_reaction(sender, wa_id)
            except BaseException:
                logger.debug(
                    "Failed to clear ⏳ reaction for %s (wa_id=%s)",
                    sender, wa_id, exc_info=True,
                )


def _verify_signature(payload: bytes, signature_header: str) -> bool:
    """Verify WhatsApp webhook HMAC-SHA256 signature.

    Args:
        payload: Raw request body bytes.
        signature_header: Value of X-Hub-Signature-256 header (e.g. "sha256=abc...").

    Returns:
        True if signature is valid, False otherwise.
    """
    if not WHATSAPP_APP_SECRET:
        # No secret configured — skip verification (dev mode)
        logger.warning("HMAC verification skipped — WHATSAPP_APP_SECRET not set")
        return True

    if not signature_header:
        return False

    if not signature_header.startswith("sha256="):
        return False

    expected_sig = signature_header[7:]  # strip "sha256=" prefix
    computed = hmac.new(
        WHATSAPP_APP_SECRET.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed, expected_sig)


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
    if not WHATSAPP_VERIFY_TOKEN:
        logger.warning("Webhook verify rejected — WHATSAPP_VERIFY_TOKEN not configured")
        return Response(content="Forbidden", status_code=403)

    if hub_mode == "subscribe" and hub_verify_token and secrets.compare_digest(
        hub_verify_token, WHATSAPP_VERIFY_TOKEN
    ):
        logger.info("Webhook verified successfully")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning("Webhook verification failed: mode=%s", hub_mode)
    return Response(content="Forbidden", status_code=403)


@webhook_router.post("/webhook", response_model=None)
async def receive_webhook(request: Request) -> dict | Response:
    """Handle incoming WhatsApp messages (POST).

    Meta sends a POST with message data when users send messages.

    Args:
        request: The incoming webhook request.

    Returns:
        Acknowledgement dict (200 OK).
    """
    # ── HMAC-SHA256 signature verification ───────────────────────────────
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not _verify_signature(raw_body, signature):
        logger.warning("Webhook signature verification failed")
        return Response(content="Invalid signature", status_code=403)

    body = json.loads(raw_body)

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

                # ── Processing indicator (Item 117, v0.38.0 → v0.40.0) ──
                # React to the user's message with ⏳ so they see the bot
                # is working.  The reaction is removed in _safe_handle's
                # ``finally`` block once the handler completes (or fails).
                # Edge-case paths that skip _safe_handle remove it inline.
                # Awaited synchronously so the reaction lands BEFORE the
                # handler task starts.  ~50 ms TLS call, well under Meta's
                # 5 s retry window.
                if msg_type in ("text", "location", "interactive", "image", "document", "audio"):
                    await send_reaction(sender, wa_id, "⏳")

                if msg_type == "text":
                    text = message.get("text", {}).get("body", "")
                    logger.info("Received message from %s: %s", sender, text[:100])

                    # Best-effort log — DB failure must not block handler dispatch
                    # (otherwise the ⏳ reaction would be stuck).
                    await _log_inbound_safe(
                        phone_number=sender,
                        message_text=text,
                        message_type="text",
                        wa_message_id=wa_id,
                    )

                    # Dispatch handler as background task — return 200 immediately
                    _fire_and_forget(
                        _safe_handle(
                            handle_incoming_message(sender, text, wa_message_id=wa_id),
                            sender, wa_id, clear_reaction=True,
                        )
                    )

                elif msg_type == "location":
                    loc = message.get("location", {})
                    lat_raw = loc.get("latitude")
                    lng_raw = loc.get("longitude")
                    logger.info(
                        "Received location from %s: %s, %s",
                        sender, lat_raw, lng_raw,
                    )

                    await _log_inbound_safe(
                        phone_number=sender,
                        message_text=f"location:{lat_raw},{lng_raw}",
                        message_type="location",
                        wa_message_id=wa_id,
                    )

                    # Coerce to float — WhatsApp sends numbers, but guard
                    # against strings / missing values just in case.
                    try:
                        lat = float(lat_raw)
                        lng = float(lng_raw)
                    except (TypeError, ValueError):
                        logger.warning(
                            "Malformed location payload from %s: %r, %r",
                            sender, lat_raw, lng_raw,
                        )
                        _fire_and_forget(
                            _safe_handle(
                                send_text_message(
                                    sender,
                                    "No pude leer las coordenadas que compartiste. "
                                    "Por favor envia tu zona por texto.",
                                ),
                                sender, wa_id, clear_reaction=True,
                            )
                        )
                        continue

                    _fire_and_forget(
                        _safe_handle(
                            handle_location_message(sender, lat, lng, wa_message_id=wa_id),
                            sender, wa_id, clear_reaction=True,
                        )
                    )

                elif msg_type == "interactive":
                    # Interactive replies — currently only list_reply is
                    # used (category quick-reply menu, Item 29, v0.13.2).
                    # button_reply is accepted defensively for future use.
                    interactive = message.get("interactive", {}) or {}
                    itype = interactive.get("type", "")
                    reply = (
                        interactive.get("list_reply")
                        or interactive.get("button_reply")
                        or {}
                    )
                    reply_id = reply.get("id", "")
                    reply_title = reply.get("title", "")
                    logger.info(
                        "Received interactive %s from %s: id=%s title=%s",
                        itype, sender, reply_id, reply_title,
                    )

                    await _log_inbound_safe(
                        phone_number=sender,
                        message_text=f"[interactive:{itype}] {reply_id} ({reply_title})",
                        message_type="interactive",
                        wa_message_id=wa_id,
                    )

                    if itype == "list_reply" and reply_id:
                        _fire_and_forget(
                            _safe_handle(
                                handle_list_reply(sender, reply_id, wa_message_id=wa_id),
                                sender, wa_id, clear_reaction=True,
                            )
                        )
                    else:
                        logger.warning(
                            "Unhandled interactive type from %s: %s",
                            sender, itype,
                        )
                        await remove_reaction(sender, wa_id)

                elif msg_type == "image":
                    image_data = message.get("image", {})
                    media_id = image_data.get("id", "")
                    caption = image_data.get("caption", "")
                    mime_type = image_data.get("mime_type", "image/jpeg")
                    logger.info(
                        "Received image from %s: media_id=%s caption=%s",
                        sender, media_id, caption[:50] if caption else "",
                    )
                    await _log_inbound_safe(
                        phone_number=sender,
                        message_text=f"[imagen] {caption}" if caption else "[imagen]",
                        message_type="image",
                        wa_message_id=wa_id,
                    )
                    if media_id:
                        _fire_and_forget(
                            _safe_handle(
                                handle_image_message(
                                    sender, media_id, mime_type,
                                    caption=caption, wa_message_id=wa_id,
                                ),
                                sender, wa_id, clear_reaction=True,
                            )
                        )
                    else:
                        logger.warning("Image from %s has no media_id", sender)
                        await remove_reaction(sender, wa_id)

                elif msg_type == "document":
                    doc_data = message.get("document", {})
                    media_id = doc_data.get("id", "")
                    mime_type = doc_data.get("mime_type", "")
                    filename = doc_data.get("filename", "")
                    caption = doc_data.get("caption", "")
                    logger.info(
                        "Received document from %s: %s (%s)",
                        sender, filename, mime_type,
                    )
                    await _log_inbound_safe(
                        phone_number=sender,
                        message_text=f"[documento] {filename}" if filename else "[documento]",
                        message_type="document",
                        wa_message_id=wa_id,
                    )
                    if media_id:
                        _fire_and_forget(
                            _safe_handle(
                                handle_image_message(
                                    sender, media_id, mime_type,
                                    caption=caption or filename, wa_message_id=wa_id,
                                ),
                                sender, wa_id, clear_reaction=True,
                            )
                        )
                    else:
                        logger.warning("Document from %s has no media_id", sender)
                        await remove_reaction(sender, wa_id)

                elif msg_type == "audio":
                    audio_data = message.get("audio", {})
                    media_id = audio_data.get("id", "")
                    mime_type = audio_data.get("mime_type", "audio/ogg")
                    logger.info(
                        "Received voice message from %s: media_id=%s mime=%s",
                        sender, media_id, mime_type,
                    )
                    await _log_inbound_safe(
                        phone_number=sender,
                        message_text="[audio]",
                        message_type="audio",
                        wa_message_id=wa_id,
                    )
                    if media_id:
                        _fire_and_forget(
                            _safe_handle(
                                handle_voice_message(sender, media_id, wa_message_id=wa_id),
                                sender, wa_id, clear_reaction=True,
                            )
                        )
                    else:
                        logger.warning("Audio from %s has no media_id", sender)
                        await remove_reaction(sender, wa_id)

                elif msg_type in ("reaction", "system", "ephemeral", "order"):
                    # Silent: reactions, system messages, and ephemeral don't
                    # need a reply — just log them.
                    logger.info("Received %s message from %s (silent)", msg_type, sender)
                    await log_inbound(
                        phone_number=sender,
                        message_text=f"[{msg_type}]",
                        message_type=msg_type,
                        wa_message_id=wa_id,
                    )

                else:
                    # Unsupported type (sticker, contacts, etc.) — tell the
                    # user what we CAN handle so they don't think the bot is
                    # broken. (Item 64, v0.25.0)
                    logger.info("Received unsupported %s message from %s", msg_type, sender)
                    await log_inbound(
                        phone_number=sender,
                        message_text=f"[{msg_type}]",
                        message_type=msg_type,
                        wa_message_id=wa_id,
                    )
                    _fire_and_forget(
                        _safe_handle(
                            send_text_message(
                                sender,
                                "No puedo procesar ese tipo de mensaje. "
                                "Envíame texto, foto, documento, nota de voz "
                                "o ubicación. \U0001f48a",
                            ),
                            sender, wa_id,
                        )
                    )

    return {"status": "ok"}
