# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# =============================================================================
# 일관성 검증 (Consistency Check) 노드 — LLM 기반 교차 검증
# =============================================================================
# 이 노드는 Phase C 의 마지막 에이전트로, 8개 평가 에이전트가 생성한 18개 항목
# 평가 결과를 원본 전사록 및 Wiki 공유 메모리와 대조하여 평가의 타당성·일관성을
# LLM (Qwen3-8B) 으로 직접 판단한다.
#
# [설계 철학 — 이 프로젝트의 현실]
# 규칙 기반 cross-check 는 거짓양성이 너무 많음:
#   - "courtesy 점수 높은데 work-accuracy 에서 감점 = 모순" → 실은 다른 관점을 보는 것
#   - "evidence 에 turn/text 없음 = 모순" → 에이전트 출력 품질 문제일 뿐
# 그래서 규칙은 사전 집계(pre-analysis)에만 쓰고, **LLM 이 전사록을 직접 읽고
# 최종 판단**한다. sLLM 이라 context 제약이 있으므로 요약된 평가 데이터 전달.
#
# [LLM 이 판단하는 것]
# 1. 증거-전사록 정합성 — 감점 사유가 실제 전사록에 부합하는지
# 2. 전사록 맥락 부합 — 고객 톤/감정이 점수에 반영됐는지
# 3. 놓친 이슈 — 규칙이 못 잡은 암묵적 문제 (우회적 불친절 등)
# 4. 내러티브 일관성 — 18개 항목이 하나의 이야기로 읽히는지
# 5. 공통 감점 재검토 — 불친절/개인정보/오안내 여부 최종 확정
#
# [규칙 기반 사전 집계 (LLM 에 참고용으로 전달)]
# - duplicate_deductions: Wiki deduction_log 에서 동일 턴 이중 감점
# - category_anomalies: Scorer 기반 카테고리 전체 0점 탐지
# - over_deduction: 총 감점률 > 50%
# - common_penalty_hints: 키워드 매칭으로 추정된 공통 감점 후보
#
# [Gate 판정]
# LLM 이 반환한 is_consistent 가 True (= critical_issues 비어있음) 이어야 Gate 통과.
# False 면 report_generator 스킵 → END (validation_failed).
#
# [LLM 실패 대비 fallback]
# LLM 호출 실패 시 규칙 기반 결과만으로 보수적 판정 (공통 감점이나 카테고리 0점
# 있으면 차단, 그 외는 통과).
# =============================================================================

"""
Consistency Check node — LLM-driven cross-validation.

Uses Qwen3-8B to holistically verify 18 evaluations against the transcript,
detecting issues that rule-based matching cannot catch (implicit rudeness,
narrative inconsistency, missed context). Rules serve as pre-analysis input
to the LLM, not as gate triggers themselves.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from langchain_core.messages import HumanMessage, SystemMessage
from nodes.llm import LLMTimeoutError, get_chat_model, invoke_and_parse
from nodes.skills.scorer import Scorer
from prompts import load_prompt
from state import QAState
from typing import Any


logger = logging.getLogger(__name__)

# 카테고리 집계용 Scorer (qa_rules 기반)
_scorer = Scorer()

# LLM 검증용 시스템 프롬프트 (prompts/consistency_check.md)
# 자체 LANGUAGE RULES 보유로 preamble opt-out
def _get_consistency_prompt(backend: str | None = None, tenant_id: str = "") -> str:
    # backend="bedrock" 이면 consistency_check.sonnet.md 우선, 없으면 .md 폴백.
    # tenant_id 는 Dev4 의 오버라이드 로더로 전달 — 테넌트 전용 프롬프트 우선 조회.
    return load_prompt("consistency_check", tenant_id=tenant_id, include_preamble=False, backend=backend)

# 프롬프트 페이로드 제한 (sLLM 컨텍스트 예산)
_TRANSCRIPT_MAX_CHARS = 3500       # 전사록 잘라내기 기준
_LOW_CONFIDENCE_THRESHOLD = 0.7    # 저신뢰도 플래그 기준
_OVER_DEDUCTION_RATIO = 0.5        # 감점률 > 50% 시 과다감점 사전 플래그

# 공통 감점 후보 키워드 (LLM 에 "힌트" 로만 전달, 판정은 LLM 이)
_RUDENESS_HINTS = ("욕설", "비하", "언쟁", "단선", "불친절", "폭언", "짜증", "한숨")
# 개인정보 관련 광의 키워드 (맥락 조건)
_PRIVACY_CONTEXT_HINTS = ("개인정보", "정보보호", "본인 확인", "본인확인")
# 실제 "유출" 로 판정 가능한 행위 키워드 (이 중 하나라도 있어야 함)
_PRIVACY_BREACH_HINTS = ("유출", "노출", "제3자", "제 3자", "무단", "타인에게", "외부에")
_UNCORRECTED_HINTS = ("오안내", "미정정", "잘못된 안내", "정정하지")
_CORRECTION_REVERSAL = (
    "정정", "바로잡", "바로 잡", "수정 안내", "재안내", "재 안내",
    "고쳐 안내", "번복", "정정함", "정정하여", "정정하며",
)


# ---------------------------------------------------------------------------
# 규칙 기반 사전 집계 (LLM 프롬프트에 context 로 투입)
# ---------------------------------------------------------------------------


def _detect_duplicate_deductions(deduction_log: list[dict]) -> list[dict]:
    """동일 (turn_ref, item_number) 조합에 2+ 에이전트가 감점 → 이중 감점 후보 플래그.

    FIX-005 격상: 같은 노드(incorrect_check)가 #17/#18 두 항목을 동일 scan_range 로
    감점하는 정당 케이스를 duplicate 에서 제외. 튜플 키로 item_number 까지 일치해야
    duplicate 로 판정한다.

    LLM 에 "이런 패턴이 있음" 정보만 전달하고, 실제 중복인지 정당한 복합 감점인지는
    LLM 이 전사록 맥락으로 판단하도록 한다.
    """
    if not deduction_log:
        return []

    def _hashable_turn_ref(tr: Any) -> Any:
        # LLM 이 list/dict 등 비정형 값을 반환할 수 있으므로 해시 가능한 형태로 정규화
        if isinstance(tr, list):
            return tuple(_hashable_turn_ref(x) for x in tr)
        if isinstance(tr, dict):
            return tuple(sorted((k, _hashable_turn_ref(v)) for k, v in tr.items()))
        return tr

    # (turn_ref, item_number) 튜플 기준 카운트
    keys = [
        (_hashable_turn_ref(d["turn_ref"]), d.get("item_number"))
        for d in deduction_log
        if d.get("turn_ref")
    ]
    duplicate_keys = {k for k, c in Counter(keys).items() if c > 1}

    result = []
    for dup_turn, dup_item in sorted(duplicate_keys, key=lambda x: (str(x[0]), x[1] or 0)):
        entries = [
            d for d in deduction_log
            if _hashable_turn_ref(d.get("turn_ref")) == dup_turn and d.get("item_number") == dup_item
        ]
        result.append(
            {
                "turn_ref": dup_turn,
                "item_number": dup_item,
                "agents": sorted({d.get("agent_id", "?") for d in entries}),
                "count": len(entries),
                "total_points": sum(d.get("points", 0) for d in entries),
                "reasons": [d.get("reason", "") for d in entries],
            }
        )
    return result


def _detect_category_anomalies(evaluations: list[dict]) -> list[dict]:
    """카테고리 전체 0점 탐지. Scorer 구조화 결과로 단순 집계."""
    scorer_report = _scorer.build_report(evaluations)
    return [
        {
            "category": c.category,
            "score": c.score,
            "max_score": c.max_score,
        }
        for c in scorer_report.categories
        if c.max_score > 0 and c.score == 0
    ]


def _analyze_evaluations_single_pass(
    evaluations: list[dict], flags: dict,
) -> tuple[int, int, float, dict, list[dict], list[dict]]:
    """Single-pass analysis over evaluations: totals, penalty hints, low-confidence, and LLM summary.

    Returns:
        (total_score, max_score, deduction_ratio,
         common_penalty_hints, low_confidence_items, eval_summary)
    """
    total = 0
    maximum = 0
    hints: dict[str, list] = {"rudeness": [], "privacy": [], "uncorrected_misinfo": []}
    low_conf: list[dict] = []
    summary: list[dict] = []

    for e in evaluations:
        ev = e.get("evaluation", {})
        agent_id = e.get("agent_id", "")
        score = ev.get("score", 0)
        max_s = ev.get("max_score", 0)
        confidence = ev.get("confidence", 0.85)
        item = ev.get("item_number", 0)

        total += score
        maximum += max_s

        if confidence < _LOW_CONFIDENCE_THRESHOLD:
            low_conf.append({"agent": agent_id, "item": item, "confidence": confidence})

        is_valid = e.get("status") != "error" and confidence != 0.0
        deductions = ev.get("deductions", [])
        for ded in deductions:
            if is_valid:
                reason = ded.get("reason", "").lower()
                if any(kw in reason for kw in _RUDENESS_HINTS):
                    hints["rudeness"].append({"agent": agent_id, "item": item, "reason": ded.get("reason", "")})
                has_privacy_context = any(kw in reason for kw in _PRIVACY_CONTEXT_HINTS)
                has_breach_action = any(kw in reason for kw in _PRIVACY_BREACH_HINTS)
                if has_privacy_context and has_breach_action:
                    hints["privacy"].append({"agent": agent_id, "item": item, "reason": ded.get("reason", "")})
                if agent_id == "work-accuracy-agent" and item in (15, 16):
                    if any(kw in reason for kw in _UNCORRECTED_HINTS):
                        if not any(rev in reason for rev in _CORRECTION_REVERSAL):
                            hints["uncorrected_misinfo"].append(
                                {"agent": agent_id, "item": item, "reason": ded.get("reason", "")}
                            )

        evidence_list = ev.get("evidence", []) or []
        summary.append({
            "agent": agent_id,
            "item": item,
            "name": ev.get("item_name", ""),
            "score": score,
            "max": max_s,
            "confidence": confidence,
            "deductions": [
                {
                    "reason": d.get("reason", ""),
                    "points": d.get("points", 0),
                    "turn_ref": d.get("evidence_ref") or d.get("turn_ref", ""),
                }
                for d in deductions
            ],
            "evidence_count": len(evidence_list),
            "evidence_samples": [
                {"turn": str(s.get("turn", "")), "text": (s.get("text", "") or "")[:150]}
                for s in evidence_list[:3]
                if isinstance(s, dict) and s.get("text")
            ],
        })

    if flags.get("privacy_violation"):
        flag_details = flags.get("details") if isinstance(flags.get("details"), list) else []
        detail_text = flags.get("privacy_violation_detail", "") or ""
        if flag_details or detail_text.strip():
            hints["privacy"].append(
                {"source": "flags", "detail": detail_text or (flag_details[0] if flag_details else "")}
            )

    ratio = ((maximum - total) / maximum) if maximum > 0 else 0.0
    summary.sort(key=lambda x: x["item"])
    return total, maximum, round(ratio, 3), hints, low_conf, summary


# ---------------------------------------------------------------------------
# LLM 호출 — 교차 검증 본체
# ---------------------------------------------------------------------------


async def _llm_verify(
    transcript: str,
    eval_summary: list[dict],
    intent_summary: dict,
    accuracy_verdict: dict,
    flags: dict,
    rule_preanalysis: dict,
    backend: str | None = None,
    bedrock_model_id: str | None = None,
    tenant_id: str = "",
) -> dict:
    """LLM 에게 전체 평가 결과를 교차 검증 요청.

    전사록은 토큰 절약을 위해 앞 `_TRANSCRIPT_MAX_CHARS` 까지만 전달.
    평가 결과는 필수 필드만 압축 전달.
    backend 파라미터로 Bedrock/SageMaker 토글 (기본은 env var).
    """
    _tenant_id = tenant_id  # 상위 스코프 바인딩 — 내부 _get_consistency_prompt 호출용
    transcript_excerpt = transcript[:_TRANSCRIPT_MAX_CHARS]
    if len(transcript) > _TRANSCRIPT_MAX_CHARS:
        transcript_excerpt += f"\n... (전체 {len(transcript)}자 중 {_TRANSCRIPT_MAX_CHARS}자 발췌)"

    user_message = (
        "## 원본 전사록 (발췌)\n"
        f"{transcript_excerpt}\n\n"
        "## 18개 평가 결과 요약\n"
        f"{json.dumps(eval_summary, ensure_ascii=False, indent=2)}\n\n"
        "## Wiki 공유 메모리\n"
        f"- intent_summary: {json.dumps(intent_summary, ensure_ascii=False)}\n"
        f"- accuracy_verdict: {json.dumps(accuracy_verdict, ensure_ascii=False)}\n"
        f"- flags: {json.dumps(flags, ensure_ascii=False)}\n\n"
        "## 규칙 기반 사전 집계 (참고용, 너가 재판정)\n"
        f"{json.dumps(rule_preanalysis, ensure_ascii=False, indent=2)}\n\n"
        "위 데이터를 검토하고 지시된 JSON 스키마로 교차 검증 결과를 반환하라."
    )

    llm = get_chat_model(
        temperature=0.1, max_tokens=3072, backend=backend, bedrock_model_id=bedrock_model_id,
    )
    return await invoke_and_parse(
        llm,
        [
            SystemMessage(content=_get_consistency_prompt(backend, tenant_id=_tenant_id)),
            HumanMessage(content=user_message),
        ],
    )


def _rule_based_issues(rule_preanalysis: dict) -> tuple[list[dict], list[dict]]:
    """규칙 기반 critical/soft 이슈 생성 — LLM 결과와 독립적으로 병합되어 최종 판정에 기여.

    규칙이 LLM 과 *병행* 판정하므로 LLM 이 못 본 이슈를 규칙이 포착 가능.
    (예: LLM 이 키워드를 놓쳐도 규칙이 불친절을 critical 로 직접 플래그)

    Returns:
        (critical_issues, soft_warnings) — 각 dict 에 source="rule" 마킹.
    """
    critical: list[dict] = []
    soft: list[dict] = []

    hints = rule_preanalysis.get("common_penalty_hints", {})
    if hints.get("rudeness"):
        critical.append(
            {
                "type": "rudeness",
                "source": "rule",
                "description": f"[규칙] 불친절 키워드 감지 {len(hints['rudeness'])}건",
                "affected_items": [h.get("item", 0) for h in hints["rudeness"]],
                "evidence": "키워드 매칭",
                "details": hints["rudeness"],
            }
        )
    if hints.get("privacy"):
        critical.append(
            {
                "type": "privacy_breach",
                "source": "rule",
                "description": f"[규칙] 개인정보 유출 키워드 감지 {len(hints['privacy'])}건",
                "affected_items": [h.get("item", 0) for h in hints["privacy"] if "item" in h],
                "evidence": "키워드 매칭 + flags",
                "details": hints["privacy"],
            }
        )
    if hints.get("uncorrected_misinfo"):
        critical.append(
            {
                "type": "uncorrected_misinfo",
                "source": "rule",
                "description": f"[규칙] 오안내 미정정 키워드 감지 {len(hints['uncorrected_misinfo'])}건",
                "affected_items": [h.get("item", 0) for h in hints["uncorrected_misinfo"]],
                "evidence": "키워드 매칭",
                "details": hints["uncorrected_misinfo"],
            }
        )
    for anom in rule_preanalysis.get("category_anomalies", []):
        critical.append(
            {
                "type": "category_zero",
                "source": "rule",
                "description": f"[규칙] '{anom['category']}' 카테고리 전체 0점",
                "affected_items": [],
                "evidence": f"{anom['score']}/{anom['max_score']}",
            }
        )

    dup_list = rule_preanalysis.get("duplicate_deductions", [])
    if dup_list:
        soft.append(
            {
                "type": "duplicate_deduction",
                "source": "rule",
                "description": f"[규칙] 동일 턴 이중 감점 {len(dup_list)}건 — LLM 정당성 판단 참조",
                "affected_items": [],
                "details": dup_list,
            }
        )

    if rule_preanalysis.get("over_deduction"):
        ratio = rule_preanalysis.get("deduction_ratio", 0)
        soft.append(
            {
                "type": "over_deduction",
                "source": "rule",
                "description": f"[규칙] 총 감점률 {ratio:.0%} > 50% — 과다 감점 의심",
                "affected_items": [],
            }
        )

    low_conf = rule_preanalysis.get("low_confidence_items", [])
    if low_conf:
        soft.append(
            {
                "type": "low_confidence",
                "source": "rule",
                "description": f"[규칙] 저신뢰도 항목 {len(low_conf)}건 (confidence < 70%)",
                "affected_items": [lc.get("item", 0) for lc in low_conf],
            }
        )

    return critical, soft


def _merge_issues(rule_issues: list[dict], llm_issues: list[dict]) -> list[dict]:
    """규칙+LLM 이슈 병합. 같은 `type` 은 중복 제거하되 LLM 버전 우선 (근거가 풍부함)."""
    merged: list[dict] = []
    seen_types: set[str] = set()

    # LLM 을 먼저 추가 (근거가 더 풍부, priority 높음)
    for item in llm_issues:
        if not isinstance(item, dict):
            continue
        item_with_source = {**item, "source": item.get("source", "llm")}
        merged.append(item_with_source)
        t = item.get("type")
        if t:
            seen_types.add(t)

    # 규칙 이슈 중 LLM 이 커버하지 않은 type 만 추가 (중복 제거)
    for item in rule_issues:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t and t in seen_types:
            # LLM 이 이미 같은 타입을 리포트했음 — 규칙 버전 생략 (LLM 이 더 상세)
            continue
        merged.append(item)
        if t:
            seen_types.add(t)

    return merged


def _fallback_verdict(rule_preanalysis: dict) -> dict:
    """LLM 호출 실패 시 규칙 기반 보수 판정 (LLM 결과가 비어있을 때 사용).

    `_rule_based_issues` 의 결과만으로 판정. LLM 영역(놓친 이슈, 내러티브 일관성 등)은 빈 값.
    """
    critical, soft = _rule_based_issues(rule_preanalysis)
    return {
        "is_consistent": len(critical) == 0,
        "needs_human_review": bool(critical or soft),
        "confidence": 0.5,
        "summary": "LLM 검증 실패 — 규칙 기반 보수 판정으로 대체",
        "critical_issues": critical,
        "soft_warnings": soft,
        "missed_issues": [],
        "score_adjustments": [],
        "fallback": True,
    }


# ---------------------------------------------------------------------------
# 메인 노드
# ---------------------------------------------------------------------------


async def consistency_check_node(state: QAState) -> dict[str, Any]:
    """LLM-driven cross-validation of 18 evaluations against the transcript.

    Returns state["verification"] containing LLM's judgment. The orchestrator
    uses the `is_consistent` field as the gate condition for report_generator.
    """
    raw_eval_list = state.get("evaluations", [])
    if not raw_eval_list:
        return {
            "verification": {
                "status": "error",
                "agent_id": "consistency-check-agent",
                "message": "No evaluations to verify.",
            }
        }

    # evaluation 포함 항목만
    eval_list = [e for e in raw_eval_list if "evaluation" in e]
    skipped_items = [e for e in raw_eval_list if "evaluation" not in e]
    errored_evals = [e for e in eval_list if e.get("evaluation", {}).get("error") is True]

    if skipped_items:
        logger.warning(f"{len(skipped_items)} item(s) without evaluation dict — skipping")
    if errored_evals:
        logger.warning(f"{len(errored_evals)} evaluation(s) had errors (score=0)")

    # Wiki 공유 메모리
    transcript = state.get("transcript", "")
    deduction_log = state.get("deduction_log", [])
    intent_summary = state.get("intent_summary", {})
    accuracy_verdict = state.get("accuracy_verdict", {})
    flags = state.get("flags", {})

    # ======================================================================
    # 1. 규칙 기반 사전 집계 (LLM 입력용 context) — single-pass
    # ======================================================================

    total_score, max_possible, deduction_ratio, penalty_hints, low_conf_items, eval_summary = (
        _analyze_evaluations_single_pass(eval_list, flags)
    )
    rule_preanalysis = {
        "total_score": total_score,
        "max_possible_score": max_possible,
        "score_percentage": round(
            (total_score / max_possible * 100) if max_possible > 0 else 0, 1
        ),
        "deduction_ratio": deduction_ratio,
        "over_deduction": deduction_ratio > _OVER_DEDUCTION_RATIO,
        "duplicate_deductions": _detect_duplicate_deductions(deduction_log),
        "category_anomalies": _detect_category_anomalies(eval_list),
        "common_penalty_hints": penalty_hints,
        "low_confidence_items": low_conf_items,
        "evaluation_errors": len(errored_evals),
        "evaluation_missing": len(skipped_items),
    }

    logger.info(
        "consistency_check: pre-analysis — score=%d/%d (%.1f%%), duplicates=%d, "
        "category_0=%d, low_conf=%d, rudeness_hints=%d, privacy_hints=%d",
        total_score, max_possible, rule_preanalysis["score_percentage"],
        len(rule_preanalysis["duplicate_deductions"]),
        len(rule_preanalysis["category_anomalies"]),
        len(rule_preanalysis["low_confidence_items"]),
        len(rule_preanalysis["common_penalty_hints"]["rudeness"]),
        len(rule_preanalysis["common_penalty_hints"]["privacy"]),
    )

    # ======================================================================
    # 2. LLM 호출 — 교차 검증
    # ======================================================================

    try:
        llm_verdict = await _llm_verify(
            transcript=transcript,
            eval_summary=eval_summary,
            intent_summary=intent_summary,
            accuracy_verdict=accuracy_verdict,
            flags=flags,
            rule_preanalysis=rule_preanalysis,
            backend=state.get("llm_backend"),
            bedrock_model_id=state.get("bedrock_model_id"),
            tenant_id=(state.get("tenant") or {}).get("tenant_id", ""),
        )
        logger.info(
            "consistency_check: LLM verdict — is_consistent=%s, confidence=%.2f, "
            "critical=%d, soft=%d, adjustments=%d",
            llm_verdict.get("is_consistent"),
            llm_verdict.get("confidence", 0),
            len(llm_verdict.get("critical_issues", [])),
            len(llm_verdict.get("soft_warnings", [])),
            len(llm_verdict.get("score_adjustments", [])),
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning(f"consistency_check: LLM verification failed — using fallback: {e}")
        llm_verdict = _fallback_verdict(rule_preanalysis)

    # ======================================================================
    # 3. 규칙 + LLM 이슈 병합 — 둘 다 독립 기여 (규칙이 놓친 건 LLM 이, LLM 이 놓친 건 규칙이)
    # ======================================================================

    llm_critical_raw = llm_verdict.get("critical_issues", []) or []
    llm_soft_raw = llm_verdict.get("soft_warnings", []) or []
    missed_issues = llm_verdict.get("missed_issues", []) or []
    score_adjustments = llm_verdict.get("score_adjustments", []) or []

    # LLM 이 fallback 모드면 이미 규칙 기반으로 생성된 critical/soft 사용 (중복 방지)
    if llm_verdict.get("fallback"):
        critical_issues = llm_critical_raw
        soft_warnings = llm_soft_raw
    else:
        # 정상 LLM 응답 — 규칙 기반 이슈와 병합
        rule_critical, rule_soft = _rule_based_issues(rule_preanalysis)
        critical_issues = _merge_issues(rule_critical, llm_critical_raw)
        soft_warnings = _merge_issues(rule_soft, llm_soft_raw)

    # ======================================================================
    # Gate 제거 — 탐지 결과를 그대로 반환. is_consistent 는 단순 보고용.
    # 문제 상세는 보고서의 별도 섹션에 기술된다.
    # ======================================================================

    is_consistent = len(critical_issues) == 0
    needs_human_review = bool(critical_issues or soft_warnings)

    logger.info(
        "consistency_check: merged verdict — is_consistent=%s, "
        "critical=%d (rule:%d + llm:%d), soft=%d (rule:%d + llm:%d)",
        is_consistent,
        len(critical_issues),
        sum(1 for c in critical_issues if c.get("source") == "rule"),
        sum(1 for c in critical_issues if c.get("source") == "llm"),
        len(soft_warnings),
        sum(1 for w in soft_warnings if w.get("source") == "rule"),
        sum(1 for w in soft_warnings if w.get("source") == "llm"),
    )

    # human_review_reasons — critical + soft 통합 (기존 소비자 호환)
    human_review_reasons = [c.get("description", "") for c in critical_issues if c.get("description")]
    human_review_reasons.extend(
        [w.get("description", "") for w in soft_warnings if w.get("description")]
    )

    # common_penalties — critical 에서 추출 (report_generator 가 참조)
    # rudeness_zero: 규칙 단독 1건(키워드 1회 매칭)은 소형 모델 false positive 가능성 높음.
    # LLM-only 1건 또한 소형 LLM(Gemma 3 4B, Llama 4 Maverick 17B) 오판 가능성 매우 높음.
    # confidence threshold 로 필터링.
    confidence = float(llm_verdict.get("confidence") or 0.0)
    rudeness_rule = [c for c in critical_issues if c.get("type") == "rudeness" and c.get("source") == "rule"]
    rudeness_llm = [c for c in critical_issues if c.get("type") == "rudeness" and c.get("source") == "llm"]
    rudeness_zero = (
        len(rudeness_rule) >= 2  # 규칙 2건+ (강증거)
        or (rudeness_rule and rudeness_llm and confidence >= 0.7)  # 규칙+LLM + 중신뢰
        or (len(rudeness_llm) >= 2 and confidence >= 0.85)  # LLM 2건+ + 고신뢰
    )
    if rudeness_rule and not rudeness_llm and len(rudeness_rule) < 2:
        logger.info(
            "rudeness_zero suppressed: rule-only %d hit(s) without LLM confirmation — treating as non-critical",
            len(rudeness_rule),
        )
    if rudeness_zero:
        logger.warning(
            "rudeness_zero activated: rule=%d, llm=%d, confidence=%.2f",
            len(rudeness_rule), len(rudeness_llm), confidence,
        )

    # uncorrected_misinfo 도 동일 패턴 — LLM-only 1건은 소형 모델 오판 가능성 높음.
    # 규칙 기반 또는 (LLM 2건+ + 고신뢰) 또는 (규칙+LLM + 중신뢰) 일 때만 penalty 적용.
    misinfo_rule = [c for c in critical_issues if c.get("type") == "uncorrected_misinfo" and c.get("source") == "rule"]
    misinfo_llm = [c for c in critical_issues if c.get("type") == "uncorrected_misinfo" and c.get("source") == "llm"]
    uncorrected_misinfo = (
        len(misinfo_rule) >= 1  # 규칙 1건+ (키워드 기반, 상대적 강증거)
        or (misinfo_rule and misinfo_llm and confidence >= 0.7)  # 규칙+LLM + 중신뢰
        or (len(misinfo_llm) >= 2 and confidence >= 0.85)  # LLM 2건+ + 고신뢰
    )
    if misinfo_llm and not misinfo_rule and len(misinfo_llm) < 2:
        logger.info(
            "uncorrected_misinfo suppressed: llm-only %d hit(s) without rule confirmation — treating as non-critical",
            len(misinfo_llm),
        )
    if uncorrected_misinfo:
        logger.warning(
            "uncorrected_misinfo activated: rule=%d, llm=%d, confidence=%.2f",
            len(misinfo_rule), len(misinfo_llm), confidence,
        )

    common_penalties = {
        "rudeness_zero": rudeness_zero,
        "privacy_breach": any(c.get("type") == "privacy_breach" for c in critical_issues)
            or bool(flags.get("privacy_violation")),
        "uncorrected_misinfo": uncorrected_misinfo,
        "details": [c.get("description", "") for c in critical_issues
                    if c.get("type") in ("rudeness", "privacy_breach", "uncorrected_misinfo")],
    }

    summary_text = llm_verdict.get("summary", "") or (
        f"Gate {'통과' if is_consistent else '차단'} — "
        f"critical {len(critical_issues)}건, soft {len(soft_warnings)}건"
    )

    return {
        "verification": {
            "status": "success",
            "agent_id": "consistency-check-agent",
            "tenant_id": (state.get("tenant") or {}).get("tenant_id", ""),
            "verification": {
                # ---- Gate 판정 ----
                "is_consistent": is_consistent,
                "needs_human_review": needs_human_review,
                "confidence": llm_verdict.get("confidence", 0.5),

                # ---- LLM 판정 결과 ----
                "critical_issues": critical_issues,
                "soft_warnings": soft_warnings,
                "missed_issues": missed_issues,
                "score_adjustments": score_adjustments,

                # ---- 사람 검토 사유 (기존 호환) ----
                "human_review_reasons": human_review_reasons,

                # ---- 공통 감점 결과 (report_generator 가 참조) ----
                "common_penalties": common_penalties,

                # ---- 규칙 기반 사전 집계 (디버깅/투명성) ----
                "rule_preanalysis": rule_preanalysis,

                # ---- 집계 ----
                "total_score": total_score,
                "max_possible_score": max_possible,
                "score_percentage": rule_preanalysis["score_percentage"],

                # ---- 요약 ----
                "details": summary_text,
                "llm_fallback": llm_verdict.get("fallback", False),

                # ---- 프론트/report_generator 소비자 호환 ----
                "conflicts": [
                    {
                        "agents": c.get("affected_items", []),
                        "description": c.get("description", ""),
                        "type": c.get("type", "critical"),
                    }
                    for c in critical_issues
                ],
                "evidence_check": {
                    "verified": sum(
                        1 for e in eval_summary if e["evidence_count"] > 0
                    ),
                    "missing": sum(1 for e in eval_summary if e["evidence_count"] == 0),
                },
            },
        }
    }
