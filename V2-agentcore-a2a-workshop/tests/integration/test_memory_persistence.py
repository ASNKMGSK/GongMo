"""
Integration tests for memory persistence — using isolated imports.
"""

import sys
import time
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


class TestLongTermMemoryPersistence:
    def setup_method(self):
        _ensure_orch_path()

    @patch("memory.long_term_memory.boto3")
    def test_store_and_retrieve_round_trip(self, mock_boto3):
        from memory.long_term_memory import LongTermMemory

        stored_items = []

        mock_table = MagicMock()
        mock_table.put_item = MagicMock(side_effect=lambda Item: stored_items.append(Item))
        mock_table.query.return_value = {"Items": []}
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_boto3.resource.return_value = mock_resource
        mock_boto3.dynamodb.conditions.Key.return_value.eq.return_value = "mock"

        mem = LongTermMemory(table_name="test-table", region="us-east-1")
        mem.store(user_id="user1", content="First interaction", memory_type="conversation_summary")
        mem.store(user_id="user1", content="User prefers Celsius", memory_type="preference")
        mem.store(user_id="user1", content="User is in Seoul", memory_type="fact")

        assert len(stored_items) == 3

        mock_table.query.return_value = {"Items": stored_items}
        memories = mem.get_recent_memories(user_id="user1", limit=10)
        assert len(memories) == 3

    @patch("memory.long_term_memory.boto3")
    def test_memory_context_format(self, mock_boto3):
        from memory.long_term_memory import LongTermMemory

        mock_table = MagicMock()
        mock_table.query.return_value = {
            "Items": [
                {"user_id": "user1", "content": "Likes hiking", "memory_type": "preference"},
                {"user_id": "user1", "content": "Lives in Seoul", "memory_type": "fact"},
            ]
        }
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_boto3.resource.return_value = mock_resource
        mock_boto3.dynamodb.conditions.Key.return_value.eq.return_value = "mock"

        mem = LongTermMemory(table_name="test-table", region="us-east-1")
        context = mem.get_user_context(user_id="user1")
        assert "[preference]" in context
        assert "[fact]" in context
        assert "Likes hiking" in context

    @patch("memory.long_term_memory.boto3")
    def test_ttl_computation(self, mock_boto3):
        from memory.long_term_memory import LongTermMemory

        captured = {}
        mock_table = MagicMock()
        mock_table.put_item = MagicMock(side_effect=lambda Item: captured.update(Item))
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_boto3.resource.return_value = mock_resource

        mem = LongTermMemory(table_name="test-table", region="us-east-1", ttl_days=7)
        mem.store(user_id="user1", content="Test", memory_type="fact")

        assert "expiry" in captured
        expected_min = int(time.time()) + (7 * 86400) - 10
        expected_max = int(time.time()) + (7 * 86400) + 10
        assert expected_min <= captured["expiry"] <= expected_max


class TestShortTermMemoryPersistence:
    def setup_method(self):
        _ensure_orch_path()

    def test_conversation_continuity(self):
        from memory.short_term_memory import SlidingWindowMemory

        mem = SlidingWindowMemory(max_size=10)
        mem.add("user", "What is the weather in Seoul?")
        mem.add("assistant", "The weather in Seoul is sunny, 25°C")
        mem.add("user", "What about tomorrow?")

        formatted = mem.format_for_prompt()
        assert "Seoul" in formatted
        assert "sunny" in formatted

    def test_window_slides_correctly(self):
        from memory.short_term_memory import SlidingWindowMemory

        mem = SlidingWindowMemory(max_size=4)
        for i in range(10):
            mem.add("user", f"Message {i}")

        messages = mem.get_messages()
        assert len(messages) == 4
        assert messages[0]["content"] == "Message 6"
        assert messages[-1]["content"] == "Message 9"
