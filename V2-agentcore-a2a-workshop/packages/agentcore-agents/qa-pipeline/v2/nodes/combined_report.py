# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""두 평가 분기 (기존 #1~#18 / KSQI 9 항목) 의 결과를 한 artifact 로 묶는 통합 보고서 노드.

Layer 3/4 체인 (hitl_queue_populator) 와 KSQI 체인 (ksqi_report) 양쪽 모두 종료된 후 실행.
두 보고서는 채점 체계가 다르므로 합치지 않고 sub-section 으로 분리 보존.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _summarize_existing(report: dict[str, Any] | None) -> dict[str, Any]:
    """기존 18 항목 보고서에서 grade / total_score / items 를 발췌."""
    if not isinstance(report, dict):
        return {"available": False}
    summary = report.get("summary") or {}
    items = report.get("items") or report.get("evaluations") or []
    return {
        "available": True,
        "grade": summary.get("grade"),
        "total_score": summary.get("total_score") or summary.get("score_total"),
        "max_score": summary.get("max_score") or summary.get("score_max"),
        "item_count": len(items) if isinstance(items, list) else 0,
        "raw": report,
    }


def _summarize_ksqi(ksqi: dict[str, Any] | None) -> dict[str, Any]:
    """KSQI 보고서 — 그대로 인용 (이미 area_a/area_b/overall 구조)."""
    if not isinstance(ksqi, dict):
        return {"available": False}
    return {"available": True, **ksqi}


def _build_summary_line(existing: dict[str, Any], ksqi: dict[str, Any]) -> str:
    """양쪽 보고서를 한 문장으로 요약."""
    parts: list[str] = []
    if existing.get("available"):
        grade = existing.get("grade") or "-"
        total = existing.get("total_score")
        max_s = existing.get("max_score")
        if total is not None and max_s is not None:
            parts.append(f"기존 평가 {total}/{max_s} ({grade})")
        else:
            parts.append(f"기존 평가 {grade}")
    if ksqi.get("available"):
        a = ksqi.get("area_a") or {}
        b = ksqi.get("area_b") or {}
        parts.append(
            f"KSQI A {a.get('scaled', '-')}점·{a.get('grade', '-')} / "
            f"B {b.get('scaled', '-')}점·{b.get('grade', '-')}"
        )
    if not parts:
        return "보고서 데이터 없음"
    return " · ".join(parts)


def combined_report_node(state: dict[str, Any]) -> dict[str, Any]:
    """두 분기의 보고서를 한 artifact 로 통합.

    LangGraph 는 다중 부모 (hitl_queue_populator + ksqi_report) 중 어느 쪽이 fire 되든
    이 노드를 활성화하므로, 두 분기가 다른 superstep 에 끝나면 노드가 두 번 발화될 수 있다.
    아래 가드로 양쪽 보고서가 모두 도착할 때까지 no-op → 다음 발화에서 정상 통합.

    예외: skip_phase_c_and_reporting=True 케이스에서는 layer4 chain 이 스킵되므로
    state.report 가 없는 게 정상 → ksqi_report 도착만으로 진행.
    """
    # 이미 한 번 생성됐으면 skip (LangGraph 의 다중 부모 fan-in 으로 같은 superstep 에 두 번 fire 되는 것 방지).
    if state.get("combined_report"):
        logger.info("combined_report: 이미 생성됨 — 중복 발화 skip")
        return {}

    plan = state.get("plan") or {}
    skip_layer4 = bool(plan.get("skip_phase_c_and_reporting"))

    raw_existing = state.get("report")
    raw_ksqi = state.get("ksqi_report")

    # KSQI 분기는 어떤 경우에도 도착해야 함 (병렬 분기 항상 활성).
    if not raw_ksqi:
        logger.info("combined_report: ksqi_report 미도착 — 다음 발화 대기")
        return {}

    # Layer4 chain 분기 도착 감지:
    #  1) state.report 가 채워졌거나 (정상 케이스)
    #  2) hitl_queue_populated 가 set 되었으면 (crash 등으로 state.report 가 비어있어도 chain 은 종료)
    layer4_chain_done = bool(raw_existing) or (state.get("hitl_queue_populated") is not None)
    if not skip_layer4 and not layer4_chain_done:
        logger.info("combined_report: layer4 chain 미도착 — 다음 발화 대기")
        return {}

    existing = _summarize_existing(raw_existing)
    ksqi = _summarize_ksqi(raw_ksqi)

    combined: dict[str, Any] = {
        "consultation_id": state.get("consultation_id") or state.get("session_id") or "",
        "tenant": {
            "site_id": state.get("site_id") or "generic",
            "channel": state.get("channel") or "inbound",
            "department": state.get("department") or "default",
        },
        "evaluated_at": state.get("evaluated_at") or "",
        "existing": existing,
        "ksqi": ksqi,
        "summary": _build_summary_line(existing, ksqi),
    }
    logger.info("combined_report: %s", combined["summary"])
    return {"combined_report": combined}


__all__ = ["combined_report_node"]
