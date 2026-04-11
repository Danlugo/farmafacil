"""Tests for the AI roles system — roles, rules, skills, router, memory, responder."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from farmafacil.db.seed import seed_ai_roles
from farmafacil.services.ai_roles import (
    RoleConfig,
    assemble_prompt,
    get_role,
    list_active_roles,
)
from farmafacil.services.ai_router import route_to_role
from farmafacil.services.user_memory import get_memory, update_memory


# ── AI Roles Service ────────────────────────────────────────────────────


class TestAiRolesService:
    """Test cached loading and assembly of AI roles."""

    @pytest.mark.asyncio
    async def test_seed_creates_default_roles(self):
        """Seeding creates pharmacy_advisor, app_admin, and app_support roles."""
        # Earlier tests (e.g. test_admin_chat) may have already seeded roles.
        # Wipe the tables first so this test is self-contained.
        from sqlalchemy import delete

        from farmafacil.db.session import async_session
        from farmafacil.models.database import AiRole, AiRoleRule, AiRoleSkill

        async with async_session() as session:
            await session.execute(delete(AiRoleRule))
            await session.execute(delete(AiRoleSkill))
            await session.execute(delete(AiRole))
            await session.commit()
        count = await seed_ai_roles()
        assert count == 3

    @pytest.mark.asyncio
    async def test_seed_is_idempotent(self):
        """Seeding twice doesn't create duplicates."""
        await seed_ai_roles()
        count = await seed_ai_roles()
        assert count == 0

    @pytest.mark.asyncio
    async def test_get_role_returns_config(self):
        """get_role returns a RoleConfig for a seeded role."""
        await seed_ai_roles()
        role = await get_role("pharmacy_advisor")
        assert role is not None
        assert role.name == "pharmacy_advisor"
        assert role.display_name == "Asesor de Farmacia"
        assert "FarmaFacil" in role.system_prompt

    @pytest.mark.asyncio
    async def test_get_role_includes_rules(self):
        """get_role loads active rules for the role."""
        await seed_ai_roles()
        role = await get_role("pharmacy_advisor")
        assert role is not None
        assert len(role.rules) >= 3  # no_dosage, no_diagnosis, venezuelan_spanish, prescription

    @pytest.mark.asyncio
    async def test_get_role_includes_skills(self):
        """get_role loads active skills for the role."""
        await seed_ai_roles()
        role = await get_role("pharmacy_advisor")
        assert role is not None
        assert len(role.skills) >= 10  # drug_search, nearest_store, symptom_translation + 8 new

    @pytest.mark.asyncio
    async def test_pharmacy_advisor_has_all_skills(self):
        """pharmacy_advisor role has all 11 expected skills in prompt."""
        await seed_ai_roles()
        role = await get_role("pharmacy_advisor")
        assert role is not None
        prompt = assemble_prompt(role)
        # Verify key skill content is present
        assert "genérico" in prompt.lower()  # generic_alternatives
        assert "compara precios" in prompt.lower() or "comparar" in prompt.lower()  # price_comparison
        assert "se me está acabando" in prompt.lower() or "se le está acabando" in prompt.lower()  # reorder_reminder
        assert "protector solar" in prompt.lower()  # product_guidance
        assert "horario" in prompt.lower()  # store_hours_info
        assert "múltiples productos" in prompt.lower() or "primer producto" in prompt.lower()  # multi_product_search
        assert "receta" in prompt.lower()  # prescription_guidance
        assert "emergencia" in prompt.lower()  # emergency_redirect

    @pytest.mark.asyncio
    async def test_get_role_nonexistent(self):
        """get_role returns None for unknown role."""
        await seed_ai_roles()
        role = await get_role("nonexistent_role")
        assert role is None

    @pytest.mark.asyncio
    async def test_list_active_roles(self):
        """list_active_roles returns all active roles."""
        await seed_ai_roles()
        roles = await list_active_roles()
        names = {r.name for r in roles}
        assert "pharmacy_advisor" in names
        assert "app_support" in names

    @pytest.mark.asyncio
    async def test_get_app_support_role(self):
        """app_support role exists with its own rules."""
        await seed_ai_roles()
        role = await get_role("app_support")
        assert role is not None
        assert role.name == "app_support"
        assert len(role.rules) >= 2  # patient_explanations, escalation


class TestAssemblePrompt:
    """Test prompt assembly from role + rules + skills + memory."""

    def test_basic_assembly(self):
        """Assembles system prompt with rules and skills."""
        role = RoleConfig(
            name="test",
            display_name="Test",
            description="Test role",
            system_prompt="You are a test bot.",
            rules=["Rule 1: Be nice.", "Rule 2: Be concise."],
            skills=["Skill: Search drugs."],
        )
        result = assemble_prompt(role)
        assert "You are a test bot." in result
        assert "## Rules" in result
        assert "Rule 1: Be nice." in result
        assert "Rule 2: Be concise." in result
        assert "## Skills" in result
        assert "Skill: Search drugs." in result

    def test_assembly_with_memory(self):
        """Includes client memory when provided."""
        role = RoleConfig(
            name="test",
            display_name="Test",
            description="Test role",
            system_prompt="Base prompt.",
            rules=[],
            skills=[],
        )
        result = assemble_prompt(role, "User takes losartan daily.")
        assert "## Client Memory" in result
        assert "User takes losartan daily." in result

    def test_assembly_with_profile(self):
        """Includes user profile when provided, before memory."""
        role = RoleConfig(
            name="test",
            display_name="Test",
            description="Test role",
            system_prompt="Base prompt.",
            rules=[],
            skills=[],
        )
        profile = {"name": "Maria", "zone": "Chacao", "city_code": "CCS", "preference": "grid"}
        result = assemble_prompt(role, "Old memory note", profile)
        assert "## User Profile" in result
        assert "Maria" in result
        assert "Chacao" in result
        assert "galería" in result
        # Profile appears before memory
        profile_pos = result.index("## User Profile")
        memory_pos = result.index("## Client Memory")
        assert profile_pos < memory_pos

    def test_assembly_profile_overrides_label(self):
        """Profile section is labeled as authoritative."""
        role = RoleConfig(
            name="test",
            display_name="Test",
            description="Test role",
            system_prompt="Base prompt.",
            rules=[],
            skills=[],
        )
        profile = {"name": "Jose", "zone": "El Cafetal"}
        result = assemble_prompt(role, None, profile)
        assert "authoritative" in result
        assert "Jose" in result

    def test_assembly_no_rules_no_skills(self):
        """No Rules/Skills sections when empty."""
        role = RoleConfig(
            name="test",
            display_name="Test",
            description="Test role",
            system_prompt="Base prompt.",
            rules=[],
            skills=[],
        )
        result = assemble_prompt(role)
        assert "## Rules" not in result
        assert "## Skills" not in result

    def test_assembly_empty_memory_ignored(self):
        """Empty/whitespace memory is not included."""
        role = RoleConfig(
            name="test",
            display_name="Test",
            description="Test role",
            system_prompt="Base prompt.",
            rules=[],
            skills=[],
        )
        result = assemble_prompt(role, "   ")
        assert "## Client Context" not in result


# ── AI Router ───────────────────────────────────────────────────────────


class TestAiRouter:
    """Test role routing logic."""

    @pytest.mark.asyncio
    async def test_single_role_no_routing(self):
        """With a single active role, routing is skipped."""
        with patch("farmafacil.services.ai_router.list_active_roles") as mock:
            mock.return_value = [
                RoleConfig("only_role", "Only", "Only role", "", [], [])
            ]
            result = await route_to_role("any message")
            assert result == "only_role"

    @pytest.mark.asyncio
    async def test_no_roles_returns_default(self):
        """With no active roles, returns default."""
        with patch("farmafacil.services.ai_router.list_active_roles") as mock:
            mock.return_value = []
            result = await route_to_role("any message")
            assert result == "pharmacy_advisor"

    @pytest.mark.asyncio
    async def test_app_support_keyword_routing(self):
        """Messages with app support keywords route to app_support."""
        with patch("farmafacil.services.ai_router.list_active_roles") as mock:
            mock.return_value = [
                RoleConfig("pharmacy_advisor", "Asesor", "Busca medicamentos", "", [], []),
                RoleConfig("app_support", "Soporte", "Ayuda técnica", "", [], []),
            ]
            result = await route_to_role("la app no funciona")
            assert result == "app_support"

    @pytest.mark.asyncio
    async def test_drug_query_routes_to_pharmacy(self):
        """Regular drug queries route to pharmacy_advisor."""
        with patch("farmafacil.services.ai_router.list_active_roles") as mock:
            mock.return_value = [
                RoleConfig("pharmacy_advisor", "Asesor", "Busca medicamentos", "", [], []),
                RoleConfig("app_support", "Soporte", "Ayuda técnica", "", [], []),
            ]
            result = await route_to_role("busco losartan")
            assert result == "pharmacy_advisor"

    @pytest.mark.asyncio
    async def test_error_keyword_routes_to_support(self):
        """Messages mentioning errors route to app_support."""
        with patch("farmafacil.services.ai_router.list_active_roles") as mock:
            mock.return_value = [
                RoleConfig("pharmacy_advisor", "Asesor", "Busca medicamentos", "", [], []),
                RoleConfig("app_support", "Soporte", "Ayuda técnica", "", [], []),
            ]
            result = await route_to_role("tiene un error cuando busco")
            assert result == "app_support"


# ── AI Responder Parsing ───────────────────────────────────────────────


class TestParseStructuredResponse:
    """Test parsing of structured LLM responses including new actions."""

    def test_parse_emergency_action(self):
        """Emergency action is parsed correctly."""
        from farmafacil.services.ai_responder import _parse_structured_response

        reply = (
            "ACTION: emergency\n"
            "RESPONSE: 🚨 Esto suena como una emergencia. Llama al 911."
        )
        result = _parse_structured_response(reply)
        assert result.action == "emergency"
        assert "911" in result.text

    def test_parse_view_similar_action(self):
        """view_similar action is parsed correctly."""
        from farmafacil.services.ai_responder import _parse_structured_response

        reply = "ACTION: view_similar"
        result = _parse_structured_response(reply)
        assert result.action == "view_similar"

    def test_parse_drug_search_with_response(self):
        """drug_search with RESPONSE is parsed correctly (symptom + search)."""
        from farmafacil.services.ai_responder import _parse_structured_response

        reply = (
            "ACTION: drug_search\n"
            "DRUG: Acetaminofen\n"
            "RESPONSE: Entiendo que tienes dolor de cabeza."
        )
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.drug_query == "Acetaminofen"
        assert "dolor de cabeza" in result.text

    def test_parse_invalid_action_with_response_becomes_question(self):
        """Unknown action with RESPONSE falls back to question."""
        from farmafacil.services.ai_responder import _parse_structured_response

        reply = (
            "ACTION: something_invalid\n"
            "RESPONSE: Aquí tienes la info."
        )
        result = _parse_structured_response(reply)
        assert result.action == "question"

    def test_parse_invalid_action_without_response_becomes_unknown(self):
        """Unknown action without RESPONSE falls back to unknown."""
        from farmafacil.services.ai_responder import _parse_structured_response

        reply = "ACTION: something_invalid"
        result = _parse_structured_response(reply)
        assert result.action == "unknown"


# ── User Memory ─────────────────────────────────────────────────────────


class TestUserMemory:
    """Test per-user memory CRUD."""

    @pytest.mark.asyncio
    async def test_get_memory_empty(self):
        """Returns empty string for user without memory."""
        result = await get_memory(99999)
        assert result == ""

    @pytest.mark.asyncio
    async def test_update_and_get_memory(self):
        """Can create and retrieve user memory."""
        await update_memory(1, "Takes losartan daily.", "ai")
        result = await get_memory(1)
        assert "losartan" in result

    @pytest.mark.asyncio
    async def test_update_existing_memory(self):
        """Can update an existing memory."""
        await update_memory(2, "Initial memory.", "ai")
        await update_memory(2, "Updated memory with new info.", "admin")
        result = await get_memory(2)
        assert "Updated memory" in result
        assert "Initial" not in result

    @pytest.mark.asyncio
    async def test_memory_truncation(self):
        """Memory is truncated to MAX_MEMORY_LENGTH."""
        long_text = "x" * 5000
        await update_memory(3, long_text, "ai")
        result = await get_memory(3)
        assert len(result) <= 3000
