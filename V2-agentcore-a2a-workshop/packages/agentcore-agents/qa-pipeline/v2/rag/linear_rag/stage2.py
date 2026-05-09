# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Stage 2 — Passage Retrieval via Global Importance Aggregation (§3.2.2).

Stage 1 의 활성 entity 점수 + query-passage 유사도를 hybrid 로 초기화한 후
passage-entity bipartite graph 위에서 Personalized PageRank (PPR) 를 돌려
글로벌 중요도 점수를 aggregation.

  식 (6) PPR (sparse iterative):
        I(v_i) = (1 - d) + d · Σ_{v_j ∈ B(v_i)} I(v_j) / deg(v_j)
        d = damping factor (0.85 권장)

  식 (7) Hybrid initialization for passage nodes:
        I(v|v ∈ V_p) = (λ · sim(q, v) + ln(1 + Σ_{e_i ∈ E_a} a^(i)_q · ln(1 + N_{e_i}) / L_{e_i})) · W_p
        — λ : DPR 유사도 가중치 (논문 권장 0.05 — entity 정보가 주, dense sim 보조)
        — N_{e_i} : passage 안 entity i 등장 횟수
        — L_{e_i} : entity 의 hierarchical level (없으면 1)
        — W_p   : passage 노드 가중치 (기본 1.0)

  Entity 노드 초기화 (논문 § 3.2.2 본문):
        I(v|v ∈ V_e) = a_q^(i)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from .stage1 import Stage1Result
from .tri_graph import TriGraph
from .types import RetrievalError, RetrievedPassage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stage2Result:
    passages: list[RetrievedPassage]
    diagnostics: dict


def retrieve_passages(
    *,
    graph: TriGraph,
    stage1_result: Stage1Result,
    query: str,
    embed_fn,
    top_k: int = 5,
    damping: float = 0.85,
    lambda_coef: float = 0.05,
    passage_weight: float = 1.0,
    max_iter: int = 50,
    tol: float = 1e-6,
) -> Stage2Result:
    """식 (6), (7) 적용하여 top-k passage retrieval.

    Args:
        graph: Tri-Graph.
        stage1_result: Stage 1 출력 (활성 entity + activation vector).
        query: 쿼리 텍스트 (DPR sim 계산용).
        embed_fn: 임베딩 함수.
        top_k: 반환 개수.
        damping: PPR damping factor (논문 0.85).
        lambda_coef: 식 (7) λ — DPR 유사도 가중치 (논문 0.05 권장).
        passage_weight: 식 (7) W_p — passage 노드 강조 (기본 1.0).
        max_iter: PPR power iteration 한도.
        tol: PPR 수렴 임계 (L1 difference).
    """
    try:
        import numpy as np  # type: ignore
        import scipy.sparse as sp  # type: ignore
    except ImportError as exc:
        raise RetrievalError("numpy / scipy 미설치") from exc

    n_p = graph.num_passages()
    n_e = graph.num_entities()
    if n_p == 0:
        return Stage2Result(passages=[], diagnostics={"reason": "empty_passages"})

    a_q = stage1_result.activation_vector
    if a_q is None:
        a_q = np.zeros(n_e, dtype=np.float32)

    # ── 식 (7) — passage 노드 초기 점수 ──
    passage_init = _hybrid_init_passages(
        graph=graph,
        query=query,
        embed_fn=embed_fn,
        a_q=a_q,
        lambda_coef=lambda_coef,
        passage_weight=passage_weight,
        np=np,
    )

    # entity 노드 초기 점수 = a_q 그대로
    entity_init = a_q.astype(np.float32, copy=True)

    # 합쳐서 PPR personalization vector 만들기. 노드 순서: [V_p ; V_e]
    # (passage 0..n_p-1, entity n_p..n_p+n_e-1)
    n_total = n_p + n_e
    pers = np.zeros(n_total, dtype=np.float32)
    pers[:n_p] = passage_init
    pers[n_p:] = entity_init

    # 정규화 (PPR personalization 은 합 1 권장)
    pers_sum = float(pers.sum())
    if pers_sum <= 0:
        # 활성 entity 도 없고 passage init 도 0 → uniform fallback (cold-start)
        pers = np.ones(n_total, dtype=np.float32) / n_total
        logger.info("Stage 2: cold-start — uniform personalization")
    else:
        pers = pers / pers_sum

    # ── 식 (6) — Personalized PageRank ──
    scores = _power_iteration_ppr(
        graph=graph,
        n_p=n_p,
        n_e=n_e,
        personalization=pers,
        damping=damping,
        max_iter=max_iter,
        tol=tol,
        np=np,
        sp=sp,
    )

    # passage 점수만 추출 + top-k
    passage_scores = scores[:n_p]
    if top_k <= 0 or top_k >= n_p:
        top_k = n_p
    top_indices = np.argpartition(-passage_scores, top_k - 1)[:top_k]
    top_indices = top_indices[np.argsort(-passage_scores[top_indices])]

    retrieved: list[RetrievedPassage] = []
    for idx in top_indices.tolist():
        p = graph.passages[idx]
        retrieved.append(
            RetrievedPassage(
                pid=p.pid,
                text=p.text,
                ppr_score=float(passage_scores[idx]),
                metadata=dict(p.metadata),
            )
        )

    diagnostics = {
        "n_total_nodes": n_total,
        "personalization_sum": pers_sum,
        "ppr_iterations": int(scores.shape[0]) if hasattr(scores, "shape") else None,
        "top_k": top_k,
    }
    return Stage2Result(passages=retrieved, diagnostics=diagnostics)


# ── helpers ──────────────────────────────────────────────────────────


def _hybrid_init_passages(
    *,
    graph: TriGraph,
    query: str,
    embed_fn,
    a_q,  # numpy.ndarray (n_e,)
    lambda_coef: float,
    passage_weight: float,
    np,  # type: ignore[no-untyped-def]
):
    """식 (7) — passage 별 초기 importance 계산.

    I(v|v ∈ V_p) = (λ · sim(q, v) + ln(1 + Σ_{e_i ∈ E_a} a^(i)_q · ln(1 + N_{e_i}) / L_{e_i})) · W_p
    """
    n_p = graph.num_passages()
    init = np.zeros(n_p, dtype=np.float32)

    # 1) DPR similarity sim(q, v) — query vs passage embedding
    q_emb = embed_fn(query)
    sim_qv = np.zeros(n_p, dtype=np.float32)
    if q_emb is not None:
        q_vec = np.asarray(q_emb, dtype=np.float32)
        # passage embedding 매트릭스 (없는 건 0)
        for i, p in enumerate(graph.passages):
            if p.embedding is None:
                continue
            v = np.asarray(p.embedding, dtype=np.float32)
            sim_qv[i] = max(0.0, float(np.dot(q_vec, v)))

    # 2) Entity 기여도 항: ln(1 + Σ a_q · ln(1 + N_ei) / L_ei)
    # contain_matrix C 는 binary [0/1] 인데 N_ei 는 occurrence count 가 필요.
    # 빌드 시 C 를 binary 로 만들었으므로 N_ei = 1 가정 (KMS 도메인은 entity 가 한 행에서
    # 보통 1번만 등장). 향후 weighted variant 가 필요하면 인덱서 단계에서 N_ei 를 그대로 누적.
    # L_ei (hierarchical level): 본 구현에서는 1 (flat). 향후 community detection 로 확장 가능.
    C = graph.contain_matrix  # |Vp| × |Ve|

    # passage 별 합계: Σ_{e_i ∈ E_a} a^(i)_q · ln(1 + N_ei) / L_ei
    # vectorized: C 의 각 행 (passage) 와 a_q 를 결합, ln(1 + 1) = ln 2 = 0.693 (binary 가정)
    # → contribution = C.dot(a_q) * ln(2)  (각 passage 에 포함된 활성 entity 의 점수 합)
    if (a_q > 0).any():
        contribution = C.dot(a_q) * math.log(2.0)
        contribution = np.log1p(np.maximum(contribution, 0.0))  # ln(1 + ...)
    else:
        contribution = np.zeros(n_p, dtype=np.float32)

    # 3) 결합
    init = (lambda_coef * sim_qv + contribution) * passage_weight
    init = np.clip(init, 0.0, None)  # 음수 방지
    return init


def _power_iteration_ppr(
    *,
    graph: TriGraph,
    n_p: int,
    n_e: int,
    personalization,  # numpy (n_p + n_e,)
    damping: float,
    max_iter: int,
    tol: float,
    np,  # type: ignore[no-untyped-def]
    sp,  # type: ignore[no-untyped-def]
):
    """passage-entity bipartite graph 위 power iteration PPR.

    노드 순서: [V_p (0..n_p-1) ; V_e (n_p..n_p+n_e-1)]
    Adjacency: passage ↔ entity (bipartite) — graph.contain_matrix C 가 곧 양방향 edge.

    sparse 전이행렬 P 를 명시적으로 만들면 메모리 폭발 가능. 대신 두 부분 (C, C^T) 을
    분리해 sparse matvec 로 step 마다 계산.
    """
    n_total = n_p + n_e
    C = graph.contain_matrix  # |Vp| × |Ve| (binary)

    # out-degree (행/열 합)
    deg_p = np.asarray(C.sum(axis=1)).flatten()  # passage out-degree → entity 로 가는 edge 수
    deg_e = np.asarray(C.sum(axis=0)).flatten()  # entity out-degree → passage 로 가는 edge 수
    # 0-degree 노드는 dangling 처리 (자기 자신으로 점프 — 일반적인 PPR 트릭)
    deg_p_safe = np.where(deg_p > 0, deg_p, 1.0)
    deg_e_safe = np.where(deg_e > 0, deg_e, 1.0)

    # 정규화된 sparse: row-stochastic 형태로 곱하기 위해 1/deg 를 미리 적용
    # row-normalize C: 각 행 (passage) 의 합이 1 → entity 로 보낼 점수 분배
    C_csr = C.tocsr()
    # diag(1/deg_p) @ C  (sparse multiplication)
    inv_deg_p = sp.diags(1.0 / deg_p_safe)
    C_norm = inv_deg_p @ C_csr  # |Vp| × |Ve|, 각 행 합 = 1 (단, deg_p>0 인 행만)

    # column-normalize C → entity 가 passage 로 보낼 점수 분배 (transpose 후 row-normalize)
    C_T_csr = C.T.tocsr()
    inv_deg_e = sp.diags(1.0 / deg_e_safe)
    C_T_norm = inv_deg_e @ C_T_csr  # |Ve| × |Vp|

    # 초기 점수 = personalization
    scores = personalization.astype(np.float32, copy=True)
    teleport = (1.0 - damping) * personalization

    for it in range(1, max_iter + 1):
        prev_scores = scores
        p_scores = scores[:n_p]
        e_scores = scores[n_p:]

        # passage → entity: e_new[j] = Σ_i p_scores[i] * C_norm[i,j]
        e_new = C_norm.T.dot(p_scores)  # (n_e,)
        # entity → passage: p_new[i] = Σ_j e_scores[j] * C_T_norm[j,i] = Σ_j C_norm[i,j]/deg_e[j] * e_scores[j]
        p_new = C_T_norm.T.dot(e_scores)  # (n_p,)

        new_scores = np.empty_like(scores)
        new_scores[:n_p] = damping * p_new + teleport[:n_p]
        new_scores[n_p:] = damping * e_new + teleport[n_p:]

        # L1 수렴 체크
        delta = float(np.abs(new_scores - prev_scores).sum())
        scores = new_scores
        if delta < tol:
            logger.debug("PPR 수렴: iter=%d, delta=%.2e", it, delta)
            break

    return scores
