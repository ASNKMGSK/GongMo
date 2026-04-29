# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""HITL 피드백 루프 발동 조건 평가.

두 종류의 트리거를 노출한다:

1. ``is_eligible_for_golden`` — 단일 human_reviews row 가 golden-set 후보로
   승격 가능한지 판정. AND 조건 5종 (점수 차, 비고 길이, AI confidence,
   검토자 권한, status=confirmed) 모두 통과해야 True.

2. ``detect_tuning_priority`` — 항목별 집계 통계 (MAE/bias/override_pct)
   기반 프롬프트 튜닝 우선순위 판정. OR 조건. 샘플 50건 미만이면
   ``insufficient_samples`` 사유로 False.

모든 임계값은 모듈 상단 상수로 노출 — 운영 튜닝 가능.
"""

from __future__ import annotations

from typing import Any


SCORE_DELTA_THRESHOLD: float = 2.0
HUMAN_NOTE_MIN_LENGTH: int = 30
AI_CONFIDENCE_MAX: float = 0.6
ELIGIBLE_REVIEWER_ROLES: frozenset[str] = frozenset({"senior", "lead"})
ELIGIBLE_REVIEW_STATUS: str = "confirmed"

TUNING_MAE_THRESHOLD: float = 1.0
TUNING_BIAS_ABS_THRESHOLD: float = 0.5
TUNING_OVERRIDE_PCT_THRESHOLD: float = 30.0
TUNING_MIN_SAMPLES: int = 50


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_eligible_for_golden(review: dict[str, Any]) -> tuple[bool, list[str]]:
    """human_reviews row 가 golden-set 후보로 승격 가능한지.

    모든 조건이 AND 로 결합된다. 실패한 조건은 reasons 리스트에
    음의 사유 문자열로 누적, 통과 시엔 양의 사유로 누적된다.
    반환은 (eligible, reasons).
    """

    reasons: list[str] = []

    status = review.get("status")
    if status == ELIGIBLE_REVIEW_STATUS:
        reasons.append(f"status={status}")
    else:
        reasons.append(f"status!={ELIGIBLE_REVIEW_STATUS} (got {status!r})")

    ai_score = _as_float(review.get("ai_score"))
    human_score = _as_float(review.get("human_score"))
    if ai_score is None or human_score is None:
        reasons.append("score_missing")
        delta_ok = False
    else:
        delta = abs(ai_score - human_score)
        if delta >= SCORE_DELTA_THRESHOLD:
            reasons.append(f"delta={delta:.2f}>={SCORE_DELTA_THRESHOLD}")
            delta_ok = True
        else:
            reasons.append(f"delta={delta:.2f}<{SCORE_DELTA_THRESHOLD}")
            delta_ok = False

    note = review.get("human_note") or ""
    note_len = len(note) if isinstance(note, str) else 0
    if note_len >= HUMAN_NOTE_MIN_LENGTH:
        reasons.append(f"note_len={note_len}>={HUMAN_NOTE_MIN_LENGTH}")
        note_ok = True
    else:
        reasons.append(f"note_len={note_len}<{HUMAN_NOTE_MIN_LENGTH}")
        note_ok = False

    conf = _as_float(review.get("ai_confidence"))
    if conf is None:
        reasons.append("ai_confidence_missing")
        conf_ok = False
    elif conf < AI_CONFIDENCE_MAX:
        reasons.append(f"ai_confidence={conf:.3f}<{AI_CONFIDENCE_MAX}")
        conf_ok = True
    else:
        reasons.append(f"ai_confidence={conf:.3f}>={AI_CONFIDENCE_MAX}")
        conf_ok = False

    role = review.get("reviewer_role")
    if role in ELIGIBLE_REVIEWER_ROLES:
        reasons.append(f"role={role}")
        role_ok = True
    else:
        reasons.append(f"role={role!r} not in {sorted(ELIGIBLE_REVIEWER_ROLES)}")
        role_ok = False

    status_ok = status == ELIGIBLE_REVIEW_STATUS
    eligible = all((status_ok, delta_ok, note_ok, conf_ok, role_ok))
    return eligible, reasons


def detect_tuning_priority(item_stats: dict[str, Any]) -> tuple[bool, list[str]]:
    """항목별 통계로 프롬프트 튜닝 우선순위 판정.

    MAE / |bias| / override_pct 중 하나라도 임계값을 넘으면 True (OR).
    샘플 수가 ``TUNING_MIN_SAMPLES`` 미만이면 판정 불가로 False +
    ``insufficient_samples`` 사유 반환.
    """

    reasons: list[str] = []

    sample_count = item_stats.get("sample_count")
    try:
        n = int(sample_count) if sample_count is not None else 0
    except (TypeError, ValueError):
        n = 0
    if n < TUNING_MIN_SAMPLES:
        return False, ["insufficient_samples"]

    mae = _as_float(item_stats.get("mae"))
    bias = _as_float(item_stats.get("bias"))
    override_pct = _as_float(item_stats.get("override_pct"))

    needs = False
    if mae is not None and mae >= TUNING_MAE_THRESHOLD:
        reasons.append(f"mae={mae:.3f}>={TUNING_MAE_THRESHOLD}")
        needs = True
    if bias is not None and abs(bias) >= TUNING_BIAS_ABS_THRESHOLD:
        reasons.append(f"|bias|={abs(bias):.3f}>={TUNING_BIAS_ABS_THRESHOLD}")
        needs = True
    if override_pct is not None and override_pct >= TUNING_OVERRIDE_PCT_THRESHOLD:
        reasons.append(f"override_pct={override_pct:.2f}>={TUNING_OVERRIDE_PCT_THRESHOLD}")
        needs = True

    if not needs:
        reasons.append("all_metrics_within_threshold")
    return needs, reasons
