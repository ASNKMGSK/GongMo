# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""LangGraph node registry for the multi-tenant QA evaluation pipeline.

NODE_REGISTRY maps node name → callable. graph.build_graph() iterates over
this registry when wiring the StateGraph. Adding a new node means:
  1) Implement it in nodes/<name>.py accepting `(state, ctx)` or `(state,)`.
  2) Import and register here.
  3) Update graph.py `_EXTRA_FIELDS_PER_NODE` if the node reads extra state.
  4) Update orchestrator.py execution plan if the node runs in a new phase.

Dev3 — all nodes already consume `state["tenant"]` via NodeContext or direct
access. `retrieval` is intentionally omitted (비활성화 — Gateway RAG 미사용).
"""

from nodes.consistency_check import consistency_check_node
from nodes.courtesy import courtesy_node
from nodes.dialogue_parser import dialogue_parser_node
from nodes.greeting import greeting_node
from nodes.incorrect_check import incorrect_check_node
from nodes.mandatory import mandatory_node
from nodes.orchestrator import orchestrator_node
from nodes.proactiveness import proactiveness_node
from nodes.report_generator import report_generator_node
from nodes.scope import scope_node
from nodes.score_validation import score_validation_node
from nodes.understanding import understanding_node
from nodes.work_accuracy import work_accuracy_node


# ---------------------------------------------------------------------------
# 노드 레지스트리 — graph.build_graph() 가 사용
# ---------------------------------------------------------------------------

NODE_REGISTRY: dict = {
    "dialogue_parser": dialogue_parser_node,
    "greeting": greeting_node,
    "understanding": understanding_node,
    "courtesy": courtesy_node,
    "mandatory": mandatory_node,
    "scope": scope_node,
    "proactiveness": proactiveness_node,
    "work_accuracy": work_accuracy_node,
    "incorrect_check": incorrect_check_node,
    "consistency_check": consistency_check_node,
    "score_validation": score_validation_node,
    "report_generator": report_generator_node,
}


__all__ = [
    "NODE_REGISTRY",
    "dialogue_parser_node",
    "orchestrator_node",
    "greeting_node",
    "understanding_node",
    "courtesy_node",
    "mandatory_node",
    "scope_node",
    "proactiveness_node",
    "work_accuracy_node",
    "incorrect_check_node",
    "consistency_check_node",
    "score_validation_node",
    "report_generator_node",
]
