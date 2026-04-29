"""
Unit tests for Summary Agent — using importlib for isolated loading.
"""

import importlib
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SUMMARY_DIR = Path(__file__).parent.parent.parent / "packages" / "agentcore-agents" / "summary-agent"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestSummaryAgentSystemPrompt:
    """Tests for Summary Agent system prompt."""

    def test_system_prompt_contains_instructions(self):
        mod = _load("summary_prompts", SUMMARY_DIR / "common" / "prompts.py")
        prompt = mod.get_summary_agent_system_prompt()
        assert "Summary Agent" in prompt
        assert "ANALYZE INPUT" in prompt
        assert "SYNTHESIZE" in prompt

    def test_system_prompt_has_format_options(self):
        mod = _load("summary_prompts2", SUMMARY_DIR / "common" / "prompts.py")
        prompt = mod.get_summary_agent_system_prompt()
        assert "brief" in prompt
        assert "detailed" in prompt
        assert "bullet_points" in prompt

    def test_system_prompt_contains_date(self):
        mod = _load("summary_prompts3", SUMMARY_DIR / "common" / "prompts.py")
        prompt = mod.get_summary_agent_system_prompt()
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2}", prompt)


class TestSummaryAgentModule:
    """Tests for summary_agent module loading."""

    def _ensure_summary_path(self):
        s = str(SUMMARY_DIR)
        agents_dir = str(SUMMARY_DIR.parent)
        sys.path = [p for p in sys.path if not (p.startswith(agents_dir) and p != s)]
        if s not in sys.path:
            sys.path.insert(0, s)
        for key in list(sys.modules.keys()):
            if key.startswith("common"):
                del sys.modules[key]
        if "summary_agent" in sys.modules:
            del sys.modules["summary_agent"]

    def test_default_model_id(self):
        self._ensure_summary_path()
        import summary_agent
        assert "anthropic" in summary_agent.DEFAULT_MODEL_ID or "claude" in summary_agent.DEFAULT_MODEL_ID

    def test_get_region_from_env(self):
        self._ensure_summary_path()
        import summary_agent
        import os
        with patch.dict(os.environ, {"AWS_DEFAULT_REGION": "eu-west-1"}):
            region = summary_agent._get_region()
            assert region == "eu-west-1"
