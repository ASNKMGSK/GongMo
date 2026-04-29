"""
Conftest helpers for isolated module loading.

Each agent has its own `common`, `a2a`, `rag`, etc. packages with the same names.
To avoid sys.path conflicts, we use importlib to load modules from specific paths.
"""

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
AGENTS_DIR = PROJECT_ROOT / "packages" / "agentcore-agents"
MCP_DIR = PROJECT_ROOT / "packages" / "agentcore-mcp-servers"

ORCH_DIR = AGENTS_DIR / "orchestrator-agent"
RAG_DIR = AGENTS_DIR / "rag-agent"
SUMMARY_DIR = AGENTS_DIR / "summary-agent"
WEATHER_MCP_DIR = MCP_DIR / "weather-mcp"

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")


# ---------------------------------------------------------------------------
# Isolated module loader
# ---------------------------------------------------------------------------

def load_module_from_path(module_name: str, file_path: Path, parent_name: str = None):
    """Load a Python module from a specific file path, bypassing sys.path."""
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Module file not found: {file_path}")
    
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_agent_module(agent_dir: Path, module_path: str):
    """Load a module from an agent directory with proper namespace isolation."""
    parts = module_path.split(".")
    file_path = agent_dir / "/".join(parts) + ".py"
    
    if not file_path.exists():
        file_path = agent_dir / "/".join(parts) / "__init__.py"
    
    return load_module_from_path(module_path, file_path)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def aws_region():
    return "us-east-1"

@pytest.fixture
def sample_user_id():
    return "test_user_001"

@pytest.fixture
def sample_session_id():
    return "test_session_001"

@pytest.fixture
def sample_payload():
    return {
        "prompt": "What is the weather in Seoul?",
        "customer_id": "test_user_001",
        "session_id": "test_session_001",
    }
