# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""개인정보보호 Sub Agent — #17 정보확인절차 (5점) + #18 정보보호준수 (5점).

V1 재활용: nodes/incorrect_check.py (compliance_based 로 재정의).

V2 차이:
- evaluation_mode = "compliance_based" — 내용 정확성이 아닌 "절차 준수" 평가
- 패턴 A/B/C 탐지 (설계서 §5.2 p11)
  - A: 본인확인 전 상담사 선언급 (PII 먼저 말함)
  - B: 제3자 지칭("남편분/지인") 후 PII 안내
  - C: 고객이 본인확인 거부 후 상담 계속
- **T3 강제 라우팅** — enums.FORCE_T3_ITEMS = {9, 17, 18} 에 이미 정의
- RuleLLMDelta — Dev1 Layer 1 preprocessing.rule_pre_verdicts[17] 와 비교

ALLOWED_STEPS (PL 확정):
- V2: `[5, 3, 0]` — iter05 회귀 해소 + 스키마-프롬프트 정합 회복 (PL 승인).
  `v2/contracts/rubric.py` 의 ALLOWED_STEPS[17]/[18] 참조.
- V1 qa_rules.py 는 불변. V2 rubric 분리 운영.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from v2.agents.group_b.base import (
    DEFAULT_EVALUATION_MODE,
    ITEM_NAMES_KO,
    build_sub_agent_response,
    compare_with_rule_pre_verdict,
    extract_wiki_updates,
    make_deduction,
    make_evidence,
    make_item_verdict,
    make_llm_self_confidence,
)
from v2.judge_agent import reconcile_hybrid
from v2.reconciler_personas import PERSONAS, apply_persona_prefix
from v2.schemas.enums import FORCE_T3_ITEMS
from v2.schemas.sub_agent_io import (
    DeductionEntry,
    EvidenceQuote,
    ItemVerdict,
    SubAgentResponse,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 패턴 A/B/C 상수 (설계서 §5.2 p11)
# ---------------------------------------------------------------------------

PATTERN_A = "A"  # 본인확인 전 상담사 선언급
PATTERN_B = "B"  # 제3자 지칭 후 PII 안내
PATTERN_C = "C"  # 고객 본인확인 거부 후 상담 진행


async def privacy_agent(
    *,
    transcript: str,
    assigned_turns: list[dict],
    consultation_type: str,
    intent_summary: dict | None = None,
    rule_pre_verdicts: dict | None = None,
    preprocessing: dict | None = None,
    llm_backend: str = "bedrock",
    bedrock_model_id: str | None = None,
    skip_llm: bool = False,
) -> tuple[SubAgentResponse, dict[str, Any]]:
    """#17, #18 평가 → (SubAgentResponse, wiki_updates).

    Returns:
      - SubAgentResponse: Dev5 스키마 준수, Layer 3 orchestrator 입력
      - wiki_updates: state.flags 에 병합될 privacy 관련 플래그 dict
                      (패턴 A/B/C, force_t3, preemptive_disclosure 등)

    인자:
      preprocessing: Layer 1 산출물 (iv_procedure_turns / preemptive_turns /
                     third_party_turns 를 포함). 없으면 V1 regex 재실행 경로
                     (TODO: Phase D1 에서 연결).
      rule_pre_verdicts: Layer 1 e) Rule 1차 판정 dict (item_number → RulePreVerdict).
                         #17 은 주로 여기에 포함됨.
    """
    preprocessing = preprocessing or {}
    # Layer 1 전처리 결과에서 패턴 탐지 입력 추출 (있으면 재스캔 불필요)
    triggers = (preprocessing.get("deduction_triggers") or {}).get("triggers", [])
    iv_turns, preemptive_turns, third_party_turns = _classify_triggers(triggers)

    # ------------------------------------------------------------------
    # Rule pre-verdict 우선 소비 (Dev1 Layer 1 합의)
    # ------------------------------------------------------------------
    # Phase E1 버그 수정 (2026-04-20 Dev1 보고):
    # skeleton 이 고정 0점 반환하던 결과, iter03_clean 9 샘플 전원 #17/#18=0점
    # 처리. compliance_based 평가는 "절차 준수 미감지 = 5점 (긍정 기본)" 원칙이
    # 맞음. 아래 우선순위로 score 결정:
    #   1) Dev1 rule_pre_verdicts[17/18] 가 있으면 그 값 채택 (hard/soft 모두)
    #   2) 패턴 A/B/C 감지되면 A=0, B=3, C=0 (프롬프트 §5.2 기준)
    #   3) 그 외 (iv 수행 기록 없고 위반 패턴도 없음) → 5점 (긍정 기본)
    # Phase D1 LLM 연결 후 rule_pre_verdict 미존재 시 LLM 판정으로 대체.
    verdicts_bundle = (rule_pre_verdicts or {}).get("verdicts") or {}

    # 패턴 A/B/C 탐지 (전처리 결과 기반)
    patterns_detected_early = []
    if preemptive_turns:
        patterns_detected_early.append(PATTERN_A)
    if third_party_turns:
        patterns_detected_early.append(PATTERN_B)

    # V2 Bedrock 실호출 (Rule hard verdict 시 LLM skip)
    # env 자동 감지: V2_GROUP_B_SKIP_LLM=1 이면 LLM 호출 skip (테스트/dev 환경)
    import os as _os
    skip_llm_resolved = skip_llm or bool(_os.getenv("V2_GROUP_B_SKIP_LLM"))

    v1_eval = await _invoke_llm_or_skeleton(
        transcript=transcript,
        assigned_turns=assigned_turns,
        consultation_type=consultation_type,
        intent_summary=intent_summary or {},
        rule_pre_verdicts=verdicts_bundle,
        patterns_detected=patterns_detected_early,
        llm_backend=llm_backend,
        bedrock_model_id=bedrock_model_id,
        skip_llm=skip_llm_resolved,
    )
    # Score 결정:
    # 1) LLM score 있으면 그 값 우선 (rule_pre_verdict 가 soft 또는 없을 때 LLM 판정)
    # 2) LLM 없거나 실패 → rule_pre_verdicts 또는 패턴/긍정 기본 fallback
    def _resolve_score(item_number: int) -> tuple[int, str]:
        llm_score = v1_eval.get(f"llm_score_{item_number}")
        verify_mode = v1_eval.get(f"verify_mode_used_{item_number}", False)
        # Rule hard verdict 채택된 경우 rule 값 사용
        if verify_mode:
            v = verdicts_bundle.get(item_number, {})
            return int(v.get("score", 5)), "rule_pre_verdict_hard"
        if llm_score is not None:
            return int(llm_score), "llm"
        # LLM 실패 or 생략 → fallback
        return _resolve_score_with_rule_fallback(
            item_number=item_number,
            rule_pre_verdicts=verdicts_bundle,
            preemptive_detected=bool(preemptive_turns),
            third_party_detected=bool(third_party_turns),
            skeleton_score=0,
        )

    score_17_raw, score_17_source = _resolve_score(17)
    score_18_raw, score_18_source = _resolve_score(18)
    ded_17_raw = v1_eval.get("deductions_17", [])
    ded_18_raw = v1_eval.get("deductions_18", [])
    evidence_17_raw = v1_eval.get("evidence_17", [])
    evidence_18_raw = v1_eval.get("evidence_18", [])

    # LLM override_hint 추출 (preamble 지시문) + 패턴 A/B/C 감지 시 자동 privacy_leak 주입
    override_hint_17 = _resolve_override_hint(
        llm_hint=v1_eval.get("override_hint_17"),
        patterns_detected=patterns_detected_early,
    )
    override_hint_18 = _resolve_override_hint(
        llm_hint=v1_eval.get("override_hint_18"),
        patterns_detected=patterns_detected_early,
    )

    # ------------------------------------------------------------------
    # 패턴 A/B/C 탐지
    # ------------------------------------------------------------------
    patterns_detected = _detect_patterns_abc(
        iv_turns=iv_turns,
        preemptive_turns=preemptive_turns,
        third_party_turns=third_party_turns,
    )

    # ------------------------------------------------------------------
    # EvidenceQuote / DeductionEntry 변환 + 산술 일관성 보정
    # ------------------------------------------------------------------
    evidence_list_17 = _convert_evidence(evidence_17_raw, assigned_turns)
    evidence_list_18 = _convert_evidence(evidence_18_raw, assigned_turns)

    deductions_list_17 = _convert_deductions(
        ded_17_raw,
        patterns_detected,
        evidence_list_17,
        rule_id_prefix="#17",
    )
    deductions_list_18 = _convert_deductions(
        ded_18_raw,
        patterns_detected,
        evidence_list_18,
        rule_id_prefix="#18",
    )
    # score + Σ deductions.points == max_score 보정. V1 adapter 가 LLM
    # deductions 제공하면 그 값을 존중. rule fallback 경로에서 skeleton
    # deductions 가 비어있으면 score 에 맞춰 자동 생성.
    deductions_list_17 = _ensure_deductions_sum_matches(
        deductions_list_17, score=score_17_raw, max_score=5,
        patterns_detected=patterns_detected,
        source=score_17_source, rule_id_prefix="#17",
    )
    deductions_list_18 = _ensure_deductions_sum_matches(
        deductions_list_18, score=score_18_raw, max_score=5,
        patterns_detected=patterns_detected,
        source=score_18_source, rule_id_prefix="#18",
    )

    # ------------------------------------------------------------------
    # RuleLLMDelta (Layer 1 Rule 1차 판정과 비교)
    # ------------------------------------------------------------------
    rule_llm_delta_17 = compare_with_rule_pre_verdict(
        item_number=17,
        llm_score=score_17_raw,
        rule_pre_verdicts=verdicts_bundle,
        override_reason=None,
    )
    rule_llm_delta_18 = compare_with_rule_pre_verdict(
        item_number=18,
        llm_score=score_18_raw,
        rule_pre_verdicts=verdicts_bundle,
        override_reason=None,
    )

    # ------------------------------------------------------------------
    # ItemVerdict 생성
    # ------------------------------------------------------------------
    item_17: ItemVerdict = make_item_verdict(
        item_number=17,
        score=score_17_raw,
        evaluation_mode=DEFAULT_EVALUATION_MODE[17],
        judgment=v1_eval.get("summary_17", "정보 확인 절차 준수 여부"),
        deductions=deductions_list_17,
        evidence=evidence_list_17,
        llm_self_confidence=make_llm_self_confidence(
            score=v1_eval.get("self_confidence_17", 4),
            rationale=v1_eval.get("rationale_17"),
        ),
        rule_llm_delta=rule_llm_delta_17,
        override_hint=override_hint_17,
    )

    item_18: ItemVerdict = make_item_verdict(
        item_number=18,
        score=score_18_raw,
        evaluation_mode=DEFAULT_EVALUATION_MODE[18],
        judgment=v1_eval.get("summary_18", "정보 보호 가이드 준수 여부"),
        deductions=deductions_list_18,
        evidence=evidence_list_18,
        llm_self_confidence=make_llm_self_confidence(
            score=v1_eval.get("self_confidence_18", 4),
            rationale=v1_eval.get("rationale_18"),
        ),
        rule_llm_delta=rule_llm_delta_18,
        override_hint=override_hint_18,
    )

    # 하이브리드 머지 메타 주입 (persona_votes / step_spread / merge_path / judge_reasoning).
    # LLM 경로에서만 의미 있음 — rule_pre_verdict_hard 경로에선 None 으로 주입 안 됨.
    _inject_hybrid_fields_from_bundle(item_17, v1_eval, 17)
    _inject_hybrid_fields_from_bundle(item_18, v1_eval, 18)

    # ------------------------------------------------------------------
    # SubAgentResponse 조립
    # ------------------------------------------------------------------
    category_confidence = min(
        item_17.get("llm_self_confidence", {}).get("score", 4),
        item_18.get("llm_self_confidence", {}).get("score", 4),
    )

    resp = build_sub_agent_response(
        agent_id="privacy-protection-agent",
        category="privacy_protection",
        status="success",
        items=[item_17, item_18],
        category_confidence=category_confidence,
        llm_backend=llm_backend,
        llm_model_id=bedrock_model_id,
    )

    # ------------------------------------------------------------------
    # wiki_updates — state.flags 병합용 (Layer 3 Override 입력)
    # ------------------------------------------------------------------
    # #17/#18 은 항상 T3 강제 라우팅 대상 (enums.FORCE_T3_ITEMS)
    privacy_flags = {
        "privacy_violation": score_18_raw == 0,
        "preemptive_disclosure": PATTERN_A in patterns_detected,
        "third_party_context_present": PATTERN_B in patterns_detected,
        "customer_refused_verification": PATTERN_C in patterns_detected,
        "patterns_detected": patterns_detected,
        "force_t3_items": sorted(FORCE_T3_ITEMS & {17, 18}),
        "details": v1_eval.get("flag_details", []),
        # Phase E1 신규: Dev1 Layer 1 rule 소비 여부 + 점수 결정 경로 로깅
        "score_source": {"item_17": score_17_source, "item_18": score_18_source},
    }
    wiki_updates = extract_wiki_updates(privacy_flags=privacy_flags)

    return resp, wiki_updates


# ---------------------------------------------------------------------------
# 패턴 A/B/C 판정
# ---------------------------------------------------------------------------


def _classify_triggers(
    triggers: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Layer 1 deduction_triggers 에서 iv/preemptive/third_party 분류.

    Dev1 DeductionTrigger.trigger_type == "preemptive_disclosure" 는 A 패턴의
    1차 신호. third_party 관련은 DeductionTrigger 에 별도 타입이 없으므로
    Layer 1 이 deduction_triggers.has_category_zero_triggers 로 전달하거나,
    Layer 2 Sub Agent 가 직접 재스캔 (TODO).
    """
    iv_turns: list[dict] = []
    preemptive_turns: list[dict] = []
    third_party_turns: list[dict] = []
    for t in triggers:
        ttype = t.get("trigger_type")
        if ttype == "preemptive_disclosure":
            preemptive_turns.append(t)
        # TODO(dev1): "identity_verification" / "third_party_context" trigger_type 추가 협의
    return iv_turns, preemptive_turns, third_party_turns


def _detect_patterns_abc(
    *,
    iv_turns: list[dict],
    preemptive_turns: list[dict],
    third_party_turns: list[dict],
) -> list[str]:
    """패턴 A/B/C 감지 결과.

    - A: preemptive_turns 존재
    - B: third_party_turns 존재
    - C: 고객 거부 발화 감지 (TODO — Layer 1 에서 customer_refusal trigger 제공 후 활성화)
    """
    detected: list[str] = []
    if preemptive_turns:
        detected.append(PATTERN_A)
    if third_party_turns:
        detected.append(PATTERN_B)
    # C: 향후 Layer 1 customer_refusal_turns 추가 시 활성화
    del iv_turns
    return detected


# ---------------------------------------------------------------------------
# V1 → V2 변환 헬퍼
# ---------------------------------------------------------------------------


def _convert_evidence(
    v1_evidence: list[dict],
    assigned_turns: list[dict],
) -> list[EvidenceQuote]:
    """V1 evidence [{turn, speaker, text}] → Dev5 EvidenceQuote 배열 변환."""
    turn_map = {t.get("turn_id"): t for t in (assigned_turns or [])}
    out: list[EvidenceQuote] = []
    for ev in v1_evidence or []:
        turn_id = ev.get("turn") or ev.get("turn_id")
        speaker = ev.get("speaker") or (
            turn_map.get(turn_id, {}).get("speaker") if turn_id else "agent"
        )
        # speaker 한글화 — tenant customization 대상 (generic tenant 기본값)
        speaker_label = {"agent": "상담사", "customer": "고객"}.get(speaker, speaker)
        out.append(
            make_evidence(
                speaker=speaker_label,
                quote=ev.get("text") or ev.get("quote", ""),
                turn_id=turn_id,
                timestamp=ev.get("timestamp"),
            )
        )
    return out


def _convert_deductions(
    v1_deductions: list[dict],
    patterns_detected: list[str],
    evidence_list: list[EvidenceQuote],
    rule_id_prefix: str,
) -> list[DeductionEntry]:
    """V1 [{reason, points, evidence_ref}] → Dev5 DeductionEntry 배열 변환.

    evidence_refs 는 evidence_list 인덱스로 매핑. V1 evidence_ref (str) 는
    매칭 불가 시 공백 리스트.
    rule_id 는 패턴 A/B/C 발견 시 prefix + pattern 로 태그 (예: "#17:A").
    """
    out: list[DeductionEntry] = []
    primary_pattern = patterns_detected[0] if patterns_detected else None
    for d in v1_deductions or []:
        evidence_refs: list[int] = []
        v1_ref = str(d.get("evidence_ref", ""))
        # V1 evidence_ref 는 "turn_5" 같은 포맷. evidence_list 의 turn_id 와 매칭.
        if v1_ref.startswith("turn_"):
            try:
                target_turn = int(v1_ref.split("_")[1].split("#")[0])
                for idx, ev in enumerate(evidence_list):
                    if ev.get("turn_id") == target_turn:
                        evidence_refs.append(idx)
                        break
            except (ValueError, IndexError):
                pass
        rule_id = (
            f"{rule_id_prefix}:{primary_pattern}" if primary_pattern else rule_id_prefix
        )
        out.append(
            make_deduction(
                reason=d.get("reason", ""),
                points=int(d.get("points", 0) or 0),
                evidence_refs=evidence_refs,
                rule_id=rule_id,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Rule-based score fallback (Phase E1 버그 수정 2026-04-20)
# ---------------------------------------------------------------------------


def _ensure_deductions_sum_matches(
    deductions: list[DeductionEntry],
    *,
    score: int,
    max_score: int,
    patterns_detected: list[str],
    source: str,
    rule_id_prefix: str,
) -> list[DeductionEntry]:
    """score + Σ points == max_score 산술 일관성 보장.

    이미 deductions 가 일치하면 그대로 반환. 비어있고 score<max 이면 source
    (rule_pre_verdict/pattern_a/pattern_b/positive_default) 에 맞는 감점 1건
    자동 생성.
    """
    current_sum = sum(int(d.get("points", 0) or 0) for d in (deductions or []))
    gap = max_score - score - current_sum
    if gap <= 0:
        return deductions
    # source 별 reason 매핑
    reason_map = {
        "pattern_a": "패턴 A 감지 (본인확인 전 PII 선언급)",
        "pattern_b": "패턴 B 감지 (제3자 맥락)",
        "rule_pre_verdict": "Layer 1 Rule 판정 기반 감점",
        "positive_default": "",
        "skeleton": "LLM 미연결 (skeleton)",
    }
    primary_pattern = patterns_detected[0] if patterns_detected else None
    rule_id = f"{rule_id_prefix}:{primary_pattern}" if primary_pattern else rule_id_prefix
    synthetic = make_deduction(
        reason=reason_map.get(source, "점수 미달 감점"),
        points=gap,
        evidence_refs=[],
        rule_id=rule_id,
    )
    return [*deductions, synthetic]


def _resolve_score_with_rule_fallback(
    *,
    item_number: int,
    rule_pre_verdicts: dict,
    preemptive_detected: bool,
    third_party_detected: bool,
    skeleton_score: int,
) -> tuple[int, str]:
    """#17/#18 점수 결정 — rule 우선 + 긍정 기본 (compliance_based 원칙).

    Phase E1 drift 분석에서 skeleton 고정 0점으로 iter03_clean 9 샘플 전원 0점
    처리되는 버그 발견. compliance_based 평가는 "절차 준수 미감지 = 5점 (긍정 기본)"
    이 맞으므로 아래 우선순위로 결정:

    1. Layer 1 rule_pre_verdicts[item_number] 에 score 있으면 그 값 채택
       (Dev1 Layer 1 이 iv_performed/preemptive_found/third_party 조합을 이미
       판정함 — 단일 진실 소스)
    2. 패턴 A 감지 (preemptive_detected) → 0점 (본인확인 전 PII 선언급)
    3. 패턴 B 감지 (third_party_detected) → 3점 (제3자 맥락 경미)
    4. 위반 패턴 미감지 → 5점 (긍정 기본, compliance_based 원칙)

    skeleton_score 는 V1 adapter 경로 완성 전까지 최후 fallback (현재는 무시).

    Returns:
      (score, source) — source 는 "rule_pre_verdict" / "pattern_a" / "pattern_b" /
                        "positive_default" / "skeleton" 중 하나.
    """
    # 1) Rule 우선 채택
    verdict = rule_pre_verdicts.get(item_number) if rule_pre_verdicts else None
    if verdict is not None:
        rule_score = verdict.get("score")
        if rule_score is not None:
            return int(rule_score), "rule_pre_verdict"

    # 2) 패턴 A — 본인확인 전 PII 선언급 → 0점 직결
    if preemptive_detected:
        return 0, "pattern_a"

    # 3) 패턴 B — 제3자 맥락 존재 → 3점 (경미한 부분 준수)
    #    #17 은 "본인확인 절차"에 영향 없지만 안전하게 3점 부여 (향후 LLM 재판정)
    #    #18 은 "유출 가능성 경미" → 3점
    if third_party_detected:
        return 3, "pattern_b"

    # 4) 위반 패턴 없음 → 긍정 기본 5점 (compliance_based)
    del skeleton_score  # skeleton 경로 현재 미사용
    return 5, "positive_default"


# ---------------------------------------------------------------------------
# V1 호출 skeleton (Phase D1 LLM 연결 예정)
# ---------------------------------------------------------------------------


def _empty_llm_result() -> dict[str, Any]:
    """skip_llm=True 시 반환 — 각 항목 LLM score=None + verify_mode_used=False."""
    out: dict[str, Any] = {"flag_details": []}
    for n in (17, 18):
        out[f"deductions_{n}"] = []
        out[f"evidence_{n}"] = []
        out[f"summary_{n}"] = ""
        out[f"self_confidence_{n}"] = 3
        out[f"rationale_{n}"] = None
        out[f"verify_mode_used_{n}"] = False
        out[f"llm_score_{n}"] = None
        out[f"llm_failed_{n}"] = "skip_llm"
        # 하이브리드 머지 메타 — skip_llm 경로에선 모두 None/False
        out[f"persona_votes_{n}"] = None
        out[f"persona_step_spread_{n}"] = None
        out[f"persona_merge_path_{n}"] = None
        out[f"persona_merge_rule_{n}"] = None
        out[f"judge_reasoning_{n}"] = None
        out[f"mandatory_human_review_{n}"] = False
    return out


async def _invoke_llm_or_skeleton(
    *,
    transcript: str,
    assigned_turns: list[dict],
    consultation_type: str,
    intent_summary: dict,
    rule_pre_verdicts: dict,
    patterns_detected: list[str],
    llm_backend: str,
    bedrock_model_id: str | None,
    skip_llm: bool = False,
) -> dict[str, Any]:
    """V2 Bedrock 실호출 경로. Rule hard verdict 시 LLM 생략.

    skip_llm=True (테스트/skeleton-only 모드) 시 LLM 호출 전혀 안 함.
    호출자가 rule_pre_verdicts + 패턴 + 긍정 기본으로 score 결정.

    반환 형식:
      {score_17, score_18, deductions_17, deductions_18, evidence_17, evidence_18,
       summary_17, summary_18, self_confidence_17, self_confidence_18,
       rationale_17, rationale_18, flag_details, verify_mode_used_17, verify_mode_used_18}
    """
    # Import local to avoid circular (privacy_agent → _llm → ...)
    from v2.agents.group_b._llm import (
        LLMTimeoutError,
        call_bedrock_json,
        load_group_b_prompt,
    )

    # skip_llm: 테스트 / V1 Bedrock 미연결 환경
    if skip_llm:
        return _empty_llm_result()

    # Rule hard verdict 우선 채택 — score ∈ {5, 0} 인 경우만 LLM skip
    use_rule_only = {}
    for item_number in (17, 18):
        v = rule_pre_verdicts.get(item_number) if rule_pre_verdicts else None
        if v and v.get("confidence_mode") == "hard" and v.get("score") in (5, 0):
            use_rule_only[item_number] = True
        else:
            use_rule_only[item_number] = False

    segment_text = _build_segment_text(assigned_turns, fallback=transcript)

    async def _call_one(item_number: int, prompt_name: str) -> dict[str, Any]:
        if use_rule_only[item_number]:
            logger.info("#%d: rule hard verdict adopted, skipping LLM", item_number)
            return {"_verify_mode_used": True}
        # 개인정보 보호 (#17/#18) 는 compliance_based + Rule 패턴 탐지 중심.
        # 3-Persona 관점 차이가 점수에 영향 주지 않으므로 neutral 1회 호출로 단순화.
        # (이전: 3 persona 병렬 + reconcile_hybrid → min_compliance 규칙으로 어차피 min 채택.
        #  single neutral 이 비용·레이턴시 측면에서 더 효율적이며, T3 라우팅이 검수 보장.)
        system_prompt_base = load_group_b_prompt(prompt_name)
        user_message = _build_privacy_user_message(
            item_number=item_number, transcript=transcript,
            assigned_turns=assigned_turns, consultation_type=consultation_type,
            intent_summary=intent_summary, patterns_detected=patterns_detected,
        )
        sys_prompt = apply_persona_prefix(system_prompt_base, "neutral")
        try:
            raw = await call_bedrock_json(
                system_prompt=sys_prompt, user_message=user_message,
                max_tokens=2048, backend=llm_backend, bedrock_model_id=bedrock_model_id,
            )
            logger.info(
                "[DEBUG #%d neutral-only] LLM raw keys=%s",
                item_number,
                list(raw.keys()) if isinstance(raw, dict) else None,
            )
        except LLMTimeoutError:
            raise
        except Exception as e:
            logger.warning("privacy #%d neutral LLM 실패: %s", item_number, e)
            return {"_llm_failed": f"neutral LLM 실패: {e}"}

        if not isinstance(raw, dict):
            return {"_llm_failed": "LLM 응답 타입 오류"}

        # single_persona_only 와 동일한 hybrid 메타 구성 — UI 호환성 유지
        from v2.contracts.rubric import snap_score_v2
        snapped = snap_score_v2(item_number, int(raw.get("score", 0) or 0))
        self_conf = raw.get("self_confidence")
        try:
            conf_int = int(self_conf) if self_conf is not None else 4
        except (TypeError, ValueError):
            conf_int = 4
        conf_int = max(1, min(5, conf_int))

        raw_merged: dict[str, Any] = dict(raw)
        raw_merged["score"] = snapped
        raw_merged["confidence"] = conf_int / 5.0
        raw_merged["self_confidence"] = conf_int
        raw_merged["_persona_votes"] = {"neutral": snapped}
        raw_merged["_persona_step_spread"] = 0
        raw_merged["_persona_merge_path"] = "single"
        raw_merged["_persona_merge_rule"] = "single"
        raw_merged["_judge_reasoning"] = None
        raw_merged["_mandatory_human_review"] = False
        raw_merged["_persona_details"] = {
            "neutral": {
                "score": snapped,
                "judgment": str(raw.get("judgment") or raw.get("summary") or "")[:400],
                "summary": str(raw.get("summary") or "")[:400],
                "deductions": [
                    {"reason": str(d.get("reason", ""))[:200], "points": d.get("points")}
                    for d in (raw.get("deductions") or [])[:5]
                    if isinstance(d, dict)
                ],
                "evidence": [
                    {
                        "speaker": e.get("speaker"),
                        "quote": str(e.get("quote") or e.get("text") or "")[:200],
                        "turn_id": e.get("turn_id") or e.get("turn"),
                    }
                    for e in (raw.get("evidence") or [])[:3]
                    if isinstance(e, dict)
                ],
                "self_confidence": conf_int,
                "override_hint": raw.get("override_hint"),
            }
        }
        return raw_merged

    gather_result = await asyncio.gather(
        _call_one(17, "item_17_iv_procedure"),
        _call_one(18, "item_18_privacy_protection"),
        return_exceptions=True,
    )
    raw_17 = gather_result[0] if not isinstance(gather_result[0], Exception) else {"_llm_failed": str(gather_result[0])}
    raw_18 = gather_result[1] if not isinstance(gather_result[1], Exception) else {"_llm_failed": str(gather_result[1])}
    # LLMTimeoutError 양쪽 동시 발생 시 전파
    if isinstance(gather_result[0], LLMTimeoutError) and isinstance(gather_result[1], LLMTimeoutError):
        raise gather_result[0]

    def _extract(raw: dict, item_number: int) -> dict[str, Any]:
        """LLM raw → 통합 key 로 추출. 실패 시 빈 값 반환."""
        if raw.get("_verify_mode_used") or raw.get("_llm_failed"):
            return {
                f"deductions_{item_number}": [],
                f"evidence_{item_number}": [],
                f"summary_{item_number}": "",
                f"self_confidence_{item_number}": 3,
                f"rationale_{item_number}": None,
                f"verify_mode_used_{item_number}": bool(raw.get("_verify_mode_used")),
                f"llm_failed_{item_number}": raw.get("_llm_failed"),
                f"llm_score_{item_number}": None,
                f"override_hint_{item_number}": None,
                f"persona_votes_{item_number}": None,
                f"persona_step_spread_{item_number}": None,
                f"persona_merge_path_{item_number}": None,
                f"persona_merge_rule_{item_number}": None,
                f"judge_reasoning_{item_number}": None,
                f"mandatory_human_review_{item_number}": False,
            }
        return {
            f"deductions_{item_number}": raw.get("deductions", []) or [],
            f"evidence_{item_number}": raw.get("evidence", []) or [],
            f"summary_{item_number}": raw.get("summary", "") or "",
            f"self_confidence_{item_number}": _raw_self_conf(raw),
            f"rationale_{item_number}": raw.get("summary", "")[:80] if raw.get("summary") else None,
            f"verify_mode_used_{item_number}": False,
            f"llm_score_{item_number}": raw.get("score"),
            f"override_hint_{item_number}": raw.get("override_hint"),
            f"persona_votes_{item_number}": raw.get("_persona_votes"),
            f"persona_step_spread_{item_number}": raw.get("_persona_step_spread"),
            f"persona_merge_path_{item_number}": raw.get("_persona_merge_path"),
            f"persona_merge_rule_{item_number}": raw.get("_persona_merge_rule"),
            f"judge_reasoning_{item_number}": raw.get("_judge_reasoning"),
            f"mandatory_human_review_{item_number}": bool(raw.get("_mandatory_human_review")),
            f"persona_details_{item_number}": raw.get("_persona_details"),
        }

    merged: dict[str, Any] = {**_extract(raw_17, 17), **_extract(raw_18, 18)}
    merged["flag_details"] = []
    return merged


def _build_segment_text(assigned_turns: list[dict], fallback: str) -> str:
    """판사 프롬프트용 대화 구간 텍스트 (privacy 용)."""
    if not assigned_turns:
        return fallback[:2800]
    return "\n".join(
        f"{t.get('speaker', '')}: {t.get('text', '')}" for t in assigned_turns
    )[:2800]


def _inject_hybrid_fields_from_bundle(
    item: ItemVerdict, v1_eval: dict[str, Any], item_number: int,
) -> None:
    """v1_eval 에서 persona_votes_{n}/persona_*_{n}/judge_reasoning_{n} 추출해 item 에 주입."""
    votes = v1_eval.get(f"persona_votes_{item_number}")
    if votes:
        item["persona_votes"] = votes
    spread = v1_eval.get(f"persona_step_spread_{item_number}")
    if spread is not None:
        item["persona_step_spread"] = spread
    path = v1_eval.get(f"persona_merge_path_{item_number}")
    if path:
        item["persona_merge_path"] = path
    rule = v1_eval.get(f"persona_merge_rule_{item_number}")
    if rule is not None:
        item["persona_merge_rule"] = rule
    reasoning = v1_eval.get(f"judge_reasoning_{item_number}")
    if reasoning:
        item["judge_reasoning"] = reasoning
    if v1_eval.get(f"mandatory_human_review_{item_number}"):
        item["mandatory_human_review"] = True
    details = v1_eval.get(f"persona_details_{item_number}")
    if details:
        item["persona_details"] = details


def _raw_self_conf(raw: dict) -> int:
    """LLM 반환의 confidence (float 0-1 또는 int 1-5) → int 1~5."""
    val = raw.get("self_confidence") or raw.get("confidence", 0.7)
    if isinstance(val, int) and 1 <= val <= 5:
        return val
    f = float(val or 0.7)
    if 0.0 <= f <= 1.0:
        return max(1, min(5, round(f * 4) + 1))
    return max(1, min(5, int(f)))


def _resolve_override_hint(
    *,
    llm_hint: str | None,
    patterns_detected: list[str],
) -> str | None:
    """privacy override_hint 결정.

    - LLM 이 명시적으로 "privacy_leak" 반환하면 그대로 채택.
    - 패턴 A/B 감지 시 (제3자 맥락 또는 본인확인 전 선언급) privacy_leak 자동 주입.
    - 그 외 (허용 값 외 값 포함) None.
    """
    allowed = {"profanity", "privacy_leak", "uncorrected_misinfo"}
    if llm_hint in allowed:
        return llm_hint
    # 패턴 A/B 감지 시 — preamble "제3자 정보 안내" 캐치
    if PATTERN_A in patterns_detected or PATTERN_B in patterns_detected:
        return "privacy_leak"
    return None


def _build_privacy_user_message(
    *,
    item_number: int,
    transcript: str,
    assigned_turns: list[dict],
    consultation_type: str,
    intent_summary: dict,
    patterns_detected: list[str],
) -> str:
    del intent_summary
    lines = [f"## Consultation Type\n{consultation_type}\n"]
    lines.append(f"## Transcript\n{transcript}\n")
    if assigned_turns:
        lines.append("## Assigned Turns")
        for t in assigned_turns[:30]:
            lines.append(
                f"- [Turn {t.get('turn_id')}] {t.get('speaker')}: {t.get('text', '')[:200]}"
            )
        lines.append("")
    if patterns_detected:
        lines.append(f"## Pre-detected Patterns\n{', '.join(patterns_detected)}\n")
    lines.append(
        f"## Instructions\n항목 #{item_number} 을 평가하세요. "
        "score ∈ {5, 3, 0} 중 하나. JSON 만 반환. "
        "`patterns_detected` 배열 필수."
    )
    return "\n".join(lines)


async def _invoke_v1_incorrect_check_skeleton(
    *,
    transcript: str,
    assigned_turns: list[dict],
    consultation_type: str,
    intent_summary: dict,
    llm_backend: str,
    bedrock_model_id: str | None,
) -> dict[str, Any]:
    """Legacy skeleton — 현재 `_invoke_llm_or_skeleton` 로 대체. 호환성 유지용.

    Phase D1 에서 v2 전용 LLM 경로 (v2/prompts/group_b/item_17/item_18) 로 교체.
    현재는 빈 dict 반환 — 실제 score 는 `_resolve_score_with_rule_fallback` 이
    rule_pre_verdicts 또는 패턴 감지 결과로 결정.
    """
    logger.info(
        "privacy_agent skeleton — transcript_len=%d, turns=%d",
        len(transcript),
        len(assigned_turns),
    )
    del consultation_type, intent_summary, llm_backend, bedrock_model_id
    return {
        # Phase E1: skeleton 은 deductions/evidence 만 반환. score 는
        # _resolve_score_with_rule_fallback 이 결정.
        "deductions_17": [],
        "deductions_18": [],
        "evidence_17": [],
        "evidence_18": [],
        "summary_17": "",
        "summary_18": "",
        "self_confidence_17": 3,
        "self_confidence_18": 3,
        "rationale_17": None,
        "rationale_18": None,
        "flag_details": [],
    }
