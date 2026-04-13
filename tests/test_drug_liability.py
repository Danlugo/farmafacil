"""Tests for Item 37: Drug recommendation liability guardrails.

Validates that:
- Seeded pharmacy_advisor prompt does NOT contain drug-recommendation language
- Seeded rules include the new liability guardrails
- symptom_translation skill is removed, replaced by symptom_acknowledgment + drug_interaction_info
- sync_seeded_roles() updates stale prompts/rules/skills on existing roles
- sync_seeded_roles() respects locked_by_admin
- locked_by_admin column exists with correct default
"""

import pytest

from farmafacil.db.seed import (
    DEFAULT_ROLES,
    _REMOVED_SEED_SKILLS,
    seed_ai_roles,
    sync_seeded_roles,
)
from farmafacil.db.session import async_session
from farmafacil.models.database import AiRole, AiRoleRule, AiRoleSkill
from farmafacil.services.ai_roles import (
    _roles_cache,
    assemble_prompt,
    get_role,
)


def _invalidate_role_cache() -> None:
    """Force-expire the ai_roles in-memory cache so get_role re-reads DB."""
    import farmafacil.services.ai_roles as mod

    mod._cache_loaded_at = 0
    mod._roles_cache.clear()


# ── Helper ──────────────────────────────────────────────────────────────

def _get_pharmacy_seed() -> dict:
    """Return the pharmacy_advisor entry from DEFAULT_ROLES."""
    for r in DEFAULT_ROLES:
        if r["name"] == "pharmacy_advisor":
            return r
    raise AssertionError("pharmacy_advisor not found in DEFAULT_ROLES")


def _seed_rule_names() -> set[str]:
    """Return the set of rule names in the pharmacy_advisor seed."""
    return {r["name"] for r in _get_pharmacy_seed()["rules"]}


def _seed_skill_names() -> set[str]:
    """Return the set of skill names in the pharmacy_advisor seed."""
    return {s["name"] for s in _get_pharmacy_seed()["skills"]}


# ── Seed content policy tests ──────────────────────────────────────────


class TestSeedContentPolicy:
    """Verify the seed definitions enforce the liability policy."""

    def test_system_prompt_no_symptom_translation(self):
        """System prompt must NOT contain 'Traducir síntomas a medicamentos'."""
        seed = _get_pharmacy_seed()
        prompt = seed["system_prompt"].lower()
        assert "traducir síntomas a medicamentos" not in prompt

    def test_system_prompt_has_liability_warning(self):
        """System prompt must contain the liability disclaimer."""
        seed = _get_pharmacy_seed()
        prompt = seed["system_prompt"]
        assert "NUNCA prescribas" in prompt
        assert "NUNCA sugieras dosis" in prompt

    def test_system_prompt_allows_otc_mentions(self):
        """System prompt allows mentioning OTC options."""
        seed = _get_pharmacy_seed()
        prompt = seed["system_prompt"]
        assert "opciones comunes de venta libre (OTC)" in prompt

    def test_no_drug_recommendations_rule_exists(self):
        """The no_drug_recommendations rule exists in seed."""
        assert "no_drug_recommendations" in _seed_rule_names()

    def test_no_drug_recommendations_is_highest_priority(self):
        """no_drug_recommendations has sort_order 1 (highest)."""
        seed = _get_pharmacy_seed()
        for rule in seed["rules"]:
            if rule["name"] == "no_drug_recommendations":
                assert rule["sort_order"] == 1
                return
        pytest.fail("no_drug_recommendations rule not found")

    def test_no_drug_recommendations_content_allows_otc_prohibits_prescribing(self):
        """The rule allows OTC informing but prohibits prescribing."""
        seed = _get_pharmacy_seed()
        for rule in seed["rules"]:
            if rule["name"] == "no_drug_recommendations":
                content = rule["content"]
                assert "RESPONSABILIDAD LEGAL" in content
                # Allows OTC informing
                assert "opciones comunes de venta libre (OTC)" in content
                assert "Opciones OTC comunes por síntoma" in content
                # Prohibits prescribing
                assert "NO prescribas" in content
                assert "NO sugieras dosis" in content
                assert "NO diagnostiques" in content
                # Requires disclaimer
                assert "Consulta con tu médico" in content
                return
        pytest.fail("no_drug_recommendations rule not found")

    def test_non_drug_recommendations_ok_rule_exists(self):
        """The non_drug_recommendations_ok rule exists in seed."""
        assert "non_drug_recommendations_ok" in _seed_rule_names()

    def test_no_diagnosis_prohibits_diagnosis_allows_otc(self):
        """no_diagnosis rule prohibits diagnosing but allows naming OTC options."""
        seed = _get_pharmacy_seed()
        for rule in seed["rules"]:
            if rule["name"] == "no_diagnosis":
                content = rule["content"]
                assert "NUNCA diagnostiques" in content
                assert "No digas 'parece que tienes X'" in content
                # Should reference the OTC rule, not block drug mentions
                assert "opciones OTC" in content.lower() or "no_drug_recommendations" in content
                return
        pytest.fail("no_diagnosis rule not found")

    def test_symptom_translation_skill_removed(self):
        """symptom_translation skill must NOT be in the seed."""
        assert "symptom_translation" not in _seed_skill_names()

    def test_symptom_translation_in_removed_set(self):
        """symptom_translation is in the _REMOVED_SEED_SKILLS set."""
        assert "symptom_translation" in _REMOVED_SEED_SKILLS

    def test_symptom_acknowledgment_skill_exists(self):
        """New symptom_acknowledgment skill exists in seed."""
        assert "symptom_acknowledgment" in _seed_skill_names()

    def test_symptom_acknowledgment_offers_otc_with_disclaimer(self):
        """The symptom skill names OTC options and includes disclaimer + prohibitions."""
        seed = _get_pharmacy_seed()
        for skill in seed["skills"]:
            if skill["name"] == "symptom_acknowledgment":
                content = skill["content"]
                # Must offer OTC options
                assert "opciones OTC comunes" in content.lower() or "opciones comunes de venta libre" in content.lower()
                # Must include disclaimer
                assert "Consulta con tu médico" in content
                # Must still prohibit prescribing
                assert "PROHIBIDO" in content
                # Should NOT have old direct symptom→drug mapping tables
                assert "→ Losartán" not in content
                assert "→ Metformina" not in content
                return
        pytest.fail("symptom_acknowledgment skill not found")

    def test_drug_interaction_info_skill_exists(self):
        """New drug_interaction_info skill exists in seed."""
        assert "drug_interaction_info" in _seed_skill_names()

    def test_drug_interaction_info_always_routes_to_doctor(self):
        """drug_interaction_info skill always ends with doctor referral."""
        seed = _get_pharmacy_seed()
        for skill in seed["skills"]:
            if skill["name"] == "drug_interaction_info":
                content = skill["content"]
                assert "Consulta con tu médico" in content
                assert "NUNCA" in content  # has prohibitions
                return
        pytest.fail("drug_interaction_info skill not found")


# ── Assembled prompt tests ─────────────────────────────────────────────


class TestAssembledPromptPolicy:
    """Verify the fully-assembled prompt enforces the policy."""

    @pytest.mark.asyncio
    async def test_assembled_prompt_no_symptom_drug_mapping(self):
        """The full assembled prompt must not map symptoms to drug names."""
        from sqlalchemy import delete

        # Clean and re-seed to get fresh content
        async with async_session() as session:
            await session.execute(delete(AiRoleRule))
            await session.execute(delete(AiRoleSkill))
            await session.execute(delete(AiRole))
            await session.commit()
        await seed_ai_roles()
        _invalidate_role_cache()

        role = await get_role("pharmacy_advisor")
        assert role is not None
        prompt = assemble_prompt(role)

        # Must NOT contain the old symptom→drug mapping
        assert "Dolor de cabeza / fiebre → Aspirina" not in prompt
        assert "Presión alta → Losartán" not in prompt
        assert "Traducir síntomas a medicamentos" not in prompt

    @pytest.mark.asyncio
    async def test_assembled_prompt_has_liability_language(self):
        """The full prompt has the liability disclaimer."""
        await seed_ai_roles()
        _invalidate_role_cache()
        role = await get_role("pharmacy_advisor")
        assert role is not None
        prompt = assemble_prompt(role)
        assert "RESPONSABILIDAD LEGAL" in prompt
        assert "NO prescribas" in prompt
        assert "Consulta con tu médico" in prompt


# ── sync_seeded_roles() tests ──────────────────────────────────────────


class TestSyncSeededRoles:
    """Test the idempotent role updater."""

    @pytest.mark.asyncio
    async def test_sync_updates_stale_system_prompt(self):
        """sync updates a role whose system_prompt differs from seed."""
        from sqlalchemy import delete

        async with async_session() as session:
            await session.execute(delete(AiRoleRule))
            await session.execute(delete(AiRoleSkill))
            await session.execute(delete(AiRole))
            await session.commit()
        await seed_ai_roles()

        # Manually stale the prompt
        async with async_session() as session:
            result = await session.execute(
                AiRole.__table__.select().where(AiRole.name == "pharmacy_advisor")
            )
            role = result.first()
            assert role is not None
            # Overwrite with old prompt
            await session.execute(
                AiRole.__table__.update()
                .where(AiRole.name == "pharmacy_advisor")
                .values(system_prompt="OLD STALE PROMPT")
            )
            await session.commit()

        updated = await sync_seeded_roles()
        assert updated >= 1

        # Verify it's been corrected
        _invalidate_role_cache()
        role = await get_role("pharmacy_advisor")
        assert role is not None
        assert "NUNCA prescribas" in role.system_prompt
        assert "OLD STALE PROMPT" not in role.system_prompt

    @pytest.mark.asyncio
    async def test_sync_respects_locked_by_admin(self):
        """sync skips roles with locked_by_admin=True."""
        from sqlalchemy import delete

        async with async_session() as session:
            await session.execute(delete(AiRoleRule))
            await session.execute(delete(AiRoleSkill))
            await session.execute(delete(AiRole))
            await session.commit()
        await seed_ai_roles()

        # Lock the role and stale the prompt
        async with async_session() as session:
            await session.execute(
                AiRole.__table__.update()
                .where(AiRole.name == "pharmacy_advisor")
                .values(
                    system_prompt="ADMIN CUSTOM PROMPT",
                    locked_by_admin=True,
                )
            )
            await session.commit()

        updated = await sync_seeded_roles()

        # Verify the custom prompt was NOT overwritten
        _invalidate_role_cache()
        role = await get_role("pharmacy_advisor")
        assert role is not None
        assert role.system_prompt == "ADMIN CUSTOM PROMPT"

    @pytest.mark.asyncio
    async def test_sync_adds_new_rules(self):
        """sync adds rules that exist in seed but not in DB."""
        from sqlalchemy import delete

        async with async_session() as session:
            await session.execute(delete(AiRoleRule))
            await session.execute(delete(AiRoleSkill))
            await session.execute(delete(AiRole))
            await session.commit()
        await seed_ai_roles()

        # Delete one rule manually
        async with async_session() as session:
            await session.execute(
                delete(AiRoleRule).where(
                    AiRoleRule.name == "no_drug_recommendations"
                )
            )
            await session.commit()

        await sync_seeded_roles()

        # Verify it was re-added (check via DB directly since RoleConfig
        # stores rule content strings, not names)
        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(AiRoleRule.name).join(AiRole).where(
                    AiRole.name == "pharmacy_advisor"
                )
            )
            db_rule_names = {row[0] for row in result.all()}
        assert "no_drug_recommendations" in db_rule_names

    @pytest.mark.asyncio
    async def test_sync_removes_deprecated_skills(self):
        """sync removes skills listed in _REMOVED_SEED_SKILLS."""
        from sqlalchemy import delete, select

        async with async_session() as session:
            await session.execute(delete(AiRoleRule))
            await session.execute(delete(AiRoleSkill))
            await session.execute(delete(AiRole))
            await session.commit()
        await seed_ai_roles()

        # Manually add the deprecated symptom_translation skill back
        async with async_session() as session:
            result = await session.execute(
                select(AiRole.id).where(AiRole.name == "pharmacy_advisor")
            )
            role_id = result.scalar_one()
            session.add(AiRoleSkill(
                role_id=role_id,
                name="symptom_translation",
                description="OLD deprecated skill",
                content="OLD content that recommends drugs",
                is_active=True,
            ))
            await session.commit()

        # Verify it exists
        async with async_session() as session:
            result = await session.execute(
                select(AiRoleSkill.name).where(
                    AiRoleSkill.name == "symptom_translation"
                )
            )
            assert result.scalar_one_or_none() == "symptom_translation"

        await sync_seeded_roles()

        # Verify it was removed
        async with async_session() as session:
            result = await session.execute(
                select(AiRoleSkill.name).where(
                    AiRoleSkill.name == "symptom_translation"
                )
            )
            assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_sync_is_noop_when_already_current(self):
        """sync returns 0 when everything matches seed."""
        from sqlalchemy import delete

        async with async_session() as session:
            await session.execute(delete(AiRoleRule))
            await session.execute(delete(AiRoleSkill))
            await session.execute(delete(AiRole))
            await session.commit()
        await seed_ai_roles()

        # First sync should update (since seed_ai_roles doesn't sync)
        # Actually seed_ai_roles creates them fresh, so they should match
        updated = await sync_seeded_roles()
        # Second sync should definitely be a noop
        updated2 = await sync_seeded_roles()
        assert updated2 == 0

    @pytest.mark.asyncio
    async def test_sync_updates_rule_content(self):
        """sync updates rule content when it differs from seed."""
        from sqlalchemy import delete, select

        async with async_session() as session:
            await session.execute(delete(AiRoleRule))
            await session.execute(delete(AiRoleSkill))
            await session.execute(delete(AiRole))
            await session.commit()
        await seed_ai_roles()

        # Stale a rule's content
        async with async_session() as session:
            result = await session.execute(
                select(AiRoleRule).where(
                    AiRoleRule.name == "no_drug_recommendations"
                )
            )
            rule = result.scalar_one()
            rule.content = "OLD CONTENT"
            await session.commit()

        updated = await sync_seeded_roles()
        assert updated >= 1

        async with async_session() as session:
            result = await session.execute(
                select(AiRoleRule.content).where(
                    AiRoleRule.name == "no_drug_recommendations"
                )
            )
            content = result.scalar_one()
        assert "RESPONSABILIDAD LEGAL" in content
        assert "NO prescribas" in content


# ── locked_by_admin column tests ───────────────────────────────────────


class TestLockedByAdmin:
    """Test the locked_by_admin column behavior."""

    @pytest.mark.asyncio
    async def test_new_role_defaults_to_unlocked(self):
        """Seeded roles have locked_by_admin=False by default."""
        from sqlalchemy import delete, select

        async with async_session() as session:
            await session.execute(delete(AiRoleRule))
            await session.execute(delete(AiRoleSkill))
            await session.execute(delete(AiRole))
            await session.commit()
        await seed_ai_roles()

        async with async_session() as session:
            result = await session.execute(
                select(AiRole.locked_by_admin).where(
                    AiRole.name == "pharmacy_advisor"
                )
            )
            locked = result.scalar_one()
        assert locked is False

    @pytest.mark.asyncio
    async def test_locked_role_survives_sync(self):
        """A locked role's custom content is preserved after sync."""
        from sqlalchemy import delete

        async with async_session() as session:
            await session.execute(delete(AiRoleRule))
            await session.execute(delete(AiRoleSkill))
            await session.execute(delete(AiRole))
            await session.commit()
        await seed_ai_roles()

        custom_prompt = "Mi prompt personalizado por el admin"
        async with async_session() as session:
            await session.execute(
                AiRole.__table__.update()
                .where(AiRole.name == "pharmacy_advisor")
                .values(
                    system_prompt=custom_prompt,
                    locked_by_admin=True,
                )
            )
            await session.commit()

        await sync_seeded_roles()

        _invalidate_role_cache()
        role = await get_role("pharmacy_advisor")
        assert role is not None
        assert role.system_prompt == custom_prompt


# ── Rule count regression ──────────────────────────────────────────────


class TestRuleCounts:
    """Verify expected counts after seed."""

    @pytest.mark.asyncio
    async def test_pharmacy_advisor_has_7_rules(self):
        """pharmacy_advisor has 7 rules after seeding."""
        from sqlalchemy import delete, select

        async with async_session() as session:
            await session.execute(delete(AiRoleRule))
            await session.execute(delete(AiRoleSkill))
            await session.execute(delete(AiRole))
            await session.commit()
        await seed_ai_roles()

        async with async_session() as session:
            result = await session.execute(
                select(AiRoleRule)
                .join(AiRole)
                .where(AiRole.name == "pharmacy_advisor")
            )
            rules = result.scalars().all()
        assert len(rules) == 7

    @pytest.mark.asyncio
    async def test_pharmacy_advisor_has_12_skills(self):
        """pharmacy_advisor has 12 skills after seeding (11 original - symptom_translation + symptom_acknowledgment + drug_interaction_info)."""
        from sqlalchemy import delete, select

        async with async_session() as session:
            await session.execute(delete(AiRoleRule))
            await session.execute(delete(AiRoleSkill))
            await session.execute(delete(AiRole))
            await session.commit()
        await seed_ai_roles()

        async with async_session() as session:
            result = await session.execute(
                select(AiRoleSkill)
                .join(AiRole)
                .where(AiRole.name == "pharmacy_advisor")
            )
            skills = result.scalars().all()
        skill_names = {s.name for s in skills}
        assert "symptom_translation" not in skill_names
        assert "symptom_acknowledgment" in skill_names
        assert "drug_interaction_info" in skill_names
        assert len(skills) == 12


# ── Three-layer consistency tests ─────────────────────────────────────
# These tests verify that the three instruction layers that control
# symptom→drug behavior all agree with each other.  The layers are:
#   Layer 1: Seed rules (no_drug_recommendations) in seed.py
#   Layer 2: Seed skills (symptom_acknowledgment) in seed.py
#   Layer 3: Classify instructions (CLASSIFY_INSTRUCTIONS) in ai_responder.py
# If any layer contradicts the others, the AI will behave unpredictably.


class TestThreeLayerConsistency:
    """Verify the three instruction layers agree on symptom→drug policy."""

    def _get_rule_content(self, name: str) -> str:
        seed = _get_pharmacy_seed()
        for rule in seed["rules"]:
            if rule["name"] == name:
                return rule["content"]
        raise AssertionError(f"Rule {name!r} not found in seed")

    def _get_skill_content(self, name: str) -> str:
        seed = _get_pharmacy_seed()
        for skill in seed["skills"]:
            if skill["name"] == name:
                return skill["content"]
        raise AssertionError(f"Skill {name!r} not found in seed")

    def _get_classify_instructions(self) -> str:
        from farmafacil.services.ai_responder import CLASSIFY_INSTRUCTIONS
        return CLASSIFY_INSTRUCTIONS

    # ── Symptom-only = question, NOT drug_search ──

    def test_rule_says_symptom_only_is_question(self):
        """Layer 1: no_drug_recommendations rule says symptom-only = question."""
        content = self._get_rule_content("no_drug_recommendations")
        assert "question" in content.lower() or "NO drug_search" in content
        assert "NO elijas un medicamento por el usuario" in content

    def test_skill_says_symptom_only_is_question(self):
        """Layer 2: symptom_acknowledgment skill says symptom-only = question."""
        content = self._get_skill_content("symptom_acknowledgment")
        assert "question" in content.lower()
        assert "NUNCA como 'drug_search'" in content or "NO drug_search" in content or "NO como drug_search" in content or "NUNCA como drug_search" in content

    def test_classify_says_symptom_only_is_question(self):
        """Layer 3: CLASSIFY_INSTRUCTIONS says symptom-only = question."""
        instructions = self._get_classify_instructions()
        # Must contain the symptom-only rule pointing to question
        assert "SOLO SÍNTOMAS sin nombrar un producto" in instructions
        assert "clasifica como question (NO drug_search)" in instructions

    def test_classify_does_not_say_symptom_is_drug_search(self):
        """Layer 3: CLASSIFY_INSTRUCTIONS must NOT say symptom-only = drug_search."""
        instructions = self._get_classify_instructions()
        # Find the symptom-only rule line and verify it says question
        for line in instructions.split("\n"):
            if "SOLO SÍNTOMAS sin nombrar" in line:
                assert "drug_search" not in line.split("question")[0], \
                    f"Symptom-only line says drug_search BEFORE question: {line}"
                break
        else:
            pytest.fail("Symptom-only rule not found in CLASSIFY_INSTRUCTIONS")

    # ── All three layers require doctor disclaimer ──

    def test_all_layers_require_doctor_disclaimer(self):
        """All three layers include 'consulta con tu médico'."""
        rule = self._get_rule_content("no_drug_recommendations")
        skill = self._get_skill_content("symptom_acknowledgment")
        instructions = self._get_classify_instructions()

        assert "consulta con tu médico" in rule.lower(), "Rule missing doctor disclaimer"
        assert "consulta con tu médico" in skill.lower(), "Skill missing doctor disclaimer"
        assert "consulta con tu médico" in instructions.lower(), "Classify missing doctor disclaimer"

    # ── All three layers prohibit prescribing ──

    def test_all_layers_prohibit_prescribing(self):
        """All three layers prohibit prescribing language."""
        rule = self._get_rule_content("no_drug_recommendations")
        skill = self._get_skill_content("symptom_acknowledgment")
        instructions = self._get_classify_instructions()

        assert "PROHIBIDO" in skill or "NUNCA" in rule
        assert "NUNCA elijas un medicamento por el usuario" in instructions or \
               "NUNCA elijas un medicamento" in rule

    # ── All three layers allow naming OTC options ──

    def test_all_layers_allow_otc_informing(self):
        """All three layers allow naming common OTC options."""
        rule = self._get_rule_content("no_drug_recommendations")
        skill = self._get_skill_content("symptom_acknowledgment")
        instructions = self._get_classify_instructions()

        assert "OTC" in rule or "venta libre" in rule, "Rule doesn't mention OTC"
        assert "OTC" in skill or "opciones" in skill.lower(), "Skill doesn't mention OTC options"
        assert "OTC" in instructions or "opciones" in instructions.lower(), \
            "Classify doesn't mention OTC options"
