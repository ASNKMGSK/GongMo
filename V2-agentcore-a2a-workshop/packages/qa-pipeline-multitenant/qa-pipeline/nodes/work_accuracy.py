# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Work Accuracy evaluation node — 업무 정확도 (#15: 정확한 안내 10점, #16: 필수 안내 이행 5점).

상담사가 안내한 정보의 정확성과 필수 안내 사항 이행 여부를 평가한다.

Key checks:
- 오안내(잘못된 정보 안내) 발생 여부
- 정정/수정 발언 탐지
- 모순된 정보 안내 탐지
- 문의 유형별 필수 안내 사항(스크립트) 이행 여부

총 배점: 15점 (정확한 안내 10점 + 필수 안내 이행 5점)
"""

# ---------------------------------------------------------------------------
# [노드 개요]
# 이 노드는 QA 평가항목 #15 "정확한 안내"와 #16 "필수 안내 이행"을 평가한다.
# 상담사가 정확한 업무 지식을 기반으로 안내했는지, 필수 안내 사항을 누락 없이
# 전달했는지를 검사한다.
#
# [평가 기준]
#
#   #15 정확한 안내 (최대 10점, 채점: 10/5/0)
#     10점: 업무 지식에 기반하여 오안내 없이 정확한 정보 안내
#      5점: 부정확한 안내 있으나 내용이 미미하거나 즉시 정정
#      0점: 오안내 발생으로 정정 안내 필요
#
#   #16 필수 안내 이행 (최대 5점, 채점: 5/3/0)
#      5점: 문의 유형별 필수 안내 사항(스크립트)을 누락 없이 전달
#      3점: 필수 안내 사항 중 일부 누락
#      0점: 필수 안내 사항 미진행 또는 다수 누락
#
# [동작 흐름]
#   1. 사전 분석(regex): 정정 표현, 모순 패턴, 필수 안내 관련 표현 탐지
#   2. LLM 호출: 두 항목을 각각 개별 평가 (asyncio.gather 병렬)
#   3. JSON 파싱 → 항목별 점수 검증 → 평가 결과 반환
# ---------------------------------------------------------------------------

from __future__ import annotations

import asyncio
import logging
import re
from langchain_core.messages import HumanMessage, SystemMessage
from nodes.llm import LLMTimeoutError, get_chat_model, invoke_and_parse
from nodes.skills.deduction_log import build_deduction_log_from_pairs
from nodes.skills.error_results import build_llm_failure_result
from nodes.skills.evidence_builder import build_turn_evidence
from nodes.skills.node_context import NodeContext, build_user_message
from nodes.skills.pattern_matcher import detect_agent_patterns
from nodes.skills.reconciler import reconcile
from nodes.skills.scorer import Scorer
from state import QAState
from typing import Any


logger = logging.getLogger(__name__)

# Scorer instance for rule-based score validation per qa_rules.py
_scorer = Scorer()

# ---------------------------------------------------------------------------
# #15 정확한 안내 관련 패턴
# ---------------------------------------------------------------------------

# 정정/수정 표현 탐지 — 상담사가 이전 안내를 정정하는 패턴
CORRECTION_PATTERNS = [
    r"아\s*[,.]?\s*잘못",  # "아, 잘못 말씀드렸습니다"
    r"수정",  # "수정해서 말씀드리면..."
    r"정정",  # "정정 드리겠습니다"
    r"다시\s*말씀",  # "다시 말씀드리면..."
    r"틀렸",  # "아까 틀렸습니다"
    r"잘못\s*안내",  # "잘못 안내드렸습니다"
    r"오류",  # "오류가 있었습니다"
    r"착오",  # "착오가 있었습니다"
    r"혼동",  # "혼동이 있었습니다"
    r"죄송.*다시",  # "죄송합니다, 다시 확인하니..."
    r"확인.*결과.*다르",  # "확인 결과 다르게..."
    r"아닙니다.*맞는\s*건",  # "아닙니다, 맞는 건..."
]

# 모순 패턴 — 동일 상담 내에서 상반된 정보를 안내하는 경우 탐지 보조
CONTRADICTION_INDICATORS = [
    r"아까.*다르",  # "아까 말씀드린 것과 다르게..."
    r"앞서.*달리",  # "앞서 말씀드린 것과 달리..."
    r"방금.*아니라",  # "방금 말씀드린 게 아니라..."
    r"반대로",  # "반대로 말씀드렸습니다"
    r"거꾸로",  # "거꾸로 말씀드렸네요"
]

# ---------------------------------------------------------------------------
# #16 필수 안내 이행 관련 패턴
# ---------------------------------------------------------------------------

# 필수 안내 사항 관련 표현 — 스크립트 이행 여부 판단 보조
MANDATORY_SCRIPT_PATTERNS = [
    r"필수\s*안내",  # "필수 안내 사항 말씀드리겠습니다"
    r"안내\s*드려야\s*할",  # "안내 드려야 할 사항이..."
    r"고지\s*사항",  # "고지 사항 안내드립니다"
    r"유의\s*사항",  # "유의 사항 말씀드리면..."
    r"주의\s*사항",  # "주의 사항 안내드립니다"
    r"반드시\s*안내",  # "반드시 안내드려야 하는..."
    r"참고\s*사항",  # "참고 사항 말씀드리면..."
    r"약관\s*상",  # "약관상 안내드리면..."
    r"규정\s*상",  # "규정상 안내드리면..."
    r"동의",  # "동의 확인 진행하겠습니다"
    r"녹취",  # "녹취 안내드리겠습니다"
    r"불이익",  # "불이익이 발생할 수 있습니다"
    r"불완전\s*판매",  # "불완전 판매 방지를 위해..."
]


# ---------------------------------------------------------------------------
# 사전 분석 헬퍼 함수
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# LLM 시스템 프롬프트
# ---------------------------------------------------------------------------


def _get_accuracy_system_prompt(backend: str | None = None, tenant_id: str = "") -> str:
    from prompts import load_prompt

    return load_prompt("item_15_accuracy", tenant_id=tenant_id, backend=backend)


def _get_mandatory_script_system_prompt(backend: str | None = None, tenant_id: str = "") -> str:
    from prompts import load_prompt

    return load_prompt("item_16_mandatory_script", tenant_id=tenant_id, backend=backend)


# ---------------------------------------------------------------------------
# 개별 평가 함수
# ---------------------------------------------------------------------------


async def _evaluate_accuracy(
    transcript: str, consultation_type: str, rules: dict, pre_analysis: dict,
    intent_context: str = "", backend: str | None = None,
    bedrock_model_id: str | None = None, tenant_id: str = "",
) -> dict:
    """#15 정확한 안내 평가 (10점)."""
    user_message = build_user_message(
        consultation_type=consultation_type, transcript=transcript,
        rules=rules, intent_context=intent_context,
    )
    user_message += "## Pre-Analysis Results\n"
    user_message += f"- 정정/수정 표현 감지: {len(pre_analysis['corrections'])}건\n"
    for c in pre_analysis["corrections"]:
        user_message += f"  - Turn {c['turn']}: {c['text']}\n"
    user_message += f"- 모순 지표 감지: {len(pre_analysis['contradictions'])}건\n"
    for ct in pre_analysis["contradictions"]:
        user_message += f"  - Turn {ct['turn']}: {ct['text']}\n"
    user_message += "\n## Instructions\n"
    user_message += "정확한 안내 (QA Item #15)를 평가하세요.\n"
    user_message += "- 상담사가 안내한 정보가 정확한지 확인하세요.\n"
    user_message += "- 정정 발언이 있으면 오안내가 있었다는 의미입니다.\n"
    user_message += "- 통화 내 모순된 정보가 있는지 확인하세요.\n"

    llm = get_chat_model(
        max_tokens=2048, backend=backend, bedrock_model_id=bedrock_model_id,
    )
    return await invoke_and_parse(
        llm,
        [
            SystemMessage(content=_get_accuracy_system_prompt(backend, tenant_id=tenant_id)),
            HumanMessage(content=user_message),
        ],
    )


async def _evaluate_mandatory_script(
    transcript: str, consultation_type: str, rules: dict, pre_analysis: dict,
    intent_context: str = "", backend: str | None = None,
    bedrock_model_id: str | None = None, tenant_id: str = "",
) -> dict:
    """#16 필수 안내 이행 평가 (5점)."""
    user_message = build_user_message(
        consultation_type=consultation_type, transcript=transcript,
        rules=rules, intent_context=intent_context,
    )
    user_message += "## Pre-Analysis Results\n"
    user_message += f"- 필수 안내 관련 표현 감지: {len(pre_analysis['mandatory'])}건\n"
    for m in pre_analysis["mandatory"]:
        user_message += f"  - Turn {m['turn']}: {m['text']}\n"
    user_message += "\n## Instructions\n"
    user_message += "필수 안내 이행 (QA Item #16)을 평가하세요.\n"
    user_message += "- 상담 유형에 맞는 필수 안내 사항을 모두 이행했는지 확인하세요.\n"
    user_message += "- QA 규칙에 명시된 필수 안내 사항과 대조하세요.\n"

    llm = get_chat_model(
        max_tokens=1024, backend=backend, bedrock_model_id=bedrock_model_id,
    )
    return await invoke_and_parse(
        llm,
        [
            SystemMessage(content=_get_mandatory_script_system_prompt(backend, tenant_id=tenant_id)),
            HumanMessage(content=user_message),
        ],
    )


# ---------------------------------------------------------------------------
# 노드 함수 (LangGraph 노드 진입점)
# ---------------------------------------------------------------------------


async def work_accuracy_node(state: QAState, ctx: NodeContext) -> dict[str, Any]:
    """Evaluate work accuracy — correct guidance and mandatory script compliance.

    QA Item #15 — 정확한 안내, max 10 points (10/5/0).
    QA Item #16 — 필수 안내 이행, max 5 points (5/3/0).

    Returns {"evaluations": [result1, result2]} for operator.add merge.
    """
    # --- 선별 턴 할당 우선 사용, 폴백으로 전체 transcript ---
    # NOTE: assignment 우선 패턴 보존 — ctx.transcript 미사용
    assignment = state.get("agent_turn_assignments", {}).get("work_accuracy", {})
    assigned_text = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])
    transcript = assigned_text

    consultation_type = ctx.consultation_type
    rules = state.get("rules", {})

    logger.info(
        f"work_accuracy_node: type='{consultation_type}', transcript_len={len(transcript)}, "
        f"assigned_turns={len(assigned_turns)}"
    )

    # 통화 내역이 없으면 평가 불가 — 에러 결과 반환
    if not transcript:
        return {
            "evaluations": [
                build_llm_failure_result("work-accuracy-agent", "No transcript provided for evaluation.")
            ]
        }

    # --- 1단계: regex 기반 사전 분석 ---
    # assigned_turns가 있으면 구조화된 턴 데이터 활용 (agent 턴 전체), 없으면 기존 텍스트 파싱
    if assigned_turns:
        agent_turns = [t for t in assigned_turns if t.get("speaker") == "agent"]

        def _match_agent_turns(patterns: list[str]) -> list[dict]:
            findings = []
            for t in agent_turns:
                for pattern in patterns:
                    if re.search(pattern, t["text"]):
                        findings.append({"turn": t["turn_id"], "text": t["text"], "pattern": pattern})
                        break
            return findings

        pre_analysis = {
            "corrections": _match_agent_turns(CORRECTION_PATTERNS),
            "contradictions": _match_agent_turns(CONTRADICTION_INDICATORS),
            "mandatory": _match_agent_turns(MANDATORY_SCRIPT_PATTERNS),
        }

        # LLM에 전달할 transcript를 턴 번호 포함 형태로 재구성
        transcript = "\n".join(f"[Turn {t['turn_id']}] {t['text']}" for t in assigned_turns)
    else:
        pre_analysis = {
            "corrections": detect_agent_patterns(transcript, CORRECTION_PATTERNS),
            "contradictions": detect_agent_patterns(transcript, CONTRADICTION_INDICATORS),
            "mandatory": detect_agent_patterns(transcript, MANDATORY_SCRIPT_PATTERNS),
        }

    # --- Wiki 공유 메모리: intent_summary 읽기 ---
    intent_summary = state.get("intent_summary", {})
    primary_intent = intent_summary.get("primary_intent", "")
    intent_context = ""
    if primary_intent and primary_intent != "미식별":
        intent_context = f"고객 주요 문의: {primary_intent}"
        product = intent_summary.get("product", "")
        if product:
            intent_context += f" (상품/유형: {product})"

    # --- 2단계: 두 항목 병렬 LLM 호출 ---
    try:
        _backend = ctx.llm_backend
        _bedrock_model_id = ctx.bedrock_model_id
        _tenant_id = ctx.tenant_id
        accuracy_result, mandatory_result = await asyncio.gather(
            _evaluate_accuracy(
                transcript, consultation_type, rules, pre_analysis, intent_context,
                backend=_backend, bedrock_model_id=_bedrock_model_id, tenant_id=_tenant_id,
            ),
            _evaluate_mandatory_script(
                transcript, consultation_type, rules, pre_analysis, intent_context,
                backend=_backend, bedrock_model_id=_bedrock_model_id, tenant_id=_tenant_id,
            ),
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Work accuracy LLM failed, fallback to rule: %s", e)
        corr_count = len(pre_analysis["corrections"])
        contra_count = len(pre_analysis["contradictions"])
        mand_count = len(pre_analysis["mandatory"])
        if corr_count == 0 and contra_count == 0:
            fb_15, fb_d15 = 10, []
        elif corr_count <= 1 and contra_count == 0:
            fb_15 = 5
            fb_d15 = [{"reason": "정정 표현 감지 — 미미한 오안내 (LLM 실패 — 규칙 폴백)", "points": 5, "evidence_ref": ""}]
        else:
            fb_15 = 0
            fb_d15 = [{"reason": "다수 정정/모순 감지 (LLM 실패 — 규칙 폴백)", "points": 10, "evidence_ref": ""}]
        if mand_count >= 2:
            fb_16, fb_d16 = 5, []
        elif mand_count >= 1:
            fb_16 = 3
            fb_d16 = [{"reason": "필수 안내 일부 감지 (LLM 실패 — 규칙 폴백)", "points": 2, "evidence_ref": ""}]
        else:
            fb_16 = 0
            fb_d16 = [{"reason": "필수 안내 미감지 (LLM 실패 — 규칙 폴백)", "points": 5, "evidence_ref": ""}]
        return {
            "evaluations": [
                {
                    "status": "success", "agent_id": "work-accuracy-agent",
                    "evaluation": {
                        "item_number": 15, "item_name": "정확한 안내", "max_score": 10,
                        "score": fb_15, "deductions": fb_d15,
                        "evidence": [{"turn": c["turn"], "text": c["text"]} for c in pre_analysis["corrections"][:3]],
                        "confidence": 0.5, "details": {"fallback": True},
                    },
                },
                {
                    "status": "success", "agent_id": "work-accuracy-agent",
                    "evaluation": {
                        "item_number": 16, "item_name": "필수 안내 이행", "max_score": 5,
                        "score": fb_16, "deductions": fb_d16,
                        "evidence": [{"turn": m["turn"], "text": m["text"]} for m in pre_analysis["mandatory"][:3]],
                        "confidence": 0.5, "details": {"fallback": True},
                    },
                },
            ]
        }

    # --- 3단계: 점수 검증 (Scorer를 통해 qa_rules.py 기준으로 검증) ---
    score_result_15 = _scorer.score_item(
        item_number=15,
        verdict=accuracy_result.get("score", 0),
        reason=accuracy_result.get("summary", ""),
        confidence=accuracy_result.get("confidence", 0.85),
    )
    score_result_16 = _scorer.score_item(
        item_number=16,
        verdict=mandatory_result.get("score", 0),
        reason=mandatory_result.get("summary", ""),
        confidence=mandatory_result.get("confidence", 0.85),
    )
    score_15 = score_result_15.score
    score_16 = score_result_16.score

    # --- 3-1단계: score × deductions 산술 보정 (LLM hallucination 방어) ---
    rec_15 = reconcile(
        item_number=15,
        score=score_15,
        max_score=10,
        deductions=accuracy_result.get("deductions", []),
    )
    if rec_15.note:
        accuracy_result["deductions"] = rec_15.deductions
        score_15 = rec_15.score

    rec_16 = reconcile(
        item_number=16,
        score=score_16,
        max_score=5,
        deductions=mandatory_result.get("deductions", []),
    )
    if rec_16.note:
        mandatory_result["deductions"] = rec_16.deductions
        score_16 = rec_16.score

    # evidence 선정: 각 항목 deductions.evidence_ref 우선 → fallback assigned_turns (greeting/단답 제외)
    deductions_15 = accuracy_result.get("deductions", [])
    deductions_16 = mandatory_result.get("deductions", [])
    accuracy_turn_evidence = build_turn_evidence(assigned_turns, deductions_15)
    mandatory_turn_evidence = build_turn_evidence(assigned_turns, deductions_16)
    accuracy_evidence = (
        accuracy_turn_evidence if accuracy_turn_evidence else accuracy_result.get("evidence", [])
    )
    mandatory_evidence = (
        mandatory_turn_evidence if mandatory_turn_evidence else mandatory_result.get("evidence", [])
    )

    # --- Wiki 공유 메모리: accuracy_verdict 작성 ---
    # #15 정확한 안내 평가 결과에서 오안내 여부를 판정하여 proactiveness 등 하류 에이전트에 전달
    accuracy_verdict = {
        "has_incorrect_guidance": score_15 < 10,
        "severity": "major" if score_15 == 0 else "minor" if score_15 == 5 else "none",
        "details": accuracy_result.get("summary", ""),
    }

    deduction_log_entries = build_deduction_log_from_pairs(
        [(15, accuracy_result), (16, mandatory_result)], "work-accuracy-agent"
    )

    # 최종 평가 결과를 operator.add 리듀서 형태로 반환
    return {
        "evaluations": [
            {
                "status": "success",
                "agent_id": "work-accuracy-agent",
                "evaluation": {
                    "item_number": 15,
                    "item_name": "정확한 안내",
                    "max_score": 10,
                    "score": score_15,
                    "incorrect_guidance": accuracy_result.get("incorrect_guidance", []),
                    "corrections_made": accuracy_result.get("corrections_made", []),
                    "contradictions": accuracy_result.get("contradictions", []),
                    "deductions": deductions_15,
                    "evidence": accuracy_evidence,
                    "confidence": accuracy_result.get("confidence", 0.85),
                    "details": {"assigned_turns": assigned_turns},
                },
            },
            {
                "status": "success",
                "agent_id": "work-accuracy-agent",
                "evaluation": {
                    "item_number": 16,
                    "item_name": "필수 안내 이행",
                    "max_score": 5,
                    "score": score_16,
                    "mandatory_items_completed": mandatory_result.get("mandatory_items_completed", []),
                    "mandatory_items_missing": mandatory_result.get("mandatory_items_missing", []),
                    "deductions": deductions_16,
                    "evidence": mandatory_evidence,
                    "confidence": mandatory_result.get("confidence", 0.85),
                    "details": {"assigned_turns": assigned_turns},
                },
            },
        ],
        "accuracy_verdict": accuracy_verdict,
        "deduction_log": deduction_log_entries,
    }
