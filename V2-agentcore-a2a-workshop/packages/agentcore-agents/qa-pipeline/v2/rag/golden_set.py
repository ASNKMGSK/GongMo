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

검색 정책 (2026-05-08, stratified 도입):
  - **기본 모드**: bucket 별 stratified retrieval (full/partial/zero 별 separate
    retrieve + bucket 내부 reranker). 골든셋 원래 컨셉인 contrastive few-shot 복원.
  - **회귀 모드**: 환경변수 ``QA_GOLDEN_SET_SIMILARITY_FIRST=1`` 설정 시 post-2026-04-21
    similarity-first (RRF top-k) 로 폴백 가능.
  - 폴백 체인: AOSS 실패 → Jaccard 로컬 검색 (변경 없음).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from ._util import jaccard, read_text, tenant_dir, tokenize
from .types import FewshotExample, FewshotResult, RAGUnavailable


logger = logging.getLogger(__name__)


_BACKEND_ENV = "QA_RAG_BACKEND"  # "aoss" (기본) | "jaccard"

# ---------------------------------------------------------------------------
# parsed_text fallback — AOSS 인덱스가 parsed_text 필드 없이 빌드된 레거시 케이스용.
# md 파일 (v2/tenants/{tenant_id}/golden_set/{NN}_*_{sample_id}.md) 의
# `## 파싱 원문` 코드블록 내용을 추출해 hit 의 비어있는 parsed_text 자리에 채워넣음.
# 모듈 레벨 캐시 (글로브 결과 + 파싱 결과) 로 반복 디스크 I/O 회피.
# ---------------------------------------------------------------------------
_PARSED_TEXT_CACHE: dict[str, str] = {}
_MD_PATH_CACHE: dict[str, Optional[Path]] = {}

# `## 파싱 원문` 헤더 (서브타이틀/괄호/공백 자유) → 다음 섹션 (`## ...`) 직전까지 본문 캡처.
_PARSED_SECTION_RE = re.compile(
    r"^##\s*파싱\s*원문[^\n]*\n(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
# 첫 번째 ```...``` (언어 태그 무시) 코드블록 내용 추출.
_FIRST_CODEBLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def _resolve_parsed_text_md(item_padded: str, sample_id: str, tenant_id: str) -> Optional[Path]:
    """{NN}_*_{sample_id}.md 파일을 글로브로 찾아 단일 경로 반환 (없으면 None).

    경로 결과는 ``_MD_PATH_CACHE`` 에 (tenant, item, sample) 키로 캐시.
    """
    cache_key = f"{tenant_id}::{item_padded}::{sample_id}"
    if cache_key in _MD_PATH_CACHE:
        return _MD_PATH_CACHE[cache_key]

    here = Path(__file__).resolve()
    # v2/rag/golden_set.py → v2/tenants/{tenant_id}/golden_set/
    golden_dir = here.parent.parent / "tenants" / tenant_id / "golden_set"
    found: Optional[Path] = None
    if golden_dir.exists():
        try:
            matches = list(golden_dir.glob(f"{item_padded}_*_{sample_id}.md"))
            if matches:
                found = matches[0]
        except OSError:
            found = None
    _MD_PATH_CACHE[cache_key] = found
    return found


def _load_parsed_text_fallback(example_id: str, item_number: int, tenant_id: str) -> str:
    """example_id (`GS-{item}-{sample}`) 로 md 파일을 찾아 `## 파싱 원문` 코드블록 추출.

    실패 (ID 파싱 / 파일 부재 / 섹션 부재 / 코드블록 부재) 는 모두 빈 문자열 반환.
    결과는 ``_PARSED_TEXT_CACHE`` 에 example_id 단위로 캐시.
    """
    if not example_id:
        return ""
    cached = _PARSED_TEXT_CACHE.get(example_id)
    if cached is not None:
        return cached

    try:
        # example_id 포맷: GS-{item_padded}-{sample_id} (e.g. GS-12-668437)
        parts = example_id.split("-")
        if len(parts) < 3 or parts[0] != "GS":
            _PARSED_TEXT_CACHE[example_id] = ""
            return ""
        item_part = parts[1]
        sample_id = "-".join(parts[2:])  # 혹시 sample_id 에 hyphen 있을 가능성 보존
        # item_padded — example_id 자체 값 우선, 아니면 item_number 패딩.
        item_padded = item_part if item_part.isdigit() and len(item_part) >= 2 else f"{item_number:02d}"

        md_path = _resolve_parsed_text_md(item_padded, sample_id, tenant_id)
        if md_path is None or not md_path.exists():
            _PARSED_TEXT_CACHE[example_id] = ""
            return ""

        text = md_path.read_text(encoding="utf-8")
        section_match = _PARSED_SECTION_RE.search(text)
        if not section_match:
            _PARSED_TEXT_CACHE[example_id] = ""
            return ""
        section_body = section_match.group(1)
        block_match = _FIRST_CODEBLOCK_RE.search(section_body)
        if not block_match:
            _PARSED_TEXT_CACHE[example_id] = ""
            return ""
        parsed = block_match.group(1).strip()
        _PARSED_TEXT_CACHE[example_id] = parsed
        return parsed
    except Exception as exc:  # noqa: BLE001 — 폴백은 항상 silent.
        logger.debug("parsed_text fallback 실패 example_id=%s: %s", example_id, exc)
        _PARSED_TEXT_CACHE[example_id] = ""
        return ""


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
            ex_id = ex["example_id"]
            ex_item = data.get("item_number", item_number)
            # parsed_text 폴백 — JSON 에 없으면 md `## 파싱 원문` 코드블록 추출.
            pt = ex.get("parsed_text") or ""
            if not pt and ex_id:
                pt = _load_parsed_text_fallback(ex_id, ex_item, self.tenant_id)
            examples.append(
                FewshotExample(
                    example_id=ex_id,
                    item_number=ex_item,
                    score=ex.get("score"),
                    score_bucket=ex.get("score_bucket", "unknown"),
                    intent=ex.get("intent", "*"),
                    segment_text=ex.get("segment_text", ""),
                    rationale=ex.get("rationale", ""),
                    rationale_tags=ex.get("rationale_tags", []),
                    evidence_refs=ex.get("evidence_refs", []),
                    parsed_text=pt,
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
        self,
        item_number: int,
        intent: str,
        segment_text: str,
        k: int,
        *,
        score_bucket: Optional[str] = None,
    ) -> FewshotResult:
        """AOSS KNN 경로 — Titan embed + cosine (FAISS HNSW).

        ``score_bucket`` 가 주어지면 AOSS 쿼리 filter 절에 ``term: {score_bucket: ...}``
        를 추가해 해당 bucket 내부에서만 retrieval. stratified retrieval 의 1차 검색
        단계에서 사용.
        """
        from .aoss_store import get_store, GOLDEN_INDEX
        from .embedding import embed

        vec = embed(segment_text)
        if vec is None:
            raise RuntimeError("Titan embed 실패")

        store = get_store(GOLDEN_INDEX)
        # bucket 필터 — search_hybrid 의 extra_filters 인자로 주입 (BM25/KNN 양쪽 동일 적용).
        extra_filters: Optional[list[dict]] = None
        if score_bucket:
            extra_filters = [{"term": {"score_bucket": score_bucket}}]
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
            extra_filters=extra_filters,
        )
        if not hits:
            raise RAGUnavailable(
                f"AOSS golden_set empty (tenant={self.tenant_id}, item={item_number}"
                + (f", bucket={score_bucket})" if score_bucket else ")")
            )

        examples: list[FewshotExample] = []
        for h in hits:
            example_id = h.get("example_id") or ""
            # parsed_text 폴백 — AOSS 인덱스가 parsed_text 없이 빌드된 레거시 케이스
            # 보호용. md 파일 (`## 파싱 원문` 코드블록) 에서 후처리 로드.
            pt = h.get("parsed_text") or ""
            if not pt and example_id:
                pt = _load_parsed_text_fallback(
                    example_id,
                    h.get("item_number") or item_number,
                    self.tenant_id,
                )
            examples.append(
                FewshotExample(
                    example_id=example_id,
                    item_number=h.get("item_number") or item_number,
                    score=h.get("score"),
                    score_bucket=h.get("score_bucket") or "unknown",
                    intent=h.get("intent") or "*",
                    segment_text=h.get("segment_text") or "",
                    rationale=h.get("rationale") or "",
                    rationale_tags=h.get("rationale_tags") or [],
                    evidence_refs=[],
                    parsed_text=pt,
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

        bucket_note = f"; bucket={score_bucket}" if score_bucket else ""
        return FewshotResult(
            item_number=item_number,
            intent=intent,
            examples=selected,
            query_segment=segment_text,
            match_reason=(
                f"item={item_number}; backend=aoss; hits={len(hits)}; "
                f"k={len(selected)}; similarity_first{bucket_note}"
            ),
            total_pool=len(hits),
        )

    # ---- Stratified retrieval (bucket-balanced + bucket-level reranker) ----
    #
    # 골든셋 원래 컨셉 (점수별 contrastive few-shot) 복원 + Cohere reranker 진화.
    # similarity-first (post-2026-04-21) 가 top-k 를 RRF 순위로만 채우면서 contrastive
    # 효과가 사라진 문제 해결 → bucket 별 separate retrieve + bucket 별 reranker.

    # 사용자 지시 (2026-05-08): 리랭커 후 bucket 별 최종 = 2건 고정.
    # → 3 buckets × 2 = 총 6 hits (top_k 입력값 무시).
    _STRATIFIED_PER_BUCKET = 2

    @staticmethod
    def _calculate_bucket_quotas(top_k: int, buckets: list[str]) -> dict[str, int]:
        """bucket 별 quota = 고정 2건 (사용자 지시 2026-05-08).

        top_k 입력값은 무시. 리랭커 후 각 bucket 별로 정확히 2건씩 채택 →
        3 buckets (full/partial/zero) × 2 = 총 6 contrastive examples.
        """
        return {b: GoldenSetRAG._STRATIFIED_PER_BUCKET for b in buckets}

    def _retrieve_with_bucket_filter(
        self,
        item_number: int,
        intent: str,
        segment_text: str,
        *,
        score_bucket: Optional[str],
        top_k: int,
    ) -> list[FewshotExample]:
        """단일 bucket 에 대해 AOSS retrieve 후 examples 만 반환.

        ``score_bucket=None`` 이면 bucket 무관 (leftover fill 용 폴백 폴링).
        실패 시 빈 리스트 반환 (caller 가 leftover quota 처리).
        """
        if top_k <= 0:
            return []
        try:
            result = self._retrieve_aoss(
                item_number, intent, segment_text, top_k, score_bucket=score_bucket
            )
        except RAGUnavailable:
            # bucket 에 데이터가 전혀 없는 케이스 — leftover 로 처리
            return []
        return list(result.examples or [])

    def retrieve_stratified(
        self,
        item_number: int,
        intent: str,
        segment_text: str,
        top_k: int = 5,
    ) -> FewshotResult:
        """Stratified retrieval — bucket 별 separate retrieve + bucket 별 reranker.

        원래 골든셋 컨셉 (점수별 contrastive few-shot) 복원 + Cohere reranker 진화.

        ★ 사용자 지시 (2026-05-08):
          - fetch_k = 10 (bucket 별 1차 검색)
          - 리랭커 후 bucket 별 최종 = 2건 고정
          - 결과: 3 buckets (full/partial/zero) × 2 = 총 6 hits
          - top_k 입력값은 무시 (호환성 유지용 인자)

        동작:
          1. 각 bucket 별로 fetch_k=10 만큼 separate AOSS 쿼리 (bucket filter)
          2. 각 bucket 별로 reranker (활성 시) → 2건씩 추림
          3. 모든 bucket 결과 합쳐서 반환 (총 6건)
          4. 어떤 bucket 이 비어있으면 (예: #1 첫인사 = 거의 full 만 있음)
             → 그 quota 를 leftover 로 모아 backup_pool 에서 채움 (similarity-first 폴백).
        """
        from . import is_reranker_enabled, rerank

        # 분배 정책 — top_k=5 기준 full:partial:zero = 2:2:1
        BUCKET_PRIORITIES = ["full", "partial", "zero"]
        quotas = self._calculate_bucket_quotas(top_k, BUCKET_PRIORITIES)

        selected: list[FewshotExample] = []
        bucket_diagnostics: dict[str, int] = {}
        leftover_quota = 0

        for bucket in BUCKET_PRIORITIES:
            quota = quotas.get(bucket, 0)
            if quota <= 0:
                continue

            # 1차 검색 — bucket filter + fetch_k. 사용자 지시 (2026-05-08): 모두 10 으로 고정.
            fetch_k = 10
            try:
                bucket_examples = self._retrieve_with_bucket_filter(
                    item_number,
                    intent,
                    segment_text,
                    score_bucket=bucket,
                    top_k=fetch_k,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "stratified retrieve bucket=%s 실패: %s — quota %d → leftover",
                    bucket, exc, quota,
                )
                leftover_quota += quota
                bucket_diagnostics[bucket] = 0
                continue

            if not bucket_examples:
                # 이 bucket 에 데이터 없음 — quota 를 leftover 로
                leftover_quota += quota
                bucket_diagnostics[bucket] = 0
                continue

            # 2차 reranker (활성 시) — bucket 내부에서만 재정렬
            if is_reranker_enabled() and len(bucket_examples) > quota:
                docs = [
                    (ex.segment_text or "") + ("\n" + ex.rationale if ex.rationale else "")
                    for ex in bucket_examples
                ]
                order, ok = rerank(segment_text or "", docs, top_n=quota)
                # 2026-05-08: rerank() 호출 시점의 provider — UI 가 chip 에 표시.
                from . import get_reranker_provider as _get_provider
                _provider_at_call = _get_provider()
                if order and ok:
                    new_examples: list[FewshotExample] = []
                    for original_idx, score in order:
                        if original_idx >= len(bucket_examples):
                            continue
                        ex = bucket_examples[original_idx]
                        rater = dict(ex.rater_meta) if ex.rater_meta else {}
                        rater["cohere_rerank_score"] = score
                        rater["reranked"] = True
                        rater["rerank_provider"] = _provider_at_call
                        rater["stratified_bucket"] = bucket  # 진단용
                        ex.rater_meta = rater
                        new_examples.append(ex)
                    bucket_examples = new_examples
                else:
                    # rerank 실패 → 입력 순서로 quota 채택 (reranked 마킹 안 함 — UI rr 0.00 회피)
                    bucket_examples = bucket_examples[:quota]
                    for ex in bucket_examples:
                        rater = dict(ex.rater_meta) if ex.rater_meta else {}
                        rater["stratified_bucket"] = bucket
                        ex.rater_meta = rater
            else:
                bucket_examples = bucket_examples[:quota]
                for ex in bucket_examples:
                    rater = dict(ex.rater_meta) if ex.rater_meta else {}
                    rater["stratified_bucket"] = bucket
                    ex.rater_meta = rater

            # 받은 게 quota 보다 적으면 (rerank 후) 차이를 leftover 로
            shortfall = quota - len(bucket_examples)
            if shortfall > 0:
                leftover_quota += shortfall

            selected.extend(bucket_examples)
            bucket_diagnostics[bucket] = len(bucket_examples)

        # leftover quota 가 있으면 — bucket 무관 similarity-first 풀에서 추가 채택
        # (현재 selected 에 안 들어간 후보 중에서)
        if leftover_quota > 0 and len(selected) < top_k:
            try:
                backup_pool = self._retrieve_with_bucket_filter(
                    item_number,
                    intent,
                    segment_text,
                    score_bucket=None,  # bucket 무관
                    top_k=top_k * 2,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("stratified leftover fill 실패: %s", exc)
                backup_pool = []

            already_ids = {ex.example_id for ex in selected if ex.example_id}
            for ex in backup_pool:
                if len(selected) >= top_k:
                    break
                if ex.example_id and ex.example_id in already_ids:
                    continue
                rater = dict(ex.rater_meta) if ex.rater_meta else {}
                rater["stratified_bucket"] = "leftover_fill"
                ex.rater_meta = rater
                selected.append(ex)

        logger.info(
            "stratified retrieve item=#%d intent=%s top_k=%d → %s (leftover=%d, picked=%d)",
            item_number, intent, top_k, bucket_diagnostics, leftover_quota, len(selected),
        )

        return FewshotResult(
            item_number=item_number,
            intent=intent,
            examples=selected,
            query_segment=segment_text,
            match_reason=(
                f"stratified; item={item_number}; backend=aoss; "
                f"buckets={bucket_diagnostics}; leftover={leftover_quota}; "
                f"reranker={is_reranker_enabled()}"
            ),
            total_pool=len(selected),  # bucket 별 + leftover 합산 = 최종 선택된 개수
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
    # 전역 RAG 비활성 토글 (사용자 비교 실험용). contextvar 가 set 되어 있으면
    # 빈 FewshotResult 반환 — Sub Agent 는 RAG hits 0건으로 진행.
    from . import is_rag_disabled, is_reranker_enabled, rerank

    if is_rag_disabled():
        logger.info(
            "[RAG fewshot] item #%d tenant=%s intent=%s → SKIPPED (rag_disabled)",
            item_number, tenant_id, intent,
        )
        return FewshotResult(
            item_number=item_number,
            intent=intent,
            examples=[],
            query_segment=segment_text or "",
            match_reason="rag_disabled",
            total_pool=0,
        )

    engine = _get_engine(tenant_id)

    # ★ 2026-05-08: stratified 가 기본 — 골든셋 원래 컨셉 (contrastive few-shot) 복원.
    # bucket 별 (full/partial/zero) 별도 retrieve + bucket 내부 reranker 로 점수 다양성 보장.
    # similarity-first 회귀가 필요한 경우 (예: 디버깅 / 회귀 비교) 환경변수
    # ``QA_GOLDEN_SET_SIMILARITY_FIRST=1`` 로 폴백 가능.
    use_similarity_first = os.environ.get(
        "QA_GOLDEN_SET_SIMILARITY_FIRST", ""
    ).strip().lower() in {"1", "true", "yes"}

    if use_similarity_first:
        # 레거시 similarity-first 경로 — 기존 동작 보존 (env var 폴백).
        # 사용자 지시 (2026-05-08): fetch_k 모두 10 으로 고정.
        fetch_k = 10
        result = engine.retrieve(item_number, intent, segment_text, top_k=fetch_k)
        if is_reranker_enabled() and len(result.examples) > top_k:
            docs = [
                (ex.segment_text or "") + ("\n" + ex.rationale if ex.rationale else "")
                for ex in result.examples
            ]
            order, ok = rerank(segment_text or "", docs, top_n=top_k)
            # 2026-05-08: rerank() 호출 시점의 provider — UI 가 chip 에 표시.
            from . import get_reranker_provider as _get_provider
            _provider_at_call = _get_provider()
            if order and ok:
                new_examples = []
                for original_idx, score in order:
                    ex = result.examples[original_idx]
                    rater = dict(ex.rater_meta) if ex.rater_meta else {}
                    rater["cohere_rerank_score"] = score
                    rater["reranked"] = True
                    rater["rerank_provider"] = _provider_at_call
                    ex.rater_meta = rater
                    new_examples.append(ex)
                result.examples = new_examples
                result.match_reason = (result.match_reason or "") + " · reranked"
            elif order and not ok:
                # 폴백 — 입력 순서로 잘렸지만 reranked 마킹 안 함. UI 가 "🎯 rr 0.00" 안 보이도록.
                new_examples = [result.examples[original_idx] for original_idx, _ in order]
                result.examples = new_examples
                result.match_reason = (result.match_reason or "") + " · reranker_fallback"
    else:
        # stratified — bucket 별 separate retrieve + bucket 별 reranker (engine 내부에서 처리).
        result = engine.retrieve_stratified(
            item_number, intent, segment_text, top_k=top_k
        )

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
