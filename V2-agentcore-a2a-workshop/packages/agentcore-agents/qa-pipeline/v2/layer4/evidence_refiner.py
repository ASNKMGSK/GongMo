# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Evidence 정제기 — 원칙 3 구현 (Dev5 Layer 4).

Sub Agent 가 반환한 ItemVerdict.evidence[] 를 QAOutputV2.ItemResult.evidence[] 에
싣기 전에 수행하는 정제 작업:

1. 구조 정규화 — {speaker, timestamp, quote, turn_id} 키 채움
2. turn_id → speaker/timestamp 보강 — Layer 1 dialogue_parser 의 turns[] 와 join
3. 중복 제거 — 동일 turn_id + quote prefix 같으면 1건으로
4. 빈 quote 제거 — quote="" 인 엔트리 제거
5. LLM hallucination 탐지 — turns 에 존재하지 않는 quote 는 경고 로깅 후 제거 (원칙 3)

단, evaluation_mode 가 `structural_only` / `compliance_based` / `unevaluable` /
`skipped` 일 때는 evidence 비어있을 수 있음 (마스킹 환경 제약).
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)


def _coerce_turn_id(raw: Any) -> int | None:
    """turn_id 를 int 로 강제 변환. Sub Agent 는 종종 'turn_14' / '14' / 14 등 혼재 반환.

    - int → 그대로
    - 'turn_14' → 14  (prefix 제거)
    - '14' → 14
    - 그 외 / 실패 → None
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.lower().startswith("turn_"):
            s = s[5:]
        if s.lstrip("-").isdigit():
            try:
                return int(s)
            except ValueError:
                return None
    try:
        return int(raw)  # float 등
    except (TypeError, ValueError):
        return None


def _normalize_entry(
    ev: dict[str, Any],
    *,
    turns_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    """evidence 1건 정규화. 무효 시 None."""
    quote = (ev.get("quote") or "").strip()
    if not quote:
        return None

    turn_id = _coerce_turn_id(ev.get("turn_id"))
    speaker = ev.get("speaker") or ""
    timestamp = ev.get("timestamp")

    # turn_id 로 Layer 1 dialogue_parser 턴 조회 가능하면 빈 필드 보강
    if turn_id is not None and turn_id in turns_by_id:
        turn = turns_by_id[turn_id]
        if not speaker:
            speaker = turn.get("speaker", "")
        if timestamp is None:
            timestamp = turn.get("timestamp")
        # Hallucination 탐지: 실제 turn.text 에 quote 가 포함돼 있지 않으면 경고만 (drop 안 함).
        # 이전엔 None 반환으로 drop 했으나 모든 evidence 가 drop 되면 ItemResult validator 의
        # "evidence 최소 1개 필수" 위반으로 report_generator_v2 크래시 → 전체 보고서 미생성 → HITL 큐 비어
        # "모든 항목 만점" 으로 잘못 표시되는 치명적 회귀가 발생. entry 는 유지하고 경고만 남김.
        canonical_text = (turn.get("text") or "").strip()
        if canonical_text and quote[:40] not in canonical_text and canonical_text[:40] not in quote:
            logger.warning(
                "evidence_refiner: quote 가 turn_id=%s 에 없음 — hallucination 의심 (entry 유지). quote=%r",
                turn_id, quote[:80],
            )

    return {
        "speaker": speaker or "상담사",  # 기본값 — 타입 유지. tenant_config 에서 override
        "timestamp": timestamp,
        "quote": quote,
        "turn_id": turn_id,
    }


def _dedup_key(entry: dict[str, Any]) -> tuple:
    """중복 판정 키 — (turn_id, quote prefix 40자)."""
    return (entry.get("turn_id"), (entry.get("quote") or "")[:40])


def refine_evidence(
    evidence: Iterable[dict[str, Any]] | None,
    *,
    turns: list[dict[str, Any]] | None = None,
    evaluation_mode: str = "full",
    item_number: int | None = None,
) -> list[dict[str, Any]]:
    """evidence 배열 정제 (원칙 3).

    Parameters
    ----------
    evidence : Sub Agent 반환 ItemVerdict.evidence (None/빈배열 허용)
    turns : Layer 1 parsed_dialogue.turns — turn_id → 본문 lookup 에 사용
    evaluation_mode : 모드에 따라 빈 배열 허용 여부 다름
    item_number : 로깅용

    Returns
    -------
    정제된 evidence list[EvidenceQuote-dict].
      - full 모드에서 모두 탈락하면 빈 배열 그대로 반환 (Validator 가 거부할 수 있도록).
      - 이 함수는 mode 검증을 하지 않음 — pydantic 단에서.
    """
    evidence = list(evidence or [])
    if not evidence:
        return []

    turns_by_id: dict[int, dict[str, Any]] = {}
    for t in turns or []:
        tid = t.get("turn_id")
        if tid is not None:
            turns_by_id[tid] = t

    normalized: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for raw in evidence:
        if not isinstance(raw, dict):
            logger.warning("evidence_refiner[item=%s]: non-dict entry skipped: %r", item_number, raw)
            continue
        entry = _normalize_entry(raw, turns_by_id=turns_by_id)
        if entry is None:
            continue
        key = _dedup_key(entry)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(entry)

    return normalized


def extract_turns_from_state(state_like: dict[str, Any]) -> list[dict[str, Any]]:
    """QAStateV2 유사 dict 에서 turns 리스트를 꺼내는 헬퍼.

    우선순위 (모두 fallback):
      1. state["preprocessing"]["turns"]         # Dev1 canonical
      2. state["preprocessing"]["parsed_dialogue"]["turns"]
      3. state["parsed_dialogue"]["turns"]        # V1 호환
    """
    pre = state_like.get("preprocessing") or {}
    turns = pre.get("turns")
    if turns:
        return list(turns)

    pd = pre.get("parsed_dialogue") or state_like.get("parsed_dialogue") or {}
    turns = pd.get("turns")
    if turns:
        return list(turns)

    return []
