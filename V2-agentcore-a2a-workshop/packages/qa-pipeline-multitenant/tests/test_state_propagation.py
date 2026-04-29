# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Dev3 LangGraph state + orchestrator tenant guard."""

from __future__ import annotations

import pytest
from state import QAState, TenantContext, require_tenant, build_initial_state


def test_require_tenant_happy_path():
    state: QAState = {
        "tenant": {"tenant_id": "kolon", "config": {}, "request_id": "req-1"},
        "transcript": "hi",
    }
    ctx = require_tenant(state)
    assert ctx["tenant_id"] == "kolon"


def test_require_tenant_missing_raises():
    with pytest.raises(ValueError, match="tenant"):
        require_tenant({})


def test_require_tenant_empty_tid_raises():
    state: QAState = {"tenant": {"tenant_id": "", "config": {}, "request_id": "r"}}
    with pytest.raises(ValueError, match="tenant_id"):
        require_tenant(state)


def test_build_initial_state_seeds_tenant_with_explicit_config():
    state = build_initial_state(
        tenant_id="kolon",
        tenant_config={"display_name": "KOLON"},
        request_id="req-42",
        transcript="안녕하세요",
    )
    assert state["tenant"]["tenant_id"] == "kolon"
    assert state["tenant"]["config"]["display_name"] == "KOLON"
    assert state["tenant"]["request_id"] == "req-42"
    assert state["transcript"] == "안녕하세요"


def test_qastate_tenant_field_is_optional_typeddict_total_false():
    # QAState uses total=False so dict constructor should accept partial dicts
    state: QAState = {"transcript": "x"}
    assert "tenant" not in state
