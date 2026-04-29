# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Layer 4 — Report Generator V2 (Dev5 주관).

Layer 1/2/3 산출물을 조립해 최종 `QAOutputV2` pydantic 모델로 직렬화.

입력 (QAStateV2 유사 dict):
 - preprocessing: Dev1 Layer 1 산출물 (intent_type / detected_sections / deduction_triggers /
                  pii_tokens / rule_pre_verdicts / quality)
 - evaluations (또는 sub_agent_responses): Dev2/Dev3 Sub Agent 산출물
 - orchestrator (optional): Dev1 Layer 3 산출물 (overrides_applied / grade 등)
 - confidence_signals (optional): Dev4 RAG 기여 (rag_stdev / evidence_quality_rag 등)
 - versions / masking_format / stt_metadata: 최상위 메타

파이프라인 순서 (호출자 관점):
 1. generate_confidence_signals(state)  → dict[item#, ConfidenceBlock dict]
 2. decide_tier(...)                    → RoutingBlock dict
 3. build_qa_output(state, confidence_map, routing) → QAOutputV2

본 모듈은 1/2/3 을 합쳐 단일 진입점 `generate_report_v2(state)` 를 노출한다.
V1 `report_generator.py` 의 LLM 기반 summary/coaching_points 는 선택적 이식
(skip_phase_c_and_reporting 플래그가 True 면 생략).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from v2.confidence.calculator import compute_item_confidence
from v2.layer4.evidence_refiner import extract_turns_from_state, refine_evidence
from v2.routing.tier_router import apply_t1_sampling, decide_tier
from v2.schemas.enums import (
    CATEGORY_META,
    FORCE_T3_ITEMS,
    GRADE_BOUNDARIES,
    get_category_meta,
)
from v2.schemas.qa_output_v2 import (
    CategoryBlock,
    CoachingPoint,
    ConfidenceBlock,
    ConfidenceSignals,
    DeductionTriggersBlock,
    DetectedSectionRange,
    DetectedSections,
    EvaluationBlock,
    FinalScoreBlock,
    ItemResult,
    MaskingFormatBlock,
    OverrideEntry,
    OverridesBlock,
    PIITokenRecord,
    PreprocessingBlock,
    PriorityFlag,
    QAOutputV2,
    RoutingBlock,
    STTMetadataBlock,
    SummaryBlock,
    VersionsBlock,
)


logger = logging.getLogger(__name__)


# ===========================================================================
# 헬퍼 — 평가 엔트리 정규화
# ===========================================================================


def _resolve_site_id(state: dict[str, Any]) -> str | None:
    """state 에서 site_id (tenant 식별자) 추출. 신한 META 분기에 사용."""
    return (
        state.get("site_id")
        or state.get("tenant_id")
        or state.get("tenant")
        or None
    )


def _iter_item_verdicts(state: dict[str, Any]):
    """state 에 들어 있는 모든 ItemVerdict-like dict 를 순회.

    V1 호환 state["evaluations"] 와 V2 state["sub_agent_responses"] 둘 다 지원.
    반환 타입은 정규화된 dict (item_number 필수).
    """
    # V2 경로: sub_agent_responses[].items[]
    for response in state.get("sub_agent_responses", []) or []:
        for item in response.get("items", []) or []:
            if "item_number" in item:
                yield dict(item)

    # V1 호환 경로: evaluations[].evaluation (래핑) 또는 evaluations[] (flat)
    for ev in state.get("evaluations", []) or []:
        inner = ev.get("evaluation") if isinstance(ev, dict) else None
        candidate = inner if (isinstance(inner, dict) and "item_number" in inner) else ev
        if isinstance(candidate, dict) and "item_number" in candidate:
            yield dict(candidate)


def _build_confidence_signals_map(state: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Dev4 기여 confidence_signals + Sub Agent llm_self 를 병합한 계산 결과 반환.

    Returns: {item_number: ConfidenceBlock dict}
    """
    rag_signals: dict[int, dict[str, Any]] = {}
    raw = state.get("confidence_signals") or {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                rag_signals[int(k)] = v
            except (TypeError, ValueError):
                continue

    confidence_map: dict[int, dict[str, Any]] = {}
    for item in _iter_item_verdicts(state):
        item_number = item["item_number"]
        mode = item.get("evaluation_mode", "full")
        llm_self = None
        llm_block = item.get("llm_self_confidence") or {}
        if isinstance(llm_block, dict):
            llm_self = llm_block.get("score")
        delta = item.get("rule_llm_delta") if isinstance(item.get("rule_llm_delta"), dict) else None

        rag = rag_signals.get(item_number, {})
        block = compute_item_confidence(
            item_number,
            evaluation_mode=mode,
            llm_self_confidence_score=llm_self,
            rule_llm_delta=delta,
            rag_stdev=rag.get("rag_stdev"),
            evidence_quality_rag=rag.get("evidence_quality_rag"),
            evidence_count=len(item.get("evidence") or []),
            # PL Q5 2026-04-20: Dev4 ReasoningResult.sample_size 전달 (없으면 penalty 미적용)
            rag_sample_size=rag.get("rag_sample_size") or rag.get("sample_size"),
            tenant_id=state.get("tenant_id") or state.get("tenant") or "generic",
        )
        confidence_map[item_number] = block

    return confidence_map


def _compute_grade(total_score: int) -> str:
    """총점 → 등급. GRADE_BOUNDARIES 내림차순 순회."""
    for grade, threshold in GRADE_BOUNDARIES:
        if total_score >= threshold:
            return grade
    return GRADE_BOUNDARIES[-1][0]


# ===========================================================================
# 카테고리/항목 조립
# ===========================================================================


def _assemble_item_result(
    item: dict[str, Any], *, confidence_block: dict[str, Any], refined_evidence: list[dict[str, Any]]
) -> ItemResult:
    """ItemVerdict dict → ItemResult pydantic 모델."""
    mode = item.get("evaluation_mode", "full")
    raw_score = item.get("score")
    max_score = int(item.get("max_score", 0))

    # score 정규화: skipped → max_score / unevaluable → None / 그 외 int
    if mode == "skipped" and raw_score is None:
        score: int | None = max_score
    elif mode == "unevaluable":
        score = None
    else:
        score = int(raw_score) if raw_score is not None else None

    item_number = int(item["item_number"])
    confidence = ConfidenceBlock(
        final=int(confidence_block["final"]), signals=ConfidenceSignals(**confidence_block["signals"])
    )
    force_t3 = bool(item.get("force_t3") or (item_number in FORCE_T3_ITEMS and mode != "skipped"))

    return ItemResult(
        item=item.get("item_name") or f"항목 {item_number}",
        item_number=item_number,
        max_score=max_score,
        evaluation_mode=mode,
        score=score,
        judgment=item.get("judgment") or "",
        evidence=refined_evidence,
        deductions=list(item.get("deductions") or []),
        confidence=confidence,
        flag=None,  # Layer 4 아래에서 priority_flags 집계 후 항목별 투영
        mandatory_human_review=bool(item.get("mandatory_human_review") or force_t3),
        force_t3=force_t3,
        # 3-Persona 앙상블 메타 — 프론트 드로어 per-persona 표시용
        persona_votes=item.get("persona_votes"),
        persona_step_spread=item.get("persona_step_spread"),
        persona_merge_path=item.get("persona_merge_path"),
        persona_merge_rule=item.get("persona_merge_rule"),
        judge_reasoning=item.get("judge_reasoning"),
        persona_details=item.get("persona_details"),
        # Post-debate judge 결과 (debate_node.apply_debate_to_evaluations 이 evaluation
        # dict 에 주입) → ItemResult 로 그대로 매핑. 프론트가 it.judge_* 로 표시.
        judge_score=item.get("judge_score"),
        judge_deductions=item.get("judge_deductions") or None,
        judge_evidence=item.get("judge_evidence") or None,
        judge_failure_reason=item.get("judge_failure_reason"),
        judge_human_cases=item.get("judge_human_cases") or None,
    )


def _group_items_by_category(
    items: list[ItemResult], *, site_id: str | None = None
) -> list[CategoryBlock]:
    """tenant 별 META 순서로 ItemResult 를 대분류별로 묶어 CategoryBlock 반환.

    site_id="shinhan" 시 SHINHAN_CATEGORY_META 사용 (V2 generic 의 #11/#13/#15/#16 제외) +
    부서특화 dept 카테고리 (901-922 synthetic) 를 추가.

    집계 규칙 (2026-04-27 수정 — 만점 100 보존):
      - category_max   = Σ item.max_score (모든 모드 포함, rubric 만점 보존)
                         · 신한일 때 #10 max=10 (rubric ALLOWED_STEPS 와 정합 — V2 도 #10 max=10)
                         · #12 max=5, #14 max=5 → 적극성 카테고리 max=10 (xlsx 정합)
      - category_score = Σ item.score
          · skipped     → max_score (만점 처리)
          · unevaluable → 0 (점수 0 으로 카운트하되 만점은 보존)
          · 그 외        → item.score (None 이면 0)
    """
    by_item: dict[int, ItemResult] = {it.item_number: it for it in items}
    categories: list[CategoryBlock] = []
    meta_map = get_category_meta(site_id)
    for category_key, meta in meta_map.items():
        bucket = [by_item[i] for i in meta["items"] if i in by_item]
        if not bucket:
            continue
        score_sum = 0
        max_sum = 0
        for it in bucket:
            max_sum += it.max_score
            if it.evaluation_mode == "skipped":
                score_sum += it.max_score
            elif it.evaluation_mode == "unevaluable":
                continue  # 분자만 제외 (분모는 max_sum 에 이미 포함)
            elif it.score is not None:
                score_sum += it.score
        categories.append(
            CategoryBlock(
                category=meta["label_ko"],
                category_key=category_key,
                category_label_en=meta.get("label_en"),
                max_score=max_sum,
                achieved_score=score_sum,
                items=bucket,
            )
        )

    # 신한 부서특화 dept categories — registry 기반으로 추가 (901-922 synthetic)
    if (site_id or "").lower() == "shinhan":
        try:
            from v2.agents.shinhan_dept.registry import DEPT_NODE_REGISTRY
        except Exception:
            DEPT_NODE_REGISTRY = {}  # type: ignore[assignment]

        # node_id 별로 items 수집
        dept_buckets: dict[str, list[ItemResult]] = {}
        for it in items:
            for node_id, spec in DEPT_NODE_REGISTRY.items():
                if any(int(s["item_number"]) == it.item_number for s in spec.get("items", [])):
                    dept_buckets.setdefault(node_id, []).append(it)
                    break

        for node_id, bucket in dept_buckets.items():
            spec = DEPT_NODE_REGISTRY.get(node_id, {})
            if not spec or not bucket:
                continue
            score_sum = 0
            max_sum = 0
            for it in bucket:
                max_sum += it.max_score
                if it.evaluation_mode == "skipped":
                    score_sum += it.max_score
                elif it.evaluation_mode == "unevaluable":
                    continue
                elif it.score is not None:
                    score_sum += it.score
            categories.append(
                CategoryBlock(
                    category=spec.get("label_ko", node_id),
                    category_key=spec.get("category_key", f"shinhan_{node_id}"),
                    category_label_en=None,
                    max_score=max_sum,
                    achieved_score=score_sum,
                    items=bucket,
                )
            )

    return categories


# ===========================================================================
# preprocessing / overrides / summary 조립
# ===========================================================================


def _resolve_intent_type(pre: dict[str, Any]) -> tuple[str | dict, str]:
    """intent_type 원본 + primary 문자열 반환 (PL 승인 2026-04-20, Dev1 Union 확장).

    우선순위:
      0. preprocessing.intent_type_primary 가 Dev1 Layer 1 에서 명시 세팅된 경우
         → primary 는 그 값 그대로 존중 (원본 intent_type 은 아래 규칙대로 파생).
      1. preprocessing.intent_type 이 dict 이면: 원본 dict + primary_intent 추출
      2. preprocessing.intent_type 이 str 이면: 원본 str + 동일 값
         (단, intent_detail sibling 있으면 primary 는 거기서)
      3. preprocessing.intent_detail 이 dict 이면 (Dev1 canonical): str + primary
      4. 모두 부재: ("general", "general")
    """
    raw = pre.get("intent_type")
    # Rule 0 — Dev1 이 명시 세팅한 primary 는 최우선 존중
    explicit_primary = pre.get("intent_type_primary")
    if isinstance(explicit_primary, str) and explicit_primary.strip():
        if isinstance(raw, dict):
            return raw, explicit_primary.strip()
        if isinstance(raw, str) and raw.strip():
            return raw, explicit_primary.strip()
        # raw 부재 — primary 값을 intent_type 자리에도 사용
        return explicit_primary.strip(), explicit_primary.strip()

    # 기존 폴백 (Rule 1~4)
    if isinstance(raw, dict):
        primary = str(raw.get("primary_intent") or "general").strip() or "general"
        return raw, primary
    if isinstance(raw, str) and raw.strip():
        detail = pre.get("intent_detail")
        if isinstance(detail, dict) and detail.get("primary_intent"):
            return raw, str(detail["primary_intent"])
        return raw, raw
    detail = pre.get("intent_detail")
    if isinstance(detail, dict) and detail.get("primary_intent"):
        return str(detail["primary_intent"]), str(detail["primary_intent"])
    return "general", "general"


def _build_preprocessing_block(state: dict[str, Any]) -> PreprocessingBlock:
    pre = state.get("preprocessing") or {}
    sections_raw = pre.get("detected_sections") or {}

    def _range(key: str) -> DetectedSectionRange:
        v = sections_raw.get(key)
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return DetectedSectionRange(start=int(v[0]), end=int(v[1]))
        if isinstance(v, dict) and "start" in v and "end" in v:
            return DetectedSectionRange(start=int(v["start"]), end=int(v["end"]))
        return DetectedSectionRange(start=0, end=0)

    sections = DetectedSections(opening=_range("opening"), body=_range("body"), closing=_range("closing"))
    triggers_raw = pre.get("deduction_triggers") or {}
    # canonical 한글 키 + 영문 alias 모두 수용
    triggers = DeductionTriggersBlock(
        **{
            "불친절": bool(triggers_raw.get("불친절") or triggers_raw.get("rudeness")),
            "개인정보_유출": bool(triggers_raw.get("개인정보_유출") or triggers_raw.get("privacy_leak")),
            "오안내_미정정": bool(
                triggers_raw.get("오안내_미정정")
                or triggers_raw.get("uncorrected_misinfo")
                or triggers_raw.get("incorrect_uncorrected")
            ),
        }
    )
    pii_tokens = []
    for tok in pre.get("pii_tokens") or []:
        try:
            pii_tokens.append(
                PIITokenRecord(
                    raw=str(tok.get("raw", "***")),
                    utterance_idx=int(tok.get("utterance_idx", 0)),
                    inferred_category=str(tok.get("inferred_category", "UNKNOWN")),
                    inference_confidence=float(tok.get("inference_confidence", 0.0)),
                )
            )
        except Exception as exc:
            logger.warning("report: pii_token 변환 실패 %r: %s", tok, exc)

    intent_type, intent_primary = _resolve_intent_type(pre)
    return PreprocessingBlock(
        intent_type=intent_type,
        intent_type_primary=intent_primary,
        detected_sections=sections,
        deduction_triggers=triggers,
        pii_tokens=pii_tokens,
    )


def _build_overrides_block(state: dict[str, Any]) -> OverridesBlock:
    orch = state.get("orchestrator") or {}
    applied: list[OverrideEntry] = []
    for entry in orch.get("overrides_applied") or []:
        if not isinstance(entry, dict):
            continue
        try:
            applied.append(
                OverrideEntry(
                    trigger=str(entry.get("trigger") or entry.get("trigger_type") or "privacy_leak"),
                    action=str(entry.get("action") or "item_zero"),
                    affected_items=list(entry.get("affected_items") or []),
                    reason=str(entry.get("reason") or entry.get("rationale") or ""),
                    evidence=list(entry.get("evidence") or []),
                )
            )
        except Exception as exc:
            logger.warning("report: override 변환 실패 %r: %s", entry, exc)
    return OverridesBlock(applied=bool(applied), reasons=applied)


def _build_summary(*, total_score: int, max_total: int, grade: str, items: list[ItemResult]) -> SummaryBlock:
    """LLM 없이 결정적으로 summary 생성 (기본). V1 LLM 기반 요약은 후속 훅으로.

    skip_phase_c_and_reporting 플래그가 True 이면 본 summary 도 생략 가능.
    """
    strengths: list[str] = []
    improvements: list[str] = []
    for it in items:
        if it.evaluation_mode == "skipped":
            continue
        if it.score is not None and it.score == it.max_score and it.evaluation_mode == "full":
            strengths.append(f"#{it.item_number} {it.item}: 만점")
        elif it.score is not None and it.score < it.max_score:
            loss = it.max_score - it.score
            improvements.append(f"#{it.item_number} {it.item}: -{loss}점 ({it.judgment[:40]})")
    one_liner = f"총점 {total_score}/{max_total} ({grade})"
    return SummaryBlock(
        total_score=total_score,
        max_score=max_total,
        grade=grade,
        one_liner=one_liner,
        strengths=strengths[:5],
        improvements=improvements[:5],
    )


def _project_priority_flags_to_items(items: list[ItemResult], priority_flags: list[dict[str, Any]]) -> list[ItemResult]:
    """routing.priority_flags 중 item_numbers 가 지정된 것은 해당 ItemResult.flag 에 투영."""
    flag_by_item: dict[int, str] = {}
    for pf in priority_flags or []:
        for item_number in pf.get("item_numbers") or []:
            if item_number not in flag_by_item:
                flag_by_item[item_number] = pf.get("code", "")
    out: list[ItemResult] = []
    for it in items:
        if it.item_number in flag_by_item:
            it = it.model_copy(update={"flag": flag_by_item[it.item_number]})
        out.append(it)
    return out


# ===========================================================================
# 단일 진입점
# ===========================================================================


def generate_report_v2(
    state: dict[str, Any], *, tenant_flags: dict[str, Any] | None = None, include_summary: bool = True
) -> QAOutputV2:
    """Layer 4 전체 실행 — Layer 3 산출물까지 준비된 state 를 받아 QAOutputV2 반환.

    Parameters
    ----------
    state          : QAStateV2 유사 dict.
    tenant_flags   : VIP/민원/신입 등 tenant 레벨 HITL 입력 (설계서 §10.1).
    include_summary: False 면 summary/coaching_points 생성 생략
                     (skip_phase_c_and_reporting 플래그 용).

    Returns
    -------
    `QAOutputV2` pydantic 인스턴스. `.model_dump(by_alias=True)` 로 JSON 직렬화.
    """
    # 1) item verdict → ItemResult 조립
    turns = extract_turns_from_state(state)
    confidence_map = _build_confidence_signals_map(state)

    item_results: list[ItemResult] = []
    item_dicts_for_router: list[dict[str, Any]] = []
    for item in _iter_item_verdicts(state):
        item_number = int(item["item_number"])
        mode = item.get("evaluation_mode", "full")
        refined = refine_evidence(item.get("evidence"), turns=turns, evaluation_mode=mode, item_number=item_number)
        conf_block = confidence_map.get(item_number) or {
            "final": 3,
            "signals": {"llm_self": 3, "rule_llm_agreement": True, "rag_stdev": None, "evidence_quality": "medium"},
        }
        item_results.append(_assemble_item_result(item, confidence_block=conf_block, refined_evidence=refined))
        item_dicts_for_router.append({"item_number": item_number, "evaluation_mode": mode})

    # 2) 카테고리 집계
    categories = _group_items_by_category(item_results, site_id=_resolve_site_id(state))
    total_score = sum(c.achieved_score for c in categories)
    max_total = sum(c.max_score for c in categories)
    preprocessing_block = _build_preprocessing_block(state)
    overrides_block = _build_overrides_block(state)

    # Orchestrator 가 grade 를 미리 계산했다면 존중, 없으면 자체 계산
    orch = state.get("orchestrator") or {}
    raw_total = int(orch.get("total_score", total_score))
    after_overrides = int(orch.get("total_after_overrides", total_score))
    grade = (
        str(orch.get("grade", {}).get("grade") if isinstance(orch.get("grade"), dict) else None)
        if orch.get("grade")
        else _compute_grade(after_overrides)
    )
    final_score_block = FinalScoreBlock(raw_total=raw_total, after_overrides=after_overrides, grade=grade)

    # 3) routing 결정 (PL Q5 외부화 — tenant_config 주입)
    # 비활성화 플래그: 환경변수 QA_TIER_ROUTER_DISABLED=1 또는 state.disable_tier_router=True
    # → tier 결정 없이 'disabled' 마커로 통과. priority_flags / hitl_driver 도 비움.
    import os as _os

    tier_disabled = _os.environ.get("QA_TIER_ROUTER_DISABLED", "").strip().lower() in {"1", "true", "yes"} or bool(
        state.get("disable_tier_router")
    )
    tenant_id = str(state.get("tenant_id") or state.get("tenant") or "generic")

    if tier_disabled:
        logger.info("tier_router: 비활성화됨 (env QA_TIER_ROUTER_DISABLED 또는 state.disable_tier_router)")
        routing_dict = {
            "decision": "disabled",
            "hitl_driver": None,
            "priority_flags": [],
            "estimated_review_time_min": 0,
            "tier_reasons": ["tier_router_disabled"],
        }
    else:
        routing_dict = decide_tier(
            confidence_results=confidence_map,
            evaluations=item_dicts_for_router,
            preprocessing=state.get("preprocessing") or {},
            final_score={"after_overrides": after_overrides, "grade": grade},
            tenant_flags=tenant_flags,
            tenant_id=tenant_id,
        )
        # T0 → T1 샘플링 (seed 는 consultation_id hash 사용 — 재현성).
        consultation_id = str(state.get("consultation_id") or state.get("session_id") or "")
        routing_dict = apply_t1_sampling(
            routing_dict, rng_seed=(hash(consultation_id) & 0xFFFFFFFF) or None, tenant_id=tenant_id
        )
    priority_flags = [PriorityFlag(**pf) for pf in routing_dict.get("priority_flags", [])]
    routing_block = RoutingBlock(
        decision=routing_dict["decision"],
        hitl_driver=routing_dict.get("hitl_driver"),
        priority_flags=priority_flags,
        estimated_review_time_min=int(routing_dict.get("estimated_review_time_min", 0)),
        tier_reasons=list(routing_dict.get("tier_reasons") or []),
    )

    # 4) priority_flags 를 item 에 투영
    item_results = _project_priority_flags_to_items(item_results, routing_dict.get("priority_flags") or [])
    categories = _group_items_by_category(item_results, site_id=_resolve_site_id(state))  # 재조립

    # 5) summary / coaching_points
    summary = (
        _build_summary(total_score=after_overrides, max_total=max_total, grade=grade, items=item_results)
        if include_summary
        else SummaryBlock(
            total_score=after_overrides, max_score=max_total, grade=grade, one_liner="", strengths=[], improvements=[]
        )
    )
    coaching_points: list[CoachingPoint] = list(state.get("coaching_points") or [])

    # 6) 메타 블록
    versions = VersionsBlock(
        **(
            state.get("versions")
            or {
                "model": "claude-sonnet-4-6",
                "rubric": "generic-v1.0",
                "prompt_bundle": "v1",
                "golden_set": "seed_v0.1",
            }
        )
    )
    mf = state.get("masking_format") or {"version": "v1_symbolic", "spec": "All PII '***'"}
    masking_format = MaskingFormatBlock(version=mf.get("version", "v1_symbolic"), spec=str(mf.get("spec", "")))
    stt = state.get("stt_metadata") or {}
    stt_block = STTMetadataBlock(
        transcription_confidence=float(stt.get("transcription_confidence", 1.0)),
        speaker_diarization_success=bool(stt.get("speaker_diarization_success", True)),
        duration_sec=float(stt.get("duration_sec", 0.0)),
        has_timestamps=bool(stt.get("has_timestamps", True)),
    )

    evaluated_at = state.get("evaluated_at") or datetime.now(UTC).isoformat()

    return QAOutputV2(
        consultation_id=consultation_id or "unknown",
        tenant=str(state.get("tenant_id") or state.get("tenant") or "generic"),
        evaluated_at=evaluated_at,
        versions=versions,
        masking_format=masking_format,
        stt_metadata=stt_block,
        preprocessing=preprocessing_block,
        evaluation=EvaluationBlock(categories=categories),
        overrides=overrides_block,
        final_score=final_score_block,
        routing=routing_block,
        summary=summary,
        coaching_points=coaching_points,
        diagnostics={
            "confidence_map": {str(k): v for k, v in confidence_map.items()},
            "node_timings": list(state.get("node_timings") or []),
            "error": state.get("error"),
        },
    )


def report_generator_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 노드 엔트리포인트 — state.report 에 QAOutputV2 dump 를 저장.

    `plan.skip_phase_c_and_reporting == True` 면 조기 종료 (V1 호환).
    """
    plan = state.get("plan") or {}
    if plan.get("skip_phase_c_and_reporting"):
        return {}

    tenant_flags = (state.get("tenant_flags") or {}) if isinstance(state.get("tenant_flags"), dict) else {}
    try:
        report = generate_report_v2(state, tenant_flags=tenant_flags)
        return {
            "report": report.model_dump(by_alias=True, mode="json"),
            "routing": report.routing.model_dump(by_alias=True, mode="json"),
            "current_phase": "complete",
        }
    except Exception as exc:  # pragma: no cover — 최종 방어선
        logger.exception("report_generator_v2 실패: %s", exc)
        return {"error": f"report_generator_v2_failed: {exc}"}
