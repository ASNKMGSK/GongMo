# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
V2 최종 JSON Output 스키마 (Phase A1, Dev5 주관).

설계서 §11.1 p23 (전체 스키마 예시) 및 §11.2 p24 (평가항목 객체 구조) 를
pydantic 모델로 엄격 정의한다. Layer 4 report_generator_v2 의 최종 직렬화 대상.

설계 원칙 (설계서 §11.3):
 1. Evidence 는 반드시 배열 — 근거 없는 판정 금지
 2. Confidence 는 final + raw signals 모두 저장 (threshold 재조정 대비)
 3. versions 필드 필수 (drift 분석 전제)
 4. tenant 필드 최상위 (Multi-tenant 쿼리 1차 키)

외부 의존성: pydantic v2 사용 (TypedDict 대신 pydantic 으로 validation/serialization 동시 지원).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from v2.schemas.enums import (
    HITLDriver,
    MaskingVersion,
    OverrideAction,
    OverrideTrigger,
    PIICategory,
    RoutingTier,
)
from v2.schemas.sub_agent_io import EvidenceQuote


# ===========================================================================
# 최상위 메타 블록
# ===========================================================================


class VersionsBlock(BaseModel):
    """versions — 설계서 §11.1 p23.

    drift 분석을 위해 "이 평가가 어떤 자산 버전으로 나왔는지" 를 기록.
    """

    model: str = Field(..., description="LLM 모델 ID (예: claude-sonnet-4-6)")
    rubric: str = Field(..., description="Rubric 버전 (예: generic-v1.0)")
    prompt_bundle: str = Field(..., description="Sub Agent 프롬프트 번들 버전")
    golden_set: str = Field(..., description="Golden-set 버전")
    pipeline: str = Field(default="v2", description="파이프라인 버전 (v1 / v2)")


class MaskingFormatBlock(BaseModel):
    """masking_format — 설계서 §9.3 Forward-compatibility."""

    version: MaskingVersion
    spec: str = Field(..., description="마스킹 스펙 설명 텍스트")


class STTMetadataBlock(BaseModel):
    """stt_metadata — 설계서 §11.1, §4 Layer 1 (a)."""

    transcription_confidence: float = Field(..., ge=0.0, le=1.0)
    speaker_diarization_success: bool
    duration_sec: float = Field(..., ge=0.0)
    has_timestamps: bool = True


# ===========================================================================
# preprocessing 블록 (Layer 1 산출물 요약 — 최종 JSON 에 노출)
# ===========================================================================


class DetectedSectionRange(BaseModel):
    """구간 범위 [start, end] — 턴 index 기준."""

    start: int = Field(..., ge=0)
    end: int = Field(..., ge=0)


class DetectedSections(BaseModel):
    """구간 분리 결과 — 설계서 §4 Layer 1 (b)."""

    opening: DetectedSectionRange
    body: DetectedSectionRange
    closing: DetectedSectionRange


class DeductionTriggersBlock(BaseModel):
    """감점 트리거 사전 탐지 결과 — 설계서 §4 Layer 1 (d).

    PL 확정 스키마 (2026-04-20, Dev1 `contracts/preprocessing.py::DEDUCTION_TRIGGER_KEYS`):
        {"불친절": bool, "개인정보_유출": bool, "오안내_미정정": bool}

    세부 evidence / turn_ref 는 별도 `deduction_trigger_details` sibling 필드로 저장.
    Layer 3 Override 는 이 3종 bool 을 O(1) 체크.

    내부 attr 이름은 Python-friendly (rudeness/privacy_leak/uncorrected_misinfo) 로
    유지하되, JSON 직렬화 시 설계서 §11.1 예시대로 한글 키로 출력되도록 alias 설정.
    """

    rudeness: bool = Field(default=False, alias="불친절")
    privacy_leak: bool = Field(default=False, alias="개인정보_유출")
    uncorrected_misinfo: bool = Field(default=False, alias="오안내_미정정")

    model_config = {"populate_by_name": True}


class PIITokenRecord(BaseModel):
    """PII 토큰 1건 — 설계서 §9.3 (2) 카테고리 추정 필드."""

    raw: str = Field(..., description="원본 마스킹 surface (예: '***' 또는 '[NAME]')")
    utterance_idx: int = Field(..., ge=0)
    inferred_category: PIICategory
    inference_confidence: float = Field(..., ge=0.0, le=1.0)


class PreprocessingBlock(BaseModel):
    """preprocessing — 설계서 §11.1 p23.

    Layer 1 산출물 중 최종 JSON 에 노출되는 필드만 포함.
    전체 Layer 1 산출물 (rule_pre_verdicts 등) 은 QAStateV2 에만 보관.

    intent_type 확장 (PL 승인 2026-04-20):
      - Dev1 권고로 단순 문자열에서 dict 형태(primary_intent/sub_intents/product/complexity/
        tenant_topic_ref) 로 확장 허용. str | dict 둘 다 받는 Union.
      - 외부 consumer 하위 호환을 위해 `intent_type_primary: str` sibling 필드 추가 —
        dict 입력 시 `intent_type["primary_intent"]` 값을 복제 노출, str 입력 시 원문.
    """

    # str | dict Union — str 은 legacy 호환, dict 은 Dev1 확장 스키마
    # Dev1 `contracts/preprocessing.py::IntentDetail` 구조와 정합:
    #   {"primary_intent": str, "sub_intents": list[str], "product": str,
    #    "complexity": "simple"|"moderate"|"complex", "tenant_topic_ref": str | None}
    intent_type: str | dict = Field(..., description="문의 유형 (str 단순 형태 또는 dict 상세 형태)")

    # 하위 호환: dict 이든 str 이든 primary 값만 뽑아 str 로 노출.
    # 외부 consumer 는 이 필드만 읽어도 기존 str 파이프라인과 호환.
    intent_type_primary: str = Field(..., description="intent_type 의 primary 값 (외부 하위 호환용)")

    detected_sections: DetectedSections
    deduction_triggers: DeductionTriggersBlock
    pii_tokens: list[PIITokenRecord] = Field(default_factory=list)

    @field_validator("intent_type_primary")
    @classmethod
    def primary_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("intent_type_primary 는 빈 문자열 금지 (외부 consumer 하위 호환)")
        return v


# ===========================================================================
# evaluation 블록 (Layer 2 산출물 + Layer 3 확정)
# ===========================================================================


class ConfidenceSignals(BaseModel):
    """Confidence 4 신호 원천값 — 설계서 §8.1.

    §11.3 원칙 2: final + raw signals 모두 저장 (threshold 재계산 대비).
    """

    # §8.1 신호 1: LLM Self-Confidence (1~5)
    llm_self: int = Field(..., ge=1, le=5)
    # §8.1 신호 2: Rule vs LLM 일치도
    rule_llm_agreement: bool
    # §8.1 신호 3: RAG 유사사례 분산 (Reasoning RAG top-k stdev)
    rag_stdev: float | None = Field(default=None, ge=0.0)
    # §8.1 신호 4: Evidence 품질 (high / medium / low — enum 대신 Literal 이 과하므로 str)
    evidence_quality: str = Field(..., pattern=r"^(high|medium|low)$")
    # 항목별 가중 조합 결과 (0~5, 내부 표준 스케일)
    weighted_composite: float | None = Field(default=None, ge=0.0, le=5.0)
    # PL Q5 2026-04-20 — RAG sample_size penalty trace (선택 필드).
    # Dev4 ReasoningResult.sample_size 기록 + penalty 적용 여부.
    rag_sample_size: int | None = Field(default=None, ge=0)
    rag_small_sample_penalty_applied: bool = Field(default=False)


class ConfidenceBlock(BaseModel):
    """Confidence — 설계서 §11.2 p24.

    final 은 1~5 정수 (Sub Agent self 와 동일 스케일로 정렬).
    signals 는 raw 원천값 — 추후 재계산 가능.
    """

    final: int = Field(..., ge=1, le=5)
    signals: ConfidenceSignals


class ItemResult(BaseModel):
    """평가항목 1건 — 설계서 §11.2 p24.

    PL 확정 공통 응답 포맷 (force_t3 필드 포함).
    """

    item: str = Field(..., description="항목명 (예: 첫인사)")
    # 1~18: V2 generic 항목 / 901~999: 신한 부서특화 dept synthetic items
    item_number: int = Field(..., ge=1, le=999)
    max_score: int
    evaluation_mode: str  # Literal EvaluationMode — JSON 직렬화 편의상 str
    # unevaluable 모드에서는 None 허용 (category_score 합산에서 제외).
    # evaluation_mode 다음에 선언해야 validator 가 info.data 로 참조 가능 (pydantic v2).
    score: int | None
    judgment: str
    evidence: list[EvidenceQuote] = Field(..., min_length=0)
    # 감점 내역 (선택 — 만점이면 빈 배열)
    deductions: list[dict] = Field(default_factory=list)
    confidence: ConfidenceBlock
    # HITL / 라우팅 플래그 (Layer 4 가 채움)
    flag: str | None = Field(default=None, description="HITL 플래그 사유 (없으면 null)")
    mandatory_human_review: bool = Field(default=False)
    # PL 확정 공통 응답 포맷: 항목 단위 T3 강제 플래그 (#9/#17/#18 및 Sub Agent 예외 판정).
    force_t3: bool = Field(default=False)
    # 3-Persona 앙상블 메타 (Phase 5, 2026-04-21) — 프론트 드로어 표시용.
    # single_persona_only 경로는 persona_votes={"neutral": N}, persona_merge_path="single".
    persona_votes: dict[str, int] | None = Field(default=None)
    persona_step_spread: int | None = Field(default=None)
    persona_merge_path: str | None = Field(default=None)
    persona_merge_rule: str | None = Field(default=None)
    judge_reasoning: str | None = Field(default=None)
    persona_details: dict[str, dict] | None = Field(default=None)
    # Post-debate judge LLM (AG2 토론 종료 후 transcript 보고 결정) — 메인 판정.
    # 2026-04-27: 사용자 정책 — 모든 토론 결과는 판사가 최종 결정.
    judge_score: int | None = Field(default=None)
    judge_deductions: list[dict] | None = Field(default=None)
    judge_evidence: list[dict] | None = Field(default=None)
    judge_failure_reason: str | None = Field(default=None)
    # 판사가 KNN 으로 끌어다 인용한 HITL 인간 검수 사례 메타 (qa-hitl-cases AOSS).
    # NodeDrawer "📚 판사 참조 HITL 사례" 섹션에서 표시.
    judge_human_cases: list[dict] | None = Field(default=None)

    @field_validator("score")
    @classmethod
    def score_bounded(cls, v: int | None, info) -> int | None:
        if v is None:
            # unevaluable 모드만 허용. 그 외 모드는 validator 가 검출.
            return v
        max_score = info.data.get("max_score")
        if max_score is not None and (v < 0 or v > max_score):
            raise ValueError(f"score {v} out of bounds [0, {max_score}]")
        return v

    @field_validator("score")
    @classmethod
    def score_none_only_when_unevaluable(cls, v: int | None, info) -> int | None:
        if v is None and info.data.get("evaluation_mode") != "unevaluable":
            raise ValueError("score=None 은 evaluation_mode='unevaluable' 에서만 허용")
        return v

    @field_validator("evidence")
    @classmethod
    def evidence_required_when_full(cls, v: list[EvidenceQuote], info) -> list[EvidenceQuote]:
        """원칙 3: full 모드 이면 evidence 최소 1개 필수.

        skipped / unevaluable / compliance_based / structural_only / partial_with_review
        모드는 근거 없이 만점/None 허용 (마스킹 등 구조적 제약).
        """
        mode = info.data.get("evaluation_mode")
        if mode == "full" and len(v) == 0:
            raise ValueError("evaluation_mode=full 이면 evidence 최소 1개 필수 (원칙 3)")
        return v


class CategoryBlock(BaseModel):
    """대분류 1건 — 설계서 §11.2 p24 (`category`, `items`).

    Dev2 O4 합의:
      - achieved_score = Σ item.score (unevaluable 제외, skipped 는 item.max_score 로 합산)
      - max_score 는 CATEGORY_META 기준값에서 unevaluable 항목의 max_score 만큼 차감
        (= 분모도 조정). Layer 3 Orchestrator 가 계산·검증.

    `category_label_en` 은 다국어(i18n) 대비 선택 필드 (2026-04-20 추가).
    Layer 3 `aggregator` / Layer 4 `_group_items_by_category` 가 CATEGORY_META.label_en 으로 주입.
    미세팅 시 None — 외부 consumer 하위 호환.
    """

    category: str = Field(..., description="한국어 카테고리명")
    category_key: str = Field(..., description="CategoryKey enum 값")
    category_label_en: str | None = Field(
        default=None, description="영어 카테고리명 (i18n 대비, 선택)"
    )
    max_score: int         # unevaluable 차감 후 값
    achieved_score: int    # unevaluable 제외, skipped 만점 처리된 합계
    items: list[ItemResult]


class EvaluationBlock(BaseModel):
    """evaluation — 설계서 §11.1 p23 (`evaluation.categories`)."""

    # max_length 상향 — V2 generic 8 카테고리 + 신한 dept synthetic 카테고리 (부서별 최대 4개)
    # 합쳐서 최대 ~13. 여유 두고 20.
    categories: list[CategoryBlock] = Field(..., min_length=1, max_length=20)


# ===========================================================================
# overrides / final_score / routing 블록
# ===========================================================================


class OverrideEntry(BaseModel):
    """Override 1건 — 설계서 §5.2, §11.1."""

    trigger: OverrideTrigger
    action: OverrideAction
    affected_items: list[int] = Field(default_factory=list)
    reason: str
    evidence: list[EvidenceQuote] = Field(default_factory=list)


class OverridesBlock(BaseModel):
    """overrides — 설계서 §11.1 p23."""

    applied: bool = False
    reasons: list[OverrideEntry] = Field(default_factory=list)


class FinalScoreBlock(BaseModel):
    """final_score — 설계서 §11.1 p23."""

    raw_total: int = Field(..., ge=0, le=100)
    after_overrides: int = Field(..., ge=0, le=100)
    grade: str = Field(..., description="S/A/B/C/D 또는 tenant customize")


class PriorityFlag(BaseModel):
    """priority_flag — 검수자 UI 의 우측 영역에서 표시."""

    code: str       # 예: "closing_low_confidence" / "privacy_protection_force_t3"
    description: str
    severity: str   # "info" | "warn" | "critical"
    item_numbers: list[int] = Field(default_factory=list)


class RoutingBlock(BaseModel):
    """routing — 설계서 §11.1 p23, §8.2, §10.

    HITL 종류(policy/uncertainty) 는 hitl_driver 로 명시적 분리 (§10.1).
    """

    decision: RoutingTier
    hitl_driver: HITLDriver | None = Field(
        default=None,
        description="T2/T3 일 때만 세팅. None 이면 T0/T1.",
    )
    priority_flags: list[PriorityFlag] = Field(default_factory=list)
    estimated_review_time_min: int = Field(default=0, ge=0)
    # Tier 결정에 기여한 raw 신호 (threshold 재조정 대비 — §11.3 원칙 2)
    tier_reasons: list[str] = Field(default_factory=list)


# ===========================================================================
# Summary / coaching 블록 (V1 report_generator LLM 텍스트 이식)
# ===========================================================================


class SummaryBlock(BaseModel):
    """summary — V1 report_generator 의 summary 섹션 유지.

    설계서 §10.2 검수자 UX 의 '상단 영역' 에 표시.
    """

    total_score: int
    max_score: int = 100
    grade: str
    one_liner: str = Field(..., description="한 줄 평가")
    strengths: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)


class CoachingPoint(BaseModel):
    """coaching_point 1건 — V1 구조 유지."""

    item_number: int | None = None
    category: str | None = None
    message: str
    priority: str = Field(default="normal", pattern=r"^(high|normal|low)$")


# ===========================================================================
# 최상위 QAOutputV2 — 최종 JSON 직렬화 진입점
# ===========================================================================


class QAOutputV2(BaseModel):
    """V2 QA 평가 최종 출력 (설계서 §11.1 p23).

    Layer 4 report_generator_v2 의 유일한 직렬화 대상.
    `model_dump(by_alias=True)` 호출 시 설계서 예시와 동일한 JSON 구조 반환.
    """

    # --- 최상위 식별 ---
    consultation_id: str
    tenant: str = Field(..., description="tenant_id (예: generic)")
    evaluated_at: str = Field(..., description="ISO-8601 timestamp (UTC)")

    # --- 메타 ---
    versions: VersionsBlock
    masking_format: MaskingFormatBlock
    stt_metadata: STTMetadataBlock

    # --- Layer 1 ---
    preprocessing: PreprocessingBlock

    # --- Layer 2 + Layer 3 ---
    evaluation: EvaluationBlock
    overrides: OverridesBlock
    final_score: FinalScoreBlock

    # --- Layer 4 (Dev5 영역) ---
    routing: RoutingBlock
    summary: SummaryBlock
    coaching_points: list[CoachingPoint] = Field(default_factory=list)

    # --- 운영 진단 (선택) ---
    diagnostics: dict = Field(default_factory=dict, description="layer별 elapsed/error 등")

    model_config = {"extra": "forbid"}
