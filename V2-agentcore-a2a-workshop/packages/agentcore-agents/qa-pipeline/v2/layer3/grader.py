# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 3 (d) — 등급 판정 + 경계 라우팅.

설계서 p10 Layer 3 (d):
    총점을 등급(S/A/B/C/D 또는 조직별)으로 매핑하고, 경계 ±3점 건은 자동으로
    인간 검수로 라우팅.

Dev5 `enums.GRADE_BOUNDARIES` + `GRADE_BOUNDARY_MARGIN` 을 단일 진실 소스로 사용.
`routing_tier_hint` 는 Layer 4 `routing/tier_router.py` 의 입력 — Layer 3 는 단순
"경계 근처" 플래그만 생성하고 최종 T2/T3 판단은 Layer 4 소관.

추가 강제 T3 조건 (설계서 §8.2):
    - Layer 1 quality.unevaluable=True → T3 (tier_route_override='T3')
    - FORCE_T3_ITEMS (#9, #17, #18) 가 evaluable(skipped 아님) 이면 T3 힌트
"""

from __future__ import annotations

import logging
from typing import Any

from v2.schemas.enums import (
    FORCE_T3_ITEMS,
    GRADE_BOUNDARIES,
    GRADE_BOUNDARY_MARGIN,
    tier_max,
)


logger = logging.getLogger(__name__)


# ===========================================================================
# 메인 함수
# ===========================================================================


def assign_grade(
    *,
    raw_total: int,
    after_overrides: int,
    max_possible: int,
    preprocessing: dict[str, Any] | None = None,
    normalized_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """총점 → 등급 + 라우팅 힌트.

    Parameters
    ----------
    raw_total : int
        override 적용 전 총점.
    after_overrides : int
        override 적용 후 총점 (실제 최종 점수).
    max_possible : int
        100 (V2_MAX_TOTAL_SCORE).
    preprocessing : dict | None
        Layer 1 산출물. quality.unevaluable 등 T3 사유 참조.
    normalized_items : list[dict] | None
        FORCE_T3_ITEMS 체크용.

    Returns
    -------
    dict
        {
          "grade": "S" | "A" | "B" | "C" | "D",
          "final_total": int,
          "boundary_distance": int,      # 다음/이전 등급 경계까지 점수 차
          "near_boundary": bool,         # |boundary_distance| <= GRADE_BOUNDARY_MARGIN
          "routing_tier_hint": "T0"|"T1"|"T2"|"T3",
          "tier_reasons": [str, ...],
          "force_t3_items_active": list[int],   # FORCE_T3_ITEMS 중 evaluable 한 항목
        }
    """
    final_total = after_overrides
    tier_reasons: list[str] = []

    # (1) 등급 매핑 (내림차순 순회 — 만족하는 첫 등급 채택)
    grade = _map_grade(final_total)

    # (2) 다음 등급 경계까지의 거리 산출
    boundary_distance = _distance_to_boundary(final_total)
    near_boundary = abs(boundary_distance) <= GRADE_BOUNDARY_MARGIN

    # (3) Routing tier 힌트 (Layer 4 가 최종 결정)
    tier_hint = "T0"

    # 강제 T3 조건 1: STT 품질 저하
    quality = (preprocessing or {}).get("quality") or {}
    if quality.get("unevaluable"):
        tier_hint = "T3"
        tier_reasons.append("layer1_unevaluable")
    elif quality.get("tier_route_override") == "T3":
        tier_hint = "T3"
        tier_reasons.append("layer1_tier_override")

    # 강제 T3 조건 2: FORCE_T3_ITEMS 활성
    force_t3_active = _force_t3_items_active(normalized_items or [])
    if force_t3_active:
        tier_hint = tier_max(tier_hint, "T3")
        tier_reasons.append(f"force_t3_items={force_t3_active}")

    # 강제 T2 조건: 등급 경계 ±3점
    if near_boundary and tier_hint == "T0":
        tier_hint = "T2"
        tier_reasons.append(f"grade_boundary_±{GRADE_BOUNDARY_MARGIN}")

    # Override 적용 자체도 T2 이상 권고 (Layer 4 가 중대도로 상향)
    # (Layer 4 가 overrides.applied + trigger type 으로 실제 결정)

    logger.info(
        "assign_grade: total=%d → grade=%s dist=%d near=%s tier=%s",
        final_total, grade, boundary_distance, near_boundary, tier_hint,
    )

    return {
        "grade": grade,
        "final_total": final_total,
        "boundary_distance": boundary_distance,
        "near_boundary": near_boundary,
        "routing_tier_hint": tier_hint,
        "tier_reasons": tier_reasons,
        "force_t3_items_active": force_t3_active,
    }


# ===========================================================================
# 내부 헬퍼
# ===========================================================================


def _map_grade(total: int) -> str:
    """GRADE_BOUNDARIES 내림차순 순회 — 만족하는 첫 등급."""
    for grade, min_score in GRADE_BOUNDARIES:
        if total >= min_score:
            return grade
    return GRADE_BOUNDARIES[-1][0]  # Fallback (가장 낮은 등급)


def _distance_to_boundary(total: int) -> int:
    """현재 점수에서 가장 가까운 등급 경계까지의 거리.

    - total 이 경계값 바로 위 (예: 87, 경계 85) 이면 양수 (2)
    - total 이 경계값 바로 아래 (예: 83, 경계 85) 이면 음수 (-2)
    - 최상위 S (>=95) 에서 100 까지는 거리 고려 안 함 (boundary 없음) → 큰 양수
    """
    # 모든 boundary 점수와의 절대거리 중 최소
    distances = [total - min_score for _, min_score in GRADE_BOUNDARIES]
    # 절대값 기준 최소 거리
    closest = min(distances, key=lambda d: abs(d))
    return closest


def _force_t3_items_active(items: list[dict[str, Any]]) -> list[int]:
    """FORCE_T3_ITEMS 중 evaluation_mode 가 skipped / unevaluable 이 아닌 것."""
    active: list[int] = []
    for item in items:
        item_num = item.get("item_number")
        if item_num not in FORCE_T3_ITEMS:
            continue
        mode = item.get("evaluation_mode", "full")
        if mode not in ("skipped", "unevaluable"):
            active.append(item_num)  # type: ignore[arg-type]
    return sorted(active)
