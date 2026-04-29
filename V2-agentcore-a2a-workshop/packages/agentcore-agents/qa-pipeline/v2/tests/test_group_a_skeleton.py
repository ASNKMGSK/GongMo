# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Group A Sub Agent skeleton 테스트 — Phase D1 통합 전 경량 검증.

LLM/RAG 호출 없이 헬퍼 로직 + 분기 판정 + 스키마 준수 확인.
"""

from __future__ import annotations

import pytest

from v2.agents.group_a._shared import (
    build_item_verdict,
    build_sub_agent_response,
    format_fewshot_block,
    get_intent,
    get_rule_pre_verdict,
    is_quality_unevaluable,
    rule_evidence_to_evidence_quote,
    should_bypass_llm,
)
from v2.agents.group_a.listening_comm import _build_skipped_full_item_3, _build_skipped_full_item_5
from v2.agents.group_a.language import _build_bypass_item_6, _build_skipped_full_item_7
from v2.schemas.enums import CATEGORY_META, FORCE_T3_ITEMS


# ---------------------------------------------------------------------------
# preprocessing consume
# ---------------------------------------------------------------------------


def test_get_intent_default_general_inquiry():
    assert get_intent({}) == "general_inquiry"
    assert get_intent({"intent_type": "상품문의"}) == "상품문의"


def test_get_rule_pre_verdict_zero_padded_key():
    pre = {"rule_pre_verdicts": {"item_01": {"score": 5}}}
    assert get_rule_pre_verdict(pre, 1) == {"score": 5}
    assert get_rule_pre_verdict(pre, 2) is None


def test_is_quality_unevaluable_true_false():
    assert is_quality_unevaluable({"quality": {"unevaluable": True}}) is True
    assert is_quality_unevaluable({"quality": {"unevaluable": False}}) is False
    assert is_quality_unevaluable({}) is False


# ---------------------------------------------------------------------------
# hybrid 3안 분기 (Dev1 합의)
# ---------------------------------------------------------------------------


def test_should_bypass_llm_hard_mode_recommended_false_then_bypass():
    rv = {"confidence_mode": "hard", "recommended_for_llm_verify": False}
    assert should_bypass_llm(rv) is True


def test_should_bypass_llm_soft_mode_no_bypass():
    rv = {"confidence_mode": "soft", "recommended_for_llm_verify": False}
    assert should_bypass_llm(rv) is False


def test_should_bypass_llm_recommended_true_no_bypass():
    rv = {"confidence_mode": "hard", "recommended_for_llm_verify": True}
    assert should_bypass_llm(rv) is False


def test_should_bypass_llm_none_no_bypass():
    assert should_bypass_llm(None) is False


# ---------------------------------------------------------------------------
# build_item_verdict — snap_score_v2 / force_t3 / mandatory_human_review
# ---------------------------------------------------------------------------


def test_build_item_verdict_snap_score_v2_item_17_three():
    """#17 = [5, 3, 0] 확장 — 3점 유지 (V1 snap_score 는 3→0 변환, V2 는 3 유지)."""
    verdict = build_item_verdict(
        item_number=17, item_name="정보 확인 절차", max_score=5,
        raw_score=3, evaluation_mode="compliance_based",
        judgment="일부 이행", evidence=[],
        llm_self=4, rule_verdict=None,
    )
    assert verdict["score"] == 3


def test_build_item_verdict_snap_score_v2_item_3_always_five():
    """#3 = [5] skipped 고정 — 어떤 raw_score 도 5 반환."""
    verdict = build_item_verdict(
        item_number=3, item_name="경청", max_score=5,
        raw_score=0, evaluation_mode="skipped",
        judgment="STT 마커 없음", evidence=[],
        llm_self=5, rule_verdict=None,
    )
    assert verdict["score"] == 5


def test_build_item_verdict_force_t3_auto_item_9():
    """#9 은 FORCE_T3_ITEMS={9,17,18} 이라 force_t3=True 자동."""
    verdict = build_item_verdict(
        item_number=9, item_name="고객정보 확인", max_score=5,
        raw_score=5, evaluation_mode="structural_only",
        judgment="양해 동반", evidence=[],
        llm_self=4, rule_verdict=None,
    )
    assert verdict["force_t3"] is True
    assert 9 in FORCE_T3_ITEMS


def test_build_item_verdict_force_t3_not_other_items():
    verdict = build_item_verdict(
        item_number=1, item_name="첫인사", max_score=5,
        raw_score=5, evaluation_mode="full",
        judgment="3요소 충족", evidence=[],
        llm_self=5, rule_verdict=None,
    )
    assert verdict["force_t3"] is False


def test_build_item_verdict_mandatory_human_review_self_confidence_low():
    """llm_self ≤ 2 → mandatory_human_review=True."""
    verdict = build_item_verdict(
        item_number=4, item_name="호응 및 공감", max_score=5,
        raw_score=3, evaluation_mode="full",
        judgment="...", evidence=[],
        llm_self=2, rule_verdict=None,
    )
    assert verdict["mandatory_human_review"] is True


def test_build_item_verdict_mandatory_human_review_unevaluable_mode():
    verdict = build_item_verdict(
        item_number=1, item_name="첫인사", max_score=5,
        raw_score=0, evaluation_mode="unevaluable",
        judgment="STT 품질 저하", evidence=[],
        llm_self=1, rule_verdict=None,
    )
    assert verdict["mandatory_human_review"] is True


def test_build_item_verdict_rule_llm_agreement_signal():
    rv = {"score": 5, "confidence": 0.9}
    verdict = build_item_verdict(
        item_number=1, item_name="첫인사", max_score=5,
        raw_score=5, evaluation_mode="full",
        judgment="...", evidence=[],
        llm_self=5, rule_verdict=rv,
    )
    assert verdict["confidence"]["signals"]["rule_llm_agreement"] is True


def test_build_item_verdict_rule_llm_disagreement():
    rv = {"score": 5, "confidence": 0.85}
    verdict = build_item_verdict(
        item_number=1, item_name="첫인사", max_score=5,
        raw_score=3, evaluation_mode="full",
        judgment="...", evidence=[],
        llm_self=4, rule_verdict=rv,
    )
    assert verdict["confidence"]["signals"]["rule_llm_agreement"] is False


def test_build_item_verdict_rag_stdev_optional():
    verdict = build_item_verdict(
        item_number=1, item_name="첫인사", max_score=5,
        raw_score=5, evaluation_mode="skipped",  # skipped 는 evidence=[] 허용
        judgment="...", evidence=[],
        llm_self=5, rule_verdict=None, rag_stdev=1.23,
    )
    assert verdict["confidence"]["signals"]["rag_stdev"] == 1.23


# ---------------------------------------------------------------------------
# Evidence 강제 규칙 (PL 2026-04-20 재공지, 원칙 3)
# ---------------------------------------------------------------------------


def test_evidence_empty_full_downgrades_to_partial_with_review():
    """full + evidence=[] → partial_with_review 자동 다운그레이드."""
    verdict = build_item_verdict(
        item_number=6, item_name="정중한 표현", max_score=5,
        raw_score=5, evaluation_mode="full",
        judgment="...", evidence=[],
        llm_self=5, rule_verdict=None,
    )
    assert verdict["evaluation_mode"] == "partial_with_review"
    assert verdict["mandatory_human_review"] is True
    assert "evidence" in (verdict.get("mode_reason") or "")


def test_evidence_empty_structural_only_downgrades():
    """structural_only + evidence=[] → partial_with_review."""
    verdict = build_item_verdict(
        item_number=9, item_name="고객정보 확인", max_score=5,
        raw_score=5, evaluation_mode="structural_only",
        judgment="...", evidence=[],
        llm_self=5, rule_verdict=None,
    )
    assert verdict["evaluation_mode"] == "partial_with_review"


def test_evidence_empty_skipped_allowed():
    """skipped + evidence=[] 은 허용 — mode 유지."""
    verdict = build_item_verdict(
        item_number=3, item_name="경청", max_score=5,
        raw_score=5, evaluation_mode="skipped",
        judgment="STT 마커 없음", evidence=[],
        llm_self=5, rule_verdict=None,
    )
    assert verdict["evaluation_mode"] == "skipped"


def test_evidence_present_no_downgrade():
    """full + evidence 1개 이상 → mode 유지."""
    ev = [{"speaker": "상담사", "timestamp": None, "quote": "안녕하세요", "turn_id": 1}]
    verdict = build_item_verdict(
        item_number=1, item_name="첫인사", max_score=5,
        raw_score=5, evaluation_mode="full",
        judgment="...", evidence=ev,
        llm_self=5, rule_verdict=None,
    )
    assert verdict["evaluation_mode"] == "full"


# ---------------------------------------------------------------------------
# build_sub_agent_response — category meta + achieved_score 집계
# ---------------------------------------------------------------------------


def test_build_sub_agent_response_greeting_category_meta():
    item_1 = build_item_verdict(
        item_number=1, item_name="첫인사", max_score=5, raw_score=5,
        evaluation_mode="full", judgment="", evidence=[],
        llm_self=5, rule_verdict=None,
    )
    item_2 = build_item_verdict(
        item_number=2, item_name="끝인사", max_score=5, raw_score=3,
        evaluation_mode="full", judgment="", evidence=[],
        llm_self=4, rule_verdict=None,
    )
    resp = build_sub_agent_response(
        category_key="greeting_etiquette",
        agent_id="greeting-agent",
        items=[item_1, item_2],
    )
    assert resp["category"] == CATEGORY_META["greeting_etiquette"]["label_ko"]
    assert resp["max_score"] == 10
    assert resp["achieved_score"] == 8
    assert resp["agent_id"] == "greeting-agent"


def test_build_sub_agent_response_listening_category_meta():
    item_3 = build_item_verdict(
        item_number=3, item_name="경청", max_score=5, raw_score=5,
        evaluation_mode="skipped", judgment="", evidence=[],
        llm_self=5, rule_verdict=None,
    )
    item_4 = build_item_verdict(
        item_number=4, item_name="호응 및 공감", max_score=5, raw_score=5,
        evaluation_mode="full", judgment="", evidence=[],
        llm_self=5, rule_verdict=None,
    )
    item_5 = build_item_verdict(
        item_number=5, item_name="대기 멘트", max_score=5, raw_score=5,
        evaluation_mode="skipped", judgment="", evidence=[],
        llm_self=5, rule_verdict=None,
    )
    resp = build_sub_agent_response(
        category_key="listening_communication",
        agent_id="listening-communication-agent",
        items=[item_3, item_4, item_5],
    )
    assert resp["max_score"] == 15
    assert resp["achieved_score"] == 15


# ---------------------------------------------------------------------------
# skipped/bypass builders
# ---------------------------------------------------------------------------


def test_skipped_full_item_3_score_five():
    verdict = _build_skipped_full_item_3(None)
    assert verdict["score"] == 5
    assert verdict["evaluation_mode"] == "skipped"


def test_skipped_full_item_5_score_five():
    verdict = _build_skipped_full_item_5(None, reason="대기 미발생")
    assert verdict["score"] == 5
    assert verdict["evaluation_mode"] == "skipped"
    assert verdict["mode_reason"] == "대기 미발생"


def test_bypass_item_6_rule_score_propagated():
    """Rule bypass 시 rule evidence 를 EvidenceQuote 로 재사용 → mode=full 유지."""
    rv = {
        "score": 5, "rationale": "부적절 표현 미감지",
        "evidence_turn_ids": [3], "evidence_snippets": ["네 감사합니다"],
    }
    verdict = _build_bypass_item_6(rv)
    assert verdict["score"] == 5
    assert verdict["evaluation_mode"] == "full"
    assert len(verdict["evidence"]) >= 1


def test_skipped_full_item_7_no_refusal_flag():
    verdict = _build_skipped_full_item_7(None)
    assert verdict["score"] == 5
    assert verdict["evaluation_mode"] == "skipped"
    assert verdict["flag"] == "no_refusal"


# ---------------------------------------------------------------------------
# Evidence / fewshot helpers
# ---------------------------------------------------------------------------


def test_rule_evidence_to_evidence_quote_shapes():
    rv = {"evidence_turn_ids": [1, 3], "evidence_snippets": ["안녕", "감사"]}
    out = rule_evidence_to_evidence_quote(rv)
    assert len(out) == 2
    assert out[0]["speaker"] == "상담사"
    assert out[0]["turn_id"] == 1
    assert out[0]["quote"] == "안녕"
    assert out[1]["turn_id"] == 3


def test_rule_evidence_to_evidence_quote_empty():
    assert rule_evidence_to_evidence_quote(None) == []
    assert rule_evidence_to_evidence_quote({}) == []


def test_format_fewshot_block_empty_returns_blank():
    assert format_fewshot_block([]) == ""


def test_format_fewshot_block_nonempty_has_header():
    block = format_fewshot_block([
        {"score": 5, "score_bucket": "full", "segment_text": "안녕하세요", "rationale": "3요소 충족"}
    ])
    assert "Few-shot" in block
    assert "안녕하세요" in block
    assert "3요소 충족" in block


# ---------------------------------------------------------------------------
# Sub Agent entrypoint — quality.unevaluable 경로
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_greeting_sub_agent_unevaluable_path():
    from v2.agents.group_a.greeting import greeting_sub_agent
    resp = await greeting_sub_agent(
        preprocessing={"quality": {"unevaluable": True}},
    )
    assert resp["status"] == "partial"
    assert len(resp["items"]) == 2
    assert all(it["evaluation_mode"] == "unevaluable" for it in resp["items"])
    assert all(it["mandatory_human_review"] is True for it in resp["items"])


@pytest.mark.asyncio
async def test_listening_comm_sub_agent_unevaluable_path():
    from v2.agents.group_a.listening_comm import listening_comm_sub_agent
    resp = await listening_comm_sub_agent(
        preprocessing={"quality": {"unevaluable": True}},
    )
    assert len(resp["items"]) == 3
    assert all(it["evaluation_mode"] == "unevaluable" for it in resp["items"])


@pytest.mark.asyncio
async def test_language_sub_agent_unevaluable_path():
    from v2.agents.group_a.language import language_sub_agent
    resp = await language_sub_agent(
        preprocessing={"quality": {"unevaluable": True}},
    )
    assert len(resp["items"]) == 2
    assert all(it["evaluation_mode"] == "unevaluable" for it in resp["items"])


@pytest.mark.asyncio
async def test_needs_sub_agent_unevaluable_path():
    from v2.agents.group_a.needs import needs_sub_agent
    resp = await needs_sub_agent(
        preprocessing={"quality": {"unevaluable": True}},
    )
    assert len(resp["items"]) == 2
    assert all(it["evaluation_mode"] == "unevaluable" for it in resp["items"])


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_greeting_sub_agent_unevaluable_path())
    print("greeting unevaluable OK")
    asyncio.run(test_listening_comm_sub_agent_unevaluable_path())
    print("listening_comm unevaluable OK")
    asyncio.run(test_language_sub_agent_unevaluable_path())
    print("language unevaluable OK")
    asyncio.run(test_needs_sub_agent_unevaluable_path())
    print("needs unevaluable OK")
    print("All Group A skeleton tests passed.")
