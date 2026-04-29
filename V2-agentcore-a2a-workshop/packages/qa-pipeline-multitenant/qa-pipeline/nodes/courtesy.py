# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Courtesy evaluation nodes for the QA LangGraph pipeline.

Provides two evaluation functions and a combined ``courtesy_node``
entry point used by the graph:

- evaluate_polite_expression(state)  — QA Item #6 (정중한 표현, 5pt) — rule-based, no LLM
- evaluate_cushion_words(state)      — QA Item #7 (쿠션어 활용, 5pt) — LLM-based

Pre-analysis logic detects inappropriate language patterns, profanity,
and cushion word usage from the transcript text.
"""

# =============================================================================
# 언어 표현 평가 노드 (courtesy.py)
# =============================================================================
# 이 모듈은 QA 평가 파이프라인에서 "언어 표현" 영역을 담당합니다.
# 총 2개의 QA 평가 항목을 처리합니다 (총 10점):
#
#   항목 #6: 정중한 표현 (최대 5점) — 규칙 기반
#     - 5점: 비속어, 반말, 명령조, 혼잣말, 습관어 등 부적절한 표현 없이 진행
#     - 3점: 부적절한 표현(일상어, 반토막말, 사물존칭, 내부용어 등) 1~2회 사용
#     - 0점: 비속어/한숨 감지 또는 부적절한 표현 다수 사용
#
#   항목 #7: 쿠션어 활용 (최대 5점) — LLM 기반 (적절성 판단 필요)
#     - 5점: 불가/거절/양해 상황에서 쿠션어를 적절히 활용
#     - 3점: 쿠션어 사용이 형식적이거나 일부 누락
#     - 0점: 쿠션어 없이 통보식으로 안내
#     - ※ 거절/불가/양해 상황이 없는 경우 만점 처리
# =============================================================================

from __future__ import annotations

import asyncio
import logging
from langchain_core.messages import HumanMessage, SystemMessage
from nodes.llm import LLMTimeoutError, get_chat_model, invoke_and_parse
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

# Max transcript length (chars) fed to LLM for #7 쿠션어 평가.
# Qwen3-8B vLLM container has a 60s hard timeout, so large prompts cause repeated timeouts.
# Head-only truncate is safe: cushion word evidence is already pre-extracted via PatternMatcher
# and passed separately in the prompt.
MAX_TRANSCRIPT_FOR_CUSHION = 2500


# ---------------------------------------------------------------------------
# 사전 분석(Pre-analysis) 헬퍼 함수
# ---------------------------------------------------------------------------


def _detect_inappropriate_language(transcript: str) -> dict[str, list[dict]]:
    """Detect inappropriate language, profanity, and sighs in agent speech.

    Delegates to PatternMatcher.detect_inappropriate().
    """
    pm_result = _pm.detect_inappropriate(transcript)
    return {
        "language": pm_result["language"],
        "profanity": pm_result["profanity"],
        "sighs": pm_result["sighs"],
        "mild": pm_result["mild"],
    }


def _detect_cushion_and_refusals(transcript: str) -> tuple[list[dict], list[dict]]:
    """Detect cushion word usage and refusal/rejection situations.

    Single pass via PatternMatcher.detect_cushion_words() — returns both lists.
    """
    pm_result = _pm.detect_cushion_words(transcript)
    return pm_result["patterns_found"], pm_result["refusal_situations"]


# ---------------------------------------------------------------------------
# 규칙 기반 채점 함수 (#6)
# ---------------------------------------------------------------------------


def _score_polite_expression(inappropriate: dict[str, list[dict]]) -> tuple[int, list[dict]]:
    """Rule-based scoring for #6 정중한 표현.

    평가 기준표(5/3/0) 정합:
      - 5점: 부적절한 표현 없음 (total == 0)
      - 3점: 부적절한 표현 1~2회 (profanity/sigh 포함 합계가 1~2)
      - 0점: 부적절한 표현 다수(3회 이상) — "반복/다수" 기준

    비속어(profanity) / 한숨(sigh) 은 severity 가 높으나, 기준표에 따라
    "1회" 만으로 0점 처리하지 않는다 (과감점 방지 — 기준표는 "다수 사용" 시 0점).
    단, 비속어 + 기타 부적절 표현이 섞여 총합이 3회 이상이면 0점.
    """
    profanity_count = len(inappropriate["profanity"])
    sigh_count = len(inappropriate["sighs"])
    language_count = len(inappropriate["language"])
    mild_count = len(inappropriate["mild"])
    total_inappropriate = language_count + profanity_count + sigh_count + mild_count
    all_findings = (
        inappropriate["profanity"]
        + inappropriate["sighs"]
        + inappropriate["language"]
        + inappropriate["mild"]
    )

    # 0회 → 만점
    if total_inappropriate == 0:
        return 5, []

    # 1~2회 → 3점 (기준표: 부적절한 표현 1~2회 사용)
    if total_inappropriate <= 2:
        first = all_findings[0]
        # 감점 사유: 가장 severity 높은 항목 우선 노출 (profanity > sigh > language/mild)
        if profanity_count > 0:
            p = inappropriate["profanity"][0]
            reason = f"비속어/짜증 표현 {total_inappropriate}회 감지: {p['text'][:60]}"
            ref = f"turn_{p['turn']}"
        elif sigh_count > 0:
            s = inappropriate["sighs"][0]
            reason = f"한숨/짜증 표현 {total_inappropriate}회 감지: {s['text'][:60]}"
            ref = f"turn_{s['turn']}"
        else:
            reason = f"부적절 표현 {total_inappropriate}회 감지: {first['text'][:60]}"
            ref = f"turn_{first['turn']}"
        return 3, [{
            "reason": reason,
            "points": 2,
            "evidence_ref": ref,
        }]

    # 3회 이상 → 0점 (기준표: 부적절한 표현 다수 사용)
    first = all_findings[0]
    return 0, [{
        "reason": (
            f"부적절 표현 다수({total_inappropriate}회) 감지 "
            f"(비속어 {profanity_count}/한숨 {sigh_count}/"
            f"기타 {language_count + mild_count})"
        ),
        "points": 5,
        "evidence_ref": f"turn_{first['turn']}",
    }]


# ---------------------------------------------------------------------------
# LLM 시스템 프롬프트 (항목 #7만 LLM 사용)
# ---------------------------------------------------------------------------


def _get_cushion_words_system_prompt(backend: str | None = None, tenant_id: str = "") -> str:
    from prompts import load_prompt

    return load_prompt("item_07_cushion", tenant_id=tenant_id, backend=backend)


# ---------------------------------------------------------------------------
# 노드 평가 함수
# ---------------------------------------------------------------------------


def evaluate_polite_expression(state: QAState) -> dict[str, Any]:
    """Evaluate polite expression quality (rule-based, no LLM).

    QA Item #6 -- 정중한 표현, max 5 points.
    Scoring: 5/3/0.

    Returns {"evaluations": [result]} for operator.add merge.
    """
    assignment = state.get("agent_turn_assignments", {}).get("courtesy", {})
    transcript = assignment.get("text") or state.get("transcript", "")

    logger.info(f"evaluate_polite_expression: transcript_len={len(transcript)}")

    if not transcript:
        return {
            "evaluations": [
                build_llm_failure_result("courtesy-agent", "No transcript provided for evaluation.")
            ]
        }

    inappropriate = _detect_inappropriate_language(transcript)
    score, deductions = _score_polite_expression(inappropriate)

    language_count = len(inappropriate["language"])
    profanity_count = len(inappropriate["profanity"])
    sigh_count = len(inappropriate["sighs"])
    mild_count = len(inappropriate["mild"])

    logger.info(
        f"Polite expression: language={language_count}, profanity={profanity_count}, "
        f"sighs={sigh_count}, mild={mild_count} → score={score}"
    )

    return {
        "evaluations": [
            {
                "status": "success",
                "agent_id": "courtesy-agent",
                "evaluation": {
                    "item_number": 6,
                    "item_name": "정중한 표현",
                    "max_score": 5,
                    "score": score,
                    "deductions": deductions,
                    "evidence": (
                        [{"turn": f["turn"], "text": f["text"]} for f in inappropriate["profanity"]]
                        + [{"turn": f["turn"], "text": f["text"]} for f in inappropriate["sighs"]]
                        + [{"turn": f["turn"], "text": f["text"]} for f in inappropriate["language"]]
                        + [{"turn": f["turn"], "text": f["text"]} for f in inappropriate["mild"]]
                    ),
                    "confidence": 0.95,
                    "details": {
                        "profanity_detected": profanity_count > 0,
                        "sighs_detected": sigh_count,
                        "inappropriate_count": language_count,
                        "mild_count": mild_count,
                    },
                },
            }
        ]
    }


async def _llm_evaluate_polite_expression(state: QAState) -> dict[str, Any]:
    """Evaluate polite expression via LLM (Sonnet only). Fallback to rule on failure.

    QA Item #6 -- 정중한 표현, max 5 points. 같은 envelope shape 을 반환한다.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from prompts import load_prompt
    assignment = state.get("agent_turn_assignments", {}).get("courtesy", {})
    transcript = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])
    backend = state.get("llm_backend")
    bedrock_model_id = state.get("bedrock_model_id")

    if not transcript:
        return {
            "evaluations": [
                build_llm_failure_result("courtesy-agent", "No transcript provided for evaluation.")
            ]
        }

    agent_turns_text = "\n".join(
        f"turn_{t['turn_id']} 상담사: {t['text']}"
        for t in assigned_turns
        if t.get("speaker") == "agent"
    )
    if not agent_turns_text:
        agent_turns_text = transcript
    _tenant_id = (state.get("tenant") or {}).get("tenant_id", "")
    system_prompt = load_prompt(
        "item_06_polite_expression", tenant_id=_tenant_id, include_preamble=True, backend=backend,
    )
    user_message = f"## 상담사 발화\n{agent_turns_text}\n\n#6 정중한 표현 평가를 해주세요."
    llm = get_chat_model(
        temperature=0.1, max_tokens=1024, backend=backend, bedrock_model_id=bedrock_model_id,
    )
    try:
        result = await invoke_and_parse(
            llm, [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #6 LLM failed, fallback to rule: %s", e)
        return evaluate_polite_expression(state)

    score = snap_score(6, result.get("score", 5))
    rec = reconcile(
        item_number=6, score=score, max_score=5,
        deductions=result.get("deductions", []),
    )
    deductions = rec.deductions if rec.note else result.get("deductions", [])
    score = rec.score if rec.note else score

    return {
        "evaluations": [
            {
                "status": "success",
                "agent_id": "courtesy-agent",
                "evaluation": {
                    "item_number": 6,
                    "item_name": "정중한 표현",
                    "max_score": 5,
                    "score": int(score),
                    "deductions": deductions,
                    "evidence": result.get("evidence", []),
                    "confidence": float(result.get("confidence", 0.85)),
                    "summary": result.get("summary", ""),
                },
            }
        ]
    }


async def evaluate_cushion_words(state: QAState) -> dict[str, Any]:
    """Evaluate cushion word usage quality (LLM-based).

    QA Item #7 -- 쿠션어 활용, max 5 points.
    Scoring: 5/3/0.

    Returns {"evaluations": [result]} for operator.add merge.
    """
    assignment = state.get("agent_turn_assignments", {}).get("courtesy", {})
    transcript = assignment.get("text") or state.get("transcript", "")
    assigned_turns = assignment.get("turns", [])
    consultation_type = state.get("consultation_type", "general")

    logger.info(f"evaluate_cushion_words: type='{consultation_type}', transcript_len={len(transcript)}")

    if not transcript:
        return {
            "evaluations": [
                build_llm_failure_result("courtesy-agent", "No transcript provided for evaluation.")
            ]
        }

    cushion_findings, refusal_findings = _detect_cushion_and_refusals(transcript)
    cushion_count = len(cushion_findings)
    refusal_count = len(refusal_findings)

    logger.info(f"Pre-analysis: cushion_words={cushion_count}, refusal_situations={refusal_count}")

    # LLM 프롬프트 구성 — 긴 transcript 는 Qwen3-8B vLLM 60s container timeout 을 유발하므로
    # MAX_TRANSCRIPT_FOR_CUSHION 초과 시 앞부분만 사용 (head-only truncate). 쿠션어 근거는
    # PatternMatcher 사전 분석에서 별도 추출하여 프롬프트에 포함되므로, truncate 되어도 LLM 은
    # 해당 근거를 활용할 수 있다.
    if assigned_turns:
        lines: list[str] = []
        acc_len = 0
        truncated = False
        for t in assigned_turns:
            line = f"[Turn {t['turn_id']}] {t['text']}"
            if acc_len + len(line) + 1 > MAX_TRANSCRIPT_FOR_CUSHION and lines:
                truncated = True
                break
            lines.append(line)
            acc_len += len(line) + 1  # +1 for newline
        numbered_text = "\n".join(lines)
        if truncated:
            numbered_text += "\n... (이후 turns 생략 — 길이 제한)"
            logger.info(
                f"evaluate_cushion_words: truncated {len(assigned_turns)} turns to "
                f"{len(lines)} turns ({acc_len} chars, limit={MAX_TRANSCRIPT_FOR_CUSHION})"
            )
        transcript_for_llm = numbered_text
    else:
        if len(transcript) > MAX_TRANSCRIPT_FOR_CUSHION:
            transcript_for_llm = transcript[:MAX_TRANSCRIPT_FOR_CUSHION] + "\n... (이후 생략 — 길이 제한)"
            logger.info(
                f"evaluate_cushion_words: truncated transcript {len(transcript)} → "
                f"{MAX_TRANSCRIPT_FOR_CUSHION} chars"
            )
        else:
            transcript_for_llm = transcript
    user_message = f"## Consultation Type\n{consultation_type}\n\n"
    user_message += f"## Transcript\n{transcript_for_llm}\n\n"
    user_message += "## Pre-Analysis Results\n"
    user_message += f"- Cushion words detected (쿠션어 사용 횟수): {cushion_count}\n"
    if cushion_findings:
        user_message += "- Cushion word instances:\n"
        for c in cushion_findings:
            user_message += f"  - Turn {c['turn']}: {c['text']}\n"
    user_message += f"- Refusal/rejection situations detected (거절/불가 상황): {refusal_count}\n"
    if refusal_findings:
        user_message += "- Refusal situation instances:\n"
        for r in refusal_findings:
            user_message += f"  - Turn {r['turn']}: {r['text']}\n"
    else:
        user_message += "- No refusal/rejection situations detected — consider auto full score.\n"
    user_message += "\n"
    user_message += (
        "## Instructions\n"
        "Evaluate for 쿠션어 활용 (QA Item #7). Consider pre-analysis above.\n"
        "IMPORTANT: 거절/불가/양해 상황이 없는 경우 → 만점(5점) 처리.\n"
        "- 적절한 쿠션어 활용 → 5점\n"
        "- 형식적/일부 누락 → 3점\n"
        "- 통보식(쿠션어 없음) → 0점\n\n"
        "Evaluate and return JSON per the system prompt format."
    )

    backend = state.get("llm_backend")
    bedrock_model_id = state.get("bedrock_model_id")
    # max_tokens 1024: 쿠션어 평가 JSON 응답은 1024 이면 충분하며, 2048 은 Qwen3-8B vLLM
    # container 60s hard timeout 을 자주 초과시킴.
    llm = get_chat_model(max_tokens=1024, backend=backend, bedrock_model_id=bedrock_model_id)
    try:
        evaluation = await invoke_and_parse(
            llm,
            [
                SystemMessage(
                    content=_get_cushion_words_system_prompt(
                        backend, tenant_id=(state.get("tenant") or {}).get("tenant_id", ""),
                    ),
                ),
                HumanMessage(content=user_message),
            ],
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.error(f"Cushion words evaluation error: {e}", exc_info=True)
        fallback_score = 5 if refusal_count == 0 else 0
        return {
            "evaluations": [
                {
                    "status": "error",
                    "agent_id": "courtesy-agent",
                    "error_type": type(e).__name__,
                    "message": f"Evaluation failed: {e}",
                    "evaluation": {
                        "item_number": 7,
                        "item_name": "쿠션어 활용",
                        "max_score": 5,
                        "score": fallback_score,
                        "deductions": [],
                        "evidence": [],
                        "confidence": 0.0,
                        "details": {"cushion_count": cushion_count, "refusal_count": refusal_count},
                    },
                }
            ]
        }

    score = snap_score(7, evaluation.get("score", 0))

    # score × deductions 산술 보정 (LLM hallucination 방어)
    rec = reconcile(
        item_number=7, score=score, max_score=5,
        deductions=evaluation.get("deductions", []),
    )
    if rec.note:
        evaluation["deductions"] = rec.deductions
        score = rec.score

    return {
        "evaluations": [
            {
                "status": "success",
                "agent_id": "courtesy-agent",
                "evaluation": {
                    "item_number": 7,
                    "item_name": "쿠션어 활용",
                    "max_score": 5,
                    "score": score,
                    "deductions": evaluation.get("deductions", []),
                    "evidence": evaluation.get("evidence", []),
                    "confidence": evaluation.get("confidence", 0.85),
                    "details": {
                        "refusal_situation_detected": evaluation.get("refusal_situation_detected", refusal_count > 0),
                        "cushion_words_found": evaluation.get("cushion_words_found", []),
                        "cushion_usage_appropriate": evaluation.get("cushion_usage_appropriate", False),
                    },
                },
            }
        ]
    }


# ---------------------------------------------------------------------------
# 통합 노드 진입점 (graph.py에서 호출)
# ---------------------------------------------------------------------------


async def courtesy_node(state: QAState, ctx: NodeContext) -> dict[str, Any]:
    """Run all courtesy-agent evaluations: items #6, #7.

    Calls the two individual evaluator node functions in parallel and merges results.
    Returns {"evaluations": [...], "deduction_log": [...]} -- merged into state via operator.add.
    """
    del ctx  # NodeContext 슬롯 — 내부 evaluate_* 함수가 state.get 직접 사용 (assignment 우선 패턴 보존)
    # SageMaker/Bedrock 모두 LLM 경로 통일. 기존 rule-based `evaluate_polite_expression` 는
    # _legacy_sagemaker_pipeline/ 에 백업되어 있고, fallback 으로만 유지.
    polite_coro = _llm_evaluate_polite_expression(state)
    # 순차 실행: 병렬 gather 는 세마포어 3개 중 2개를 동시에 점유하여 Qwen3-8B vLLM 에서
    # 전체 병목 + container timeout 의 원인이 되므로, #6 → #7 순서로 직렬화한다.
    polite_result = await polite_coro  # 항목 #6: 정중한 표현 — LLM
    cushion_result = await evaluate_cushion_words(state)  # 항목 #7: 쿠션어 활용 — LLM (async)
    results = [polite_result, cushion_result]

    merged: list[dict] = []
    for result in results:
        merged.extend(result.get("evaluations", []))

    deduction_log = build_deduction_log_from_evaluations(
        merged, "courtesy", with_empty_fallback=True
    )
    return {"evaluations": merged, "deduction_log": deduction_log}
