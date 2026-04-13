"""SQLAdmin dashboard for FarmaFacil.

Provides a full admin UI at /admin for managing all database tables.
Authentication required via username/password.
"""

from markupsafe import Markup
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from farmafacil.config import ADMIN_PASSWORD, ADMIN_SECRET_KEY, ADMIN_USERNAME
from farmafacil.models.database import (
    AiRole,
    AiRoleRule,
    AiRoleSkill,
    AppSetting,
    ConversationLog,
    DrugListing,
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
        "total_tokens_in": "Tokens In",
        "total_tokens_out": "Tokens Out",
        "calls_haiku": "Haiku Calls",
        "calls_sonnet": "Sonnet Calls",
        "calls_admin": "Admin Calls",
        "tokens_in_admin": "Admin Tokens In",
        "tokens_out_admin": "Admin Tokens Out",
        "onboarding_step": "Onboarding Step",
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
        PharmacyLocation.address,
        PharmacyLocation.is_active,
    ]
    column_searchable_list = [
        PharmacyLocation.name,
        PharmacyLocation.pharmacy_chain,
        PharmacyLocation.city_code,
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
