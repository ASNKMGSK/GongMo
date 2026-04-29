# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
AOSS (OpenSearch Serverless) 벡터 스토어 — opensearch-py 기반.

- opensearch-py AWSV4SignerAuth 로 SigV4 처리 (body 서명 버그 없음).
- 인덱스 스키마: knn_vector (1024-dim, cosinesimil, FAISS HNSW).
- Collection: `a2a-rag-documents` (기존 배포 재사용)
- QA 전용 인덱스 2개:
    * `qa-golden-set`       — Few-shot 예시
    * `qa-reasoning-index`  — 판정 근거 (stdev 산출)
- Multi-tenant: `tenant_id` 키워드 필드로 필터.

환경변수:
    QA_AOSS_ENDPOINT : AOSS 엔드포인트 URL (없으면 SSM `/a2a_rag/opensearch_endpoint`)
    AWS_REGION       : 리전 (기본 us-east-1)

의존성: pip install opensearch-py
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

GOLDEN_INDEX = "qa-golden-set"
REASONING_INDEX = "qa-reasoning-index"
KNOWLEDGE_INDEX = "qa-business-knowledge"
_EMBED_DIM = 1024


def _build_tenant_filters(
    tenant_id: Optional[str] = None,
    channel: Optional[str] = None,
    department: Optional[str] = None,
    item_number: Optional[int] = None,
    extra_filters: Optional[list[dict]] = None,
) -> list[dict]:
    """3단계 멀티테넌트 AOSS filter 빌더 (2026-04-24).

    None 인 축은 생략 → 인덱스에 해당 필드가 없는 레거시 문서도 매칭 가능.

    channel/department 의 경우 (2026-04-28 수정):
    인덱스 doc 에 channel/department 필드 자체가 없는 site-level 자원 (예: 신한
    `tenants/shinhan/golden_set/`) 도 fallback 매칭되도록 `should: [exact OR missing]`
    형태로 변환. 빌드 스크립트가 channel/department 를 doc 에 박지 않는 현 시점의
    호환층. 향후 빌드 스크립트가 모든 doc 에 channel/department 를 명시하면 단순
    term filter 로 회귀해도 된다.
    """
    out: list[dict] = []
    if tenant_id:
        out.append({"term": {"tenant_id": tenant_id}})

    def _exact_or_missing(field: str, value: str) -> dict:
        """exact term 매칭 OR 해당 필드 missing — 둘 중 하나면 통과."""
        return {
            "bool": {
                "should": [
                    {"term": {field: value}},
                    {"bool": {"must_not": [{"exists": {"field": field}}]}},
                ],
                "minimum_should_match": 1,
            }
        }

    if channel:
        out.append(_exact_or_missing("channel", channel))
    if department:
        out.append(_exact_or_missing("department", department))
    if item_number is not None:
        out.append({"term": {"item_number": item_number}})
    if extra_filters:
        out.extend(extra_filters)
    return out


def _resolve_endpoint() -> Optional[str]:
    env_ep = os.environ.get("QA_AOSS_ENDPOINT")
    if env_ep:
        return env_ep.rstrip("/")
    try:
        import boto3  # type: ignore
        ssm = boto3.client("ssm", region_name=_region())
        resp = ssm.get_parameter(Name="/a2a_rag/opensearch_endpoint")
        return resp["Parameter"]["Value"].rstrip("/")
    except Exception as e:  # noqa: BLE001
        logger.warning("AOSS endpoint 미확인 (env + SSM 둘 다 실패): %s", e)
        return None


def _region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def _make_client(endpoint: str):
    """opensearch-py OpenSearch 클라이언트 (AWSV4SignerAuth)."""
    try:
        import boto3  # type: ignore
        from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth  # type: ignore
    except ImportError as e:
        raise RuntimeError("opensearch-py 미설치 — pip install opensearch-py") from e

    parsed = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}")
    host = parsed.hostname
    port = parsed.port or 443

    creds = boto3.Session().get_credentials()
    if creds is None:
        raise RuntimeError("AWS 자격증명 없음")
    auth = AWSV4SignerAuth(creds, _region(), "aoss")

    return OpenSearch(
        hosts=[{"host": host, "port": port}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        pool_maxsize=50,
        timeout=30,
    )


# 프로세스 전역 클라이언트 캐시 — 반복 호출 시 boto3 세션·OpenSearch 연결 재사용.
_CLIENT_CACHE: dict[str, Any] = {}
_STORE_CACHE: dict[tuple[str, str], "AossStore"] = {}


def get_store(index_name: str, endpoint: Optional[str] = None) -> "AossStore":
    """`AossStore` 싱글톤 — 동일 (index, endpoint) 는 인스턴스 재사용."""
    ep = endpoint or _resolve_endpoint() or ""
    key = (index_name, ep)
    if key not in _STORE_CACHE:
        _STORE_CACHE[key] = AossStore(index_name, endpoint=ep)
    return _STORE_CACHE[key]


class AossStore:
    """AOSS KNN 벡터 인덱스 클라이언트."""

    def __init__(self, index_name: str, endpoint: Optional[str] = None):
        self.index_name = index_name
        self.endpoint = endpoint or _resolve_endpoint()
        if not self.endpoint:
            raise RuntimeError("AOSS endpoint 확보 실패 — QA_AOSS_ENDPOINT 또는 SSM 설정 필요")

    @property
    def client(self):
        """엔드포인트 단위 싱글톤 — 프로세스 재시작 전까지 유지."""
        if self.endpoint not in _CLIENT_CACHE:
            _CLIENT_CACHE[self.endpoint] = _make_client(self.endpoint)
        return _CLIENT_CACHE[self.endpoint]

    # ---- Index lifecycle ---------------------------------------------------

    def index_exists(self) -> bool:
        """인덱스 존재 여부.

        주의: 권한 오류 (401/403) 는 "미존재" 로 둔갑시키지 않고 즉시 raise.
        과거 모든 예외를 swallow 해서 권한 박탈 → "0건" 으로 잘못 표기된 사례 있음.
        """
        try:
            from opensearchpy.exceptions import (  # noqa: WPS433
                AuthenticationException, AuthorizationException,
            )
        except Exception:  # pragma: no cover
            # opensearch-py 버전에 따라 AuthenticationException 미존재 가능 — Python 3.13 의
            # strict 동작 ("catching classes that do not inherit from BaseException is not
            # allowed") 회피 위해 빈 튜플 () 대신 절대 매칭 안 되는 dummy Exception 사용.
            class _NoMatch(Exception):
                pass
            AuthenticationException = AuthorizationException = _NoMatch  # type: ignore[assignment]
        try:
            return bool(self.client.indices.exists(index=self.index_name))
        except (AuthenticationException, AuthorizationException) as e:
            logger.error(
                "AOSS 권한 거부 (%s) — index=%s. data access policy 의 Principals 확인 필요.",
                type(e).__name__, self.index_name,
            )
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("index_exists 실패 (%s): %s", self.index_name, e)
            return False

    def create_index(self) -> None:
        body = {
            "settings": {"index": {"knn": True}},
            "mappings": {
                "properties": {
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": _EMBED_DIM,
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "faiss",
                            "parameters": {"ef_construction": 512, "m": 16},
                        },
                    },
                    "tenant_id": {"type": "keyword"},
                    "item_number": {"type": "integer"},
                    "score": {"type": "integer"},
                    "intent": {"type": "keyword"},
                    "score_bucket": {"type": "keyword"},
                    "example_id": {"type": "keyword"},
                    "record_id": {"type": "keyword"},
                    "rationale_tags": {"type": "keyword"},
                    "segment_text": {"type": "text"},
                    "rationale": {"type": "text"},
                    "quote_example": {"type": "text"},
                    "rater_type": {"type": "keyword"},
                    "rater_source": {"type": "keyword"},
                    "evaluator_id": {"type": "keyword"},
                    # Business Knowledge 전용 필드
                    "chunk_id": {"type": "keyword"},
                    "title": {"type": "text"},
                    "text": {"type": "text"},
                    "intents": {"type": "keyword"},
                    "tags": {"type": "keyword"},
                    "source_ref": {"type": "keyword"},
                }
            },
        }
        self.client.indices.create(index=self.index_name, body=body)
        logger.info("AOSS 인덱스 생성 완료: %s", self.index_name)

    def delete_index(self) -> None:
        try:
            self.client.indices.delete(index=self.index_name)
            logger.info("AOSS 인덱스 삭제: %s", self.index_name)
        except Exception as e:  # noqa: BLE001
            logger.info("삭제 실패 (존재하지 않음?): %s", e)

    def ensure_index(self) -> None:
        if not self.index_exists():
            self.create_index()

    def count_docs(
        self,
        *,
        tenant_id: Optional[str] = None,
        channel: Optional[str] = None,
        department: Optional[str] = None,
    ) -> int:
        """문서 수 카운트 (tenant_id / channel / department 필터 가능)."""
        filters = _build_tenant_filters(tenant_id, channel, department)
        body = {"query": {"bool": {"filter": filters}}} if filters else None
        try:
            resp = self.client.count(index=self.index_name, body=body)
            return int(resp.get("count", 0))
        except Exception as e:  # noqa: BLE001
            logger.warning("count 실패 %s: %s", self.index_name, e)
            return 0

    # ---- Document I/O ------------------------------------------------------

    def upsert(self, doc_id: str, doc: dict) -> None:
        """단일 문서 색인 — AOSS Serverless 제약으로 auto-ID 사용.

        ⚠ AOSS Serverless 는 `index` op 에서 custom ID 거부 ("Document ID is not
        supported in create/index operation request"). 따라서:
        - external_id 는 doc 필드로 저장 (검색용)
        - 중복 누적 방지는 호출자가 책임 — recreate 후 재빌드 필요.
        """
        body = dict(doc)
        body.setdefault("external_id", doc_id)
        self.client.index(index=self.index_name, body=body)

    def delete_by_tenant(self, tenant_id: str) -> int:
        """tenant 단위 모든 doc 삭제 — _delete_by_query 사용.

        주의: AOSS Serverless 에서 _delete_by_query 가 지원되지 않으면 Exception.
        실패 시 호출자가 --recreate 로 폴백 권장.
        """
        body = {"query": {"term": {"tenant_id": tenant_id}}}
        try:
            resp = self.client.delete_by_query(
                index=self.index_name, body=body,
                refresh=True, conflicts="proceed",
            )
            return int(resp.get("deleted", 0))
        except Exception as e:
            logger.warning("delete_by_tenant 실패 (AOSS Serverless 미지원 가능): %s", e)
            raise

    def count_by_tenant(self, tenant_id: str) -> int:
        """tenant 단위 현재 doc 개수 — delete_by_query 후 eventual consistency 폴링용."""
        body = {"query": {"term": {"tenant_id": tenant_id}}}
        try:
            resp = self.client.count(index=self.index_name, body=body)
            return int(resp.get("count", 0))
        except Exception as e:  # noqa: BLE001
            logger.warning("count_by_tenant 실패: %s", e)
            return -1

    def wait_until_tenant_empty(self, tenant_id: str, timeout_sec: float = 60.0, poll_interval: float = 2.0) -> bool:
        """delete_by_tenant 호출 후 실제 count 가 0 이 될 때까지 폴링.

        AOSS Serverless 는 _delete_by_query 가 eventual consistency 라 즉시 반영 안 됨.
        반환 True 면 확인됨, False 면 timeout (호출자가 경고 후 계속 진행할지 판단).
        """
        import time as _time
        start = _time.monotonic()
        while _time.monotonic() - start < timeout_sec:
            n = self.count_by_tenant(tenant_id)
            if n == 0:
                return True
            if n < 0:
                return False  # count 실패 — 기다려도 소용없음
            logger.info("  [wait_until_empty] tenant=%s index=%s count=%d (대기 중)",
                        tenant_id, self.index_name, n)
            _time.sleep(poll_interval)
        return False

    def existing_external_ids(self, tenant_id: str, external_ids: list[str]) -> set[str]:
        """주어진 external_id 후보 중 이미 색인된 놈 반환 — 중복 방지용.

        bootstrap 에서 bulk insert 전에 호출해서, 이미 존재하는 external_id 는
        actions 에서 제거하면 중복 누적 방지.
        """
        if not external_ids:
            return set()
        found: set[str] = set()
        try:
            # terms 쿼리로 해당 tenant 의 external_id 매칭만 검색
            body = {
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"tenant_id": tenant_id}},
                            {"terms": {"external_id": external_ids}},
                        ]
                    }
                },
                "_source": ["external_id"],
                "size": len(external_ids),
            }
            resp = self.client.search(index=self.index_name, body=body)
            for hit in resp.get("hits", {}).get("hits", []):
                eid = hit.get("_source", {}).get("external_id")
                if eid:
                    found.add(str(eid))
        except Exception as e:  # noqa: BLE001
            logger.warning("existing_external_ids 조회 실패 (일단 전체 재색인 진행): %s", e)
        return found

    def bulk_upsert(
        self,
        docs: list[tuple[str, dict]],
        *,
        skip_existing: bool = False,
        tenant_id: Optional[str] = None,
    ) -> int:
        """배치 색인 — 레거시 시그니처 (success 만 반환). 내부적으로 bulk_upsert_with_stats 호출.

        신규 호출처는 `bulk_upsert_with_stats` 사용 권장 — (success, skipped) 반환.
        """
        success, _skipped = self.bulk_upsert_with_stats(
            docs, skip_existing=skip_existing, tenant_id=tenant_id
        )
        return success

    def bulk_upsert_with_stats(
        self,
        docs: list[tuple[str, dict]],
        *,
        skip_existing: bool = False,
        tenant_id: Optional[str] = None,
    ) -> tuple[int, int]:
        """배치 색인 — (success, skipped) 반환.

        중복 방지 정책 (2026-04-24 갱신):
          - external_id 는 호출자가 파일 내용 해시를 포함해 구성 권장 (예: "{tenant}:{id}:{hash8}")
          - external_id 완전 일치 = 같은 내용 = 스킵 (중복 색인 방지)
          - external_id 변경 = 내용 변경 = 신규 색인 (구 doc 은 남아 있음 — 주기적 정리 필요)

        반환:
          (success_indexed, skipped_existing) — skipped 는 실패가 아닌 "이미 색인됨".
        """
        try:
            from opensearchpy.helpers import bulk  # type: ignore
        except ImportError:
            # 폴백: 순차 upsert — opensearchpy bulk helper 없는 환경
            n = 0
            for did, body in docs:
                self.upsert(did, body)
                n += 1
            return n, 0

        skipped = 0
        # 중복 방지 — 이미 색인된 external_id 필터링
        if skip_existing and tenant_id:
            candidate_ids = [did for did, _b in docs]
            already = self.existing_external_ids(tenant_id, candidate_ids)
            if already:
                before = len(docs)
                docs = [(did, b) for (did, b) in docs if did not in already]
                skipped = before - len(docs)
                if skipped:
                    logger.info(
                        "  [bulk_upsert] tenant=%s index=%s skip_existing=%d (이미 색인됨)",
                        tenant_id, self.index_name, skipped,
                    )

        if not docs:
            return 0, skipped

        actions = []
        for did, body in docs:
            b = dict(body)
            b.setdefault("external_id", did)
            # AOSS Serverless 는 custom _id 거부 → auto-ID 사용
            actions.append({"_op_type": "index", "_index": self.index_name, "_source": b})
        success, errors = bulk(self.client, actions, raise_on_error=False, stats_only=False)
        if errors:
            logger.warning("bulk 일부 실패 %d 건: %s", len(errors), str(errors)[:300])
        return success, skipped

    def search_bm25(
        self,
        query_text: str,
        top_k: int = 5,
        *,
        tenant_id: Optional[str] = None,
        channel: Optional[str] = None,
        department: Optional[str] = None,
        item_number: Optional[int] = None,
        text_fields: Optional[list[str]] = None,
        extra_filters: Optional[list[dict]] = None,
    ) -> list[dict]:
        """BM25F 스타일 텍스트 검색 — `multi_match` 로 여러 text 필드 가중 검색.

        기본 필드 가중 (golden_set 기준):
          - segment_text^2  (원문 발화가 가장 중요)
          - rationale^1     (판정 사유)
          - quote_example^1 (reasoning_index 용)
          - text^1          (business_knowledge 용)

        3단계 멀티테넌트 (2026-04-24): channel/department 필터 추가.
        """
        filters = _build_tenant_filters(
            tenant_id, channel, department, item_number, extra_filters
        )

        fields = text_fields or ["segment_text^2", "rationale^1", "quote_example^1", "text^1"]
        must = [
            {"multi_match": {
                "query": query_text,
                "fields": fields,
                "type": "best_fields",
                "operator": "or",
            }}
        ]
        body: dict[str, Any] = {
            "size": top_k,
            "query": {"bool": {"must": must, "filter": filters}} if filters else {"bool": {"must": must}},
        }
        try:
            resp = self.client.search(index=self.index_name, body=body)
        except Exception as e:  # noqa: BLE001
            logger.warning("BM25 검색 실패 %s: %s", self.index_name, e)
            return []
        hits = resp.get("hits", {}).get("hits", [])
        out = []
        for h in hits:
            src = dict(h.get("_source") or {})
            src["_score"] = h.get("_score")
            out.append(src)
        return out

    def search_hybrid(
        self,
        query_text: str,
        query_vector: list[float],
        top_k: int = 5,
        *,
        tenant_id: Optional[str] = None,
        channel: Optional[str] = None,
        department: Optional[str] = None,
        item_number: Optional[int] = None,
        text_fields: Optional[list[str]] = None,
        extra_filters: Optional[list[dict]] = None,
        rrf_k: int = 60,
        knn_top_k_multiplier: int = 3,
    ) -> list[dict]:
        """BM25 + KNN 하이브리드 검색 — RRF (Reciprocal Rank Fusion) 로 융합.

        1) BM25 top-K*3 + KNN top-K*3 각각 가져옴
        2) 각 문서에 대해 RRF score = 1/(rrf_k + rank_bm25) + 1/(rrf_k + rank_knn)
        3) RRF score 내림차순 top-K 반환

        rrf_k=60 은 BM25/KNN fusion 표준값 (Cormack et al. 2009).
        """
        over_fetch = max(top_k * knn_top_k_multiplier, top_k + 5)
        bm25_hits = self.search_bm25(
            query_text, top_k=over_fetch,
            tenant_id=tenant_id, channel=channel, department=department,
            item_number=item_number,
            text_fields=text_fields, extra_filters=extra_filters,
        )
        knn_hits = self.search_knn(
            query_vector, top_k=over_fetch,
            tenant_id=tenant_id, channel=channel, department=department,
            item_number=item_number,
            extra_filters=extra_filters,
        )

        # 문서 ID 로 합치기 — external_id 우선, 없으면 (example_id | record_id | chunk_id)
        def _key(h: dict) -> str:
            for k in ("external_id", "example_id", "record_id", "chunk_id"):
                v = h.get(k)
                if v:
                    return str(v)
            # fallback: 내용 해시 대신 _score + 첫 필드값 (거의 안 쓰임)
            return f"_no_id_{h.get('segment_text','')[:50]}"

        rrf_scores: dict[str, float] = {}
        bm25_raw: dict[str, float] = {}
        knn_raw: dict[str, float] = {}
        bm25_rank: dict[str, int] = {}
        knn_rank: dict[str, int] = {}
        merged: dict[str, dict] = {}
        for rank, h in enumerate(bm25_hits):
            k = _key(h)
            rrf_scores[k] = rrf_scores.get(k, 0.0) + 1.0 / (rrf_k + rank + 1)
            bm25_raw[k] = float(h.get("_score") or 0.0)
            bm25_rank[k] = rank
            merged.setdefault(k, h)
        for rank, h in enumerate(knn_hits):
            k = _key(h)
            rrf_scores[k] = rrf_scores.get(k, 0.0) + 1.0 / (rrf_k + rank + 1)
            knn_raw[k] = float(h.get("_score") or 0.0)
            knn_rank[k] = rank
            merged.setdefault(k, h)

        # BM25 정규화 (0~100%) — 쿼리 내 top-1 대비 상대 점수.
        # BM25 는 수학적 상한이 없으므로 "이 쿼리에서 가장 잘 매칭된 문서 대비 몇 %" 로 표현.
        # 업계 관례: IR 평가에서 relative score normalization (Min-max within-query).
        bm25_top = max(bm25_raw.values()) if bm25_raw else 0.0
        bm25_norm: dict[str, float] = {}
        if bm25_top > 0:
            for k, v in bm25_raw.items():
                bm25_norm[k] = max(0.0, min(100.0, (v / bm25_top) * 100.0))

        ranked_keys = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        out: list[dict] = []
        for k in ranked_keys[:top_k]:
            h = dict(merged[k])
            # 기존 호환: _score = RRF
            h["_score"] = rrf_scores[k]
            # 추가: BM25/KNN 원본 점수 + rank 보존 (UI/디버깅용)
            h["_rrf_score"] = rrf_scores[k]
            h["_bm25_score"] = bm25_raw.get(k)
            h["_bm25_pct"] = bm25_norm.get(k)  # 쿼리 내 top-1 대비 % (0~100)
            h["_knn_score"] = knn_raw.get(k)  # OpenSearch FAISS cosine = (1+cos_sim)/2 범위 [0,1]
            h["_bm25_rank"] = bm25_rank.get(k)
            h["_knn_rank"] = knn_rank.get(k)
            out.append(h)
        return out

    def search_knn(
        self,
        query_vector: list[float],
        top_k: int = 5,
        *,
        tenant_id: Optional[str] = None,
        channel: Optional[str] = None,
        department: Optional[str] = None,
        item_number: Optional[int] = None,
        extra_filters: Optional[list[dict]] = None,
    ) -> list[dict]:
        """KNN 검색 + tenant(3단계)/item 사전 필터.

        **pre-filter 사용** (OpenSearch 2.x FAISS HNSW `efficient_filter`) — post-filter 는
        KNN 이 전역 top-K 먼저 반환 후 필터 걸면 타 tenant 벡터만 남는 empty 폴백 유발.

        3단계 멀티테넌트 (2026-04-24): channel/department 필터 추가.
        """
        filters = _build_tenant_filters(
            tenant_id, channel, department, item_number, extra_filters
        )

        # KNN inner filter (pre-filter) — AOSS FAISS HNSW 에서 지원
        knn_clause: dict[str, Any] = {
            "vector": query_vector,
            "k": max(top_k * 3, 10),  # 후보 over-fetch (필터 후 top_k 확보)
        }
        if filters:
            knn_clause["filter"] = {"bool": {"filter": filters}}

        body = {
            "size": top_k,
            "query": {"knn": {"embedding": knn_clause}},
        }
        resp = self.client.search(index=self.index_name, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        out = []
        for h in hits:
            src = dict(h.get("_source") or {})
            src["_score"] = h.get("_score")
            out.append(src)
        return out
