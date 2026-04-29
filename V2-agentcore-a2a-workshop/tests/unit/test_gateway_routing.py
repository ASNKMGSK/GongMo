"""
Unit tests for Gateway routing architecture.

Tests that the Gateway stack correctly registers all MCP targets,
the orchestrator connects through Gateway, and tool discovery works.
"""

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


class TestGatewayUrlResolution:
    """Tests for Gateway URL resolution."""

    def setup_method(self):
        _ensure_orch_path()
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith(("common", "orchestrator")):
                if mod_name in sys.modules:
                    del sys.modules[mod_name]

    @patch("orchestrator.boto3")
    def test_gateway_url_env_override(self, mock_boto3):
        """Test that GATEWAY_URL env var takes precedence over SSM."""
        os.environ["GATEWAY_URL"] = "https://env-gateway.test/mcp"
        try:
            import orchestrator
            url = orchestrator._get_gateway_url("us-east-1")
            assert url == "https://env-gateway.test/mcp"
            # SSM should NOT be called
            mock_boto3.client.assert_not_called()
        finally:
            del os.environ["GATEWAY_URL"]

    @patch("orchestrator.boto3")
    def test_gateway_url_ssm_fallback(self, mock_boto3):
        """Test SSM fallback when env var is not set."""
        os.environ.pop("GATEWAY_URL", None)
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": "https://ssm-gateway.test/mcp"}
        }
        mock_boto3.client.return_value = mock_ssm

        import orchestrator
        url = orchestrator._get_gateway_url("us-east-1")
        assert url == "https://ssm-gateway.test/mcp"
        mock_ssm.get_parameter.assert_called_once_with(Name="/a2a_gateway/gateway_url")

    @patch("orchestrator.boto3")
    def test_gateway_url_error_when_unavailable(self, mock_boto3):
        """Test RuntimeError when neither env var nor SSM has gateway URL."""
        os.environ.pop("GATEWAY_URL", None)
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.side_effect = Exception("ParameterNotFound")
        mock_boto3.client.return_value = mock_ssm

        import orchestrator
        with pytest.raises(RuntimeError, match="Cannot resolve"):
            orchestrator._get_gateway_url("us-east-1")


class TestSigV4Auth:
    """Tests for SigV4 authentication."""

    def setup_method(self):
        _ensure_orch_path()
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith(("common", "orchestrator")):
                if mod_name in sys.modules:
                    del sys.modules[mod_name]

    def test_sigv4_auth_class_exists(self):
        """Test that the SigV4 auth class exists in the orchestrator module."""
        import orchestrator
        assert hasattr(orchestrator, "_SigV4HTTPXAuth")
        assert hasattr(orchestrator, "_get_sigv4_auth")


class TestToolDescriptions:
    """Tests for tool description generation in system prompt."""

    def setup_method(self):
        _ensure_orch_path()
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith("common"):
                if mod_name in sys.modules:
                    del sys.modules[mod_name]

    def test_system_prompt_includes_tools(self):
        """Test that system prompt includes tool descriptions."""
        from common.prompts import get_orchestrator_system_prompt

        tool_descs = [
            "get_current_weather: Get current weather for a location",
            "search_knowledge_base: Search documents in knowledge base",
            "summarize_text: Summarize text from multiple sources",
        ]
        prompt = get_orchestrator_system_prompt(tool_descs)

        assert "get_current_weather" in prompt
        assert "search_knowledge_base" in prompt
        assert "summarize_text" in prompt
        assert "Gateway" in prompt

    def test_system_prompt_with_user_context(self):
        """Test system prompt with user context."""
        from common.prompts import get_orchestrator_system_prompt

        prompt = get_orchestrator_system_prompt(
            tool_descriptions=["tool1: desc1"],
            user_context="User prefers Celsius for temperatures",
        )
        assert "User prefers Celsius" in prompt
        assert "USER CONTEXT" in prompt
