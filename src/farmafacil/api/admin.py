"""SQLAdmin dashboard for FarmaFacil.

Provides a full admin UI at /admin for managing all database tables.
Authentication required via username/password.
"""

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from farmafacil.config import ADMIN_PASSWORD, ADMIN_SECRET_KEY, ADMIN_USERNAME
from farmafacil.models.database import (
    AppSetting,
    ConversationLog,
    DrugListing,
    IntentKeyword,
    Pharmacy,
    PharmacyLocation,
    ProductCache,
    SearchLog,
    User,
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
        User.onboarding_step,
        User.created_at,
    ]
    column_searchable_list = [User.phone_number, User.name, User.zone_name]
    column_sortable_list = [
        User.id,
        User.phone_number,
        User.name,
        User.city_code,
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
        "onboarding_step": "Onboarding Step",
        "created_at": "Created",
        "updated_at": "Updated",
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


class ProductCacheAdmin(ModelView, model=ProductCache):
    """Admin view for cached product search results."""

    name = "Product Cache"
    name_plural = "Product Cache"
    icon = "fa-solid fa-database"

    column_list = [
        ProductCache.id,
        ProductCache.query,
        ProductCache.city_code,
        ProductCache.result_count,
        ProductCache.cached_at,
    ]
    column_searchable_list = [ProductCache.query, ProductCache.city_code]
    column_sortable_list = [
        ProductCache.id,
        ProductCache.query,
        ProductCache.result_count,
        ProductCache.cached_at,
    ]
    column_default_sort = ("cached_at", True)

    column_labels = {
        "query": "Search Query",
        "city_code": "City Code",
        "results_json": "Results (JSON)",
        "result_count": "Result Count",
        "cached_at": "Cached At",
    }

    # Allow delete (for clearing cache) but show the JSON as read-only in detail
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


# All admin views to register — order determines sidebar order
ADMIN_VIEWS = [
    UserAdmin,
    ConversationLogAdmin,
    SearchLogAdmin,
    IntentKeywordAdmin,
    PharmacyLocationAdmin,
    PharmacyAdmin,
    DrugListingAdmin,
    ProductCacheAdmin,
    AppSettingAdmin,
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
