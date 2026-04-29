# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase E1 validation 도구 smoke tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


_QA_PIPELINE_ROOT = Path(__file__).resolve().parents[2]
if str(_QA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_QA_PIPELINE_ROOT))


# ---------------------------------------------------------------------------
# schema_compat
# ---------------------------------------------------------------------------


def test_check_item_schema_compat_detects_confidence_mismatch():
    from v2.validation.schema_compat import check_item_schema_compat

    v1_item = {
        "item_number": 1,
        "item_name": "첫인사",
        "score": 5,
        "max_score": 5,
        "confidence": 0.95,   # float → mismatch
        "evidence": [{"turn": 1, "speaker": "agent", "text": "안녕"}],
        "summary": "mock",
    }
    report = check_item_schema_compat(v1_item)
    assert report["confidence_scale_mismatch"] is True
    assert report["item_number"] == 1


def test_check_item_schema_compat_flags_missing_evaluation_mode():
    from v2.validation.schema_compat import check_item_schema_compat

    v1_item = {"item_number": 2, "score": 3, "max_score": 5, "evidence": []}
    report = check_item_schema_compat(v1_item)
    assert "evaluation_mode" in report["missing_v2_required"]


def test_check_item_schema_compat_detects_dropped_relevance():
    from v2.validation.schema_compat import check_item_schema_compat

    v1_item = {
        "item_number": 3,
        "score": 5,
        "max_score": 5,
        "evidence": [{"turn": 1, "speaker": "agent", "text": "x", "relevance": "foo"}],
    }
    report = check_item_schema_compat(v1_item)
    assert "evidence[].relevance" in report["v1_dropped_fields"]


def test_analyze_schema_compat_on_iter03_clean_dir(tmp_path):
    from v2.validation.schema_compat import analyze_schema_compat

    # mini 배치 1개 샘플 합성
    sample = {
        "evaluations": [
            {"agent_id": "greeting-agent", "evaluation": {
                "item_number": 1, "item_name": "첫인사", "score": 5, "max_score": 5,
                "confidence": 0.95, "evidence": [{"turn": 1, "speaker": "agent", "text": "x"}],
                "summary": "mock",
            }}
        ]
    }
    (tmp_path / "12345.json").write_text(json.dumps(sample), encoding="utf-8")

    report = analyze_schema_compat(tmp_path)
    assert report["aggregate"]["total_samples"] == 1
    assert report["aggregate"]["total_items"] == 1
    assert report["aggregate"]["items_with_confidence_mismatch"] == 1


# ---------------------------------------------------------------------------
# score_drift
# ---------------------------------------------------------------------------


def test_item_metrics_perfect_agreement():
    from v2.validation.score_drift import item_metrics

    m = item_metrics([5, 3, 5, 0, 5], [5, 3, 5, 0, 5])
    assert m["n"] == 5
    assert m["MAE"] == 0.0
    assert m["RMSE"] == 0.0
    assert m["Bias"] == 0.0
    assert m["Accuracy"] == 1.0


def test_item_metrics_with_drift():
    from v2.validation.score_drift import item_metrics

    # V1 모두 5, V2 모두 3 → MAE=2, Bias=-2, Accuracy=0
    m = item_metrics([5, 5, 5], [3, 3, 3])
    assert m["MAE"] == 2.0
    assert m["Bias"] == -2.0
    assert m["Accuracy"] == 0.0
    assert m["MAPE"] == 40.0  # (2/5)*100


def test_item_metrics_handles_zero_v1():
    from v2.validation.score_drift import item_metrics

    # V1=0 케이스에서 MAPE 분모 폴백 (max(1, 0)=1) 동작 확인
    m = item_metrics([0, 0], [3, 0])
    assert m["n"] == 2
    # diff = [3, 0] → MAPE = (3/1 + 0/1) / 2 * 100 = 150
    assert m["MAPE"] == 150.0


def test_compute_drift_report_structure():
    from v2.validation.score_drift import compute_drift_report

    v1 = {"sampleA": {1: 5, 2: 3}, "sampleB": {1: 0, 2: 5}}
    v2 = {"sampleA": {1: 5, 2: 0}, "sampleB": {1: 3, 2: 5}}

    report = compute_drift_report(v1, v2, item_numbers=[1, 2])
    assert len(report["common_samples"]) == 2
    assert 1 in report["per_item"]
    assert 2 in report["per_item"]
    # Overall: V1=[5,0,3,5], V2=[5,3,0,5], diffs=[0,3,-3,0] → MAE=1.5, Bias=0
    assert report["overall"]["MAE"] == 1.5
    assert report["overall"]["Bias"] == 0.0
    # Per-sample total
    assert report["per_sample_total"]["sampleA"]["diff"] == -3
    assert report["per_sample_total"]["sampleB"]["diff"] == 3


# ---------------------------------------------------------------------------
# tier / confidence / evidence / mode freq
# ---------------------------------------------------------------------------


def test_analyze_tier_distribution_empty_dir(tmp_path):
    from v2.validation.score_drift import analyze_tier_distribution

    result = analyze_tier_distribution(tmp_path)
    assert result["counts"]["T0"] == 0
    assert result["per_sample"] == []


def test_analyze_evidence_quality_counts_empty(tmp_path):
    from v2.validation.score_drift import analyze_evidence_quality

    sample = {
        "evaluations": [
            {"evaluation": {"evidence": []}},
            {"evaluation": {"evidence": [{"quote": "hello", "speaker": "agent"}]}},
        ]
    }
    (tmp_path / "01.json").write_text(json.dumps(sample), encoding="utf-8")
    result = analyze_evidence_quality(tmp_path)
    assert result["total_items"] == 2
    assert result["empty_evidence_count"] == 1


def test_analyze_evaluation_mode_frequency(tmp_path):
    from v2.validation.score_drift import analyze_evaluation_mode_frequency

    sample = {
        "evaluations": [
            {"evaluation": {"evaluation_mode": "full"}},
            {"evaluation": {"evaluation_mode": "skipped"}},
            {"evaluation": {"evaluation_mode": "compliance_based"}},
        ]
    }
    (tmp_path / "01.json").write_text(json.dumps(sample), encoding="utf-8")
    result = analyze_evaluation_mode_frequency(tmp_path)
    assert result["full"] == 1
    assert result["skipped"] == 1
    assert result["compliance_based"] == 1


# ---------------------------------------------------------------------------
# QAOutputV2 경로 (Dev5 리뷰 2026-04-20 — 3경로 통합 검증)
# ---------------------------------------------------------------------------


def _qa_output_v2_sample() -> dict:
    """Dev5 `QAOutputV2.model_dump(by_alias=True)` 와 유사한 실물 구조 샘플."""
    return {
        "consultation_id": "test-001",
        "tenant": "generic",
        "evaluation": {
            "categories": [
                {
                    "category": "인사 예절",
                    "category_key": "greeting_etiquette",
                    "max_score": 10,
                    "achieved_score": 8,
                    "items": [
                        {
                            "item": "첫인사", "item_number": 1, "max_score": 5, "score": 5,
                            "evaluation_mode": "full",
                            "evidence": [{"speaker": "agent", "timestamp": "", "quote": "안녕하세요", "turn_id": 1}],
                            "confidence": {"final": 5, "signals": {"llm_self": 5, "rule_llm_agreement": True, "rag_stdev": None, "evidence_quality": "high"}},
                        },
                        {
                            "item": "끝인사", "item_number": 2, "max_score": 5, "score": 3,
                            "evaluation_mode": "full",
                            "evidence": [{"speaker": "agent", "timestamp": "", "quote": "감사합니다", "turn_id": 15}],
                            "confidence": {"final": 4, "signals": {"llm_self": 4, "rule_llm_agreement": True, "rag_stdev": None, "evidence_quality": "medium"}},
                        },
                    ],
                }
            ]
        },
        "routing": {"decision": "T1", "hitl_driver": None},
    }


def test_load_batch_results_qa_output_v2_path(tmp_path):
    """Dev5 QAOutputV2 실물 구조 (`evaluation.categories[].items[]`) 로부터 score 로드."""
    from v2.validation.score_drift import load_batch_results

    (tmp_path / "668437_result.json").write_text(
        json.dumps(_qa_output_v2_sample()), encoding="utf-8",
    )
    results = load_batch_results(tmp_path)
    assert "668437" in results
    assert results["668437"] == {1: 5, 2: 3}


def test_analyze_evidence_quality_qa_output_v2_path(tmp_path):
    """Dev5 QAOutputV2 경로에서 evidence 추출 정상."""
    from v2.validation.score_drift import analyze_evidence_quality

    (tmp_path / "668437_result.json").write_text(
        json.dumps(_qa_output_v2_sample()), encoding="utf-8",
    )
    result = analyze_evidence_quality(tmp_path)
    assert result["total_items"] == 2
    assert result["empty_evidence_count"] == 0
    assert result["evidence_quote_count"] == 2


def test_analyze_confidence_calibration_qa_output_v2_path(tmp_path):
    """Dev5 QAOutputV2 ConfidenceBlock.final 추출 정상."""
    from v2.validation.score_drift import analyze_confidence_calibration

    (tmp_path / "668437_result.json").write_text(
        json.dumps(_qa_output_v2_sample()), encoding="utf-8",
    )
    result = analyze_confidence_calibration(tmp_path)
    dist = result["distribution"]
    assert dist[5] == 1
    assert dist[4] == 1


def test_analyze_evaluation_mode_frequency_qa_output_v2_path(tmp_path):
    """Dev5 QAOutputV2 경로에서 evaluation_mode 빈도 집계."""
    from v2.validation.score_drift import analyze_evaluation_mode_frequency

    (tmp_path / "668437_result.json").write_text(
        json.dumps(_qa_output_v2_sample()), encoding="utf-8",
    )
    result = analyze_evaluation_mode_frequency(tmp_path)
    assert result["full"] == 2


def test_iter_items_dedups_by_item_number():
    """V1 과 V2 QAOutputV2 경로 동시 존재 시 first-seen 우선 (중복 제거)."""
    from v2.validation.score_drift import _iter_items

    data = {
        "evaluations": [
            {"agent_id": "g", "evaluation": {"item_number": 1, "score": 5, "evaluation_mode": "full"}}
        ],
        "evaluation": {
            "categories": [
                {"items": [{"item_number": 1, "score": 0, "evaluation_mode": "unevaluable"}]}
            ]
        },
    }
    items = list(_iter_items(data))
    # item 1 은 한 번만 — V1 경로(score=5) 가 먼저
    assert len(items) == 1
    assert items[0]["score"] == 5


def test_analyze_confidence_calibration_reads_diagnostics_confidence_map(tmp_path):
    """Dev5 diagnostics.confidence_map 경로에서 final 추출 (items 미제공 시 fallback)."""
    from v2.validation.score_drift import analyze_confidence_calibration

    data = {
        "evaluation": {"categories": []},
        "diagnostics": {
            "confidence_map": {
                "1": {"final": 5, "signals": {}},
                "2": {"final": 2, "signals": {}},
                "17": {"final": 3, "signals": {}},
            }
        },
    }
    (tmp_path / "999999_result.json").write_text(json.dumps(data), encoding="utf-8")
    result = analyze_confidence_calibration(tmp_path)
    dist = result["distribution"]
    assert dist[5] == 1
    assert dist[2] == 1
    assert dist[3] == 1
    low = [x for x in result["low_confidence_items"] if x.get("source") == "diagnostics"]
    assert any(x["item_number"] == 2 for x in low)


def test_analyze_confidence_calibration_avoids_double_count(tmp_path):
    """items 와 diagnostics 동일 item# 중복 시 items 우선, diagnostics skip."""
    from v2.validation.score_drift import analyze_confidence_calibration

    data = {
        "evaluations": [
            {"evaluation": {"item_number": 1, "confidence": {"final": 5}}}
        ],
        "diagnostics": {
            "confidence_map": {
                "1": {"final": 2, "signals": {}},  # 중복 — skip
                "3": {"final": 4, "signals": {}},  # 신규
            }
        },
    }
    (tmp_path / "999999_result.json").write_text(json.dumps(data), encoding="utf-8")
    result = analyze_confidence_calibration(tmp_path)
    dist = result["distribution"]
    assert dist[5] == 1
    assert dist[2] == 0
    assert dist[4] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
