"""
E2E scenario tests for the Gateway-routed architecture.

These tests verify end-to-end scenarios using the new architecture where
the orchestrator routes all tool calls through the Gateway.
"""

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
    if "orchestrator" in sys.modules:
        del sys.modules["orchestrator"]


class TestArchitectureIntegrity:
    """Tests that verify the Gateway-routed architecture is correct."""

    def setup_method(self):
        _ensure_orch_path()

    def test_orchestrator_uses_mcp_client(self):
        """Orchestrator should use MCPClient to connect to Gateway."""
        source = (ORCH_DIR / "orchestrator.py").read_text()
        assert "MCPClient" in source
        assert "streamablehttp_client" in source
        assert "SigV4" in source

    def test_no_direct_runtime_calls(self):
        """Orchestrator should NOT make direct runtime invocations."""
        source = (ORCH_DIR / "orchestrator.py").read_text()
        assert "A2AClient" not in source
        assert "invoke_agent" not in source
        assert "fetch_external_data" not in source
        assert "search_knowledge_base" not in source.split("def agent_invocation")[0]

    def test_gateway_url_is_configurable(self):
        """Gateway URL should be configurable via env var or SSM."""
        source = (ORCH_DIR / "orchestrator.py").read_text()
        assert "GATEWAY_URL" in source
        assert "/a2a_gateway/gateway_url" in source

    def test_system_prompt_includes_tool_routing(self):
        """System prompt should have intent routing instructions."""
        from common.prompts import get_orchestrator_system_prompt
        prompt = get_orchestrator_system_prompt(
            tool_descriptions=["get_current_weather: weather data", "search_knowledge_base: doc search"]
        )
        assert "WEATHER" in prompt or "weather" in prompt.lower()
        assert "DOCUMENTS" in prompt or "knowledge" in prompt.lower()
        assert "tool" in prompt.lower()


class TestMCPServerAgents:
    """Verify MCP server agents are correctly structured."""

    def test_rag_agent_is_mcp_server(self):
        """RAG agent should use FastMCP, not BedrockAgentCoreApp."""
        rag_path = ORCH_DIR.parent / "rag-agent" / "rag_agent.py"
        source = rag_path.read_text()
        assert "FastMCP" in source
        assert "mcp.tool" in source or "@mcp.tool()" in source
        assert "BedrockAgentCoreApp" not in source

    def test_summary_agent_is_mcp_server(self):
        """Summary agent should use FastMCP."""
        summary_path = ORCH_DIR.parent / "summary-agent" / "summary_agent.py"
        source = summary_path.read_text()
        assert "FastMCP" in source
        assert "mcp.tool" in source or "@mcp.tool()" in source
        assert "BedrockAgentCoreApp" not in source

    def test_weather_mcp_is_mcp_server(self):
        """Weather MCP server should use FastMCP."""
        weather_path = ORCH_DIR.parent.parent / "agentcore-mcp-servers" / "weather-mcp" / "weather_mcp.py"
        source = weather_path.read_text()
        assert "FastMCP" in source
        assert "@mcp.tool()" in source

    def test_rag_agent_has_search_tool(self):
        """RAG agent should expose search_knowledge_base tool."""
        rag_path = ORCH_DIR.parent / "rag-agent" / "rag_agent.py"
        source = rag_path.read_text()
        assert "search_knowledge_base" in source

    def test_summary_agent_has_summarize_tool(self):
        """Summary agent should expose summarize_text tool."""
        summary_path = ORCH_DIR.parent / "summary-agent" / "summary_agent.py"
        source = summary_path.read_text()
        assert "summarize_text" in source

    def test_weather_has_all_tools(self):
        """Weather MCP server should expose all weather tools."""
        weather_path = ORCH_DIR.parent.parent / "agentcore-mcp-servers" / "weather-mcp" / "weather_mcp.py"
        source = weather_path.read_text()
        assert "get_current_weather" in source
        assert "get_forecast" in source
        assert "get_weather_alerts" in source


class TestGatewayStack:
    """Verify CDK Gateway stack registers all MCP targets."""

    def test_gateway_stack_registers_all_targets(self):
        """Gateway stack should register weather, RAG, and summary targets."""
        gateway_path = (
            ORCH_DIR.parent.parent / "cdk-infra-python" / "src" / "stacks" / "agentcore_gateway_stack.py"
        )
        source = gateway_path.read_text()
        assert "weather" in source.lower()
        assert "rag" in source.lower()
        assert "summary" in source.lower()
        assert "add_mcp_server_target" in source

    def test_gateway_uses_iam_auth(self):
        """Gateway should use IAM auth for orchestrator access."""
        gateway_path = (
            ORCH_DIR.parent.parent / "cdk-infra-python" / "src" / "stacks" / "agentcore_gateway_stack.py"
        )
        source = gateway_path.read_text()
        assert "using_aws_iam" in source

    def test_agent_stacks_use_mcp_protocol(self):
        """All agent stacks should use MCP protocol type."""
        stacks_dir = ORCH_DIR.parent.parent / "cdk-infra-python" / "src" / "stacks"
        for stack_name in ["rag_agent_stack.py", "summary_agent_stack.py", "weather_mcp_stack.py"]:
            source = (stacks_dir / stack_name).read_text()
            assert "ProtocolType.MCP" in source, f"{stack_name} should use MCP protocol"

    def test_agent_stacks_have_oauth(self):
        """All agent stacks should have OAuth2 client_credentials flow for Gateway."""
        stacks_dir = ORCH_DIR.parent.parent / "cdk-infra-python" / "src" / "stacks"
        for stack_name in ["rag_agent_stack.py", "summary_agent_stack.py", "weather_mcp_stack.py"]:
            source = (stacks_dir / stack_name).read_text()
            assert "client_credentials" in source, f"{stack_name} should have client_credentials flow"
            assert "oauth_provider_arn" in source, f"{stack_name} should have OAuth provider ARN"
            assert "runtime_endpoint_url" in source, f"{stack_name} should have endpoint URL"
