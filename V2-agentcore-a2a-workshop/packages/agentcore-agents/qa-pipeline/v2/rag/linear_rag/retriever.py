# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
LinearRAG Retriever — Stage 1 + Stage 2 통합 진입점.

V3 의 다른 RAG 모듈 (`golden_set`, `reasoning`, `business_knowledge`) 과
같은 패턴으로 노출:
    from rag.linear_rag import LinearRAG, retrieve_linear

사용 예:
    rag = LinearRAG(tenant_id="kolong", tenant_root=Path("v2_data/tenants"))
    result = rag.retrieve("카드 환불 어떻게 해요?", top_k=5)
    for p in result.passages:
        print(p.pid, p.ppr_score, p.text[:50])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .stage1 import Stage1Result, activate_entities
from .stage2 import retrieve_passages
from .tri_graph import TriGraph, load_tri_graph, tri_graph_exists
from .types import LinearRAGResult, RetrievalError, TenantNotIndexed

logger = logging.getLogger(__name__)


# 기본값 (논문 권장)
DEFAULT_TOP_K = 5
DEFAULT_THRESHOLD = 0.4
DEFAULT_MAX_ITER_STAGE1 = 4
DEFAULT_DAMPING = 0.85
DEFAULT_LAMBDA = 0.05
DEFAULT_PASSAGE_WEIGHT = 1.0
DEFAULT_INITIAL_MATCH_MIN_SIM = 0.5


@dataclass
class LinearRAGConfig:
    """LinearRAG hyper-parameters — 논문 §E (Sensitivity) 권장값."""

    top_k: int = DEFAULT_TOP_K
    threshold_delta: float = DEFAULT_THRESHOLD
    max_iter_stage1: int = DEFAULT_MAX_ITER_STAGE1
    damping: float = DEFAULT_DAMPING
    lambda_coef: float = DEFAULT_LAMBDA
    passage_weight: float = DEFAULT_PASSAGE_WEIGHT
    initial_match_min_sim: float = DEFAULT_INITIAL_MATCH_MIN_SIM
    max_iter_ppr: int = 50
    ppr_tol: float = 1e-6


class LinearRAG:
    """tenant 단위 LinearRAG retriever.

    그래프는 lazy load — 첫 retrieve 호출 시 디스크에서 읽음. 동일 인스턴스 재사용 시
    그래프 메모리에 유지되어 latency 최소화.
    """

    def __init__(
        self,
        tenant_id: str,
        tenant_root: Path,
        *,
        config: Optional[LinearRAGConfig] = None,
        embed_fn: Optional[Callable[[str], Optional[tuple[float, ...]]]] = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.tenant_root = Path(tenant_root)
        self.config = config or LinearRAGConfig()
        self._graph: Optional[TriGraph] = None
        self._embed_fn = embed_fn

    @property
    def graph(self) -> TriGraph:
        if self._graph is None:
            if not tri_graph_exists(self.tenant_id, self.tenant_root):
                raise TenantNotIndexed(
                    f"LinearRAG 인덱스 없음: tenant={self.tenant_id}. "
                    "indexer.build_index() 로 빌드 후 재시도."
                )
            self._graph = load_tri_graph(self.tenant_id, self.tenant_root)
        return self._graph

    def _resolve_embed_fn(self) -> Callable:
        if self._embed_fn is not None:
            return self._embed_fn
        # V3 기본 임베딩 — Bedrock Titan v2
        try:
            from .. import embedding as v3_embedding  # type: ignore
        except ImportError as exc:
            raise RetrievalError(
                "embed_fn 미지정 + V3 embedding 모듈 import 실패 — "
                "LinearRAG(embed_fn=...) 또는 v2/rag/embedding.py 가 필요"
            ) from exc
        return v3_embedding.embed

    def retrieve(
        self,
        query: str,
        *,
        top_k: Optional[int] = None,
        config_override: Optional[LinearRAGConfig] = None,
    ) -> LinearRAGResult:
        """Stage 1 + Stage 2 통합 실행.

        Args:
            query: 검색 쿼리 (고객 발화 또는 인텐트 문장).
            top_k: 반환 passage 수 (기본 config 값).
            config_override: 이 호출에 한정한 config 변경.

        Returns:
            LinearRAGResult — passages + activated_entities + diagnostics.
        """
        cfg = config_override or self.config
        k = top_k if top_k is not None else cfg.top_k

        if not query or not query.strip():
            return LinearRAGResult(
                passages=[],
                activated_entities=[],
                diagnostics={"reason": "empty_query"},
            )

        graph = self.graph
        embed_fn = self._resolve_embed_fn()

        # ── Stage 1 ──
        s1: Stage1Result = activate_entities(
            graph=graph,
            query=query,
            embed_fn=embed_fn,
            threshold=cfg.threshold_delta,
            max_iterations=cfg.max_iter_stage1,
            initial_match_min_sim=cfg.initial_match_min_sim,
        )

        # ── Stage 2 ──
        s2 = retrieve_passages(
            graph=graph,
            stage1_result=s1,
            query=query,
            embed_fn=embed_fn,
            top_k=k,
            damping=cfg.damping,
            lambda_coef=cfg.lambda_coef,
            passage_weight=cfg.passage_weight,
            max_iter=cfg.max_iter_ppr,
            tol=cfg.ppr_tol,
        )

        return LinearRAGResult(
            passages=s2.passages,
            activated_entities=s1.activated_entities,
            diagnostics={
                "stage1": s1.diagnostics,
                "stage2": s2.diagnostics,
                "tenant_id": self.tenant_id,
                "config": {
                    "top_k": k,
                    "threshold_delta": cfg.threshold_delta,
                    "lambda_coef": cfg.lambda_coef,
                    "damping": cfg.damping,
                },
            },
        )


# ── 함수형 wrapper (V3 의 다른 RAG 모듈과 일관) ────────────────────────


def retrieve_linear(
    *,
    tenant_id: str,
    tenant_root: Path,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    config: Optional[LinearRAGConfig] = None,
    embed_fn: Optional[Callable[[str], Optional[tuple[float, ...]]]] = None,
) -> LinearRAGResult:
    """LinearRAG 일회성 검색 함수.

    Per-tenant 그래프를 매 호출 로드하므로 latency 가 중요한 곳에서는 LinearRAG 클래스
    인스턴스를 재사용할 것.
    """
    rag = LinearRAG(tenant_id=tenant_id, tenant_root=tenant_root, config=config, embed_fn=embed_fn)
    return rag.retrieve(query, top_k=top_k)


__all__ = [
    "LinearRAG",
    "LinearRAGConfig",
    "retrieve_linear",
    "DEFAULT_TOP_K",
    "DEFAULT_THRESHOLD",
    "DEFAULT_DAMPING",
    "DEFAULT_LAMBDA",
]
