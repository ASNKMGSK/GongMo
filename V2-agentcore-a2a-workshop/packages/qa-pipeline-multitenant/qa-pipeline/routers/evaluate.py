# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""/evaluate 계열 엔드포인트 — 멀티테넌트 확장.

단일 테넌트 원본(packages/agentcore-agents/qa-pipeline/routers/evaluate.py) 로직을 그대로 유지하면서
- `request.state.tenant_id` 를 LangGraph state 의 `tenant` 필드로 주입 (ARCHITECTURE.md §5)
- 응답 payload 최상위에 `tenant_id` 를 포함 (프론트·감사 로그 용)
만 추가한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from ._tenant_deps import require_tenant_id, tenant_context
from .schemas import EvaluateCsvRequest, EvaluatePentagonRequest, EvaluateRequest
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from state import build_initial_state as _state_build_initial_state
from typing import Any


logger = logging.getLogger(__name__)

router = APIRouter()

PIPELINE_TIMEOUT_SECONDS: float = float(os.environ.get("PIPELINE_TIMEOUT_SECONDS", "600"))

_active_runs: set[asyncio.Task[Any]] = set()


class _track_active_run:
    """Async context manager — 현재 실행 중인 task 를 _active_runs 에 등록/해제."""

    async def __aenter__(self) -> _track_active_run:
        task = asyncio.current_task()
        if task is not None:
            _active_runs.add(task)
            self._task: asyncio.Task[Any] | None = task
        else:
            self._task = None
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._task is not None:
            _active_runs.discard(self._task)


AGENT_ID_GREETING = "greeting-agent"
AGENT_ID_UNDERSTANDING = "understanding-agent"
AGENT_ID_COURTESY = "courtesy-agent"
AGENT_ID_MANDATORY = "mandatory-agent"
AGENT_ID_SCOPE = "scope-agent"
AGENT_ID_PROACTIVENESS = "proactiveness-agent"
AGENT_ID_WORK_ACCURACY = "work-accuracy-agent"
AGENT_ID_INCORRECT_CHECK = "incorrect-check-agent"

NODE_LABELS: dict[str, str] = {
    "orchestrator": "Supervisor 라우팅",
    "dialogue_parser": "대화 파싱",
    "greeting": "인사 예절 (#1~#2)",
    "understanding": "경청 및 소통 (#3~#5)",
    "courtesy": "언어 표현 (#6~#7)",
    "mandatory": "니즈 파악 (#8~#9)",
    "scope": "설명력 및 전달력 (#10~#11)",
    "proactiveness": "적극성 (#12~#14)",
    "work_accuracy": "업무 정확도 (#15~#16)",
    "incorrect_check": "개인정보 보호 (#17~#18)",
    "consistency_check": "일관성 검증 (모순·증거)",
    "score_validation": "점수 산술 검증",
    "report_generator": "QA 리포트 생성",
}

PHASE_LABELS: dict[str, str] = {
    "init": "초기화",
    "dp_done": "대화 파싱 완료",
    "phase_a": "Phase A — 병렬 평가 (5)",
    "phase_b1": "Phase B1 — 병렬 평가 (2)",
    "phase_b2": "Phase B2 — 순차 평가",
    "phase_c": "Phase C — 교차 검증 (일관성 + 점수 산술)",
    "verification": "검증 단계",
    "reporting": "리포트 생성 단계",
    "complete": "완료",
}

_AGENT_ID_TO_NODE: dict[str, str] = {
    AGENT_ID_GREETING: "greeting",
    AGENT_ID_UNDERSTANDING: "understanding",
    AGENT_ID_COURTESY: "courtesy",
    AGENT_ID_MANDATORY: "mandatory",
    AGENT_ID_SCOPE: "scope",
    AGENT_ID_PROACTIVENESS: "proactiveness",
    AGENT_ID_WORK_ACCURACY: "work_accuracy",
    AGENT_ID_INCORRECT_CHECK: "incorrect_check",
}


def _build_initial_state(body: dict[str, Any], tenant_ctx: dict[str, Any]) -> dict[str, Any]:
    """초기 LangGraph state — Dev3 `state.build_initial_state` 위임 (ARCHITECTURE.md §5).

    Dev3 헬퍼는 `tenant` 필드 주입과 QAState 필수 키만 설정하므로 원본 단일 테넌트에서
    사용하던 에그리게이션용 필드들(`evaluations`, `completed_nodes`, `node_timings`, `next_node`)
    은 여기서 빈 컬렉션으로 초기화 후 병합한다. 이렇게 하면 SSE 라우터 / 리포트 합산 로직이
    None 체크 없이 바로 동작한다.
    """
    seed = _state_build_initial_state(
        tenant_id=tenant_ctx["tenant_id"],
        tenant_config=tenant_ctx.get("config") or {},
        request_id=tenant_ctx["request_id"],
        transcript=body.get("transcript", ""),
        consultation_type=body.get("consultation_type", "general"),
        customer_id=body.get("customer_id", "anonymous"),
        session_id=body.get("session_id", str(uuid.uuid4())),
        llm_backend=body.get("llm_backend"),
        bedrock_model_id=body.get("bedrock_model_id"),
    )
    # 집계용 필드 기본값 — Dev3 헬퍼가 None 으로 두는 키에 한해 빈 컬렉션 부여.
    # 원본 single-tenant 의 SSE 이벤트 빌더/리포트 합산 로직이 None 체크 없이 .append()/set()
    # 을 사용하므로 라우터가 빈 컬렉션을 seed 에 보장.
    seed.setdefault("evaluations", [])
    seed.setdefault("completed_nodes", [])
    seed.setdefault("node_timings", [])
    seed.setdefault("next_node", "")
    return seed


def _detect_routing_event(state: dict[str, Any], prev_phase: str, prev_next_node: str) -> dict[str, Any] | None:
    current_phase = state.get("current_phase", "")
    next_node = state.get("next_node", "")
    if current_phase and (current_phase != prev_phase or next_node != prev_next_node):
        phase_label = PHASE_LABELS.get(current_phase, current_phase)
        target_label = NODE_LABELS.get(next_node, next_node) if next_node and next_node != "__end__" else ""
        return {"phase": current_phase, "phase_label": phase_label, "next_node": next_node, "next_label": target_label}
    return None


def _extract_report(final_state: dict[str, Any] | None) -> dict[str, Any]:
    if not final_state:
        return {}
    if final_state.get("error"):
        return {"status": "error", "message": final_state["error"]}
    report = final_state.get("report")
    if report:
        return report
    return {
        "status": "validation_failed"
        if final_state.get("score_validation") or final_state.get("verification")
        else "completed",
        "evaluations": final_state.get("evaluations", []),
        "verification": final_state.get("verification"),
        "score_validation": final_state.get("score_validation"),
    }


def _build_node_status_event(
    node_name: str, node_output_or_state: dict[str, Any], node_timings_list: list[dict[str, Any]]
) -> dict[str, Any]:
    label = NODE_LABELS.get(node_name, node_name)
    node_elapsed = None
    for t in node_timings_list:
        if t.get("node") == node_name:
            node_elapsed = t["elapsed"]
    evt: dict[str, Any] = {"node": node_name, "label": label, "status": "completed"}
    if node_elapsed is not None:
        evt["elapsed"] = node_elapsed

    evaluations = node_output_or_state.get("evaluations") or []
    node_scores: list[dict[str, Any]] = []
    node_errors: list[dict[str, Any]] = []
    for ev in evaluations:
        agent_id = ev.get("agent_id", "")
        matched_node = _AGENT_ID_TO_NODE.get(agent_id)
        if matched_node == node_name:
            e = ev.get("evaluation", ev)
            if e.get("item_number") is not None:
                node_scores.append(
                    {
                        "item_number": e["item_number"],
                        "item_name": e.get("item_name", ""),
                        "score": e.get("score", 0),
                        "max_score": e.get("max_score", 0),
                        "deductions": e.get("deductions", []),
                        "evidence": e.get("evidence", []),
                    }
                )
            if ev.get("status") == "error":
                node_errors.append(
                    {
                        "item_number": e.get("item_number"),
                        "item_name": e.get("item_name", ""),
                        "error_type": ev.get("error_type", "Error"),
                        "error_message": ev.get("message", ""),
                    }
                )
    if node_scores:
        evt["scores"] = node_scores
    if node_errors:
        evt["node_status"] = "error"
        evt["error_info"] = node_errors

    if node_name == "consistency_check":
        verification = node_output_or_state.get("verification")
        if verification:
            evt["verification"] = verification
    if node_name == "score_validation":
        score_val = node_output_or_state.get("score_validation")
        if score_val:
            evt["score_validation"] = score_val

    return evt


async def _run_stream(initial_state: dict[str, Any], request: Request | None = None):
    from server import LLMTimeoutError, _get_compiled_graph

    start = time.time()
    tid = (initial_state.get("tenant") or {}).get("tenant_id", "")
    yield {
        "event": "status",
        "data": json.dumps(
            {"node": "__start__", "label": "파이프라인 시작", "status": "completed", "tenant_id": tid},
            ensure_ascii=False,
        ),
    }

    _run_task = asyncio.current_task()
    if _run_task is not None:
        _active_runs.add(_run_task)

    final_state: dict[str, Any] = {}
    astream_gen = None
    try:
        graph = await _get_compiled_graph()

        prev_completed: set[str] = set()
        prev_phase = "init"
        prev_next_node = ""
        prev_traces_count = 0
        emitted_nodes: set[str] = set()

        astream_gen = graph.astream(initial_state, stream_mode=["updates", "values"])
        deadline = time.monotonic() + PIPELINE_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            try:
                mode, chunk = await asyncio.wait_for(astream_gen.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                break

            if request is not None and await request.is_disconnected():
                logger.info("Client disconnected — aborting pipeline (tenant=%s)", tid)
                break

            if mode == "updates":
                for node_name, node_output in chunk.items():
                    if node_name == "orchestrator" or node_name in emitted_nodes:
                        continue
                    if not isinstance(node_output, dict):
                        continue
                    completed_in_update = node_output.get("completed_nodes") or []
                    if node_name not in completed_in_update:
                        continue
                    emitted_nodes.add(node_name)

                    evt = _build_node_status_event(node_name, node_output, node_output.get("node_timings") or [])
                    yield {"event": "status", "data": json.dumps(evt, ensure_ascii=False)}

                    for trace in node_output.get("node_traces") or []:
                        trace_evt = {**trace, "label": NODE_LABELS.get(trace.get("node", ""), trace.get("node", ""))}
                        yield {"event": "node_trace", "data": json.dumps(trace_evt, ensure_ascii=False)}
                        prev_traces_count += 1

                continue

            state_snapshot = chunk
            final_state = state_snapshot

            routing = _detect_routing_event(state_snapshot, prev_phase, prev_next_node)
            if routing:
                prev_phase = routing["phase"]
                prev_next_node = routing["next_node"]
                yield {"event": "routing", "data": json.dumps(routing, ensure_ascii=False)}

            current_completed = set(state_snapshot.get("completed_nodes") or [])
            newly_in_values = current_completed - prev_completed - emitted_nodes
            prev_completed = current_completed

            for node_name in newly_in_values:
                emitted_nodes.add(node_name)
                evt = _build_node_status_event(node_name, state_snapshot, state_snapshot.get("node_timings") or [])
                yield {"event": "status", "data": json.dumps(evt, ensure_ascii=False)}

            current_traces = state_snapshot.get("node_traces") or []
            if len(current_traces) > prev_traces_count:
                for trace in current_traces[prev_traces_count:]:
                    trace_evt = {**trace, "label": NODE_LABELS.get(trace.get("node", ""), trace.get("node", ""))}
                    yield {"event": "node_trace", "data": json.dumps(trace_evt, ensure_ascii=False)}
                prev_traces_count = len(current_traces)

        elapsed = round(time.time() - start, 2)
        result = _extract_report(final_state)
        result["elapsed_seconds"] = elapsed
        result["node_timings"] = final_state.get("node_timings", [])
        result["tenant_id"] = tid

        yield {"event": "result", "data": json.dumps(result, ensure_ascii=False)}
        yield {"event": "done", "data": json.dumps({"elapsed_seconds": elapsed, "tenant_id": tid})}

    except asyncio.CancelledError:
        logger.warning("Stream cancelled (tenant=%s)", tid)
        raise
    except TimeoutError:
        logger.error("Pipeline timeout after %ss (tenant=%s)", PIPELINE_TIMEOUT_SECONDS, tid)
        try:
            partial = _extract_report(final_state)
        except Exception:
            partial = {}
        yield {
            "event": "error",
            "data": json.dumps(
                {
                    "type": "timeout",
                    "message": f"파이프라인 실행이 {PIPELINE_TIMEOUT_SECONDS}초를 초과했습니다.",
                    "partial_result": partial,
                    "tenant_id": tid,
                },
                ensure_ascii=False,
            ),
        }
    except Exception as e:
        if LLMTimeoutError is not None and isinstance(e, LLMTimeoutError):
            logger.error("LLM timeout: %s (tenant=%s)", e, tid, exc_info=True)
            try:
                partial = _extract_report(final_state)
            except Exception:
                partial = {}
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "type": "timeout",
                        "message": f"LLM 응답 대기 시간이 240초를 초과했습니다. ({e})",
                        "partial_result": partial,
                        "tenant_id": tid,
                    },
                    ensure_ascii=False,
                ),
            }
        else:
            logger.error("Stream error: %s (tenant=%s)", e, tid, exc_info=True)
            try:
                partial = _extract_report(final_state)
            except Exception:
                partial = {}
            yield {
                "event": "error",
                "data": json.dumps(
                    {"type": "error", "message": str(e), "partial_result": partial, "tenant_id": tid},
                    ensure_ascii=False,
                ),
            }
    finally:
        if astream_gen is not None:
            try:
                await astream_gen.aclose()
            except Exception:
                pass
        if _run_task is not None:
            _active_runs.discard(_run_task)


# ---------------------------------------------------------------------------
# POST /evaluate
# ---------------------------------------------------------------------------


async def _evaluate_impl(body: dict[str, Any], tenant_ctx: dict[str, Any]) -> JSONResponse:
    """공통 /evaluate 구현.

    Args:
        body: 요청 body dict (transcript, consultation_type, ...)
        tenant_ctx: `tenant_context(request)` 반환 dict (ARCHITECTURE.md §5)
    """
    from server import _get_compiled_graph

    transcript = body.get("transcript", "").strip()
    if not transcript:
        return JSONResponse(status_code=400, content={"status": "error", "message": "transcript is required."})

    initial_state = _build_initial_state(body, tenant_ctx)
    tid = tenant_ctx.get("tenant_id", "")
    start = time.time()

    async with _track_active_run():
        try:
            graph = await _get_compiled_graph()
            final_state = await asyncio.wait_for(graph.ainvoke(initial_state), timeout=PIPELINE_TIMEOUT_SECONDS)
            elapsed = round(time.time() - start, 2)
            result = _extract_report(final_state)
            result["elapsed_seconds"] = elapsed
            result["tenant_id"] = tid
            return JSONResponse(content=result)
        except TimeoutError:
            logger.error("Pipeline timeout after %ss (tenant=%s)", PIPELINE_TIMEOUT_SECONDS, tid)
            return JSONResponse(
                status_code=504,
                content={
                    "status": "error",
                    "type": "timeout",
                    "message": f"파이프라인 실행이 {PIPELINE_TIMEOUT_SECONDS}초를 초과했습니다.",
                    "tenant_id": tid,
                },
            )
        except Exception as e:
            logger.error("Pipeline error: %s (tenant=%s)", e, tid, exc_info=True)
            return JSONResponse(
                status_code=500, content={"status": "error", "message": str(e), "tenant_id": tid}
            )


@router.post("/evaluate")
async def evaluate(payload: EvaluateRequest, request: Request) -> JSONResponse:
    return await _evaluate_impl(payload.model_dump(), tenant_context(request))


# ---------------------------------------------------------------------------
# POST /evaluate/csv-compatible — 배치/CSV 파이프라인 연계용 (UI 는 미사용)
# ---------------------------------------------------------------------------


@router.post("/evaluate/csv-compatible")
async def evaluate_csv_compatible(payload: EvaluateCsvRequest, request: Request) -> JSONResponse:
    from server import _get_compiled_graph, _lazy_deps, to_csv_compatible

    _lazy_deps()

    body = payload.model_dump()
    tenant_ctx = tenant_context(request)
    tid = tenant_ctx.get("tenant_id", "")

    initial_state = _build_initial_state(
        {
            "transcript": body["CONTENT"],
            "consultation_type": "general",
            "customer_id": body["UID"],
            "session_id": f"{body['ID']}_{body['CALL_SEQ']}",
            "llm_backend": body.get("llm_backend"),
            "bedrock_model_id": body.get("bedrock_model_id"),
        },
        tenant_ctx,
    )

    start = time.time()
    async with _track_active_run():
        try:
            graph = await _get_compiled_graph()
            final_state = await asyncio.wait_for(graph.ainvoke(initial_state), timeout=PIPELINE_TIMEOUT_SECONDS)
            elapsed = round(time.time() - start, 2)

            report_wrap = final_state.get("report", {}) or {}
            report = report_wrap.get("report", {}) or {}
            parsed = final_state.get("parsed_dialogue", {}) or {}
            evaluations = final_state.get("evaluations", []) or []

            result = to_csv_compatible(body, report, parsed, evaluations)
            result["elapsed_seconds"] = elapsed
            result["tenant_id"] = tid
            return JSONResponse(content=result)
        except TimeoutError:
            logger.error(
                "csv-compatible timeout after %ss (tenant=%s, ID=%s)",
                PIPELINE_TIMEOUT_SECONDS,
                tid,
                body.get("ID", ""),
            )
            return JSONResponse(
                status_code=504,
                content={
                    "status": "error",
                    "type": "timeout",
                    "message": f"파이프라인 실행이 {PIPELINE_TIMEOUT_SECONDS}초를 초과했습니다.",
                    "ID": body.get("ID", ""),
                    "tenant_id": tid,
                },
            )
        except Exception as e:
            logger.error("csv-compatible error: %s (tenant=%s)", e, tid, exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": str(e), "ID": body.get("ID", ""), "tenant_id": tid},
            )


# ---------------------------------------------------------------------------
# POST /evaluate/pentagon
# ---------------------------------------------------------------------------


@router.post("/evaluate/pentagon")
async def evaluate_pentagon(payload: EvaluatePentagonRequest, request: Request) -> JSONResponse:
    from server import _lazy_deps, evaluate_pentagon_direct

    _lazy_deps()

    body = payload.model_dump()
    tid = require_tenant_id(request)
    content = (body.get("CONTENT") or "").strip()
    if not content:
        return JSONResponse(status_code=400, content={"status": "error", "message": "CONTENT is required."})

    try:
        result = await evaluate_pentagon_direct(
            content, backend=body.get("llm_backend"), bedrock_model_id=body.get("bedrock_model_id")
        )
        result["ID"] = body.get("ID", "")
        result["CALL_SEQ"] = body.get("CALL_SEQ", "")
        result["CDATE"] = body.get("CDATE", "")
        result["UID"] = body.get("UID", "")
        result["tenant_id"] = tid
        return JSONResponse(content=result)
    except Exception as e:
        logger.error("pentagon-direct error: %s (tenant=%s)", e, tid, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e), "ID": body.get("ID", ""), "tenant_id": tid},
        )


# ---------------------------------------------------------------------------
# GET /evaluate/stream
# ---------------------------------------------------------------------------


@router.get("/evaluate/stream")
async def evaluate_stream(request: Request) -> EventSourceResponse:
    transcript = request.query_params.get("transcript", "").strip()
    if not transcript:

        async def _err():
            yield {"event": "error", "data": json.dumps({"message": "transcript is required."})}

        return EventSourceResponse(_err())

    tenant_ctx = tenant_context(request)

    initial_state = _build_initial_state(
        {
            "transcript": transcript,
            "consultation_type": request.query_params.get("consultation_type", "general"),
            "customer_id": request.query_params.get("customer_id", "anonymous"),
            "session_id": request.query_params.get("session_id", str(uuid.uuid4())),
            "llm_backend": request.query_params.get("llm_backend"),
            "bedrock_model_id": request.query_params.get("bedrock_model_id"),
        },
        tenant_ctx,
    )
    return EventSourceResponse(_run_stream(initial_state, request))


# ---------------------------------------------------------------------------
# POST /evaluate/stream
# ---------------------------------------------------------------------------


@router.post("/evaluate/stream")
async def evaluate_stream_post(payload: EvaluateRequest, request: Request) -> EventSourceResponse:
    body = payload.model_dump()
    transcript = body.get("transcript", "").strip()
    if not transcript:

        async def _err():
            yield {"event": "error", "data": json.dumps({"message": "transcript is required."})}

        return EventSourceResponse(_err())

    tenant_ctx = tenant_context(request)
    initial_state = _build_initial_state(body, tenant_ctx)
    return EventSourceResponse(_run_stream(initial_state, request))
