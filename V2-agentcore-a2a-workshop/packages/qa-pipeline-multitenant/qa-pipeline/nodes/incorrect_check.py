# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Incorrect Check node — 개인정보 보호 (#17: 정보 확인 절차 5점, #18: 정보 보호 준수 5점).

본인 확인 절차 이행 여부와 개인정보 유출 방지를 평가한다.
Scoring is entirely rule-based — no LLM calls.

Key checks:
- 개인정보 확인 시 본인 확인 절차 이행 여부
- 고객 정보 선언급(상담사가 먼저 고객 정보를 말함) 여부
- 제3자에게 정보 안내 또는 정보 유출 발생 여부

총 배점: 10점 (정보 확인 절차 5점 + 정보 보호 준수 5점)
"""

# ---------------------------------------------------------------------------
# [노드 개요]
# 이 노드는 QA 평가항목 #17 "정보 확인 절차"와 #18 "정보 보호 준수"를 평가한다.
# 상담사가 개인정보를 다룰 때 적절한 절차를 따랐는지, 정보 보호 가이드를
# 준수했는지를 검사한다.
#
# [평가 기준]
#
#   #17 정보 확인 절차 (최대 5점, 2단계 채점: 5/0)
#     5점: 개인정보 확인 시 본인 확인 절차를 가이드에 따라 정확히 이행
#     0점: 본인 확인 절차 누락 또는 고객 정보 선언급
#          (상담사가 먼저 고객의 개인정보를 말하는 행위)
#
#   #18 정보 보호 준수 (최대 5점, 2단계 채점: 5/0)
#     5점: 상담 중 개인정보 유출이 발생하지 않음 — 개인정보 보호 가이드 준수
#     0점: 제3자에게 정보 안내 또는 정보 유출 발생
#
# [동작 흐름]
#   1. 사전 분석(regex): 본인확인 절차, 정보 선언급, 제3자 정보 안내 패턴 탐지
#   2. 규칙 기반 채점: 사전 분석 결과로 직접 점수 산출 (LLM 호출 없음)
#   3. 항목별 점수 검증(5/0만 허용) → 평가 결과 반환
# ---------------------------------------------------------------------------

from __future__ import annotations

import asyncio
import logging
import re

# LLM path imports (bedrock backend only — lazy usage to preserve regex path)
from langchain_core.messages import HumanMessage, SystemMessage
from nodes.llm import LLMTimeoutError, get_chat_model, invoke_and_parse
from nodes.skills.constants import PRIVACY_VIOLATION_PATTERNS, THIRD_PARTY_DISCLOSURE_PATTERNS
from nodes.skills.error_results import build_llm_failure_result
from nodes.skills.node_context import NodeContext
from nodes.skills.pattern_matcher import PatternMatcher, detect_agent_patterns, parse_turns
from prompts import load_prompt
from state import QAState
from typing import Any


logger = logging.getLogger(__name__)

# Module-level PatternMatcher instance (stateless, safe to share)
_pm = PatternMatcher()


# ---------------------------------------------------------------------------
# 사전 분석 헬퍼 함수
# ---------------------------------------------------------------------------


# 본인 확인 목적의 정보 *요청* 은 유출 아님 — 실제 "유출 행위" 만 남김
_IDENTITY_REQUEST_MARKERS = (
    "어떻게 되실까요",
    "어떻게 되시나요",
    "어떻게 되세요",
    "여쭤봐도",
    "말씀해 주시",
    "말씀해주시",
    "알려주시겠",
    "확인 부탁",
    "본인 맞으",
    "본인 맞으실까요",
    "본인 맞으십니까",
)


def _is_identity_request(text: str) -> bool:
    """본인 확인 목적의 정보 요청 문장인지 판별 (유출 아님)."""
    if not text:
        return False
    return any(m in text for m in _IDENTITY_REQUEST_MARKERS)


def _detect_all_patterns(transcript: str, patterns: list[str]) -> list[dict]:
    """모든 발화(고객+상담사)에서 지정된 패턴 목록을 탐지한다."""
    findings: list[dict] = []
    lines = transcript.strip().split("\n")
    turn_number = 0
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        turn_number += 1
        for pattern in patterns:
            if re.search(pattern, line_stripped):
                findings.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                break
    return findings


# ---------------------------------------------------------------------------
# 규칙 기반 채점 함수
# ---------------------------------------------------------------------------


def _score_iv_procedure(pre_analysis: dict) -> tuple[int, list[dict]]:
    """Rule-based scoring for #17 정보 확인 절차 (5/0 binary).

    5점: iv_procedure patterns found AND no preemptive disclosure.
    0점: else (missing IV procedure OR preemptive disclosure detected).
    """
    iv_found = len(pre_analysis["iv_procedure"]) > 0
    preemptive_found = len(pre_analysis["preemptive"]) > 0

    if iv_found and not preemptive_found:
        return 5, []

    deductions = []
    if not iv_found:
        deductions.append({
            "reason": "본인 확인 절차 패턴 미감지",
            "points": 5,
            "evidence_ref": "",
        })
    if preemptive_found:
        first_p = pre_analysis["preemptive"][0]
        deductions.append({
            "reason": f"고객 정보 선언급 감지: {first_p['text'][:60]}",
            "points": 5,
            "evidence_ref": f"turn_{first_p['turn']}",
        })
    return 0, deductions


def _score_privacy_protection(pre_analysis: dict) -> tuple[int, list[dict]]:
    """Rule-based scoring for #18 정보 보호 준수 (5/0 binary).

    5점: no privacy violations AND no unverified third-party disclosure.
    0점: else.
    """
    privacy_violations = pre_analysis["privacy_violation"]
    third_party_ctx = pre_analysis["third_party_context"]
    third_party_disc = pre_analysis["third_party_disclosure"]

    has_violation = len(privacy_violations) > 0
    # 제3자 문맥이 있고 제3자에게 정보를 안내한 경우 — 위반
    has_unverified_third_party = len(third_party_ctx) > 0 and len(third_party_disc) > 0

    if not has_violation and not has_unverified_third_party:
        return 5, []

    deductions = []
    if has_violation:
        first_v = privacy_violations[0]
        deductions.append({
            "reason": f"개인정보 유출 위험 패턴 감지: {first_v['text'][:60]}",
            "points": 5,
            "evidence_ref": f"turn_{first_v['turn']}",
        })
    if has_unverified_third_party:
        first_t = third_party_disc[0]
        deductions.append({
            "reason": f"제3자 정보 안내 감지: {first_t['text'][:60]}",
            "points": 5,
            "evidence_ref": f"turn_{first_t['turn']}",
        })
    return 0, deductions


# ---------------------------------------------------------------------------
# LLM 기반 채점 함수 (backend="bedrock" 경로 전용 — Sonnet 자연어 판단)
# ---------------------------------------------------------------------------


async def _llm_evaluate_iv_procedure(
    transcript: str,
    assigned_turns: list[dict],
    backend: str | None,
    pre_analysis: dict,
    bedrock_model_id: str | None = None,
    tenant_id: str = "",
) -> dict:
    """Item #17 정보 확인 절차 — Sonnet 자연어 판단. 실패 시 regex 폴백."""
    try:
        system_prompt = load_prompt(
            "item_17_iv_procedure", tenant_id=tenant_id, include_preamble=True, backend=backend,
        )
        user_message = (
            f"## 전사록\n{transcript}\n\n"
            "#17 정보 확인 절차 (max 5pt) 평가를 해주세요. "
            "score ∈ {5, 3, 0} 중 하나로 판정하고 JSON 만 반환하세요."
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
        logger.warning("Item #17 LLM evaluation failed, fallback to rule: %s", e)
        score, deductions = _score_iv_procedure(pre_analysis)
        return {
            "item_number": 17,
            "item_name": "정보 확인 절차",
            "max_score": 5,
            "score": score,
            "deductions": deductions,
            "evidence": [],
            "confidence": 0.8,
            "summary": "LLM 실패 — 규칙 폴백",
        }
    return {
        "item_number": 17,
        "item_name": "정보 확인 절차",
        "max_score": 5,
        "score": int(result.get("score", 5)),
        "deductions": result.get("deductions", []),
        "evidence": result.get("evidence", []),
        "confidence": float(result.get("confidence", 0.85)),
        "summary": result.get("summary", ""),
    }


async def _llm_evaluate_privacy_protection(
    transcript: str,
    assigned_turns: list[dict],
    backend: str | None,
    pre_analysis: dict,
    bedrock_model_id: str | None = None,
    tenant_id: str = "",
) -> dict:
    """Item #18 정보 보호 준수 — Sonnet 자연어 판단. 실패 시 regex 폴백."""
    try:
        system_prompt = load_prompt(
            "item_18_privacy_protection", tenant_id=tenant_id, include_preamble=True, backend=backend,
        )
        user_message = (
            f"## 전사록\n{transcript}\n\n"
            "#18 정보 보호 준수 (max 5pt) 평가를 해주세요. "
            "score ∈ {5, 0} 중 하나로 판정하고 JSON 만 반환하세요. "
            "상담사의 '정보 요청/재확인' 발화는 유출이 아님에 유의."
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
        logger.warning("Item #18 LLM evaluation failed, fallback to rule: %s", e)
        score, deductions = _score_privacy_protection(pre_analysis)
        return {
            "item_number": 18,
            "item_name": "정보 보호 준수",
            "max_score": 5,
            "score": score,
            "deductions": deductions,
            "evidence": [],
            "confidence": 0.8,
            "summary": "LLM 실패 — 규칙 폴백",
        }
    return {
        "item_number": 18,
        "item_name": "정보 보호 준수",
        "max_score": 5,
        "score": int(result.get("score", 5)),
        "deductions": result.get("deductions", []),
        "evidence": result.get("evidence", []),
        "confidence": float(result.get("confidence", 0.85)),
        "summary": result.get("summary", ""),
    }


_LEAK_KEYWORDS = ("유출", "노출", "제3자", "제 3자", "선언급")
_PREEMPTIVE_KEYWORDS = ("선언급", "먼저 말", "preemptive")


def _flags_from_llm_evals(eval_17: dict, eval_18: dict, pre_analysis: dict) -> dict:
    """LLM eval 결과에서 downstream 용 flags dict 를 구성한다.

    - privacy_violation: #18 score == 0 AND 감점 사유에 유출/노출/제3자/선언급 키워드 포함
    - preemptive_disclosure: #17 or #18 감점 사유에 선언급 관련 키워드 포함
    """
    d18 = eval_18.get("deductions", []) or []
    d17 = eval_17.get("deductions", []) or []

    privacy_violation = False
    if int(eval_18.get("score", 5)) == 0:
        for d in d18:
            reason = str(d.get("reason", ""))
            if any(k in reason for k in _LEAK_KEYWORDS):
                privacy_violation = True
                break

    preemptive_disclosure = False
    for d in (d17 + d18):
        reason = str(d.get("reason", ""))
        if any(k in reason for k in _PREEMPTIVE_KEYWORDS):
            preemptive_disclosure = True
            break

    details: list[str] = []
    if privacy_violation:
        for ev in eval_18.get("evidence", []) or []:
            turn = ev.get("turn", "?")
            text = str(ev.get("text", ""))[:60]
            details.append(f"turn_{turn}: {text}")
    if preemptive_disclosure:
        for ev in eval_17.get("evidence", []) or []:
            turn = ev.get("turn", "?")
            text = str(ev.get("text", ""))[:60]
            details.append(f"turn_{turn}: 선언급 - {text}")

    return {
        "privacy_violation": privacy_violation,
        "preemptive_disclosure": preemptive_disclosure,
        "details": details,
    }


# ---------------------------------------------------------------------------
# 노드 함수 (LangGraph 노드 진입점)
# ---------------------------------------------------------------------------


async def incorrect_check_node(state: QAState, ctx: NodeContext) -> dict[str, Any]:
    """Evaluate privacy protection — identity verification procedure and data protection compliance.

    QA Item #17 — 정보 확인 절차, max 5 points (5/0, binary).
    QA Item #18 — 정보 보호 준수, max 5 points (5/0, binary).

    Returns {"evaluations": [...], "deduction_log": [...], "flags": {...}} for reducer merge.
    """
    del ctx  # NodeContext 슬롯 — assignment 우선 패턴 보존, 본문 직접 접근 없음
    # --- 선별 턴 할당 우선 사용, 폴백으로 전체 transcript ---
    assignment = state.get("agent_turn_assignments", {}).get("incorrect_check", {})
    assigned_text = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])
    transcript = assigned_text

    logger.info(
        f"incorrect_check_node: transcript_len={len(transcript)}, "
        f"assigned_turns={len(assigned_turns)}"
    )

    # 통화 내역이 없으면 평가 불가 — 에러 결과 반환
    if not transcript:
        return {
            "evaluations": [
                build_llm_failure_result("incorrect-check-agent", "No transcript provided for evaluation.")
            ]
        }

    # --- 1단계: regex 기반 사전 분석 ---
    # PatternMatcher.check_identity_verification covers iv_procedure,
    # preemptive disclosure, and third_party_context.  The remaining two
    # (third_party_disclosure, privacy_violation) are not in PatternMatcher
    # so we still detect them with the local helpers.
    if assigned_turns:
        # Normalise assigned_turns to the shape PatternMatcher expects
        # (keys: speaker, text, turn).
        pm_turns = [
            {"speaker": t.get("speaker", "unknown"), "text": t["text"], "turn": t["turn_id"]}
            for t in assigned_turns
        ]
        pm_iv = _pm.check_identity_verification(pm_turns)

        # Local helpers for patterns not covered by PatternMatcher
        agent_turns = [t for t in assigned_turns if t.get("speaker") == "agent"]

        def _match_agent(patterns: list[str]) -> list[dict]:
            findings = []
            for t in agent_turns:
                for pattern in patterns:
                    if re.search(pattern, t["text"]):
                        findings.append({"turn": t["turn_id"], "text": t["text"], "pattern": pattern})
                        break
            return findings

        pre_analysis = {
            "iv_procedure": pm_iv["iv_details"],
            "preemptive": pm_iv["preemptive_details"],
            "third_party_disclosure": _match_agent(THIRD_PARTY_DISCLOSURE_PATTERNS),
            "privacy_violation": _match_agent(PRIVACY_VIOLATION_PATTERNS),
            "third_party_context": pm_iv["third_party_details"],
        }
    else:
        # Transcript path: parse turns for PatternMatcher, keep helpers for the rest.
        parsed_turns = parse_turns(transcript)
        pm_iv = _pm.check_identity_verification(parsed_turns)

        pre_analysis = {
            "iv_procedure": pm_iv["iv_details"],
            "preemptive": pm_iv["preemptive_details"],
            "third_party_disclosure": detect_agent_patterns(transcript, THIRD_PARTY_DISCLOSURE_PATTERNS),
            "privacy_violation": detect_agent_patterns(transcript, PRIVACY_VIOLATION_PATTERNS),
            "third_party_context": pm_iv["third_party_details"],
        }

    # 본인 확인 요청 문장은 유출이 아니므로 필터링 (FP 방지)
    pre_analysis["privacy_violation"] = [
        pv for pv in pre_analysis["privacy_violation"] if not _is_identity_request(pv.get("text", ""))
    ]

    # --- 2단계: 채점 — SageMaker/Bedrock 모두 LLM 경로 통일 ---
    # 기존 규칙 기반 (_score_iv_procedure / _score_privacy_protection) 은
    # _legacy_sagemaker_pipeline/ 에 백업되어 있음. 현재 파일에도 fallback 용도로 유지.
    backend = state.get("llm_backend")
    bedrock_model_id = state.get("bedrock_model_id")
    tenant_id = (state.get("tenant") or {}).get("tenant_id", "")
    eval_17_llm, eval_18_llm = await asyncio.gather(
        _llm_evaluate_iv_procedure(
            transcript, assigned_turns, backend, pre_analysis,
            bedrock_model_id=bedrock_model_id, tenant_id=tenant_id,
        ),
        _llm_evaluate_privacy_protection(
            transcript, assigned_turns, backend, pre_analysis,
            bedrock_model_id=bedrock_model_id, tenant_id=tenant_id,
        ),
    )
    score_17 = eval_17_llm["score"]
    deductions_17 = eval_17_llm["deductions"]
    score_18 = eval_18_llm["score"]
    deductions_18 = eval_18_llm["deductions"]
    flags = _flags_from_llm_evals(eval_17_llm, eval_18_llm, pre_analysis)
    llm_evidence_17 = eval_17_llm.get("evidence", [])
    llm_evidence_18 = eval_18_llm.get("evidence", [])
    conf_17 = eval_17_llm.get("confidence", 0.9)
    conf_18 = eval_18_llm.get("confidence", 0.9)

    # 빈 evidence_ref 보강: 스캔 범위 턴 번호 + item suffix 삽입
    # FIX-005 격상: #17/#18 동일 scan_range 시 consistency_check duplicate FP 방지
    if assigned_turns:
        scan_range = f"turn_{assigned_turns[0]['turn_id']}_to_{assigned_turns[-1]['turn_id']}"
    else:
        scan_range = "intro"
    for d in deductions_17:
        if not d.get("evidence_ref"):
            d["evidence_ref"] = f"{scan_range}#17"
    for d in deductions_18:
        if not d.get("evidence_ref"):
            d["evidence_ref"] = f"{scan_range}#18"

    # assigned_turns가 있으면 evidence에 turn_id 포함
    if assigned_turns:
        turn_evidence = [{"turn": t["turn_id"], "speaker": t["speaker"], "text": t["text"]} for t in assigned_turns]
    else:
        turn_evidence = None

    # --- deduction_log 구성 ---
    deduction_log: list[dict[str, Any]] = []
    if score_17 < 5:
        for d in deductions_17:
            deduction_log.append({
                "agent_id": "incorrect_check",
                "item_number": 17,
                "reason": d["reason"],
                "points": d["points"],
                "turn_ref": d.get("evidence_ref", ""),
            })
    if score_18 < 5:
        for d in deductions_18:
            deduction_log.append({
                "agent_id": "incorrect_check",
                "item_number": 18,
                "reason": d["reason"],
                "points": d["points"],
                "turn_ref": d.get("evidence_ref", ""),
            })

    # 최종 평가 결과를 operator.add 리듀서 형태로 반환
    evidence_17 = llm_evidence_17 if llm_evidence_17 else (turn_evidence if turn_evidence else [])
    evidence_18 = llm_evidence_18 if llm_evidence_18 else (turn_evidence if turn_evidence else [])
    return {
        "evaluations": [
            {
                "status": "success",
                "agent_id": "incorrect-check-agent",
                "evaluation": {
                    "item_number": 17,
                    "item_name": "정보 확인 절차",
                    "max_score": 5,
                    "score": score_17,
                    "iv_procedure_performed": len(pre_analysis["iv_procedure"]) > 0,
                    "iv_items_asked": [
                        {"turn": iv["turn"], "text": iv["text"]} for iv in pre_analysis["iv_procedure"]
                    ],
                    "preemptive_disclosures": [
                        {"turn": p["turn"], "text": p["text"]} for p in pre_analysis["preemptive"]
                    ],
                    "deductions": deductions_17,
                    "evidence": evidence_17,
                    "confidence": conf_17,
                },
            },
            {
                "status": "success",
                "agent_id": "incorrect-check-agent",
                "evaluation": {
                    "item_number": 18,
                    "item_name": "정보 보호 준수",
                    "max_score": 5,
                    "score": score_18,
                    "third_party_context": len(pre_analysis["third_party_context"]) > 0,
                    "privacy_violations": [
                        {"turn": pv["turn"], "text": pv["text"]} for pv in pre_analysis["privacy_violation"]
                    ],
                    "authority_verified": True,
                    "deductions": deductions_18,
                    "evidence": evidence_18,
                    "confidence": conf_18,
                },
            },
        ],
        "deduction_log": deduction_log,
        "flags": flags,
    }
