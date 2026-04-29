# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""적극성 Sub Agent — #12 문제해결의지 (5점) + #13 부연설명 (5점) + #14 사후안내 (5점).

Phase D2 이후 실 Bedrock 경로:
- 3 항목 각각 V2 prompts/group_b/item_12/13/14 로드 후 `asyncio.gather` 병렬 호출
- `accuracy_verdict` (Wiki 공유 메모리) 를 #12 프롬프트 user 메시지에 주입
- #14 은 `immediate_resolution=True` 감지 시 `evaluation_mode="skipped"` 동적 전환

카테고리: proactiveness (max 15점)
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


_PROMPT_NAME = {
    12: "item_12_problem_solving",
    13: "item_13_supplementary",
    14: "item_14_followup",
}


async def proactiveness_agent(
    *,
    transcript: str,
    assigned_turns: list[dict],
    consultation_type: str,
    intent_summary: dict | None = None,
    accuracy_verdict: dict | None = None,
    rule_pre_verdicts: dict | None = None,
    preprocessing: dict | None = None,
    tenant_id: str = "generic",
    llm_backend: str = "bedrock",
    bedrock_model_id: str | None = None,
) -> tuple[SubAgentResponse, dict[str, Any]]:
    """#12, #13, #14 평가 — 실 Bedrock 호출 (병렬 3 항목).

    site_id=shinhan 시 #13 (부연) 은 xlsx 에 별도 항목으로 없고 #12 에 통합되어 있으므로
    LLM 호출 스킵 — 평가 대상 항목 [12, 14] 만 실행.
    """
    verdicts_bundle = (rule_pre_verdicts or {}).get("verdicts") or {}
    intent = (intent_summary or {}).get("primary_intent") or "*"
    segment_text = _build_segment_text(assigned_turns, fallback=transcript)
    is_shinhan = (tenant_id or "").lower() == "shinhan"
    target_items: tuple[int, ...] = (12, 14) if is_shinhan else (12, 13, 14)

    # RAG 병렬 — target_items 만 (신한 시 #13 RAG 도 스킵)
    _rag_coros = []
    _rag_keys = []
    for n in target_items:
        _rag_coros.append(asyncio.to_thread(_safe_fewshot, n, intent, segment_text, tenant_id))
        _rag_keys.append(("fewshot", n))
    for n in target_items:
        _rag_coros.append(asyncio.to_thread(_safe_reasoning, n, segment_text, tenant_id))
        _rag_keys.append(("reasoning", n))
    _rag_results = await asyncio.gather(*_rag_coros)
    fewshots: dict[int, Any] = {}
    reasonings: dict[int, Any] = {}
    for (kind, n), res in zip(_rag_keys, _rag_results):
        if kind == "fewshot":
            fewshots[n] = res
        else:
            reasonings[n] = res

    eval_tasks = []
    for n in target_items:
        eval_tasks.append(_evaluate_item(
            item_number=n, transcript=transcript, assigned_turns=assigned_turns,
            consultation_type=consultation_type, fewshot=fewshots[n],
            accuracy_context=_build_accuracy_context(accuracy_verdict) if n == 12 else "",
            segment_text=segment_text,
            llm_backend=llm_backend, bedrock_model_id=bedrock_model_id,
        ))
    gather_result = await asyncio.gather(*eval_tasks, return_exceptions=True)
    timeouts = [r for r in gather_result if isinstance(r, LLMTimeoutError)]
    if len(timeouts) == len(target_items):
        raise timeouts[0]

    raws: dict[int, Any] = {}
    for idx, n in enumerate(target_items):
        raws[n] = _fallback_result(n, gather_result[idx])

    if is_shinhan:
        logger.info("proactiveness_agent: site=shinhan → #13 LLM 호출 스킵 (xlsx 정합 — #12 통합)")

    items: list[ItemVerdict] = []
    for n in target_items:
        raw = raws[n]
        # #14 동적 mode 전환 — 즉시해결 플래그 (neutral persona 대표값 기준)
        eval_mode_override = None
        if n == 14 and raw.get("immediate_resolution"):
            eval_mode_override = "skipped"
            raw["score"] = 5  # 만점 고정
            raw["deductions"] = []
        reasoning_n = reasonings.get(n)
        item_n = convert_llm_raw_to_item_verdict(
            item_number=n, raw=raw,
            assigned_turns=assigned_turns,
            verdicts_bundle=verdicts_bundle,
            evaluation_mode_override=eval_mode_override,
            rag_evidence=make_rag_evidence(
                fewshot=fewshots.get(n),
                rag_stdev=getattr(reasoning_n, "stdev", None) if reasoning_n else None,
                reasoning_sample_size=getattr(reasoning_n, "sample_size", None) if reasoning_n else None,
                reasoning_example_ids=[
                    getattr(ex, "example_id", "") for ex in (getattr(reasoning_n, "examples", None) or [])
                ] if reasoning_n else None,
                reasoning_examples=(getattr(reasoning_n, "examples", None) or []) if reasoning_n else None,
                fewshot_query=segment_text, reasoning_query=segment_text, intent=intent,
            ),
        )
        _inject_hybrid_fields(item_n, raw)
        items.append(item_n)

    confs = [it.get("llm_self_confidence", {}).get("score", 3) for it in items]
    category_confidence = int(sum(confs) / max(1, len(confs)))
    resp = build_sub_agent_response(
        agent_id="proactiveness-agent",
        category="proactiveness",
        status="success",
        items=items,
        category_confidence=category_confidence,
        llm_backend=llm_backend,
        llm_model_id=bedrock_model_id,
    )
    wiki_updates = extract_wiki_updates()
    rag_diag = {}
    for n in (12, 13, 14):
        r = reasonings.get(n)
        if r is not None:
            rag_diag[n] = {
                "reasoning_stdev": r.stdev,
                "reasoning_mean": r.mean,
                "reasoning_sample_size": r.sample_size,
            }
    if rag_diag:
        wiki_updates["rag_diagnostics"] = rag_diag
    _ = preprocessing
    return resp, wiki_updates


# ---------------------------------------------------------------------------
# LLM 호출
# ---------------------------------------------------------------------------


async def _evaluate_item(
    *,
    item_number: int,
    transcript: str,
    assigned_turns: list[dict],
    consultation_type: str,
    fewshot: Any,
    accuracy_context: str,
    segment_text: str,
    llm_backend: str,
    bedrock_model_id: str | None,
) -> dict[str, Any]:
    """#12/#13/#14 3-Persona 병렬 + 하이브리드 머지.

    Persona 별 LLM 호출은 동일 user_message / accuracy_context / RAG fewshot 공유.
    LLMTimeoutError 는 상위 전파, 그 외 rule fallback.
    """
    system_prompt_base = load_group_b_prompt(_PROMPT_NAME[item_number])
    user_message = _build_user_message(
        item_number=item_number, transcript=transcript,
        assigned_turns=assigned_turns, consultation_type=consultation_type,
        fewshot=fewshot, accuracy_context=accuracy_context,
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
            "proactiveness item #%d 3 persona 모두 실패 → rule fallback", item_number
        )
        return _rule_fallback_result(item_number, "3 persona 모두 실패")

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
            "proactiveness item #%d reconcile_hybrid 실패 → neutral 대표 채택: %s",
            item_number, e,
        )
        representative = persona_outputs.get("neutral") or next(iter(persona_outputs.values()))
        return representative

    # neutral 대표 채택 — judgment/deductions/evidence/immediate_resolution 는 neutral 의 것.
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


def _rule_fallback_result(item_number: int, err_msg: str) -> dict[str, Any]:
    """LLM 실패 시 긍정 기본 (만점). reconciler 가 [SKIPPED_INFRA] 정화."""
    del item_number
    return {
        "score": 5,
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


def _build_user_message(
    *,
    item_number: int,
    transcript: str,
    assigned_turns: list[dict],
    consultation_type: str,
    fewshot: Any,
    accuracy_context: str,
) -> str:
    lines: list[str] = []
    lines.append(f"## Consultation Type\n{consultation_type}\n")
    if accuracy_context:
        lines.append(f"## Wiki — accuracy_verdict\n{accuracy_context}\n")
    lines.append(f"## Transcript\n{transcript}\n")
    if assigned_turns:
        lines.append("## Assigned Turns")
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


def _build_accuracy_context(accuracy_verdict: dict | None) -> str:
    if not accuracy_verdict or not accuracy_verdict.get("has_incorrect_guidance"):
        return ""
    severity = accuracy_verdict.get("severity", "unknown")
    rationale = accuracy_verdict.get("rationale", "")
    return f"심각도: {severity}\n사유: {rationale}\n(적극성 평가에 부정적 영향 고려)"


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
