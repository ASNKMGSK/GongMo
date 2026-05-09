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
import threading
from typing import Any, Optional

from ..rag import aoss_store as _aoss
from ..rag.embedding import embed
from .rag_ingester import INDEX_NAME

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AOSS client — silent failure 정책 (인덱스 부재/권한 문제는 빈 리스트 반환)
#
# ★ 2026-04-30 S6 fix: 모듈 전역 캐시 추가.
#   이전엔 retrieve_human_cases 호출마다 _make_client → boto3 sigV4 + IMDS round-trip
#   + 새 OpenSearch HTTP 풀(pool_maxsize=50) 생성. 한 평가당 16 항목 × 2 (judge + judge_agent)
#   = 30+ 신규 client/run → fd 누수 + STS 호출 폭증. endpoint 단위 싱글톤으로 통일.
# ---------------------------------------------------------------------------


_HITL_CLIENT_CACHE: dict[str, Any] = {}
_HITL_CLIENT_LOCK = threading.Lock()


def _client_or_none():
    """opensearch-py / endpoint 둘 다 있어야 client. 하나라도 빠지면 None.

    endpoint 단위로 캐싱 — 한 번 생성한 client 는 재사용 (boto3 STS 호출 / fd 절감).
    """
    try:
        import opensearchpy  # noqa: F401
    except ImportError:
        return None
    endpoint = _aoss._resolve_endpoint()
    if not endpoint:
        return None

    cached = _HITL_CLIENT_CACHE.get(endpoint)
    if cached is not None:
        return cached

    with _HITL_CLIENT_LOCK:
        cached = _HITL_CLIENT_CACHE.get(endpoint)
        if cached is not None:
            return cached
        try:
            client = _aoss._make_client(endpoint)
        except Exception as exc:
            logger.warning("hitl_rag_retriever: client 생성 실패 — %s", exc)
            return None
        _HITL_CLIENT_CACHE[endpoint] = client
        return client


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
    consultation_id: Optional[str] = None,
) -> list[dict]:
    """HITL 검수 사례 KNN 검색 (BM25 fallback).

    Parameters
    ----------
    item_number : int
        평가 항목 번호 (1..18). 사전 필터 — 다른 항목 사례 매칭 차단.
    query_text : str
        판사가 매칭하려는 segment_text (또는 발화 발췌). 빈 문자열이면 빈 리스트.
    top_k : int
        반환할 최대 사례 수. 기본 3. (자기상담 매칭은 별도 추가, top_k 카운트 제외.)
    tenant_id : str | None
        지정 시 ``tenant_id`` 필드로 추가 필터. 미지정 시 모든 tenant 검색.
    consultation_id : str | None
        ★ 2026-04-30 fix: 현재 평가 중인 상담 ID. 지정 시 그 cid 의 (item_number) HITL doc 을
        KNN/BM25 와 무관하게 강제 1차 매칭하여 결과 맨 앞에 prepend (``is_self_match=True`` 마킹).
        이렇게 안 하면 자기상담 사례의 cos 유사도가 낮아 top_k 탈락 시 anchor 룰 발동 불가.

    Returns
    -------
    list[dict]
        hit dict 리스트. 인덱스 부재 / 클라이언트 미가용 / 검색 실패 시 빈 리스트.
        각 hit: ``ai_score`` / ``human_score`` / ``ai_judgment`` / ``human_note`` /
        ``transcript_excerpt`` / ``_knn_score`` / ``consultation_id`` /
        ``confirmed_at`` / ``delta``. 자기상담 매칭 시 ``is_self_match=True`` 추가.
    """
    if top_k <= 0:
        return []

    # 전역 RAG 비활성 토글 — HITL 골든셋도 RAG 의 일종이므로 동일하게 우회.
    # 페르소나 토론은 hitl_cases=[] 로 진행 → anchor 룰 비활성, 페르소나 자율 판단.
    try:
        from v2.rag import is_rag_disabled

        if is_rag_disabled():
            return []
    except ImportError:
        pass  # 방어적 — v2.rag 가 없는 단위테스트 환경 호환.

    client = _client_or_none()
    if client is None:
        return []
    if not _index_exists(client):
        return []

    filters: list[dict[str, Any]] = [{"term": {"item_number": int(item_number)}}]
    if tenant_id:
        filters.append({"term": {"tenant_id": tenant_id}})

    # ★ 자기상담 강제 매칭 — KNN 유사도와 무관하게 consultation_id term query 로 직접 fetch.
    # 결과는 results 맨 앞에 prepend, is_self_match 마킹. 호출 측 마킹은 그대로 유지 (방어적).
    self_hits: list[dict] = []
    cid_norm = str(consultation_id or "").strip()
    if cid_norm:
        try:
            self_filters = list(filters) + [{"term": {"consultation_id": cid_norm}}]
            self_body = {
                "size": 5,  # 같은 (cid, item) 의 signature 다른 잔여 doc 까지 cover
                "query": {"bool": {"filter": self_filters}},
                "sort": [{"confirmed_at": {"order": "desc"}}],
            }
            resp = client.search(index=INDEX_NAME, body=self_body)
            for h in resp.get("hits", {}).get("hits", []):
                norm = _normalize_hit(dict(h.get("_source") or {}), knn_score=None)
                norm["is_self_match"] = True
                self_hits.append(norm)
        except Exception as exc:
            logger.warning(
                "hitl_rag_retriever: 자기상담 매칭 실패 cid=%s — KNN 만 진행 (%s)",
                cid_norm, exc,
            )

    # query_text 가 비어있어도 자기상담 매칭만 있으면 반환 (anchor 룰 단독 발동 가능).
    if not query_text or not str(query_text).strip():
        return self_hits

    # ★ 2026-05-07: 하이브리드 (BM25 + KNN, RRF 병합) — 다른 RAG 인덱스와 정합 통일.
    # 기존엔 KNN 우선 → 실패 시에만 BM25 fallback (직렬). 이제 항상 둘 다 실행 후 RRF 병합.
    # 자기상담(self_hits) 은 anchor 룰이라 RRF 뒤에 그대로 prepend (KNN/BM25 무관 강제 매칭).
    # 사용자 지시 (2026-05-08): fetch_k/over_fetch 모두 10 으로 고정.
    over_fetch = 10
    knn_hits: list[dict] = []
    bm25_hits: list[dict] = []

    # KNN — Titan 임베딩 가능할 때만
    vec = embed(query_text)
    if vec is not None:
        try:
            knn_clause: dict[str, Any] = {
                "vector": list(vec),
                "k": over_fetch,
                "filter": {"bool": {"filter": filters}},
            }
            body = {
                "size": over_fetch,
                "query": {"knn": {"embedding": knn_clause}},
            }
            resp = client.search(index=INDEX_NAME, body=body)
            for h in resp.get("hits", {}).get("hits", []):
                knn_hits.append(
                    _normalize_hit(dict(h.get("_source") or {}), knn_score=h.get("_score"))
                )
        except Exception as exc:
            logger.warning("hitl_rag_retriever: KNN 실패 (BM25 만으로 진행) — %s", exc)

    # BM25 — 항상 실행 (RRF 병합용)
    try:
        body = {
            "size": over_fetch,
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
        for h in resp.get("hits", {}).get("hits", []):
            bm25_hits.append(
                _normalize_hit(dict(h.get("_source") or {}), knn_score=h.get("_score"))
            )
    except Exception as exc:
        logger.warning("hitl_rag_retriever: BM25 실패 — %s", exc)

    # RRF 병합 — Cormack et al. 2009, k=60 표준
    def _hit_key(h: dict) -> str:
        for k in ("external_id", "example_id", "record_id"):
            v = h.get(k)
            if v:
                return str(v)
        # fallback: (consultation_id, item_number)
        cid = str(h.get("consultation_id") or "")
        try:
            num = int(h.get("item_number") or 0)
        except (TypeError, ValueError):
            num = 0
        return f"{cid}:{num}" if cid else f"_no_id:{(h.get('transcript_excerpt') or '')[:50]}"

    RRF_K = 60
    rrf_scores: dict[str, float] = {}
    bm25_rank_map: dict[str, int] = {}
    knn_rank_map: dict[str, int] = {}
    merged_hits: dict[str, dict] = {}
    for rank, h in enumerate(bm25_hits):
        k = _hit_key(h)
        rrf_scores[k] = rrf_scores.get(k, 0.0) + 1.0 / (RRF_K + rank + 1)
        bm25_rank_map[k] = rank
        merged_hits.setdefault(k, h)
    for rank, h in enumerate(knn_hits):
        k = _hit_key(h)
        rrf_scores[k] = rrf_scores.get(k, 0.0) + 1.0 / (RRF_K + rank + 1)
        knn_rank_map[k] = rank
        # KNN hit 가 더 풍부한 _knn_score 를 갖고 있을 수 있으니 우선
        if k not in merged_hits or merged_hits[k].get("_knn_score") is None:
            merged_hits[k] = h

    # RRF 점수 내림차순. reranker 활성 시 over_fetch 후보 전체를 reranker 로 재정렬,
    # 비활성 시 top_k 슬라이싱.
    try:
        from v2.rag import is_reranker_enabled, rerank as _rerank_call
    except ImportError:
        is_reranker_enabled = lambda: False  # noqa: E731
        _rerank_call = None  # type: ignore[assignment]

    ranked_keys = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
    candidate_pool_size = over_fetch if is_reranker_enabled() else top_k
    hybrid_top: list[dict] = []
    for k in ranked_keys[:candidate_pool_size]:
        h = dict(merged_hits[k])
        h["_rrf_score"] = rrf_scores[k]
        h["_bm25_rank"] = bm25_rank_map.get(k)
        h["_knn_rank"] = knn_rank_map.get(k)
        hybrid_top.append(h)

    # Reranker 적용 — RRF 후보 풀에서 top_k 정밀 재정렬.
    if is_reranker_enabled() and _rerank_call is not None and len(hybrid_top) > top_k:
        docs = [
            (h.get("transcript_excerpt") or h.get("body") or h.get("ai_judgment") or "")
            for h in hybrid_top
        ]
        order, ok = _rerank_call(query_text, docs, top_n=top_k)
        # 2026-05-08: rerank() 호출 시점의 provider — UI 가 chip 에 표시.
        try:
            from v2.rag import get_reranker_provider as _get_provider
            _provider_at_call = _get_provider()
        except Exception:  # noqa: BLE001
            _provider_at_call = "cohere"
        if order and ok:
            reranked: list[dict] = []
            for original_idx, score in order:
                if 0 <= original_idx < len(hybrid_top):
                    h = dict(hybrid_top[original_idx])
                    h["_cohere_rerank_score"] = score
                    h["_reranked"] = True
                    h["_rerank_provider"] = _provider_at_call
                    reranked.append(h)
            hybrid_top = reranked
        elif order and not ok:
            # 폴백 — 순서만 적용, reranked 마킹 안 함. UI 가 "🎯 rr 0.00" 안 보이도록.
            hybrid_top = [
                hybrid_top[i] for i, _ in order if 0 <= i < len(hybrid_top)
            ]
        else:
            hybrid_top = hybrid_top[:top_k]
    else:
        hybrid_top = hybrid_top[:top_k]

    def _dedup_merge(primary: list[dict], secondary: list[dict]) -> list[dict]:
        """primary (자기상담 anchor) 가 우선, secondary (RRF 결과) dedup 후 결합."""
        seen: set[tuple[str, int]] = set()
        out: list[dict] = []
        for h in primary + secondary:
            cid = str(h.get("consultation_id") or "")
            try:
                num = int(h.get("item_number") or 0)
            except (TypeError, ValueError):
                num = 0
            key = (cid, num)
            if cid and key in seen:
                continue
            seen.add(key)
            out.append(h)
        return out

    # 결과 0건 + KNN/BM25 둘 다 실패한 경우 자기상담만이라도 반환
    if not hybrid_top and not knn_hits and not bm25_hits:
        return self_hits

    return _dedup_merge(self_hits, hybrid_top)


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

        사례 N (cos X.XX) 🔁 동일 상담 — 자기 자신 정답: [원문] {transcript_excerpt[:300]}
              [AI {ai_score}점] {ai_judgment[:200]}
              [휴먼 {human_score}점] {human_note[:200]}

    cos 값은 ``_knn_score`` 가 None 이 아닐 때만 표기 (BM25 fallback / 미가용 시 생략).
    ★ 2026-04-30: ``is_self_match=True`` 인 사례는 라인에 "🔁 동일 상담 — 자기 자신 정답"
    마커 추가 + 자기상담 사례를 리스트 맨 앞으로 정렬 (debate_rules.md §7 anchor 룰 매칭용).
    빈 리스트면 빈 문자열 반환 — 호출 측에서 ``if block: prompt += block`` 가능.
    """
    if not cases:
        return ""
    # 자기상담 사례 우선 정렬 (anchor 룰 식별 용이성). 나머지는 입력 순서 유지.
    sorted_cases = sorted(cases, key=lambda c: 0 if c.get("is_self_match") else 1)
    lines: list[str] = []
    for i, c in enumerate(sorted_cases, start=1):
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
        self_marker = " 🔁 동일 상담 — 자기 자신 정답" if c.get("is_self_match") else ""
        lines.append(f"사례 {i}{cos_label}{self_marker}: [원문] {excerpt}")
        lines.append(f"      [AI {ai_score}점] {ai_judgment}")
        lines.append(f"      [휴먼 {human_score}점] {human_note}")
    return "\n".join(lines)


__all__ = [
    "retrieve_human_cases",
    "format_human_cases_for_prompt",
]
