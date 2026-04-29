# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 4 report_generator_v2 통합 유닛 테스트."""

from __future__ import annotations

from typing import Any

from v2.layer4 import generate_report_v2, refine_evidence
from v2.layer4.report_generator_v2 import report_generator_node


def _item(n, name, mode, score, mx, **extra):
    base = {
        "item_number": n, "item_name": name, "max_score": mx, "score": score,
        "evaluation_mode": mode, "judgment": f"#{n} ok",
        "evidence": [{"speaker": "상담사", "timestamp": "00:00:02", "quote": "인용", "turn_id": 0}]
                    if mode == "full" else [],
        "llm_self_confidence": {"score": 4, "rationale": None},
        "rule_llm_delta": None,
    }
    base.update(extra)
    return base


def _base_state() -> dict[str, Any]:
    return {
        "consultation_id": "TEST_001",
        "tenant_id": "generic",
        "evaluated_at": "2026-04-20T10:00:00Z",
        "versions": {"model": "m", "rubric": "r", "prompt_bundle": "p", "golden_set": "g"},
        "masking_format": {"version": "v1_symbolic", "spec": "***"},
        "stt_metadata": {"transcription_confidence": 0.9, "speaker_diarization_success": True,
                         "duration_sec": 120.0, "has_timestamps": True},
        "preprocessing": {
            "intent_type": "일반문의",
            "detected_sections": {"opening": [0, 3], "body": [3, 20], "closing": [20, 25]},
            "deduction_triggers": {"불친절": False, "개인정보_유출": False, "오안내_미정정": False},
            "pii_tokens": [],
            "turns": [{"turn_id": 0, "speaker": "상담사", "timestamp": "00:00:02", "text": "인용입니다"}],
        },
        "orchestrator": {"overrides_applied": [], "total_score": 0, "total_after_overrides": 0},
    }


class TestAssembly:
    def test_category_grouping_uses_category_meta(self):
        state = _base_state()
        state["sub_agent_responses"] = [{
            "category": "greeting_etiquette", "status": "success",
            "items": [_item(1, "첫인사", "full", 5, 5), _item(2, "끝인사", "full", 3, 5)],
        }]
        report = generate_report_v2(state)
        assert len(report.evaluation.categories) == 1
        cat = report.evaluation.categories[0]
        assert cat.category_key == "greeting_etiquette"
        assert cat.achieved_score == 8
        assert cat.max_score == 10

    def test_skipped_item_scored_as_max(self):
        state = _base_state()
        state["sub_agent_responses"] = [{
            "category": "listening_communication", "status": "success",
            "items": [_item(3, "경청", "skipped", None, 5),
                      _item(4, "호응", "full", 5, 5),
                      _item(5, "대기", "full", 5, 5)],
        }]
        report = generate_report_v2(state)
        cat = report.evaluation.categories[0]
        assert cat.achieved_score == 15
        assert cat.max_score == 15
        item3 = next(i for i in cat.items if i.item_number == 3)
        assert item3.score == 5

    def test_unevaluable_item_excluded_from_denominator(self):
        state = _base_state()
        state["sub_agent_responses"] = [{
            "category": "explanation_delivery", "status": "success",
            "items": [_item(10, "명확성", "unevaluable", None, 10),
                      _item(11, "두괄식", "full", 5, 5)],
        }]
        report = generate_report_v2(state)
        cat = report.evaluation.categories[0]
        # #10 unevaluable → 분자/분모 모두 제외
        assert cat.achieved_score == 5
        assert cat.max_score == 5

    def test_total_score_is_sum_of_categories(self):
        state = _base_state()
        state["sub_agent_responses"] = [
            {"category": "greeting_etiquette", "status": "success",
             "items": [_item(1, "첫인사", "full", 5, 5), _item(2, "끝인사", "full", 5, 5)]},
            {"category": "language_expression", "status": "success",
             "items": [_item(6, "정중", "full", 5, 5), _item(7, "쿠션어", "skipped", None, 5)]},
        ]
        report = generate_report_v2(state)
        total = sum(c.achieved_score for c in report.evaluation.categories)
        max_total = sum(c.max_score for c in report.evaluation.categories)
        assert total == 20
        assert max_total == 20


class TestForceT3:
    def test_privacy_items_evaluable_trigger_t3(self):
        state = _base_state()
        state["sub_agent_responses"] = [{
            "category": "privacy_protection", "status": "success",
            "items": [_item(17, "정보확인절차", "compliance_based", 5, 5),
                      _item(18, "정보보호준수", "compliance_based", 5, 5)],
        }]
        report = generate_report_v2(state)
        assert report.routing.decision == "T3"
        assert report.routing.hitl_driver == "policy_driven"
        # 각 item 의 force_t3 필드
        items = report.evaluation.categories[0].items
        assert all(it.force_t3 for it in items)

    def test_item15_partial_forces_t3(self):
        state = _base_state()
        state["sub_agent_responses"] = [{
            "category": "work_accuracy", "status": "success",
            "items": [_item(15, "정확한안내", "partial_with_review", 5, 10),
                      _item(16, "필수안내", "full", 5, 5)],
        }]
        report = generate_report_v2(state)
        assert report.routing.decision == "T3"
        reasons = report.routing.tier_reasons
        assert "accuracy_partial_with_review" in reasons


class TestEvidenceRefiner:
    def test_turn_id_lookup_fills_missing_fields(self):
        turns = [{"turn_id": 5, "speaker": "상담사", "timestamp": "00:01:00", "text": "안녕하세요 반갑습니다"}]
        refined = refine_evidence(
            [{"speaker": "", "timestamp": None, "quote": "안녕하세요", "turn_id": 5}],
            turns=turns, evaluation_mode="full",
        )
        assert len(refined) == 1
        assert refined[0]["speaker"] == "상담사"
        assert refined[0]["timestamp"] == "00:01:00"

    def test_empty_quote_removed(self):
        refined = refine_evidence(
            [{"speaker": "상담사", "timestamp": None, "quote": "", "turn_id": 0}],
            turns=[], evaluation_mode="full",
        )
        assert refined == []

    def test_dedup_same_turn(self):
        """동일 turn_id + 동일 quote → 1건으로 축약."""
        refined = refine_evidence(
            [
                {"speaker": "상담사", "timestamp": None, "quote": "hello there", "turn_id": 1},
                {"speaker": "상담사", "timestamp": None, "quote": "hello there", "turn_id": 1},
            ],
            turns=[], evaluation_mode="full",
        )
        assert len(refined) == 1

    def test_hallucination_dropped(self):
        turns = [{"turn_id": 0, "speaker": "상담사", "timestamp": None, "text": "요금제 안내드립니다"}]
        refined = refine_evidence(
            [{"speaker": "상담사", "timestamp": None, "quote": "완전히 다른 문장으로 홀루시네이션",
              "turn_id": 0}],
            turns=turns, evaluation_mode="full",
        )
        assert refined == []


class TestSkipPhaseCAndReporting:
    def test_node_returns_empty_when_skip_flag(self):
        state = _base_state()
        state["plan"] = {"skip_phase_c_and_reporting": True}
        out = report_generator_node(state)
        assert out == {}

    def test_node_builds_report_without_skip(self):
        state = _base_state()
        state["sub_agent_responses"] = [{
            "category": "greeting_etiquette", "status": "success",
            "items": [_item(1, "첫인사", "full", 5, 5), _item(2, "끝인사", "full", 5, 5)],
        }]
        out = report_generator_node(state)
        assert "report" in out
        assert "routing" in out
        assert out["current_phase"] == "complete"


class TestOverrideSerialization:
    def test_override_entry_via_state(self):
        state = _base_state()
        state["orchestrator"]["overrides_applied"] = [
            {"trigger": "privacy_leak", "action": "category_zero",
             "affected_items": [17, 18], "reason": "PII leaked in turn 12"}
        ]
        state["sub_agent_responses"] = [{
            "category": "privacy_protection", "status": "success",
            "items": [_item(17, "정보확인절차", "compliance_based", 0, 5),
                      _item(18, "정보보호준수", "compliance_based", 0, 5)],
        }]
        report = generate_report_v2(state)
        assert report.overrides.applied is True
        assert report.overrides.reasons[0].trigger == "privacy_leak"
        assert report.overrides.reasons[0].action == "category_zero"
