# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Group A Sub Agent 공용 헬퍼.

책임:
  - Layer 1 preprocessing 에서 필요한 필드 추출 (rule_pre_verdicts/agent_turn_assignments/quality/intent)
  - hybrid 3안 consume 분기 판정 (hard bypass vs LLM verify)
  - 공통 응답 스키마 빌더 (PL 회람 2026-04-20 확정 포맷)
  - snap_score / reconcile_evaluation 호출 wrapper
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Literal

from nodes.skills.reconciler import normalize_fallback_deductions
from v2.contracts.preprocessing import Preprocessing, RulePreVerdict, item_key
from v2.contracts.rubric import snap_score_v2
from v2.schemas.enums import CATEGORY_META, FORCE_T3_ITEMS, CategoryKey, EvaluationMode


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Layer 1 preprocessing 읽기
# ---------------------------------------------------------------------------


def get_rule_pre_verdict(preprocessing: Preprocessing | dict, item_number: int) -> RulePreVerdict | None:
    """preprocessing.rule_pre_verdicts["item_NN"] lookup — 없으면 None."""
    verdicts = (preprocessing or {}).get("rule_pre_verdicts", {})
    return verdicts.get(item_key(item_number))


def get_assigned_turns(
    preprocessing: Preprocessing | dict,
    category_v1_key: Literal["greeting", "understanding", "courtesy", "mandatory"],
) -> dict:
    """V1 호환 `agent_turn_assignments[key]` 추출.

    V2 Sub Agent 명 → V1 카테고리명 매핑 (Dev1 A3 확정):
      greeting        ↔ greeting
      listening_comm  ↔ understanding
      language        ↔ courtesy
      needs           ↔ mandatory
    """
    assignments = (preprocessing or {}).get("agent_turn_assignments", {})
    return assignments.get(category_v1_key, {})


def get_canonical_transcript(preprocessing: Preprocessing | dict) -> str:
    """PII canonical transcript (없으면 빈 문자열)."""
    return (preprocessing or {}).get("canonical_transcript", "") or ""


# ---------------------------------------------------------------------------
# hybrid 3안 consume 분기 (Dev1 합의)
# ---------------------------------------------------------------------------


def should_bypass_llm(rule_verdict: RulePreVerdict | None) -> bool:
    """Rule 1차 판정이 hard + recommended_for_llm_verify=False 이면 LLM bypass."""
    if not rule_verdict:
        return False
    if rule_verdict.get("confidence_mode") != "hard":
        return False
    if rule_verdict.get("recommended_for_llm_verify", True):
        return False
    return True


def is_quality_unevaluable(preprocessing: Preprocessing | dict) -> bool:
    """Layer 1 quality.unevaluable=True 면 Group A 전체 unevaluable 처리."""
    quality = (preprocessing or {}).get("quality", {})
    return bool(quality.get("unevaluable", False))


# ---------------------------------------------------------------------------
# ItemVerdict / SubAgentResponse 빌더 (PL 회람 2026-04-20 포맷)
# ---------------------------------------------------------------------------


# Sub Agent override_hint 허용 값 (PDF 원칙 4 — preamble 지시문 `override_hint` 필드 유효성 검증)
_ALLOWED_OVERRIDE_HINTS: frozenset[str] = frozenset(
    {"profanity", "privacy_leak", "uncorrected_misinfo"}
)


def make_rag_evidence(
    *,
    fewshot: Any = None,
    rag_stdev: float | None = None,
    reasoning_sample_size: int | None = None,
    reasoning_example_ids: list[str] | None = None,
    reasoning_examples: Any = None,
    knowledge_chunk_ids: list[str] | None = None,
    knowledge_chunks: Any = None,
    fewshot_query: str | None = None,
    reasoning_query: str | None = None,
    knowledge_query: str | None = None,
    intent: str | None = None,
) -> dict[str, Any]:
    """RAG hit 메타데이터 dict 빌더 — 프론트 추적 + Layer 4 진단용.

    fewshot 은 두 형태 모두 허용:
      - Group A: `safe_retrieve_fewshot()` 의 list[dict] 반환
      - Group B: `retrieve_fewshot()` 의 FewshotResult 객체 (examples 속성)

    reasoning_examples / knowledge_chunks 가 주어지면 ID 외에 score / rationale / segment_text /
    chunk text 까지 함께 직렬화하여 프론트 drawer 에서 펼쳐 볼 수 있게 한다.
    """
    fewshot_details: list[dict[str, Any]] = []
    fewshot_ids: list[str] = []
    raw_examples: list[Any] = []
    if isinstance(fewshot, list):
        raw_examples = fewshot
    elif fewshot is not None and hasattr(fewshot, "examples"):
        raw_examples = list(getattr(fewshot, "examples", None) or [])

    for ex in raw_examples:
        if isinstance(ex, dict):
            ex_id = ex.get("example_id")
            if not ex_id:
                continue
            fewshot_ids.append(str(ex_id))
            rater = ex.get("rater_meta") or {}
            fewshot_details.append({
                "example_id": str(ex_id),
                "item_number": ex.get("item_number"),
                "score": ex.get("score"),
                "score_bucket": ex.get("score_bucket"),
                "intent": ex.get("intent"),
                "segment_text": (ex.get("segment_text") or "")[:300],
                "rationale": (ex.get("rationale") or "")[:300],
                "rationale_tags": ex.get("rationale_tags") or [],
                "rater_type": rater.get("rater_type"),
                "rater_source": rater.get("source"),
                # similarity = 기존 호환 필드 (RRF score)
                "similarity": rater.get("similarity"),
                # 분리된 진단 점수
                "rrf_score": rater.get("rrf_score"),
                "bm25_score": rater.get("bm25_score"),
                "cosine_score": rater.get("cosine_score"),
                "bm25_rank": rater.get("bm25_rank"),
                "knn_rank": rater.get("knn_rank"),
            })
        else:
            ex_id = getattr(ex, "example_id", None)
            if not ex_id:
                continue
            fewshot_ids.append(str(ex_id))
            rater = getattr(ex, "rater_meta", None) or {}
            rater_dict = rater if isinstance(rater, dict) else {}
            fewshot_details.append({
                "example_id": str(ex_id),
                "item_number": getattr(ex, "item_number", None),
                "score": getattr(ex, "score", None),
                "score_bucket": getattr(ex, "score_bucket", None),
                "intent": getattr(ex, "intent", None),
                "segment_text": (getattr(ex, "segment_text", "") or "")[:300],
                "rationale": (getattr(ex, "rationale", "") or "")[:300],
                "rationale_tags": getattr(ex, "rationale_tags", None) or [],
                "rater_type": rater_dict.get("rater_type"),
                "rater_source": rater_dict.get("source"),
                "similarity": rater_dict.get("similarity"),
                "rrf_score": rater_dict.get("rrf_score"),
                "bm25_score": rater_dict.get("bm25_score"),
                "cosine_score": rater_dict.get("cosine_score"),
                "bm25_rank": rater_dict.get("bm25_rank"),
                "knn_rank": rater_dict.get("knn_rank"),
            })

    reasoning_details: list[dict[str, Any]] = []
    if reasoning_examples:
        for ex in reasoning_examples:
            ex_id = getattr(ex, "example_id", None) if not isinstance(ex, dict) else ex.get("example_id")
            if not ex_id:
                continue
            score_val = getattr(ex, "score", None) if not isinstance(ex, dict) else ex.get("score")
            rationale = (getattr(ex, "rationale", "") if not isinstance(ex, dict) else ex.get("rationale", "")) or ""
            tags = (getattr(ex, "rationale_tags", None) if not isinstance(ex, dict) else ex.get("rationale_tags")) or []
            rater = getattr(ex, "rater_meta", None) if not isinstance(ex, dict) else ex.get("rater_meta", {})
            rater = rater or {}
            quote_example = rater.get("quote_example", "") if isinstance(rater, dict) else ""
            evaluator_id = rater.get("evaluator_id", "") if isinstance(rater, dict) else ""
            item_num = getattr(ex, "item_number", None) if not isinstance(ex, dict) else ex.get("item_number")
            similarity = rater.get("similarity") if isinstance(rater, dict) else None
            rater_dict_r = rater if isinstance(rater, dict) else {}
            reasoning_details.append({
                "example_id": str(ex_id),
                "item_number": item_num,
                "score": score_val,
                "rationale": rationale[:300],
                "rationale_tags": tags,
                "quote_example": (quote_example or "")[:300],
                "evaluator_id": evaluator_id,
                "similarity": similarity,
                "rrf_score": rater_dict_r.get("rrf_score"),
                "bm25_score": rater_dict_r.get("bm25_score"),
                "cosine_score": rater_dict_r.get("cosine_score"),
                "bm25_rank": rater_dict_r.get("bm25_rank"),
                "knn_rank": rater_dict_r.get("knn_rank"),
            })

    knowledge_details: list[dict[str, Any]] = []
    if knowledge_chunks:
        for ch in knowledge_chunks:
            cid = getattr(ch, "chunk_id", None) if not isinstance(ch, dict) else ch.get("chunk_id")
            if not cid:
                continue
            knowledge_details.append({
                "chunk_id": str(cid),
                "score": getattr(ch, "score", None) if not isinstance(ch, dict) else ch.get("score"),
                "tags": getattr(ch, "tags", None) if not isinstance(ch, dict) else ch.get("tags") or [],
                "source_ref": getattr(ch, "source_ref", None) if not isinstance(ch, dict) else ch.get("source_ref"),
                "text": ((getattr(ch, "text", "") if not isinstance(ch, dict) else ch.get("text", "")) or "")[:400],
            })

    def _trim(s: str | None, n: int = 2000) -> str | None:
        # query 표시용 — 5~10턴 segment_text 가 자연스럽게 1000자+ 이므로 220 → 2000 상향.
        # frontend RagHitsPanel 의 query 박스는 maxHeight=180 + overflowY=auto 라 길어도 스크롤로 OK.
        if not s:
            return None
        s = str(s)
        return (s[:n] + "…") if len(s) > n else s

    return {
        "intent": intent,
        "fewshot_query": _trim(fewshot_query, 2000),
        "fewshot_ids": fewshot_ids,
        "fewshot_details": fewshot_details,
        "reasoning_query": _trim(reasoning_query, 2000),
        "reasoning_stdev": float(rag_stdev) if rag_stdev is not None else None,
        "reasoning_sample_size": reasoning_sample_size,
        "reasoning_example_ids": reasoning_example_ids or [],
        "reasoning_details": reasoning_details,
        "knowledge_query": _trim(knowledge_query, 2000),
        "knowledge_chunk_ids": knowledge_chunk_ids or [getattr(c, "chunk_id", "") if not isinstance(c, dict) else c.get("chunk_id", "") for c in (knowledge_chunks or [])],
        "knowledge_details": knowledge_details,
    }


def build_item_verdict(
    *,
    item_number: int,
    item_name: str,
    max_score: int,
    raw_score: int,
    evaluation_mode: EvaluationMode,
    judgment: str,
    evidence: list[dict[str, Any]],
    llm_self: int,
    rule_verdict: RulePreVerdict | None,
    rag_stdev: float | None = None,
    evidence_quality: Literal["high", "medium", "low"] = "medium",
    flag: str | None = None,
    mode_reason: str | None = None,
    override_hint: str | None = None,
    rag_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """PL 회람 확정 ItemVerdict dict 생성 + snap_score + force_t3 + mandatory_human_review 자동 세팅.

    Evidence 정책 (PL 2026-04-20 재공지, 원칙 3 강제):
      - evaluation_mode ∈ {full, structural_only, compliance_based} + evidence=[] →
        mode 자동 다운그레이드 to `partial_with_review` + mode_reason 기록
      - evaluation_mode ∈ {skipped, unevaluable, partial_with_review} 만 evidence=[] 허용

    override_hint (PDF 원칙 4, preamble 체크리스트 #6):
      - Sub Agent 가 LLM 맥락 판정에서 불친절·개인정보 유출·오안내 미정정 감지 시 기재.
      - 허용 값: "profanity" / "privacy_leak" / "uncorrected_misinfo" / None.
      - Layer 3 Override 가 Layer 1 Rule 트리거 부재 시 보조 시그널로 consume.
    """
    # override_hint 유효성 검증 — 허용 값 외는 None 으로 정화
    if override_hint is not None and override_hint not in _ALLOWED_OVERRIDE_HINTS:
        logger.warning(
            "item #%s: invalid override_hint=%r 무시 (allowed=%s)",
            item_number, override_hint, sorted(_ALLOWED_OVERRIDE_HINTS),
        )
        override_hint = None

    # ──────────────────────────────────────────────────────────────────
    # LLM 응답 누락 시 Rule 우선 폴백 (PL 2026-04-20)
    # ──────────────────────────────────────────────────────────────────
    # LLM 이 잘못된 item_number 응답 / score 키 부재 등으로 raw_score=0 + judgment 빈
    # 케이스에서, Rule pre-verdict 가 hard 모드 (높은 신뢰도) + 점수 ≥ raw_score 이면
    # Rule 점수를 채택. (예: #5 LLM=0 vs Rule=5 hard → Rule 5 채택)
    rule_score_val = None
    rule_conf_mode = None
    if rule_verdict is not None and rule_verdict.get("score") is not None:
        try:
            rule_score_val = int(rule_verdict["score"])
        except (TypeError, ValueError):
            rule_score_val = None
        rule_conf_mode = rule_verdict.get("confidence_mode")

    # LLM 응답 누락 신호: judgment 가 비어있고 raw_score=0 이면 LLM 이 답을 못한 것.
    # evidence 는 _normalize_evidence 가 rule 폴백으로 채울 수 있으니 신호에서 제외.
    llm_data_empty = (not judgment) and (raw_score in (None, 0))
    if (llm_data_empty and rule_score_val is not None and rule_score_val > 0
            and rule_conf_mode == "hard"):
        logger.warning(
            "item #%s: LLM 응답 누락 (judgment 빈/score 0) — Rule(hard, %d) 폴백 채택",
            item_number, rule_score_val,
        )
        raw_score = rule_score_val
        judgment = f"LLM 응답 누락 — Rule 판정({rule_verdict.get('rationale', '')}) 채택"
        if not evidence:
            evidence = rule_evidence_to_evidence_quote(rule_verdict)
        mode_reason = (mode_reason or
                       f"Rule(hard, {rule_score_val}) 우선 채택 — LLM 응답 누락")

    # snap_score_v2 강제 (V2 ALLOWED_STEPS — #17/#18=[5,3,0] 복원 반영, #3=[5] 만점 고정)
    if raw_score is None:
        score = 0 if evaluation_mode == "unevaluable" else max_score
    else:
        score = snap_score_v2(item_number, int(raw_score))

    # Evidence 강제 검증 — full/structural_only/compliance_based 는 evidence≥1 필요
    evidence_required_modes = {"full", "structural_only", "compliance_based"}
    if evaluation_mode in evidence_required_modes and not evidence:
        downgrade_note = (
            f"evidence 공백으로 mode 자동 다운그레이드 ({evaluation_mode} → partial_with_review) "
            "— 원칙 3 강제 (Dev5 pydantic 검증 정합)"
        )
        logger.warning("item #%s: %s", item_number, downgrade_note)
        evaluation_mode = "partial_with_review"
        mode_reason = mode_reason or downgrade_note

    rule_llm_agreement: bool | None = None
    if rule_verdict is not None and rule_verdict.get("score") is not None:
        rule_llm_agreement = int(rule_verdict["score"]) == score

    mandatory_human_review = (
        evaluation_mode in ("unevaluable", "partial_with_review") or llm_self <= 2
    )

    signals: dict[str, Any] = {
        "llm_self": int(llm_self),
        "evidence_quality": evidence_quality,
    }
    if rule_llm_agreement is not None:
        signals["rule_llm_agreement"] = rule_llm_agreement
    if rag_stdev is not None:
        signals["rag_stdev"] = float(rag_stdev)

    verdict: dict[str, Any] = {
        "item": item_name,
        "item_number": item_number,
        "max_score": max_score,
        "score": score,
        "evaluation_mode": evaluation_mode,
        "judgment": judgment,
        "evidence": evidence,
        "confidence": {"final": int(llm_self), "signals": signals},
        "flag": flag,
        "mandatory_human_review": mandatory_human_review,
        "force_t3": item_number in FORCE_T3_ITEMS,
        "override_hint": override_hint,
    }
    if mode_reason:
        verdict["mode_reason"] = mode_reason
    if rag_evidence is not None:
        verdict["rag_evidence"] = rag_evidence
    return verdict


def build_sub_agent_response(
    *,
    category_key: CategoryKey,
    agent_id: str,
    items: list[dict[str, Any]],
    status: Literal["success", "partial", "error"] = "success",
    llm_backend: str | None = None,
    elapsed_ms: int | None = None,
    error_message: str | None = None,
    flags: dict | None = None,
    wiki_updates: dict | None = None,
    deduction_log: list[dict] | None = None,
) -> dict[str, Any]:
    """PL 확정 SubAgentResponse dict 생성 — 인프라 폴백 정화 후 category_score 집계.

    Group A 는 감점 사유를 `judgment` + `evidence` 로 흡수하고 `score` 는 이미
    `snap_score_v2` 를 거쳤으므로 V1 `reconcile_evaluation` 의 산술 강제 보정
    (score + Σ points == max_score) 은 적용하지 않는다. 다만 `DeductionEntry` 가
    채워지는 항목에 한해 인프라 폴백 감점(`[SKIPPED_INFRA]`) 정화만 수행.
    """

    normalized_items: list[dict[str, Any]] = []
    for it in items:
        deductions = it.get("deductions") or []
        if deductions:
            cleaned, skipped_count, _ = normalize_fallback_deductions(deductions)
            if skipped_count > 0:
                logger.info(
                    "[reconcile-infra] item #%s: %d fallback deductions nullified",
                    it.get("item_number"), skipped_count,
                )
                it = {**it, "deductions": cleaned}
        normalized_items.append(it)

    # category_score 집계 규칙: unevaluable 항목은 0 합산 (Dev5 O4 확정 전 기본값)
    achieved = sum(int(it.get("score") or 0) for it in normalized_items)

    # override_hints 수집 — Layer 3 Override 가 Layer 1 Rule 트리거 부재 시 보조 consume
    override_hints: list[dict[str, Any]] = [
        {"item_number": it.get("item_number"), "hint": it.get("override_hint")}
        for it in normalized_items
        if it.get("override_hint")
    ]

    meta = CATEGORY_META[category_key]
    return {
        "category": meta["label_ko"],
        "category_key": category_key,
        "max_score": meta["max_score"],
        "achieved_score": achieved,
        "items": normalized_items,
        "status": status,
        "agent_id": agent_id,
        "llm_backend": llm_backend,
        "elapsed_ms": elapsed_ms,
        "error_message": error_message,
        "flags": flags,
        "wiki_updates": wiki_updates,
        "deduction_log": deduction_log,
        "override_hints": override_hints,
    }


# ---------------------------------------------------------------------------
# 타이머
# ---------------------------------------------------------------------------


class Stopwatch:
    """elapsed_ms 측정용 간이 timer. 진입 시점에 elapsed_ms=0 초기화하여
    early-return 경로(context 내부에서 `sw.elapsed_ms` 참조)에도 안전."""

    elapsed_ms: int = 0

    def __enter__(self) -> Stopwatch:
        self._t0 = time.perf_counter()
        self.elapsed_ms = 0
        return self

    @property
    def current_ms(self) -> int:
        return int((time.perf_counter() - self._t0) * 1000)

    def __exit__(self, *_exc: object) -> None:
        self.elapsed_ms = self.current_ms


# ---------------------------------------------------------------------------
# Evidence helper
# ---------------------------------------------------------------------------


def rule_evidence_to_evidence_quote(rule_verdict: RulePreVerdict | None) -> list[dict[str, Any]]:
    """Rule pre_verdict 의 evidence_turn_ids + evidence_snippets 를 EvidenceQuote 배열로 변환."""
    if not rule_verdict:
        return []
    turn_ids = rule_verdict.get("evidence_turn_ids") or []
    snippets = rule_verdict.get("evidence_snippets") or []
    out: list[dict[str, Any]] = []
    for i, tid in enumerate(turn_ids):
        quote = snippets[i] if i < len(snippets) else ""
        out.append({"speaker": "상담사", "timestamp": None, "quote": quote, "turn_id": int(tid)})
    return out


# ---------------------------------------------------------------------------
# RAG 통합 헬퍼 (Dev4 API)
# ---------------------------------------------------------------------------


def safe_retrieve_fewshot(
    item_number: int,
    intent: str,
    segment_text: str,
    *,
    tenant_id: str = "generic",
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """`retrieve_fewshot` 호출 + 실패/빈 결과 시 빈 리스트 반환 (degrade 없이 동작)."""
    if not segment_text or not segment_text.strip():
        return []
    try:
        from v2.rag import retrieve_fewshot
        result = retrieve_fewshot(
            item_number=item_number, intent=intent or "general_inquiry",
            segment_text=segment_text, tenant_id=tenant_id, top_k=top_k,
        )
        examples = list(result.examples or [])
        if examples:
            logger.info(
                "[RAG fewshot] item #%d tenant=%s intent=%s → %d hits (ids=%s, scores=%s)",
                item_number, tenant_id, intent or "general_inquiry", len(examples),
                [ex.example_id for ex in examples[:5]],
                [ex.score for ex in examples[:5]],
            )
        else:
            logger.info(
                "[RAG fewshot] item #%d tenant=%s intent=%s → 0 hits (segment_len=%d)",
                item_number, tenant_id, intent or "general_inquiry", len(segment_text),
            )
        return [
            {
                "example_id": ex.example_id,
                "item_number": ex.item_number,
                "score": ex.score, "score_bucket": ex.score_bucket,
                "intent": ex.intent,
                "segment_text": ex.segment_text, "rationale": ex.rationale,
                "rationale_tags": ex.rationale_tags,
                "rater_meta": ex.rater_meta or {},
            }
            for ex in examples
        ]
    except Exception as e:
        logger.info("[RAG fewshot] unavailable for item #%s tenant=%s: %s", item_number, tenant_id, e)
        return []


def extract_item_from_llm_result(result: Any, item_number: int) -> dict[str, Any]:
    """LLM 응답에서 특정 item_number 의 객체 추출.

    프롬프트가 `{"items": [{"item_number": N, ...}, ...]}` 형식으로 응답하면
    해당 item 의 dict 를 반환. 단일 객체로 응답하면 그대로 반환.
    `{"item_number": N, ...}` 가 최상위면 N 일치 시 반환, 불일치 시 빈 dict.
    """
    if not isinstance(result, dict):
        return {}
    # Case 1: {"items": [{...}, {...}]}
    items = result.get("items")
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and it.get("item_number") == item_number:
                return it
        # 단일 객체 + item_number 미지정인 경우만 폴백 채택.
        # 다른 item_number (예: #5 요청에 #3 응답) 이면 빈 dict — build_item_verdict 의
        # rule_verdict 폴백 경로로 전환되어 Rule 점수가 살아남음.
        if (len(items) == 1 and isinstance(items[0], dict)
                and items[0].get("item_number") is None
                and "score" in items[0]):
            return items[0]
        logger.warning(
            "extract_item_from_llm_result: items[] 에서 item_number=%d 매칭 실패. 받은 번호: %s",
            item_number, [it.get("item_number") for it in items if isinstance(it, dict)],
        )
        return {}
    # Case 2: 단일 객체 — item_number 매칭 또는 score 키 존재
    if "score" in result or result.get("item_number") == item_number:
        return result
    return {}


def safe_retrieve_reasoning_evidence(
    item_number: int, transcript_slice: str, *, top_k: int = 10,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    """`retrieve_reasoning` 결과 → {stdev, sample_size, example_ids, examples}.

    실패/빈 결과 시 stdev=None, sample_size=0, example_ids=[], examples=[].
    프론트 drawer 가 rationale/score 펼치기 위해 examples 까지 노출.
    """
    if not transcript_slice or not transcript_slice.strip():
        return {"stdev": None, "sample_size": 0, "example_ids": [], "examples": []}
    try:
        from v2.rag import retrieve_reasoning
        result = retrieve_reasoning(
            item_number=item_number, transcript_slice=transcript_slice,
            tenant_id=tenant_id, top_k=top_k,
        )
        examples = list(result.examples or [])
        ex_ids = [str(getattr(ex, "example_id", "")) for ex in examples]
        ex_ids = [eid for eid in ex_ids if eid]
        return {
            "stdev": float(result.stdev) if result.sample_size > 0 else None,
            "sample_size": int(result.sample_size or 0),
            "example_ids": ex_ids,
            "examples": examples,
        }
    except Exception as e:
        logger.info("[RAG reasoning evidence] unavailable item #%s tenant=%s: %s", item_number, tenant_id, e)
        return {"stdev": None, "sample_size": 0, "example_ids": [], "examples": []}


def safe_retrieve_reasoning_stdev(
    item_number: int, transcript_slice: str, *, top_k: int = 10,
    tenant_id: str = "generic",
) -> float | None:
    """`retrieve_reasoning` stdev 추출 — confidence.signals.rag_stdev 용.

    Dev4 주의: "점수 산출에 사용 금지, stdev 만 confidence 지표로".
    """
    if not transcript_slice or not transcript_slice.strip():
        return None
    try:
        from v2.rag import retrieve_reasoning
        result = retrieve_reasoning(
            item_number=item_number, transcript_slice=transcript_slice,
            tenant_id=tenant_id, top_k=top_k,
        )
        if result.sample_size > 0:
            stdev_val = float(result.stdev)
            mean_val = getattr(result, "mean", None)
            logger.info(
                "[RAG reasoning] item #%d tenant=%s → n=%d stdev=%.3f mean=%s",
                item_number, tenant_id, result.sample_size, stdev_val,
                f"{mean_val:.2f}" if mean_val is not None else "N/A",
            )
            return stdev_val
        logger.info(
            "[RAG reasoning] item #%d tenant=%s → 0 hits (slice_len=%d)",
            item_number, tenant_id, len(transcript_slice),
        )
        return None
    except Exception as e:
        logger.info("[RAG reasoning] unavailable for item #%s tenant=%s: %s", item_number, tenant_id, e)
        return None


async def async_safe_retrieve_fewshot(
    item_number: int,
    intent: str,
    segment_text: str,
    *,
    tenant_id: str = "generic",
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """sync `safe_retrieve_fewshot` 를 thread pool 로 오프로드 — 이벤트 루프 블로킹 방지."""
    return await asyncio.to_thread(
        safe_retrieve_fewshot, item_number, intent, segment_text,
        tenant_id=tenant_id, top_k=top_k,
    )


async def async_safe_retrieve_reasoning_evidence(
    item_number: int,
    transcript_slice: str,
    *,
    top_k: int = 10,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    """sync `safe_retrieve_reasoning_evidence` 를 thread pool 로 오프로드."""
    return await asyncio.to_thread(
        safe_retrieve_reasoning_evidence, item_number, transcript_slice,
        top_k=top_k, tenant_id=tenant_id,
    )


async def async_safe_retrieve_reasoning_stdev(
    item_number: int,
    transcript_slice: str,
    *,
    top_k: int = 10,
    tenant_id: str = "generic",
) -> float | None:
    """sync `safe_retrieve_reasoning_stdev` 를 thread pool 로 오프로드."""
    return await asyncio.to_thread(
        safe_retrieve_reasoning_stdev, item_number, transcript_slice,
        top_k=top_k, tenant_id=tenant_id,
    )


def format_fewshot_block(examples: list[dict[str, Any]]) -> str:
    """Few-shot examples 를 LLM 프롬프트용 Korean 블록으로 포매팅. 빈 리스트면 빈 문자열.

    **활용 규칙** 블록을 서두에 추가 — LLM 이 예시를 "참고 자료" 가 아닌 "판정 기준"
    으로 사용하도록 명시. rubric 의 단계별 정의는 아래 예시로 구체화된다.
    """
    if not examples:
        return ""
    parts = [
        "## Few-shot 예시 (golden-set) — 판정 기준 (필수 준수)",
        "",
        "**활용 규칙**:",
        "1. 아래 예시는 **사람 평가자가 동일 항목에 대해 실제 판정한 케이스** 이다.",
        "2. 평가 대상 발화가 아래 예시 중 하나와 **매우 유사하면 해당 예시의 점수를 우선 채택**.",
        "3. 예시의 사람 평가자 사유(rationale) 와 본인 판단이 상충하면 **사람 평가자 판단을 존중**.",
        "4. Rubric 의 각 단계 정의는 아래 예시로 구체화된다 — 예시 없이 rubric 만으로 판정 금지.",
        "5. 예시의 score_bucket(full/partial/zero) 과 rationale 패턴을 매칭해 동일 bucket 채택.",
    ]
    for i, ex in enumerate(examples, 1):
        parts.append(
            f"\n### 예시 {i} — score={ex.get('score')} ({ex.get('score_bucket')})"
            f"\n발화: {ex.get('segment_text', '')}"
            f"\n사람 평가자 사유: {ex.get('rationale', '')}"
        )
    parts.append("\n---\n")
    return "\n".join(parts)


def get_intent(preprocessing: Preprocessing | dict) -> str:
    """preprocessing.intent_type — 없으면 'general_inquiry'."""
    return (preprocessing or {}).get("intent_type") or "general_inquiry"


# ---------------------------------------------------------------------------
# Evidence 강제 instruction (PL 2026-04-20 재공지, 원칙 3)
# ---------------------------------------------------------------------------

EVIDENCE_INSTRUCTION = (
    "\n## Evidence 강제 규칙 (원칙 3 — 최우선)\n"
    "- `evaluation_mode=full / structural_only / compliance_based` 인 경우 "
    "`evidence` 배열에 **최소 1개 필수** (speaker/timestamp/quote/turn_id).\n"
    "- Evidence 없으면 5점도 부여 금지.\n"
    "- Evidence 생략이 필요한 경우 `evaluation_mode=partial_with_review` 또는 "
    "`skipped` 로 다운그레이드하고 `mode_reason` 을 기록.\n"
    "- `evaluation_mode=skipped/unevaluable/partial_with_review` 만 `evidence=[]` 허용.\n"
)


# ---------------------------------------------------------------------------
# 3-Persona 앙상블 + 판사형 하이브리드 머지 (Phase 5, 2026-04-21)
# ---------------------------------------------------------------------------


async def run_persona_ensemble(
    *,
    item_number: int,
    item_name: str,
    system_prompt: str,
    user_message: str,
    transcript_slice: str,
    llm_backend: str | None,
    bedrock_model_id: str | None,
    max_tokens: int = 2048,
    temperature: float = 0.1,
    single_persona_only: bool = False,
) -> dict[str, Any]:
    """Strict / Neutral / Loose 3 persona 병렬 호출 + 하이브리드 머지.

    Returns a dict with:
      - hybrid              : reconcile_hybrid() 반환값 (final_score, persona_votes,
                              step_spread, confidence, mandatory_human_review,
                              merge_path, merge_rule, judge_reasoning, override_hint,
                              persona_label_map)
      - representative      : dict — neutral (없으면 첫 가용 persona) 의 raw 응답.
                              judgment / evidence / deductions / self_confidence /
                              override_hint 추출에 사용.
      - persona_outputs     : dict[str, dict | None] — 진단용 원본 (실패 key 누락)

    Raises
    ------
    LLMTimeoutError
        3 persona 중 하나라도 타임아웃. 파이프라인 중단 시그널 (CLAUDE.md 규약).
    RuntimeError
        3 persona 모두 LLM 실패 시 (상위에서 rule fallback 로 전환해야 함).
    """
    from nodes.llm import LLMTimeoutError, get_chat_model, invoke_and_parse
    from v2.judge_agent import reconcile_hybrid
    from v2.reconciler_personas import PERSONAS, build_messages_with_persona, force_single_persona

    # QA_FORCE_SINGLE_PERSONA env 가 켜져 있으면 무조건 single 모드로 전환
    if force_single_persona():
        single_persona_only = True

    llm = get_chat_model(
        temperature=temperature,
        max_tokens=max_tokens,
        backend=llm_backend,
        bedrock_model_id=bedrock_model_id,
    )

    async def _call_persona(persona: str) -> dict[str, Any] | None:
        msgs = build_messages_with_persona(
            system_prompt=system_prompt,
            user_message=user_message,
            persona=persona,
        )
        try:
            raw = await invoke_and_parse(llm, msgs)
            if not isinstance(raw, dict):
                logger.warning(
                    "persona=%s item #%d LLM 응답 dict 아님: %r",
                    persona, item_number, type(raw).__name__,
                )
                return None
            target = extract_item_from_llm_result(raw, item_number)
            # target 빈 dict → item_number 불일치. 단일 score 가 raw 에 있으면 raw 사용
            if not target and "score" in raw:
                target = raw
            if not target or "score" not in target:
                logger.warning(
                    "persona=%s item #%d 응답에 score 필드 없음 — 실패 처리",
                    persona, item_number,
                )
                return None
            return target
        except LLMTimeoutError:
            raise  # 상위 전파 — 파이프라인 중단 시그널
        except Exception as e:
            logger.warning(
                "persona=%s item #%d LLM 실패: %s", persona, item_number, e,
            )
            return None

    # ── single_persona_only=True: neutral 1회만 호출 (객관적/구조적 항목용 — 예: #1/#2)
    if single_persona_only:
        neutral_result = await _call_persona("neutral")
        if neutral_result is None:
            raise RuntimeError(f"item #{item_number} neutral persona 실패 (single mode)")
        from v2.contracts.rubric import snap_score_v2
        snapped = snap_score_v2(item_number, int(neutral_result.get("score", 0) or 0))
        self_conf = neutral_result.get("self_confidence")
        try:
            conf_int = int(self_conf) if self_conf is not None else 4
        except (TypeError, ValueError):
            conf_int = 4
        hybrid = {
            "final_score": snapped,
            "persona_votes": {"neutral": snapped},
            "step_spread": 0,
            "confidence": max(1, min(5, conf_int)),
            "mandatory_human_review": False,
            "merge_path": "single",
            "merge_rule": "single",
            "judge_reasoning": None,
            "override_hint": neutral_result.get("override_hint"),
            "persona_label_map": None,
        }
        return {
            "hybrid": hybrid,
            "representative": neutral_result,
            "persona_outputs": {"neutral": neutral_result},
        }

    # return_exceptions=False — LLMTimeoutError 는 바로 전파
    results = await asyncio.gather(
        _call_persona("strict"),
        _call_persona("neutral"),
        _call_persona("loose"),
        return_exceptions=False,
    )
    persona_outputs: dict[str, dict[str, Any]] = {
        p: r for p, r in zip(PERSONAS, results) if r is not None
    }

    if not persona_outputs:
        raise RuntimeError(f"item #{item_number} 3 persona 모두 실패")

    # neutral 우선 대표 채택 (편향 최소) — 없으면 첫 가용 persona
    representative = (
        persona_outputs.get("neutral")
        or next(iter(persona_outputs.values()))
    )

    hybrid = await reconcile_hybrid(
        item_number=item_number,
        item_name=item_name,
        transcript_slice=(transcript_slice or "")[:2500],
        persona_outputs=persona_outputs,
        llm_backend=llm_backend or "bedrock",
        bedrock_model_id=bedrock_model_id,
    )

    return {
        "hybrid": hybrid,
        "representative": representative,
        "persona_outputs": persona_outputs,
    }


def attach_persona_meta(
    verdict: dict[str, Any],
    hybrid: dict[str, Any],
    persona_outputs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """ItemVerdict dict 에 persona/judge 메타 필드 주입 (sub_agent_io.py 스키마 준수).

    `persona_votes` / `persona_step_spread` / `persona_merge_path` /
    `persona_merge_rule` / `judge_reasoning` / `persona_details` 필드를 verdict 에 설정하고,
    confidence.signals 에도 진단 필드를 추가한다.

    Parameters
    ----------
    persona_outputs : dict[str, dict] | None
        각 persona 의 원본 LLM 응답. 있으면 `persona_details` 필드에 정제해서 저장
        (UI 에서 persona 별 judgment / deductions / evidence 비교 표시용).
    """
    verdict["persona_votes"] = hybrid.get("persona_votes") or {}
    verdict["persona_step_spread"] = int(hybrid.get("step_spread") or 0)
    verdict["persona_merge_path"] = hybrid.get("merge_path") or "stats"
    verdict["persona_merge_rule"] = hybrid.get("merge_rule")
    if hybrid.get("judge_reasoning"):
        verdict["judge_reasoning"] = hybrid["judge_reasoning"]
    if persona_outputs:
        verdict["persona_details"] = _extract_persona_details(persona_outputs)
    # confidence.signals 에도 요약 반영 — Layer 4 진단용
    conf = verdict.get("confidence") or {}
    signals = conf.get("signals") or {}
    signals["persona_step_spread"] = int(hybrid.get("step_spread") or 0)
    signals["persona_merge_path"] = hybrid.get("merge_path") or "stats"
    conf["signals"] = signals
    verdict["confidence"] = conf
    return verdict


def _extract_persona_details(
    persona_outputs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """persona_outputs 에서 UI 표시용 필드만 정제 추출.

    저장 용량 절감 위해 judgment/summary 400자, deductions 5개, evidence 3개로 제한.
    """
    details: dict[str, dict[str, Any]] = {}
    for persona, raw in persona_outputs.items():
        if not raw or not isinstance(raw, dict):
            continue
        judgment = raw.get("judgment") or raw.get("summary") or ""
        deductions = raw.get("deductions") or []
        evidence = raw.get("evidence") or []
        details[persona] = {
            "score": raw.get("score"),
            "judgment": str(judgment)[:400],
            "summary": str(raw.get("summary") or "")[:400],
            "deductions": [
                {
                    "reason": str(d.get("reason", ""))[:200],
                    "points": d.get("points"),
                }
                for d in (deductions[:5] if isinstance(deductions, list) else [])
                if isinstance(d, dict)
            ],
            "evidence": [
                {
                    "speaker": e.get("speaker"),
                    "quote": str(e.get("quote") or e.get("text") or "")[:200],
                    "turn_id": e.get("turn_id") or e.get("turn"),
                }
                for e in (evidence[:3] if isinstance(evidence, list) else [])
                if isinstance(e, dict)
            ],
            "self_confidence": raw.get("self_confidence"),
            "override_hint": raw.get("override_hint"),
        }
    return details
