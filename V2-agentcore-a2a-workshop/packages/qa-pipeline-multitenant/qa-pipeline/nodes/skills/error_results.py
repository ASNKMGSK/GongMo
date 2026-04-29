# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Standard error-result builder for evaluation agents.

평가 노드들이 LLM 호출 실패·전사록 누락·예외 상황에서 반환하는 dict를
한 가지 형태로 통일한다.  반환 키(`status`/`agent_id`/`message`/`evaluation`)는
프론트 호환을 위해 그대로 유지된다.
"""

from __future__ import annotations

from typing import Any


def build_llm_failure_result(
    agent_id: str,
    message: str,
    *,
    item_number: int | None = None,
    item_name: str | None = None,
    max_score: int = 0,
    score: int = 0,
    extra: dict[str, Any] | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    """Build a standard error evaluation entry for an agent.

    Args:
        agent_id: 평가 결과를 생성한 에이전트 식별자 (예: "scope-agent").
        message: 사용자에게 노출할 에러 사유.
        item_number: 평가 항목 번호 (1~18). None 이면 evaluation 블록을 생략.
        item_name: 평가 항목 이름. item_number 와 함께 지정.
        max_score: 항목 만점.
        score: 부여 점수 (실패이므로 기본 0).
        extra: evaluation 블록에 병합할 추가 키 (rule_violations 등).

    Returns:
        ``{"status": "error", "agent_id": ..., "message": ..., "evaluation": {...}}``
        형식의 dict.  ``item_number`` 가 None 이면 ``evaluation`` 키 자체가
        생략되어 노드 단위(전사록 누락 등) 실패와 동일한 형태가 된다.
    """
    result: dict[str, Any] = {
        "status": "error",
        "agent_id": agent_id,
        "message": message,
    }
    if error_type:
        result["error_type"] = error_type
    if item_number is not None:
        eval_block: dict[str, Any] = {
            "item_number": item_number,
            "item_name": item_name or "",
            "max_score": max_score,
            "score": score,
            "deductions": [],
            "evidence": [],
            "confidence": 0.0,
        }
        if extra:
            eval_block.update(extra)
        result["evaluation"] = eval_block
    return result
