# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Dev1 DeductionTriggerResult ↔ Dev5 OverridesBlock 어댑터 유닛 테스트."""

from __future__ import annotations

from v2.layer4.overrides_adapter import apply_overrides_to_scores, build_overrides_block
from v2.schemas.enums import CATEGORY_META
from v2.schemas.qa_output_v2 import ConfidenceBlock, ConfidenceSignals, ItemResult


def _ev():
    return [{"speaker": "상담사", "timestamp": "00:00:02", "quote": "인용", "turn_id": 0}]


def _conf():
    return ConfidenceBlock(
        final=5,
        signals=ConfidenceSignals(llm_self=5, rule_llm_agreement=True, evidence_quality="high"),
    )


def _item(n, score, mode="full", mx=5):
    return ItemResult(
        item=f"item{n}", item_number=n, max_score=mx,
        evaluation_mode=mode, score=score, judgment="x",
        evidence=_ev() if mode == "full" else [],
        confidence=_conf(),
    )


class TestBuildOverridesBlock:
    def test_recommended_none_returns_empty(self):
        ob = build_overrides_block(recommended_override="none")
        assert ob.applied is False
        assert ob.reasons == []

    def test_all_zero_covers_all_items(self):
        ob = build_overrides_block(
            has_all_zero_trigger=True,
            triggers=[{
                "trigger_type": "profanity", "turn_id": 5,
                "evidence_text": "**욕설**", "recommended_override": "all_zero",
            }],
        )
        assert ob.applied is True
        assert len(ob.reasons) == 1
        entry = ob.reasons[0]
        assert entry.action == "all_zero"
        assert entry.trigger == "profanity"
        assert entry.affected_items == list(range(1, 19))
        assert len(entry.evidence) == 1
        assert entry.evidence[0]["quote"] == "**욕설**"

    def test_category_zero_expands_category_items(self):
        ob = build_overrides_block(
            has_category_zero_categories=["privacy_protection"],
            triggers=[{
                "trigger_type": "privacy_leak", "turn_id": 12,
                "evidence_text": "제3자에게 PII 안내",
                "recommended_override": "category_zero",
                "category_key": "privacy_protection",
            }],
        )
        assert ob.applied is True
        assert len(ob.reasons) == 1
        entry = ob.reasons[0]
        assert entry.action == "category_zero"
        assert entry.trigger == "privacy_leak"
        assert set(entry.affected_items) == set(CATEGORY_META["privacy_protection"]["items"])

    def test_item_zero_only_that_item(self):
        ob = build_overrides_block(
            triggers=[{
                "trigger_type": "uncorrected_misinfo", "turn_id": 20, "item_number": 15,
                "evidence_text": "요금제 정보 오안내", "recommended_override": "item_zero",
            }],
        )
        assert ob.applied is True
        entry = ob.reasons[0]
        assert entry.action == "item_zero"
        assert entry.affected_items == [15]
        assert entry.trigger == "uncorrected_misinfo"

    def test_unknown_category_key_skipped(self):
        ob = build_overrides_block(has_category_zero_categories=["nonexistent_category"])
        assert ob.applied is False
        assert ob.reasons == []

    def test_multiple_overrides_combined(self):
        ob = build_overrides_block(
            has_all_zero_trigger=True,
            has_category_zero_categories=["privacy_protection"],
            triggers=[
                {"trigger_type": "profanity", "recommended_override": "all_zero"},
                {"trigger_type": "privacy_leak", "recommended_override": "category_zero",
                 "category_key": "privacy_protection"},
                {"trigger_type": "uncorrected_misinfo", "recommended_override": "item_zero",
                 "item_number": 15},
            ],
        )
        actions = sorted(e.action for e in ob.reasons)
        assert actions == ["all_zero", "category_zero", "item_zero"]


class TestApplyOverrides:
    def test_all_zero_sets_every_score_to_zero(self):
        items = [_item(1, 5), _item(2, 5), _item(3, 5, mode="skipped"), _item(17, 5, mode="compliance_based")]
        ob = build_overrides_block(
            has_all_zero_trigger=True,
            triggers=[{"trigger_type": "profanity", "recommended_override": "all_zero"}],
        )
        out = apply_overrides_to_scores(items, overrides_block=ob)
        # skipped 는 제외, 나머지 0점
        scores_by_id = {i.item_number: i.score for i in out}
        assert scores_by_id[1] == 0
        assert scores_by_id[2] == 0
        assert scores_by_id[3] == 5  # skipped 제외
        assert scores_by_id[17] == 0

    def test_category_zero_only_affected_items(self):
        items = [_item(1, 5), _item(17, 5, mode="compliance_based"), _item(18, 5, mode="compliance_based")]
        ob = build_overrides_block(
            has_category_zero_categories=["privacy_protection"],
            triggers=[{"trigger_type": "privacy_leak", "recommended_override": "category_zero",
                       "category_key": "privacy_protection"}],
        )
        out = apply_overrides_to_scores(items, overrides_block=ob)
        scores_by_id = {i.item_number: i.score for i in out}
        assert scores_by_id[1] == 5
        assert scores_by_id[17] == 0
        assert scores_by_id[18] == 0

    def test_unevaluable_untouched(self):
        items = [_item(17, None, mode="unevaluable")]
        ob = build_overrides_block(
            has_category_zero_categories=["privacy_protection"],
            triggers=[{"trigger_type": "privacy_leak", "recommended_override": "category_zero",
                       "category_key": "privacy_protection"}],
        )
        out = apply_overrides_to_scores(items, overrides_block=ob)
        assert out[0].score is None  # unevaluable 유지

    def test_empty_overrides_is_noop(self):
        items = [_item(1, 5)]
        ob = build_overrides_block(recommended_override="none")
        out = apply_overrides_to_scores(items, overrides_block=ob)
        assert out is items
