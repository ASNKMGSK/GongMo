"""
Unit tests for Short-Term Memory (Sliding Window + AgentCore Memory Hooks).

Tests the in-memory SlidingWindowMemory and the ShortTermMemoryHooks
that integrate with the AgentCore Memory service.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ORCH_DIR = Path(__file__).parent.parent.parent / "packages" / "agentcore-agents" / "orchestrator-agent"
if str(ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(ORCH_DIR))

from memory.short_term_memory import SlidingWindowMemory


class TestSlidingWindowMemory:
    """Tests for the in-memory SlidingWindowMemory."""

    def test_init_default(self):
        """Test default initialization."""
        mem = SlidingWindowMemory()
        assert len(mem) == 0
        assert mem.max_size == 20

    def test_init_custom_size(self):
        """Test initialization with custom window size."""
        mem = SlidingWindowMemory(max_size=5)
        assert mem.max_size == 5

    def test_add_message(self):
        """Test adding a single message."""
        mem = SlidingWindowMemory()
        mem.add("user", "Hello")
        assert len(mem) == 1
        messages = mem.get_messages()
        assert messages[0] == {"role": "user", "content": "Hello"}

    def test_add_multiple_messages(self):
        """Test adding multiple messages."""
        mem = SlidingWindowMemory()
        mem.add("user", "Question 1")
        mem.add("assistant", "Answer 1")
        mem.add("user", "Question 2")
        assert len(mem) == 3

    def test_sliding_window_trimming(self):
        """Test that messages are trimmed when exceeding max_size."""
        mem = SlidingWindowMemory(max_size=3)
        mem.add("user", "Message 1")
        mem.add("assistant", "Message 2")
        mem.add("user", "Message 3")
        mem.add("assistant", "Message 4")  # Should trigger trimming

        assert len(mem) == 3
        messages = mem.get_messages()
        assert messages[0]["content"] == "Message 2"
        assert messages[-1]["content"] == "Message 4"

    def test_get_messages_returns_copy(self):
        """Test that get_messages returns a copy, not the internal list."""
        mem = SlidingWindowMemory()
        mem.add("user", "Hello")
        messages = mem.get_messages()
        messages.clear()
        assert len(mem) == 1  # Internal list should be unaffected

    def test_format_for_prompt_empty(self):
        """Test prompt formatting with no messages."""
        mem = SlidingWindowMemory()
        result = mem.format_for_prompt()
        assert result == ""

    def test_format_for_prompt_with_messages(self):
        """Test prompt formatting with messages."""
        mem = SlidingWindowMemory()
        mem.add("user", "What is the weather?")
        mem.add("assistant", "The weather in Seoul is sunny.")
        result = mem.format_for_prompt()
        assert "user: What is the weather?" in result
        assert "assistant: The weather in Seoul is sunny." in result

    def test_clear(self):
        """Test clearing all messages."""
        mem = SlidingWindowMemory()
        mem.add("user", "Hello")
        mem.add("assistant", "Hi")
        mem.clear()
        assert len(mem) == 0
        assert mem.get_messages() == []

    def test_len(self):
        """Test __len__ method."""
        mem = SlidingWindowMemory()
        assert len(mem) == 0
        mem.add("user", "A")
        assert len(mem) == 1
        mem.add("user", "B")
        assert len(mem) == 2


class TestShortTermMemoryHooks:
    """Tests for ShortTermMemoryHooks integration with AgentCore Memory."""

    def test_hooks_init(self):
        """Test hooks initialization."""
        from memory.short_term_memory import ShortTermMemoryHooks

        mock_client = MagicMock()
        hooks = ShortTermMemoryHooks(
            memory_client=mock_client,
            memory_id="mem-123",
            actor_id="user-1",
            session_id="sess-1",
            logger=MagicMock(),
        )
        assert hooks.memory_id == "mem-123"
        assert hooks.actor_id == "user-1"
        assert hooks.session_id == "sess-1"

    def test_hooks_register(self):
        """Test hooks registration with a HookRegistry."""
        from memory.short_term_memory import ShortTermMemoryHooks

        mock_client = MagicMock()
        hooks = ShortTermMemoryHooks(
            memory_client=mock_client,
            memory_id="mem-123",
            actor_id="user-1",
            session_id="sess-1",
            logger=MagicMock(),
        )
        mock_registry = MagicMock()
        hooks.register_hooks(mock_registry)
        # Should register at least 2 callbacks (agent_initialized, message_added)
        assert mock_registry.add_callback.call_count >= 2

    def test_on_message_added_stores_message(self):
        """Test that on_message_added persists to AgentCore Memory."""
        from memory.short_term_memory import ShortTermMemoryHooks

        mock_client = MagicMock()
        hooks = ShortTermMemoryHooks(
            memory_client=mock_client,
            memory_id="mem-123",
            actor_id="user-1",
            session_id="sess-1",
            logger=MagicMock(),
        )

        # Create mock event with agent messages
        mock_event = MagicMock()
        mock_event.agent.messages = [
            {"role": "user", "content": "Hello world"}
        ]

        hooks.on_message_added(mock_event)

        mock_client.create_event.assert_called_once()
        call_kwargs = mock_client.create_event.call_args
        assert call_kwargs.kwargs["memory_id"] == "mem-123"
        assert call_kwargs.kwargs["actor_id"] == "user-1"

    def test_on_message_added_handles_list_content(self):
        """Test handling of list-format content in messages."""
        from memory.short_term_memory import ShortTermMemoryHooks

        mock_client = MagicMock()
        hooks = ShortTermMemoryHooks(
            memory_client=mock_client,
            memory_id="mem-123",
            actor_id="user-1",
            session_id="sess-1",
            logger=MagicMock(),
        )

        mock_event = MagicMock()
        mock_event.agent.messages = [
            {"role": "assistant", "content": [{"text": "Response text"}]}
        ]

        hooks.on_message_added(mock_event)
        mock_client.create_event.assert_called_once()

    def test_on_message_added_handles_empty_messages(self):
        """Test graceful handling of empty message list."""
        from memory.short_term_memory import ShortTermMemoryHooks

        mock_client = MagicMock()
        hooks = ShortTermMemoryHooks(
            memory_client=mock_client,
            memory_id="mem-123",
            actor_id="user-1",
            session_id="sess-1",
            logger=MagicMock(),
        )

        mock_event = MagicMock()
        mock_event.agent.messages = []

        hooks.on_message_added(mock_event)
        mock_client.create_event.assert_not_called()
