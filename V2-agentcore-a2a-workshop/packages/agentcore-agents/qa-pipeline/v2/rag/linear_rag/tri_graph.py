# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Tri-Graph — LinearRAG 의 핵심 자료구조 (§3.1).

3종 노드:
    Vp = passages   (검색 대상 텍스트 단위)
    Vs = sentences  (passage 의 문장 분할)
    Ve = entities   (NER + KMS 키워드 추출 결과의 canonical form)

2종 sparse adjacency matrix:
    C: |Vp| × |Ve|  contain matrix    — 식 (1) C[i,j] = ⊮{p_i contains e_j}
    M: |Vs| × |Ve|  mention matrix    — 식 (2) M[i,j] = ⊮{s_i mentions e_j}

scipy.sparse CSR 사용 (행 슬라이싱이 빈번한 PPR/SpMM 패턴에 적합).

영속화 — per-tenant:
    /v2_data/tenants/<tenant>/linear_rag/
      ├── passages.parquet   (pid, text, metadata, embedding[1024])
      ├── sentences.parquet  (sid, parent_pid, text, embedding[1024])
      ├── entities.parquet   (eid, canonical, surface, embedding[1024])
      ├── matrix_C.npz       (scipy.sparse CSR)
      └── matrix_M.npz       (scipy.sparse CSR)

AOSS 로 옮길 시 embedding 컬럼만 knn_vector 인덱스에 별도 저장하고 매트릭스/메타는
parquet 유지하면 된다 (V3 의 기존 aoss_store 패턴과 동일).
"""

from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .types import Entity, IndexingError, Passage, Sentence, TenantNotIndexed

logger = logging.getLogger(__name__)


@dataclass
class TriGraph:
    """LinearRAG Tri-Graph — passages / sentences / entities + C / M matrices.

    노드는 list 로 보관하고 dict 로 ID → index 빠른 조회. scipy.sparse 매트릭스는
    빌드 후 직렬화하여 디스크에 저장 (한 번 빌드, 여러 retrieval 에서 재사용).
    """

    tenant_id: str
    passages: list[Passage]
    sentences: list[Sentence]
    entities: list[Entity]

    # ID → row index (검색 시 빠른 lookup)
    pid_to_idx: dict[str, int]
    sid_to_idx: dict[str, int]
    eid_to_idx: dict[str, int]
    canonical_to_eid: dict[str, str]  # canonical_form → eid (정규화 후 lookup)

    # sparse matrices (scipy.sparse.csr_matrix); 타입은 임포트 의존성으로 Any 처리
    contain_matrix: object  # |Vp| × |Ve|
    mention_matrix: object  # |Vs| × |Ve|

    def num_passages(self) -> int:
        return len(self.passages)

    def num_sentences(self) -> int:
        return len(self.sentences)

    def num_entities(self) -> int:
        return len(self.entities)

    def stats(self) -> dict:
        """디버그/모니터링용 그래프 통계."""
        try:
            import scipy.sparse as sp  # type: ignore

            c_density = (
                float(self.contain_matrix.nnz) / (self.contain_matrix.shape[0] * self.contain_matrix.shape[1])
                if self.num_passages() and self.num_entities()
                else 0.0
            )
            m_density = (
                float(self.mention_matrix.nnz) / (self.mention_matrix.shape[0] * self.mention_matrix.shape[1])
                if self.num_sentences() and self.num_entities()
                else 0.0
            )
        except ImportError:
            c_density = m_density = 0.0
        return {
            "tenant_id": self.tenant_id,
            "num_passages": self.num_passages(),
            "num_sentences": self.num_sentences(),
            "num_entities": self.num_entities(),
            "C_nnz": getattr(self.contain_matrix, "nnz", 0),
            "M_nnz": getattr(self.mention_matrix, "nnz", 0),
            "C_density": c_density,
            "M_density": m_density,
        }


# ── 빌더 ──────────────────────────────────────────────────────────────


def build_tri_graph(
    tenant_id: str,
    passages: list[Passage],
    sentences: list[Sentence],
    entities: list[Entity],
    passage_entity_links: list[tuple[str, str]],  # (pid, eid) 튜플 — 식 (1) 입력
    sentence_entity_links: list[tuple[str, str]],  # (sid, eid) 튜플 — 식 (2) 입력
) -> TriGraph:
    """노드 + edge 리스트로부터 Tri-Graph 생성.

    매트릭스 sparsity 가 보장됨 (논문: 한 sentence 평균 ~4 entities, 한 passage ~10).
    scipy.sparse CSR 로 메모리 선형 (논문 §D).
    """
    try:
        import numpy as np  # type: ignore
        import scipy.sparse as sp  # type: ignore
    except ImportError as exc:
        raise IndexingError("scipy / numpy 미설치 — `pip install scipy numpy`") from exc

    if not passages:
        raise IndexingError("passages 가 비어있음 — 인덱싱 불가")
    if not entities:
        raise IndexingError("entities 가 비어있음 — NER 또는 키워드 추출 결과 확인")

    pid_to_idx = {p.pid: i for i, p in enumerate(passages)}
    sid_to_idx = {s.sid: i for i, s in enumerate(sentences)}
    eid_to_idx = {e.eid: i for i, e in enumerate(entities)}
    canonical_to_eid = {e.canonical_form: e.eid for e in entities}

    n_p = len(passages)
    n_s = len(sentences)
    n_e = len(entities)

    # contain_matrix C: |Vp| × |Ve|
    c_rows: list[int] = []
    c_cols: list[int] = []
    skipped_c = 0
    for pid, eid in passage_entity_links:
        if pid not in pid_to_idx or eid not in eid_to_idx:
            skipped_c += 1
            continue
        c_rows.append(pid_to_idx[pid])
        c_cols.append(eid_to_idx[eid])
    c_data = np.ones(len(c_rows), dtype=np.float32)
    contain_matrix = sp.csr_matrix(
        (c_data, (c_rows, c_cols)),
        shape=(n_p, n_e),
        dtype=np.float32,
    )
    contain_matrix.sum_duplicates()

    # mention_matrix M: |Vs| × |Ve|
    m_rows: list[int] = []
    m_cols: list[int] = []
    skipped_m = 0
    for sid, eid in sentence_entity_links:
        if sid not in sid_to_idx or eid not in eid_to_idx:
            skipped_m += 1
            continue
        m_rows.append(sid_to_idx[sid])
        m_cols.append(eid_to_idx[eid])
    m_data = np.ones(len(m_rows), dtype=np.float32)
    mention_matrix = sp.csr_matrix(
        (m_data, (m_rows, m_cols)),
        shape=(n_s, n_e),
        dtype=np.float32,
    )
    mention_matrix.sum_duplicates()

    if skipped_c or skipped_m:
        logger.warning(
            "Tri-Graph 빌드 — orphan link skipped: C=%d, M=%d (passage/entity ID mismatch)",
            skipped_c,
            skipped_m,
        )

    graph = TriGraph(
        tenant_id=tenant_id,
        passages=passages,
        sentences=sentences,
        entities=entities,
        pid_to_idx=pid_to_idx,
        sid_to_idx=sid_to_idx,
        eid_to_idx=eid_to_idx,
        canonical_to_eid=canonical_to_eid,
        contain_matrix=contain_matrix,
        mention_matrix=mention_matrix,
    )
    logger.info("Tri-Graph 빌드 완료: %s", graph.stats())
    return graph


# ── 영속화 ────────────────────────────────────────────────────────────


def _tenant_dir(tenant_root: Path, tenant_id: str) -> Path:
    return tenant_root / tenant_id / "linear_rag"


def save_tri_graph(graph: TriGraph, tenant_root: Path) -> Path:
    """Tri-Graph 를 디스크에 저장. 노드는 pickle, 매트릭스는 scipy npz."""
    try:
        import scipy.sparse as sp  # type: ignore
    except ImportError as exc:
        raise IndexingError("scipy 미설치") from exc

    out_dir = _tenant_dir(tenant_root, graph.tenant_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 노드 (passages/sentences/entities) — pickle
    with (out_dir / "nodes.pkl").open("wb") as f:
        pickle.dump(
            {
                "tenant_id": graph.tenant_id,
                "passages": graph.passages,
                "sentences": graph.sentences,
                "entities": graph.entities,
                "pid_to_idx": graph.pid_to_idx,
                "sid_to_idx": graph.sid_to_idx,
                "eid_to_idx": graph.eid_to_idx,
                "canonical_to_eid": graph.canonical_to_eid,
            },
            f,
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    # 매트릭스 — npz
    sp.save_npz(out_dir / "matrix_C.npz", graph.contain_matrix)
    sp.save_npz(out_dir / "matrix_M.npz", graph.mention_matrix)

    # 메타 — JSON (디버깅용)
    meta = {
        **graph.stats(),
        "schema_version": 1,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("Tri-Graph 저장: %s", out_dir)
    return out_dir


def load_tri_graph(tenant_id: str, tenant_root: Path) -> TriGraph:
    """디스크에서 Tri-Graph 로드."""
    try:
        import scipy.sparse as sp  # type: ignore
    except ImportError as exc:
        raise IndexingError("scipy 미설치") from exc

    in_dir = _tenant_dir(tenant_root, tenant_id)
    if not in_dir.exists():
        raise TenantNotIndexed(f"Tri-Graph 없음: {in_dir}")

    nodes_path = in_dir / "nodes.pkl"
    c_path = in_dir / "matrix_C.npz"
    m_path = in_dir / "matrix_M.npz"
    if not nodes_path.exists() or not c_path.exists() or not m_path.exists():
        raise TenantNotIndexed(f"Tri-Graph 일부 파일 누락: {in_dir}")

    with nodes_path.open("rb") as f:
        nodes = pickle.load(f)

    contain_matrix = sp.load_npz(c_path)
    mention_matrix = sp.load_npz(m_path)

    graph = TriGraph(
        tenant_id=nodes["tenant_id"],
        passages=nodes["passages"],
        sentences=nodes["sentences"],
        entities=nodes["entities"],
        pid_to_idx=nodes["pid_to_idx"],
        sid_to_idx=nodes["sid_to_idx"],
        eid_to_idx=nodes["eid_to_idx"],
        canonical_to_eid=nodes["canonical_to_eid"],
        contain_matrix=contain_matrix,
        mention_matrix=mention_matrix,
    )
    logger.info("Tri-Graph 로드: %s", graph.stats())
    return graph


def tri_graph_exists(tenant_id: str, tenant_root: Path) -> bool:
    in_dir = _tenant_dir(tenant_root, tenant_id)
    return all(
        (in_dir / name).exists()
        for name in ("nodes.pkl", "matrix_C.npz", "matrix_M.npz")
    )
