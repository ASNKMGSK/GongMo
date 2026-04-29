# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase A1 계약 스키마 pydantic 검증 — Dev2/Dev3 합의 사항 반영."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from v2.schemas.enums import CATEGORY_META, FORCE_T3_ITEMS
from v2.schemas.qa_output_v2 import (
    CategoryBlock,
    ConfidenceBlock,
    ConfidenceSignals,
    DeductionTriggersBlock,
    EvaluationBlock,
    ItemResult,
    OverrideEntry,
)


def _conf(final=5, quality="high"):
    return ConfidenceBlock(
        final=final,
        signals=ConfidenceSignals(
            llm_self=final, rule_llm_agreement=True, evidence_quality=quality,
        ),
    )


def _ev():
    return [{"speaker": "상담사", "timestamp": "00:00:02", "quote": "인용", "turn_id": 0}]


class TestItemResultValidation:
    def test_full_requires_evidence(self):
        with pytest.raises(ValidationError):
            ItemResult(item="x", item_number=1, max_score=5,
                       evaluation_mode="full", score=5, judgment="x",
                       evidence=[], confidence=_conf())

    def test_compliance_based_allows_empty_evidence(self):
        ItemResult(item="x", item_number=17, max_score=5,
                   evaluation_mode="compliance_based", score=5, judgment="x",
                   evidence=[], confidence=_conf(), force_t3=True)

    def test_unevaluable_allows_score_none(self):
        ItemResult(item="x", item_number=17, max_score=5,
                   evaluation_mode="unevaluable", score=None, judgment="",
                   evidence=[], confidence=_conf(final=1, quality="low"),
                   force_t3=True, mandatory_human_review=True)

    def test_full_rejects_score_none(self):
        with pytest.raises(ValidationError):
            ItemResult(item="x", item_number=1, max_score=5,
                       evaluation_mode="full", score=None, judgment="x",
                       evidence=_ev(), confidence=_conf())

    def test_score_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            ItemResult(item="x", item_number=1, max_score=5,
                       evaluation_mode="full", score=10, judgment="x",
                       evidence=_ev(), confidence=_conf())

    def test_skipped_allows_empty_evidence_and_full_score(self):
        ItemResult(item="경청", item_number=3, max_score=5,
                   evaluation_mode="skipped", score=5, judgment="skipped",
                   evidence=[], confidence=_conf())


class TestDeductionTriggersBlock:
    def test_canonical_3_keys_only(self):
        b = DeductionTriggersBlock(**{"불친절": True})
        out = b.model_dump(by_alias=True)
        assert set(out.keys()) == {"불친절", "개인정보_유출", "오안내_미정정"}
        assert out["불친절"] is True
        assert out["개인정보_유출"] is False
        assert out["오안내_미정정"] is False

    def test_attr_access_via_python_names(self):
        b = DeductionTriggersBlock(**{"불친절": True, "오안내_미정정": True})
        assert b.rudeness is True
        assert b.uncorrected_misinfo is True
        assert b.privacy_leak is False


class TestOverrideEntry:
    @pytest.mark.parametrize("trigger", [
        "profanity", "contempt", "arbitrary_disconnect",
        "preemptive_disclosure", "privacy_leak", "uncorrected_misinfo",
    ])
    def test_all_canonical_triggers(self, trigger):
        e = OverrideEntry(trigger=trigger, action="item_zero", reason="x")
        assert e.trigger == trigger

    def test_invalid_trigger_rejected(self):
        with pytest.raises(ValidationError):
            OverrideEntry(trigger="some_random", action="item_zero", reason="x")


class TestCategoryMeta:
    def test_total_max_score_is_100(self):
        assert sum(meta["max_score"] for meta in CATEGORY_META.values()) == 100

    def test_all_18_items_covered(self):
        covered = set()
        for meta in CATEGORY_META.values():
            covered.update(meta["items"])
        assert covered == set(range(1, 19))

    def test_force_t3_items_contain_9_17_18(self):
        assert FORCE_T3_ITEMS == frozenset({9, 17, 18})


class TestEvaluationBlock:
    def test_rejects_more_than_8_categories(self):
        good = CategoryBlock(category="인사 예절", category_key="greeting_etiquette",
                             max_score=10, achieved_score=10, items=[])
        EvaluationBlock(categories=[good])
        with pytest.raises(ValidationError):
            EvaluationBlock(categories=[good] * 9)
