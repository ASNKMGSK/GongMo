# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""intent_type Union (str | dict) + intent_type_primary sibling 테스트 (PL 승인 2026-04-20)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from v2.layer4 import generate_report_v2
from v2.layer4.report_generator_v2 import _resolve_intent_type
from v2.schemas.qa_output_v2 import (
    DeductionTriggersBlock,
    DetectedSectionRange,
    DetectedSections,
    PreprocessingBlock,
)


def _sections():
    return DetectedSections(
        opening=DetectedSectionRange(start=0, end=3),
        body=DetectedSectionRange(start=3, end=20),
        closing=DetectedSectionRange(start=20, end=25),
    )


class TestPreprocessingBlockIntentType:
    def test_str_intent_type_valid(self):
        pb = PreprocessingBlock(
            intent_type="상품문의", intent_type_primary="상품문의",
            detected_sections=_sections(),
            deduction_triggers=DeductionTriggersBlock(),
            pii_tokens=[],
        )
        js = pb.model_dump()
        assert js["intent_type"] == "상품문의"
        assert js["intent_type_primary"] == "상품문의"

    def test_dict_intent_type_valid(self):
        intent_dict = {"primary_intent": "상품문의", "sub_intents": ["가입"],
                       "product": "자동이체", "complexity": "moderate"}
        pb = PreprocessingBlock(
            intent_type=intent_dict, intent_type_primary="상품문의",
            detected_sections=_sections(),
            deduction_triggers=DeductionTriggersBlock(),
        )
        js = pb.model_dump()
        assert js["intent_type"]["primary_intent"] == "상품문의"
        assert js["intent_type"]["product"] == "자동이체"
        assert js["intent_type_primary"] == "상품문의"

    def test_empty_primary_rejected(self):
        with pytest.raises(ValidationError):
            PreprocessingBlock(
                intent_type="x", intent_type_primary="   ",
                detected_sections=_sections(),
                deduction_triggers=DeductionTriggersBlock(),
            )


class TestResolveIntentType:
    def test_rule0_explicit_primary_respected(self):
        """Dev1 Layer 1 이 preprocessing.intent_type_primary 를 명시 세팅하면 최우선."""
        raw, primary = _resolve_intent_type({
            "intent_type": "상품문의",
            "intent_type_primary": "해지문의",
        })
        assert raw == "상품문의"
        assert primary == "해지문의"

    def test_rule0_with_dict_intent_keeps_raw(self):
        raw, primary = _resolve_intent_type({
            "intent_type": {"primary_intent": "원본"},
            "intent_type_primary": "override명",
        })
        assert raw == {"primary_intent": "원본"}
        assert primary == "override명"

    def test_rule0_without_raw_uses_primary_for_both(self):
        raw, primary = _resolve_intent_type({"intent_type_primary": "일반문의"})
        assert raw == "일반문의"
        assert primary == "일반문의"

    def test_rule0_empty_primary_falls_through(self):
        raw, primary = _resolve_intent_type({
            "intent_type": "상품문의",
            "intent_type_primary": "   ",
        })
        assert raw == "상품문의"
        assert primary == "상품문의"

    def test_dict_source_extracts_primary(self):
        raw, primary = _resolve_intent_type({"intent_type": {"primary_intent": "변경요청"}})
        assert raw == {"primary_intent": "변경요청"}
        assert primary == "변경요청"

    def test_str_source_passes_through(self):
        raw, primary = _resolve_intent_type({"intent_type": "상품문의"})
        assert raw == "상품문의"
        assert primary == "상품문의"

    def test_str_with_detail_sibling_uses_detail_primary(self):
        raw, primary = _resolve_intent_type({
            "intent_type": "상품문의",
            "intent_detail": {"primary_intent": "가입문의"},
        })
        assert raw == "상품문의"
        assert primary == "가입문의"  # detail 우선

    def test_missing_intent_falls_back_to_detail(self):
        raw, primary = _resolve_intent_type({
            "intent_detail": {"primary_intent": "이관"},
        })
        assert raw == "이관"
        assert primary == "이관"

    def test_all_missing_returns_general(self):
        raw, primary = _resolve_intent_type({})
        assert raw == "general"
        assert primary == "general"

    def test_dict_empty_primary_falls_back_to_general(self):
        raw, primary = _resolve_intent_type({"intent_type": {"sub_intents": []}})
        assert raw == {"sub_intents": []}
        assert primary == "general"


class TestGenerateReportV2IntentType:
    def _base_state(self):
        return {
            "consultation_id": "T",
            "tenant_id": "generic",
            "versions": {"model": "m", "rubric": "r", "prompt_bundle": "p", "golden_set": "g"},
            "preprocessing": {
                "detected_sections": {"opening": [0, 3], "body": [3, 20], "closing": [20, 25]},
                "deduction_triggers": {},
                "pii_tokens": [],
            },
            "sub_agent_responses": [{
                "category": "greeting_etiquette", "status": "success",
                "items": [{
                    "item_number": 1, "item_name": "첫인사", "max_score": 5, "score": 5,
                    "evaluation_mode": "full", "judgment": "ok",
                    "evidence": [{"speaker": "상담사", "timestamp": None, "quote": "안녕하세요", "turn_id": 0}],
                    "llm_self_confidence": {"score": 5},
                    "rule_llm_delta": None,
                }],
            }],
            "orchestrator": {"overrides_applied": [], "total_score": 5, "total_after_overrides": 5},
        }

    def test_str_intent_serialized(self):
        state = self._base_state()
        state["preprocessing"]["intent_type"] = "상품문의"
        report = generate_report_v2(state)
        js = report.model_dump(mode="json")
        assert js["preprocessing"]["intent_type"] == "상품문의"
        assert js["preprocessing"]["intent_type_primary"] == "상품문의"

    def test_dict_intent_serialized(self):
        state = self._base_state()
        state["preprocessing"]["intent_type"] = {
            "primary_intent": "해지", "sub_intents": ["만기해지"],
            "product": "약정", "complexity": "complex",
        }
        report = generate_report_v2(state)
        js = report.model_dump(mode="json")
        assert isinstance(js["preprocessing"]["intent_type"], dict)
        assert js["preprocessing"]["intent_type"]["primary_intent"] == "해지"
        assert js["preprocessing"]["intent_type_primary"] == "해지"
