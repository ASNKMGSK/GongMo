"""
Unit tests for Long-Term Memory (DynamoDB-backed).

Uses mocked DynamoDB to test store/retrieve/delete operations
without requiring real AWS infrastructure.
"""

import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import sys
from pathlib import Path

ORCH_DIR = Path(__file__).parent.parent.parent / "packages" / "agentcore-agents" / "orchestrator-agent"
if str(ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(ORCH_DIR))


class TestLongTermMemory:
    """Tests for LongTermMemory class."""

    @patch("memory.long_term_memory.boto3")
    def test_init(self, mock_boto3):
        """Test LongTermMemory initialization."""
        mock_table = MagicMock()
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_boto3.resource.return_value = mock_resource

        from memory.long_term_memory import LongTermMemory

        mem = LongTermMemory(table_name="test-table", region="us-east-1", ttl_days=30)
        assert mem.table_name == "test-table"
        assert mem.ttl_days == 30
        mock_boto3.resource.assert_called_once_with("dynamodb", region_name="us-east-1")

    @patch("memory.long_term_memory.boto3")
    def test_store(self, mock_boto3):
        """Test storing a memory entry."""
        mock_table = MagicMock()
        mock_table.put_item.return_value = {}
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_boto3.resource.return_value = mock_resource

        from memory.long_term_memory import LongTermMemory

        mem = LongTermMemory(table_name="test-table", region="us-east-1")
        result = mem.store(user_id="user1", content="Test memory", memory_type="fact")

        assert result["user_id"] == "user1"
        assert result["content"] == "Test memory"
        assert result["memory_type"] == "fact"
        assert "timestamp" in result
        assert "expiry" in result
        mock_table.put_item.assert_called_once()

    @patch("memory.long_term_memory.boto3")
    def test_store_conversation_summary(self, mock_boto3):
        """Test storing a conversation summary."""
        mock_table = MagicMock()
        mock_table.put_item.return_value = {}
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_boto3.resource.return_value = mock_resource

        from memory.long_term_memory import LongTermMemory

        mem = LongTermMemory(table_name="test-table", region="us-east-1")
        result = mem.store_conversation_summary(
            user_id="user1",
            session_id="session1",
            summary="User asked about weather in Seoul",
            agent_used="mcp-data-agent",
        )

        assert result["memory_type"] == "conversation_summary"
        assert "Seoul" in result["content"]

    @patch("memory.long_term_memory.boto3")
    def test_store_user_preference(self, mock_boto3):
        """Test storing a user preference."""
        mock_table = MagicMock()
        mock_table.put_item.return_value = {}
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_boto3.resource.return_value = mock_resource

        from memory.long_term_memory import LongTermMemory

        mem = LongTermMemory(table_name="test-table", region="us-east-1")
        result = mem.store_user_preference(user_id="user1", preference="Prefers Celsius")

        assert result["memory_type"] == "preference"
        assert "Celsius" in result["content"]

    @patch("memory.long_term_memory.boto3")
    def test_get_recent_memories(self, mock_boto3):
        """Test retrieving recent memories."""
        mock_table = MagicMock()
        mock_table.query.return_value = {
            "Items": [
                {"user_id": "user1", "timestamp": "2025-01-01T00:00:00Z", "content": "Memory 1", "memory_type": "fact"},
                {"user_id": "user1", "timestamp": "2025-01-02T00:00:00Z", "content": "Memory 2", "memory_type": "preference"},
            ]
        }
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_boto3.resource.return_value = mock_resource
        mock_boto3.dynamodb.conditions.Key.return_value.eq.return_value = "mock_condition"

        from memory.long_term_memory import LongTermMemory

        mem = LongTermMemory(table_name="test-table", region="us-east-1")
        results = mem.get_recent_memories(user_id="user1", limit=5)

        assert len(results) == 2
        assert results[0]["content"] == "Memory 1"

    @patch("memory.long_term_memory.boto3")
    def test_get_user_context(self, mock_boto3):
        """Test building user context string from memories."""
        mock_table = MagicMock()
        mock_table.query.return_value = {
            "Items": [
                {"user_id": "user1", "timestamp": "2025-01-01T00:00:00Z", "content": "Prefers metric units", "memory_type": "preference"},
                {"user_id": "user1", "timestamp": "2025-01-02T00:00:00Z", "content": "Asked about Seoul weather", "memory_type": "conversation_summary"},
            ]
        }
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_boto3.resource.return_value = mock_resource
        mock_boto3.dynamodb.conditions.Key.return_value.eq.return_value = "mock_condition"

        from memory.long_term_memory import LongTermMemory

        mem = LongTermMemory(table_name="test-table", region="us-east-1")
        context = mem.get_user_context(user_id="user1")

        assert "[preference]" in context
        assert "metric units" in context
        assert "[conversation_summary]" in context

    @patch("memory.long_term_memory.boto3")
    def test_get_user_context_empty(self, mock_boto3):
        """Test building context when no memories exist."""
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": []}
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_boto3.resource.return_value = mock_resource
        mock_boto3.dynamodb.conditions.Key.return_value.eq.return_value = "mock_condition"

        from memory.long_term_memory import LongTermMemory

        mem = LongTermMemory(table_name="test-table", region="us-east-1")
        context = mem.get_user_context(user_id="user1")

        assert context == ""

    @patch("memory.long_term_memory.boto3")
    def test_delete_memory(self, mock_boto3):
        """Test deleting a specific memory entry."""
        mock_table = MagicMock()
        mock_table.delete_item.return_value = {}
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_boto3.resource.return_value = mock_resource

        from memory.long_term_memory import LongTermMemory

        mem = LongTermMemory(table_name="test-table", region="us-east-1")
        mem.delete_memory(user_id="user1", timestamp="2025-01-01T00:00:00Z")

        mock_table.delete_item.assert_called_once_with(
            Key={"user_id": "user1", "timestamp": "2025-01-01T00:00:00Z"}
        )

    @patch("memory.long_term_memory.boto3")
    def test_clear_user_memories(self, mock_boto3):
        """Test clearing all memories for a user."""
        mock_table = MagicMock()
        mock_table.query.return_value = {
            "Items": [
                {"user_id": "user1", "timestamp": "2025-01-01T00:00:00Z"},
                {"user_id": "user1", "timestamp": "2025-01-02T00:00:00Z"},
            ]
        }
        mock_batch_writer = MagicMock()
        mock_table.batch_writer.return_value.__enter__ = MagicMock(return_value=mock_batch_writer)
        mock_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        mock_boto3.resource.return_value = mock_resource
        mock_boto3.dynamodb.conditions.Key.return_value.eq.return_value = "mock_condition"

        from memory.long_term_memory import LongTermMemory

        mem = LongTermMemory(table_name="test-table", region="us-east-1")
        count = mem.clear_user_memories(user_id="user1")

        assert count == 2

    @patch("memory.long_term_memory.boto3")
    def test_compute_expiry(self, mock_boto3):
        """Test TTL computation."""
        mock_resource = MagicMock()
        mock_resource.Table.return_value = MagicMock()
        mock_boto3.resource.return_value = mock_resource

        from memory.long_term_memory import LongTermMemory

        mem = LongTermMemory(table_name="test-table", region="us-east-1", ttl_days=30)
        expiry = mem._compute_expiry()

        expected_min = int(time.time()) + (30 * 86400) - 5  # 5 second tolerance
        expected_max = int(time.time()) + (30 * 86400) + 5
        assert expected_min <= expiry <= expected_max
