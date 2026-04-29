# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 1 파이프라인 smoke tests.

확정 스펙 검증 포인트:
  1. run_layer1 출력 키 = PL 확정 preprocessing 스키마
  2. snap_score_v2(17, 3) == 3  — iter05 회귀 해소 검증
  3. V1 qa_rules.py 불변 — V1 snap_score(17, 3) == 0 여전히 유효
  4. quality.unevaluable=True 시 short-circuit
  5. v1_symbolic + v2_categorical 양 경로 자동 감지
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


# qa-pipeline 루트를 path 에 (v2 및 nodes_v1 import)
_QA_PIPELINE_ROOT = Path(__file__).resolve().parents[2]
if str(_QA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_QA_PIPELINE_ROOT))


# ---------------------------------------------------------------------------
# 테스트용 고정 전사록 — 도입/본론/종결 3구간이 명확한 정상 샘플
# ---------------------------------------------------------------------------

NORMAL_TRANSCRIPT = """상담사: 안녕하세요 코오롱 고객센터 김상담입니다.
고객: 네 안녕하세요 주문 취소하고 싶어요.
상담사: 네 고객님 성함이 어떻게 되시나요?
고객: 홍길동입니다
상담사: 네 확인되셨습니다 취소 접수 도와드리겠습니다
고객: 감사합니다
상담사: 추가 문의사항 있으실까요?
고객: 없어요
상담사: 네 좋은 하루 되세요 김상담이였습니다"""


# ---------------------------------------------------------------------------
# snap_score_v2 검증
# ---------------------------------------------------------------------------


def test_allowed_steps_v2_includes_17_3():
    from v2.contracts.rubric import ALLOWED_STEPS

    assert ALLOWED_STEPS[17] == [5, 3, 0]
    assert ALLOWED_STEPS[18] == [5, 3, 0]


def test_snap_score_v2_preserves_3_for_item17():
    """iter05 회귀의 핵심 — 3이 0으로 강제 변환되지 않아야."""
    from v2.contracts.rubric import snap_score_v2

    assert snap_score_v2(17, 3) == 3
    assert snap_score_v2(18, 3) == 3


def test_snap_score_v2_floors_intermediate_values():
    """허용 단계에 없는 값은 '이하 방향' 최대로 snap."""
    from v2.contracts.rubric import snap_score_v2

    assert snap_score_v2(17, 4) == 3   # 4 이하 최대 = 3
    assert snap_score_v2(10, 6) == 5   # #10 = [10,7,5,0] → 5
    assert snap_score_v2(10, 8) == 7
    assert snap_score_v2(3, 2) == 5    # skipped 만점 고정


def test_v2_total_max_unchanged_at_100():
    from v2.contracts.rubric import V2_MAX_TOTAL_SCORE

    assert V2_MAX_TOTAL_SCORE == 100


def test_v1_snap_score_still_zero_for_17_3():
    """V1 qa_rules.py 가 불변임을 보장 — V1 snap_score 는 여전히 [5,0] 기준."""
    from nodes.skills.reconciler import snap_score

    # V1 은 3 → 0 강제 변환 (변경 없음 — V1 보존)
    assert snap_score(17, 3) == 0
    assert snap_score(18, 3) == 0


# ---------------------------------------------------------------------------
# run_layer1 전체 파이프라인
# ---------------------------------------------------------------------------


def test_run_layer1_returns_required_keys():
    from v2.layer1 import run_layer1

    result = run_layer1(
        NORMAL_TRANSCRIPT,
        stt_metadata={
            "transcription_confidence": 0.95,
            "speaker_diarization_success": True,
            "duration_sec": 120,
        },
    )

    required_keys = {
        "quality",
        "detected_sections",
        "detected_sections_meta",
        "pii_tokens",
        "canonical_transcript",
        "masking_format_version",
        "deduction_triggers",
        "deduction_trigger_details",
        "rule_pre_verdicts",
        "intent_type",
        "intent_detail",
        "iv_evidence",
        "agent_turn_assignments",
        "layer1_diagnostics",
    }
    assert required_keys.issubset(set(result.keys())), (
        f"missing keys: {required_keys - set(result.keys())}"
    )


def test_run_layer1_deduction_triggers_bool_dict():
    from v2.layer1 import run_layer1

    result = run_layer1(
        NORMAL_TRANSCRIPT,
        stt_metadata={
            "transcription_confidence": 0.95,
            "speaker_diarization_success": True,
            "duration_sec": 120,
        },
    )
    dt = result["deduction_triggers"]

    assert set(dt.keys()) == {"불친절", "개인정보_유출", "오안내_미정정"}
    assert dt == {"불친절": False, "개인정보_유출": False, "오안내_미정정": False}


def test_run_layer1_sections_are_index_pairs():
    from v2.layer1 import run_layer1

    result = run_layer1(
        NORMAL_TRANSCRIPT,
        stt_metadata={
            "transcription_confidence": 0.95,
            "speaker_diarization_success": True,
            "duration_sec": 120,
        },
    )
    sections = result["detected_sections"]

    for key in ("opening", "body", "closing"):
        pair = sections[key]
        assert isinstance(pair, list) and len(pair) == 2
        assert pair[0] <= pair[1]


def test_run_layer1_rule_pre_verdicts_zero_padded_keys():
    from v2.layer1 import run_layer1

    result = run_layer1(
        NORMAL_TRANSCRIPT,
        stt_metadata={
            "transcription_confidence": 0.95,
            "speaker_diarization_success": True,
            "duration_sec": 120,
        },
    )
    keys = set(result["rule_pre_verdicts"].keys())

    assert "item_01" in keys
    assert "item_17" in keys
    assert "item_18" in keys
    # 모든 키가 zero-padded 형식
    for k in keys:
        assert k.startswith("item_") and len(k) == 7
        num = int(k.split("_")[1])
        assert 1 <= num <= 18


def test_run_layer1_item17_score_in_allowed_steps_v2():
    from v2.contracts.rubric import ALLOWED_STEPS
    from v2.layer1 import run_layer1

    result = run_layer1(
        NORMAL_TRANSCRIPT,
        stt_metadata={
            "transcription_confidence": 0.95,
            "speaker_diarization_success": True,
            "duration_sec": 120,
        },
    )
    score = result["rule_pre_verdicts"]["item_17"]["score"]

    assert score in ALLOWED_STEPS[17]


# ---------------------------------------------------------------------------
# 품질 게이트 short-circuit
# ---------------------------------------------------------------------------


def test_quality_gate_unevaluable_triggers_short_circuit():
    from v2.layer1 import run_layer1

    result = run_layer1(
        NORMAL_TRANSCRIPT,
        stt_metadata={
            "transcription_confidence": 0.30,   # 낮은 신뢰도 → unevaluable
            "speaker_diarization_success": True,
            "duration_sec": 120,
        },
    )

    assert result["quality"]["unevaluable"] is True
    assert result["quality"]["tier_route_override"] == "T3"
    # 하류 필드는 빈 기본값
    assert result["rule_pre_verdicts"] == {}
    assert result["pii_tokens"] == []


# ---------------------------------------------------------------------------
# PII 자동 감지
# ---------------------------------------------------------------------------


def test_pii_normalizer_detects_v2_categorical():
    from v2.layer1.pii_normalizer import normalize_pii

    v2_transcript = (
        "상담사: 안녕하세요 [NAME] 고객님 맞으신가요?\n"
        "고객: 네 맞아요\n"
        "상담사: 전화번호 [PHONE] 맞으세요?"
    )
    out = normalize_pii(v2_transcript)

    assert out["masking_format_version"] == "v2_categorical"
    assert out["total_pii_count"] == 2
    cats = [t["inferred_category"] for t in out["pii_tokens"]]
    assert "NAME" in cats
    assert "PHONE" in cats


def test_pii_normalizer_detects_v1_symbolic_with_context_inference():
    from v2.layer1.pii_normalizer import normalize_pii

    v1_transcript = (
        "상담사: 성함이 *** 맞으신가요?\n"
        "고객: 네 맞아요\n"
        "상담사: 전화번호가 *** 으로 되어있네요"
    )
    out = normalize_pii(v1_transcript)

    assert out["masking_format_version"] == "v1_symbolic"
    assert out["total_pii_count"] == 2
    # context heuristic 으로 NAME / PHONE 추정
    cats = {t["inferred_category"] for t in out["pii_tokens"]}
    assert "NAME" in cats
    assert "PHONE" in cats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
