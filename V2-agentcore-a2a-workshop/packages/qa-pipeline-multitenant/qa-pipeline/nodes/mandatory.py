# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Mandatory evaluation nodes for the QA LangGraph pipeline.

Provides two evaluation functions and a combined ``mandatory_node``
entry point used by the graph:

- evaluate_inquiry_identification(state)  — QA Item #8 (문의 파악 및 재확인, 5pt)
- evaluate_customer_info_check(state)     — QA Item #9 (고객정보 확인, 5pt)

Pre-analysis logic detects paraphrasing/reconfirmation patterns, re-questioning
patterns, and customer info verification patterns from the transcript text.
"""

# =============================================================================
# 니즈 파악 평가 노드 (mandatory.py)
# =============================================================================
# 이 모듈은 QA 평가 파이프라인에서 "니즈 파악" 영역을 담당합니다.
# 총 2개의 QA 평가 항목을 처리합니다 (총 10점):
#
#   항목 #8: 문의 파악 및 재확인(복창) (최대 5점)
#     - 5점: 고객 문의를 정확히 파악 후 핵심 내용 재확인(복창) 진행
#     - 3점: 문의 파악은 되었으나 재확인 누락, 또는 1회 재질의 발생
#     - 0점: 문의 내용 미파악으로 동문서답 또는 반복 재질의 발생
#     - 키워드: 복창("~말씀이시죠", "~하신 거죠"), 재질의 패턴, 동문서답 감지
#
#   항목 #9: 고객정보 확인 (최대 5점)
#     - 5점: 필요한 고객 정보(성함, 연락처 등)를 양해 표현과 함께 확인
#     - 3점: 고객 정보 일부만 확인 또는 양해 표현 없이 확인
#     - 0점: 고객 정보 확인 누락
#     - ※ 고객이 먼저 정보를 제공한 경우, 상담사가 복창 확인하면 만점 인정
#     - 키워드: "성함", "이름", "연락처", "전화번호", "주민", 양해 표현 패턴
#
# 처리 흐름:
#   1) 전사록에서 정규식 기반 사전 분석 수행
#   2) 사전 분석 결과 + 전체 전사록을 LLM에 전달하여 최종 채점
#   3) 2개 항목을 asyncio.gather로 병렬 실행하여 결과 병합
# =============================================================================

from __future__ import annotations

import asyncio
import logging
import re
from langchain_core.messages import HumanMessage, SystemMessage
from nodes.llm import LLMTimeoutError, get_chat_model, invoke_and_parse
from nodes.skills.deduction_log import build_deduction_log_from_evaluations
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
# 정규식 패턴 목록
# ---------------------------------------------------------------------------

# 상담사 복창/재확인 패턴 (항목 #8 사전 분석용)
# 상담사가 고객의 문의를 자신의 말로 바꾸어 확인하는 표현
# 복창이 있으면 5점, 없으면 3점으로 차등 채점
PARAPHRASE_PATTERNS = [
    r"말씀이시죠\??",  # "~말씀이시죠?"
    r"말씀이신\s*거죠\??",
    r"이시죠\??",  # "~이시죠?" (재확인 질문형)
    r"맞으시죠\??",  # "~맞으시죠?"
    r"맞으신\s*거죠\??",
    r"이신\s*거죠\??",
    r"하신\s*거죠\??",  # "~하신 거죠?"
    r"라는\s*말씀이시죠",
    r"라는\s*말씀이신\s*거죠",
    r"확인해\s*드리겠습니다",  # "확인해 드리겠습니다"
    r"확인\s*드리겠습니다",
    r"정리해\s*드리면",  # "정리해 드리면~" (요약 후 확인)
    r"정리하면",
    r"다시\s*한번\s*확인",
    r"요약하면",  # "요약하면~"
    r"요약해\s*드리면",
    r"말씀하신\s*내용.*정리",  # "말씀하신 내용을 정리하면"
    r"그러니까.*말씀은",  # "그러니까 말씀은~"
    r"네,?\s*.*건으로\s*문의",  # "네, ~건으로 문의주신 거죠?"
]

# 고객 재질의 패턴 (항목 #8 사전 분석용)
# 고객이 상담사의 응답에 만족하지 못해 같은 내용을 다시 질문하는 패턴
# 재질의가 발생하면 문의 파악이 불충분한 것으로 간주
CUSTOMER_REQUERY_PATTERNS = [
    r"아까\s*말씀\s*드렸는데",  # 이전에 이미 말한 내용을 다시 언급
    r"아까\s*말했는데",
    r"다시\s*말씀\s*드리면",  # 다시 설명하겠다는 표현
    r"다시\s*말하면",
    r"제가\s*방금",  # 방금 전 발화를 재차 언급
    r"방금\s*말씀\s*드렸",
    r"이미\s*말씀\s*드렸",  # 이미 전달한 내용을 강조
    r"아까도\s*말씀",  # 반복 강조
    r"같은\s*말\s*반복",  # 같은 말 반복 불만
    r"또\s*같은\s*얘기",
    r"몇\s*번을\s*말해야",  # 강한 불만 표현
    r"계속\s*말씀\s*드리는데",
    r"그러니까\s*제\s*말은",  # 자신의 의도를 재차 설명
    r"아니요?\s*그게\s*아니라",  # 상담사 이해가 틀렸다고 교정
    r"그게\s*아니고",
]

# 고객정보 확인 패턴 (항목 #9 사전 분석용)
# 상담사가 고객의 개인정보를 확인하는 표현
CUSTOMER_INFO_PATTERNS = [
    r"성함",  # "성함이 어떻게 되세요?"
    r"이름",  # "이름 확인 부탁드립니다"
    r"연락처",  # "연락처 알려주시겠습니까?"
    r"전화번호",  # "전화번호 확인해 드리겠습니다"
    r"휴대폰\s*번호",
    r"핸드폰\s*번호",
    r"주민",  # "주민등록번호 앞자리"
    r"생년월일",  # "생년월일 확인 부탁드립니다"
    r"주소",  # "주소 확인"
    r"고객\s*번호",  # "고객 번호"
    r"회원\s*번호",
    r"계약\s*번호",
    r"본인\s*확인",  # "본인 확인"
]

# 양해 표현 패턴 (항목 #9 - 정보 확인 시 양해를 구하는 표현)
# 고객정보 확인 전에 양해를 구하면 5점, 양해 없이 바로 확인하면 3점
INFO_COURTESY_PATTERNS = [
    r"확인을?\s*위해",  # "확인을 위해 ~"
    r"본인\s*확인.*위해",
    r"죄송하지만",  # "죄송하지만 ~"
    r"죄송합니다만",
    r"번거로우시겠지만",
    r"실례지만",
    r"양해\s*부탁",
    r"말씀해\s*주시겠",  # "말씀해 주시겠습니까?"
    r"알려\s*주시겠",  # "알려 주시겠습니까?"
    r"부탁드려도\s*될까",
    # 정보 안내 예절 (LIVE-001 니즈파악 #9 회복)
    # FIX-NEW-b2: 단독 "도와드리/안내드리"는 FP 과다 → 정보 요청 맥락 접두사 제약
    r"확인\s*도와드[리립]",
    r"(확인|예약|접수|처리|조회|답변|문의|상담|가입|변경|해지|등록|회수)[^.!?\n]{0,10}도와\s*드[리립]",
    r"알려\s*드[리립]",
    r"(확인|예약|접수|처리|조회|답변|문의|상담|가입|변경|해지|등록|회수|자세히|상세히|절차|방법|내용)[^.!?\n]{0,10}안내\s*드[리립]",
    r"말씀\s*드[리립]",
    r"정보\s*드[리립]",
    r"조회\s*해\s*드[리립]",
    r"처리\s*해\s*드[리립]",
    r"확인\s*해\s*드[리립]",
    r"답변\s*드[리립]",
    r"설명\s*드[리립]",
    r"전달\s*드[리립]",
    r"찾아\s*드[리립]",
    r"접수\s*도와드[리립]",
    r"예약\s*도와드[리립]",
]

# 고객 선제 정보 제공 패턴 (항목 #9)
# 고객이 먼저 자신의 정보를 제공하는 경우 (이 경우 상담사 복창만으로 만점 인정)
CUSTOMER_INFO_PROVISION_PATTERNS = [
    r"제\s*이름은",
    r"제\s*성함은",
    r"제\s*번호는",
    r"제\s*연락처는",
    r"제\s*전화번호는",
    r"제\s*주소는",
]


# ---------------------------------------------------------------------------
# 사전 분석(Pre-analysis) 헬퍼 함수
# ---------------------------------------------------------------------------
# LLM 호출 전에 정규식으로 전사록을 분석하여 핵심 지표를 추출하는 함수들.
# 각 함수는 전사록을 줄 단위로 순회하며, 화자를 식별하고 패턴을 매칭함.
# ---------------------------------------------------------------------------


def _detect_paraphrasing(transcript: str) -> list[dict]:
    """Detect agent paraphrasing / reconfirmation of customer's request."""
    # 상담사 복창/재확인 탐지 (항목 #8)
    # 상담사 발화에서 PARAPHRASE_PATTERNS 매칭
    # 복창 여부가 5점(있음)과 3점(없음)의 차이를 결정
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
        for pattern in PARAPHRASE_PATTERNS:
            if re.search(pattern, line_stripped):
                findings.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                break
    return findings


def _detect_customer_requery(transcript: str) -> list[dict]:
    """Detect instances where the customer re-queries or corrects the agent."""
    # 고객 재질의 탐지 (항목 #8)
    # 고객 발화에서 CUSTOMER_REQUERY_PATTERNS 매칭
    # 재질의가 반복되면 동문서답/미파악으로 판단하여 0점 대상
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
        for pattern in CUSTOMER_REQUERY_PATTERNS:
            if re.search(pattern, line_stripped):
                findings.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                break  # 한 턴에서 첫 번째 매칭만 기록
    return findings


def _detect_customer_info_check(transcript: str) -> dict[str, list[dict]]:
    """Detect customer information verification patterns."""
    # 고객정보 확인 관련 패턴 탐지 (항목 #9)
    # 3가지 유형으로 분류:
    #   info_check: 상담사가 고객 정보를 직접 확인하는 표현
    #   courtesy: 정보 확인 시 양해 표현을 사용하는지
    #   customer_provided: 고객이 먼저 정보를 제공하는 경우
    info_check: list[dict] = []
    courtesy: list[dict] = []
    customer_provided: list[dict] = []
    lines = transcript.strip().split("\n")
    turn_number = 0
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        turn_number += 1

        # 고객 발화에서 선제 정보 제공 탐지
        if is_customer(line_stripped):
            for pattern in CUSTOMER_INFO_PROVISION_PATTERNS:
                if re.search(pattern, line_stripped):
                    customer_provided.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                    break
            continue

        # 상담사 발화에서 정보 확인 + 양해 표현 탐지
        if not is_agent(line_stripped):
            continue

        # 고객정보 확인 패턴 탐색
        for pattern in CUSTOMER_INFO_PATTERNS:
            if re.search(pattern, line_stripped):
                info_check.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                break

        # 양해 표현 패턴 탐색
        for pattern in INFO_COURTESY_PATTERNS:
            if re.search(pattern, line_stripped):
                courtesy.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                break

    return {"info_check": info_check, "courtesy": courtesy, "customer_provided": customer_provided}


# ---------------------------------------------------------------------------
# LLM 시스템 프롬프트
# ---------------------------------------------------------------------------
# 각 평가 항목별로 LLM에게 전달할 시스템 프롬프트를 정의.
# 프롬프트에는 채점 기준, 복창/재질의 규칙, 출력 JSON 포맷이 포함됨.
# ---------------------------------------------------------------------------


def _get_inquiry_identification_system_prompt(backend: str | None = None, tenant_id: str = "") -> str:
    # 문의 파악 및 재확인(복창) 시스템 프롬프트 (항목 #8, 최대 5점)
    # 채점: 5/3/0 (3단계)
    # 핵심 체크: 문의 파악 여부, 복창/재확인 수행 여부, 재질의 횟수
    from prompts import load_prompt

    return load_prompt("item_08_inquiry_paraphrase", tenant_id=tenant_id, backend=backend)


def _get_customer_info_check_system_prompt(backend: str | None = None, tenant_id: str = "") -> str:
    # 고객정보 확인 시스템 프롬프트 (항목 #9, 최대 5점)
    # 채점: 5/3/0 (3단계)
    # 핵심 규칙: 양해 표현과 함께 확인=5점, 양해 없이 확인=3점, 확인 누락=0점
    # 특이사항: 고객 선제 정보 제공 시 상담사 복창 확인만으로 만점 인정
    from prompts import load_prompt

    return load_prompt("item_09_customer_info", tenant_id=tenant_id, backend=backend)


# ---------------------------------------------------------------------------
# 노드 평가 함수 (QA 항목당 1개)
# ---------------------------------------------------------------------------
# 각 함수는 다음 순서로 동작:
#   1) state에서 전사록 + 상담유형 추출
#   2) 정규식 사전 분석으로 핵심 지표 탐지
#   3) 사전 분석 결과 + 전사록을 LLM 프롬프트로 구성
#   4) LLM 호출 및 JSON 파싱
#   5) 점수 보정 후 결과 반환
# ---------------------------------------------------------------------------


async def evaluate_inquiry_identification(state: QAState) -> dict[str, Any]:
    """Evaluate inquiry identification and paraphrasing.

    QA Item #8 -- 문의 파악 및 재확인(복창), max 5 points.
    Scoring: 5/3/0.

    Returns {"evaluations": [result]} for operator.add merge.
    """
    # 항목 #8: 문의 파악 및 재확인(복창) 평가
    # 평가 대상: 상담사가 고객의 문의를 정확히 파악하고 복창/재확인했는지
    # 핵심 지표: 복창 횟수, 고객 재질의 횟수

    # 선별 턴 할당 데이터 우선 사용, 없으면 전체 transcript 폴백
    assignment = state.get("agent_turn_assignments", {}).get("mandatory", {})
    transcript = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])
    consultation_type = state.get("consultation_type", "general")

    logger.info(f"evaluate_inquiry_identification: type='{consultation_type}', transcript_len={len(transcript)}")

    if not transcript:
        return {
            "evaluations": [
                build_llm_failure_result("mandatory-agent", "No transcript provided for evaluation.")
            ]
        }

    # 사전 분석: 복창 + 고객 재질의 탐지
    paraphrases = _detect_paraphrasing(transcript)
    requeries = _detect_customer_requery(transcript)
    paraphrase_count = len(paraphrases)
    requery_count = len(requeries)

    logger.info(f"Pre-analysis: paraphrases={paraphrase_count}, requeries={requery_count}")

    # LLM 프롬프트 구성: 선별 턴 번호 포함 또는 전체 전사록
    if assigned_turns:
        numbered_text = "\n".join(f"[Turn {t['turn_id']}] {t['text']}" for t in assigned_turns)
        transcript_for_llm = numbered_text
    else:
        transcript_for_llm = transcript
    user_message = f"## Consultation Type\n{consultation_type}\n\n"
    user_message += f"## Transcript\n{transcript_for_llm}\n\n"
    # 사전 분석 결과
    user_message += "## Pre-Analysis Results\n"
    user_message += f"- Agent paraphrase/reconfirmation count (복창 횟수): {paraphrase_count}\n"
    if paraphrases:
        user_message += "- Paraphrase instances:\n"
        for p in paraphrases:
            user_message += f"  - Turn {p['turn']}: {p['text']}\n"
    user_message += f"- Customer re-query count (재질의 횟수): {requery_count}\n"
    if requeries:
        user_message += "- Re-query instances:\n"
        for r in requeries:
            user_message += f"  - Turn {r['turn']}: {r['text']}\n"
    user_message += "\n"
    # 평가 지시사항
    user_message += (
        "## Instructions\n"
        "Evaluate for 문의 파악 및 재확인(복창) (QA Item #8). Consider pre-analysis above.\n"
        "- 정확 파악 + 복창 → 5점\n"
        "- 파악O, 복창X / 1회 재질의 → 3점\n"
        "- 미파악/동문서답/반복 재질의 → 0점\n\n"
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
                    content=_get_inquiry_identification_system_prompt(
                        backend, tenant_id=(state.get("tenant") or {}).get("tenant_id", ""),
                    ),
                ),
                HumanMessage(content=user_message),
            ],
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #8 LLM failed, fallback to rule: %s", e)
        if paraphrase_count >= 1 and requery_count == 0:
            fb_score_8, fb_ded_8 = 5, []
        elif paraphrase_count == 0 or requery_count == 1:
            fb_score_8 = 3
            fb_ded_8 = [{"reason": "복창 누락 또는 재질의 1회 (LLM 실패 — 규칙 폴백)", "points": 2, "evidence_ref": ""}]
        else:
            fb_score_8 = 0
            fb_ded_8 = [{"reason": "반복 재질의 감지 (LLM 실패 — 규칙 폴백)", "points": 5, "evidence_ref": ""}]
        return {
            "evaluations": [
                {
                    "status": "success",
                    "agent_id": "mandatory-agent",
                    "evaluation": {
                        "item_number": 8,
                        "item_name": "문의 파악 및 재확인(복창)",
                        "max_score": 5,
                        "score": fb_score_8,
                        "deductions": fb_ded_8,
                        "evidence": [{"turn": p["turn"], "text": p["text"]} for p in paraphrases[:3]],
                        "confidence": 0.6,
                        "details": {"paraphrase_count": paraphrase_count, "requery_count": requery_count, "fallback": True},
                    },
                }
            ]
        }

    # 점수 보정: Scorer를 통해 qa_rules.py 기준으로 검증
    raw_score = evaluation.get("score", 0)
    score_result = _scorer.score_item(
        item_number=8,
        verdict=raw_score,
        reason=evaluation.get("summary", ""),
        confidence=evaluation.get("confidence", 0.85),
    )
    score = score_result.score

    # score × deductions 산술 보정 (LLM hallucination 방어)
    rec = reconcile(
        item_number=8, score=score, max_score=5,
        deductions=evaluation.get("deductions", []),
    )
    if rec.note:
        evaluation["deductions"] = rec.deductions
        score = rec.score

    # evidence 선정: deductions.evidence_ref 우선 → fallback assigned_turns (greeting/단답 제외)
    deductions_8 = evaluation.get("deductions", [])
    turn_evidence = build_turn_evidence(assigned_turns, deductions_8)

    return {
        "evaluations": [
            {
                "status": "success",
                "agent_id": "mandatory-agent",
                "evaluation": {
                    "item_number": 8,
                    "item_name": "문의 파악 및 재확인(복창)",
                    "max_score": 5,
                    "score": score,
                    "deductions": deductions_8,
                    "evidence": turn_evidence if turn_evidence else evaluation.get("evidence", []),
                    "confidence": evaluation.get("confidence", 0.85),
                    "details": {
                        "customer_need_identified": evaluation.get("customer_need_identified", ""),
                        "paraphrase_found": evaluation.get("paraphrase_found", paraphrase_count > 0),
                        "requery_count": evaluation.get("requery_count", requery_count),
                        "paraphrase_count": paraphrase_count,
                        "assigned_turns": assigned_turns,
                    },
                },
            }
        ]
    }


async def evaluate_customer_info_check(state: QAState) -> dict[str, Any]:
    """Evaluate customer information verification quality.

    QA Item #9 -- 고객정보 확인, max 5 points.
    Scoring: 5/3/0.

    Returns {"evaluations": [result]} for operator.add merge.
    """
    # 항목 #9: 고객정보 확인 평가
    # 평가 대상: 상담사가 필요한 고객 정보를 양해 표현과 함께 확인했는지
    # 핵심 지표: 정보 확인 횟수, 양해 표현 사용, 고객 선제 제공 여부

    # 선별 턴 할당 데이터 우선 사용, 없으면 전체 transcript 폴백
    assignment = state.get("agent_turn_assignments", {}).get("mandatory", {})
    transcript = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])
    consultation_type = state.get("consultation_type", "general")

    logger.info(f"evaluate_customer_info_check: type='{consultation_type}', transcript_len={len(transcript)}")

    if not transcript:
        return {
            "evaluations": [
                build_llm_failure_result("mandatory-agent", "No transcript provided for evaluation.")
            ]
        }

    # 사전 분석: 정보 확인 + 양해 표현 + 고객 선제 제공 탐지
    info_findings = _detect_customer_info_check(transcript)
    info_count = len(info_findings["info_check"])
    courtesy_count = len(info_findings["courtesy"])
    customer_provided_count = len(info_findings["customer_provided"])

    logger.info(
        f"Pre-analysis: info_check={info_count}, courtesy={courtesy_count}, customer_provided={customer_provided_count}"
    )

    # LLM 프롬프트 구성: 선별 턴 번호 포함 또는 전체 전사록
    if assigned_turns:
        numbered_text = "\n".join(f"[Turn {t['turn_id']}] {t['text']}" for t in assigned_turns)
        transcript_for_llm = numbered_text
    else:
        transcript_for_llm = transcript
    user_message = f"## Consultation Type\n{consultation_type}\n\n"
    user_message += f"## Transcript\n{transcript_for_llm}\n\n"
    # 사전 분석 결과
    user_message += "## Pre-Analysis Results\n"
    user_message += f"- Customer info check count (정보 확인 횟수): {info_count}\n"
    if info_findings["info_check"]:
        user_message += "- Info check instances:\n"
        for i in info_findings["info_check"]:
            user_message += f"  - Turn {i['turn']}: {i['text']}\n"
    user_message += f"- Courtesy/politeness expressions count (양해 표현): {courtesy_count}\n"
    if info_findings["courtesy"]:
        user_message += "- Courtesy instances:\n"
        for c in info_findings["courtesy"]:
            user_message += f"  - Turn {c['turn']}: {c['text']}\n"
    user_message += f"- Customer provided info first (고객 선제 제공): {customer_provided_count}\n"
    if info_findings["customer_provided"]:
        user_message += "- Customer provision instances:\n"
        for cp in info_findings["customer_provided"]:
            user_message += f"  - Turn {cp['turn']}: {cp['text']}\n"
    user_message += "\n"
    # 평가 지시사항 (기준표 정합 — 감점 스케일 동기화)
    user_message += (
        "## Instructions\n"
        "Evaluate for 고객정보 확인 (QA Item #9). Consider pre-analysis above.\n"
        "- 양해 표현 + 정보 확인 → 5점 (만점, deductions 합계 0)\n"
        "- 정보 일부 확인 또는 양해 없이 확인 → 3점 (감점 -2)\n"
        "- 정보 확인 누락 → 0점 (감점 -5)\n"
        "- 고객 선제 제공 + 상담사 복창/재확인 → 5점 (양해 표현 별도 요구 없음)\n"
        "  ※ 고객 선제 제공이 있더라도 상담사가 복창/재확인하지 않았다면 3점 이하.\n\n"
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
                    content=_get_customer_info_check_system_prompt(
                        backend, tenant_id=(state.get("tenant") or {}).get("tenant_id", ""),
                    ),
                ),
                HumanMessage(content=user_message),
            ],
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #9 LLM failed, fallback to rule: %s", e)
        # 기준표 정합:
        #  5점: 양해 표현 + 정보 확인 / 고객 선제 제공 + 상담사 복창 확인
        #  3점: 정보 일부 확인 또는 양해 없이 확인 / 고객 선제 제공했으나 복창 확인 누락
        #  0점: 고객 정보 확인 누락 (상담사 정보 확인 발화 전무)
        # 고객 선제 제공 시 상담사가 복창(info_count>=1) 또는 양해(courtesy_count>=1) 흔적이
        # 있어야 5점 인정 — 기준표 "복창 확인하면 만점 인정" 준수.
        if customer_provided_count >= 1 and (info_count >= 1 or courtesy_count >= 1):
            fb_score_9, fb_ded_9 = 5, []
        elif info_count >= 1 and courtesy_count >= 1:
            fb_score_9, fb_ded_9 = 5, []
        elif customer_provided_count >= 1:
            # 고객 선제 제공했으나 상담사 복창 확인 근거가 약함 — 3점
            fb_score_9 = 3
            fb_ded_9 = [
                {
                    "reason": "고객 선제 제공했으나 복창/재확인 근거 부족 (LLM 실패 — 규칙 폴백)",
                    "points": 2,
                    "evidence_ref": "",
                }
            ]
        elif info_count >= 1:
            fb_score_9 = 3
            fb_ded_9 = [{"reason": "양해 표현 없이 정보 확인 (LLM 실패 — 규칙 폴백)", "points": 2, "evidence_ref": ""}]
        else:
            fb_score_9 = 0
            fb_ded_9 = [{"reason": "고객정보 확인 누락 (LLM 실패 — 규칙 폴백)", "points": 5, "evidence_ref": ""}]
        return {
            "evaluations": [
                {
                    "status": "success",
                    "agent_id": "mandatory-agent",
                    "evaluation": {
                        "item_number": 9,
                        "item_name": "고객정보 확인",
                        "max_score": 5,
                        "score": fb_score_9,
                        "deductions": fb_ded_9,
                        "evidence": [{"turn": i["turn"], "text": i["text"]} for i in info_findings["info_check"][:3]],
                        "confidence": 0.6,
                        "details": {
                            "info_count": info_count,
                            "courtesy_count": courtesy_count,
                            "customer_provided_count": customer_provided_count,
                            "fallback": True,
                        },
                    },
                }
            ]
        }

    # 점수 보정: Scorer를 통해 qa_rules.py 기준으로 검증
    raw_score = evaluation.get("score", 0)
    score_result = _scorer.score_item(
        item_number=9,
        verdict=raw_score,
        reason=evaluation.get("summary", ""),
        confidence=evaluation.get("confidence", 0.85),
    )
    score = score_result.score

    # G-1 룰 가드: 정보 요청이 1회 이상 존재하면 score=0 금지 (룰상 3점 이상)
    # LLM 이 0점/3점 구분에 혼동하는 케이스 방어 — 2026-04-14 발견
    if score == 0 and info_count >= 1:
        logger.warning(
            f"Item #9 guard: LLM returned score=0 with info_count={info_count} (rule violation); "
            f"force-corrected to score=3 (양해 없이 정보 확인)"
        )
        score = 3
        # deductions 를 -2 단건으로 정리 (기존 다건 deduction 은 룰 위반)
        evaluation["deductions"] = [
            {
                "reason": "양해 표현 없이 정보 확인 (룰 가드: 0→3 보정)",
                "points": 2,
                "evidence_ref": (
                    info_findings["info_check"][0].get("turn", "")
                    if info_findings["info_check"] else ""
                ),
            }
        ]

    # score × deductions 산술 보정 (LLM hallucination 방어)
    rec = reconcile(
        item_number=9, score=score, max_score=5,
        deductions=evaluation.get("deductions", []),
    )
    if rec.note:
        evaluation["deductions"] = rec.deductions
        score = rec.score

    # evidence 선정: deductions.evidence_ref 우선 → fallback assigned_turns (greeting/단답 제외)
    deductions_9 = evaluation.get("deductions", [])
    turn_evidence = build_turn_evidence(assigned_turns, deductions_9)

    return {
        "evaluations": [
            {
                "status": "success",
                "agent_id": "mandatory-agent",
                "evaluation": {
                    "item_number": 9,
                    "item_name": "고객정보 확인",
                    "max_score": 5,
                    "score": score,
                    "deductions": deductions_9,
                    "evidence": turn_evidence if turn_evidence else evaluation.get("evidence", []),
                    "confidence": evaluation.get("confidence", 0.85),
                    "details": {
                        "info_items_checked": evaluation.get("info_items_checked", []),
                        "courtesy_used": evaluation.get("courtesy_used", courtesy_count > 0),
                        "customer_provided_first": evaluation.get(
                            "customer_provided_first", customer_provided_count > 0
                        ),
                        "assigned_turns": assigned_turns,
                    },
                },
            }
        ]
    }


# ---------------------------------------------------------------------------
# 통합 노드 진입점 (graph.py에서 호출)
# ---------------------------------------------------------------------------


async def mandatory_node(state: QAState, ctx: NodeContext) -> dict[str, Any]:
    """Run all mandatory-agent evaluations: items #8, #9.

    Calls two internal evaluation functions in parallel and merges results.
    Returns {"evaluations": [...]} — merged into state via operator.add.
    """
    # 니즈 파악 영역의 2개 평가 항목을 asyncio.gather로 병렬 실행
    # 각 평가 함수는 동기 함수이므로 asyncio.to_thread로 래핑하여 비동기 실행
    results = await asyncio.gather(
        evaluate_inquiry_identification(state),  # 항목 #8: 문의 파악 및 재확인(복창) (async)
        evaluate_customer_info_check(state),  # 항목 #9: 고객정보 확인 (async)
    )

    # 각 결과의 evaluations 리스트를 하나로 병합
    # state에 operator.add로 합쳐지므로 리스트 형태로 반환
    merged: list[dict] = []
    for result in results:
        merged.extend(result.get("evaluations", []))

    # --- Wiki 공유 메모리: intent_summary 작성 ---
    # 항목 #8 평가 결과에서 고객 의도 정보를 추출하여 하류 에이전트에 전달
    item_8_eval = None
    for ev in merged:
        evaluation = ev.get("evaluation", {})
        if evaluation.get("item_number") == 8:
            item_8_eval = evaluation
            break

    primary_intent = ""
    if item_8_eval:
        # LLM이 반환한 customer_need_identified에서 주요 의도를 추출
        details = item_8_eval.get("details", {})
        primary_intent = details.get("customer_need_identified", "")
        # details에 없으면 evaluation 직계에서 시도
        if not primary_intent:
            primary_intent = item_8_eval.get("customer_need_identified", "")

    # 턴 수 기반 복잡도 판단 (간단한 휴리스틱)
    transcript = ctx.transcript
    turn_count = len([line for line in transcript.strip().split("\n") if line.strip()])
    complexity = "complex" if turn_count > 20 else "simple"

    intent_summary = {
        "primary_intent": primary_intent or "미식별",
        "sub_intents": [],
        "product": ctx.consultation_type,
        "complexity": complexity,
    }

    deduction_log_entries = build_deduction_log_from_evaluations(merged, "mandatory-agent")

    return {
        "evaluations": merged,
        "intent_summary": intent_summary,
        "deduction_log": deduction_log_entries,
    }
