"""
Unit tests for MCP Data Agent — using importlib for isolated loading.
"""

import importlib
import importlib.util
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

MCP_DIR = Path(__file__).parent.parent.parent / "packages" / "agentcore-agents" / "mcp-data-agent"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestMCPDataAgentConfig:
    """Tests for MCP Data Agent configuration (loaded in isolation)."""

    def test_default_model_id(self):
        mod = _load("mcp_common_config", MCP_DIR / "common" / "config.py")
        assert "anthropic" in mod.DEFAULT_MODEL_ID or "claude" in mod.DEFAULT_MODEL_ID

    def test_tool_name(self):
        mod = _load("mcp_common_config2", MCP_DIR / "common" / "config.py")
        assert isinstance(mod.TOOL_NAME, str)
        assert len(mod.TOOL_NAME) > 0

    def test_aws_config_init(self):
        mod = _load("mcp_common_config3", MCP_DIR / "common" / "config.py")
        config = mod.AWSConfig()
        assert config._region is None
        assert config._session is None


def _setup_mcp_modules():
    """Pre-load MCP agent's common package so imports resolve correctly."""
    _load("common", MCP_DIR / "common" / "__init__.py")
    _load("common.config", MCP_DIR / "common" / "config.py")
    _load("common.bedrock_client", MCP_DIR / "common" / "bedrock_client.py")
    _load("common.logger", MCP_DIR / "common" / "logger.py")


class TestMCPDataAgentSystemPrompt:
    """Tests for MCP Data Agent system prompt."""

    def test_system_prompt_exists(self):
        _setup_mcp_modules()
        mod = _load("mcp_data_agent_mod", MCP_DIR / "mcp_data_agent.py")
        assert isinstance(mod.SYSTEM_PROMPT, str)
        assert len(mod.SYSTEM_PROMPT) > 0

    def test_system_prompt_mentions_weather(self):
        _setup_mcp_modules()
        mod = _load("mcp_data_agent_mod2", MCP_DIR / "mcp_data_agent.py")
        assert "weather" in mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_mcp(self):
        _setup_mcp_modules()
        mod = _load("mcp_data_agent_mod3", MCP_DIR / "mcp_data_agent.py")
        assert "MCP" in mod.SYSTEM_PROMPT


class TestMCPDataAgentGateway:
    """Tests for Gateway URL resolution."""

    def test_get_gateway_url_from_env(self):
        mod = _load("mcp_data_agent_gw", MCP_DIR / "mcp_data_agent.py")
        with patch.dict(os.environ, {"GATEWAY_URL": "https://test-gateway.example.com"}):
            url = mod._get_gateway_url("us-east-1")
            assert url == "https://test-gateway.example.com"

    def test_get_gateway_url_from_ssm(self):
        _setup_mcp_modules()
        mod = _load("mcp_data_agent_gw2", MCP_DIR / "mcp_data_agent.py")
        mock_client = MagicMock()
        mock_client.get_parameter.return_value = {
            "Parameter": {"Value": "https://ssm-gateway.example.com"}
        }
        with patch.object(mod.boto3, "client", return_value=mock_client):
            env = dict(os.environ)
            env.pop("GATEWAY_URL", None)
            with patch.dict(os.environ, env, clear=True):
                url = mod._get_gateway_url("us-east-1")
                assert url == "https://ssm-gateway.example.com"


class TestMCPDataAgentLogger:
    """Tests for MCP Data Agent logging setup."""

    def test_setup_logging(self):
        mod = _load("mcp_common_logger", MCP_DIR / "common" / "logger.py")
        logger = mod.setup_logging()
        assert logger is not None

    def test_get_logger(self):
        mod = _load("mcp_common_logger2", MCP_DIR / "common" / "logger.py")
        logger = mod.get_logger("test_component")
        assert logger is not None
