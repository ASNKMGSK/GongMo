"""
Unit tests for the MCP-based architecture (replacing old A2A Client).

Verifies that the orchestrator no longer uses direct A2A client and
instead routes everything through the Gateway.
"""

import sys
from pathlib import Path

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


class TestGatewayArchitectureMigration:
    """Verify the old A2A Client is replaced by Gateway MCP pattern."""

    def setup_method(self):
        _ensure_orch_path()

    def test_orchestrator_uses_gateway_not_a2a(self):
        """The orchestrator should use MCPClient, not A2AClient."""
        source = (ORCH_DIR / "orchestrator.py").read_text()
        
        # Should NOT contain old A2A patterns
        assert "A2AClient" not in source
        assert "invoke_agent" not in source
        assert "discover_agents" not in source
        
        # Should contain new Gateway patterns
        assert "MCPClient" in source
        assert "streamablehttp_client" in source
        assert "gateway_url" in source.lower() or "GATEWAY_URL" in source
        assert "SigV4" in source

    def test_gateway_is_single_entry_point(self):
        """All tool calls should go through the Gateway."""
        source = (ORCH_DIR / "orchestrator.py").read_text()
        
        # Only one external connection point: the Gateway
        assert "_get_gateway_url" in source
        
        # bedrock-agentcore only appears as SigV4 service name, not as direct URL construction
        # Should not have direct /runtimes/ URL patterns
        assert "/runtimes/" not in source.replace("_SigV4HTTPXAuth", "").replace("SigV4Auth", "")

    def test_mcp_tools_from_gateway(self):
        """Tools should be discovered from Gateway, not hardcoded."""
        source = (ORCH_DIR / "orchestrator.py").read_text()
        
        assert "list_tools_sync" in source
        assert "mcp_tools" in source
        assert "all_tools" in source
