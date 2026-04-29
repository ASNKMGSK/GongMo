# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""니즈파악 Sub Agent (Group A #8~#9) — Dev2.

책임:
  - #8 문의 파악 및 재확인/복창 (5점, full)
  - #9 고객정보 확인 (5점, structural_only — 마스킹으로 내용 검증 불가, 절차만 평가)

V2 원칙:
  - #9 는 evaluation_mode="structural_only" 로 고정. 프롬프트는 "판정 절차 0~4" 만 유지,
    "내용 대조" 조항 제거 (Phase A2 rubric 확정 후 프롬프트 본문 동기화 필요).
  - #9 는 FORCE_T3_ITEMS={9,17,18} 에 속해 force_t3=True 자동 (build_item_verdict 경유).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from v2.agents.group_a._shared import (
    EVIDENCE_INSTRUCTION,
    Stopwatch,
    attach_persona_meta,
    build_item_verdict,
    build_sub_agent_response,
    format_fewshot_block,
    get_assigned_turns,
    get_intent,
    get_rule_pre_verdict,
    is_quality_unevaluable,
    make_rag_evidence,
    rule_evidence_to_evidence_quote,
    run_persona_ensemble,
    async_safe_retrieve_fewshot,
    async_safe_retrieve_reasoning_evidence,
)
from v2.contracts.preprocessing import Preprocessing


logger = logging.getLogger(__name__)

AGENT_ID = "needs-identification-agent"
CATEGORY_KEY = "needs_identification"


async def needs_sub_agent(
    *,
    preprocessing: Preprocessing | dict,
    llm_backend: str | None = None,
    bedrock_model_id: str | None = None,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    with Stopwatch() as sw:
        if is_quality_unevaluable(preprocessing):
            return _build_unevaluable_response(llm_backend, sw)

        rv8 = get_rule_pre_verdict(preprocessing, 8)
        rv9 = get_rule_pre_verdict(preprocessing, 9)

        # 양쪽 모두 LLM verify 필수 (Dev1 합의 확정) — asyncio.gather 로 병렬화 (옵션 A)
        # return_exceptions=False — LLMTimeoutError 는 상위로 전파
        item_8, item_9 = await asyncio.gather(
            _llm_evaluate_item_8(preprocessing, rv8, llm_backend, bedrock_model_id, tenant_id=tenant_id),
            _llm_evaluate_item_9(preprocessing, rv9, llm_backend, bedrock_model_id, tenant_id=tenant_id),
            return_exceptions=False,
        )

        items = [item_8, item_9]

    # Layer 1 의 deduction_triggers["개인정보_유출"] = True 이면 category_zero override 는
    # Layer 3 orchestrator 에서 최종 적용. 여기서는 Sub Agent 판정만 반환.
    return build_sub_agent_response(
        category_key=CATEGORY_KEY,
        agent_id=AGENT_ID,
        items=items,
        llm_backend=llm_backend,
        elapsed_ms=sw.elapsed_ms,
    )


# ---------------------------------------------------------------------------
# LLM verify
# ---------------------------------------------------------------------------


async def _llm_evaluate_item_8(
    preprocessing: Preprocessing | dict,
    rv: dict | None,
    llm_backend: str | None,
    bedrock_model_id: str | None,
    *,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    from nodes.llm import LLMTimeoutError
    from v2.prompts.group_a import load_prompt

    assignment = get_assigned_turns(preprocessing, "mandatory")
    assigned_turns = assignment.get("turns") or []
    numbered = "\n".join(
        f"[Turn {t['turn_id']}] {t.get('speaker','?')}: {t.get('text','')}"
        for t in assigned_turns
    )
    pre = (rv or {}).get("elements") or {}

    try:
        system_prompt = load_prompt("needs", item="item_08", backend=llm_backend)
    except Exception:  # pragma: no cover
        from prompts import load_prompt as load_v1_prompt
        system_prompt = load_v1_prompt("item_08_inquiry_paraphrase", backend=llm_backend)

    intent = get_intent(preprocessing)
    fewshot, reasoning = await asyncio.gather(
        async_safe_retrieve_fewshot(8, intent, numbered, top_k=4, tenant_id=tenant_id),
        async_safe_retrieve_reasoning_evidence(8, numbered, top_k=7, tenant_id=tenant_id),
    )
    fewshot_block = format_fewshot_block(fewshot)
    rag_stdev = reasoning["stdev"]

    user_message = (
        f"{fewshot_block}"
        f"## Transcript\n{numbered}\n\n"
        "## Pre-Analysis\n"
        f"- paraphrase_count: {pre.get('paraphrase_count', 0)}\n"
        f"- requery_count: {pre.get('requery_count', 0)}\n\n"
        f"{EVIDENCE_INSTRUCTION}\n"
        "Evaluate #8 문의 파악 및 재확인(복창). 복창 신호 확장 인정 (의문형/평서형/핵심 키워드 복창)."
    )

    try:
        ensemble = await run_persona_ensemble(
            item_number=8,
            item_name="문의 파악 및 재확인(복창)",
            system_prompt=system_prompt,
            user_message=user_message,
            transcript_slice=numbered,
            llm_backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #8 persona ensemble failed, rule fallback: %s", e)
        return _fallback_item(8, "문의 파악 및 재확인(복창)", rv, reason=f"LLM 실패: {e}", mode="partial_with_review")

    hybrid = ensemble["hybrid"]
    representative = ensemble["representative"]
    logger.info(
        "[DEBUG #8] persona_votes=%s step_spread=%d merge_path=%s final=%s",
        hybrid.get("persona_votes"), hybrid.get("step_spread"),
        hybrid.get("merge_path"), hybrid.get("final_score"),
    )

    verdict = build_item_verdict(
        item_number=8, item_name="문의 파악 및 재확인(복창)", max_score=5,
        raw_score=int(hybrid["final_score"]),
        evaluation_mode="full",
        judgment=representative.get("summary") or representative.get("judgment", ""),
        evidence=_normalize_evidence(representative.get("evidence", []), rv),
        llm_self=int(hybrid["confidence"]),
        rule_verdict=rv,
        rag_stdev=rag_stdev,
        evidence_quality=_infer_evidence_quality(representative.get("evidence", [])),
        override_hint=hybrid.get("override_hint") or representative.get("override_hint"),
        rag_evidence=make_rag_evidence(
            fewshot=fewshot, rag_stdev=rag_stdev,
            reasoning_sample_size=reasoning["sample_size"],
            reasoning_example_ids=reasoning["example_ids"],
            reasoning_examples=reasoning.get("examples"),
            fewshot_query=numbered, reasoning_query=numbered, intent=intent,
        ),
    )
    if hybrid.get("mandatory_human_review"):
        verdict["mandatory_human_review"] = True
    return attach_persona_meta(verdict, hybrid, ensemble.get("persona_outputs"))


async def _llm_evaluate_item_9(
    preprocessing: Preprocessing | dict,
    rv: dict | None,
    llm_backend: str | None,
    bedrock_model_id: str | None,
    *,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    from nodes.llm import LLMTimeoutError
    from v2.prompts.group_a import load_prompt

    assignment = get_assigned_turns(preprocessing, "mandatory")
    assigned_turns = assignment.get("turns") or []
    numbered = "\n".join(
        f"[Turn {t['turn_id']}] {t.get('speaker','?')}: {t.get('text','')}"
        for t in assigned_turns
    )
    pre = (rv or {}).get("elements") or {}

    try:
        system_prompt = load_prompt("needs", item="item_09", backend=llm_backend)
    except Exception:  # pragma: no cover
        from prompts import load_prompt as load_v1_prompt
        system_prompt = load_v1_prompt("item_09_customer_info", backend=llm_backend)

    # #9 고객정보 확인 은 structural_only (마스킹 환경, LLM only) — RAG 미사용.
    # 내용 검증 불가하므로 절차(양해 표현 동반 / 고객 선제 제공 시 복창) 만 판정.
    user_message = (
        f"## Transcript\n{numbered}\n\n"
        "## Pre-Analysis\n"
        f"- info_check_count: {pre.get('info_check_count', 0)}\n"
        f"- courtesy_count: {pre.get('courtesy_count', 0)}\n"
        f"- customer_provided_count: {pre.get('customer_provided_count', 0)}\n\n"
        "## V2 원칙 — structural_only\n"
        "마스킹으로 개인정보 내용 검증 불가. **절차**(양해 표현 동반/고객 선제 제공 시 복창 확인) "
        "만으로 판정. 내용 대조 사유 감점 금지.\n\n"
        f"{EVIDENCE_INSTRUCTION}\n"
        "Evaluate #9 고객정보 확인 (판정 절차 0~4)."
    )

    try:
        ensemble = await run_persona_ensemble(
            item_number=9,
            item_name="고객정보 확인",
            system_prompt=system_prompt,
            user_message=user_message,
            transcript_slice=numbered,
            llm_backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #9 persona ensemble failed, rule fallback: %s", e)
        return _fallback_item(9, "고객정보 확인", rv, reason=f"LLM 실패: {e}", mode="structural_only")

    hybrid = ensemble["hybrid"]
    representative = ensemble["representative"]
    logger.info(
        "[DEBUG #9] persona_votes=%s step_spread=%d merge_path=%s final=%s",
        hybrid.get("persona_votes"), hybrid.get("step_spread"),
        hybrid.get("merge_path"), hybrid.get("final_score"),
    )

    verdict = build_item_verdict(
        item_number=9, item_name="고객정보 확인", max_score=5,
        raw_score=int(hybrid["final_score"]),
        evaluation_mode="structural_only",  # V2 원칙 고정
        judgment=representative.get("summary") or representative.get("judgment", ""),
        evidence=_normalize_evidence(representative.get("evidence", []), rv),
        llm_self=int(hybrid["confidence"]),
        rule_verdict=rv,
        evidence_quality=_infer_evidence_quality(representative.get("evidence", [])),
        mode_reason="마스킹 환경 — 절차만 평가 (force_t3 자동 True)",
        override_hint=hybrid.get("override_hint") or representative.get("override_hint"),
    )
    if hybrid.get("mandatory_human_review"):
        verdict["mandatory_human_review"] = True
    return attach_persona_meta(verdict, hybrid, ensemble.get("persona_outputs"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_evidence(raw: list[dict], rv: dict | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in raw or []:
        out.append({
            "speaker": e.get("speaker", "상담사"),
            "timestamp": e.get("timestamp"),
            "quote": e.get("text") or e.get("quote", ""),
            "turn_id": int(e.get("turn", e.get("turn_id", 0)) or 0),
        })
    if not out and rv:
        out = rule_evidence_to_evidence_quote(rv)
    return out


def _infer_evidence_quality(evidence: list[dict]) -> str:
    if not evidence:
        return "low"
    if len(evidence) >= 2 and all(e.get("text") or e.get("quote") for e in evidence):
        return "high"
    return "medium"


def _fallback_item(
    item_number: int, item_name: str, rv: dict | None, *, reason: str,
    mode: str = "partial_with_review",
) -> dict[str, Any]:
    score = int((rv or {}).get("score", 0))
    return build_item_verdict(
        item_number=item_number, item_name=item_name, max_score=5, raw_score=score,
        evaluation_mode=mode, judgment=reason,
        evidence=rule_evidence_to_evidence_quote(rv), llm_self=2,
        rule_verdict=rv, evidence_quality="low",
        mode_reason="LLM 실패 — Rule 폴백 + 인간 검수 권고",
    )


def _build_unevaluable_response(llm_backend: str | None, sw: Stopwatch) -> dict[str, Any]:
    items = [
        build_item_verdict(
            item_number=n, item_name=name, max_score=5, raw_score=0,
            evaluation_mode="unevaluable", judgment="STT 품질 저하로 평가 불가",
            evidence=[], llm_self=1, rule_verdict=None, evidence_quality="low",
            mode_reason="quality.unevaluable=True",
        )
        for n, name in [(8, "문의 파악 및 재확인(복창)"), (9, "고객정보 확인")]
    ]
    return build_sub_agent_response(
        category_key=CATEGORY_KEY, agent_id=AGENT_ID, items=items,
        status="partial", llm_backend=llm_backend, elapsed_ms=sw.elapsed_ms,
    )
