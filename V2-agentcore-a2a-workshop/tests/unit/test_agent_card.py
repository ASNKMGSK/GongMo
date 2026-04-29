"""
Unit tests for A2A Agent Card schema.

Tests the AgentCard dataclass and the pre-defined agent cards for all agents.
"""

import sys
from pathlib import Path

import pytest

# Import agent card modules from each agent
ORCH_DIR = Path(__file__).parent.parent.parent / "packages" / "agentcore-agents" / "orchestrator-agent"
MCP_DIR = Path(__file__).parent.parent.parent / "packages" / "agentcore-agents" / "mcp-data-agent"
RAG_DIR = Path(__file__).parent.parent.parent / "packages" / "agentcore-agents" / "rag-agent"
SUMMARY_DIR = Path(__file__).parent.parent.parent / "packages" / "agentcore-agents" / "summary-agent"

for d in [ORCH_DIR, MCP_DIR, RAG_DIR, SUMMARY_DIR]:
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))


class TestAgentCard:
    """Tests for the AgentCard dataclass."""

    def test_orchestrator_card_creation(self):
        from a2a.agent_card import ORCHESTRATOR_AGENT_CARD

        card = ORCHESTRATOR_AGENT_CARD
        assert card.name == "orchestrator-agent"
        assert card.description is not None
        assert len(card.description) > 0
        assert "intent_routing" in card.capabilities

    def test_orchestrator_card_to_dict(self):
        from a2a.agent_card import ORCHESTRATOR_AGENT_CARD

        card_dict = ORCHESTRATOR_AGENT_CARD.to_dict()
        assert isinstance(card_dict, dict)
        assert card_dict["name"] == "orchestrator-agent"
        assert "capabilities" in card_dict
        assert "input_schema" in card_dict
        assert "output_schema" in card_dict

    def test_orchestrator_card_input_schema(self):
        from a2a.agent_card import ORCHESTRATOR_AGENT_CARD

        schema = ORCHESTRATOR_AGENT_CARD.input_schema
        assert schema["type"] == "object"
        assert "prompt" in schema["properties"]
        assert "prompt" in schema["required"]

    def test_mcp_data_agent_card(self):
        # Need to reset imports since each agent has its own a2a module
        import importlib
        sys.path.insert(0, str(MCP_DIR))
        spec = importlib.util.spec_from_file_location(
            "mcp_a2a_agent_card", MCP_DIR / "a2a" / "agent_card.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        card = mod.MCP_DATA_AGENT_CARD
        assert card.name == "mcp-data-agent"
        assert "weather" in card.description.lower()
        card_dict = card.to_dict()
        assert isinstance(card_dict, dict)
        assert "prompt" in card_dict["input_schema"]["properties"]

    def test_rag_agent_card(self):
        import importlib
        spec = importlib.util.spec_from_file_location(
            "rag_a2a_agent_card", RAG_DIR / "a2a" / "agent_card.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        card = mod.RAG_AGENT_CARD
        assert card.name == "rag-agent"
        assert "document" in card.description.lower() or "retriev" in card.description.lower()
        card_dict = card.to_dict()
        assert "document_search" in card_dict["capabilities"]

    def test_summary_agent_card(self):
        import importlib
        spec = importlib.util.spec_from_file_location(
            "summary_a2a_agent_card", SUMMARY_DIR / "a2a" / "agent_card.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        card = mod.SUMMARY_AGENT_CARD
        assert card.name == "summary-agent"
        assert "summar" in card.description.lower()
        card_dict = card.to_dict()
        assert "summarization" in card_dict["capabilities"]

    def test_agent_card_url_is_empty_by_default(self):
        from a2a.agent_card import ORCHESTRATOR_AGENT_CARD

        # URL is set at runtime, so should be empty string by default
        assert ORCHESTRATOR_AGENT_CARD.url == ""

    def test_all_cards_have_required_fields(self):
        """Verify all cards have the mandatory A2A fields."""
        import importlib

        cards = []

        # Load all agent cards using importlib to avoid naming conflicts
        for agent_dir, module_var in [
            (ORCH_DIR, "ORCHESTRATOR_AGENT_CARD"),
            (MCP_DIR, "MCP_DATA_AGENT_CARD"),
            (RAG_DIR, "RAG_AGENT_CARD"),
            (SUMMARY_DIR, "SUMMARY_AGENT_CARD"),
        ]:
            spec = importlib.util.spec_from_file_location(
                f"{agent_dir.name}_card", agent_dir / "a2a" / "agent_card.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            cards.append(getattr(mod, module_var))

        required_fields = ["name", "description", "url", "capabilities", "input_schema", "output_schema"]
        for card in cards:
            card_dict = card.to_dict()
            for field in required_fields:
                assert field in card_dict, f"Agent card {card.name} missing field: {field}"
