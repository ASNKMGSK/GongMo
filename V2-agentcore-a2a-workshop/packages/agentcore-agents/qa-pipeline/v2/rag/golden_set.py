# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Golden-set RAG (RAG-1) — Few-shot 예시 공급.

설계서 7장:
  - retrieval key = (평가항목 item_number, intent, segment_text)
  - 반환: FewshotResult (k=3~5 examples)

**금지 사용 (원칙 7.5)**:
  - 과거 인간 평가자 점수를 가중평균하여 "현재 점수" 를 산출하는 용도 사용 금지.
    본 RAG 는 **유사 상황 판정 근거의 제시** 용도. Sub Agent 점수 산출은 LLM 재판정.
  - Transcript 전체 semantic 유사도 단독 retrieval 금지 — 반드시 item_number 로
    먼저 pool 을 좁힌 후 intent → segment 유사도 순으로 재정렬.
  - 평가자 메타(rater_meta) 없는 수기 점수 pool 사용 금지.

프로토타입 구현: tenants/<tenant_id>/golden_set/*.json 을 메모리 로드하여
토큰 겹침 기반 match_reason 제공. production 에서는 FAISS/OpenSearch 등으로
교체하되 본 모듈의 공개 API (retrieve_fewshot) 는 호환 유지.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from ._util import jaccard, read_text, tenant_dir, tokenize
from .types import FewshotExample, FewshotResult, RAGUnavailable


logger = logging.getLogger(__name__)


_BACKEND_ENV = "QA_RAG_BACKEND"  # "aoss" (기본) | "jaccard"


def _active_backend() -> str:
    """기본값은 `aoss` — AOSS 실패 시 golden_set/reasoning retrieve 가 jaccard 로 자동 폴백."""
    return (os.environ.get(_BACKEND_ENV) or "aoss").strip().lower()


_FILENAME_BY_ITEM: dict[int, str] = {
    1: "01_first_greeting.json",
    2: "02_closing_greeting.json",
    3: "03_listening_interruption.json",
    4: "04_empathy.json",
    5: "05_hold_notice.json",
    6: "06_polite_language.json",
    7: "07_cushion_words.json",
    8: "08_needs_identification.json",
    9: "09_customer_info_verification.json",
    10: "10_explanation_clarity.json",
    11: "11_top_down_answer.json",
    12: "12_problem_solving_attitude.json",
    13: "13_additional_guidance.json",
    14: "14_follow_up.json",
    15: "15_correct_information.json",
    16: "16_mandatory_notice.json",
    17: "17_pii_verification.json",
    18: "18_privacy_compliance.json",
}


class GoldenSetRAG:
    """메모리 로드 기반 Golden-set retrieval 엔진.

    3단계 멀티테넌트 (2026-04-24): channel/department 파라미터 추가.
    경로 탐색은 `resolve_tenant_subdir` fallback 체인으로 처리 → 기존 단일
    tenant 호출 (`GoldenSetRAG(tenant_id)`) 은 channel="inbound",
    department="default" 기본값으로 동작하며, 레거시 tenants/{site}/golden_set/
    경로를 4단계 fallback 에서 찾아주므로 기존 데이터는 그대로 작동.
    """

    def __init__(
        self,
        tenant_id: str = "generic",
        top_k: int = 5,
        channel: str = "inbound",
        department: str = "default",
    ):
        self.tenant_id = tenant_id
        self.channel = channel
        self.department = department
        self.top_k = top_k
        # resolve_tenant_subdir 는 os 경로 문자열 반환 — 기존 os.path.join 호환.
        from v2.rag._util import resolve_tenant_subdir
        self._golden_dir = resolve_tenant_subdir(tenant_id, "golden_set", channel, department)
        self._cache: dict[int, list[FewshotExample]] = {}

    # ---- loading -----------------------------------------------------------

    def _load_item(self, item_number: int) -> list[FewshotExample]:
        if item_number in self._cache:
            return self._cache[item_number]

        fname = _FILENAME_BY_ITEM.get(item_number)
        if not fname:
            raise RAGUnavailable(f"golden_set: unknown item_number={item_number}")

        path = os.path.join(self._golden_dir, fname)
        raw = read_text(path)
        if not raw.strip():
            logger.warning("golden_set file missing: %s", path)
            self._cache[item_number] = []
            return []

        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RAGUnavailable(f"golden_set parse error ({fname}): {e}") from e

        examples = []
        for ex in data.get("examples", []):
            # rater_meta 없는 데이터는 원칙 7.5 위반이므로 필터링
            if not ex.get("rater_meta"):
                logger.warning("golden_set example missing rater_meta — skipped: %s", ex.get("example_id"))
                continue
            examples.append(
                FewshotExample(
                    example_id=ex["example_id"],
                    item_number=data.get("item_number", item_number),
                    score=ex.get("score"),
                    score_bucket=ex.get("score_bucket", "unknown"),
                    intent=ex.get("intent", "*"),
                    segment_text=ex.get("segment_text", ""),
                    rationale=ex.get("rationale", ""),
                    rationale_tags=ex.get("rationale_tags", []),
                    evidence_refs=ex.get("evidence_refs", []),
                    rater_meta=ex.get("rater_meta", {}),
                )
            )

        self._cache[item_number] = examples
        return examples

    # ---- retrieval ---------------------------------------------------------

    def retrieve(
        self,
        item_number: int,
        intent: str,
        segment_text: str,
        top_k: Optional[int] = None,
    ) -> FewshotResult:
        """item_number 로 pool 을 좁힌 뒤 intent 일치 → segment 유사도 순 정렬.

        `QA_RAG_BACKEND=aoss` 이면 AOSS KNN 사용, 아니면 Jaccard 로 로컬 검색.
        반환 개수는 `top_k` (기본 `self.top_k`) 이하.
        """
        k = top_k or self.top_k

        # === AOSS 경로 ===
        if _active_backend() == "aoss":
            try:
                return self._retrieve_aoss(item_number, intent, segment_text, k)
            except Exception as e:  # noqa: BLE001 — 실패 시 jaccard 로 폴백
                logger.warning("AOSS golden retrieve 실패 → jaccard 폴백: %s", e)

        # === Jaccard 로컬 경로 (기본) ===
        pool = self._load_item(item_number)
        if not pool:
            raise RAGUnavailable(
                f"golden_set empty for item_number={item_number} (tenant={self.tenant_id})"
            )

        query_tokens = tokenize(segment_text)

        # 1) intent 필터 — '*' 또는 정확 일치
        def intent_match(ex: FewshotExample) -> bool:
            return ex.intent == "*" or intent == "*" or ex.intent == intent

        candidates = [ex for ex in pool if intent_match(ex)]
        if not candidates:
            # intent 일치 없으면 전체 pool 사용 — match_reason 에 기록
            candidates = list(pool)
            intent_note = "intent_fallback_all"
        else:
            intent_note = "intent_filtered"

        # 2) Jaccard 유사도 계산 (segment 로만; transcript 전체 X — 원칙 7.5)
        scored: list[tuple[float, FewshotExample]] = []
        for ex in candidates:
            sim = jaccard(query_tokens, tokenize(ex.segment_text))
            scored.append((sim, ex))
        scored.sort(key=lambda t: t[0], reverse=True)

        # 3) 유사도 우선 — AOSS 경로와 동일 정책 (2026-04-21, bucket balance 제거)
        #    동일 bucket 이 연속 3건 넘어가면 4건째부터 스킵 (다양성 최소 보장)
        selected: list[FewshotExample] = []
        same_bucket_run = {"bucket": None, "count": 0}
        skipped: list[FewshotExample] = []
        for _sim, ex in scored:
            if len(selected) >= k:
                break
            if same_bucket_run["bucket"] == ex.score_bucket:
                if same_bucket_run["count"] >= 3:
                    skipped.append(ex)
                    continue
                same_bucket_run["count"] += 1
            else:
                same_bucket_run = {"bucket": ex.score_bucket, "count": 1}
            selected.append(ex)
        if len(selected) < k and skipped:
            selected.extend(skipped[: k - len(selected)])

        return FewshotResult(
            item_number=item_number,
            intent=intent,
            examples=selected,
            query_segment=segment_text,
            match_reason=f"item={item_number}; {intent_note}; pool={len(pool)}; cand={len(candidates)}; k={len(selected)}",
            total_pool=len(pool),
        )

    def _retrieve_aoss(
        self, item_number: int, intent: str, segment_text: str, k: int
    ) -> FewshotResult:
        """AOSS KNN 경로 — Titan embed + cosine (FAISS HNSW)."""
        from .aoss_store import get_store, GOLDEN_INDEX
        from .embedding import embed

        vec = embed(segment_text)
        if vec is None:
            raise RuntimeError("Titan embed 실패")

        store = get_store(GOLDEN_INDEX)
        # 하이브리드 검색 (BM25 + KNN RRF) — 키워드 매칭 + 의미 유사도 동시 활용.
        # segment_text^2 + rationale^1 로 BM25F 스타일 가중.
        hits = store.search_hybrid(
            query_text=segment_text,
            query_vector=list(vec),
            top_k=max(k * 2, k + 3),
            tenant_id=self.tenant_id,
            channel=self.channel,
            department=self.department,
            item_number=item_number,
            text_fields=["segment_text^2", "rationale^1"],
        )
        if not hits:
            raise RAGUnavailable(
                f"AOSS golden_set empty (tenant={self.tenant_id}, item={item_number})"
            )

        examples: list[FewshotExample] = []
        for h in hits:
            examples.append(
                FewshotExample(
                    example_id=h.get("example_id") or "",
                    item_number=h.get("item_number") or item_number,
                    score=h.get("score"),
                    score_bucket=h.get("score_bucket") or "unknown",
                    intent=h.get("intent") or "*",
                    segment_text=h.get("segment_text") or "",
                    rationale=h.get("rationale") or "",
                    rationale_tags=h.get("rationale_tags") or [],
                    evidence_refs=[],
                    rater_meta={
                        "rater_type": h.get("rater_type"),
                        "source": h.get("rater_source"),
                        "similarity": float(h.get("_score") or 0.0),  # legacy (=RRF)
                        # 정규화된 지표 (0~100 또는 0~1)
                        "cosine_score": h.get("_knn_score"),   # 0~1 정규화
                        "bm25_pct": h.get("_bm25_pct"),        # 쿼리 내 top-1 대비 % (0~100)
                        "rrf_score": h.get("_rrf_score"),      # 0~0.033 (이론 최대)
                        # 원본/순위 (디버깅용)
                        "bm25_score": h.get("_bm25_score"),
                        "bm25_rank": h.get("_bm25_rank"),
                        "knn_rank": h.get("_knn_rank"),
                    },
                )
            )

        # 유사도 우선 정책 — bucket balance 제거 (2026-04-21).
        # 사유: bucket 라운드로빈이 top-1 유사도 예시를 희석시켜 오라클 retrieval 가치를 손상.
        # 이제 AOSS KNN 순위 (cosine similarity 내림차순) 그대로 상위 k 개 채택.
        # bucket 다양성 이 필요한 케이스는 dedup-by-bucket heuristic 만 최소 적용 (동일 bucket
        # 연속 3 개 초과 시 다음 bucket 으로 교체 — top-1 는 항상 보존).
        selected: list[FewshotExample] = []
        same_bucket_run = {"bucket": None, "count": 0}
        for ex in examples:
            if len(selected) >= k:
                break
            # 첫 picks 는 무조건 순위 그대로 채택 (top-1 보존)
            if same_bucket_run["bucket"] == ex.score_bucket:
                if same_bucket_run["count"] >= 3:
                    # 동일 bucket 4번째부터는 스킵 (다른 bucket 이 올 때까지)
                    continue
                same_bucket_run["count"] += 1
            else:
                same_bucket_run = {"bucket": ex.score_bucket, "count": 1}
            selected.append(ex)

        # k 미달 시 스킵했던 항목 재채워서 k 채우기 (bucket 다양성 강제 조항 해제)
        if len(selected) < k:
            remaining = [ex for ex in examples if ex not in selected]
            selected.extend(remaining[: k - len(selected)])

        return FewshotResult(
            item_number=item_number,
            intent=intent,
            examples=selected,
            query_segment=segment_text,
            match_reason=f"item={item_number}; backend=aoss; hits={len(hits)}; k={len(selected)}; similarity_first",
            total_pool=len(hits),
        )


# ---------------------------------------------------------------------------
# Module-level convenience wrapper (설계서 공개 API)
# ---------------------------------------------------------------------------

_DEFAULT_ENGINE: Optional[GoldenSetRAG] = None


def _get_engine(tenant_id: str = "generic") -> GoldenSetRAG:
    global _DEFAULT_ENGINE
    if _DEFAULT_ENGINE is None or _DEFAULT_ENGINE.tenant_id != tenant_id:
        _DEFAULT_ENGINE = GoldenSetRAG(tenant_id=tenant_id)
    return _DEFAULT_ENGINE


def retrieve_fewshot(
    item_number: int,
    intent: str,
    segment_text: str,
    *,
    tenant_id: str = "generic",
    top_k: int = 5,
) -> FewshotResult:
    """설계서 공개 API — Sub Agent 가 직접 호출하는 형식.

    Parameters
    ----------
    item_number : 1~18 평가항목 번호.
    intent      : Layer 1 분류 결과 intent 또는 "*".
    segment_text: Segment 추출기가 고른 발화 묶음. transcript 전체 금지.
    tenant_id   : tenants/ 디렉토리 이름 (기본 generic).
    top_k       : 반환 예시 최대 개수.

    Raises
    ------
    RAGUnavailable : 골든셋 부재 / 파싱 오류.
    """
    engine = _get_engine(tenant_id)
    result = engine.retrieve(item_number, intent, segment_text, top_k=top_k)
    examples = result.examples or []
    if examples:
        logger.info(
            "[RAG fewshot] item #%d tenant=%s intent=%s → %d hits (ids=%s, scores=%s)",
            item_number, tenant_id, intent, len(examples),
            [ex.example_id for ex in examples[:5]],
            [ex.score for ex in examples[:5]],
        )
    else:
        logger.info(
            "[RAG fewshot] item #%d tenant=%s intent=%s → 0 hits (segment_len=%d)",
            item_number, tenant_id, intent, len(segment_text or ""),
        )
    return result
