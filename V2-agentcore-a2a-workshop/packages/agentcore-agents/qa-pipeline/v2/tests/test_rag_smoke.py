# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""V2 RAG 3종 스모크 테스트 — Dev4 산출물 (#5, #6) 검증."""

from __future__ import annotations

import os
import sys

import pytest


# qa-pipeline 루트를 path 에 삽입하여 `v2.rag` import 가능하게 한다.
_PIPELINE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

from v2.rag import (  # noqa: E402
    GoldenSetRAG,
    RAGUnavailable,
    retrieve_fewshot,
    retrieve_knowledge,
    retrieve_reasoning,
)


# ---------------------------------------------------------------------------
# Golden-set RAG
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("item", list(range(1, 19)))
def test_golden_set_all_items_loadable(item: int) -> None:
    """18 개 항목 모두 최소 1개 이상 예시 반환되어야 함."""
    result = retrieve_fewshot(item, "*", "")
    assert result.total_pool >= 1
    assert len(result.examples) >= 1
    assert result.item_number == item


def test_golden_set_intent_filter() -> None:
    """intent 일치하는 예시 우선 반환."""
    result = retrieve_fewshot(9, "info_change", "본인확인 성함 생년월일")
    # #9 는 intent 필터 후 matching 예시 존재
    assert len(result.examples) >= 1
    assert "intent_filtered" in result.match_reason or "intent_fallback_all" in result.match_reason


def test_golden_set_bucket_balance() -> None:
    """top_k=3 요청 시 full/partial/zero 가 가능한 균형."""
    result = retrieve_fewshot(1, "general_inquiry", "안녕하세요 고객센터", top_k=3)
    buckets = {ex.score_bucket for ex in result.examples}
    # 3 개 요청이면 최소 2 bucket 이상 포함 (#1 은 full/partial/zero 존재)
    assert len(buckets) >= 2


def test_golden_set_raises_on_unknown_item() -> None:
    with pytest.raises(RAGUnavailable):
        GoldenSetRAG().retrieve(999, "*", "")


def test_golden_set_no_rater_meta_filtered() -> None:
    """rater_meta 없는 예시는 로드 단계에서 제외 (원칙 7.5)."""
    engine = GoldenSetRAG()
    for item in (1, 15, 18):
        for ex in engine._load_item(item):
            assert ex.rater_meta, f"{ex.example_id} missing rater_meta"


# ---------------------------------------------------------------------------
# Reasoning RAG
# ---------------------------------------------------------------------------


def test_reasoning_returns_stdev() -> None:
    result = retrieve_reasoning(1, "상담사가 소속과 이름을 밝힘")
    assert result.item_number == 1
    assert result.sample_size >= 1
    assert result.stdev >= 0.0
    assert result.mean >= 0.0


def test_reasoning_unavailable_on_unknown_item() -> None:
    with pytest.raises(RAGUnavailable):
        retrieve_reasoning(999, "임의")


# ---------------------------------------------------------------------------
# Business Knowledge RAG
# ---------------------------------------------------------------------------


def test_business_knowledge_chunks_loaded() -> None:
    """manual.md 에서 meta 붙은 chunk 5 개 로드."""
    from v2.rag.business_knowledge import BusinessKnowledgeRAG

    rag = BusinessKnowledgeRAG()
    chunks = rag._load_chunks()
    assert len(chunks) == 5
    ids = {c.chunk_id for c in chunks}
    assert ids == {"BK-GEN-001", "BK-GEN-002", "BK-GEN-003", "BK-GEN-004", "BK-GEN-005"}


@pytest.mark.parametrize(
    "intent,query,expected_chunk",
    [
        ("general_inquiry", "상담 운영 시간 문의", "BK-GEN-001"),
        ("billing", "자동이체 등록", "BK-GEN-002"),
        ("cancellation", "해지 환불 규정", "BK-GEN-003"),
        ("claim", "처리 영업일 안내", "BK-GEN-004"),
        ("technical_support", "장애 접수 번호", "BK-GEN-005"),
    ],
)
def test_business_knowledge_intent_routing(intent: str, query: str, expected_chunk: str) -> None:
    """각 intent 가 올바른 chunk 로 라우팅되는지."""
    result = retrieve_knowledge(intent, query)
    assert result.unevaluable is False, f"unexpected unevaluable for intent={intent}"
    assert result.chunks
    assert result.chunks[0].chunk_id == expected_chunk


def test_business_knowledge_unevaluable_when_no_intent_match() -> None:
    """intent 가 매뉴얼 밖이면 unevaluable=True — 원칙 7.5 / #15 unevaluable 분기."""
    result = retrieve_knowledge("product_inquiry", "신상품 A 요금")
    assert result.unevaluable is True
    assert not result.source_refs


def test_business_knowledge_source_refs_populated() -> None:
    result = retrieve_knowledge("billing", "자동이체 등록")
    assert result.source_refs == ["generic-billing-guide v1.2"]
