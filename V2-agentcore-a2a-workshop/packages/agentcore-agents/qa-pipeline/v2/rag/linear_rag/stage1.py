# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Stage 1 — Relevant Entity Activation via Local Semantic Bridging (§3.2.1).

논문 핵심 식 3개를 sparse 연산으로 구현. LLM 호출 0회.

  식 (3) Initial activation:
        a_q[i] = ⊮{i = argmax sim(e_q, e_j)} · sim(e_q, e_i)
        — query 에서 NER 추출한 entity e_q 마다 KG 의 가장 비슷한 entity e_i 를
          찾아 그 자리에만 similarity 점수 기록 (sparse vector)

  식 (4) Query-Sentence relevance distribution:
        σ_q[i] = sim(q, s_i)
        — query 와 모든 sentence 의 cosine similarity (dense vector)

  식 (5) Semantic propagation:
        a^t_q = MAX(M^T (σ_q ⊙ (M a^{t-1}_q)), a^{t-1}_q)
        — sentence-entity bipartite graph 위에서 entity → sentence → entity 양방향
          propagation. ⊙ 는 element-wise. MAX 는 element-wise max (단조 증가 보장).
        — n iterations = n-hop 활성화 (논문: n ≤ 4 충분).

  Dynamic pruning:
        threshold δ 미만 entity 는 다음 iteration 에서 제외 (sparse 유지 + 노이즈 제거).
        δ 너무 작으면 noise, 너무 크면 long-range bridge 손실 → δ ≈ 0.4 (논문 권장).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .ner_korean import ExtractedEntity, extract_entities
from .tri_graph import TriGraph
from .types import ActivatedEntity, RetrievalError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stage1Result:
    """Stage 1 의 출력 — Stage 2 PPR 입력으로 흘러감."""

    activated_entities: list[ActivatedEntity]
    activation_vector: object  # numpy.ndarray (|Ve|,) — entity index → activation score
    iterations_used: int
    diagnostics: dict


def activate_entities(
    *,
    graph: TriGraph,
    query: str,
    embed_fn,  # callable(text: str) -> Optional[tuple[float, ...]]
    threshold: float = 0.4,
    max_iterations: int = 4,
    initial_match_min_sim: float = 0.5,
) -> Stage1Result:
    """식 (3)~(5) 적용하여 query 와 의미적으로 연결된 entity 활성화.

    Args:
        graph: 사전 빌드된 Tri-Graph.
        query: 고객 발화 또는 쿼리 텍스트.
        embed_fn: 텍스트 → embedding tuple. None 반환 시 해당 항목 skip.
        threshold: dynamic pruning δ — 새 iteration entity 가 이 값 미만이면 제외.
        max_iterations: 최대 propagation 반복 (논문 ≤ 4).
        initial_match_min_sim: query entity → KG entity 매칭 최소 유사도.

    Returns:
        Stage1Result with activated_entities (sorted desc by score) + diagnostic.
    """
    try:
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise RetrievalError("numpy 미설치") from exc

    n_e = graph.num_entities()
    n_s = graph.num_sentences()
    if n_e == 0 or n_s == 0:
        return Stage1Result(
            activated_entities=[],
            activation_vector=np.zeros(n_e, dtype=np.float32),
            iterations_used=0,
            diagnostics={"reason": "empty_graph"},
        )

    # ── 식 (3): Initial entity activation ──
    a_q = _initial_activation(
        graph=graph,
        query=query,
        embed_fn=embed_fn,
        n_e=n_e,
        min_sim=initial_match_min_sim,
        np=np,
    )
    activation_iter: dict[int, int] = {i: 0 for i in np.flatnonzero(a_q).tolist()}

    if not activation_iter:
        # 쿼리에서 entity 추출 실패 또는 매칭 없음 → fallback dense retrieval 신호
        logger.info("Stage 1: query entity 매칭 0건 — fallback signal")
        return Stage1Result(
            activated_entities=[],
            activation_vector=a_q,
            iterations_used=0,
            diagnostics={
                "reason": "no_initial_match",
                "extracted_entities": _extract_query_entity_summary(query),
            },
        )

    # ── 식 (4): Query-sentence relevance ──
    sigma_q = _query_sentence_relevance(graph=graph, query=query, embed_fn=embed_fn, n_s=n_s, np=np)

    # ── 식 (5): Semantic propagation (sparse SpMM) ──
    M = graph.mention_matrix  # |Vs| × |Ve| sparse
    M_T = M.T.tocsr()  # |Ve| × |Vs|
    iter_counts: list[int] = []
    converged_iter = 0

    for t in range(1, max_iterations + 1):
        # M @ a_q → sentence 별 점수 (sparse)
        sentence_score = M.dot(a_q)  # shape (|Vs|,)

        # σ_q ⊙ (M a_q)
        weighted_sentences = sigma_q * sentence_score  # shape (|Vs|,)

        # M^T @ ... → entity 별 점수
        new_a = M_T.dot(weighted_sentences)  # shape (|Ve|,)

        # MAX(new, prev) — 단조 증가 + 기존 활성 유지
        before_active_count = int(np.count_nonzero(a_q))
        a_q_next = np.maximum(new_a, a_q)

        # Dynamic pruning: threshold δ 미만 entity 제거 (이미 활성이었던 건 유지 — MAX 보장)
        prune_mask = (a_q_next < threshold) & (a_q == 0)  # 새로 활성화됐지만 threshold 미만
        a_q_next[prune_mask] = 0.0

        # 새 활성 entity 식별
        new_actives = np.flatnonzero((a_q_next > 0) & (a_q == 0)).tolist()
        for idx in new_actives:
            activation_iter[idx] = t

        iter_counts.append(len(new_actives))

        # 종료 조건 — 새 활성 entity 0개
        if not new_actives:
            converged_iter = t
            a_q = a_q_next
            break
        a_q = a_q_next
        converged_iter = t

    # 활성 entity 결과 패키징
    nonzero_indices = np.flatnonzero(a_q)
    # 점수 desc 정렬
    nonzero_indices_sorted = nonzero_indices[np.argsort(-a_q[nonzero_indices])]
    activated_entities: list[ActivatedEntity] = []
    for idx in nonzero_indices_sorted.tolist():
        ent = graph.entities[idx]
        activated_entities.append(
            ActivatedEntity(
                eid=ent.eid,
                canonical_form=ent.canonical_form,
                activation_score=float(a_q[idx]),
                iteration=activation_iter.get(idx, -1),
            )
        )

    diagnostics = {
        "iterations_used": converged_iter,
        "new_actives_per_iter": iter_counts,
        "total_activated": len(activated_entities),
        "threshold_delta": threshold,
        "extracted_entities": _extract_query_entity_summary(query),
    }
    logger.debug("Stage 1 활성화: %s", diagnostics)

    return Stage1Result(
        activated_entities=activated_entities,
        activation_vector=a_q,
        iterations_used=converged_iter,
        diagnostics=diagnostics,
    )


# ── 내부 helper ───────────────────────────────────────────────────────


def _initial_activation(
    *,
    graph: TriGraph,
    query: str,
    embed_fn,
    n_e: int,
    min_sim: float,
    np,  # type: ignore[no-untyped-def]
):
    """식 (3) — query 에서 NER 추출한 entity → KG 에서 가장 비슷한 entity 매칭.

    매칭 우선순위:
      1. canonical_form 정확 일치 (sim = 1.0)
      2. embedding cosine similarity 최대값 (≥ min_sim)
    """
    a_q = np.zeros(n_e, dtype=np.float32)

    query_entities: list[ExtractedEntity] = extract_entities(query)
    if not query_entities:
        return a_q

    # KG entity embedding matrix 미리 추출 (캐시 가능 — 향후 그래프 객체에 보관)
    kg_emb_matrix: Optional[object] = None  # numpy 배열, lazy
    kg_eids_with_emb: list[int] = []

    for q_ent in query_entities:
        canonical = q_ent.canonical
        # 1. canonical 정확 일치
        if canonical in graph.canonical_to_eid:
            eid = graph.canonical_to_eid[canonical]
            idx = graph.eid_to_idx.get(eid)
            if idx is not None:
                # 정확 일치는 sim = 1.0 으로 처리
                if a_q[idx] < 1.0:
                    a_q[idx] = 1.0
                continue

        # 2. embedding cosine 매칭
        q_emb = embed_fn(q_ent.surface) or embed_fn(canonical)
        if q_emb is None:
            continue

        if kg_emb_matrix is None:
            # KG 의 모든 entity embedding 을 dense matrix 로 준비 (entity 수만큼 1024-dim)
            embeddings: list[tuple[float, ...]] = []
            for i, e in enumerate(graph.entities):
                if e.embedding is not None:
                    embeddings.append(e.embedding)
                    kg_eids_with_emb.append(i)
            if not embeddings:
                continue
            kg_emb_matrix = np.asarray(embeddings, dtype=np.float32)

        if kg_emb_matrix is None:
            continue

        q_vec = np.asarray(q_emb, dtype=np.float32)
        sims = kg_emb_matrix @ q_vec  # (N_with_emb,) — Titan 은 L2 정규화이므로 dot=cosine
        best = int(np.argmax(sims))
        best_sim = float(sims[best])
        if best_sim < min_sim:
            continue
        target_idx = kg_eids_with_emb[best]
        if a_q[target_idx] < best_sim:
            a_q[target_idx] = best_sim

    return a_q


def _query_sentence_relevance(
    *,
    graph: TriGraph,
    query: str,
    embed_fn,
    n_s: int,
    np,  # type: ignore[no-untyped-def]
):
    """식 (4) — query 와 모든 sentence 의 cosine similarity 벡터.

    embedding 이 없는 sentence 는 0 (sparse 효과 — sigma_q 가 dense 지만 0 이 많아도 OK).
    """
    sigma = np.zeros(n_s, dtype=np.float32)
    q_emb = embed_fn(query)
    if q_emb is None:
        return sigma
    q_vec = np.asarray(q_emb, dtype=np.float32)

    # sentence embeddings 행렬 (lazy build — embedding 없는 sentence skip)
    s_indices: list[int] = []
    s_embs: list[tuple[float, ...]] = []
    for i, s in enumerate(graph.sentences):
        if s.embedding is None:
            continue
        s_indices.append(i)
        s_embs.append(s.embedding)
    if not s_embs:
        return sigma
    s_matrix = np.asarray(s_embs, dtype=np.float32)
    sims = s_matrix @ q_vec  # (N_with_emb,)

    # 음수 cosine 은 0 으로 클리핑 (활성화 신호로는 양의 유사도만 의미 있음)
    sims = np.clip(sims, 0.0, None)

    for j, idx in enumerate(s_indices):
        sigma[idx] = sims[j]
    return sigma


def _extract_query_entity_summary(query: str) -> list[str]:
    """디버깅용 — query NER 결과의 canonical 리스트."""
    try:
        ents = extract_entities(query)
        return [e.canonical for e in ents][:10]
    except Exception:  # noqa: BLE001
        return []
