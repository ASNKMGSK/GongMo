# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
V2 QA Pipeline — RAG 3종 프레임워크.

설계서 7장 (p15-16) 준수. **원칙 2 강제** — 아래 금지 사용 규칙을 위반하는 구현 금지.

- `golden_set`     : Few-shot 공급. `retrieve_fewshot(item_number, intent, segment_text)`
- `reasoning`      : 과거 판정 근거 embedding. `retrieve_reasoning(item_number, transcript_slice)`
- `business_knowledge` : 업무지식 매뉴얼 chunk. `retrieve_knowledge(intent, query)`

모듈 상단 `금지 사용 (7.5)` 주석 확인 필수.
"""

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
]
