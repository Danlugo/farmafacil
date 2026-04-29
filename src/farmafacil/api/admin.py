"""SQLAdmin dashboard for FarmaFacil.

Provides a full admin UI at /admin for managing all database tables.
Authentication required via username/password.
"""

from markupsafe import Markup
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from wtforms import SelectField

from farmafacil.config import ADMIN_PASSWORD, ADMIN_SECRET_KEY, ADMIN_USERNAME
from farmafacil.models.database import (
    AiRole,
    AiRoleRule,
    AiRoleSkill,
    AppSetting,
    ConversationLog,
    DrugListing,
    GeocodeCache,
    IntentKeyword,
    Pharmacy,
    PharmacyLocation,
    Product,
    ProductPrice,
    ScheduledTask,
    SearchLog,
    SearchQuery,
    User,
    UserFeedback,
    UserMemory,
)
from farmafacil.services.settings import _VALID_DEBUG, _VALID_MODES
from farmafacil.services.store_backfill import FARMATODO_CITIES


# --- UserAdmin form constants ---------------------------------------------
# Single source of truth for the constrained value sets used by the UserAdmin
# edit form. These are pulled from canonical locations in the codebase so a
# future change to (e.g.) FARMATODO_CITIES automatically updates the dropdown.
# A unit test in tests/test_admin_user_form.py asserts each constant matches
# its canonical source.

# city_code: pulled from store_backfill.FARMATODO_CITIES (the same dict the
# stores service uses for nearest-store lookups)
USER_CITY_CODE_CHOICES: list[tuple[str, str]] = [
    (code, code) for code in sorted(FARMATODO_CITIES.keys())
]

# display_preference: NOT NULL on the model, default "grid". Code references
# "grid", "detail", and "image" (see services/users.py and tests). Onboarding
# no longer asks for this post-v0.15.2 but the column remains for compat.
USER_DISPLAY_PREFERENCE_CHOICES: list[tuple[str, str]] = [
    ("grid", "grid"),
    ("detail", "detail"),
    ("image", "image"),
]

# response_mode: nullable on the model. NULL = use global app setting.
# Valid non-null values come from settings._VALID_MODES.
USER_RESPONSE_MODE_CHOICES: list[tuple[str, str]] = [
    ("", "— use global —"),
    *[(v, v) for v in sorted(_VALID_MODES)],
]

# chat_debug: nullable on the model. NULL = use global app setting.
# Valid non-null values come from settings._VALID_DEBUG.
USER_CHAT_DEBUG_CHOICES: list[tuple[str, str]] = [
    ("", "— use global —"),
    *[(v, v) for v in sorted(_VALID_DEBUG)],
]

# onboarding_step: nullable. NULL = onboarding complete. Non-null values are
# the steps set by bot/handler.py and services/users.py. See:
#   services/users.py: "welcome" (initial)
#   bot/handler.py: "awaiting_name", "awaiting_location",
#                   "awaiting_feedback", "awaiting_feedback_detail"
# (services/users.py also references "awaiting_preference" in the validation
# branch for legacy rows; keep it as a selectable value so admins can repair
# stuck rows.)
USER_ONBOARDING_STEP_CHOICES: list[tuple[str, str]] = [
    ("", "— complete (NULL) —"),
    ("welcome", "welcome"),
    ("awaiting_name", "awaiting_name"),
    ("awaiting_location", "awaiting_location"),
    ("awaiting_preference", "awaiting_preference (legacy)"),
    ("awaiting_feedback", "awaiting_feedback"),
    ("awaiting_feedback_detail", "awaiting_feedback_detail"),
]

# Counter / log-pointer fields rendered as readonly inputs in the edit form.
# Visible (so admins can see usage in context) but not editable from the UI —
# these are written by the bot, never by humans.
USER_READONLY_FIELDS: tuple[str, ...] = (
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
)

# Tooltip / help-text mapping for free-text fields. The text is fed to the
# wtforms Field constructor as ``description`` (via form_args, NOT
# form_widget_args) so SQLAdmin's _macros.html renders it as
# ``<small class="text-muted">…</small>`` below the input.
USER_FORM_TOOLTIPS: dict[str, str] = {
    "phone_number": (
        "WhatsApp E.164 phone number, no plus. Required and unique. "
        "Acts as the natural key — avoid changing on existing users."
    ),
    "latitude": (
        "GPS decimal degrees. Venezuela bbox: lat 0.6 to 12.2, "
        "lng -73.4 to -59.8. Caracas ≈ 10.48, -66.86."
    ),
    "longitude": (
        "GPS decimal degrees. Venezuela bbox: lat 0.6 to 12.2, "
        "lng -73.4 to -59.8. Caracas ≈ 10.48, -66.86."
    ),
    "zone_name": (
        "Neighborhood name (e.g., 'La Boyera'). Set automatically by "
        "Nominatim reverse-geocode when the user shares a location pin."
    ),
    "awaiting_clarification_context": (
        "Set by the bot when waiting for the user to clarify an ambiguous "
        "drug query. Set to NULL (clear field) to unstick a stuck session."
    ),
    "awaiting_category_search": (
        "Category slug set by the bot when waiting for the user to choose "
        "a category from the menu. Set to NULL to clear stuck state."
    ),
}


def _coerce_optional_str(value: object) -> str | None:
    """WTForms coerce that maps empty submissions to NULL.

    SQLAdmin's SelectField submits the empty string when the "— use global —"
    or "— complete —" placeholder is selected; we want that to round-trip to
    a true Python None so it lands as NULL in the database.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class UserAdmin(ModelView, model=User):
    """Admin view for WhatsApp users."""

    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"

    column_list = [
        User.id,
        User.phone_number,
        User.name,
        User.zone_name,
        User.city_code,
        User.display_preference,
        User.response_mode,
        User.chat_debug,
        User.chat_admin,
        User.admin_mode_active,
        User.total_tokens_in,
        User.total_tokens_out,
        User.calls_haiku,
        User.calls_sonnet,
        User.calls_admin,
        User.onboarding_step,
        User.created_at,
    ]
    column_searchable_list = [User.phone_number, User.name, User.zone_name]
    column_sortable_list = [
        User.id,
        User.phone_number,
        User.name,
        User.city_code,
        User.chat_admin,
        User.total_tokens_in,
        User.total_tokens_out,
        User.calls_haiku,
        User.calls_sonnet,
        User.calls_admin,
        User.created_at,
    ]
    column_default_sort = ("created_at", True)

    column_labels = {
        "phone_number": "Phone Number",
        "name": "Name",
        "latitude": "Latitude",
        "longitude": "Longitude",
        "zone_name": "Zone / Neighborhood",
        "city_code": "City Code",
        "display_preference": "Display Preference",
        "response_mode": "Response Mode",
        "chat_debug": "Chat Debug",
        "chat_admin": "Chat Admin (UI-only)",
        "admin_mode_active": "Admin Mode Active",
        "total_tokens_in": "Tokens In (total)",
        "total_tokens_out": "Tokens Out (total)",
        "last_tokens_in": "Tokens In (last call)",
        "last_tokens_out": "Tokens Out (last call)",
        "tokens_in_haiku": "Haiku Tokens In",
        "tokens_out_haiku": "Haiku Tokens Out",
        "calls_haiku": "Haiku Calls",
        "tokens_in_sonnet": "Sonnet Tokens In",
        "tokens_out_sonnet": "Sonnet Tokens Out",
        "calls_sonnet": "Sonnet Calls",
        "calls_admin": "Admin Calls",
        "tokens_in_admin": "Admin Tokens In",
        "tokens_out_admin": "Admin Tokens Out",
        "onboarding_step": "Onboarding Step",
        "awaiting_clarification_context": "Awaiting Clarification Context",
        "awaiting_category_search": "Awaiting Category Search",
        "last_search_query": "Last Search Query",
        "last_search_log_id": "Last Search Log ID",
        "created_at": "Created",
        "updated_at": "Updated",
    }

    column_formatters = {
        User.id: lambda m, _: Markup(
            f'{m.id} <a href="/admin/user-stats/{m.id}" '
            f'title="View stats" style="text-decoration:none">📊</a>'
        ),
    }
    column_formatters_detail = {
        User.total_tokens_in: lambda m, _: Markup(
            f'{m.total_tokens_in:,} &nbsp; '
            f'<a href="/admin/user-stats/{m.id}" '
            f'style="color:#1a73e8;text-decoration:none;font-size:13px;">'
            f'View full stats &rarr;</a>'
        ),
    }

    form_include_pk = False
    column_details_exclude_list = []
    page_size = 25
    page_size_options = [10, 25, 50, 100]

    # ------------------------------------------------------------------
    # Edit-form configuration (Q3, v0.20.0)
    #
    # Goals (driven by https://github.com/.../q3 — admin Daniel reported
    # the form let him type garbage into status/enum columns and edit
    # counter columns that should be bot-only):
    #
    #   1. Render constrained fields as <select> dropdowns whose choices
    #      come from canonical sources in the codebase. The choices are
    #      defined as module-level constants above and verified by
    #      tests/test_admin_user_form.py to stay in sync.
    #   2. Render counter / log-pointer fields as readonly inputs so
    #      they're visible in context but cannot be edited by humans.
    #      (Approach (c) from the Q3 plan — keep them visible while
    #      editing other fields.)
    #   3. Add tooltip help text on free-text fields whose meaning isn't
    #      obvious (lat/lng range, awaiting_* state recovery, etc).
    #
    # NOTE on ``chat_admin``: this column is the security gate for admin
    # chat mode (see CLAUDE.md "Admin Chat Mode"). It MUST remain
    # editable from this UI (it is the only sanctioned way to grant
    # admin chat access) but it MUST NOT be added to the read-only set,
    # exposed via any chat-side tool, or otherwise weakened. SQLAdmin
    # auto-renders Boolean columns as checkboxes which is the current
    # behaviour — we do not override it here.
    # ------------------------------------------------------------------
    form_columns = [
        # Identity
        User.phone_number,
        User.name,
        # Location
        User.latitude,
        User.longitude,
        User.zone_name,
        User.city_code,
        # Display + bot behaviour
        User.display_preference,
        User.response_mode,
        User.chat_debug,
        # Onboarding / stuck-state recovery
        User.onboarding_step,
        User.awaiting_clarification_context,
        User.awaiting_category_search,
        # Admin gates (booleans → auto-rendered as checkboxes)
        User.chat_admin,
        User.admin_mode_active,
        # Read-only counters (kept in the form so admins see them in
        # context, but rendered with readonly=True so they cannot be
        # edited from the UI — they are written exclusively by the bot).
        User.total_tokens_in,
        User.total_tokens_out,
        User.last_tokens_in,
        User.last_tokens_out,
        User.tokens_in_haiku,
        User.tokens_out_haiku,
        User.calls_haiku,
        User.tokens_in_sonnet,
        User.tokens_out_sonnet,
        User.calls_sonnet,
        User.tokens_in_admin,
        User.tokens_out_admin,
        User.calls_admin,
        User.last_search_query,
        User.last_search_log_id,
    ]

    form_overrides = {
        "city_code": SelectField,
        "display_preference": SelectField,
        "response_mode": SelectField,
        "chat_debug": SelectField,
        "onboarding_step": SelectField,
    }

    # IMPORTANT — wtforms vs. HTML attribute split:
    #
    #   * ``form_args`` is passed to the wtforms Field *constructor*. The
    #     ``description`` kwarg here is what SQLAdmin's
    #     ``templates/sqladmin/_macros.html`` renders as the
    #     ``<small class="text-muted">…</small>`` help-text node below the
    #     input. This is the ONLY way to get visible tooltip text.
    #   * ``form_widget_args`` is passed to the wtforms widget as
    #     ``render_kw`` — every key/value becomes an HTML attribute on the
    #     rendered ``<input>``. So ``readonly: True`` belongs here (it
    #     becomes ``readonly`` on the element), but ``description: "…"``
    #     does NOT — it would render as a non-standard ``description``
    #     attribute that browsers ignore.
    form_args = {
        # --- SelectField dropdowns ---
        # city_code is nullable on the model — allow blank to clear it.
        "city_code": {
            "choices": [("", "— none —"), *USER_CITY_CODE_CHOICES],
            "coerce": _coerce_optional_str,
            "validate_choice": False,
        },
        # display_preference is NOT NULL with a default of "grid", so we
        # do not offer a blank option here.
        "display_preference": {
            "choices": USER_DISPLAY_PREFERENCE_CHOICES,
            "coerce": str,
            "validate_choice": False,
        },
        "response_mode": {
            "choices": USER_RESPONSE_MODE_CHOICES,
            "coerce": _coerce_optional_str,
            "validate_choice": False,
        },
        "chat_debug": {
            "choices": USER_CHAT_DEBUG_CHOICES,
            "coerce": _coerce_optional_str,
            "validate_choice": False,
        },
        "onboarding_step": {
            "choices": USER_ONBOARDING_STEP_CHOICES,
            "coerce": _coerce_optional_str,
            "validate_choice": False,
        },
        # --- Tooltip help text (rendered as <small class="text-muted">) ---
        **{
            field: {"description": tooltip}
            for field, tooltip in USER_FORM_TOOLTIPS.items()
        },
        # --- Read-only counters: also get an explanatory tooltip ---
        **{
            field: {
                "description": (
                    "Read-only — written by the bot. "
                    "View full breakdown via the 📊 link on the listing page."
                ),
            }
            for field in USER_READONLY_FIELDS
        },
    }

    form_widget_args = {
        # Read-only counter / log-pointer fields. ``readonly`` becomes the
        # HTML ``readonly`` attribute on the input (greyed out, not
        # editable). The browser still submits the value with the form,
        # but SQLAdmin will write it back unchanged.
        field: {"readonly": True}
        for field in USER_READONLY_FIELDS
    }


class IntentKeywordAdmin(ModelView, model=IntentKeyword):
    """Admin view for bot intent keyword mappings."""

    name = "Intent Keyword"
    name_plural = "Intent Keywords"
    icon = "fa-solid fa-robot"

    column_list = [
        IntentKeyword.id,
        IntentKeyword.action,
        IntentKeyword.keyword,
        IntentKeyword.response,
        IntentKeyword.is_active,
        IntentKeyword.updated_at,
    ]
    column_searchable_list = [IntentKeyword.action, IntentKeyword.keyword]
    column_sortable_list = [
        IntentKeyword.id,
        IntentKeyword.action,
        IntentKeyword.keyword,
        IntentKeyword.is_active,
    ]
    column_default_sort = "action"

    column_labels = {
        "action": "Intent Action",
        "keyword": "Keyword / Phrase",
        "response": "Canned Response",
        "is_active": "Active",
        "created_at": "Created",
        "updated_at": "Updated",
    }

    form_include_pk = False
    page_size = 25
    page_size_options = [10, 25, 50, 100]


class PharmacyLocationAdmin(ModelView, model=PharmacyLocation):
    """Admin view for physical pharmacy locations."""

    name = "Pharmacy Location"
    name_plural = "Pharmacy Locations"
    icon = "fa-solid fa-map-location-dot"

    column_list = [
        PharmacyLocation.id,
        PharmacyLocation.pharmacy_chain,
        PharmacyLocation.name,
        PharmacyLocation.city_code,
        PharmacyLocation.zone_name,
        PharmacyLocation.address,
        PharmacyLocation.opening_hours,
        PharmacyLocation.is_24h,
        PharmacyLocation.phone,
        PharmacyLocation.website,
        PharmacyLocation.is_active,
    ]
    column_searchable_list = [
        PharmacyLocation.name,
        PharmacyLocation.pharmacy_chain,
        PharmacyLocation.city_code,
        PharmacyLocation.zone_name,
        PharmacyLocation.address,
    ]
    column_sortable_list = [
        PharmacyLocation.id,
        PharmacyLocation.pharmacy_chain,
        PharmacyLocation.name,
        PharmacyLocation.city_code,
        PharmacyLocation.is_active,
    ]
    column_default_sort = "pharmacy_chain"

    column_labels = {
        "external_id": "External ID",
        "pharmacy_chain": "Pharmacy Chain",
        "name": "Store Name",
        "name_lower": "Name (lowercase)",
        "city_code": "City Code",
        "address": "Address",
        "latitude": "Latitude",
        "longitude": "Longitude",
        "phone": "Phone",
        "is_active": "Active",
        "created_at": "Created",
        "updated_at": "Updated",
    }

    form_include_pk = False
    page_size = 25
    page_size_options = [10, 25, 50, 100]


class ProductAdmin(ModelView, model=Product):
    """Admin view for the permanent product catalog."""

    name = "Product"
    name_plural = "Products"
    icon = "fa-solid fa-pills"

    column_list = [
        Product.id,
        Product.pharmacy_chain,
        Product.drug_name,
        Product.brand,
        Product.requires_prescription,
        Product.unit_count,
        Product.created_at,
        Product.updated_at,
    ]
    column_searchable_list = [Product.drug_name, Product.brand, Product.external_id]
    column_sortable_list = [
        Product.id,
        Product.pharmacy_chain,
        Product.drug_name,
        Product.brand,
        Product.updated_at,
    ]
    column_default_sort = ("updated_at", True)

    column_labels = {
        "external_id": "External ID",
        "pharmacy_chain": "Pharmacy Chain",
        "drug_name": "Drug Name",
        "brand": "Brand",
        "description": "Description",
        "image_url": "Image URL",
        "drug_class": "Drug Class",
        "requires_prescription": "Rx Required",
        "unit_count": "Unit Count",
        "unit_label": "Unit Label",
        "product_url": "Product URL",
        "created_at": "Created",
        "updated_at": "Updated",
    }

    can_create = False
    can_edit = True
    can_delete = False
    form_include_pk = False
    page_size = 25
    page_size_options = [10, 25, 50, 100]


class ProductPriceAdmin(ModelView, model=ProductPrice):
    """Admin view for per-location product pricing."""

    name = "Product Price"
    name_plural = "Product Prices"
    icon = "fa-solid fa-tags"

    column_list = [
        ProductPrice.id,
        ProductPrice.product_id,
        ProductPrice.city_code,
        ProductPrice.full_price_bs,
        ProductPrice.offer_price_bs,
        ProductPrice.discount_pct,
        ProductPrice.in_stock,
        ProductPrice.stores_in_stock_count,
        ProductPrice.refreshed_at,
    ]
    column_searchable_list = [ProductPrice.city_code]
    column_sortable_list = [
        ProductPrice.id,
        ProductPrice.product_id,
        ProductPrice.city_code,
        ProductPrice.full_price_bs,
        ProductPrice.in_stock,
        ProductPrice.refreshed_at,
    ]
    column_default_sort = ("refreshed_at", True)

    column_labels = {
        "product_id": "Product ID",
        "city_code": "City Code",
        "full_price_bs": "Full Price (Bs)",
        "offer_price_bs": "Offer Price (Bs)",
        "discount_pct": "Discount",
        "in_stock": "In Stock",
        "stores_in_stock_count": "Stores In Stock",
        "stores_with_stock_ids": "Store IDs (JSON)",
        "refreshed_at": "Last Refreshed",
    }

    can_create = False
    can_edit = False
    can_delete = False
    form_include_pk = False
    page_size = 25
    page_size_options = [10, 25, 50, 100]


class SearchQueryAdmin(ModelView, model=SearchQuery):
    """Admin view for search query cache mappings."""

    name = "Search Query"
    name_plural = "Search Queries"
    icon = "fa-solid fa-magnifying-glass"

    column_list = [
        SearchQuery.id,
        SearchQuery.query,
        SearchQuery.city_code,
        SearchQuery.total_results,
        SearchQuery.searched_at,
    ]
    column_searchable_list = [SearchQuery.query, SearchQuery.city_code]
    column_sortable_list = [
        SearchQuery.id,
        SearchQuery.query,
        SearchQuery.total_results,
        SearchQuery.searched_at,
    ]
    column_default_sort = ("searched_at", True)

    column_labels = {
        "query": "Search Query",
        "city_code": "City Code",
        "product_ids": "Product IDs (JSON)",
        "total_results": "Total Results",
        "searched_at": "Searched At",
    }

    can_create = False
    can_edit = False
    can_delete = True
    form_include_pk = False
    page_size = 25
    page_size_options = [10, 25, 50, 100]


class AppSettingAdmin(ModelView, model=AppSetting):
    """Admin view for application settings."""

    name = "App Setting"
    name_plural = "App Settings"
    icon = "fa-solid fa-gear"

    column_list = [
        AppSetting.id,
        AppSetting.key,
        AppSetting.value,
        AppSetting.description,
        AppSetting.updated_at,
    ]
    column_searchable_list = [AppSetting.key, AppSetting.description]
    column_sortable_list = [AppSetting.id, AppSetting.key, AppSetting.updated_at]
    column_default_sort = "key"

    column_labels = {
        "key": "Setting Key",
        "value": "Value",
        "description": "Description",
        "updated_at": "Last Updated",
    }

    form_include_pk = False
    page_size = 25
    page_size_options = [10, 25, 50, 100]


class ConversationLogAdmin(ModelView, model=ConversationLog):
    """Admin view for WhatsApp conversation logs (read-only)."""

    name = "Conversation Log"
    name_plural = "Conversation Logs"
    icon = "fa-solid fa-comments"

    column_list = [
        ConversationLog.id,
        ConversationLog.phone_number,
        ConversationLog.direction,
        ConversationLog.message_type,
        ConversationLog.message_text,
        ConversationLog.created_at,
    ]
    column_searchable_list = [
        ConversationLog.phone_number,
        ConversationLog.message_text,
    ]
    column_sortable_list = [
        ConversationLog.id,
        ConversationLog.phone_number,
        ConversationLog.direction,
        ConversationLog.message_type,
        ConversationLog.created_at,
    ]
    column_default_sort = ("created_at", True)

    column_labels = {
        "phone_number": "Phone Number",
        "direction": "Direction",
        "message_text": "Message",
        "message_type": "Type",
        "wa_message_id": "WhatsApp Message ID",
        "created_at": "Timestamp",
    }

    can_create = False
    can_edit = False
    can_delete = False
    form_include_pk = False
    page_size = 50
    page_size_options = [25, 50, 100, 200]


class SearchLogAdmin(ModelView, model=SearchLog):
    """Admin view for search analytics (read-only)."""

    name = "Search Log"
    name_plural = "Search Logs"
    icon = "fa-solid fa-magnifying-glass-chart"

    column_list = [
        SearchLog.id,
        SearchLog.user_id,
        SearchLog.query,
        SearchLog.results_count,
        SearchLog.feedback,
        SearchLog.source,
        SearchLog.searched_at,
    ]
    column_searchable_list = [SearchLog.query, SearchLog.source]
    column_sortable_list = [
        SearchLog.id,
        SearchLog.query,
        SearchLog.results_count,
        SearchLog.source,
        SearchLog.searched_at,
    ]
    column_default_sort = ("searched_at", True)

    column_labels = {
        "user_id": "User ID",
        "query": "Search Query",
        "results_count": "Results Found",
        "feedback": "Feedback",
        "feedback_detail": "Feedback Detail",
        "source": "Source",
        "searched_at": "Searched At",
    }

    can_create = False
    can_edit = False
    can_delete = False
    form_include_pk = False
    page_size = 50
    page_size_options = [25, 50, 100, 200]


class PharmacyAdmin(ModelView, model=Pharmacy):
    """Admin view for pharmacy chains."""

    name = "Pharmacy Chain"
    name_plural = "Pharmacy Chains"
    icon = "fa-solid fa-prescription-bottle-medical"

    column_list = [
        Pharmacy.id,
        Pharmacy.name,
        Pharmacy.website_url,
        Pharmacy.is_active,
        Pharmacy.created_at,
    ]
    column_searchable_list = [Pharmacy.name, Pharmacy.website_url]
    column_sortable_list = [Pharmacy.id, Pharmacy.name, Pharmacy.is_active]
    column_default_sort = "name"

    column_labels = {
        "name": "Pharmacy Name",
        "website_url": "Website URL",
        "search_url_template": "Search URL Template",
        "is_active": "Active",
        "created_at": "Created",
        "updated_at": "Updated",
    }

    form_include_pk = False
    page_size = 25
    page_size_options = [10, 25, 50]


class DrugListingAdmin(ModelView, model=DrugListing):
    """Admin view for drug listings."""

    name = "Drug Listing"
    name_plural = "Drug Listings"
    icon = "fa-solid fa-pills"

    column_list = [
        DrugListing.id,
        DrugListing.pharmacy_id,
        DrugListing.drug_name,
        DrugListing.price_usd,
        DrugListing.price_bs,
        DrugListing.available,
        DrugListing.scraped_at,
    ]
    column_searchable_list = [DrugListing.drug_name, DrugListing.drug_name_normalized]
    column_sortable_list = [
        DrugListing.id,
        DrugListing.drug_name,
        DrugListing.price_usd,
        DrugListing.available,
        DrugListing.scraped_at,
    ]
    column_default_sort = ("scraped_at", True)

    column_labels = {
        "pharmacy_id": "Pharmacy ID",
        "drug_name": "Drug Name",
        "drug_name_normalized": "Normalized Name",
        "price_usd": "Price (USD)",
        "price_bs": "Price (Bs)",
        "available": "Available",
        "product_url": "Product URL",
        "scraped_at": "Scraped At",
    }

    can_create = False
    can_edit = False
    can_delete = True
    form_include_pk = False
    page_size = 25
    page_size_options = [10, 25, 50, 100]


class AiRoleAdmin(ModelView, model=AiRole):
    """Admin view for AI roles (personas with system prompts)."""

    name = "AI Role"
    name_plural = "AI Roles"
    icon = "fa-solid fa-brain"

    column_list = [
        AiRole.id,
        AiRole.name,
        AiRole.display_name,
        AiRole.is_active,
        AiRole.locked_by_admin,
        AiRole.updated_at,
    ]
    column_searchable_list = [AiRole.name, AiRole.display_name]
    column_sortable_list = [AiRole.id, AiRole.name, AiRole.is_active, AiRole.locked_by_admin]
    column_default_sort = "name"

    column_labels = {
        "name": "Slug",
        "display_name": "Display Name",
        "description": "Description (for router)",
        "system_prompt": "System Prompt",
        "is_active": "Active",
        "locked_by_admin": "Locked (skip seed sync)",
        "created_at": "Created",
        "updated_at": "Updated",
    }

    form_widget_args = {
        "system_prompt": {"rows": 20},
        "description": {"rows": 3},
    }

    form_include_pk = False
    page_size = 25


class AiRoleRuleAdmin(ModelView, model=AiRoleRule):
    """Admin view for AI role rules (behavioral guidelines)."""

    name = "AI Rule"
    name_plural = "AI Rules"
    icon = "fa-solid fa-scale-balanced"

    column_list = [
        AiRoleRule.id,
        AiRoleRule.role_id,
        AiRoleRule.name,
        AiRoleRule.sort_order,
        AiRoleRule.is_active,
        AiRoleRule.updated_at,
    ]
    column_searchable_list = [AiRoleRule.name]
    column_sortable_list = [
        AiRoleRule.id,
        AiRoleRule.role_id,
        AiRoleRule.name,
        AiRoleRule.sort_order,
        AiRoleRule.is_active,
    ]
    column_default_sort = [("role_id", False), ("sort_order", False)]

    column_labels = {
        "role_id": "Role ID",
        "name": "Rule Name",
        "description": "Description",
        "content": "Rule Content",
        "sort_order": "Sort Order",
        "is_active": "Active",
        "created_at": "Created",
        "updated_at": "Updated",
    }

    form_widget_args = {
        "content": {"rows": 15},
        "description": {"rows": 2},
    }

    form_include_pk = False
    page_size = 25


class AiRoleSkillAdmin(ModelView, model=AiRoleSkill):
    """Admin view for AI role skills (capabilities)."""

    name = "AI Skill"
    name_plural = "AI Skills"
    icon = "fa-solid fa-wand-magic-sparkles"

    column_list = [
        AiRoleSkill.id,
        AiRoleSkill.role_id,
        AiRoleSkill.name,
        AiRoleSkill.is_active,
        AiRoleSkill.updated_at,
    ]
    column_searchable_list = [AiRoleSkill.name]
    column_sortable_list = [
        AiRoleSkill.id,
        AiRoleSkill.role_id,
        AiRoleSkill.name,
        AiRoleSkill.is_active,
    ]
    column_default_sort = [("role_id", False), ("name", False)]

    column_labels = {
        "role_id": "Role ID",
        "name": "Skill Name",
        "description": "Description",
        "content": "Skill Definition",
        "is_active": "Active",
        "created_at": "Created",
        "updated_at": "Updated",
    }

    form_widget_args = {
        "content": {"rows": 15},
        "description": {"rows": 2},
    }

    form_include_pk = False
    page_size = 25


class UserFeedbackAdmin(ModelView, model=UserFeedback):
    """Admin view for /bug and /comentario submissions."""

    name = "User Feedback"
    name_plural = "User Feedback"
    icon = "fa-solid fa-comment-dots"

    column_list = [
        UserFeedback.id,
        UserFeedback.user_id,
        UserFeedback.feedback_type,
        UserFeedback.message,
        UserFeedback.conversation_log_id,
        UserFeedback.reviewed,
        UserFeedback.created_at,
    ]
    column_searchable_list = [UserFeedback.message, UserFeedback.feedback_type]
    column_sortable_list = [
        UserFeedback.id,
        UserFeedback.user_id,
        UserFeedback.feedback_type,
        UserFeedback.reviewed,
        UserFeedback.created_at,
    ]
    column_default_sort = ("created_at", True)

    column_labels = {
        "user_id": "User ID",
        "feedback_type": "Type",
        "message": "Message",
        "conversation_log_id": "Conversation Log ID",
        "reviewed": "Reviewed",
        "reviewer_notes": "Reviewer Notes",
        "reviewed_at": "Reviewed At",
        "created_at": "Submitted At",
    }

    form_widget_args = {
        "message": {"rows": 6, "readonly": True},
        "reviewer_notes": {"rows": 6},
    }
    form_excluded_columns = [
        UserFeedback.user,
        UserFeedback.user_id,
        UserFeedback.conversation_log_id,
        UserFeedback.feedback_type,
        UserFeedback.message,
        UserFeedback.created_at,
    ]

    can_create = False
    can_edit = True
    can_delete = True
    form_include_pk = False
    page_size = 50
    page_size_options = [25, 50, 100, 200]


class UserMemoryAdmin(ModelView, model=UserMemory):
    """Admin view for per-user AI memory."""

    name = "User Memory"
    name_plural = "User Memories"
    icon = "fa-solid fa-book-open"

    column_list = [
        UserMemory.id,
        UserMemory.user_id,
        UserMemory.updated_by,
        UserMemory.updated_at,
    ]
    column_searchable_list = [UserMemory.memory_text]
    column_sortable_list = [
        UserMemory.id,
        UserMemory.user_id,
        UserMemory.updated_by,
        UserMemory.updated_at,
    ]
    column_default_sort = ("updated_at", True)

    column_labels = {
        "user_id": "User ID",
        "memory_text": "Memory (Markdown)",
        "updated_by": "Updated By",
        "created_at": "Created",
        "updated_at": "Updated",
    }

    form_widget_args = {
        "memory_text": {"rows": 20},
    }

    form_include_pk = False
    page_size = 25


# All admin views to register — order determines sidebar order
class ScheduledTaskAdmin(ModelView, model=ScheduledTask):
    """Admin view for background scheduled tasks."""

    name = "Scheduled Task"
    name_plural = "Scheduled Tasks"
    icon = "fa-solid fa-clock"

    column_list = [
        ScheduledTask.id,
        ScheduledTask.name,
        ScheduledTask.task_key,
        ScheduledTask.interval_minutes,
        ScheduledTask.enabled,
        ScheduledTask.status,
        ScheduledTask.last_run_at,
        ScheduledTask.next_run_at,
        ScheduledTask.last_duration_seconds,
        ScheduledTask.last_result,
    ]
    column_sortable_list = [
        ScheduledTask.name,
        ScheduledTask.enabled,
        ScheduledTask.status,
        ScheduledTask.last_run_at,
        ScheduledTask.next_run_at,
    ]
    column_searchable_list = [ScheduledTask.name, ScheduledTask.task_key]
    column_labels = {
        ScheduledTask.interval_minutes: "Interval (min)",
        ScheduledTask.last_duration_seconds: "Duration (s)",
        ScheduledTask.task_key: "Task Function",
    }
    form_include_pk = False
    form_columns = [
        ScheduledTask.name,
        ScheduledTask.task_key,
        ScheduledTask.interval_minutes,
        ScheduledTask.enabled,
    ]


class GeocodeCacheAdmin(ModelView, model=GeocodeCache):
    """v0.19.0 — geocode cache rows for diagnostics."""

    name = "Geocode Cache"
    name_plural = "Geocode Cache"
    icon = "fa-solid fa-map-pin"

    column_list = [
        GeocodeCache.id,
        GeocodeCache.source,
        GeocodeCache.query_text,
        GeocodeCache.zone_name,
        GeocodeCache.city_code,
        GeocodeCache.confidence,
        GeocodeCache.latitude,
        GeocodeCache.longitude,
        GeocodeCache.fetched_at,
    ]
    column_sortable_list = [
        GeocodeCache.fetched_at,
        GeocodeCache.confidence,
        GeocodeCache.source,
    ]
    column_searchable_list = [
        GeocodeCache.query_text,
        GeocodeCache.zone_name,
        GeocodeCache.display_name,
    ]
    column_default_sort = ("fetched_at", True)
    can_create = False
    can_edit = False  # rows are managed by services.location only
    page_size = 50


ADMIN_VIEWS = [
    UserAdmin,
    UserFeedbackAdmin,
    ConversationLogAdmin,
    SearchLogAdmin,
    IntentKeywordAdmin,
    AiRoleAdmin,
    AiRoleRuleAdmin,
    AiRoleSkillAdmin,
    UserMemoryAdmin,
    PharmacyLocationAdmin,
    PharmacyAdmin,
    DrugListingAdmin,
    ProductAdmin,
    ProductPriceAdmin,
    SearchQueryAdmin,
    AppSettingAdmin,
    ScheduledTaskAdmin,
    GeocodeCacheAdmin,
]


class AdminAuth(AuthenticationBackend):
    """Simple username/password authentication for the admin dashboard."""

    async def login(self, request: Request) -> bool:
        """Validate login credentials from the login form.

        Args:
            request: The incoming HTTP request with form data.

        Returns:
            True if credentials are valid, False otherwise.
        """
        form = await request.form()
        username = form.get("username", "")
        password = form.get("password", "")

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            request.session.update({"authenticated": True})
            return True
        return False

    async def logout(self, request: Request) -> bool:
        """Clear the session on logout.

        Args:
            request: The incoming HTTP request.

        Returns:
            Always True.
        """
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        """Check if the current session is authenticated.

        Args:
            request: The incoming HTTP request.

        Returns:
            True if authenticated, False otherwise.
        """
        return request.session.get("authenticated", False)


def setup_admin(app, engine) -> Admin:
    """Create and configure the SQLAdmin instance on the given FastAPI app.

    Parameters
    ----------
    app : FastAPI
        The FastAPI application instance.
    engine : AsyncEngine | Engine
        The SQLAlchemy engine (async or sync).

    Returns
    -------
    Admin
        The configured SQLAdmin instance.
    """
    authentication_backend = AdminAuth(secret_key=ADMIN_SECRET_KEY)
    admin = Admin(
        app,
        engine,
        title="FarmaFacil Admin",
        base_url="/admin",
        authentication_backend=authentication_backend,
    )
    for view in ADMIN_VIEWS:
        admin.add_view(view)
    return admin
