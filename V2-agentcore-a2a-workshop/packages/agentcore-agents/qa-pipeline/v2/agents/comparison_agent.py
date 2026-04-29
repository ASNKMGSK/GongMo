# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Comparison Agent — 사람 정답(human_score) vs AI 평가(report.evaluation) 차이 비교.

평가 결과 탭에서 "AI 가 얼마나 사람 정답에 가까운가" 한눈에 보기 위한 백엔드.

데이터 소스:
  - AI report : `~/Desktop/QA평가결과/JSON/{cid}.json` (queue_populator 가 저장한 JSON)
  - 사람 정답 : SQLite `human_reviews` 테이블 (status='confirmed', human_score IS NOT NULL)

지표 정책 (memory: feedback_qa_metric_framing):
  Pearson / Spearman / R² / κ 등 상관계수 노출 금지.
  허용 지표 — MAE, RMSE, Bias, MAPE, Accuracy(exact_match_rate).

조인 규칙:
  - item_number 기준으로 AI item ↔ human_reviews row 1:1 매칭.
  - 사람 정답 없는 AI item 은 비교 대상에서 제외 (compared_count 미포함).
  - 이로써 부분 검수 상태에서도 "검수된 항목만큼" 의 일치도가 즉시 보임.

agreement 분류:
  - "exact"   : ai_score == human_score
  - "close"   : ALLOWED_STEPS 인접 단계 1칸 이내 (배점이 다른 항목 호환)
  - "diverge" : 그 외

agreement_label (compared_count 기반):
  - >=0.95 "perfect" / >=0.80 "high" / >=0.60 "moderate" / else "low"
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from v2.contracts.rubric import ALLOWED_STEPS, max_score_of
from v2.hitl.db import get_conn
from v2.schemas.enums import CATEGORY_META


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 항목 번호 → 한국어 표준 이름 (qa_rules.py 의 name 필드 기준).
# AI report 의 item 필드는 "항목 1" / "첫인사" 등 케이스가 섞여 있어 직접 노출 불가 →
# 항상 본 매핑을 사용한다.
# ---------------------------------------------------------------------------
_ITEM_NAME_KO: dict[int, str] = {
    1: "첫인사",
    2: "끝인사",
    3: "경청/말겹침/말자름",
    4: "호응 및 공감",
    5: "대기 멘트",
    6: "정중한 표현",
    7: "쿠션어 활용",
    8: "문의 파악 및 재확인/복창",
    9: "고객정보 확인",
    10: "설명의 명확성",
    11: "두괄식 답변",
    12: "문제 해결 의지",
    13: "부연 설명 및 추가 안내",
    14: "사후 안내",
    15: "정확한 안내",
    16: "필수 안내 이행",
    17: "정보 확인 절차",
    18: "정보 보호 준수",
}


# 항목 → 카테고리 키 (CATEGORY_META 역인덱스)
def _build_item_to_category() -> dict[int, str]:
    mapping: dict[int, str] = {}
    for cat_key, meta in CATEGORY_META.items():
        for item_no in meta.get("items", []):
            mapping[int(item_no)] = cat_key
    return mapping


_ITEM_TO_CATEGORY: dict[int, str] = _build_item_to_category()


# ---------------------------------------------------------------------------
# JSON 파일 위치 (queue_populator 와 동일 규칙)
# ---------------------------------------------------------------------------


def _resolve_json_root() -> Path:
    root_str = os.environ.get("QA_RESULT_JSON_ROOT") or str(Path.home() / "Desktop" / "QA평가결과" / "JSON")
    return Path(root_str)


def _safe_consultation_id(cid: str) -> str:
    """경로 traversal 차단 — 슬래시 / 백슬래시 / 상대경로 표시 제거."""
    safe = str(cid).replace("/", "_").replace("\\", "_")
    return safe if safe and safe not in {".", ".."} else "unknown"


def _load_ai_report(consultation_id: str) -> dict[str, Any] | None:
    """JSON 파일 → AI report. 파일 없으면 None."""
    safe_cid = _safe_consultation_id(consultation_id)
    target = _resolve_json_root() / f"{safe_cid}.json"
    if not target.exists():
        logger.info("comparison: AI report 파일 없음 — %s", target)
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("comparison: AI report 파싱 실패 cid=%s err=%s", consultation_id, exc)
        return None


# ---------------------------------------------------------------------------
# AI report 파싱
# ---------------------------------------------------------------------------


def _extract_ai_items(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """payload → {item_number: {score, max_score, judgment, category_id}}.

    payload 구조 (queue_populator 저장):
      {
        "consultation_id": ...,
        "report": {"evaluation": {"categories": [{"items": [...]}]}}
      }
    """
    report = payload.get("report") if isinstance(payload, dict) else None
    if not isinstance(report, dict):
        return {}
    evaluation = report.get("evaluation") or {}
    categories = evaluation.get("categories") or []
    if not isinstance(categories, list):
        return {}

    items: dict[int, dict[str, Any]] = {}
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        cat_key = cat.get("category_key") or ""
        for it in cat.get("items") or []:
            if not isinstance(it, dict):
                continue
            num_raw = it.get("item_number")
            try:
                num = int(num_raw) if num_raw is not None else None
            except (TypeError, ValueError):
                num = None
            if num is None or num in items:
                continue
            items[num] = {
                "score": it.get("score"),
                "max_score": it.get("max_score"),
                "judgment": it.get("judgment") or "",
                "category_id": cat_key or _ITEM_TO_CATEGORY.get(num, ""),
            }
    return items


# ---------------------------------------------------------------------------
# 사람 정답 조회
# ---------------------------------------------------------------------------


def _load_human_reviews(consultation_id: str) -> dict[int, dict[str, Any]]:
    """human_reviews 에서 status='confirmed' AND human_score IS NOT NULL 만 조회.

    동일 (cid, item_number) UNIQUE 제약으로 1행만 반환됨 — dict[item_number] 매핑.
    """
    sql = """
    SELECT item_number, human_score, human_note, reviewer_id, confirmed_at
      FROM human_reviews
     WHERE consultation_id = ?
       AND status = 'confirmed'
       AND human_score IS NOT NULL
    """
    out: dict[int, dict[str, Any]] = {}
    try:
        with get_conn() as conn:
            rows = conn.execute(sql, (consultation_id,)).fetchall()
    except sqlite3.Error as exc:
        logger.warning("comparison: HITL DB 조회 실패 cid=%s err=%s", consultation_id, exc)
        return out

    for row in rows:
        try:
            num = int(row["item_number"])
        except (TypeError, ValueError, KeyError):
            continue
        out[num] = {
            "human_score": float(row["human_score"]),
            "human_note": (row["human_note"] or "") if "human_note" in row.keys() else "",
            "reviewer_id": (row["reviewer_id"] or "") if "reviewer_id" in row.keys() else "",
            "confirmed_at": (row["confirmed_at"] or "") if "confirmed_at" in row.keys() else "",
        }
    return out


# ---------------------------------------------------------------------------
# 비교 로직
# ---------------------------------------------------------------------------


def _agreement_for(item_number: int, ai_score: float, human_score: float) -> str:
    """exact / close / diverge 판정.

    - exact   : 동일 점수
    - close   : ALLOWED_STEPS 에서 두 점수가 인접 단계 (1칸 이내)
    - diverge : 그 외 (skip 단계 이상 차이)

    배점이 다른 항목(예: #10 [10,7,5,0], #15 [15,10,5,0]) 도 동일 규칙 적용.
    인접 단계 정의는 ALLOWED_STEPS 에 의존하므로, ALLOWED_STEPS 변경 시 자동 반영됨.
    """
    if ai_score == human_score:
        return "exact"

    steps = ALLOWED_STEPS.get(int(item_number))
    if not steps:
        # 항목이 ALLOWED_STEPS 에 없으면 단순 |Δ|<=1 폴백
        return "close" if abs(ai_score - human_score) <= 1 else "diverge"

    # ALLOWED_STEPS 는 내림차순. 인접 단계 = 인덱스 차이 1
    sorted_steps = sorted(steps)  # 오름차순
    try:
        ai_idx = sorted_steps.index(int(ai_score)) if float(ai_score).is_integer() else -1
        hu_idx = sorted_steps.index(int(human_score)) if float(human_score).is_integer() else -1
    except ValueError:
        ai_idx, hu_idx = -1, -1

    if ai_idx >= 0 and hu_idx >= 0 and abs(ai_idx - hu_idx) <= 1:
        return "close"
    return "diverge"


def _agreement_label(exact_match_rate: float) -> str:
    if exact_match_rate >= 0.95:
        return "perfect"
    if exact_match_rate >= 0.80:
        return "high"
    if exact_match_rate >= 0.60:
        return "moderate"
    return "low"


def _safe_max_score(item_number: int, ai_max: Any) -> float:
    """AI report 에 max_score 가 있으면 그 값, 없으면 ALLOWED_STEPS 기준."""
    try:
        if ai_max is not None:
            return float(ai_max)
    except (TypeError, ValueError):
        pass
    try:
        return float(max_score_of(int(item_number)))
    except KeyError:
        return 0.0


def _compute_summary(items: list[dict[str, Any]], total_items: int) -> dict[str, Any]:
    """비교된 항목 리스트 → 전체 집계 dict."""
    compared = len(items)
    if compared == 0:
        return {
            "compared_count": 0,
            "total_items": int(total_items),
            "exact_match_count": 0,
            "exact_match_rate": 0.0,
            "mae": 0.0,
            "rmse": 0.0,
            "bias": 0.0,
            "mape": None,
            "ai_total": 0.0,
            "human_total": 0.0,
            "ai_normalized": 0.0,
            "human_normalized": 0.0,
            "agreement_label": "low",
        }

    deltas = [it["delta"] for it in items]
    abs_deltas = [it["abs_delta"] for it in items]
    exact = sum(1 for it in items if it["agreement"] == "exact")
    exact_rate = exact / compared

    mae = sum(abs_deltas) / compared
    rmse = math.sqrt(sum(d * d for d in deltas) / compared)
    bias = sum(deltas) / compared

    # MAPE — human_score=0 항목은 분모 0 → 평균에서 제외
    mape_terms = [abs(it["delta"]) / it["human_score"] for it in items if it["human_score"] not in (0, 0.0)]
    mape: float | None
    if mape_terms:
        mape = round((sum(mape_terms) / len(mape_terms)) * 100.0, 2)
    else:
        mape = None

    ai_total = sum(it["ai_score"] for it in items)
    human_total = sum(it["human_score"] for it in items)
    # 비교된 항목들의 max_score 합계 (정규화 분모)
    max_compared = sum(it["max_score"] for it in items)
    if max_compared > 0:
        ai_norm = round((ai_total / max_compared) * 100.0, 2)
        human_norm = round((human_total / max_compared) * 100.0, 2)
    else:
        ai_norm = 0.0
        human_norm = 0.0

    return {
        "compared_count": compared,
        "total_items": int(total_items),
        "exact_match_count": exact,
        "exact_match_rate": round(exact_rate, 4),
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "bias": round(bias, 4),
        "mape": mape,
        "ai_total": round(ai_total, 2),
        "human_total": round(human_total, 2),
        "ai_normalized": ai_norm,
        "human_normalized": human_norm,
        "agreement_label": _agreement_label(exact_rate),
    }


def _compute_by_category(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """카테고리별 mae / bias / exact_match_rate 집계.

    CATEGORY_META 의 정의 순서를 보존하며, 비교된 항목이 없는 카테고리는 제외한다.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        cat_id = it.get("category_id") or ""
        if not cat_id:
            continue
        grouped.setdefault(cat_id, []).append(it)

    out: list[dict[str, Any]] = []
    for cat_id, meta in CATEGORY_META.items():
        bucket = grouped.get(cat_id)
        if not bucket:
            continue
        n = len(bucket)
        deltas = [b["delta"] for b in bucket]
        abs_deltas = [b["abs_delta"] for b in bucket]
        exact = sum(1 for b in bucket if b["agreement"] == "exact")
        out.append(
            {
                "category_id": cat_id,
                "name": meta.get("label_ko") or cat_id,
                "compared": n,
                "mae": round(sum(abs_deltas) / n, 4),
                "bias": round(sum(deltas) / n, 4),
                "exact_match_rate": round(exact / n, 4),
            }
        )
    return out


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------


def _skip_envelope(consultation_id: str, reason: str) -> dict[str, Any]:
    """available=False 응답 일관 셰이프. 비교 데이터 부재 사유만 다름."""
    return {
        "available": False,
        "reason": reason,
        "consultation_id": consultation_id,
        "computed_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "summary": None,
        "by_category": [],
        "items": [],
    }


def compute_comparison(consultation_id: str) -> dict[str, Any]:
    """사람 정답(confirmed) 과 AI 평가의 차이 비교 결과 반환.

    Parameters
    ----------
    consultation_id : str
        상담 ID. JSON 파일명 / DB consultation_id 와 동일.

    Returns
    -------
    dict
        항상 dict 반환 (None 반환 안 함 — 프론트가 200 으로 일관 처리).
        - JSON 파일 자체가 없으면
          ``{"available": False, "reason": "no_ai_report", "summary": None, ...}``.
        - confirmed 사람 정답이 0건이면
          ``{"available": False, "reason": "no_confirmed_reviews", "summary": None, ...}``.
        - 정상 케이스 — 본 모듈 docstring 의 풀 스키마 + ``available: True``.
    """
    payload = _load_ai_report(consultation_id)
    if payload is None:
        return _skip_envelope(consultation_id, "no_ai_report")

    ai_items = _extract_ai_items(payload)
    total_items = len(ai_items)
    human_rows = _load_human_reviews(consultation_id)

    if not human_rows:
        return _skip_envelope(consultation_id, "no_confirmed_reviews")

    compared_items: list[dict[str, Any]] = []
    for item_number, human in human_rows.items():
        ai = ai_items.get(item_number)
        # AI 가 평가하지 않은 항목 (예: skipped) 은 비교 대상에서 제외
        if ai is None:
            continue
        ai_score_raw = ai.get("score")
        try:
            ai_score = float(ai_score_raw) if ai_score_raw is not None else None
        except (TypeError, ValueError):
            ai_score = None
        if ai_score is None:
            continue
        human_score = human["human_score"]
        delta = ai_score - human_score
        abs_delta = abs(delta)
        compared_items.append(
            {
                "item_number": item_number,
                "item_name": _ITEM_NAME_KO.get(item_number, f"항목 {item_number}"),
                "category_id": ai.get("category_id") or _ITEM_TO_CATEGORY.get(item_number, ""),
                "max_score": _safe_max_score(item_number, ai.get("max_score")),
                "ai_score": ai_score,
                "human_score": human_score,
                "delta": round(delta, 4),
                "abs_delta": round(abs_delta, 4),
                "agreement": _agreement_for(item_number, ai_score, human_score),
                "ai_judgment": str(ai.get("judgment") or ""),
                "human_note": human["human_note"],
                "confirmed_at": human["confirmed_at"],
                "reviewer_id": human["reviewer_id"],
            }
        )

    # item_number 오름차순 정렬 — 프론트 표 안정적 렌더링
    compared_items.sort(key=lambda x: x["item_number"])

    summary = _compute_summary(compared_items, total_items=total_items)
    by_category = _compute_by_category(compared_items)

    return {
        "available": True,
        "consultation_id": consultation_id,
        "computed_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "summary": summary,
        "by_category": by_category,
        "items": compared_items,
    }
