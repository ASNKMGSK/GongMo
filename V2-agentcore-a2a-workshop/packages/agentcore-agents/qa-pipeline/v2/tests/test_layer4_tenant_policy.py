# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""PL Q5 외부화 — tenant_policy 로드 + tier_router/calculator 통합 테스트."""

from __future__ import annotations

import os

import pytest

from v2.confidence.calculator import compute_item_confidence
from v2.routing import (
    apply_t1_sampling,
    decide_tier,
    enforce_t0_cap,
    load_tenant_policy,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _reset_policy_cache():
    """각 테스트 전후 정책 캐시/env 초기화."""
    for k in [
        "ROUTING_INITIAL_T0_CAP", "ROUTING_T1_SAMPLE_RATE", "ROUTING_GRADE_BOUNDARY_MARGIN",
        "CONFIDENCE_RAG_MIN_SAMPLE_SIZE", "CONFIDENCE_RAG_SMALL_SAMPLE_WEIGHT",
    ]:
        os.environ.pop(k, None)
    reset_cache()
    yield
    reset_cache()


class TestTenantPolicyLoad:
    def test_generic_defaults_loaded_from_yaml(self):
        p = load_tenant_policy("generic")
        # tenant_config.yaml Q5 섹션
        assert p.routing.initial_t0_cap == pytest.approx(0.30)
        assert p.routing.grade_boundary_margin == 3
        assert p.confidence.rag_min_sample_size == 3
        assert p.confidence.rag_small_sample_weight == pytest.approx(0.5)

    def test_env_override_routing(self):
        os.environ["ROUTING_INITIAL_T0_CAP"] = "0.55"
        os.environ["ROUTING_T1_SAMPLE_RATE"] = "0.08"
        os.environ["ROUTING_GRADE_BOUNDARY_MARGIN"] = "5"
        reset_cache()
        p = load_tenant_policy("generic")
        assert p.routing.initial_t0_cap == pytest.approx(0.55)
        assert p.routing.t1_sample_rate == pytest.approx(0.08)
        assert p.routing.grade_boundary_margin == 5

    def test_env_override_confidence(self):
        os.environ["CONFIDENCE_RAG_MIN_SAMPLE_SIZE"] = "5"
        os.environ["CONFIDENCE_RAG_SMALL_SAMPLE_WEIGHT"] = "0.2"
        reset_cache()
        p = load_tenant_policy("generic")
        assert p.confidence.rag_min_sample_size == 5
        assert p.confidence.rag_small_sample_weight == pytest.approx(0.2)

    def test_unknown_tenant_falls_back_to_defaults(self):
        p = load_tenant_policy("nonexistent_tenant_xyz")
        # YAML 없음 → 코드 기본값
        assert p.routing.initial_t0_cap == pytest.approx(0.30)
        assert p.confidence.rag_min_sample_size == 3


class TestTierRouterExternal:
    def test_grade_boundary_uses_tenant_margin(self):
        os.environ["ROUTING_GRADE_BOUNDARY_MARGIN"] = "5"
        reset_cache()
        # 85 경계 기준 — margin=5 면 80~90 모두 T2, margin=3 이면 82~88만
        r = decide_tier(
            confidence_results={1: {"final": 5}},
            evaluations=[{"item_number": 1, "evaluation_mode": "full"}],
            preprocessing={"quality": {"passed": True}, "deduction_triggers": {}},
            final_score={"after_overrides": 80, "grade": "B"},
        )
        assert r["decision"] == "T2"
        assert "grade_boundary:80" in r["tier_reasons"]

    def test_t1_sampling_uses_tenant_rate_when_rate_none(self):
        # env 로 100% 승격 강제
        os.environ["ROUTING_T1_SAMPLE_RATE"] = "1.0"
        reset_cache()
        r0 = {"decision": "T0", "hitl_driver": None, "priority_flags": [],
              "tier_reasons": [], "estimated_review_time_min": 0}
        r1 = apply_t1_sampling(r0, rng_seed=0)
        assert r1["decision"] == "T1"
        assert any("t1_sampling" in reason for reason in r1["tier_reasons"])

    def test_t1_sampling_explicit_rate_overrides_tenant(self):
        os.environ["ROUTING_T1_SAMPLE_RATE"] = "1.0"
        reset_cache()
        r0 = {"decision": "T0", "hitl_driver": None, "priority_flags": [],
              "tier_reasons": [], "estimated_review_time_min": 0}
        # 명시 sample_rate=0.0 이 tenant override (1.0) 를 이김
        r1 = apply_t1_sampling(r0, rng_seed=0, sample_rate=0.0)
        assert r1["decision"] == "T0"


class TestEnforceT0Cap:
    def test_no_downgrade_when_below_cap(self):
        routings = [
            {"decision": "T0", "tier_reasons": [], "priority_flags": [],
             "estimated_review_time_min": 0, "hitl_driver": None},
            {"decision": "T3", "tier_reasons": ["x"], "priority_flags": [],
             "estimated_review_time_min": 10, "hitl_driver": "policy_driven"},
        ]
        # cap=0.5, total=2, T0=1 → max_t0=1 → 강등 없음
        out = enforce_t0_cap(routings, cap=0.5)
        assert out[0]["decision"] == "T0"
        assert out[1]["decision"] == "T3"

    def test_downgrades_lowest_composite_first(self):
        # 10 샘플, 모두 T0, cap=0.3 → 최대 3개만 T0 유지, 7개 T2 강등
        routings = []
        for i in range(10):
            routings.append({
                "decision": "T0", "tier_reasons": [], "priority_flags": [],
                "estimated_review_time_min": 0, "hitl_driver": None,
                "overall_confidence": float(i),  # 0,1,2,...,9
            })
        out = enforce_t0_cap(routings, cap=0.3)
        # composite 낮은 0~6 번이 T2 로 강등, 7~9 번이 T0 유지 (3개)
        t0_count = sum(1 for r in out if r["decision"] == "T0")
        t2_count = sum(1 for r in out if r["decision"] == "T2")
        assert t0_count == 3
        assert t2_count == 7
        # 강등된 샘플은 이유 기록
        for i in range(7):
            assert any("t0_cap_downgrade" in reason for reason in out[i]["tier_reasons"])
        # composite 높은 샘플은 T0 유지
        for i in range(7, 10):
            assert out[i]["decision"] == "T0"

    def test_empty_routings(self):
        assert enforce_t0_cap([], cap=0.3) == []

    def test_cap_loaded_from_tenant_config(self):
        # 명시 cap 없으면 tenant_config 기본값 0.30 사용
        routings = [{"decision": "T0", "tier_reasons": [], "priority_flags": [],
                     "estimated_review_time_min": 0, "hitl_driver": None}] * 10
        out = enforce_t0_cap(routings)  # cap=None → tenant_config 로드
        t0_count = sum(1 for r in out if r["decision"] == "T0")
        assert t0_count == 3  # 10 * 0.30 = 3


class TestConfidenceSampleSizePenalty:
    def test_no_penalty_when_sample_size_none(self):
        """sample_size=None 이면 penalty 미적용 (하위 호환)."""
        c = compute_item_confidence(
            7, evaluation_mode="full", llm_self_confidence_score=5,
            rule_llm_delta=None, rag_stdev=0.0,
            evidence_quality_rag="high", evidence_count=2,
            rag_sample_size=None,
        )
        assert c["signals"]["rag_small_sample_penalty_applied"] is False
        assert c["signals"]["rag_sample_size"] is None

    def test_no_penalty_when_sample_size_above_threshold(self):
        c = compute_item_confidence(
            7, evaluation_mode="full", llm_self_confidence_score=5,
            rule_llm_delta=None, rag_stdev=0.0,
            evidence_quality_rag="high", evidence_count=2,
            rag_sample_size=5,  # >= min 3
        )
        assert c["signals"]["rag_small_sample_penalty_applied"] is False

    def test_penalty_applied_when_sample_size_below_threshold(self):
        """sample_size < min_sample_size → rag_stdev 신호 약화."""
        # sample_size=1 < min=3 → penalty
        c_with_penalty = compute_item_confidence(
            7, evaluation_mode="full", llm_self_confidence_score=3,
            rule_llm_delta=None, rag_stdev=0.0,  # 원래 완벽 일치 신호
            evidence_quality_rag="medium", evidence_count=1,
            rag_sample_size=1,
        )
        c_no_penalty = compute_item_confidence(
            7, evaluation_mode="full", llm_self_confidence_score=3,
            rule_llm_delta=None, rag_stdev=0.0,
            evidence_quality_rag="medium", evidence_count=1,
            rag_sample_size=10,  # 충분한 sample
        )
        assert c_with_penalty["signals"]["rag_small_sample_penalty_applied"] is True
        assert c_no_penalty["signals"]["rag_small_sample_penalty_applied"] is False
        # penalty 적용된 composite <= 정상 composite (rag_stdev=0 완벽 신호가 약화되므로)
        assert c_with_penalty["signals"]["weighted_composite"] <= c_no_penalty["signals"]["weighted_composite"]

    def test_penalty_respects_env_override(self):
        os.environ["CONFIDENCE_RAG_MIN_SAMPLE_SIZE"] = "10"
        reset_cache()
        # sample_size=5 < 10 → penalty
        c = compute_item_confidence(
            7, evaluation_mode="full", llm_self_confidence_score=3,
            rule_llm_delta=None, rag_stdev=0.5,
            evidence_quality_rag="medium", evidence_count=1,
            rag_sample_size=5,
        )
        assert c["signals"]["rag_small_sample_penalty_applied"] is True

    def test_penalty_skipped_mode_still_returns_trace(self):
        c = compute_item_confidence(
            3, evaluation_mode="skipped", llm_self_confidence_score=5,
            rule_llm_delta=None, rag_stdev=None,
            evidence_quality_rag="high", evidence_count=0,
            rag_sample_size=1,
        )
        # skipped 는 final=5 강제지만 trace 필드는 유지
        assert c["final"] == 5
        assert "rag_sample_size" in c["signals"]
        assert c["signals"]["rag_small_sample_penalty_applied"] is False  # skipped 경로는 미적용
