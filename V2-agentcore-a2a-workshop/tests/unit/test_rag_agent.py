"""
Unit tests for RAG Agent — using importlib for isolated loading.
"""

import importlib
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

RAG_DIR = Path(__file__).parent.parent.parent / "packages" / "agentcore-agents" / "rag-agent"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestRAGAgentConfig:
    """Tests for RAG agent configuration."""

    def test_default_model_id(self):
        mod = _load("rag_config", RAG_DIR / "common" / "config.py")
        assert "anthropic" in mod.DEFAULT_MODEL_ID or "claude" in mod.DEFAULT_MODEL_ID

    def test_embedding_constants(self):
        mod = _load("rag_config2", RAG_DIR / "common" / "config.py")
        assert mod.EMBEDDING_DIMENSION == 1024
        assert "titan-embed" in mod.EMBEDDING_MODEL_ID
        assert mod.OPENSEARCH_INDEX_NAME == "rag-documents"

    def test_chunk_defaults(self):
        mod = _load("rag_config3", RAG_DIR / "common" / "config.py")
        assert mod.DEFAULT_CHUNK_SIZE > 0
        assert mod.DEFAULT_CHUNK_OVERLAP >= 0
        assert mod.DEFAULT_CHUNK_OVERLAP < mod.DEFAULT_CHUNK_SIZE


class TestRAGAgentSystemPrompt:
    """Tests for RAG agent system prompt."""

    def test_system_prompt_contains_instructions(self):
        mod = _load("rag_prompts", RAG_DIR / "common" / "prompts.py")
        prompt = mod.get_rag_agent_system_prompt()
        assert "search_documents" in prompt

    def test_system_prompt_contains_date(self):
        mod = _load("rag_prompts2", RAG_DIR / "common" / "prompts.py")
        prompt = mod.get_rag_agent_system_prompt()
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2}", prompt)


class TestDocumentProcessor:
    """Tests for the DocumentProcessor — loaded in isolation."""

    def _get_proc_class(self):
        # Remove orchestrator's common from sys.modules so RAG's common takes over
        for key in list(sys.modules.keys()):
            if key.startswith("common"):
                del sys.modules[key]
        # Pre-load RAG's common.config FIRST, then common.__init__
        _load("common.config", RAG_DIR / "common" / "config.py")
        _load("common", RAG_DIR / "common" / "__init__.py")
        mod = _load("rag_doc_proc", RAG_DIR / "rag" / "document_processor.py")
        return mod.DocumentProcessor

    def test_chunk_text_short(self):
        DP = self._get_proc_class()
        proc = DP(chunk_size=100, chunk_overlap=20)
        chunks = proc.chunk_text("Hello world this is a test")
        assert len(chunks) >= 1

    def test_chunk_text_long(self):
        DP = self._get_proc_class()
        proc = DP(chunk_size=50, chunk_overlap=10)
        text = " ".join(f"word{i}" for i in range(200))
        chunks = proc.chunk_text(text)
        assert len(chunks) > 1

    def test_chunk_text_empty(self):
        DP = self._get_proc_class()
        proc = DP()
        chunks = proc.chunk_text("")
        assert chunks == []

    def test_chunk_text_whitespace_only(self):
        DP = self._get_proc_class()
        proc = DP()
        chunks = proc.chunk_text("   \n  \t  ")
        assert chunks == []

    def test_extract_text_plain(self):
        DP = self._get_proc_class()
        proc = DP()
        text = proc.extract_text(b"Hello World", "text/plain")
        assert text == "Hello World"

    def test_extract_text_json(self):
        DP = self._get_proc_class()
        proc = DP()
        text = proc.extract_text(b'{"key": "value"}', "application/json")
        assert '"key"' in text

    def test_process_document(self):
        DP = self._get_proc_class()
        proc = DP(chunk_size=50, chunk_overlap=10)
        content = b"This is a test document with enough words to be chunked properly for testing purposes and we need more text here"
        chunks = proc.process_document(content, "text/plain", "test.txt")
        assert len(chunks) > 0
        assert chunks[0]["filename"] == "test.txt"
        assert chunks[0]["chunk_index"] == 0
        assert "text" in chunks[0]

    def test_process_document_empty(self):
        DP = self._get_proc_class()
        proc = DP()
        chunks = proc.process_document(b"", "text/plain", "empty.txt")
        assert chunks == []
