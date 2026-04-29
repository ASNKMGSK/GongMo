# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""HITL 피드백 루프 — 일배치 + 항목별 통계 집계.

- ``run_nightly_batch`` : confirmed 리뷰를 순회하며 golden-set 후보 승격
  조건을 평가, 통과분만 ``golden_set_candidates`` 에 신규 INSERT.
  중복 (동일 review_id 에 이미 candidate 존재) 은 skip.

- ``compute_item_stats`` : 단일 item 에 대해 최근 N 건 confirmed 리뷰의
  MAE / Bias / override_pct / sample_count 산출.

- ``compute_all_item_stats`` : item 1..18 전체 순회.

DB 직접 SQL 은 통계 집계에만 사용 (db.py 에는 rolling N 고정 AND N 미만
스킵 형태만 존재 — feedback loop 는 rolling_window 파라미터 필요).
INSERT / SELECT 는 db.py 헬퍼 경유.
"""

from __future__ import annotations

import os
import sys
from typing import Any


_QA_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _QA_PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _QA_PIPELINE_DIR)


from v2.hitl import db  # noqa: E402
from v2.hitl.trigger_conditions import is_eligible_for_golden  # noqa: E402


ITEM_NUMBERS: tuple[int, ...] = tuple(range(1, 19))
TRANSCRIPT_EXCERPT_MAX_LEN: int = 200


def _extract_transcript_excerpt(ai_evidence: Any) -> str:
    """ai_evidence 의 첫 200자 추출. dict/list 는 str 변환."""
    if ai_evidence is None:
        return ""
    if isinstance(ai_evidence, str):
        text = ai_evidence
    else:
        text = str(ai_evidence)
    return text[:TRANSCRIPT_EXCERPT_MAX_LEN]


def _candidate_exists(review_id: int) -> bool:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM golden_set_candidates WHERE review_id = ? LIMIT 1",
            (int(review_id),),
        ).fetchone()
        return row is not None


def run_nightly_batch(dry_run: bool = False) -> dict[str, Any]:
    """Confirmed 리뷰 → golden-set 후보 승격 일배치.

    반환: {processed, eligible, skipped_existing, inserted, errors}. errors
    항목은 {review_id, error} dict 리스트.
    """
    db.init_db()

    processed = 0
    eligible = 0
    skipped_existing = 0
    inserted = 0
    errors: list[dict[str, Any]] = []

    reviews = db.list_reviews(status="confirmed", limit=10_000)
    for review in reviews:
        processed += 1
        review_id = int(review.get("id") or 0)
        try:
            ok, _reasons = is_eligible_for_golden(review)
            if not ok:
                continue
            eligible += 1

            if _candidate_exists(review_id):
                skipped_existing += 1
                continue

            if dry_run:
                continue

            ai_score = review.get("ai_score")
            human_score = review.get("human_score")
            try:
                delta: float | None = (
                    abs(float(ai_score) - float(human_score))
                    if ai_score is not None and human_score is not None
                    else None
                )
            except (TypeError, ValueError):
                delta = None

            db.insert_golden_candidate(
                review_id=review_id,
                consultation_id=review.get("consultation_id"),
                item_number=review.get("item_number"),
                transcript_excerpt=_extract_transcript_excerpt(review.get("ai_evidence")),
                human_score=human_score,
                human_note=review.get("human_note"),
                delta=delta,
                ai_confidence=review.get("ai_confidence"),
            )
            inserted += 1
        except Exception as exc:  # noqa: BLE001 — 배치는 per-row 복원
            errors.append({"review_id": review_id, "error": f"{type(exc).__name__}: {exc}"})

    return {
        "processed": processed,
        "eligible": eligible,
        "skipped_existing": skipped_existing,
        "inserted": inserted,
        "errors": errors,
        "dry_run": bool(dry_run),
    }


def compute_item_stats(item_number: int, rolling_window: int = 50) -> dict[str, Any]:
    """단일 item 의 최근 N 건 confirmed 리뷰 통계.

    SQL aggregation 으로 MAE / Bias / override_pct / sample_count.
    override = abs(ai_score - human_score) > 0.
    rolling_window 미만 샘플이면 실제 샘플 수로 계산 (None 아님).
    """
    sql = """
        SELECT ai_score, human_score
          FROM human_reviews
         WHERE item_number = ?
           AND status = 'confirmed'
           AND ai_score IS NOT NULL
           AND human_score IS NOT NULL
         ORDER BY COALESCE(confirmed_at, created_at) DESC
         LIMIT ?
    """
    with db.get_conn() as conn:
        rows = conn.execute(sql, (int(item_number), int(rolling_window))).fetchall()

    n = len(rows)
    if n == 0:
        return {
            "item_number": int(item_number),
            "mae": 0.0,
            "bias": 0.0,
            "override_pct": 0.0,
            "sample_count": 0,
        }

    diffs = [float(r["ai_score"]) - float(r["human_score"]) for r in rows]
    abs_diffs = [abs(d) for d in diffs]
    mae = sum(abs_diffs) / n
    bias = sum(diffs) / n
    overrides = sum(1 for d in abs_diffs if d > 0)
    override_pct = (overrides / n) * 100.0

    return {
        "item_number": int(item_number),
        "mae": round(mae, 4),
        "bias": round(bias, 4),
        "override_pct": round(override_pct, 2),
        "sample_count": n,
    }


def compute_all_item_stats(rolling_window: int = 50) -> list[dict[str, Any]]:
    """항목 1..18 전체 통계 리스트."""
    return [compute_item_stats(i, rolling_window=rolling_window) for i in ITEM_NUMBERS]
