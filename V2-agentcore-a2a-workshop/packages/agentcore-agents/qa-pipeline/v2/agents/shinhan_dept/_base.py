# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""신한 부서특화 sub-agent factory.

각 부서 노드 (`coll_accuracy` / `iss_terms_compliance` / ...) 는 동일한 패턴으로 동작:
  1. transcript 전체를 LLM 에 제시
  2. 노드의 sub-items 별로 점수 + 판정 + evidence 산출
  3. SubAgentResponse 형태로 반환

LLM backend 는 group_b 의 call_bedrock_json 재사용 — 환경변수 V2_GROUP_B_SKIP_LLM=1
이면 rule fallback (모든 sub-item 만점 + skipped 모드) 로 동작.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, cast

import asyncio

from v2.agents.group_a._shared import make_rag_evidence
from v2.agents.group_b._llm import LLMTimeoutError, call_bedrock_json
from v2.agents.shinhan_dept.registry import DEPT_NODE_REGISTRY, DeptNodeSpec
from v2.reconciler_personas import PERSONAS, apply_persona_prefix, force_single_persona
from v2.rag import retrieve_knowledge
from v2.rag.types import RAGUnavailable
from v2.schemas.sub_agent_io import (
    DeductionEntry,
    EvidenceQuote,
    ItemVerdict,
    LLMSelfConfidence,
    SubAgentResponse,
)


logger = logging.getLogger(__name__)


# ===========================================================================
# Score snap helper — synthetic dept items 용
# ===========================================================================


def _snap_to_steps(raw_score: int, allowed_steps: list[int]) -> int:
    """raw_score 를 allowed_steps 중 가장 가까운 값으로 변환.

    동률이면 더 낮은 단계 선택 (보수적 채점).
    """
    if not allowed_steps:
        return raw_score
    sorted_steps = sorted(allowed_steps, reverse=True)
    best = sorted_steps[-1]  # 0
    best_diff = abs(raw_score - best)
    for step in sorted_steps:
        diff = abs(raw_score - step)
        if diff < best_diff or (diff == best_diff and step < best):
            best = step
            best_diff = diff
    return best


# ===========================================================================
# Prompt builder
# ===========================================================================


_SYSTEM_PROMPT_TEMPLATE = """당신은 신한카드 {team_label} 의 인바운드 상담 QA 평가자입니다.

평가 노드: **{label_ko}** (총 {max_score}점)
평가 포커스: {rubric_focus}

다음 평가항목 각각에 대해 점수 / 판정 사유 / 근거 발화를 산출하세요.

## 평가항목

{items_block}

## 출력 형식 (JSON 만 반환)

```json
{{
  "items": [
    {{
      "item_number": <int>,
      "score": <int — allowed_steps 중 1개>,
      "judgment": "<한 줄 판정 사유>",
      "deductions": [{{"reason": "<감점 사유>", "points": <int>}}],
      "evidence": [{{"speaker": "상담사|고객", "quote": "<원문 인용>"}}],
      "self_confidence": <1~5 정수>
    }}
  ],
  "category_judgment": "<노드 전체 한 줄 요약>",
  "category_confidence": <1~5 정수>
}}
```

## 판정 원칙

- 근거 없는 추측 금지. evidence 인용은 transcript 원문 그대로.
- score 는 반드시 allowed_steps 중 하나 (중간값 금지).
- LLM 실패/판단 불가 시 self_confidence=1 + judgment 에 "판단 불가" 명시.

## ★ 항목 RAG-gating (정확성 검증 항목)

평가항목명에 **★** 가 표시된 항목 (예: "정확한 안내 ★", "설명의무 이행 ★") 은 외부 ground truth
(업무지식 RAG / 회사 매뉴얼 / 시스템 데이터) 대조가 본질이다.

- **★ 항목 + `## 업무지식 RAG hits` 섹션 hit 있음**: chunk 의 표준 정보 (금액·기한·계좌·이자율·약관 등)
  와 transcript 발화를 사실 일치 비교하여 점수 산출.
  - 일치 + 5요소 모두 안내 + 복창: 만점
  - 일치하나 복창 누락: 한 단계 감점
  - 불일치하나 즉시 정정: 부분 점수
  - 불일치 + 정정 미시도: 0점
- **★ 항목 + RAG hits 비어있음 (또는 "매칭된 chunk 없음")**:
  → 코드가 자동으로 score=0 + partial_with_review 강제. LLM 응답은 무시되니 그대로 임의 점수
  반환해도 무방. 다만 judgment 에 "RAG 부재 — 인간 검수 필수" 로 명시할 것.

비-★ 항목 (필수 안내 / 절차 준수 등) 은 RAG 유무와 무관하게 transcript 자체로 평가.
"""


def _build_items_block(spec: DeptNodeSpec) -> str:
    lines = []
    for it in spec["items"]:
        steps_str = " / ".join(str(s) for s in it["allowed_steps"])
        lines.append(
            f"- **#{it['item_number']} {it['item_name']}** "
            f"(max {it['max_score']}점, allowed_steps: [{steps_str}])"
        )
    return "\n".join(lines)


_TEAM_LABEL_KO: dict[str, str] = {
    "collection": "컬렉션관리부",
    "review": "심사발급부",
    "crm": "CRM부",
    "consumer": "소비자보호부",
    "compliance": "준법관리부",
}


# team_id → (channel, department) 매핑 — 신한카드 부서별 RAG 라우팅용.
# tenants/shinhan/{channel}/{department}/business_knowledge/manual.md 경로와 1:1.
_TEAM_TO_CHANNEL_DEPT: dict[str, tuple[str, str]] = {
    "collection": ("inbound", "collection"),
    "consumer": ("inbound", "consumer"),
    "review": ("inbound", "review"),
    "compliance": ("outbound", "compliance"),
    "crm": ("outbound", "crm"),
}


def _build_system_prompt(spec: DeptNodeSpec) -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(
        team_label=_TEAM_LABEL_KO.get(spec["team_id"], spec["team_id"]),
        label_ko=spec["label_ko"],
        max_score=spec["max_score"],
        rubric_focus=spec["rubric_focus"],
        items_block=_build_items_block(spec),
    )


def _build_user_message(
    transcript: str,
    intent_summary: dict | None,
    knowledge: Any | None = None,
) -> str:
    intent_block = ""
    if intent_summary:
        primary = intent_summary.get("primary_intent") or "*"
        product = intent_summary.get("product") or ""
        intent_block = f"\n## 상담 인텐트\n- primary_intent: {primary}\n- product: {product}\n"

    # 업무지식 RAG hits — chunk 가 있으면 LLM 이 사실 일치 판정에 활용. 없으면 transcript only.
    rag_block = ""
    if knowledge is not None:
        chunks = list(getattr(knowledge, "chunks", None) or [])
        if chunks:
            lines = ["\n## 업무지식 RAG hits"]
            for c in chunks[:3]:
                cid = getattr(c, "chunk_id", "?")
                score = float(getattr(c, "score", 0.0) or 0.0)
                text = (getattr(c, "text", "") or "")[:300]
                lines.append(f"- [{cid} score={score:.2f}] {text}")
            mr = getattr(knowledge, "match_reason", "") or ""
            lines.append(f"(match_reason: {mr})\n")
            rag_block = "\n".join(lines)
        else:
            mr = getattr(knowledge, "match_reason", "") or "no chunks"
            rag_block = (
                f"\n## 업무지식 RAG hits\n"
                f"(매칭된 chunk 없음 — transcript only 평가; reason: {mr})\n"
            )

    return f"## STT Transcript{intent_block}{rag_block}\n\n{transcript}\n"


# ===========================================================================
# Rule fallback — LLM 실패 시 unevaluable 처리 ([SKIPPED_INFRA] 태그)
#
# Dev2 O4 규약: skipped=만점 / unevaluable=합산 제외 (분모/분자 동시 조정).
# 인프라 실패 (Bedrock 인증/타임아웃) 는 "평가가 안 된 것" 이지 만점이 아님.
# score=None + evaluation_mode="unevaluable" 로 표기해 Layer 3 가 합산에서 제외.
# ===========================================================================


def _rule_fallback_items(spec: DeptNodeSpec, reason: str) -> list[ItemVerdict]:
    items: list[ItemVerdict] = []
    for it in spec["items"]:
        evidence_placeholder: list[EvidenceQuote] = [{
            "speaker": "system",
            "quote": f"[SKIPPED_INFRA] LLM 호출 실패로 평가 보류 — {reason}",
            "timestamp": None,
            "turn_id": None,
        }]
        verdict: ItemVerdict = {
            "item_number": it["item_number"],
            "item_name": it["item_name"],
            "max_score": it["max_score"],
            "score": None,  # 합산에서 제외 (Dev2 O4 규약 — unevaluable)
            "evaluation_mode": "unevaluable",
            "judgment": f"[SKIPPED_INFRA] LLM 호출 실패로 평가 보류 — 인프라 복구 후 재실행 필요 ({reason})",
            "mode_reason": reason,
            "deductions": [],
            "evidence": evidence_placeholder,
            "llm_self_confidence": cast(LLMSelfConfidence, {"value": 1, "reason": reason}),
            "infra_tags": ["[SKIPPED_INFRA]"],
        }
        items.append(verdict)
    return items


# ===========================================================================
# LLM raw → ItemVerdict 변환
# ===========================================================================


def _convert_raw_to_verdicts(spec: DeptNodeSpec, raw: dict) -> list[ItemVerdict]:
    raw_items = raw.get("items") or []
    raw_by_num: dict[int, dict] = {}
    for r in raw_items:
        try:
            raw_by_num[int(r.get("item_number", 0))] = r
        except (TypeError, ValueError):
            continue

    verdicts: list[ItemVerdict] = []
    for it in spec["items"]:
        item_no = it["item_number"]
        r = raw_by_num.get(item_no, {})
        try:
            raw_score = int(r.get("score", 0))
        except (TypeError, ValueError):
            raw_score = 0
        snapped = _snap_to_steps(raw_score, it["allowed_steps"])

        evidence_list: list[EvidenceQuote] = []
        for e in (r.get("evidence") or [])[:3]:
            if not isinstance(e, dict):
                continue
            evidence_list.append({
                "speaker": str(e.get("speaker", "상담사")),
                "quote": str(e.get("quote", ""))[:300],
                "timestamp": None,
                "turn_id": None,
            })
        if not evidence_list:
            evidence_list.append({
                "speaker": "상담사", "quote": "(근거 없음)", "timestamp": None, "turn_id": None,
            })

        deductions: list[DeductionEntry] = []
        for d in (r.get("deductions") or [])[:5]:
            if not isinstance(d, dict):
                continue
            try:
                pts = int(d.get("points", 0))
            except (TypeError, ValueError):
                pts = 0
            deductions.append({
                "reason": str(d.get("reason", ""))[:200],
                "points": pts,
                "rule_id": None,
                "evidence_refs": [],
            })

        try:
            self_conf = max(1, min(5, int(r.get("self_confidence", 3))))
        except (TypeError, ValueError):
            self_conf = 3

        verdict: ItemVerdict = {
            "item_number": item_no,
            "item_name": it["item_name"],
            "max_score": it["max_score"],
            "score": snapped,
            "evaluation_mode": "full",
            "judgment": str(r.get("judgment", ""))[:300] or "(판정 미제공)",
            "deductions": deductions,
            "evidence": evidence_list,
            "llm_self_confidence": cast(LLMSelfConfidence, {"value": self_conf, "reason": ""}),
            "infra_tags": [],
        }
        verdicts.append(verdict)
    return verdicts


# ===========================================================================
# Multi-persona 점수 합의 (median + step-snap)
# ===========================================================================


def _median(nums: list[int]) -> int:
    """정수 리스트 median. 짝수 개수 시 하위값 (보수적 채점)."""
    if not nums:
        return 0
    sorted_n = sorted(nums)
    mid = len(sorted_n) // 2
    if len(sorted_n) % 2 == 1:
        return sorted_n[mid]
    return sorted_n[mid - 1]  # 보수적 — 더 낮은 값


def _merge_persona_items(spec: DeptNodeSpec, persona_outputs: dict[str, dict[str, Any]]) -> list[ItemVerdict]:
    """3-persona LLM 응답들을 item 별로 합의해 ItemVerdict 리스트 생성.

    - 각 persona 의 items[] 에서 동일 item_number 의 score 추출
    - median → snap_to_steps → 최종 score
    - representative (neutral 우선) 의 judgment / evidence / deductions 채택
    - persona_votes / persona_step_spread 메타 추가
    """
    rep = persona_outputs.get("neutral") or next(iter(persona_outputs.values()))
    rep_items = {int(it.get("item_number", 0)): it for it in (rep.get("items") or [])}

    # persona 별 item_number → score 맵
    persona_scores: dict[str, dict[int, int]] = {}
    persona_judgments: dict[str, dict[int, str]] = {}
    for persona, raw in persona_outputs.items():
        scores: dict[int, int] = {}
        judgments: dict[int, str] = {}
        for r in raw.get("items") or []:
            try:
                inum = int(r.get("item_number", 0))
                scores[inum] = int(r.get("score", 0) or 0)
                judgments[inum] = str(r.get("judgment", ""))[:300]
            except (TypeError, ValueError):
                continue
        persona_scores[persona] = scores
        persona_judgments[persona] = judgments

    out: list[ItemVerdict] = []
    for it in spec["items"]:
        inum = it["item_number"]
        votes_raw: list[int] = []
        votes_dict: dict[str, int] = {}
        for persona in PERSONAS:
            sc = persona_scores.get(persona, {}).get(inum)
            if sc is not None:
                snapped = _snap_to_steps(int(sc), it["allowed_steps"])
                votes_raw.append(snapped)
                votes_dict[persona] = snapped

        if not votes_raw:
            # 모든 persona 가 이 item 에 대해 점수 누락 → unevaluable
            out.append({
                "item_number": inum,
                "item_name": it["item_name"],
                "max_score": it["max_score"],
                "score": None,
                "evaluation_mode": "unevaluable",
                "judgment": "[SKIPPED_INFRA] 모든 persona 응답 누락",
                "deductions": [],
                "evidence": [{
                    "speaker": "system",
                    "quote": "[SKIPPED_INFRA] 3-persona 모두 응답 누락",
                    "timestamp": None,
                    "turn_id": None,
                }],
                "llm_self_confidence": cast(LLMSelfConfidence, {"value": 1, "reason": "all-persona miss"}),
                "infra_tags": ["[SKIPPED_INFRA]"],
            })
            continue

        merged_score = _snap_to_steps(_median(votes_raw), it["allowed_steps"])
        step_spread = max(votes_raw) - min(votes_raw) if len(votes_raw) > 1 else 0

        # representative 의 evidence / deductions / judgment
        rep_item = rep_items.get(inum, {})
        evidence_list: list[EvidenceQuote] = []
        for e in (rep_item.get("evidence") or [])[:3]:
            if not isinstance(e, dict):
                continue
            evidence_list.append({
                "speaker": str(e.get("speaker", "상담사")),
                "quote": str(e.get("quote", ""))[:300],
                "timestamp": None,
                "turn_id": None,
            })
        if not evidence_list:
            evidence_list.append({"speaker": "상담사", "quote": "(근거 없음)", "timestamp": None, "turn_id": None})

        deductions: list[DeductionEntry] = []
        for d in (rep_item.get("deductions") or [])[:5]:
            if not isinstance(d, dict):
                continue
            try:
                pts = int(d.get("points", 0))
            except (TypeError, ValueError):
                pts = 0
            deductions.append({
                "reason": str(d.get("reason", ""))[:200],
                "points": pts,
                "rule_id": None,
                "evidence_refs": [],
            })

        try:
            self_conf = max(1, min(5, int(rep_item.get("self_confidence", 3))))
        except (TypeError, ValueError):
            self_conf = 3

        verdict: ItemVerdict = {
            "item_number": inum,
            "item_name": it["item_name"],
            "max_score": it["max_score"],
            "score": merged_score,
            "evaluation_mode": "full",
            "judgment": str(rep_item.get("judgment", ""))[:300] or "(판정 미제공)",
            "deductions": deductions,
            "evidence": evidence_list,
            "llm_self_confidence": cast(LLMSelfConfidence, {"value": self_conf, "reason": ""}),
            "infra_tags": [],
            "persona_votes": votes_dict,
            "persona_step_spread": step_spread,
            "persona_merge_path": "stats" if step_spread == 0 else "judge_consensus",
            "persona_merge_rule": "consensus" if step_spread == 0 else "median",
            "persona_details": {
                p: {
                    "score": persona_scores.get(p, {}).get(inum),
                    "judgment": persona_judgments.get(p, {}).get(inum, ""),
                }
                for p in PERSONAS if p in persona_outputs
            },
        }
        out.append(verdict)
    return out


# ===========================================================================
# Sub-agent factory
# ===========================================================================


def make_dept_agent(node_id: str) -> Callable:
    """node_id 에 해당하는 부서특화 sub-agent callable 생성.

    반환된 callable 시그니처:
        async def agent(*, transcript, assigned_turns=None, consultation_type=None,
                        intent_summary=None, preprocessing=None, tenant_id="generic",
                        team_id=None, llm_backend="bedrock", bedrock_model_id=None)
            -> tuple[SubAgentResponse, dict]
    """
    spec = DEPT_NODE_REGISTRY.get(node_id)
    if spec is None:
        raise KeyError(f"Unknown dept node_id: {node_id}")

    system_prompt = _build_system_prompt(spec)
    agent_id = f"shinhan-{node_id}"

    # 노드 mode (xlsx 처리방식 정합) — "multi" | "single"
    node_mode = (spec.get("mode") or "multi").lower()

    async def _call_persona(persona: str, user_msg: str, llm_backend_: str, model_id: str | None) -> dict[str, Any] | None:
        """단일 persona LLM 호출. 실패 시 None 반환."""
        sys_prompt = apply_persona_prefix(system_prompt, persona)
        try:
            raw = await call_bedrock_json(
                system_prompt=sys_prompt,
                user_message=user_msg,
                max_tokens=2048,
                backend=llm_backend_,
                bedrock_model_id=model_id,
            )
            return raw
        except LLMTimeoutError:
            raise
        except Exception as e:
            logger.warning("[%s persona=%s] LLM 실패: %s", agent_id, persona, e)
            return None

    async def _agent(
        *,
        transcript: str,
        assigned_turns: list[dict] | None = None,  # noqa: ARG001 — 시그니처 호환
        consultation_type: str | None = None,  # noqa: ARG001
        intent_summary: dict | None = None,
        rule_pre_verdicts: dict | None = None,  # noqa: ARG001
        preprocessing: dict | None = None,  # noqa: ARG001
        tenant_id: str = "generic",
        team_id: str | None = None,  # noqa: ARG001
        llm_backend: str = "bedrock",
        bedrock_model_id: str | None = None,
    ) -> tuple[SubAgentResponse, dict[str, Any]]:
        started = time.perf_counter()
        items: list[ItemVerdict]
        error_msg: str | None = None

        # === 업무지식 RAG 조회 (graceful — 실패/누락 시 transcript-only 평가로 폴백) ===
        # 부서특화 노드 (coll_accuracy / iss_accuracy / crm_accuracy / cons_complaint /
        # comp_unfair_sale_check 등) 는 도메인 지식 검증이 필요. tenants/{tid}/{channel}/
        # {department}/business_knowledge/manual.md 의 chunk 와 transcript 간 사실 일치를
        # LLM 이 판정. team_id → channel/department 매핑은 _TEAM_TO_CHANNEL_DEPT 사용.
        intent = ((intent_summary or {}).get("primary_intent")) or "*"
        knowledge = None
        knowledge_query = f"{spec['rubric_focus']}\n{transcript[:1500]}"
        rag_channel, rag_department = _TEAM_TO_CHANNEL_DEPT.get(
            spec["team_id"], ("inbound", "default")
        )
        try:
            knowledge = await asyncio.to_thread(
                retrieve_knowledge,
                intent=intent,
                query=knowledge_query,
                tenant_id=tenant_id,
                channel=rag_channel,
                department=rag_department,
                top_k=3,
            )
        except RAGUnavailable as e:
            logger.info("[%s] business_knowledge unavailable: %s", agent_id, e)
        except Exception as e:  # noqa: BLE001 — RAG 실패는 평가 자체를 막지 않음
            logger.warning("[%s] business_knowledge retrieve 실패 (transcript only 진행): %s", agent_id, e)

        user_msg = _build_user_message(transcript, intent_summary, knowledge=knowledge)

        # 모드 결정 — node_mode + global force_single 토글
        effective_mode = "single" if force_single_persona() or node_mode == "single" else "multi"
        logger.info("[%s] mode=%s (node_mode=%s)", agent_id, effective_mode, node_mode)

        try:
            if effective_mode == "single":
                # neutral 1회 호출
                raw = await _call_persona("neutral", user_msg, llm_backend, bedrock_model_id)
                if raw is None:
                    raise RuntimeError("single-persona LLM returned None")
                items = _convert_raw_to_verdicts(spec, raw)
                category_judgment = str(raw.get("category_judgment", ""))[:300]
                try:
                    cat_conf = max(1, min(5, int(raw.get("category_confidence", 3))))
                except (TypeError, ValueError):
                    cat_conf = 3
                status: str = "success"
                persona_outputs_used: dict[str, dict[str, Any]] = {"neutral": raw}
            else:
                # multi — strict/neutral/loose 병렬 호출
                results = await asyncio.gather(
                    _call_persona("strict", user_msg, llm_backend, bedrock_model_id),
                    _call_persona("neutral", user_msg, llm_backend, bedrock_model_id),
                    _call_persona("loose", user_msg, llm_backend, bedrock_model_id),
                    return_exceptions=False,
                )
                persona_outputs_used = {p: r for p, r in zip(PERSONAS, results, strict=False) if r is not None}
                if not persona_outputs_used:
                    raise RuntimeError("multi-persona 3 모두 실패")
                # neutral 우선, 없으면 첫 응답을 representative
                rep = persona_outputs_used.get("neutral") or next(iter(persona_outputs_used.values()))
                # 각 item 별로 strict/neutral/loose 점수 median 합의
                items = _merge_persona_items(spec, persona_outputs_used)
                category_judgment = str(rep.get("category_judgment", ""))[:300]
                try:
                    cat_conf = max(1, min(5, int(rep.get("category_confidence", 3))))
                except (TypeError, ValueError):
                    cat_conf = 3
                status = "success"
        except LLMTimeoutError:
            raise
        except Exception as e:  # pragma: no cover — rule fallback
            logger.warning("[%s] LLM 실패 → rule fallback: %s", agent_id, e)
            items = _rule_fallback_items(spec, f"LLM 실패: {type(e).__name__}")
            category_judgment = "[SKIPPED_INFRA] LLM fallback"
            cat_conf = 1
            error_msg = f"{type(e).__name__}: {e}"
            status = "fallback"
            persona_outputs_used = {}

        # Dev2 O4 규약 — unevaluable 항목은 합산에서 제외 (max 도 차감)
        scored_items = [it for it in items if it.get("evaluation_mode") != "unevaluable"]
        category_score = sum((it.get("score") or 0) for it in scored_items)
        category_max = sum(it.get("max_score", 0) for it in scored_items) if scored_items else 0
        # 모든 항목이 unevaluable 인 경우 → category_score=None 명시 (UI 가 "—" 표시)
        if not scored_items:
            category_score = None  # type: ignore[assignment]
            category_max = 0
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        # === 업무지식 RAG evidence — 각 ItemVerdict 에 attach (frontend 진단 박스 / RagHitsPanel 가 인식) ===
        knowledge_chunks: list[Any] = []
        if knowledge is not None:
            knowledge_chunks = list(getattr(knowledge, "chunks", None) or [])
            knowledge_chunk_ids = [getattr(c, "chunk_id", "") or "" for c in knowledge_chunks]
            rag_evidence = make_rag_evidence(
                knowledge_chunk_ids=knowledge_chunk_ids,
                knowledge_chunks=knowledge_chunks,
                knowledge_query=knowledge_query[:500],
                intent=intent,
            )
            for it in items:
                # ItemVerdict 는 TypedDict total=False — typed-key 외 필드 attach 가능
                it["rag_evidence"] = rag_evidence  # type: ignore[typeddict-unknown-key]

        # === ★ 항목 RAG-gating (강제) — 사용자 정책 (2026-04-30) ===
        # ★ 항목은 외부 ground truth (업무지식 RAG / 시스템 데이터) 대조가 본질. RAG hit 0건 또는
        # unevaluable=True 면 LLM 점수 무시 → score=0 + partial_with_review + 인간 검수 의무.
        # ★ 항목에 RAG hit 가 있으면 LLM 이 chunks 와 transcript 비교 후 결정한 점수를 그대로 채택.
        # 비-★ 항목은 RAG 유무와 무관 — LLM 이 transcript 만으로 평가 가능.
        rag_unevaluable = (
            knowledge is None
            or bool(getattr(knowledge, "unevaluable", False))
            or len(knowledge_chunks) == 0
        )
        if rag_unevaluable:
            mr = (
                getattr(knowledge, "match_reason", "RAG 호출 자체 실패")
                if knowledge is not None
                else "RAG 호출 자체 실패"
            )
            mutated = False
            for it in items:
                if "★" in (it.get("item_name") or ""):
                    it["score"] = 0
                    it["evaluation_mode"] = "partial_with_review"
                    it["judgment"] = (
                        "업무지식 RAG 부재로 정확성 검증 불가 — 인간 검수 필수 "
                        f"(reason: {mr})"
                    )
                    it["mode_reason"] = f"★ 항목 RAG-gating: {mr}"
                    it["mandatory_human_review"] = True  # type: ignore[typeddict-unknown-key]
                    # llm_self_confidence 도 1로 낮춤 (검증 불가 신호)
                    it["llm_self_confidence"] = cast(
                        LLMSelfConfidence, {"value": 1, "reason": f"RAG miss: {mr}"}
                    )
                    mutated = True
            if mutated:
                # ★ 강제 후 합산 재계산 — partial_with_review 는 분모/분자 모두 포함 (Dev2 O4 규약)
                scored_items = [
                    it for it in items if it.get("evaluation_mode") != "unevaluable"
                ]
                category_score = sum((it.get("score") or 0) for it in scored_items)
                category_max = (
                    sum(it.get("max_score", 0) for it in scored_items) if scored_items else 0
                )
                if not scored_items:
                    category_score = None  # type: ignore[assignment]
                    category_max = 0
                logger.info(
                    "[%s] ★ 항목 RAG-gating 발동: %d items → score=0/partial_with_review (reason: %s)",
                    agent_id,
                    sum(1 for it in items if "★" in (it.get("item_name") or "")),
                    mr,
                )

        response: SubAgentResponse = {
            "agent_id": agent_id,
            "category": cast(Any, spec["category_key"]),  # synthetic key (typed as CategoryKey via cast)
            "status": cast(Any, status),
            "items": items,
            "category_score": category_score,
            "category_max": category_max,
            "category_confidence": cat_conf,
            "llm_backend": llm_backend,
            "llm_model_id": bedrock_model_id,
            "elapsed_ms": elapsed_ms,
            "error_message": error_msg,
        }
        # Layer 3 가 노드 라벨 표시에 사용할 수 있도록 별도 필드 — TypedDict total=False 라 추가 가능
        response["category_judgment"] = category_judgment  # type: ignore[typeddict-unknown-key]
        response["node_label_ko"] = spec["label_ko"]  # type: ignore[typeddict-unknown-key]

        return response, {"node_id": node_id, "team_id": spec["team_id"]}

    _agent.__name__ = f"{node_id}_agent"
    _agent.__qualname__ = f"shinhan_dept.{node_id}_agent"
    return _agent
