# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deduction-log builder helpers shared by evaluation nodes.

평가 노드가 각자 작성하던 deduction_log 구성 루프를 두 가지 형태로 통일한다.

- ``build_deduction_log_from_evaluations`` — ``{"evaluation": {...}}`` 결과
  리스트(merged)를 받는 노드용. 옵션으로 ``score < max_score`` 게이트와 빈
  deductions fallback entry 생성을 켤 수 있다.
- ``build_deduction_log_from_pairs`` — ``(item_number, result_dict)`` 페어
  리스트를 받는 노드용. 사전 계산된 deductions 만 평탄화한다.

두 헬퍼 모두 출력 dict 키는 ``agent_id / item_number / reason / points /
turn_ref`` 로 고정 (프론트 호환).
"""

from __future__ import annotations

from typing import Any


def _normalize_turn_ref(ref: Any) -> str:
    """LLM 응답에서 ``evidence_ref`` 가 list/dict/숫자 등 비정형 값으로 올 때 문자열로 정규화.

    예) ``["turn_5", "turn_9"]`` → ``"turn_5,turn_9"``, ``5`` → ``"turn_5"``, ``None`` → ``""``.
    해시 가능한 string 으로 고정해야 consistency_check 의 Counter 집계에서 TypeError 가 나지 않음.
    """
    if ref is None:
        return ""
    if isinstance(ref, str):
        return ref
    if isinstance(ref, (int, float)):
        return f"turn_{int(ref)}"
    if isinstance(ref, list):
        return ",".join(_normalize_turn_ref(x) for x in ref if x is not None)
    if isinstance(ref, dict):
        turn = ref.get("turn") or ref.get("turn_id") or ref.get("index")
        if turn is not None:
            return _normalize_turn_ref(turn)
        return ""
    return str(ref)


def build_deduction_log_from_evaluations(
    evaluations: list[dict[str, Any]],
    agent_id: str,
    *,
    with_empty_fallback: bool = False,
) -> list[dict[str, Any]]:
    """Build deduction_log entries from a list of evaluation result dicts.

    Args:
        evaluations: 각 원소가 ``{"evaluation": {...}}`` 형태인 결과 리스트.
            원소 자체가 ``agent_id`` 키를 가지면 그 값을 우선 사용하고,
            없으면 인자로 받은 ``agent_id`` 를 fallback 으로 사용한다.
        agent_id: 결과 dict 에 ``agent_id`` 가 없을 때 적용할 기본 에이전트 id.
        with_empty_fallback: True 면 ``score < max_score`` 인데
            ``deductions`` 가 비어있을 때 점수 차이를 기반으로 한 기본 entry
            1개를 추가한다 (greeting/courtesy/understanding 패턴).
            False 면 score 게이트 없이 모든 deductions 를 평탄화한다
            (mandatory 패턴).

    Returns:
        ``[{"agent_id", "item_number", "reason", "points", "turn_ref"}, ...]``
        리스트.
    """
    log: list[dict[str, Any]] = []
    for ev in evaluations:
        eval_data = ev.get("evaluation", {})
        item_number = eval_data.get("item_number")
        item_name = eval_data.get("item_name", "")
        score = eval_data.get("score", 0)
        max_score = eval_data.get("max_score", 5)
        entry_agent_id = ev.get("agent_id", agent_id)
        deductions = eval_data.get("deductions", [])

        if with_empty_fallback:
            if score >= max_score:
                continue
            if deductions:
                for d in deductions:
                    log.append({
                        "agent_id": entry_agent_id,
                        "item_number": item_number,
                        "reason": d.get("reason", ""),
                        "points": d.get("points", max_score - score),
                        "turn_ref": _normalize_turn_ref(d.get("evidence_ref", "")),
                    })
            else:
                log.append({
                    "agent_id": entry_agent_id,
                    "item_number": item_number,
                    "reason": f"{item_name} 감점 ({max_score - score}점)",
                    "points": max_score - score,
                    "turn_ref": "",
                })
        else:
            for d in deductions:
                log.append({
                    "agent_id": entry_agent_id,
                    "item_number": item_number,
                    "reason": d.get("reason", ""),
                    "points": d.get("points", 0),
                    "turn_ref": _normalize_turn_ref(d.get("evidence_ref", "")),
                })
    return log


def build_deduction_log_from_pairs(
    pairs: list[tuple[int, dict[str, Any]]],
    agent_id: str,
) -> list[dict[str, Any]]:
    """Build deduction_log entries from ``(item_number, result_dict)`` pairs.

    Args:
        pairs: 각 원소가 ``(item_number, result_dict)`` 인 리스트.
            ``result_dict`` 의 ``deductions`` 만 평탄화한다.
        agent_id: 모든 entry 에 적용할 에이전트 id (예: ``"scope-agent"``).

    Returns:
        ``[{"agent_id", "item_number", "reason", "points", "turn_ref"}, ...]``
        리스트. score 게이트나 fallback entry 는 만들지 않는다.
    """
    log: list[dict[str, Any]] = []
    for item_number, result in pairs:
        for d in result.get("deductions", []):
            log.append({
                "agent_id": agent_id,
                "item_number": item_number,
                "reason": d.get("reason", ""),
                "points": d.get("points", 0),
                "turn_ref": _normalize_turn_ref(d.get("evidence_ref", "")),
            })
    return log
