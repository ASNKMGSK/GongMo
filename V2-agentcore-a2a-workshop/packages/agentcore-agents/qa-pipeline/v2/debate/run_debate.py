# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""debate_node 안에서 호출되는 동기 AG2 토론 실행기.

핵심:
 - 실패 시 평가 전체 중단 금지 — ``median_vote`` fallback DebateRecord 반환.
 - ALLOWED_STEPS 밖 점수 금지 — ``snap_score_v2`` 필수.
 - SSE 콜백 (``on_event``) 으로 실시간 이벤트 4종 중계. 콜백 실패는 무시.
 - AG2 / autogen 미설치 환경에서는 ``build_debate_team`` import 가 실패 → fallback 실행.

Interactive discussion (V3 ensemble 모드 — 2026-04-23 추가):
 - ``discussion_*`` 6종 이벤트 추가 emit: discussion_started / persona_speaking /
   persona_message / vote_cast / discussion_round_complete / discussion_finalized.
 - ``auto_start=False`` + ``gate_factory`` 주입 시 discussion_started 후 threading.Event.wait() 로
   블록 → 프론트가 ``POST /v2/discussion/{id}/start`` 호출하면 해제.
 - AG2 미설치 fallback 에서도 동일 이벤트를 synth 해 프론트 UI 를 끊김 없이 유지.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from v2.contracts.rubric import snap_score_v2
from v2.debate.personas import PERSONA_LABELS, PERSONA_ORDER, build_speak_user_message
from v2.debate.schemas import (
    DebateRecord,
    DebateRequest,
    ModeratorVerdict,
    PersonaTurn,
    RoundRecord,
    TurnRecord,
    VerdictRecord,
)


logger = logging.getLogger(__name__)


EventCallback = Callable[[str, dict[str, Any]], None]

# gate_factory(discussion_id) → threading.Event. 프론트가 start/next-round 호출 시 .set().
GateFactory = Callable[[str], "threading.Event"]


# ---------------------------------------------------------------------------
# RAG 미사용 항목 (LLM + 금지어 사전 등 — 골든셋 / HITL 컨텍스트 불필요).
#
# 포함 시:
#   - 토론 시작 전 골든셋 / HITL retrieve 스킵
#   - rag_hits_ready 이벤트는 빈 fewshot + ``rag_disabled_for_item=True`` 플래그로 emit
#   - 프론트 NodeDrawer 가 해당 항목 카드에 "RAG 사용 안 함" 안내만 표시
#
# #6 정중한 표현: 금지어 사전 1차 필터 + LLM 맥락 판정 (사용자 정책 2026-05-08).
# ---------------------------------------------------------------------------
RAG_DISABLED_ITEMS: frozenset[int] = frozenset({6})


# ---------------------------------------------------------------------------
# 페르소나 메타 — 프론트 표시용 (discussion_started 이벤트 payload)
# ---------------------------------------------------------------------------

PERSONA_META: dict[str, dict[str, str]] = {
    "strict": {
        "id": "strict",
        "name": "페르소나 A",
        "avatar": "👨‍💼",
        "role": "VOC 품격 평가자 (27년차 시니어 매니저)",
    },
    "neutral": {
        "id": "neutral",
        "name": "페르소나 B",
        "avatar": "🧑‍💻",
        "role": "업무 정확도 · 팩트 평가자 (상품 PM 출신 QA 책임)",
    },
    "loose": {
        "id": "loose",
        "name": "페르소나 C",
        "avatar": "🎯",
        "role": "고객 경험 · 적극성 평가자 (영업 MVP 센터장)",
    },
}


# Node 단위 item 매핑 — 프론트가 discussion 을 어느 평가 노드에 귀속시킬지 참조.
_ITEM_TO_NODE: dict[int, str] = {
    1: "greeting",
    2: "greeting",
    3: "listening_comm",
    4: "listening_comm",
    5: "listening_comm",
    6: "language",
    7: "language",
    8: "needs",
    9: "needs",
    10: "explanation",
    11: "explanation",
    12: "proactiveness",
    13: "proactiveness",
    14: "proactiveness",
    15: "work_accuracy",
    16: "work_accuracy",
    17: "privacy",
    18: "privacy",
}


def _node_id_for_item(item_number: int) -> str:
    return _ITEM_TO_NODE.get(int(item_number), "unknown")


def _discussion_started_payload(req: DebateRequest, *, discussion_id: str, auto_start: bool) -> dict[str, Any]:
    personas = [PERSONA_META[p] for p in PERSONA_ORDER if p in PERSONA_META]
    return {
        "discussion_id": discussion_id,
        "node_id": _node_id_for_item(req.item_number),
        "item_number": req.item_number,
        "item_name": req.item_name,
        "max_score": req.max_score,
        "allowed_steps": list(req.allowed_steps),
        "personas": personas,
        "max_rounds": int(req.max_rounds),
        "auto_start": bool(auto_start),
    }


def _safe_event(on_event: EventCallback | None, name: str, payload: dict[str, Any]) -> None:
    if on_event is None:
        return
    try:
        on_event(name, payload)
    except Exception:  # pragma: no cover — SSE 라인 오류가 토론 전체 중단 X
        logger.exception("debate on_event callback failed: %s", name)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# JSON 파서 — persona / moderator 응답 추출
# ---------------------------------------------------------------------------


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _strip_meta_tokens(text: str) -> str:
    """페르소나 reasoning/rebuttal 에서 내부 제어 토큰 제거.
    ★ 2026-05-07: VOTE_FINAL / CONSENSUS 는 AG2 GroupChat 종료 신호용. 본문에 박혀
    프론트에 노출되면 어색 ('갑자기 VOTE_FINAL 이 뭐?'). 백엔드에서 일괄 strip.
    종료 detection 은 별도 로직 (`is_termination_msg`) 에서 raw content 로 판정.
    """
    if not text:
        return text
    import re as _re
    # 패턴: "VOTE_FINAL:" / "VOTE_FINAL " / "VOTE_FINAL\n" / "[VOTE_FINAL]" 등
    cleaned = _re.sub(r"\s*\[?\s*(VOTE_FINAL|CONSENSUS)\s*[:\]]?\s*", " ", text, flags=_re.IGNORECASE)
    # 연속 공백 정리
    cleaned = _re.sub(r"  +", " ", cleaned).strip()
    return cleaned


def _parse_persona_json(content: str, *, allowed_steps: list[int]) -> dict[str, Any] | None:
    """persona 발언 JSON 추출. 실패 시 None.
    ★ 2026-05-07: reasoning/rebuttal 에서 내부 메타 토큰 (VOTE_FINAL/CONSENSUS) 자동 strip.
    """
    if not content:
        return None
    match = _JSON_BLOCK_RE.search(content)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    score = obj.get("score")
    if not isinstance(score, (int, float)):
        return None
    # allowed_steps 밖이면 None — 호출자가 median fallback 에 포함시킴
    if allowed_steps and int(score) not in allowed_steps:
        # snap 이 가능하지만 여기선 raw 를 보존하고 caller 가 스냅/스킵 결정
        pass
    # 내부 메타 토큰 strip — UI 노출용 텍스트 정리
    if isinstance(obj.get("reasoning"), str):
        obj["reasoning"] = _strip_meta_tokens(obj["reasoning"])
    if isinstance(obj.get("rebuttal"), str):
        obj["rebuttal"] = _strip_meta_tokens(obj["rebuttal"])
    return obj


def _parse_moderator_json(content: str) -> dict[str, Any] | None:
    match = _JSON_BLOCK_RE.search(content or "")
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


# ---------------------------------------------------------------------------
# Fallback — AG2 실패 / 미설치 시 median_vote 로 종료
# ---------------------------------------------------------------------------


def _build_judge_only_record(
    *,
    req: DebateRequest,
    reason: str,
    elapsed_start: float,
    on_event: EventCallback | None,
    discussion_id: str,
    node_id: str,
) -> DebateRecord:
    """AG2 토론 실패 시 판사 단독 결정 — 사용자 정책 (2026-04-29).

    AG2 ThrottlingException / build error 등으로 토론이 시작도 못한 경우:
      1. 판사 호출 시도 (rounds=[] 로 deliberate_post_debate)
      2. 판사 성공 → 판사 점수로 DebateRecord 생성 (merge_rule="judge_only_fallback")
      3. 판사도 실패 → median fallback 으로 회귀 + judge_failure_reason 기록

    SSE: persona_speaking/persona_message 이벤트는 _emit_fallback_discussion_events 로
    fallback template 발화 emit (UI 가 페르소나 카드 렌더링 가능하도록).
    """
    scores = [int(v) for v in req.initial_positions.values() if isinstance(v, (int, float))]
    median = int(round(statistics.median(scores))) if scores else 0
    median_snapped = snap_score_v2(req.item_number, median)
    median_rationale = f"[median fallback] AG2 실패 — {reason}. initial median={median} → snap={median_snapped}."

    # 판사 호출 시도 — rounds 비어있어도 initial_positions + transcript 로 결정
    judge_result = _invoke_post_debate_judge(
        req=req,
        rounds=[],
        median_score=median_snapped,
        median_rationale=median_rationale,
        median_converged=False,
        median_merge_rule="fallback_median",
        on_event=on_event,
        discussion_id=discussion_id,
        node_id=node_id,
    )

    judge_used = bool(judge_result.get("judge_score") is not None and not judge_result.get("judge_failure_reason"))
    final_score = judge_result.get("final_score")
    final_rationale = judge_result.get("final_rationale") or median_rationale
    merge_rule = "judge_only_fallback" if judge_used else "fallback_median"
    converged = judge_used  # 판사가 결정했으면 수렴으로 표기

    rec = DebateRecord(
        item_number=req.item_number,
        item_name=req.item_name,
        max_score=req.max_score,
        allowed_steps=list(req.allowed_steps),
        initial_positions=dict(req.initial_positions),
        rounds=[],
        final_score=float(final_score) if final_score is not None else float(median_snapped),
        final_rationale=final_rationale,
        converged=converged,
        ended_at=_now_iso(),
        merge_rule=merge_rule,
        rounds_used=0,
        judge_score=float(judge_result["judge_score"]) if judge_result.get("judge_score") is not None else None,
        judge_reasoning=judge_result.get("judge_reasoning"),
        judge_failure_reason=judge_result.get("judge_failure_reason"),
        judge_deductions=judge_result.get("deductions") or [],
        judge_evidence=judge_result.get("evidence") or [],
        judge_human_cases=judge_result.get("human_cases_meta") or judge_result.get("human_cases") or [],
        debate_stats={
            "elapsed_sec": round(time.perf_counter() - elapsed_start, 3),
            "ag2_failure_reason": reason,
            "judge_used": judge_used,
        },
    )
    _safe_event(on_event, "debate_final", _debate_final_payload(rec))
    _emit_fallback_discussion_events(
        req,
        discussion_id=discussion_id,
        node_id=node_id,
        on_event=on_event,
        final_score=rec.final_score if rec.final_score is not None else 0.0,
        final_rationale=rec.final_rationale,
        rounds_used=0,
        method=merge_rule,
    )
    return rec


def _fallback_record(req: DebateRequest, *, reason: str, elapsed: float) -> DebateRecord:
    """median_vote fallback DebateRecord. CLAUDE.md 계약 준수."""
    scores = [int(v) for v in req.initial_positions.values() if isinstance(v, (int, float))]
    if not scores:
        median = 0
    else:
        median = int(round(statistics.median(scores)))
    snapped = snap_score_v2(req.item_number, median)
    rationale = (
        f"[fallback/median_vote] AG2 토론 실행 실패 — {reason}. 초기 persona 점수 median={median} → snap={snapped}."
    )
    return DebateRecord(
        item_number=req.item_number,
        item_name=req.item_name,
        max_score=req.max_score,
        allowed_steps=list(req.allowed_steps),
        initial_positions=dict(req.initial_positions),
        rounds=[],
        final_score=float(snapped),
        final_rationale=rationale,
        converged=False,
        ended_at=_now_iso(),
        merge_rule="fallback_median",
        rounds_used=0,
        debate_stats={"elapsed_sec": elapsed, "fallback_reason": reason},
    )


# ---------------------------------------------------------------------------
# Fallback discussion event synthesis — AG2 미설치 시에도 프론트 UI 를 유지
# ---------------------------------------------------------------------------

_FALLBACK_PERSONA_TEMPLATES: dict[str, str] = {
    "strict": ("[페르소나 A] 쿠션어·인사 완결성 관점에서 {score}점 — 원문 근거: {evidence_ref}. 품격 중심 판정."),
    "neutral": ("[페르소나 B] 업무 매뉴얼 대조 결과 {score}점 — 원문 근거: {evidence_ref}. 팩트·정확도 관점."),
    "loose": ("[페르소나 C] 고객 경험·끝맺음 관점에서 {score}점 — 원문 근거: {evidence_ref}. 고객 만족 시선."),
}


def _emit_fallback_discussion_events(
    req: DebateRequest,
    *,
    discussion_id: str,
    node_id: str,
    on_event: EventCallback | None,
    final_score: float,
    final_rationale: str,
    rounds_used: int,
    method: str,
) -> None:
    """AG2 실패 경로에서도 프론트가 interactive UI 를 표시할 수 있도록 이벤트 synth.

    초기 persona_votes 로 1 라운드짜리 가짜 토론을 구성 — 실제 LLM 호출 없음.
    """
    if on_event is None:
        return

    votes: dict[str, int] = {}
    for persona_id in PERSONA_ORDER:
        raw = req.initial_positions.get(persona_id)
        if raw is None:
            continue
        try:
            votes[persona_id] = int(snap_score_v2(req.item_number, int(raw)))
        except Exception:
            continue

    if not votes:
        return

    ev_first_ref = ""
    if isinstance(req.ai_evidence, list) and req.ai_evidence:
        first = req.ai_evidence[0]
        if isinstance(first, dict):
            ev_first_ref = str(first.get("turn_id") or first.get("text") or "")[:80]

    for persona_id, score in votes.items():
        _safe_event(
            on_event,
            "persona_speaking",
            {
                "discussion_id": discussion_id,
                "node_id": node_id,
                "item_number": req.item_number,
                "round": 1,
                "persona_id": persona_id,
            },
        )
        msg = _FALLBACK_PERSONA_TEMPLATES.get(persona_id, "[{persona_id}] 초기 판정 {score}점").format(
            score=score, evidence_ref=ev_first_ref or "(근거 미확보)", persona_id=persona_id
        )
        _safe_event(
            on_event,
            "persona_message",
            {
                "discussion_id": discussion_id,
                "node_id": node_id,
                "item_number": req.item_number,
                "round": 1,
                "persona_id": persona_id,
                "message": msg,
                "score_proposal": float(score),
                "evidence_refs": [ev_first_ref] if ev_first_ref else [],
            },
        )
        _safe_event(
            on_event,
            "vote_cast",
            {
                "discussion_id": discussion_id,
                "node_id": node_id,
                "item_number": req.item_number,
                "round": 1,
                "persona_id": persona_id,
                "score": float(score),
            },
        )

    numeric = list(votes.values())
    median = float(statistics.median(numeric))
    spread = max(numeric) - min(numeric) if numeric else 0
    _safe_event(
        on_event,
        "discussion_round_complete",
        {
            "discussion_id": discussion_id,
            "node_id": node_id,
            "item_number": req.item_number,
            "round": 1,
            "votes": {k: float(v) for k, v in votes.items()},
            "median": median,
            "consensus_reached": spread == 0,
        },
    )

    _safe_event(
        on_event,
        "discussion_finalized",
        {
            "discussion_id": discussion_id,
            "node_id": node_id,
            "item_number": req.item_number,
            "final_score": float(final_score),
            "final_reasoning": final_rationale,
            "rounds_used": int(rounds_used),
            "method": method,
        },
    )


# ---------------------------------------------------------------------------
# 핵심 실행기
# ---------------------------------------------------------------------------


def run_debate(
    req: DebateRequest,
    *,
    on_event: EventCallback | None = None,
    auto_start: bool = True,
    gate_factory: GateFactory | None = None,
    gate_timeout_sec: float = 300.0,
) -> DebateRecord:
    """AG2 GroupChat 토론 실행 → DebateRecord 반환.

    실패 시 반드시 ``_fallback_record`` 를 반환 — raise 금지 (caller 가 평가 전체 중단 X).

    Parameters
    ----------
    req : DebateRequest
    on_event : optional SSE 콜백
    auto_start : False 이면 ``discussion_started`` 발송 후 gate 가 set 될 때까지 블록.
    gate_factory : callable(discussion_id) → threading.Event. 프론트의 /start 호출 핸들러가 .set().
    gate_timeout_sec : gate.wait() 최대 대기 시간 (기본 300초).
    """
    t0 = time.perf_counter()
    discussion_id = uuid.uuid4().hex
    node_id = _node_id_for_item(req.item_number)

    # 1) discussion_started — AG2 성공/실패 관계없이 항상 emit
    _safe_event(
        on_event,
        "discussion_started",
        _discussion_started_payload(req, discussion_id=discussion_id, auto_start=auto_start),
    )

    # 2) auto_start=False 면 프론트 start 호출 대기
    if not auto_start and gate_factory is not None:
        try:
            gate = gate_factory(discussion_id)
            logger.info(
                "run_debate: auto_start=False — discussion_id=%s gate wait (max %.0fs)", discussion_id, gate_timeout_sec
            )
            signaled = gate.wait(timeout=gate_timeout_sec)
            if not signaled:
                logger.warning("run_debate: gate timeout (%ss) — 진행 강제", gate_timeout_sec)
        except Exception:
            logger.exception("run_debate: gate 대기 실패 — 진행")

    # AG2 import — 미설치 환경에서는 ImportError 로 fallback
    try:
        from v2.debate.team import build_debate_team
    except Exception as exc:  # pragma: no cover
        logger.warning("AG2 팀 import 실패 → fallback: %s", exc)
        rec = _fallback_record(req, reason=f"ag2_import_error:{type(exc).__name__}", elapsed=time.perf_counter() - t0)
        _safe_event(on_event, "debate_final", _debate_final_payload(rec))
        _emit_fallback_discussion_events(
            req,
            discussion_id=discussion_id,
            node_id=node_id,
            on_event=on_event,
            final_score=rec.final_score if rec.final_score is not None else 0.0,
            final_rationale=rec.final_rationale,
            rounds_used=0,
            method="fallback_median",
        )
        return rec

    # round_start 이벤트 — round=1 시점
    _safe_event(
        on_event, "debate_round_start", {"item_number": req.item_number, "round": 1, "max_rounds": int(req.max_rounds)}
    )

    # raw_turn 수집을 위한 내부 버퍼 (team.py 의 hook 이 흘려준다)
    raw_turns: list[dict[str, Any]] = []

    def _local_on_event(name: str, payload: dict[str, Any]) -> None:
        if name == "raw_turn":
            raw_turns.append(payload)
            return
        _safe_event(on_event, name, payload)

    try:
        manager, personas = build_debate_team(req, _local_on_event, discussion_id=discussion_id, node_id=node_id)
    except Exception as exc:  # pragma: no cover
        logger.warning("AG2 team build 실패 → 판사 단독 결정 시도: %s: %s", type(exc).__name__, exc, exc_info=True)
        # AG2 실패 시에도 판사 호출 — 사용자 정책 (2026-04-29).
        rec = _build_judge_only_record(
            req=req, reason=f"team_build_error:{type(exc).__name__}:{str(exc)[:160]}",
            elapsed_start=t0, on_event=on_event, discussion_id=discussion_id, node_id=node_id,
        )
        return rec

    # ★ 2026-05-08: RAG 미사용 항목 단축 경로.
    # #6 정중한 표현 (LLM + 금지어 사전) 처럼 RAG 컨텍스트가 무의미한 항목은
    # 골든셋/HITL/리랭커 모두 우회. 빈 hits_ready 이벤트만 1회 emit →
    # 프론트 NodeDrawer 가 카드에 "RAG 사용 안 함" 안내만 표시.
    rag_disabled_for_item: bool = int(req.item_number) in RAG_DISABLED_ITEMS
    if rag_disabled_for_item:
        _safe_event(on_event, "rag_hits_ready", {
            "node_id": node_id,
            "agent_id": node_id,
            "item_number": int(req.item_number),
            "phase": "debate",
            "fewshot": [],
            "fewshot_query": "",
            "intent": "general_inquiry",
            "reranked": False,
            "rag_disabled_for_item": True,
            "rag_disabled_reason": "이 항목은 'LLM + 금지어 사전' 으로 평가 — 골든셋/HITL/리랭커 미사용",
            "ts": _now_iso(),
        })
        logger.info(
            "debate #%d RAG 미사용 항목 — golden_set/HITL retrieve 스킵 (RAG_DISABLED_ITEMS)",
            req.item_number,
        )

    # ★ 2026-04-30: 골든셋 (HITL) 사례 — 토론 시작 전 1회 retrieve 후 페르소나 broadcast 메시지에 주입.
    # 판사는 RAG 미사용 정책. 골든셋은 페르소나 토론 단계에서만 보조 컨텍스트로 사용.
    debate_hitl_cases: list[dict] = []
    if not rag_disabled_for_item:
        try:
            from v2.hitl.rag_retriever import retrieve_human_cases
            # ★ 2026-04-30: consultation_id 전달 — retriever 가 KNN 무관 자기상담 강제 매칭 + is_self_match 마킹.
            # ★ 2026-05-08: sub-agent (1차 페르소나 평가) 가 쓴 segment_text 우선 사용 →
            # 1차 평가 ↔ 토론 RAG evidence 일관성 보장. 미지정 시 transcript 폴백.
            debate_hitl_cases = retrieve_human_cases(
                item_number=req.item_number,
                query_text=req.segment_text or req.transcript or req.item_name,
                top_k=3,
                consultation_id=req.consultation_id,
            ) or []
        except Exception as exc:
            logger.warning(
                "debate #%d 골든셋 retrieve 실패 — 사례 없이 진행: %s", req.item_number, exc,
            )
            debate_hitl_cases = []

    # ★ 2026-04-30: 자기 자신 매칭 표시 — 현재 평가 중 상담의 골든셋이 검색되면 is_self_match=True.
    # 사용자에게 "이 골든셋은 평가 중인 원문 자체의 사례" 임을 UI 에서 식별 가능하게.
    current_cid = str(req.consultation_id or "").strip()
    if current_cid:
        for h in debate_hitl_cases:
            if not isinstance(h, dict):
                continue
            case_cid = str(h.get("consultation_id") or "").strip()
            if case_cid and case_cid == current_cid:
                h["is_self_match"] = True

    # ★ 2026-05-08: sub-agent (Layer 2) 가 이미 retrieve 한 fewshot 재사용 (중복 retrieve 제거).
    # 같은 qa-golden-set 인덱스를 두 번 호출하던 비효율 + AI 평가자 ↔ 페르소나 evidence 불일치
    # 문제 해결. precomputed_golden_set 가 비어있으면 폴백으로 retrieve.
    # (이전: sub-agent 가 item-specific intent 로 retrieve → 토론에서 general_inquiry 로 재 retrieve.
    #        같은 인덱스인데 query 달라 hits 가 미세하게 다름 → 토론 노이즈.)
    debate_golden_cases: list[dict] = []
    precomputed = getattr(req, "precomputed_golden_set", None) or []
    if rag_disabled_for_item:
        precomputed = []
        debate_golden_cases = []
    elif precomputed:
        for ex in precomputed:
            if not isinstance(ex, dict):
                continue
            debate_golden_cases.append({
                "transcript_excerpt": ex.get("segment_text", ""),
                "human_score": ex.get("score"),
                "human_note": ex.get("rationale", ""),
                # parsed_text 는 sub-agent fewshot_details (make_rag_evidence) 에서는 누락 —
                # 빈 문자열로 둠. 폴백 retrieve 시에는 safe_retrieve_fewshot 가 채움.
                "parsed_text": ex.get("parsed_text", ""),
                "ai_score": None,
                "ai_judgment": "",
                # 진단용 — sub-agent fewshot_details 의 rrf_score 와 의미 호환.
                "_knn_score": ex.get("rrf_score"),
                "is_self_match": False,
                "source": "golden_set",
                "example_id": ex.get("example_id"),
                "score_bucket": ex.get("score_bucket"),
            })
        logger.info(
            "debate #%d 골든셋: sub-agent precomputed %d건 재사용 (retrieve 생략)",
            req.item_number, len(debate_golden_cases),
        )
    else:
        # 폴백 — sub-agent 결과 누락 시 (e.g., conditional 모드 unevaluable, evaluation 자체 skip,
        # rag_evidence 미생성) 만 retrieve. 정상 경로에서는 도달 안 함.
        try:
            from v2.agents.group_a._shared import safe_retrieve_fewshot
            golden_raw = safe_retrieve_fewshot(
                item_number=req.item_number,
                intent="general_inquiry",  # qa-golden-set 메타 intent="*" 라 모든 intent 매칭
                # ★ 2026-05-08: segment_text 우선 (1차 평가 ↔ 토론 RAG 일관성).
                segment_text=req.segment_text or req.transcript or "",
                tenant_id=req.tenant_id or "kolon",
                top_k=3,
            ) or []
            for ex in golden_raw:
                if not isinstance(ex, dict):
                    continue
                debate_golden_cases.append({
                    "transcript_excerpt": ex.get("segment_text", ""),
                    "human_score": ex.get("score"),
                    "human_note": ex.get("rationale", ""),
                    # ★ 2026-05-07: md "## 파싱 원문" 본문 (Layer1 평가그룹별 분할 문맥).
                    # 페르소나 broadcast 메시지 + 프론트 카드 표시. segment_text(=근거) 와 별개.
                    "parsed_text": ex.get("parsed_text", ""),
                    "ai_score": None,
                    "ai_judgment": "",
                    "_knn_score": None,
                    "is_self_match": False,
                    "source": "golden_set",
                    "example_id": ex.get("example_id"),
                    "score_bucket": ex.get("score_bucket"),
                })
            logger.warning(
                "debate #%d 골든셋: precomputed 누락 — fallback retrieve %d건",
                req.item_number, len(debate_golden_cases),
            )
        except Exception as exc:
            logger.warning(
                "debate #%d golden_set fallback retrieve 실패 — golden 사례 없이 진행: %s",
                req.item_number, exc,
            )
            debate_golden_cases = []

    # 두 출처 합쳐서 페르소나에 전달 — HITL 우선, golden 뒤. format 은 동일 함수 사용.
    debate_hitl_cases = list(debate_hitl_cases) + debate_golden_cases
    logger.info(
        "debate #%d 골든셋 컨텍스트: HITL=%d + golden_set=%d (총 %d)",
        req.item_number,
        len(debate_hitl_cases) - len(debate_golden_cases),
        len(debate_golden_cases),
        len(debate_hitl_cases),
    )

    # 2026-05-08: 토론용 골든셋/HITL 사례 라이브 SSE — 페르소나 broadcast 직전.
    # 프론트 NodeDrawer 가 토론 finalized 까지 기다리지 않고 즉시 표시.
    # 페이로드 포맷: emit_rag_hits_ready 와 동일 (fewshot 배열).
    # rag_disabled_for_item 인 경우는 위에서 빈 emit 1회 처리 — 여기 build/emit 모두 스킵.
    if not rag_disabled_for_item:
        debate_fewshot_for_emit: list[dict[str, Any]] = []
        for c in debate_hitl_cases:
            if not isinstance(c, dict):
                continue
            # HITL 케이스 (transcript_excerpt/human_score/human_note) → fewshot 포맷 매핑.
            # golden_set 케이스도 같은 path — example_id 가 미리 채워져 있음.
            ex_id = c.get("example_id") or f"hitl-{c.get('consultation_id', '')}"
            debate_fewshot_for_emit.append({
                "example_id": str(ex_id),
                "score": c.get("human_score"),
                "score_bucket": c.get("score_bucket"),
                # 2026-05-08: 사용자 요청 — 검색 원문 truncation 폐지. 프론트 토글로 접음.
                "segment_text": c.get("transcript_excerpt") or "",
                "rationale": c.get("human_note") or "",
                # parsed_text 도 같이 전달 (golden_set 사례면 source case 에 있음).
                "parsed_text": c.get("parsed_text") or "",
                "intent": "general_inquiry",
                "rater_meta": {
                    "rater_type": "human",
                    "source": c.get("source") or "hitl",
                },
                "is_self_match": bool(c.get("is_self_match", False)),
            })
        _safe_event(on_event, "rag_hits_ready", {
            "node_id": node_id,
            "agent_id": node_id,
            "item_number": int(req.item_number),
            "phase": "debate",
            "fewshot": debate_fewshot_for_emit,
            "fewshot_query": (req.transcript or "")[:4000],
            "intent": "general_inquiry",
            "reranked": False,
            "ts": _now_iso(),
        })

    # frontend 표시용 요약 — judge_agent._summarize_human_cases 와 동일 포맷 (200자 truncation)
    persona_hitl_cases_summary: list[dict] = []
    try:
        from v2.judge_agent import _summarize_human_cases as _summ
        persona_hitl_cases_summary = _summ(debate_hitl_cases)
    except Exception:
        # 요약 실패 시에도 toString 으로라도 노출
        persona_hitl_cases_summary = [
            {k: v for k, v in (h or {}).items() if k != "_knn_score"}
            for h in debate_hitl_cases
        ]

    # ★ 2026-05-07: source 보존 — _summ 가 source 누락 시 entry 별 원본 source 로 patch.
    # golden_set 사례가 hitl 로 둔갑하는 버그 방지.
    try:
        for src_case, summary in zip(debate_hitl_cases, persona_hitl_cases_summary):
            if isinstance(src_case, dict) and isinstance(summary, dict):
                orig_source = src_case.get("source")
                if orig_source and summary.get("source") != orig_source:
                    summary["source"] = orig_source
                # example_id (golden_set 전용) 도 유지 — 표시에 활용
                if src_case.get("example_id"):
                    summary["example_id"] = src_case.get("example_id")
                # ★ 2026-05-07: parsed_text (golden_set "## 파싱 원문") — _summ 가 채웠어도
                # 누락 시 src 에서 다시 채우는 안전망. truncate 600자 cap.
                if not summary.get("parsed_text") and src_case.get("parsed_text"):
                    # 2026-05-08: parsed_text 전체 보존 — 프론트 토글로 처리.
                    summary["parsed_text"] = src_case.get("parsed_text") or ""
    except Exception:
        pass

    # 페르소나 RAG 검색에 쓴 query 원문 — 프론트 표시용.
    # ★ 2026-05-08: sub-agent (1차 페르소나 평가) 가 쓴 segment_text 우선 사용 →
    # 1차 평가 ↔ 토론 RAG evidence 일관성 보장. 미지정 시 transcript 폴백.
    # cap 4000자 로 확장 (사용자 요청, 프론트가 동일 query 표시 시 sub-agent 1000자 와
    # prefix 일치 검출하여 박스 통합 가능).
    persona_rag_query_text: str = (
        (req.segment_text or req.transcript or req.item_name or "")
    )[:4000]

    # 발언 순서 보장 — AG2 round_robin 은 initiator 의 다음 agent 를 첫 발언자로 선택.
    # personas 가 PERSONA_ORDER 순서 (strict, neutral, loose) 일 때, 마지막 (loose) 가 initiate 하면
    # round_robin 이 wrap around 해서 strict 부터 발언 시작 → 라운드 1 순서가 PERSONA_ORDER 와 일치.
    # initial_msg 는 broadcast 되므로 persona=None 으로 "[당신]" 마킹 없이 3명 모두 동등 표시.
    try:
        initial_msg = build_speak_user_message(
            item_name=req.item_name,
            item_number=req.item_number,
            max_score=req.max_score,
            transcript=req.transcript,
            rag_context=req.rag_context,
            ai_evidence=req.ai_evidence,
            ai_judgment=req.ai_judgment,
            prev_turns=[],
            persona=None,  # broadcast 메시지 — "[당신]" 마킹 X, 모든 페르소나 동등 표시
            persona_details=req.persona_details,
            hitl_cases=debate_hitl_cases,
        )
        # ★ 2026-05-07: ThrottlingException 시 지수백오프 재시도 (8s, 16s, 32s).
        # 기존엔 첫 throttle 한 방에 _build_judge_only_record 로 fallback → 사용자 화면에서
        # "AG2 토론 실패" 보고 median 폴백. node.py:565 의 outer retry 는 run_debate 가 예외를
        # 삼키니까 동작 못함. 안쪽에서 직접 재시도.
        # personas[-1] (PERSONA_ORDER 마지막) 이 initiate → round_robin 이 personas[0] 부터 발언 시작.
        # Bedrock TPM throttle cooldown 은 30~60s 단위라 긴 backoff 필요.
        # 4회 시도 (15s, 30s, 60s 누적 105s + 마지막 실패 = 105s 까지 대기 가능).
        last_chat_exc: Exception | None = None
        backoffs = [15.0, 30.0, 60.0]  # 4회 시도, 사이 backoff 3개
        for chat_attempt in range(4):
            try:
                personas[-1].initiate_chat(
                    manager,
                    message=initial_msg,
                    max_turns=max(1, req.max_rounds) * 4,
                    silent=True,
                )
                last_chat_exc = None
                if chat_attempt > 0:
                    logger.info(
                        "AG2 initiate_chat 재시도 성공 (attempt %d)", chat_attempt + 1
                    )
                break
            except Exception as exc:
                last_chat_exc = exc
                msg = str(exc)
                is_throttle = (
                    "ThrottlingException" in msg
                    or "Too many tokens" in msg
                    or "Too many requests" in msg
                    or "Rate exceeded" in msg
                )
                if not is_throttle or chat_attempt == 3:
                    break
                backoff = backoffs[chat_attempt]
                logger.warning(
                    "AG2 initiate_chat Throttling (attempt %d/4) → %.1fs 후 재시도",
                    chat_attempt + 1,
                    backoff,
                )
                time.sleep(backoff)
                # 재시도 시 raw_turns 정리 — 부분 emit 된 이벤트가 _structure_rounds 에 섞이는 거 방지.
                raw_turns.clear()
        if last_chat_exc is not None:
            logger.warning("AG2 initiate_chat 최종 실패 → 판사 단독 결정 시도: %s", last_chat_exc)
            # AG2 실패 시에도 판사 호출 — 사용자 정책 (2026-04-29).
            rec = _build_judge_only_record(
                req=req, reason=f"initiate_chat_error:{type(last_chat_exc).__name__}",
                elapsed_start=t0, on_event=on_event, discussion_id=discussion_id, node_id=node_id,
            )
            return rec
    except Exception as exc:
        logger.warning("AG2 initiate_chat 외 예외 → 판사 단독 결정 시도: %s", exc)
        rec = _build_judge_only_record(
            req=req, reason=f"initiate_chat_error:{type(exc).__name__}",
            elapsed_start=t0, on_event=on_event, discussion_id=discussion_id, node_id=node_id,
        )
        return rec

    # raw_turns → 구조화 트랜스크립트로 파싱
    rounds, legacy_transcripts, legacy_verdicts = _structure_rounds(
        raw_turns, req, on_event, discussion_id=discussion_id, node_id=node_id
    )

    # 최종 점수 결정 (1차) — 통계적 머지 (median / consensus 감지)
    median_score, median_rationale, median_converged, median_merge_rule = _decide_final(rounds, req)

    # ★ Post-Debate Judge — 토론 transcript 통째로 보고 최종 판정 (2026-04-27 개정).
    # 판사가 호출되어 성공하면 메인 본문이 곧 판사 결정. 실패 시에만 median fallback.
    # 사용자 명시 요구 (2026-04-27): "합의든 비합의든 모든 토론은 판사 LLM 이 최종 결정".
    judge_result = _invoke_post_debate_judge(
        req=req,
        rounds=rounds,
        median_score=median_score,
        median_rationale=median_rationale,
        median_converged=median_converged,
        median_merge_rule=median_merge_rule,
        on_event=on_event,
        discussion_id=discussion_id,
        node_id=node_id,
    )
    final_score = judge_result["final_score"]
    final_rationale = judge_result["final_rationale"]
    converged = judge_result["converged"]
    merge_rule = judge_result["merge_rule"]
    judge_score = judge_result.get("judge_score")
    judge_reasoning = judge_result.get("judge_reasoning")
    judge_failure_reason = judge_result.get("judge_failure_reason")
    judge_deductions = judge_result.get("deductions") or []
    judge_evidence = judge_result.get("evidence") or []
    judge_human_cases = judge_result.get("human_cases_meta") or []

    rec = DebateRecord(
        item_number=req.item_number,
        item_name=req.item_name,
        max_score=req.max_score,
        allowed_steps=list(req.allowed_steps),
        initial_positions=dict(req.initial_positions),
        rounds=rounds,
        final_score=float(final_score) if final_score is not None else None,
        final_rationale=final_rationale,
        converged=converged,
        ended_at=_now_iso(),
        merge_rule=merge_rule,
        rounds_used=len(rounds),
        judge_score=float(judge_score) if judge_score is not None else None,
        judge_reasoning=judge_reasoning,
        judge_failure_reason=judge_failure_reason,
        judge_deductions=judge_deductions,
        judge_evidence=judge_evidence,
        judge_human_cases=judge_human_cases,
        # ★ 2026-04-30: 페르소나가 토론에서 참고한 HITL 사례 (판사 아님).
        persona_hitl_cases=persona_hitl_cases_summary,
        persona_rag_query=persona_rag_query_text or None,
        debate_stats={
            "elapsed_sec": round(time.perf_counter() - t0, 3),
            "raw_turn_count": len(raw_turns),
            "legacy_transcript_count": len(legacy_transcripts),
            "legacy_verdict_count": len(legacy_verdicts),
            "discussion_id": discussion_id,
        },
    )
    _safe_event(on_event, "debate_final", _debate_final_payload(rec))

    # discussion_finalized — 새 이벤트 스키마에 맞춰 최종 결과 전파
    if merge_rule == "judge_post_debate":
        _method = "judge_post_debate"
    elif merge_rule == "consensus":
        _method = "ag2_consensus"
    elif merge_rule:
        _method = "ag2_" + merge_rule
    else:
        _method = "ag2_group_chat"
    _safe_event(
        on_event,
        "discussion_finalized",
        {
            "discussion_id": discussion_id,
            "node_id": node_id,
            "item_number": req.item_number,
            "item_name": req.item_name,
            "max_score": req.max_score,
            "final_score": float(final_score) if final_score is not None else None,
            "final_reasoning": final_rationale,
            "rounds_used": len(rounds),
            "method": _method,
            # 판사 출력 — 별도 카드용 (메인 final_reasoning 과 분리)
            "judge_score": rec.judge_score,
            "judge_reasoning": rec.judge_reasoning,
            "judge_failure_reason": rec.judge_failure_reason,
            "judge_deductions": list(rec.judge_deductions or []),
            "judge_evidence": list(rec.judge_evidence or []),
            "judge_human_cases": list(rec.judge_human_cases or []),
            # ★ 2026-04-30: 페르소나 HITL 사례 — frontend 노드 드로어에 표시.
            "persona_hitl_cases": list(rec.persona_hitl_cases or []),
            # ★ 2026-05-07: 검색에 사용된 원문 — 프론트가 "이 원문으로 검색됐다" 노출.
            "persona_rag_query": rec.persona_rag_query,
        },
    )
    return rec


# ---------------------------------------------------------------------------
# raw_turns → RoundRecord 리스트 변환
# ---------------------------------------------------------------------------


def _structure_rounds(
    raw_turns: list[dict[str, Any]],
    req: DebateRequest,
    on_event: EventCallback | None,
    *,
    discussion_id: str = "",
    node_id: str = "",
) -> tuple[list[RoundRecord], list[PersonaTurn], list[ModeratorVerdict]]:
    """raw_turns (agent_name/content 쌍) 을 라운드 단위 구조로 재조립.

    한 라운드 = (strict, neutral, loose, moderator) 4 발언. 부족한 라운드는 그대로 잘림.
    두 이벤트 계열 emit:
      - 레거시: persona_turn / moderator_verdict (기존 프론트 호환)
      - 신규 interactive: persona_speaking / persona_message / vote_cast /
        discussion_round_complete (V3 ensemble 모드)
    """
    rounds: list[RoundRecord] = []
    legacy_transcripts: list[PersonaTurn] = []
    legacy_verdicts: list[ModeratorVerdict] = []

    current_turns: list[TurnRecord] = []
    current_verdict: VerdictRecord | None = None
    current_votes: dict[str, float] = {}
    round_no = 1
    current_turn_round: int | None = None  # team.py round_tracker 가 실어 보낸 round (truth)

    for rt in raw_turns:
        agent_name = str(rt.get("agent_name") or "").lower()
        content = str(rt.get("content") or "")
        # team.py 가 raw_turn 에 round 를 실어 보냄 (2026-04-29). 추측 알고리즘 대신 사용.
        raw_round = rt.get("round")
        raw_round_int = int(raw_round) if isinstance(raw_round, (int, float)) else None

        if agent_name in PERSONA_ORDER:
            parsed = _parse_persona_json(content, allowed_steps=list(req.allowed_steps))
            # ★ 초기 task 메시지 skip — personas[0].initiate_chat(manager, message=initial_msg)
            # 때문에 첫 raw_turn 의 sender 는 strict 인데 content 는 `[상담 원문]` 을 포함한
            # 평가 요청 그대로임. parser 가 score 를 못 뽑으면 이 turn 은 persona 응답이 아니라
            # 초기 task → TurnRecord 로 기록하면 "strict 가 0 점 주면서 상담 원문을 argument 로
            # 답변" 한 것처럼 보이는 버그 (사용자 리포트). team.py hook 과 동일한 방어 로직.
            has_score = parsed is not None and isinstance(parsed.get("score"), (int, float))
            if not has_score:
                logger.info(
                    "[_structure_rounds] skip non-persona turn · sender=%s content=%d chars (initial task or parse fail)",
                    agent_name,
                    len(content),
                )
                continue
            score_raw = int(parsed["score"])  # type: ignore[index]
            snapped = snap_score_v2(req.item_number, score_raw)
            reasoning = str(parsed.get("reasoning") or content[:500])
            rebuttal = (parsed.get("rebuttal") if parsed else None) or None
            evidence_refs_raw = (parsed.get("evidence_refs") if parsed else None) or []
            if not isinstance(evidence_refs_raw, list):
                evidence_refs_raw = []

            # ── 라운드 경계 감지 (raw_round 기반) ──
            # team.py 가 실어 보낸 round 가 이전 turn 의 round 와 다르면 새 라운드 시작.
            # 이전 라운드의 turns 는 verdict (median) 와 함께 RoundRecord 로 confirm.
            if (
                raw_round_int is not None
                and current_turn_round is not None
                and raw_round_int != current_turn_round
                and current_turns
            ):
                bucket_score = int(round(statistics.median(t.score for t in current_turns)))
                bucket_score = snap_score_v2(req.item_number, bucket_score)
                rounds.append(
                    RoundRecord(
                        round=current_turn_round,
                        turns=list(current_turns),
                        verdict=VerdictRecord(
                            consensus=False,
                            score=bucket_score,
                            rationale=f"[R{current_turn_round} 자동 마감] 모더레이터 미참여 — persona median 으로 봉합",
                        ),
                    )
                )
                current_turns = []
                current_votes = {}
                current_verdict = None

            if raw_round_int is not None:
                current_turn_round = raw_round_int
            elif current_turn_round is None:
                current_turn_round = round_no

            event_round = current_turn_round

            turn = TurnRecord(persona=agent_name, score=snapped, argument=reasoning)  # type: ignore[arg-type]
            current_turns.append(turn)
            current_votes[agent_name] = float(snapped)
            legacy_transcripts.append(
                PersonaTurn(
                    round_no=event_round,
                    persona=agent_name,  # type: ignore[arg-type]
                    persona_label=PERSONA_LABELS.get(agent_name, agent_name),
                    score=snapped,
                    reasoning=reasoning,
                    rebuttal=rebuttal,
                    timestamp=_now_iso(),
                )
            )
            # 레거시 이벤트 유지 — max_score 포함 (프론트가 per-item 배점 표시에 사용)
            _safe_event(
                on_event,
                "persona_turn",
                {
                    "item_number": req.item_number,
                    "max_score": req.max_score,
                    "round": event_round,
                    "persona": agent_name,
                    "score": snapped,
                    "argument": reasoning,
                },
            )
            # 신규 interactive 이벤트 3종 (speaking → message → vote)
            _safe_event(
                on_event,
                "persona_speaking",
                {
                    "discussion_id": discussion_id,
                    "node_id": node_id,
                    "item_number": req.item_number,
                    "round": event_round,
                    "persona_id": agent_name,
                },
            )
            _safe_event(
                on_event,
                "persona_message",
                {
                    "discussion_id": discussion_id,
                    "node_id": node_id,
                    "item_number": req.item_number,
                    "round": event_round,
                    "persona_id": agent_name,
                    "message": reasoning,
                    "score_proposal": float(snapped),
                    "evidence_refs": evidence_refs_raw,
                    "rebuttal": rebuttal,
                },
            )
            _safe_event(
                on_event,
                "vote_cast",
                {
                    "discussion_id": discussion_id,
                    "node_id": node_id,
                    "item_number": req.item_number,
                    "round": event_round,
                    "persona_id": agent_name,
                    "score": float(snapped),
                },
            )

        elif agent_name == "moderator":
            parsed = _parse_moderator_json(content)
            consensus = bool(parsed.get("consensus_reached")) if parsed else False
            standings = parsed.get("standings") if parsed else None
            spread_val = int(parsed.get("spread", 0)) if parsed else 0
            next_action = str(parsed.get("next_action", "continue")) if parsed else "continue"
            summary_text = str(parsed.get("summary", content[:300])) if parsed else content[:300]

            # moderator score 는 standings 의 median 을 채택 (없으면 current_turns 에서 도출)
            mod_score: int | None = None
            if isinstance(standings, dict) and standings:
                try:
                    mod_score = int(round(statistics.median(int(v) for v in standings.values())))
                    mod_score = snap_score_v2(req.item_number, mod_score)
                except Exception:
                    mod_score = None
            if mod_score is None and current_turns:
                mod_score = int(round(statistics.median(t.score for t in current_turns)))
                mod_score = snap_score_v2(req.item_number, mod_score)

            current_verdict = VerdictRecord(consensus=consensus, score=mod_score, rationale=summary_text)
            legacy_verdicts.append(
                ModeratorVerdict(
                    round_no=round_no,
                    consensus_reached=consensus,
                    spread=spread_val,
                    standings=standings if isinstance(standings, dict) else {},
                    next_action=next_action if next_action in ("continue", "finalize", "force_vote") else "continue",
                    summary=summary_text,
                )
            )
            _safe_event(
                on_event,
                "moderator_verdict",
                {
                    "item_number": req.item_number,
                    "round": round_no,
                    "consensus": consensus,
                    "score": mod_score,
                    "rationale": summary_text,
                },
            )
            # 신규 interactive — 라운드 완결 이벤트
            _round_votes = dict(current_votes)
            _median: float | None = None
            if _round_votes:
                try:
                    _median = float(statistics.median(_round_votes.values()))
                except Exception:
                    _median = None
            _safe_event(
                on_event,
                "discussion_round_complete",
                {
                    "discussion_id": discussion_id,
                    "node_id": node_id,
                    "item_number": req.item_number,
                    "round": round_no,
                    "votes": _round_votes,
                    "median": _median,
                    "consensus_reached": consensus,
                    "moderator_summary": summary_text,
                },
            )

            # 라운드 완결
            rounds.append(RoundRecord(round=round_no, turns=list(current_turns), verdict=current_verdict))
            current_turns = []
            current_verdict = None
            current_votes = {}
            round_no += 1

            if consensus:
                break
            # 다음 라운드 시작 이벤트
            if round_no <= int(req.max_rounds):
                _safe_event(
                    on_event,
                    "debate_round_start",
                    {"item_number": req.item_number, "round": round_no, "max_rounds": int(req.max_rounds)},
                )
        else:
            # 알 수 없는 화자 — 무시
            continue

    # 마지막 라운드 마무리 — main loop 안에서 round 전환 감지로 이전 라운드들은 이미 close 됨.
    # 여기서는 현재 누적 중인 마지막 라운드 turn 들을 verdict 와 함께 하나의 RoundRecord 로 마감.
    # raw_turn 의 round 필드 (team.py round_tracker) 가 truth — 추측 알고리즘 불필요.
    if current_turns:
        last_round = current_turn_round if current_turn_round is not None else round_no
        bucket_score = int(round(statistics.median(t.score for t in current_turns)))
        bucket_score = snap_score_v2(req.item_number, bucket_score)
        forced = VerdictRecord(
            consensus=False,
            score=bucket_score,
            rationale=f"[강제 마감 R{last_round}] 모더레이터 발언 누락 — persona median 으로 봉합",
        )
        rounds.append(RoundRecord(round=last_round, turns=list(current_turns), verdict=forced))

    return rounds, legacy_transcripts, legacy_verdicts


# ---------------------------------------------------------------------------
# 최종 점수 결정 로직
# ---------------------------------------------------------------------------


def _invoke_post_debate_judge(
    *,
    req: DebateRequest,
    rounds: list[RoundRecord],
    median_score: int | None,
    median_rationale: str,
    median_converged: bool,
    median_merge_rule: str,
    on_event: EventCallback | None,
    discussion_id: str,
    node_id: str,
) -> dict[str, Any]:
    """AG2 토론 종료 후 판사 호출 → 최종 판정 dict.

    설계 (2026-04-27 개정 v2 — 판사 = 메인 결정자):
      - 사용자 요구: "합의든 비합의든 모든 토론은 판사 LLM 이 최종 결정".
      - 판사 호출 성공 시 메인 본문 (final_score / final_rationale / merge_rule / converged) =
        판사 결정. merge_rule="judge_post_debate", converged=True.
      - 판사 호출 실패 / 스킵 시에만 median fallback 으로 메인 본문 유지.
      - judge_score / judge_reasoning 은 별도 필드로도 계속 반환 (DebateRecord 보존용).
      - rounds 가 비면 (fallback_record 케이스) 판사 호출 자체 불가 → median fallback.
      - LLMTimeoutError 는 상위 전파 (CLAUDE.md 규약).

    Returns dict:
      - final_score   : int | None
      - final_rationale: str
      - converged     : bool
      - merge_rule    : str  ("judge_post_debate" | "consensus" | "median_vote" | "fallback_median")
      - judge_score   : int | None  (판사 결정 점수, 실패/스킵 시 None)
      - judge_reasoning: str | None (판사 reasoning, 실패/스킵 시 None)
      - judge_failure_reason: str | None (판사 실패 사유, 성공 시 None)
      - deductions    : list[dict]
      - evidence      : list[dict]

    SSE 이벤트:
      - judge_post_debate : 판사 판정 이벤트
    """
    # rounds 가 비어도 판사 호출 시도 — 사용자 정책 (2026-04-29):
    # AG2 ThrottlingException 등으로 토론 자체가 실패해도 판사는 무조건 호출.
    # 판사는 initial_positions (Sub Agent 1차 결과) + transcript 만 보고도 결정 가능.
    # 그래도 실패하면 catch 절에서 median fallback 으로 처리.

    try:
        import asyncio
        from v2.judge_agent import deliberate_post_debate

        # rounds → JSON serializable dict
        rounds_dump = [r.model_dump() for r in rounds]

        # asyncio.run 은 새 event loop 생성 — run_debate 가 ThreadPoolExecutor 워커에서
        # 돌고 있으면 안전. (AG2 내부에서 별도 loop 안 쓰므로 충돌 없음)
        result = asyncio.run(
            deliberate_post_debate(
                item_number=req.item_number,
                item_name=req.item_name,
                transcript_slice=req.transcript[:2500] if req.transcript else "",
                debate_rounds=rounds_dump,
                initial_positions=dict(req.initial_positions),
                fallback_score=median_score,
                fallback_reason=median_rationale[:120],
                # ★ 2026-05-07: 판사 LLM 도 프론트 모델 드롭다운 적용.
                bedrock_model_id=getattr(req, "bedrock_model_id", None),
            )
        )
    except Exception as exc:
        # LLMTimeoutError 는 deliberate_post_debate 안에서 raise → 여기로 올라옴
        if "LLMTimeoutError" in type(exc).__name__:
            raise
        failure = f"{type(exc).__name__}: {str(exc)[:160]}"
        logger.exception("post-debate judge #%d 호출 실패 → median fallback | %s", req.item_number, failure)
        return {
            "final_score": median_score,
            "final_rationale": median_rationale,
            "converged": median_converged,
            "merge_rule": median_merge_rule,
            "judge_score": None,
            "judge_reasoning": None,
            "judge_failure_reason": failure,
            "deductions": [],
            "evidence": [],
        }

    judge_used = bool(result.get("judge_used"))
    judge_score_raw = result.get("final_score")
    judge_reasoning = result.get("reasoning") or None
    judge_failure_reason = result.get("judge_failure_reason")
    judge_deductions = result.get("deductions") or []
    judge_evidence = result.get("evidence") or []
    judge_human_cases = result.get("human_cases_meta") or []

    # judge_used=True 라도 final_score 가 None 이면 무효 — judge 로 promote 불가.
    # 이 경우 reasoning 은 채워져있지만 점수 없이는 메인 결정 불가능 → fallback 처리.
    if judge_used and judge_score_raw is None:
        logger.warning(
            "post-debate judge #%d: judge_used=True 이지만 final_score=None → fallback 처리",
            req.item_number,
        )
        judge_used = False
        judge_failure_reason = judge_failure_reason or "judge_score_missing"

    logger.info(
        "post-debate judge #%d → judge_used=%s, judge_score=%s, merge_rule=%s",
        req.item_number,
        judge_used,
        judge_score_raw,
        "judge_post_debate" if judge_used else median_merge_rule,
    )

    # SSE 이벤트 — 프론트가 판사 판정을 별도 표시할 수 있도록
    _safe_event(
        on_event,
        "judge_post_debate",
        {
            "discussion_id": discussion_id,
            "node_id": node_id,
            "item_number": req.item_number,
            "item_name": req.item_name,
            "max_score": req.max_score,
            "judge_score": float(judge_score_raw) if judge_score_raw is not None else None,
            "judge_reasoning": judge_reasoning,
            "deductions": judge_deductions,
            "evidence": judge_evidence,
            "judge_used": judge_used,
            "judge_failure_reason": judge_failure_reason,
            "chosen_evaluator": result.get("chosen_evaluator"),
            "override_hint": result.get("override_hint"),
            "mandatory_human_review": result.get("mandatory_human_review", False),
            "median_score": float(median_score) if median_score is not None else None,
            "median_rationale": median_rationale,
            "human_cases": judge_human_cases,
        },
    )

    # 판사 호출 성공 시 메인 본문 = 판사 결정. 실패 시에만 median fallback.
    if judge_used and judge_score_raw is not None:
        return {
            "final_score": judge_score_raw,
            "final_rationale": judge_reasoning or median_rationale,
            "converged": True,
            "merge_rule": "judge_post_debate",
            "judge_score": judge_score_raw,
            "judge_reasoning": judge_reasoning,
            "judge_failure_reason": None,
            "deductions": judge_deductions,
            "evidence": judge_evidence,
            "human_cases_meta": judge_human_cases,
        }
    return {
        "final_score": median_score,
        "final_rationale": median_rationale,
        "converged": median_converged,
        "merge_rule": median_merge_rule,
        "judge_score": None,
        "judge_reasoning": None,
        "judge_failure_reason": judge_failure_reason or "judge_used_false",
        "deductions": [],
        "evidence": [],
        "human_cases_meta": judge_human_cases,
    }


def _decide_final(rounds: list[RoundRecord], req: DebateRequest) -> tuple[int | None, str, bool, str]:
    """rounds → (final_score, rationale, converged, merge_rule).

    우선순위:
      1. 마지막 verdict.consensus == True AND 실제 3-persona turns 점수 전원 일치 →
         verdict.score 채택 (merge_rule="consensus", converged=True).
      2. Moderator claim consensus 지만 실제 점수 spread>0 → median_vote 로 강제 다운그레이드
         (AG2 Moderator hallucination 보정).
      3. consensus 미달 → 마지막 라운드 turns 의 median (merge_rule="median_vote"), converged=False.
      4. rounds 가 비면 initial_positions median (merge_rule="fallback_median").

    최종 점수는 모두 snap_score_v2 통과.
    """
    if not rounds:
        scores = [int(v) for v in req.initial_positions.values() if isinstance(v, (int, float))]
        if not scores:
            return None, "[판정 실패] 라운드/초기값 모두 비어있음", False, "fallback_median"
        median = int(round(statistics.median(scores)))
        snapped = snap_score_v2(req.item_number, median)
        return snapped, f"[fallback] 라운드 없음 → initial median={median} → snap={snapped}", False, "fallback_median"

    last = rounds[-1]

    # Moderator 가 consensus 주장 시 — 실제 점수 전원 일치 여부 검증
    if last.verdict.consensus and last.verdict.score is not None:
        all_turn_scores = [int(t.score) for t in last.turns] if last.turns else []
        # round_no 증가 실패로 두 라운드 turns 가 합쳐진 경우 마지막 N개만 사용
        persona_count = len({t.persona for t in last.turns}) if last.turns else len(PERSONA_ORDER)
        persona_count = persona_count or len(PERSONA_ORDER)
        if len(all_turn_scores) > persona_count:
            turn_scores = all_turn_scores[-persona_count:]
        else:
            turn_scores = all_turn_scores
        all_match = len(turn_scores) >= 2 and len(set(turn_scores)) == 1 and turn_scores[0] == int(last.verdict.score)
        if all_match:
            snapped = snap_score_v2(req.item_number, int(last.verdict.score))
            return (
                snapped,
                f"[consensus] 라운드 {last.round} 만장일치 (점수={snapped}) — {last.verdict.rationale}",
                True,
                "consensus",
            )
        # Moderator hallucination — spread 있음에도 consensus 주장. median_vote 로 강제.
        if turn_scores:
            median = int(round(statistics.median(turn_scores)))
            snapped = snap_score_v2(req.item_number, median)
            return (
                snapped,
                (
                    f"[median_vote] Moderator 는 consensus 판정했으나 실제 점수 spread>0 "
                    f"(turns={turn_scores}) — median={median} → snap={snapped} 강제 적용"
                ),
                False,
                "median_vote",
            )

    if last.turns:
        all_turn_scores = [int(t.score) for t in last.turns]
        # ── Moderator 가 라운드 사이 verdict 를 못 내서 두 라운드 turns 가 한 RoundRecord 로
        # 합쳐진 경우 (round_no 미증가) → 마지막 N (=PERSONA_COUNT) turns 만 본다.
        # 페르소나당 마지막 발화 = 최종 입장. 그 N개가 전원 일치면 consensus.
        persona_count = len({t.persona for t in last.turns}) or len(PERSONA_ORDER)
        if len(all_turn_scores) > persona_count:
            tail_scores = all_turn_scores[-persona_count:]
        else:
            tail_scores = all_turn_scores
        median = int(round(statistics.median(tail_scores)))
        snapped = snap_score_v2(req.item_number, median)
        # turns 점수가 전원 일치하면 consensus 로 승격 (Moderator 가 놓쳤을 경우 보정)
        if len(set(tail_scores)) == 1:
            return (
                snapped,
                f"[consensus] 마지막 라운드 {persona_count}명 점수 전원 일치 ({tail_scores[0]}) — Moderator 판정과 별개로 자동 인정",
                True,
                "consensus",
            )
        return (
            snapped,
            f"[median_vote] 합의 미달 — 마지막 라운드 persona turns={tail_scores} median={median} → snap={snapped}",
            False,
            "median_vote",
        )

    # 극단 케이스 — turns 도 비면 verdict.score 사용
    if last.verdict.score is not None:
        snapped = snap_score_v2(req.item_number, int(last.verdict.score))
        return snapped, f"[verdict_fallback] turns 없음, verdict.score={snapped}", False, "median_vote"

    return None, "[판정 실패] 라운드 있으나 점수 없음", False, "fallback_median"


def _debate_final_payload(rec: DebateRecord) -> dict[str, Any]:
    """CLAUDE.md 명시 포맷 — ``debate_final`` SSE 페이로드.

    프론트 DiscussionModal 이 per-item 배점 (max_score) 을 정확히 표시할 수 있도록
    rec.max_score 를 payload 에 포함. 누락 시 UI 는 카테고리 합산 (예: 12점) 으로
    폴백하여 잘못된 "5/12" 같은 표시가 나올 수 있음.
    """
    return {
        "item_number": rec.item_number,
        "item_name": rec.item_name,
        "max_score": rec.max_score,
        "final_score": rec.final_score,
        "converged": rec.converged,
        "rounds_used": rec.rounds_used,
        "rationale": rec.final_rationale,
        # 결정 방식 — UI 가 "만장일치" / "중위값(median)" / "AG2 실패" 라벨 분기에 사용
        "merge_rule": rec.merge_rule,
        # 판사 출력 — 메인 본문과 분리. 프론트가 별도 "🎭 판사 결정" 카드로 표시.
        "judge_score": rec.judge_score,
        "judge_reasoning": rec.judge_reasoning,
        "judge_failure_reason": rec.judge_failure_reason,
        "judge_deductions": list(rec.judge_deductions or []),
        "judge_evidence": list(rec.judge_evidence or []),
    }
