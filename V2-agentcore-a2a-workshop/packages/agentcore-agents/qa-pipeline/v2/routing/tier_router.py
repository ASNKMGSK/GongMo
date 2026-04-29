# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Tier 라우터 — 설계서 §8.2, §10.1 (Policy / Uncertainty 분리).

입력: Layer 4 Confidence 계산 결과 + Layer 1 deduction_triggers + Orchestrator grade.
출력: RoutingBlock (qa_output_v2.RoutingBlock 호환 dict).

Tier 결정 규칙 (우선순위 내림차순):
 1. Policy-driven T3 강제 조건 (설계서 §10.1 policy 표):
    - 감점 트리거 (rudeness / privacy_leak / incorrect_uncorrected)
    - STT 품질 저하 (Layer 1 quality.passed == False)
    - VIP / 민원 플래그 (tenant 메타)
    - 개인정보 3개 항목 (#9, #17, #18) 중 evaluable 한 항목 존재 → FORCE_T3_ITEMS
    - #15 정확한 안내 가 partial_with_review 모드
    - AI self-report "판단 불가" (evaluation_mode=unevaluable 항목 존재)

 2. Uncertainty-driven T3:
    - 항목 confidence ≤ 2 & 개인정보/고배점 항목

 3. Policy-driven T2 강제 조건:
    - 총점 경계 ±3 (설계서 §8.3)
    - 신입 상담사 (tenant 플래그)

 4. Uncertainty-driven T2:
    - 항목 중 하나 이상 confidence ≤ 2, 또는 신호 간 불일치

 5. T1: 무작위 5~10% 샘플링 (설계서 §8.2 — 별도 샘플러 호출, 여기서는 결정 안함)

 6. T0: 위 조건 모두 아님.
"""

from __future__ import annotations

import logging
from typing import Any

from v2.routing.tenant_policy import load_tenant_policy
from v2.schemas.enums import (
    FORCE_T3_ITEMS,
    GRADE_BOUNDARIES,
    GRADE_BOUNDARY_MARGIN,
)

logger = logging.getLogger(__name__)


# Uncertainty T3 대상 항목 (confidence ≤ 2 시 즉시 T3) — 고배점/개인정보
_HIGH_STAKES_ITEMS: frozenset[int] = frozenset({15, 10} | FORCE_T3_ITEMS)

# Tier 별 예상 검수 시간 (분) — 검수자 UI `estimated_review_time_min` 필드
_TIER_REVIEW_TIME_MIN: dict[str, int] = {
    "T0": 0,
    "T1": 2,
    "T2": 4,
    "T3": 10,
}


def _near_grade_boundary(total_score: int, margin: int | None = None) -> bool:
    """경계 ±margin 이내인지 판정. 설계서 §8.3.

    `margin=None` 이면 호출자가 tenant_config 에서 로드한 값 주입 책임.
    하위 호환을 위해 None 일 때 GRADE_BOUNDARY_MARGIN 로 폴백.
    """
    if margin is None:
        margin = GRADE_BOUNDARY_MARGIN
    for _grade, threshold in GRADE_BOUNDARIES:
        if abs(total_score - threshold) <= margin:
            return True
    return False


def _priority_flag(
    code: str,
    description: str,
    severity: str,
    item_numbers: list[int] | None = None,
) -> dict[str, Any]:
    """priority_flag dict 생성 헬퍼."""
    return {
        "code": code,
        "description": description,
        "severity": severity,
        "item_numbers": item_numbers or [],
    }


def decide_tier(
    *,
    confidence_results: dict[int, dict[str, Any]],
    evaluations: list[dict[str, Any]],
    preprocessing: dict[str, Any],
    final_score: dict[str, Any],
    tenant_flags: dict[str, Any] | None = None,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    """Tier 결정 진입점.

    Parameters
    ----------
    confidence_results : {item_number: ConfidenceBlock dict} — calculator.compute_item_confidence 출력.
    evaluations        : QAStateV2.evaluations (각 원소는 ItemVerdict 또는 V1 EvaluationResult).
    preprocessing      : QAStateV2.preprocessing (quality / deduction_triggers 포함).
    final_score        : {"raw_total": int, "after_overrides": int, "grade": str}.
    tenant_flags       : {"is_vip": bool, "is_complaint": bool, "is_rookie": bool, ...} — Optional.
    tenant_id          : tenant_config 로드 키. 기본 "generic". PL Q5 외부화 대응.

    Returns
    -------
    RoutingBlock 호환 dict:
      {decision, hitl_driver, priority_flags, estimated_review_time_min, tier_reasons}
    """
    tenant_flags = tenant_flags or {}
    policy = load_tenant_policy(tenant_id).routing
    reasons: list[str] = []
    priority_flags: list[dict[str, Any]] = []
    hitl_driver: str | None = None
    decision = "T0"  # 기본값

    # -----------------------------------------------------------------
    # 1) Policy-driven T3
    # -----------------------------------------------------------------

    # 1-a) Layer 1 감점 트리거
    deduction_triggers = preprocessing.get("deduction_triggers", {}) or {}
    # dict or PreprocessingBlock.deduction_triggers 둘 다 허용 — 키가 영문/한글 혼재 가능
    def _trig(*keys: str) -> bool:
        return any(bool(deduction_triggers.get(k)) for k in keys)

    # PL 확정 canonical 키 (Dev1 DEDUCTION_TRIGGER_KEYS): {불친절, 개인정보_유출, 오안내_미정정}
    # Python-friendly alias 도 함께 허용 (하위 호환).
    if _trig("불친절", "rudeness"):
        decision = "T3"
        hitl_driver = "policy_driven"
        reasons.append("deduction_trigger:rudeness")
        priority_flags.append(_priority_flag(
            code="rudeness_detected",
            description="불친절(욕설·비하·단선) 감점 트리거 탐지 — 전체 재평가",
            severity="critical",
        ))
    if _trig("개인정보_유출", "privacy_leak"):
        decision = "T3"
        hitl_driver = "policy_driven"
        reasons.append("deduction_trigger:privacy_leak")
        priority_flags.append(_priority_flag(
            code="privacy_leak_detected",
            description="개인정보 유출 감점 트리거 탐지 — 전체 인간 검수 필수",
            severity="critical",
        ))
    if _trig("오안내_미정정", "uncorrected_misinfo", "incorrect_uncorrected"):
        decision = "T3"
        hitl_driver = "policy_driven"
        reasons.append("deduction_trigger:uncorrected_misinfo")
        priority_flags.append(_priority_flag(
            code="uncorrected_misinfo",
            description="오안내 후 미정정 감점 트리거 탐지",
            severity="critical",
        ))

    # 1-b) STT 품질 저하
    quality = preprocessing.get("quality", {}) or {}
    if quality.get("passed") is False:
        decision = "T3"
        hitl_driver = "policy_driven"
        reasons.append("stt_quality_failure")
        priority_flags.append(_priority_flag(
            code="stt_quality_failure",
            description=f"STT 품질 저하 — {', '.join(quality.get('reasons', []))}",
            severity="critical",
        ))

    # 1-c) VIP / 민원 플래그
    if tenant_flags.get("is_vip"):
        decision = "T3" if decision != "T3" else decision
        hitl_driver = hitl_driver or "policy_driven"
        reasons.append("vip_call")
        priority_flags.append(_priority_flag(
            code="vip_call",
            description="VIP 상담 — 전수 검수",
            severity="warn",
        ))
    if tenant_flags.get("is_complaint"):
        decision = "T3" if decision != "T3" else decision
        hitl_driver = hitl_driver or "policy_driven"
        reasons.append("complaint_call")
        priority_flags.append(_priority_flag(
            code="complaint_call",
            description="민원 상담 — 전수 검수",
            severity="warn",
        ))

    # 1-d) 개인정보 3개 항목 (#9, #17, #18) evaluable — FORCE_T3_ITEMS
    pii_items_evaluable = []
    for ev in evaluations:
        item = ev.get("evaluation") or ev  # V1 vs V2 래핑 호환
        item_number = item.get("item_number")
        mode = item.get("evaluation_mode")
        if item_number in FORCE_T3_ITEMS and mode not in ("skipped",):
            pii_items_evaluable.append(item_number)
    if pii_items_evaluable:
        decision = "T3"
        hitl_driver = "policy_driven"
        reasons.append(f"privacy_items_evaluable:{sorted(pii_items_evaluable)}")
        priority_flags.append(_priority_flag(
            code="privacy_force_t3",
            description=f"개인정보 3개 항목 중 {sorted(pii_items_evaluable)} 평가됨 — 전수 인간 검수",
            severity="critical",
            item_numbers=sorted(pii_items_evaluable),
        ))

    # 1-e) #15 partial_with_review 모드
    for ev in evaluations:
        item = ev.get("evaluation") or ev
        if item.get("item_number") == 15 and item.get("evaluation_mode") == "partial_with_review":
            decision = "T3"
            hitl_driver = "policy_driven"
            reasons.append("accuracy_partial_with_review")
            priority_flags.append(_priority_flag(
                code="accuracy_partial_with_review",
                description="정확한 안내 — 업무지식 RAG 부재로 AI 초안 + 인간 검수 필수",
                severity="warn",
                item_numbers=[15],
            ))
            break

    # 1-f) unevaluable 항목 존재 → self-report "판단 불가"
    unevaluable_items: list[int] = []
    for ev in evaluations:
        item = ev.get("evaluation") or ev
        if item.get("evaluation_mode") == "unevaluable":
            unevaluable_items.append(item.get("item_number"))
    if unevaluable_items:
        decision = "T3"
        hitl_driver = "policy_driven"
        reasons.append(f"unevaluable_items:{unevaluable_items}")
        priority_flags.append(_priority_flag(
            code="self_report_unevaluable",
            description=f"AI 판단 불가 — 항목 {unevaluable_items}",
            severity="warn",
            item_numbers=[i for i in unevaluable_items if i is not None],
        ))

    # -----------------------------------------------------------------
    # 2) Uncertainty-driven T3 (고배점 / 개인정보 항목 confidence ≤ 2)
    # -----------------------------------------------------------------
    if decision != "T3":
        for item_number, conf in confidence_results.items():
            if item_number in _HIGH_STAKES_ITEMS and conf.get("final", 5) <= 2:
                decision = "T3"
                hitl_driver = "uncertainty_driven"
                reasons.append(f"high_stakes_low_confidence:{item_number}")
                priority_flags.append(_priority_flag(
                    code="high_stakes_low_confidence",
                    description=f"고배점/개인정보 항목 #{item_number} confidence={conf.get('final')} — 필수 검수",
                    severity="warn",
                    item_numbers=[item_number],
                ))

    # -----------------------------------------------------------------
    # 3) Policy-driven T2 (총점 경계 ±3, 신입 상담사)
    # -----------------------------------------------------------------
    if decision not in ("T3",):
        total = int(final_score.get("after_overrides") or final_score.get("raw_total") or 0)
        if _near_grade_boundary(total, margin=policy.grade_boundary_margin):
            decision = "T2"
            hitl_driver = hitl_driver or "policy_driven"
            reasons.append(f"grade_boundary:{total}")
            priority_flags.append(_priority_flag(
                code="grade_boundary",
                description=f"총점 {total} — 등급 경계 ±{policy.grade_boundary_margin} 이내, 경량 검수",
                severity="info",
            ))
        if tenant_flags.get("is_rookie"):
            decision = "T2" if decision == "T0" else decision
            hitl_driver = hitl_driver or "policy_driven"
            reasons.append("rookie_counselor")
            priority_flags.append(_priority_flag(
                code="rookie_counselor",
                description="신입 상담사 — 전수 검수",
                severity="info",
            ))

    # -----------------------------------------------------------------
    # 4) Uncertainty-driven T2 (그 외 항목 confidence ≤ 2)
    # -----------------------------------------------------------------
    if decision == "T0":
        low_confidence_items = [
            i for i, conf in confidence_results.items() if conf.get("final", 5) <= 2
        ]
        if low_confidence_items:
            decision = "T2"
            hitl_driver = "uncertainty_driven"
            reasons.append(f"low_confidence_items:{sorted(low_confidence_items)}")
            priority_flags.append(_priority_flag(
                code="low_confidence_items",
                description=f"항목 {sorted(low_confidence_items)} — confidence ≤ 2",
                severity="info",
                item_numbers=sorted(low_confidence_items),
            ))

    # -----------------------------------------------------------------
    # T1 은 별도 샘플러에서 결정 (T0 중 5~10% 무작위). 여기서는 T0 유지.
    # -----------------------------------------------------------------

    return {
        "decision": decision,
        "hitl_driver": hitl_driver,
        "priority_flags": priority_flags,
        "estimated_review_time_min": _TIER_REVIEW_TIME_MIN[decision],
        "tier_reasons": reasons,
    }


def apply_t1_sampling(
    routing: dict[str, Any],
    *,
    rng_seed: int | None = None,
    sample_rate: float | None = None,
    sample_rate_max: float | None = None,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    """T0 건 중 무작위 sample_rate 만큼 T1 으로 승격 (설계서 §8.2 "5~10%").

    seed 고정 가능 (테스트/재현성). 호출자가 consultation_id 해시 등을 seed 로 주면 안전.

    샘플링 rate 결정:
      - sample_rate 명시 / sample_rate_max=None → 단일값 (기존 동작).
      - tenant_config.routing.t1_sample_rate_max 가 지정되면 consultation 별로
        [t1_sample_rate, t1_sample_rate_max] 균등분포에서 effective rate 추첨.
        `rng_seed` 동일하면 동일 rate + 동일 승격 결과 (deterministic).

    Parameters
    ----------
    sample_rate     : None 이면 tenant_config.routing.t1_sample_rate 로드 (하한/단일값).
    sample_rate_max : None 이면 tenant_config.routing.t1_sample_rate_max 로드.
                     최종적으로 None 이면 단일값 모드. float 이면 [sample_rate, max] 범위.
    tenant_id       : PL Q5 외부화 — 기본 "generic".
    """
    import random

    if routing.get("decision") != "T0":
        return routing

    policy = load_tenant_policy(tenant_id).routing
    if sample_rate is None:
        sample_rate = policy.t1_sample_rate
    if sample_rate_max is None:
        sample_rate_max = policy.t1_sample_rate_max

    rng = random.Random(rng_seed)

    # 범위 모드: [min, max] 균등분포에서 effective rate 1회 추첨.
    # 잘못된 범위 (max <= min) 는 단일값 모드로 강등.
    if sample_rate_max is not None and sample_rate_max > sample_rate:
        effective_rate = rng.uniform(sample_rate, sample_rate_max)
    else:
        effective_rate = sample_rate

    if rng.random() < effective_rate:
        routing = dict(routing)
        routing["decision"] = "T1"
        routing["hitl_driver"] = "policy_driven"  # 샘플링은 정책 주도
        routing["estimated_review_time_min"] = _TIER_REVIEW_TIME_MIN["T1"]
        reasons = list(routing.get("tier_reasons") or [])
        reasons.append(f"t1_sampling:rate={effective_rate:.4f}")
        routing["tier_reasons"] = reasons
    return routing


def enforce_t0_cap(
    routings: list[dict[str, Any]],
    *,
    tenant_id: str = "generic",
    cap: float | None = None,
) -> list[dict[str, Any]]:
    """배치 단위 T0 비중 cap 적용 (PL Q5 2026-04-20).

    현재 T0 로 분류된 샘플 비율이 cap 을 초과하면, 초과분을 T2 (monitoring) 로 강등.
    초과분 선정 기준: `weighted_composite` 이 낮은 샘플부터 (경계 샘플 우선).

    Parameters
    ----------
    routings : list of decide_tier() 반환 dict. 순서는 샘플 순서.
    cap      : None 이면 tenant_config.routing.initial_t0_cap 로드.
    tenant_id: tenant 키.

    Returns
    -------
    새 list (원본 불변). T0 초과분은 T2 + tier_reasons 에 "t0_cap_downgrade" 추가.

    주의:
    - 단일 샘플 파이프라인에서는 N=1 이므로 cap 적용 무의미 — 배치 수준에서만 호출.
    - T3 가 많아 T0 가 이미 낮은 경우 cap 미작동 (초과하지 않음).
    """
    if cap is None:
        cap = load_tenant_policy(tenant_id).routing.initial_t0_cap
    total = len(routings)
    if total == 0:
        return list(routings)

    t0_indices = [i for i, r in enumerate(routings) if r.get("decision") == "T0"]
    max_t0 = int(total * cap)
    if len(t0_indices) <= max_t0:
        return list(routings)

    # 초과분 중 weighted_composite 낮은 샘플부터 강등 (null 은 최하위로 취급)
    def _composite(idx: int) -> float:
        r = routings[idx]
        # routing dict 에서 composite 는 per-sample 전체 스냅샷이 없으므로
        # priority_flags 수 또는 tier_reasons 수로 근사 대체 (낮은 신뢰 순서 대용).
        # 명시적 composite 필드가 제공되면 사용.
        raw = r.get("overall_confidence")
        if isinstance(raw, (int, float)):
            return float(raw)
        return float(5 - len(r.get("tier_reasons", [])))  # reasons 많을수록 낮은 신뢰

    t0_sorted = sorted(t0_indices, key=_composite)
    downgrade_count = len(t0_indices) - max_t0
    downgrade_set = set(t0_sorted[:downgrade_count])

    out: list[dict[str, Any]] = []
    for i, r in enumerate(routings):
        if i in downgrade_set:
            r = dict(r)
            r["decision"] = "T2"
            r["hitl_driver"] = r.get("hitl_driver") or "policy_driven"
            r["estimated_review_time_min"] = _TIER_REVIEW_TIME_MIN["T2"]
            reasons = list(r.get("tier_reasons") or [])
            reasons.append(f"t0_cap_downgrade:cap={cap}")
            r["tier_reasons"] = reasons
        out.append(r)
    return out
