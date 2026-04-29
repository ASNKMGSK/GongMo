# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 3 (b) — Override 적용.

설계서 p10 Layer 3 (b) + p11 §5.2:
    Layer 1 에서 감점 트리거가 탐지된 경우 해당 정책에 따라 점수 무효화 또는
    강제 0점 처리. Override 는 Sub Agent 결과보다 항상 우선한다.

Override 3종 (설계서 §5.2):
    1. 불친절 (Layer 1 profanity/contempt/arbitrary_disconnect) → 전체 0점 (all_zero)
    2. 개인정보 유출 (Layer 1 privacy_leak) → 개인정보 보호 카테고리 0점 + 별도 보고
    3. 오안내 미정정 (Layer 2 work_accuracy.accuracy_verdict) → 업무정확도 카테고리 0점

Dev3 와 합의된 accuracy_verdict payload 소비:
    {has_incorrect_guidance: bool, correction_attempted: bool,
     incorrect_items: list[int], severity, evidence_turn_ids, recommended_override, rationale}

반환: `{applied: bool, reasons: list[OverrideEntry], after_overrides: int,
       items_modified: list[int]}`
"""

from __future__ import annotations

import logging
from typing import Any

from v2.schemas.enums import CATEGORY_META, OverrideAction, OverrideTrigger


logger = logging.getLogger(__name__)


# Category key 별 포함 item_number 리스트 (상수화)
_CATEGORY_ITEMS: dict[str, list[int]] = {
    key: list(meta["items"]) for key, meta in CATEGORY_META.items()
}


# Layer 1 DeductionTrigger 한글 key (Dev1 contracts.DEDUCTION_TRIGGER_KEYS) → Dev5 OverrideTrigger 매핑
_KOREAN_TRIGGER_TO_V5: dict[str, OverrideTrigger] = {
    "불친절": "profanity",             # 대표 매핑 (contempt/arbitrary_disconnect 도 불친절 bucket)
    "개인정보_유출": "privacy_leak",
    "오안내_미정정": "uncorrected_misinfo",
}


# ===========================================================================
# 메인 함수
# ===========================================================================


def apply_overrides(
    category_scores: list[dict[str, Any]],
    preprocessing: dict[str, Any] | None,
    accuracy_verdict: dict[str, Any] | None,
    sub_agent_override_hints: list[dict[str, Any]] | None = None,
    *,
    raw_total: int,
) -> dict[str, Any]:
    """Layer 1 감점 트리거 + Layer 2 accuracy_verdict + Sub Agent override_hint 를 읽어 Override 적용.

    Parameters
    ----------
    category_scores : list[dict]
        aggregator 출력의 category_scores (in-place 수정 — item score 0 으로 강제 가능).
    preprocessing : dict | None
        QAStateV2.preprocessing (Layer 1 산출물). deduction_triggers +
        deduction_trigger_details 소비.
    accuracy_verdict : dict | None
        QAStateV2.accuracy_verdict (Layer 2 work_accuracy Sub Agent 산출). Dev3 합의 포맷.
    sub_agent_override_hints : list[dict] | None
        Sub Agent 가 판정 중 감지한 override 시그널 (PDF 원칙 4 — preamble 체크리스트 #6).
        포맷: `[{"item_number": int, "hint": "profanity"|"privacy_leak"|"uncorrected_misinfo"}, ...]`
        Layer 1 Rule 트리거가 부재할 때 보조 시그널로 consume.
    raw_total : int
        aggregator 의 raw_total (override 전 총점).

    Returns
    -------
    dict
        {
          "applied": bool,
          "reasons": [OverrideEntry],   # Dev5 OverrideEntry 호환 (+ source 필드)
          "after_overrides": int,
          "items_modified": list[int],
          "category_scores": [...],      # 수정된 category_scores (in-place)
        }
    """
    prep = preprocessing or {}
    triggers: dict[str, bool] = prep.get("deduction_triggers") or {}
    trigger_details: list[dict] = prep.get("deduction_trigger_details") or []
    av: dict[str, Any] = accuracy_verdict or {}
    hints: list[dict[str, Any]] = list(sub_agent_override_hints or [])

    # Sub Agent hint 를 유형별로 버킷화
    hint_profanity: list[dict[str, Any]] = [
        h for h in hints if h.get("hint") == "profanity"
    ]
    hint_privacy_leak: list[dict[str, Any]] = [
        h for h in hints if h.get("hint") == "privacy_leak"
    ]
    hint_uncorrected: list[dict[str, Any]] = [
        h for h in hints if h.get("hint") == "uncorrected_misinfo"
    ]

    reasons: list[dict[str, Any]] = []
    items_modified: set[int] = set()

    # --- (1) 불친절 → all_zero ---
    # 우선순위: Layer 1 Rule 트리거가 활성화돼 있으면 그대로 사용, 아니면 Sub Agent hint 로 보강.
    if triggers.get("불친절"):
        unfriendly_details = [
            d for d in trigger_details
            if d.get("trigger_type") in ("profanity", "contempt", "arbitrary_disconnect")
        ]
        reasons.append(_build_reason(
            trigger=_dominant_unfriendly_trigger(unfriendly_details),
            action="all_zero",
            affected_items=sorted(_all_item_numbers()),
            reason_text="불친절 (욕설/비하/임의 단선) 탐지 — 전체 평가 0점 처리",
            evidence=_details_to_evidence(unfriendly_details, limit=3),
            source="rule",
        ))
        items_modified |= set(_all_item_numbers())
        _force_zero_all(category_scores)
    elif hint_profanity:
        # Sub Agent hint 보강 — Layer 1 Rule 놓친 케이스
        affected_item_nums = sorted(
            {int(h.get("item_number", 0)) for h in hint_profanity if h.get("item_number")}
        )
        reasons.append(_build_reason(
            trigger="profanity",
            action="all_zero",
            affected_items=sorted(_all_item_numbers()),
            reason_text=(
                "Sub Agent 불친절 감지 (Layer 1 Rule 미탐지 보강) — 전체 평가 0점 처리 "
                f"(source item_numbers={affected_item_nums})"
            ),
            evidence=[],
            source="sub_agent_hint",
        ))
        items_modified |= set(_all_item_numbers())
        _force_zero_all(category_scores)

    # --- (2) 개인정보 유출 → category_zero (개인정보 보호) ---
    if triggers.get("개인정보_유출"):
        privacy_details = [
            d for d in trigger_details
            if d.get("trigger_type") in ("privacy_leak", "preemptive_disclosure")
        ]
        privacy_items = _CATEGORY_ITEMS["privacy_protection"]
        reasons.append(_build_reason(
            trigger="privacy_leak",
            action="category_zero",
            affected_items=privacy_items,
            reason_text="개인정보 유출 탐지 — 개인정보 보호 카테고리 0점 처리 + 별도 보고",
            evidence=_details_to_evidence(privacy_details, limit=3),
            source="rule",
        ))
        items_modified |= set(privacy_items)
        _force_zero_items(category_scores, privacy_items)
    elif hint_privacy_leak:
        # Sub Agent hint 보강 — Layer 1 Rule 놓친 케이스
        privacy_items = _CATEGORY_ITEMS["privacy_protection"]
        affected_item_nums = sorted(
            {int(h.get("item_number", 0)) for h in hint_privacy_leak if h.get("item_number")}
        )
        reasons.append(_build_reason(
            trigger="privacy_leak",
            action="category_zero",
            affected_items=privacy_items,
            reason_text=(
                "Sub Agent 개인정보 유출 감지 (Layer 1 Rule 미탐지 보강) — "
                f"개인정보 보호 카테고리 0점 처리 (source item_numbers={affected_item_nums})"
            ),
            evidence=[],
            source="sub_agent_hint",
        ))
        items_modified |= set(privacy_items)
        _force_zero_items(category_scores, privacy_items)

    # --- (3) 오안내 → work_accuracy 카테고리/개별 항목 0점 ---
    # Dev3 합의 기준:
    #   has_incorrect_guidance=True AND correction_attempted=False → category_zero (대분류 전체)
    #   has_incorrect_guidance=True AND correction_attempted=True  → item_zero    (개별 항목)
    # recommended_override 필드를 우선 소비하고, incorrect_items 가 대분류 전체를 덮으면
    # category_zero 로 승격.
    uncorrected_handled = False
    if av.get("has_incorrect_guidance"):
        incorrect_items = set(av.get("incorrect_items") or [])
        work_accuracy_items = set(_CATEGORY_ITEMS["work_accuracy"])
        recommended = av.get("recommended_override", "none")

        action: OverrideAction | None = None
        affected: list[int] = []

        if recommended == "category_zero" or (
            incorrect_items and incorrect_items >= work_accuracy_items
        ):
            # 업무정확도 대분류 전체 0점
            action = "category_zero"
            affected = list(work_accuracy_items)
        elif recommended == "item_zero" and incorrect_items:
            # 개별 항목만 0점
            action = "item_zero"
            affected = sorted(incorrect_items)
        elif not av.get("correction_attempted", True):
            # 폴백: recommended_override 가 비어있지만 미정정 시 카테고리 전체 0점
            # (오래된 accuracy_verdict payload 호환)
            action = "category_zero"
            affected = list(work_accuracy_items)

        if action:
            reasons.append(_build_reason(
                trigger="uncorrected_misinfo",
                action=action,
                affected_items=sorted(affected),
                reason_text=(
                    f"오안내 (severity={av.get('severity', 'unknown')}, "
                    f"correction_attempted={av.get('correction_attempted', False)}) — "
                    f"업무정확도 {action} 처리"
                ),
                evidence=_evidence_from_verdict(av),
                source="rule",
            ))
            items_modified |= set(affected)
            _force_zero_items(category_scores, affected)
            uncorrected_handled = True
    if hint_uncorrected and not uncorrected_handled:
        # accuracy_verdict 가 비어있거나 has_incorrect_guidance=False 면서 Sub Agent hint 만 있는 경우 —
        # 해당 item 만 0 점 처리 (item_zero). accuracy_verdict 가 이미 있으면 보조 확인만.
        affected_item_nums = sorted(
            {int(h.get("item_number", 0)) for h in hint_uncorrected if h.get("item_number")}
        )
        if affected_item_nums:
            reasons.append(_build_reason(
                trigger="uncorrected_misinfo",
                action="item_zero",
                affected_items=affected_item_nums,
                reason_text=(
                    "Sub Agent 오안내 미정정 감지 (Layer 2 accuracy_verdict 미확정 보강) — "
                    f"해당 item {affected_item_nums} 0점 처리"
                ),
                evidence=[],
                source="sub_agent_hint",
            ))
            items_modified |= set(affected_item_nums)
            _force_zero_items(category_scores, affected_item_nums)

    applied = len(reasons) > 0

    # --- (4) after_overrides 재계산 ---
    after_total = sum(
        int(item.get("score", 0) or 0)
        for cat in category_scores
        for item in cat.get("items", [])
    )

    logger.info(
        "apply_overrides: applied=%s reasons=%d raw=%d → after=%d items_modified=%d",
        applied, len(reasons), raw_total, after_total, len(items_modified),
    )

    return {
        "applied": applied,
        "reasons": reasons,
        "after_overrides": after_total,
        "items_modified": sorted(items_modified),
        "category_scores": category_scores,
    }


# ===========================================================================
# Score 강제 0 처리 (in-place)
# ===========================================================================


def _force_zero_all(category_scores: list[dict[str, Any]]) -> None:
    """모든 item score 를 0 으로 강제. achieved_score 도 재계산."""
    for cat in category_scores:
        for item in cat.get("items", []):
            _zero_item(item)
        cat["achieved_score"] = 0


def _force_zero_items(category_scores: list[dict[str, Any]], item_numbers: list[int]) -> None:
    """지정된 item_number 만 0 으로 강제."""
    target = set(item_numbers)
    for cat in category_scores:
        changed = False
        for item in cat.get("items", []):
            if item.get("item_number") in target:
                _zero_item(item)
                changed = True
        if changed:
            cat["achieved_score"] = sum(
                int(i.get("score", 0) or 0) for i in cat.get("items", [])
            )


def _zero_item(item: dict[str, Any]) -> None:
    """item 의 score 를 0 으로 강제하고 메타 표시."""
    original = int(item.get("score", 0) or 0)
    item["_original_score"] = original
    item["score"] = 0
    # deductions 누적 (기존 deductions 유지 + override 표시 추가)
    deductions = item.get("deductions") or []
    if original > 0:
        deductions.append({
            "reason": "[OVERRIDE] Layer 3 강제 0점 처리",
            "points": original,
            "override": True,
        })
    item["deductions"] = deductions


# ===========================================================================
# OverrideEntry 빌더 — Dev5 OverridesBlock 호환
# ===========================================================================


def _build_reason(
    *,
    trigger: OverrideTrigger,
    action: OverrideAction,
    affected_items: list[int],
    reason_text: str,
    evidence: list[dict[str, Any]],
    source: str = "rule",
) -> dict[str, Any]:
    """Dev5 `OverrideEntry` 호환 dict 생성.

    source: "rule" (Layer 1 Rule 트리거) | "sub_agent_hint" (Sub Agent LLM 판정 보강).
    기본 "rule" — Layer 1 preprocessing 경로 기존 동작 유지.
    """
    return {
        "trigger": trigger,
        "action": action,
        "affected_items": affected_items,
        "reason": reason_text,
        "evidence": evidence,
        "source": source,
    }


def _dominant_unfriendly_trigger(details: list[dict]) -> OverrideTrigger:
    """가장 많이 탐지된 불친절 유형을 대표 trigger 로 선택 (없으면 profanity)."""
    counts: dict[str, int] = {}
    for d in details:
        t = d.get("trigger_type")
        if t in ("profanity", "contempt", "arbitrary_disconnect"):
            counts[t] = counts.get(t, 0) + 1
    if not counts:
        return "profanity"
    return max(counts, key=counts.get)  # type: ignore[arg-type,return-value]


def _details_to_evidence(details: list[dict], *, limit: int) -> list[dict[str, Any]]:
    """DeductionTriggerDetail → EvidenceQuote-like list."""
    out: list[dict[str, Any]] = []
    for d in details[:limit]:
        out.append({
            "speaker": "agent",
            "timestamp": "",
            "quote": d.get("evidence_text", ""),
            "turn_id": d.get("turn_id", 0),
        })
    return out


def _evidence_from_verdict(av: dict[str, Any]) -> list[dict[str, Any]]:
    """accuracy_verdict 의 evidence_turn_ids 를 EvidenceQuote-like 로 변환."""
    out: list[dict[str, Any]] = []
    for tid in (av.get("evidence_turn_ids") or [])[:3]:
        out.append({
            "speaker": "agent",
            "timestamp": "",
            "quote": av.get("rationale", ""),
            "turn_id": int(tid) if isinstance(tid, int) else 0,
        })
    return out


def _all_item_numbers() -> list[int]:
    """CATEGORY_META 전체 item_number (1~18)."""
    return sorted({n for meta in CATEGORY_META.values() for n in meta["items"]})
