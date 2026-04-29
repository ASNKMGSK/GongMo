# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 3 (c) — 전체 일관성 체크 (Rule 기반, LLM 없음).

설계서 p10 Layer 3 (c):
    예컨대 '첫인사'는 만점인데 '정중한 표현'이 0점이면 모순이므로 재검토 flag.
    항목 간 논리 정합성을 점검한다.

V1 `nodes/consistency_check.py` 는 LLM 을 사용했으나, V2 Layer 3 는 설계서에 따라
**순수 Rule 기반** 으로 수행 (LLM 판단 필요한 경우는 Layer 2 Sub Agent 에서 처리).

현 Rule 목록:
    CR1 #1 첫인사 만점 AND #6 정중한 표현 0점 → 모순 flag
    CR2 #2 끝인사 만점 AND #14 사후 안내 0점 → 모순 flag
    CR3 #4 호응공감 만점 AND #3/#5 경청 0점 → 의심 flag
    CR4 #15 정확한 안내 만점 AND work_accuracy 감점 트리거 → 충돌 flag
    CR5 #8 문의 파악 만점 AND #10 설명 명확성 0점 → 의심 flag
    CR6 evidence[]=[] + evaluation_mode=full 인 item → 원칙 3 위반 flag

반환: consistency_flags[] — 재검토 플래그 리스트. Layer 4 가 priority_flags 로 포워드.
"""

from __future__ import annotations

import logging
from typing import Any

from v2.layer3.aggregator import category_of_item


logger = logging.getLogger(__name__)


# ===========================================================================
# Rule 정의 — (rule_id, description, item_pair_checker)
# ===========================================================================


_CONSISTENCY_RULES = [
    # CR1: 첫인사 만점인데 정중한 표현 0점
    {
        "code": "greeting_courtesy_mismatch",
        "items": [1, 6],
        "severity": "warn",
        "description": "#1 첫인사 만점인데 #6 정중한 표현 0점 — 모순 가능 (재검토 권장)",
        "checker": lambda items: (
            _item_score(items, 1) == _item_max(items, 1)
            and _item_score(items, 6) == 0
        ),
    },
    # CR2: 끝인사 만점인데 사후 안내 0점
    {
        "code": "closing_followup_mismatch",
        "items": [2, 14],
        "severity": "info",
        "description": "#2 끝인사 만점인데 #14 사후 안내 0점 — 종결 품질 편차",
        "checker": lambda items: (
            _item_score(items, 2) == _item_max(items, 2)
            and _item_score(items, 14) == 0
        ),
    },
    # CR3: 호응공감 만점인데 경청 0점
    {
        "code": "empathy_listening_mismatch",
        "items": [3, 4, 5],
        "severity": "info",
        "description": "#4 호응공감 만점인데 #3/#5 경청 0점 — 경청 품질 의심",
        "checker": lambda items: (
            _item_score(items, 4) == _item_max(items, 4)
            and (_item_score(items, 3) == 0 or _item_score(items, 5) == 0)
        ),
    },
    # CR4: 정확한 안내 만점인데 인접 감점 존재
    {
        "code": "accuracy_mandatory_mismatch",
        "items": [15, 16],
        "severity": "warn",
        "description": "#15 정확한 안내 만점인데 #16 필수 안내 0점 — 업무 숙지 편차",
        "checker": lambda items: (
            _item_score(items, 15) == _item_max(items, 15)
            and _item_score(items, 16) == 0
        ),
    },
    # CR5: 문의 파악 만점인데 설명 명확성 0점
    {
        "code": "understanding_explanation_mismatch",
        "items": [8, 10],
        "severity": "info",
        "description": "#8 문의 파악 만점인데 #10 설명 명확성 0점 — 설명 품질 의심",
        "checker": lambda items: (
            _item_score(items, 8) == _item_max(items, 8)
            and _item_score(items, 10) == 0
        ),
    },
]


# ===========================================================================
# 메인 함수
# ===========================================================================


def check_consistency(
    category_scores: list[dict[str, Any]],
    normalized_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rule 기반 교차 점검 → consistency_flags 리스트 반환.

    Parameters
    ----------
    category_scores : list[dict]
        aggregator/override 후의 category_scores (item.score 가 최종값).
    normalized_items : list[dict] | None
        aggregator 가 생성한 플랫 리스트 (없으면 category_scores 에서 추출).

    Returns
    -------
    dict
        {
          "flags": [
              {"code": "...", "severity": "info|warn|critical",
               "description": "...", "item_numbers": [...]},
              ...
          ],
          "has_critical": bool,
          "has_warning": bool,
        }
    """
    items = normalized_items or _flatten(category_scores)
    flags: list[dict[str, Any]] = []

    # (A) Rule 기반 모순 체크 — 5 rules
    for rule in _CONSISTENCY_RULES:
        try:
            if rule["checker"](items):
                flags.append({
                    "code": rule["code"],
                    "severity": rule["severity"],
                    "description": rule["description"],
                    "item_numbers": rule["items"],
                })
        except Exception:
            logger.exception("consistency_checker: rule %s 실패", rule["code"])

    # (B) Evidence 원칙 3 위반 체크 (evaluation_mode=full + evidence 비어있음)
    for item in items:
        mode = item.get("evaluation_mode", "full")
        evidence = item.get("evidence") or []
        if mode == "full" and len(evidence) == 0:
            flags.append({
                "code": "evidence_missing_full_mode",
                "severity": "warn",
                "description": (
                    f"#{item['item_number']} {item.get('item_name', '')} — "
                    "evaluation_mode=full 이지만 evidence 없음 (원칙 3 위반)"
                ),
                "item_numbers": [item["item_number"]],
            })

    has_critical = any(f["severity"] == "critical" for f in flags)
    has_warning = any(f["severity"] == "warn" for f in flags)

    logger.info(
        "check_consistency: flags=%d (critical=%s warn=%s)",
        len(flags), has_critical, has_warning,
    )

    return {
        "flags": flags,
        "has_critical": has_critical,
        "has_warning": has_warning,
    }


# ===========================================================================
# 헬퍼
# ===========================================================================


def _flatten(category_scores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """category_scores → 평가 item 플랫 리스트."""
    return [
        item
        for cat in category_scores
        for item in cat.get("items", [])
    ]


def _item_score(items: list[dict[str, Any]], item_number: int) -> int:
    """item_number 의 score 를 반환. 없으면 -1 (checker 가 if 로 걸러냄)."""
    for it in items:
        if it.get("item_number") == item_number:
            return int(it.get("score", 0) or 0)
    return -1


def _item_max(items: list[dict[str, Any]], item_number: int) -> int:
    """item_number 의 max_score. 없으면 0."""
    for it in items:
        if it.get("item_number") == item_number:
            return int(it.get("max_score", 0) or 0)
    return 0
