# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""KSQI (Korean Service Quality Index) 평가 그룹.

STT 전사 텍스트 기반 9개 항목 자동평가. Layer 2 barrier 직후 layer3 와 병렬로 fan-out.
기존 #1~#18 평가와 채점 체계 다름 (결함 1건 = 배점 전액 차감) → 별도 보고서로 산출.
"""

from .ksqi_rules import KSQI_RULES, KSQI_NODES
from .ksqi_orchestrator import ksqi_orchestrator_node, ksqi_barrier_node, route_ksqi_fanout
from .ksqi_report import ksqi_report_node
from .nodes_rule import (
    ksqi_acknowledgment_node,
    ksqi_greeting_close_node,
    ksqi_greeting_open_node,
    ksqi_terse_response_node,
)
from .nodes_llm import (
    ksqi_advanced_empathy_node,
    ksqi_basic_empathy_node,
    ksqi_easy_explain_node,
    ksqi_inquiry_grasp_node,
    ksqi_refusal_followup_node,
)


# 노드 이름 → 함수 매핑 (graph builder 에서 add_node 일괄 등록용)
KSQI_NODE_FUNCS: dict[str, callable] = {
    "ksqi_greeting_open": ksqi_greeting_open_node,        # #1 규칙
    "ksqi_terse_response": ksqi_terse_response_node,      # #2 규칙
    "ksqi_refusal_followup": ksqi_refusal_followup_node,  # #3 LLM (stub)
    "ksqi_easy_explain": ksqi_easy_explain_node,          # #4 LLM (stub)
    "ksqi_inquiry_grasp": ksqi_inquiry_grasp_node,        # #5 LLM (stub)
    "ksqi_greeting_close": ksqi_greeting_close_node,      # #6 규칙
    "ksqi_acknowledgment": ksqi_acknowledgment_node,      # #7 규칙
    "ksqi_basic_empathy": ksqi_basic_empathy_node,        # #8 hybrid (stub)
    "ksqi_advanced_empathy": ksqi_advanced_empathy_node,  # #9 LLM (stub)
}


__all__ = [
    "KSQI_RULES",
    "KSQI_NODES",
    "KSQI_NODE_FUNCS",
    "ksqi_orchestrator_node",
    "ksqi_barrier_node",
    "route_ksqi_fanout",
    "ksqi_report_node",
]
