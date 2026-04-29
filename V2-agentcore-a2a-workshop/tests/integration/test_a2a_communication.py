"""
Integration tests for Gateway-routed A2A communication.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ORCH_DIR = Path(__file__).parent.parent.parent / "packages" / "agentcore-agents" / "orchestrator-agent"


def _ensure_orch_path():
    orch_str = str(ORCH_DIR)
    agents_dir = str(ORCH_DIR.parent)
    sys.path = [p for p in sys.path if not (p.startswith(agents_dir) and p != orch_str)]
    if orch_str not in sys.path:
        sys.path.insert(0, orch_str)
    for key in list(sys.modules.keys()):
        if key.startswith("common") and key != "common":
            del sys.modules[key]
        elif key == "common":
            del sys.modules[key]


class TestGatewayIntegration:
    """Tests for Gateway-based communication flow."""

    def setup_method(self):
        _ensure_orch_path()
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith(("common", "orchestrator")):
                if mod_name in sys.modules:
                    del sys.modules[mod_name]

    @patch("orchestrator.boto3")
    def test_gateway_url_resolution_chain(self, mock_boto3):
        """Test Gateway URL resolution from SSM."""
        os.environ.pop("GATEWAY_URL", None)
        
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": "https://gateway.test.amazonaws.com/mcp"}
        }
        mock_boto3.client.return_value = mock_ssm

        import orchestrator
        url = orchestrator._get_gateway_url("us-east-1")
        assert "gateway.test" in url

    @patch("orchestrator.boto3")
    def test_gateway_url_env_takes_precedence(self, mock_boto3):
        """Test that env var GATEWAY_URL takes precedence over SSM."""
        os.environ["GATEWAY_URL"] = "https://env-override.test/mcp"
        try:
            import orchestrator
            url = orchestrator._get_gateway_url("us-east-1")
            assert url == "https://env-override.test/mcp"
        finally:
            del os.environ["GATEWAY_URL"]


class TestCognitoTokenFlow:
    """Tests for Cognito token management."""

    def setup_method(self):
        _ensure_orch_path()
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith("common"):
                if mod_name in sys.modules:
                    del sys.modules[mod_name]

    @patch("common.cognito_token_manager.boto3")
    def test_token_manager_user_password_flow(self, mock_boto3):
        """Test USER_PASSWORD_AUTH flow."""
        from common.cognito_token_manager import CognitoTokenManager

        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({
                "user_pool_id": "us-east-1_TEST",
                "client_id": "test-client-id",
                "username": "testuser",
                "password": "testpassword",
            })
        }
        mock_cognito = MagicMock()
        mock_cognito.initiate_auth.return_value = {
            "AuthenticationResult": {"AccessToken": "test-access-token"}
        }

        def client_factory(service, **kwargs):
            if service == "secretsmanager":
                return mock_secrets
            if service == "cognito-idp":
                return mock_cognito
            return MagicMock()

        mock_boto3.client = MagicMock(side_effect=client_factory)

        manager = CognitoTokenManager(secret_name="test/credentials")
        token = manager.get_fresh_token()
        assert token == "test-access-token"

    @patch("common.cognito_token_manager.boto3")
    @patch("common.cognito_token_manager.requests")
    def test_token_manager_client_credentials_flow(self, mock_requests, mock_boto3):
        """Test client_credentials flow (M2M for Gateway targets)."""
        from common.cognito_token_manager import CognitoTokenManager

        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({
                "user_pool_id": "us-east-1_TEST",
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                "token_url": "https://auth.test.com/oauth2/token",
                "scope": "test-api/invoke",
            })
        }
        mock_boto3.client.return_value = mock_secrets

        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "m2m-access-token"}
        mock_response.raise_for_status = MagicMock()
        mock_requests.post.return_value = mock_response

        manager = CognitoTokenManager(secret_name="test/credentials")
        token = manager.get_fresh_token()
        assert token == "m2m-access-token"


class TestMemoryModule:
    """Tests for memory module (short-term and long-term)."""

    def setup_method(self):
        _ensure_orch_path()
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith(("common", "memory")):
                if mod_name in sys.modules:
                    del sys.modules[mod_name]

    def test_sliding_window_memory(self):
        """Test in-memory sliding window."""
        from memory.short_term_memory import SlidingWindowMemory

        mem = SlidingWindowMemory(max_size=3)
        mem.add("user", "Hello")
        mem.add("assistant", "Hi there")
        mem.add("user", "How are you?")
        mem.add("assistant", "I'm fine")

        assert len(mem) == 3
        messages = mem.get_messages()
        assert messages[0]["content"] == "Hi there"
        assert messages[-1]["content"] == "I'm fine"

    def test_sliding_window_format(self):
        """Test formatting for prompt injection."""
        from memory.short_term_memory import SlidingWindowMemory

        mem = SlidingWindowMemory()
        mem.add("user", "test message")
        formatted = mem.format_for_prompt()
        assert "user: test message" in formatted
