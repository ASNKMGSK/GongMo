# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 1 orchestrator — 5개 서브 모듈을 순차 호출해 preprocessing dict 생성.

설계서 p9 Layer 1 순서 엄격:
    (a) quality_gate → (b) segment_splitter → (c) pii_normalizer →
    (d) deduction_trigger_detector → (e) rule_pre_verdictor

Short-circuit 규칙:
    - (a) quality.unevaluable=True 이면 (b)~(e) 스킵하고 빈 preprocessing 반환.
      Orchestrator 가 이를 보고 Layer 2/3 전체 스킵 후 Layer 4 T3 라우팅 처리.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from v2.contracts.preprocessing import (
    Preprocessing,
    empty_deduction_triggers,
)
from v2.layer1.deduction_trigger_detector import detect_triggers
from v2.layer1.pii_normalizer import normalize_pii
from v2.layer1.quality_gate import quality_gate_check
from v2.layer1.rule_pre_verdictor import build_rule_pre_verdicts
from v2.layer1.segment_splitter import split_sections


logger = logging.getLogger(__name__)


def run_layer1(
    transcript: str,
    stt_metadata: dict[str, Any] | None = None,
) -> Preprocessing:
    """Layer 1 파이프라인 실행.

    Parameters
    ----------
    transcript : str
        원본 STT 전사록.
    stt_metadata : dict | None
        STT 품질 메타데이터 (quality_gate 소비).

    Returns
    -------
    Preprocessing
        QAStateV2.preprocessing 필드에 저장되는 dict. PL 확정 스펙.
    """
    diagnostics: list[dict[str, Any]] = []

    # (a) 품질 검증
    t0 = time.perf_counter()
    quality = quality_gate_check(stt_metadata)
    diagnostics.append(_diag("quality_gate", t0, "ok"))

    # unevaluable 이면 short-circuit
    if quality.get("unevaluable"):
        logger.warning("run_layer1: unevaluable — Layer 2/3 스킵 (T3 라우팅)")
        return _short_circuit_preprocessing(quality, diagnostics)

    # (b) 구간 분리
    t0 = time.perf_counter()
    segments_out = split_sections(transcript)
    diagnostics.append(_diag("segment_splitter", t0, "ok"))

    turns: list[dict[str, Any]] = segments_out["turns"]
    turn_pairs: list[dict[str, Any]] = segments_out["turn_pairs"]

    # (c) PII 정규화
    t0 = time.perf_counter()
    pii_out = normalize_pii(
        transcript,
        turns=turns,
        declared_version=quality.get("masking_version"),
    )
    diagnostics.append(_diag("pii_normalizer", t0, "ok"))

    # quality.masking_version 을 pii_normalizer 자동 감지 결과로 갱신 (일관성)
    detected_version = pii_out["masking_format_version"]
    if detected_version != quality.get("masking_version"):
        logger.info(
            "run_layer1: masking_version updated %s → %s (auto-detected)",
            quality.get("masking_version"), detected_version,
        )
        quality = {**quality, "masking_version": detected_version}

    # (d) 감점 트리거 탐지
    t0 = time.perf_counter()
    triggers_out = detect_triggers(turns)
    diagnostics.append(_diag("deduction_trigger_detector", t0, "ok"))

    # (e) Rule 1차 판정
    t0 = time.perf_counter()
    verdicts_out = build_rule_pre_verdicts(turns, transcript, turn_pairs)
    diagnostics.append(_diag("rule_pre_verdictor", t0, "ok"))

    preprocessing: Preprocessing = {
        # (a)
        "quality": quality,
        # (b)
        "detected_sections": segments_out["detected_sections"],
        "detected_sections_meta": segments_out["detected_sections_meta"],
        # (b-추가) 파싱된 턴 배열 — HITL 검수 UI 에서 항목별 파싱 원문 표시에 사용.
        # 스키마: [{turn_id(int, 1-based), speaker(str), text(str), segment(str)}, ...].
        # 프론트 components/ReviewItemCard turns prop 으로 그대로 전달됨.
        "turns": turns,
        # (c)
        "pii_tokens": pii_out["pii_tokens"],
        "canonical_transcript": pii_out["canonical_transcript"],
        "masking_format_version": pii_out["masking_format_version"],
        # (d)
        "deduction_triggers": triggers_out["deduction_triggers"],
        "deduction_trigger_details": triggers_out["deduction_trigger_details"],
        # Dev5 overrides_adapter.build_overrides_block() 입력 (hoisted from triggers_out)
        "has_all_zero_trigger": triggers_out.get("has_all_zero_trigger", False),
        "has_category_zero_categories": triggers_out.get("has_category_zero_categories", []),
        "recommended_override": triggers_out.get("recommended_override", "none"),
        # (e)
        "rule_pre_verdicts": verdicts_out["rule_pre_verdicts"],
        "intent_type": verdicts_out["intent_type"],
        "intent_type_primary": verdicts_out.get("intent_type_primary", verdicts_out["intent_type"]),
        "intent_detail": verdicts_out["intent_detail"],
        "iv_evidence": verdicts_out["iv_evidence"],
        # V1 호환
        "agent_turn_assignments": segments_out["agent_turn_assignments"],
        # diagnostics
        "layer1_diagnostics": diagnostics,
    }

    logger.info(
        "run_layer1: done — turns=%d intent=%s pii=%d triggers(불친절=%s,개인정보=%s)",
        len(turns),
        verdicts_out["intent_type"],
        pii_out["total_pii_count"],
        triggers_out["deduction_triggers"]["불친절"],
        triggers_out["deduction_triggers"]["개인정보_유출"],
    )

    return preprocessing


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _diag(module: str, started: float, status: str) -> dict[str, Any]:
    return {
        "module": module,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "status": status,
    }


def _short_circuit_preprocessing(
    quality: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> Preprocessing:
    """unevaluable 시 하류 필드를 빈 기본값으로 채운 preprocessing 반환."""
    return {
        "quality": quality,  # type: ignore[typeddict-item]
        "detected_sections": {"opening": [0, 0], "body": [0, 0], "closing": [0, 0]},
        "detected_sections_meta": {
            "agent_turn_ids": [],
            "customer_turn_ids": [],
            "turn_pairs": [],
        },
        "turns": [],
        "pii_tokens": [],
        "canonical_transcript": "",
        "masking_format_version": quality.get("masking_version", "v1_symbolic"),  # type: ignore[typeddict-item]
        "deduction_triggers": empty_deduction_triggers(),
        "deduction_trigger_details": [],
        "has_all_zero_trigger": False,
        "has_category_zero_categories": [],
        "recommended_override": "none",
        "rule_pre_verdicts": {},
        "intent_type": "일반문의",
        "intent_type_primary": "일반문의",
        "intent_detail": {
            "primary_intent": "일반문의",
            "sub_intents": [],
            "product": "",
            "complexity": "simple",
            "tenant_topic_ref": None,
        },
        "iv_evidence": {
            "iv_procedure_turns": [],
            "preemptive_turns": [],
            "third_party_turns": [],
        },
        "agent_turn_assignments": {},
        "layer1_diagnostics": diagnostics,
    }
