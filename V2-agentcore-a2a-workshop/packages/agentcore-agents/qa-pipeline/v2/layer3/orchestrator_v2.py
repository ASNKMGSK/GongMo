# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 3 Orchestrator V2 — 4 모듈 순차 호출 + 상태 조립.

설계서 p10 Layer 3 (a)→(b)→(c)→(d) 순서 엄격:
    (a) aggregate_scores        — 대분류별 집계 + raw_total
    (b) apply_overrides         — Layer 1/Layer 2 감점 트리거 Override
    (c) check_consistency       — Rule 기반 교차 점검
    (d) assign_grade            — 등급 + 라우팅 힌트

skip_phase_c_and_reporting 플래그 유지 — 프롬프트 튜닝 배치 시 (c)/(d) 스킵 가능.

출력 구조 (Dev5 `v2/schemas/qa_output_v2.py` 호환):
    final_score: {raw_total, after_overrides, grade}
    overrides:   {applied, reasons[]}
    consistency_flags: [{code, severity, description, item_numbers}]
    grade_detail: {boundary_distance, near_boundary, routing_tier_hint, tier_reasons, force_t3_items_active}
    category_scores: [{category_key, category, max_score, achieved_score, items[]}]
    normalized_items: [...] (플랫 18 항목)
    final_evaluations: [...] (최종 확정 evaluations — Layer 4 가 consume)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from v2.layer3.aggregator import aggregate_scores
from v2.layer3.consistency_checker import check_consistency
from v2.layer3.grader import assign_grade
from v2.layer3.override_rules import apply_overrides
from v2.schemas.enums import tier_max


logger = logging.getLogger(__name__)


# ===========================================================================
# 메인 함수
# ===========================================================================


def _collect_override_hints(evaluations: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Sub Agent evaluations 에서 override_hint 필드 집계 — Layer 3 Override 보조 시그널.

    V1 포맷: {"status", "agent_id", "evaluation": {..., "override_hint": "profanity"}}
    V2 포맷: {"item_number": ..., "override_hint": "privacy_leak", ...} (평면)
    """
    hints: list[dict[str, Any]] = []
    for ev in evaluations or []:
        if not isinstance(ev, dict):
            continue
        # V1 중첩 포맷 우선, V2 평면 포맷 fallback
        inner = ev.get("evaluation") if isinstance(ev.get("evaluation"), dict) else ev
        if not isinstance(inner, dict):
            continue
        hint = inner.get("override_hint")
        if not hint:
            continue
        item_number = inner.get("item_number")
        hints.append({
            "item_number": item_number,
            "hint": hint,
        })
    return hints


def run_layer3(
    evaluations: list[dict[str, Any]],
    *,
    preprocessing: dict[str, Any] | None = None,
    accuracy_verdict: dict[str, Any] | None = None,
    skip_phase_c_and_reporting: bool = False,
    site_id: str | None = None,
) -> dict[str, Any]:
    """Layer 3 전체 실행.

    Parameters
    ----------
    evaluations : list[dict]
        Layer 2 Sub Agent 결과 (V1 호환 포맷 허용).
    preprocessing : dict | None
        QAStateV2.preprocessing (Layer 1 산출물).
    accuracy_verdict : dict | None
        QAStateV2.accuracy_verdict (Dev3 Group B work_accuracy Sub Agent 산출).
    skip_phase_c_and_reporting : bool
        True 시 consistency_checker / grader 스킵 (프롬프트 튜닝 배치 경로).
        V1 orchestrator 의 동명 플래그와 동일 의미 — 평가만 수집.

    Returns
    -------
    dict
        QAStateV2.orchestrator 에 저장될 최종 산출물.
    """
    diagnostics: list[dict[str, Any]] = []

    # (a) 집계
    t0 = time.perf_counter()
    agg = aggregate_scores(evaluations, site_id=site_id)
    diagnostics.append(_diag("aggregate_scores", t0, "ok"))

    category_scores = agg["category_scores"]
    raw_total = agg["raw_total"]
    max_possible = agg["max_possible"]
    normalized_items = agg["normalized_items"]
    missing_items = agg["missing_items"]

    # Sub Agent override_hints 집계 — Layer 1 Rule 트리거 부재 시 보조 consume
    override_hints = _collect_override_hints(evaluations)

    # (b) Override
    t0 = time.perf_counter()
    ov = apply_overrides(
        category_scores,
        preprocessing=preprocessing,
        accuracy_verdict=accuracy_verdict,
        sub_agent_override_hints=override_hints,
        raw_total=raw_total,
    )
    diagnostics.append(_diag("apply_overrides", t0, "ok"))

    overrides_applied = ov["applied"]
    overrides_reasons = ov["reasons"]
    after_overrides = ov["after_overrides"]
    items_modified = ov["items_modified"]

    # Override 후 normalized_items 도 갱신 (score 변화 반영)
    normalized_items = _refresh_normalized(category_scores)

    # skip_phase_c_and_reporting — (c) 와 (d) 스킵, 최소 집계만 반환
    if skip_phase_c_and_reporting:
        logger.info("run_layer3: skip_phase_c_and_reporting=True → consistency/grade 스킵")
        return _build_output(
            category_scores=category_scores,
            raw_total=raw_total,
            after_overrides=after_overrides,
            max_possible=max_possible,
            overrides_applied=overrides_applied,
            overrides_reasons=overrides_reasons,
            items_modified=items_modified,
            missing_items=missing_items,
            normalized_items=normalized_items,
            consistency=None,
            grade_detail=None,
            diagnostics=diagnostics,
        )

    # (c) Consistency
    t0 = time.perf_counter()
    cc = check_consistency(category_scores, normalized_items=normalized_items)
    diagnostics.append(_diag("check_consistency", t0, "ok"))

    # (d) Grade
    t0 = time.perf_counter()
    gd = assign_grade(
        raw_total=raw_total,
        after_overrides=after_overrides,
        max_possible=max_possible,
        preprocessing=preprocessing,
        normalized_items=normalized_items,
    )
    diagnostics.append(_diag("assign_grade", t0, "ok"))

    # 일관성 critical 이면 tier 상향
    if cc["has_critical"]:
        gd["tier_reasons"] = list(gd.get("tier_reasons", [])) + ["consistency_critical"]
        gd["routing_tier_hint"] = tier_max(gd["routing_tier_hint"], "T2")

    logger.info(
        "run_layer3: done — raw=%d after=%d grade=%s tier=%s overrides=%s consistency_flags=%d",
        raw_total, after_overrides, gd["grade"], gd["routing_tier_hint"],
        overrides_applied, len(cc["flags"]),
    )

    return _build_output(
        category_scores=category_scores,
        raw_total=raw_total,
        after_overrides=after_overrides,
        max_possible=max_possible,
        overrides_applied=overrides_applied,
        overrides_reasons=overrides_reasons,
        items_modified=items_modified,
        missing_items=missing_items,
        normalized_items=normalized_items,
        consistency=cc,
        grade_detail=gd,
        diagnostics=diagnostics,
    )


# ===========================================================================
# 내부 헬퍼
# ===========================================================================


def _diag(module: str, started: float, status: str) -> dict[str, Any]:
    return {
        "module": module,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "status": status,
    }


def _refresh_normalized(category_scores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """override 적용 후 category_scores 에서 플랫 item 리스트 재생성."""
    items: list[dict[str, Any]] = []
    for cat in category_scores:
        for item in cat.get("items", []):
            items.append(item)
    items.sort(key=lambda x: x.get("item_number", 0))
    return items


def _build_output(
    *,
    category_scores: list[dict[str, Any]],
    raw_total: int,
    after_overrides: int,
    max_possible: int,
    overrides_applied: bool,
    overrides_reasons: list[dict[str, Any]],
    items_modified: list[int],
    missing_items: list[int],
    normalized_items: list[dict[str, Any]],
    consistency: dict[str, Any] | None,
    grade_detail: dict[str, Any] | None,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    """Layer 3 최종 output dict 빌드 (Dev5 schemas 호환)."""
    grade = grade_detail["grade"] if grade_detail else ""
    tier_hint = grade_detail["routing_tier_hint"] if grade_detail else "T0"

    return {
        # Dev5 FinalScoreBlock 호환
        "final_score": {
            "raw_total": raw_total,
            "after_overrides": after_overrides,
            "grade": grade,
        },
        # Dev5 OverridesBlock 호환
        "overrides": {
            "applied": overrides_applied,
            "reasons": overrides_reasons,
            "items_modified": items_modified,
        },
        # 카테고리별 집계 (Dev5 CategoryBlock 소스)
        "category_scores": category_scores,
        # 일관성 flag (Layer 4 priority_flags 소스)
        "consistency_flags": consistency["flags"] if consistency else [],
        "consistency_has_critical": consistency["has_critical"] if consistency else False,
        "consistency_has_warning": consistency["has_warning"] if consistency else False,
        # 등급 상세 (Layer 4 routing 입력)
        "grade_detail": grade_detail or {},
        "routing_tier_hint": tier_hint,
        # 확정 evaluations (Layer 4 가 consume)
        "final_evaluations": normalized_items,
        # 진단
        "max_possible": max_possible,
        "missing_items": missing_items,
        "layer3_diagnostics": diagnostics,
    }
