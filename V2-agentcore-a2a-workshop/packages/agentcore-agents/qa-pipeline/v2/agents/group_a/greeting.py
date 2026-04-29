# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""인사예절 Sub Agent (Group A #1~#2) — Dev2.

책임:
  - #1 첫인사 (5점, full) — 인사말/소속/상담사명 3요소
  - #2 끝인사 (5점, full) — 2요소 완화(iter03_clean) + STT 잘림 예외
  - Rule 1차 bypass (hybrid 3안) + LLM verify
  - Golden-set RAG Few-shot 주입 (Dev4 API)

Layer 1 수신:
  preprocessing.rule_pre_verdicts["item_01"] / ["item_02"]
  preprocessing.agent_turn_assignments["greeting"] — V1 호환 키

출력: PL 회람 2026-04-20 확정 SubAgentResponse 포맷 (v2/agents/group_a/_shared.py 빌더 경유)
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
    get_assigned_turns,
    get_rule_pre_verdict,
    is_quality_unevaluable,
    rule_evidence_to_evidence_quote,
    run_persona_ensemble,
    should_bypass_llm,
)
from v2.contracts.preprocessing import Preprocessing


logger = logging.getLogger(__name__)

AGENT_ID = "greeting-agent"
CATEGORY_KEY = "greeting_etiquette"


async def greeting_sub_agent(
    *,
    preprocessing: Preprocessing | dict,
    llm_backend: str | None = None,
    bedrock_model_id: str | None = None,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    """1 LLM 호출로 #1 + #2 동시 평가.

    단, hard bypass 항목은 LLM 생략 가능 — #1 이 모든 요소 충족이면 LLM 불필요.
    skeleton 단계에서는 LLM 호출 전체 경로와 Rule bypass 분기만 구현,
    병합 프롬프트 본문은 `v2/prompts/group_a/greeting.md` 참조 (Phase A2 rubric 확정 후 작성).
    """
    with Stopwatch() as sw:
        # 1) STT quality unevaluable → Group A 전체 unevaluable 처리
        if is_quality_unevaluable(preprocessing):
            return _build_unevaluable_response(llm_backend, sw)

        # 2) Rule 1차 판정 수신
        rv1 = get_rule_pre_verdict(preprocessing, 1)
        rv2 = get_rule_pre_verdict(preprocessing, 2)

        # 3) Hybrid 3안 분기 — #1 hard bypass 가능, #2 는 Dev2 정책상 항상 LLM verify
        bypass_1 = should_bypass_llm(rv1)
        # rv2 는 iter03_clean 2요소 완화 + STT 잘림 판정 필수 → LLM 강제
        bypass_2 = False

        # 4) LLM verify 대상만 병렬 호출 (옵션 A — asyncio.gather 로 레이턴시 절감)
        resolved: dict[int, dict[str, Any]] = {}
        tasks: list[tuple[int, Any]] = []

        if bypass_1 and rv1 is not None:
            resolved[1] = _build_bypass_item_1(rv1)
        else:
            tasks.append((1, _llm_evaluate_item_1(
                preprocessing, rv1, llm_backend, bedrock_model_id, tenant_id=tenant_id,
            )))

        if bypass_2 and rv2 is not None:
            resolved[2] = _build_bypass_item_2(rv2)
        else:
            tasks.append((2, _llm_evaluate_item_2(
                preprocessing, rv2, llm_backend, bedrock_model_id, tenant_id=tenant_id,
            )))

        if tasks:
            keys, coros = zip(*tasks)
            # return_exceptions=False — LLMTimeoutError 등 예외는 상위로 전파
            results = await asyncio.gather(*coros, return_exceptions=False)
            for key, result in zip(keys, results):
                resolved[key] = result

        items: list[dict[str, Any]] = [resolved[1], resolved[2]]

    return build_sub_agent_response(
        category_key=CATEGORY_KEY,
        agent_id=AGENT_ID,
        items=items,
        llm_backend=llm_backend,
        elapsed_ms=sw.elapsed_ms,
    )


# ---------------------------------------------------------------------------
# Rule bypass 빌더 (#1 전용 — #2 는 정책상 bypass 없음)
# ---------------------------------------------------------------------------


def _build_bypass_item_1(rv: dict) -> dict[str, Any]:
    return build_item_verdict(
        item_number=1,
        item_name="첫인사",
        max_score=5,
        raw_score=int(rv.get("score", 0)),
        evaluation_mode="full",
        judgment=rv.get("rationale", "Rule 1차 확정 (hard bypass)"),
        evidence=rule_evidence_to_evidence_quote(rv),
        llm_self=5,  # hard bypass 는 rule 확신이 높은 케이스만 허용됨
        rule_verdict=rv,
        evidence_quality="high",
    )


def _build_bypass_item_2(rv: dict) -> dict[str, Any]:
    # 정책상 호출되지 않으나 hybrid 3안 정책 변경 가능성 대비 구현 유지
    return build_item_verdict(
        item_number=2,
        item_name="끝인사",
        max_score=5,
        raw_score=int(rv.get("score", 0)),
        evaluation_mode="full",
        judgment=rv.get("rationale", "Rule 1차 확정 (hard bypass)"),
        evidence=rule_evidence_to_evidence_quote(rv),
        llm_self=5,
        rule_verdict=rv,
        evidence_quality="high",
    )


# ---------------------------------------------------------------------------
# LLM verify 경로 (병합 프롬프트 v2/prompts/group_a/greeting.md)
# ---------------------------------------------------------------------------


async def _llm_evaluate_item_1(
    preprocessing: Preprocessing | dict,
    rv: dict | None,
    llm_backend: str | None,
    bedrock_model_id: str | None,
    *,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    from nodes.llm import LLMTimeoutError
    from v2.prompts.group_a import load_prompt  # Phase A2 rubric 확정 후 구현

    assignment = get_assigned_turns(preprocessing, "greeting")
    first_turns = (assignment.get("turns") or [])[:5]
    turns_text = _format_turns(first_turns) or assignment.get("text", "")

    try:
        system_prompt = load_prompt("greeting", item="item_01", backend=llm_backend)
    except Exception:  # pragma: no cover — skeleton placeholder
        # Phase A2 rubric 확정 전: V1 프롬프트 재사용 (import 전용, 수정 금지)
        from prompts import load_prompt as load_v1_prompt
        system_prompt = load_v1_prompt("item_01_greeting", include_preamble=True, backend=llm_backend)

    # #1 첫인사 는 Rule + LLM verify 항목 — RAG (Few-shot / Reasoning) 미사용.
    # 고정 구간(도입부) 의 인사말/소속/상담사명 3요소 포함 여부만 판정.
    user_message = (
        "## Input (first 5 turns)\n"
        f"{turns_text}\n\n"
        "## Rule pre-verdict\n"
        f"{rv or '없음'}\n\n"
        f"{EVIDENCE_INSTRUCTION}\n"
        "Evaluate item #1 첫인사 per system rules. Return JSON with score/deductions/evidence/confidence/self_confidence."
    )

    # 인사 예절 (#1) 은 구조적 요소 체크 (인사말/소속/상담사명) — 객관적 존재 여부 판정.
    # 3 persona 관점 차이가 의미 없어 single_persona_only=True 로 neutral 1회만 호출.
    try:
        ensemble = await run_persona_ensemble(
            item_number=1,
            item_name="첫인사",
            system_prompt=system_prompt,
            user_message=user_message,
            transcript_slice=turns_text,
            llm_backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
            single_persona_only=True,
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #1 evaluation failed, rule fallback: %s", e)
        return _fallback_item(1, "첫인사", rv, reason=f"LLM 실패 — 규칙 폴백: {e}")

    hybrid = ensemble["hybrid"]
    representative = ensemble["representative"]
    logger.info(
        "[DEBUG #1] persona_votes=%s step_spread=%d merge_path=%s final=%s",
        hybrid.get("persona_votes"), hybrid.get("step_spread"),
        hybrid.get("merge_path"), hybrid.get("final_score"),
    )

    verdict = build_item_verdict(
        item_number=1,
        item_name="첫인사",
        max_score=5,
        raw_score=int(hybrid["final_score"]),
        evaluation_mode="full",
        judgment=representative.get("summary") or representative.get("judgment", ""),
        evidence=_normalize_evidence(representative.get("evidence", []), rv),
        llm_self=int(hybrid["confidence"]),
        rule_verdict=rv,
        evidence_quality=_infer_evidence_quality(representative.get("evidence", [])),
        override_hint=hybrid.get("override_hint") or representative.get("override_hint"),
    )
    # 하이브리드 머지 결과에 따라 mandatory_human_review 갱신 (build_item_verdict 의
    # llm_self 기반 자동 세팅 위에 덮어쓰기 — hybrid 쪽 신호가 우선)
    if hybrid.get("mandatory_human_review"):
        verdict["mandatory_human_review"] = True
    return attach_persona_meta(verdict, hybrid, ensemble.get("persona_outputs"))


async def _llm_evaluate_item_2(
    preprocessing: Preprocessing | dict,
    rv: dict | None,
    llm_backend: str | None,
    bedrock_model_id: str | None,
    *,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    from nodes.llm import LLMTimeoutError
    from v2.prompts.group_a import load_prompt

    assignment = get_assigned_turns(preprocessing, "greeting")
    all_turns = assignment.get("turns") or []
    agent_turns = [t for t in all_turns if t.get("speaker") == "agent"]
    last_turns = (agent_turns[-5:] if agent_turns else all_turns[-5:]) or []
    turns_text = _format_turns(last_turns) or assignment.get("text", "")

    try:
        system_prompt = load_prompt("greeting", item="item_02", backend=llm_backend)
    except Exception:  # pragma: no cover
        from prompts import load_prompt as load_v1_prompt
        system_prompt = load_v1_prompt("item_02_farewell", include_preamble=True, backend=llm_backend)

    # #2 끝인사 는 Rule + LLM verify 항목 — RAG (Few-shot / Reasoning) 미사용.
    # 고정 구간(종료부) 의 인사말/상담사명/추가문의 확인 요소만 판정.
    user_message = (
        "## Input (last 5 agent turns)\n"
        f"{turns_text}\n\n"
        "## Rule pre-verdict\n"
        f"{rv or '없음'}\n\n"
        f"{EVIDENCE_INSTRUCTION}\n"
        "Evaluate item #2 끝인사. iter03_clean 2요소 완화 준수. "
        "STT 잘림 의심 시 stt_truncation_suspected=true, 감점 상한 -2."
    )

    # 인사 예절 (#2) 역시 구조적 체크 (인사말/상담사명/추가문의 확인 요소 유무).
    # 3 persona 불필요 — single_persona_only=True (neutral 1회 호출).
    try:
        ensemble = await run_persona_ensemble(
            item_number=2,
            item_name="끝인사",
            system_prompt=system_prompt,
            user_message=user_message,
            transcript_slice=turns_text,
            llm_backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
            single_persona_only=True,
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        logger.warning("Item #2 evaluation failed, rule fallback: %s", e)
        return _fallback_item(2, "끝인사", rv, reason=f"LLM 실패 — 규칙 폴백: {e}")

    hybrid = ensemble["hybrid"]
    representative = ensemble["representative"]
    logger.info(
        "[DEBUG #2] persona_votes=%s step_spread=%d merge_path=%s final=%s",
        hybrid.get("persona_votes"), hybrid.get("step_spread"),
        hybrid.get("merge_path"), hybrid.get("final_score"),
    )

    verdict = build_item_verdict(
        item_number=2,
        item_name="끝인사",
        max_score=5,
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_turns(turns: list[dict]) -> str:
    return "\n".join(
        f"[Turn {t.get('turn_id', t.get('turn', '?'))}] {t.get('speaker', '?')}: {t.get('text', '')}"
        for t in turns
    )


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
    """LLM 실패 시 Rule 점수로 폴백. reconcile_evaluation 이 [SKIPPED_INFRA] 태그 적용."""
    score = int((rv or {}).get("score", 0))
    return build_item_verdict(
        item_number=item_number,
        item_name=item_name,
        max_score=5,
        raw_score=score,
        evaluation_mode="partial_with_review",
        judgment=reason,
        evidence=rule_evidence_to_evidence_quote(rv),
        llm_self=2,
        rule_verdict=rv,
        evidence_quality="low",
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
        for n, name in [(1, "첫인사"), (2, "끝인사")]
    ]
    return build_sub_agent_response(
        category_key=CATEGORY_KEY, agent_id=AGENT_ID, items=items,
        status="partial", llm_backend=llm_backend, elapsed_ms=sw.elapsed_ms,
    )
