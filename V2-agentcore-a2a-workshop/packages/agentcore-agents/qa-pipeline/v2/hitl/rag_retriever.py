# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""HITL RAG Retriever — 판사(judge) LLM 이 ``과거 휴먼 검증 사례`` 를 주입하기 위한
Titan v2 KNN + BM25 fallback 검색기.

데이터 흐름:
    judge_agent — 항목별 segment 판정 시
      → rag_retriever.retrieve_human_cases(item_number=N, query_text=...)
        → AOSS 인덱스 ``qa-hitl-cases`` (rag_ingester 가 색인)
          → list[dict] (top_k=3 기본)
            → format_human_cases_for_prompt(cases) → judge user message 에 주입

핵심 동작:
1. 인덱스가 없거나 opensearch-py 미설치면 **silent 빈 리스트** 반환 — 인덱서가 아직
   안 돌았거나 첫 사용자 시나리오에서 판사가 깨지지 않도록.
2. Titan v2 로 query 임베딩 → KNN 검색 (``item_number`` 사전 필터, 옵션 tenant_id).
3. Titan 실패(embed=None) 시 BM25 fallback — ``transcript_excerpt`` / ``body`` /
   ``human_note`` / ``ai_judgment`` 멀티 필드 검색.
4. 결과 hit dict: ``ai_score`` / ``human_score`` / ``ai_judgment`` / ``human_note`` /
   ``transcript_excerpt`` / ``_knn_score`` / ``consultation_id`` / ``confirmed_at`` / ``delta``.

INDEX_NAME 은 :mod:`v2.hitl.rag_ingester` 에서 import — 단일 출처 원칙.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..rag import aoss_store as _aoss
from ..rag.embedding import embed
from .rag_ingester import INDEX_NAME

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AOSS client — silent failure 정책 (인덱스 부재/권한 문제는 빈 리스트 반환)
# ---------------------------------------------------------------------------


def _client_or_none():
    """opensearch-py / endpoint 둘 다 있어야 client. 하나라도 빠지면 None."""
    try:
        import opensearchpy  # noqa: F401
    except ImportError:
        return None
    endpoint = _aoss._resolve_endpoint()
    if not endpoint:
        return None
    try:
        return _aoss._make_client(endpoint)
    except Exception as exc:
        logger.warning("hitl_rag_retriever: client 생성 실패 — %s", exc)
        return None


def _index_exists(client) -> bool:
    try:
        return bool(client.indices.exists(index=INDEX_NAME))
    except Exception as exc:
        logger.warning("hitl_rag_retriever: indices.exists 실패 — %s", exc)
        return False


# ---------------------------------------------------------------------------
# 결과 포매팅 — judge 가 주입할 형태로 정규화
# ---------------------------------------------------------------------------


_HIT_FIELDS = (
    "ai_score",
    "human_score",
    "ai_judgment",
    "human_note",
    "transcript_excerpt",
    "consultation_id",
    "confirmed_at",
    "delta",
)


def _normalize_hit(src: dict[str, Any], *, knn_score: float | None) -> dict[str, Any]:
    out: dict[str, Any] = {k: src.get(k) for k in _HIT_FIELDS}
    out["_knn_score"] = knn_score
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def retrieve_human_cases(
    item_number: int,
    query_text: str,
    *,
    top_k: int = 3,
    tenant_id: Optional[str] = None,
) -> list[dict]:
    """HITL 검수 사례 KNN 검색 (BM25 fallback).

    Parameters
    ----------
    item_number : int
        평가 항목 번호 (1..18). 사전 필터 — 다른 항목 사례 매칭 차단.
    query_text : str
        판사가 매칭하려는 segment_text (또는 발화 발췌). 빈 문자열이면 빈 리스트.
    top_k : int
        반환할 최대 사례 수. 기본 3.
    tenant_id : str | None
        지정 시 ``tenant_id`` 필드로 추가 필터. 미지정 시 모든 tenant 검색.

    Returns
    -------
    list[dict]
        hit dict 리스트. 인덱스 부재 / 클라이언트 미가용 / 검색 실패 시 빈 리스트.
        각 hit: ``ai_score`` / ``human_score`` / ``ai_judgment`` / ``human_note`` /
        ``transcript_excerpt`` / ``_knn_score`` / ``consultation_id`` /
        ``confirmed_at`` / ``delta``.
    """
    if not query_text or not str(query_text).strip():
        return []
    if top_k <= 0:
        return []

    client = _client_or_none()
    if client is None:
        return []
    if not _index_exists(client):
        return []

    filters: list[dict[str, Any]] = [{"term": {"item_number": int(item_number)}}]
    if tenant_id:
        filters.append({"term": {"tenant_id": tenant_id}})

    # 1차: KNN — Titan 임베딩 가능할 때만
    vec = embed(query_text)
    if vec is not None:
        try:
            knn_clause: dict[str, Any] = {
                "vector": list(vec),
                "k": max(top_k * 3, 10),
                "filter": {"bool": {"filter": filters}},
            }
            body = {
                "size": top_k,
                "query": {"knn": {"embedding": knn_clause}},
            }
            resp = client.search(index=INDEX_NAME, body=body)
            hits = resp.get("hits", {}).get("hits", [])
            return [
                _normalize_hit(dict(h.get("_source") or {}), knn_score=h.get("_score"))
                for h in hits
            ]
        except Exception as exc:
            logger.warning(
                "hitl_rag_retriever: KNN 실패 — BM25 fallback 시도 (%s)", exc
            )

    # 2차: BM25 fallback — multi_match 로 transcript / body / human_note / ai_judgment
    try:
        body = {
            "size": top_k,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query_text,
                                "fields": [
                                    "transcript_excerpt^2",
                                    "body^1",
                                    "human_note^1",
                                    "ai_judgment^1",
                                ],
                                "type": "best_fields",
                                "operator": "or",
                            }
                        }
                    ],
                    "filter": filters,
                }
            },
        }
        resp = client.search(index=INDEX_NAME, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        return [
            _normalize_hit(dict(h.get("_source") or {}), knn_score=h.get("_score"))
            for h in hits
        ]
    except Exception as exc:
        logger.warning("hitl_rag_retriever: BM25 fallback 도 실패 — %s", exc)
        return []


# ---------------------------------------------------------------------------
# 프롬프트 주입용 포매터
# ---------------------------------------------------------------------------


def _truncate(text: Any, n: int) -> str:
    s = str(text or "").strip()
    if len(s) <= n:
        return s
    return s[:n].rstrip() + "…"


def format_human_cases_for_prompt(cases: list[dict]) -> str:
    """judge user message 에 주입할 사람-친화적 텍스트 블록.

    형식 (각 사례)::

        사례 N (cos X.XX): [원문] {transcript_excerpt[:300]}
              [AI {ai_score}점] {ai_judgment[:200]}
              [휴먼 {human_score}점] {human_note[:200]}

    cos 값은 ``_knn_score`` 가 None 이 아닐 때만 표기 (BM25 fallback / 미가용 시 생략).
    빈 리스트면 빈 문자열 반환 — judge 측에서 ``if block: prompt += block`` 가능.
    """
    if not cases:
        return ""
    lines: list[str] = []
    for i, c in enumerate(cases, start=1):
        excerpt = _truncate(c.get("transcript_excerpt"), 300)
        ai_score = c.get("ai_score")
        human_score = c.get("human_score")
        ai_judgment = _truncate(c.get("ai_judgment"), 200)
        human_note = _truncate(c.get("human_note"), 200)
        knn = c.get("_knn_score")
        try:
            cos_label = f" (cos {float(knn):.2f})" if knn is not None else ""
        except (TypeError, ValueError):
            cos_label = ""
        lines.append(f"사례 {i}{cos_label}: [원문] {excerpt}")
        lines.append(f"      [AI {ai_score}점] {ai_judgment}")
        lines.append(f"      [휴먼 {human_score}점] {human_note}")
    return "\n".join(lines)


__all__ = [
    "retrieve_human_cases",
    "format_human_cases_for_prompt",
]
