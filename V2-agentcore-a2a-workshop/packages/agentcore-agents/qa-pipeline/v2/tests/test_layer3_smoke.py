# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 3 Orchestrator smoke tests.

검증 포인트:
  1. aggregate_scores — 18 항목 평가를 8 카테고리로 집계, 총점 정확
  2. apply_overrides  — 불친절/개인정보유출/오안내미정정 3 Override 동작
  3. check_consistency— Rule 기반 모순 감지
  4. assign_grade     — GRADE_BOUNDARIES 매핑 + ±3점 경계 T2 상향
  5. run_layer3       — 4 모듈 순차 + skip_phase_c_and_reporting 플래그
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


_QA_PIPELINE_ROOT = Path(__file__).resolve().parents[2]
if str(_QA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_QA_PIPELINE_ROOT))


# ---------------------------------------------------------------------------
# 테스트 fixture — 18 항목 평가 생성 헬퍼
# ---------------------------------------------------------------------------

from v2.contracts.rubric import max_score_of


def _perfect_item(item_number: int, **overrides):
    """만점 item (V1 EvaluationResult 포맷)."""
    max_s = max_score_of(item_number)
    return {
        "status": "success",
        "agent_id": f"item{item_number}-agent",
        "evaluation": {
            "item_number": item_number,
            "item_name": f"item_{item_number}",
            "max_score": max_s,
            "score": max_s,
            "evaluation_mode": "full",
            "deductions": [],
            "evidence": [{"speaker": "agent", "quote": "mock", "turn_id": 1, "timestamp": ""}],
            "confidence": 0.9,
            **overrides,
        },
    }


def _all_perfect_evaluations():
    """전 18 항목 만점 (100점)."""
    return [_perfect_item(n) for n in range(1, 19)]


# ---------------------------------------------------------------------------
# aggregate_scores
# ---------------------------------------------------------------------------


def test_aggregate_scores_perfect_returns_100():
    from v2.layer3.aggregator import aggregate_scores

    result = aggregate_scores(_all_perfect_evaluations())
    assert result["raw_total"] == 100
    assert result["max_possible"] == 100
    assert result["missing_items"] == []
    assert len(result["category_scores"]) == 8


def test_aggregate_scores_partial_correct_total():
    from v2.layer3.aggregator import aggregate_scores

    # #1=3, #10=7 (부분 점수), 나머지 만점 = 100 - 2 - 3 = 95
    evals = _all_perfect_evaluations()
    evals[0]["evaluation"]["score"] = 3   # #1
    evals[9]["evaluation"]["score"] = 7   # #10

    result = aggregate_scores(evals)
    assert result["raw_total"] == 95


def test_aggregate_scores_snaps_invalid_value():
    """V2 ALLOWED_STEPS 위반 값이 들어와도 snap."""
    from v2.layer3.aggregator import aggregate_scores

    evals = _all_perfect_evaluations()
    evals[16]["evaluation"]["score"] = 4  # #17 에 4 (허용값 아님 [5,3,0]) → snap → 3
    result = aggregate_scores(evals)
    item17 = next(i for i in result["normalized_items"] if i["item_number"] == 17)
    assert item17["score"] == 3


def test_aggregate_scores_missing_items_tracked():
    from v2.layer3.aggregator import aggregate_scores

    evals = [_perfect_item(n) for n in range(1, 15)]  # #15-18 누락
    result = aggregate_scores(evals)
    assert set(result["missing_items"]) == {15, 16, 17, 18}


# ---------------------------------------------------------------------------
# apply_overrides
# ---------------------------------------------------------------------------


def test_apply_overrides_unfriendly_forces_all_zero():
    from v2.layer3.aggregator import aggregate_scores
    from v2.layer3.override_rules import apply_overrides

    agg = aggregate_scores(_all_perfect_evaluations())
    preprocessing = {
        "deduction_triggers": {"불친절": True, "개인정보_유출": False, "오안내_미정정": False},
        "deduction_trigger_details": [
            {"trigger_type": "profanity", "turn_id": 5, "evidence_text": "mock", "confidence": 0.9,
             "source": "rule", "recommended_override": "all_zero"},
        ],
    }
    ov = apply_overrides(agg["category_scores"], preprocessing, None, raw_total=agg["raw_total"])
    assert ov["applied"] is True
    assert ov["after_overrides"] == 0
    assert len(ov["reasons"]) == 1
    assert ov["reasons"][0]["action"] == "all_zero"


def test_apply_overrides_privacy_leak_category_zero():
    from v2.layer3.aggregator import aggregate_scores
    from v2.layer3.override_rules import apply_overrides

    agg = aggregate_scores(_all_perfect_evaluations())
    preprocessing = {
        "deduction_triggers": {"불친절": False, "개인정보_유출": True, "오안내_미정정": False},
        "deduction_trigger_details": [
            {"trigger_type": "privacy_leak", "turn_id": 10, "evidence_text": "mock",
             "confidence": 0.8, "source": "rule", "recommended_override": "category_zero"},
        ],
    }
    ov = apply_overrides(agg["category_scores"], preprocessing, None, raw_total=agg["raw_total"])
    assert ov["applied"] is True
    # 개인정보 보호 카테고리 (#17, #18 = 10점) 만 감산
    assert ov["after_overrides"] == 90
    assert set(ov["items_modified"]) == {17, 18}


def test_apply_overrides_uncorrected_misinfo_work_accuracy_zero():
    from v2.layer3.aggregator import aggregate_scores
    from v2.layer3.override_rules import apply_overrides

    agg = aggregate_scores(_all_perfect_evaluations())
    accuracy_verdict = {
        "has_incorrect_guidance": True,
        "correction_attempted": False,
        "severity": "major",
        "incorrect_items": [15, 16],
        "evidence_turn_ids": [42, 45],
        "recommended_override": "category_zero",
        "rationale": "오안내 후 정정 없음",
    }
    ov = apply_overrides(
        agg["category_scores"], preprocessing=None, accuracy_verdict=accuracy_verdict,
        raw_total=agg["raw_total"],
    )
    assert ov["applied"] is True
    # #15 (10점) + #16 (5점) = 15점 감산
    assert ov["after_overrides"] == 85
    assert ov["reasons"][0]["trigger"] == "uncorrected_misinfo"


def test_apply_overrides_work_accuracy_category_zero_sets_both_items_to_zero():
    """PDF §5.2: 오안내 후 미정정 → 업무정확도 대분류 전체 (#15, #16) 0점.

    accuracy_verdict.incorrect_items=[15, 16] + recommended_override="category_zero"
    입력 시 work_accuracy 카테고리의 item 15, 16 모두 score=0 이어야 하며,
    overrides.applied=True 이어야 함.
    """
    from v2.layer3.aggregator import aggregate_scores
    from v2.layer3.override_rules import apply_overrides

    agg = aggregate_scores(_all_perfect_evaluations())
    accuracy_verdict = {
        "has_incorrect_guidance": True,
        "correction_attempted": False,
        "severity": "major",
        "incorrect_items": [15, 16],
        "evidence_turn_ids": [42, 45],
        "recommended_override": "category_zero",
        "rationale": "오안내 후 정정 없음 — 업무정확도 대분류 전체 0점",
    }
    ov = apply_overrides(
        agg["category_scores"], preprocessing=None, accuracy_verdict=accuracy_verdict,
        raw_total=agg["raw_total"],
    )

    # overrides.applied = True 확인
    assert ov["applied"] is True
    assert len(ov["reasons"]) == 1
    assert ov["reasons"][0]["trigger"] == "uncorrected_misinfo"
    assert ov["reasons"][0]["action"] == "category_zero"
    assert set(ov["items_modified"]) == {15, 16}

    # work_accuracy 카테고리 내 item 15, 16 모두 score=0 확인
    work_accuracy_cat = next(
        c for c in ov["category_scores"] if c.get("category_key") == "work_accuracy"
    )
    item_scores = {i["item_number"]: i["score"] for i in work_accuracy_cat["items"]}
    assert item_scores[15] == 0, "item #15 should be 0"
    assert item_scores[16] == 0, "item #16 should be 0"
    assert work_accuracy_cat["achieved_score"] == 0

    # 다른 카테고리는 영향 없음 확인 (총 100 - 15 = 85)
    assert ov["after_overrides"] == 85


def test_apply_overrides_work_accuracy_item_zero_keeps_item_16():
    """correction_attempted=True + recommended_override="item_zero" → item 15 만 0점."""
    from v2.layer3.aggregator import aggregate_scores
    from v2.layer3.override_rules import apply_overrides

    agg = aggregate_scores(_all_perfect_evaluations())
    accuracy_verdict = {
        "has_incorrect_guidance": True,
        "correction_attempted": True,
        "severity": "minor",
        "incorrect_items": [15],
        "evidence_turn_ids": [12],
        "recommended_override": "item_zero",
        "rationale": "오안내 발생 — 다만 상담사 본인 발화로 즉시 정정",
    }
    ov = apply_overrides(
        agg["category_scores"], preprocessing=None, accuracy_verdict=accuracy_verdict,
        raw_total=agg["raw_total"],
    )

    assert ov["applied"] is True
    assert ov["reasons"][0]["action"] == "item_zero"
    assert set(ov["items_modified"]) == {15}

    work_accuracy_cat = next(
        c for c in ov["category_scores"] if c.get("category_key") == "work_accuracy"
    )
    item_scores = {i["item_number"]: i["score"] for i in work_accuracy_cat["items"]}
    assert item_scores[15] == 0
    assert item_scores[16] == 5, "item #16 은 감점 없어야 함 (만점 5점 유지)"

    # #15 (10점) 만 감산 → 100 - 10 = 90
    assert ov["after_overrides"] == 90


def test_apply_overrides_noop_when_no_triggers():
    from v2.layer3.aggregator import aggregate_scores
    from v2.layer3.override_rules import apply_overrides

    agg = aggregate_scores(_all_perfect_evaluations())
    ov = apply_overrides(
        agg["category_scores"],
        preprocessing={"deduction_triggers": {"불친절": False, "개인정보_유출": False, "오안내_미정정": False}},
        accuracy_verdict=None,
        raw_total=agg["raw_total"],
    )
    assert ov["applied"] is False
    assert ov["after_overrides"] == 100


def test_sub_agent_profanity_hint_triggers_all_zero():
    """PDF 원칙 4: Layer 1 Rule 트리거는 비어 있지만 Sub Agent 가 profanity hint 를
    반환하면 all_zero override 가 발동되어야 한다.

    검증:
      - ov.applied = True
      - 모든 item score = 0
      - overrides.reasons[].source = "sub_agent_hint"
      - overrides.reasons[].trigger = "profanity"
      - overrides.reasons[].action = "all_zero"
    """
    from v2.layer3.aggregator import aggregate_scores
    from v2.layer3.override_rules import apply_overrides

    agg = aggregate_scores(_all_perfect_evaluations())
    # Layer 1 Rule 트리거 미탐지
    preprocessing = {
        "deduction_triggers": {"불친절": False, "개인정보_유출": False, "오안내_미정정": False},
        "deduction_trigger_details": [],
    }
    # Sub Agent 가 #6 item 에서 LLM 맥락 판정으로 profanity 감지
    sub_agent_hints = [{"item_number": 6, "hint": "profanity"}]

    ov = apply_overrides(
        agg["category_scores"],
        preprocessing=preprocessing,
        accuracy_verdict=None,
        sub_agent_override_hints=sub_agent_hints,
        raw_total=agg["raw_total"],
    )

    assert ov["applied"] is True
    assert ov["after_overrides"] == 0
    assert len(ov["reasons"]) == 1

    reason = ov["reasons"][0]
    assert reason["trigger"] == "profanity"
    assert reason["action"] == "all_zero"
    assert reason["source"] == "sub_agent_hint"

    # 모든 item score 가 0 인지 확인
    for cat in ov["category_scores"]:
        for item in cat["items"]:
            assert item["score"] == 0, f"item #{item['item_number']} 이 0 이 아님"
        assert cat["achieved_score"] == 0


def test_sub_agent_privacy_leak_hint_triggers_category_zero():
    """PDF 원칙 4: Sub Agent 가 privacy_leak hint 를 반환하면 privacy_protection
    카테고리만 category_zero 처리되어야 한다 (다른 카테고리 영향 없음).

    검증:
      - 개인정보 보호 카테고리 (item 17, 18) 만 score=0
      - 나머지 카테고리는 만점 유지 → after_overrides = 100 - 10 = 90
      - overrides.reasons[].source = "sub_agent_hint"
    """
    from v2.layer3.aggregator import aggregate_scores
    from v2.layer3.override_rules import apply_overrides

    agg = aggregate_scores(_all_perfect_evaluations())
    preprocessing = {
        "deduction_triggers": {"불친절": False, "개인정보_유출": False, "오안내_미정정": False},
        "deduction_trigger_details": [],
    }
    sub_agent_hints = [{"item_number": 18, "hint": "privacy_leak"}]

    ov = apply_overrides(
        agg["category_scores"],
        preprocessing=preprocessing,
        accuracy_verdict=None,
        sub_agent_override_hints=sub_agent_hints,
        raw_total=agg["raw_total"],
    )

    assert ov["applied"] is True
    assert ov["after_overrides"] == 90  # 100 - 10 (privacy_protection category max)
    assert set(ov["items_modified"]) == {17, 18}

    reason = ov["reasons"][0]
    assert reason["trigger"] == "privacy_leak"
    assert reason["action"] == "category_zero"
    assert reason["source"] == "sub_agent_hint"
    assert set(reason["affected_items"]) == {17, 18}

    # privacy_protection 카테고리만 0점, 다른 카테고리는 만점 유지
    privacy_cat = next(
        c for c in ov["category_scores"] if c.get("category_key") == "privacy_protection"
    )
    item_scores = {i["item_number"]: i["score"] for i in privacy_cat["items"]}
    assert item_scores[17] == 0
    assert item_scores[18] == 0
    assert privacy_cat["achieved_score"] == 0

    # 다른 카테고리 (예: greeting_etiquette) 는 변동 없음
    greeting_cat = next(
        c for c in ov["category_scores"] if c.get("category_key") == "greeting_etiquette"
    )
    assert greeting_cat["achieved_score"] == greeting_cat["max_score"]


# ---------------------------------------------------------------------------
# check_consistency
# ---------------------------------------------------------------------------


def test_check_consistency_flags_greeting_courtesy_mismatch():
    from v2.layer3.aggregator import aggregate_scores
    from v2.layer3.consistency_checker import check_consistency

    # #1 첫인사 만점 + #6 정중한 표현 0점 → CR1 모순
    evals = _all_perfect_evaluations()
    evals[5]["evaluation"]["score"] = 0   # #6
    agg = aggregate_scores(evals)

    cc = check_consistency(agg["category_scores"], normalized_items=agg["normalized_items"])
    codes = {f["code"] for f in cc["flags"]}
    assert "greeting_courtesy_mismatch" in codes


def test_check_consistency_flags_evidence_missing_full():
    from v2.layer3.aggregator import aggregate_scores
    from v2.layer3.consistency_checker import check_consistency

    evals = _all_perfect_evaluations()
    # #1 을 full 모드인데 evidence 비우기
    evals[0]["evaluation"]["evidence"] = []
    agg = aggregate_scores(evals)

    cc = check_consistency(agg["category_scores"], normalized_items=agg["normalized_items"])
    codes = {f["code"] for f in cc["flags"]}
    assert "evidence_missing_full_mode" in codes


# ---------------------------------------------------------------------------
# assign_grade
# ---------------------------------------------------------------------------


def test_assign_grade_maps_boundaries():
    from v2.layer3.grader import assign_grade

    for total, expected in [(100, "S"), (95, "S"), (94, "A"), (85, "A"), (84, "B"),
                             (70, "B"), (69, "C"), (50, "C"), (49, "D"), (0, "D")]:
        result = assign_grade(
            raw_total=total, after_overrides=total, max_possible=100,
            preprocessing=None, normalized_items=[],
        )
        assert result["grade"] == expected, f"total={total} → expected {expected} got {result['grade']}"


def test_assign_grade_near_boundary_triggers_t2():
    from v2.layer3.grader import assign_grade

    # 87 점 → A (>=85) 이고 boundary distance = 2 (3 이내) → T2
    result = assign_grade(
        raw_total=87, after_overrides=87, max_possible=100,
        preprocessing=None, normalized_items=[],
    )
    assert result["near_boundary"] is True
    assert result["routing_tier_hint"] == "T2"


def test_assign_grade_unevaluable_forces_t3():
    from v2.layer3.grader import assign_grade

    result = assign_grade(
        raw_total=100, after_overrides=100, max_possible=100,
        preprocessing={"quality": {"unevaluable": True, "tier_route_override": "T3"}},
        normalized_items=[],
    )
    assert result["routing_tier_hint"] == "T3"


def test_assign_grade_force_t3_items_active():
    from v2.layer3.grader import assign_grade

    # #17 이 evaluable (skipped 아님) → T3 강제
    result = assign_grade(
        raw_total=90, after_overrides=90, max_possible=100,
        preprocessing=None,
        normalized_items=[
            {"item_number": 17, "evaluation_mode": "compliance_based"},
        ],
    )
    assert 17 in result["force_t3_items_active"]
    assert result["routing_tier_hint"] == "T3"


# ---------------------------------------------------------------------------
# run_layer3 전체 파이프라인
# ---------------------------------------------------------------------------


def test_run_layer3_perfect_case():
    from v2.layer3 import run_layer3

    result = run_layer3(_all_perfect_evaluations())
    assert result["final_score"]["raw_total"] == 100
    assert result["final_score"]["after_overrides"] == 100
    assert result["final_score"]["grade"] == "S"
    assert result["overrides"]["applied"] is False
    # 경계 체크 (100은 S=95 기준 +5 로 경계 밖 혹은 다른 boundary 2 이내 가능)
    # -> assign_grade 의 tier_hint 검증 생략, 단순히 "T2 이상" 여부만
    assert result["routing_tier_hint"] in ("T0", "T1", "T2", "T3")


def test_run_layer3_all_zero_when_unfriendly():
    from v2.layer3 import run_layer3

    preprocessing = {
        "deduction_triggers": {"불친절": True, "개인정보_유출": False, "오안내_미정정": False},
        "deduction_trigger_details": [
            {"trigger_type": "profanity", "turn_id": 3, "evidence_text": "mock",
             "confidence": 0.9, "source": "rule", "recommended_override": "all_zero"},
        ],
    }
    result = run_layer3(_all_perfect_evaluations(), preprocessing=preprocessing)
    assert result["final_score"]["after_overrides"] == 0
    assert result["final_score"]["grade"] == "D"
    assert result["overrides"]["applied"] is True


def test_run_layer3_skip_phase_c_and_reporting_returns_minimal():
    from v2.layer3 import run_layer3

    result = run_layer3(_all_perfect_evaluations(), skip_phase_c_and_reporting=True)
    assert result["final_score"]["raw_total"] == 100
    # 스킵 시 consistency/grade 비어있어야
    assert result["consistency_flags"] == []
    assert result["grade_detail"] == {}
    assert result["final_score"]["grade"] == ""


def test_run_layer3_outputs_dev5_schema_shape():
    """Dev5 OverridesBlock / FinalScoreBlock 호환 구조 확인."""
    from v2.layer3 import run_layer3

    result = run_layer3(_all_perfect_evaluations())

    # final_score shape
    assert set(result["final_score"].keys()) == {"raw_total", "after_overrides", "grade"}

    # overrides shape
    assert "applied" in result["overrides"]
    assert "reasons" in result["overrides"]

    # category_scores shape
    for cat in result["category_scores"]:
        assert set(cat.keys()) >= {"category_key", "category", "max_score", "achieved_score", "items"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
