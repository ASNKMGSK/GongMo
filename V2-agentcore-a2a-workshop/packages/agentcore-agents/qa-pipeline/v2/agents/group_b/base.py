# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Group B Sub Agent 공통 base — v2.schemas.sub_agent_io / enums 준수 빌더.

Dev5 가 `v2/schemas/sub_agent_io.py` 에 `SubAgentResponse` / `ItemVerdict` /
`EvidenceQuote` / `DeductionEntry` / `LLMSelfConfidence` / `RuleLLMDelta` 를
엄격히 정의해 두었으므로, Group B 4개 Sub Agent 는 이 스키마를 **정식 계약**
으로 따른다.

제공 헬퍼:
- `build_sub_agent_response()` — SubAgentResponse TypedDict 빌더
- `make_item_verdict()` — ItemVerdict 빌더 (snap_score 검증 포함 placeholder)
- `make_evidence()` — EvidenceQuote 빌더
- `make_deduction()` — DeductionEntry 빌더
- `make_llm_self_confidence()` — LLMSelfConfidence 빌더
- `compare_with_rule_pre_verdict()` — RuleLLMDelta 빌더

Group B 카테고리:
- #10, #11 → explanation_delivery (15점)
- #12, #13, #14 → proactiveness (15점)
- #15, #16 → work_accuracy (15점)
- #17, #18 → privacy_protection (10점)

Score snap 계약 (Phase A2 확정 2026-04-20):
- V1 `nodes.skills.reconciler.snap_score` 는 V1 qa_rules 의 ALLOWED_STEPS 를 사용 →
  #17/#18 에서 3점을 0점으로 **강제 변환**. iter05 회귀 원인.
- V2 는 `v2.contracts.rubric.snap_score_v2` 를 반드시 경유. `ALLOWED_STEPS[17/18] =
  [5, 3, 0]` 확장이 적용되어 3점 중간단계가 유지된다.
- 총점 100점 불변 (V2 rubric 총합 검증은 `v2.contracts.rubric` 에서 assert).
"""

from __future__ import annotations

import logging
from typing import Any

from v2.contracts.rubric import ALLOWED_STEPS, snap_score_v2
from v2.schemas.enums import (
    CATEGORY_META,
    CategoryKey,
    EvaluationMode,
    SubAgentStatus,
)


logger = logging.getLogger(__name__)


# Sub Agent override_hint 허용 값 (PDF 원칙 4 — preamble 지시문 `override_hint` 필드 유효성 검증)
_ALLOWED_OVERRIDE_HINTS: frozenset[str] = frozenset(
    {"profanity", "privacy_leak", "uncorrected_misinfo"}
)
from v2.schemas.sub_agent_io import (
    DeductionEntry,
    EvidenceQuote,
    ItemVerdict,
    LLMSelfConfidence,
    RuleLLMDelta,
    SubAgentResponse,
)


# ---------------------------------------------------------------------------
# Group B 매핑 상수
# ---------------------------------------------------------------------------

# V1 qa_rules 의 한국어 항목명 (Group B 범위)
ITEM_NAMES_KO: dict[int, str] = {
    10: "설명의 명확성",
    11: "두괄식 답변",
    12: "문제 해결 의지",
    13: "부연 설명 및 추가 안내",
    14: "사후 안내",
    15: "정확한 안내",
    16: "필수 안내 이행",
    17: "정보 확인 절차",
    18: "정보 보호 준수",
}

ITEM_NAMES_EN: dict[int, str] = {
    10: "Clarity of explanation",
    11: "Conclusion-first response",
    12: "Problem-solving willingness",
    13: "Supplementary explanation and additional guidance",
    14: "Follow-up guidance",
    15: "Accurate guidance",
    16: "Mandatory guidance compliance",
    17: "Information verification procedure",
    18: "Privacy compliance",
}

# 항목 → CategoryKey
ITEM_CATEGORY: dict[int, CategoryKey] = {
    10: "explanation_delivery",
    11: "explanation_delivery",
    12: "proactiveness",
    13: "proactiveness",
    14: "proactiveness",
    15: "work_accuracy",
    16: "work_accuracy",
    17: "privacy_protection",
    18: "privacy_protection",
}

# 기본 EvaluationMode (Phase A1 확정 대기 — 설계서 §5.3 기반 Dev3 초안)
# Dev5 답변 수령 후 필요 시 조정.
DEFAULT_EVALUATION_MODE: dict[int, EvaluationMode] = {
    10: "full",
    11: "full",
    12: "full",
    13: "full",
    14: "full",  # 즉시해결 건이면 "skipped" 로 동적 전환
    15: "partial_with_review",  # 업무지식 RAG 필수 → 인간 검수 기본값
    16: "full",
    17: "compliance_based",
    18: "compliance_based",
}

# 항목별 만점 — v2.contracts.rubric.ALLOWED_STEPS 에서 도출 (단일 진실 소스)
ITEM_MAX_SCORE: dict[int, int] = {
    item: steps[0]
    for item, steps in ALLOWED_STEPS.items()
    if item in (10, 11, 12, 13, 14, 15, 16, 17, 18)  # Group B 범위만
}

# 카테고리 max (CATEGORY_META 에서 가져온 값 — 중복 정의 회피)
CATEGORY_MAX_SCORE: dict[CategoryKey, int] = {
    key: meta["max_score"]
    for key, meta in CATEGORY_META.items()
}


# ---------------------------------------------------------------------------
# Builder helpers (Dev5 스키마 준수)
# ---------------------------------------------------------------------------


def make_evidence(
    *,
    speaker: str,
    quote: str,
    turn_id: int | None = None,
    timestamp: str | None = None,
) -> EvidenceQuote:
    """EvidenceQuote TypedDict 빌더.

    speaker: "상담사" | "고객" (tenant 별 customize 가능).
    quote:   원문 그대로 (수정 금지).
    """
    ev: EvidenceQuote = {"speaker": speaker, "quote": quote}
    if turn_id is not None:
        ev["turn_id"] = turn_id
    ev["timestamp"] = timestamp
    return ev


def make_deduction(
    *,
    reason: str,
    points: int,
    evidence_refs: list[int] | None = None,
    rule_id: str | None = None,
) -> DeductionEntry:
    """DeductionEntry TypedDict 빌더."""
    d: DeductionEntry = {
        "reason": reason,
        "points": int(points),
        "evidence_refs": list(evidence_refs or []),
    }
    d["rule_id"] = rule_id
    return d


def make_llm_self_confidence(
    *,
    score: int,
    rationale: str | None = None,
) -> LLMSelfConfidence:
    """LLMSelfConfidence (1~5) 빌더.

    score 는 프롬프트 앵커 기준 1~5 정수 (설계서 §8.1).
    """
    s = max(1, min(5, int(score)))
    out: LLMSelfConfidence = {"score": s}
    out["rationale"] = rationale
    return out


def compare_with_rule_pre_verdict(
    *,
    item_number: int,
    llm_score: int,
    rule_pre_verdicts: dict | None,
    override_reason: str | None = None,
    verify_mode_used: bool = False,
) -> RuleLLMDelta | None:
    """Layer 1 RulePreVerdict 와 LLM 판정의 차이를 계산.

    rule_pre_verdicts: `preprocessing.rule_pre_verdicts.verdicts` (Dev1 계약).
    key = item_number → {score, confidence, confidence_mode, ...} (RulePreVerdict).

    Returns:
      RuleLLMDelta — Layer 1 판정 없으면 None (Dev5 ItemVerdict 에서 optional).
    """
    if not rule_pre_verdicts:
        return None
    verdict = rule_pre_verdicts.get(item_number)
    if not verdict:
        return None
    rule_score = verdict.get("score")
    delta: RuleLLMDelta = {
        "has_rule_pre_verdict": True,
        "rule_score": int(rule_score) if rule_score is not None else None,
        "llm_score": int(llm_score),
        "agreement": rule_score == llm_score,
        "override_reason": override_reason,
        "verify_mode_used": verify_mode_used,
    }
    return delta


def make_item_verdict(
    *,
    item_number: int,
    score: int | None,
    evaluation_mode: EvaluationMode,
    judgment: str,
    deductions: list[DeductionEntry],
    evidence: list[EvidenceQuote],
    llm_self_confidence: LLMSelfConfidence,
    rule_llm_delta: RuleLLMDelta | None = None,
    mode_reason: str | None = None,
    override_hint: str | None = None,
    rag_evidence: dict[str, Any] | None = None,
) -> ItemVerdict:
    """ItemVerdict TypedDict 빌더.

    score=None 은 unevaluable 모드에서만 허용 (프롬프트 단계 혹은 호출자 검증).
    score 는 반드시 `snap_score_v2(item_number, score)` 로 snap 되어 ALLOWED_STEPS
    허용값으로 강제 (Phase A2 계약 준수).

    override_hint (PDF 원칙 4, preamble 체크리스트 #6):
      - Sub Agent 가 LLM 맥락 판정에서 감지한 override 트리거.
      - 허용 값: "profanity" / "privacy_leak" / "uncorrected_misinfo" / None.
      - Layer 3 Override 가 Layer 1 Rule 트리거 부재 시 보조 시그널로 consume.
    """
    max_score = ITEM_MAX_SCORE.get(item_number, 0)
    if score is None:
        resolved_score = 0  # unevaluable 모드 — mode_reason 으로 투명성 보장
    else:
        resolved_score = snap_score_v2(item_number, int(score))

    # override_hint 유효성 검증 — 허용 값 외는 None 으로 정화
    if override_hint is not None and override_hint not in _ALLOWED_OVERRIDE_HINTS:
        logger.warning(
            "item #%s: invalid override_hint=%r 무시 (allowed=%s)",
            item_number, override_hint, sorted(_ALLOWED_OVERRIDE_HINTS),
        )
        override_hint = None

    item: ItemVerdict = {
        "item_number": item_number,
        "item_name": ITEM_NAMES_KO.get(item_number, ""),
        "item_name_en": ITEM_NAMES_EN.get(item_number),
        "max_score": max_score,
        "score": resolved_score,
        "evaluation_mode": evaluation_mode,
        "judgment": judgment,
        "deductions": deductions,
        "evidence": evidence,
        "llm_self_confidence": llm_self_confidence,
    }
    item["rule_llm_delta"] = rule_llm_delta
    item["mode_reason"] = mode_reason
    item["override_hint"] = override_hint
    if rag_evidence is not None:
        item["rag_evidence"] = rag_evidence  # type: ignore[typeddict-unknown-key]
    return item


def build_sub_agent_response(
    *,
    agent_id: str,
    category: CategoryKey,
    status: SubAgentStatus,
    items: list[ItemVerdict],
    category_confidence: int,
    llm_backend: str,
    llm_model_id: str | None = None,
    elapsed_ms: int | None = None,
    error_message: str | None = None,
) -> SubAgentResponse:
    """SubAgentResponse TypedDict 빌더.

    category_score 는 items[].score 의 합으로 자동 계산.
    category_max 는 CATEGORY_META 에서 조회.
    category_confidence 는 Sub Agent self-report (1~5).
    """
    category_max = CATEGORY_MAX_SCORE.get(category, 0)
    category_score = sum(int(it.get("score", 0) or 0) for it in items)

    # override_hints 수집 — Layer 3 Override 가 Layer 1 Rule 트리거 부재 시 보조 consume
    override_hints: list[dict[str, Any]] = [
        {"item_number": it.get("item_number"), "hint": it.get("override_hint")}
        for it in items
        if it.get("override_hint")
    ]

    resp: SubAgentResponse = {
        "agent_id": agent_id,
        "category": category,
        "status": status,
        "items": items,
        "category_score": category_score,
        "category_max": category_max,
        "category_confidence": max(1, min(5, int(category_confidence))),
        "llm_backend": llm_backend,
    }
    resp["llm_model_id"] = llm_model_id
    resp["elapsed_ms"] = elapsed_ms
    resp["error_message"] = error_message
    resp["override_hints"] = override_hints  # type: ignore[typeddict-unknown-key]
    return resp


# ---------------------------------------------------------------------------
# 공통 헬퍼: LLM raw → ItemVerdict 변환 (Bedrock 실호출 경로 공용)
# ---------------------------------------------------------------------------


def _confidence_to_int_1_5(raw: Any, fallback_float: float) -> int:
    """V1 float (0-1) 또는 int (1-5) → Dev5 int (1-5)."""
    if isinstance(raw, int) and 1 <= raw <= 5:
        return raw
    f = float(raw) if raw is not None else fallback_float
    if 0.0 <= f <= 1.0:
        return max(1, min(5, round(f * 4) + 1))
    return max(1, min(5, int(f)))


def convert_llm_raw_to_item_verdict(
    *,
    item_number: int,
    raw: dict,
    assigned_turns: list[dict],
    verdicts_bundle: dict,
    evaluation_mode_override: EvaluationMode | None = None,
    rule_id_prefix: str | None = None,
    rag_evidence: dict[str, Any] | None = None,
) -> ItemVerdict:
    """LLM raw dict → Dev5 ItemVerdict 공통 변환.

    - `nodes.skills.reconciler.normalize_fallback_deductions` 로 [SKIPPED_INFRA] 정화
    - score 에 복구 points 반영
    - evidence/deductions Dev5 스키마로 변환 + 턴 매칭
    - `make_item_verdict` 내부 `snap_score_v2` 경유 (ALLOWED_STEPS 존중)
    """
    # Late import — V1 경로는 _llm.py 가 sys.path 에 prepend
    from nodes.skills.reconciler import normalize_fallback_deductions  # type: ignore[import-not-found]

    max_score = ITEM_MAX_SCORE.get(item_number, 0)
    raw_deductions = raw.get("deductions", []) or []
    normalized_deds, skipped, recovered = normalize_fallback_deductions(raw_deductions)

    raw_score = int(raw.get("score", 0) or 0)
    if skipped > 0:
        raw_score = min(max_score, raw_score + recovered)

    v1_evidence = raw.get("evidence", []) or []
    turn_map = {t.get("turn_id"): t for t in (assigned_turns or [])}
    evidence_list: list[EvidenceQuote] = []
    for ev in v1_evidence:
        turn_id = ev.get("turn") or ev.get("turn_id")
        speaker = ev.get("speaker") or (
            turn_map.get(turn_id, {}).get("speaker") if turn_id else "agent"
        )
        speaker_label = {"agent": "상담사", "customer": "고객"}.get(
            speaker, speaker or "상담사"
        )
        evidence_list.append(
            make_evidence(
                speaker=speaker_label,
                quote=(ev.get("text") or ev.get("quote") or "")[:200],
                turn_id=turn_id,
                timestamp=ev.get("timestamp"),
            )
        )
    # evidence 비어있으면 상담사 첫 턴 1개 보강 (원칙 3)
    if not evidence_list:
        for t in assigned_turns:
            if t.get("speaker") == "agent":
                evidence_list.append(
                    make_evidence(
                        speaker="상담사",
                        quote=t.get("text", "")[:200],
                        turn_id=t.get("turn_id"),
                    )
                )
                break

    ded_list: list[DeductionEntry] = []
    prefix = rule_id_prefix or f"#{item_number}"
    for d in normalized_deds:
        evidence_refs: list[int] = []
        v1_ref = str(d.get("evidence_ref", ""))
        if v1_ref.startswith("turn_"):
            try:
                target_turn = int(v1_ref.split("_")[1].split("#")[0])
                for idx, ev in enumerate(evidence_list):
                    if ev.get("turn_id") == target_turn:
                        evidence_refs.append(idx)
                        break
            except (ValueError, IndexError):
                pass
        ded_list.append(
            make_deduction(
                reason=d.get("reason", "") or "",
                points=int(d.get("points", 0) or 0),
                evidence_refs=evidence_refs,
                rule_id=prefix,
            )
        )

    conf_float = float(raw.get("confidence", 0.7) or 0.7)
    self_conf_int = _confidence_to_int_1_5(raw.get("self_confidence"), conf_float)

    # LLM 반환의 override_hint 파싱 (preamble 지시문 — 허용 값 외는 None 처리)
    override_hint = raw.get("override_hint")
    if override_hint not in (None, "profanity", "privacy_leak", "uncorrected_misinfo"):
        override_hint = None

    return make_item_verdict(
        item_number=item_number,
        score=raw_score,
        evaluation_mode=evaluation_mode_override or DEFAULT_EVALUATION_MODE[item_number],
        judgment=raw.get("summary", "") or "",
        deductions=ded_list,
        evidence=evidence_list,
        llm_self_confidence=make_llm_self_confidence(
            score=self_conf_int,
            rationale=raw.get("summary", "")[:80] if raw.get("summary") else None,
        ),
        rule_llm_delta=compare_with_rule_pre_verdict(
            item_number=item_number,
            llm_score=raw_score,
            rule_pre_verdicts=verdicts_bundle,
        ),
        override_hint=override_hint,
        rag_evidence=rag_evidence,
    )


# ---------------------------------------------------------------------------
# Extension payload (SubAgentResponse 외 Dev5 스키마에는 없는 항목)
# ---------------------------------------------------------------------------


def extract_wiki_updates(
    *,
    accuracy_verdict: dict | None = None,
    privacy_flags: dict | None = None,
    intent_summary_patch: dict | None = None,
) -> dict[str, Any]:
    """LangGraph state update 에 추가로 병합할 wiki 필드 묶음.

    Layer 3 Orchestrator (Dev1) 는 state.accuracy_verdict / state.flags /
    state.intent_summary 를 읽어 Override 및 consistency 판정에 사용.
    """
    out: dict[str, Any] = {}
    if accuracy_verdict is not None:
        out["accuracy_verdict"] = accuracy_verdict
    if privacy_flags is not None:
        out["flags"] = privacy_flags
    if intent_summary_patch is not None:
        out["intent_summary"] = intent_summary_patch
    return out
