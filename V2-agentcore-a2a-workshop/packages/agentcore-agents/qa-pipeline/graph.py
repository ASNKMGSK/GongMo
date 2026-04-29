# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""V1 → V2 전환 후 잔존 스텁.

V2 `v2/graph_v2.py` 가 `_make_tracked_node` 래퍼만 재활용하기 때문에,
트레이스 래퍼 및 부속 헬퍼만 이 모듈에 남겨둔다. 기존의 V1 `StateGraph`
빌더(`build_graph`), V1 노드 의존성(`nodes.greeting`, `nodes.understanding`,
... 등 12종) 은 모두 제거됐다.

V2 그래프는 `v2.graph_v2.build_v2_graph()` 를 통해 빌드된다.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any


_PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

from nodes.skills.node_context import NodeContext  # noqa: E402


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 추적 래퍼 — completed_nodes + node_timings + node_traces 자동 기록
# ---------------------------------------------------------------------------


def _capture_node_input(name: str, state: Any) -> dict[str, Any]:
    """노드 실행 전 관련 입력 상태를 스냅샷한다 (트레이스용).

    V1 QAState 에 맞게 설계된 원본 구현을 유지. V2 QAStateV2 에서도
    동일 키가 노출되면 유효하고, 그렇지 않으면 조용히 건너뛴다 (`dict.get` 사용).
    """
    inp: dict[str, Any] = {"consultation_type": state.get("consultation_type", "")}

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
    """Normalize *result* to a dict and attach tracking fields in place.

    Shared by both the async and sync branches of ``_make_tracked_node``
    so only the ``await`` differs between the two.
    """
    import time

    elapsed = round(time.time() - t0, 2)
    logger.info(f"[TIMING] {name}: {elapsed}s")
    if not isinstance(result, dict):
        result = {}
    result.setdefault("completed_nodes", [])
    result["completed_nodes"] = result["completed_nodes"] + [name]
    result["node_timings"] = [{"node": name, "elapsed": elapsed}]
    result["node_traces"] = [
        {"node": name, "elapsed": elapsed, "input": input_snapshot, "output": _sanitize_trace_output(result)}
    ]
    return result


def _accepts_ctx(fn) -> bool:
    """노드 함수가 두 번째 인자 ``ctx: NodeContext`` 를 받는지 검사한다.

    inspect.signature 결과는 호출자(_make_tracked_node) 단계에서 1회만
    수행되어 모듈 레벨에서 캐시된다. 결과 True 면 래퍼가 ``fn(state, ctx)``
    형태로, False 면 ``fn(state)`` 형태로 호출한다.
    """
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
    """Wrap *fn* so it appends *name* to ``completed_nodes`` and captures I/O trace.

    NodeContext 주입: ``fn`` 시그니처가 두 인자(state, ctx) 형태면 매 호출마다
    ``NodeContext.from_state(state)`` 를 만들어 두 번째 인자로 전달한다.
    한 인자(state) 형태의 비평가 노드는 그대로 ``fn(state)`` 로 호출.

    예외 발생 시 ``state["error"]`` 에 에러 메시지를 기록하고 정상 반환하여
    파이프라인이 중단되지 않고 오케스트레이터의 에러 핸들링으로 넘어간다.
    ``LLMTimeoutError`` 는 server.py까지 전파해야 하므로 re-raise한다.
    """
    import asyncio
    import time
    from nodes.llm import LLMTimeoutError

    accepts_ctx = _accepts_ctx(fn)

    if asyncio.iscoroutinefunction(fn):

        async def _wrapped(state: Any) -> dict[str, Any]:
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

        def _wrapped(state: Any) -> dict[str, Any]:
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


__all__ = ["_make_tracked_node"]
