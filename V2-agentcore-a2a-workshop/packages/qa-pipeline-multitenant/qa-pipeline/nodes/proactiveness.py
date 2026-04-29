# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Proactiveness evaluation node — 적극성 (#12: 문제 해결 의지 5점, #13: 부연 설명 및 추가 안내 5점, #14: 사후 안내 5점).

상담사의 적극적인 문제 해결 자세, 선제적 부연 설명, 후속 절차 안내를 평가한다.

Key checks:
- 대안 제시 및 문제 해결 의지 vs 업무 회피/단순 반복 안내
- 예상 질문 선제 안내 및 원스톱 처리 vs 단답형 안내
- 후속 절차, 예상 소요시간, 연락 수단 안내 vs 사후 안내 누락

총 배점: 15점 (문제 해결 의지 5점 + 부연 설명 및 추가 안내 5점 + 사후 안내 5점)
"""

# ---------------------------------------------------------------------------
# [노드 개요]
# 이 노드는 QA 평가항목 #12, #13, #14 "적극성" 영역을 평가한다.
# 상담사가 얼마나 적극적으로 문제를 해결하고, 부가적인 정보를 제공하며,
# 사후 처리까지 안내했는지를 검사한다.
#
# [평가 기준]
#
#   #12 문제 해결 의지 (최대 5점, 채점: 5/3/0)
#     5점: 적극적으로 대안 제시 및 문제 해결 의지 전달
#     3점: 기본 안내는 되었으나 추가 대안 제시 미흡
#     0점: 해결 의지 없이 단순 안내 반복 또는 업무 회피
#
#   #13 부연 설명 및 추가 안내 (최대 5점, 채점: 5/3/0)
#     5점: 예상 질문까지 선제적으로 안내하여 원스톱 처리
#     3점: 기본 답변은 되었으나 부연 설명 부족
#     0점: 단답형 안내로 고객 재문의 유발
#
#   #14 사후 안내 (최대 5점, 채점: 5/3/0)
#     5점: 후속 절차, 예상 소요시간, 연락 수단 등 명확히 안내
#     3점: 사후 안내가 일부 진행되었으나 구체성 부족
#     0점: 사후 안내 누락
#     ※ 즉시 해결 건으로 사후 안내가 불필요한 경우 만점 처리
#
# [동작 흐름]
#   1. 사전 분석(regex): 대안 제시, 회피, 선제 안내, 단답, 사후 안내 패턴 탐지
#   2. LLM 호출: 세 항목을 각각 개별 평가 (asyncio.gather 병렬)
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
from nodes.skills.node_context import NodeContext, build_user_message
from nodes.skills.pattern_matcher import detect_agent_patterns, detect_customer_patterns
from nodes.skills.reconciler import reconcile
from nodes.skills.scorer import Scorer
from state import QAState
from typing import Any


logger = logging.getLogger(__name__)

# Scorer instance for rule-based score validation per qa_rules.py
_scorer = Scorer()

# ---------------------------------------------------------------------------
# #12 문제 해결 의지 관련 패턴
# ---------------------------------------------------------------------------

# 대안 제시 / 적극적 해결 의지 패턴
ALTERNATIVE_PATTERNS = [
    r"다른\s*방법",  # "다른 방법으로..."
    r"대안",  # "대안을 안내드리면..."
    r"대신",  # "대신 ~하시면..."
    r"~도\s*가능",  # "이 방법도 가능합니다"
    r"방법이\s*있",  # "다른 방법이 있습니다"
    r"이렇게\s*하시면",  # "이렇게 하시면 됩니다"
    r"도움\s*드릴",  # "도움 드릴 수 있는..."
    r"확인\s*해\s*보겠",  # "확인해 보겠습니다"
    r"알아보겠",  # "알아보겠습니다"
    r"제가\s*직접",  # "제가 직접 처리..."
    r"바로\s*처리",  # "바로 처리해 드리겠습니다"
    r"진행\s*해\s*드리겠",  # "진행해 드리겠습니다"
]

# 업무 회피 / 소극적 태도 패턴
AVOIDANCE_PATTERNS = [
    r"저희\s*쪽(은|에서는)",  # "저희 쪽은 어렵습니다"
    r"다른\s*부서",  # "다른 부서로 연락하셔야..."
    r"어렵습니다",  # "어렵습니다" (대안 없이)
    r"안\s*됩니다",  # "안 됩니다" (대안 없이)
    r"그건\s*저희가",  # "그건 저희가 처리 못합니다"
    r"못\s*해\s*드",  # "못 해 드립니다"
    r"권한이\s*없",  # "권한이 없습니다"
    r"담당이\s*아니",  # "담당이 아닙니다"
]

# ---------------------------------------------------------------------------
# #13 부연 설명 및 추가 안내 관련 패턴
# ---------------------------------------------------------------------------

# 선제적 안내 패턴
PROACTIVE_PATTERNS = [
    r"참고로",  # "참고로 말씀드리면..."
    r"추가로\s*말씀드리면",  # "추가로 말씀드리면..."
    r"추가\s*안내",  # "추가 안내 드리겠습니다"
    r"혹시.*도",  # "혹시 ~도 궁금하시면..."
    r"덧붙여",  # "덧붙여 말씀드리면..."
    r"아울러",  # "아울러 안내드리면..."
    r"함께\s*안내",  # "함께 안내드리겠습니다"
    r"미리\s*말씀",  # "미리 말씀드리면..."
    r"알아두시면",  # "알아두시면 좋은 점은..."
    r"참고하시면",  # "참고하시면 좋을 것 같습니다"
]

# 단답형 / 재문의 유발 패턴 — 고객 발화에서 탐지
REINQUIRY_PATTERNS = [
    r"그래서\s*어떻게",  # "그래서 어떻게 해야 하나요?"
    r"그\s*다음은",  # "그 다음은 어떻게 해요?"
    r"더\s*알려주세요",  # "더 알려주세요"
    r"다른\s*건\s*없나요",  # "다른 건 없나요?"
    r"그것만\s*인가요",  # "그것만 인가요?"
    r"그러면\s*어떻게",  # "그러면 어떻게 하죠?"
]

# ---------------------------------------------------------------------------
# #14 사후 안내 관련 패턴
# ---------------------------------------------------------------------------

# 사후 안내 키워드 — 후속 절차, 소요시간, 연락 방법
FOLLOWUP_PATTERNS = [
    r"처리\s*기간",  # "처리 기간은..."
    r"영업일",  # "2~3 영업일 이내..."
    r"소요",  # "소요 시간은..."
    r"일\s*(이내|안에|내로)",  # "3일 이내 처리..."
    r"연락\s*(드리겠|주시면)",  # "연락 드리겠습니다"
    r"문자",  # "문자로 안내..."
    r"이메일",  # "이메일로 발송..."
    r"결과",  # "결과를 안내드리겠습니다"
    r"회신",  # "회신 드리겠습니다"
    r"콜백",  # "콜백 드리겠습니다"
    r"완료\s*되면",  # "완료되면 안내드리겠습니다"
    r"처리\s*후",  # "처리 후 연락드리겠습니다"
    r"접수",  # "접수 완료되었습니다"
    r"진행\s*상황",  # "진행 상황을 안내..."
]


# ---------------------------------------------------------------------------
# 사전 분석 헬퍼 함수
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# LLM 시스템 프롬프트
# ---------------------------------------------------------------------------


def _get_resolve_will_prompt(backend: str | None = None, tenant_id: str = "") -> str:
    from prompts import load_prompt

    return load_prompt("item_12_problem_solving", tenant_id=tenant_id, backend=backend)


def _get_supplementary_prompt(backend: str | None = None, tenant_id: str = "") -> str:
    from prompts import load_prompt

    return load_prompt("item_13_supplementary", tenant_id=tenant_id, backend=backend)


def _get_followup_prompt(backend: str | None = None, tenant_id: str = "") -> str:
    from prompts import load_prompt

    return load_prompt("item_14_followup", tenant_id=tenant_id, backend=backend)


# ---------------------------------------------------------------------------
# 개별 평가 함수
# ---------------------------------------------------------------------------


async def _evaluate_resolve_will(
    transcript: str, consultation_type: str, rules: dict, pre_analysis: dict,
    intent_context: str = "", accuracy_context: str = "", backend: str | None = None,
    bedrock_model_id: str | None = None, tenant_id: str = "",
) -> dict:
    """#12 문제 해결 의지 평가 (5점)."""
    user_message = build_user_message(
        consultation_type=consultation_type, transcript=transcript,
        rules=rules, intent_context=intent_context, accuracy_context=accuracy_context,
    )
    user_message += "## Pre-Analysis Results\n"
    user_message += f"- 대안 제시 패턴 감지: {len(pre_analysis['alternatives'])}건\n"
    for a in pre_analysis["alternatives"]:
        user_message += f"  - Turn {a['turn']}: {a['text']}\n"
    user_message += f"- 업무 회피 패턴 감지: {len(pre_analysis['avoidance'])}건\n"
    for av in pre_analysis["avoidance"]:
        user_message += f"  - Turn {av['turn']}: {av['text']}\n"
    user_message += "\n## Instructions\n"
    user_message += "문제 해결 의지 (QA Item #12)를 평가하세요.\n"

    llm = get_chat_model(
        max_tokens=1024, backend=backend, bedrock_model_id=bedrock_model_id,
    )
    return await invoke_and_parse(
        llm,
        [
            SystemMessage(content=_get_resolve_will_prompt(backend, tenant_id=tenant_id)),
            HumanMessage(content=user_message),
        ],
    )


async def _evaluate_supplementary(
    transcript: str, consultation_type: str, rules: dict, pre_analysis: dict,
    intent_context: str = "", backend: str | None = None,
    bedrock_model_id: str | None = None, tenant_id: str = "",
) -> dict:
    """#13 부연 설명 및 추가 안내 평가 (5점)."""
    user_message = build_user_message(
        consultation_type=consultation_type, transcript=transcript,
        rules=rules, intent_context=intent_context,
    )
    user_message += "## Pre-Analysis Results\n"
    user_message += f"- 선제적 안내 패턴 감지: {len(pre_analysis['proactive'])}건\n"
    for p in pre_analysis["proactive"]:
        user_message += f"  - Turn {p['turn']}: {p['text']}\n"
    user_message += f"- 고객 재문의 패턴 감지: {len(pre_analysis['reinquiry'])}건\n"
    for r in pre_analysis["reinquiry"]:
        user_message += f"  - Turn {r['turn']}: {r['text']}\n"
    user_message += "\n## Instructions\n"
    user_message += "부연 설명 및 추가 안내 (QA Item #13)를 평가하세요.\n"

    llm = get_chat_model(
        max_tokens=1024, backend=backend, bedrock_model_id=bedrock_model_id,
    )
    return await invoke_and_parse(
        llm,
        [
            SystemMessage(content=_get_supplementary_prompt(backend, tenant_id=tenant_id)),
            HumanMessage(content=user_message),
        ],
    )


async def _evaluate_followup(
    transcript: str, consultation_type: str, rules: dict, pre_analysis: dict,
    intent_context: str = "", backend: str | None = None,
    bedrock_model_id: str | None = None, tenant_id: str = "",
) -> dict:
    """#14 사후 안내 평가 (5점)."""
    user_message = build_user_message(
        consultation_type=consultation_type, transcript=transcript,
        rules=rules, intent_context=intent_context,
    )
    user_message += "## Pre-Analysis Results\n"
    user_message += f"- 사후 안내 키워드 감지: {len(pre_analysis['followup'])}건\n"
    for f in pre_analysis["followup"]:
        user_message += f"  - Turn {f['turn']}: {f['text']}\n"
    user_message += "\n## Instructions\n"
    user_message += "사후 안내 (QA Item #14)를 평가하세요.\n"
    user_message += "- 즉시 해결 건 여부를 먼저 판단하세요.\n"

    llm = get_chat_model(
        max_tokens=1024, backend=backend, bedrock_model_id=bedrock_model_id,
    )
    return await invoke_and_parse(
        llm,
        [
            SystemMessage(content=_get_followup_prompt(backend, tenant_id=tenant_id)),
            HumanMessage(content=user_message),
        ],
    )


# ---------------------------------------------------------------------------
# 점수 검증 함수
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 노드 함수 (LangGraph 노드 진입점)
# ---------------------------------------------------------------------------


async def proactiveness_node(state: QAState, ctx: NodeContext) -> dict[str, Any]:
    """Evaluate proactiveness in problem resolution.

    QA Item #12 — 문제 해결 의지, max 5 points (5/3/0).
    QA Item #13 — 부연 설명 및 추가 안내, max 5 points (5/3/0).
    QA Item #14 — 사후 안내, max 5 points (5/3/0).

    Returns {"evaluations": [result1, result2, result3]} for operator.add merge.
    """
    # --- 선별 턴 할당 우선 사용, 폴백으로 전체 transcript ---
    # NOTE: assignment 우선 패턴 보존 — ctx.transcript 미사용
    assignment = state.get("agent_turn_assignments", {}).get("proactiveness", {})
    assigned_text = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])
    transcript = assigned_text

    consultation_type = ctx.consultation_type
    rules = state.get("rules", {})

    logger.info(
        f"proactiveness_node: type='{consultation_type}', transcript_len={len(transcript)}, "
        f"assigned_turns={len(assigned_turns)}"
    )

    # 통화 내역이 없으면 평가 불가 — 에러 결과 반환
    if not transcript:
        return {
            "evaluations": [
                build_llm_failure_result("proactiveness-agent", "No transcript provided for evaluation.")
            ]
        }

    # --- 1단계: regex 기반 사전 분석 ---
    # assigned_turns가 있으면 구조화된 턴 데이터 활용, 없으면 기존 텍스트 파싱
    if assigned_turns:
        agent_turns = [t for t in assigned_turns if t.get("speaker") == "agent"]
        customer_turns = [t for t in assigned_turns if t.get("speaker") == "customer"]

        def _match_turns(turns: list[dict], patterns: list[str]) -> list[dict]:
            findings = []
            for t in turns:
                for pattern in patterns:
                    if re.search(pattern, t["text"]):
                        findings.append({"turn": t["turn_id"], "text": t["text"], "pattern": pattern})
                        break
            return findings

        pre_analysis = {
            "alternatives": _match_turns(agent_turns, ALTERNATIVE_PATTERNS),
            "avoidance": _match_turns(agent_turns, AVOIDANCE_PATTERNS),
            "proactive": _match_turns(agent_turns, PROACTIVE_PATTERNS),
            "reinquiry": _match_turns(customer_turns, REINQUIRY_PATTERNS),
            "followup": _match_turns(agent_turns, FOLLOWUP_PATTERNS),
        }

        # LLM에 전달할 transcript를 턴 번호 포함 형태로 재구성
        transcript = "\n".join(f"[Turn {t['turn_id']}] {t['text']}" for t in assigned_turns)
    else:
        pre_analysis = {
            "alternatives": detect_agent_patterns(transcript, ALTERNATIVE_PATTERNS),
            "avoidance": detect_agent_patterns(transcript, AVOIDANCE_PATTERNS),
            "proactive": detect_agent_patterns(transcript, PROACTIVE_PATTERNS),
            "reinquiry": detect_customer_patterns(transcript, REINQUIRY_PATTERNS),
            "followup": detect_agent_patterns(transcript, FOLLOWUP_PATTERNS),
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

    # --- Wiki 공유 메모리: accuracy_verdict 읽기 ---
    accuracy_verdict = state.get("accuracy_verdict", {})
    accuracy_context = ""
    if accuracy_verdict.get("has_incorrect_guidance"):
        severity = accuracy_verdict.get("severity", "unknown")
        details = accuracy_verdict.get("details", "")
        accuracy_context = f"업무정확도 에이전트가 오안내를 감지했습니다 (심각도: {severity}). 이 맥락에서 적극성을 평가하세요."
        if details:
            accuracy_context += f" 상세: {details}"

    # --- 2단계: 세 항목 병렬 LLM 호출 ---
    try:
        _backend = ctx.llm_backend
        _bedrock_model_id = ctx.bedrock_model_id
        _tenant_id = ctx.tenant_id
        resolve_result, supplementary_result, followup_result = await asyncio.gather(
            _evaluate_resolve_will(
                transcript, consultation_type, rules, pre_analysis, intent_context, accuracy_context,
                backend=_backend, bedrock_model_id=_bedrock_model_id, tenant_id=_tenant_id,
            ),
            _evaluate_supplementary(
                transcript, consultation_type, rules, pre_analysis, intent_context,
                backend=_backend, bedrock_model_id=_bedrock_model_id, tenant_id=_tenant_id,
            ),
            _evaluate_followup(
                transcript, consultation_type, rules, pre_analysis, intent_context,
                backend=_backend, bedrock_model_id=_bedrock_model_id, tenant_id=_tenant_id,
            ),
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Proactiveness LLM failed, fallback to rule: %s", e)
        alt_count = len(pre_analysis["alternatives"])
        avoid_count = len(pre_analysis["avoidance"])
        pro_count = len(pre_analysis["proactive"])
        reinq_count = len(pre_analysis["reinquiry"])
        fu_count = len(pre_analysis["followup"])
        if alt_count >= 1 and avoid_count == 0:
            fb_12, fb_d12 = 5, []
        elif alt_count >= 1 or avoid_count == 0:
            fb_12 = 3
            fb_d12 = [{"reason": "대안 제시 미흡 (LLM 실패 — 규칙 폴백)", "points": 2, "evidence_ref": ""}]
        else:
            fb_12 = 0
            fb_d12 = [{"reason": "업무 회피 패턴 감지 (LLM 실패 — 규칙 폴백)", "points": 5, "evidence_ref": ""}]
        # #13 기준표 정합:
        #  5점: 선제 안내 있고 재문의 0회 — 원스톱 처리
        #  3점: 기본 답변은 되었으나 부연 부족 (선제 안내 없음 혹은 재문의 1회)
        #  0점: 선제 안내 전무 + 재문의 2회 이상 — 단답형으로 재문의 유발 (명백한 경우만)
        if pro_count >= 1 and reinq_count == 0:
            fb_13, fb_d13 = 5, []
        elif pro_count == 0 and reinq_count >= 2:
            # 선제 안내 전무 + 재문의 반복 — "단답형으로 재문의 유발" 명백
            fb_13 = 0
            fb_d13 = [{"reason": "단답형 안내 / 재문의 반복 유발 (LLM 실패 — 규칙 폴백)", "points": 5, "evidence_ref": ""}]
        else:
            # 그 외 (선제 안내 1회 + 재문의 있음 / 선제 안내 없음 + 재문의 0~1회) → 부연 부족 = 3점
            fb_13 = 3
            fb_d13 = [{"reason": "부연 설명 부족 (LLM 실패 — 규칙 폴백)", "points": 2, "evidence_ref": ""}]
        if fu_count >= 2:
            fb_14, fb_d14 = 5, []
        elif fu_count >= 1:
            fb_14 = 3
            fb_d14 = [{"reason": "사후 안내 일부 (LLM 실패 — 규칙 폴백)", "points": 2, "evidence_ref": ""}]
        else:
            fb_14 = 0
            fb_d14 = [{"reason": "사후 안내 누락 (LLM 실패 — 규칙 폴백)", "points": 5, "evidence_ref": ""}]
        return {
            "evaluations": [
                {
                    "status": "success", "agent_id": "proactiveness-agent",
                    "evaluation": {
                        "item_number": 12, "item_name": "문제 해결 의지", "max_score": 5,
                        "score": fb_12, "deductions": fb_d12,
                        "evidence": [{"turn": a["turn"], "text": a["text"]} for a in pre_analysis["alternatives"][:3]],
                        "confidence": 0.5, "details": {"fallback": True},
                    },
                },
                {
                    "status": "success", "agent_id": "proactiveness-agent",
                    "evaluation": {
                        "item_number": 13, "item_name": "부연 설명 및 추가 안내", "max_score": 5,
                        "score": fb_13, "deductions": fb_d13,
                        "evidence": [{"turn": p["turn"], "text": p["text"]} for p in pre_analysis["proactive"][:3]],
                        "confidence": 0.5, "details": {"fallback": True},
                    },
                },
                {
                    "status": "success", "agent_id": "proactiveness-agent",
                    "evaluation": {
                        "item_number": 14, "item_name": "사후 안내", "max_score": 5,
                        "score": fb_14, "deductions": fb_d14,
                        "evidence": [{"turn": f["turn"], "text": f["text"]} for f in pre_analysis["followup"][:3]],
                        "confidence": 0.5, "details": {"fallback": True},
                    },
                },
            ]
        }

    # --- 3단계: 점수 검증 (Scorer를 통해 qa_rules.py 기준으로 검증) ---
    score_result_12 = _scorer.score_item(
        item_number=12,
        verdict=resolve_result.get("score", 0),
        reason=resolve_result.get("summary", ""),
        confidence=resolve_result.get("confidence", 0.85),
    )
    score_result_13 = _scorer.score_item(
        item_number=13,
        verdict=supplementary_result.get("score", 0),
        reason=supplementary_result.get("summary", ""),
        confidence=supplementary_result.get("confidence", 0.85),
    )
    score_result_14 = _scorer.score_item(
        item_number=14,
        verdict=followup_result.get("score", 0),
        reason=followup_result.get("summary", ""),
        confidence=followup_result.get("confidence", 0.85),
    )
    score_12 = score_result_12.score
    score_13 = score_result_13.score
    score_14 = score_result_14.score

    # --- 3-1단계: score × deductions 산술 보정 (LLM hallucination 방어) ---
    rec_12 = reconcile(
        item_number=12, score=score_12, max_score=5,
        deductions=resolve_result.get("deductions", []),
    )
    if rec_12.note:
        resolve_result["deductions"] = rec_12.deductions
        score_12 = rec_12.score
    rec_13 = reconcile(
        item_number=13, score=score_13, max_score=5,
        deductions=supplementary_result.get("deductions", []),
    )
    if rec_13.note:
        supplementary_result["deductions"] = rec_13.deductions
        score_13 = rec_13.score
    rec_14 = reconcile(
        item_number=14, score=score_14, max_score=5,
        deductions=followup_result.get("deductions", []),
    )
    if rec_14.note:
        followup_result["deductions"] = rec_14.deductions
        score_14 = rec_14.score

    # assigned_turns가 있으면 evidence에 turn_id 포함
    if assigned_turns:
        turn_evidence = [{"turn": t["turn_id"], "speaker": t["speaker"], "text": t["text"]} for t in assigned_turns]
    else:
        turn_evidence = None

    deduction_log_entries = build_deduction_log_from_pairs(
        [(12, resolve_result), (13, supplementary_result), (14, followup_result)],
        "proactiveness-agent",
    )

    # 최종 평가 결과를 operator.add 리듀서 형태로 반환
    return {
        "evaluations": [
            {
                "status": "success",
                "agent_id": "proactiveness-agent",
                "evaluation": {
                    "item_number": 12,
                    "item_name": "문제 해결 의지",
                    "max_score": 5,
                    "score": score_12,
                    "alternatives_offered": resolve_result.get("alternatives_offered", []),
                    "avoidance_detected": resolve_result.get("avoidance_detected", []),
                    "deductions": resolve_result.get("deductions", []),
                    "evidence": turn_evidence if turn_evidence else resolve_result.get("evidence", []),
                    "confidence": resolve_result.get("confidence", 0.85),
                },
            },
            {
                "status": "success",
                "agent_id": "proactiveness-agent",
                "evaluation": {
                    "item_number": 13,
                    "item_name": "부연 설명 및 추가 안내",
                    "max_score": 5,
                    "score": score_13,
                    "proactive_guidance": supplementary_result.get("proactive_guidance", []),
                    "reinquiry_detected": supplementary_result.get("reinquiry_detected", []),
                    "deductions": supplementary_result.get("deductions", []),
                    "evidence": turn_evidence if turn_evidence else supplementary_result.get("evidence", []),
                    "confidence": supplementary_result.get("confidence", 0.85),
                },
            },
            {
                "status": "success",
                "agent_id": "proactiveness-agent",
                "evaluation": {
                    "item_number": 14,
                    "item_name": "사후 안내",
                    "max_score": 5,
                    "score": score_14,
                    "followup_items": followup_result.get("followup_items", []),
                    "immediate_resolution": followup_result.get("immediate_resolution", False),
                    "deductions": followup_result.get("deductions", []),
                    "evidence": turn_evidence if turn_evidence else followup_result.get("evidence", []),
                    "confidence": followup_result.get("confidence", 0.85),
                },
            },
        ],
        "deduction_log": deduction_log_entries,
    }
