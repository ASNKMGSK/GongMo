# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-invocation context dataclass injected by the graph wrapper into
evaluation nodes.

Multi-tenant delta
------------------
state["tenant"] 필드(TenantContext)에서 tenant_id/config/request_id 를 뽑아
NodeContext 에 포함시켜 각 노드가 read-only 로 접근할 수 있도록 한다.
프롬프트 로드 시 `load_prompt(key, tenant_id=ctx.tenant_id, backend=...)`
호출 패턴을 권장한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    # ---- 멀티테넌트 필드 (신규) -----------------------------------------
    tenant_id: str = ""
    tenant_config: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> NodeContext:
        """Build a NodeContext from a LangGraph state dict.

        Extracts `state["tenant"]` (TenantContext) into top-level fields.
        tenant 필드 누락 시 빈 문자열/딕셔너리 기본값이 들어가지만, graph.py
        orchestrator_node 가 진입 시점에 이미 검증하므로 여기선 방어적으로만
        처리한다 (정상 흐름에서는 항상 채워져 있음).
        """
        tenant = state.get("tenant") or {}
        return cls(
            transcript=state.get("transcript", ""),
            consultation_type=state.get("consultation_type", "general"),
            llm_backend=state.get("llm_backend"),
            bedrock_model_id=state.get("bedrock_model_id"),
            session_id=state.get("session_id", ""),
            customer_id=state.get("customer_id", ""),
            state=state,
            tenant_id=tenant.get("tenant_id", ""),
            tenant_config=dict(tenant.get("config") or {}),
            request_id=tenant.get("request_id", ""),
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


def tenant_id_from_state(state: dict[str, Any]) -> str:
    """Helper: extract tenant_id from state. Returns "" if absent.

    Callers that need a string-only tenant_id (for metadata attachment or
    load_prompt kwargs) should prefer this over `NodeContext.from_state(...)`
    when they already hold `state` and don't need the full ctx object.
    """
    tenant = state.get("tenant") or {}
    return tenant.get("tenant_id", "") or ""
