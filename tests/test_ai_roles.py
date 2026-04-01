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
from farmafacil.services.ai_router import _build_roles_list, route_to_role
from farmafacil.services.user_memory import get_memory, update_memory


# ── AI Roles Service ────────────────────────────────────────────────────


class TestAiRolesService:
    """Test cached loading and assembly of AI roles."""

    @pytest.mark.asyncio
    async def test_seed_creates_default_roles(self):
        """Seeding creates pharmacy_advisor and app_support roles."""
        count = await seed_ai_roles()
        assert count == 2

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
        assert len(role.skills) >= 2  # drug_search, symptom_translation

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
        assert "## Client Context" in result
        assert "User takes losartan daily." in result

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

    def test_build_roles_list(self):
        """Formats role list for the router prompt."""
        roles = [
            RoleConfig("pharmacy_advisor", "Asesor", "Busca medicamentos", "", [], []),
            RoleConfig("app_support", "Soporte", "Ayuda técnica", "", [], []),
        ]
        result = _build_roles_list(roles)
        assert "pharmacy_advisor: Busca medicamentos" in result
        assert "app_support: Ayuda técnica" in result

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
    async def test_no_api_key_returns_default(self):
        """Without API key, returns default role."""
        with patch("farmafacil.services.ai_router.list_active_roles") as mock_roles, \
             patch("farmafacil.services.ai_router.ANTHROPIC_API_KEY", ""):
            mock_roles.return_value = [
                RoleConfig("r1", "R1", "Role 1", "", [], []),
                RoleConfig("r2", "R2", "Role 2", "", [], []),
            ]
            result = await route_to_role("test")
            assert result == "pharmacy_advisor"


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
