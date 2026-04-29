# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Business Knowledge RAG (RAG-3) — 업무지식 매뉴얼 chunk retrieval.

설계서 7장:
  - retrieval key = (intent, query)
  - 반환: KnowledgeResult(chunks, source_refs, unevaluable)
  - **RAG 부재 시 `unevaluable=True` 반환** — #15 Sub Agent 가 unevaluable 로 분기.
  - 사용 주체: #15 정확한 안내 (Dev3) 전용. 다른 평가 항목은 호출 금지.

**금지 사용 (원칙 7.5)**:
  - 업무지식 RAG 결과를 LLM 에 재질의하지 않고 점수를 자동 산출하는 방식 금지.
    반드시 Sub Agent 가 LLM 으로 transcript 와 chunk 간 사실 일치를 판단해야 함.
  - chunk 가 없음에도 heuristic 으로 점수를 부여하는 로직 금지 → unevaluable.

Chunk 파싱 규약 (tenants/<id>/business_knowledge/manual.md):
  - `## H2` 제목 **직전 라인** 에 `<!-- meta: {"chunk_id": "...", "intent": [...], "tags": [...]} -->`
    주석이 있을 때만 정식 chunk 로 인정. meta 없는 H2 는 문서 서문/꼬리표로 간주 스킵.
  - 본문 마지막 `**source_ref**: <ref>` 는 출처 (선택).
  - H2 본문은 다음 H2 또는 다음 meta 주석 또는 EOF 까지.

prototype 유사도: tokenize + Jaccard + intent 필터. production 교체 시
embedding 모델 (Bedrock Titan Embed V2) + vector store 로 변경.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from ._util import jaccard, read_text, tenant_dir, tokenize
from .types import KnowledgeChunk, KnowledgeResult, RAGUnavailable


logger = logging.getLogger(__name__)

_BACKEND_ENV = "QA_RAG_BACKEND"  # "aoss" (기본) | "jaccard"


def _active_backend() -> str:
    return (os.environ.get(_BACKEND_ENV) or "aoss").strip().lower()


# meta 주석 + 바로 뒤 H2 제목을 한 쌍으로 매칭. 다음 meta 또는 EOF 직전까지가 chunk 본문.
# meta JSON 은 반드시 `{"chunk_id":` 로 시작해야 실제 chunk 메타로 인정
# (예시 주석 / 문서 설명에 포함된 가짜 `{...}` 오탐 방지).
_CHUNK_RE = re.compile(
    r'<!--\s*meta:\s*(\{\s*"chunk_id"\s*:\s*"[^"]+".*?\})\s*-->\s*\n'  # meta 주석 (chunk_id 필수)
    r"##\s+(.+?)\n"                                                      # H2 제목
    r"(.*?)"                                                             # 본문 (non-greedy)
    r"(?=\n<!--\s*meta:\s*\{|\Z)",                                       # 다음 meta 시작 또는 문서 끝
    re.DOTALL,
)
_SOURCE_RE = re.compile(r"\*\*source_ref\*\*:\s*(.+?)(?:\n|$)")


class BusinessKnowledgeRAG:
    """manual.md 로드 → H2 단위 chunk 분할 → 메타 파싱.

    3단계 멀티테넌트 (2026-04-24): channel/department 추가. 경로는
    resolve_tenant_subdir fallback 체인으로 찾는다 → 레거시
    tenants/{site}/business_knowledge/manual.md 도 fallback 4단계에서 잡힘.
    """

    def __init__(
        self,
        tenant_id: str = "generic",
        top_k: int = 3,
        manual_filename: str = "manual.md",
        channel: str = "inbound",
        department: str = "default",
    ):
        self.tenant_id = tenant_id
        self.channel = channel
        self.department = department
        self.top_k = top_k
        from v2.rag._util import resolve_tenant_subdir
        _bk_dir = resolve_tenant_subdir(tenant_id, "business_knowledge", channel, department)
        self._manual_path = os.path.join(_bk_dir, manual_filename)
        self._chunks: Optional[list[KnowledgeChunk]] = None

    # ---- loading -----------------------------------------------------------

    def _load_chunks(self) -> list[KnowledgeChunk]:
        if self._chunks is not None:
            return self._chunks

        raw = read_text(self._manual_path)
        if not raw.strip():
            logger.warning("business_knowledge manual missing: %s", self._manual_path)
            self._chunks = []
            return []

        chunks: list[KnowledgeChunk] = []
        for idx, match in enumerate(_CHUNK_RE.finditer(raw)):
            meta_raw, title, body = match.group(1), match.group(2).strip(), match.group(3)

            try:
                meta = json.loads(meta_raw)
            except json.JSONDecodeError as e:
                logger.warning("business_knowledge: meta json parse failed (chunk idx=%d): %s", idx, e)
                continue  # meta 없으면 skip — 이 chunk 는 신뢰할 수 없음

            chunk_id = meta.get("chunk_id", f"{self.tenant_id}-BK-{idx:03d}")
            intents = meta.get("intent", [])
            tags = meta.get("tags", [])

            body_text = body.strip()
            source_ref = ""
            sm = _SOURCE_RE.search(body_text)
            if sm:
                source_ref = sm.group(1).strip()
                body_text = _SOURCE_RE.sub("", body_text).strip()

            # '---' 구분선은 chunk 경계이므로 끝단 정리
            body_text = body_text.rstrip("-").strip()

            # tags 를 body 에 append — 키워드 매칭 정확도 향상
            tag_line = " ".join(tags) if tags else ""
            full_text = f"{title}\n{body_text}\n{tag_line}".strip()
            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    text=full_text,
                    intents=intents,
                    tags=tags,
                    source_ref=source_ref,
                    score=0.0,
                )
            )

        self._chunks = chunks
        return chunks

    # ---- retrieval ---------------------------------------------------------

    def retrieve(
        self,
        intent: str,
        query: str,
        top_k: Optional[int] = None,
    ) -> KnowledgeResult:
        """3 경로 분기 (Dev3 #15 A안 절충):

        - 경로 1 정상 hit: `unevaluable=False, no_hit_but_evaluable=False` → Sub Agent 정상 evaluation.
        - 경로 2 chunk 는 있으나 top hit 유사도 낮음: `unevaluable=False, no_hit_but_evaluable=True`
          → Sub Agent `evaluation_mode=partial_with_review` 권고, force_hitl + llm_self cap.
        - 경로 3 매뉴얼 부재 / intent 범위 밖: `unevaluable=True, truly_unevaluable=True`
          → Sub Agent `evaluation_mode=unevaluable` 분기, 점수 산출 중단.
        """
        k = top_k or self.top_k

        # === AOSS 경로 ===
        if _active_backend() == "aoss":
            try:
                return self._retrieve_aoss(intent, query, k)
            except RAGUnavailable:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("AOSS business_knowledge retrieve 실패 → jaccard 폴백: %s", e)

        # === Jaccard 로컬 경로 (원본) ===
        chunks = self._load_chunks()

        if not chunks:
            # 경로 3: 매뉴얼 자체 부재
            return KnowledgeResult(
                intent=intent,
                query=query,
                chunks=[],
                source_refs=[],
                unevaluable=True,
                truly_unevaluable=True,
                no_hit_but_evaluable=False,
                match_reason=f"manual_missing: {self._manual_path}",
            )

        # 1) intent 필터
        def intent_match(c: KnowledgeChunk) -> bool:
            if not c.intents:
                return True
            if intent == "*":
                return True
            return intent in c.intents

        candidates = [c for c in chunks if intent_match(c)]
        if not candidates:
            # 경로 3: intent 매칭 chunk 없음 — 매뉴얼 범위 밖
            return KnowledgeResult(
                intent=intent,
                query=query,
                chunks=[],
                source_refs=[],
                unevaluable=True,
                truly_unevaluable=True,
                no_hit_but_evaluable=False,
                match_reason=f"no_chunk_for_intent={intent}",
            )

        # 2) Jaccard 유사도
        qt = tokenize(query)
        scored: list[KnowledgeChunk] = []
        for c in candidates:
            sim = jaccard(qt, tokenize(c.text))
            scored.append(
                KnowledgeChunk(
                    chunk_id=c.chunk_id,
                    text=c.text,
                    intents=c.intents,
                    tags=c.tags,
                    source_ref=c.source_ref,
                    score=sim,
                )
            )
        scored.sort(key=lambda x: x.score, reverse=True)
        top = scored[:k]

        # 3) 경로 2: intent chunk 존재하지만 top 유사도 너무 낮음 (partial_with_review)
        if top and max(c.score for c in top) == 0.0:
            return KnowledgeResult(
                intent=intent,
                query=query,
                chunks=top,
                source_refs=[c.source_ref for c in top if c.source_ref],
                unevaluable=False,
                truly_unevaluable=False,
                no_hit_but_evaluable=True,
                match_reason=f"zero_similarity_but_intent_matched: pool={len(candidates)}",
            )

        # 경로 1: 정상 hit
        return KnowledgeResult(
            intent=intent,
            query=query,
            chunks=top,
            source_refs=[c.source_ref for c in top if c.source_ref],
            unevaluable=False,
            truly_unevaluable=False,
            no_hit_but_evaluable=False,
            match_reason=f"intent={intent}; pool={len(candidates)}; k={len(top)}; top_sim={top[0].score:.3f}",
        )

    def _retrieve_aoss(self, intent: str, query: str, k: int) -> KnowledgeResult:
        """AOSS KNN — Titan embed + cosine. intent 필터는 pre-filter 로 적용."""
        from .aoss_store import get_store, KNOWLEDGE_INDEX
        from .embedding import embed

        vec = embed(query)
        if vec is None:
            raise RuntimeError("Titan embed 실패")
        store = get_store(KNOWLEDGE_INDEX)

        # intent 필터 — intents 배열에 요청 intent 포함된 chunk
        extra_filters = None
        if intent and intent != "*":
            extra_filters = [{"term": {"intents": intent}}]

        hits = store.search_hybrid(
            query_text=query,
            query_vector=list(vec),
            top_k=k,
            tenant_id=self.tenant_id,
            channel=self.channel,
            department=self.department,
            extra_filters=extra_filters,
            text_fields=["title^2", "text^1", "tags^1"],
        )
        if not hits:
            # intent 매칭 chunk 없음 → tenant 만으로 재시도 (매뉴얼 범위 밖 판단용)
            if extra_filters:
                tenant_hits = store.search_hybrid(
                    query_text=query, query_vector=list(vec),
                    top_k=1, tenant_id=self.tenant_id,
                    channel=self.channel, department=self.department,
                    text_fields=["title^2", "text^1", "tags^1"],
                )
                if not tenant_hits:
                    return KnowledgeResult(
                        intent=intent, query=query, chunks=[], source_refs=[],
                        unevaluable=True, truly_unevaluable=True,
                        no_hit_but_evaluable=False,
                        match_reason=f"aoss: tenant={self.tenant_id} 매뉴얼 색인 전무",
                    )
                return KnowledgeResult(
                    intent=intent, query=query, chunks=[], source_refs=[],
                    unevaluable=True, truly_unevaluable=True,
                    no_hit_but_evaluable=False,
                    match_reason=f"aoss: no_chunk_for_intent={intent}",
                )
            return KnowledgeResult(
                intent=intent, query=query, chunks=[], source_refs=[],
                unevaluable=True, truly_unevaluable=True,
                no_hit_but_evaluable=False,
                match_reason=f"aoss: no chunks for tenant={self.tenant_id}",
            )

        chunks: list[KnowledgeChunk] = []
        for h in hits:
            chunks.append(
                KnowledgeChunk(
                    chunk_id=h.get("chunk_id") or "",
                    text=h.get("text") or "",
                    intents=h.get("intents") or [],
                    tags=h.get("tags") or [],
                    source_ref=h.get("source_ref") or "",
                    score=float(h.get("_score") or 0.0),
                )
            )
        return KnowledgeResult(
            intent=intent, query=query,
            chunks=chunks,
            source_refs=[c.source_ref for c in chunks if c.source_ref],
            unevaluable=False, truly_unevaluable=False,
            no_hit_but_evaluable=False,
            match_reason=f"aoss intent={intent}; k={len(chunks)}; top_sim={chunks[0].score:.3f}",
        )


# ---------------------------------------------------------------------------
# Module-level API
# ---------------------------------------------------------------------------

_ENGINE_CACHE: dict[tuple[str, str, str], BusinessKnowledgeRAG] = {}


def _get_engine(
    tenant_id: str = "generic",
    channel: str = "inbound",
    department: str = "default",
) -> BusinessKnowledgeRAG:
    """tenant + channel + department 조합 별 engine 캐시.

    한 프로세스에서 신한 5개 부서 (collection/consumer/review/compliance/crm) 동시 호출
    되므로 단일 모듈 캐시 (_DEFAULT_ENGINE) 로는 매 호출마다 재인스턴스화 발생 → tuple 키
    캐시로 변경.
    """
    key = (tenant_id, channel, department)
    eng = _ENGINE_CACHE.get(key)
    if eng is None:
        eng = BusinessKnowledgeRAG(tenant_id=tenant_id, channel=channel, department=department)
        _ENGINE_CACHE[key] = eng
    return eng


def retrieve_knowledge(
    intent: str,
    query: str,
    *,
    tenant_id: str = "generic",
    channel: str = "inbound",
    department: str = "default",
    top_k: int = 3,
) -> KnowledgeResult:
    """설계서 공개 API — #15 정확한 안내 + 부서특화 Sub Agent 용.

    `KnowledgeResult.unevaluable=True` 이면 Sub Agent 는 반드시 점수 산출을 중단하고
    `unevaluable` 상태로 반환해야 한다 (원칙 7.5 / 설계서 7장 강제).

    3단계 멀티테넌트 (2026-04-30): channel/department 인자 추가. resolve_tenant_subdir
    fallback 체인이 적용되어 부서별 manual.md 가 우선, 없으면 tenant 메인 manual.md 로
    폴백.
    """
    engine = _get_engine(tenant_id, channel, department)
    result = engine.retrieve(intent, query, top_k=top_k)
    chunks = result.chunks or []
    if chunks:
        logger.info(
            "[RAG knowledge] tenant=%s ch=%s dept=%s intent=%s → %d hits unevaluable=%s (chunk_ids=%s, scores=%s)",
            tenant_id, channel, department, intent, len(chunks),
            getattr(result, "unevaluable", False),
            [c.chunk_id for c in chunks[:5]],
            [round(c.score, 3) for c in chunks[:5]],
        )
    else:
        logger.info(
            "[RAG knowledge] tenant=%s ch=%s dept=%s intent=%s → 0 hits unevaluable=%s (query_len=%d)",
            tenant_id, channel, department, intent, getattr(result, "unevaluable", False), len(query or ""),
        )
    return result


# ---------------------------------------------------------------------------
# Dev3 Sub Agent 호환 어댑터 — async + dict 반환 (_rag_mock 대체용)
# ---------------------------------------------------------------------------


async def lookup_business_knowledge(
    consultation_type: str,
    intent: str,
    product: str | None,
    transcript_slice: str,
    top_k: int = 5,
    *,
    tenant_id: str = "generic",
) -> dict:
    """Dev3 `v2/agents/group_b/work_accuracy.py` 호환 async 어댑터.

    `_rag_mock.lookup_business_knowledge_mock` 과 동일 시그니처 + 반환 형태로 구현되어
    `from v2.rag import lookup_business_knowledge as lookup_business_knowledge_mock`
    한 줄 교체로 실제 RAG 전환 가능.

    반환 dict 키 (Dev3 계약):
        available  : bool — 매뉴얼 chunk 로드 + intent 매칭 chunk 존재 여부
        hits       : list[{doc_id, title, snippet, score, source}]
        coverage   : float (0.0~1.0) — intent 매칭 chunk 비율
        confidence : float (0.0~1.0) — top-1 retrieval 유사도

    `consultation_type` / `product` 는 현재 prototype 에서 query boost 와 디버그 로그에만
    사용. production 에서는 tenant 분기 / 상품 특화 chunk 필터에 활용될 수 있다.
    """
    engine = _get_engine(tenant_id)
    all_chunks = engine._load_chunks()
    if not all_chunks:
        return {
            "available": False,
            "hits": [],
            "coverage": 0.0,
            "confidence": 0.0,
            "match_reason": f"manual_missing (tenant={tenant_id})",
            "consultation_type": consultation_type,
            "product": product,
        }

    # query 구성: transcript_slice + product (제품명 가중치)
    query_parts = [transcript_slice]
    if product:
        query_parts.append(product)
    query = " ".join(q for q in query_parts if q)

    kr = engine.retrieve(intent, query, top_k=top_k)

    # intent 매칭 chunk 전체 — coverage 분모
    intent_chunks = [
        c for c in all_chunks
        if (not c.intents) or intent in c.intents or intent == "*"
    ]
    coverage = (len(kr.chunks) / len(intent_chunks)) if intent_chunks else 0.0

    confidence = kr.chunks[0].score if kr.chunks else 0.0

    hits: list[dict] = []
    for c in kr.chunks:
        title_split = c.text.split("\n", 1)
        title = title_split[0].strip()
        snippet = (title_split[1].strip() if len(title_split) > 1 else c.text)[:400]
        hits.append(
            {
                "doc_id": c.chunk_id,
                "title": title,
                "snippet": snippet,
                "score": c.score,
                "source": c.source_ref,
            }
        )

    available = (not kr.unevaluable) and bool(hits)

    return {
        "available": available,
        "hits": hits,
        "coverage": coverage,
        "confidence": confidence,
        "match_reason": kr.match_reason,
        "consultation_type": consultation_type,
        "product": product,
        # 3 경로 flag (Dev3 #15 A안 절충)
        "no_hit_but_evaluable": kr.no_hit_but_evaluable,
        "truly_unevaluable": kr.truly_unevaluable,
    }
