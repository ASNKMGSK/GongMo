# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
V2 QA Pipeline — RAG 4종 프레임워크.

설계서 7장 (p15-16) 준수. **원칙 2 강제** — 아래 금지 사용 규칙을 위반하는 구현 금지.

- `golden_set`     : Few-shot 공급. `retrieve_fewshot(item_number, intent, segment_text)`
- `reasoning`      : 과거 판정 근거 embedding. `retrieve_reasoning(item_number, transcript_slice)`
- `business_knowledge` : 업무지식 매뉴얼 chunk. `retrieve_knowledge(intent, query)`
- `linear_rag`     : LinearRAG (ICLR'26) Clean-Room 구현 — Tri-Graph + 2-stage retrieval.
                     `LinearRAG(tenant_id, tenant_root).retrieve(query, top_k=5)`
                     KMS 매뉴얼 prose / 멀티홉 평가 도입 시점에 활성화.

모듈 상단 `금지 사용 (7.5)` 주석 확인 필수.
"""

import contextvars

from .types import (
    FewshotExample,
    FewshotResult,
    ReasoningExample,
    ReasoningResult,
    KnowledgeChunk,
    KnowledgeResult,
    RAGError,
    RAGUnavailable,
)
from .golden_set import GoldenSetRAG, retrieve_fewshot
from .reasoning import ReasoningRAG, retrieve_reasoning
from .business_knowledge import BusinessKnowledgeRAG, lookup_business_knowledge, retrieve_knowledge
from .linear_rag import (
    LinearRAG,
    LinearRAGConfig,
    retrieve_linear,
    build_index as build_linear_index,
    kms_table_to_corpus,
)
from .reranker import (
    is_reranker_enabled,
    set_reranker_enabled,
    reset_reranker_enabled,
    get_reranker_provider,
    set_reranker_provider,
    reset_reranker_provider,
    set_reranker_llm_model,
    reset_reranker_llm_model,
    get_reranker_llm_model,
    rerank,
    get_reranker_meta,
    init_reranker_stats,
    reset_reranker_stats,
    get_reranker_stats,
)


# ---------------------------------------------------------------------------
# RAG 전역 비활성화 토글 (2026-05-08)
#
# 사용자가 프론트에서 "RAG 사용/미사용" 비교 실험을 돌릴 때 사용. 요청 단위로
# contextvar 를 set 하면 같은 task 트리 (asyncio.gather / asyncio.to_thread / LangGraph
# 노드) 내부에서 자동 전파된다. 진입점 4종이 함수 시작에서 is_rag_disabled() 를
# 체크하고 빈 결과 반환:
#   - retrieve_fewshot          (Group A/B 평가 few-shot)
#   - retrieve_reasoning        (Layer 4 confidence stdev 신호)
#   - retrieve_knowledge        (#15 정확안내 / 부서특화 매뉴얼 lookup)
#   - retrieve_human_cases      (HITL 골든셋 — debate 페르소나 anchor)
#
# 영향 범위 밖 (의도적):
#   - kms_intent_mode == "linear_rag"  → KMS 인텐트 분류 대안. 별도 토글이라 무관.
# ---------------------------------------------------------------------------

_RAG_DISABLED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "qa_rag_disabled", default=False
)


def is_rag_disabled() -> bool:
    """현재 contextvar 가 RAG 비활성 상태인지."""
    return bool(_RAG_DISABLED.get())


def set_rag_disabled(value: bool) -> contextvars.Token[bool]:
    """RAG 비활성 contextvar 를 set. 반환 토큰을 reset_rag_disabled 에 넘겨 복원."""
    return _RAG_DISABLED.set(bool(value))


def reset_rag_disabled(token: contextvars.Token[bool]) -> None:
    """set_rag_disabled 가 반환한 토큰으로 이전 상태 복원."""
    _RAG_DISABLED.reset(token)


__all__ = [
    "FewshotExample",
    "FewshotResult",
    "ReasoningExample",
    "ReasoningResult",
    "KnowledgeChunk",
    "KnowledgeResult",
    "RAGError",
    "RAGUnavailable",
    "GoldenSetRAG",
    "retrieve_fewshot",
    "ReasoningRAG",
    "retrieve_reasoning",
    "BusinessKnowledgeRAG",
    "retrieve_knowledge",
    "lookup_business_knowledge",
    # LinearRAG (Clean-Room ICLR'26)
    "LinearRAG",
    "LinearRAGConfig",
    "retrieve_linear",
    "build_linear_index",
    "kms_table_to_corpus",
    # 전역 비활성 토글
    "is_rag_disabled",
    "set_rag_disabled",
    "reset_rag_disabled",
]
