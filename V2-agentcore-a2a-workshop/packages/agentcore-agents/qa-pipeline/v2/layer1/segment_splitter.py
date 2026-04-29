# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 1 (b) — 구간 분리 (opening / body / closing).

설계서 p9:
    (b) 구간 분리 — 상담을 인사(도입) / 본론 / 끝인사(종료) 구간으로 분할.
        각 Sub Agent 가 필요한 구간만 참조.

V1 `nodes/dialogue_parser.py` 의 _parse_turns / _detect_segments /
_separate_speakers / _create_turn_pairs / _build_turn_assignments 를
import 로 재활용한다. V1 원본 수정 없이 import only.

출력 (PL 확정 스펙):
    detected_sections: dict[str, list[int]]
        {"opening": [start_turn_idx, end_turn_idx],
         "body":    [start_turn_idx, end_turn_idx],
         "closing": [start_turn_idx, end_turn_idx]}
        (0-based turn index, end 는 exclusive)

부가 (sibling):
    turns                  — 모든 턴 파싱 결과 (화자/텍스트/segment)
    agent_turn_ids         — V1 호환
    customer_turn_ids      — V1 호환
    turn_pairs             — V1 호환
    agent_turn_assignments — V1 호환 (Sub Agent 8 key 별 턴 범위 + 텍스트)
"""

from __future__ import annotations

import logging
from typing import Any

# V1 자산 재활용 — import only (수정 금지)
from nodes.dialogue_parser import (  # type: ignore[import-untyped]
    _build_turn_assignments,
    _create_turn_pairs,
    _detect_segments,
    _parse_turns,
    _separate_speakers,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 메인 함수
# ---------------------------------------------------------------------------


def split_sections(transcript: str) -> dict[str, Any]:
    """전사록을 턴 단위로 파싱하고 opening/body/closing 3구간으로 분할.

    Parameters
    ----------
    transcript : str
        원본 전사록 텍스트 (줄바꿈이 턴 경계).

    Returns
    -------
    dict
        {
          "detected_sections": {"opening": [start, end], "body": [...], "closing": [...]},
          "turns": [{"turn_id", "speaker", "text", "segment"}, ...],
          "agent_turn_ids": list[int],
          "customer_turn_ids": list[int],
          "turn_pairs": list[dict],
          "agent_turn_assignments": dict[str, dict],
          "detected_sections_meta": {
              "agent_turn_ids": [...],
              "customer_turn_ids": [...],
              "turn_pairs": [...],
          },
        }

    Notes
    -----
    - V1 dialogue_parser 는 turn_id 가 1-based 이지만 PL 확정 스펙은
      detected_sections 가 turn index pair (start, end) 를 요구 — 0-based idx
      (start inclusive, end exclusive) 로 매핑해 반환.
    - turns 리스트에는 원래의 1-based turn_id 를 그대로 유지 (V1 호환).
    """
    if not transcript or not transcript.strip():
        logger.warning("split_sections: 전사록이 비어있음")
        return _empty_result()

    # (1) 턴 파싱 (V1 재활용)
    turns = _parse_turns(transcript)
    if not turns:
        logger.warning("split_sections: 파싱된 턴이 없음")
        return _empty_result()

    # (2) 구간 분할 (V1 재활용) — turn_id(1-based) 리스트 반환
    segments_by_turn_id = _detect_segments(turns)
    intro_ids = segments_by_turn_id.get("intro", [])
    body_ids = segments_by_turn_id.get("body", [])
    closing_ids = segments_by_turn_id.get("closing", [])

    # 각 턴에 segment 필드 주입 (V1 dialogue_parser_node 와 동일 로직)
    intro_set = set(intro_ids)
    body_set = set(body_ids)
    closing_set = set(closing_ids)
    for t in turns:
        tid = t.get("turn_id")
        if tid in intro_set:
            t["segment"] = "도입"
        elif tid in closing_set:
            t["segment"] = "종결"
        elif tid in body_set:
            t["segment"] = "본문"
        else:
            t["segment"] = ""

    # (3) 화자별 분리 (V1 재활용)
    agent_turn_ids, customer_turn_ids = _separate_speakers(turns)

    # (4) 턴 페어링 (V1 재활용)
    turn_pairs = _create_turn_pairs(turns)

    # (5) 에이전트별 턴 할당 (V1 재활용)
    agent_turn_assignments = _build_turn_assignments(
        turns, segments_by_turn_id, agent_turn_ids, customer_turn_ids, turn_pairs
    )

    # (6) turn_id(1-based) → turn index(0-based) 변환
    # V1 dialogue_parser 는 turn_id 가 1부터 시작해 순차 증가하고 전체 turns 의 인덱스+1 과 일치.
    # 따라서 turn_index = turn_id - 1 로 단순 변환 가능.
    detected_sections = {
        "opening": _ids_to_index_range(intro_ids),
        "body": _ids_to_index_range(body_ids),
        "closing": _ids_to_index_range(closing_ids),
    }

    logger.info(
        "split_sections: opening=%s body=%s closing=%s (total=%d turns)",
        detected_sections["opening"],
        detected_sections["body"],
        detected_sections["closing"],
        len(turns),
    )

    return {
        "detected_sections": detected_sections,
        "turns": turns,
        "agent_turn_ids": agent_turn_ids,
        "customer_turn_ids": customer_turn_ids,
        "turn_pairs": turn_pairs,
        "agent_turn_assignments": agent_turn_assignments,
        "detected_sections_meta": {
            "agent_turn_ids": agent_turn_ids,
            "customer_turn_ids": customer_turn_ids,
            "turn_pairs": turn_pairs,
        },
    }


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _ids_to_index_range(turn_ids: list[int]) -> list[int]:
    """turn_id (1-based, 연속 구간) 리스트를 [start_idx, end_idx] 로 변환.

    - turn_id 는 1..N 순차 증가하므로 min-1 / max 로 산출 (end 는 exclusive).
    - 빈 리스트는 [0, 0] 반환 (빈 구간 표시).

    Examples
    --------
    >>> _ids_to_index_range([1, 2, 3, 4, 5])
    [0, 5]
    >>> _ids_to_index_range([])
    [0, 0]
    """
    if not turn_ids:
        return [0, 0]
    start_idx = min(turn_ids) - 1
    end_idx = max(turn_ids)  # exclusive 이므로 +0
    return [max(0, start_idx), end_idx]


def _empty_result() -> dict[str, Any]:
    """빈 전사록용 no-op 결과."""
    return {
        "detected_sections": {"opening": [0, 0], "body": [0, 0], "closing": [0, 0]},
        "turns": [],
        "agent_turn_ids": [],
        "customer_turn_ids": [],
        "turn_pairs": [],
        "agent_turn_assignments": {},
        "detected_sections_meta": {
            "agent_turn_ids": [],
            "customer_turn_ids": [],
            "turn_pairs": [],
        },
    }
