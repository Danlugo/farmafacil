"""SQLAlchemy ORM models for the FarmaFacil database."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


class User(Base):
    """A FarmaFacil user identified by their WhatsApp phone number."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_number: Mapped[str] = mapped_column(
        String(20), nullable=False, unique=True, index=True,
        comment="WhatsApp phone number with country code",
    )
    name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="User display name",
    )
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="Neighborhood or zone name (e.g., El Cafetal)",
    )
    city_code: Mapped[str | None] = mapped_column(
        String(10), nullable=True, comment="Farmatodo city code (e.g., CCS, MCBO)",
    )
    display_preference: Mapped[str] = mapped_column(
        String(20), default="grid",
        comment="How to show results: grid or detail",
    )
    onboarding_step: Mapped[str | None] = mapped_column(
        String(30), nullable=True, default="awaiting_name",
        comment="Current onboarding step (NULL = complete)",
    )
    response_mode: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default=None,
        comment="Override response mode: NULL=use global, hybrid, ai_only",
    )
    chat_debug: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default=None,
        comment="Override chat debug: NULL=use global, enabled, disabled",
    )
    last_search_query: Mapped[str | None] = mapped_column(
        String(300), nullable=True,
        comment="Last drug search query for 'ver similares' feature",
    )
    last_search_log_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
        comment="ID of the most recent search_logs entry (for feedback)",
    )
    awaiting_clarification_context: Mapped[str | None] = mapped_column(
        String(300), nullable=True,
        comment="Stored original vague query while bot waits for a clarification answer",
    )
    awaiting_category_search: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
        comment="Category the user picked from the greeting menu while bot waits for a product name (Item 29, v0.13.2)",
    )
    chat_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False,
        comment="Gate for admin chat role — editable ONLY from SQLAdmin (v0.14.0, Item 35)",
    )
    admin_mode_active: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False,
        comment="Runtime flag — True while a chat_admin user has activated admin mode via /admin",
    )
    total_tokens_in: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0",
        comment="Cumulative input tokens across all LLM calls",
    )
    total_tokens_out: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0",
        comment="Cumulative output tokens across all LLM calls",
    )
    last_tokens_in: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0",
        comment="Input tokens from the most recent LLM call",
    )
    last_tokens_out: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0",
        comment="Output tokens from the most recent LLM call",
    )
    # Per-model token tracking — Haiku
    tokens_in_haiku: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0",
        comment="Cumulative input tokens for Claude Haiku calls",
    )
    tokens_out_haiku: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0",
        comment="Cumulative output tokens for Claude Haiku calls",
    )
    calls_haiku: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0",
        comment="Total number of Claude Haiku API calls",
    )
    # Per-model token tracking — Sonnet
    tokens_in_sonnet: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0",
        comment="Cumulative input tokens for Claude Sonnet calls",
    )
    tokens_out_sonnet: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0",
        comment="Cumulative output tokens for Claude Sonnet calls",
    )
    calls_sonnet: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0",
        comment="Total number of Claude Sonnet API calls",
    )
    # Per-model token tracking — Admin (Opus-priced; tracks ALL admin chat
    # turns separately from user-facing token usage so admin ops don't
    # contaminate user cost metrics). v0.14.0, Item 35.
    tokens_in_admin: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0",
        comment="Cumulative input tokens spent in admin chat turns",
    )
    tokens_out_admin: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0",
        comment="Cumulative output tokens spent in admin chat turns",
    )
    calls_admin: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0",
        comment="Total number of admin chat LLM calls",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        """Return user name and phone for admin UI display."""
        label = self.name or self.phone_number
        return f"{label} ({self.phone_number})" if self.name else self.phone_number


class Pharmacy(Base):
    """Pharmacy chain that we scrape."""

    __tablename__ = "pharmacies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    website_url: Mapped[str] = mapped_column(String(500), nullable=False)
    search_url_template: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="URL template with {query} placeholder",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class DrugListing(Base):
    """A drug found on a pharmacy website."""

    __tablename__ = "drug_listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    drug_name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    drug_name_normalized: Mapped[str] = mapped_column(
        String(300), nullable=False, index=True,
        comment="Lowercase, stripped, for fuzzy matching",
    )
    price_usd: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_bs: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    product_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ConversationLog(Base):
    """Log of every WhatsApp message in and out for troubleshooting."""

    __tablename__ = "conversation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_number: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True,
        comment="WhatsApp phone number",
    )
    direction: Mapped[str] = mapped_column(
        String(10), nullable=False,
        comment="inbound (user→bot) or outbound (bot→user)",
    )
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(
        String(20), default="text",
        comment="text, location, image, etc.",
    )
    wa_message_id: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="WhatsApp message ID for dedup",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class IntentKeyword(Base):
    """Keyword-to-intent mapping, editable via admin."""

    __tablename__ = "intent_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True,
        comment="Intent action: greeting, help, location_change, preference_change, name_change, farewell",
    )
    keyword: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True, index=True,
        comment="Lowercase keyword or phrase to match",
    )
    response: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Optional canned response for this keyword",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class PharmacyLocation(Base):
    """A physical pharmacy location — generic across all pharmacy chains."""

    __tablename__ = "pharmacy_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(
        String(150), nullable=False, index=True,
        comment="ID from the source system (e.g., Farmatodo store ID, VTEX pickup point ID)",
    )
    pharmacy_chain: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
        comment="Which chain: Farmatodo, Locatel, XANA, etc.",
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name_lower: Mapped[str] = mapped_column(String(100), nullable=False, index=True,
        comment="Lowercase for case-insensitive lookup",
    )
    city_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # v0.18.0 Item 45 — Nominatim-derived neighborhood (e.g., "Las Mercedes")
    zone_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # v0.18.0 Item 46 — OSM tags
    opening_hours: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
        comment="Raw OSM opening_hours format, e.g., 'Mo-Fr 08:00-20:00'",
    )
    is_24h: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
        comment="Derived: True if opening_hours == '24/7'",
    )
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Product(Base):
    """Permanent product catalog — never deleted, only updated."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True,
        comment="Algolia objectID or equivalent, for deduplication",
    )
    pharmacy_chain: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
        comment="Which chain: Farmatodo, Armirene, etc.",
    )
    drug_name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    brand: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    drug_class: Mapped[str | None] = mapped_column(String(100), nullable=True)
    requires_prescription: Mapped[bool] = mapped_column(Boolean, default=False)
    unit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_label: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="Per-unit label (e.g., 'Capsulas')",
    )
    product_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    keywords: Mapped[list | None] = mapped_column(
        JSON, nullable=True,
        comment="Lowercase tokens from drug_name split by whitespace, for cross-chain matching",
    )
    is_pharmaceutical: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, default=None,
        comment="True if drug_class is pharma, False if non-pharma, NULL if unknown (Item 38, v0.15.0)",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    prices: Mapped[list["ProductPrice"]] = relationship(
        "ProductPrice", back_populates="product", lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("external_id", "pharmacy_chain", name="uq_product_external"),
    )


class ProductKeyword(Base):
    """Inverted index of product keywords for fast cross-chain keyword matching.

    Added in v0.12.6 (Item 30). ``Product.keywords`` stores a denormalized
    JSON list for backwards compatibility; this table mirrors those tokens as
    one row per (product_id, keyword) so ``find_cross_chain_matches`` can do
    a single indexed ``WHERE keyword IN (...) GROUP BY product_id HAVING
    COUNT(DISTINCT keyword) = N`` query instead of loading every product with
    keywords into memory and filtering in Python.

    Both columns are indexed; ``keyword`` is the most important index for
    the lookup path. The table is fully derivable from ``Product.keywords``
    and is backfilled idempotently on startup.
    """

    __tablename__ = "product_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    keyword: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
        comment="Lowercase token from Product.drug_name",
    )

    __table_args__ = (
        UniqueConstraint("product_id", "keyword", name="uq_product_keyword"),
    )


class ProductPrice(Base):
    """Per-location pricing for a product — updated on each search refresh."""

    __tablename__ = "product_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    city_code: Mapped[str] = mapped_column(
        String(10), nullable=False, index=True,
        comment="Farmatodo city code (e.g., CCS, MCBO)",
    )
    full_price_bs: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    offer_price_bs: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    discount_pct: Mapped[str | None] = mapped_column(
        String(20), nullable=True, comment="Discount text (e.g., '20%')",
    )
    in_stock: Mapped[bool] = mapped_column(Boolean, default=False)
    stores_in_stock_count: Mapped[int] = mapped_column(Integer, default=0)
    stores_with_stock_ids: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="JSON list of store IDs with stock",
    )
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
        comment="When this price was last updated from the API",
    )

    product: Mapped["Product"] = relationship("Product", back_populates="prices")

    __table_args__ = (
        UniqueConstraint("product_id", "city_code", name="uq_price_product_city"),
    )


class SearchQuery(Base):
    """Maps search queries to product results for cache lookups."""

    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True,
        comment="Normalized search term (lowercase, stripped)",
    )
    city_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    product_ids: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="Ordered list of product IDs in result order",
    )
    total_results: Mapped[int] = mapped_column(Integer, default=0)
    searched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("query", "city_code", name="uq_search_query_city"),
    )


class AppSetting(Base):
    """Admin-editable application settings."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AiRole(Base):
    """An AI persona with a system prompt, used for LLM-powered responses.

    Each role defines a distinct AI personality (e.g., pharmacy advisor,
    app support) with its own system prompt, rules, and skills.
    Analogous to a CLAUDE.md project file.
    """

    __tablename__ = "ai_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True, index=True,
        comment="Slug identifier (e.g., pharmacy_advisor, app_support)",
    )
    display_name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Human-readable name",
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Short description for the role router to select this role",
    )
    system_prompt: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Base system prompt for this AI persona",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    locked_by_admin: Mapped[bool] = mapped_column(
        Boolean, default=False,
        comment="If True, the startup seed updater will NOT overwrite this role's "
                "prompt/rules/skills. Set via SQLAdmin when you hand-edit a role.",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    rules: Mapped[list["AiRoleRule"]] = relationship(
        "AiRoleRule", back_populates="role", lazy="selectin",
        order_by="AiRoleRule.sort_order",
    )
    skills: Mapped[list["AiRoleSkill"]] = relationship(
        "AiRoleSkill", back_populates="role", lazy="selectin",
        order_by="AiRoleSkill.name",
    )

    def __repr__(self) -> str:
        return self.display_name or self.name


class AiRoleRule(Base):
    """A behavioral rule attached to an AI role.

    Analogous to a rules/*.md file in Claude's system.
    Rules are injected into the prompt after the system prompt.
    """

    __tablename__ = "ai_role_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_roles.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Rule name (e.g., no_dosage_advice)",
    )
    description: Mapped[str | None] = mapped_column(
        String(300), nullable=True, comment="Short description of this rule",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Full rule text injected into prompt",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, comment="Order in which rules appear in prompt",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    role: Mapped["AiRole"] = relationship("AiRole", back_populates="rules")

    def __repr__(self) -> str:
        return self.name


class AiRoleSkill(Base):
    """A skill/capability attached to an AI role.

    Describes what the AI can do in this role (e.g., drug search,
    store lookup, price comparison). Injected into the prompt.
    """

    __tablename__ = "ai_role_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_roles.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Skill name (e.g., drug_search)",
    )
    description: Mapped[str | None] = mapped_column(
        String(300), nullable=True, comment="Short description of this skill",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Skill definition/instructions injected into prompt",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    role: Mapped["AiRole"] = relationship("AiRole", back_populates="skills")

    def __repr__(self) -> str:
        return self.name


class UserMemory(Base):
    """Per-user AI memory — stores conversation context across sessions.

    Analogous to a CLAUDE.md project file per client. Auto-updated by
    the AI after conversations, also editable by admins.
    """

    __tablename__ = "user_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    memory_text: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
        comment="Markdown-formatted memory (preferences, history, medical context)",
    )
    updated_by: Mapped[str] = mapped_column(
        String(20), default="ai",
        comment="Who last updated: ai or admin",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship("User", backref="memory", uselist=False)


class SearchLog(Base):
    """Log of user searches for analytics."""

    __tablename__ = "search_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    query: Mapped[str] = mapped_column(String(200), nullable=False)
    results_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(
        String(50), default="whatsapp", comment="api, whatsapp, web",
    )
    feedback: Mapped[str | None] = mapped_column(
        String(10), nullable=True,
        comment="User feedback: yes, no, or NULL (no response)",
    )
    feedback_detail: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="User explanation when feedback is negative",
    )
    searched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserFeedback(Base):
    """User-submitted feedback via /bug or /comentario commands.

    Stores a case record that can be reviewed by the team. Linked to the
    originating conversation log row so reviewers can read the surrounding
    context of the conversation.
    """

    __tablename__ = "user_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    feedback_type: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True,
        comment="bug or comentario",
    )
    message: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="The user's feedback text (body of the command)",
    )
    conversation_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("conversation_logs.id", ondelete="SET NULL"),
        nullable=True, index=True,
        comment="Link to the inbound message that triggered this feedback",
    )
    reviewed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0",
        comment="Whether a team member has reviewed this feedback",
    )
    reviewer_notes: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Notes from the reviewer",
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="When the feedback was reviewed",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return f"#{self.id} [{self.feedback_type}]"


class ScheduledTask(Base):
    """A recurring background maintenance task managed via the admin UI.

    Task functions are registered in ``services/scheduler.py:TASK_REGISTRY``.
    Admins can enable/disable, change the interval, and trigger manual runs
    from SQLAdmin.  The scheduler loop reads ``next_run_at`` from this table
    so timing survives container restarts.
    """

    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True,
        comment="Human-readable task name",
    )
    task_key: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="Key into TASK_REGISTRY — maps to the Python function",
    )
    interval_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60,
        comment="How often to run (minutes)",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1",
        comment="Toggle to pause/resume this task",
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
        comment="When this task last ran",
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
        comment="When this task is due to run next",
    )
    status: Mapped[str] = mapped_column(
        String(20), default="idle", server_default="idle",
        comment="idle, running, success, failed",
    )
    last_result: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Output or error from the last run",
    )
    last_duration_seconds: Mapped[float | None] = mapped_column(
        Float, nullable=True,
        comment="How long the last run took",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        status = "enabled" if self.enabled else "paused"
        return f"{self.name} ({status})"


class GeocodeCache(Base):
    """v0.19.0 — Cache Nominatim forward and reverse geocode results.

    Key insight: pharmacies do not move; user-typed zone names do not
    change either. Caching trims our Nominatim free-tier usage from
    ~1500 req per OSM cycle to a few dozen, and onboarding latency from
    ~1s to 0ms on repeat zones (e.g., everyone in Caracas typing
    \"La Boyera\").
    """

    __tablename__ = "geocode_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
        comment="sha256 of normalized query + source — see services.location",
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="forward (text\u2192coords) or reverse (coords\u2192zone)",
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(
        Float, nullable=True,
        comment="Nominatim importance score 0\u20131 \u2014 used for low-confidence rejection",
    )
    city_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    zone_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True,
        comment="Used by cache TTL and the geocode_cache_cleanup task",
    )

    def __repr__(self) -> str:
        return f"GeocodeCache({self.source}: {self.query_text} \u2192 {self.latitude}, {self.longitude})"
