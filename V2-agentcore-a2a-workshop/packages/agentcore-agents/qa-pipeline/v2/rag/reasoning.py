# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Reasoning RAG (RAG-2) — 과거 판정 근거 embedding 인덱스 (prototype).

설계서 7장 / PDF §7.2:
  - retrieval key = (item_number, transcript_slice)
  - 반환: ReasoningResult(examples, stdev, mean, ...)
  - 목적: **Confidence 분산 계산** (Dev5 Layer 4 연동) — 점수의 불확실성 지표.
           판정 근거 embedding 유사도 상위 샘플들의 score 분산을 신호로 사용.

**금지 사용 (원칙 7.5)**:
  - 본 RAG 의 examples[].score 를 가중평균 / 중앙값으로 **최종 점수 산출 금지**.
    오직 stdev 를 통한 confidence 지표 생성 용도.
  - 전체 transcript 만으로 retrieval 금지 — item 별로 pool 분리.
  - 타이트/느슨 평가자 평균 사용 금지 (rater_meta.rater_type 이 일관되지 않으면 skip).

인덱스 경로 (우선순위):
  1. tenants/<tenant>/reasoning_index/<NN>_<slug>.json  — PDF §7.2 별도 인덱스 (우선)
  2. fallback: golden_set 의 rationale 필드 재사용 (하위 호환)

별도 인덱스 는 판정 근거 문장만으로 구성해 "품질 유사도 ↔ embedding 유사도" 정렬을
강화한다 (PDF §7.2). production 에서는 FAISS / pgvector + bedrock titan-embed-v2
로 교체. API (retrieve_reasoning) 는 호환 유지.
"""

from __future__ import annotations

import glob
import json
import logging
import math
import os
from typing import Any, Optional

from ._util import jaccard, tenant_dir, tokenize
from .golden_set import GoldenSetRAG
from .types import RAGUnavailable, ReasoningExample, ReasoningResult


logger = logging.getLogger(__name__)

_BACKEND_ENV = "QA_RAG_BACKEND"  # "aoss" (기본) | "jaccard"


def _active_backend() -> str:
    """기본값은 `aoss` — AOSS 실패 시 golden_set/reasoning retrieve 가 jaccard 로 자동 폴백."""
    return (os.environ.get(_BACKEND_ENV) or "aoss").strip().lower()


class ReasoningRAG:
    """과거 판정 근거 인덱스 — 별도 reasoning_index 우선, golden_set fallback."""

    def __init__(
        self,
        tenant_id: str = "generic",
        top_k: int = 10,
        stdev_window: int = 20,
        *,
        golden_engine: Optional[GoldenSetRAG] = None,
        channel: str = "inbound",
        department: str = "default",
    ):
        self.tenant_id = tenant_id
        self.channel = channel
        self.department = department
        self.top_k = top_k
        self.stdev_window = stdev_window
        self._golden = golden_engine or GoldenSetRAG(
            tenant_id=tenant_id, top_k=stdev_window, channel=channel, department=department
        )
        from v2.rag._util import resolve_tenant_subdir
        self._reasoning_dir = resolve_tenant_subdir(tenant_id, "reasoning_index", channel, department)
        self._reasoning_cache: dict[int, list[ReasoningExample]] = {}

    # ----- 별도 reasoning_index (PDF §7.2 우선 경로) ------------------------

    def _load_reasoning_index(self, item_number: int) -> list[ReasoningExample]:
        """tenants/<tenant>/reasoning_index/<NN>_*.json 에서 판정 근거 레코드 로드.

        파일 부재 또는 reasoning_records 비어있으면 빈 리스트 반환 (golden_set fallback 유도).
        """
        if item_number in self._reasoning_cache:
            return self._reasoning_cache[item_number]

        if not os.path.isdir(self._reasoning_dir):
            self._reasoning_cache[item_number] = []
            return []

        pattern = os.path.join(self._reasoning_dir, f"{item_number:02d}_*.json")
        matches = glob.glob(pattern)
        if not matches:
            self._reasoning_cache[item_number] = []
            return []

        path = matches[0]
        try:
            with open(path, encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("reasoning_index: 로드 실패 %s: %s", path, e)
            self._reasoning_cache[item_number] = []
            return []

        out: list[ReasoningExample] = []
        for rec in data.get("reasoning_records", []):
            rationale = rec.get("rationale", "")
            if not rationale:
                continue
            out.append(
                ReasoningExample(
                    example_id=rec.get("record_id", ""),
                    item_number=data.get("item_number", item_number),
                    rationale=rationale,
                    score=rec.get("score"),
                    rationale_tags=rec.get("tags", []),
                    rater_meta={
                        "evaluator_id": rec.get("evaluator_id", ""),
                        "source": "reasoning_index",
                        "stub_seed": bool(rec.get("stub_seed", False)),
                        "quote_example": rec.get("quote_example", ""),
                    },
                )
            )
        self._reasoning_cache[item_number] = out
        return out

    # ----- Fallback: golden_set rationale 재활용 -----------------------------

    def _load_fallback_from_golden(self, item_number: int) -> list[ReasoningExample]:
        examples = self._golden._load_item(item_number)
        if not examples:
            return []
        out: list[ReasoningExample] = []
        for ex in examples:
            if not ex.rationale:
                continue
            out.append(
                ReasoningExample(
                    example_id=ex.example_id,
                    item_number=ex.item_number,
                    rationale=ex.rationale,
                    score=ex.score,
                    rationale_tags=ex.rationale_tags,
                    rater_meta=ex.rater_meta,
                )
            )
        return out

    def _pool(self, item_number: int) -> list[ReasoningExample]:
        """별도 인덱스 우선, 비어있으면 golden_set rationale 로 폴백."""
        primary = self._load_reasoning_index(item_number)
        if primary:
            return primary
        logger.debug(
            "reasoning: reasoning_index miss for item=%d, falling back to golden_set",
            item_number,
        )
        return self._load_fallback_from_golden(item_number)

    def retrieve(
        self,
        item_number: int,
        transcript_slice: str,
        top_k: Optional[int] = None,
    ) -> ReasoningResult:
        k = top_k or self.top_k

        # === AOSS 경로 ===
        if _active_backend() == "aoss":
            try:
                return self._retrieve_aoss(item_number, transcript_slice, k)
            except Exception as e:  # noqa: BLE001
                logger.warning("AOSS reasoning retrieve 실패 → jaccard 폴백: %s", e)

        # === Jaccard 로컬 경로 ===
        pool = self._pool(item_number)
        if not pool:
            raise RAGUnavailable(
                f"reasoning: empty pool for item_number={item_number} (tenant={self.tenant_id})"
            )

        slice_tokens = tokenize(transcript_slice)

        scored: list[tuple[float, ReasoningExample]] = []
        for ex in pool:
            sim = jaccard(slice_tokens, tokenize(ex.rationale))
            scored.append((sim, ex))
        scored.sort(key=lambda t: t[0], reverse=True)

        selected = [ex for _sim, ex in scored[:k]]

        # stdev/mean 계산 — score 가 None 인 예시는 제외
        scores = [ex.score for ex in selected if ex.score is not None]
        if len(scores) >= 2:
            mean = sum(scores) / len(scores)
            var = sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)
            stdev = math.sqrt(var)
        elif len(scores) == 1:
            mean = scores[0]
            stdev = 0.0
        else:
            mean = 0.0
            stdev = 0.0

        return ReasoningResult(
            item_number=item_number,
            examples=selected,
            stdev=stdev,
            mean=mean,
            sample_size=len(scores),
            query_slice=transcript_slice,
            match_reason=(
                f"item={item_number}; pool={len(pool)}; k={len(selected)}; scored={len(scores)}"
            ),
        )

    def _retrieve_aoss(
        self, item_number: int, transcript_slice: str, k: int
    ) -> ReasoningResult:
        """AOSS KNN 경로 — Titan embed + cosine. stdev 는 반환 score 로 재계산."""
        from .aoss_store import get_store, REASONING_INDEX
        from .embedding import embed

        vec = embed(transcript_slice)
        if vec is None:
            raise RuntimeError("Titan embed 실패")

        store = get_store(REASONING_INDEX)
        # 하이브리드 검색 (BM25 + KNN RRF) — rationale + quote_example 키워드 매칭.
        hits = store.search_hybrid(
            query_text=transcript_slice,
            query_vector=list(vec),
            top_k=k,
            tenant_id=self.tenant_id,
            channel=self.channel,
            department=self.department,
            item_number=item_number,
            text_fields=["rationale^1", "quote_example^2"],
        )
        if not hits:
            raise RAGUnavailable(
                f"AOSS reasoning empty (tenant={self.tenant_id}, item={item_number})"
            )

        examples: list[ReasoningExample] = []
        for h in hits:
            examples.append(
                ReasoningExample(
                    example_id=h.get("record_id") or "",
                    item_number=h.get("item_number") or item_number,
                    rationale=h.get("rationale") or "",
                    score=h.get("score"),
                    rationale_tags=h.get("rationale_tags") or [],
                    rater_meta={
                        "evaluator_id": h.get("evaluator_id"),
                        "source": "aoss",
                        "stub_seed": False,
                        "quote_example": h.get("quote_example", ""),
                        "similarity": float(h.get("_score") or 0.0),  # legacy (=RRF)
                        "cosine_score": h.get("_knn_score"),
                        "bm25_pct": h.get("_bm25_pct"),
                        "rrf_score": h.get("_rrf_score"),
                        "bm25_score": h.get("_bm25_score"),
                        "bm25_rank": h.get("_bm25_rank"),
                        "knn_rank": h.get("_knn_rank"),
                    },
                )
            )

        scores = [ex.score for ex in examples if ex.score is not None]
        if len(scores) >= 2:
            mean = sum(scores) / len(scores)
            var = sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)
            stdev = math.sqrt(var)
        elif len(scores) == 1:
            mean, stdev = scores[0], 0.0
        else:
            mean, stdev = 0.0, 0.0

        return ReasoningResult(
            item_number=item_number,
            examples=examples,
            stdev=stdev,
            mean=mean,
            sample_size=len(scores),
            query_slice=transcript_slice,
            match_reason=f"item={item_number}; backend=aoss; k={len(examples)}; scored={len(scores)}",
        )


# ---------------------------------------------------------------------------
# Module-level API
# ---------------------------------------------------------------------------

_DEFAULT_ENGINE: Optional[ReasoningRAG] = None


def _get_engine(tenant_id: str = "generic") -> ReasoningRAG:
    global _DEFAULT_ENGINE
    if _DEFAULT_ENGINE is None or _DEFAULT_ENGINE.tenant_id != tenant_id:
        _DEFAULT_ENGINE = ReasoningRAG(tenant_id=tenant_id)
    return _DEFAULT_ENGINE


def retrieve_reasoning(
    item_number: int,
    transcript_slice: str,
    *,
    tenant_id: str = "generic",
    top_k: int = 10,
) -> ReasoningResult:
    """설계서 공개 API.

    반환 `ReasoningResult.stdev` 는 Dev5 Layer 4 에서 confidence 지표로 사용.
    본 반환값을 절대 점수 산출에 사용하지 말 것 (원칙 7.5 위반).
    """
    engine = _get_engine(tenant_id)
    result = engine.retrieve(item_number, transcript_slice, top_k=top_k)
    if result.sample_size > 0:
        mean_val = getattr(result, "mean", None)
        logger.info(
            "[RAG reasoning] item #%d tenant=%s → n=%d stdev=%.3f mean=%s",
            item_number, tenant_id, result.sample_size, float(result.stdev),
            f"{mean_val:.2f}" if mean_val is not None else "N/A",
        )
    else:
        logger.info(
            "[RAG reasoning] item #%d tenant=%s → 0 hits (slice_len=%d)",
            item_number, tenant_id, len(transcript_slice or ""),
        )
    return result
