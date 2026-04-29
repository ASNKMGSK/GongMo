"""
E2E tests for the full Gateway-routed pipeline.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ORCH_DIR = Path(__file__).parent.parent.parent / "packages" / "agentcore-agents" / "orchestrator-agent"
DEPLOYED = os.environ.get("DEPLOYED", "false").lower() == "true"


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
    if "orchestrator" in sys.modules:
        del sys.modules["orchestrator"]


class TestPipelineImports:
    """Test that pipeline modules can be imported."""

    def setup_method(self):
        _ensure_orch_path()

    def test_orchestrator_imports_successfully(self):
        """Verify the orchestrator module imports without errors."""
        import orchestrator
        assert hasattr(orchestrator, "agent_invocation")
        assert hasattr(orchestrator, "_get_gateway_url")
        assert hasattr(orchestrator, "_get_sigv4_auth")
        assert hasattr(orchestrator, "_initialize_memory")

    def test_config_module_loads(self):
        """Verify config module loads correctly."""
        from common.config import AWSConfig, DEFAULT_MODEL_ID
        assert DEFAULT_MODEL_ID is not None
        config = AWSConfig()
        assert config.get_region() == "us-east-1"

    def test_prompts_module_loads(self):
        """Verify prompts module loads correctly."""
        from common.prompts import get_orchestrator_system_prompt
        prompt = get_orchestrator_system_prompt(["tool1: desc1"])
        assert len(prompt) > 100


class TestGatewayArchitecture:
    """Test that the Gateway-routed architecture is properly configured."""

    def setup_method(self):
        _ensure_orch_path()
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith(("common", "orchestrator")):
                if mod_name in sys.modules:
                    del sys.modules[mod_name]

    def test_no_direct_a2a_client(self):
        """Ensure the orchestrator no longer uses direct A2A client calls."""
        import orchestrator
        # Should NOT have the old A2A patterns
        source = open(ORCH_DIR / "orchestrator.py").read()
        assert "A2AClient" not in source
        assert "invoke_agent" not in source
        assert "/runtimes/" not in source  # No direct runtime URL construction
        # Should have Gateway pattern
        assert "gateway_url" in source.lower() or "GATEWAY_URL" in source
        assert "MCPClient" in source
        assert "streamablehttp_client" in source

    def test_gateway_is_single_entry_point(self):
        """Verify Gateway is the only external call point."""
        import orchestrator
        source = open(ORCH_DIR / "orchestrator.py").read()
        # Gateway URL should be the connection point
        assert "_get_gateway_url" in source
        assert "SigV4" in source
        # Should use MCP client
        assert "MCPClient" in source


@pytest.mark.skipif(not DEPLOYED, reason="Requires deployed infrastructure")
class TestDeployedPipeline:
    """Tests that run against actually deployed infrastructure."""

    def setup_method(self):
        _ensure_orch_path()

    def test_gateway_ssm_parameter_exists(self):
        """Test that Gateway SSM parameters are deployed."""
        import boto3
        ssm = boto3.client("ssm", region_name="us-east-1")
        required_params = [
            "/a2a_gateway/gateway_id",
            "/a2a_gateway/gateway_url",
        ]
        for param_name in required_params:
            response = ssm.get_parameter(Name=param_name)
            assert response["Parameter"]["Value"], f"Parameter {param_name} is empty"

    def test_agent_ssm_parameters_exist(self):
        """Test that all agent SSM parameters are deployed."""
        import boto3
        ssm = boto3.client("ssm", region_name="us-east-1")
        required_params = [
            "/a2a_weather_mcp/runtime/agent_arn",
            "/a2a_weather_mcp/runtime/endpoint_url",
            "/a2a_rag_agent/runtime/agent_arn",
            "/a2a_rag_agent/runtime/endpoint_url",
            "/a2a_summary_agent/runtime/agent_arn",
            "/a2a_summary_agent/runtime/endpoint_url",
        ]
        for param_name in required_params:
            response = ssm.get_parameter(Name=param_name)
            assert response["Parameter"]["Value"], f"Parameter {param_name} is empty"

    def test_gateway_url_resolvable(self):
        """Test that Gateway URL can be resolved."""
        import orchestrator
        url = orchestrator._get_gateway_url("us-east-1")
        assert url.startswith("https://")
        assert "bedrock-agentcore" in url or "amazonaws.com" in url
