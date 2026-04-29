# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 4 Confidence 계산기 유닛 테스트."""

from __future__ import annotations

import pytest

from v2.confidence import (
    ITEM_WEIGHTS,
    SIGNAL_KEYS,
    compute_item_confidence,
    get_weights,
    validate_weights,
)


class TestWeights:
    def test_all_items_weights_sum_to_one(self):
        errs = validate_weights()
        assert errs == [], f"가중치 합 불일치: {errs}"

    def test_each_item_has_four_signals(self):
        for item_number, weights in ITEM_WEIGHTS.items():
            assert set(weights.keys()) == set(SIGNAL_KEYS), (
                f"item {item_number}: keys 불일치 {set(weights.keys())}"
            )

    def test_unknown_item_returns_uniform(self):
        weights = get_weights(99)
        assert pytest.approx(sum(weights.values()), abs=1e-6) == 1.0
        assert set(weights.keys()) == set(SIGNAL_KEYS)

    def test_design_examples_match(self):
        """설계서 §8.1 예시 — #7 쿠션어 llm_self+rag_stdev 가중↑, #15 evidence_quality+rule 가중↑."""
        cushion = get_weights(7)
        assert cushion["llm_self"] >= 0.3
        assert cushion["rag_stdev"] >= 0.25

        accuracy = get_weights(15)
        assert accuracy["evidence_quality"] >= 0.35
        assert accuracy["rule_llm_agreement"] >= 0.3


class TestCompute:
    def test_skipped_returns_final_5(self):
        r = compute_item_confidence(
            3, evaluation_mode="skipped",
            llm_self_confidence_score=None,
            rule_llm_delta=None, rag_stdev=None,
            evidence_quality_rag=None, evidence_count=0,
        )
        assert r["final"] == 5
        assert r["signals"]["evidence_quality"] == "high"

    def test_unevaluable_returns_final_1(self):
        r = compute_item_confidence(
            17, evaluation_mode="unevaluable",
            llm_self_confidence_score=None,
            rule_llm_delta=None, rag_stdev=None,
            evidence_quality_rag=None, evidence_count=0,
        )
        assert r["final"] == 1
        assert r["signals"]["evidence_quality"] == "low"

    def test_high_confidence_greeting(self):
        r = compute_item_confidence(
            1, evaluation_mode="full", llm_self_confidence_score=5,
            rule_llm_delta={"has_rule_pre_verdict": True, "rule_score": 5, "llm_score": 5, "agreement": True},
            rag_stdev=0.3, evidence_quality_rag="high", evidence_count=2,
        )
        assert r["final"] == 5
        assert r["signals"]["rule_llm_agreement"] is True

    def test_cushion_with_low_llm_and_high_stdev(self):
        r = compute_item_confidence(
            7, evaluation_mode="full", llm_self_confidence_score=2,
            rule_llm_delta=None, rag_stdev=2.0,
            evidence_quality_rag="medium", evidence_count=1,
        )
        assert r["final"] <= 2  # 쿠션어는 llm_self 가중치가 높음 — 낮으면 바로 떨어져야
        assert r["signals"]["rule_llm_agreement"] is False

    def test_accuracy_partial_with_review(self):
        r = compute_item_confidence(
            15, evaluation_mode="partial_with_review",
            llm_self_confidence_score=3,
            rule_llm_delta={"has_rule_pre_verdict": True, "rule_score": 7, "llm_score": 7, "agreement": True},
            rag_stdev=None, evidence_quality_rag="low", evidence_count=0,
        )
        # rag_stdev=None → 0.4, evidence 0건 → quality cap 0.2
        assert r["final"] <= 3

    def test_rule_llm_disagreement_penalizes(self):
        agree = compute_item_confidence(
            17, evaluation_mode="compliance_based",
            llm_self_confidence_score=4,
            rule_llm_delta={"has_rule_pre_verdict": True, "rule_score": 5, "llm_score": 5, "agreement": True},
            rag_stdev=None, evidence_quality_rag="high", evidence_count=1,
        )
        disagree = compute_item_confidence(
            17, evaluation_mode="compliance_based",
            llm_self_confidence_score=4,
            rule_llm_delta={"has_rule_pre_verdict": True, "rule_score": 5, "llm_score": 0, "agreement": False},
            rag_stdev=None, evidence_quality_rag="high", evidence_count=1,
        )
        assert agree["final"] >= disagree["final"]

    def test_no_rule_pre_verdict_is_neutral(self):
        r = compute_item_confidence(
            8, evaluation_mode="full", llm_self_confidence_score=5,
            rule_llm_delta=None, rag_stdev=0.0,
            evidence_quality_rag="high", evidence_count=2,
        )
        assert r["final"] >= 4  # Rule 없음은 0.7 중립 — 그래도 고신뢰 유지
