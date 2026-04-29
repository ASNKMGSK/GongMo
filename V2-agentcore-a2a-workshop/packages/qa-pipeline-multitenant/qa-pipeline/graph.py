# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# =============================================================================
# Multi-tenant QA 평가 파이프라인 그래프 정의 모듈
# =============================================================================
# Hub-and-Spoke(Supervisor) 패턴. orchestrator가 중심 허브로 동작하며
# 상태 기반으로 다음 노드를 라우팅합니다.
#
# [파이프라인 실행 순서 — 절대 변경 금지]
#   전처리:  dialogue_parser
#   Phase A: greeting, understanding, courtesy, incorrect_check, mandatory (5개 병렬)
#   Phase B1: scope, work_accuracy (2개 병렬)
#   Phase B2: proactiveness (단독)
#   Phase C: consistency_check || score_validation (2개 병렬, 교차 검증)
#   보고서:  report_generator (gate 없음 — 문제는 보고서에 기술)
#
# Multi-tenant 델타: orchestrator_node 진입 시 state["tenant"] 존재를 검증한다.
# 누락 시 즉시 ValueError 발생 — Dev1 라우터가 TenantContext 를 반드시 주입.
# =============================================================================

"""
QA Evaluation LangGraph pipeline — Supervisor-pattern StateGraph (multi-tenant).

Identical runtime shape to the single-tenant original. The only graph-level
change is the tenant guard inside orchestrator_node (see nodes/orchestrator.py).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any


_PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Send  # noqa: E402
from nodes import NODE_REGISTRY  # noqa: E402
from nodes.orchestrator import orchestrator_node  # noqa: E402
from nodes.skills.node_context import NodeContext  # noqa: E402
from state import QAState  # noqa: E402


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 노드 함수 레지스트리 (nodes/__init__.py 가 공식 출처)
# ---------------------------------------------------------------------------

_NODE_FUNCTIONS: dict[str, Any] = dict(NODE_REGISTRY)


# ---------------------------------------------------------------------------
# 추적 래퍼 — completed_nodes + node_timings + node_traces 자동 기록
# ---------------------------------------------------------------------------


def _capture_node_input(name: str, state: QAState) -> dict[str, Any]:
    """노드 실행 전 관련 입력 상태를 스냅샷한다 (트레이스용).

    멀티테넌트: tenant_id 를 스냅샷 상단에 포함시켜 trace 필터링에 활용.
    """
    tenant = state.get("tenant") or {}
    inp: dict[str, Any] = {
        "consultation_type": state.get("consultation_type", ""),
        "tenant_id": tenant.get("tenant_id", ""),
        "request_id": tenant.get("request_id", ""),
    }

    transcript = state.get("transcript", "")
    if transcript:
        inp["transcript_length"] = len(transcript)

    if name == "dialogue_parser":
        inp["transcript_preview"] = transcript[:500] + ("…" if len(transcript) > 500 else "")
        return inp

    assignments = state.get("agent_turn_assignments") or {}
    if name in assignments:
        a = assignments[name]
        turns = a.get("turns", [])
        inp["assigned_turns_count"] = len(turns)
        text_preview = a.get("text", "")
        inp["assigned_text_preview"] = text_preview[:300] + ("…" if len(text_preview) > 300 else "")
        inp["turns"] = [
            {"turn_id": t.get("turn_id"), "speaker": t.get("speaker", ""), "text": t.get("text", "")[:100]}
            for t in turns[:15]
        ]

    for key in ("intent_summary", "accuracy_verdict", "flags"):
        val = state.get(key)
        if val:
            inp[key] = val

    dl = state.get("deduction_log") or []
    if dl:
        inp["deduction_log_count"] = len(dl)

    if name in ("consistency_check", "report_generator"):
        evals = state.get("evaluations") or []
        inp["evaluations_count"] = len(evals)
        inp["evaluations_summary"] = [
            {
                "agent_id": e.get("agent_id", ""),
                "item": (e.get("evaluation") or {}).get("item_number"),
                "score": (e.get("evaluation") or {}).get("score"),
            }
            for e in evals[:25]
        ]
    if name == "report_generator" and state.get("verification"):
        inp["verification"] = state["verification"]

    return inp


def _sanitize_trace_output(result: dict[str, Any]) -> dict[str, Any]:
    """결과에서 내부 추적 필드를 제외하고, 긴 문자열을 잘라낸다."""
    _EXCLUDE = {"completed_nodes", "node_timings", "node_traces"}

    def _trim(obj: Any, depth: int = 0) -> Any:
        if depth > 5:
            return "…"
        if isinstance(obj, str):
            return obj[:2000] + ("…" if len(obj) > 2000 else "")
        if isinstance(obj, list):
            return [_trim(item, depth + 1) for item in obj]
        if isinstance(obj, dict):
            return {k: _trim(v, depth + 1) for k, v in obj.items()}
        return obj

    return {k: _trim(v) for k, v in result.items() if k not in _EXCLUDE}


def _record_trace(name: str, t0: float, input_snapshot: dict[str, Any], result: Any) -> dict[str, Any]:
    """Normalize *result* to a dict and attach tracking fields in place."""
    import time

    elapsed = round(time.time() - t0, 2)
    logger.info("[TIMING] %s: %ss tenant=%s", name, elapsed, input_snapshot.get("tenant_id", ""))
    if not isinstance(result, dict):
        result = {}
    result.setdefault("completed_nodes", [])
    result["completed_nodes"] = result["completed_nodes"] + [name]
    result["node_timings"] = [{"node": name, "elapsed": elapsed, "tenant_id": input_snapshot.get("tenant_id", "")}]
    result["node_traces"] = [
        {
            "node": name,
            "elapsed": elapsed,
            "tenant_id": input_snapshot.get("tenant_id", ""),
            "input": input_snapshot,
            "output": _sanitize_trace_output(result),
        }
    ]
    return result


def _accepts_ctx(fn) -> bool:
    """노드 함수가 두 번째 인자 ``ctx: NodeContext`` 를 받는지 검사한다."""
    import inspect

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    params = [
        p
        for p in sig.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY)
    ]
    return len(params) >= 2


def _format_exc() -> str:
    """Return a concise single-line traceback of the current exception."""
    import traceback

    return traceback.format_exc().strip().rsplit("\n", 1)[-1]


def _make_tracked_node(name: str, fn):
    """Wrap *fn* so it appends *name* to ``completed_nodes`` and captures I/O trace."""
    import asyncio
    import time
    from nodes.llm import LLMTimeoutError

    accepts_ctx = _accepts_ctx(fn)

    if asyncio.iscoroutinefunction(fn):

        async def _wrapped(state: QAState) -> dict[str, Any]:
            input_snapshot = _capture_node_input(name, state)
            t0 = time.time()
            try:
                if accepts_ctx:
                    result = await fn(state, NodeContext.from_state(state))
                else:
                    result = await fn(state)
            except LLMTimeoutError:
                raise
            except Exception:
                logger.exception("Node '%s' raised an exception", name)
                result = {"error": f"Node '{name}' failed: {_format_exc()}"}
            return _record_trace(name, t0, input_snapshot, result)

    else:

        def _wrapped(state: QAState) -> dict[str, Any]:
            input_snapshot = _capture_node_input(name, state)
            t0 = time.time()
            try:
                if accepts_ctx:
                    result = fn(state, NodeContext.from_state(state))
                else:
                    result = fn(state)
            except LLMTimeoutError:
                raise
            except Exception:
                logger.exception("Node '%s' raised an exception", name)
                result = {"error": f"Node '{name}' failed: {_format_exc()}"}
            return _record_trace(name, t0, input_snapshot, result)

    _wrapped.__name__ = f"{name}_tracked"
    _wrapped.__doc__ = fn.__doc__
    return _wrapped


# ---------------------------------------------------------------------------
# Supervisor 라우팅 함수
# ---------------------------------------------------------------------------


_BASE_FIELDS: set[str] = {
    # 멀티테넌트: tenant 는 모든 노드가 반드시 받아야 하는 필드
    "tenant",
    "transcript",
    "consultation_type",
    "customer_id",
    "session_id",
    "llm_backend",
    "bedrock_model_id",
    "agent_turn_assignments",
    "current_phase",
    "next_node",
    "parallel_targets",
    "completed_nodes",
    "node_timings",
    "node_traces",
    "error",
}

_EXTRA_FIELDS_PER_NODE: dict[str, set[str]] = {
    "dialogue_parser": set(),
    "greeting": set(),
    "understanding": set(),
    "courtesy": set(),
    "incorrect_check": set(),
    "mandatory": {"rules"},
    "scope": {"rules", "intent_summary"},
    "work_accuracy": {"rules", "intent_summary"},
    "proactiveness": {"rules", "intent_summary", "accuracy_verdict"},
    "consistency_check": {"evaluations", "deduction_log", "intent_summary", "accuracy_verdict", "flags"},
    "score_validation": {"evaluations"},
    "report_generator": {"evaluations", "verification", "score_validation", "flags"},
}


def _select_state_for_node(node: str, state: QAState) -> dict[str, Any]:
    """Build a minimal state copy containing only the fields *node* needs.

    Multi-tenant: `tenant` is always included (it's in _BASE_FIELDS) so every
    Send() fan-out carries the tenant context into the target node's state.
    """
    keys = _BASE_FIELDS | _EXTRA_FIELDS_PER_NODE.get(node, set())
    return {k: state[k] for k in keys if k in state}


def _route_from_orchestrator(state: QAState) -> str | list[Send]:
    """orchestrator 출력을 분석하여 다음 노드(들)를 결정합니다."""
    next_node = state.get("next_node", "__end__")

    parallel_targets = state.get("parallel_targets") or []
    if parallel_targets:
        return [Send(node, _select_state_for_node(node, state)) for node in parallel_targets]

    if next_node in ("__end__", "END", ""):
        return END

    return next_node


_CONDITIONAL_TARGETS: dict[str, str] = {name: name for name in _NODE_FUNCTIONS}
_CONDITIONAL_TARGETS[END] = END


# ---------------------------------------------------------------------------
# 그래프 빌더
# ---------------------------------------------------------------------------


def build_graph():
    """Hub-and-Spoke 패턴 QA 파이프라인 그래프를 구성하고 컴파일합니다.

    Runtime 순서는 단일테넌트 원본과 동일 — 순서/병렬성 변경 없음.
    orchestrator_node 가 진입 시점에 state["tenant"] 를 검증한다.
    """
    builder = StateGraph(QAState)

    # orchestrator (Supervisor) — 추적 래퍼 미적용 (tenant 가드가 진입부에 있음)
    builder.add_node("orchestrator", orchestrator_node)

    # 나머지 노드 — 추적 래퍼 적용
    for name, fn in _NODE_FUNCTIONS.items():
        builder.add_node(name, _make_tracked_node(name, fn))

    # 고정 엣지: START → orchestrator, 모든 노드 → orchestrator 복귀
    builder.add_edge(START, "orchestrator")
    for name in _NODE_FUNCTIONS:
        builder.add_edge(name, "orchestrator")

    # 조건부 엣지: orchestrator → 동적 라우팅
    builder.add_conditional_edges("orchestrator", _route_from_orchestrator, _CONDITIONAL_TARGETS)

    graph = builder.compile()
    logger.info(
        "Multi-tenant QA pipeline graph compiled (Supervisor pattern): "
        "orchestrator hub with %d target nodes. "
        "Runtime order (unchanged from single-tenant): dialogue_parser -> "
        "Phase A (greeting || understanding || courtesy || incorrect_check || mandatory) -> "
        "Phase B1 (scope || work_accuracy) -> Phase B2 (proactiveness) -> "
        "Phase C (consistency_check || score_validation) -> report_generator -> END.",
        len(_NODE_FUNCTIONS),
    )
    return graph
