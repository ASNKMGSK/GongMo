# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Understanding evaluation nodes for the QA LangGraph pipeline.

Provides three evaluation functions and a combined ``understanding_node``
entry point used by the graph:

- evaluate_listening(state)       — QA Item #3 (경청/말겹침/말자름, 5pt) — rule-based (SageMaker)
- _llm_evaluate_listening(state)  — QA Item #3 LLM 경로 (Bedrock)
- evaluate_empathy_response(state) — QA Item #4 (호응 및 공감, 5pt) — LLM-based
- evaluate_hold_mention(state)    — QA Item #5 (대기 멘트, 5pt) — rule-based (SageMaker)
- _llm_evaluate_hold_mention(state) — QA Item #5 LLM 경로 (Bedrock)

Pre-analysis logic detects speech-overlap markers, empathy/rapport expressions,
and hold/wait guidance patterns from the transcript text.
"""

# =============================================================================
# 경청 및 소통 평가 노드 (understanding.py)
# =============================================================================
# 이 모듈은 QA 평가 파이프라인에서 "경청 및 소통" 영역을 담당합니다.
# 총 3개의 QA 평가 항목을 처리합니다 (총 15점):
#
#   항목 #3: 경청(말겹침/말자름) (최대 5점) — 규칙 기반
#     - 5점: 말겹침/말자름 없이 경청
#     - 3점: 말겹침 또는 중간개입 1회 발생
#     - 0점: 말자름 1회 이상 또는 말겹침 2회 이상 발생
#     - ※ STT 전사 시 화자 구분 및 겹침 구간이 표기된 경우에 한해 평가. 미표기 시 만점 처리
#
#   항목 #4: 호응 및 공감 (최대 5점) — LLM 기반 (맥락 판단 필요)
#     - 5점: 상황에 맞는 다양한 호응/공감 표현 활용 (1회 이상)
#     - 3점: 단순 "네" 위주의 호응으로 공감이 미흡
#     - 0점: 호응/공감 표현 전혀 없음 또는 상황에 맞지 않는 호응
#
#   항목 #5: 대기 멘트 (최대 5점) — 규칙 기반
#     - 5점: 대기 전 양해 + 대기 후 감사 멘트 모두 진행 (또는 대기 미발생)
#     - 3점: 대기 전 또는 후 멘트 중 1가지 누락
#     - 0점: 양해 멘트 없이 대기 발생
# =============================================================================

from __future__ import annotations

import asyncio
import logging
import re
from langchain_core.messages import HumanMessage, SystemMessage
from nodes.llm import LLMTimeoutError, get_chat_model, invoke_and_parse
from nodes.skills.constants import SIMPLE_RESPONSE_PATTERNS
from nodes.skills.deduction_log import build_deduction_log_from_evaluations
from nodes.skills.error_results import build_llm_failure_result
from nodes.skills.node_context import NodeContext
from nodes.skills.pattern_matcher import PatternMatcher
from nodes.skills.reconciler import reconcile, snap_score
from state import QAState
from typing import Any


logger = logging.getLogger(__name__)

# Module-level PatternMatcher instance (stateless, safe to share)
_pm = PatternMatcher()


# ---------------------------------------------------------------------------
# 사전 분석(Pre-analysis) 헬퍼 함수
# ---------------------------------------------------------------------------


def _detect_speech_overlap(transcript: str) -> list[dict]:
    """Detect speech overlap / interruption markers in the transcript.

    Delegates to PatternMatcher.detect_speech_overlap().
    """
    pm_result = _pm.detect_speech_overlap(transcript)
    return pm_result["overlaps"]


def _detect_empathy_expressions(transcript: str) -> list[dict]:
    """Detect agent empathy / rapport expressions.

    Delegates to PatternMatcher.count_empathy() for the empathy portion.
    """
    pm_result = _pm.count_empathy(transcript)
    return pm_result["patterns_found"]


def _detect_simple_responses(transcript: str) -> list[dict]:
    """Detect simple '네' only responses — 상담사(agent) 발화만 대상.

    고객의 '네네네' 응답은 평가 대상이 아니므로 반드시 '상담사:' prefix 로
    시작하는 라인만 검출한다. 과거에는 speaker 구분 없이 전체 transcript 에서
    '네' 를 찾아 감점 근거로 사용했으나, 이는 고객 발화를 상담사의 단순 응대로
    오판정하는 버그의 원인이었다.
    """
    findings: list[dict] = []
    lines = transcript.strip().split("\n")
    turn_number = 0
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        turn_number += 1
        # 상담사 발화만 감점 대상. 고객 발화("고객: 네네네") 는 건너뜀.
        if not line_stripped.startswith("상담사:"):
            continue
        for pattern in SIMPLE_RESPONSE_PATTERNS:
            if re.search(pattern, line_stripped, re.IGNORECASE):
                findings.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                break
    return findings


def _detect_hold_guidance(transcript: str) -> dict[str, list[dict]]:
    """Detect hold/wait guidance patterns (before and after hold).

    Delegates to PatternMatcher.detect_hold_mentions().
    """
    pm_result = _pm.detect_hold_mentions(transcript)
    return {
        "before": pm_result["before"],
        "after": pm_result["after"],
        "silence_markers": pm_result["silence"],
    }


# ---------------------------------------------------------------------------
# LLM 시스템 프롬프트 (항목 #4만 LLM 사용)
# ---------------------------------------------------------------------------


def _get_empathy_response_system_prompt(backend: str | None = None, tenant_id: str = "") -> str:
    from prompts import load_prompt

    return load_prompt("item_04_empathy", tenant_id=tenant_id, backend=backend)


# ---------------------------------------------------------------------------
# 규칙 기반 채점 함수 (#3, #5)
# ---------------------------------------------------------------------------


def _score_listening(overlap_count: int) -> tuple[int, list[dict]]:
    """Rule-based scoring for #3 경청(말겹침/말자름).

    overlap_count 0 → 5, 1 → 3, 2+ → 0.
    """
    if overlap_count == 0:
        return 5, []
    elif overlap_count == 1:
        return 3, [{"reason": "말겹침/동시발화 1회 감지", "points": 2, "evidence_ref": ""}]
    else:
        return 0, [{"reason": f"말겹침/동시발화 {overlap_count}회 감지 (2회 이상)", "points": 5, "evidence_ref": ""}]


def _score_hold_mention(before_count: int, after_count: int, hold_detected: bool) -> tuple[int, list[dict]]:
    """Rule-based scoring for #5 대기 멘트.

    No hold detected → 5. before+after both → 5. One only → 3. None → 0.
    """
    if not hold_detected:
        return 5, []

    has_before = before_count > 0
    has_after = after_count > 0

    if has_before and has_after:
        return 5, []
    elif has_before or has_after:
        missing = "사후 감사 멘트" if not has_after else "사전 양해 멘트"
        return 3, [{"reason": f"대기 멘트 1가지 누락: {missing}", "points": 2, "evidence_ref": ""}]
    else:
        return 0, [{"reason": "양해 멘트 없이 대기 발생", "points": 5, "evidence_ref": ""}]


# ---------------------------------------------------------------------------
# 노드 평가 함수
# ---------------------------------------------------------------------------


def evaluate_listening(state: QAState) -> dict[str, Any]:
    """Evaluate listening quality and speech overlap (rule-based, no LLM).

    QA Item #3 -- 경청(말겹침/말자름), max 5 points.
    Scoring: 5/3/0.

    Returns {"evaluations": [result]} for operator.add merge.
    """
    assignment = state.get("agent_turn_assignments", {}).get("understanding", {})
    transcript = assignment.get("text") or state.get("transcript", "")

    logger.info(f"evaluate_listening: transcript_len={len(transcript)}")

    if not transcript:
        return {
            "evaluations": [
                build_llm_failure_result("understanding-agent", "No transcript provided for evaluation.")
            ]
        }

    overlaps = _detect_speech_overlap(transcript)
    overlap_count = len(overlaps)
    score, deductions = _score_listening(overlap_count)

    # 오버랩 인스턴스가 있으면 deduction에 turn_ref 보강
    if deductions and overlaps:
        deductions[0]["evidence_ref"] = f"turn_{overlaps[0]['turn']}"

    logger.info(f"Listening: overlap_count={overlap_count} → score={score}")

    return {
        "evaluations": [
            {
                "status": "success",
                "agent_id": "understanding-agent",
                "evaluation": {
                    "item_number": 3,
                    "item_name": "경청(말겹침/말자름)",
                    "max_score": 5,
                    "score": score,
                    "deductions": deductions,
                    "evidence": [{"turn": o["turn"], "text": o["text"]} for o in overlaps],
                    "confidence": 0.95,
                    "details": {
                        "overlap_count": overlap_count,
                        "overlap_instances": overlaps,
                        "stt_markers_present": overlap_count > 0,
                    },
                },
            }
        ]
    }


async def evaluate_empathy_response(state: QAState) -> dict[str, Any]:
    """Evaluate empathy and rapport response quality (LLM-based).

    QA Item #4 -- 호응 및 공감, max 5 points.
    Scoring: 5/3/0.

    Returns {"evaluations": [result]} for operator.add merge.
    """
    assignment = state.get("agent_turn_assignments", {}).get("understanding", {})
    transcript = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])
    consultation_type = state.get("consultation_type", "general")

    logger.info(f"evaluate_empathy_response: type='{consultation_type}', transcript_len={len(transcript)}")

    if not transcript:
        return {
            "evaluations": [
                build_llm_failure_result("understanding-agent", "No transcript provided for evaluation.")
            ]
        }

    # 사전 분석
    if assigned_turns:
        agent_texts = [t["text"] for t in assigned_turns if t["speaker"] == "agent"]
        agent_transcript = "\n".join(agent_texts)
        empathy_findings = _detect_empathy_expressions(agent_transcript) if agent_texts else []
        simple_findings = _detect_simple_responses(transcript) if agent_texts else []
    else:
        empathy_findings = _detect_empathy_expressions(transcript)
        simple_findings = _detect_simple_responses(transcript)
    empathy_count = len(empathy_findings)
    simple_count = len(simple_findings)

    logger.info(f"Pre-analysis: empathy_expressions={empathy_count}, simple_responses={simple_count}")

    # LLM 프롬프트 구성
    if assigned_turns:
        numbered_text = "\n".join(f"[Turn {t['turn_id']}] {t['text']}" for t in assigned_turns)
        transcript_for_llm = numbered_text
    else:
        transcript_for_llm = transcript
    user_message = f"## Consultation Type\n{consultation_type}\n\n"
    user_message += f"## Transcript\n{transcript_for_llm}\n\n"
    user_message += "## Pre-Analysis Results\n"
    user_message += f"- Empathy/rapport expressions detected (다양한 호응/공감 횟수): {empathy_count}\n"
    if empathy_findings:
        user_message += "- Empathy instances:\n"
        for e in empathy_findings:
            user_message += f"  - Turn {e['turn']}: {e['text']}\n"
    user_message += f"- Simple '네' responses detected (단순 호응 횟수): {simple_count}\n"
    if simple_findings:
        user_message += "- Simple response instances:\n"
        for s in simple_findings:
            user_message += f"  - Turn {s['turn']}: {s['text']}\n"
    user_message += "\n"
    user_message += (
        "## Instructions\n"
        "Evaluate for 호응 및 공감 (QA Item #4). Consider pre-analysis above.\n"
        "\n"
        "**매우 중요 — 평가 대상 화자**:\n"
        "- 이 항목은 **상담사(agent)** 의 호응/공감 표현만 평가합니다.\n"
        "- 고객(customer) 의 '네네네', '네', '어' 같은 응답은 평가 대상도 아니고 감점 근거도 아닙니다.\n"
        "- evidence 배열에는 **반드시 상담사 발화만** 인용하세요. 고객 발화를 evidence 로 쓰면 오답입니다.\n"
        "- '[Turn N]' 으로 시작하는 각 줄에서 '상담사:' prefix 를 확인하고 상담사 발화만 참조하세요.\n"
        "\n"
        "**점수 기준**:\n"
        "- '네네네' type mechanical repetitions (상담사의 기계적 반복) do NOT count as genuine empathy.\n"
        "- 다양한 호응/공감 표현 1회 이상 활용 → 5점\n"
        "- 상담사가 단순 '네' 위주의 호응만 사용 → 3점\n"
        "- 상담사의 호응/공감 전혀 없음 또는 부적절 → 0점\n\n"
        "Evaluate and return JSON per the system prompt format."
    )

    backend = state.get("llm_backend")
    bedrock_model_id = state.get("bedrock_model_id")
    llm = get_chat_model(max_tokens=2048, backend=backend, bedrock_model_id=bedrock_model_id)
    try:
        evaluation = await invoke_and_parse(
            llm,
            [
                SystemMessage(
                    content=_get_empathy_response_system_prompt(
                        backend, tenant_id=(state.get("tenant") or {}).get("tenant_id", ""),
                    ),
                ),
                HumanMessage(content=user_message),
            ],
        )
    except LLMTimeoutError:
        # 240초 초과 — 파이프라인 중단하고 프론트에 알림 전송
        raise
    except Exception as e:
        logger.warning("Item #4 LLM failed, fallback to rule: %s", e)
        if empathy_count >= 1:
            fb_score, fb_deductions = 5, []
        elif simple_count >= 1:
            fb_score = 3
            fb_deductions = [{"reason": "단순 '네' 위주의 호응 (LLM 실패 — 규칙 폴백)", "points": 2, "evidence_ref": ""}]
        else:
            fb_score = 0
            fb_deductions = [{"reason": "호응/공감 표현 미감지 (LLM 실패 — 규칙 폴백)", "points": 5, "evidence_ref": ""}]
        return {
            "evaluations": [
                {
                    "status": "success",
                    "agent_id": "understanding-agent",
                    "evaluation": {
                        "item_number": 4,
                        "item_name": "호응 및 공감",
                        "max_score": 5,
                        "score": fb_score,
                        "deductions": fb_deductions,
                        "evidence": [{"turn": e["turn"], "text": e["text"]} for e in empathy_findings[:3]],
                        "confidence": 0.6,
                        "details": {"empathy_count": empathy_count, "simple_count": simple_count, "fallback": True},
                    },
                }
            ]
        }

    score = snap_score(4, evaluation.get("score", 0))

    # score × deductions 산술 보정 (LLM hallucination 방어)
    rec = reconcile(
        item_number=4, score=score, max_score=5,
        deductions=evaluation.get("deductions", []),
    )
    if rec.note:
        evaluation["deductions"] = rec.deductions
        score = rec.score

    return {
        "evaluations": [
            {
                "status": "success",
                "agent_id": "understanding-agent",
                "evaluation": {
                    "item_number": 4,
                    "item_name": "호응 및 공감",
                    "max_score": 5,
                    "score": score,
                    "deductions": evaluation.get("deductions", []),
                    "evidence": evaluation.get("evidence", []),
                    "confidence": evaluation.get("confidence", 0.85),
                    "details": {
                        "empathy_expressions_found": evaluation.get("empathy_expressions_found", []),
                        "simple_response_only": evaluation.get("simple_response_only", False),
                        "empathy_count": empathy_count,
                        "simple_count": simple_count,
                    },
                },
            }
        ]
    }


def evaluate_hold_mention(state: QAState) -> dict[str, Any]:
    """Evaluate hold/wait mention quality (rule-based, no LLM).

    QA Item #5 -- 대기 멘트, max 5 points.
    Scoring: 5/3/0.

    Returns {"evaluations": [result]} for operator.add merge.
    """
    assignment = state.get("agent_turn_assignments", {}).get("understanding", {})
    transcript = assignment.get("text") or state.get("transcript", "")

    logger.info(f"evaluate_hold_mention: transcript_len={len(transcript)}")

    if not transcript:
        return {
            "evaluations": [
                build_llm_failure_result("understanding-agent", "No transcript provided for evaluation.")
            ]
        }

    hold_findings = _detect_hold_guidance(transcript)
    before_count = len(hold_findings["before"])
    after_count = len(hold_findings["after"])
    silence_count = len(hold_findings["silence_markers"])
    hold_detected = before_count > 0 or after_count > 0 or silence_count > 0

    score, deductions = _score_hold_mention(before_count, after_count, hold_detected)

    # 대기 멘트 감점 시 관련 턴 번호 보강
    if deductions and hold_findings.get("silence_markers"):
        deductions[0]["evidence_ref"] = f"turn_{hold_findings['silence_markers'][0].get('turn', '?')}"
    elif deductions and hold_findings.get("before"):
        deductions[0]["evidence_ref"] = f"turn_{hold_findings['before'][0].get('turn', '?')}"

    logger.info(
        f"Hold mention: hold_detected={hold_detected}, before={before_count}, "
        f"after={after_count}, silence={silence_count} → score={score}"
    )

    return {
        "evaluations": [
            {
                "status": "success",
                "agent_id": "understanding-agent",
                "evaluation": {
                    "item_number": 5,
                    "item_name": "대기 멘트",
                    "max_score": 5,
                    "score": score,
                    "deductions": deductions,
                    "evidence": (
                        [{"turn": h["turn"], "text": h["text"]} for h in hold_findings["before"]]
                        + [{"turn": h["turn"], "text": h["text"]} for h in hold_findings["after"]]
                    ),
                    "confidence": 0.95,
                    "details": {
                        "hold_detected": hold_detected,
                        "before_hold_mention": before_count > 0,
                        "after_hold_mention": after_count > 0,
                        "before_count": before_count,
                        "after_count": after_count,
                    },
                },
            }
        ]
    }


# ---------------------------------------------------------------------------
# LLM 기반 평가 함수 (#3, #5) — backend="bedrock" 경로
# ---------------------------------------------------------------------------


def _format_turns_for_llm(turns: list[dict]) -> str:
    lines = []
    for t in turns:
        tid = t.get("turn_id", t.get("turn", "?"))
        lines.append(f"[Turn {tid}] {t.get('speaker', 'unknown')}: {t.get('text', '')}")
    return "\n".join(lines)


async def _llm_evaluate_listening(state: QAState) -> dict[str, Any]:
    """LLM 기반 경청(말겹침/말자름) 평가 (#3) — backend="bedrock" 경로."""
    from prompts import load_prompt

    backend = state.get("llm_backend")
    bedrock_model_id = state.get("bedrock_model_id")
    tenant_id = (state.get("tenant") or {}).get("tenant_id", "")
    assignment = state.get("agent_turn_assignments", {}).get("understanding", {})
    transcript = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])

    if not transcript:
        return {
            "evaluations": [
                build_llm_failure_result("understanding-agent", "No transcript provided for evaluation.")
            ]
        }

    overlaps = _detect_speech_overlap(transcript)
    overlap_count = len(overlaps)

    try:
        system_prompt = load_prompt(
            "item_03_listening", tenant_id=tenant_id, include_preamble=True, backend=backend,
        )
        if assigned_turns:
            transcript_for_llm = "\n".join(
                f"[Turn {t.get('turn_id', '?')}] {t.get('speaker', '?')}: {t.get('text', '')}"
                for t in assigned_turns
            )
        else:
            transcript_for_llm = transcript
        user_message = f"## Transcript\n{transcript_for_llm}\n\n"
        user_message += "## Pre-Analysis Results\n"
        user_message += f"- STT 감지 말겹침/중첩 마커 개수: {overlap_count}\n"
        if overlaps:
            user_message += "- Overlap instances:\n"
            for o in overlaps:
                user_message += f"  - Turn {o.get('turn', '?')}: {o.get('text', '')}\n"
        user_message += "\n## Instructions\nEvaluate item #3 경청(말겹침/말자름) per the system rules."

        llm = get_chat_model(
            temperature=0.1, max_tokens=1024, backend=backend, bedrock_model_id=bedrock_model_id,
        )
        result = await invoke_and_parse(
            llm, [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #3 LLM failed, falling back to rule: %s", e)
        return evaluate_listening(state)

    score = snap_score(3, result.get("score", 0))
    logger.info("Listening (LLM): score=%s, overlap_count=%s", score, overlap_count)

    return {
        "evaluations": [
            {
                "status": "success",
                "agent_id": "understanding-agent",
                "evaluation": {
                    "item_number": 3,
                    "item_name": "경청(말겹침/말자름)",
                    "max_score": 5,
                    "score": int(score),
                    "deductions": result.get("deductions", []),
                    "evidence": result.get("evidence", []),
                    "confidence": float(result.get("confidence", 0.85)),
                    "summary": result.get("summary", ""),
                    "details": {
                        "backend": "bedrock",
                        "llm_based": True,
                        "overlap_count": overlap_count,
                        "overlap_instances": overlaps,
                    },
                },
            }
        ]
    }


async def _llm_evaluate_hold_mention(state: QAState) -> dict[str, Any]:
    """LLM 기반 대기 멘트 평가 (#5) — backend="bedrock" 경로."""
    from prompts import load_prompt

    backend = state.get("llm_backend")
    bedrock_model_id = state.get("bedrock_model_id")
    assignment = state.get("agent_turn_assignments", {}).get("understanding", {})
    transcript = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])

    if not transcript:
        return {
            "evaluations": [
                build_llm_failure_result("understanding-agent", "No transcript provided for evaluation.")
            ]
        }

    hold_findings = _detect_hold_guidance(transcript)
    before_count = len(hold_findings["before"])
    after_count = len(hold_findings["after"])
    silence_count = len(hold_findings["silence_markers"])
    hold_detected = before_count > 0 or after_count > 0 or silence_count > 0

    _tenant_id = (state.get("tenant") or {}).get("tenant_id", "")
    try:
        system_prompt = load_prompt(
            "item_05_hold_mention", tenant_id=_tenant_id, include_preamble=True, backend=backend,
        )
        if assigned_turns:
            transcript_for_llm = "\n".join(
                f"[Turn {t.get('turn_id', '?')}] {t.get('speaker', '?')}: {t.get('text', '')}"
                for t in assigned_turns
            )
        else:
            transcript_for_llm = transcript
        user_message = f"## Transcript\n{transcript_for_llm}\n\n"
        user_message += "## Pre-Analysis Results\n"
        user_message += f"- 대기 상황 감지: {hold_detected}\n"
        user_message += f"- 대기 전 양해 멘트 후보: {before_count}개\n"
        if hold_findings["before"]:
            for h in hold_findings["before"]:
                user_message += f"  - Turn {h.get('turn', '?')}: {h.get('text', '')}\n"
        user_message += f"- 대기 후 감사 멘트 후보: {after_count}개\n"
        if hold_findings["after"]:
            for h in hold_findings["after"]:
                user_message += f"  - Turn {h.get('turn', '?')}: {h.get('text', '')}\n"
        user_message += f"- 침묵/공백 마커: {silence_count}개\n"
        if hold_findings["silence_markers"]:
            for h in hold_findings["silence_markers"]:
                user_message += f"  - Turn {h.get('turn', '?')}: {h.get('text', '')}\n"
        user_message += "\n## Instructions\nEvaluate item #5 대기 멘트 per the system rules."

        llm = get_chat_model(
            temperature=0.1, max_tokens=1024, backend=backend, bedrock_model_id=bedrock_model_id,
        )
        result = await invoke_and_parse(
            llm, [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #5 LLM failed, falling back to rule: %s", e)
        return evaluate_hold_mention(state)

    score = snap_score(5, result.get("score", 0))
    logger.info(
        "Hold mention (LLM): score=%s, hold_detected=%s, before=%s, after=%s, silence=%s",
        score, hold_detected, before_count, after_count, silence_count,
    )

    return {
        "evaluations": [
            {
                "status": "success",
                "agent_id": "understanding-agent",
                "evaluation": {
                    "item_number": 5,
                    "item_name": "대기 멘트",
                    "max_score": 5,
                    "score": int(score),
                    "deductions": result.get("deductions", []),
                    "evidence": result.get("evidence", []),
                    "confidence": float(result.get("confidence", 0.85)),
                    "summary": result.get("summary", ""),
                    "details": {
                        "backend": "bedrock",
                        "llm_based": True,
                        "hold_detected": hold_detected,
                        "before_count": before_count,
                        "after_count": after_count,
                        "silence_count": silence_count,
                    },
                },
            }
        ]
    }


# ---------------------------------------------------------------------------
# 통합 노드 진입점 (graph.py에서 호출)
# ---------------------------------------------------------------------------


async def understanding_node(state: QAState, ctx: NodeContext) -> dict[str, Any]:
    """Run all understanding-agent evaluations: items #3, #4, #5.

    Calls three internal evaluation functions in parallel and merges results.
    backend="bedrock" 이면 #3/#5 도 LLM 경로, 그 외(SageMaker 등)는 규칙 기반.
    Returns {"evaluations": [...], "deduction_log": [...]} — merged into state via operator.add.
    """
    del ctx  # NodeContext 슬롯 — 내부 evaluate_* 함수가 state.get 직접 사용 (assignment 우선 패턴 보존)
    # SageMaker/Bedrock 모두 LLM 경로 통일. 기존 rule-based `evaluate_listening` / `evaluate_hold_mention` 는
    # _legacy_sagemaker_pipeline/ 에 백업되어 있고, fallback 으로만 유지.
    results = await asyncio.gather(
        _llm_evaluate_listening(state),  # 항목 #3: 경청 — LLM
        evaluate_empathy_response(state),  # 항목 #4: 호응 및 공감 — LLM
        _llm_evaluate_hold_mention(state),  # 항목 #5: 대기 멘트 — LLM
    )

    merged: list[dict] = []
    for result in results:
        merged.extend(result.get("evaluations", []))

    deduction_log = build_deduction_log_from_evaluations(
        merged, "understanding", with_empty_fallback=True
    )
    return {"evaluations": merged, "deduction_log": deduction_log}
