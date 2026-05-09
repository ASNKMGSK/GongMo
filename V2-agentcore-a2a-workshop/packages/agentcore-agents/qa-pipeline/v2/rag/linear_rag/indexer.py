# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Offline Indexer — Tri-Graph 빌드 파이프라인 (§3.1 Token-free Graph Construction).

입력 (corpus 형태 둘 중 하나):
    1. KMS 표 (JSON): list[ {pid, text, intent, branch, required_keywords, metadata}]
       — 영준님 V3 케이스의 메인 입력
    2. Prose corpus (JSONL): list[ {pid, text, metadata} ]
       — 약관/매뉴얼 자유서술 형태 (P2 용 확장)

처리 흐름:
    passage → sentence split → NER → entity vocab 통합 → C/M 매트릭스 → 영속화

LLM 호출 0회 — 임베딩만 (Bedrock Titan v2). 논문 §D 효율성 그대로.

V3 통합 hook:
    - tenant_root: V3 의 `/v2_data/tenants/` 디렉토리 (멀티테넌트 격리)
    - embed_fn: `from rag.embedding import embed`
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from .ner_korean import extract_entities
from .tri_graph import TriGraph, build_tri_graph, save_tri_graph
from .types import Entity, IndexingError, Passage, Sentence

logger = logging.getLogger(__name__)


# 한국어/영어 문장 분할 (간단 punctuation 기반 — paper §3.1 동일 방식)
_SENT_BOUNDARY = re.compile(r"(?<=[\.\?\!\!\?다요까})·])\s+")


@dataclass(frozen=True)
class CorpusItem:
    """인덱서 입력 표준 포맷.

    KMS 표 1행 또는 prose chunk 1개에 해당. metadata 에는 intent / branch /
    required_keywords / required_statements 등 도메인 필드를 자유롭게 담는다.
    """

    pid: str
    text: str
    metadata: dict
    # KMS 표의 "필수 키워드" 컬럼처럼 사전에 알려진 entity 후보 — NER 결과에 추가됨
    additional_keywords: list[str]


@dataclass(frozen=True)
class IndexBuildResult:
    """인덱싱 결과 요약."""

    tenant_id: str
    output_dir: Path
    graph_stats: dict
    elapsed_seconds: float


def build_index(
    *,
    tenant_id: str,
    corpus: Iterable[CorpusItem],
    tenant_root: Path,
    embed_fn: Callable[[str], Optional[tuple[float, ...]]],
    embed_passages: bool = True,
    embed_sentences: bool = True,
    embed_entities: bool = True,
    sentence_min_chars: int = 4,
) -> IndexBuildResult:
    """corpus → Tri-Graph 빌드 후 디스크 영속화.

    Args:
        tenant_id: 테넌트 식별자 (V3 의 `tenants/<tenant_id>` 와 동일).
        corpus: CorpusItem iterable (KMS 표 변환 결과 또는 prose chunks).
        tenant_root: V3 `/v2_data/tenants/` 루트 경로.
        embed_fn: 텍스트 → embedding tuple. Bedrock Titan 권장.
        embed_passages/sentences/entities: 임베딩 빌드 토글 — 비활성화 시 None 저장.
        sentence_min_chars: 문장 분할 후 너무 짧은 토막 (예: "네.") 제외 길이.

    Returns:
        IndexBuildResult — output_dir + 통계 + 경과 시간.

    Raises:
        IndexingError: corpus 비었거나 entity 0건이면.
    """
    import time

    t0 = time.perf_counter()

    passages: list[Passage] = []
    sentences: list[Sentence] = []

    # entity vocab — canonical_form 기준 dedupe
    entity_vocab: dict[str, str] = {}  # canonical → eid
    entity_surface_sample: dict[str, str] = {}  # canonical → 첫 surface 표면형
    passage_entity_links: list[tuple[str, str]] = []
    sentence_entity_links: list[tuple[str, str]] = []

    corpus_count = 0

    for item in corpus:
        corpus_count += 1
        pid = item.pid or f"p_{uuid.uuid4().hex[:12]}"
        passages.append(
            Passage(
                pid=pid,
                text=item.text,
                metadata=dict(item.metadata),
                embedding=None,  # 임베딩은 별도 패스에서 일괄 채움
            )
        )

        # passage 단위 entity 추출 (additional_keywords 포함)
        passage_entities = extract_entities(
            item.text,
            additional_keywords=item.additional_keywords or None,
        )
        for ent in passage_entities:
            canonical = ent.canonical
            if canonical not in entity_vocab:
                eid = f"e_{uuid.uuid4().hex[:12]}"
                entity_vocab[canonical] = eid
                entity_surface_sample[canonical] = ent.surface
            else:
                eid = entity_vocab[canonical]
            passage_entity_links.append((pid, eid))

        # sentence split + entity 추출
        for j, sent_text in enumerate(_split_sentences(item.text, min_chars=sentence_min_chars)):
            sid = f"{pid}_s{j}"
            sentences.append(
                Sentence(
                    sid=sid,
                    text=sent_text,
                    parent_pid=pid,
                    embedding=None,
                )
            )
            sent_entities = extract_entities(sent_text, additional_keywords=item.additional_keywords or None)
            for ent in sent_entities:
                canonical = ent.canonical
                if canonical not in entity_vocab:
                    eid = f"e_{uuid.uuid4().hex[:12]}"
                    entity_vocab[canonical] = eid
                    entity_surface_sample[canonical] = ent.surface
                else:
                    eid = entity_vocab[canonical]
                sentence_entity_links.append((sid, eid))

    if corpus_count == 0:
        raise IndexingError("corpus 가 비어있음")
    if not entity_vocab:
        raise IndexingError("entity 0건 — NER 실패 또는 corpus 의 명사가 너무 적음")

    # Entity 객체 생성 (entity_vocab 기반)
    entities: list[Entity] = []
    for canonical, eid in entity_vocab.items():
        entities.append(
            Entity(
                eid=eid,
                canonical_form=canonical,
                surface=entity_surface_sample.get(canonical, canonical),
                embedding=None,
            )
        )

    logger.info(
        "Tri-Graph 노드 수집: passages=%d, sentences=%d, entities=%d, "
        "P-E links=%d, S-E links=%d",
        len(passages),
        len(sentences),
        len(entities),
        len(passage_entity_links),
        len(sentence_entity_links),
    )

    # ── Embedding 패스 (Bedrock Titan 호출 — 인덱싱 단계 LLM 비용 0 유지) ──
    if embed_passages:
        passages = _embed_nodes(passages, embed_fn, label="passages")
    if embed_sentences:
        sentences = _embed_nodes(sentences, embed_fn, label="sentences")
    if embed_entities:
        # entity 임베딩은 canonical_form 또는 surface 둘 중 정보량 많은 쪽 사용
        entities = _embed_entities(entities, embed_fn)

    # Tri-Graph 빌드
    graph = build_tri_graph(
        tenant_id=tenant_id,
        passages=passages,
        sentences=sentences,
        entities=entities,
        passage_entity_links=passage_entity_links,
        sentence_entity_links=sentence_entity_links,
    )

    out_dir = save_tri_graph(graph, tenant_root)
    elapsed = time.perf_counter() - t0
    logger.info("LinearRAG 인덱싱 완료: tenant=%s, %.1fs", tenant_id, elapsed)

    return IndexBuildResult(
        tenant_id=tenant_id,
        output_dir=out_dir,
        graph_stats=graph.stats(),
        elapsed_seconds=elapsed,
    )


# ── helpers ──────────────────────────────────────────────────────────


def _split_sentences(text: str, *, min_chars: int) -> list[str]:
    """간단 punctuation 기반 문장 분할 (논문 §3.1 동일 방식)."""
    if not text:
        return []
    raw = _SENT_BOUNDARY.split(text)
    out: list[str] = []
    for s in raw:
        s_clean = s.strip()
        if len(s_clean) < min_chars:
            continue
        out.append(s_clean)
    return out


def _embed_nodes(nodes: list, embed_fn, *, label: str) -> list:
    """노드 리스트의 text 필드를 embed 하여 새 dataclass 인스턴스 리스트로 반환.

    dataclass 가 frozen 이므로 새로 만들기. 진행률 로그 1000개마다.
    """
    if not nodes:
        return nodes
    out = []
    n = len(nodes)
    for i, node in enumerate(nodes):
        # frozen dataclass 라 dataclasses.replace 사용
        from dataclasses import replace

        emb = embed_fn(node.text)
        out.append(replace(node, embedding=emb))
        if (i + 1) % 1000 == 0:
            logger.info("Embedding %s: %d/%d", label, i + 1, n)
    logger.info("Embedding %s 완료: %d", label, n)
    return out


def _embed_entities(entities: list[Entity], embed_fn) -> list[Entity]:
    """Entity 임베딩 — surface 우선, 짧으면 canonical 도 합쳐서 임베딩."""
    from dataclasses import replace

    out: list[Entity] = []
    for ent in entities:
        text_for_emb = ent.surface if len(ent.surface) >= 2 else ent.canonical_form
        emb = embed_fn(text_for_emb)
        out.append(replace(ent, embedding=emb))
    logger.info("Embedding entities 완료: %d", len(entities))
    return out


# ── KMS 표 → CorpusItem 변환 헬퍼 ────────────────────────────────────


def kms_table_to_corpus(kms_rows: list[dict]) -> list[CorpusItem]:
    """KMS 표 (영준님 포맷) → CorpusItem 리스트.

    기대 입력 행 스키마:
      {
        "pid": "kms_취소_온라인취소완료",        # 없으면 자동 생성
        "intent": "취소",
        "branch": "온라인 취소완료",
        "condition": "고객이 ...",
        "required_keywords": ["쿠폰", "포인트", ...],
        "required_statements": ["...", "..."],
        "is_evaluation_skip": false
      }

    text 필드는 "intent + branch + condition + required_statements 합본" 으로 구성.
    additional_keywords = required_keywords (NER 결과 + 사전 키워드 통합 → 더 풍부한 entity).
    """
    out: list[CorpusItem] = []
    for i, row in enumerate(kms_rows):
        pid = row.get("pid") or f"kms_{i}"
        intent = row.get("intent", "")
        branch = row.get("branch", "")
        condition = row.get("condition", "")
        statements = row.get("required_statements", []) or []
        keywords = row.get("required_keywords", []) or []
        is_skip = bool(row.get("is_evaluation_skip", False))

        # text 합본 — 검색 가능한 모든 정보를 한 chunk 에 (passage 단위)
        statements_text = "\n".join(statements) if isinstance(statements, list) else str(statements)
        text = (
            f"[{intent}] {branch}\n"
            f"조건: {condition}\n"
            f"필수 키워드: {', '.join(keywords)}\n"
            f"필수 안내: {statements_text}"
        ).strip()

        metadata = {
            "intent": intent,
            "branch": branch,
            "condition": condition,
            "required_keywords": keywords,
            "required_statements": statements,
            "is_evaluation_skip": is_skip,
            "source": "kms_table",
        }
        out.append(
            CorpusItem(
                pid=pid,
                text=text,
                metadata=metadata,
                additional_keywords=keywords,
            )
        )
    return out
