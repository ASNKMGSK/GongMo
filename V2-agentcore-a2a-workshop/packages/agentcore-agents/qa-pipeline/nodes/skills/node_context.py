# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-invocation context dataclass injected by the graph wrapper into
evaluation nodes.

graph.py 의 ``_make_tracked_node`` 래퍼가 매 호출 시 state 에서 공통
필드를 추출해 ``NodeContext`` 인스턴스를 만들고, 노드가 두 번째
인자로 받는다.  노드는 더 이상 5개 핵심 키를 매번 ``state.get(...)``
으로 꺼낼 필요가 없다.

Note
----
``transcript`` 필드는 raw transcript 이다. 평가 노드들은 보통
``assignment.get("text") or state.get("transcript", "")`` 패턴으로
agent_turn_assignments 우선 정책을 따르므로, ctx.transcript 는
fallback 용도로만 쓰거나 그대로 두는 것이 안전하다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class NodeContext:
    """Common per-invocation context passed to evaluation nodes."""

    transcript: str
    consultation_type: str
    llm_backend: str | None
    bedrock_model_id: str | None
    session_id: str
    customer_id: str
    state: dict[str, Any]

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> NodeContext:
        """Build a NodeContext from a LangGraph state dict."""
        return cls(
            transcript=state.get("transcript", ""),
            consultation_type=state.get("consultation_type", "general"),
            llm_backend=state.get("llm_backend"),
            bedrock_model_id=state.get("bedrock_model_id"),
            session_id=state.get("session_id", ""),
            customer_id=state.get("customer_id", ""),
            state=state,
        )


def build_user_message(
    *,
    consultation_type: str,
    transcript: str,
    rules: dict | None = None,
    intent_context: str = "",
    accuracy_context: str = "",
) -> str:
    """Build the common 4-section prefix for LLM evaluation user messages.

    Sections: Consultation Type, optional context blocks, optional rules, Transcript.
    Callers append their own Pre-Analysis and Instructions sections.
    """
    parts = [f"## Consultation Type\n{consultation_type}\n"]
    if intent_context:
        parts.append(f"## 고객 주요 문의\n{intent_context}\n")
    if accuracy_context:
        parts.append(f"## 업무정확도 맥락\n{accuracy_context}\n")
    if rules:
        rules_str = json.dumps(rules, ensure_ascii=False)
        parts.append(f"## QA Rules and Criteria\n{rules_str}\n")
    parts.append(f"## Transcript\n{transcript}\n")
    return "\n".join(parts)
