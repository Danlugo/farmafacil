"""Image analysis service — prescription reader & medicine identifier.

Uses Claude Vision to analyze photos sent via WhatsApp:
- **Prescription photos**: reads all medicines, translates medical
  terminology to plain Spanish, explains dosage instructions.
- **Medicine photos** (pill box, bottle, packaging): identifies the
  medicine name for automatic pharmacy catalog search.

Item 124, v0.45.0.
"""

import logging
from dataclasses import dataclass, field

from farmafacil.config import ANTHROPIC_API_KEY
from farmafacil.services.ai_responder import _get_client
from farmafacil.services.settings import resolve_user_model

logger = logging.getLogger(__name__)

# Maximum number of drug names to return for searching (prevents spam).
MAX_DRUG_NAMES = 3

# Token budget for the Vision response.  2048 accommodates complex
# prescriptions (5-6 drugs × ~6 lines each + structured wrappers).
_MAX_TOKENS = 2048

# Maximum caption length to prevent prompt injection via user text.
_MAX_CAPTION_LEN = 200


# ── Result dataclass ─────────────────────────────────────────────────────


@dataclass
class ImageAnalysisResult:
    """Result of analyzing an image with Claude Vision.

    Attributes:
        image_type: One of "prescription", "medicine", "unknown".
        analysis_text: Full formatted Spanish text for the user
            (prescription breakdown or medicine identification).
        drug_names: List of drug names extracted (for searching).
        model_used: Model that produced the result.
        tokens_in: Input tokens consumed.
        tokens_out: Output tokens consumed.
    """

    image_type: str = "unknown"
    analysis_text: str = ""
    drug_names: list[str] = field(default_factory=list)
    model_used: str = ""
    tokens_in: int = 0
    tokens_out: int = 0


# ── System prompt ────────────────────────────────────────────────────────

_VISION_SYSTEM_PROMPT = """\
Eres un asistente farmacéutico venezolano. El usuario te envía una foto \
por WhatsApp. Debes determinar qué tipo de imagen es y responder \
según el caso.

## CASO 1: RECETA MÉDICA / RÉCIPE
Si la imagen es una receta médica, récipe, orden médica o prescripción:
1. Lee TODOS los medicamentos listados.
2. Para CADA medicamento, indica:
   - Nombre del medicamento (genérico si es posible)
   - Dosis prescrita
   - Frecuencia (traduce abreviaturas: QD=1 vez al día, BID=2 veces al día, \
TID=3 veces al día, QID=4 veces al día, PRN=según necesidad, \
q8h=cada 8 horas, VO=vía oral, IM=intramuscular, IV=intravenoso)
   - Duración del tratamiento (si se indica)
   - Instrucciones especiales (con alimentos, en ayunas, etc.)
3. Si hay términos médicos, tradúcelos a español sencillo.
4. Si algo no se puede leer claramente, indícalo con ⚠️.

Formato de respuesta para recetas:
```
TIPO: RECETA

📋 *Receta Médica*

💊 *1. [Nombre del medicamento]*
   Dosis: [dosis]
   Tomar: [frecuencia en español sencillo]
   Duración: [duración si se indica]
   Nota: [instrucciones especiales]

💊 *2. [Siguiente medicamento]*
   ...

⚠️ [Cualquier advertencia o texto ilegible]

MEDICAMENTOS: [nombre1], [nombre2], [nombre3]
```

## CASO 2: FOTO DE MEDICAMENTO / EMPAQUE
Si la imagen muestra una caja, frasco, blister, etiqueta o envase \
de un medicamento:
1. Identifica el nombre del medicamento (genérico o comercial).
2. Identifica la dosis/concentración si es visible.
3. Identifica la forma farmacéutica si es visible (tabletas, jarabe, etc.).

Formato de respuesta:
```
TIPO: MEDICAMENTO

📦 *[Nombre completo del producto]*
Principio activo: [nombre genérico]
Presentación: [forma + cantidad si visible]

MEDICAMENTOS: [nombre genérico]
```

## CASO 3: IMAGEN NO RECONOCIDA
Si no puedes identificar ni una receta ni un medicamento:
```
TIPO: DESCONOCIDO
```

## REGLAS IMPORTANTES:
- Responde SIEMPRE en español.
- La línea TIPO: debe ser la primera línea (RECETA, MEDICAMENTO, o DESCONOCIDO).
- La línea MEDICAMENTOS: debe ser la última línea y contener SOLO los \
nombres de los medicamentos separados por coma (máximo 3). \
Usa el nombre genérico cuando sea posible para facilitar la búsqueda.
- NO incluyas dosis en la línea MEDICAMENTOS (solo nombres).
- Si no puedes leer un nombre, NO lo incluyas en MEDICAMENTOS.
- Usa formato WhatsApp: *negrita*, _cursiva_.
"""


# ── Public API ───────────────────────────────────────────────────────────


async def analyze_image(
    image_block: dict,
    caption: str = "",
) -> ImageAnalysisResult | None:
    """Analyze a WhatsApp image using Claude Vision.

    Determines if the image is a prescription or a medicine photo and
    returns structured results including drug names for searching.

    Args:
        image_block: Anthropic Vision content block (from
            ``encode_image_for_vision``).
        caption: Optional caption text the user sent with the image.

    Returns:
        ``ImageAnalysisResult`` on success, ``None`` on API failure
        or missing API key.
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set — cannot analyze image")
        return None

    content: list[dict] = [image_block]
    user_text = "Analiza esta imagen."
    if caption:
        # Truncate to prevent prompt injection via long/crafted captions.
        safe_caption = caption[:_MAX_CAPTION_LEN]
        user_text += f"\n\nEl usuario también escribió: «{safe_caption}»"
    content.append({"type": "text", "text": user_text})

    try:
        # Uses global default — Vision does not support per-user model overrides.
        resolved_model = await resolve_user_model()
        client = _get_client()
        response = await client.messages.create(
            model=resolved_model,
            max_tokens=_MAX_TOKENS,
            system=_VISION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )

        # Guard against empty or non-text responses (content-filtered, etc.)
        if not response.content or not hasattr(response.content[0], "text"):
            logger.warning("Vision: empty or non-text response")
            return None

        reply = response.content[0].text.strip()
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens

        if response.stop_reason == "max_tokens":
            logger.warning(
                "Vision response truncated (max_tokens=%d)", _MAX_TOKENS,
            )

        logger.info(
            "Image analysis (model=%s, in=%d, out=%d): %.100s",
            resolved_model, tokens_in, tokens_out, reply,
        )

        return _parse_vision_response(reply, resolved_model, tokens_in, tokens_out)

    except Exception as exc:
        logger.error("Image analysis failed: %s", exc)
        return None


# ── Response parser ──────────────────────────────────────────────────────


def _parse_vision_response(
    reply: str,
    model_used: str,
    tokens_in: int,
    tokens_out: int,
) -> ImageAnalysisResult:
    """Parse the structured Vision response into an ImageAnalysisResult.

    Extracts image_type, user-facing text, and drug names from the
    structured response format.
    """
    result = ImageAnalysisResult(
        model_used=model_used,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )

    lines = reply.split("\n")

    # Extract type from first line
    first_line = lines[0].strip().upper() if lines else ""
    if "RECETA" in first_line:
        result.image_type = "prescription"
    elif "MEDICAMENTO" in first_line:
        result.image_type = "medicine"
    else:
        result.image_type = "unknown"
        return result

    # Extract drug names from MEDICAMENTOS: line
    drug_names: list[str] = []
    # Build user-facing text (everything between TIPO: and MEDICAMENTOS:)
    text_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("TIPO:"):
            continue
        if upper.startswith("MEDICAMENTOS:"):
            # Parse comma-separated drug names
            raw = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            if raw:
                names = [n.strip() for n in raw.split(",") if n.strip()]
                drug_names = names[:MAX_DRUG_NAMES]
            continue
        # Skip markdown code fences that leak from the prompt
        if stripped in ("```", ):
            continue
        text_lines.append(line)

    result.analysis_text = "\n".join(text_lines).strip()
    result.drug_names = drug_names

    return result
