# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase E1 (1) V1 ↔ V2 스키마 호환성 분석.

V1 `batch_20260419_160641_iter03_clean/NNNNNN.json` 포맷과 V2 `QAOutputV2`
포맷 간 필드 매핑 분석 + 누락 필드 집계.

V1 형태:
  {"evaluations": [{"agent_id", "evaluation": {item_number, item_name, score,
                     max_score, deductions[], evidence[{turn, speaker, text,
                     relevance}], confidence(float), summary, details}}, ...]}

V2 형태 (Dev5 QAOutputV2.ItemResult):
  {item, item_number, max_score, score, evaluation_mode, judgment,
   evidence[{speaker, timestamp, quote, turn_id}], deductions, confidence{final, signals},
   flag, mandatory_human_review, force_t3}

본 모듈은 LLM 없이 정적으로 스키마 차이를 분석하고 마이그레이션 가이드를 생성.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


# ===========================================================================
# V1 ↔ V2 필드 매핑 테이블
# ===========================================================================

V1_TO_V2_ITEM_FIELDS: dict[str, str] = {
    # 공통 (이름 동일)
    "item_number": "item_number",
    "max_score": "max_score",
    "score": "score",
    "deductions": "deductions",
    # 필드명 변경
    "item_name": "item",
    "summary": "judgment",
    # 스케일 변경
    "confidence": "confidence.final",  # V1 float(0~1) → V2 int(1~5)
}

V1_TO_V2_EVIDENCE_FIELDS: dict[str, str] = {
    "turn": "turn_id",
    "speaker": "speaker",
    "text": "quote",
    # V1 relevance 는 V2 에서 item.judgment 로 통합 (evidence 별 사유 분리 없음)
}

# V2 신규 (V1 에 없음) — 필수/선택 구분
V2_NEW_REQUIRED_FIELDS: tuple[str, ...] = (
    "evaluation_mode",          # V1: 없음, V2: 필수 (full/structural_only/...)
)

V2_NEW_OPTIONAL_FIELDS: tuple[str, ...] = (
    "flag",                      # HITL flag 사유
    "mandatory_human_review",    # bool
    "force_t3",                  # #9/#17/#18 True
)

V1_LEGACY_FIELDS_DROPPED: tuple[str, ...] = (
    "evidence[].relevance",       # V2 에서 evidence 단위 개별 relevance 제거
    "details",                    # V1 {"backend", "llm_based"} — V2 diagnostics 로 이관
)


# ===========================================================================
# 단일 item 호환성 분석
# ===========================================================================


def check_item_schema_compat(v1_item: dict[str, Any]) -> dict[str, Any]:
    """V1 단일 item 을 V2 스키마 기준으로 검증.

    Parameters
    ----------
    v1_item : dict
        V1 evaluations[i].evaluation 구조.

    Returns
    -------
    dict
        {
          "item_number": int,
          "v1_fields_present": list[str],
          "mapped_to_v2": list[{"v1": str, "v2": str}],
          "missing_v2_required": list[str],   # V2 에서 필요한데 V1 에 없음
          "v1_dropped_fields": list[str],     # V1 에 있지만 V2 에서 drop
          "evidence_count": int,
          "evidence_missing_v2_fields": list[str],
          "confidence_scale_mismatch": bool,  # V1 float 확인
        }
    """
    report: dict[str, Any] = {
        "item_number": v1_item.get("item_number"),
        "v1_fields_present": sorted(v1_item.keys()),
        "mapped_to_v2": [],
        "missing_v2_required": [],
        "v1_dropped_fields": [],
        "evidence_count": len(v1_item.get("evidence") or []),
        "evidence_missing_v2_fields": [],
        "confidence_scale_mismatch": False,
    }

    # 1. 매핑 테이블 기반 V1 → V2 변환 가능 필드
    for v1_key, v2_key in V1_TO_V2_ITEM_FIELDS.items():
        if v1_key in v1_item:
            report["mapped_to_v2"].append({"v1": v1_key, "v2": v2_key})

    # 2. V2 신규 필수 필드 — V1 에 없는 것
    for field in V2_NEW_REQUIRED_FIELDS:
        report["missing_v2_required"].append(field)  # V1 에는 항상 없음

    # 3. V1 drop 필드
    if "details" in v1_item:
        report["v1_dropped_fields"].append("details")
    for ev in v1_item.get("evidence") or []:
        if "relevance" in ev:
            report["v1_dropped_fields"].append("evidence[].relevance")
            break

    # 4. Evidence 필드 gap
    for ev in v1_item.get("evidence") or []:
        missing = set(V1_TO_V2_EVIDENCE_FIELDS.keys()) - set(ev.keys())
        if missing:
            report["evidence_missing_v2_fields"].extend(sorted(missing))
        # V2 신규 timestamp 필드 — V1 에는 없음
        if "timestamp" not in ev:
            report["evidence_missing_v2_fields"].append("timestamp")
            break

    # 5. Confidence scale — V1 은 float (0~1), V2 는 int (1~5)
    v1_conf = v1_item.get("confidence")
    if isinstance(v1_conf, float):
        report["confidence_scale_mismatch"] = True

    return report


# ===========================================================================
# 전체 배치 호환성 분석
# ===========================================================================


def analyze_schema_compat(v1_batch_dir: Path | str) -> dict[str, Any]:
    """V1 배치 결과 디렉토리 전체를 분석.

    Parameters
    ----------
    v1_batch_dir : Path
        V1 배치 JSON 파일들이 있는 디렉토리.

    Returns
    -------
    dict
        샘플별 + 전체 집계 리포트.
    """
    batch_dir = Path(v1_batch_dir)
    if not batch_dir.exists():
        raise FileNotFoundError(f"batch_dir not found: {batch_dir}")

    sample_reports: dict[str, list[dict[str, Any]]] = {}
    aggregate: dict[str, int] = {
        "total_samples": 0,
        "total_items": 0,
        "items_with_confidence_mismatch": 0,
        "items_missing_v2_required": 0,
        "evidence_missing_timestamp": 0,
        "evidence_missing_turn_id_name": 0,
        "items_with_dropped_details": 0,
    }

    for json_file in sorted(batch_dir.glob("*.json")):
        if json_file.name.startswith("_"):
            continue
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("JSON 파싱 실패 %s: %s", json_file.name, e)
            continue

        sample_id = json_file.stem
        aggregate["total_samples"] += 1
        per_item_reports = []

        for eval_entry in data.get("evaluations") or []:
            v1_item = eval_entry.get("evaluation") or {}
            report = check_item_schema_compat(v1_item)
            per_item_reports.append(report)

            aggregate["total_items"] += 1
            if report["confidence_scale_mismatch"]:
                aggregate["items_with_confidence_mismatch"] += 1
            if report["missing_v2_required"]:
                aggregate["items_missing_v2_required"] += 1
            if "timestamp" in report["evidence_missing_v2_fields"]:
                aggregate["evidence_missing_timestamp"] += 1
            if any(f != "timestamp" for f in report["evidence_missing_v2_fields"]):
                aggregate["evidence_missing_turn_id_name"] += 1
            if "details" in report["v1_dropped_fields"]:
                aggregate["items_with_dropped_details"] += 1

        sample_reports[sample_id] = per_item_reports

    logger.info(
        "analyze_schema_compat: %d samples / %d items analyzed",
        aggregate["total_samples"], aggregate["total_items"],
    )

    return {
        "batch_dir": str(batch_dir),
        "aggregate": aggregate,
        "samples": sample_reports,
        "migration_notes": _build_migration_notes(aggregate),
    }


# ===========================================================================
# 마이그레이션 가이드 생성
# ===========================================================================


def _build_migration_notes(aggregate: dict[str, int]) -> list[str]:
    """집계값 기반 마이그레이션 우선순위 가이드."""
    notes: list[str] = []
    total = aggregate["total_items"] or 1

    if aggregate["items_with_confidence_mismatch"] > 0:
        pct = aggregate["items_with_confidence_mismatch"] * 100.0 / total
        notes.append(
            f"[필수] V1 confidence float(0~1) → V2 int(1~5) 스케일 변환 필요 "
            f"({aggregate['items_with_confidence_mismatch']}/{total} 항목, {pct:.1f}%). "
            f"변환 규칙 예: round(v1 * 5) with clamp [1,5]."
        )

    if aggregate["items_missing_v2_required"] > 0:
        notes.append(
            f"[필수] V2 evaluation_mode 필드 V1 전체 누락 "
            f"({aggregate['items_missing_v2_required']}/{total} 항목). "
            f"기본값 'full' 부여 후 #9/#17/#18 은 'structural_only'/'compliance_based' 로 재지정."
        )

    if aggregate["evidence_missing_timestamp"] > 0:
        notes.append(
            f"[권장] V2 evidence.timestamp 필드 V1 에 없음 "
            f"({aggregate['evidence_missing_timestamp']}/{total} 항목). "
            f"V1 샘플에는 STT timestamp 미포함 — timestamp='' 빈문자열로 폴백."
        )

    if aggregate["items_with_dropped_details"] > 0:
        notes.append(
            f"[정보] V1 details {{'backend', 'llm_based'}} V2 drop — "
            f"QAOutputV2.diagnostics 로 이관 가능."
        )

    if not notes:
        notes.append("스키마 호환성 양호 — 별도 변환 작업 불필요.")
    return notes
