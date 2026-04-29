# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
V2 공용 Enum / Literal 정의 (Dev5 주관, Phase A1).

설계서 참조:
 - §5.3 p12 — evaluation_mode 6종
 - §8.2 p17-18 — Tier 4종 (T0/T1/T2/T3)
 - §9 p19-20 — masking_format 2종 (v1_symbolic / v2_categorical)
 - §10.1 p21 — HITL 2종 (policy_driven / uncertainty_driven)

모든 Enum 은 JSON 직렬화 편의를 위해 `Literal` 로 정의한다.
값 추가·변경 시 `qa_output_v2.py` / `sub_agent_io.py` / `routing/` 전체를 동기화.
"""

from __future__ import annotations

from typing import Literal


# ============================================================================
# 평가 모드 (Evaluation Mode) — 설계서 §5.3 p12
# ============================================================================
#
# 원칙 5 "한계를 명시적으로 선언" 의 핵심 구현.
# 각 평가 항목은 자신이 어떤 조건 하에 평가되었는지를 투명하게 노출한다.
#
#   full                : 완전 평가 (모든 정보 사용 가능)
#   structural_only     : 마스킹 등으로 내용 검증 불가, 구조/절차만 평가
#                         예) #9 고객정보 확인
#   compliance_based    : 규정 준수 여부 기준 평가 (내용 무관)
#                         예) #17 정보 확인 절차, #18 정보 보호 준수
#   partial_with_review : AI 판정 + 인간 검수 필수
#                         예) #15 정확한 안내 (업무지식 RAG 부재 시)
#   skipped             : 해당 상황 부재 (만점 고정)
#                         예) 말겹침, 쿠션어 (거절 상황 없을 때)
#   unevaluable         : STT 품질 등으로 평가 불가 (항목 점수는 None 또는 0, T3 라우팅)
# ============================================================================
EvaluationMode = Literal[
    "full",
    "structural_only",
    "compliance_based",
    "partial_with_review",
    "skipped",
    "unevaluable",
]


# ============================================================================
# Tier 라우팅 (설계서 §8.2 p17-18)
# ============================================================================
#
#   T0 (자동 통과)        — 목표 ~70% (초기 30~40% 권장). 모든 신호 high, 강제 검수 조건 없음.
#   T1 (스팟체크)         — 5~10%. T0 중 무작위 샘플링. 품질 모니터링 목적.
#   T2 (플래그 검수)       — 15~20%. 항목 confidence ≤ 2 또는 신호 간 불일치.
#   T3 (필수 검수)         — ≤5%. 감점 트리거 / STT 품질 저하 / 총점 경계 ±3 / VIP·민원 /
#                          개인정보 3개 항목(#9, #17, #18) / AI self-report "판단 불가".
# ============================================================================
RoutingTier = Literal["T0", "T1", "T2", "T3"]


# ============================================================================
# HITL 트리거 성격 (설계서 §10.1 p21)
# ============================================================================
#
#   policy_driven       : 비즈니스 룰에 의한 강제 검수 — Confidence 와 무관.
#                         (감점 트리거 / STT 품질 저하 / 민원·VIP / 합불 경계 / 신입 / 개인정보 3개 항목)
#   uncertainty_driven  : AI 자체 신뢰도 부족 — Confidence 신호에 의한 검수.
#                         (Confidence low / Rule-LLM 불일치 / Evidence 품질 저하 / "판단 불가")
# ============================================================================
HITLDriver = Literal["policy_driven", "uncertainty_driven"]


# ============================================================================
# 마스킹 포맷 버전 (설계서 §9 p19-20)
# ============================================================================
#
#   v1_symbolic   : 모든 PII 가 *** 단일 symbol 로 치환 (현재 운영 환경).
#   v2_categorical: [NAME] / [PHONE] / [RRN] / [ACCOUNT] / [CARD] / [ADDRESS] /
#                   [EMAIL] / [AMOUNT] / [DATE] / [PII_OTHER] 등 카테고리 보존 형태 (미래).
# ============================================================================
MaskingVersion = Literal["v1_symbolic", "v2_categorical"]


# ============================================================================
# PII 카테고리 (설계서 §9.3 v2 토큰 스펙 표 + PL 확정 2026-04-20)
# ============================================================================
#
# v1_symbolic 환경에서는 inferred_category 필드로만 존재 (문맥 기반 추정).
# v2 전환 시 canonical_token 으로 승격.
#
# Dev1 `contracts/preprocessing.py::PIICategory` 와 정합 (PL 확정 키):
#   NAME / PHONE / ADDR / CARD / DOB / EMAIL / RRN / ACCT / ORDER / OTHER / UNKNOWN
# ============================================================================
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


# ============================================================================
# Override 액션 (설계서 §5.2 p11, §4 Layer 3 (b))
# ============================================================================
#
#   all_zero       : 전체 평가 0점 (불친절).
#   category_zero  : 해당 대분류 전체 0점 (개인정보 유출 → 개인정보 보호 0점 등).
#   item_zero      : 해당 평가항목만 0점.
#   none           : Override 미적용 (Sub Agent 판정 존중).
# ============================================================================
OverrideAction = Literal["all_zero", "category_zero", "item_zero", "none"]


# ============================================================================
# Override 트리거 유형 (설계서 §5.2 p11 표 + Dev1 canonical 정합)
# ============================================================================
#
# Dev1 `contracts/preprocessing.py::DeductionTriggerType` 와 정합:
#   profanity              : 욕설 (불친절 카테고리)
#   contempt               : 비하 (불친절 카테고리)
#   arbitrary_disconnect   : 임의 단선 (불친절 카테고리)
#   preemptive_disclosure  : 본인확인 전 선언급 (#17 패턴 A)
#   privacy_leak           : 개인정보 유출 (제3자 정보 안내 등)
#   uncorrected_misinfo    : 오안내 후 미정정
#
# Layer 4 tier_router 는 `rudeness` 상위 카테고리를 {profanity, contempt,
# arbitrary_disconnect} 중 하나로 매핑하여 처리.
# STT 품질 저하는 OverrideTrigger 가 아니라 `preprocessing.quality.passed`
# 필드로 별도 처리 (Dev1 확정).
# ============================================================================
OverrideTrigger = Literal[
    "profanity",
    "contempt",
    "arbitrary_disconnect",
    "preemptive_disclosure",
    "privacy_leak",
    "uncorrected_misinfo",
]


# ============================================================================
# Sub Agent 처리 상태 (V1 EvaluationResult.status 호환)
# ============================================================================
SubAgentStatus = Literal["success", "partial", "error"]


# ============================================================================
# 8개 대분류 식별자 (설계서 §4 Layer 2, 부록 A)
# ============================================================================
#
# Sub Agent 의 agent_id (예: "greeting-agent") 의 카테고리 키로 사용.
# 각 키의 포함 평가항목 번호:
#   greeting_etiquette      : #1, #2
#   listening_communication : #3(skipped), #4, #5
#   language_expression     : #6, #7
#   needs_identification    : #8, #9
#   explanation_delivery    : #10, #11
#   proactiveness           : #12, #13, #14
#   work_accuracy           : #15, #16
#   privacy_protection      : #17, #18
# ============================================================================
CategoryKey = Literal[
    "greeting_etiquette",
    "listening_communication",
    "language_expression",
    "needs_identification",
    "explanation_delivery",
    "proactiveness",
    "work_accuracy",
    "privacy_protection",
]


# ============================================================================
# 카테고리 메타 (설계서 부록 A p30)
# ============================================================================
#
# Dev2/Dev3 Sub Agent 구현에서 import 하여 category label / items 매핑에 활용.
# Layer 3 집계 시 대분류별 max_score 검증에도 사용.
# ============================================================================
CATEGORY_META: dict[CategoryKey, dict] = {
    "greeting_etiquette": {
        "label_ko": "인사 예절",
        "label_en": "Greeting etiquette",
        "items": [1, 2],
        "max_score": 10,
    },
    "listening_communication": {
        "label_ko": "경청 및 소통",
        "label_en": "Listening and communication",
        "items": [4, 5],
        "max_score": 10,
    },
    "language_expression": {
        "label_ko": "언어 표현",
        "label_en": "Language expression",
        "items": [6, 7],
        "max_score": 10,
    },
    "needs_identification": {
        "label_ko": "니즈 파악",
        "label_en": "Needs identification",
        "items": [8, 9],
        "max_score": 10,
    },
    "explanation_delivery": {
        "label_ko": "설명력 및 전달력",
        "label_en": "Explanation and delivery",
        "items": [10, 11],
        "max_score": 15,
    },
    "proactiveness": {
        "label_ko": "적극성",
        "label_en": "Proactiveness",
        "items": [12, 13, 14],
        "max_score": 15,
    },
    "work_accuracy": {
        "label_ko": "업무 정확도",
        "label_en": "Work accuracy",
        "items": [15, 16],
        "max_score": 20,
    },
    "privacy_protection": {
        "label_ko": "개인정보 보호",
        "label_en": "Privacy protection",
        "items": [17, 18],
        "max_score": 10,
    },
}


# ============================================================================
# 신한 부서별 평가 — xlsx (`부서별_AI_QA_평가표_통합.xlsx`) 정합 (2026-04-28)
# ============================================================================
#
# 핵심 차이:
#   - #11 두괄식 → #10 "설명의 명확성 및 두괄식 답변" 단일 항목 (max 10) 으로 통합 → #11 제거
#   - #13 부연 → #12 "문제 해결 의지 및 부연 안내" 단일 항목 (max 5) 으로 통합 → #13 제거
#   - #15/#16 업무 정확도 → 부서특화 dept 노드 (901-922 synthetic) 로 대체 → 제거
#   - 부서특화 4개 항목 (각 부서마다 다름, 30점) 추가 — `shinhan_dept/registry.py` 참조
#
# 합계 검증:
#   공통: 5+5(인사) + 5+5(경청) + 5+5(언어) + 5+5(니즈) + 10(설명) + 5+5(적극) + 5+5(개인정보) = 70
#   부서특화: 30
#   = 100 ✓
# ============================================================================
SHINHAN_CATEGORY_META: dict[str, dict] = {
    "greeting_etiquette": {
        "label_ko": "인사 예절",
        "label_en": "Greeting etiquette",
        "items": [1, 2],
        "max_score": 10,
    },
    "listening_communication": {
        "label_ko": "경청 및 소통",
        "label_en": "Listening and communication",
        "items": [4, 5],
        "max_score": 10,
    },
    "language_expression": {
        "label_ko": "언어 표현",
        "label_en": "Language expression",
        "items": [6, 7],
        "max_score": 10,
    },
    "needs_identification": {
        "label_ko": "니즈 파악",
        "label_en": "Needs identification",
        "items": [8, 9],
        "max_score": 10,
    },
    # 설명력 — #10 단일 항목 max 10 (V2 generic 의 #11 두괄식 통합됨)
    "explanation_delivery": {
        "label_ko": "설명력",
        "label_en": "Explanation and delivery",
        "items": [10],
        "max_score": 10,
    },
    # 적극성 — #12 + #14 (V2 generic 의 #13 부연 통합됨)
    "proactiveness": {
        "label_ko": "적극성",
        "label_en": "Proactiveness",
        "items": [12, 14],
        "max_score": 10,
    },
    # work_accuracy 카테고리 자체 제거 — 부서특화 dept 로 대체
    "privacy_protection": {
        "label_ko": "개인정보 보호",
        "label_en": "Privacy protection",
        "items": [17, 18],
        "max_score": 10,
    },
}


def get_category_meta(site_id: str | None) -> dict[str, dict]:
    """tenant 별 카테고리 메타 조회. 신한 시 SHINHAN_CATEGORY_META, 그 외 V2 generic.

    부서특화 dept categories (901-922) 는 별도 처리 (`shinhan_dept/registry.py`).
    이 함수는 공통 카테고리만 반환.
    """
    if (site_id or "").lower() == "shinhan":
        return SHINHAN_CATEGORY_META
    return CATEGORY_META  # type: ignore[return-value]


def get_category_items_set(site_id: str | None) -> frozenset[int]:
    """tenant META 가 채점 대상으로 인정하는 item_number 집합."""
    meta = get_category_meta(site_id)
    return frozenset(i for cat in meta.values() for i in cat["items"])


# ============================================================================
# T3 강제 라우팅 대상 평가항목 (설계서 §8.2 Tier 표 "개인정보 관련 3개 항목")
# ============================================================================
#
# 이 3개 항목이 evaluable (skipped 가 아님) 이면 해당 상담 전체는 T3 로 강제 라우팅.
# v1_symbolic 환경에서는 개인정보 내용 검증이 구조적으로 불가하므로,
# AI 는 플래그만 제공하고 최종 판정은 반드시 인간 검수자가 내린다.
# ============================================================================
FORCE_T3_ITEMS: frozenset[int] = frozenset({9, 17, 18})


# ============================================================================
# 등급 경계 (설계서 §8.3, §4 Layer 3 (d))
# ============================================================================
#
# 경계 ±3점 이내면 confidence 와 무관하게 T2 이상으로 강제 라우팅.
# GRADE_BOUNDARIES 는 (grade, min_total_score) 튜플의 내림차순 리스트.
# 조직별로 customize 가능하도록 tenant_config 에서 override 할 것.
# ============================================================================
GRADE_BOUNDARIES: list[tuple[str, int]] = [
    ("S", 95),  # 탁월
    ("A", 85),  # 우수
    ("B", 70),  # 보통
    ("C", 50),  # 미흡
    ("D", 0),   # 부진
]

GRADE_BOUNDARY_MARGIN: int = 3  # ±3점 이내면 T2 강제


# ============================================================================
# Tier 비교 유틸 — 여러 Tier 중 가장 엄격한 값 (T3 가 가장 strict)
# ============================================================================
#
# Layer 3 `grader.py`, `orchestrator_v2.py`, Layer 4 `tier_router.py` 등
# 다수 모듈이 동일 로직을 중복 정의하던 것을 공용화.
# ============================================================================

_TIER_ORDER: tuple[str, ...] = ("T0", "T1", "T2", "T3")


def tier_max(*tiers: str) -> str:
    """여러 Tier 중 가장 엄격한 (T3 가 가장 strict) 값을 반환.

    Parameters
    ----------
    *tiers : str
        Tier 문자열 가변인자. None / 빈 문자열 / `_TIER_ORDER` 외 값은 무시.

    Returns
    -------
    str
        가장 높은 Tier. 유효한 인자가 하나도 없으면 "T0".
    """
    valid = [t for t in tiers if t in _TIER_ORDER]
    if not valid:
        return "T0"
    return max(valid, key=_TIER_ORDER.index)
