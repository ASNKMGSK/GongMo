# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Phase A1 — Layer 간 인터페이스 계약 초안 (Dev1 기안).

설계서 p9-12 기반. V1 state/skills/dialogue_parser 를 재활용하며 V2 요구사항
(4-Layer, evaluation_mode, T2/T3 라우팅, PII 토큰 포워드 호환성) 을 반영.

이 파일은 TypedDict / Protocol 정의만 보관한다. 실제 구현은 layer1/, layer3/ 에.

협업 필요 항목(다른 Dev 합의 대상):
- Dev2/Dev3: preprocessing.rule_pre_verdicts 를 Sub Agent 가 어떻게 consume 할지
- Dev4: rag_context (Golden-set / 업무지식 / 금지어) 주입 스키마
- Dev5: QAState V2 최상위 필드 확정 + HITL 라우팅 flag 통일
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict
import operator


# ===========================================================================
# 1) STT 품질/원본 입력 — Layer 1 진입점
# ===========================================================================
#
# 설계서 p9 (a) 품질 검증 요구:
#   - 화자 분리 성공 여부
#   - 타임스탬프 유무
#   - 전사 신뢰도 점수
#   - 품질 저하 시 인간 검수 자동 라우팅
# ===========================================================================

# 평가 모드 (설계서 p12 — 한계 투명성 원칙)
EvaluationMode = Literal[
    "full",              # 완전 평가 수행
    "structural_only",   # 마스킹 등으로 내용 검증 불가, 구조/절차만 평가
    "compliance_based",  # 규정 준수 여부 기준 평가 (내용 무관)
    "partial_with_review",  # AI 판정 + 인간 검수 필수
    "skipped",           # 해당 상황 부재 (만점 처리)
    "unevaluable",       # STT 품질 등으로 평가 불가
]

# HITL 라우팅 Tier (설계서 경계 ±3점 → T2/T3)
RoutingTier = Literal[
    "T1",  # 자동 확정
    "T2",  # 자동 + 경고 (경계 근처, 경량 인간 검수)
    "T3",  # 인간 검수 필수 (품질 저하, 개인정보, 등급 경계)
]


class MaskingFormat(TypedDict, total=False):
    """PII 마스킹 포맷 메타데이터. V1(***) → V2(v2_categorical) 전환 대비."""

    version: Literal["v1_symbolic", "v2_categorical"]
    # v1_symbolic: 모든 PII 가 *** 로 치환
    # v2_categorical: [NAME] [PHONE] [ADDR] [CARD] [DOB] [EMAIL] [RRN] [ACCT] [ORDER] [OTHER]
    token_spec_ref: str  # tenants/<id>/pii_tokens.md 링크 (없으면 fallback)


class STTMetadata(TypedDict, total=False):
    """STT 상위 메타데이터 — Layer 1 (a) 품질 검증에서 소비."""

    transcription_confidence: float  # 0.0 ~ 1.0
    speaker_diarization_success: bool  # 화자 분리 성공 여부
    duration_sec: float  # 통화 길이 (초)
    has_timestamps: bool  # 턴별 타임스탬프 유무
    masking_format: MaskingFormat


class QAInputV2(TypedDict, total=False):
    """V2 파이프라인 입력 컨테이너. (V1 QAState.transcript/consultation_type/... 상위 호환)."""

    # --- 필수 입력 ---
    transcript: str
    session_id: str
    customer_id: str
    tenant_id: str  # "generic" (프로토타입) / "finance" / "telco" / ... (설계서 §6)

    # --- 선택 입력 ---
    consultation_type: str  # V1 호환 — intent 분류가 재추정 (n-ref)
    stt_metadata: STTMetadata

    # --- 운영 플래그 ---
    llm_backend: Literal["bedrock", "sagemaker"]
    bedrock_model_id: str | None
    # 프롬프트 튜닝 배치 경로 — phase_c/reporting 스킵 (V1 호환)
    skip_phase_c_and_reporting: bool


# ===========================================================================
# 2) Layer 1 산출물 (preprocessing) — Layer 2/3 입력
# ===========================================================================
#
# 설계서 p9-10 (a)~(e) 5개 서브모듈 산출물의 구조화된 집합.
# QAState V2 의 `preprocessing` 필드에 저장되어 Layer 2 Sub Agent 가 consume.
# ===========================================================================


# ----- (a) 품질 검증 결과 -------------------------------------------------
class QualityVerdict(TypedDict, total=False):
    """Layer 1 (a) STT 품질 검증 결과."""

    passed: bool  # False 면 하류 평가 스킵
    reasons: list[str]  # 품질 저하 사유 (예: ["diarization_failed", "confidence<0.6"])
    evaluation_mode_override: EvaluationMode  # 일반적으로 "unevaluable"
    tier_route_override: RoutingTier  # "T3"
    # 세부 지표
    transcription_confidence: float
    speaker_diarization_success: bool
    duration_sec: float
    masking_version: str


# ----- (b) 구간 분리 -----------------------------------------------------
class SegmentSpan(TypedDict, total=False):
    """opening/body/closing 한 구간. V1 dialogue_parser 의 segments 필드 확장."""

    turn_ids: list[int]  # 구간에 속한 turn_id 리스트
    start_turn: int | None
    end_turn: int | None
    start_ts_sec: float | None  # STT timestamp (있으면)
    end_ts_sec: float | None


class DialogueSegments(TypedDict, total=False):
    """Layer 1 (b) 구간 분리 결과. V1 segments 상위 호환."""

    opening: SegmentSpan  # = V1 intro
    body: SegmentSpan
    closing: SegmentSpan
    # V1 compat: 화자별 turn_id, turn_pairs
    agent_turn_ids: list[int]
    customer_turn_ids: list[int]
    turn_pairs: list[dict[str, Any]]


# ----- (c) PII 토큰 정규화 결과 -------------------------------------------
class PIIToken(TypedDict, total=False):
    """PII 등장 1건. V2_categorical 전환 시 inferred_category 활용."""

    turn_id: int
    char_start: int
    char_end: int
    original_surface: str  # "***" 또는 "[NAME]"
    canonical_token: str  # V2 canonical (예: "[PII_NAME_1]")
    inferred_category: Literal[
        "NAME", "PHONE", "ADDR", "CARD", "DOB", "EMAIL",
        "RRN", "ACCT", "ORDER", "OTHER", "UNKNOWN",
    ]
    inference_confidence: float  # 0.0 ~ 1.0 (카테고리 추정 신뢰도)


class PIINormalization(TypedDict, total=False):
    """Layer 1 (c) PII 정규화 결과. V2 카테고리 전환 시 유일한 수정 지점."""

    source_version: Literal["v1_symbolic", "v2_categorical"]
    canonical_transcript: str  # PII 가 canonical_token 으로 치환된 본문
    tokens: list[PIIToken]
    total_pii_count: int


# ----- (d) 감점 트리거 사전 탐지 ------------------------------------------
class DeductionTrigger(TypedDict, total=False):
    """감점 트리거 1건. Rule 1차 탐지 + LLM 보강 (Layer 1 혼합)."""

    trigger_type: Literal[
        "profanity",           # 욕설
        "contempt",             # 비하
        "arbitrary_disconnect",  # 임의 단선
        "preemptive_disclosure",  # 선언급 (본인확인 전 정보 발화)
    ]
    turn_id: int
    evidence_text: str  # Evidence 인용 (원칙 3 충족)
    source: Literal["rule", "llm"]
    confidence: float
    pattern_id: str | None  # rule 매칭 시 constants.py 의 패턴 id
    # Orchestrator(Layer 3) override 참고용
    recommended_override: Literal[
        "all_zero",           # 전체 0점 (불친절)
        "category_zero",       # 해당 카테고리 0점 (개인정보 유출)
        "item_zero",           # 해당 항목만 0점
        "none",                # Layer 2 판정 존중
    ]


class DeductionTriggerResult(TypedDict, total=False):
    """Layer 1 (d) 감점 트리거 사전 탐지 집합."""

    triggers: list[DeductionTrigger]
    has_all_zero_trigger: bool  # 불친절 등 전체 0점 사유 존재
    has_category_zero_triggers: list[str]  # 카테고리 0점 사유 (예: ["개인정보 보호"])


# ----- (e) Rule 1차 판정 결과 --------------------------------------------
class RulePreVerdict(TypedDict, total=False):
    """1개 평가 항목에 대한 Rule 1차 판정.

    Layer 2 Sub Agent 가 `verify` 타입일 때 LLM 재검증 대상.
    confidence_mode 가 'hard' 이면 Sub Agent 가 이 값을 그대로 채택하고
    LLM 호출을 생략(또는 skipped)해 비용 절감.
    """

    item_number: int  # 1~18
    score: int  # Rule 1차 점수 (5/3/0 등, 항목별 ALLOWED_STEPS 준수)
    confidence: float  # 0.0 ~ 1.0
    confidence_mode: Literal["hard", "soft"]  # hard = LLM 생략 가능
    rationale: str  # 짧은 설명
    evidence_turn_ids: list[int]  # 근거 턴 ID (원칙 3: Evidence 인용)
    evidence_snippets: list[str]  # 근거 텍스트 발췌 (Rule matched)
    elements: dict[str, bool]  # 구성요소 체크리스트 (예: greeting/affiliation/agent_name)
    # LLM 이 재검증해야 할지 Layer 2 에게 알려주는 signal
    recommended_for_llm_verify: bool


class IntentClassification(TypedDict, total=False):
    """문의 유형 분류 결과 (V1 mandatory 의 intent_summary 와 호환)."""

    primary_intent: str
    sub_intents: list[str]
    product: str
    complexity: Literal["simple", "moderate", "complex"]
    tenant_topic_ref: str | None  # tenants/<id>/mandatory_scripts/<intent>.md


class RulePreVerdictBundle(TypedDict, total=False):
    """Layer 1 (e) Rule 1차 판정 묶음. item_number -> RulePreVerdict.

    현재 범위 (V1 `greeting.py` / `mandatory.py` / `incorrect_check.py` 재활용):
      - #1 첫인사 (greeting)
      - #2 끝인사 (greeting)
      - #17 정보 확인 절차 (incorrect_check)
      - #16 필수 안내 이행 (mandatory)
      - (Intent 분류는 별도 필드 intent)

    Sub Agent 가 필요한 재검증만 LLM 으로 수행하게끔 recommended_for_llm_verify 시그널.
    """

    verdicts: dict[int, RulePreVerdict]  # key = item_number
    intent: IntentClassification


# ----- 통합 preprocessing 컨테이너 -----------------------------------------
class PreprocessingOutput(TypedDict, total=False):
    """Layer 1 종합 산출물. QAState V2 의 preprocessing 필드로 저장."""

    # (a)
    quality: QualityVerdict
    # (b)
    segments: DialogueSegments
    # V1 호환 — agent_turn_assignments (각 Sub Agent 의 턴 범위)
    agent_turn_assignments: dict[str, Any]
    # 파싱된 turns (세그먼트/화자 주석 포함)
    turns: list[dict[str, Any]]
    # (c)
    pii: PIINormalization
    # (d)
    deduction_triggers: DeductionTriggerResult
    # (e)
    rule_pre_verdicts: RulePreVerdictBundle

    # Layer 1 실행 진단
    layer1_diagnostics: dict[str, Any]
    # [{"module": "quality_gate", "elapsed_ms": 12, "status": "ok"}, ...]


# ===========================================================================
# 3) Layer 2 산출물 — 기존 V1 evaluations 와 호환
# ===========================================================================
#
# Dev2/Dev3 Sub Agent 가 evaluations 리스트에 항목별 채점 결과 append.
# V1 EvaluationResult 와 동일 스키마 유지 — 단, `evaluation_mode` 필수 추가.
# ===========================================================================


class EvidenceRef(TypedDict, total=False):
    turn_id: int
    text: str  # 인용 원문 (원칙 3)


class ItemEvaluation(TypedDict, total=False):
    """Layer 2 Sub Agent 의 항목별 채점 결과."""

    item_number: int
    item_name: str
    max_score: int
    score: int  # 반드시 snap_score(item_number, ...) 경유
    evaluation_mode: EvaluationMode  # V2 신규 필수
    deductions: list[dict[str, Any]]  # {reason, points, evidence_ref, turn_ref}
    evidence: list[EvidenceRef]  # Evidence 인용 (원칙 3)
    confidence: float  # 0.0 ~ 1.0
    # Layer 1 rule_pre_verdicts 와 비교 — 수정 시그널
    rule_verdict_diff: dict[str, Any] | None
    # {"rule_score": 5, "llm_score": 3, "reason": "..."}


class EvaluationEntry(TypedDict, total=False):
    """QAState.evaluations 의 원소 (V1 호환)."""

    status: Literal["success", "partial", "error"]
    agent_id: str  # "greeting-agent", "work-accuracy-agent", ...
    evaluation: ItemEvaluation


# ===========================================================================
# 4) Layer 3 산출물 — Orchestrator 최종 확정
# ===========================================================================


class CategoryScore(TypedDict, total=False):
    category: str
    category_en: str
    score: int
    max_score: int
    items: list[int]  # 포함된 item_number 리스트


class OverrideApplication(TypedDict, total=False):
    """Layer 3 (b) Override 적용 기록."""

    trigger_type: str  # DeductionTrigger.trigger_type 와 동일
    action: Literal["all_zero", "category_zero", "item_zero"]
    affected_items: list[int]
    evidence: list[EvidenceRef]
    source_layer: Literal["layer1", "layer2"]
    rationale: str


class ConsistencyFlag(TypedDict, total=False):
    """Layer 3 (c) 전체 일관성 체크 결과."""

    flag_type: str  # 예: "greeting_courtesy_mismatch"
    items: list[int]
    description: str
    requires_review: bool


class GradeVerdict(TypedDict, total=False):
    """Layer 3 (d) 등급 판정."""

    total_score: int
    max_possible: int
    grade: str  # "A" / "B" / "C" / 조직별
    boundary_distance: int  # 경계까지 점수 차 (±3 이하면 T2)
    routing_tier: RoutingTier
    routing_reasons: list[str]


class OrchestratorOutputV2(TypedDict, total=False):
    """Layer 3 종합 산출물. QAState V2 의 orchestrator 필드."""

    category_scores: list[CategoryScore]
    overrides_applied: list[OverrideApplication]
    consistency_flags: list[ConsistencyFlag]
    grade: GradeVerdict
    # Layer 4 에 전달될 최종 확정 evaluations 스냅샷
    final_evaluations: list[EvaluationEntry]
    total_score: int
    max_possible: int


# ===========================================================================
# 5) QAState V2 — 위 필드들을 담는 LangGraph TypedDict
# ===========================================================================
#
# 주의: 실제 state 는 packages/.../qa-pipeline/state.py 에 병합되어야 함.
# Dev5 가 계약 확정 후 state.py 에 반영. 아래는 초안 스펙.
# ===========================================================================


class QAStateV2(TypedDict, total=False):
    """V2 QA pipeline shared state (LangGraph TypedDict).

    V1 QAState 와 호환 유지 (transcript/evaluations/... 그대로).
    추가 필드: preprocessing, orchestrator, routing_tier, evaluation_modes.
    """

    # --- 입력 (V1 호환) ---
    transcript: str
    session_id: str
    customer_id: str
    consultation_type: str
    tenant_id: str
    stt_metadata: STTMetadata

    llm_backend: str
    bedrock_model_id: str | None

    # --- Layer 1 산출물 (신규) ---
    preprocessing: PreprocessingOutput

    # V1 호환 — 기존 필드 유지 (하위 호환성)
    parsed_dialogue: dict[str, Any]
    agent_turn_assignments: dict[str, Any]

    # --- Layer 2 산출물 (V1 호환 — operator.add 리듀서) ---
    evaluations: Annotated[list[EvaluationEntry], operator.add]
    deduction_log: Annotated[list[dict[str, Any]], operator.add]
    intent_summary: dict[str, Any]
    accuracy_verdict: dict[str, Any]
    flags: dict[str, Any]

    # --- Layer 3 산출물 (신규) ---
    orchestrator: OrchestratorOutputV2

    # --- Layer 4 산출물 (신규 — Dev5 영역) ---
    post_processing: dict[str, Any]  # confidence / tier / evidence_pack / drift

    # V1 호환 — verification / score_validation / report
    verification: dict[str, Any]
    score_validation: dict[str, Any]
    report: dict[str, Any]

    # --- 오케스트레이션/운영 (V1 호환) ---
    plan: dict[str, Any]
    current_phase: str
    next_node: str
    parallel_targets: list[str]
    completed_nodes: Annotated[list[str], operator.add]
    node_timings: Annotated[list[dict[str, Any]], operator.add]
    node_traces: Annotated[list[dict[str, Any]], operator.add]

    # --- 에러 ---
    error: str | None


# ===========================================================================
# 6) 순서 및 short-circuit 계약 (설계서 p9)
# ===========================================================================
#
# 엄격 순차:
#   Layer 1 (a → b → c → d → e)
#     ↓ quality.passed == False 이면 Layer 2 진입 차단 → 즉시 Layer 3 로
#   Layer 2 (Sub Agent 8개 병렬 또는 그룹별 순차)
#     ↓
#   Layer 3 (순수 집계/override/consistency/grade — LLM 없음)
#     ↓
#   Layer 4 (confidence/tier/evidence/drift)
#
# skip_phase_c_and_reporting == True 면 Layer 2 후 Layer 3 의 집계까지만 수행
# (consistency_flag 는 건너뛰고 total_score/grade/final_evaluations 만 채움).
# ===========================================================================
