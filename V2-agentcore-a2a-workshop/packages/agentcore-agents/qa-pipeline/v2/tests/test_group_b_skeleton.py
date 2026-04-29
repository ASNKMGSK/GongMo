# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Group B Sub Agent skeleton 테스트.

Phase D1 통합 전까지 validation 용 경량 테스트. 실제 LLM 호출 없이
스키마 준수 여부만 검증한다.
"""

from __future__ import annotations

import asyncio

import pytest

from v2.agents.group_b.base import (
    CATEGORY_MAX_SCORE,
    DEFAULT_EVALUATION_MODE,
    ITEM_CATEGORY,
    ITEM_MAX_SCORE,
    build_sub_agent_response,
    compare_with_rule_pre_verdict,
    make_deduction,
    make_evidence,
    make_item_verdict,
    make_llm_self_confidence,
)
from v2.agents.group_b.explanation import explanation_agent
from v2.agents.group_b.privacy import privacy_agent
from v2.agents.group_b.proactiveness import proactiveness_agent
from v2.agents.group_b.work_accuracy import work_accuracy_agent
from v2.contracts.rubric import ALLOWED_STEPS, snap_score_v2
from v2.schemas.enums import FORCE_T3_ITEMS


def test_group_b_item_category_coverage():
    """Group B 담당 항목 9개 전부 category 매핑 존재."""
    group_b_items = {10, 11, 12, 13, 14, 15, 16, 17, 18}
    assert group_b_items == set(ITEM_CATEGORY.keys())
    assert group_b_items == set(ITEM_MAX_SCORE.keys())


def test_category_max_score_sums():
    """Group B 카테고리 max 합계 == 55 (15+15+15+10)."""
    group_b_keys = {"explanation_delivery", "proactiveness", "work_accuracy", "privacy_protection"}
    total = sum(CATEGORY_MAX_SCORE[k] for k in group_b_keys)
    assert total == 55, f"Expected 55, got {total}"


def test_force_t3_items_includes_privacy():
    """FORCE_T3_ITEMS 에 #17, #18 포함 (Dev5 enums 계약)."""
    assert 17 in FORCE_T3_ITEMS
    assert 18 in FORCE_T3_ITEMS


def test_item_15_default_partial_with_review():
    """업무지식 RAG 필수 항목 #15 는 기본 evaluation_mode=partial_with_review."""
    assert DEFAULT_EVALUATION_MODE[15] == "partial_with_review"


def test_rubric_v2_allowed_steps_17_18_expanded():
    """Phase A2 확정: #17/#18 ALLOWED_STEPS = [5, 3, 0] 확장."""
    assert ALLOWED_STEPS[17] == [5, 3, 0]
    assert ALLOWED_STEPS[18] == [5, 3, 0]


def test_snap_score_v2_item_17_preserves_3():
    """iter05 회귀 해소 검증: #17 에서 LLM 이 3점 반환해도 0 으로 강제 변환되지 않음."""
    assert snap_score_v2(17, 3) == 3
    assert snap_score_v2(18, 3) == 3
    # 허용 단계 외 값은 이하 방향 snap
    assert snap_score_v2(17, 4) == 3
    assert snap_score_v2(17, 2) == 0
    assert snap_score_v2(17, 5) == 5


def test_snap_score_v2_item_15_unchanged():
    """#15 는 V1 그대로 [10, 5, 0] — snap_score_v2 호환성 확인."""
    assert snap_score_v2(15, 10) == 10
    assert snap_score_v2(15, 7) == 5
    assert snap_score_v2(15, 5) == 5
    assert snap_score_v2(15, 3) == 0


def test_make_item_verdict_snaps_score_via_rubric_v2():
    """make_item_verdict 이 snap_score_v2 경유하여 #17 3점 보존."""
    ev = make_evidence(speaker="상담사", quote="성함 알려주세요")
    conf = make_llm_self_confidence(score=3)
    # LLM 이 raw score=3 으로 반환 (iter05 재현 케이스)
    item = make_item_verdict(
        item_number=17,
        score=3,
        evaluation_mode="compliance_based",
        judgment="양해 표현 누락 — 부분 준수",
        deductions=[make_deduction(reason="목적 설명 없음", points=2, evidence_refs=[0])],
        evidence=[ev],
        llm_self_confidence=conf,
    )
    # V1 snap_score 경로에서는 0 으로 변환되던 값이 V2 에서는 3 유지
    assert item["score"] == 3, "iter05 회귀: #17 에서 3점이 0으로 강제 변환됨"


def test_privacy_items_compliance_based():
    """#17, #18 은 compliance_based 모드."""
    assert DEFAULT_EVALUATION_MODE[17] == "compliance_based"
    assert DEFAULT_EVALUATION_MODE[18] == "compliance_based"


def test_make_evidence_shape():
    """EvidenceQuote 필수 필드."""
    ev = make_evidence(speaker="상담사", quote="안녕하세요", turn_id=1)
    assert ev["speaker"] == "상담사"
    assert ev["quote"] == "안녕하세요"
    assert ev.get("turn_id") == 1


def test_make_deduction_evidence_refs_list():
    """DeductionEntry.evidence_refs 는 list[int]."""
    d = make_deduction(reason="test", points=3, evidence_refs=[0, 1], rule_id="#10")
    assert isinstance(d["evidence_refs"], list)
    assert d["evidence_refs"] == [0, 1]
    assert d["points"] == 3


def test_make_llm_self_confidence_range():
    """LLMSelfConfidence.score 는 1~5 clamped."""
    assert make_llm_self_confidence(score=10)["score"] == 5
    assert make_llm_self_confidence(score=-1)["score"] == 1
    assert make_llm_self_confidence(score=3)["score"] == 3


def test_make_item_verdict_core_fields():
    """ItemVerdict 필수 필드 구성 확인."""
    ev = make_evidence(speaker="상담사", quote="네.")
    conf = make_llm_self_confidence(score=4)
    item = make_item_verdict(
        item_number=10,
        score=7,
        evaluation_mode="full",
        judgment="부분적 장황",
        deductions=[make_deduction(reason="장황", points=3, evidence_refs=[0])],
        evidence=[ev],
        llm_self_confidence=conf,
    )
    assert item["item_number"] == 10
    assert item["score"] == 7
    assert item["max_score"] == 10
    assert item["evaluation_mode"] == "full"
    assert item["item_name"] == "설명의 명확성"


def test_build_sub_agent_response_computes_category_score():
    """SubAgentResponse.category_score 는 items[].score 합계."""
    ev = make_evidence(speaker="상담사", quote="안녕하세요")
    conf = make_llm_self_confidence(score=4)
    item_10 = make_item_verdict(
        item_number=10, score=7, evaluation_mode="full",
        judgment="", deductions=[], evidence=[ev], llm_self_confidence=conf,
    )
    item_11 = make_item_verdict(
        item_number=11, score=5, evaluation_mode="full",
        judgment="", deductions=[], evidence=[ev], llm_self_confidence=conf,
    )
    resp = build_sub_agent_response(
        agent_id="explanation-delivery-agent",
        category="explanation_delivery",
        status="success",
        items=[item_10, item_11],
        category_confidence=4,
        llm_backend="bedrock",
    )
    assert resp["category_score"] == 12
    assert resp["category_max"] == 15
    assert resp["status"] == "success"


def test_compare_with_rule_pre_verdict_no_rule():
    """rule_pre_verdicts 부재 시 None 반환."""
    assert compare_with_rule_pre_verdict(
        item_number=10, llm_score=7, rule_pre_verdicts=None,
    ) is None
    assert compare_with_rule_pre_verdict(
        item_number=10, llm_score=7, rule_pre_verdicts={},
    ) is None


def test_compare_with_rule_pre_verdict_agreement():
    """rule_score == llm_score 시 agreement=True."""
    delta = compare_with_rule_pre_verdict(
        item_number=17, llm_score=5,
        rule_pre_verdicts={17: {"score": 5, "confidence": 0.9}},
    )
    assert delta is not None
    assert delta["rule_score"] == 5
    assert delta["llm_score"] == 5
    assert delta["agreement"] is True


def test_compare_with_rule_pre_verdict_disagreement():
    """rule_score != llm_score 시 agreement=False."""
    delta = compare_with_rule_pre_verdict(
        item_number=17, llm_score=0,
        rule_pre_verdicts={17: {"score": 5, "confidence": 0.9}},
    )
    assert delta is not None
    assert delta["agreement"] is False


# ---------------------------------------------------------------------------
# Agent-level smoke tests (skeleton — 실제 LLM 호출 없음)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explanation_agent_returns_response_and_wiki():
    resp, wiki = await explanation_agent(
        transcript="상담사: 안녕하세요.\n고객: 네.",
        assigned_turns=[{"turn_id": 1, "speaker": "agent", "text": "안녕하세요"}],
        consultation_type="general",
        intent_summary={"primary_intent": "단순 문의"},
    )
    assert resp["agent_id"] == "explanation-delivery-agent"
    assert resp["category"] == "explanation_delivery"
    assert len(resp["items"]) == 2
    assert {it["item_number"] for it in resp["items"]} == {10, 11}
    assert isinstance(wiki, dict)


@pytest.mark.asyncio
async def test_privacy_agent_pattern_detection_empty_triggers():
    resp, wiki = await privacy_agent(
        transcript="상담사: 안녕하세요.",
        assigned_turns=[{"turn_id": 1, "speaker": "agent", "text": "안녕하세요"}],
        consultation_type="general",
        preprocessing={"deduction_triggers": {"triggers": []}},
    )
    assert resp["category"] == "privacy_protection"
    assert wiki.get("flags", {}).get("patterns_detected") == []
    assert wiki["flags"]["force_t3_items"] == [17, 18]


@pytest.mark.asyncio
async def test_privacy_agent_pattern_a_detected():
    """preemptive_disclosure trigger → 패턴 A 검출."""
    resp, wiki = await privacy_agent(
        transcript="상담사: 홍길동 고객님이시죠?",
        assigned_turns=[{"turn_id": 1, "speaker": "agent", "text": "홍길동 고객님이시죠?"}],
        consultation_type="general",
        preprocessing={
            "deduction_triggers": {
                "triggers": [
                    {
                        "trigger_type": "preemptive_disclosure",
                        "turn_id": 1,
                        "evidence_text": "홍길동 고객님이시죠?",
                    }
                ]
            }
        },
    )
    _ = resp
    assert "A" in wiki["flags"]["patterns_detected"]
    assert wiki["flags"]["preemptive_disclosure"] is True


@pytest.mark.asyncio
async def test_work_accuracy_agent_unevaluable_when_rag_misses():
    """Unknown intent → RAG returns unevaluable=True → #15 unevaluable.

    Dev4 `retrieve_knowledge` 는 intent 미매칭 시 `unevaluable=True` 반환.
    테스트는 존재하지 않는 intent 로 이 경로 유발.
    """
    resp, wiki = await work_accuracy_agent(
        transcript="상담사: 이 상품은 ...",
        assigned_turns=[{"turn_id": 1, "speaker": "agent", "text": "이 상품은"}],
        consultation_type="insurance",
        intent_summary={"primary_intent": "__nonexistent_intent__"},
    )
    item_15 = next(it for it in resp["items"] if it["item_number"] == 15)
    assert item_15["evaluation_mode"] == "unevaluable"
    assert wiki["accuracy_verdict"]["severity"] == "unevaluable"
    assert wiki["accuracy_verdict"]["recommended_override"] == "none"
    assert wiki["accuracy_verdict"]["incorrect_items"] == []


@pytest.mark.asyncio
async def test_privacy_agent_no_patterns_defaults_to_5():
    """Phase E1 회귀 테스트: 패턴 A/B/C 미감지 + rule 부재 시 긍정 기본 5점.

    iter03_clean 9 샘플 전원 #17/#18=0점 버그 (skeleton 고정 0) 재발 방지.
    compliance_based 평가는 "절차 준수 미감지 = 5점" 이 원칙.
    """
    resp, wiki = await privacy_agent(
        transcript="상담사: 본인확인 진행하겠습니다. 성함 부탁드립니다.",
        assigned_turns=[{"turn_id": 1, "speaker": "agent", "text": "성함 부탁드립니다"}],
        consultation_type="general",
        preprocessing={"deduction_triggers": {"triggers": []}},
    )
    item_17 = next(it for it in resp["items"] if it["item_number"] == 17)
    item_18 = next(it for it in resp["items"] if it["item_number"] == 18)
    assert item_17["score"] == 5, f"#17 긍정 기본 5점 기대, 실제={item_17['score']}"
    assert item_18["score"] == 5, f"#18 긍정 기본 5점 기대, 실제={item_18['score']}"
    # 산술 일관성: 5점이면 deductions 합계 0
    assert sum(d.get("points", 0) for d in item_17["deductions"]) == 0
    assert wiki["flags"]["score_source"]["item_17"] == "positive_default"


def test_load_group_b_prompt_all_9_items():
    """V2 Bedrock wrapper: 9개 프롬프트 로드 검증 (#10~#18)."""
    from v2.agents.group_b._llm import load_group_b_prompt

    names = [
        "item_10_clarity", "item_11_conclusion_first",
        "item_12_problem_solving", "item_13_supplementary", "item_14_followup",
        "item_15_accuracy", "item_16_mandatory_script",
        "item_17_iv_procedure", "item_18_privacy_protection",
    ]
    for name in names:
        content = load_group_b_prompt(name)
        assert len(content) > 100, f"{name} 프롬프트가 비어있거나 짧음"


def test_item_17_prompt_mentions_3_point_snap():
    """#17 프롬프트가 [5,3,0] 3점 스냅을 명시적으로 허용 (iter05 회귀 해소 공식 증거)."""
    from v2.agents.group_b._llm import load_group_b_prompt

    prompt_17 = load_group_b_prompt("item_17_iv_procedure")
    assert "[5, 3, 0]" in prompt_17 or "[5,3,0]" in prompt_17, "ALLOWED_STEPS 3점 명시 없음"
    assert "3점" in prompt_17, "3점 허용 조항 없음"
    # 패턴 A/B/C 탐지 서술
    assert "패턴 A" in prompt_17
    assert "패턴 B" in prompt_17
    assert "패턴 C" in prompt_17


def test_load_group_b_prompt_includes_evidence_preamble():
    """PL 긴급 지시: 모든 프롬프트 로드 시 evidence 강제 preamble 1회 append."""
    from v2.agents.group_b._llm import load_group_b_prompt

    for name in (
        "item_10_clarity", "item_15_accuracy",
        "item_17_iv_procedure", "item_18_privacy_protection",
    ):
        prompt = load_group_b_prompt(name)
        assert "evidence 최소 1개 필수" in prompt, f"{name} evidence preamble 누락"
        assert "evidence 없이 5점 부여 금지" in prompt, f"{name} evidence-first 문구 누락"


def test_item_17_prompt_has_3_point_cases():
    """PL 긴급 지시: #17 에 3점 판정 사례 명시 (iter05 회귀 해소 실증)."""
    from v2.agents.group_b._llm import load_group_b_prompt

    prompt_17 = load_group_b_prompt("item_17_iv_procedure")
    assert "3점 판정 사례" in prompt_17, "3점 판정 사례 섹션 없음"
    assert "양해" in prompt_17 and "목적" in prompt_17, "양해/목적 누락 케이스 누락"


def test_item_18_prompt_has_normal_5point_principle():
    """PL 긴급 지시: #18 에 '5점이 normal' 기본 원칙 명시."""
    from v2.agents.group_b._llm import load_group_b_prompt

    prompt_18 = load_group_b_prompt("item_18_privacy_protection")
    assert "5점이 기본값" in prompt_18, "5점 기본 원칙 누락"
    assert "normal case" in prompt_18, "normal case 표기 누락"


def test_v1_llm_import_available():
    """V1 `nodes.llm` 의 3개 심볼이 V2 에서 import 가능 (V1 수정 없이 재활용)."""
    from v2.agents.group_b._llm import (
        LLMTimeoutError, get_chat_model, invoke_and_parse,
    )
    assert callable(get_chat_model)
    assert callable(invoke_and_parse)
    assert issubclass(LLMTimeoutError, Exception)


@pytest.mark.asyncio
async def test_privacy_agent_rule_pre_verdict_takes_precedence():
    """Dev1 Layer 1 rule_pre_verdicts 가 있으면 우선 채택."""
    resp, wiki = await privacy_agent(
        transcript="상담사: 테스트",
        assigned_turns=[{"turn_id": 1, "speaker": "agent", "text": "테스트"}],
        consultation_type="general",
        rule_pre_verdicts={
            "verdicts": {
                17: {"score": 5, "confidence_mode": "hard"},
                18: {"score": 3, "confidence_mode": "soft"},
            }
        },
        preprocessing={"deduction_triggers": {"triggers": []}},
    )
    item_17 = next(it for it in resp["items"] if it["item_number"] == 17)
    item_18 = next(it for it in resp["items"] if it["item_number"] == 18)
    assert item_17["score"] == 5
    assert item_18["score"] == 3
    # #18 score=3 이면 deductions 합계 2 자동 생성 (산술 일관성)
    assert sum(d["points"] for d in item_18["deductions"]) == 2
    assert wiki["flags"]["score_source"]["item_17"] == "rule_pre_verdict"
    assert wiki["flags"]["score_source"]["item_18"] == "rule_pre_verdict"


@pytest.mark.asyncio
async def test_proactiveness_agent_item_14_immediate_resolution():
    """#14 즉시해결 건은 skeleton 기본 False — 향후 LLM 통합 후 동적 전환 검증."""
    resp, wiki = await proactiveness_agent(
        transcript="상담사: 네 해결되었습니다.",
        assigned_turns=[{"turn_id": 1, "speaker": "agent", "text": "해결됨"}],
        consultation_type="general",
    )
    _ = wiki
    assert resp["category"] == "proactiveness"
    assert len(resp["items"]) == 3
    assert {it["item_number"] for it in resp["items"]} == {12, 13, 14}


def _run_sync_rubric_tests():
    test_rubric_v2_allowed_steps_17_18_expanded()
    test_snap_score_v2_item_17_preserves_3()
    test_snap_score_v2_item_15_unchanged()
    test_make_item_verdict_snaps_score_via_rubric_v2()


if __name__ == "__main__":
    _run_sync_rubric_tests()
    print("rubric v2 tests OK")
    # Quick manual run fallback
    asyncio.run(test_explanation_agent_returns_response_and_wiki())
    print("test_explanation_agent OK")
    asyncio.run(test_privacy_agent_pattern_detection_empty_triggers())
    print("test_privacy_agent_empty OK")
    asyncio.run(test_privacy_agent_pattern_a_detected())
    print("test_privacy_agent_pattern_a OK")
    asyncio.run(test_work_accuracy_agent_unevaluable_when_rag_misses())
    print("test_work_accuracy_unevaluable OK")
    asyncio.run(test_proactiveness_agent_item_14_immediate_resolution())
    print("test_proactiveness_agent OK")
    print("All smoke tests passed.")
