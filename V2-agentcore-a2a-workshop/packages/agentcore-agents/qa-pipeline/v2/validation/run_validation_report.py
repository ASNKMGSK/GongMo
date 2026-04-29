# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase E1 통합 리포트 생성기.

V1 배치 결과 + (선택) V2 배치 결과 디렉토리를 받아 6 항목 검증 리포트를
`v2/validation/reports/{timestamp}/` 에 생성.

생성 파일:
  - 01_schema_compat.json   (V1 → V2 스키마 호환성)
  - 02_score_drift.json     (V1 vs V2 drift, V2 배치 있을 때만)
  - 03_tier_distribution.json
  - 04_confidence_calibration.json
  - 05_evidence_quality.json
  - 06_evaluation_mode_freq.json
  - summary.md              (전체 요약)

사용:
  python v2/validation/run_validation_report.py \
      --v1-dir "C:/Users/META M/Desktop/프롬프트 튜닝/batch_20260419_160641_iter03_clean" \
      --v2-dir "C:/Users/META M/Desktop/프롬프트 튜닝/batch_YYYYMMDD_HHMMSS_v2_direct"

V2 배치 미실행 시 --v2-dir 생략 가능 — V1 스키마 호환성만 분석.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


_VALIDATION_ROOT = Path(__file__).resolve().parent
_QA_PIPELINE_ROOT = _VALIDATION_ROOT.parent.parent
if str(_QA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_QA_PIPELINE_ROOT))


from v2.validation.schema_compat import analyze_schema_compat  # noqa: E402
from v2.validation.score_drift import (  # noqa: E402
    analyze_confidence_calibration,
    analyze_evaluation_mode_frequency,
    analyze_evidence_quality,
    analyze_tier_distribution,
    compute_drift_report,
    load_batch_results,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("written: %s", path)


def _build_summary_md(
    schema_report: dict[str, Any],
    drift_report: dict[str, Any] | None,
    tier_dist: dict[str, Any] | None,
    conf_calib: dict[str, Any] | None,
    evidence_quality: dict[str, Any] | None,
    mode_freq: dict[str, int] | None,
    v1_dir: str,
    v2_dir: str | None,
) -> str:
    lines = [
        "# Phase E1 V2 Validation Report",
        f"- V1 batch: `{v1_dir}`",
        f"- V2 batch: `{v2_dir if v2_dir else '— (V2 배치 미실행, V1 분석만 수행)'}`",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 1. Schema Compatibility (V1 → V2)",
    ]
    agg = schema_report.get("aggregate", {})
    lines.append(f"- Samples: {agg.get('total_samples', 0)} / Items: {agg.get('total_items', 0)}")
    lines.append(f"- Confidence scale mismatch: {agg.get('items_with_confidence_mismatch', 0)} items")
    lines.append(f"- Missing V2 required (evaluation_mode): {agg.get('items_missing_v2_required', 0)} items")
    lines.append(f"- Evidence missing timestamp: {agg.get('evidence_missing_timestamp', 0)} items")
    lines.append(f"- V1 dropped fields (details): {agg.get('items_with_dropped_details', 0)} items")
    lines.append("")
    lines.append("### Migration notes")
    for note in schema_report.get("migration_notes", []):
        lines.append(f"- {note}")
    lines.append("")

    if drift_report:
        lines.append("## 2. Score Drift (V1 vs V2)")
        lines.append(f"- Common samples: {len(drift_report['common_samples'])}")
        lines.append(f"- V1-only: {len(drift_report['v1_only_samples'])}")
        lines.append(f"- V2-only: {len(drift_report['v2_only_samples'])}")
        overall = drift_report["overall"]
        lines.append(
            f"- **Overall** (n={overall['n']}): "
            f"MAE={overall['MAE']} · RMSE={overall['RMSE']} · Bias={overall['Bias']} · "
            f"MAPE={overall['MAPE']}% · Accuracy={overall['Accuracy']}"
        )
        lines.append("")
        lines.append("### Per-item metrics (MAE / MAPE / Accuracy)")
        lines.append("| item | n | MAE | RMSE | Bias | MAPE | Acc |")
        lines.append("|---|---|---|---|---|---|---|")
        for item_num in sorted(drift_report["per_item"].keys()):
            m = drift_report["per_item"][item_num]
            lines.append(
                f"| #{item_num} | {m['n']} | {m['MAE']} | {m['RMSE']} | "
                f"{m['Bias']} | {m['MAPE']}% | {m['Accuracy']} |"
            )
        lines.append("")

    if tier_dist:
        lines.append("## 3. Tier Distribution (V2)")
        for tier, count in tier_dist["counts"].items():
            pct = tier_dist["percent"].get(tier, 0.0)
            lines.append(f"- {tier}: {count} ({pct}%)")
        lines.append(f"- Target: {tier_dist.get('target_v2')}")
        lines.append("")

    if conf_calib:
        lines.append("## 4. Confidence Calibration (V2)")
        lines.append(f"- Distribution: {conf_calib['distribution']}")
        lines.append(f"- Low confidence (≤2) items: {conf_calib['low_confidence_count']}")
        lines.append("")

    if evidence_quality:
        lines.append("## 5. Evidence Quality")
        lines.append(f"- Total items: {evidence_quality['total_items']}")
        lines.append(
            f"- Empty evidence: {evidence_quality['empty_evidence_count']} "
            f"({evidence_quality['empty_evidence_rate']}%)"
        )
        lines.append(f"- Avg quote length: {evidence_quality['avg_evidence_text_length']} chars")
        lines.append(f"- Speaker mismatch: {evidence_quality['speaker_mismatch_count']}")
        lines.append("")

    if mode_freq:
        lines.append("## 6. Evaluation Mode Frequency (V2)")
        total = sum(mode_freq.values()) or 1
        for mode, count in mode_freq.items():
            lines.append(f"- {mode}: {count} ({round(count * 100.0 / total, 1)}%)")
        lines.append("")

    lines.append("---")
    lines.append("*지표 제한: MAE/RMSE/Bias/MAPE/Accuracy 만 사용. Pearson/Spearman/κ/R² 금지 (CLAUDE.md).*")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-dir", required=True, help="V1 iter03_clean 배치 디렉토리")
    parser.add_argument("--v2-dir", default=None, help="V2 배치 디렉토리 (선택)")
    parser.add_argument("--output", default=None, help="리포트 출력 디렉토리 (기본 v2/validation/reports/{ts}/)")
    args = parser.parse_args()

    v1_dir = Path(args.v1_dir)
    v2_dir = Path(args.v2_dir) if args.v2_dir else None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output) if args.output else _VALIDATION_ROOT / "reports" / ts
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("output: %s", output_dir)

    # 1. Schema compat
    schema_report = analyze_schema_compat(v1_dir)
    _write_json(output_dir / "01_schema_compat.json", schema_report)

    drift_report: dict[str, Any] | None = None
    tier_dist: dict[str, Any] | None = None
    conf_calib: dict[str, Any] | None = None
    evidence_quality: dict[str, Any] | None = None
    mode_freq: dict[str, int] | None = None

    if v2_dir and v2_dir.exists():
        # 2. Score drift
        v1_results = load_batch_results(v1_dir)
        v2_results = load_batch_results(v2_dir)
        drift_report = compute_drift_report(v1_results, v2_results)
        _write_json(output_dir / "02_score_drift.json", drift_report)

        # 3. Tier distribution
        tier_dist = analyze_tier_distribution(v2_dir)
        _write_json(output_dir / "03_tier_distribution.json", tier_dist)

        # 4. Confidence calibration
        conf_calib = analyze_confidence_calibration(v2_dir)
        _write_json(output_dir / "04_confidence_calibration.json", conf_calib)

        # 6. Evaluation mode frequency
        mode_freq = analyze_evaluation_mode_frequency(v2_dir)
        _write_json(output_dir / "06_evaluation_mode_freq.json", mode_freq)
        # 5. Evidence quality (V2 기준)
        evidence_quality = analyze_evidence_quality(v2_dir)
    else:
        logger.warning("V2 디렉토리 미지정/부재 — V1 스키마 호환성만 분석")
        # V1 evidence quality 도 유효 — 리포트 참고용
        evidence_quality = analyze_evidence_quality(v1_dir)

    _write_json(output_dir / "05_evidence_quality.json", evidence_quality)

    # 7. Summary.md
    summary = _build_summary_md(
        schema_report, drift_report, tier_dist, conf_calib,
        evidence_quality, mode_freq,
        v1_dir=str(v1_dir), v2_dir=str(v2_dir) if v2_dir else None,
    )
    (output_dir / "summary.md").write_text(summary, encoding="utf-8")
    logger.info("written: %s/summary.md", output_dir)
    print(f"\n[SUCCESS] Report generated: {output_dir}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
