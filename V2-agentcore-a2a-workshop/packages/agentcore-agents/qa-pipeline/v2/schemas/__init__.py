# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
V2 QA Pipeline 스키마 (Phase A1 산출물).

import 예시:
    from v2.schemas.enums import EvaluationMode, RoutingTier, CATEGORY_META, FORCE_T3_ITEMS
    from v2.schemas.sub_agent_io import SubAgentResponse, ItemVerdict, EvidenceQuote
    from v2.schemas.qa_output_v2 import QAOutputV2, ItemResult, CategoryBlock
    from v2.schemas.state_v2 import QAStateV2
"""

from v2.schemas.enums import (
    CATEGORY_META,
    FORCE_T3_ITEMS,
    GRADE_BOUNDARIES,
    GRADE_BOUNDARY_MARGIN,
    CategoryKey,
    EvaluationMode,
    HITLDriver,
    MaskingVersion,
    OverrideAction,
    OverrideTrigger,
    PIICategory,
    RoutingTier,
    SubAgentStatus,
)
from v2.schemas.sub_agent_io import (
    DeductionEntry,
    EvidenceQuote,
    ItemVerdict,
    LLMSelfConfidence,
    RuleLLMDelta,
    SubAgentResponse,
)
from v2.schemas.state_v2 import QAStateV2

__all__ = [
    # enums
    "EvaluationMode",
    "RoutingTier",
    "HITLDriver",
    "MaskingVersion",
    "PIICategory",
    "OverrideAction",
    "OverrideTrigger",
    "SubAgentStatus",
    "CategoryKey",
    "CATEGORY_META",
    "FORCE_T3_ITEMS",
    "GRADE_BOUNDARIES",
    "GRADE_BOUNDARY_MARGIN",
    # sub agent io
    "SubAgentResponse",
    "ItemVerdict",
    "EvidenceQuote",
    "DeductionEntry",
    "LLMSelfConfidence",
    "RuleLLMDelta",
    # state
    "QAStateV2",
]
