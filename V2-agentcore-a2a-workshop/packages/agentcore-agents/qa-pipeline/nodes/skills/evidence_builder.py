# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Evidence builder — deductions.evidence_ref 기반 우선 선택 헬퍼.

assigned_turns + deductions 로부터 agent_utterance 에 실제로 표시될 evidence 리스트를
만든다. 핵심 규칙:

1. deductions[].evidence_ref = "turn_N" 에서 턴 번호를 추출해 assigned_turns 에서 매칭
2. 매칭된 턴만 evidence 로 반환 (우선순위)
3. 매칭 실패/빈 deductions → assigned_turns 중 turn_id=1(첫인사) 제외 + agent 발화 중
   짧은 응답("네","예","아니요") 제외한 최초 agent 턴 반환
4. 그래도 없으면 LLM 이 준 evidence 를 그대로 사용 (호출측 책임)

이로써 mandatory/scope/work_accuracy 등에서 "상담사: 반갑습니다..." 또는 "상담사: 네"
같은 부적절 턴이 agent_utterance 에 유출되는 문제를 해결한다.
"""

from __future__ import annotations

import re
from typing import Any


# 짧은 단답만으로 구성된 agent 발화 (content-poor) — 이런 턴은 evidence 로 부적합
_SHORT_REPLIES = {"네", "예", "네.", "예.", "아니요", "아닙니다", "네 네", "네네"}


def _extract_turn_number(evidence_ref: Any) -> int | None:
    """evidence_ref (예: "turn_3", "turn_42~49", "3") → int 턴번호. 실패시 None."""
    if evidence_ref is None:
        return None
    s = str(evidence_ref).strip()
    if not s:
        return None
    m = re.search(r"(\d+)", s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def _turn_to_evidence(turn: dict[str, Any]) -> dict[str, Any]:
    """assigned_turns 의 turn dict → evidence dict."""
    return {
        "turn": turn.get("turn_id"),
        "speaker": turn.get("speaker", ""),
        "text": turn.get("text", ""),
    }


def _is_low_content_agent_turn(turn: dict[str, Any]) -> bool:
    """agent 단답 / 짧은 호응 (5자 이하 또는 사전 등재) 여부."""
    if turn.get("speaker") != "agent":
        return False
    text = (turn.get("text") or "").strip()
    if not text:
        return True
    if text in _SHORT_REPLIES:
        return True
    if len(text) <= 4:
        return True
    return False


def build_turn_evidence(
    assigned_turns: list[dict[str, Any]] | None,
    deductions: list[dict[str, Any]] | None,
    *,
    skip_greeting: bool = True,
) -> list[dict[str, Any]]:
    """assigned_turns + deductions → evidence 리스트.

    Args:
        assigned_turns: 오케스트레이터가 해당 에이전트에 할당한 턴 리스트.
            각 원소는 ``{"turn_id": int, "speaker": str, "text": str}``.
        deductions: 해당 평가의 감점 엔트리. ``evidence_ref`` 에 turn_N 이 포함되면
            그 턴을 우선적으로 evidence 로 선택한다.
        skip_greeting: True 면 fallback 경로에서 turn_id=1 (첫인사) 을 제외.

    Returns:
        evidence 리스트 (빈 리스트 가능). agent_utterance 는 이 중 첫 요소 text.
        매칭 실패 + fallback 미결정 시 빈 리스트 → 호출측이 LLM 원본 evidence 를 사용하도록.
    """
    if not assigned_turns:
        return []

    # turn_id → turn dict 인덱스
    turn_by_id: dict[int, dict[str, Any]] = {}
    for t in assigned_turns:
        if not isinstance(t, dict):
            continue
        tid = t.get("turn_id")
        if isinstance(tid, int):
            turn_by_id[tid] = t

    # 1) deductions.evidence_ref 로 지정된 턴 우선 (agent 만, 없으면 customer 포함)
    referenced_ids: list[int] = []
    for d in deductions or []:
        if not isinstance(d, dict):
            continue
        tid = _extract_turn_number(d.get("evidence_ref"))
        if tid is not None and tid in turn_by_id and tid not in referenced_ids:
            referenced_ids.append(tid)

    if referenced_ids:
        evidence = [_turn_to_evidence(turn_by_id[tid]) for tid in referenced_ids]
        # agent 턴이 하나라도 있으면 그대로 반환; 전부 customer 면 뒤에 fallback agent 추가
        if any(e.get("speaker") == "agent" for e in evidence):
            return evidence
        # customer-only 참조의 경우 context 용으로 남기고, agent 턴을 하나 더 붙임
        fallback_agent = _first_content_agent_turn(assigned_turns, skip_greeting=skip_greeting)
        if fallback_agent is not None:
            evidence.insert(0, _turn_to_evidence(fallback_agent))
        return evidence

    # 2) deductions 가 없거나 ref 매칭 실패 → agent 발화 중 적절한 턴 선택
    fallback_agent = _first_content_agent_turn(assigned_turns, skip_greeting=skip_greeting)
    if fallback_agent is not None:
        return [_turn_to_evidence(fallback_agent)]

    # 3) 최후 수단 — assigned_turns 전체 반환 (빈 리스트보단 낫다; 호출측에서 선택)
    return [_turn_to_evidence(t) for t in assigned_turns if isinstance(t, dict)]


def _first_content_agent_turn(
    assigned_turns: list[dict[str, Any]],
    *,
    skip_greeting: bool,
) -> dict[str, Any] | None:
    """agent 턴 중 첫인사/짧은 호응 제외한 최초 content-rich 턴 반환. 없으면 None."""
    for t in assigned_turns:
        if not isinstance(t, dict):
            continue
        if skip_greeting and t.get("turn_id") == 1:
            continue
        if t.get("speaker") != "agent":
            continue
        if _is_low_content_agent_turn(t):
            continue
        return t
    return None


__all__ = ["build_turn_evidence"]
