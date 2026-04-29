# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Sub Agent 공통 IO 계약 (Phase A1, Dev5 기안 — Dev2/Dev3 협의 대상).

8개 대분류 Sub Agent 는 모두 동일한 응답 스키마로 응답한다.
Orchestrator (Layer 3) 와 Post-processing (Layer 4) 는 이 스키마만 알면 된다.

설계서 참조:
 - §4 Layer 2 — 카테고리 Sub Agent 역할 (카테고리 내 항목 동시 평가)
 - §8.1 — Confidence 4 신호 중 LLM Self-Confidence 가 Sub Agent 출력 필드
 - §11.2 p24 — 평가항목 객체 구조 (category/items/evidence/confidence)

합의 포인트 (Dev2/Dev3 에게 먼저 회람):
 1. 각 Sub Agent 는 자신의 카테고리 items[] 전부를 1회 LLM 호출로 반환
 2. items[].score 는 반드시 snap_score(item_number, score) 경유 (V1 규약 유지)
 3. evaluation_mode 는 항목별 개별 지정 (카테고리 단위가 아님)
 4. evidence 는 {speaker, timestamp, quote} 배열, 최소 1개 필수 (원칙 3)
 5. llm_self_confidence 는 1~5 정수 (프롬프트에 앵커 명시, §8.1)
 6. rule_pre_verdict_consumed 는 Layer 1 과의 대조 로그 — Layer 4 confidence 계산 입력
"""

from __future__ import annotations

from typing import Any, TypedDict

from v2.schemas.enums import (
    CategoryKey,
    EvaluationMode,
    SubAgentStatus,
)


# ===========================================================================
# Evidence — 근거 인용 (원칙 3)
# ===========================================================================


class EvidenceQuote(TypedDict, total=False):
    """평가 판정의 근거가 되는 STT 발화 1건.

    설계서 §11.2 p24: `{speaker, timestamp, quote}` 배열 형태 강제.
    판정당 최소 1개 필수 (프롬프트 단계에서 생성 차단).
    """

    # 화자 레이블.
    # Dev2 O1 반영: 권장값은 "상담사" / "고객" (V1 constants.AGENT_SPEAKER_PREFIXES /
    # CUSTOMER_SPEAKER_PREFIXES 와 정렬). tenant 별 customize 는 허용하되
    # tenant_config 에서 alias 명시 필수. Literal 제약을 걸지 않는 이유는
    # Multi-tenant 확장 시 "고객사 명"/"Agent" 등 override 가 필요하기 때문.
    speaker: str
    # 타임스탬프 (예: "00:00:02") — STT 가 제공하는 경우에만, 없으면 None
    timestamp: str | None
    # 인용 텍스트 (원문 그대로, 수정 금지)
    quote: str
    # turn_id — V1 dialogue_parser 가 생성하는 턴 식별자 (Layer 1 → 하류 참조)
    turn_id: int | None


# ===========================================================================
# Deduction — 감점 항목 1건
# ===========================================================================


class DeductionEntry(TypedDict, total=False):
    """항목별 감점 사유 1건. 다건 가능.

    score = max_score - Σ points (score_validation 에서 수치 검증).
    """

    reason: str          # 감점 사유 (예: "3가지 요소 중 소속 누락")
    points: int          # 감점 점수 (정수)
    rule_id: str | None  # qa_rules.py deduction_rules 의 condition 참조 (있으면)
    evidence_refs: list[int]  # evidence[] 배열의 인덱스 참조 (다중 evidence 지원)


# ===========================================================================
# LLM Self-Confidence (설계서 §8.1 첫 번째 신호)
# ===========================================================================


class LLMSelfConfidence(TypedDict, total=False):
    """Sub Agent 가 판정과 함께 강제 출력하는 자기 확신도.

    프롬프트에 `1~5 자기확신도 앵커 명시` (§8.1).
    Layer 4 Confidence 계산의 가장 빠른/저렴한 필터.
    """

    score: int  # 1~5 (1=매우 낮음, 5=매우 높음)
    rationale: str | None  # 짧은 설명 — 낮을 때 특히 중요


# ===========================================================================
# Rule-LLM 대조 (설계서 §8.1 두 번째 신호 계산 입력)
# ===========================================================================


class RuleLLMDelta(TypedDict, total=False):
    """Layer 1 rule_pre_verdict 와 LLM 판정의 차이.

    Sub Agent 는 Layer 1 rule_pre_verdict 를 consume 해서 이 필드를 채운다.
    Layer 4 `rule_llm_agreement` 신호가 이 필드를 직접 읽는다.
    """

    has_rule_pre_verdict: bool  # Layer 1 에서 1차 판정을 수행했는지
    rule_score: int | None       # Layer 1 이 매긴 점수
    llm_score: int               # Sub Agent 의 LLM 판정 점수
    agreement: bool              # rule_score == llm_score 여부
    # 불일치 시 LLM 이 rule 을 뒤집은 사유 (또는 반대) — 검수자 UX 에 노출
    override_reason: str | None
    # verify 모드 여부: Layer 1 rule_pre_verdict.confidence_mode == "hard" 이면
    # Sub Agent 가 LLM 호출을 생략하고 rule 을 그대로 채택할 수 있음 (비용 절감).
    verify_mode_used: bool


# ===========================================================================
# Sub Agent 항목 판정 (카테고리 내 평가항목 1개 당 1건)
# ===========================================================================


class ItemVerdict(TypedDict, total=False):
    """Sub Agent 의 평가항목 1건 판정.

    설계서 §11.2 p24 item 객체와 1:1 대응.
    """

    # 항목 식별 (V1 qa_rules 와 동일)
    item_number: int       # 1~18
    item_name: str         # "첫인사" / "끝인사" / "쿠션어 활용" / ...
    item_name_en: str | None  # Optional — 로깅/덤프 편의 (예: "opening_greeting")

    # 점수 (V1 snap_score 강제 경유 — ALLOWED_STEPS 준수)
    max_score: int         # qa_rules.max_score
    # snap_score(item_number, raw_llm_score) 결과.
    # unevaluable 모드에서는 None 허용 — category_score 합산에서 제외
    # (Dev2 O4 확정: unevaluable=합산 제외, skipped=max_score 로 만점 처리).
    score: int | None

    # 평가 모드 (§5.3) — 항목별 개별 지정
    evaluation_mode: EvaluationMode

    # 판정 설명 (설계서 §11.2 p24: `judgment`)
    judgment: str          # 한 줄 요약 (예: "인사말/소속/상담사명 모두 포함")

    # 감점 내역
    deductions: list[DeductionEntry]

    # 근거 인용 (원칙 3) — 최소 1개 필수
    evidence: list[EvidenceQuote]

    # Confidence 신호 원천 (Layer 4 가 종합)
    llm_self_confidence: LLMSelfConfidence
    rule_llm_delta: RuleLLMDelta | None  # Rule 1차 판정이 있었던 항목만 (예: #1, #2, #17)

    # skipped/unevaluable/partial_with_review 사유 (해당 모드에서만)
    mode_reason: str | None

    # ---- V2 신규 — Dev2/Dev3 협의 반영 ----

    # 항목별 메타 (Dev2 O2 제안 수용). V1 항목별 details 와 호환:
    #   #2 {farewell_elements: {...}, stt_truncation_suspected: bool}
    #   #7 {refusal_count, cushion_word_count}
    #   #8 {paraphrase_found, requery_count}
    # 자유 형식 — Layer 4 리포트/consistency_check 에서 활용. None/누락 허용.
    details: dict[str, Any] | None

    # HITL / T3 강제 힌트 (Dev3 제안 수용, Sub Agent → Layer 4 전달용)
    #   force_t3           : Sub Agent 자체 판정으로 T3 강제 권고.
    #                        FORCE_T3_ITEMS={9,17,18} 는 Layer 4 가 자동 처리하므로
    #                        이 플래그는 "해당 FORCE_T3_ITEMS 외 예외 상황" 용.
    #   mandatory_human_review : 본 항목 인간 검수 필수 권고 (uncertainty-driven HITL 보조).
    force_t3: bool
    mandatory_human_review: bool

    # Sub Agent 자체 에러/부분실패 태그 (V1 reconciler 규약 유지)
    #   "[SKIPPED_INFRA]" — 인프라 폴백 (Bedrock throttle / LLM 실패)
    #   "[RAG_UNAVAILABLE]" — 업무지식/Reasoning RAG 미연결
    infra_tags: list[str]

    # ---- 3-Persona 앙상블 (Phase 5, 2026-04-21) ----
    #
    # persona_votes    : {"strict": int, "neutral": int, "loose": int} — 원본 snap 점수.
    #                    실패한 persona 는 키 누락.
    # persona_step_spread : ALLOWED_STEPS 내 step 단위 spread (0=합의, >=2=충돌).
    # persona_merge_path  : "stats" (통계 머지) | "judge" (판사 숙고) | "judge_fallback".
    # persona_merge_rule  : 통계 머지 경로일 때 "min_compliance"/"median_full_split"/
    #                       "mode_majority"/"single". 판사 경로이면 None.
    # judge_reasoning     : 판사 경로일 때만 — 판사의 숙고 근거 (2~4 문장).
    persona_votes: dict[str, int]
    persona_step_spread: int
    persona_merge_path: str
    persona_merge_rule: str | None
    judge_reasoning: str | None

    # persona_details : 각 persona 별 개별 LLM 응답 원본 보존 (UI 에서 비교 표시용).
    # 키: "strict" | "neutral" | "loose" (실패 시 키 누락).
    # 값: {score, judgment, deductions[], evidence[], self_confidence, override_hint, summary}.
    # single_persona_only=True 경로는 "neutral" 키만 담긴다.
    persona_details: dict[str, dict[str, Any]]


# ===========================================================================
# Sub Agent 카테고리 응답 (1 Sub Agent = 1 응답)
# ===========================================================================


class SubAgentResponse(TypedDict, total=False):
    """Sub Agent 1개가 반환하는 표준 응답.

    Layer 2 병렬 실행 시 각 Sub Agent 가 이 스키마로 응답하면
    Layer 3 Orchestrator 가 단순 집계 로직만으로 카테고리 점수를 확정 가능.
    """

    # Sub Agent 식별 (로깅/디버깅)
    agent_id: str                # "greeting-agent" / "privacy-protection-agent" / ...
    category: CategoryKey
    status: SubAgentStatus

    # 항목별 판정 (카테고리에 속한 모든 item_number 포함 — 빠진 항목은 score_validation 이 검출)
    items: list[ItemVerdict]

    # 카테고리 소계 (Sub Agent 가 미리 계산해서 반환 — Orchestrator 가 검증 용도로 재계산)
    #
    # category_score 합산 규칙 (Dev2 O4 확정):
    #   - evaluation_mode="full" / "structural_only" / "compliance_based" / "partial_with_review"
    #       → items[i].score 그대로 합산 (0 포함).
    #   - evaluation_mode="skipped"
    #       → items[i].max_score 로 합산 (만점 고정, 예: #3 경청 5점).
    #   - evaluation_mode="unevaluable"
    #       → 합산에서 **제외** (category_max 에서도 차감 → 분모/분자 동시 조정).
    # 즉, unevaluable 항목이 있으면 category_score / category_max 둘 다 줄어든다.
    # Layer 3 Orchestrator 가 이 규칙대로 재검증.
    category_score: int          # 위 규칙으로 합산된 소계
    category_max: int            # 위 규칙으로 조정된 category 만점 (unevaluable 차감)

    # 카테고리 전반 Confidence (Sub Agent self-report — Layer 4 참조용)
    category_confidence: int     # 1~5 (카테고리 전체에 대한 자기 확신도)

    # 실행 진단
    llm_backend: str             # "bedrock" | "sagemaker"
    llm_model_id: str | None
    elapsed_ms: int | None

    # 에러/부분 실패 사유 (status != "success" 일 때)
    error_message: str | None
