# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Baseline Vector RAG — basic cosine 유사도만 사용하는 dense retrieval.

V3 의 `business_knowledge.py` 와 동일한 접근:
    - 각 KMS passage 텍스트를 Bedrock Titan v2 로 임베딩 (1024-dim L2)
    - 쿼리 임베딩 vs passage 임베딩 cosine similarity 계산
    - top-k 정렬

LinearRAG 와의 차이:
    - 그래프 구조 없음 (entity activation, PPR 없음)
    - sentence 분해 없음 (passage 통째 임베딩)
    - 인덱싱 시 LLM/NER 호출 없음 (embedding 만)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BaselineResult:
    pid: str
    text: str
    score: float
    metadata: dict


class BaselineVectorRAG:
    """단순 dense vector retrieval — passage embedding 만 사용."""

    def __init__(self, embed_fn: Callable[[str], Optional[tuple[float, ...]]]):
        self._embed_fn = embed_fn
        self._passages: list[dict] = []
        self._passage_matrix: Optional[np.ndarray] = None

    def index(self, kms_rows: list[dict]) -> None:
        """KMS 행 리스트 → passage 임베딩 매트릭스 구성."""
        embeddings: list[np.ndarray] = []
        passages: list[dict] = []
        for row in kms_rows:
            text = self._row_to_text(row)
            emb = self._embed_fn(text)
            if emb is None:
                logger.warning("Baseline: embed 실패 pid=%s — skip", row.get("pid"))
                continue
            embeddings.append(np.asarray(emb, dtype=np.float32))
            passages.append({**row, "_text": text})
        if not embeddings:
            raise RuntimeError("Baseline 인덱싱 실패 — 임베딩 0건")
        self._passages = passages
        self._passage_matrix = np.vstack(embeddings)
        logger.info("Baseline 인덱싱 완료: %d passages", len(passages))

    def retrieve(self, query: str, top_k: int = 5) -> list[BaselineResult]:
        if self._passage_matrix is None:
            raise RuntimeError("index() 미호출")
        q_emb = self._embed_fn(query)
        if q_emb is None:
            return []
        q_vec = np.asarray(q_emb, dtype=np.float32)
        sims = self._passage_matrix @ q_vec  # cosine (Titan 정규화 후)
        n = len(self._passages)
        k = min(top_k, n)
        top_idx = np.argpartition(-sims, k - 1)[:k]
        top_idx = top_idx[np.argsort(-sims[top_idx])]
        return [
            BaselineResult(
                pid=self._passages[i]["pid"],
                text=self._passages[i]["_text"],
                score=float(sims[i]),
                metadata={k: v for k, v in self._passages[i].items() if not k.startswith("_")},
            )
            for i in top_idx.tolist()
        ]

    @staticmethod
    def _row_to_text(row: dict) -> str:
        """KMS 행 → 검색용 텍스트 (intent + branch + condition + statements)."""
        statements = row.get("required_statements", []) or []
        if isinstance(statements, list):
            stmt_text = "\n".join(statements)
        else:
            stmt_text = str(statements)
        keywords = row.get("required_keywords", []) or []
        kw_text = ", ".join(keywords) if keywords else ""
        return (
            f"[{row.get('intent', '')}] {row.get('branch', '')}\n"
            f"조건: {row.get('condition', '')}\n"
            f"필수 키워드: {kw_text}\n"
            f"필수 안내: {stmt_text}"
        ).strip()
