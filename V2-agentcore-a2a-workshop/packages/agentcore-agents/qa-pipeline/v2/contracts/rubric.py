# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
V2 Rubric 및 ALLOWED_STEPS 테이블 (Phase A2 확정 2026-04-20).

PL 최종 승인 내용:
  - #17 / #18 = [5, 3, 0]  — iter05 snap_score 강제 0 변환 회귀 해소 (3점 중간단계 복원)
  - 그 외 항목은 V1 qa_rules.py 와 동일 단계
  - V1 `nodes/qa_rules.py` 는 변경 금지. V2 전용 ALLOWED_STEPS 를 본 모듈에 둔다.

`snap_score_v2(item_number, score)` 는 V2 ALLOWED_STEPS 기준으로 snap 한다.
V1 `nodes/skills/reconciler.snap_score` 는 V1 테이블을 사용하므로 #17/#18 에서
3 → 0 으로 강제 변환됨 → V2 Sub Agent / Layer 1 rule_pre_verdictor 는 반드시
`snap_score_v2` 를 경유해야 한다.
"""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


# ===========================================================================
# V2 ALLOWED_STEPS — 항목별 허용 점수 단계 (내림차순)
# ===========================================================================
#
# 값 규칙:
#   - 첫 원소 = 만점 (max_score)
#   - 마지막 원소 = 0 (완전 미준수)
#   - 중간 원소 = 부분 준수 허용 단계
#
# PL Phase A2 확정:
#   - #17 정보 확인 절차  : [5, 3, 0]  (확장 — 부분 이행 복원)
#   - #18 정보 보호 준수   : [5, 3, 0]  (확장 — 경미 위반 복원)
#   - 그 외                : V1 `nodes/qa_rules.py` 와 동일
# ===========================================================================

ALLOWED_STEPS: dict[int, list[int]] = {
    # 인사 예절 (10점)
    1: [5, 3, 0],
    2: [5, 3, 0],
    # 경청 및 소통 (10점) — #3 경청/말겹침/말자름 은 평가표에서 제거 (STT 한계 · 2026-04-21)
    #   제거된 5점은 #15 정확한 안내 로 이관 (업무지식 RAG 기반 오안내 방지 강화).
    4: [5, 3, 0],
    5: [5, 3, 0],
    # 언어 표현 (10점)
    6: [5, 3, 0],
    7: [5, 3, 0],
    # 니즈 파악 (10점)
    8: [5, 3, 0],
    9: [5, 3, 0],
    # 설명력 및 전달력 (15점)
    10: [10, 7, 5, 0],
    11: [5, 3, 0],
    # 적극성 (15점)
    12: [5, 3, 0],
    13: [5, 3, 0],
    14: [5, 3, 0],
    # 업무 정확도 (20점) — #15 확장 [10,5,0] → [15,10,5,0] (+5, 2026-04-21)
    15: [15, 10, 5, 0],
    16: [5, 3, 0],
    # 개인정보 보호 (10점) — Phase A2 확장
    17: [5, 3, 0],
    18: [5, 3, 0],
}


def max_score_of(item_number: int) -> int:
    """항목별 만점 값."""
    steps = ALLOWED_STEPS.get(item_number)
    if not steps:
        raise KeyError(f"unknown item_number={item_number}")
    return steps[0]


def snap_score_v2(item_number: int, score: int) -> int:
    """V2 ALLOWED_STEPS 기준으로 score 를 허용 단계로 snap.

    V1 `reconciler.snap_score` 와 동일한 '이하 방향' 스냅 정책을 따른다:
      - score 이하 중 최대값을 선택 (없으면 최솟값, 보통 0)
      - 결과는 반드시 ALLOWED_STEPS[item_number] 에 속한다

    Parameters
    ----------
    item_number : int
        1~18.
    score : int
        LLM 또는 Rule 이 판정한 raw 점수.

    Returns
    -------
    int
        허용 단계에 snap 된 점수.

    Examples
    --------
    >>> snap_score_v2(17, 3)  # V1 은 0 으로 강제 변환, V2 는 3 유지
    3
    >>> snap_score_v2(17, 4)  # 허용 단계 중 4 이하 최대 = 3
    3
    >>> snap_score_v2(10, 6)  # #10 = [10, 7, 5, 0] → 6 이하 최대 = 5
    5
    >>> snap_score_v2(15, 12) # #15 = [15, 10, 5, 0] → 12 이하 최대 = 10
    10
    """
    steps = ALLOWED_STEPS.get(item_number)
    if not steps:
        raise KeyError(f"unknown item_number={item_number}")

    # 단일 값(예: #3 [5]) 은 강제 만점
    if len(steps) == 1:
        return steps[0]

    # 음수 방어
    s = max(0, int(score))

    # score 이하 후보 중 최대값
    candidates = [v for v in steps if v <= s]
    if candidates:
        return max(candidates)

    # 모든 허용값보다 작음 (일반적으로 score < 0) → 최소값 (= 0)
    return min(steps)


def is_valid_step(item_number: int, score: int) -> bool:
    """score 가 해당 항목의 허용 단계에 속하는지 검증 (score_validation 용)."""
    steps = ALLOWED_STEPS.get(item_number)
    if not steps:
        return False
    return int(score) in steps


def allowed_steps_of(item_number: int) -> list[int]:
    """항목별 허용 단계 리스트 복사본 반환 (외부 mutation 방지)."""
    steps = ALLOWED_STEPS.get(item_number)
    if not steps:
        raise KeyError(f"unknown item_number={item_number}")
    return list(steps)


# ===========================================================================
# V2 총점 (100점 만점 검증용)
# ===========================================================================

V2_MAX_TOTAL_SCORE: int = sum(steps[0] for steps in ALLOWED_STEPS.values())
# = 5+5 + 5+5 + 5+5 + 5+5 + 10+5 + 5+5+5 + 15+5 + 5+5 = 100
# 2026-04-21: #3 경청 제거 (-5) + #15 정확한 안내 확장 (+5). 총점 100 불변.
# Phase A2 에서 #17/#18 은 max_score 만 보존(5), 중간단계만 3 추가.

assert V2_MAX_TOTAL_SCORE == 100, (
    f"V2 rubric 총점 {V2_MAX_TOTAL_SCORE} != 100 — ALLOWED_STEPS 재검토 필요"
)
