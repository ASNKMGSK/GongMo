# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 1 LangGraph 노드 wrapper.

`run_layer1(transcript, stt_metadata)` 를 QAStateV2 → dict update 로 감싸
LangGraph StateGraph 에 등록 가능하게 한다.

출력 필드:
  preprocessing       — Layer 1 종합 산출물 (Dev5 `v2/schemas/qa_output_v2.py::PreprocessingBlock` 과 정합)
  parsed_dialogue     — V1 호환 (Sub Agent 가 V1 패턴으로 접근 시 사용)
  agent_turn_assignments — V1 호환 (Sub Agent 가 카테고리별 턴 범위 조회)
"""

from __future__ import annotations

import logging
from typing import Any

from v2.layer1.run_layer1 import run_layer1


logger = logging.getLogger(__name__)


def layer1_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 노드 함수. QAStateV2 → preprocessing 업데이트.

    Parameters
    ----------
    state : QAStateV2 (dict)
        최소 필드: `transcript`. 선택: `stt_metadata`.

    Returns
    -------
    dict
        state 에 머지될 부분 dict. 아래 키를 채운다:
        - preprocessing
        - parsed_dialogue (V1 compat mirror)
        - agent_turn_assignments (V1 compat mirror)
    """
    transcript = state.get("transcript", "")
    stt_metadata = state.get("stt_metadata")
    preprocessing = run_layer1(transcript=transcript, stt_metadata=stt_metadata)

    # V1 compat mirror — Sub Agent 가 기존 패턴으로 접근 가능하도록
    segments_meta = preprocessing.get("detected_sections_meta", {})
    parsed_dialogue = {
        "segments": preprocessing.get("detected_sections", {}),
        "turn_pairs": segments_meta.get("turn_pairs", []),
        "agent_turns": segments_meta.get("agent_turn_ids", []),
        "customer_turns": segments_meta.get("customer_turn_ids", []),
    }

    logger.info(
        "layer1_node: intent=%s triggers=%s unevaluable=%s",
        preprocessing.get("intent_type"),
        preprocessing.get("deduction_triggers"),
        preprocessing.get("quality", {}).get("unevaluable"),
    )

    return {
        "preprocessing": preprocessing,
        "parsed_dialogue": parsed_dialogue,
        "agent_turn_assignments": preprocessing.get("agent_turn_assignments", {}),
    }
