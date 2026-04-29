# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Score-deduction reconciler — enforces the invariant
``score + Σ(deductions[].points) == max_score`` for every evaluation.

sLLM (Qwen3-8B) 은 프롬프트 예시를 그대로 흉내내는 경향이 강해
``{"score": 10, "deductions": [{"points": 3}]}`` 같은 산술 모순 출력을
자주 낸다. 이 유틸은 에이전트 출력 직전에 한 번 호출되어 자동 보정한다.

보정 정책:
  1. ``score == max_score`` 이면서 ``deductions`` 가 비어있지 않음
     → LLM 환각 (만점인데 감점 기록) — ``deductions`` 를 [] 로 리셋.
  2. ``score + Σ points != max_score`` (일반 불일치)
     → deductions 를 "증거" 로 신뢰 → ``score = max - Σ points`` 로 재계산,
       허용된 stepped 값으로 snap.
  3. ``score == max_score`` 이고 ``deductions`` 비어있음 → 그대로 통과.
  4. 일치하면 → 그대로 통과.

반환값에 ``note`` (str | None) 가 있어 보정 발생 시 로깅에 사용.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from nodes.qa_rules import QA_RULES


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Iter 03 — 규칙 폴백 감점 무효화 (인프라 실패를 프롬프트 이슈로 오진단 방지)
# ---------------------------------------------------------------------------
# v1/v3 배치에서 Bedrock ThrottlingException 또는 LLM 일반 실패 시
# 각 노드가 "규칙 폴백" 경로로 -2 ~ -5 감점을 자동 부여해 왔다.
# 이는 인프라(throttle/네트워크) 이슈를 QA 평가 감점으로 표출하여
# 프롬프트 튜닝 분석을 오염시키므로, 본 유틸이 감점 사유에서
# 폴백 키워드를 탐지하고 해당 deduction 의 points 를 0 으로 무효화한다.
# reason 에 [SKIPPED_INFRA] 태그 부여 → 리포트/분석에서 식별 가능.

_FALLBACK_KEYWORDS: tuple[str, ...] = (
    "LLM 실패",
    "LLM 호출 실패",
    "ThrottlingException",
    "throttling",
    "Throttling",
    "규칙 폴백",
    "rule fallback",
    "rule_fallback",
    "fallback",
    "Too many tokens",
    "Too Many Requests",
)


def _is_fallback_deduction(deduction: dict) -> bool:
    """감점 사유에 인프라 실패 / 규칙 폴백 키워드가 포함되었는지 검사."""
    reason = str(deduction.get("reason", ""))
    return any(kw in reason for kw in _FALLBACK_KEYWORDS)


def normalize_fallback_deductions(deductions: list[dict]) -> tuple[list[dict], int, int]:
    """규칙 폴백 감점의 points 를 0 으로 무효화.

    Returns
    -------
    (수정된 deductions, 무효화된 건수, 복구 점수 합계)
    """
    if not deductions:
        return deductions, 0, 0
    result: list[dict] = []
    skipped_count = 0
    recovered_points = 0
    for d in deductions:
        if _is_fallback_deduction(d):
            orig_pts = int(d.get("points", 0) or 0)
            new_d = dict(d)
            new_d["points"] = 0
            new_d["reason"] = f"[SKIPPED_INFRA] {d.get('reason', '')}".strip()
            new_d["infra_skipped"] = True
            result.append(new_d)
            skipped_count += 1
            recovered_points += orig_pts
        else:
            result.append(d)
    return result, skipped_count, recovered_points


# ---------------------------------------------------------------------------
# Allowed stepped-score map, derived from QA_RULES at import time.
# 예: {10: [10, 7, 5, 0], 11: [5, 3, 0], 15: [10, 5, 0], ...}
# ---------------------------------------------------------------------------


def _build_allowed_scores_map() -> dict[int, list[int]]:
    m: dict[int, list[int]] = {}
    for rule in QA_RULES:
        item_no = rule["item_number"]
        max_score = rule["max_score"]
        steps = {max_score, 0}
        for dr in rule.get("deduction_rules", []):
            to_score = dr.get("to_score")
            if isinstance(to_score, int):
                steps.add(to_score)
        m[item_no] = sorted(steps, reverse=True)  # 큰 값부터
    return m


_ALLOWED_SCORES: dict[int, list[int]] = _build_allowed_scores_map()


def _snap_to_allowed(score: int, item_number: int) -> int:
    """score 를 해당 item 의 허용 stepped 값 중 가장 가까운 값으로 snap (이하 방향 선호)."""
    allowed = _ALLOWED_SCORES.get(item_number)
    if not allowed:
        return max(0, score)
    # score 이하 중 최대값 (없으면 가장 가까운 값)
    candidates = [s for s in allowed if s <= score]
    if candidates:
        return max(candidates)
    return min(allowed)  # score 가 모든 허용값보다 작음 → 최솟값(보통 0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class ReconcileResult:
    score: int
    deductions: list[dict]
    note: str | None = None  # 보정 발생 시 설명 (None 이면 변경 없음)


def snap_score(item_number: int, score: int) -> int:
    """score 를 해당 item 의 허용 stepped 값으로 snap (이하 방향 선호)."""
    return _snap_to_allowed(score, item_number)


def reconcile(
    *,
    item_number: int,
    score: int,
    max_score: int,
    deductions: list[dict] | None,
) -> ReconcileResult:
    """score + Σ(deductions.points) == max_score 를 강제 보정.

    Parameters
    ----------
    item_number : int  — 1~18
    score       : int  — LLM 또는 Scorer 가 판정한 점수
    max_score   : int  — 해당 항목 만점
    deductions  : list[dict] — 각 원소에 ``points`` 키 필요

    Returns
    -------
    ReconcileResult(score, deductions, note)
    """
    dedu = list(deductions or [])
    ded_sum = sum(int(d.get("points", 0) or 0) for d in dedu)
    # 이미 일치 — 변경 없음
    if score + ded_sum == max_score:
        return ReconcileResult(score=score, deductions=dedu, note=None)

    # 케이스 1: 만점인데 deductions 가 있음 → hallucination. deductions 드롭.
    if score == max_score and ded_sum > 0:
        note = (
            f"[reconcile] item #{item_number}: score={max_score} (만점) 인데 "
            f"deductions_sum={ded_sum} → deductions 드롭 (LLM hallucination)"
        )
        logger.warning(note)
        return ReconcileResult(score=max_score, deductions=[], note=note)

    # 케이스 2: 일반 불일치 → deductions 를 증거로 신뢰, score 재계산
    derived = max_score - ded_sum
    snapped = _snap_to_allowed(derived, item_number)
    # snap 으로 값이 변경되면 deductions_sum 도 재조정 필요 (비례 축소)
    # 간단화를 위해 deductions 는 그대로 두고 score 만 snap — score_validation 재검증
    # 과정에서 snap 결과와 일치하는지 한 번 더 비교.
    # snap 결과 != derived 라면 deductions 합계를 다시 max - snapped 로 맞춰야 함.
    if snapped != derived:
        target_sum = max_score - snapped
        # deductions 에 가장 큰 points 를 기준으로 비례 축소 — 단순화를 위해 전체 points 를 target_sum 으로 재분배
        dedu = _rescale_deductions(dedu, target_sum)
        ded_sum = target_sum
    else:
        ded_sum = derived  # 이미 max - snapped 와 동일

    note = (
        f"[reconcile] item #{item_number}: score={score}, deductions_sum={ded_sum} 불일치 "
        f"→ score={snapped} 로 재계산 (deductions 증거 우선)"
    )
    logger.warning(note)
    return ReconcileResult(score=snapped, deductions=dedu, note=note)


def _rescale_deductions(deductions: list[dict], target_sum: int) -> list[dict]:
    """deductions points 합계를 target_sum 과 일치하도록 비례 조정.

    가장 큰 항목부터 정수 분배. 예:
      [{points:3}, {points:2}] with target=4 → [{points:2}, {points:2}] or similar.
    """
    if not deductions or target_sum <= 0:
        return [{**d, "points": 0} for d in deductions] if target_sum == 0 else deductions
    current_sum = sum(int(d.get("points", 0) or 0) for d in deductions)
    if current_sum == target_sum:
        return deductions
    if current_sum == 0:
        # 모든 points 가 0 인데 target_sum > 0 — 첫 항목에 target_sum 부여
        result = [dict(d) for d in deductions]
        result[0]["points"] = target_sum
        return result
    # 비례 조정 후 잔차를 첫 항목에 흡수
    scaled: list[dict] = []
    running = 0
    for i, d in enumerate(deductions):
        orig_pts = int(d.get("points", 0) or 0)
        if i == len(deductions) - 1:
            new_pts = max(0, target_sum - running)
        else:
            new_pts = max(0, round(orig_pts * target_sum / current_sum))
            running += new_pts
        scaled.append({**d, "points": new_pts})
    return scaled


def reconcile_evaluation(evaluation: dict) -> tuple[dict, str | None]:
    """평가 dict (``{item_number, max_score, score, deductions, ...}``) 를 in-place 보정.

    처리 순서:
    1. 규칙 폴백 감점 무효화 (Iter 03 인프라 분리)
    2. 산술 일관성 보정 (score + Σ points == max_score)

    반환: (보정된 evaluation, 보정 note — None 이면 변경 없음)
    """
    item_no = evaluation.get("item_number")
    max_score = evaluation.get("max_score")
    score = evaluation.get("score", 0)
    deductions = evaluation.get("deductions", [])
    if not isinstance(item_no, int) or not isinstance(max_score, int):
        return evaluation, None

    # 1단계: 규칙 폴백 감점 무효화
    normalized_deductions, skipped_count, recovered_points = normalize_fallback_deductions(deductions)
    infra_note: str | None = None
    current_score = int(score or 0)
    if skipped_count > 0:
        # 폴백으로 깎였던 점수를 score 에 복구 (만점 상한까지만)
        new_score = min(max_score, current_score + recovered_points)
        infra_note = (
            f"[reconcile-infra] item #{item_no}: fallback deductions {skipped_count}건 무효화, "
            f"score {current_score} → {new_score} (+{new_score - current_score})"
        )
        logger.warning(infra_note)
        current_score = new_score

    # 2단계: 산술 일관성 보정
    result = reconcile(
        item_number=item_no,
        score=current_score,
        max_score=max_score,
        deductions=normalized_deductions,
    )

    # 변경 여부 판단
    changed = (
        skipped_count > 0
        or result.note is not None
        or current_score != int(score or 0)
        or normalized_deductions is not deductions
    )
    if not changed:
        return evaluation, None

    fixed = dict(evaluation)
    fixed["score"] = result.score
    fixed["deductions"] = result.deductions
    # 폴백 복구 메타데이터를 평가에 부착 (리포트에서 활용)
    if skipped_count > 0:
        fixed["infra_fallback_skipped"] = skipped_count
        fixed["infra_fallback_recovered_points"] = recovered_points

    combined_note = " | ".join(n for n in (infra_note, result.note) if n)
    return fixed, combined_note or None
