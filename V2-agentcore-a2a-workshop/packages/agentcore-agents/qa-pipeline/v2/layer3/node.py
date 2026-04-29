# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 3 LangGraph 노드 wrapper.

`run_layer3(evaluations, preprocessing, accuracy_verdict, skip_phase_c_and_reporting)` 를
QAStateV2 → orchestrator 필드 업데이트로 감싼다.
"""

from __future__ import annotations

import logging
from typing import Any

from v2.layer3.orchestrator_v2 import run_layer3


logger = logging.getLogger(__name__)


def layer3_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 노드 함수. QAStateV2 → orchestrator 업데이트.

    Parameters
    ----------
    state : QAStateV2 (dict)
        최소: `evaluations`. 선택: `preprocessing`, `accuracy_verdict`, `plan`.

    Returns
    -------
    dict
        `orchestrator` 필드를 포함한 부분 dict.
        추가로 V1 compat mirror 로 `verification` / `score_validation` 일부 채움.
    """
    evaluations = state.get("evaluations", []) or []
    preprocessing = state.get("preprocessing")
    accuracy_verdict = state.get("accuracy_verdict")
    plan = state.get("plan") or {}
    skip_tail = bool(plan.get("skip_phase_c_and_reporting"))
    site_id = state.get("site_id") or state.get("tenant_id") or state.get("tenant")

    result = run_layer3(
        evaluations=evaluations,
        preprocessing=preprocessing,
        accuracy_verdict=accuracy_verdict,
        skip_phase_c_and_reporting=skip_tail,
        site_id=site_id,
    )

    logger.info(
        "layer3_node: raw=%s after=%s grade=%s tier=%s overrides=%s",
        result["final_score"]["raw_total"],
        result["final_score"]["after_overrides"],
        result["final_score"].get("grade"),
        result.get("routing_tier_hint"),
        result["overrides"]["applied"],
    )

    update: dict[str, Any] = {"orchestrator": result}

    # V1 compat mirror — report_generator / legacy consumer 가 참조
    update["verification"] = {
        "status": "success",
        "agent_id": "layer3-consistency",
        "verification": {
            "is_consistent": not result.get("consistency_has_critical", False),
            "needs_human_review": result.get("consistency_has_warning", False)
            or result.get("consistency_has_critical", False),
            "conflicts": [
                {
                    "type": f["code"],
                    "description": f.get("description", ""),
                    "agents": f.get("item_numbers", []),
                }
                for f in result.get("consistency_flags", [])
            ],
            "total_score": result["final_score"]["after_overrides"],
            "max_possible_score": result.get("max_possible", 100),
        },
    }
    update["score_validation"] = {
        "status": "success",
        "agent_id": "layer3-score-validation",
        "validation": {
            "passed": len(result.get("missing_items", [])) == 0,
            "missing_items": result.get("missing_items", []),
        },
    }

    return update
