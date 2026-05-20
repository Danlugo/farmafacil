"""Voice message processing — download, transcribe, and (future) translate.

Downloads WhatsApp voice notes via the Media API, stores them locally,
transcribes via OpenAI Whisper API, and returns the text for processing
as a normal message.

Translation is stubbed for future multi-language support (v0.22.0 shell).
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path

import httpx

from farmafacil.config import OPENAI_API_KEY

logger = logging.getLogger(__name__)

# Base directory for audio storage — /app/data/audio/ in production,
# ./data/audio/ in local dev.  Created on first use.
AUDIO_BASE_DIR = Path(os.getenv("AUDIO_BASE_DIR", "data/audio"))

# Whisper API endpoint
_WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"

# Max audio file size (bytes) — Whisper API limit is 25 MB
MAX_AUDIO_BYTES = 25 * 1024 * 1024

# ── Module-level httpx client singleton ─────────────────────────────────
# A single AsyncClient reuses the underlying connection pool across all
# Whisper API and WhatsApp Media download calls, avoiding per-call TLS
# handshakes. 60 s timeout covers Whisper transcription latency on large
# voice notes.
# (Item 78, v0.25.0 — was creating a new client per call.)
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return the module-level async httpx client, creating it lazily."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=60.0)
    return _http_client


def _ensure_audio_dir(user_id: int) -> Path:
    """Create the user's audio directory if it doesn't exist.

    Args:
        user_id: The database user ID.

    Returns:
        Path to the user's audio directory.
    """
    user_dir = AUDIO_BASE_DIR / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def save_audio_file(
    data: bytes, user_id: int, wa_message_id: str,
) -> str:
    """Save audio bytes to disk.

    File is stored at: {AUDIO_BASE_DIR}/{user_id}/{YYYYMMDD}_{wa_msg_id}.ogg

    Args:
        data: Raw audio bytes.
        user_id: The database user ID.
        wa_message_id: WhatsApp message ID (used in filename).

    Returns:
        Relative path from AUDIO_BASE_DIR parent (e.g. "audio/1/20260518_wamid123.ogg").
    """
    user_dir = _ensure_audio_dir(user_id)
    date_prefix = datetime.utcnow().strftime("%Y%m%d")
    # Sanitize wa_message_id for filesystem safety — allowlist approach
    safe_id = re.sub(r"[^A-Za-z0-9_\-.]", "_", wa_message_id)[:80] if wa_message_id else "unknown"
    filename = f"{date_prefix}_{safe_id}.ogg"
    filepath = user_dir / filename
    filepath.write_bytes(data)
    logger.info("Saved audio file: %s (%d bytes)", filepath, len(data))
    # Return path relative to data/ parent for DB storage
    return str(filepath.relative_to(AUDIO_BASE_DIR.parent))


def get_audio_absolute_path(relative_path: str) -> Path:
    """Convert a DB-stored relative path to an absolute filesystem path.

    Args:
        relative_path: Path stored in voice_messages.audio_path (e.g. "audio/1/file.ogg").

    Returns:
        Absolute path on disk.
    """
    return AUDIO_BASE_DIR.parent / relative_path


async def transcribe_audio(
    file_path: str | Path,
) -> tuple[str | None, str | None, float | None]:
    """Transcribe an audio file using OpenAI Whisper API.

    Args:
        file_path: Path to the audio file on disk.

    Returns:
        Tuple of (transcription_text, detected_language, duration_seconds)
        or (None, None, None) on failure.
        Language is the ISO 639-1 code (e.g. "es", "en", "pt").
    """
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — skipping transcription")
        return None, None, None

    file_path = Path(file_path)
    if not file_path.exists():
        logger.error("Audio file not found: %s", file_path)
        return None, None, None

    file_size = file_path.stat().st_size
    if file_size > MAX_AUDIO_BYTES:
        logger.warning("Audio file too large for Whisper: %d bytes", file_size)
        return None, None, None

    try:
        client = _get_http_client()
        # NOTE: open() is synchronous but acceptable here — audio files
        # are bounded to 25 MB and httpx streams the multipart upload.
        with open(file_path, "rb") as f:
            response = await client.post(
                _WHISPER_URL,
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                data={
                    "model": "whisper-1",
                    "language": "es",
                    "response_format": "verbose_json",
                },
                files={"file": (file_path.name, f, "audio/ogg")},
            )
        response.raise_for_status()
        result = response.json()

        text = result.get("text", "").strip()
        language = result.get("language", "es")
        duration = result.get("duration")

        logger.info(
            "Whisper transcription: lang=%s duration=%.1fs text='%s'",
            language, duration or 0, text[:80],
        )
        return text if text else None, language, duration

    except httpx.HTTPStatusError as exc:
        logger.error(
            "Whisper API HTTP error: %s — %s",
            exc.response.status_code,
            exc.response.text[:200],
        )
    except httpx.RequestError as exc:
        logger.error("Whisper API network error: %s", exc)
    except Exception as exc:
        logger.error("Whisper transcription failed: %s", exc)

    return None, None, None


async def translate_text(
    text: str, source_lang: str, target_lang: str,
) -> str | None:
    """Translate text from one language to another.

    STUB — returns None.  Reserved for future multi-language support.
    When implemented, will use Claude or a dedicated translation API.

    Args:
        text: The text to translate.
        source_lang: Source language ISO 639-1 code.
        target_lang: Target language ISO 639-1 code.

    Returns:
        Translated text, or None (always None in v0.22.0).
    """
    # Future implementation placeholder
    logger.debug(
        "translate_text stub called: %s→%s, text='%s'",
        source_lang, target_lang, text[:50],
    )
    return None
