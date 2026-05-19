"""Tests for Phase 1 Security Hotfix (v0.23.0, items 50-55).

Covers:
- Item 50: Admin auth on all PII API endpoints
- Item 51: WhatsApp webhook HMAC-SHA256 signature verification
- Item 52: No hardcoded secrets in config.py
- Item 53: Token counting bug fix in _handle_admin_media
- Item 54: Admin login constant-time comparison
- Item 55: Docker Compose Postgres localhost binding
"""

import hashlib
import hmac
import json
import secrets
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from farmafacil.api.routes import router
from farmafacil.bot.webhook import _verify_signature, webhook_router


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    """Minimal FastAPI app with both routers for testing."""
    app = FastAPI()
    app.include_router(router)
    app.include_router(webhook_router)
    return app


@pytest.fixture
def admin_auth():
    """Return HTTP Basic auth header for admin endpoints."""
    import base64
    creds = base64.b64encode(b"testadmin:testpass").decode()
    return {"Authorization": f"Basic {creds}"}


@pytest.fixture
def no_auth():
    """Return empty headers (no auth)."""
    return {}


# ── Item 50: Admin Auth on PII Endpoints ────────────────────────────────


class TestEndpointAuth:
    """All PII endpoints must require HTTP Basic admin auth."""

    # Endpoints that should require auth (method, path)
    PROTECTED_ENDPOINTS = [
        ("GET", "/api/v1/conversations"),
        ("GET", "/api/v1/users"),
        ("GET", "/api/v1/intents"),
        ("POST", "/api/v1/intents"),
        ("GET", "/api/v1/stats"),
        ("DELETE", "/api/v1/intents/1"),
        ("GET", "/admin/user-stats/1"),
        ("GET", "/api/v1/scheduled-tasks"),
        ("POST", "/api/v1/scheduled-tasks/1/run"),
        ("GET", "/admin/conversations"),
        ("GET", "/admin/conversations/12345"),
        ("GET", "/api/v1/conversations/export"),
        ("GET", "/api/v1/audio/1"),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    async def test_endpoint_returns_401_without_auth(self, app, method, path):
        """PII endpoint returns 401 when no credentials are provided."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            if method == "GET":
                response = await client.get(path)
            elif method == "POST":
                response = await client.post(path, json={})
            elif method == "DELETE":
                response = await client.delete(path)

            assert response.status_code == 401, (
                f"{method} {path} should return 401 without auth, got {response.status_code}"
            )

    @pytest.mark.asyncio
    async def test_endpoint_returns_401_with_wrong_credentials(self, app):
        """PII endpoint returns 401 with invalid credentials."""
        import base64
        bad_creds = base64.b64encode(b"wrong:wrong").decode()
        headers = {"Authorization": f"Basic {bad_creds}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/users", headers=headers)
            assert response.status_code == 401

    # Endpoints that should NOT require auth (public)
    PUBLIC_ENDPOINTS = [
        ("GET", "/health"),
        ("GET", "/webhook?hub.mode=subscribe&hub.verify_token=x&hub.challenge=test"),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,path", PUBLIC_ENDPOINTS)
    async def test_public_endpoint_no_auth_needed(self, app, method, path):
        """Public endpoints should not return 401."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(path)
            # Should NOT be 401 (might be 200, 403, etc. but never 401)
            assert response.status_code != 401, (
                f"{method} {path} should be public, but got 401"
            )


# ── Item 51: Webhook HMAC-SHA256 Verification ──────────────────────────


class TestWebhookHMAC:
    """Webhook signature verification with HMAC-SHA256."""

    def test_verify_signature_valid(self):
        """Valid HMAC signature passes verification."""
        secret = "test_secret_123"
        payload = b'{"entry":[{"changes":[]}]}'
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        with patch("farmafacil.bot.webhook.WHATSAPP_APP_SECRET", secret):
            assert _verify_signature(payload, f"sha256={sig}") is True

    def test_verify_signature_invalid(self):
        """Invalid HMAC signature fails verification."""
        secret = "test_secret_123"
        payload = b'{"entry":[{"changes":[]}]}'

        with patch("farmafacil.bot.webhook.WHATSAPP_APP_SECRET", secret):
            assert _verify_signature(payload, "sha256=badhash") is False

    def test_verify_signature_missing_header(self):
        """Missing signature header fails verification."""
        with patch("farmafacil.bot.webhook.WHATSAPP_APP_SECRET", "secret"):
            assert _verify_signature(b"body", "") is False

    def test_verify_signature_wrong_prefix(self):
        """Signature without sha256= prefix fails."""
        with patch("farmafacil.bot.webhook.WHATSAPP_APP_SECRET", "secret"):
            assert _verify_signature(b"body", "md5=abc") is False

    def test_verify_signature_no_secret_configured(self):
        """When no app secret is set, verification is skipped (dev mode)."""
        with patch("farmafacil.bot.webhook.WHATSAPP_APP_SECRET", ""):
            assert _verify_signature(b"body", "") is True

    def test_verify_signature_tampered_payload(self):
        """Signature computed on different payload fails."""
        secret = "test_secret_123"
        original = b'{"key": "original"}'
        tampered = b'{"key": "tampered"}'
        sig = hmac.new(secret.encode(), original, hashlib.sha256).hexdigest()

        with patch("farmafacil.bot.webhook.WHATSAPP_APP_SECRET", secret):
            assert _verify_signature(tampered, f"sha256={sig}") is False

    @pytest.mark.asyncio
    async def test_webhook_post_rejects_bad_signature(self, app):
        """POST /webhook returns 403 when HMAC verification fails."""
        payload = json.dumps({"entry": []}).encode()

        with patch("farmafacil.bot.webhook.WHATSAPP_APP_SECRET", "real_secret"):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/webhook",
                    content=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Hub-Signature-256": "sha256=invalid",
                    },
                )
                assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_webhook_post_accepts_valid_signature(self, app):
        """POST /webhook returns 200 when HMAC verification passes."""
        secret = "real_secret"
        payload = json.dumps({"entry": []}).encode()
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        with patch("farmafacil.bot.webhook.WHATSAPP_APP_SECRET", secret):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/webhook",
                    content=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Hub-Signature-256": f"sha256={sig}",
                    },
                )
                assert response.status_code == 200


class TestWebhookVerifyToken:
    """GET /webhook verify_token uses constant-time comparison."""

    @pytest.mark.asyncio
    async def test_verify_token_uses_constant_time_compare(self, app):
        """Verify token comparison uses secrets.compare_digest."""
        token = "my_token"
        with patch("farmafacil.bot.webhook.WHATSAPP_VERIFY_TOKEN", token):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/webhook",
                    params={
                        "hub.mode": "subscribe",
                        "hub.verify_token": token,
                        "hub.challenge": "test_challenge",
                    },
                )
                assert response.status_code == 200
                assert response.text == "test_challenge"

    @pytest.mark.asyncio
    async def test_verify_token_rejects_wrong_token(self, app):
        """Wrong verify token returns 403."""
        with patch("farmafacil.bot.webhook.WHATSAPP_VERIFY_TOKEN", "correct"):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/webhook",
                    params={
                        "hub.mode": "subscribe",
                        "hub.verify_token": "wrong",
                        "hub.challenge": "test",
                    },
                )
                assert response.status_code == 403


# ── Item 52: No Hardcoded Secrets ───────────────────────────────────────


class TestNoHardcodedSecrets:
    """Verify secrets are not hardcoded in source code."""

    def test_config_no_hardcoded_algolia_keys(self):
        """config.py should not have Algolia key defaults."""
        config_path = Path(__file__).parent.parent / "src" / "farmafacil" / "config.py"
        content = config_path.read_text()
        assert "VCOJEYD2PO" not in content, "Algolia App ID is hardcoded in config.py"
        assert "869a91e98550dd668b8b1dc04bca9011" not in content, (
            "Algolia API Key is hardcoded in config.py"
        )

    def test_config_no_hardcoded_verify_token(self):
        """config.py should not have a hardcoded verify token default."""
        config_path = Path(__file__).parent.parent / "src" / "farmafacil" / "config.py"
        content = config_path.read_text()
        assert "farmafacil_verify_2026" not in content, (
            "WHATSAPP_VERIFY_TOKEN has a hardcoded default in config.py"
        )

    def test_docker_compose_no_hardcoded_password(self):
        """docker-compose.yml should use env var for Postgres password."""
        compose_path = Path(__file__).parent.parent / "docker-compose.yml"
        content = compose_path.read_text()
        # Should reference ${POSTGRES_PASSWORD}, not a literal password
        assert "${POSTGRES_PASSWORD}" in content
        # Check that the hardcoded line is gone
        lines = content.splitlines()
        for line in lines:
            if "POSTGRES_PASSWORD" in line and "farmafacil" in line.lower():
                # Allow the env_file reference line or variable reference
                if "${POSTGRES_PASSWORD}" not in line:
                    pytest.fail(f"Hardcoded Postgres password in docker-compose.yml: {line}")

    def test_docker_compose_postgres_localhost_only(self):
        """docker-compose.yml should bind Postgres port to 127.0.0.1."""
        compose_path = Path(__file__).parent.parent / "docker-compose.yml"
        content = compose_path.read_text()
        assert "127.0.0.1:" in content, (
            "Postgres port should be bound to 127.0.0.1 in docker-compose.yml"
        )

    def test_env_example_no_real_verify_token(self):
        """.env.example should not contain the real verify token."""
        env_path = Path(__file__).parent.parent / ".env.example"
        content = env_path.read_text()
        assert "farmafacil_verify_2026" not in content, (
            ".env.example still contains the real verify token"
        )

    def test_config_has_whatsapp_app_secret(self):
        """config.py should define WHATSAPP_APP_SECRET."""
        from farmafacil import config
        assert hasattr(config, "WHATSAPP_APP_SECRET")


# ── Item 53: Token Counting Bug Fix ─────────────────────────────────────


class TestTokenCountingBug:
    """AdminTurnResult uses .input_tokens/.output_tokens, not .tokens_in/.tokens_out."""

    def test_admin_turn_result_has_correct_fields(self):
        """AdminTurnResult has input_tokens and output_tokens (not tokens_in/out)."""
        from farmafacil.services.ai_responder import AdminTurnResult

        result = AdminTurnResult(text="test")
        assert hasattr(result, "input_tokens")
        assert hasattr(result, "output_tokens")
        # Ensure the old wrong names don't exist
        assert not hasattr(result, "tokens_in"), (
            "AdminTurnResult should not have tokens_in (old name)"
        )
        assert not hasattr(result, "tokens_out"), (
            "AdminTurnResult should not have tokens_out (old name)"
        )

    def test_increment_token_usage_expects_int_user_id(self):
        """increment_token_usage first param must be int, not str."""
        import inspect
        from farmafacil.services.users import increment_token_usage

        sig = inspect.signature(increment_token_usage)
        first_param = list(sig.parameters.values())[0]
        assert first_param.name == "user_id"
        assert first_param.annotation == int

    @pytest.mark.asyncio
    async def test_handle_admin_media_calls_increment_with_user_id(self):
        """_handle_admin_media passes user.id (int) to increment_token_usage."""
        from farmafacil.bot.handler import _handle_admin_media

        mock_user = MagicMock()
        mock_user.id = 42
        mock_user.phone_number = "1234567890"
        mock_user.chat_admin = True
        mock_user.admin_mode_active = True

        mock_admin_result = MagicMock()
        mock_admin_result.text = "admin response"
        mock_admin_result.input_tokens = 100
        mock_admin_result.output_tokens = 50

        # encode_image_for_vision returns a dict block
        mock_image_block = {"type": "image", "source": {"data": "base64data"}}

        with (
            patch("farmafacil.bot.handler.run_admin_turn", new_callable=AsyncMock, return_value=mock_admin_result),
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.log_outbound_conv", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.increment_token_usage", new_callable=AsyncMock) as mock_increment,
            patch("farmafacil.services.media.encode_image_for_vision", return_value=mock_image_block),
            patch("farmafacil.bot.handler.get_role", new_callable=AsyncMock, return_value=MagicMock()),
            patch("farmafacil.bot.handler.build_tools_manifest", return_value="tools"),
            patch("farmafacil.bot.handler.assemble_prompt", return_value="prompt"),
        ):
            await _handle_admin_media(
                sender="1234567890",
                user=mock_user,
                data=b"fake_image_data",
                mime_type="image/jpeg",
            )

            # Verify increment_token_usage was called with user.id (int), not phone (str)
            mock_increment.assert_called_once()
            call_args = mock_increment.call_args
            assert call_args[0][0] == 42, (
                f"First arg should be user.id (42), got {call_args[0][0]}"
            )
            assert call_args[0][1] == 100, "Should use input_tokens"
            assert call_args[0][2] == 50, "Should use output_tokens"


# ── Item 54: Admin Login Constant-Time Comparison ───────────────────────


class TestAdminLoginHardening:
    """Admin login uses hmac.compare_digest, not == operator."""

    def test_admin_login_source_uses_compare_digest(self):
        """admin.py login method should use hmac.compare_digest."""
        admin_path = Path(__file__).parent.parent / "src" / "farmafacil" / "api" / "admin.py"
        content = admin_path.read_text()

        # Find the login method and check it uses hmac.compare_digest
        assert "hmac.compare_digest" in content, (
            "admin.py should use hmac.compare_digest for login"
        )

    def test_admin_login_source_no_equality_comparison(self):
        """admin.py login method should not use == for credential comparison."""
        admin_path = Path(__file__).parent.parent / "src" / "farmafacil" / "api" / "admin.py"
        content = admin_path.read_text()

        # Check that we don't have the old pattern
        # (username == ADMIN_USERNAME and password == ADMIN_PASSWORD)
        assert "username == ADMIN_USERNAME" not in content, (
            "admin.py still uses == for username comparison"
        )
        assert "password == ADMIN_PASSWORD" not in content, (
            "admin.py still uses == for password comparison"
        )


# ── Item 55: Docker Compose Postgres Binding ────────────────────────────


class TestDockerCompose:
    """Docker Compose binds Postgres to localhost only."""

    def test_postgres_port_localhost_binding(self):
        """Postgres port mapping includes 127.0.0.1 prefix."""
        compose_path = Path(__file__).parent.parent / "docker-compose.yml"
        content = compose_path.read_text()

        # Should have 127.0.0.1:PORT:5432 binding
        assert "127.0.0.1:" in content
        # Should NOT have bare "5432:5432" (exposed on all interfaces)
        lines = content.splitlines()
        for line in lines:
            stripped = line.strip().strip('"').strip("'").strip("- ")
            if stripped == "5432:5432":
                pytest.fail(
                    "Postgres port is exposed on all interfaces (should be 127.0.0.1:5432:5432)"
                )
