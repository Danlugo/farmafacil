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

import pytest
from wtforms import SelectField

from farmafacil.api.admin import (
    USER_CHAT_DEBUG_CHOICES,
    USER_CITY_CODE_CHOICES,
    USER_DISPLAY_PREFERENCE_CHOICES,
    USER_FORM_TOOLTIPS,
    USER_ONBOARDING_STEP_CHOICES,
    USER_POST_FEEDBACK_CHOICES,
    USER_READONLY_FIELDS,
    USER_RESPONSE_MODE_CHOICES,
    UserAdmin,
    _coerce_optional_str,
)
from farmafacil.services.settings import _VALID_DEBUG, _VALID_MODES, _VALID_TOGGLE
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


class TestPostFeedbackChoices:
    """post_feedback_suggestion / post_feedback_bug_report dropdowns come from
    settings._VALID_TOGGLE."""

    def test_blank_option_first(self):
        # NULL means "use global".
        assert USER_POST_FEEDBACK_CHOICES[0][0] == ""

    def test_non_blank_values_match_valid_toggle(self):
        non_blank = {value for value, _ in USER_POST_FEEDBACK_CHOICES if value}
        assert non_blank == set(_VALID_TOGGLE)

    def test_true_and_false_present(self):
        values = {value for value, _ in USER_POST_FEEDBACK_CHOICES}
        assert "true" in values
        assert "false" in values


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
            # v0.22.2: post-feedback follow-up states
            "awaiting_post_suggestion",
            "awaiting_post_bug",
            # Legacy step still referenced in services/users.py
            # validate_user_profile branch — keep selectable for repair.
            "awaiting_preference",
        }
        assert expected.issubset(values)


# --- form_overrides wires SelectField on every constrained column --------


class TestFormOverrides:
    """Every dropdown field must be overridden to a SelectField."""

    @pytest.mark.parametrize("field", [
        "city_code",
        "display_preference",
        "response_mode",
        "chat_debug",
        "post_feedback_suggestion",
        "post_feedback_bug_report",
        "onboarding_step",
    ])
    def test_field_is_select(self, field):
        assert UserAdmin.form_overrides[field] is SelectField


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

    @pytest.mark.parametrize("field,sample_value", [
        ("response_mode", "hybrid"),
        ("chat_debug", "enabled"),
        ("post_feedback_suggestion", "true"),
        ("post_feedback_bug_report", "false"),
        ("onboarding_step", "awaiting_name"),
    ])
    def test_nullable_coerce(self, field, sample_value):
        # All these fields are nullable — empty string must round-trip to None,
        # and a real value must pass through unchanged.
        coerce = UserAdmin.form_args[field]["coerce"]
        assert coerce("") is None
        assert coerce(sample_value) == sample_value


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

    @pytest.mark.parametrize("field", ["chat_admin", "admin_mode_active"])
    def test_security_field_present(self, field):
        names = {
            (col.key if hasattr(col, "key") else str(col))
            for col in UserAdmin.form_columns
        }
        assert field in names

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


# --- Non-User model dropdowns (v0.33.0) -----------------------------------
# These tests verify that models with constrained values use SelectField
# dropdowns, and that the choice sets match their canonical sources.


class TestSelectFieldOverrides:
    """Admin classes with constrained values must use SelectField overrides.

    Covers IntentKeywordAdmin, PharmacyLocationAdmin, ProductAdmin, and
    ScheduledTaskAdmin — all follow the identical assertion pattern.
    """

    @pytest.mark.parametrize("admin_cls_name,field", [
        ("IntentKeywordAdmin", "action"),
        ("PharmacyLocationAdmin", "pharmacy_chain"),
        ("PharmacyLocationAdmin", "city_code"),
        ("ProductAdmin", "pharmacy_chain"),
        ("ScheduledTaskAdmin", "task_key"),
    ])
    def test_field_is_select(self, admin_cls_name, field):
        import farmafacil.api.admin as admin_module
        admin_cls = getattr(admin_module, admin_cls_name)
        assert admin_cls.form_overrides.get(field) is SelectField


class TestIntentKeywordDropdown:
    """IntentKeywordAdmin action choices cover all known intent types."""

    def test_action_choices_include_drug_search(self):
        from farmafacil.api.admin import INTENT_ACTION_CHOICES
        values = {v for v, _ in INTENT_ACTION_CHOICES}
        assert "drug_search" in values
        assert "greeting" in values
        assert "emergency" in values
        assert "location_change" in values


class TestPharmacyLocationDropdowns:
    """PharmacyLocationAdmin chain choices cover known pharmacy chains."""

    def test_chain_choices_include_known_chains(self):
        from farmafacil.api.admin import PHARMACY_CHAIN_CHOICES
        values = {v for v, _ in PHARMACY_CHAIN_CHOICES}
        assert "Farmatodo" in values
        assert "Farmacias SAAS" in values
        assert "Locatel" in values


class TestScheduledTaskDropdown:
    """ScheduledTaskAdmin task_key choices match the live TASK_REGISTRY."""

    def test_task_key_choices_match_registry(self):
        from farmafacil.api.admin import TASK_KEY_CHOICES
        from farmafacil.services.scheduler import TASK_REGISTRY
        choice_keys = {v for v, _ in TASK_KEY_CHOICES}
        assert choice_keys == set(TASK_REGISTRY.keys())


class TestAppSettingValidation:
    """AppSettingAdmin validates constrained values on save."""

    def test_setting_value_choices_covers_constrained_keys(self):
        from farmafacil.api.admin import SETTING_VALUE_CHOICES
        assert "response_mode" in SETTING_VALUE_CHOICES
        assert "chat_debug" in SETTING_VALUE_CHOICES
        assert "default_model" in SETTING_VALUE_CHOICES
        assert "category_menu_enabled" in SETTING_VALUE_CHOICES
        assert "post_feedback_suggestion" in SETTING_VALUE_CHOICES
        assert "post_feedback_bug_report" in SETTING_VALUE_CHOICES

    def test_free_text_settings_not_constrained(self):
        from farmafacil.api.admin import SETTING_VALUE_CHOICES
        assert "cache_ttl_minutes" not in SETTING_VALUE_CHOICES
        assert "max_search_results" not in SETTING_VALUE_CHOICES
        assert "relevance_threshold" not in SETTING_VALUE_CHOICES

    def test_response_mode_valid_values(self):
        from farmafacil.api.admin import SETTING_VALUE_CHOICES
        assert set(SETTING_VALUE_CHOICES["response_mode"]) == {"hybrid", "ai_only"}

    def test_default_model_valid_values(self):
        from farmafacil.api.admin import SETTING_VALUE_CHOICES
        from farmafacil.services.settings import VALID_MODEL_ALIASES
        assert set(SETTING_VALUE_CHOICES["default_model"]) == VALID_MODEL_ALIASES

    @pytest.mark.asyncio
    async def test_on_model_change_rejects_invalid_value(self):
        """on_model_change raises ValueError for invalid constrained values."""
        from farmafacil.api.admin import AppSettingAdmin
        from farmafacil.models.database import AppSetting

        admin = AppSettingAdmin.__new__(AppSettingAdmin)
        model = AppSetting(key="response_mode", value="hybrid")
        data = {"key": "response_mode", "value": "invalid_mode"}
        with pytest.raises(ValueError, match="Invalid value 'invalid_mode'"):
            await admin.on_model_change(data, model, False, None)

    @pytest.mark.asyncio
    async def test_on_model_change_allows_valid_value(self):
        """on_model_change passes for valid constrained values."""
        from farmafacil.api.admin import AppSettingAdmin
        from farmafacil.models.database import AppSetting

        admin = AppSettingAdmin.__new__(AppSettingAdmin)
        model = AppSetting(key="response_mode", value="hybrid")
        data = {"key": "response_mode", "value": "ai_only"}
        # Should not raise
        await admin.on_model_change(data, model, False, None)

    @pytest.mark.asyncio
    async def test_on_model_change_allows_free_text_settings(self):
        """on_model_change skips validation for free-text settings."""
        from farmafacil.api.admin import AppSettingAdmin
        from farmafacil.models.database import AppSetting

        admin = AppSettingAdmin.__new__(AppSettingAdmin)
        model = AppSetting(key="cache_ttl_minutes", value="10080")
        data = {"key": "cache_ttl_minutes", "value": "anything_goes"}
        # Should not raise — free-text setting
        await admin.on_model_change(data, model, False, None)


# --- Cross-object consistency: AppSetting ↔ User dropdowns ↔ seed --------
#
# The system has three layers where constrained values appear:
#   1. AppSetting admin (SETTING_VALUE_CHOICES) — what an admin can set globally
#   2. User admin dropdowns — what an admin can set per-user as overrides
#   3. Seed defaults (DEFAULTS dict) — what gets seeded into the DB
# All three MUST use exactly the same valid-value sets, sourced from the
# canonical constants in settings.py. These tests enforce that invariant.


class TestAppSettingToUserDropdownConsistency:
    """AppSetting constrained values must exactly match the non-blank values
    in the corresponding User dropdown for each shared field.

    If someone adds a new valid value (e.g. 'turbo' to _VALID_MODES), both
    SETTING_VALUE_CHOICES and USER_*_CHOICES must pick it up automatically.
    This test catches any drift between the two.
    """

    @pytest.mark.parametrize("setting_key,user_choices", [
        ("response_mode", USER_RESPONSE_MODE_CHOICES),
        ("chat_debug", USER_CHAT_DEBUG_CHOICES),
        ("post_feedback_suggestion", USER_POST_FEEDBACK_CHOICES),
        ("post_feedback_bug_report", USER_POST_FEEDBACK_CHOICES),
    ], ids=["response_mode", "chat_debug", "post_feedback_suggestion", "post_feedback_bug_report"])
    def test_app_setting_values_match_user_dropdown(self, setting_key, user_choices):
        from farmafacil.api.admin import SETTING_VALUE_CHOICES

        app_valid = set(SETTING_VALUE_CHOICES[setting_key])
        user_non_blank = {v for v, _ in user_choices if v}
        assert app_valid == user_non_blank, (
            f"Mismatch for '{setting_key}': "
            f"AppSetting accepts {app_valid}, User dropdown offers {user_non_blank}"
        )


class TestSeedDefaultsAreValid:
    """Every seed default for a constrained setting must be a value that
    the AppSetting admin validation would accept.

    Catches: someone changes DEFAULTS["response_mode"] to ("turbo", ...)
    but forgets to add "turbo" to _VALID_MODES.
    """

    @pytest.mark.parametrize("key", [
        "response_mode",
        "chat_debug",
        "category_menu_enabled",
        "post_feedback_suggestion",
        "post_feedback_bug_report",
        "default_model",
    ])
    def test_seed_default_is_valid(self, key):
        from farmafacil.api.admin import SETTING_VALUE_CHOICES
        from farmafacil.services.settings import DEFAULTS

        default_value = DEFAULTS[key][0]
        valid_values = SETTING_VALUE_CHOICES[key]
        assert default_value in valid_values, (
            f"Seed default '{default_value}' for '{key}' is not in "
            f"SETTING_VALUE_CHOICES: {valid_values}"
        )


class TestResolutionFunctionsAcceptCanonicalValues:
    """Resolution functions must correctly resolve every canonical valid value
    without logging warnings or falling back to defaults.

    These functions bridge AppSetting global values → User per-user overrides.
    If they don't accept a value the admin UI allows, the setting silently
    falls back, which is a bug.
    """

    @pytest.mark.parametrize("mode", sorted(_VALID_MODES))
    def test_resolve_response_mode_accepts_all_valid_modes_as_user(self, mode):
        from farmafacil.services.settings import resolve_response_mode
        result = resolve_response_mode(mode, "hybrid")
        assert result == mode

    @pytest.mark.parametrize("mode", sorted(_VALID_MODES))
    def test_resolve_response_mode_accepts_all_valid_modes_as_global(self, mode):
        from farmafacil.services.settings import resolve_response_mode
        result = resolve_response_mode(None, mode)
        assert result == mode

    @pytest.mark.parametrize("debug_val", sorted(_VALID_DEBUG))
    def test_resolve_chat_debug_accepts_all_valid_values_as_user(self, debug_val):
        from farmafacil.services.settings import resolve_chat_debug
        result = resolve_chat_debug(debug_val, "disabled")
        assert result == (debug_val == "enabled")

    @pytest.mark.parametrize("debug_val", sorted(_VALID_DEBUG))
    def test_resolve_chat_debug_accepts_all_valid_values_as_global(self, debug_val):
        from farmafacil.services.settings import resolve_chat_debug
        result = resolve_chat_debug(None, debug_val)
        assert result == (debug_val == "enabled")

    @pytest.mark.parametrize("toggle_val", sorted(_VALID_TOGGLE))
    def test_resolve_post_feedback_accepts_all_valid_values_as_user(self, toggle_val):
        from farmafacil.services.settings import resolve_post_feedback
        result = resolve_post_feedback(toggle_val, "false")
        assert result == (toggle_val == "true")

    @pytest.mark.parametrize("toggle_val", sorted(_VALID_TOGGLE))
    def test_resolve_post_feedback_accepts_all_valid_values_as_global(self, toggle_val):
        from farmafacil.services.settings import resolve_post_feedback
        result = resolve_post_feedback(None, toggle_val)
        assert result == (toggle_val == "true")

    def test_resolve_response_mode_null_user_falls_to_global(self):
        from farmafacil.services.settings import resolve_response_mode
        assert resolve_response_mode(None, "ai_only") == "ai_only"

    def test_resolve_chat_debug_null_user_falls_to_global(self):
        from farmafacil.services.settings import resolve_chat_debug
        assert resolve_chat_debug(None, "enabled") is True

    def test_resolve_post_feedback_null_user_falls_to_global(self):
        from farmafacil.services.settings import resolve_post_feedback
        assert resolve_post_feedback(None, "true") is True


# --- Friendly-name formatters on FK columns (v0.36.0, Item 115) -----------
#
# Every admin view that displays a foreign-key ID column must have a
# column_formatter rendering the related object's __repr__ as a clickable
# link.  These tests verify the invariant so future admin views can't
# regress to bare integer IDs.


class TestFriendlyNameFormatters:
    """FK columns in admin list views must render human-friendly linked names.

    Each parametrized case checks that the admin class has a
    ``column_formatters`` entry for the FK column attribute, and that the
    formatter produces a Markup ``<a>`` link (not a bare integer).
    """

    # (admin_class_name, model_fk_attribute_name, relationship_attr)
    FK_FORMATTER_CASES = [
        ("UserMemoryAdmin", "user_id", "user"),
        ("AiRoleRuleAdmin", "role_id", "role"),
        ("AiRoleSkillAdmin", "role_id", "role"),
        ("SearchLogAdmin", "user_id", "user"),
        ("UserFeedbackAdmin", "user_id", "user"),
        ("UserFeedbackAdmin", "conversation_log_id", None),
        ("UserSuggestionAdmin", "user_id", "user"),
        ("ProductPriceAdmin", "product_id", "product"),
        ("DrugListingAdmin", "pharmacy_id", "pharmacy"),
        ("VoiceMessageAdmin", "user_id", "user"),
    ]

    @pytest.mark.parametrize(
        "admin_cls_name,fk_attr,rel_attr",
        FK_FORMATTER_CASES,
        ids=[f"{c[0]}.{c[1]}" for c in FK_FORMATTER_CASES],
    )
    def test_fk_column_has_formatter(self, admin_cls_name, fk_attr, rel_attr):
        """Admin view registers a column_formatter for the FK column."""
        import farmafacil.api.admin as admin_module
        admin_cls = getattr(admin_module, admin_cls_name)

        # column_formatters keys can be InstrumentedAttribute or string
        formatter_keys = set()
        for key in admin_cls.column_formatters:
            if hasattr(key, "key"):
                formatter_keys.add(key.key)
            else:
                formatter_keys.add(str(key))

        assert fk_attr in formatter_keys, (
            f"{admin_cls_name} is missing a column_formatter for '{fk_attr}'. "
            f"FK columns must render friendly names, not bare IDs."
        )

    @pytest.mark.parametrize(
        "admin_cls_name,fk_attr",
        [
            ("UserMemoryAdmin", "user_id"),
            ("AiRoleRuleAdmin", "role_id"),
            ("AiRoleSkillAdmin", "role_id"),
            ("SearchLogAdmin", "user_id"),
            ("UserFeedbackAdmin", "user_id"),
            ("UserSuggestionAdmin", "user_id"),
            ("ProductPriceAdmin", "product_id"),
            ("DrugListingAdmin", "pharmacy_id"),
            ("VoiceMessageAdmin", "user_id"),
        ],
        ids=[
            "UserMemory.user_id", "AiRoleRule.role_id", "AiRoleSkill.role_id",
            "SearchLog.user_id", "UserFeedback.user_id", "UserSuggestion.user_id",
            "ProductPrice.product_id", "DrugListing.pharmacy_id", "VoiceMessage.user_id",
        ],
    )
    def test_fk_column_label_is_friendly(self, admin_cls_name, fk_attr):
        """Column labels for FK columns must NOT end with 'ID'."""
        import farmafacil.api.admin as admin_module
        admin_cls = getattr(admin_module, admin_cls_name)

        label = admin_cls.column_labels.get(fk_attr, fk_attr)
        assert not label.strip().endswith("ID"), (
            f"{admin_cls_name}.column_labels['{fk_attr}'] = '{label}' "
            f"still ends with 'ID'. Use a friendly name like 'User' or 'Product'."
        )


class TestFkFormatterHelper:
    """Unit tests for the _fk_formatter helper function."""

    def test_returns_dash_for_null_fk(self):
        from farmafacil.api.admin import _fk_formatter

        fmt = _fk_formatter("user", "user_id", "user")

        class FakeModel:
            user_id = None
            user = None

        result = fmt(FakeModel(), "user_id")
        assert result == "—"

    def test_returns_link_with_repr(self):
        from farmafacil.api.admin import _fk_formatter
        from markupsafe import Markup

        fmt = _fk_formatter("user", "user_id", "user")

        class FakeUser:
            def __repr__(self):
                return "Daniel (14258904657)"

        class FakeModel:
            user_id = 3
            user = FakeUser()

        result = fmt(FakeModel(), "user_id")
        assert isinstance(result, Markup)
        assert "/admin/user/details/3" in result
        assert "Daniel" in result

    def test_falls_back_to_id_when_relationship_missing(self):
        from farmafacil.api.admin import _fk_formatter
        from markupsafe import Markup

        fmt = _fk_formatter("user", "user_id", "user")

        class FakeModel:
            user_id = 99
            user = None

        result = fmt(FakeModel(), "user_id")
        assert isinstance(result, Markup)
        assert "#99" in result
        assert "/admin/user/details/99" in result


class TestModelReprForAdmin:
    """Models referenced by FK formatters must have useful __repr__."""

    def test_user_repr_includes_name_and_phone(self):
        from farmafacil.models.database import User
        u = User(name="Daniel", phone_number="14258904657")
        assert "Daniel" in repr(u)
        assert "14258904657" in repr(u)

    def test_user_repr_phone_only_when_no_name(self):
        from farmafacil.models.database import User
        u = User(name=None, phone_number="14258904657")
        assert repr(u) == "14258904657"

    def test_product_repr_includes_drug_and_chain(self):
        from farmafacil.models.database import Product
        p = Product(drug_name="Losartán 50mg", pharmacy_chain="Farmatodo",
                    external_id="test")
        assert "Losartán 50mg" in repr(p)
        assert "Farmatodo" in repr(p)

    def test_pharmacy_repr_is_name(self):
        from farmafacil.models.database import Pharmacy
        ph = Pharmacy(name="Farmatodo", website_url="https://farmatodo.com")
        assert repr(ph) == "Farmatodo"

    def test_ai_role_repr_uses_display_name(self):
        from farmafacil.models.database import AiRole
        r = AiRole(display_name="Asesor Farmacéutico", name="pharmacy_advisor",
                   system_prompt="test")
        assert "Asesor Farmacéutico" in repr(r)
