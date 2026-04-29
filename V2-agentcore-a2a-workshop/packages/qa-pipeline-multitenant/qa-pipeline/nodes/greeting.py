# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Greeting evaluation nodes for the QA LangGraph pipeline.

Provides two evaluation functions and a combined ``greeting_node`` entry point:

- _evaluate_first_greeting(state)   — QA Item #1 (첫인사, 5pt)
- _evaluate_closing(state)          — QA Item #2 (끝인사, 5pt)

Pre-analysis logic detects greeting keywords, agent/customer names, and
organisation mentions in the first/last N turns of the transcript.
Scoring is entirely rule-based — no LLM calls.
"""

# =============================================================================
# 인사 예절 평가 노드 (greeting.py)
# =============================================================================
# 이 모듈은 QA 평가 파이프라인에서 "인사 예절" 영역을 담당합니다.
# 총 2개의 QA 평가 항목을 처리합니다 (총 10점):
#
#   항목 #1: 첫인사 (최대 5점)
#     - 평가요소: 인사말 + 소속 + 상담사명
#     - 채점: 5점(3요소 모두 포함) / 3점(1가지 누락) / 0점(2가지 이상 누락 또는 인사 미진행)
#     - 키워드: "안녕하세요", "감사합니다...전화", "고객센터입니다", 소속명, 상담사명
#
#   항목 #2: 끝인사 (최대 5점)
#     - 평가요소: 추가문의 확인 + 인사말 + 상담사명
#     - 채점: 5점(3요소 모두 진행) / 3점(1가지 누락 또는 추가문의 확인 누락)
#            / 0점(끝인사 미진행 또는 2가지 이상 누락)
#     - 키워드: "감사합니다", "좋은 하루", "더 궁금하신", "추가 문의", 상담사명
#
# 처리 흐름:
#   1. 전사록(transcript)을 턴(turn) 단위로 파싱
#   2. 정규식 기반 사전 분석(pre-analysis)으로 키워드 탐지
#   3. 사전 분석 결과에서 직접 규칙 기반 채점 (LLM 호출 없음)
#   4. 2개 항목을 asyncio.gather로 병렬 실행하여 결과 병합
# =============================================================================

from __future__ import annotations

import asyncio
import logging
import re
from langchain_core.messages import HumanMessage, SystemMessage
from nodes.skills.constants import CLOSING_ADDITIONAL_INQUIRY_PATTERNS
from nodes.skills.deduction_log import build_deduction_log_from_evaluations
from nodes.skills.error_results import build_llm_failure_result
from nodes.skills.node_context import NodeContext
from nodes.skills.pattern_matcher import PatternMatcher, parse_turns
from nodes.skills.reconciler import snap_score
from state import QAState
from typing import Any


logger = logging.getLogger(__name__)

# Module-level PatternMatcher instance (stateless, safe to share)
_pm = PatternMatcher()

# ---------------------------------------------------------------------------
# 상수 정의
# ---------------------------------------------------------------------------

# 첫인사 분석 시 전사록 앞쪽에서 스캔할 턴 수 (통상 5턴 이내에 인사 완료)
FIRST_N_TURNS = 5
# 끝인사 분석 시 전사록 뒤쪽에서 스캔할 턴 수 (마지막 10턴 이내)
LAST_N_TURNS = 10


# ---------------------------------------------------------------------------
# 전사록 파싱 헬퍼 함수
# ---------------------------------------------------------------------------


_parse_turns = parse_turns


def _match_patterns(text: str, patterns: list[str]) -> list[str]:
    """Return all patterns that match in the given text."""
    # 주어진 텍스트에서 정규식 패턴 목록 중 매칭되는 것을 모두 반환
    # 대소문자 무시(IGNORECASE)로 검색
    matched = []
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            matched.append(pattern)
    return matched


# ---------------------------------------------------------------------------
# 사전 분석(Pre-analysis) 헬퍼 함수
# ---------------------------------------------------------------------------
# 정규식으로 전사록을 미리 분석하는 함수들.
# 사전 분석 결과에서 직접 규칙 기반 점수를 산출한다.
# ---------------------------------------------------------------------------


def _preanalyze_first_greeting(turns: list[dict[str, str]]) -> dict[str, Any]:
    """Scan the first N turns for first-greeting elements.

    Delegates pattern detection to PatternMatcher.match_greeting().
    """
    # PatternMatcher expects turn dicts with "speaker", "text", and "turn" keys.
    # The turns from _parse_turns already have this shape (turn is str here).
    pm_result = _pm.match_greeting(turns, first_n=FIRST_N_TURNS)

    first_turns = turns[:FIRST_N_TURNS]

    return {
        "agent_greeting_found": pm_result["greeting_found"],
        "greeting_turn": pm_result["greeting_turn"],
        "greeting_text": pm_result["greeting_text"],
        "detected_keywords": pm_result["detected_keywords"],
        "first_turns_scanned": len(first_turns),
        "greeting_element": pm_result["greeting_found"],
        "affiliation_found": pm_result["affiliation_found"],
        "agent_name_found": pm_result["agent_name_found"],
    }


def _preanalyze_closing(turns: list[dict[str, str]]) -> dict[str, Any]:
    """Scan the last N turns for closing-greeting elements.

    Delegates pattern detection to PatternMatcher.match_closing().
    """
    pm_result = _pm.match_closing(turns, last_n=LAST_N_TURNS)

    last_turns = turns[-LAST_N_TURNS:] if len(turns) >= LAST_N_TURNS else turns

    # Recover additional_inquiry_text (not provided by PatternMatcher)
    additional_inquiry_text = ""
    if pm_result["additional_inquiry"]:
        for t in last_turns:
            if t["speaker"] != "agent":
                continue
            if _match_patterns(t["text"], CLOSING_ADDITIONAL_INQUIRY_PATTERNS):
                additional_inquiry_text = t["text"]
                break

    return {
        "closing_found": pm_result["closing_found"],
        "closing_turn": pm_result["closing_turn"],
        "closing_text": pm_result["closing_text"],
        "detected_keywords": pm_result["detected_keywords"],
        "additional_inquiry_found": pm_result["additional_inquiry"],
        "additional_inquiry_text": additional_inquiry_text,
        "customer_ended_first": pm_result["customer_ended_first"],
        "agent_name_mentioned": pm_result["agent_name_mentioned"],
        "last_turns_scanned": len(last_turns),
    }


# ---------------------------------------------------------------------------
# 규칙 기반 채점 함수
# ---------------------------------------------------------------------------


def _score_first_greeting(pre: dict[str, Any]) -> tuple[int, list[dict]]:
    """Rule-based scoring for first greeting (#1).

    3 elements: 인사말(greeting), 소속(affiliation), 상담사명(agent_name).
    Missing 0 → 5점, missing 1 → 3점, missing 2+ → 0점.

    Returns (score, deduction_reasons).
    """
    elements = {
        "greeting": pre["greeting_element"],
        "affiliation": pre["affiliation_found"],
        "agent_name": pre["agent_name_found"],
    }
    missing = [k for k, v in elements.items() if not v]
    missing_count = len(missing)

    # 턴 번호: 인사말 발견 시 해당 턴, 미발견 시 스캔 범위(도입부 1~3턴)
    g_turn = pre.get("greeting_turn")
    ref = f"turn_{g_turn}" if g_turn else f"turn_1_to_{pre.get('first_turns_scanned', 3)}"

    if missing_count == 0:
        return 5, []
    elif missing_count == 1:
        label_map = {"greeting": "인사말", "affiliation": "소속", "agent_name": "상담사명"}
        reason = f"첫인사 요소 1가지 누락: {label_map[missing[0]]}"
        return 3, [{"reason": reason, "points": 2, "evidence_ref": ref}]
    else:
        label_map = {"greeting": "인사말", "affiliation": "소속", "agent_name": "상담사명"}
        missing_labels = [label_map[m] for m in missing]
        reason = f"첫인사 요소 {missing_count}가지 누락: {', '.join(missing_labels)}"
        return 0, [{"reason": reason, "points": 5, "evidence_ref": ref}]


def _score_closing(pre: dict[str, Any]) -> tuple[int, list[dict]]:
    """Rule-based scoring for closing greeting (#2).

    3 elements: 추가문의 확인(additional_inquiry), 끝인사(closing_greeting), 상담사명(agent_name).
    Missing 0 → 5점, missing 1 → 3점, missing 2+ → 0점.

    Returns (score, deduction_reasons).
    """
    elements = {
        "additional_inquiry": pre["additional_inquiry_found"],
        "closing_greeting": pre["closing_found"],
        "agent_name": pre["agent_name_mentioned"],
    }
    missing = [k for k, v in elements.items() if not v]
    missing_count = len(missing)

    # 턴 번호: 끝인사 발견 시 해당 턴, 미발견 시 스캔 범위(종결부)
    c_turn = pre.get("closing_turn")
    ref = f"turn_{c_turn}" if c_turn else f"turn_{pre.get('last_turns_scanned', 'closing')}"

    if missing_count == 0:
        return 5, []
    elif missing_count == 1:
        label_map = {"additional_inquiry": "추가문의 확인", "closing_greeting": "끝인사", "agent_name": "상담사명"}
        reason = f"끝인사 요소 1가지 누락: {label_map[missing[0]]}"
        return 3, [{"reason": reason, "points": 2, "evidence_ref": ref}]
    else:
        label_map = {"additional_inquiry": "추가문의 확인", "closing_greeting": "끝인사", "agent_name": "상담사명"}
        missing_labels = [label_map[m] for m in missing]
        reason = f"끝인사 요소 {missing_count}가지 누락: {', '.join(missing_labels)}"
        return 0, [{"reason": reason, "points": 5, "evidence_ref": ref}]


# ---------------------------------------------------------------------------
# 개별 평가 함수
# ---------------------------------------------------------------------------


def _evaluate_first_greeting(state: QAState) -> dict[str, Any]:
    """Evaluate first greeting quality (rule-based, no LLM).

    QA Item #1 — 첫인사, max 5 points.
    Scoring: 5/3/0.

    Returns a single evaluation result dict.
    """
    # 선별 턴 할당 데이터 우선 사용, 없으면 전체 transcript 폴백
    assignment = state.get("agent_turn_assignments", {}).get("greeting", {})
    transcript = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])

    logger.info(f"_evaluate_first_greeting: transcript_len={len(transcript)}")

    if not transcript:
        return build_llm_failure_result("greeting-agent", "No transcript provided.")

    # 사전 분석: assigned_turns가 있으면 이미 파싱된 턴 데이터 직접 사용
    if assigned_turns:
        intro_turns = [
            {"speaker": t["speaker"], "text": t["text"], "turn": str(t["turn_id"])}
            for t in assigned_turns
            if t.get("segment") == "도입"
        ]
        if not intro_turns:
            intro_turns = [
                {"speaker": t["speaker"], "text": t["text"], "turn": str(t["turn_id"])} for t in assigned_turns
            ]
        turns = intro_turns
    else:
        turns = _parse_turns(transcript)

    pre = _preanalyze_first_greeting(turns)
    score, deductions = _score_first_greeting(pre)

    logger.info(
        f"First greeting: greeting={pre['greeting_element']}, "
        f"affiliation={pre['affiliation_found']}, agent_name={pre['agent_name_found']} → score={score}"
    )

    return {
        "status": "success",
        "agent_id": "greeting-agent",
        "evaluation": {
            "item_number": 1,
            "item_name": "첫인사",
            "max_score": 5,
            "score": score,
            "elements_detected": {
                "greeting": pre["greeting_element"],
                "affiliation": pre["affiliation_found"],
                "agent_name": pre["agent_name_found"],
            },
            "deductions": deductions,
            "evidence": (
                [{"turn": pre["greeting_turn"], "speaker": "agent", "text": pre["greeting_text"]}]
                if pre["agent_greeting_found"]
                else []
            ),
            "confidence": 0.95,
            "details": {"pre_analysis": pre},
        },
    }


def _evaluate_closing(state: QAState) -> dict[str, Any]:
    """Evaluate closing greeting quality (rule-based, no LLM).

    QA Item #2 — 끝인사, max 5 points.
    Scoring: 5/3/0.

    Returns a single evaluation result dict.
    """
    # 선별 턴 할당 데이터 우선 사용, 없으면 전체 transcript 폴백
    assignment = state.get("agent_turn_assignments", {}).get("greeting", {})
    transcript = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])

    logger.info(f"_evaluate_closing: transcript_len={len(transcript)}")

    if not transcript:
        return build_llm_failure_result("greeting-agent", "No transcript provided.")

    # 사전 분석: assigned_turns가 있으면 종결 구간 턴 사용
    if assigned_turns:
        closing_turns = [
            {"speaker": t["speaker"], "text": t["text"], "turn": str(t["turn_id"])}
            for t in assigned_turns
            if t.get("segment") == "종결"
        ]
        if not closing_turns:
            closing_turns = [
                {"speaker": t["speaker"], "text": t["text"], "turn": str(t["turn_id"])} for t in assigned_turns
            ]
        turns = closing_turns
    else:
        turns = _parse_turns(transcript)

    pre = _preanalyze_closing(turns)
    score, deductions = _score_closing(pre)

    logger.info(
        f"Closing: closing={pre['closing_found']}, additional_inquiry={pre['additional_inquiry_found']}, "
        f"agent_name={pre['agent_name_mentioned']}, customer_ended_first={pre['customer_ended_first']} → score={score}"
    )

    return {
        "status": "success",
        "agent_id": "greeting-agent",
        "evaluation": {
            "item_number": 2,
            "item_name": "끝인사",
            "max_score": 5,
            "score": score,
            "elements_detected": {
                "additional_inquiry": pre["additional_inquiry_found"],
                "closing_greeting": pre["closing_found"],
                "agent_name": pre["agent_name_mentioned"],
            },
            "customer_ended_first": pre["customer_ended_first"],
            "deductions": deductions,
            "evidence": (
                [{"turn": pre["closing_turn"], "speaker": "agent", "text": pre["closing_text"]}]
                if pre["closing_found"]
                else []
            ),
            "confidence": 0.95,
            "details": {"pre_analysis": pre},
        },
    }


# ---------------------------------------------------------------------------
# LLM 기반 평가 함수 (backend="bedrock" 경로 전용)
# ---------------------------------------------------------------------------


def _format_turns_for_llm(turns: list[dict]) -> str:
    """Format a list of assigned-turn dicts for the LLM user message."""
    lines = []
    for t in turns:
        tid = t.get("turn_id", t.get("turn", "?"))
        lines.append(f"turn_{tid} {t.get('speaker', 'unknown')}: {t.get('text', '')}")
    return "\n".join(lines)


async def _llm_evaluate_first_greeting(state: QAState) -> dict[str, Any]:
    """LLM 기반 첫인사 평가 (#1) — backend="bedrock" 경로."""
    from nodes.llm import LLMTimeoutError, get_chat_model, invoke_and_parse
    from prompts import load_prompt

    backend = state.get("llm_backend")
    bedrock_model_id = state.get("bedrock_model_id")
    tenant_id = (state.get("tenant") or {}).get("tenant_id", "")
    assignment = state.get("agent_turn_assignments", {}).get("greeting", {})
    transcript = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])

    if not transcript:
        return build_llm_failure_result("greeting-agent", "No transcript provided.")

    first_turns = assigned_turns[:5] if assigned_turns else []

    try:
        system_prompt = load_prompt(
            "item_01_greeting", tenant_id=tenant_id, include_preamble=True, backend=backend,
        )
        if first_turns:
            turns_text = _format_turns_for_llm(first_turns)
        else:
            turns_text = transcript
        user_message = (
            "## Input transcript (first 5 turns)\n"
            f"{turns_text}\n\n"
            "Evaluate item #1 첫인사 per the system rules. "
            "3요소(인사말/소속/상담사명) 충족 여부로 5/3/0 채점."
        )
        llm = get_chat_model(
            temperature=0.1, max_tokens=1024, backend=backend, bedrock_model_id=bedrock_model_id,
        )
        result = await invoke_and_parse(
            llm, [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #1 LLM failed, falling back to rule: %s", e)
        return _evaluate_first_greeting(state)

    score = snap_score(1, result.get("score", 0))

    logger.info("First greeting (LLM): score=%s", score)

    return {
        "status": "success",
        "agent_id": "greeting-agent",
        "evaluation": {
            "item_number": 1,
            "item_name": "첫인사",
            "max_score": 5,
            "score": int(score),
            "deductions": result.get("deductions", []),
            "evidence": result.get("evidence", []),
            "confidence": float(result.get("confidence", 0.85)),
            "summary": result.get("summary", ""),
            "details": {"backend": "bedrock", "llm_based": True},
        },
    }


async def _llm_evaluate_closing(state: QAState) -> dict[str, Any]:
    """LLM 기반 끝인사 평가 (#2) — backend="bedrock" 경로."""
    from nodes.llm import LLMTimeoutError, get_chat_model, invoke_and_parse
    from prompts import load_prompt

    backend = state.get("llm_backend")
    bedrock_model_id = state.get("bedrock_model_id")
    tenant_id = (state.get("tenant") or {}).get("tenant_id", "")
    assignment = state.get("agent_turn_assignments", {}).get("greeting", {})
    transcript = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])

    if not transcript:
        return build_llm_failure_result("greeting-agent", "No transcript provided.")

    # 마지막 상담사 5턴 (없으면 assigned_turns 마지막 5턴)
    agent_turns = [t for t in assigned_turns if t.get("speaker") == "agent"]
    last_turns = (agent_turns[-5:] if agent_turns else assigned_turns[-5:]) if assigned_turns else []

    try:
        system_prompt = load_prompt(
            "item_02_farewell", tenant_id=tenant_id, include_preamble=True, backend=backend,
        )
        if last_turns:
            turns_text = _format_turns_for_llm(last_turns)
        else:
            turns_text = transcript
        user_message = (
            "## Input transcript (last 5 agent turns)\n"
            f"{turns_text}\n\n"
            "Evaluate item #2 끝인사 per the system rules. "
            "3요소(마무리 인사/상담사명 재언급/소속 재언급) 충족 여부로 5/3/0 채점."
        )
        llm = get_chat_model(
            temperature=0.1, max_tokens=1024, backend=backend, bedrock_model_id=bedrock_model_id,
        )
        result = await invoke_and_parse(
            llm, [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #2 LLM failed, falling back to rule: %s", e)
        return _evaluate_closing(state)

    score = snap_score(2, result.get("score", 0))

    logger.info("Closing (LLM): score=%s", score)

    return {
        "status": "success",
        "agent_id": "greeting-agent",
        "evaluation": {
            "item_number": 2,
            "item_name": "끝인사",
            "max_score": 5,
            "score": int(score),
            "deductions": result.get("deductions", []),
            "evidence": result.get("evidence", []),
            "confidence": float(result.get("confidence", 0.85)),
            "summary": result.get("summary", ""),
            "details": {"backend": "bedrock", "llm_based": True},
        },
    }


# ---------------------------------------------------------------------------
# 통합 노드 진입점 (graph.py에서 호출)
# ---------------------------------------------------------------------------


async def greeting_node(state: QAState, ctx: NodeContext) -> dict[str, Any]:
    """Run all greeting evaluations: items #1, #2.

    Calls two internal evaluation functions in parallel and merges results.
    backend="bedrock" 이면 LLM 경로, 그 외(SageMaker 등)는 규칙 기반 경로.
    Returns {"evaluations": [...], "deduction_log": [...]} — merged into state via operator.add.
    """
    del ctx  # NodeContext 슬롯 — 본문 미사용 (assignment 우선 패턴 보존)

    # SageMaker/Bedrock 모두 LLM 경로 통일. 기존 rule-based 는 _legacy_sagemaker_pipeline/ 에 백업.
    # LLM 실패 시 rule fallback 은 개별 `_llm_evaluate_*` 내부에서 유지.
    results = await asyncio.gather(
        _llm_evaluate_first_greeting(state),  # 항목 #1: 첫인사 (LLM)
        _llm_evaluate_closing(state),  # 항목 #2: 끝인사 (LLM)
    )

    deduction_log = build_deduction_log_from_evaluations(
        list(results), "greeting", with_empty_fallback=True
    )
    return {"evaluations": list(results), "deduction_log": deduction_log}
