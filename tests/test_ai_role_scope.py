"""Tests for AI role scope — verify pharmacy_advisor covers all pharmacy products.

Ensures the system prompt, skills, and rules in seed.py are broad enough
to handle skincare, vitamins, personal care, etc. — not just medications.
"""

import pytest

from farmafacil.db.seed import DEFAULT_ROLES, _PHARMACY_ADVISOR_PROMPT


class TestPharmacyAdvisorPrompt:
    """Verify the pharmacy_advisor system prompt covers all product types."""

    def test_prompt_mentions_productos(self):
        """Prompt should reference 'productos' not just 'medicamentos'."""
        assert "productos" in _PHARMACY_ADVISOR_PROMPT.lower()

    def test_prompt_mentions_skincare(self):
        """Prompt should mention skincare/belleza category."""
        prompt_lower = _PHARMACY_ADVISOR_PROMPT.lower()
        assert "belleza" in prompt_lower or "skincare" in prompt_lower

    def test_prompt_mentions_vitamins(self):
        """Prompt should mention vitamins/supplements."""
        prompt_lower = _PHARMACY_ADVISOR_PROMPT.lower()
        assert "vitamina" in prompt_lower or "suplemento" in prompt_lower

    def test_prompt_mentions_personal_care(self):
        """Prompt should mention personal care products."""
        prompt_lower = _PHARMACY_ADVISOR_PROMPT.lower()
        assert "cuidado personal" in prompt_lower

    def test_prompt_mentions_baby_products(self):
        """Prompt should mention baby products."""
        prompt_lower = _PHARMACY_ADVISOR_PROMPT.lower()
        assert "bebé" in prompt_lower or "bebe" in prompt_lower

    def test_prompt_has_search_instruction(self):
        """Prompt should instruct AI to always search for pharmacy products."""
        prompt_lower = _PHARMACY_ADVISOR_PROMPT.lower()
        assert "siempre" in prompt_lower and "busca" in prompt_lower

    def test_prompt_has_refuse_only_non_pharmacy(self):
        """Prompt should say to refuse only non-pharmacy items."""
        prompt_lower = _PHARMACY_ADVISOR_PROMPT.lower()
        assert "electrónico" in prompt_lower or "ropa" in prompt_lower


class TestPharmacyAdvisorRoleConfig:
    """Verify the DEFAULT_ROLES seed data structure."""

    @pytest.fixture
    def pharmacy_role(self):
        """Get the pharmacy_advisor role from seed data."""
        for role in DEFAULT_ROLES:
            if role["name"] == "pharmacy_advisor":
                return role
        pytest.fail("pharmacy_advisor role not found in DEFAULT_ROLES")

    def test_role_description_mentions_products(self, pharmacy_role):
        """Role description should mention broad product categories."""
        desc = pharmacy_role["description"].lower()
        assert "productos" in desc or "cuidado personal" in desc

    def test_drug_search_skill_mentions_products(self, pharmacy_role):
        """drug_search skill should cover all pharmacy products."""
        drug_skill = None
        for skill in pharmacy_role["skills"]:
            if skill["name"] == "drug_search":
                drug_skill = skill
                break
        assert drug_skill is not None, "drug_search skill not found"
        content_lower = drug_skill["content"].lower()
        assert "skincare" in content_lower or "belleza" in content_lower
        assert "vitamina" in content_lower or "suplemento" in content_lower

    def test_product_scope_rule_exists(self, pharmacy_role):
        """A product_scope rule should exist."""
        rule_names = [r["name"] for r in pharmacy_role["rules"]]
        assert "product_scope" in rule_names

    def test_product_scope_rule_content(self, pharmacy_role):
        """product_scope rule should instruct to always search pharmacy products."""
        scope_rule = None
        for rule in pharmacy_role["rules"]:
            if rule["name"] == "product_scope":
                scope_rule = rule
                break
        assert scope_rule is not None
        content_lower = scope_rule["content"].lower()
        assert "siempre" in content_lower
        assert "drug_search" in content_lower


class TestFallbackPrompt:
    """Verify the hardcoded fallback prompt is also broad."""

    def test_fallback_mentions_products(self):
        from farmafacil.services.ai_responder import _FALLBACK_PROMPT

        assert "productos" in _FALLBACK_PROMPT.lower()

    def test_fallback_mentions_categories(self):
        from farmafacil.services.ai_responder import _FALLBACK_PROMPT

        prompt_lower = _FALLBACK_PROMPT.lower()
        assert "belleza" in prompt_lower or "cuidado personal" in prompt_lower


class TestHelpMessage:
    """Verify the help message references broad product scope."""

    def test_help_mentions_products(self):
        from farmafacil.services.intent import HELP_MESSAGE

        assert "productos" in HELP_MESSAGE.lower()

    def test_help_has_non_drug_examples(self):
        from farmafacil.services.intent import HELP_MESSAGE

        msg_lower = HELP_MESSAGE.lower()
        assert "protector solar" in msg_lower or "vitamina" in msg_lower


class TestUserFacingMessages:
    """Verify user-facing messages use 'producto de farmacia' not just 'medicamento'."""

    def test_welcome_message(self):
        from farmafacil.bot.handler import MSG_WELCOME

        msg_lower = MSG_WELCOME.lower()
        assert "producto" in msg_lower
        assert "medicamento" not in msg_lower

    def test_ready_message(self):
        from farmafacil.bot.handler import MSG_READY

        assert "producto" in MSG_READY.lower()

    def test_returning_message(self):
        from farmafacil.bot.handler import MSG_RETURNING

        assert "producto" in MSG_RETURNING.lower()
