# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Multi-tenant QA Pipeline shared state definition.

Extends the single-tenant QAState with a `tenant` field (TenantContext)
carried through every LangGraph node. All nodes read from `state["tenant"]`
but never mutate it — enforcement is documented in ARCHITECTURE.md §5.

Base: packages/agentcore-agents/qa-pipeline/state.py (single-tenant original)
"""

# ---------------------------------------------------------------------------
# 멀티테넌트 확장 사항 (ARCHITECTURE.md 5절):
#
#   - TenantContext TypedDict: tenant_id, config(dict), request_id
#   - QAState 에 `tenant: TenantContext` 추가 (필수)
#   - 모든 노드는 read-only 로 state["tenant"] 접근
#   - 단일 테넌트 원본의 모든 필드는 그대로 유지 (파이프라인 호환성)
# ---------------------------------------------------------------------------

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


# ---------------------------------------------------------------------------
# 테넌트 컨텍스트 — state 에 주입되는 핵심 신규 필드
# ---------------------------------------------------------------------------


class TenantContext(TypedDict):
    """Per-request tenant context propagated through the LangGraph pipeline.

    Fields
    ------
    tenant_id : str
        테넌트 식별자 (영문/숫자/언더스코어). JWT `custom:tenant_id` 에서 유래.
    config : dict
        `TenantConfig.to_dict()` 결과. Dev4 의 프리셋/오버라이드 포함.
        노드는 `config["qa_items_enabled"]`, `config["score_overrides"]`,
        `config["default_models"]`, `config["prompt_overrides_dir"]` 등을 읽는다.
    request_id : str
        요청 추적 ID. 로그/메트릭/감사 로그 상관(correlate)용.
    """

    tenant_id: str
    config: dict[str, Any]
    request_id: str


# ---------------------------------------------------------------------------
# 개별 QA 평가 항목 결과 (채점 노드가 항목 하나를 평가할 때마다 생성)
# ---------------------------------------------------------------------------


class EvaluationResult(TypedDict, total=False):
    """Single QA evaluation item result produced by a scoring node.

    tenant_id 필드는 채점 노드가 LLM 호출 직후 `state["tenant"]["tenant_id"]`
    값을 메타로 첨부하여 audit/trace 에 활용한다.
    """

    status: str  # "success" | "partial" | "error"
    agent_id: str
    evaluation: dict[str, Any]
    # 멀티테넌트 메타 — 채점 노드가 붙인 tenant_id (감사/추적용)
    tenant_id: str


# ---------------------------------------------------------------------------
# 일관성 검증 결과
# ---------------------------------------------------------------------------


class VerificationResult(TypedDict, total=False):
    """Output of the consistency-check node."""

    is_consistent: bool
    needs_human_review: bool
    human_review_reasons: list[str]
    conflicts: list[dict[str, Any]]
    evidence_check: dict[str, Any]
    total_score: int
    max_possible_score: int
    details: str
    tenant_id: str


# ---------------------------------------------------------------------------
# 최종 보고서 구조
# ---------------------------------------------------------------------------


class ReportResult(TypedDict, total=False):
    """Output of the report-generator node."""

    summary: dict[str, Any]
    item_scores: list[dict[str, Any]]
    deductions: list[dict[str, Any]]
    strengths: list[str]
    improvements: list[str]
    coaching_points: list[dict[str, Any]]
    full_report_text: str
    tenant_id: str


# ---------------------------------------------------------------------------
# 메인 그래프 상태
# ---------------------------------------------------------------------------


class QAState(TypedDict, total=False):
    """Shared state flowing through the multi-tenant QA evaluation pipeline.

    Multi-tenant delta
    ------------------
    tenant : TenantContext
        필수 — state 진입 시 Dev1 라우터가 설정. 누락 시 orchestrator_node 가
        즉시 ValueError 로 실패하여 크로스-테넌트 누출을 방지.

    Fields (single-tenant original 유지)
    ------
    transcript, consultation_type, customer_id, session_id,
    llm_backend, bedrock_model_id, rules, evaluations, verification,
    score_validation, report, current_phase, next_node, parallel_targets,
    completed_nodes, node_timings, node_traces, parsed_dialogue,
    agent_turn_assignments, intent_summary, deduction_log, accuracy_verdict,
    flags, error
    """

    # ---- 멀티테넌트 (신규) -------------------------------------------------
    tenant: TenantContext

    # ---- 입력 필드 (파이프라인 진입 시 1회 설정) --------------------------
    transcript: str
    consultation_type: str
    customer_id: str
    session_id: str
    llm_backend: str
    bedrock_model_id: str | None

    # ---- 중간 결과 --------------------------------------------------------
    rules: dict[str, Any]

    # ---- 평가 결과 (operator.add 리듀서) ---------------------------------
    evaluations: Annotated[list[EvaluationResult], operator.add]

    # ---- 평가 후처리 ------------------------------------------------------
    verification: dict[str, Any]
    score_validation: dict[str, Any]
    report: dict[str, Any]

    # ---- 오케스트레이터 라우팅 -------------------------------------------
    current_phase: str
    next_node: str
    parallel_targets: list[str]
    completed_nodes: Annotated[list[str], operator.add]

    # ---- 성능 진단 --------------------------------------------------------
    node_timings: Annotated[list[dict[str, Any]], operator.add]
    node_traces: Annotated[list[dict[str, Any]], operator.add]

    # ---- 전처리 결과 (Dialogue Parser) -----------------------------------
    parsed_dialogue: dict[str, Any]
    agent_turn_assignments: dict[str, Any]

    # ---- 에이전트 간 공유 메모리 -----------------------------------------
    intent_summary: dict[str, Any]
    deduction_log: Annotated[list[dict[str, Any]], operator.add]
    accuracy_verdict: dict[str, Any]
    flags: dict[str, Any]

    # ---- 오케스트레이터 실행 계획 (task_planner 출력) --------------------
    plan: dict[str, Any]

    # ---- 에러 전파 --------------------------------------------------------
    error: str | None


# ---------------------------------------------------------------------------
# 어댑터 / 검증 헬퍼
# ---------------------------------------------------------------------------


def require_tenant(state: QAState) -> TenantContext:
    """Extract TenantContext from state or raise. Call at node entries.

    orchestrator_node 가 그래프 진입부에서 이 함수를 호출하여 tenant 누락
    요청을 즉시 차단한다. 개별 노드는 `state["tenant"]["tenant_id"]` 를
    직접 참조하되, 디버깅/테스트에서는 이 헬퍼를 사용할 수 있다.
    """
    tenant = state.get("tenant")
    if not tenant:
        raise ValueError(
            "QAState is missing required 'tenant' field. "
            "The /evaluate router must inject TenantContext before graph invocation."
        )
    tid = tenant.get("tenant_id")
    if not tid or not isinstance(tid, str):
        raise ValueError(
            f"TenantContext.tenant_id is invalid (got {tid!r}). "
            "Ensure JWT claim 'custom:tenant_id' is set and middleware populates request.state.tenant_id."
        )
    return tenant


def build_initial_state(
    *,
    tenant_id: str,
    request_id: str,
    transcript: str,
    tenant_config: dict[str, Any] | None = None,
    consultation_type: str = "general",
    customer_id: str = "",
    session_id: str = "",
    llm_backend: str | None = None,
    bedrock_model_id: str | None = None,
) -> QAState:
    """Construct a fresh QAState seed for a new evaluation request.

    Tenant config resolution (PL 지침):
      - ``tenant_config`` 명시 주입 시 그대로 사용 (테스트/로컬 개발용)
      - 미지정 시 ``tenant.store.get_config(tenant_id).to_dict()`` 로 자동 조회
        → KeyError 전파 (테넌트 미존재 시 라우터에서 404/403 로 맵핑)

    Dev1 의 /evaluate 라우터가 이 헬퍼를 사용해 state 를 빌드하도록 권장.
    config 는 to_dict() snapshot 이므로 score_overrides 의 키는 string 이다.
    int 키로 복원이 필요하면 `TenantConfig.from_dict(state["tenant"]["config"])` 사용.
    """
    if tenant_config is None:
        # 런타임 경로: DynamoDB + 캐시 조회. 테넌트 미존재면 KeyError.
        from tenant.store import get_config

        cfg = get_config(tenant_id)
        tenant_config = cfg.to_dict()

    # llm_backend / bedrock_model_id 는 None 이어도 키를 항상 포함한다.
    # LLM 노드가 `state["llm_backend"]` / `state["bedrock_model_id"]` 를 직접
    # 참조하며 None 허용 (env var 기본값 사용). 원본 single-tenant state shape
    # 과의 호환성 유지 (Dev1 diff 검증 결과 반영).
    state: QAState = {
        "tenant": {
            "tenant_id": tenant_id,
            "config": dict(tenant_config),
            "request_id": request_id,
        },
        "transcript": transcript,
        "consultation_type": consultation_type,
        "customer_id": customer_id,
        "session_id": session_id,
        "llm_backend": llm_backend,
        "bedrock_model_id": bedrock_model_id,
        "current_phase": "init",
    }
    return state
