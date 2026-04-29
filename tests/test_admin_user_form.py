"""Tests for the SQLAdmin UserAdmin edit form (Q3, v0.20.0).

Verifies that:
  * The constrained-value dropdowns (city_code, response_mode, chat_debug,
    onboarding_step, display_preference) match their canonical sources.
  * Counter / log-pointer fields are rendered read-only via
    ``form_widget_args``.
  * Tooltip help text is wired on the documented free-text fields.
  * The security boundary on ``chat_admin`` and ``admin_mode_active``
    is preserved (still in ``form_columns``, not in the read-only set).

These tests assert against the declarative class attributes — no live
HTTP/HTML rendering — so they're fast and stable.
"""

from wtforms import SelectField

from farmafacil.api.admin import (
    USER_CHAT_DEBUG_CHOICES,
    USER_CITY_CODE_CHOICES,
    USER_DISPLAY_PREFERENCE_CHOICES,
    USER_FORM_TOOLTIPS,
    USER_ONBOARDING_STEP_CHOICES,
    USER_READONLY_FIELDS,
    USER_RESPONSE_MODE_CHOICES,
    UserAdmin,
    _coerce_optional_str,
)
from farmafacil.services.settings import _VALID_DEBUG, _VALID_MODES
from farmafacil.services.store_backfill import FARMATODO_CITIES


# --- Dropdown choices match canonical sources ----------------------------


class TestCityCodeChoices:
    """city_code dropdown values come from FARMATODO_CITIES."""

    def test_choices_cover_every_farmatodo_city(self):
        choice_values = {value for value, _ in USER_CITY_CODE_CHOICES}
        assert choice_values == set(FARMATODO_CITIES.keys())

    def test_choices_are_sorted_alphabetically(self):
        values = [value for value, _ in USER_CITY_CODE_CHOICES]
        assert values == sorted(values)

    def test_choices_are_value_label_pairs(self):
        # All entries are (str, str) tuples — wtforms requirement.
        for value, label in USER_CITY_CODE_CHOICES:
            assert isinstance(value, str) and value
            assert isinstance(label, str) and label


class TestDisplayPreferenceChoices:
    """display_preference is NOT NULL with default 'grid' — no blank row."""

    def test_no_blank_choice(self):
        values = {value for value, _ in USER_DISPLAY_PREFERENCE_CHOICES}
        assert "" not in values

    def test_known_values_present(self):
        # "grid", "detail", "image" are the values the codebase writes
        # (see services/users.py and tests/test_location_sharing.py).
        values = {value for value, _ in USER_DISPLAY_PREFERENCE_CHOICES}
        assert {"grid", "detail", "image"}.issubset(values)


class TestResponseModeChoices:
    """response_mode dropdown values come from settings._VALID_MODES."""

    def test_blank_option_first(self):
        # NULL means "use global", so a blank placeholder is required.
        assert USER_RESPONSE_MODE_CHOICES[0][0] == ""

    def test_non_blank_values_match_valid_modes(self):
        non_blank = {value for value, _ in USER_RESPONSE_MODE_CHOICES if value}
        assert non_blank == set(_VALID_MODES)


class TestChatDebugChoices:
    """chat_debug dropdown values come from settings._VALID_DEBUG."""

    def test_blank_option_first(self):
        # NULL means "use global".
        assert USER_CHAT_DEBUG_CHOICES[0][0] == ""

    def test_non_blank_values_match_valid_debug(self):
        non_blank = {value for value, _ in USER_CHAT_DEBUG_CHOICES if value}
        assert non_blank == set(_VALID_DEBUG)


class TestOnboardingStepChoices:
    """onboarding_step dropdown covers all states the bot writes."""

    def test_blank_option_first(self):
        # NULL means "onboarding complete".
        assert USER_ONBOARDING_STEP_CHOICES[0][0] == ""

    def test_includes_all_known_steps(self):
        values = {value for value, _ in USER_ONBOARDING_STEP_CHOICES if value}
        # Steps written by services/users.py and bot/handler.py:
        expected = {
            "welcome",
            "awaiting_name",
            "awaiting_location",
            "awaiting_feedback",
            "awaiting_feedback_detail",
            # Legacy step still referenced in services/users.py
            # validate_user_profile branch — keep selectable for repair.
            "awaiting_preference",
        }
        assert expected.issubset(values)


# --- form_overrides wires SelectField on every constrained column --------


class TestFormOverrides:
    """Every dropdown field must be overridden to a SelectField."""

    def test_city_code_is_select(self):
        assert UserAdmin.form_overrides["city_code"] is SelectField

    def test_display_preference_is_select(self):
        assert UserAdmin.form_overrides["display_preference"] is SelectField

    def test_response_mode_is_select(self):
        assert UserAdmin.form_overrides["response_mode"] is SelectField

    def test_chat_debug_is_select(self):
        assert UserAdmin.form_overrides["chat_debug"] is SelectField

    def test_onboarding_step_is_select(self):
        assert UserAdmin.form_overrides["onboarding_step"] is SelectField


# --- form_args wires choices + nullable coerce on each select ------------


class TestFormArgs:
    """Each SelectField has the right choices and (where applicable) the
    nullable coerce that maps empty submissions to None."""

    def test_city_code_choices_include_blank_and_canonical(self):
        choices = UserAdmin.form_args["city_code"]["choices"]
        values = {value for value, _ in choices}
        # Blank for "clear the field" + every canonical city.
        assert "" in values
        assert set(FARMATODO_CITIES.keys()).issubset(values)

    def test_city_code_coerce_is_nullable(self):
        # city_code is nullable on the model — empty must round-trip to None.
        coerce = UserAdmin.form_args["city_code"]["coerce"]
        assert coerce("") is None
        assert coerce("CCS") == "CCS"

    def test_display_preference_coerce_is_str_not_optional(self):
        # display_preference is NOT NULL on the model — never nullable.
        coerce = UserAdmin.form_args["display_preference"]["coerce"]
        assert coerce is str

    def test_response_mode_coerce_is_nullable(self):
        coerce = UserAdmin.form_args["response_mode"]["coerce"]
        assert coerce("") is None
        assert coerce("hybrid") == "hybrid"

    def test_chat_debug_coerce_is_nullable(self):
        coerce = UserAdmin.form_args["chat_debug"]["coerce"]
        assert coerce("") is None
        assert coerce("enabled") == "enabled"

    def test_onboarding_step_coerce_is_nullable(self):
        coerce = UserAdmin.form_args["onboarding_step"]["coerce"]
        assert coerce("") is None
        assert coerce("awaiting_name") == "awaiting_name"


class TestCoerceHelper:
    """The shared _coerce_optional_str helper must collapse empty / None /
    whitespace to None and pass through real values unchanged."""

    def test_none_in_returns_none(self):
        assert _coerce_optional_str(None) is None

    def test_empty_string_returns_none(self):
        assert _coerce_optional_str("") is None

    def test_whitespace_returns_none(self):
        assert _coerce_optional_str("   ") is None

    def test_real_value_returns_string(self):
        assert _coerce_optional_str("hybrid") == "hybrid"

    def test_strips_surrounding_whitespace(self):
        assert _coerce_optional_str("  hybrid  ") == "hybrid"


# --- form_widget_args (HTML attrs) vs. form_args (wtforms Field kwargs) --
#
# Important split:
#   * ``form_widget_args`` → wtforms ``render_kw`` → HTML attributes on
#     the rendered ``<input>``. Use for things like ``readonly: True``,
#     ``placeholder``, etc.
#   * ``form_args`` → wtforms Field constructor kwargs. The
#     ``description`` kwarg is what SQLAdmin's _macros.html renders as
#     ``<small class="text-muted">``. Putting ``description`` in
#     ``form_widget_args`` would generate a useless non-standard HTML
#     attribute (regression caught during the v0.20.0 smoke test).


class TestReadonlyFields:
    """Counter and log-pointer fields must be rendered read-only.

    ``readonly=True`` lives in ``form_widget_args`` because that is what
    SQLAdmin passes to wtforms as ``render_kw``, which becomes the HTML
    ``readonly`` attribute on the rendered ``<input>``.
    """

    EXPECTED_READONLY = {
        "total_tokens_in",
        "total_tokens_out",
        "last_tokens_in",
        "last_tokens_out",
        "tokens_in_haiku",
        "tokens_out_haiku",
        "calls_haiku",
        "tokens_in_sonnet",
        "tokens_out_sonnet",
        "calls_sonnet",
        "tokens_in_admin",
        "tokens_out_admin",
        "calls_admin",
        "last_search_query",
        "last_search_log_id",
        "created_at",
        "updated_at",
    }

    def test_module_constant_lists_every_expected_field(self):
        assert set(USER_READONLY_FIELDS) == self.EXPECTED_READONLY

    def test_each_readonly_field_has_readonly_widget_arg(self):
        for field in USER_READONLY_FIELDS:
            args = UserAdmin.form_widget_args.get(field)
            assert args is not None, f"{field} missing from form_widget_args"
            assert args.get("readonly") is True, (
                f"{field} not marked readonly=True"
            )

    def test_each_readonly_field_has_explanatory_tooltip(self):
        # Read-only counters also carry a "Read-only — written by the bot"
        # description so admins understand why they can't edit. The
        # description must live in form_args, NOT form_widget_args.
        for field in USER_READONLY_FIELDS:
            args = UserAdmin.form_args.get(field, {})
            assert args.get("description"), (
                f"{field} missing readonly explanation in form_args"
            )

    def test_chat_admin_is_NOT_readonly(self):
        # SECURITY INVARIANT: chat_admin is the gate for admin chat mode
        # and must remain editable from the SQLAdmin UI (which is the only
        # sanctioned channel to grant admin chat access).
        widget = UserAdmin.form_widget_args.get("chat_admin", {})
        assert widget.get("readonly") is not True

    def test_admin_mode_active_is_NOT_readonly(self):
        # Toggleable so admins can manually exit a stuck admin session.
        widget = UserAdmin.form_widget_args.get("admin_mode_active", {})
        assert widget.get("readonly") is not True


class TestTooltips:
    """Free-text fields whose meaning isn't obvious must have help text.

    Tooltips MUST live in ``form_args`` (passed to the wtforms Field
    constructor as ``description``) — that's what SQLAdmin's _macros.html
    renders as ``<small class="text-muted">``. Putting them in
    ``form_widget_args`` would generate a non-standard HTML attribute that
    browsers ignore (regression caught during the v0.20.0 smoke test).
    """

    EXPECTED_TOOLTIP_FIELDS = {
        "phone_number",
        "latitude",
        "longitude",
        "zone_name",
        "awaiting_clarification_context",
        "awaiting_category_search",
    }

    def test_module_constant_covers_every_expected_field(self):
        assert set(USER_FORM_TOOLTIPS.keys()) == self.EXPECTED_TOOLTIP_FIELDS

    def test_each_tooltip_field_has_description_in_form_args(self):
        # Regression guard: tooltips MUST be in form_args, not
        # form_widget_args. See TestTooltips docstring.
        for field in USER_FORM_TOOLTIPS:
            args = UserAdmin.form_args.get(field, {})
            assert args.get("description") == USER_FORM_TOOLTIPS[field], (
                f"{field} tooltip missing or wrong in form_args"
            )

    def test_tooltip_NOT_in_form_widget_args(self):
        # If a tooltip leaks into form_widget_args it renders as a useless
        # HTML attribute. Catch that explicitly.
        for field in USER_FORM_TOOLTIPS:
            widget = UserAdmin.form_widget_args.get(field, {})
            assert "description" not in widget, (
                f"{field}: 'description' must not be in form_widget_args"
            )

    def test_lat_lng_tooltips_mention_venezuela_bbox(self):
        # Make sure the tooltip text actually references the VE bbox so
        # admins entering coordinates have a sanity-check range.
        for field in ("latitude", "longitude"):
            text = UserAdmin.form_args[field]["description"].lower()
            assert "venezuela" in text or "bbox" in text

    def test_awaiting_tooltips_mention_null_recovery(self):
        # The awaiting_* tooltips must explain that NULL clears stuck state.
        for field in (
            "awaiting_clarification_context",
            "awaiting_category_search",
        ):
            text = UserAdmin.form_args[field]["description"].lower()
            assert "null" in text or "clear" in text


# --- form_columns ordering + presence ------------------------------------


class TestFormColumns:
    """The ordered list of columns visible in the edit form."""

    def test_phone_number_is_first(self):
        # Identity field — anchors the form so admins know which user
        # they're editing without scrolling.
        first = UserAdmin.form_columns[0]
        # form_columns can hold either string names or Column attributes.
        first_name = (
            first.key if hasattr(first, "key") else str(first)
        )
        assert first_name == "phone_number"

    def test_chat_admin_present(self):
        names = {
            (col.key if hasattr(col, "key") else str(col))
            for col in UserAdmin.form_columns
        }
        assert "chat_admin" in names

    def test_admin_mode_active_present(self):
        names = {
            (col.key if hasattr(col, "key") else str(col))
            for col in UserAdmin.form_columns
        }
        assert "admin_mode_active" in names

    def test_all_constrained_fields_present(self):
        names = {
            (col.key if hasattr(col, "key") else str(col))
            for col in UserAdmin.form_columns
        }
        # Every field that has a SelectField override must be in the form.
        for field in UserAdmin.form_overrides:
            assert field in names, f"{field} missing from form_columns"

    def test_all_readonly_counters_present(self):
        names = {
            (col.key if hasattr(col, "key") else str(col))
            for col in UserAdmin.form_columns
        }
        # Counter columns are kept in the form (visible-but-readonly), so
        # admins see usage in context. Timestamps are not in form_columns
        # because SQLAdmin auto-manages those — exclude from this check.
        for field in USER_READONLY_FIELDS:
            if field in {"created_at", "updated_at"}:
                continue
            assert field in names, f"{field} missing from form_columns"
