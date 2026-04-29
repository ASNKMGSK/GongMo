"""
Unit tests for the Orchestrator Agent (Gateway-routed architecture).

Tests that the orchestrator correctly connects to the Gateway, discovers MCP tools,
and builds the agent with proper configuration.
"""

import importlib
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ORCH_DIR = Path(__file__).parent.parent.parent / "packages" / "agentcore-agents" / "orchestrator-agent"


def _ensure_orch_path():
    """Ensure orchestrator dir is at the front of sys.path, remove conflicting entries."""
    orch_str = str(ORCH_DIR)
    agents_dir = str(ORCH_DIR.parent)
    sys.path = [p for p in sys.path if not (p.startswith(agents_dir) and p != orch_str)]
    if orch_str not in sys.path:
        sys.path.insert(0, orch_str)
    # Clear cached common modules
    for key in list(sys.modules.keys()):
        if key.startswith("common") and key != "common":
            del sys.modules[key]
        elif key == "common":
            del sys.modules[key]


class TestOrchestratorConfig:
    """Tests for orchestrator configuration modules."""

    def setup_method(self):
        _ensure_orch_path()
        for mod_name in ["common", "common.config", "common.bedrock_client",
                         "common.cognito_token_manager", "common.logger", "common.prompts"]:
            if mod_name in sys.modules:
                del sys.modules[mod_name]

    def test_config_imports_successfully(self):
        """Verify common.config module imports without errors."""
        from common.config import AWSConfig, DEFAULT_MODEL_ID, SHORT_TERM_MEMORY_NAME
        assert DEFAULT_MODEL_ID is not None
        assert SHORT_TERM_MEMORY_NAME == "A2AWorkshopOrchestratorSTM"

    def test_aws_config_region_from_env(self):
        """Test region detection from environment variable."""
        from common.config import AWSConfig
        import os
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
        config = AWSConfig()
        assert config.get_region() == "us-east-1"

    def test_prompts_generation(self):
        """Test system prompt generation with tool descriptions."""
        from common.prompts import get_orchestrator_system_prompt
        prompt = get_orchestrator_system_prompt(
            tool_descriptions=["get_current_weather: Get weather data", "search_knowledge_base: Search docs"],
            user_context="User prefers metric units",
        )
        assert "get_current_weather" in prompt
        assert "search_knowledge_base" in prompt
        assert "User prefers metric units" in prompt
        assert "A2A Orchestrator Agent" in prompt


class TestOrchestratorModule:
    """Tests for orchestrator module (Gateway-routed)."""

    def setup_method(self):
        _ensure_orch_path()
        for mod_name in ["common", "common.config", "common.bedrock_client",
                         "common.cognito_token_manager", "common.logger", "common.prompts",
                         "orchestrator"]:
            if mod_name in sys.modules:
                del sys.modules[mod_name]

    def test_orchestrator_imports_successfully(self):
        """Verify the orchestrator module imports without errors."""
        import orchestrator
        assert hasattr(orchestrator, "agent_invocation")
        assert hasattr(orchestrator, "_get_gateway_url")
        assert hasattr(orchestrator, "_get_sigv4_auth")

    @patch("orchestrator.boto3")
    def test_gateway_url_from_env(self, mock_boto3):
        """Test Gateway URL resolution from environment variable."""
        import os
        os.environ["GATEWAY_URL"] = "https://test-gateway.example.com/mcp"
        try:
            import orchestrator
            url = orchestrator._get_gateway_url("us-east-1")
            assert url == "https://test-gateway.example.com/mcp"
        finally:
            del os.environ["GATEWAY_URL"]

    @patch("orchestrator.boto3")
    def test_gateway_url_from_ssm(self, mock_boto3):
        """Test Gateway URL resolution from SSM."""
        import os
        # Clear env var
        os.environ.pop("GATEWAY_URL", None)

        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": "https://ssm-gateway.example.com/mcp"}
        }
        mock_boto3.client.return_value = mock_ssm

        import orchestrator
        url = orchestrator._get_gateway_url("us-east-1")
        assert url == "https://ssm-gateway.example.com/mcp"

    def test_sigv4_auth_creation(self):
        """Test SigV4 auth handler can be created."""
        import orchestrator
        # This will attempt to get credentials — should not crash even if none found
        try:
            auth = orchestrator._get_sigv4_auth("us-east-1")
            assert auth is not None
        except ValueError:
            # Expected if no AWS credentials are configured
            pass


class TestCognitoTokenManager:
    """Tests for Cognito token management."""

    def setup_method(self):
        _ensure_orch_path()
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith("common"):
                del sys.modules[mod_name]

    @patch("common.cognito_token_manager.boto3")
    def test_token_manager_user_password_flow(self, mock_boto3):
        """Test USER_PASSWORD_AUTH token refresh."""
        import json
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
            "AuthenticationResult": {"AccessToken": "test-access-token-xyz"}
        }

        def client_factory(service, **kwargs):
            if service == "secretsmanager":
                return mock_secrets
            if service == "cognito-idp":
                return mock_cognito
            return MagicMock()

        mock_boto3.client = MagicMock(side_effect=client_factory)

        manager = CognitoTokenManager(secret_name="test/cognito/credentials")
        token = manager.get_fresh_token()
        assert token == "test-access-token-xyz"
