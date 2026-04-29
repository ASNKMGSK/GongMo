# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase E1 (2) 점수 drift 측정.

V1 vs V2 배치 결과 간 항목별 MAE/RMSE/Bias/MAPE/Accuracy 계산.
CLAUDE.md 지침에 따라 **Pearson/Spearman/κ/R² 금지**.

사용법:
    v1_results = load_batch_results("C:/.../batch_20260419_160641_iter03_clean")
    v2_results = load_batch_results("C:/.../batch_YYYYMMDD_HHMMSS_v2_direct")
    report = compute_drift_report(v1_results, v2_results)
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


# ===========================================================================
# 통합 item 순회 제너레이터 (V1 / V2 / QAOutputV2 3 경로 — Dev5 리뷰 2026-04-20)
# ===========================================================================


def _iter_items(data: dict) -> Iterator[dict]:
    """배치 JSON 에서 item dict 를 3 경로 통합 순회.

    경로:
      1. V1 호환: `data["evaluations"][i]["evaluation"]`
      2. V2 Dev1 Layer 3: `data["orchestrator"]["final_evaluations"]`
      3. V2 Dev5 QAOutputV2: `data["evaluation"]["categories"][j]["items"]`

    중복 방지를 위해 item_number 기준 first-seen 우선 (Dev1/Dev5 경로가 동일 데이터를
    다른 루트에서 노출할 수 있음).
    """
    seen_item_numbers: set[int] = set()

    def _emit(item: Any) -> Iterator[dict]:
        if not isinstance(item, dict):
            return
        num = item.get("item_number")
        if isinstance(num, int):
            if num in seen_item_numbers:
                return
            seen_item_numbers.add(num)
        yield item

    # 1) V1 호환 — evaluations[i].evaluation 우선 (가장 널리 쓰이는 포맷)
    for e in data.get("evaluations") or []:
        if isinstance(e, dict):
            inner = e.get("evaluation") or e
            yield from _emit(inner)

    # 2) V2 Dev1 Layer 3 — orchestrator.final_evaluations
    for item in (data.get("orchestrator") or {}).get("final_evaluations") or []:
        yield from _emit(item)

    # 3) V2 Dev5 QAOutputV2 — evaluation.categories[].items[]
    for cat in (data.get("evaluation") or {}).get("categories") or []:
        if not isinstance(cat, dict):
            continue
        for item in cat.get("items") or []:
            yield from _emit(item)


# ===========================================================================
# 결과 로더
# ===========================================================================


def load_batch_results(batch_dir: Path | str) -> dict[str, dict[int, int]]:
    """배치 디렉토리에서 sample_id → {item_number: score} 매핑 로드.

    V1 / V2 Dev1 Layer 3 / V2 Dev5 QAOutputV2 3 포맷 모두 `_iter_items()` 로 처리.
    """
    batch_dir = Path(batch_dir)
    if not batch_dir.exists():
        raise FileNotFoundError(f"batch_dir not found: {batch_dir}")

    import re as _re
    results: dict[str, dict[int, int]] = {}
    for json_file in sorted(batch_dir.glob("*.json")):
        if json_file.name.startswith("_"):
            continue
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("로드 실패 %s: %s", json_file.name, e)
            continue

        # sample_id 정규화 — V1 "668437.json" / V2 "668437_result.json" / QAOutputV2 일부
        # 파일명 공통 매칭을 위해 앞 6자리 숫자 우선 추출 (없으면 stem 전체).
        _m = _re.match(r"^(\d{6})", json_file.stem)
        sample_id = _m.group(1) if _m else json_file.stem

        item_scores: dict[int, int] = {}
        for item in _iter_items(data):
            item_num = item.get("item_number")
            score = item.get("score")
            # unevaluable 은 score=None → isinstance 체크로 자연 필터
            if isinstance(item_num, int) and isinstance(score, (int, float)):
                item_scores[item_num] = int(score)

        if item_scores:
            results[sample_id] = item_scores

    logger.info("load_batch_results: %d samples loaded from %s", len(results), batch_dir)
    return results


# ===========================================================================
# 항목별 metric 계산
# ===========================================================================


def item_metrics(v1_scores: list[int], v2_scores: list[int]) -> dict[str, float]:
    """V1 vs V2 샘플 쌍 리스트 → metric 계산.

    Returns
    -------
    {
      "n": int,
      "MAE": float,
      "RMSE": float,
      "Bias": float,     # mean(v2 - v1) — V2 가 양수면 V1 대비 점수 상승 경향
      "MAPE": float,     # % — v1==0 케이스는 분모 1 로 처리
      "Accuracy": float, # exact match ratio
    }
    """
    n = min(len(v1_scores), len(v2_scores))
    if n == 0:
        return {"n": 0, "MAE": 0.0, "RMSE": 0.0, "Bias": 0.0, "MAPE": 0.0, "Accuracy": 0.0}

    diffs = [int(v2_scores[i]) - int(v1_scores[i]) for i in range(n)]
    abs_diffs = [abs(d) for d in diffs]
    sq_diffs = [d * d for d in diffs]
    # MAPE — V1 점수 0 인 경우 분모를 1 로 치환하여 과대평가 방지
    mape_terms = [
        abs(diffs[i]) / max(1, abs(int(v1_scores[i]))) * 100.0
        for i in range(n)
    ]

    matches = sum(1 for i in range(n) if int(v1_scores[i]) == int(v2_scores[i]))

    return {
        "n": n,
        "MAE": round(sum(abs_diffs) / n, 3),
        "RMSE": round(math.sqrt(sum(sq_diffs) / n), 3),
        "Bias": round(sum(diffs) / n, 3),
        "MAPE": round(sum(mape_terms) / n, 3),
        "Accuracy": round(matches / n, 3),
    }


# ===========================================================================
# 전체 drift 리포트
# ===========================================================================


def compute_drift_report(
    v1_results: dict[str, dict[int, int]],
    v2_results: dict[str, dict[int, int]],
    item_numbers: list[int] | None = None,
) -> dict[str, Any]:
    """V1/V2 배치 결과 간 drift 계산.

    Parameters
    ----------
    v1_results, v2_results : dict[sample_id, {item_number: score}]
    item_numbers : list[int] | None
        분석 대상 항목. 기본은 1~18 전체.

    Returns
    -------
    dict
        {
          "common_samples": list[str],
          "per_item": {item_number: {MAE, RMSE, Bias, MAPE, Accuracy, n}},
          "overall": {MAE, RMSE, Bias, MAPE, Accuracy, n},
          "per_sample_total": {sample_id: {v1_total, v2_total, diff, abs_diff}},
        }
    """
    item_numbers = item_numbers or list(range(1, 19))
    common = sorted(set(v1_results.keys()) & set(v2_results.keys()))

    per_item: dict[int, dict[str, float]] = {}
    for item_num in item_numbers:
        v1_arr: list[int] = []
        v2_arr: list[int] = []
        for sid in common:
            if item_num in v1_results[sid] and item_num in v2_results[sid]:
                v1_arr.append(v1_results[sid][item_num])
                v2_arr.append(v2_results[sid][item_num])
        per_item[item_num] = item_metrics(v1_arr, v2_arr)

    # Overall — 전 항목 전 샘플 합집합
    all_v1: list[int] = []
    all_v2: list[int] = []
    for sid in common:
        for item_num in item_numbers:
            if item_num in v1_results[sid] and item_num in v2_results[sid]:
                all_v1.append(v1_results[sid][item_num])
                all_v2.append(v2_results[sid][item_num])
    overall = item_metrics(all_v1, all_v2)

    # Per-sample total
    per_sample: dict[str, dict[str, int]] = {}
    for sid in common:
        v1_total = sum(v1_results[sid].get(n, 0) for n in item_numbers)
        v2_total = sum(v2_results[sid].get(n, 0) for n in item_numbers)
        per_sample[sid] = {
            "v1_total": v1_total,
            "v2_total": v2_total,
            "diff": v2_total - v1_total,
            "abs_diff": abs(v2_total - v1_total),
        }

    logger.info(
        "compute_drift_report: %d common samples · overall MAE=%.3f MAPE=%.2f%% Accuracy=%.2f",
        len(common), overall["MAE"], overall["MAPE"], overall["Accuracy"],
    )

    return {
        "common_samples": common,
        "v1_only_samples": sorted(set(v1_results.keys()) - set(v2_results.keys())),
        "v2_only_samples": sorted(set(v2_results.keys()) - set(v1_results.keys())),
        "per_item": per_item,
        "overall": overall,
        "per_sample_total": per_sample,
    }


# ===========================================================================
# Tier 분포 / Confidence calibration / Evidence 품질 — 리포트 항목 5종
# ===========================================================================


def analyze_tier_distribution(v2_results_dir: Path | str) -> dict[str, Any]:
    """V2 배치 결과의 routing.decision / orchestrator.routing_tier_hint 분포."""
    batch_dir = Path(v2_results_dir)
    counts: dict[str, int] = {"T0": 0, "T1": 0, "T2": 0, "T3": 0, "unknown": 0}
    per_sample: list[tuple[str, str]] = []

    for json_file in sorted(batch_dir.glob("*.json")):
        if json_file.name.startswith("_"):
            continue
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        sid = json_file.stem
        tier = (
            (data.get("routing") or {}).get("decision")
            or (data.get("orchestrator") or {}).get("routing_tier_hint")
            or "unknown"
        )
        counts[tier if tier in counts else "unknown"] += 1
        per_sample.append((sid, tier))

    total = sum(counts.values()) or 1
    return {
        "counts": counts,
        "percent": {k: round(v * 100.0 / total, 1) for k, v in counts.items()},
        "per_sample": per_sample,
        "target_v2": {"T0": "~70%", "T1": "5~10%", "T2": "15~20%", "T3": "≤5%"},
    }


def analyze_confidence_calibration(v2_results_dir: Path | str) -> dict[str, Any]:
    """V2 confidence.final (1~5) 분포 + score 정합성 간단 점검.

    경로 우선순위 (Dev5 리뷰 2026-04-20):
      1. `_iter_items` (V1 / V2 Layer 3 / V2 QAOutputV2 items) — ev["confidence"] 사용
      2. `data["diagnostics"]["confidence_map"]` — item_number str key, {final, signals}
         Dev5 `report_generator_v2::_build_confidence_signals_map` 산출. item# 중복 시
         이미 (1) 에서 카운트되었으면 skip.
    """
    batch_dir = Path(v2_results_dir)
    bucket: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, -1: 0}
    low_conf_items: list[dict[str, Any]] = []

    for json_file in sorted(batch_dir.glob("*.json")):
        if json_file.name.startswith("_"):
            continue
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        sid = json_file.stem
        seen_item_nums: set[int] = set()

        # (1) Sub Agent items 경로
        for ev in _iter_items(data):
            item_num = ev.get("item_number")
            if isinstance(item_num, int):
                seen_item_nums.add(item_num)
            conf = ev.get("confidence")
            if isinstance(conf, dict):
                final = conf.get("final", -1)
            elif isinstance(conf, (int, float)):
                # 0~1 float → 1~5 로 변환 (V1 호환)
                final = max(1, min(5, round(float(conf) * 5)))
            else:
                final = -1
            if final in bucket:
                bucket[final] += 1
            else:
                bucket[-1] += 1
            if isinstance(final, int) and 1 <= final <= 2:
                low_conf_items.append({
                    "sample_id": sid,
                    "item_number": item_num,
                    "score": ev.get("score"),
                    "confidence_final": final,
                    "source": "items",
                })

        # (2) diagnostics.confidence_map 경로 — Dev5 report_generator_v2 가 덤프
        cm = (data.get("diagnostics") or {}).get("confidence_map") or {}
        for item_num_str, block in cm.items():
            if not isinstance(block, dict):
                continue
            try:
                item_num = int(item_num_str)
            except (TypeError, ValueError):
                continue
            # 이미 items 경로에서 본 항목은 skip (중복 방지)
            if item_num in seen_item_nums:
                continue
            final = block.get("final", -1)
            try:
                final = int(final)
            except (TypeError, ValueError):
                final = -1
            if final in bucket:
                bucket[final] += 1
            else:
                bucket[-1] += 1
            if 1 <= final <= 2:
                low_conf_items.append({
                    "sample_id": sid,
                    "item_number": item_num,
                    "score": None,  # diagnostics 경로는 score 미보유
                    "confidence_final": final,
                    "source": "diagnostics",
                })

    total = sum(bucket.values()) or 1
    return {
        "distribution": bucket,
        "percent": {str(k): round(v * 100.0 / total, 1) for k, v in bucket.items()},
        "low_confidence_items": low_conf_items[:50],  # 상위 50건만
        "low_confidence_count": len(low_conf_items),
    }


def analyze_evidence_quality(results_dir: Path | str) -> dict[str, Any]:
    """Evidence 누락율 / 길이 / speaker 일치율 — 3 경로 통합 순회."""
    batch_dir = Path(results_dir)
    total_items = 0
    empty_evidence = 0
    speaker_mismatch = 0
    text_lengths: list[int] = []

    for json_file in sorted(batch_dir.glob("*.json")):
        if json_file.name.startswith("_"):
            continue
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for ev in _iter_items(data):
            total_items += 1
            evidence_list = ev.get("evidence") or []
            if len(evidence_list) == 0:
                empty_evidence += 1
                continue
            for quote in evidence_list:
                if not isinstance(quote, dict):
                    continue
                text = quote.get("quote") or quote.get("text") or ""
                text_lengths.append(len(text))
                sp = (quote.get("speaker") or "").lower()
                # speaker 필드 값 정합성 — 상담사/agent/customer/고객 이외는 mismatch 로 카운트
                if sp and not any(k in sp for k in ("agent", "상담", "customer", "고객")):
                    speaker_mismatch += 1

    avg_len = round(sum(text_lengths) / len(text_lengths), 1) if text_lengths else 0.0
    return {
        "total_items": total_items,
        "empty_evidence_count": empty_evidence,
        "empty_evidence_rate": round(empty_evidence * 100.0 / max(1, total_items), 2),
        "speaker_mismatch_count": speaker_mismatch,
        "avg_evidence_text_length": avg_len,
        "evidence_quote_count": len(text_lengths),
    }


def analyze_evaluation_mode_frequency(v2_results_dir: Path | str) -> dict[str, int]:
    """V2 evaluation_mode 6종 등장 빈도 — 3 경로 통합 순회."""
    batch_dir = Path(v2_results_dir)
    counts: dict[str, int] = {
        "full": 0, "structural_only": 0, "compliance_based": 0,
        "partial_with_review": 0, "skipped": 0, "unevaluable": 0, "unknown": 0,
    }
    for json_file in sorted(batch_dir.glob("*.json")):
        if json_file.name.startswith("_"):
            continue
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for ev in _iter_items(data):
            mode = ev.get("evaluation_mode") or "unknown"
            counts[mode if mode in counts else "unknown"] += 1
    return counts
