# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Skills layer -- deterministic pattern matching and rule-based scoring."""

from nodes.skills.constants import *  # noqa: F401,F403 — re-export all pattern constants
from nodes.skills.deduction_log import (
    build_deduction_log_from_evaluations,
    build_deduction_log_from_pairs,
)
from nodes.skills.error_results import build_llm_failure_result
from nodes.skills.node_context import NodeContext, build_user_message
from nodes.skills.pattern_matcher import (
    AGENT_MARKERS,
    CUSTOMER_MARKERS,
    MatchResult,
    PatternMatcher,
    detect_agent_patterns,
    detect_customer_patterns,
    is_agent,
    is_customer,
    match_any,
    parse_turns,
)
from nodes.skills.reconciler import ReconcileResult, reconcile, reconcile_evaluation, snap_score
from nodes.skills.scorer import Scorer, ScoreResult, CategoryResult, TotalResult

__all__ = [
    "PatternMatcher",
    "MatchResult",
    "Scorer",
    "ScoreResult",
    "CategoryResult",
    "TotalResult",
    "ReconcileResult",
    "reconcile",
    "reconcile_evaluation",
    "snap_score",
    "build_user_message",
    "detect_agent_patterns",
    "detect_customer_patterns",
    "build_deduction_log_from_evaluations",
    "build_deduction_log_from_pairs",
    "build_llm_failure_result",
    "NodeContext",
    "is_agent",
    "is_customer",
    "parse_turns",
    "match_any",
    "AGENT_MARKERS",
    "CUSTOMER_MARKERS",
]
