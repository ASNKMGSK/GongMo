# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 4 Tier 라우터 유닛 테스트 (설계서 §8.2 / §10.1)."""

from __future__ import annotations

from v2.routing import apply_t1_sampling, decide_tier


def _fullscore_state(**override):
    """모든 항목 만점/high-confidence 상태 — 기본 T0."""
    base = dict(
        confidence_results={1: {"final": 5}, 2: {"final": 5}, 8: {"final": 5}, 16: {"final": 5}},
        evaluations=[
            {"item_number": 1, "evaluation_mode": "full"},
            {"item_number": 2, "evaluation_mode": "full"},
            {"item_number": 9, "evaluation_mode": "skipped"},
            {"item_number": 17, "evaluation_mode": "skipped"},
            {"item_number": 18, "evaluation_mode": "skipped"},
        ],
        preprocessing={"quality": {"passed": True}, "deduction_triggers": {}},
        final_score={"after_overrides": 90, "grade": "A"},
    )
    base.update(override)
    return base


class TestPolicyT3:
    def test_rudeness_canonical_key(self):
        r = decide_tier(**_fullscore_state(
            preprocessing={"quality": {"passed": True}, "deduction_triggers": {"불친절": True}},
        ))
        assert r["decision"] == "T3"
        assert r["hitl_driver"] == "policy_driven"
        assert "deduction_trigger:rudeness" in r["tier_reasons"]

    def test_rudeness_alias_rudeness(self):
        r = decide_tier(**_fullscore_state(
            preprocessing={"quality": {"passed": True}, "deduction_triggers": {"rudeness": True}},
        ))
        assert r["decision"] == "T3"

    def test_privacy_leak_canonical(self):
        r = decide_tier(**_fullscore_state(
            preprocessing={"quality": {"passed": True}, "deduction_triggers": {"개인정보_유출": True}},
        ))
        assert r["decision"] == "T3"
        assert "deduction_trigger:privacy_leak" in r["tier_reasons"]

    def test_uncorrected_misinfo_canonical(self):
        r = decide_tier(**_fullscore_state(
            preprocessing={"quality": {"passed": True}, "deduction_triggers": {"오안내_미정정": True}},
        ))
        assert r["decision"] == "T3"
        assert "deduction_trigger:uncorrected_misinfo" in r["tier_reasons"]

    def test_stt_quality_failure_triggers_t3(self):
        r = decide_tier(**_fullscore_state(
            preprocessing={"quality": {"passed": False, "reasons": ["diarization_failed"]},
                           "deduction_triggers": {}},
        ))
        assert r["decision"] == "T3"
        assert "stt_quality_failure" in r["tier_reasons"]
        codes = [pf["code"] for pf in r["priority_flags"]]
        assert "stt_quality_failure" in codes

    def test_privacy_item_evaluable_force_t3(self):
        r = decide_tier(
            confidence_results={17: {"final": 4}},
            evaluations=[{"item_number": 17, "evaluation_mode": "compliance_based"}],
            preprocessing={"quality": {"passed": True}, "deduction_triggers": {}},
            final_score={"after_overrides": 85, "grade": "A"},
        )
        assert r["decision"] == "T3"
        assert any("privacy_items_evaluable" in reason for reason in r["tier_reasons"])

    def test_item15_partial_review_triggers_t3(self):
        r = decide_tier(
            confidence_results={15: {"final": 3}},
            evaluations=[{"item_number": 15, "evaluation_mode": "partial_with_review"}],
            preprocessing={"quality": {"passed": True}, "deduction_triggers": {}},
            final_score={"after_overrides": 75, "grade": "B"},
        )
        assert r["decision"] == "T3"
        assert "accuracy_partial_with_review" in r["tier_reasons"]

    def test_unevaluable_item_triggers_t3(self):
        r = decide_tier(
            confidence_results={10: {"final": 1}},
            evaluations=[{"item_number": 10, "evaluation_mode": "unevaluable"}],
            preprocessing={"quality": {"passed": True}, "deduction_triggers": {}},
            final_score={"after_overrides": 70, "grade": "B"},
        )
        assert r["decision"] == "T3"
        assert any("unevaluable_items" in reason for reason in r["tier_reasons"])

    def test_vip_call(self):
        r = decide_tier(**_fullscore_state(), tenant_flags={"is_vip": True})
        assert r["decision"] == "T3"
        assert "vip_call" in r["tier_reasons"]


class TestUncertaintyT3:
    def test_high_stakes_low_confidence_item15(self):
        r = decide_tier(
            confidence_results={15: {"final": 2}},
            evaluations=[{"item_number": 15, "evaluation_mode": "full"}],
            preprocessing={"quality": {"passed": True}, "deduction_triggers": {}},
            final_score={"after_overrides": 72, "grade": "B"},
        )
        assert r["decision"] == "T3"
        assert r["hitl_driver"] == "uncertainty_driven"
        assert any("high_stakes_low_confidence:15" in reason for reason in r["tier_reasons"])


class TestPolicyT2:
    def test_grade_boundary_triggers_t2(self):
        # 85=A 경계, 83 은 -2 차이로 T2
        r = decide_tier(
            confidence_results={1: {"final": 5}},
            evaluations=[{"item_number": 1, "evaluation_mode": "full"}],
            preprocessing={"quality": {"passed": True}, "deduction_triggers": {}},
            final_score={"after_overrides": 83, "grade": "B"},
        )
        assert r["decision"] == "T2"
        assert any("grade_boundary" in reason for reason in r["tier_reasons"])

    def test_rookie_counselor(self):
        r = decide_tier(**_fullscore_state(), tenant_flags={"is_rookie": True})
        assert r["decision"] == "T2"
        assert "rookie_counselor" in r["tier_reasons"]


class TestUncertaintyT2:
    def test_low_confidence_general_item(self):
        r = decide_tier(
            confidence_results={6: {"final": 2}, 1: {"final": 5}},
            evaluations=[
                {"item_number": 6, "evaluation_mode": "full"},
                {"item_number": 1, "evaluation_mode": "full"},
            ],
            preprocessing={"quality": {"passed": True}, "deduction_triggers": {}},
            final_score={"after_overrides": 76, "grade": "B"},
        )
        assert r["decision"] == "T2"
        assert r["hitl_driver"] == "uncertainty_driven"


class TestT0AndSampling:
    def test_clean_path_returns_t0(self):
        r = decide_tier(**_fullscore_state(final_score={"after_overrides": 78, "grade": "B"}))
        assert r["decision"] == "T0"
        assert r["hitl_driver"] is None

    def test_t1_sampling_deterministic(self):
        r0 = decide_tier(**_fullscore_state(final_score={"after_overrides": 78, "grade": "B"}))
        assert r0["decision"] == "T0"
        r1 = apply_t1_sampling(r0, rng_seed=7, sample_rate=1.0)
        assert r1["decision"] == "T1"
        assert r1["hitl_driver"] == "policy_driven"

    def test_t1_sampling_skips_non_t0(self):
        r = {"decision": "T3", "hitl_driver": "policy_driven", "tier_reasons": [], "priority_flags": [], "estimated_review_time_min": 10}
        assert apply_t1_sampling(r, rng_seed=1, sample_rate=1.0)["decision"] == "T3"
