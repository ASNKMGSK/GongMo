# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""언어표현 Sub Agent (Group A #6~#7) — Dev2.

책임:
  - #6 정중한 표현 (5점, full, LLM + 금지어 사전)
  - #7 쿠션어 활용 (5점, full 조건부 — refusal_count=0 이면 skipped 만점)

LLM 호출 정책 (iter03_clean 준수):
  - #6: Rule 0 탐지면 hard bypass 만점, 탐지 시 LLM verify
  - #7: refusal_count=0 이면 무조건 skipped 만점, ≥1 이면 LLM verify
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
    should_bypass_llm,
)
from v2.contracts.preprocessing import Preprocessing


logger = logging.getLogger(__name__)

AGENT_ID = "language-expression-agent"
CATEGORY_KEY = "language_expression"


async def language_sub_agent(
    *,
    preprocessing: Preprocessing | dict,
    llm_backend: str | None = None,
    bedrock_model_id: str | None = None,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    with Stopwatch() as sw:
        if is_quality_unevaluable(preprocessing):
            return _build_unevaluable_response(llm_backend, sw)

        rv6 = get_rule_pre_verdict(preprocessing, 6)
        rv7 = get_rule_pre_verdict(preprocessing, 7)

        # #6 — Rule 0 탐지면 만점 확정, 아니면 LLM verify
        # #7 — refusal-gated (iter03_clean 핵심 규칙): refusal_count=0 → skipped 5점
        # 두 LLM 경로는 asyncio.gather 로 병렬화 (옵션 A)
        resolved: dict[int, dict[str, Any]] = {}
        tasks: list[tuple[int, Any]] = []

        if should_bypass_llm(rv6):
            resolved[6] = _build_bypass_item_6(rv6)
        else:
            tasks.append((6, _llm_evaluate_item_6(preprocessing, rv6, llm_backend, bedrock_model_id, tenant_id=tenant_id)))

        refusal_count = int(((rv7 or {}).get("elements") or {}).get("refusal_count", 0) or 0)
        if refusal_count == 0:
            resolved[7] = _build_skipped_full_item_7(rv7)
        else:
            tasks.append((7, _llm_evaluate_item_7(preprocessing, rv7, llm_backend, bedrock_model_id, tenant_id=tenant_id)))

        if tasks:
            keys, coros = zip(*tasks)
            # return_exceptions=False — LLMTimeoutError 는 상위로 전파
            results = await asyncio.gather(*coros, return_exceptions=False)
            for key, result in zip(keys, results):
                resolved[key] = result

        items = [resolved[6], resolved[7]]

    return build_sub_agent_response(
        category_key=CATEGORY_KEY,
        agent_id=AGENT_ID,
        items=items,
        llm_backend=llm_backend,
        elapsed_ms=sw.elapsed_ms,
    )


# ---------------------------------------------------------------------------
# Bypass / skipped builders
# ---------------------------------------------------------------------------


def _build_bypass_item_6(rv: dict) -> dict[str, Any]:
    return build_item_verdict(
        item_number=6, item_name="정중한 표현", max_score=5,
        raw_score=int(rv.get("score", 5)),
        evaluation_mode="full",
        judgment=rv.get("rationale", "부적절 표현 미감지 — Rule bypass"),
        evidence=rule_evidence_to_evidence_quote(rv),
        llm_self=5, rule_verdict=rv, evidence_quality="high",
    )


def _build_skipped_full_item_7(rv: dict | None) -> dict[str, Any]:
    evidence = rule_evidence_to_evidence_quote(rv) if rv else []
    return build_item_verdict(
        item_number=7, item_name="쿠션어 활용", max_score=5, raw_score=5,
        evaluation_mode="skipped",
        judgment="거절/불가 상황 미발생 — 쿠션어 불필요 (iter03_clean 규칙)",
        evidence=evidence, llm_self=5, rule_verdict=rv,
        evidence_quality="high",
        flag="no_refusal",
        mode_reason="refusal_count=0",
    )


# ---------------------------------------------------------------------------
# LLM verify
# ---------------------------------------------------------------------------


async def _llm_evaluate_item_6(
    preprocessing: Preprocessing | dict,
    rv: dict | None,
    llm_backend: str | None,
    bedrock_model_id: str | None,
    *,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    from nodes.llm import LLMTimeoutError
    from v2.prompts.group_a import load_prompt

    assignment = get_assigned_turns(preprocessing, "courtesy")
    assigned_turns = assignment.get("turns") or []
    agent_turns_text = "\n".join(
        f"turn_{t['turn_id']} 상담사: {t.get('text','')}"
        for t in assigned_turns if t.get("speaker") == "agent"
    ) or assignment.get("text", "")
    pre = (rv or {}).get("elements") or {}

    try:
        system_prompt = load_prompt("language", item="item_06", backend=llm_backend)
    except Exception:  # pragma: no cover
        from prompts import load_prompt as load_v1_prompt
        system_prompt = load_v1_prompt("item_06_polite_expression", include_preamble=True, backend=llm_backend)

    # #6 정중한 표현 은 "LLM + 금지어 사전" 항목 — RAG (Few-shot / Reasoning) 미사용.
    # Pre-Analysis 의 profanity/sigh/language/mild count 가 금지어 사전 1차 필터로 작동,
    # LLM 은 맥락 판정만 담당.
    user_message = (
        f"## 상담사 발화\n{agent_turns_text}\n\n"
        "## Pre-Analysis\n"
        f"- profanity_count: {pre.get('profanity_count', 0)}\n"
        f"- sigh_count: {pre.get('sigh_count', 0)}\n"
        f"- language_count: {pre.get('language_count', 0)}\n"
        f"- mild_count: {pre.get('mild_count', 0)}\n\n"
        f"{EVIDENCE_INSTRUCTION}\n"
        "Evaluate #6 정중한 표현. iter05 구어체 축약('같애요'/'에용') 정상 존대 인정. "
        "#7(쿠션어) 영역은 감점 사유로 사용 금지."
    )

    try:
        ensemble = await run_persona_ensemble(
            item_number=6,
            item_name="정중한 표현",
            system_prompt=system_prompt,
            user_message=user_message,
            transcript_slice=agent_turns_text,
            llm_backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #6 persona ensemble failed, rule fallback: %s", e)
        return _fallback_item(6, "정중한 표현", rv, reason=f"LLM 실패: {e}")

    hybrid = ensemble["hybrid"]
    representative = ensemble["representative"]
    logger.info(
        "[DEBUG #6] persona_votes=%s step_spread=%d merge_path=%s final=%s",
        hybrid.get("persona_votes"), hybrid.get("step_spread"),
        hybrid.get("merge_path"), hybrid.get("final_score"),
    )

    verdict = build_item_verdict(
        item_number=6, item_name="정중한 표현", max_score=5,
        raw_score=int(hybrid["final_score"]),
        evaluation_mode="full",
        judgment=representative.get("summary") or representative.get("judgment", ""),
        evidence=_normalize_evidence(representative.get("evidence", []), rv),
        llm_self=int(hybrid["confidence"]),
        rule_verdict=rv,
        evidence_quality=_infer_evidence_quality(representative.get("evidence", [])),
        override_hint=hybrid.get("override_hint") or representative.get("override_hint"),
    )
    if hybrid.get("mandatory_human_review"):
        verdict["mandatory_human_review"] = True
    return attach_persona_meta(verdict, hybrid, ensemble.get("persona_outputs"))


async def _llm_evaluate_item_7(
    preprocessing: Preprocessing | dict,
    rv: dict | None,
    llm_backend: str | None,
    bedrock_model_id: str | None,
    *,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    from nodes.llm import LLMTimeoutError
    from v2.prompts.group_a import load_prompt

    assignment = get_assigned_turns(preprocessing, "courtesy")
    assigned_turns = assignment.get("turns") or []
    numbered = "\n".join(
        f"[Turn {t['turn_id']}] {t.get('speaker','?')}: {t.get('text','')}"
        for t in assigned_turns
    )
    pre = (rv or {}).get("elements") or {}

    try:
        system_prompt = load_prompt("language", item="item_07", backend=llm_backend)
    except Exception:  # pragma: no cover
        from prompts import load_prompt as load_v1_prompt
        system_prompt = load_v1_prompt("item_07_cushion", backend=llm_backend)

    intent = get_intent(preprocessing)
    fewshot, reasoning = await asyncio.gather(
        async_safe_retrieve_fewshot(7, intent, numbered, top_k=4, tenant_id=tenant_id),
        async_safe_retrieve_reasoning_evidence(7, numbered, top_k=7, tenant_id=tenant_id),
    )
    fewshot_block = format_fewshot_block(fewshot)
    rag_stdev = reasoning["stdev"]

    user_message = (
        f"{fewshot_block}"
        f"## Transcript\n{numbered}\n\n"
        "## Pre-Analysis (iter03_clean 0단계 게이팅)\n"
        f"- refusal_count: {pre.get('refusal_count', 0)}\n"
        f"- cushion_count: {pre.get('cushion_count', 0)}\n\n"
        f"{EVIDENCE_INSTRUCTION}\n"
        "Evaluate #7 쿠션어 활용. refusal_count=0 이면 자동 5점 — 이 단계 전 SubAgent 분기에서 처리됨. "
        "여기는 refusal_count≥1 케이스 전용."
    )

    try:
        ensemble = await run_persona_ensemble(
            item_number=7,
            item_name="쿠션어 활용",
            system_prompt=system_prompt,
            user_message=user_message,
            transcript_slice=numbered,
            llm_backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #7 persona ensemble failed, rule fallback: %s", e)
        return _fallback_item(7, "쿠션어 활용", rv, reason=f"LLM 실패: {e}")

    hybrid = ensemble["hybrid"]
    representative = ensemble["representative"]
    logger.info(
        "[DEBUG #7] persona_votes=%s step_spread=%d merge_path=%s final=%s",
        hybrid.get("persona_votes"), hybrid.get("step_spread"),
        hybrid.get("merge_path"), hybrid.get("final_score"),
    )

    verdict = build_item_verdict(
        item_number=7, item_name="쿠션어 활용", max_score=5,
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
) -> dict[str, Any]:
    score = int((rv or {}).get("score", 0))
    return build_item_verdict(
        item_number=item_number, item_name=item_name, max_score=5, raw_score=score,
        evaluation_mode="partial_with_review", judgment=reason,
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
        for n, name in [(6, "정중한 표현"), (7, "쿠션어 활용")]
    ]
    return build_sub_agent_response(
        category_key=CATEGORY_KEY, agent_id=AGENT_ID, items=items,
        status="partial", llm_backend=llm_backend, elapsed_ms=sw.elapsed_ms,
    )
