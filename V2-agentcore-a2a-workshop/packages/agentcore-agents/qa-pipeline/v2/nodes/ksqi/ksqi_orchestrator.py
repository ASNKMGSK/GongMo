# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""KSQI 그룹 fan-out / barrier 노드.

`ksqi_orchestrator_node` 는 사실상 no-op (trace 용). 그 직후 conditional edge 가
`Send` 로 9개 항목 노드로 fan-out. 9개 노드가 `ksqi_evaluations` (operator.add)
에 결과 append → `ksqi_barrier_node` 가 수렴 + 리포트 노드로 인계.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import Send

from .ksqi_rules import KSQI_NODES

logger = logging.getLogger(__name__)


def ksqi_orchestrator_node(state: dict[str, Any]) -> dict[str, Any]:
    """KSQI fan-out 직전 진입점 — trace/log 용 no-op."""
    logger.info("ksqi_orchestrator: dispatching %d KSQI nodes in parallel", len(KSQI_NODES))
    return {}


def route_ksqi_fanout(state: dict[str, Any]) -> list[Send]:
    """9개 KSQI 노드로 Send fan-out."""
    return [Send(name, state) for name in KSQI_NODES]


def ksqi_barrier_node(state: dict[str, Any]) -> dict[str, Any]:
    """KSQI 9개 노드 수렴 — operator.add 로 ksqi_evaluations 가 채워졌음을 가정."""
    evals = state.get("ksqi_evaluations") or []
    logger.info("ksqi_barrier: collected %d KSQI evaluations", len(evals))
    return {}
