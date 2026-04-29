# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""신한카드 부서특화 Sub Agent 모음 (Layer 2 동적 fan-out).

각 부서별 평가 노드는 단일 SubAgent 로 동작하며,
xlsx 대분류 = 노드, 평가항목 = 노드 내부 sub-items 구조.

frontend `lib/pipeline.ts::SHINHAN_DEPT_NODE_IDS` 와 정합 강제.
"""

from v2.agents.shinhan_dept.registry import (
    DEPT_NODE_REGISTRY,
    DEPT_NODES_BY_TEAM,
    get_dept_agent,
    get_dept_nodes_for_tenant,
)


__all__ = [
    "DEPT_NODE_REGISTRY",
    "DEPT_NODES_BY_TEAM",
    "get_dept_agent",
    "get_dept_nodes_for_tenant",
]
