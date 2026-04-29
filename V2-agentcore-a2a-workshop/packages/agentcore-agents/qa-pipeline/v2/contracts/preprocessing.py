# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Layer 1 preprocessing 계약 (PL 확정 2026-04-20).

설계서 p9-10 Layer 1 5개 서브 모듈 산출물 + Dev1 제안 `quality` 필드의 통합 계약.
PL 2번 공지로 키명 최종 승인:
    intent_type / detected_sections / deduction_triggers /
    pii_tokens / rule_pre_verdicts / quality

이 파일은 Layer 1 (Dev1) → Layer 2 (Dev2/Dev3) → Layer 3 (Dev1) 간 공유 데이터
형식의 단일 진실 원본(single source of truth). Dev5 의 `schemas/` 와 Enum/TypedDict
중복 정의 금지 — 중복 위험 있으면 Dev5 schemas 를 import 해 사용할 것.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


# ===========================================================================
# Enum-like Literals (Dev5 schemas 에 정식 Enum 으로 정의되면 그것을 import)
# ===========================================================================

MaskingVersion = Literal["v1_symbolic", "v2_categorical"]
"""PII 마스킹 포맷 버전. v1_symbolic 은 `***`, v2_categorical 은 `[NAME]` 류 10 토큰."""

PIICategory = Literal[
    "NAME",
    "PHONE",
    "ADDR",
    "CARD",
    "DOB",
    "EMAIL",
    "RRN",
    "ACCT",
    "ORDER",
    "OTHER",
    "UNKNOWN",
]
"""PII 추정 카테고리. v1_symbolic 에서도 inferred_category 로 사전 기록."""

DeductionTriggerType = Literal[
    "profanity",              # 욕설
    "contempt",               # 비하
    "arbitrary_disconnect",    # 임의 단선
    "preemptive_disclosure",   # 정보 선언급
    "privacy_leak",            # 개인정보 유출
    "uncorrected_misinfo",     # 오안내 미정정
]

RecommendedOverride = Literal["all_zero", "category_zero", "item_zero", "none"]

ConfidenceMode = Literal["hard", "soft"]
"""hard = Sub Agent 가 Rule score 채택 + LLM 스킵 가능, soft = LLM 재검증 필요."""

EvaluationMode = Literal[
    "full",
    "structural_only",
    "compliance_based",
    "partial_with_review",
    "skipped",
    "unevaluable",
]
"""설계서 p12 — 한계 투명성 원칙 6종."""

RoutingTier = Literal["T0", "T1", "T2", "T3"]


# ===========================================================================
# (a) quality — STT 품질 검증 결과
# ===========================================================================


class Quality(TypedDict, total=False):
    """Layer 1 (a) STT 품질 검증 결과. unevaluable 이면 하류 스킵."""

    transcription_confidence: float   # 0.0 ~ 1.0
    diarization_success: bool          # 화자 분리 성공 여부
    duration_sec: float                # 통화 길이
    unevaluable: bool                  # True 면 Layer 2/3 은 unevaluable 모드로만 기록
    # 상세 필드
    has_timestamps: bool
    masking_version: MaskingVersion
    reasons: list[str]                 # 품질 저하 사유
    # Layer 4 라우팅 hint (Dev5 소비)
    tier_route_override: RoutingTier | None


# ===========================================================================
# (b) detected_sections — 구간 분리
# ===========================================================================
#
# PL 확정 스키마: {"opening": [start_turn_idx, end_turn_idx], "body": [...], "closing": [...]}
# tuple[int, int] 형태를 Python JSON 직렬화 호환을 위해 list[int] 로 표현.
# 확장 필드(start_ts_sec / end_ts_sec) 는 별도 meta dict 로.
# ===========================================================================


class SectionTimestamps(TypedDict, total=False):
    """구간별 STT 타임스탬프 (있으면 채움)."""

    opening_start_sec: float
    opening_end_sec: float
    body_start_sec: float
    body_end_sec: float
    closing_start_sec: float
    closing_end_sec: float


class DetectedSectionsMeta(TypedDict, total=False):
    """detected_sections 의 부가 정보. 본체는 dict[str, list[int]]."""

    timestamps: SectionTimestamps
    agent_turn_ids: list[int]
    customer_turn_ids: list[int]
    turn_pairs: list[dict]   # V1 dialogue_parser.turn_pairs 와 동일


# ===========================================================================
# (c) pii_tokens — PII 정규화 결과
# ===========================================================================


class PIIToken(TypedDict, total=False):
    """PII 등장 1건. R6 forward-compat: v1_symbolic 에서도 inferred_* 기록."""

    raw: str                           # 원본 토큰 ("***" 또는 "[NAME]")
    utterance_idx: int                 # turn_id (0-based)
    canonical_token: str               # V2 canonical (예: "[PII_NAME_1]")
    inferred_category: PIICategory
    inference_confidence: float        # 0.0 ~ 1.0
    # 선택 — 문자 위치 (있으면)
    char_start: int
    char_end: int


# ===========================================================================
# (d) deduction_triggers — 감점 트리거 사전 탐지
# ===========================================================================
#
# PL 확정 스키마는 bool dict:
#   {"불친절": False, "개인정보_유출": False, "오안내_미정정": False}
#
# 상세 evidence/turn_ref 는 별도 필드(`deduction_trigger_details`) 로 sibling 추가.
# Layer 3 가 Override 판정 시 bool 로 빠른 체크 + details 로 상세 로깅.
# ===========================================================================


class DeductionTriggerDetail(TypedDict, total=False):
    """감점 트리거 1건의 상세. deduction_triggers bool 과 쌍으로 저장."""

    trigger_type: DeductionTriggerType
    turn_id: int
    evidence_text: str
    source: Literal["rule", "llm"]
    confidence: float
    pattern_id: str | None
    recommended_override: RecommendedOverride


# ===========================================================================
# (e) rule_pre_verdicts — Rule 1차 판정
# ===========================================================================
#
# PL 확정 키명: "item_01" / "item_02" / "item_17" (zero-padded 2자리).
# Dev2/Dev3 합의: item# flat dict. 카테고리 grouping 하지 않음.
# ===========================================================================


class RulePreVerdictElements(TypedDict, total=False):
    """항목별 구성요소 체크리스트. 항목마다 키 집합이 다름.

    공통 패턴(자주 쓰는 키):
      #1 greeting: {greeting, affiliation, agent_name}
      #2 closing:  {additional_inquiry, closing_greeting, agent_name}
      #3 overlap:  {overlap_count, stt_markers_present}
      #4 empathy:  {empathy_count, simple_response_count}
      #5 hold:     {hold_detected, before_count, after_count, silence_count}
      #6 polite:   {profanity_count, sigh_count, language_count, mild_count}
      #7 cushion:  {refusal_count, cushion_count}
      #8 paraphrase:{paraphrase_count, requery_count}
      #9 info_check:{info_check_count, courtesy_count, customer_provided_count}
      #16 mandatory:{mandatory_items_covered, mandatory_items_missing}
      #17 iv:      {iv_performed, preemptive_found, third_party}
      #18 privacy: {privacy_violation, third_party_disclosure}
    """


class RulePreVerdict(TypedDict, total=False):
    """1개 평가 항목에 대한 Rule 1차 판정. Layer 2 Sub Agent 의 verify 대상."""

    item_number: int                           # 1~18
    score: int                                 # ALLOWED_STEPS 준수 (snap_score 경유)
    confidence: float                           # 0.0 ~ 1.0
    confidence_mode: ConfidenceMode             # hard = LLM 생략 가능
    rationale: str
    evidence_turn_ids: list[int]
    evidence_snippets: list[str]
    elements: dict[str, bool | int]             # 항목별 구성요소 (위 주석 참조)
    recommended_for_llm_verify: bool
    # 항목별 V1 세부 필드 (예: greeting_turn / greeting_text / detected_keywords)
    # 는 top-level sibling 으로 저장 가능 (TypedDict total=False 로 확장 허용).


# ===========================================================================
# intent_type — 문의 유형 분류
# ===========================================================================
#
# PL 확정: `intent_type: str` (단일 문자열 예 "상품문의").
# 세부 구조(primary_intent/sub_intents/product/complexity) 는 sibling
# `intent_detail` 로 별도 저장. V1 `mandatory.py` 의 intent_summary 호환.
# ===========================================================================


class IntentDetail(TypedDict, total=False):
    """intent_type 의 부가 세부. V1 mandatory.intent_summary 와 호환."""

    primary_intent: str
    sub_intents: list[str]
    product: str
    complexity: Literal["simple", "moderate", "complex"]
    tenant_topic_ref: str | None


# ===========================================================================
# iv_evidence — 본인확인 절차 근거 턴 세트 (Dev3 요청)
# ===========================================================================


class IVEvidenceTurn(TypedDict, total=False):
    turn: int
    text: str
    pattern: str


class IVEvidence(TypedDict, total=False):
    iv_procedure_turns: list[IVEvidenceTurn]
    preemptive_turns: list[IVEvidenceTurn]
    third_party_turns: list[IVEvidenceTurn]


# ===========================================================================
# Preprocessing (Layer 1 종합 산출물) — PL 확정 스펙
# ===========================================================================


class Preprocessing(TypedDict, total=False):
    """Layer 1 산출물. QAStateV2.preprocessing 필드.

    키명은 PL 2번 공지(2026-04-20) 확정:
      intent_type / detected_sections / deduction_triggers / pii_tokens /
      rule_pre_verdicts / quality
    """

    # (a) STT 품질 검증 결과 (Dev1 제안 승인됨 — sibling 추가)
    quality: Quality

    # (b) 구간 분리 — {"opening": [start, end], "body": [...], "closing": [...]}
    detected_sections: dict[str, list[int]]
    detected_sections_meta: DetectedSectionsMeta     # 확장 메타 (별도 필드)

    # (c) PII 정규화
    pii_tokens: list[PIIToken]
    canonical_transcript: str                         # PII 치환된 본문
    masking_format_version: MaskingVersion

    # (d) 감점 트리거 사전 탐지
    #     bool dict — {"불친절": False, "개인정보_유출": False, "오안내_미정정": False}
    deduction_triggers: dict[str, bool]
    deduction_trigger_details: list[DeductionTriggerDetail]
    # Dev5 `v2/layer4/overrides_adapter.build_overrides_block()` 입력 (Dev1↔Dev5 합의 2026-04-20)
    has_all_zero_trigger: bool
    has_category_zero_categories: list[str]       # CategoryKey 영문 (예: ["privacy_protection"])
    recommended_override: RecommendedOverride     # top-level 단일값 (all_zero/category_zero/item_zero/none)

    # (e) Rule 1차 판정 — item_01/item_02/item_17 zero-padded flat dict
    rule_pre_verdicts: dict[str, RulePreVerdict]

    # intent 분류
    intent_type: str                                  # 예: "상품문의" (설계서 p23 스키마 + primary 값)
    intent_type_primary: str                          # intent_type 의 명시적 alias (외부 consumer 하위 호환)
    intent_detail: IntentDetail

    # 선택: 본인확인 근거 턴 (Dev3 요청)
    iv_evidence: IVEvidence

    # V1 호환 — Sub Agent 포팅 부담 최소화
    agent_turn_assignments: dict[str, dict]

    # 파싱된 턴 배열 — segment_splitter 가 생성. 각 dict 는 {turn_id, speaker, text, segment}.
    # HITL 검수 UI (항목별 "파싱 원문" 섹션) 가 이 값을 직접 렌더. 원문 raw transcript 대신.
    turns: list[dict[str, Any]]

    # Layer 1 실행 진단
    layer1_diagnostics: list[dict]
    # [{"module": "quality_gate", "elapsed_ms": 12, "status": "ok"}, ...]


# ===========================================================================
# 고정 트리거 키 (Dev1/3/Layer3 공통 사용)
# ===========================================================================

DEDUCTION_TRIGGER_KEYS: tuple[str, ...] = (
    "불친절",
    "개인정보_유출",
    "오안내_미정정",
)


def empty_deduction_triggers() -> dict[str, bool]:
    """모든 트리거를 False 로 초기화한 dict 반환."""
    return {k: False for k in DEDUCTION_TRIGGER_KEYS}


# ===========================================================================
# Rule 1차 판정 대상 항목 (Layer 1 rule_pre_verdictor.py 가 채우는 대상)
# ===========================================================================
#
# V1 자산 재활용 매트릭스:
#   #1  — V1 nodes/skills/pattern_matcher.py::PatternMatcher.match_greeting
#   #2  — V1 PatternMatcher.match_closing
#   #3  — V1 PatternMatcher.detect_speech_overlap
#   #4  — V1 PatternMatcher.count_empathy
#   #5  — V1 PatternMatcher.detect_hold_mentions
#   #6  — V1 PatternMatcher.detect_inappropriate
#   #7  — V1 PatternMatcher.detect_cushion_words
#   #8  — V1 mandatory 의 paraphrase/requery 로직 Rule 부분
#   #9  — V1 mandatory 의 고객정보 확인 로직 Rule 부분
#   #16 — V1 mandatory 의 mandatory_items 이행 체크
#   #17 — V1 PatternMatcher.check_identity_verification (iv_performed/preemptive_found)
#   #18 — V1 PatternMatcher.check_identity_verification (third_party) +
#         PatternMatcher.detect_pii
# ===========================================================================

RULE_VERDICT_TARGET_ITEMS: tuple[int, ...] = (
    1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 17, 18,
)


def item_key(item_number: int) -> str:
    """item_number → zero-padded key (PL 확정 포맷 'item_01'/'item_17')."""
    return f"item_{item_number:02d}"
