"""Tests for user profile validation and name checking."""

import pytest

from farmafacil.bot.handler import _is_valid_name
from farmafacil.db.session import async_session, init_db
from farmafacil.models.database import User
from farmafacil.services.users import (
    get_or_create_user,
    set_onboarding_step,
    update_user_location,
    update_user_name,
    validate_user_profile,
)


class TestIsValidName:
    """Test the name validation helper."""

    def test_valid_simple_names(self):
        """Common Spanish names are accepted."""
        assert _is_valid_name("Maria") is True
        assert _is_valid_name("Jose") is True
        assert _is_valid_name("Carlos") is True
        assert _is_valid_name("Daniel Gonzalez") is True

    def test_rejects_greetings(self):
        """Common greetings are rejected as names."""
        assert _is_valid_name("Hi") is False
        assert _is_valid_name("hello") is False
        assert _is_valid_name("hola") is False
        assert _is_valid_name("buenas") is False
        assert _is_valid_name("hey") is False

    def test_rejects_common_words(self):
        """Bot commands and common words are rejected."""
        assert _is_valid_name("ayuda") is False
        assert _is_valid_name("help") is False
        assert _is_valid_name("ok") is False
        assert _is_valid_name("si") is False
        assert _is_valid_name("gracias") is False

    def test_rejects_drug_names(self):
        """Drug names in the blocklist are rejected."""
        assert _is_valid_name("losartan") is False
        assert _is_valid_name("acetaminofen") is False

    def test_rejects_empty_and_short(self):
        """Empty strings and single characters are rejected."""
        assert _is_valid_name("") is False
        assert _is_valid_name("a") is False
        assert _is_valid_name(" ") is False

    def test_rejects_numbers(self):
        """Pure digit strings are rejected."""
        assert _is_valid_name("123") is False
        assert _is_valid_name("1") is False
        assert _is_valid_name("2") is False

    def test_rejects_long_sentences(self):
        """Sentences with more than 4 words are rejected."""
        assert _is_valid_name("Necesito conseguir un medicamento urgente") is False

    def test_accepts_compound_names(self):
        """Compound names up to 4 words are accepted."""
        assert _is_valid_name("Maria del Carmen") is True
        assert _is_valid_name("Jose Luis Rodriguez Perez") is True


class TestValidateUserProfile:
    """Test the user profile integrity checker."""

    async def test_complete_profile_unchanged(self):
        """A valid complete profile is not modified."""
        user = await get_or_create_user("test_valid_001")
        await update_user_name("test_valid_001", "TestUser")
        await update_user_location("test_valid_001", 10.45, -66.85, "El Cafetal", "CCS")
        from farmafacil.services.users import update_user_preference
        user = await update_user_preference("test_valid_001", "grid")

        # Profile is complete — validate should not change anything
        validated = await validate_user_profile(user)
        assert validated.onboarding_step is None
        assert validated.name == "TestUser"

    async def test_complete_step_but_no_name_resets(self):
        """Onboarding complete but missing name resets to awaiting_name."""
        user = await get_or_create_user("test_noname_001")
        # Force step to None (complete) without setting name
        await set_onboarding_step("test_noname_001", None)
        user = await get_or_create_user("test_noname_001")

        validated = await validate_user_profile(user)
        assert validated.onboarding_step == "awaiting_name"

    async def test_complete_step_but_no_location_resets(self):
        """Onboarding complete but missing location resets to awaiting_location."""
        user = await get_or_create_user("test_noloc_001")
        await update_user_name("test_noloc_001", "TestUser")
        # Force step to None (complete) without setting location
        await set_onboarding_step("test_noloc_001", None)
        user = await get_or_create_user("test_noloc_001")

        validated = await validate_user_profile(user)
        assert validated.onboarding_step == "awaiting_location"

    async def test_awaiting_preference_but_no_name_resets(self):
        """At awaiting_preference but name is missing — resets to awaiting_name."""
        user = await get_or_create_user("test_badpref_001")
        # Force step to awaiting_preference without name
        await set_onboarding_step("test_badpref_001", "awaiting_preference")
        user = await get_or_create_user("test_badpref_001")

        validated = await validate_user_profile(user)
        assert validated.onboarding_step == "awaiting_name"

    async def test_awaiting_location_but_no_name_resets(self):
        """At awaiting_location but name is missing — resets to awaiting_name."""
        user = await get_or_create_user("test_badloc_001")
        await set_onboarding_step("test_badloc_001", "awaiting_location")
        user = await get_or_create_user("test_badloc_001")

        validated = await validate_user_profile(user)
        assert validated.onboarding_step == "awaiting_name"

    async def test_welcome_step_unchanged(self):
        """Welcome step is not changed by validation."""
        user = await get_or_create_user("test_welcome_001")
        validated = await validate_user_profile(user)
        assert validated.onboarding_step == "welcome"
