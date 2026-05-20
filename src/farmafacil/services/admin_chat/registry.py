"""Tool registry: TOOLS dict, build_tools_manifest, execute_tool, parse_tool_args.

All tool functions are imported from their domain modules and assembled into
the TOOLS dict that drives the admin AI chat loop.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .ai_tools import (
    _tool_add_ai_rule,
    _tool_add_ai_skill,
    _tool_delete_ai_rule,
    _tool_delete_ai_skill,
    _tool_get_ai_role,
    _tool_list_ai_roles,
    _tool_update_ai_role,
    _tool_update_ai_rule,
    _tool_update_ai_skill,
)
from .code_tools import _tool_list_code, _tool_read_code
from .conversation_tools import _tool_get_conversation_log, _tool_list_conversation_logs
from .feedback_tools import (
    _tool_get_feedback,
    _tool_get_suggestion,
    _tool_get_voice_message,
    _tool_list_feedback,
    _tool_list_suggestions,
    _tool_list_voice_messages,
    _tool_report_issue,
    _tool_update_feedback,
    _tool_update_suggestion,
)
from .file_tools import (
    _tool_delete_file,
    _tool_list_files,
    _tool_read_file,
    _tool_write_file,
)
from .geocode_tools import (
    _tool_geocode_health,
    _tool_geocode_query,
    _tool_geocode_reverse,
    _tool_set_pharmacy_location,
    _tool_set_user_location,
)
from .pharmacy_tools import (
    _tool_get_product,
    _tool_list_pharmacies,
    _tool_search_products,
    _tool_toggle_pharmacy,
)
from .scheduler_tools import (
    _tool_list_scheduled_tasks,
    _tool_run_scheduled_task,
    _tool_toggle_scheduled_task,
    _tool_update_scheduled_task,
)
from .search_tools import _tool_web_search
from .settings_tools import (
    _tool_get_app_setting,
    _tool_get_default_model,
    _tool_list_app_settings,
    _tool_set_app_setting,
    _tool_set_default_model,
)
from .simulation_tools import _tool_batch_simulate
from .stats_tools import _tool_counts, _tool_top_searches
from .user_tools import (
    _tool_clear_user_memory,
    _tool_get_user,
    _tool_get_user_memory,
    _tool_list_users,
    _tool_set_user_memory,
    _tool_set_user_setting,
)

logger = logging.getLogger(__name__)

# Mapping: tool name → (description shown in manifest, coroutine)
TOOLS: dict[str, tuple[str, Any]] = {
    # Feedback
    "list_feedback": (
        "Listar casos recientes. Args: limit?, type?, reviewed?",
        _tool_list_feedback,
    ),
    "get_feedback": ("Ver un caso por id. Args: id", _tool_get_feedback),
    "update_feedback": (
        "Marcar revisado o agregar nota. Args: id, reviewed?, reviewer_notes?",
        _tool_update_feedback,
    ),
    "report_issue": (
        "Registrar bug/idea/issue flaggeado por el admin para el backlog de "
        "desarrollo. Args: type (bug|idea|issue), message",
        _tool_report_issue,
    ),
    # Suggestions
    "list_suggestions": (
        "Listar sugerencias de usuarios. Args: limit?, reviewed?",
        _tool_list_suggestions,
    ),
    "get_suggestion": ("Ver una sugerencia por id. Args: id", _tool_get_suggestion),
    "update_suggestion": (
        "Marcar sugerencia revisada o agregar notas. Args: id, reviewed?, admin_notes?",
        _tool_update_suggestion,
    ),
    # Voice messages
    "list_voice_messages": (
        "Listar mensajes de voz recientes. Args: limit?, phone?",
        _tool_list_voice_messages,
    ),
    "get_voice_message": (
        "Ver detalles de un mensaje de voz por id. Args: id",
        _tool_get_voice_message,
    ),
    # Conversation logs
    "list_conversation_logs": (
        "Listar logs recientes. Args: limit?, direction?, phone?",
        _tool_list_conversation_logs,
    ),
    "get_conversation_log": ("Ver un log por id. Args: id", _tool_get_conversation_log),
    # AI roles
    "list_ai_roles": ("Listar todos los roles AI.", _tool_list_ai_roles),
    "get_ai_role": ("Ver rol con sus reglas/skills. Args: name", _tool_get_ai_role),
    "update_ai_role": (
        "Actualizar rol. Args: name, description?, system_prompt?, is_active?",
        _tool_update_ai_role,
    ),
    "add_ai_rule": (
        "Agregar regla a un rol. Args: role_name, name, content, sort_order?",
        _tool_add_ai_rule,
    ),
    "update_ai_rule": (
        "Actualizar regla. Args: id, name?, content?, is_active?, sort_order?",
        _tool_update_ai_rule,
    ),
    "delete_ai_rule": ("Eliminar regla. Args: id", _tool_delete_ai_rule),
    "add_ai_skill": (
        "Agregar skill a un rol. Args: role_name, name, content",
        _tool_add_ai_skill,
    ),
    "update_ai_skill": (
        "Actualizar skill. Args: id, name?, content?, is_active?",
        _tool_update_ai_skill,
    ),
    "delete_ai_skill": ("Eliminar skill. Args: id", _tool_delete_ai_skill),
    # Users
    "list_users": (
        "Listar usuarios. Args: limit?, phone_like?", _tool_list_users,
    ),
    "get_user": (
        "Ver perfil de usuario. Args: user_ref (id o phone)", _tool_get_user,
    ),
    "get_user_memory": (
        "Leer memoria de usuario. Args: user_ref", _tool_get_user_memory,
    ),
    "set_user_memory": (
        "Escribir memoria de usuario. Args: user_ref, text", _tool_set_user_memory,
    ),
    "clear_user_memory": (
        "Borrar memoria de usuario. Args: user_ref", _tool_clear_user_memory,
    ),
    "set_user_setting": (
        "Actualizar un campo permitido del perfil. "
        "Args: user_ref, field, value. "
        "Campos: name, display_preference, response_mode_override, "
        "chat_debug, onboarding_step, admin_mode_active.",
        _tool_set_user_setting,
    ),
    # Pharmacies / products
    "list_pharmacies": (
        "Listar farmacias. Args: chain?, city?, is_active?, limit?",
        _tool_list_pharmacies,
    ),
    "toggle_pharmacy": (
        "Activar/desactivar farmacia. Args: id, is_active",
        _tool_toggle_pharmacy,
    ),
    "search_products": (
        "Buscar productos en el catálogo local. Args: query, limit?",
        _tool_search_products,
    ),
    "get_product": ("Ver producto por id. Args: id", _tool_get_product),
    # Stats
    "counts": ("Conteos globales (usuarios, farmacias, productos, etc).", _tool_counts),
    "top_searches": (
        "Top queries del search_logs. Args: limit?", _tool_top_searches,
    ),
    # App settings
    "list_app_settings": ("Listar todas las app_settings.", _tool_list_app_settings),
    "get_app_setting": ("Ver una setting. Args: key", _tool_get_app_setting),
    "set_app_setting": ("Actualizar setting. Args: key, value", _tool_set_app_setting),
    "get_default_model": (
        "Modelo default actual + lista de alias disponibles.",
        _tool_get_default_model,
    ),
    "set_default_model": (
        "Cambiar modelo default para usuarios. Args: alias (haiku|sonnet|opus)",
        _tool_set_default_model,
    ),
    # Code introspection
    "read_code": (
        "Leer archivo del proyecto (solo src/, tests/, docs/ y archivos raíz "
        "permitidos). Args: path",
        _tool_read_code,
    ),
    "list_code": (
        "Listar directorio del proyecto (allowlist). Args: path?",
        _tool_list_code,
    ),
    "list_files": (
        "Listar archivos en carpeta de usuario o docs del proyecto. "
        "Args: scope ('user'|'docs'), phone? (default: admin's phone)",
        _tool_list_files,
    ),
    "read_file": (
        "Leer contenido de un archivo. Args: path (user:file, docs/file, project:file), phone?",
        _tool_read_file,
    ),
    "write_file": (
        "Crear o sobrescribir un archivo. Args: path, content, phone?",
        _tool_write_file,
    ),
    "delete_file": (
        "Eliminar un archivo (solo carpeta de usuario). Args: path, phone?",
        _tool_delete_file,
    ),
    "batch_simulate": (
        "Ejecutar preguntas de un archivo por el AI de farmacia y guardar resultados. "
        "Args: input_file (path), output_file? (default: batch_results.txt)",
        _tool_batch_simulate,
    ),
    "web_search": (
        "Buscar en internet via Brave Search API. Args: query (str)",
        _tool_web_search,
    ),
    "list_scheduled_tasks": (
        "Listar todas las tareas programadas con su estado, intervalo, y "
        "última ejecución. Args: ninguno",
        _tool_list_scheduled_tasks,
    ),
    "run_scheduled_task": (
        "Ejecutar una tarea programada manualmente por ID. Args: task_id (int)",
        _tool_run_scheduled_task,
    ),
    "toggle_scheduled_task": (
        "Habilitar o deshabilitar una tarea programada. Args: task_id (int), enabled (bool)",
        _tool_toggle_scheduled_task,
    ),
    "update_scheduled_task": (
        "Actualizar intervalo de una tarea programada. Args: task_id (int), interval_minutes (int)",
        _tool_update_scheduled_task,
    ),
    # Geocode / location (v0.19.0, Item 47)
    "geocode_query": (
        "Resolver un nombre de lugar a coordenadas via Nominatim+cache. "
        "Muestra confidence + alternativas. Args: text",
        _tool_geocode_query,
    ),
    "geocode_reverse": (
        "Reverso: ¿qué hay en (lat, lng)? Args: lat, lng",
        _tool_geocode_reverse,
    ),
    "set_user_location": (
        "Re-geocodificar y guardar las coordenadas de un usuario. "
        "Args: phone, query (ej: 'La Boyera, Caracas')",
        _tool_set_user_location,
    ),
    "set_pharmacy_location": (
        "Sobrescribir coords de una farmacia. Pasa query (re-geocode) "
        "O lat+lng (manual). Args: pharmacy_id, query? | lat?+lng?",
        _tool_set_pharmacy_location,
    ),
    "geocode_health": (
        "Salud del cache de geocoding (hits, fallos, TTL). Args: days?",
        _tool_geocode_health,
    ),
}


def build_tools_manifest() -> str:
    """Return the textual tool manifest injected into the admin system prompt."""
    lines = ["HERRAMIENTAS DISPONIBLES:"]
    for name, (desc, _) in TOOLS.items():
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)


async def execute_tool(
    name: str, args: dict[str, Any], *, admin_user_id: int | None = None,
) -> str:
    """Dispatch a tool call by name. Safe-by-default on unknown / failure.

    Args:
        name: Tool name (must exist in ``TOOLS``).
        args: Arguments dict parsed from the LLM's TOOL_CALL block.
        admin_user_id: The calling admin's User.id — injected as
            ``_admin_user_id`` into args so tools that need audit context
            (``report_issue``) can attribute the action.

    Returns:
        A short text string describing the tool result, suitable to feed
        back to the LLM OR to forward to WhatsApp on a FINAL answer.
    """
    if name not in TOOLS:
        return f"Tool desconocida: {name}"
    if not isinstance(args, dict):
        args = {}
    # Strip any LLM-supplied `_admin_user_id` before injection so the LLM can
    # NEVER spoof the caller identity used for audit trails / report_issue.
    # The caller-provided ``admin_user_id`` kwarg is the single source of truth.
    args = {k: v for k, v in args.items() if k != "_admin_user_id"}
    if admin_user_id is not None:
        args["_admin_user_id"] = admin_user_id
    _, fn = TOOLS[name]
    try:
        return await fn(args)
    except Exception as exc:  # noqa: BLE001 — tool errors must never kill the loop
        logger.error(
            "admin_chat tool %s failed args=%s", name, args, exc_info=True,
        )
        return f"Error ejecutando {name}: {exc}"


def parse_tool_args(raw: str) -> dict[str, Any]:
    """Parse the ARGS block from an LLM TOOL_CALL.

    The LLM is instructed to emit JSON. We accept either a JSON object or an
    empty string (= no args). Non-JSON fallback: return empty dict — the tool
    can then report "Falta …" and the LLM will retry with corrected args.
    """
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
