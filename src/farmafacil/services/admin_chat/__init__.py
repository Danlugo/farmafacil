"""Admin Chat tools package.

Public API (callers use these):
    build_tools_manifest()  — tool manifest string for the admin system prompt
    execute_tool(name, args, *, admin_user_id)  — dispatcher
    parse_tool_args(raw)  — JSON-decode LLM TOOL_CALL ARGS block

Internal modules:
    registry          — TOOLS dict + public API functions
    _helpers          — _resolve_user_ref, _fmt_bool, _truncate
    feedback_tools    — feedback, suggestions, voice messages
    conversation_tools — conversation logs
    ai_tools          — AI roles, rules, skills
    user_tools        — users, user memory
    pharmacy_tools    — pharmacies, products
    stats_tools       — counts, top searches
    settings_tools    — app settings, default model
    code_tools        — code introspection (_is_allowed_path, PROJECT_ROOT)
    file_tools        — user file management
    simulation_tools  — batch simulate
    search_tools      — web search
    scheduler_tools   — scheduled tasks
    geocode_tools     — geocode / location admin
"""

from __future__ import annotations

# ── Public API ──────────────────────────────────────────────────────────
from .registry import TOOLS, build_tools_manifest, execute_tool, parse_tool_args

# ── Re-exports for test backward-compatibility ──────────────────────────
# Tests import tool functions and constants directly from
# ``farmafacil.services.admin_chat``.  The submodule patches
# (``patch("farmafacil.services.admin_chat.async_session")``) target the
# *defining* module, not this package, so those tests must be updated
# separately to patch the correct submodule path.  The name re-exports
# below ensure that ``from farmafacil.services.admin_chat import _tool_*``
# continues to work without changes to test imports.

from ._helpers import _fmt_bool, _resolve_user_ref, _truncate
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
from .code_tools import (
    PROJECT_ROOT,
    _is_allowed_path,
    _tool_list_code,
    _tool_read_code,
)
from .conversation_tools import (
    _tool_get_conversation_log,
    _tool_list_conversation_logs,
)
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

__all__ = [
    # Public API
    "build_tools_manifest",
    "execute_tool",
    "parse_tool_args",
    "TOOLS",
    # Constants / helpers (used by tests)
    "PROJECT_ROOT",
    "_is_allowed_path",
    "_resolve_user_ref",
    "_fmt_bool",
    "_truncate",
    # Feedback tools
    "_tool_list_feedback",
    "_tool_get_feedback",
    "_tool_update_feedback",
    "_tool_report_issue",
    "_tool_list_suggestions",
    "_tool_get_suggestion",
    "_tool_update_suggestion",
    "_tool_list_voice_messages",
    "_tool_get_voice_message",
    # Conversation tools
    "_tool_list_conversation_logs",
    "_tool_get_conversation_log",
    # AI tools
    "_tool_list_ai_roles",
    "_tool_get_ai_role",
    "_tool_update_ai_role",
    "_tool_add_ai_rule",
    "_tool_update_ai_rule",
    "_tool_delete_ai_rule",
    "_tool_add_ai_skill",
    "_tool_update_ai_skill",
    "_tool_delete_ai_skill",
    # User tools
    "_tool_list_users",
    "_tool_get_user",
    "_tool_get_user_memory",
    "_tool_set_user_memory",
    "_tool_clear_user_memory",
    "_tool_set_user_setting",
    # Pharmacy tools
    "_tool_list_pharmacies",
    "_tool_toggle_pharmacy",
    "_tool_search_products",
    "_tool_get_product",
    # Stats tools
    "_tool_counts",
    "_tool_top_searches",
    # Settings tools
    "_tool_list_app_settings",
    "_tool_get_app_setting",
    "_tool_set_app_setting",
    "_tool_get_default_model",
    "_tool_set_default_model",
    # Code tools
    "_tool_read_code",
    "_tool_list_code",
    # File tools
    "_tool_list_files",
    "_tool_read_file",
    "_tool_write_file",
    "_tool_delete_file",
    # Simulation tools
    "_tool_batch_simulate",
    # Search tools
    "_tool_web_search",
    # Scheduler tools
    "_tool_list_scheduled_tasks",
    "_tool_run_scheduled_task",
    "_tool_toggle_scheduled_task",
    "_tool_update_scheduled_task",
    # Geocode tools
    "_tool_geocode_query",
    "_tool_geocode_reverse",
    "_tool_set_user_location",
    "_tool_set_pharmacy_location",
    "_tool_geocode_health",
]
