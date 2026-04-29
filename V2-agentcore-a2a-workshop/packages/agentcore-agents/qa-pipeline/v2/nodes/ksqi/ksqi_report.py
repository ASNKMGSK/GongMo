# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""KSQI 최종 보고서 — 영역별 환산 점수 + 우수 콜센터 판정."""

from __future__ import annotations

import logging
from typing import Any

from .ksqi_rules import AREA_MAX, EXCELLENT_THRESHOLD, KSQI_RULES, get_rule

logger = logging.getLogger(__name__)


def _scaled_score(raw: int, max_total: int) -> float:
    """환산 점수 = (획득 / 배점합계) × 100."""
    if max_total <= 0:
        return 0.0
    return round(raw / max_total * 100, 1)


def _grade_for(area: str, scaled: float) -> str:
    """우수 콜센터 판정 — A 92↑ / B 80↑ 이면 '우수', 미만은 '일반'."""
    threshold = EXCELLENT_THRESHOLD.get(area, 100)
    return "우수" if scaled >= threshold else "일반"


def ksqi_report_node(state: dict[str, Any]) -> dict[str, Any]:
    """KSQI 9개 평가 결과 → 영역별 환산 + 판정."""
    items: list[dict[str, Any]] = state.get("ksqi_evaluations") or []

    # item_number 기준 정렬 + 누락 항목 0점 보강
    by_number: dict[int, dict[str, Any]] = {}
    for it in items:
        try:
            n = int(it.get("item_number"))
            by_number[n] = it
        except (TypeError, ValueError):
            continue

    sorted_items: list[dict[str, Any]] = []
    raw_by_area: dict[str, int] = {"A": 0, "B": 0}
    for rule in KSQI_RULES:
        n = rule["item_number"]
        ev = by_number.get(n)
        if ev is None:
            ev = {
                "item_number": n,
                "item_name": rule["item_name"],
                "area": rule["area"],
                "max_score": rule["max_score"],
                "score": 0,
                "defect": True,
                "evidence": [],
                "rationale": "평가 누락 — 노드 미실행",
            }
        sorted_items.append(ev)
        try:
            raw_by_area[rule["area"]] += int(ev.get("score", 0))
        except (TypeError, ValueError):
            pass

    area_a = {
        "raw": raw_by_area["A"],
        "max": AREA_MAX["A"],
        "scaled": _scaled_score(raw_by_area["A"], AREA_MAX["A"]),
    }
    area_a["grade"] = _grade_for("A", area_a["scaled"])

    area_b = {
        "raw": raw_by_area["B"],
        "max": AREA_MAX["B"],
        "scaled": _scaled_score(raw_by_area["B"], AREA_MAX["B"]),
    }
    area_b["grade"] = _grade_for("B", area_b["scaled"])

    overall_total = raw_by_area["A"] + raw_by_area["B"]
    overall_max = AREA_MAX["A"] + AREA_MAX["B"]

    summary = (
        f"KSQI 종합: A {area_a['raw']}/{area_a['max']} ({area_a['scaled']}점, {area_a['grade']}) · "
        f"B {area_b['raw']}/{area_b['max']} ({area_b['scaled']}점, {area_b['grade']}) · "
        f"전체 {overall_total}/{overall_max}"
    )
    logger.info("ksqi_report: %s", summary)

    report = {
        "area_a": area_a,
        "area_b": area_b,
        "overall": {"raw": overall_total, "max": overall_max},
        "items": sorted_items,
        "summary": summary,
    }
    return {"ksqi_report": report}


__all__ = ["ksqi_report_node", "get_rule"]
