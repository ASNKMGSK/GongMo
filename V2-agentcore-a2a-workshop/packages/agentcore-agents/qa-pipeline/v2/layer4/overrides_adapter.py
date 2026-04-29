# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Override 인터페이스 (Dev1 Layer 1/3 ↔ Dev5 Layer 4).

PL 지시 (2026-04-20): Dev1 Orchestrator V2 가 Layer 1 `DeductionTriggerResult` 의
세 필드를 Dev5 `OverridesBlock` 에 공급하도록 단일 어댑터 제공.

입력 (Dev1 가 `orchestrator.overrides_applied` 또는
state.preprocessing.deduction_trigger_details 로 넣어주는 구조):

    {
      "recommended_override": "all_zero" | "category_zero" | "item_zero" | "none",
      "has_all_zero_trigger": bool,
      "has_category_zero_categories": list[str],   # 예: ["privacy_protection"]
      "triggers": [DeductionTrigger, ...],          # 세부 트리거 리스트 (원천)
    }

출력: `OverridesBlock` pydantic 인스턴스 (qa_output_v2.OverridesBlock 호환).

설계 규칙 (설계서 §5.2, §4 Layer 3 (b)):
  1. `has_all_zero_trigger=True` 이면 → `action="all_zero"` + affected_items=1~18 전체
  2. `has_category_zero_categories` 가 있으면 → 각 카테고리당 1 OverrideEntry,
     action="category_zero", affected_items=CATEGORY_META[key].items
  3. 그 외 triggers 의 `recommended_override="item_zero"` 는 해당 item 만
  4. `recommended_override="none"` 은 Sub Agent 판정 존중 → OverrideEntry 생성 안 함
  5. applied = len(reasons) > 0
"""

from __future__ import annotations

import logging
from typing import Any

from v2.schemas.enums import CATEGORY_META, CategoryKey, OverrideAction, OverrideTrigger
from v2.schemas.qa_output_v2 import OverrideEntry, OverridesBlock

logger = logging.getLogger(__name__)


# Dev1 DeductionTriggerType (profanity/contempt/arbitrary_disconnect) →
# 우리 OverrideTrigger 와 동일 (enum 통합 완료)
_VALID_TRIGGERS: frozenset[str] = frozenset({
    "profanity", "contempt", "arbitrary_disconnect",
    "preemptive_disclosure", "privacy_leak", "uncorrected_misinfo",
})

_VALID_ACTIONS: frozenset[str] = frozenset({
    "all_zero", "category_zero", "item_zero", "none",
})

_ALL_ITEM_NUMBERS: tuple[int, ...] = tuple(range(1, 19))


def _category_items(category_key: str) -> list[int]:
    """CATEGORY_META 에서 카테고리별 item_number 리스트."""
    meta = CATEGORY_META.get(category_key)  # type: ignore[arg-type]
    if not meta:
        return []
    return list(meta["items"])


def _trigger_or_default(value: Any, default: str) -> str:
    """OverrideTrigger 리터럴로 정규화. 부재 시 default."""
    if isinstance(value, str) and value in _VALID_TRIGGERS:
        return value
    return default


def build_overrides_block(
    *,
    recommended_override: str | None = None,
    has_all_zero_trigger: bool = False,
    has_category_zero_categories: list[str] | None = None,
    triggers: list[dict[str, Any]] | None = None,
) -> OverridesBlock:
    """Dev1 필드 3종 + 세부 triggers 를 받아 OverridesBlock 조립.

    Parameters
    ----------
    recommended_override : Layer 1 종합 권고 ("all_zero" | "category_zero" | "item_zero" | "none").
        `none` 이면 Layer 3 Override 는 적용 안 함 (Sub Agent 판정 유지).
    has_all_zero_trigger : 불친절 등으로 전체 0점 트리거 존재.
    has_category_zero_categories : category_zero 대상 카테고리 키 목록 (예: ["privacy_protection"]).
    triggers : 세부 감점 트리거 리스트 — OverrideEntry 의 evidence/rationale 원천.
        형태: [{"trigger_type": "privacy_leak", "turn_id": 12, "evidence_text": "...",
                "recommended_override": "category_zero", ...}, ...]

    Returns
    -------
    OverridesBlock — qa_output_v2 호환. `applied=True` 이면 하나 이상의 reasons 존재.
    """
    reasons: list[OverrideEntry] = []
    triggers = list(triggers or [])
    has_category_zero_categories = list(has_category_zero_categories or [])

    # 1) 전체 0점 (최상위 — 설계서 §5.2 "불친절")
    if has_all_zero_trigger:
        # 세부 triggers 에서 all_zero 후보를 뽑아 evidence/reason 확장
        all_zero_triggers = [t for t in triggers if t.get("recommended_override") == "all_zero"]
        evidence_list: list[dict[str, Any]] = []
        reason_parts: list[str] = []
        trigger_type = "profanity"  # 기본값 — 세부가 비어도 action 은 유지
        for t in all_zero_triggers:
            trigger_type = _trigger_or_default(t.get("trigger_type"), trigger_type)
            if t.get("evidence_text"):
                evidence_list.append({
                    "speaker": t.get("speaker") or "상담사",
                    "timestamp": t.get("timestamp"),
                    "quote": str(t["evidence_text"]),
                    "turn_id": t.get("turn_id"),
                })
                reason_parts.append(f"turn {t.get('turn_id')}: {t['evidence_text'][:40]}")

        reasons.append(OverrideEntry(
            trigger=trigger_type,  # type: ignore[arg-type]
            action="all_zero",
            affected_items=list(_ALL_ITEM_NUMBERS),
            reason="전체 0점 Override (" + "; ".join(reason_parts or ["Layer 1 감점 트리거"]) + ")",
            evidence=evidence_list,
        ))
        # all_zero 가 적용되면 category_zero/item_zero 는 상위 규칙에 의해 무의미 —
        # 하지만 감사 가시성을 위해 같이 기록.

    # 2) 카테고리 전체 0점 (예: 개인정보 유출 → privacy_protection 카테고리 0점)
    seen_categories: set[str] = set()
    for category_key in has_category_zero_categories:
        if category_key in seen_categories:
            continue
        seen_categories.add(category_key)
        items = _category_items(category_key)
        if not items:
            logger.warning(
                "overrides_adapter: 미등록 category key=%r — skip category_zero 생성",
                category_key,
            )
            continue
        # category_zero 관련 triggers 에서 evidence/reason 추출
        cat_triggers = [
            t for t in triggers
            if t.get("recommended_override") == "category_zero"
            and (t.get("category_key") == category_key or not t.get("category_key"))
        ]
        evidence_list = []
        reason_parts = []
        trigger_type = "privacy_leak"
        for t in cat_triggers:
            trigger_type = _trigger_or_default(t.get("trigger_type"), trigger_type)
            if t.get("evidence_text"):
                evidence_list.append({
                    "speaker": t.get("speaker") or "상담사",
                    "timestamp": t.get("timestamp"),
                    "quote": str(t["evidence_text"]),
                    "turn_id": t.get("turn_id"),
                })
                reason_parts.append(f"turn {t.get('turn_id')}: {t['evidence_text'][:40]}")

        reasons.append(OverrideEntry(
            trigger=trigger_type,  # type: ignore[arg-type]
            action="category_zero",
            affected_items=items,
            reason=f"카테고리 전체 0점 — {category_key} (" + "; ".join(reason_parts or ["Layer 1 트리거"]) + ")",
            evidence=evidence_list,
        ))

    # 3) 개별 항목 0점 (recommended_override=item_zero 인 triggers)
    item_zero_triggers = [t for t in triggers if t.get("recommended_override") == "item_zero"]
    for t in item_zero_triggers:
        item_number = t.get("item_number")
        if not isinstance(item_number, int):
            continue
        trigger_type = _trigger_or_default(t.get("trigger_type"), "uncorrected_misinfo")
        evidence_list = []
        if t.get("evidence_text"):
            evidence_list.append({
                "speaker": t.get("speaker") or "상담사",
                "timestamp": t.get("timestamp"),
                "quote": str(t["evidence_text"]),
                "turn_id": t.get("turn_id"),
            })
        reasons.append(OverrideEntry(
            trigger=trigger_type,  # type: ignore[arg-type]
            action="item_zero",
            affected_items=[item_number],
            reason=f"항목 {item_number} 0점 — {t.get('evidence_text', '')[:60]}",
            evidence=evidence_list,
        ))

    # 4) recommended_override=="none" 은 override 생성 안 함 — Sub Agent 판정 유지
    # (reasons 에 아무것도 추가하지 않음)

    return OverridesBlock(applied=bool(reasons), reasons=reasons)


def apply_overrides_to_scores(
    item_results: list[Any],  # list[ItemResult] — 순환 import 회피
    *,
    overrides_block: OverridesBlock,
) -> list[Any]:
    """Override 규약에 따라 ItemResult.score 를 실제 0점으로 덮어씀.

    규칙 우선순위:
      1. action="all_zero" → 모든 item.score = 0
      2. action="category_zero" → affected_items 에 포함된 item.score = 0
      3. action="item_zero" → affected_items 에 포함된 item.score = 0

    unevaluable 항목은 그대로 None 유지 (Override 가 부여하는 0점도 의미 없음).
    skipped 항목은 Override 대상에서 제외 (설계상 특수 상황).
    """
    if not overrides_block.applied:
        return item_results

    zero_items: set[int] = set()
    for entry in overrides_block.reasons:
        if entry.action == "all_zero":
            zero_items.update(_ALL_ITEM_NUMBERS)
        elif entry.action in ("category_zero", "item_zero"):
            zero_items.update(entry.affected_items)

    out = []
    for it in item_results:
        mode = getattr(it, "evaluation_mode", "full")
        if it.item_number in zero_items and mode not in ("unevaluable", "skipped"):
            it = it.model_copy(update={"score": 0})
        out.append(it)
    return out


# ---------------------------------------------------------------------------
# Public interface summary (Dev1 ↔ Dev5 계약)
# ---------------------------------------------------------------------------
#
# Dev1 (Layer 3 Orchestrator V2) 호출 예:
#
#     from v2.layer4.overrides_adapter import build_overrides_block
#     overrides_block = build_overrides_block(
#         recommended_override=layer1_dtr.get("recommended_override"),
#         has_all_zero_trigger=layer1_dtr.get("has_all_zero_trigger", False),
#         has_category_zero_categories=layer1_dtr.get("has_category_zero_categories", []),
#         triggers=layer1_dtr.get("triggers", []),
#     )
#     state["orchestrator"]["overrides_applied"] = [
#         entry.model_dump() for entry in overrides_block.reasons
#     ]
#
# 또는 Dev1 이 trigger 리스트를 이미 가공해서 넣어주면 Dev5 report_generator_v2 가
# 최종 JSON 직렬화 시 그대로 사용. 현재 `_build_overrides_block()` 도
# orchestrator.overrides_applied 를 먼저 확인하므로 기존 흐름과 공존 가능.
