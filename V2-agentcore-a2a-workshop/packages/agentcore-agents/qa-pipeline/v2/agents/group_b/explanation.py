# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""설명력·전달력 Sub Agent — #10 설명의 명확성 (10점) + #11 두괄식 답변 (5점).

Phase D2 (2026-04-20 옵션 A 승인) 이후 실 Bedrock 경로 전환:
- V2 prompts/group_b/item_10_clarity.sonnet.md (iter03_clean + iter04) 사용
- V2 prompts/group_b/item_11_conclusion_first.sonnet.md (V1 유지) 사용
- V1 `nodes.llm.get_chat_model` + `invoke_and_parse` + `LLMTimeoutError` 재활용 (import only)
- `snap_score_v2` 경유 (V1 [5,0] 아님, V2 ALLOWED_STEPS 존중)
- `normalize_fallback_deductions` 로 인프라 폴백 정화

카테고리: explanation_delivery (max 15점)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from v2.agents.group_b._llm import (
    LLMTimeoutError,
    call_bedrock_json,
    load_group_b_prompt,
)
from v2.agents.group_b.base import (
    ITEM_NAMES_KO,
    build_sub_agent_response,
    convert_llm_raw_to_item_verdict,
    extract_wiki_updates,
)
from v2.agents.group_a._shared import make_rag_evidence
from v2.judge_agent import reconcile_hybrid
from v2.rag import retrieve_fewshot, retrieve_reasoning
from v2.rag.types import RAGError
from v2.reconciler_personas import PERSONAS, apply_persona_prefix
from v2.schemas.sub_agent_io import ItemVerdict, SubAgentResponse


logger = logging.getLogger(__name__)


async def explanation_agent(
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
    """#10, #11 평가 — 실 Bedrock 호출 (병렬 2 항목).

    site_id=shinhan 시 #11 (두괄식 답변) 은 xlsx 에 별도 항목으로 없고 #10 에 통합되어
    있으므로 LLM 호출 스킵 — score=None / evaluation_mode=excluded_by_tenant.
    """
    verdicts_bundle = (rule_pre_verdicts or {}).get("verdicts") or {}
    intent = (intent_summary or {}).get("primary_intent") or "*"
    segment_text = _build_segment_text(assigned_turns, fallback=transcript)
    is_shinhan = (tenant_id or "").lower() == "shinhan"

    # RAG (#10 항상, #11 신한 시 스킵)
    rag_tasks = [
        asyncio.to_thread(_safe_fewshot, 10, intent, segment_text, tenant_id),
        asyncio.to_thread(_safe_reasoning, 10, segment_text, tenant_id),
    ]
    if not is_shinhan:
        rag_tasks.extend([
            asyncio.to_thread(_safe_fewshot, 11, intent, segment_text, tenant_id),
            asyncio.to_thread(_safe_reasoning, 11, segment_text, tenant_id),
        ])
    rag_results = await asyncio.gather(*rag_tasks)
    fewshot_10 = rag_results[0]
    reasoning_10 = rag_results[1]
    fewshot_11 = rag_results[2] if not is_shinhan else None
    reasoning_11 = rag_results[3] if not is_shinhan else None

    # LLM 호출 — #10 항상, #11 신한 시 스킵 (별도 evaluation 처리)
    eval_tasks = [
        _evaluate_item(
            item_number=10,
            prompt_name="item_10_clarity",
            max_tokens=2048,
            transcript=transcript,
            assigned_turns=assigned_turns,
            consultation_type=consultation_type,
            fewshot=fewshot_10,
            segment_text=segment_text,
            llm_backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
        ),
    ]
    if not is_shinhan:
        eval_tasks.append(_evaluate_item(
            item_number=11,
            prompt_name="item_11_conclusion_first",
            max_tokens=1536,
            transcript=transcript,
            assigned_turns=assigned_turns,
            consultation_type=consultation_type,
            fewshot=fewshot_11,
            segment_text=segment_text,
            llm_backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
        ))
    gather_result = await asyncio.gather(*eval_tasks, return_exceptions=True)
    timeouts = [r for r in gather_result if isinstance(r, LLMTimeoutError)]
    if not is_shinhan and len(timeouts) == 2:
        raise timeouts[0]
    if is_shinhan and len(timeouts) == 1:
        raise timeouts[0]

    llm_10 = _fallback_result(10, gather_result[0])
    llm_11 = _fallback_result(11, gather_result[1]) if not is_shinhan else None

    rag_evidence_10 = make_rag_evidence(
        fewshot=fewshot_10,
        rag_stdev=getattr(reasoning_10, "stdev", None) if reasoning_10 else None,
        reasoning_sample_size=getattr(reasoning_10, "sample_size", None) if reasoning_10 else None,
        reasoning_example_ids=[
            getattr(ex, "example_id", "") for ex in (getattr(reasoning_10, "examples", None) or [])
        ] if reasoning_10 else None,
        reasoning_examples=(getattr(reasoning_10, "examples", None) or []) if reasoning_10 else None,
        fewshot_query=segment_text, reasoning_query=segment_text, intent=intent,
    )
    item_10 = convert_llm_raw_to_item_verdict(
        item_number=10, raw=llm_10,
        assigned_turns=assigned_turns, verdicts_bundle=verdicts_bundle,
        rag_evidence=rag_evidence_10,
    )
    _inject_hybrid_fields(item_10, llm_10)
    items: list[ItemVerdict] = [item_10]

    if not is_shinhan:
        rag_evidence_11 = make_rag_evidence(
            fewshot=fewshot_11,
            rag_stdev=getattr(reasoning_11, "stdev", None) if reasoning_11 else None,
            reasoning_sample_size=getattr(reasoning_11, "sample_size", None) if reasoning_11 else None,
            reasoning_example_ids=[
                getattr(ex, "example_id", "") for ex in (getattr(reasoning_11, "examples", None) or [])
            ] if reasoning_11 else None,
            reasoning_examples=(getattr(reasoning_11, "examples", None) or []) if reasoning_11 else None,
            fewshot_query=segment_text, reasoning_query=segment_text, intent=intent,
        )
        item_11 = convert_llm_raw_to_item_verdict(
            item_number=11, raw=llm_11,
            assigned_turns=assigned_turns, verdicts_bundle=verdicts_bundle,
            rag_evidence=rag_evidence_11,
        )
        _inject_hybrid_fields(item_11, llm_11)
        items.append(item_11)
    else:
        logger.info("explanation_agent: site=shinhan → #11 LLM 호출 스킵 (xlsx 정합 — #10 통합)")
    confs = [it.get("llm_self_confidence", {}).get("score", 3) for it in items]
    category_confidence = int(sum(confs) / max(1, len(confs)))

    resp = build_sub_agent_response(
        agent_id="explanation-delivery-agent",
        category="explanation_delivery",
        status="success",
        items=items,
        category_confidence=category_confidence,
        llm_backend=llm_backend,
        llm_model_id=bedrock_model_id,
    )
    wiki_updates = extract_wiki_updates()
    if reasoning_10 is not None or reasoning_11 is not None:
        wiki_updates["rag_diagnostics"] = {}
        if reasoning_10 is not None:
            wiki_updates["rag_diagnostics"][10] = {
                "reasoning_stdev": reasoning_10.stdev,
                "reasoning_mean": reasoning_10.mean,
                "reasoning_sample_size": reasoning_10.sample_size,
            }
        if reasoning_11 is not None:
            wiki_updates["rag_diagnostics"][11] = {
                "reasoning_stdev": reasoning_11.stdev,
                "reasoning_mean": reasoning_11.mean,
                "reasoning_sample_size": reasoning_11.sample_size,
            }
    _ = preprocessing
    return resp, wiki_updates


# ---------------------------------------------------------------------------
# LLM 호출 & 결과 변환
# ---------------------------------------------------------------------------


async def _evaluate_item(
    *,
    item_number: int,
    prompt_name: str,
    max_tokens: int,
    transcript: str,
    assigned_turns: list[dict],
    consultation_type: str,
    fewshot: Any,
    segment_text: str,
    llm_backend: str,
    bedrock_model_id: str | None,
) -> dict[str, Any]:
    """단일 항목 3-Persona 병렬 호출 + 하이브리드 머지.

    Persona 별 LLM 호출은 동일 user_message / RAG 컨텍스트 공유. system_prompt
    앞에 persona prefix (strict/neutral/loose) 만 달리 주입.
    LLMTimeoutError 는 상위 전파, 그 외 rule fallback.
    """
    system_prompt_base = load_group_b_prompt(prompt_name)
    user_message = _build_user_message(
        item_number=item_number,
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
                max_tokens=max_tokens,
                backend=llm_backend,
                bedrock_model_id=bedrock_model_id,
            )
            logger.info(
                "[DEBUG #%d persona=%s] LLM raw keys=%s",
                item_number, persona,
                list(raw.keys()) if isinstance(raw, dict) else None,
            )
            return raw
        except LLMTimeoutError:
            raise
        except Exception as e:
            logger.warning(
                "persona=%s item #%d LLM 실패: %s", persona, item_number, e
            )
            return None

    # QA_FORCE_SINGLE_PERSONA env 가 켜지면 neutral 1회만 호출 (강제 single 모드)
    from v2.reconciler_personas import force_single_persona as _fsp
    if _fsp():
        neutral_only = await _call_persona("neutral")
        results = [None, neutral_only, None]
    else:
        results = await asyncio.gather(
            _call_persona("strict"),
            _call_persona("neutral"),
            _call_persona("loose"),
            return_exceptions=False,
        )
    persona_outputs: dict[str, dict[str, Any]] = {
        p: r for p, r in zip(PERSONAS, results, strict=False) if r is not None
    }

    if not persona_outputs:
        logger.warning(
            "explanation item #%d 3 persona 모두 실패 → rule fallback", item_number
        )
        return _rule_fallback_result(item_number, "3 persona 모두 실패")

    # 하이브리드 머지 (통계 우선 → step_spread>=2 시 판사 호출)
    try:
        hybrid = await reconcile_hybrid(
            item_number=item_number,
            item_name=ITEM_NAMES_KO.get(item_number, ""),
            transcript_slice=segment_text,
            persona_outputs=persona_outputs,
            llm_backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning(
            "explanation item #%d reconcile_hybrid 실패 → neutral 대표 채택: %s",
            item_number, e,
        )
        representative = persona_outputs.get("neutral") or next(iter(persona_outputs.values()))
        return representative

    # neutral 대표 채택 (judgment/deductions/evidence)
    representative = persona_outputs.get("neutral") or next(iter(persona_outputs.values()))
    raw_merged: dict[str, Any] = dict(representative)
    raw_merged["score"] = hybrid["final_score"]
    raw_merged["confidence"] = hybrid["confidence"] / 5.0  # 1~5 → 0~1
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


def _rule_fallback_result(item_number: int, err_msg: str) -> dict[str, Any]:
    """LLM 실패 시 rule 긍정 기본값. reconciler 가 [SKIPPED_INFRA] 로 정화 예정."""
    max_score = 10 if item_number == 10 else 5
    return {
        "score": max_score,
        "deductions": [],
        "evidence": [],
        "confidence": 0.5,
        "self_confidence": 2,
        "summary": f"LLM 실패 — 규칙 폴백 (err={err_msg[:80]})",
    }


def _fallback_result(item_number: int, gather_slot: Any) -> dict[str, Any]:
    """asyncio.gather return_exceptions=True slot 처리."""
    if isinstance(gather_slot, Exception):
        if isinstance(gather_slot, LLMTimeoutError):
            raise gather_slot
        return _rule_fallback_result(item_number, str(gather_slot))
    return gather_slot or _rule_fallback_result(item_number, "empty_result")


def _build_user_message(
    *,
    item_number: int,
    transcript: str,
    assigned_turns: list[dict],
    consultation_type: str,
    fewshot: Any,
) -> str:
    """LLM user 메시지 빌더."""
    lines: list[str] = []
    lines.append(f"## Consultation Type\n{consultation_type}\n")
    lines.append(f"## Transcript\n{transcript}\n")
    if assigned_turns:
        lines.append("## Assigned Turns (상담사 평가 대상)")
        for t in assigned_turns[:30]:
            lines.append(
                f"- [Turn {t.get('turn_id')}] {t.get('speaker')}: {t.get('text', '')[:200]}"
            )
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


# ---------------------------------------------------------------------------
# Segment / RAG helpers
# ---------------------------------------------------------------------------


def _build_segment_text(assigned_turns: list[dict], fallback: str) -> str:
    if not assigned_turns:
        return fallback[:2800]
    return "\n".join(
        f"{t.get('speaker', '')}: {t.get('text', '')}" for t in assigned_turns
    )[:2800]


def _safe_fewshot(item_number: int, intent: str, segment_text: str, tenant_id: str):
    try:
        return retrieve_fewshot(
            item_number=item_number, intent=intent,
            segment_text=segment_text, tenant_id=tenant_id, top_k=3,
        )
    except RAGError as e:
        logger.info("retrieve_fewshot #%d unavailable: %s", item_number, e)
        return None
    except Exception as e:
        logger.warning("retrieve_fewshot #%d unexpected: %s", item_number, e)
        return None


def _safe_reasoning(item_number: int, segment_text: str, tenant_id: str):
    try:
        return retrieve_reasoning(
            item_number=item_number, transcript_slice=segment_text,
            tenant_id=tenant_id, top_k=7,
        )
    except RAGError as e:
        logger.info("retrieve_reasoning #%d unavailable: %s", item_number, e)
        return None
    except Exception as e:
        logger.warning("retrieve_reasoning #%d unexpected: %s", item_number, e)
        return None
