# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Confidence 계산기 — 설계서 §8.1 (4 신호 가중 조합).

Sub Agent 응답 + Dev4 RAG 결과를 입력받아 항목별 final confidence(1~5) 를 산출.

입력 (Layer 4 진입 시점의 State):
 - QAStateV2.sub_agent_responses  : Sub Agent 응답 (llm_self_confidence, rule_llm_delta 포함)
 - QAStateV2.confidence_signals   : Dev4 RAG 기여 (rag_stdev, evidence_quality_rag, ...)
 - QAStateV2.evaluations          : 항목별 evidence 배열 (Evidence 품질 판정용)

출력: {item_number: ConfidenceBlock dict} — qa_output_v2.ConfidenceBlock 호환.

설계 주의:
 - `rag_stdev=None` (retrieve 실패) → Evidence 품질 저하로 penalty
 - `rule_llm_delta.has_rule_pre_verdict == False` → rule_llm_agreement 신호를 중립 (0.7) 로 간주
 - evaluation_mode 에 따라 일부 신호는 무시 (skipped/unevaluable → confidence=1 강제)
"""

from __future__ import annotations

import logging
from typing import Any

from v2.confidence.weights import SIGNAL_KEYS, get_weights
from v2.routing.tenant_policy import load_tenant_policy

logger = logging.getLogger(__name__)


# Evidence 품질 레이블 → 수치 (0~1)
_EVIDENCE_QUALITY_SCORE: dict[str, float] = {
    "high": 1.0,
    "medium": 0.6,
    "low": 0.3,
}


def _normalize_llm_self(llm_self: int | None) -> float:
    """1~5 → 0~1."""
    if llm_self is None:
        return 0.5  # 중립
    return max(0.0, min(1.0, (llm_self - 1) / 4.0))


def _normalize_rule_llm_agreement(delta: dict[str, Any] | None) -> float:
    """rule_llm_delta → 0~1.

    - Rule 1차 판정 없음 (has_rule_pre_verdict=False): 0.7 중립 상향.
    - agreement=True: 1.0
    - agreement=False: rule_score, llm_score 차이를 0~1 로 매핑 (차이 클수록 0 에 근접).
    """
    if delta is None:
        return 0.7
    if not delta.get("has_rule_pre_verdict"):
        return 0.7
    if delta.get("agreement"):
        return 1.0
    rule_score = delta.get("rule_score")
    llm_score = delta.get("llm_score")
    if rule_score is None or llm_score is None:
        return 0.3
    diff = abs(int(rule_score) - int(llm_score))
    # 5점 차이면 0, 0점 차이면 1
    return max(0.0, 1.0 - diff / 5.0)


def _normalize_rag_stdev(rag_stdev: float | None) -> float:
    """RAG 유사사례 점수 stdev → 0~1 (낮을수록 confidence 높음).

    - stdev=None: 0.4 (retrieve 실패 → 약한 페널티)
    - stdev=0.0: 1.0 (완벽 일치)
    - stdev=3.0 이상: 0.0 (완전 혼재)
    """
    if rag_stdev is None:
        return 0.4
    stdev = max(0.0, float(rag_stdev))
    return max(0.0, 1.0 - min(stdev, 3.0) / 3.0)


def _apply_sample_size_penalty(
    norm_rag: float,
    *,
    rag_sample_size: int | None,
    tenant_id: str,
) -> float:
    """소표본 RAG penalty — PL Q5 2026-04-20 외부화.

    sample_size 가 tenant_config.confidence.rag_min_sample_size 미만이면
    rag_stdev 신호의 가중치를 하향 (중립값 0.5 와의 가중평균).

    - sample_size=None 또는 ≥ min → 원값 그대로
    - sample_size < min → weight 만큼만 원값 반영, 나머지는 0.5 중립

    공식: result = weight * norm_rag + (1 - weight) * 0.5
    weight=1.0 이면 무영향, weight=0.5 면 절반씩, weight=0.0 이면 완전 무시.
    """
    if rag_sample_size is None:
        return norm_rag
    policy = load_tenant_policy(tenant_id).confidence
    if rag_sample_size >= policy.rag_min_sample_size:
        return norm_rag
    w = max(0.0, min(1.0, policy.rag_small_sample_weight))
    return w * norm_rag + (1.0 - w) * 0.5


def _normalize_evidence_quality(quality: str | None) -> float:
    """Evidence 품질 → 0~1."""
    if quality is None:
        return 0.5
    return _EVIDENCE_QUALITY_SCORE.get(quality.lower(), 0.5)


def _composite_to_tier(composite: float) -> int:
    """0~1 composite → 1~5 final (5구간 bucketing).

    - >= 0.85 → 5
    - >= 0.70 → 4
    - >= 0.50 → 3
    - >= 0.30 → 2
    - < 0.30  → 1
    """
    if composite >= 0.85:
        return 5
    if composite >= 0.70:
        return 4
    if composite >= 0.50:
        return 3
    if composite >= 0.30:
        return 2
    return 1


def compute_item_confidence(
    item_number: int,
    *,
    evaluation_mode: str,
    llm_self_confidence_score: int | None,
    rule_llm_delta: dict[str, Any] | None,
    rag_stdev: float | None,
    evidence_quality_rag: str | None,
    evidence_count: int,
    rag_sample_size: int | None = None,
    tenant_id: str = "generic",
) -> dict[str, Any]:
    """항목 1건의 Confidence 를 계산해 qa_output_v2.ConfidenceBlock 호환 dict 반환.

    Parameters
    ----------
    rag_sample_size : Dev4 ReasoningResult.sample_size. None 이면 penalty 미적용 (하위 호환).
        PL Q5 2026-04-20: min sample 미만이면 rag_stdev 신호를 중립값 쪽으로 약화.
    tenant_id       : tenant_config 로드 키. 기본 "generic".

    Returns
    -------
    {
        "final": int (1~5),
        "signals": {
            "llm_self": int (1~5),
            "rule_llm_agreement": bool,
            "rag_stdev": float | None,
            "evidence_quality": "high"|"medium"|"low",
            "weighted_composite": float (0~5),
            "rag_sample_size": int | None,
            "rag_small_sample_penalty_applied": bool,
        }
    }
    """
    # 1) 특수 모드: skipped / unevaluable
    if evaluation_mode in ("skipped", "unevaluable"):
        # skipped=만점 고정 (말겹침 등) → final=5
        # unevaluable=평가 불가 → final=1 (강제 T3 로 하류 처리)
        final = 5 if evaluation_mode == "skipped" else 1
        return {
            "final": final,
            "signals": {
                "llm_self": llm_self_confidence_score or (5 if final == 5 else 1),
                "rule_llm_agreement": True,
                "rag_stdev": rag_stdev,
                "evidence_quality": evidence_quality_rag or ("high" if final == 5 else "low"),
                "weighted_composite": float(final),
                "rag_sample_size": rag_sample_size,
                "rag_small_sample_penalty_applied": False,
            },
        }

    weights = get_weights(item_number, tenant_id=tenant_id)

    norm_llm = _normalize_llm_self(llm_self_confidence_score)
    norm_rule = _normalize_rule_llm_agreement(rule_llm_delta)
    norm_rag_raw = _normalize_rag_stdev(rag_stdev)
    norm_rag = _apply_sample_size_penalty(
        norm_rag_raw, rag_sample_size=rag_sample_size, tenant_id=tenant_id,
    )
    penalty_applied = (
        rag_sample_size is not None
        and rag_sample_size < load_tenant_policy(tenant_id).confidence.rag_min_sample_size
    )

    # Evidence 품질: RAG 기여 + 실제 evidence 개수(원칙 3) 보강
    base_quality = _normalize_evidence_quality(evidence_quality_rag)
    if evidence_count == 0:
        base_quality = min(base_quality, 0.2)  # 근거 0건이면 강력 페널티
    elif evidence_count >= 2:
        base_quality = min(1.0, base_quality + 0.1)
    quality_label = (
        "high" if base_quality >= 0.7 else "medium" if base_quality >= 0.4 else "low"
    )

    composite_0_1 = (
        weights["llm_self"] * norm_llm
        + weights["rule_llm_agreement"] * norm_rule
        + weights["rag_stdev"] * norm_rag
        + weights["evidence_quality"] * base_quality
    )
    composite_0_5 = round(composite_0_1 * 5.0, 3)
    final = _composite_to_tier(composite_0_1)

    return {
        "final": final,
        "signals": {
            "llm_self": llm_self_confidence_score if llm_self_confidence_score is not None else 3,
            "rule_llm_agreement": bool(rule_llm_delta and rule_llm_delta.get("agreement", False)),
            "rag_stdev": rag_stdev,
            "evidence_quality": quality_label,
            "weighted_composite": composite_0_5,
            "rag_sample_size": rag_sample_size,
            "rag_small_sample_penalty_applied": penalty_applied,
        },
    }
