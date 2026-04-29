# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""KSQI LLM 기반 노드 5개 — 다음 단계에서 실제 LLM 통합.

#3 거부 후 재안내    (5점)  — 거절 의도 인식 + 후속 발화 검사
#4 쉬운 설명         (10점) — 논리 / 일관성 / 완결성 평가
#5 문의내용 파악도   (10점) — 재진술 패턴 + 복합질의 누락 검출
#8 단순 공감 표현    (10점) — 패턴 매칭 + 공감 적절성 (hybrid)
#9 고차원 공감 표현  (10점) — 상황 인식 (불만/위로/경조사/양해) + 공감 매칭

현재는 "정상 (결함 없음)" 으로 처리하는 placeholder. 실제 LLM 평가는 다음 단계에서
prompts/ksqi/item_*.md 와 함께 통합 예정. 단, ksqi_evaluations append 는 정상 작동.
"""

from __future__ import annotations

import logging
from typing import Any

from .nodes_rule import _make_eval

logger = logging.getLogger(__name__)


def _stub_llm(item_number: int, msg: str = "LLM 미통합 — placeholder 정상 처리") -> dict[str, Any]:
    """LLM 평가 미통합 시 임시 결과 (defect=False)."""
    return {"ksqi_evaluations": [_make_eval(item_number, defect=False, rationale=msg)]}


def ksqi_refusal_followup_node(state: dict[str, Any]) -> dict[str, Any]:
    return _stub_llm(3)


def ksqi_easy_explain_node(state: dict[str, Any]) -> dict[str, Any]:
    return _stub_llm(4)


def ksqi_inquiry_grasp_node(state: dict[str, Any]) -> dict[str, Any]:
    return _stub_llm(5)


def ksqi_basic_empathy_node(state: dict[str, Any]) -> dict[str, Any]:
    return _stub_llm(8)


def ksqi_advanced_empathy_node(state: dict[str, Any]) -> dict[str, Any]:
    return _stub_llm(9)
