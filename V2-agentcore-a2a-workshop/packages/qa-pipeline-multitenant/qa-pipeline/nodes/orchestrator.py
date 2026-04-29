# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# =============================================================================
# 오케스트레이터 (Supervisor) 노드
# =============================================================================
# 이 모듈은 QA 파이프라인의 중앙 라우팅 허브(Hub-and-Spoke 패턴)를 담당한다.
# LLM 호출 없이 순수 상태 기반 분기 로직만으로 다음 실행 노드를 결정한다.
#
# [핵심 역할]
# - 현재 상태(phase)를 검사하여 다음에 실행할 노드(단일 또는 병렬)를 반환
# - LangGraph Send API를 통한 병렬 팬아웃(parallel fan-out) 지원
# - 그래프의 조건부 엣지 루프에서 반복 호출되며 전체 워크플로우를 조율
#
# [페이즈 전환 흐름 — 절대 순서 변경 금지]
# init → dialogue_parser → dp_done → phase_a (5개 병렬) → phase_b1 (2개 병렬) → phase_b2 (1개)
#   → phase_c (consistency_check || score_validation, 2개 병렬) → reporting → complete
# =============================================================================

"""
Orchestrator (Supervisor) node — pure routing logic, no LLM calls.

Hub-and-spoke pattern with Send-based parallel fan-out: the orchestrator
is called repeatedly via a conditional-edge loop in the graph.  Each
invocation inspects the current state to determine the next action,
returning either a single ``next_node`` or a ``parallel_targets`` list
for concurrent dispatch via the LangGraph Send API.
"""

from __future__ import annotations

import logging
from state import QAState
from typing import Any


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default execution plan
# ---------------------------------------------------------------------------
# 기본 실행 계획: 3-Phase 파이프라인
# Phase A(독립 5개 병렬) → Phase B1(의존 2개 병렬) → Phase B2(의존 1개) → Phase C(검증) → 보고서

DEFAULT_EXECUTION_PLAN: list[dict[str, Any]] = [
    # Phase A: 독립 평가 (5개 병렬) — intent_summary/flags 생산
    {
        "phase": "phase_a",
        "agents": [
            "greeting",          # 인사 예절 (#1-#2, 10점) — Skills only, LLM 0회
            "understanding",     # 경청 및 소통 (#3-#5, 15점)
            "courtesy",          # 언어 표현 (#6-#7, 10점)
            "incorrect_check",   # 개인정보 보호 (#17-#18, 10점) — Skills only, LLM 0회
            "mandatory",         # 니즈 파악 (#8-#9, 10점) + intent_summary 생산
        ],
    },
    # Phase B-1: 의존 평가 (2개 병렬) — intent_summary 읽기, accuracy_verdict 쓰기
    {
        "phase": "phase_b1",
        "agents": [
            "scope",             # 설명력 및 전달력 (#10-#11, 15점) — intent_summary 읽기
            "work_accuracy",     # 업무 정확도 (#15-#16, 15점) — intent_summary 읽기, accuracy_verdict 쓰기
        ],
    },
    # Phase B-2: 의존 평가 (1개) — intent_summary + accuracy_verdict 읽기
    {
        "phase": "phase_b2",
        "agents": [
            "proactiveness",     # 적극성 (#12-#14, 15점) — accuracy_verdict 참조
        ],
    },
    # Phase C: 교차 검증 (2개 병렬) — 일관성 검증 + 점수 산술 검증
    {"phase": "phase_c", "agents": ["consistency_check", "score_validation"]},
    # 보고서 단계
    {"phase": "reporting", "agents": ["report_generator"]},
]

# 노드 이름 → evaluations 리스트에서 사용하는 agent_id 부분문자열 매핑
# evaluations에 저장된 agent_id 값과 노드 이름이 다른 경우를 처리
# (예: "proactiveness" 노드의 결과는 agent_id에 "proactiveness"로 저장됨)
_NODE_TO_AGENT_ID: dict[str, str] = {
    "greeting": "greeting",           # 인사 예절 (#1-#2)
    "understanding": "understanding",  # 경청 및 소통 (#3-#5)
    "courtesy": "courtesy",           # 언어 표현 (#6-#7)
    "mandatory": "mandatory",         # 니즈 파악 (#8-#9)
    "scope": "scope",                 # 설명력 및 전달력 (#10-#11)
    "proactiveness": "proactiveness",  # 적극성 (#12-#14)
    "work_accuracy": "work-accuracy", # 업무 정확도 (#15-#16)
    "incorrect_check": "incorrect-check",  # 개인정보 보호 (#17-#18)
}

# 페이즈 전이 테이블 — (현재 페이즈가 모두 완료되면 다음 페이즈로 이동)
# 절대 순서 변경 금지. 순차를 병렬로 바꾸지 말 것.
PHASE_TRANSITIONS: list[tuple[str, str]] = [
    ("dp_done", "phase_a"),
    ("phase_a", "phase_b1"),
    ("phase_b1", "phase_b2"),
    ("phase_b2", "phase_c"),
]


# ---------------------------------------------------------------------------
# Completion detection helpers
# ---------------------------------------------------------------------------


def _is_node_completed(node: str, state: QAState) -> bool:
    """Check whether *node* has already produced its output in state."""
    if node == "dialogue_parser":
        return state.get("parsed_dialogue") is not None
    if node == "retrieval":
        return state.get("rules") is not None
    if node == "consistency_check":
        return state.get("verification") is not None
    if node == "score_validation":
        return state.get("score_validation") is not None
    if node == "report_generator":
        return state.get("report") is not None

    agent_id_substr = _NODE_TO_AGENT_ID.get(node)
    if agent_id_substr is None:
        return False

    evaluations = state.get("evaluations") or []
    return any(agent_id_substr in (e.get("agent_id") or "") for e in evaluations)


def _get_execution_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract execution_plan from the task planner output, or use default."""
    return plan.get("execution_plan", DEFAULT_EXECUTION_PLAN)


def _get_agents_for_phase(execution_plan: list[dict[str, Any]], phase: str) -> list[str]:
    """Return the agent list for a given phase from the execution plan."""
    for step in execution_plan:
        if step.get("phase") == phase:
            return step.get("agents", [])
    return []


def _incomplete_agents(execution_plan: list[dict[str, Any]], phase: str, state: QAState) -> list[str]:
    """Return the subset of *phase* agents that are not yet completed."""
    return [a for a in _get_agents_for_phase(execution_plan, phase) if not _is_node_completed(a, state)]


def _dispatch_phase(phase: str, incomplete: list[str], session_id: str, phase_from: str) -> dict[str, Any]:
    """Build the orchestrator response dict for dispatching *phase*.

    Single-agent phases use ``next_node``; multi-agent phases use
    ``parallel_targets`` so the graph router fans out via Send API.
    """
    logger.info(
        "phase_transition",
        extra={
            "session_id": session_id,
            "phase_from": phase_from,
            "phase_to": phase,
            "parallel_targets": incomplete,
        },
    )
    if len(incomplete) == 1:
        return {"current_phase": phase, "next_node": incomplete[0], "parallel_targets": []}
    return {"current_phase": phase, "next_node": "__parallel__", "parallel_targets": incomplete}


# ---------------------------------------------------------------------------
# Main orchestrator node
# ---------------------------------------------------------------------------


def orchestrator_node(state: QAState) -> dict[str, Any]:
    """Determine the next node(s) to execute.

    Multi-tenant: validates state["tenant"] at entry. Missing tenant_id causes
    an immediate ValueError — the /evaluate router must inject TenantContext
    before calling `graph.invoke(...)`. This is the single defensive gate
    against cross-tenant leakage inside the graph.
    """
    # ---- 멀티테넌트 가드 — 진입 시 tenant 검증 (누락 시 즉시 실패) -----------
    tenant = state.get("tenant")
    if not tenant or not tenant.get("tenant_id"):
        raise ValueError(
            "QAState missing 'tenant' context. The /evaluate router must populate "
            "state['tenant'] = {'tenant_id', 'config', 'request_id'} before graph "
            "invocation. See packages/qa-pipeline-multitenant/docs/STATE_MIGRATION.md."
        )

    phase = state.get("current_phase", "init")
    error = state.get("error")
    session_id = state.get("session_id", "")

    logger.debug(
        "orchestrator_node: tenant=%s phase=%s",
        tenant.get("tenant_id"), phase,
    )

    # 치명적 에러 발생 시 평가를 건너뛰고 검증/보고서 단계로 즉시 이동 (빠른 종료)
    if error:
        logger.warning("orchestrator_node: error detected (%s), fast-forwarding", error)
        execution_plan = _get_execution_plan(state.get("plan") or {})
        incomplete_c = _incomplete_agents(execution_plan, "phase_c", state)
        if incomplete_c:
            return _dispatch_phase("phase_c", incomplete_c, session_id, phase)
        if not _is_node_completed("report_generator", state):
            return _dispatch_phase("reporting", ["report_generator"], session_id, phase)
        return {"current_phase": "complete", "next_node": "__end__", "parallel_targets": []}

    execution_plan = _get_execution_plan(state.get("plan") or {})

    # ----- init → dialogue_parser 실행 ---------------------------------------
    if phase == "init":
        if _is_node_completed("dialogue_parser", state):
            phase = "dp_done"
        else:
            return _dispatch_phase("init", ["dialogue_parser"], session_id, phase)

    # ----- 순차 페이즈 전이 (테이블 기반) -------------------------------------
    # 각 전이는 (완료 대기 중인 페이즈 → 다음 페이즈) 순서로 정의됨.
    # 현재 페이즈가 아직 완료되지 않았으면 남은 agent 들을 재디스패치,
    # 완료되었으면 다음 페이즈로 진입.
    for cur_phase, next_phase in PHASE_TRANSITIONS:
        if phase != cur_phase:
            continue
        # dp_done 은 phase_a 시작 신호 — "현재" 대기 집합은 phase_a 자체.
        # 그 외 (phase_a/b1/b2) 는 자기 페이즈 완료 후 다음 페이즈로.
        if cur_phase == "dp_done":
            incomplete_next = _incomplete_agents(execution_plan, next_phase, state)
            if incomplete_next:
                return _dispatch_phase(next_phase, incomplete_next, session_id, phase)
            phase = next_phase
            continue
        incomplete_cur = _incomplete_agents(execution_plan, cur_phase, state)
        if incomplete_cur:
            return _dispatch_phase(cur_phase, incomplete_cur, session_id, phase)
        incomplete_next = _incomplete_agents(execution_plan, next_phase, state)
        if incomplete_next:
            return _dispatch_phase(next_phase, incomplete_next, session_id, phase)
        phase = next_phase

    # ----- phase_c → reporting (Gate 없음) -----------------------------------
    # consistency_check / score_validation 결과와 무관하게 항상 report_generator 진행.
    # 문제가 있으면 보고서에 별도 섹션으로 기술.
    if phase == "phase_c":
        incomplete_c = _incomplete_agents(execution_plan, "phase_c", state)
        if incomplete_c:
            return _dispatch_phase("phase_c", incomplete_c, session_id, phase)

        verification = state.get("verification") or {}
        verification_data = verification.get("verification", verification)
        is_consistent = verification_data.get("is_consistent", False)

        score_val = state.get("score_validation") or {}
        score_val_data = score_val.get("validation", score_val)
        score_passed = score_val_data.get("passed", False)

        logger.debug(
            "Phase C complete — is_consistent=%s, score_passed=%s",
            is_consistent, score_passed,
        )
        return _dispatch_phase("reporting", ["report_generator"], session_id, phase)

    # ----- reporting → complete ----------------------------------------------
    if phase == "reporting":
        if not _is_node_completed("report_generator", state):
            return _dispatch_phase("reporting", ["report_generator"], session_id, phase)
        return {"current_phase": "complete", "next_node": "__end__", "parallel_targets": []}

    # ----- complete (또는 알 수 없는 페이즈) → 종료 --------------------------
    return {"current_phase": "complete", "next_node": "__end__", "parallel_targets": []}
