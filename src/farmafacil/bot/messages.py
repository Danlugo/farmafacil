"""Message constants and category definitions for the FarmaFacil WhatsApp bot.

All user-visible string constants (MSG_*), category tuples (CATEGORIES),
and small auxiliary sets (_CLARIFY_CANCEL_WORDS, _ADMIN_OFF_PHRASES,
_NOT_NAMES) live here so they can be imported by handler.py and the
domain-specific sub-modules without circular dependencies.
"""

# ── Onboarding messages ─────────────────────────────────────────────────

MSG_WELCOME = (
    "\U0001f48a *¡Hola! Soy FarmaFacil*\n\n"
    "Te ayudo a encontrar productos en farmacias de Venezuela.\n\n"
    "*¿Cómo te llamas?*"
)

MSG_ASK_LOCATION = (
    "¡Mucho gusto *{name}*! \U0001f91d\n\n"
    "*¿En qué zona o barrio estás?*\n"
    "Ejemplo: _La Boyera_, _Chacao_, _Maracaibo_"
)

MSG_ASK_PREFERENCE = (
    "✅ *{zone}* guardado!\n\n"
    "¿Cómo prefieres ver los resultados?\n\n"
    "*1.* \U0001f4f8 *Imagen grande* — un producto a la vez con detalles\n"
    "*2.* \U0001f5bc *Galería* — varios productos en una imagen\n\n"
    "Responde *1* o *2*"
)

MSG_READY = (
    "✅ ¡Listo *{name}*! Ya estás configurado.\n\n"
    "Envíame el nombre de un producto de farmacia.\n"
    "Ejemplo: _losartan_, _protector solar_, _vitamina C_\n\n"
    "_Escribe 'ayuda' para ver todos los comandos._"
)

MSG_LOCATION_NOT_FOUND = (
    "No logré ubicar esa zona en Venezuela.\n"
    "Intenta con el nombre de tu barrio o urbanización.\n\n"
    "Ejemplos: _La Boyera_, _El Cafetal_, _Chacao_, _Maracaibo_"
)

# Shown when a user shares a GPS location pin that cannot be reverse-geocoded
# (unreachable Nominatim, coordinates outside Venezuela, malformed response).
# Added in v0.13.0 (Item 24) — users can fall back to typing a city name.
MSG_LOCATION_PIN_NOT_FOUND = (
    "No pude ubicar las coordenadas que compartiste.\n"
    "Por favor, envía el *nombre de tu zona o barrio* por texto.\n\n"
    "Ejemplos: _La Boyera_, _El Cafetal_, _Chacao_, _Maracaibo_"
)

MSG_INVALID_PREFERENCE = "Responde *1* para imagen grande o *2* para galería."

MSG_RETURNING = (
    "\U0001f48a *¡Hola {name}!* Buscando en *{zone}*.\n\n"
    "Envíame el nombre de un producto de farmacia.\n\n"
    "\U0001f527 _Comandos:_\n"
    "• _cambiar zona_ — nueva ubicación\n"
    "• _cambiar nombre_ — actualizar nombre\n"
    "• _ayuda_ — instrucciones"
)

MSG_ASK_NEW_LOCATION = "Dime tu nueva zona o barrio.\nEjemplo: _La Boyera_, _Chacao_, _Maracaibo_"
MSG_LOCATION_UPDATED = "📍 ¡Listo! Tu ubicación fue actualizada a *{zone_name}*."
MSG_ASK_NEW_PREFERENCE = (
    "¿Cómo prefieres ver los resultados?\n\n"
    "*1.* \U0001f4f8 *Imagen grande*\n*2.* \U0001f5bc *Galería*\n\nResponde *1* o *2*"
)
MSG_ASK_NEW_NAME = "¿Cómo te llamas?"

MSG_ASK_FEEDBACK = "¿Te sirvió? (sí/no)"
MSG_FEEDBACK_THANKS = "¡Gracias por tu respuesta! \U0001f44d"
MSG_FEEDBACK_SORRY = "Lamento eso. ¿Qué buscabas exactamente o qué estuvo mal?"
MSG_FEEDBACK_DETAIL_THANKS = "Gracias por explicarnos. Vamos a mejorar. \U0001f4aa"

# ── Post-feedback follow-up messages (v0.22.2) ────────────────────────
MSG_POST_SUGGESTION_OFFER = (
    "¡Gracias! 🙏 ¿Quieres dejar una sugerencia?\n\n"
    "Escribe tu sugerencia o envía una nota de voz 🎙️\n"
    "_Responde 'no' para continuar_"
)
MSG_POST_SUGGESTION_PROMPT = (
    "Perfecto, escribe tu sugerencia o envía una nota de voz 🎙️"
)
MSG_POST_SUGGESTION_THANKS = (
    "✅ ¡Gracias por tu sugerencia!\n\n"
    "📋 *Sugerencia #{case_id}*\n\n"
    "La revisaremos pronto."
)
MSG_POST_BUG_OFFER = (
    "Lamento eso. ¿Quieres contarnos qué no funcionó?\n\n"
    "Escribe tu mensaje o envía una nota de voz 🎙️\n"
    "_Responde 'no' para continuar_"
)
MSG_POST_BUG_PROMPT = (
    "Escribe tu mensaje o envía una nota de voz 🎙️"
)
MSG_POST_BUG_THANKS = (
    "✅ ¡Gracias por contarnos!\n\n"
    "📋 *Reporte #{case_id}*\n\n"
    "Lo revisaremos pronto. 💪"
)
MSG_POST_FEEDBACK_SKIP = "No hay problema. ¡Gracias por tu respuesta! 👍"

MSG_FEEDBACK_EMPTY = (
    "Por favor incluye tu {label} después del comando.\n\n"
    "Ejemplo: _/{cmd} la búsqueda de losartan no me devolvió resultados_"
)
MSG_FEEDBACK_REGISTERED = (
    "✅ ¡Gracias! Tu {label} ha sido registrado.\n\n"
    "\U0001f4cb *Caso #{case_id}*\n\n"
    "Nuestro equipo lo revisará pronto."
)
MSG_FEEDBACK_ERROR = (
    "Lo siento, no pude registrar tu comentario en este momento. "
    "Por favor inténtalo de nuevo en unos minutos."
)

MSG_SUGGESTION_EMPTY = (
    "Por favor incluye tu sugerencia después del comando.\n\n"
    "Ejemplo: _/sugerencia me gustaría poder filtrar por precio_"
)
MSG_SUGGESTION_REGISTERED = (
    "✅ ¡Gracias por tu sugerencia!\n\n"
    "📋 *Sugerencia #{case_id}*\n\n"
    "La revisaremos pronto."
)
MSG_SUGGESTION_ERROR = (
    "Lo siento, no pude registrar tu sugerencia en este momento. "
    "Por favor inténtalo de nuevo en unos minutos."
)

MSG_NEED_LOCATION = (
    "{name}, necesito saber tu ubicación para buscarte farmacias cercanas.\n\n"
    "*¿En qué zona o barrio estás?*\nEjemplo: _La Boyera_, _Chacao_, _Maracaibo_"
)

MSG_CLARIFY_CANCELED = (
    "Listo, cancelé la búsqueda anterior. Dime que necesitas \U0001f64c"
)

# Shown instead of the ¿Te sirvió? prompt when a drug search returns zero
# results. Asking "did it help?" when there is literally nothing to rate was
# a UX confusion signal in the v0.12.3 prod test (Item 34).
MSG_RETRY_DIFFERENT_NAME = (
    "\U0001f4a1 Si no encontraste lo que buscabas, prueba con otro nombre "
    "o el principio activo. Ejemplo: _acetaminofen_ en vez de _tachipirin_."
)

# Words that cancel a pending clarification and reset the state.
_CLARIFY_CANCEL_WORDS = {
    "cancelar", "cancela", "cancel", "olvidalo", "olvídalo", "nada",
    "dejalo", "déjalo", "no", "ninguno", "ninguna",
}

# ── Category quick-reply menu (Item 29, v0.13.2) ────────────────────────
# Shown as a WhatsApp interactive list when an onboarded user sends a bare
# greeting. Each tuple is (reply_id, display_title). Reply IDs are stable
# machine identifiers — the title is what the user sees in the list AND
# what we echo back in the follow-up prompt.
#
# Category set chosen after Item 29 product review (2026-04-11):
# kept Medicamentos + Cuidado Personal (core pharmacy volume), added
# Belleza (high-margin), Alimentos (Farmatodo sells food), and
# Artículos Hogar (pharmacy-adjacent). Dropped Higiene (overlap with
# Cuidado Personal) and Equipos Ortopédicos (too niche). See MEMORY.md.
CATEGORIES: list[tuple[str, str]] = [
    ("cat_medicamentos", "Medicamentos"),
    ("cat_cuidado_personal", "Cuidado Personal"),
    ("cat_belleza", "Belleza"),
    ("cat_alimentos", "Alimentos"),
    ("cat_hogar", "Articulos Hogar"),
]
_CATEGORY_BY_ID: dict[str, str] = {cat_id: title for cat_id, title in CATEGORIES}

MSG_CATEGORY_LIST_BODY = (
    "\U0001f48a Hola *{name}*! ¿Qué estas buscando hoy?\n\n"
    "Elige una categoría o escribe directamente el nombre de un producto."
)
MSG_CATEGORY_LIST_BUTTON = "Ver categorias"
MSG_CATEGORY_LIST_HEADER = "FarmaFacil"
MSG_CATEGORY_LIST_FOOTER = "Puedes cancelar en cualquier momento"

MSG_CATEGORY_PROMPT = (
    "\U0001f6cd *{category}* - ¿Que producto buscas?\n\n"
    "Escribe el nombre y lo busco para ti.\n"
    "Ejemplo: _losartan_, _shampoo_, _vitamina C_"
)
MSG_CATEGORY_CANCELED = (
    "Listo, cancelé la búsqueda por categoría. Dime que necesitas \U0001f64c"
)

# ── Admin chat messages (Item 35, v0.14.0) ─────────────────────────────
MSG_ADMIN_DENIED = (
    "\U0001f512 No tienes permisos de admin. "
    "Solo usuarios con *chat_admin* activado desde el dashboard pueden "
    "usar este modo."
)

MSG_ADMIN_WELCOME = (
    "\U0001f6e0️ *Modo Admin ACTIVADO* — usando Claude Opus.\n\n"
    "Puedes pedirme cosas en lenguaje natural y yo llamo las "
    "herramientas necesarias para responderte.\n\n"
    "*Comandos disponibles:*\n"
    "• */admin* — activar/desactivar este modo\n"
    "• */admin off* — salir del modo admin\n"
    "• */models* — ver modelo default + alternativas\n"
    "• */model haiku|sonnet|opus* — cambiar modelo default "
    "de usuarios\n"
    "• */stats* — estadisticas de uso + costos\n"
    "• */bug <texto>* o */comentario <texto>* — registrar feedback\n"
    "• */sugerencia <texto>* — enviar una sugerencia\n"
    "• */simulate* — adjunta un archivo con preguntas + caption /simulate\n\n"
    "*Ejemplos de lo que puedo hacer:*\n"
    "• _Mostrame los ultimos 10 feedbacks pendientes_\n"
    "• _Ver caso #12_ / _Marcar caso #12 revisado_\n"
    "• _Ver el rol pharmacy_advisor con sus reglas_\n"
    "• _Agregar una regla al rol X diciendo Y_\n"
    "• _Ver usuarios recientes_\n"
    "• _Ver la memoria del usuario 5491112345678_\n"
    "• _Listar farmacias de Locatel en CCS_\n"
    "• _Desactivar la farmacia #42_\n"
    "• _Cuantos productos tenemos?_\n"
    "• _Top 10 busquedas_\n"
    "• _Leer el archivo src/farmafacil/bot/handler.py_\n"
    "• _Cambiar el default_model a sonnet_\n"
    "• _Registrar un bug: el scraper de Farmatodo esta lento_\n"
    "• _Mostrame las sugerencias pendientes_"
)

MSG_ADMIN_OFF = (
    "✅ *Modo Admin DESACTIVADO*.\n"
    "Volves al flujo normal de busqueda de productos."
)

MSG_ADMIN_NOT_ACTIVE = (
    "El modo admin no esta activo. Usa */admin* para activarlo."
)

# Natural-language phrases that also turn off admin mode (redundant UX
# sugar in case the user forgets the slash command).
_ADMIN_OFF_PHRASES: frozenset[str] = frozenset({
    "/admin off", "admin off", "salir admin", "desactivar admin",
    "turn off admin", "apagar admin", "cerrar admin",
})

# Words that are not valid person names (used by _is_valid_name).
_NOT_NAMES = {
    "hi", "hello", "hey", "hola", "buenas", "buenos", "ola", "que tal",
    "good", "ok", "si", "no", "yes", "gracias", "thanks", "bye", "chao",
    "ayuda", "help", "losartan", "acetaminofen", "ibuprofeno", "1", "2",
}
