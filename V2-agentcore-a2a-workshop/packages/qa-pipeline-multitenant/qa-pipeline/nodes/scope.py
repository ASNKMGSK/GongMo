# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Scope evaluation node — 설명력 및 전달력 (#10: 설명의 명확성 10점, #11: 두괄식 답변 5점).

고객 눈높이에 맞는 명확한 설명과 두괄식(핵심 먼저) 전달 방식을 평가한다.

Key checks:
- 내부 용어/전문 용어 사용 여부
- 고객 되물음(이해 불가) 발생 여부
- 장황함 vs 간결한 핵심 전달
- 두괄식(결론 먼저) 답변 구조

총 배점: 15점 (설명의 명확성 10점 + 두괄식 답변 5점)
"""

# ---------------------------------------------------------------------------
# [노드 개요]
# 이 노드는 QA 평가항목 #10 "설명의 명확성"과 #11 "두괄식 답변"을 평가한다.
# 상담사가 고객에게 얼마나 명확하고 효과적으로 정보를 전달했는지 검사한다.
#
# [평가 기준]
#
#   #10 설명의 명확성 (최대 10점, 채점: 10/7/5/0)
#     10점: 고객 눈높이에 맞춰 핵심을 정리하여 이해하기 쉽게 설명
#      7점: 설명은 되었으나 부분적으로 장황하거나 매끄럽지 못함
#      5점: 내부 용어 사용, 일방적 나열식 설명, 또는 고객 되물음 발생
#      0점: 설명 불가 또는 고객이 전혀 이해하지 못함
#
#   #11 두괄식 답변 (최대 5점, 채점: 5/3/0)
#      5점: 핵심 내용을 먼저 전달하고 부연 설명을 진행
#      3점: 설명이 다소 장황하나 핵심은 전달됨
#      0점: 두서 없이 장황하여 핵심 파악이 어려움
#
# [동작 흐름]
#   1. 사전 분석(regex): 내부 용어, 고객 되물음, 장황함, 두괄식 패턴 탐지
#   2. LLM 호출: 두 항목을 하나의 프롬프트로 통합 평가
#   3. JSON 파싱 → 항목별 점수 검증 → 평가 결과 반환
# ---------------------------------------------------------------------------

from __future__ import annotations

import asyncio
import json
import logging
import re
from langchain_core.messages import HumanMessage, SystemMessage
from nodes.llm import LLMTimeoutError, get_chat_model, invoke_and_parse
from nodes.skills.deduction_log import build_deduction_log_from_pairs
from nodes.skills.error_results import build_llm_failure_result
from nodes.skills.evidence_builder import build_turn_evidence
from nodes.skills.node_context import NodeContext
from nodes.skills.pattern_matcher import is_agent, is_customer
from nodes.skills.reconciler import reconcile
from nodes.skills.scorer import Scorer
from state import QAState
from typing import Any


logger = logging.getLogger(__name__)

# Scorer instance for rule-based score validation per qa_rules.py
_scorer = Scorer()

# ---------------------------------------------------------------------------
# 내부 용어 / 전문 용어 탐지 패턴
# ---------------------------------------------------------------------------
# 상담사가 고객이 이해하기 어려운 내부 용어나 전문 용어를 사용하는 경우를 탐지한다.
# 이 패턴이 탐지되면 설명의 명확성(#10)에서 5점 이하 가능성을 시사한다.

INTERNAL_JARGON_PATTERNS = [
    r"전산\s*상",  # "전산상 처리가..."
    r"전산\s*시스템",  # "전산 시스템에서..."
    r"내부\s*규정",  # "내부 규정에 의해..."
    r"내부\s*프로세스",  # "내부 프로세스상..."
    r"백오피스",  # "백오피스에서 처리..."
    r"CRM",  # "CRM에 등록..."
    r"ERP",  # "ERP 시스템에서..."
    r"인바운드",  # "인바운드 콜이..."
    r"아웃바운드",  # "아웃바운드 발신..."
    r"에스컬레이션",  # "에스컬레이션 처리..."
    r"SLA",  # "SLA 기준에..."
    r"TM\s",  # "TM 진행..."
    r"VOC",  # "VOC 접수..."
    r"상담\s*코드",  # "상담 코드 입력..."
    r"처리\s*코드",  # "처리 코드가..."
    r"전결",  # "전결 처리..."
    r"결재\s*라인",  # "결재 라인을..."
    r"시스템\s*오류",  # "시스템 오류로..."
    r"DB\s",  # "DB에서 조회..."
    r"PG\s*사",  # "PG사에서..."
]

# ---------------------------------------------------------------------------
# 고객 되물음 패턴 (이해하지 못했다는 신호)
# ---------------------------------------------------------------------------
# 고객이 상담사의 설명을 이해하지 못해 되묻는 표현을 탐지한다.
# 이 패턴이 탐지되면 설명의 명확성(#10)에서 5점 이하 가능성을 시사한다.

CUSTOMER_REASK_PATTERNS = [
    r"다시\s*설명",  # "다시 설명해 주세요"
    r"무슨\s*말씀",  # "무슨 말씀이세요?"
    r"이해가\s*안",  # "이해가 안 돼요"
    r"뭐라고요",  # "뭐라고요?"
    r"못\s*알아듣겠",  # "못 알아듣겠어요"
    r"쉽게\s*설명",  # "좀 쉽게 설명해 주세요"
    r"무슨\s*뜻",  # "무슨 뜻이에요?"
    r"무슨\s*소리",  # "무슨 소리예요?"
    r"다시\s*한번",  # "다시 한번 말씀해 주세요"
    r"잘\s*모르겠",  # "잘 모르겠는데요"
    r"그게\s*뭐예요",  # "그게 뭐예요?"
    r"그게\s*뭔가요",  # "그게 뭔가요?"
    r"이해.*못\s*했",  # "이해를 못 했어요"
    r"어렵",  # "어렵네요", "좀 어렵습니다"
]

# ---------------------------------------------------------------------------
# 장황함 감지 보조 지표
# ---------------------------------------------------------------------------
# 상담사 발화가 과도하게 길거나 접속사를 남발하는 경우를 탐지한다.
# 장황함 자체가 직접 감점 사유는 아니지만, LLM 판단의 보조 지표로 활용한다.

VERBOSITY_INDICATOR_PATTERNS = [
    r"그리고\s*또",  # "그리고 또..."
    r"아\s*그리고",  # "아 그리고..."
    r"거기에\s*더해서",  # "거기에 더해서..."
    r"추가적으로\s*말씀드리면",  # 부연 반복
    r"덧붙여서",  # "덧붙여서 말씀드리면..."
    r"참고로\s*말씀드리면.*참고로",  # "참고로" 반복 사용
]

# ---------------------------------------------------------------------------
# 두괄식 답변 탐지 패턴 (핵심을 먼저 전달하는 표현)
# ---------------------------------------------------------------------------
# 상담사가 결론/핵심을 먼저 말하고 부연 설명을 이어가는 패턴을 탐지한다.
# 이 패턴이 탐지되면 두괄식 답변(#11)에서 5점 가능성을 시사한다.

CONCLUSION_FIRST_PATTERNS = [
    r"결론적으로",  # "결론적으로 말씀드리면..."
    r"결론부터",  # "결론부터 말씀드리면..."
    r"먼저\s*말씀드리면",  # "먼저 말씀드리면..."
    r"먼저\s*안내\s*드리면",  # "먼저 안내 드리면..."
    r"핵심(은|을|부터)",  # "핵심은...", "핵심부터..."
    r"간단히\s*말씀드리면",  # "간단히 말씀드리면..."
    r"요약하면",  # "요약하면..."
    r"말씀드릴\s*것은",  # "말씀드릴 것은..."
    r"답변\s*드리면",  # "답변 드리면..."
    r"네\s*[,.]?\s*(됩니다|가능합니다|맞습니다|맞으십니다)",  # 명확한 즉답 후 부연
    r"아니요\s*[,.]?\s*(안\s*됩니다|불가합니다|어렵습니다)",  # 명확한 즉답 후 부연
]


# ---------------------------------------------------------------------------
# 사전 분석 헬퍼 함수
# ---------------------------------------------------------------------------


def _detect_jargon(transcript: str) -> list[dict]:
    """상담사 발화에서 내부 용어/전문 용어 사용을 탐지한다."""
    findings: list[dict] = []
    lines = transcript.strip().split("\n")
    turn_number = 0
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        turn_number += 1
        if not is_agent(line_stripped):
            continue
        for pattern in INTERNAL_JARGON_PATTERNS:
            if re.search(pattern, line_stripped, re.IGNORECASE):
                findings.append({"turn": turn_number, "text": line_stripped, "pattern": pattern, "type": "jargon"})
                break
    return findings


def _detect_customer_reask(transcript: str) -> list[dict]:
    """고객 발화에서 되물음(이해 불가) 표현을 탐지한다."""
    findings: list[dict] = []
    lines = transcript.strip().split("\n")
    turn_number = 0
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        turn_number += 1
        if not is_customer(line_stripped):
            continue
        for pattern in CUSTOMER_REASK_PATTERNS:
            if re.search(pattern, line_stripped):
                findings.append({"turn": turn_number, "text": line_stripped, "pattern": pattern, "type": "reask"})
                break
    return findings


def _detect_verbosity(transcript: str) -> list[dict]:
    """상담사 발화에서 장황함 지표를 탐지한다."""
    findings: list[dict] = []
    lines = transcript.strip().split("\n")
    turn_number = 0
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        turn_number += 1
        if not is_agent(line_stripped):
            continue
        for pattern in VERBOSITY_INDICATOR_PATTERNS:
            if re.search(pattern, line_stripped):
                findings.append({"turn": turn_number, "text": line_stripped, "pattern": pattern, "type": "verbosity"})
    return findings


def _detect_conclusion_first(transcript: str) -> list[dict]:
    """상담사 발화에서 두괄식(핵심 먼저) 표현을 탐지한다."""
    findings: list[dict] = []
    lines = transcript.strip().split("\n")
    turn_number = 0
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        turn_number += 1
        if not is_agent(line_stripped):
            continue
        for pattern in CONCLUSION_FIRST_PATTERNS:
            if re.search(pattern, line_stripped):
                findings.append(
                    {"turn": turn_number, "text": line_stripped, "pattern": pattern, "type": "conclusion_first"}
                )
                break
    return findings


# ---------------------------------------------------------------------------
# LLM 시스템 프롬프트 — 설명의 명확성 (#10)
# ---------------------------------------------------------------------------


def _get_clarity_system_prompt(backend: str | None = None, tenant_id: str = "") -> str:
    from prompts import load_prompt

    return load_prompt("item_10_clarity", tenant_id=tenant_id, backend=backend)


# ---------------------------------------------------------------------------
# LLM 시스템 프롬프트 — 두괄식 답변 (#11)
# ---------------------------------------------------------------------------


def _get_structure_system_prompt(backend: str | None = None, tenant_id: str = "") -> str:
    from prompts import load_prompt

    return load_prompt("item_11_conclusion_first", tenant_id=tenant_id, backend=backend)


# ---------------------------------------------------------------------------
# 개별 평가 함수
# ---------------------------------------------------------------------------


def _rule_fallback_clarity(pre_analysis: dict, err: str) -> dict:
    """#10 rule fallback — 예외 메시지를 reason 에 포함해 추적 가능."""
    jargon_count = len(pre_analysis.get("jargon", []))
    reask_count = len(pre_analysis.get("reask", []))
    tag = f"규칙 폴백: {err[:80]}" if err else "규칙 폴백"
    if jargon_count == 0 and reask_count == 0:
        return {"score": 10, "deductions": [], "evidence": [], "confidence": 0.5,
                "summary": f"LLM 응답 파싱 실패 — {tag} (감점 요소 없음)"}
    if jargon_count <= 1 and reask_count == 0:
        return {"score": 7, "deductions": [
            {"reason": f"부분적 장황/매끄럽지 못함 ({tag})", "points": 3, "evidence_ref": ""}
        ], "evidence": [], "confidence": 0.5, "summary": f"LLM 응답 파싱 실패 — {tag}"}
    if jargon_count >= 1 or reask_count >= 1:
        return {"score": 5, "deductions": [
            {"reason": f"내부 용어/고객 되물음 감지 ({tag})", "points": 5, "evidence_ref": ""}
        ], "evidence": [], "confidence": 0.5, "summary": f"LLM 응답 파싱 실패 — {tag}"}
    return {"score": 0, "deductions": [
        {"reason": f"설명 불가 ({tag})", "points": 10, "evidence_ref": ""}
    ], "evidence": [], "confidence": 0.5, "summary": f"LLM 응답 파싱 실패 — {tag}"}


def _rule_fallback_structure(pre_analysis: dict, err: str) -> dict:
    """#11 rule fallback — 예외 메시지를 reason 에 포함해 추적 가능."""
    cf_count = len(pre_analysis.get("conclusion_first", []))
    verb_count = len(pre_analysis.get("verbosity", []))
    tag = f"규칙 폴백: {err[:80]}" if err else "규칙 폴백"
    if cf_count >= 1:
        return {"score": 5, "deductions": [], "evidence": [], "confidence": 0.5,
                "summary": f"LLM 응답 파싱 실패 — {tag} (두괄식 패턴 존재)"}
    if verb_count <= 1:
        return {"score": 3, "deductions": [
            {"reason": f"두괄식 패턴 미감지 ({tag})", "points": 2, "evidence_ref": ""}
        ], "evidence": [], "confidence": 0.5, "summary": f"LLM 응답 파싱 실패 — {tag}"}
    return {"score": 0, "deductions": [
        {"reason": f"장황함 다수 감지 ({tag})", "points": 5, "evidence_ref": ""}
    ], "evidence": [], "confidence": 0.5, "summary": f"LLM 응답 파싱 실패 — {tag}"}


async def _evaluate_clarity(
    transcript: str, consultation_type: str, rules: dict, pre_analysis: dict,
    intent_context: str = "", backend: str | None = None, tenant_id: str = "",
    bedrock_model_id: str | None = None,
) -> dict:
    """#10 설명의 명확성 평가 (10점). 실패 시 내부 rule fallback 반환 — 예외 던지지 않음."""
    rules_str = json.dumps(rules, ensure_ascii=False) if rules else ""

    user_message = f"## Consultation Type\n{consultation_type}\n\n"
    if intent_context:
        user_message += f"## 고객 주요 문의\n{intent_context}\n\n"
    if rules_str:
        user_message += f"## QA Rules and Criteria\n{rules_str}\n\n"
    user_message += f"## Transcript\n{transcript}\n\n"
    user_message += "## Pre-Analysis Results\n"
    user_message += f"- 내부 용어/전문 용어 감지: {len(pre_analysis['jargon'])}건\n"
    for j in pre_analysis["jargon"]:
        user_message += f"  - Turn {j['turn']}: {j['text']}\n"
    user_message += f"- 고객 되물음 감지: {len(pre_analysis['reask'])}건\n"
    for r in pre_analysis["reask"]:
        user_message += f"  - Turn {r['turn']}: {r['text']}\n"
    user_message += f"- 장황함 지표: {len(pre_analysis['verbosity'])}건\n"
    for v in pre_analysis["verbosity"]:
        user_message += f"  - Turn {v['turn']}: {v['text']}\n"
    user_message += "\n## Instructions\n"
    user_message += "설명의 명확성 (QA Item #10)을 평가하세요.\n"
    user_message += "- 고객 눈높이에 맞춘 설명인가?\n"
    user_message += "- 내부 용어/전문 용어를 사용했는가?\n"
    user_message += "- 고객이 되물음 없이 이해했는가?\n"
    user_message += "- 장황하지 않고 핵심을 정리하여 전달했는가?\n"
    user_message += "\n## 출력 길이 제약\n"
    user_message += "- JSON 전체 2500자 이내. summary/reason 은 각 80자 이내, evidence[].text 는 120자 이내.\n"

    # #10 은 JSON 페이로드가 커서 max_tokens 상향. Sonnet Converse API 는 max_tokens 부족 시 중간에 잘림.
    llm = get_chat_model(
        max_tokens=2048, backend=backend, bedrock_model_id=bedrock_model_id,
    )
    try:
        return await invoke_and_parse(
            llm,
            [
                SystemMessage(content=_get_clarity_system_prompt(backend, tenant_id=tenant_id)),
                HumanMessage(content=user_message),
            ],
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("#10 clarity LLM failed → rule fallback: %s", e)
        return _rule_fallback_clarity(pre_analysis, str(e))


async def _evaluate_structure(
    transcript: str, consultation_type: str, rules: dict, pre_analysis: dict,
    intent_context: str = "", backend: str | None = None, tenant_id: str = "",
    bedrock_model_id: str | None = None,
) -> dict:
    """#11 두괄식 답변 평가 (5점). 실패 시 내부 rule fallback 반환 — 예외 던지지 않음."""
    rules_str = json.dumps(rules, ensure_ascii=False) if rules else ""

    user_message = f"## Consultation Type\n{consultation_type}\n\n"
    if intent_context:
        user_message += f"## 고객 주요 문의\n{intent_context}\n\n"
    if rules_str:
        user_message += f"## QA Rules and Criteria\n{rules_str}\n\n"
    user_message += f"## Transcript\n{transcript}\n\n"
    user_message += "## Pre-Analysis Results\n"
    user_message += f"- 두괄식(결론 먼저) 패턴 감지: {len(pre_analysis['conclusion_first'])}건\n"
    for c in pre_analysis["conclusion_first"]:
        user_message += f"  - Turn {c['turn']}: {c['text']}\n"
    user_message += f"- 장황함 지표: {len(pre_analysis['verbosity'])}건\n"
    for v in pre_analysis["verbosity"]:
        user_message += f"  - Turn {v['turn']}: {v['text']}\n"
    user_message += "\n## Instructions\n"
    user_message += "두괄식 답변 (QA Item #11)을 평가하세요.\n"
    user_message += "- 핵심 내용을 먼저 전달했는가?\n"
    user_message += "- 부연 설명이 핵심 뒤에 이어지는 구조인가?\n"
    user_message += "- 두서 없이 장황하지 않은가?\n"
    user_message += "\n## 출력 길이 제약\n"
    user_message += "- JSON 전체 1800자 이내. summary/reason 은 각 80자 이내, evidence[].text 는 120자 이내.\n"

    # max_tokens 1024 → 1536 으로 상향 (#11 응답도 Sonnet 에서 잘리는 사례 관측)
    llm = get_chat_model(
        max_tokens=1536, backend=backend, bedrock_model_id=bedrock_model_id,
    )
    try:
        return await invoke_and_parse(
            llm,
            [
                SystemMessage(content=_get_structure_system_prompt(backend, tenant_id=tenant_id)),
                HumanMessage(content=user_message),
            ],
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("#11 structure LLM failed → rule fallback: %s", e)
        return _rule_fallback_structure(pre_analysis, str(e))


# ---------------------------------------------------------------------------
# 노드 함수 (LangGraph 노드 진입점)
# ---------------------------------------------------------------------------


async def scope_node(state: QAState, ctx: NodeContext) -> dict[str, Any]:
    """Evaluate explanation clarity and structured answer delivery.

    QA Item #10 — 설명의 명확성, max 10 points (10/7/5/0).
    QA Item #11 — 두괄식 답변, max 5 points (5/3/0).

    Returns {"evaluations": [result1, result2]} for operator.add merge.
    """
    # --- 선별 턴 할당 우선 사용, 폴백으로 전체 transcript ---
    # NOTE: assignment 우선 패턴 보존 — ctx.transcript 미사용
    assignment = state.get("agent_turn_assignments", {}).get("scope", {})
    assigned_text = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])
    transcript = assigned_text

    consultation_type = ctx.consultation_type
    rules = state.get("rules", {})

    logger.info(
        f"scope_node: type='{consultation_type}', transcript_len={len(transcript)}, "
        f"assigned_turns={len(assigned_turns)}"
    )

    # 통화 내역이 없으면 평가 불가 — 에러 결과 반환
    if not transcript:
        return {
            "evaluations": [
                build_llm_failure_result("scope-agent", "No transcript provided for evaluation.")
            ]
        }

    # --- 1단계: regex 기반 사전 분석 ---
    # assigned_turns가 있으면 구조화된 턴 데이터 활용, 없으면 기존 텍스트 파싱
    if assigned_turns:
        agent_lines = [t for t in assigned_turns if t.get("speaker") == "agent"]
        customer_lines = [t for t in assigned_turns if t.get("speaker") == "customer"]

        # 내부 용어: agent 턴에서 탐지
        jargon_findings = []
        for t in agent_lines:
            for pattern in INTERNAL_JARGON_PATTERNS:
                if re.search(pattern, t["text"], re.IGNORECASE):
                    jargon_findings.append(
                        {"turn": t["turn_id"], "text": t["text"], "pattern": pattern, "type": "jargon"}
                    )
                    break

        # 고객 되물음: customer 턴에서 탐지
        reask_findings = []
        for t in customer_lines:
            for pattern in CUSTOMER_REASK_PATTERNS:
                if re.search(pattern, t["text"]):
                    reask_findings.append(
                        {"turn": t["turn_id"], "text": t["text"], "pattern": pattern, "type": "reask"}
                    )
                    break

        # 장황함: agent 턴에서 탐지
        verbosity_findings = []
        for t in agent_lines:
            for pattern in VERBOSITY_INDICATOR_PATTERNS:
                if re.search(pattern, t["text"]):
                    verbosity_findings.append(
                        {"turn": t["turn_id"], "text": t["text"], "pattern": pattern, "type": "verbosity"}
                    )

        # 두괄식: agent 턴에서 탐지
        conclusion_first_findings = []
        for t in agent_lines:
            for pattern in CONCLUSION_FIRST_PATTERNS:
                if re.search(pattern, t["text"]):
                    conclusion_first_findings.append(
                        {"turn": t["turn_id"], "text": t["text"], "pattern": pattern, "type": "conclusion_first"}
                    )
                    break

        pre_analysis = {
            "jargon": jargon_findings,
            "reask": reask_findings,
            "verbosity": verbosity_findings,
            "conclusion_first": conclusion_first_findings,
        }

        # LLM에 전달할 transcript를 턴 번호 포함 형태로 재구성
        transcript = "\n".join(f"[Turn {t['turn_id']}] {t['text']}" for t in assigned_turns)
    else:
        pre_analysis = {
            "jargon": _detect_jargon(transcript),
            "reask": _detect_customer_reask(transcript),
            "verbosity": _detect_verbosity(transcript),
            "conclusion_first": _detect_conclusion_first(transcript),
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
    # _evaluate_clarity / _evaluate_structure 는 내부에서 rule fallback 을 반환하므로
    # 일반 실패는 예외를 던지지 않는다. return_exceptions=True 로 LLMTimeoutError 같은
    # reraise 예외가 한 쪽에서 나도 다른 쪽 결과는 버리지 않는다.
    _backend = ctx.llm_backend
    _bedrock_model_id = ctx.bedrock_model_id
    _tenant_id = ctx.tenant_id
    gathered = await asyncio.gather(
        _evaluate_clarity(
            transcript, consultation_type, rules, pre_analysis, intent_context,
            backend=_backend, bedrock_model_id=_bedrock_model_id, tenant_id=_tenant_id,
        ),
        _evaluate_structure(
            transcript, consultation_type, rules, pre_analysis, intent_context,
            backend=_backend, bedrock_model_id=_bedrock_model_id, tenant_id=_tenant_id,
        ),
        return_exceptions=True,
    )
    clarity_result, structure_result = gathered
    # 한 쪽만 LLMTimeoutError/기타 reraise 예외면 그 항목만 rule fallback 으로 대체.
    # 전체 실패(양쪽 모두 타임아웃)는 기존 정책대로 상위에 전파.
    timeouts = [r for r in gathered if isinstance(r, LLMTimeoutError)]
    if len(timeouts) == 2:
        raise timeouts[0]
    if isinstance(clarity_result, Exception):
        logger.warning("#10 clarity gather exception → rule fallback: %s", clarity_result)
        clarity_result = _rule_fallback_clarity(pre_analysis, str(clarity_result))
    if isinstance(structure_result, Exception):
        logger.warning("#11 structure gather exception → rule fallback: %s", structure_result)
        structure_result = _rule_fallback_structure(pre_analysis, str(structure_result))

    # --- 3단계: 점수 검증 (Scorer를 통해 qa_rules.py 기준으로 검증) ---
    score_result_10 = _scorer.score_item(
        item_number=10,
        verdict=clarity_result.get("score", 0),
        reason=clarity_result.get("summary", ""),
        confidence=clarity_result.get("confidence", 0.85),
    )
    score_result_11 = _scorer.score_item(
        item_number=11,
        verdict=structure_result.get("score", 0),
        reason=structure_result.get("summary", ""),
        confidence=structure_result.get("confidence", 0.85),
    )
    score_10 = score_result_10.score
    score_11 = score_result_11.score

    # --- 3-1단계: score × deductions 산술 보정 (LLM hallucination 방어) ---
    # sLLM 이 종종 score=만점이면서 deductions 에도 값을 채우는 모순을 낸다.
    # score_validation 이 이를 arithmetic_mismatch 로 차단하므로 여기서 미리 보정.
    rec_10 = reconcile(
        item_number=10,
        score=score_10,
        max_score=10,
        deductions=clarity_result.get("deductions", []),
    )
    if rec_10.note:
        clarity_result["deductions"] = rec_10.deductions
        score_10 = rec_10.score

    rec_11 = reconcile(
        item_number=11,
        score=score_11,
        max_score=5,
        deductions=structure_result.get("deductions", []),
    )
    if rec_11.note:
        structure_result["deductions"] = rec_11.deductions
        score_11 = rec_11.score

    # evidence 선정: 각 항목 deductions.evidence_ref 우선 → fallback assigned_turns (greeting/단답 제외)
    deductions_10 = clarity_result.get("deductions", [])
    deductions_11 = structure_result.get("deductions", [])
    clarity_turn_evidence = build_turn_evidence(assigned_turns, deductions_10)
    structure_turn_evidence = build_turn_evidence(assigned_turns, deductions_11)
    clarity_evidence = clarity_turn_evidence if clarity_turn_evidence else clarity_result.get("evidence", [])
    structure_evidence = (
        structure_turn_evidence if structure_turn_evidence else structure_result.get("evidence", [])
    )

    deduction_log_entries = build_deduction_log_from_pairs(
        [(10, clarity_result), (11, structure_result)], "scope-agent"
    )

    # 최종 평가 결과를 operator.add 리듀서 형태로 반환
    return {
        "evaluations": [
            {
                "status": "success",
                "agent_id": "scope-agent",
                "evaluation": {
                    "item_number": 10,
                    "item_name": "설명의 명확성",
                    "max_score": 10,
                    "score": score_10,
                    "jargon_found": clarity_result.get("jargon_found", []),
                    "customer_reasks": clarity_result.get("customer_reasks", []),
                    "clarity_assessment": clarity_result.get("clarity_assessment", ""),
                    "deductions": deductions_10,
                    "evidence": clarity_evidence,
                    "confidence": clarity_result.get("confidence", 0.85),
                    "details": {"assigned_turns": assigned_turns},
                },
            },
            {
                "status": "success",
                "agent_id": "scope-agent",
                "evaluation": {
                    "item_number": 11,
                    "item_name": "두괄식 답변",
                    "max_score": 5,
                    "score": score_11,
                    "conclusion_first_patterns": structure_result.get("conclusion_first_patterns", []),
                    "structure_assessment": structure_result.get("structure_assessment", ""),
                    "deductions": deductions_11,
                    "evidence": structure_evidence,
                    "confidence": structure_result.get("confidence", 0.85),
                    "details": {"assigned_turns": assigned_turns},
                },
            },
        ],
        "deduction_log": deduction_log_entries,
    }
