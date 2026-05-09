# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
LinearRAG — Clean-Room 구현 (ICLR 2026 LinearRAG 논문 알고리즘 기반).

원 GPL-3.0 코드 (DEEP-PolyU/LinearRAG) 와 코드 공유 없음 — 페이퍼의 알고리즘과
수식만 참조하여 V3 환경 (Bedrock · 한국어 · 멀티테넌트 · AOSS) 에 맞게 재구현.
라이선스: Apache 2.0 (V3 프로젝트와 동일).

논문: "LinearRAG: Linear Graph Retrieval Augmented Generation on Large-scale Corpora"
     (arxiv 2510.10114, ICLR 2026)

핵심:
  - Tri-Graph (passage / sentence / entity 3종 노드)
  - Token-free graph construction — LLM 호출 0회 (NER + 임베딩만)
  - Stage 1: entity activation via local semantic bridging (식 3, 4, 5)
  - Stage 2: passage retrieval via global importance aggregation (PPR, 식 6, 7)

사용 예 (인덱싱):
    from rag.linear_rag import build_index, kms_table_to_corpus
    from rag.embedding import embed
    from pathlib import Path

    kms_rows = [...]  # KMS 표 JSON
    corpus = kms_table_to_corpus(kms_rows)
    result = build_index(
        tenant_id="kolong",
        corpus=corpus,
        tenant_root=Path("/v2_data/tenants"),
        embed_fn=embed,
    )

사용 예 (검색):
    from rag.linear_rag import LinearRAG
    rag = LinearRAG(tenant_id="kolong", tenant_root=Path("/v2_data/tenants"))
    result = rag.retrieve("카드 환불 어떻게 해요?", top_k=5)
    for p in result.passages:
        print(p.pid, p.ppr_score, p.text[:80])
"""

from .indexer import (
    CorpusItem,
    IndexBuildResult,
    build_index,
    kms_table_to_corpus,
)
from .ner_korean import (
    ExtractedEntity,
    NERBackend,
    extract_entities,
    get_ner_backend,
    reset_backend_cache,
)
from .retriever import (
    DEFAULT_DAMPING,
    DEFAULT_LAMBDA,
    DEFAULT_THRESHOLD,
    DEFAULT_TOP_K,
    LinearRAG,
    LinearRAGConfig,
    retrieve_linear,
)
from .stage1 import Stage1Result, activate_entities
from .stage2 import Stage2Result, retrieve_passages
from .tri_graph import (
    TriGraph,
    build_tri_graph,
    load_tri_graph,
    save_tri_graph,
    tri_graph_exists,
)
from .types import (
    ActivatedEntity,
    Entity,
    IndexingError,
    LinearRAGError,
    LinearRAGResult,
    Passage,
    RetrievalError,
    RetrievedPassage,
    Sentence,
    TenantNotIndexed,
)

__all__ = [
    # 핵심 API
    "LinearRAG",
    "LinearRAGConfig",
    "retrieve_linear",
    "build_index",
    "kms_table_to_corpus",
    # 자료구조
    "TriGraph",
    "Passage",
    "Sentence",
    "Entity",
    "CorpusItem",
    "ActivatedEntity",
    "RetrievedPassage",
    "LinearRAGResult",
    "IndexBuildResult",
    "ExtractedEntity",
    "Stage1Result",
    "Stage2Result",
    # 저수준 API
    "build_tri_graph",
    "load_tri_graph",
    "save_tri_graph",
    "tri_graph_exists",
    "activate_entities",
    "retrieve_passages",
    "extract_entities",
    "get_ner_backend",
    "reset_backend_cache",
    "NERBackend",
    # 예외
    "LinearRAGError",
    "IndexingError",
    "RetrievalError",
    "TenantNotIndexed",
    # 상수
    "DEFAULT_TOP_K",
    "DEFAULT_THRESHOLD",
    "DEFAULT_DAMPING",
    "DEFAULT_LAMBDA",
]
