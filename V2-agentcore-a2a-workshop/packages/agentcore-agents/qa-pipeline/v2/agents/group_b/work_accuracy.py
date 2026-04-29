# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""업무정확도 Sub Agent — #15 정확한 안내 (15점) + #16 필수 안내 이행 (5점).

Phase D2 이후 실 Bedrock 경로:
- #15: `retrieve_knowledge` → unevaluable=True 시 분기. 그 외엔 RAG chunks 를
  프롬프트 `## 업무지식 RAG hits` 섹션으로 주입하여 LLM 사실일치 판정.
- #16: Intent + mandatory_scripts + Few-shot 주입.
- `accuracy_verdict` 생성 — Layer 3 Override 입력 (Dev1 합의 필드).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from v2.agents.group_a._shared import make_rag_evidence
from v2.agents.group_b._llm import LLMTimeoutError, call_bedrock_json, load_group_b_prompt
from v2.agents.group_b.base import (
    ITEM_MAX_SCORE,
    ITEM_NAMES_KO,
    build_sub_agent_response,
    compare_with_rule_pre_verdict,
    convert_llm_raw_to_item_verdict,
    extract_wiki_updates,
    make_item_verdict,
    make_llm_self_confidence,
)
from v2.judge_agent import reconcile_hybrid
from v2.rag import retrieve_fewshot, retrieve_knowledge
from v2.rag.types import RAGError
from v2.reconciler_personas import PERSONAS, apply_persona_prefix
from v2.schemas.enums import CATEGORY_META
from v2.schemas.sub_agent_io import EvidenceQuote, ItemVerdict, SubAgentResponse


logger = logging.getLogger(__name__)


# work_accuracy 카테고리 전체 item 번호 (설계서: 15, 16)
# PDF §5.2 (p11) Override 정책: "오안내 후 미정정 → 업무 정확도 대분류 전체 0점"
_WORK_ACCURACY_ITEMS: list[int] = list(CATEGORY_META["work_accuracy"]["items"])


async def work_accuracy_agent(
    *,
    transcript: str,
    assigned_turns: list[dict],
    consultation_type: str,
    intent_summary: dict | None = None,
    rule_pre_verdicts: dict | None = None,
    preprocessing: dict | None = None,
    tenant_id: str = "generic",
    llm_backend: str = "bedrock",
    bedrock_model_id: str | None = None,
) -> tuple[SubAgentResponse, dict[str, Any]]:
    """#15, #16 평가."""
    verdicts_bundle = (rule_pre_verdicts or {}).get("verdicts") or {}
    intent_summary = intent_summary or {}
    primary_intent = intent_summary.get("primary_intent") or "*"
    product = intent_summary.get("product")

    # #15 RAG 조회 + #16 Few-shot 을 병렬 (asyncio.to_thread 로 sync opensearch 호출을 thread pool 로 오프로드)
    query_text = _build_rag_query(transcript, primary_intent, product)
    segment_text = _build_segment_text(assigned_turns, fallback=transcript)
    knowledge, fewshot_16 = await asyncio.gather(
        asyncio.to_thread(retrieve_knowledge, intent=primary_intent, query=query_text, tenant_id=tenant_id, top_k=3),
        asyncio.to_thread(_safe_fewshot, 16, primary_intent, segment_text, tenant_id),
    )

    # #15 분기 결정
    knowledge_chunks = list(getattr(knowledge, "chunks", None) or [])
    knowledge_chunk_ids = [getattr(c, "chunk_id", "") for c in knowledge_chunks]
    rag_evidence_15 = make_rag_evidence(
        knowledge_chunk_ids=knowledge_chunk_ids,
        knowledge_chunks=knowledge_chunks,
        knowledge_query=query_text,
        intent=primary_intent,
    )
    rag_evidence_16 = make_rag_evidence(fewshot=fewshot_16, fewshot_query=segment_text, intent=primary_intent)

    if knowledge.unevaluable:
        # unevaluable 경로 — LLM 호출 skip
        item_15, accuracy_verdict = _unevaluable_item_15(knowledge, verdicts_bundle)
        if rag_evidence_15:
            item_15["rag_evidence"] = rag_evidence_15  # type: ignore[typeddict-unknown-key]
        # #16 은 단독 LLM 호출 (3-persona + hybrid)
        gather_result = await asyncio.gather(
            _evaluate_item_16(
                transcript=transcript,
                assigned_turns=assigned_turns,
                consultation_type=consultation_type,
                fewshot=fewshot_16,
                segment_text=segment_text,
                llm_backend=llm_backend,
                bedrock_model_id=bedrock_model_id,
            ),
            return_exceptions=True,
        )
        raw_16 = _fallback_result(16, gather_result[0])
        item_16 = convert_llm_raw_to_item_verdict(
            item_number=16,
            raw=raw_16,
            assigned_turns=assigned_turns,
            verdicts_bundle=verdicts_bundle,
            rag_evidence=rag_evidence_16,
        )
        _inject_hybrid_fields(item_16, raw_16)
    else:
        # 정상 경로 — #15/#16 병렬 LLM (각각 3-persona + hybrid)
        gather_result = await asyncio.gather(
            _evaluate_item_15(
                transcript=transcript,
                assigned_turns=assigned_turns,
                consultation_type=consultation_type,
                knowledge=knowledge,
                segment_text=segment_text,
                llm_backend=llm_backend,
                bedrock_model_id=bedrock_model_id,
            ),
            _evaluate_item_16(
                transcript=transcript,
                assigned_turns=assigned_turns,
                consultation_type=consultation_type,
                fewshot=fewshot_16,
                segment_text=segment_text,
                llm_backend=llm_backend,
                bedrock_model_id=bedrock_model_id,
            ),
            return_exceptions=True,
        )
        timeouts = [r for r in gather_result if isinstance(r, LLMTimeoutError)]
        if len(timeouts) == 2:
            raise timeouts[0]
        raw_15 = _fallback_result(15, gather_result[0])
        raw_16 = _fallback_result(16, gather_result[1])
        # #15 는 RAG chunks 를 evidence 로 보강 + partial_with_review 모드 강제
        raw_15 = _enrich_with_rag_evidence(raw_15, knowledge)
        accuracy_verdict = _build_accuracy_verdict(raw_15, knowledge)
        # 오안내 미정정 탐지 시 override_hint 자동 주입 (preamble 체크리스트 #6)
        if accuracy_verdict.get("has_incorrect_guidance") and not accuracy_verdict.get("correction_attempted", True):
            raw_15.setdefault("override_hint", "uncorrected_misinfo")
        item_15 = convert_llm_raw_to_item_verdict(
            item_number=15,
            raw=raw_15,
            assigned_turns=assigned_turns,
            verdicts_bundle=verdicts_bundle,
            evaluation_mode_override="partial_with_review",
            rag_evidence=rag_evidence_15,
        )
        _inject_hybrid_fields(item_15, raw_15)
        item_16 = convert_llm_raw_to_item_verdict(
            item_number=16,
            raw=raw_16,
            assigned_turns=assigned_turns,
            verdicts_bundle=verdicts_bundle,
            rag_evidence=rag_evidence_16,
        )
        _inject_hybrid_fields(item_16, raw_16)

    confs = [
        item_15.get("llm_self_confidence", {}).get("score", 3),
        item_16.get("llm_self_confidence", {}).get("score", 3),
    ]
    category_confidence = int(sum(confs) / max(1, len(confs)))

    resp = build_sub_agent_response(
        agent_id="work-accuracy-agent",
        category="work_accuracy",
        status="success",
        items=[item_15, item_16],
        category_confidence=category_confidence,
        llm_backend=llm_backend,
        llm_model_id=bedrock_model_id,
    )
    wiki_updates = extract_wiki_updates(accuracy_verdict=accuracy_verdict)
    _ = preprocessing
    return resp, wiki_updates


# ---------------------------------------------------------------------------
# #15 LLM 호출 (RAG hits 주입)
# ---------------------------------------------------------------------------


async def _evaluate_item_15(
    *,
    transcript: str,
    assigned_turns: list[dict],
    consultation_type: str,
    knowledge,
    segment_text: str,
    llm_backend: str,
    bedrock_model_id: str | None,
) -> dict[str, Any]:
    """#15 정확한 안내 — 3-Persona 병렬 + 하이브리드 머지.

    partial_with_review 모드 — 판사 프롬프트의 compliance 보수성 원칙은 본 항목엔
    직접 적용 안 되지만, step_spread>=2 시 판사가 숙고로 최종 결정.
    """
    system_prompt_base = load_group_b_prompt("item_15_accuracy")
    user_message = _build_user_message_15(
        transcript=transcript, assigned_turns=assigned_turns, consultation_type=consultation_type, knowledge=knowledge
    )

    async def _call_persona(persona: str) -> dict[str, Any] | None:
        sys_prompt = apply_persona_prefix(system_prompt_base, persona)
        try:
            raw = await call_bedrock_json(
                system_prompt=sys_prompt,
                user_message=user_message,
                max_tokens=2048,
                backend=llm_backend,
                bedrock_model_id=bedrock_model_id,
            )
            logger.info(
                "[DEBUG #15 persona=%s] LLM raw keys=%s", persona, list(raw.keys()) if isinstance(raw, dict) else None
            )
            return raw
        except LLMTimeoutError:
            raise
        except Exception as e:
            logger.warning("persona=%s #15 LLM 실패: %s", persona, e)
            return None

    # QA_FORCE_SINGLE_PERSONA env 가 켜지면 neutral 1회만 호출 (강제 single 모드)
    from v2.reconciler_personas import force_single_persona as _fsp

    if _fsp():
        neutral_only = await _call_persona("neutral")
        results = [None, neutral_only, None]
    else:
        results = await asyncio.gather(
            _call_persona("strict"), _call_persona("neutral"), _call_persona("loose"), return_exceptions=False
        )
    persona_outputs: dict[str, dict[str, Any]] = {
        p: r for p, r in zip(PERSONAS, results, strict=False) if r is not None
    }

    if not persona_outputs:
        logger.warning("#15 3 persona 모두 실패 → rule fallback")
        return _rule_fallback_result(15, "3 persona 모두 실패")

    try:
        hybrid = await reconcile_hybrid(
            item_number=15,
            item_name=ITEM_NAMES_KO.get(15, ""),
            transcript_slice=segment_text,
            persona_outputs=persona_outputs,
            llm_backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("#15 reconcile_hybrid 실패 → neutral 대표 채택: %s", e)
        representative = persona_outputs.get("neutral") or next(iter(persona_outputs.values()))
        return representative

    # neutral 대표 채택 — corrections_made / has_incorrect_guidance 류는 neutral 것
    representative = persona_outputs.get("neutral") or next(iter(persona_outputs.values()))
    raw_merged: dict[str, Any] = dict(representative)
    raw_merged["score"] = hybrid["final_score"]
    raw_merged["confidence"] = hybrid["confidence"] / 5.0
    raw_merged["self_confidence"] = hybrid["confidence"]
    raw_merged["override_hint"] = hybrid["override_hint"]
    raw_merged["_persona_votes"] = hybrid["persona_votes"]
    raw_merged["_persona_step_spread"] = hybrid["step_spread"]
    raw_merged["_persona_merge_path"] = hybrid["merge_path"]
    raw_merged["_persona_merge_rule"] = hybrid["merge_rule"]
    raw_merged["_judge_reasoning"] = hybrid["judge_reasoning"]
    raw_merged["_mandatory_human_review"] = hybrid["mandatory_human_review"]
    from v2.agents.group_a._shared import _extract_persona_details

    raw_merged["_persona_details"] = _extract_persona_details(persona_outputs)
    return raw_merged


def _build_user_message_15(*, transcript: str, assigned_turns: list[dict], consultation_type: str, knowledge) -> str:
    lines: list[str] = []
    lines.append(f"## Consultation Type\n{consultation_type}\n")
    lines.append("## 업무지식 RAG hits")
    for chunk in knowledge.chunks[:3]:
        lines.append(f"- [{chunk.chunk_id} score={chunk.score:.2f}] {chunk.text[:300]}")
    lines.append(f"(match_reason: {knowledge.match_reason})\n")
    lines.append(f"## Transcript\n{transcript}\n")
    if assigned_turns:
        lines.append("## Assigned Turns")
        for t in assigned_turns[:30]:
            lines.append(f"- [Turn {t.get('turn_id')}] {t.get('speaker')}: {t.get('text', '')[:200]}")
        lines.append("")
    lines.append(
        "## Instructions\n항목 #15 (정확한 안내) 을 평가하세요. "
        "상담사 안내와 업무지식 chunks 간 사실 일치를 LLM 이 직접 판정. "
        "chunks 점수 가중평균으로 점수 산출 금지 (원칙 7.5). JSON 만 반환."
    )
    return "\n".join(lines)


def _enrich_with_rag_evidence(raw_15: dict, knowledge) -> dict:
    """LLM evidence 에 RAG chunks 1-2건 보강 (출처: 업무지식)."""
    existing = raw_15.get("evidence", []) or []
    # RAG evidence 는 speaker="업무지식" 태그로 구분
    for chunk in knowledge.chunks[:2]:
        existing.append({"turn": None, "speaker": "업무지식", "text": (chunk.text or "")[:200], "timestamp": None})
    raw_15["evidence"] = existing
    return raw_15


def _unevaluable_item_15(knowledge, verdicts_bundle: dict) -> tuple[ItemVerdict, dict]:
    """knowledge.unevaluable=True 시 분기."""
    logger.info("#15 RAG unevaluable: %s", knowledge.match_reason)
    item_15 = make_item_verdict(
        item_number=15,
        score=0,
        evaluation_mode="unevaluable",
        judgment="업무지식 RAG 부재로 평가 보류 — 인간 검수 필수",
        deductions=[],
        evidence=[],
        llm_self_confidence=make_llm_self_confidence(score=1, rationale=f"RAG unevaluable: {knowledge.match_reason}"),
        rule_llm_delta=compare_with_rule_pre_verdict(item_number=15, llm_score=0, rule_pre_verdicts=verdicts_bundle),
        mode_reason=f"업무지식 RAG 평가 불가: {knowledge.match_reason}",
    )
    accuracy_verdict = {
        "has_incorrect_guidance": False,
        "severity": "unevaluable",
        "correction_attempted": False,
        "incorrect_items": [],
        "evidence_turn_ids": [],
        "recommended_override": "none",
        "rationale": knowledge.match_reason,
    }
    return item_15, accuracy_verdict


def _build_accuracy_verdict(raw_15: dict, knowledge) -> dict:
    """#15 LLM 결과 → Layer 3 Override 입력.

    PDF §5.2 (p11) Override 정책:
        "오안내 후 미정정 → 업무 정확도 대분류 전체 0점"

    incorrect_items 매핑:
        - has_incorrect_guidance=True AND correction_attempted=False
          → 업무정확도 대분류 전체 (item 15, 16) 포함 → category_zero Override 트리거
        - has_incorrect_guidance=True AND correction_attempted=True
          → item 15 만 포함 (감점 있어도 카테고리 전체 0점은 아님) → item_zero
        - has_incorrect_guidance=False → 빈 리스트
    """
    item_15_max = ITEM_MAX_SCORE.get(15, 15)
    score = int(raw_15.get("score", item_15_max) or item_15_max)
    has_incorrect_guidance = score < item_15_max
    correction_attempted = bool(raw_15.get("corrections_made") or ("정정" in (raw_15.get("summary", "") or "")))
    severity = "major" if score == 0 else "minor" if score < item_15_max else "none"

    if has_incorrect_guidance and not correction_attempted:
        incorrect_items = list(_WORK_ACCURACY_ITEMS)
        recommended_override = "category_zero"
    elif has_incorrect_guidance and correction_attempted:
        incorrect_items = [15]
        recommended_override = "item_zero"
    else:
        incorrect_items = []
        recommended_override = "none"

    return {
        "has_incorrect_guidance": has_incorrect_guidance,
        "severity": severity,
        "correction_attempted": correction_attempted,
        "incorrect_items": incorrect_items,
        "evidence_turn_ids": [
            ev.get("turn") for ev in (raw_15.get("evidence", []) or []) if isinstance(ev.get("turn"), int)
        ],
        "recommended_override": recommended_override,
        "rationale": raw_15.get("summary", "") or "",
        "rag_matched_chunks": len(knowledge.chunks),
    }


# ---------------------------------------------------------------------------
# #16 LLM 호출 (RAG 없음, Few-shot 만)
# ---------------------------------------------------------------------------


async def _evaluate_item_16(
    *,
    transcript: str,
    assigned_turns: list[dict],
    consultation_type: str,
    fewshot: Any,
    segment_text: str,
    llm_backend: str,
    bedrock_model_id: str | None,
) -> dict[str, Any]:
    """#16 필수 안내 이행 — 3-Persona 병렬 + 하이브리드 머지."""
    system_prompt_base = load_group_b_prompt("item_16_mandatory_script")
    user_message = _build_user_message_generic(
        item_number=16,
        transcript=transcript,
        assigned_turns=assigned_turns,
        consultation_type=consultation_type,
        fewshot=fewshot,
    )

    async def _call_persona(persona: str) -> dict[str, Any] | None:
        sys_prompt = apply_persona_prefix(system_prompt_base, persona)
        try:
            raw = await call_bedrock_json(
                system_prompt=sys_prompt,
                user_message=user_message,
                max_tokens=2048,
                backend=llm_backend,
                bedrock_model_id=bedrock_model_id,
            )
            logger.info(
                "[DEBUG #16 persona=%s] LLM raw keys=%s", persona, list(raw.keys()) if isinstance(raw, dict) else None
            )
            return raw
        except LLMTimeoutError:
            raise
        except Exception as e:
            logger.warning("persona=%s #16 LLM 실패: %s", persona, e)
            return None

    # 사용자 지시 (2026-04-28): #16 "필수 안내 이행" 은 Intent 분류 + 스크립트 매칭 기반
    # 객관 판정 → single-persona 강제 (페르소나 관점 차이 무의미).
    # QA_FORCE_SINGLE_PERSONA env 와 무관하게 하드코딩.
    neutral_only = await _call_persona("neutral")
    results = [None, neutral_only, None]
    persona_outputs: dict[str, dict[str, Any]] = {
        p: r for p, r in zip(PERSONAS, results, strict=False) if r is not None
    }

    if not persona_outputs:
        logger.warning("#16 single-persona 실패 → rule fallback")
        return _rule_fallback_result(16, "single-persona 실패")

    try:
        hybrid = await reconcile_hybrid(
            item_number=16,
            item_name=ITEM_NAMES_KO.get(16, ""),
            transcript_slice=segment_text,
            persona_outputs=persona_outputs,
            llm_backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("#16 reconcile_hybrid 실패 → neutral 대표 채택: %s", e)
        representative = persona_outputs.get("neutral") or next(iter(persona_outputs.values()))
        return representative

    representative = persona_outputs.get("neutral") or next(iter(persona_outputs.values()))
    raw_merged: dict[str, Any] = dict(representative)
    raw_merged["score"] = hybrid["final_score"]
    raw_merged["confidence"] = hybrid["confidence"] / 5.0
    raw_merged["self_confidence"] = hybrid["confidence"]
    raw_merged["override_hint"] = hybrid["override_hint"]
    raw_merged["_persona_votes"] = hybrid["persona_votes"]
    raw_merged["_persona_step_spread"] = hybrid["step_spread"]
    raw_merged["_persona_merge_path"] = hybrid["merge_path"]
    raw_merged["_persona_merge_rule"] = hybrid["merge_rule"]
    raw_merged["_judge_reasoning"] = hybrid["judge_reasoning"]
    raw_merged["_mandatory_human_review"] = hybrid["mandatory_human_review"]
    from v2.agents.group_a._shared import _extract_persona_details

    raw_merged["_persona_details"] = _extract_persona_details(persona_outputs)
    return raw_merged


def _inject_hybrid_fields(item: ItemVerdict, raw: dict[str, Any]) -> None:
    """convert_llm_raw_to_item_verdict 반환 후 하이브리드 머지 필드 주입."""
    if "_persona_votes" in raw:
        item["persona_votes"] = raw["_persona_votes"]
    if "_persona_step_spread" in raw:
        item["persona_step_spread"] = raw["_persona_step_spread"]
    if "_persona_merge_path" in raw:
        item["persona_merge_path"] = raw["_persona_merge_path"]
    if "_persona_merge_rule" in raw:
        item["persona_merge_rule"] = raw["_persona_merge_rule"]
    if "_judge_reasoning" in raw:
        item["judge_reasoning"] = raw["_judge_reasoning"]
    if raw.get("_mandatory_human_review"):
        item["mandatory_human_review"] = True
    if raw.get("_persona_details"):
        item["persona_details"] = raw["_persona_details"]


# ---------------------------------------------------------------------------
# 공통
# ---------------------------------------------------------------------------


def _rule_fallback_result(item_number: int, err_msg: str) -> dict[str, Any]:
    max_score = ITEM_MAX_SCORE.get(item_number, 5)
    return {
        "score": max_score,
        "deductions": [],
        "evidence": [],
        "confidence": 0.5,
        "self_confidence": 2,
        "summary": f"LLM 실패 — 규칙 폴백 (err={err_msg[:80]})",
    }


def _fallback_result(item_number: int, gather_slot: Any) -> dict[str, Any]:
    if isinstance(gather_slot, Exception):
        if isinstance(gather_slot, LLMTimeoutError):
            raise gather_slot
        return _rule_fallback_result(item_number, str(gather_slot))
    return gather_slot or _rule_fallback_result(item_number, "empty_result")


def _build_user_message_generic(
    *, item_number: int, transcript: str, assigned_turns: list[dict], consultation_type: str, fewshot: Any
) -> str:
    lines: list[str] = []
    lines.append(f"## Consultation Type\n{consultation_type}\n")
    lines.append(f"## Transcript\n{transcript}\n")
    if assigned_turns:
        lines.append("## Assigned Turns")
        for t in assigned_turns[:30]:
            lines.append(f"- [Turn {t.get('turn_id')}] {t.get('speaker')}: {t.get('text', '')[:200]}")
        lines.append("")
    if fewshot and getattr(fewshot, "examples", None):
        lines.append(f"## Golden-set 유사 예시 (top {len(fewshot.examples)}) — 판정 기준 (필수 준수)")
        lines.append("")
        lines.append("**활용 규칙**:")
        lines.append("1. 아래 예시는 **사람 평가자가 동일 항목에 대해 실제 판정한 케이스** 이다.")
        lines.append("2. 평가 대상 발화가 예시 중 하나와 **매우 유사하면 해당 점수를 우선 채택**.")
        lines.append("3. 예시 rationale 과 본인 판단 상충 시 **사람 평가자 판단 우선**.")
        lines.append("4. Rubric 각 단계 정의는 예시로 구체화된다 — rubric 단독 판정 금지.")
        lines.append("")
        for ex in fewshot.examples[:5]:
            lines.append(
                f"- score={ex.score} bucket={ex.score_bucket}"
                f"\n  segment: {(ex.segment_text or '')[:300]}"
                f"\n  rationale: {(ex.rationale or '')[:300]}"
            )
        lines.append("")
    lines.append(f"## Instructions\n항목 #{item_number} 을 평가하세요. JSON 만 반환.")
    return "\n".join(lines)


def _build_rag_query(transcript: str, intent: str, product: str | None) -> str:
    parts = [intent] if intent and intent != "*" else []
    if product:
        parts.append(product)
    parts.append(transcript[:2800])
    return " ".join(parts).strip()


def _build_segment_text(assigned_turns: list[dict], fallback: str) -> str:
    if not assigned_turns:
        return fallback[:2800]
    return "\n".join(f"{t.get('speaker', '')}: {t.get('text', '')}" for t in assigned_turns)[:2800]


def _safe_fewshot(item_number: int, intent: str, segment_text: str, tenant_id: str):
    try:
        return retrieve_fewshot(
            item_number=item_number, intent=intent, segment_text=segment_text, tenant_id=tenant_id, top_k=3
        )
    except RAGError as e:
        logger.info("retrieve_fewshot #%d unavailable: %s", item_number, e)
        return None
    except Exception as e:
        logger.warning("retrieve_fewshot #%d unexpected: %s", item_number, e)
        return None


# silence unused type-hint-only imports
_ = EvidenceQuote
