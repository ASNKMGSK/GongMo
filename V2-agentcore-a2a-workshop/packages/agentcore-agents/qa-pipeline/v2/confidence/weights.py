# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
항목별 Confidence 신호 가중치 테이블 (설계서 §8.1 "가중 조합").

설계 근거 (p17):
 > 4개 신호는 단순 OR/AND 가 아니라 항목별로 가중 조합된다.
 > 예컨대 "정확한 안내" 항목은 Evidence 품질과 Rule 일치도 가중치를 높게,
 > "쿠션어" 는 LLM Self-Confidence 와 RAG 분산 가중치를 높게 설정한다.
 > 가중치 자체는 Phase 0 이후 실제 데이터로 calibration 된다.

주의:
 - 초기 가중치는 설계서 §8.1 예시에 근거한 기본값 (Phase 0 calibration 전 임시).
 - Phase E1 이후 실제 인간-AI 일치도 데이터로 재조정 필요.
 - 4개 신호 각 가중치 합은 항목별로 1.0 (정규화).
 - tenant 별 override 는 `tenants/<tenant_id>/tenant_config.yaml` 의
   `confidence.item_weights.{item_number:str}` 블록에서 로드 (2026-04-20 이관).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# 4 신호 키 (고정)
SIGNAL_KEYS = ("llm_self", "rule_llm_agreement", "rag_stdev", "evidence_quality")


# ---------------------------------------------------------------------------
# 항목별 기본 가중치 (Phase 0 calibration 전 초기값)
# ---------------------------------------------------------------------------
# 형태: {item_number: {signal_key: weight}}. 합 = 1.0.
#
# 가중 배분 논리 (설계서 §8.1):
#   - Rule 1차 판정이 있는 항목 (#1, #2, #17): rule_llm_agreement 가중치↑
#   - 감정/맥락 판단 항목 (#4, #7): llm_self + rag_stdev↑
#   - 업무지식 RAG 의존 항목 (#15): evidence_quality + rule_llm_agreement↑
#   - 구조/절차 항목 (#9, #17, #18): rule_llm_agreement + evidence_quality↑
#   - 기본 (Explanation / Proactiveness): 균등에 가까움
# ---------------------------------------------------------------------------

ITEM_WEIGHTS: dict[int, dict[str, float]] = {
    # === 카테고리 1: 인사 예절 ===
    # Rule 1차 판정 강함 (3요소 포함 여부) → rule_llm_agreement 가중
    1: {"llm_self": 0.20, "rule_llm_agreement": 0.50, "rag_stdev": 0.10, "evidence_quality": 0.20},
    2: {"llm_self": 0.20, "rule_llm_agreement": 0.50, "rag_stdev": 0.10, "evidence_quality": 0.20},

    # === 카테고리 2: 경청 및 소통 ===
    # #3 경청/말겹침은 skipped (만점 고정) — 계산 대상 아님. weights 유지만.
    3: {"llm_self": 0.25, "rule_llm_agreement": 0.25, "rag_stdev": 0.25, "evidence_quality": 0.25},
    # 호응/공감 — 감정 맥락, 사람 의견 갈림 → llm_self + rag_stdev↑
    4: {"llm_self": 0.35, "rule_llm_agreement": 0.15, "rag_stdev": 0.30, "evidence_quality": 0.20},
    # 대기 멘트 — rule 로 커버 가능 + LLM 보강
    5: {"llm_self": 0.25, "rule_llm_agreement": 0.35, "rag_stdev": 0.15, "evidence_quality": 0.25},

    # === 카테고리 3: 언어 표현 ===
    # 정중한 표현 — 금지어 사전 rule + LLM 맥락
    6: {"llm_self": 0.30, "rule_llm_agreement": 0.30, "rag_stdev": 0.15, "evidence_quality": 0.25},
    # 쿠션어 활용 — 맥락 판단, 사람 의견 갈림 → 설계서 §8.1 예시대로 llm_self + rag_stdev↑
    7: {"llm_self": 0.40, "rule_llm_agreement": 0.15, "rag_stdev": 0.30, "evidence_quality": 0.15},

    # === 카테고리 4: 니즈 파악 ===
    # 문의 파악 및 복창 — 중성
    8: {"llm_self": 0.30, "rule_llm_agreement": 0.20, "rag_stdev": 0.20, "evidence_quality": 0.30},
    # 고객정보 확인 — structural_only. 본인확인 절차 순서 rule → rule_llm_agreement↑
    9: {"llm_self": 0.15, "rule_llm_agreement": 0.45, "rag_stdev": 0.10, "evidence_quality": 0.30},

    # === 카테고리 5: 설명력 및 전달력 ===
    # 설명의 명확성 — Dynamic Few-shot, evidence_quality + llm_self
    10: {"llm_self": 0.30, "rule_llm_agreement": 0.15, "rag_stdev": 0.25, "evidence_quality": 0.30},
    # 두괄식 답변 — 비교적 rule 가능 + LLM 맥락
    11: {"llm_self": 0.30, "rule_llm_agreement": 0.25, "rag_stdev": 0.20, "evidence_quality": 0.25},

    # === 카테고리 6: 적극성 ===
    12: {"llm_self": 0.30, "rule_llm_agreement": 0.15, "rag_stdev": 0.25, "evidence_quality": 0.30},
    13: {"llm_self": 0.30, "rule_llm_agreement": 0.15, "rag_stdev": 0.25, "evidence_quality": 0.30},
    14: {"llm_self": 0.30, "rule_llm_agreement": 0.15, "rag_stdev": 0.25, "evidence_quality": 0.30},

    # === 카테고리 7: 업무 정확도 ===
    # 정확한 안내 — 설계서 §8.1 예시: Evidence 품질 + Rule 일치도 가중↑
    15: {"llm_self": 0.15, "rule_llm_agreement": 0.35, "rag_stdev": 0.10, "evidence_quality": 0.40},
    # 필수 안내 이행 — Intent 분류 + 스크립트 매칭 (rule 강함)
    16: {"llm_self": 0.20, "rule_llm_agreement": 0.45, "rag_stdev": 0.10, "evidence_quality": 0.25},

    # === 카테고리 8: 개인정보 보호 ===
    # 정보 확인 절차 — compliance_based, rule 우선
    17: {"llm_self": 0.15, "rule_llm_agreement": 0.50, "rag_stdev": 0.05, "evidence_quality": 0.30},
    # 정보 보호 준수 — rule 패턴 탐지 + 강제 T3
    18: {"llm_self": 0.15, "rule_llm_agreement": 0.50, "rag_stdev": 0.05, "evidence_quality": 0.30},
}


_DEFAULT_UNIFORM: dict[str, float] = {k: 0.25 for k in SIGNAL_KEYS}


def _coerce_weight_block(raw: object) -> dict[str, float] | None:
    """tenant_config 에서 읽은 dict-like 블록을 {signal_key: float} 로 정규화.

    모든 SIGNAL_KEYS 가 존재해야 함. 타입 캐스팅 실패 / 누락 시 None.
    """
    if not isinstance(raw, dict):
        return None
    out: dict[str, float] = {}
    for k in SIGNAL_KEYS:
        if k not in raw:
            return None
        try:
            out[k] = float(raw[k])
        except (TypeError, ValueError):
            return None
    return out


def get_weights(item_number: int, tenant_id: str | None = None) -> dict[str, float]:
    """항목 번호에 대응하는 가중치 dict 반환.

    우선순위:
      1. `tenant_id` 지정 시 tenant_config.confidence.item_weights["<item>"] 로드 시도.
      2. 본 모듈의 하드코드 ITEM_WEIGHTS fallback.
      3. 미등록 항목은 균등 (0.25 x 4).

    tenant_config 로드 실패 / 스키마 부적합은 조용히 fallback — 파이프라인 중단 금지.
    """
    if tenant_id:
        try:
            # 순환 import 회피 — 런타임 import
            from v2.routing.tenant_policy import load_tenant_policy

            policy = load_tenant_policy(tenant_id)
            item_weights = (policy.item_weights or {}).get(str(item_number))
            coerced = _coerce_weight_block(item_weights)
            if coerced is not None:
                return coerced
            if item_weights is not None:
                logger.debug(
                    "tenant[%s] item_weights['%s'] 스키마 부적합 — 하드코드 fallback",
                    tenant_id, item_number,
                )
        except Exception as exc:  # noqa: BLE001 — tenant 로드 실패 silent fallback
            logger.debug("tenant_policy 로드 실패(%s): %s — 하드코드 fallback", tenant_id, exc)

    if item_number in ITEM_WEIGHTS:
        return ITEM_WEIGHTS[item_number]
    return dict(_DEFAULT_UNIFORM)


def validate_weights() -> list[str]:
    """모든 항목 가중치 합이 1.0 (±ε) 인지 검증. 실패 시 에러 메시지 리스트."""
    errors: list[str] = []
    for item_number, weights in ITEM_WEIGHTS.items():
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-6:
            errors.append(f"item {item_number}: weights sum={total:.6f} != 1.0")
        missing = set(SIGNAL_KEYS) - set(weights.keys())
        if missing:
            errors.append(f"item {item_number}: missing keys {sorted(missing)}")
    return errors
