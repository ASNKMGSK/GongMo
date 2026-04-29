# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""LangGraph debate_node — Layer 3 다음, Layer 4 전에 선택적으로 실행.

조건:
  - ``QA_DEBATE_ENABLED`` 환경변수가 ``false`` 면 즉시 skip (debates={}).
  - **모든 평가 항목**이 토론 대상 (spread 임계값 게이트 없음).
  - 각 토론 호출은 독립 try — 하나가 실패해도 다른 항목 토론 계속, 평가 전체 중단 없음.

결과:
  - ``state["debates"][item_number] = DebateRecord.model_dump()``
  - 토론으로 확정된 score 가 기존 reconciler 결과와 다를 경우, caller (server) 가 debate_final 이벤트를 프론트에 emit.
  - 기존 evaluations / orchestrator 필드는 그대로 유지 — 동작 변경 최소화.

SSE 콜백은 graph 실행자 (server_v2) 가 ``state["_debate_on_event"]`` 로 주입하면 사용.
run_direct_batch 경로처럼 콜백이 없으면 silent 실행.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from v2.debate.run_debate import run_debate
from v2.debate.schemas import DEFAULT_MAX_ROUNDS, DebateRecord, DebateRequest


logger = logging.getLogger(__name__)


DEFAULT_SPREAD_THRESHOLD = 3
ENV_ENABLED = "QA_DEBATE_ENABLED"
ENV_SPREAD_THRESHOLD = "QA_DEBATE_SPREAD_THRESHOLD"
ENV_MAX_ROUNDS = "QA_DEBATE_MAX_ROUNDS"
ENV_MAX_ITEMS = "QA_DEBATE_MAX_ITEMS"  # 한 상담당 토론 대상 최대 항목 수 (Bedrock quota 보호)
ENV_MAX_PARALLEL = "QA_DEBATE_MAX_PARALLEL"  # 동시 토론 수 (기본 4, Bedrock quota 보호)

# 토론에서 제외할 evaluation_mode:
#  - compliance_based / structural_only : 규정/절차 패턴 판정 — LLM 토론 부적합
#  - skipped                              : 해당 상황 부재 (#5 대기 없음, #7 거절 없음, #13 즉시해결)
#                                           → 평가 자체가 수행 안 된 케이스. 토론할 재료 없음.
#  - unevaluable                          : STT 품질 등 사유로 평가 불가 → 토론 불가
DEBATE_EXCLUDED_MODES: frozenset[str] = frozenset({"compliance_based", "structural_only", "skipped", "unevaluable"})

# 토론에서 제외할 item_number — 평가 구조 자체가 3-페르소나가 필요없는 rule/compliance 기반.
# (사용자 지시 2026-04-24 재확정 — 평가자 3명이 필요한 노드 자체가 아님)
#   #1  : 첫인사 (인사말 + 소속 + 상담사명) — Rule + LLM verify, 고정 구간(도입부) 평가
#   #2  : 끝인사 (추가문의 확인 + 인사말 + 상담사명) — Rule + LLM verify, 고정 구간(종료부)
#   #16 : 필수 안내 이행 — Intent 분류 + 스크립트 매칭 (사실상 rule 기반)
#   #17 : 정보 확인 절차 — compliance_based (절차 준수 플로우 판정)
#   #18 : 정보 보호 준수 — compliance_based + T3 인간 검수 강제 (위반 패턴 탐지)
DEBATE_EXCLUDED_ITEMS: frozenset[int] = frozenset({1, 2, 16, 17, 18})


EventCallback = Callable[[str, dict[str, Any]], None]


def is_debate_enabled() -> bool:
    """``QA_DEBATE_ENABLED`` 환경변수 — 기본 true. ``false``/``0``/``no``/``off`` 시 비활성."""
    v = os.getenv(ENV_ENABLED, "true").strip().lower()
    return v not in ("false", "0", "no", "off", "")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (ValueError, AttributeError):
        return default


def _extract_persona_votes(ev: dict[str, Any]) -> dict[str, int] | None:
    """evaluation dict 에서 persona_votes 꺼냄. 중첩/평면 포맷 모두 지원."""
    inner = ev.get("evaluation") if isinstance(ev.get("evaluation"), dict) else ev
    if not isinstance(inner, dict):
        return None
    votes = inner.get("persona_votes")
    if not isinstance(votes, dict) or not votes:
        return None
    result: dict[str, int] = {}
    for k, v in votes.items():
        if k not in ("strict", "neutral", "loose"):
            continue
        try:
            result[k] = int(v)
        except (TypeError, ValueError):
            continue
    return result or None


def _extract_step_spread(ev: dict[str, Any]) -> int:
    inner = ev.get("evaluation") if isinstance(ev.get("evaluation"), dict) else ev
    if not isinstance(inner, dict):
        return 0
    val = inner.get("persona_step_spread", inner.get("step_spread", 0))
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def _extract_item_meta(ev: dict[str, Any]) -> dict[str, Any]:
    inner = ev.get("evaluation") if isinstance(ev.get("evaluation"), dict) else ev
    return inner if isinstance(inner, dict) else {}


def _fabricate_votes_from_score(meta: dict[str, Any]) -> dict[str, int]:
    """persona_votes 가 없을 때 score 단일값으로 3-페르소나 초기 위치 생성.

    평가 결과에 persona_votes 가 없어도 토론을 시작할 수 있도록 fallback.
    strict/neutral/loose 모두 동일 점수로 시작 → 토론에서 발산/수렴 가능.
    """
    score = meta.get("score")
    try:
        s = int(score) if score is not None else 0
    except (TypeError, ValueError):
        s = 0
    return {"strict": s, "neutral": s, "loose": s}


def _pick_candidates(
    evaluations: list[dict[str, Any]], *, threshold: int, max_items: int
) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, int]]]:
    """토론 대상 평가 항목 선별. (ev, inner_meta, persona_votes) 튜플 리스트.

    **사용자 정책 (2026-04-27 재정의)**:
      - **rule-based / compliance 항목은 토론 제외** (DEBATE_EXCLUDED_ITEMS / EXCLUDED_MODES)
        — 이 항목들은 패턴 매칭 / 규정 준수 판정이라 LLM 토론 부적합. 프론트에도 토론 UI 없음.
      - **나머지 항목은 무조건 토론 진입** — 의견 차이 / spread 무관 (threshold 무시)
      - **단일 persona 모드여도 fabricated votes 로 진입** (단, 위 제외 항목은 그대로 skip)

    파라미터 호환:
      - threshold : 무시 (모든 spread 진입). 호출부 호환 위해 시그니처 유지.
      - max_items : 0 이면 무제한, > 0 이면 상위 N개만.
    """
    cands: list[tuple[int, dict[str, Any], dict[str, Any], dict[str, int]]] = []
    seen_items: set[int] = set()
    for ev in evaluations or []:
        if not isinstance(ev, dict):
            continue
        meta = _extract_item_meta(ev)
        item_no = meta.get("item_number")
        if not isinstance(item_no, int) or item_no in seen_items:
            continue
        seen_items.add(item_no)

        # (1) rule-based 명시적 제외 항목 — 토론 부적합
        if item_no in DEBATE_EXCLUDED_ITEMS:
            logger.info("  ⏭ item=#%s skip — DEBATE_EXCLUDED_ITEMS (rule-based 판정, 토론 부적합)", item_no)
            continue

        # (2) compliance / structural 모드 — 패턴 매칭 기반 판정
        eval_mode = str(meta.get("evaluation_mode") or "").strip()
        if eval_mode in DEBATE_EXCLUDED_MODES:
            logger.info("  ⏭ item=#%s skip (mode=%s) — 규정/절차 기반 판정", item_no, eval_mode)
            continue

        # persona_votes 가 없으면 단일 점수에서 fabricate (3명 동일 점수로 시작 → 페르소나
        # 시스템 프롬프트 차이로 토론 중 발산/수렴 가능)
        votes = _extract_persona_votes(ev)
        if votes is None:
            votes = _fabricate_votes_from_score(meta)
            logger.info("  🎲 item=#%s persona_votes 부재 — single score 로 fabricate: %s", item_no, votes)

        spread = _extract_step_spread(ev)
        cands.append((spread, ev, meta, votes))

    cands.sort(key=lambda x: -x[0])
    if max_items > 0:
        cands = cands[:max_items]
    return [(ev, meta, votes) for _s, ev, meta, votes in cands]


def _build_request(
    *, state: dict[str, Any], meta: dict[str, Any], votes: dict[str, int], max_rounds: int
) -> DebateRequest:
    """evaluation meta + state 에서 DebateRequest 조립."""
    from v2.contracts.rubric import ALLOWED_STEPS, max_score_of

    item_no = int(meta["item_number"])
    allowed = list(ALLOWED_STEPS.get(item_no) or [])
    max_score: int
    if not allowed:
        # 신한 dept items (901-922) 는 V2 rubric ALLOWED_STEPS 에 없으므로 registry 조회.
        try:
            from v2.agents.shinhan_dept.registry import DEPT_NODE_REGISTRY
            for spec in DEPT_NODE_REGISTRY.values():
                for it in spec.get("items", []):
                    if int(it.get("item_number", 0)) == item_no:
                        allowed = list(it.get("allowed_steps") or [])
                        break
                if allowed:
                    break
        except Exception:
            allowed = []
        if not allowed:
            raise ValueError(f"ALLOWED_STEPS missing for item={item_no}")
        max_score = int(allowed[0]) if allowed else 0
    else:
        max_score = max_score_of(item_no)

    return DebateRequest(
        consultation_id=str(state.get("consultation_id") or state.get("session_id") or "unknown"),
        item_number=item_no,
        item_name=str(meta.get("item_name") or f"item_{item_no}"),
        max_score=max_score,
        allowed_steps=allowed,
        transcript=str(state.get("transcript") or ""),
        rag_context=None,  # Phase 2 는 AI 근거로 충분, RAG 컨텍스트는 Phase 3 에서
        ai_evidence=meta.get("evidence") if isinstance(meta.get("evidence"), list) else None,
        ai_judgment=str(meta.get("judgment") or "") or None,
        persona_details=meta.get("persona_details") if isinstance(meta.get("persona_details"), dict) else None,
        initial_positions=votes,
        max_rounds=max_rounds,
        consensus_threshold=0,
    )


def run_debates_for_evaluations(
    *, state: dict[str, Any], evaluations: list[dict[str, Any]], agent_name: str = ""
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """★ Option A — sub-agent 가 평가 후 즉시 호출하는 inline debate runner.

    evaluations (해당 sub-agent 가 평가한 1~3개 item) 를 받아, QA_DEBATE_ENABLED 이면
    각 항목별로 run_debate 를 즉시 실행한다. 결과:
      - debates dict: {item_number: DebateRecord.model_dump()}
      - events list: [{event, data}, ...] — SSE 로 중계할 이벤트 버퍼

    layer2_barrier 대기 없이 sub-agent 단위로 토론이 진행되므로, 먼저 끝난 sub-agent
    의 토론이 다른 sub-agent 대기 없이 즉시 시작/종료 가능. server_v2 가
    `state["_debate_on_event"]` 를 주입해놨으면 실시간 SSE 전파도 함께 일어남.

    sub-agent 내부 병렬 토론은 하지 않음 — LangGraph Send fan-out 으로 이미
    sub-agent 자체가 다른 sub-agent 와 병렬. sub-agent 내부에서 item 1~3개는 직렬로 충분.
    """
    if not is_debate_enabled():
        return {}, []
    if not evaluations:
        return {}, []

    max_rounds = _int_env(ENV_MAX_ROUNDS, DEFAULT_MAX_ROUNDS)
    # ★ 2026-04-27 사용자 정책: 무조건 토론 (threshold=0, 의견 차이 무관)
    candidates = _pick_candidates(evaluations, threshold=0, max_items=0)
    if not candidates:
        logger.info("  ⏭ [inline-debate agent=%s] 후보 없음 — 평가 결과가 비어있음", agent_name)
        return {}, []

    events_buffer: list[dict[str, Any]] = []
    events_lock = threading.Lock()
    external_on_event: EventCallback | None = state.get("_debate_on_event")  # type: ignore[assignment]
    auto_start = bool(state.get("_discussion_auto_start", True))
    gate_factory = state.get("_discussion_gate_factory")
    if gate_factory is not None and not callable(gate_factory):
        gate_factory = None

    def _on_event(name: str, payload: dict[str, Any]) -> None:
        with events_lock:
            events_buffer.append({"event": name, "data": payload})
        if external_on_event is not None:
            try:
                external_on_event(name, payload)
            except Exception:
                logger.exception("inline debate: external on_event 실패")

    debates: dict[int, dict[str, Any]] = {}
    for _ev, meta, votes in candidates:
        item_no = int(meta["item_number"])
        try:
            req = _build_request(state=state, meta=meta, votes=votes, max_rounds=max_rounds)
        except Exception:
            logger.exception("inline debate [%s]: item=%s request build 실패 — skip", agent_name, item_no)
            continue
        logger.info(
            "🎭 [inline-debate agent=%s item=#%s] 시작 · votes=%s max_rounds=%d", agent_name, item_no, votes, max_rounds
        )
        try:
            rec = run_debate(req, on_event=_on_event, auto_start=auto_start, gate_factory=gate_factory)
            debates[item_no] = rec.model_dump()
            logger.info(
                "🎭 [inline-debate agent=%s item=#%s] 완료 · final=%s merge=%s rounds=%d",
                agent_name,
                item_no,
                rec.final_score,
                rec.merge_rule,
                rec.rounds_used,
            )
        except Exception as exc:
            logger.exception(
                "inline debate [%s]: item=%s run_debate 실패 · %s", agent_name, item_no, type(exc).__name__
            )
            continue

    return debates, events_buffer


def apply_debate_to_evaluations(
    *, evaluations: list[dict[str, Any]], debates: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """debates 결과를 evaluations 의 score 만 덮어쓰고 판사 결과는 별도 필드로 보존.

    설계 (2026-04-27 개정):
      - **메인 본문** (judgment / deductions / evidence) = sub-agent 페르소나 머지 원본 유지
      - **판사 결과** (judge_score / judge_reasoning / judge_deductions / judge_evidence) = 별도 필드
      - 점수만 판사 결정으로 덮어씀 (최종 점수 = 판사 결정)
      → 프론트는 메인 본문 + 🎭 판사 결정 카드 를 별도로 표시 → 중복 없음.

    덮어쓰는 필드:
      - score          : DebateRecord.final_score (판사 결정 점수)
      - debate_*       : 토론 메타 (merge_rule / rationale / converged)
    추가하는 필드:
      - judge_score    : 판사 점수
      - judge_reasoning: 판사 reasoning
      - judge_deductions / judge_evidence : 판사가 명시한 감점·인용
    유지하는 필드 (sub-agent 원본):
      - judgment       : 페르소나 머지 reasoning
      - deductions     : 페르소나 감점 근거
      - evidence       : 페르소나 원문 인용
      - persona_votes  : {strict, neutral, loose} 점수
    """
    if not debates:
        return evaluations
    out: list[dict[str, Any]] = []
    for ev in evaluations:
        if not isinstance(ev, dict):
            out.append(ev)
            continue
        inner = ev.get("evaluation") if isinstance(ev.get("evaluation"), dict) else None
        if not isinstance(inner, dict):
            out.append(ev)
            continue
        item_no = inner.get("item_number")
        if not isinstance(item_no, int) or item_no not in debates:
            out.append(ev)
            continue
        debate_rec = debates[item_no]
        final_score = debate_rec.get("final_score")
        if final_score is None:
            out.append(ev)
            continue
        final_rationale = debate_rec.get("final_rationale") or ""
        merge_rule = debate_rec.get("merge_rule")
        # 2026-04-27 개정 v2: judge_* 필드는 debate_rec 에서 직접 매핑.
        # _invoke_post_debate_judge 가 판사 호출 성공 시 final_score 자체가 판사 결정 점수임
        # (merge_rule="judge_post_debate"). 실패 시 median fallback.
        # 메인 본문 (judgment / deductions / evidence) 은 sub-agent 페르소나 머지 원본 유지.
        merged_inner = {
            **inner,
            "score": final_score,  # 판사 호출 성공 시 판사 점수, 실패 시 median fallback
            "debate_merge_rule": merge_rule,
            "debate_rationale": final_rationale,
            "debate_converged": debate_rec.get("converged"),
            "judge_score": debate_rec.get("judge_score"),
            "judge_reasoning": debate_rec.get("judge_reasoning"),
            "judge_failure_reason": debate_rec.get("judge_failure_reason"),
            "judge_deductions": debate_rec.get("judge_deductions") or [],
            "judge_evidence": debate_rec.get("judge_evidence") or [],
            "judge_human_cases": debate_rec.get("judge_human_cases") or [],
        }
        out.append({**ev, "evaluation": merged_inner})
    return out


def debate_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph debate_node — spread 높은 평가 항목들을 토론으로 재판정.

    state 업데이트:
      - ``debates``: dict[int, dict] — item_number → DebateRecord.model_dump()
      - ``_debate_events``: list[dict] — 노드 실행 중 발생한 SSE 이벤트 버퍼.
        각 원소는 ``{"event": str, "data": dict}``. server_v2 의 SSE 스트림이 이 배열을
        풀어 프론트에 이벤트로 중계.

    SSE 콜백은 다음 우선순위:
      1. ``state["_debate_on_event"]`` 가 있으면 그 callable 에 실시간 전달 (best-effort).
      2. 항상 ``_debate_events`` 버퍼에도 기록 (graph.astream 이 노드 완료 시 delta 로 배달).

    Interactive 제어 (V3 ensemble 모드):
      - ``state["_discussion_auto_start"]``: bool (기본 True). False 시 discussion_started 후 블록.
      - ``state["_discussion_gate_factory"]``: callable(discussion_id) → threading.Event.
    """
    if not is_debate_enabled():
        logger.info("debate_node: disabled via %s", ENV_ENABLED)
        return {"debates": {}, "_debate_events": []}

    max_rounds = _int_env(ENV_MAX_ROUNDS, DEFAULT_MAX_ROUNDS)
    max_items = _int_env(ENV_MAX_ITEMS, 0)  # 0 = 무제한
    # Bedrock ThrottlingException 회피 — 기본값 1 (직렬). 한 debate 당 persona 3명 +
    # moderator-manager = 최소 4 Bedrock 호출/턴, max_turns=12 면 48 호출/debate.
    # parallel>1 이면 TPS / TPM quota 초과로 ThrottlingException 속출.
    # 고쿼터 환경에서만 env 로 상향: QA_DEBATE_MAX_PARALLEL=2~4
    max_parallel = max(1, _int_env(ENV_MAX_PARALLEL, 1))
    # worker 간 시작 stagger (초) — 동시 출발을 어긋나게 해 Bedrock TPS burst 완화.
    stagger_sec = max(0.0, float(os.getenv("QA_DEBATE_STAGGER_SEC", "3.0")))

    evaluations = state.get("evaluations") or []
    # ★ Option A — sub-agent 에서 이미 inline debate 실행한 item 은 skip.
    # state.debates 는 operator 리듀서가 없으므로 sub-agent 별 결과가 dict merge 로 누적됨.
    # (단, LangGraph 기본은 override — conftest.py 나 state 스키마 확인 필요)
    already_debated = set((state.get("debates") or {}).keys())
    if already_debated:
        evaluations_remaining = [
            ev
            for ev in evaluations
            if not isinstance(ev, dict)
            or not isinstance(ev.get("evaluation"), dict)
            or ev["evaluation"].get("item_number") not in already_debated
        ]
        logger.info(
            "debate_node: inline debate 로 이미 처리된 %d item 제외 (remaining=%d)",
            len(already_debated),
            len(evaluations_remaining),
        )
        evaluations = evaluations_remaining
    # ★ 사용자 정책 (2026-04-24): spread=0 (3-persona 만장일치) 항목도 토론 진입.
    #   페르소나 간 관점 차이로 재검토 중 점수가 ±될 수 있다는 논리.
    #   제외 대상은 _pick_candidates 내부의 3가지 필터로만 제한:
    #     (1) DEBATE_EXCLUDED_ITEMS (#9/#16/#17/#18 룰 기반)
    #     (2) DEBATE_EXCLUDED_MODES (compliance_based / structural_only)
    #     (3) persona_votes 미존재 (single-persona 평가)
    #   → threshold 는 0 으로 둬 spread 필터 사실상 비활성화.
    #   inline debate (L250) 는 별개 환경변수로 동작하므로 통일하지 않음.
    candidates = _pick_candidates(evaluations, threshold=0, max_items=max_items)

    auto_start = bool(state.get("_discussion_auto_start", True))
    gate_factory = state.get("_discussion_gate_factory")
    if gate_factory is not None and not callable(gate_factory):
        logger.warning("debate_node: _discussion_gate_factory 가 callable 이 아님 — 무시")
        gate_factory = None

    logger.info("=" * 78)
    logger.info("🎭 DEBATE NODE START — 병렬 토론 실행")
    logger.info("  · mode=always max_rounds=%d max_items=%d max_parallel=%d", max_rounds, max_items, max_parallel)
    logger.info(
        "  · auto_start=%s gate_factory=%s external_callback=%s",
        auto_start,
        gate_factory is not None,
        state.get("_debate_on_event") is not None,
    )
    logger.info("  · candidates=%d / evaluations=%d", len(candidates), len(evaluations))
    if candidates:
        item_list = ", ".join(
            f"#{int(meta['item_number'])}(spread={_extract_step_spread(ev)})" for ev, meta, _ in candidates
        )
        logger.info("  · items: %s", item_list)
    logger.info("=" * 78)

    # ── 이벤트 버퍼 + 콜백 — 병렬 worker 가 동시 push 하므로 Lock 필수 ──
    events_buffer: list[dict[str, Any]] = []
    events_lock = threading.Lock()
    event_counts: dict[str, int] = {}
    external_on_event: EventCallback | None = state.get("_debate_on_event")  # type: ignore[assignment]

    def _buffered_on_event(name: str, payload: dict[str, Any]) -> None:
        # 버퍼 append 는 GIL 하에 thread-safe 하지만 정합성을 위해 Lock 사용
        with events_lock:
            events_buffer.append({"event": name, "data": payload})
            event_counts[name] = event_counts.get(name, 0) + 1
        # ── 상세 로그 — 프론트로 흘러가는 주요 이벤트를 터미널에 찍음 ──
        item_no_log = payload.get("item_number")
        pid = payload.get("persona_id")
        rnd = payload.get("round")
        if name == "discussion_started":
            logger.info(
                "  ▶ [item=#%s] discussion_started · node=%s max_rounds=%s personas=%s",
                item_no_log,
                payload.get("node_id"),
                payload.get("max_rounds"),
                len(payload.get("personas") or []),
            )
        elif name == "persona_speaking":
            logger.info("  🎙 [item=#%s R%s] persona=%s 발언 시작", item_no_log, rnd, pid)
        elif name == "persona_message":
            msg_preview = str(payload.get("message") or "")[:120].replace("\n", " ")
            score = payload.get("score_proposal")
            logger.info(
                "  💬 [item=#%s R%s] persona=%s 발언 완료 · score=%s · %s%s",
                item_no_log,
                rnd,
                pid,
                score,
                msg_preview,
                "…" if len(str(payload.get("message") or "")) > 120 else "",
            )
        elif name == "vote_cast":
            logger.info("  🗳 [item=#%s R%s] persona=%s 표결 · score=%s", item_no_log, rnd, pid, payload.get("score"))
        elif name == "moderator_verdict":
            logger.info(
                "  ⚖️ [item=#%s R%s] moderator · consensus=%s score=%s",
                item_no_log,
                rnd,
                payload.get("consensus"),
                payload.get("score"),
            )
        elif name == "discussion_round_complete":
            votes = payload.get("votes") or {}
            logger.info(
                "  🔔 [item=#%s R%s] round complete · votes=%s median=%s consensus=%s",
                item_no_log,
                rnd,
                votes,
                payload.get("median"),
                payload.get("consensus_reached"),
            )
        elif name == "debate_final" or name == "discussion_finalized":
            logger.info(
                "  ✅ [item=#%s] %s · final_score=%s method=%s rounds_used=%s",
                item_no_log,
                name,
                payload.get("final_score"),
                payload.get("method") or payload.get("rationale", "?")[:40],
                payload.get("rounds_used"),
            )
        # 실시간 콜백 (server_v2 의 asyncio.Queue bridge) — 이미 thread-safe
        if external_on_event is not None:
            try:
                external_on_event(name, payload)
            except Exception:  # pragma: no cover
                logger.exception("debate_node: external on_event 실패 — 버퍼는 그대로 유지")

    if not candidates:
        logger.warning("debate_node: candidates 비어있음 — 토론 스킵")
        return {"debates": {}, "_debate_events": events_buffer}

    # ── item 별 DebateRequest 사전 빌드 (실패 시 해당 item 만 skip) ──
    reqs: list[tuple[int, DebateRequest]] = []
    for _ev, meta, votes in candidates:
        item_no = int(meta["item_number"])
        try:
            req = _build_request(state=state, meta=meta, votes=votes, max_rounds=max_rounds)
        except Exception:
            logger.exception("debate_node: item=%s DebateRequest 빌드 실패 — skip", item_no)
            continue
        logger.info(
            "  📦 [item=#%s] request built · votes=%s allowed_steps=%s max_score=%s evidence=%d",
            item_no,
            req.initial_positions,
            req.allowed_steps,
            req.max_score,
            len(req.ai_evidence or []),
        )
        reqs.append((item_no, req))

    debates: dict[int, dict[str, Any]] = {}
    node_start = time.perf_counter()

    def _worker(item_no: int, req: DebateRequest, submit_idx: int) -> tuple[int, DebateRecord | None, float]:
        tid = threading.current_thread().name
        # stagger — 동시 출발 방지. submit_idx 순서대로 약간씩 밀어 Bedrock burst 완화.
        if stagger_sec > 0 and submit_idx > 0:
            delay = stagger_sec * (submit_idx % max_parallel)
            if delay > 0:
                logger.info("  ⏱ [thread=%s item=#%s] Bedrock stagger %.2fs 대기", tid, item_no, delay)
                time.sleep(delay)
        w_start = time.perf_counter()
        logger.info("  🚀 [thread=%s item=#%s] run_debate 시작", tid, item_no)

        # ThrottlingException 발생 시 지수백오프로 최대 3회 재시도.
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                rec = run_debate(req, on_event=_buffered_on_event, auto_start=auto_start, gate_factory=gate_factory)
                elapsed = time.perf_counter() - w_start
                logger.info(
                    "  🏁 [thread=%s item=#%s] run_debate 완료 · %.2fs · final=%s merge=%s rounds=%d%s",
                    tid,
                    item_no,
                    elapsed,
                    rec.final_score,
                    rec.merge_rule,
                    rec.rounds_used,
                    f" · (attempt {attempt + 1})" if attempt > 0 else "",
                )
                return item_no, rec, elapsed
            except Exception as exc:
                msg = str(exc)
                last_exc = exc
                is_throttle = "ThrottlingException" in msg or "Too many tokens" in msg or "Rate exceeded" in msg
                if not is_throttle or attempt == 2:
                    break
                backoff = (2**attempt) * 8.0  # 8s, 16s
                logger.warning(
                    "  ⚠️ [thread=%s item=#%s] Bedrock Throttling (attempt %d) → %.1fs 후 재시도",
                    tid,
                    item_no,
                    attempt + 1,
                    backoff,
                )
                time.sleep(backoff)

        elapsed = time.perf_counter() - w_start
        logger.error(
            "  💥 [thread=%s item=#%s] run_debate 실패 · %.2fs · %s",
            tid,
            item_no,
            elapsed,
            type(last_exc).__name__ if last_exc else "?",
            exc_info=last_exc,
        )
        return item_no, None, elapsed

    # ── ThreadPoolExecutor — item 단위 병렬 실행 ──
    # AG2 initiate_chat 이 blocking (Bedrock HTTP), GIL 밖 I/O 라 ThreadPool 로 충분히 병렬화.
    logger.info(
        "  🧵 ThreadPoolExecutor max_workers=%d stagger=%.2fs — %d items 제출", max_parallel, stagger_sec, len(reqs)
    )
    with ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="qa-debate") as pool:
        futures = [pool.submit(_worker, item_no, req, idx) for idx, (item_no, req) in enumerate(reqs)]
        completed = 0
        for fut in as_completed(futures):
            try:
                item_no, rec, _elapsed = fut.result()
            except Exception:  # pragma: no cover — _worker 내부에서 이미 swallow
                logger.exception("debate_node: worker future 예외")
                continue
            completed += 1
            if rec is not None:
                debates[item_no] = rec.model_dump()
            logger.info(
                "  📊 진행률: %d/%d 완료 (item=#%s %s)", completed, len(reqs), item_no, "✓" if rec is not None else "✗"
            )

    total_elapsed = time.perf_counter() - node_start
    logger.info("=" * 78)
    logger.info(
        "🎭 DEBATE NODE END — %d/%d 토론 완료 · %.2fs · parallel=%d",
        len(debates),
        len(reqs),
        total_elapsed,
        max_parallel,
    )
    logger.info(
        "  · SSE events buffered=%d · breakdown: %s",
        len(events_buffer),
        ", ".join(f"{k}={v}" for k, v in sorted(event_counts.items())) or "(none)",
    )
    if debates:
        scores_summary = ", ".join(
            f"#{k}={v.get('final_score')}({v.get('merge_rule')})" for k, v in sorted(debates.items())
        )
        logger.info("  · final scores: %s", scores_summary)
    logger.info("=" * 78)
    # NOTE: evaluations 는 operator.add 리듀서 — 여기서 반환하면 append 돼 중복됨.
    # 토론 최종 점수는 debates[item_no].final_score 에 저장하고, server_v2 가
    # _apply_debate_overrides 로 응답 직전에 evaluations/report 를 덮어쓴다.
    return {"debates": debates, "_debate_events": events_buffer}


__all__ = ["DEFAULT_SPREAD_THRESHOLD", "ENV_ENABLED", "ENV_SPREAD_THRESHOLD", "debate_node", "is_debate_enabled"]
