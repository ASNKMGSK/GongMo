# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""GAP-G2 (cascade deduction blocking) + GAP-G3 (unfriendly relocation) verifier tests.

Direct unit tests against `_block_cascade_deductions`, `_check_unfriendly`,
`_build_unfriendly_verdict`, `_normalize_turn_ids` in consistency_check.py.

These tests are fixture-driven — no LLM calls, no network, no AWS.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

QA_PIPELINE_ROOT = Path(
    "C:/Users/META M/Desktop/업무/qa/agentcore-a2a-workshop/packages/agentcore-agents/qa-pipeline"
)
if str(QA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(QA_PIPELINE_ROOT))


@pytest.fixture(scope="module")
def cc_module():
    from nodes import consistency_check
    return consistency_check


# ===========================================================================
# G2: _normalize_turn_ids
# ===========================================================================


def test_normalize_turn_ids_simple_string(cc_module):
    assert cc_module._normalize_turn_ids("turn_42") == {"42"}
    assert cc_module._normalize_turn_ids("42") == {"42"}


def test_normalize_turn_ids_comma_list(cc_module):
    assert cc_module._normalize_turn_ids("turn_1, turn_2, turn_3") == {"1", "2", "3"}


def test_normalize_turn_ids_range_to(cc_module):
    assert cc_module._normalize_turn_ids("turn_1_to_3") == {"1", "2", "3"}


def test_normalize_turn_ids_range_tilde(cc_module):
    assert cc_module._normalize_turn_ids("turn_42~44") == {"42", "43", "44"}


def test_normalize_turn_ids_list_input(cc_module):
    assert cc_module._normalize_turn_ids(["turn_1", "turn_2"]) == {"1", "2"}


def test_normalize_turn_ids_empty(cc_module):
    assert cc_module._normalize_turn_ids(None) == set()
    assert cc_module._normalize_turn_ids("") == set()


# ===========================================================================
# G2: _block_cascade_deductions
# ===========================================================================


def _make_eval(agent_id: str, score: int, max_score: int, deductions: list[dict]) -> dict:
    return {
        "agent_id": agent_id,
        "status": "success",
        "evaluation": {
            "score": score,
            "max_score": max_score,
            "item_number": {"work-accuracy-agent": 15, "scope-agent": 10, "proactiveness-agent": 12}.get(agent_id, 0),
            "deductions": deductions,
            "confidence": 0.9,
        },
    }


def test_g2_blocks_cascade_when_overlap(cc_module):
    """work_accuracy=0 + scope cascade with evidence overlap → restored."""
    evaluations = [
        _make_eval(
            "work-accuracy-agent", 0, 15,
            [{"reason": "오안내 발생", "points": 15, "evidence_ref": "turn_42"}],
        ),
        _make_eval(
            "scope-agent", 5, 15,
            [{"reason": "오안내로 인한 부정확 안내", "points": 10, "evidence_ref": "turn_42"}],
        ),
    ]
    result = cc_module._block_cascade_deductions(evaluations)
    assert result["applied"] is True
    assert len(result["blocked_deductions"]) == 1
    # scope score restored 5 → 15
    scope_eval = evaluations[1]["evaluation"]
    assert scope_eval["score"] == 15
    assert scope_eval["deductions"][0]["points"] == 0
    assert scope_eval["deductions"][0]["_original_points"] == 10


def test_g2_no_block_when_no_overlap(cc_module):
    """work_accuracy=0 but scope deduction on different turns → not blocked."""
    evaluations = [
        _make_eval(
            "work-accuracy-agent", 0, 15,
            [{"reason": "오안내 발생", "points": 15, "evidence_ref": "turn_42"}],
        ),
        _make_eval(
            "scope-agent", 5, 15,
            [{"reason": "잘못된 안내", "points": 10, "evidence_ref": "turn_99"}],
        ),
    ]
    result = cc_module._block_cascade_deductions(evaluations)
    assert result["applied"] is False
    assert evaluations[1]["evaluation"]["score"] == 5


def test_g2_no_block_when_wa_not_zero(cc_module):
    evaluations = [
        _make_eval(
            "work-accuracy-agent", 10, 15,
            [{"reason": "오안내 발생", "points": 5, "evidence_ref": "turn_42"}],
        ),
        _make_eval(
            "scope-agent", 5, 15,
            [{"reason": "오안내로 인한 부정확 안내", "points": 10, "evidence_ref": "turn_42"}],
        ),
    ]
    result = cc_module._block_cascade_deductions(evaluations)
    assert result["applied"] is False


def test_g2_no_block_when_no_cascade_keyword(cc_module):
    """scope deduction reason has no cascade keyword (오안내/부정확/etc.) → not blocked."""
    evaluations = [
        _make_eval(
            "work-accuracy-agent", 0, 15,
            [{"reason": "오안내 발생", "points": 15, "evidence_ref": "turn_42"}],
        ),
        _make_eval(
            "scope-agent", 5, 15,
            [{"reason": "설명이 너무 짧음", "points": 10, "evidence_ref": "turn_42"}],
        ),
    ]
    result = cc_module._block_cascade_deductions(evaluations)
    assert result["applied"] is False


def test_g2_blocks_proactiveness_too(cc_module):
    evaluations = [
        _make_eval(
            "work-accuracy-agent", 0, 15,
            [{"reason": "오안내 발생", "points": 15, "evidence_ref": "turn_42, turn_43"}],
        ),
        _make_eval(
            "proactiveness-agent", 5, 15,
            [{"reason": "오안내 후 능동 안내 부족", "points": 10, "evidence_ref": "turn_43"}],
        ),
    ]
    result = cc_module._block_cascade_deductions(evaluations)
    assert result["applied"] is True
    assert evaluations[1]["evaluation"]["score"] == 15


# ===========================================================================
# G3: _check_unfriendly
# ===========================================================================


def test_g3_unfriendly_via_deductions(cc_module):
    """2+ rudeness keyword hits in deductions → True (uses hints from _RUDENESS_HINTS)."""
    evaluations = [
        {
            "agent_id": "courtesy-agent",
            "status": "success",
            "evaluation": {
                "score": 0,
                "max_score": 5,
                "deductions": [
                    {"reason": "고압적 발언 (불친절)", "points": 5},
                    {"reason": "고객과 언쟁 발생", "points": 5},
                ],
                "confidence": 0.9,
            },
        },
    ]
    assert cc_module._check_unfriendly(evaluations, None) is True


def test_g3_unfriendly_single_hint_not_enough(cc_module):
    """단일 hint 만 있으면 False (>=2 필요)."""
    evaluations = [
        {
            "agent_id": "courtesy-agent",
            "status": "success",
            "evaluation": {
                "score": 3,
                "max_score": 5,
                "deductions": [{"reason": "불친절 약간 감지", "points": 2}],
                "confidence": 0.9,
            },
        },
    ]
    assert cc_module._check_unfriendly(evaluations, None) is False


def test_g3_unfriendly_via_dialogue_text(cc_module):
    """parsed_dialogue 의 상담사 발화 직접 매칭 → True."""
    evaluations = []
    parsed = {
        "courtesy": {
            "text": "정상 발화",
            "turns": [
                {"speaker": "agent", "text": "씨발 무슨 말이에요"},
            ],
        }
    }
    assert cc_module._check_unfriendly(evaluations, parsed) is True


def test_g3_no_unfriendly_clean_dialogue(cc_module):
    evaluations = []
    parsed = {
        "courtesy": {
            "text": "안녕하세요 고객님, 무엇을 도와드릴까요",
            "turns": [{"speaker": "agent", "text": "네 알겠습니다"}],
        }
    }
    assert cc_module._check_unfriendly(evaluations, parsed) is False


# ===========================================================================
# G3: _build_unfriendly_verdict
# ===========================================================================


def test_g3_build_unfriendly_verdict_zeros_all(cc_module):
    evaluations = [
        _make_eval("greeting-agent", 4, 5, [{"reason": "x", "points": 1}]),
        _make_eval("courtesy-agent", 2, 5, [{"reason": "y", "points": 3}]),
    ]
    verdict = cc_module._build_unfriendly_verdict(evaluations, total_score=0, max_possible=10)
    inner = verdict["verification"]["verification"]
    assert inner["unfriendly"] is True
    assert inner["common_penalties"]["rudeness_zero"] is True
    assert inner["total_score"] == 0
    assert inner["score_percentage"] == 0.0
    # mutate side-effect: all evaluation scores → 0
    for e in evaluations:
        assert e["evaluation"]["score"] == 0
