# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""경청소통 Sub Agent (Group A #4~#5) — Dev2.

책임:
  - #4 호응/공감 (5점, full, LLM+Few-shot)
  - #5 대기 멘트 (5점, full 조건부 — hold_detected=False 면 skipped 만점)

#3 경청/말겹침 은 2026-04-21 평가표에서 제거됨. 해당 5점은 #15 정확한 안내 로 이관.

LLM 호출 정책:
  - #4 + #5: hold_detected 에 따라 #5 는 skipped / full 분기. #4 단독 또는 #4+#5 병합 LLM 호출
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

AGENT_ID = "listening-communication-agent"
CATEGORY_KEY = "listening_communication"


async def listening_comm_sub_agent(
    *,
    preprocessing: Preprocessing | dict,
    llm_backend: str | None = None,
    bedrock_model_id: str | None = None,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    with Stopwatch() as sw:
        if is_quality_unevaluable(preprocessing):
            return _build_unevaluable_response(llm_backend, sw)

        rv4 = get_rule_pre_verdict(preprocessing, 4)
        rv5 = get_rule_pre_verdict(preprocessing, 5)

        # #5 — hold_detected=False 면 skipped 만점
        hold_detected = bool(((rv5 or {}).get("elements") or {}).get("hold_detected", False))

        # #4 는 항상 LLM, #5 는 hold_detected 에 따라 분기 — 둘 다 LLM 경로면 병렬화 (옵션 A)
        resolved: dict[int, dict[str, Any]] = {}
        tasks: list[tuple[int, Any]] = []

        tasks.append((4, _llm_evaluate_item_4(preprocessing, rv4, llm_backend, bedrock_model_id, tenant_id=tenant_id)))
        if hold_detected:
            tasks.append((5, _llm_evaluate_item_5(preprocessing, rv5, llm_backend, bedrock_model_id, tenant_id=tenant_id)))
        else:
            resolved[5] = _build_skipped_full_item_5(rv5, reason="대기 미발생")

        if tasks:
            keys, coros = zip(*tasks)
            # return_exceptions=False — LLMTimeoutError 는 상위로 전파
            results = await asyncio.gather(*coros, return_exceptions=False)
            for key, result in zip(keys, results):
                resolved[key] = result

        items = [resolved[4], resolved[5]]

    return build_sub_agent_response(
        category_key=CATEGORY_KEY,
        agent_id=AGENT_ID,
        items=items,
        llm_backend=llm_backend,
        elapsed_ms=sw.elapsed_ms,
    )


# ---------------------------------------------------------------------------
# Skipped full builders
# ---------------------------------------------------------------------------


def _build_skipped_full_item_5(rv: dict | None, *, reason: str) -> dict[str, Any]:
    evidence = rule_evidence_to_evidence_quote(rv) if rv else []
    return build_item_verdict(
        item_number=5,
        item_name="대기 멘트",
        max_score=5,
        raw_score=5,
        evaluation_mode="skipped",
        judgment=f"대기 상황 미감지 — 만점 고정 ({reason})",
        evidence=evidence,
        llm_self=5,
        rule_verdict=rv,
        evidence_quality="high",
        mode_reason=reason,
    )


# ---------------------------------------------------------------------------
# LLM verify #4, #5
# ---------------------------------------------------------------------------


async def _llm_evaluate_item_4(
    preprocessing: Preprocessing | dict,
    rv: dict | None,
    llm_backend: str | None,
    bedrock_model_id: str | None,
    *,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    from nodes.llm import LLMTimeoutError
    from v2.prompts.group_a import load_prompt

    assignment = get_assigned_turns(preprocessing, "understanding")
    assigned_turns = assignment.get("turns") or []
    numbered = "\n".join(f"[Turn {t['turn_id']}] {t.get('speaker','?')}: {t.get('text','')}" for t in assigned_turns)
    pre = (rv or {}).get("elements") or {}

    try:
        system_prompt = load_prompt("listening_comm", item="item_04", backend=llm_backend)
    except Exception:  # pragma: no cover — skeleton fallback
        from prompts import load_prompt as load_v1_prompt
        system_prompt = load_v1_prompt("item_04_empathy", backend=llm_backend)

    # RAG 1회만 (persona 독립) — fewshot + reasoning 병렬
    intent = get_intent(preprocessing)
    fewshot, reasoning = await asyncio.gather(
        async_safe_retrieve_fewshot(4, intent, numbered, top_k=4, tenant_id=tenant_id),
        async_safe_retrieve_reasoning_evidence(4, numbered, top_k=7, tenant_id=tenant_id),
    )
    fewshot_block = format_fewshot_block(fewshot)
    rag_stdev = reasoning["stdev"]

    user_message = (
        f"{fewshot_block}"
        f"## Transcript\n{numbered}\n\n"
        "## Pre-Analysis\n"
        f"- empathy_count: {pre.get('empathy_count', 0)}\n"
        f"- simple_response_count: {pre.get('simple_response_count', 0)}\n\n"
        f"{EVIDENCE_INSTRUCTION}\n"
        "Evaluate #4 호응 및 공감. 상담사(agent) 발화만 평가. "
        "실질 공감 키워드(그러셨군요/죄송합니다/이해합니다/불편하셨...) 1회 이상 → 5점."
    )

    # 경청 및 소통 (#4 호응 및 공감) — 사용자 지시 (2026-04-28): multi-persona 모드.
    # 처리방식 "LLM + Few-shot" 으로 주관 판정 요소 있음 — 3 페르소나 관점 차이 유의미.
    try:
        ensemble = await run_persona_ensemble(
            item_number=4,
            item_name="호응 및 공감",
            system_prompt=system_prompt,
            user_message=user_message,
            transcript_slice=numbered,
            llm_backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
            single_persona_only=False,
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #4 evaluation failed, rule fallback: %s", e)
        return _fallback_item(4, "호응 및 공감", rv, reason=f"LLM 실패: {e}")

    hybrid = ensemble["hybrid"]
    representative = ensemble["representative"]
    logger.info(
        "[DEBUG #4] persona_votes=%s step_spread=%d merge_path=%s final=%s",
        hybrid.get("persona_votes"), hybrid.get("step_spread"),
        hybrid.get("merge_path"), hybrid.get("final_score"),
    )

    verdict = build_item_verdict(
        item_number=4, item_name="호응 및 공감", max_score=5,
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


async def _llm_evaluate_item_5(
    preprocessing: Preprocessing | dict,
    rv: dict | None,
    llm_backend: str | None,
    bedrock_model_id: str | None,
    *,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    from nodes.llm import LLMTimeoutError
    from v2.prompts.group_a import load_prompt

    assignment = get_assigned_turns(preprocessing, "understanding")
    assigned_turns = assignment.get("turns") or []
    numbered = "\n".join(f"[Turn {t['turn_id']}] {t.get('speaker','?')}: {t.get('text','')}" for t in assigned_turns)
    pre = (rv or {}).get("elements") or {}

    try:
        system_prompt = load_prompt("listening_comm", item="item_05", backend=llm_backend)
    except Exception:  # pragma: no cover
        from prompts import load_prompt as load_v1_prompt
        system_prompt = load_v1_prompt("item_05_hold_mention", include_preamble=True, backend=llm_backend)

    intent = get_intent(preprocessing)
    fewshot, reasoning = await asyncio.gather(
        async_safe_retrieve_fewshot(5, intent, numbered, top_k=4, tenant_id=tenant_id),
        async_safe_retrieve_reasoning_evidence(5, numbered, top_k=7, tenant_id=tenant_id),
    )
    fewshot_block = format_fewshot_block(fewshot)
    rag_stdev = reasoning["stdev"]

    user_message = (
        f"{fewshot_block}"
        f"## Transcript\n{numbered}\n\n"
        "## Pre-Analysis\n"
        f"- hold_detected: {pre.get('hold_detected', True)}\n"
        f"- before_count: {pre.get('before_count', 0)}\n"
        f"- after_count: {pre.get('after_count', 0)}\n"
        f"- silence_count: {pre.get('silence_count', 0)}\n\n"
        f"{EVIDENCE_INSTRUCTION}\n"
        "Evaluate #5 대기 멘트 — 대기 전 양해 유/무로 판정. 사후 감사는 가점 요소."
    )

    # 경청 및 소통 (#5 대기 멘트) — 사용자 지시 (2026-04-28): xlsx 처리방식 LLM + Few-shot 이므로 multi.
    try:
        ensemble = await run_persona_ensemble(
            item_number=5,
            item_name="대기 멘트",
            system_prompt=system_prompt,
            user_message=user_message,
            transcript_slice=numbered,
            llm_backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
            single_persona_only=False,
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #5 evaluation failed, rule fallback: %s", e)
        return _fallback_item(5, "대기 멘트", rv, reason=f"LLM 실패: {e}")

    hybrid = ensemble["hybrid"]
    representative = ensemble["representative"]
    logger.info(
        "[DEBUG #5] persona_votes=%s step_spread=%d merge_path=%s final=%s",
        hybrid.get("persona_votes"), hybrid.get("step_spread"),
        hybrid.get("merge_path"), hybrid.get("final_score"),
    )

    verdict = build_item_verdict(
        item_number=5, item_name="대기 멘트", max_score=5,
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
# Helpers (공용화 후보 — _shared.py 로 이관 고려)
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
        for n, name in [(4, "호응 및 공감"), (5, "대기 멘트")]
    ]
    return build_sub_agent_response(
        category_key=CATEGORY_KEY, agent_id=AGENT_ID, items=items,
        status="partial", llm_backend=llm_backend, elapsed_ms=sw.elapsed_ms,
    )
