# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""HITL RAG Ingester — md_exporter 가 적재한 검수 확정 MD 파일을 OpenSearch Serverless
인덱스 ``qa-hitl-cases`` 로 색인.

데이터 흐름:
    md_exporter.export_review_row(...)
      → ~/Desktop/QA평가결과/HITL_RAG/{cid}_{item:02d}.md
        → rag_ingester.index_pending()  ← 본 모듈
          → AOSS index ``qa-hitl-cases``
            → rag_retriever.retrieve_human_cases() (판사 LLM 주입)

핵심 설계:
- 변경 감지: MD frontmatter 의 ``score_signature`` (= ``ai={ai}|human={human}``) 가
  바뀌었거나 ``indexed_at`` 가 비어있으면 "신규" 로 처리.
- AOSS Serverless 는 custom ``_id`` 거부 → ``external_id`` 필드 (= ``{cid}_{item:02d}_{sig_hash8}``)
  로만 dedup. 기존 doc 은 ``_delete_by_query`` 로 제거 시도하되 실패해도 새 doc 추가
  강행 (구 doc 잔여는 운영자 주기 청소).
- 색인 성공 시 MD frontmatter 의 ``indexed_at`` 갱신 후 다시 저장 → 다음 호출 시 skip.

의존:
- :mod:`v2.rag.embedding` — Titan Embed Text V2 (1024d, L2 normalized)
- :mod:`v2.rag.aoss_store` — ``_make_client`` / ``_resolve_endpoint``
- :mod:`v2.hitl.md_exporter` — ``resolve_rag_root`` / ``parse_md_file`` / 형식 합의

opensearch-py 미설치 환경에서는 ``ensure_index`` / ``index_pending`` / ``index_one`` 호출 시
``NotImplementedError`` 를 명시 raise (silent fallback 없음).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..rag import aoss_store as _aoss
from ..rag.embedding import embed
from . import md_exporter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 상수 — retriever 가 import 해서 사용
# ---------------------------------------------------------------------------

INDEX_NAME = "qa-hitl-cases"
"""HITL 검수 사례 AOSS 인덱스명. retriever 와 공유."""

EMBED_DIM = 1024
"""Titan Embed Text V2 차원."""

DEFAULT_TENANT = "kolon-inbound"
"""env ``QA_HITL_RAG_TENANT`` 미지정 시 기본 tenant."""


def _resolve_tenant() -> str:
    return os.environ.get("QA_HITL_RAG_TENANT") or DEFAULT_TENANT


# ---------------------------------------------------------------------------
# AOSS client 헬퍼 — opensearch-py 미설치 시 명시 NotImplementedError
# ---------------------------------------------------------------------------


def _client():
    """AOSS 클라이언트. opensearch-py 미설치 시 NotImplementedError raise."""
    try:
        import opensearchpy  # noqa: F401
    except ImportError as exc:
        raise NotImplementedError(
            "opensearch-py 미설치 — HITL RAG ingester 사용 불가. "
            "pip install opensearch-py 후 재시도."
        ) from exc
    endpoint = _aoss._resolve_endpoint()
    if not endpoint:
        raise RuntimeError(
            "AOSS endpoint 확보 실패 — QA_AOSS_ENDPOINT 또는 SSM /a2a_rag/opensearch_endpoint 설정 필요"
        )
    return _aoss._make_client(endpoint)


# ---------------------------------------------------------------------------
# Index lifecycle
# ---------------------------------------------------------------------------


def _index_body() -> dict[str, Any]:
    """``qa-hitl-cases`` 인덱스 매핑 — knn_vector + 메타 필드."""
    return {
        "settings": {"index": {"knn": True}},
        "mappings": {
            "properties": {
                "embedding": {
                    "type": "knn_vector",
                    "dimension": EMBED_DIM,
                    "method": {
                        "name": "hnsw",
                        "space_type": "cosinesimil",
                        "engine": "faiss",
                        "parameters": {"ef_construction": 512, "m": 16},
                    },
                },
                "consultation_id": {"type": "keyword"},
                "item_number": {"type": "integer"},
                "human_score": {"type": "float"},
                "ai_score": {"type": "float"},
                "delta": {"type": "float"},
                "external_id": {"type": "keyword"},
                "score_signature": {"type": "keyword"},
                "tenant_id": {"type": "keyword"},
                "site_id": {"type": "keyword"},
                "channel": {"type": "keyword"},
                "department": {"type": "keyword"},
                "reviewer_id": {"type": "keyword"},
                "reviewer_role": {"type": "keyword"},
                "confirmed_at": {"type": "date"},
                "body": {"type": "text"},
                "human_note": {"type": "text"},
                "ai_judgment": {"type": "text"},
                "transcript_excerpt": {"type": "text"},
                "item_name": {"type": "text"},
            }
        },
    }


def ensure_index() -> None:
    """``qa-hitl-cases`` 인덱스 보장 — 없으면 생성, 있으면 no-op."""
    client = _client()
    if client.indices.exists(index=INDEX_NAME):
        return
    client.indices.create(index=INDEX_NAME, body=_index_body())
    logger.info("hitl_rag_ingester: 인덱스 생성 완료 — %s", INDEX_NAME)


# ---------------------------------------------------------------------------
# external_id / signature 헬퍼
# ---------------------------------------------------------------------------


_SAFE_RE = re.compile(r"[^A-Za-z0-9_\-]")


def _sig_hash8(score_signature: str) -> str:
    """score_signature → 8자 hex hash. external_id 후미 dedup 키."""
    return hashlib.sha1(score_signature.encode("utf-8")).hexdigest()[:8]


def _build_external_id(cid: str, item_number: int, score_signature: str) -> str:
    safe_cid = _SAFE_RE.sub("_", str(cid or "unknown")) or "unknown"
    return f"{safe_cid}_{int(item_number):02d}_{_sig_hash8(score_signature)}"


# ---------------------------------------------------------------------------
# MD → 색인 변환
# ---------------------------------------------------------------------------


def _embed_text_for(meta: dict[str, Any], body: str) -> str:
    """임베딩 입력 텍스트. body 가 비면 transcript_excerpt + ai/human 텍스트로 fallback."""
    body = (body or "").strip()
    if body:
        return body
    parts: list[str] = []
    if meta.get("item_name"):
        parts.append(f"항목: {meta['item_name']}")
    if meta.get("ai_score") is not None:
        parts.append(f"AI 점수: {meta['ai_score']}")
    if meta.get("human_score") is not None:
        parts.append(f"휴먼 점수: {meta['human_score']}")
    return "\n".join(parts) or "(빈 사례)"


def _to_doc(meta: dict[str, Any], body: str, embedding: list[float], tenant_id: str) -> dict[str, Any]:
    """frontmatter + body → AOSS doc dict."""
    cid = str(meta.get("consultation_id") or "").strip()
    try:
        item_number = int(meta.get("item_number") or 0)
    except (TypeError, ValueError):
        item_number = 0
    score_signature = str(meta.get("score_signature") or "")
    external_id = _build_external_id(cid, item_number, score_signature)

    # body 본문 안에서 사람이 작성한 코멘트/AI 사유/발화 추출 (검색 시 BM25 fallback 용)
    transcript_excerpt = _extract_section(body, "## 발화 발췌 (AI evidence 파싱)")
    ai_judgment = _extract_section(body, "## AI 판정")
    human_note = _extract_section(body, "## 사람 정답")

    def _f(key: str) -> float | None:
        v = meta.get(key)
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    doc: dict[str, Any] = {
        "embedding": embedding,
        "consultation_id": cid,
        "item_number": item_number,
        "human_score": _f("human_score"),
        "ai_score": _f("ai_score"),
        "delta": _f("delta"),
        "external_id": external_id,
        "score_signature": score_signature,
        "tenant_id": tenant_id,
        "site_id": meta.get("site_id"),
        "channel": meta.get("channel"),
        "department": meta.get("department"),
        "reviewer_id": meta.get("reviewer_id"),
        "reviewer_role": meta.get("reviewer_role"),
        "confirmed_at": meta.get("confirmed_at"),
        "body": body,
        "human_note": human_note,
        "ai_judgment": ai_judgment,
        "transcript_excerpt": transcript_excerpt,
        "item_name": meta.get("item_name"),
    }
    # null 값 제거 — date 필드에 빈 문자열/None 들어가면 색인 거부될 수 있음
    return {k: v for k, v in doc.items() if v not in (None, "")}


_SECTION_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _extract_section(body: str, header: str) -> str:
    """MD body 에서 ``## 헤더`` 다음 다음 ``##`` 직전까지 텍스트 추출."""
    if not body:
        return ""
    pat = _SECTION_RE_CACHE.get(header)
    if pat is None:
        # header 줄 다음의 모든 줄을 - 다음 ## 또는 EOF 까지
        esc = re.escape(header)
        pat = re.compile(rf"{esc}\s*\n(.*?)(?=\n##\s|\Z)", re.DOTALL)
        _SECTION_RE_CACHE[header] = pat
    m = pat.search(body)
    if not m:
        return ""
    return m.group(1).strip()


# ---------------------------------------------------------------------------
# 변경 감지 — frontmatter 기반
# ---------------------------------------------------------------------------


def _needs_indexing(meta: dict[str, Any], force: bool) -> bool:
    if force:
        return True
    if not meta.get("score_signature"):
        # 헤더 없는 옛날 파일 — 일단 처리
        return True
    indexed_at = meta.get("indexed_at")
    if indexed_at in (None, "", "null"):
        return True
    return False


# ---------------------------------------------------------------------------
# 단일 doc 처리
# ---------------------------------------------------------------------------


def _delete_existing_external_id(client, external_id: str) -> int:
    """외부 ID 가 같은 기존 doc 삭제 시도. AOSS Serverless _delete_by_query 미지원 시 0 반환."""
    try:
        body = {"query": {"term": {"external_id": external_id}}}
        resp = client.delete_by_query(
            index=INDEX_NAME, body=body, refresh=True, conflicts="proceed"
        )
        return int(resp.get("deleted") or 0)
    except Exception as exc:
        # AOSS Serverless 가 _delete_by_query 미지원이면 무시 — 새 doc 만 추가
        logger.info(
            "hitl_rag_ingester: delete_by_query 실패 (구 doc 잔여, 무시): %s", exc
        )
        return 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _stamp_indexed_at(md_path: Path, when_iso: str) -> None:
    """MD 파일 frontmatter 의 indexed_at 만 in-place 갱신해 다시 저장."""
    text = md_path.read_text(encoding="utf-8")
    new_line = f'indexed_at: "{when_iso}"'
    # 기존 indexed_at 라인 교체
    pattern = re.compile(r"^indexed_at:.*$", re.MULTILINE)
    if pattern.search(text):
        new_text = pattern.sub(new_line, text, count=1)
    else:
        # frontmatter 닫는 --- 직전에 삽입
        fm_close = re.compile(r"^---\s*\n(.*?)\n(---\s*\n)", re.DOTALL)
        m = fm_close.match(text)
        if not m:
            logger.warning("hitl_rag_ingester: frontmatter 미발견 — indexed_at 미갱신 (%s)", md_path.name)
            return
        body = m.group(1)
        new_body = body + "\n" + new_line
        new_text = f"---\n{new_body}\n{m.group(2)}{text[m.end():]}"
    md_path.write_text(new_text, encoding="utf-8")


def index_one(md_path: Path) -> bool:
    """단일 MD 파일 색인. 처리 완료 시 True, skip 또는 실패 시 False.

    실패는 raise 가 아닌 False — index_pending 이 errors 에 누적해서 한꺼번에 보고.
    """
    if not md_path.exists():
        logger.warning("hitl_rag_ingester: 파일 없음 — %s", md_path)
        return False
    parsed = md_exporter.parse_md_file(md_path)
    meta = parsed.get("meta") or {}
    body = parsed.get("body") or ""

    cid = str(meta.get("consultation_id") or "").strip()
    if not cid or meta.get("item_number") is None:
        logger.warning("hitl_rag_ingester: cid/item_number 누락 — skip %s", md_path.name)
        return False
    if meta.get("human_score") is None:
        # exporter 가 이미 막아두지만 방어적으로 한번 더
        logger.info("hitl_rag_ingester: human_score 없음 — skip %s", md_path.name)
        return False

    # 임베딩 — 실패 시 색인 불가 (jaccard fallback 은 KNN 인덱스에 무의미)
    text_for_embed = _embed_text_for(meta, body)
    vec = embed(text_for_embed)
    if vec is None:
        logger.warning(
            "hitl_rag_ingester: Titan 임베딩 실패 — skip %s (인덱싱 보류, 다음 호출 재시도)",
            md_path.name,
        )
        return False

    tenant_id = _resolve_tenant()
    doc = _to_doc(meta, body, list(vec), tenant_id)
    external_id = doc.get("external_id")

    client = _client()

    # 같은 external_id 의 이전 doc 제거 시도 (실패해도 진행)
    if external_id:
        _delete_existing_external_id(client, external_id)

    try:
        client.index(index=INDEX_NAME, body=doc)
    except Exception as exc:
        logger.error("hitl_rag_ingester: index 실패 %s — %s", md_path.name, exc)
        return False

    _stamp_indexed_at(md_path, _now_iso())
    logger.info(
        "hitl_rag_ingester: indexed %s (external_id=%s, item=%s)",
        md_path.name, external_id, meta.get("item_number"),
    )
    return True


# ---------------------------------------------------------------------------
# 폴더 전체 스캔
# ---------------------------------------------------------------------------


def index_pending(force: bool = False) -> dict[str, Any]:
    """HITL_RAG 폴더 스캔 → 신규/변경 MD 파일 색인.

    Returns
    -------
    dict
        ``{"indexed": int, "skipped": int, "errors": list[str]}``.
        errors 는 ``"{filename}: {reason}"`` 형식.
    """
    root = md_exporter.resolve_rag_root()
    if not root.exists():
        logger.info("hitl_rag_ingester: 폴더 없음 — %s (HITL 검수 확정 전)", root)
        return {"indexed": 0, "skipped": 0, "errors": []}

    # 인덱스 보장 — opensearch-py 미설치면 여기서 NotImplementedError
    ensure_index()

    indexed = 0
    skipped = 0
    errors: list[str] = []

    for md_path in sorted(root.glob("*.md")):
        try:
            parsed = md_exporter.parse_md_file(md_path)
        except Exception as exc:
            errors.append(f"{md_path.name}: parse 실패 — {exc}")
            continue
        meta = parsed.get("meta") or {}
        if not _needs_indexing(meta, force):
            skipped += 1
            continue
        try:
            ok = index_one(md_path)
        except Exception as exc:
            errors.append(f"{md_path.name}: {exc}")
            continue
        if ok:
            indexed += 1
        else:
            errors.append(f"{md_path.name}: index 실패 (로그 확인)")

    logger.info(
        "hitl_rag_ingester: 완료 — indexed=%d skipped=%d errors=%d",
        indexed, skipped, len(errors),
    )
    return {"indexed": indexed, "skipped": skipped, "errors": errors}


__all__ = [
    "INDEX_NAME",
    "EMBED_DIM",
    "DEFAULT_TENANT",
    "ensure_index",
    "index_pending",
    "index_one",
]
