# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 1 (d) — 감점 트리거 사전 탐지.

설계서 p10 (d):
    욕설·비하·임의 단선·선언급 패턴 등을 규칙으로 1차 탐지해 별도 채널로
    Orchestrator 에 전달한다.

설계서 p11 "감점 Override":
    - 불친절 (욕설/비하/언쟁/임의 단선) → 전체 평가 0점 + 관리자 통보
    - 개인정보 유출 → 해당 항목 0점 + 별도 보고서 생성
    - 오안내 미정정 → 업무 정확도 대분류 전체 0점  ← Layer 2 work_accuracy 소관
    - STT 품질 저하 → 평가 보류 (Layer 1 quality_gate 소관)

이 모듈은 rule 탐지만 수행 (LLM 보강은 향후 옵션 — 현 단계는 rule only).
V1 `nodes/skills/pattern_matcher.py` 와 `nodes/skills/constants.py` 의 패턴을
import 로 재활용. 출력은 PL 확정 스펙 `{"불친절": bool, "개인정보_유출": bool,
"오안내_미정정": bool}` bool dict + details sibling.
"""

from __future__ import annotations

import logging
import re
from typing import Any

# V1 자산 재활용 — import only (수정 금지)
from nodes.skills.constants import (  # type: ignore[import-untyped]
    INAPPROPRIATE_LANGUAGE_PATTERNS,
    PREEMPTIVE_DISCLOSURE_PATTERNS,
    PRIVACY_VIOLATION_PATTERNS,
    PROFANITY_PATTERNS,
    THIRD_PARTY_DISCLOSURE_PATTERNS,
)

from v2.contracts.preprocessing import (
    DeductionTriggerDetail,
    DeductionTriggerType,
    RecommendedOverride,
    empty_deduction_triggers,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 임의 단선 패턴 (V1 에 미정의 — 자체 추가)
# ---------------------------------------------------------------------------

# 상담사가 대화를 임의로 끊는 패턴 (설계서 p11 "불친절" 의 한 요소)
_ARBITRARY_DISCONNECT_PATTERNS: list[str] = [
    r"끊겠습니다",
    r"통화\s*종료",
    r"안녕히\s*계세요\s*(?:뚝|끊|끊기)",
    r"더\s*이상\s*(할\s*말\s*없|드릴\s*말씀\s*없)",
    r"나중에\s*(전화|연락).*(주세요|하세요)",
    # 이상 종료 패턴 — 상담 완결 없이 중간에 끊는 정황
]


# ---------------------------------------------------------------------------
# 메인 함수
# ---------------------------------------------------------------------------


def detect_triggers(
    turns: list[dict[str, Any]],
) -> dict[str, Any]:
    """감점 트리거 사전 탐지. Rule 기반 only (LLM 보강 미사용).

    Parameters
    ----------
    turns : list[dict]
        segment_splitter 출력의 turns (speaker/text/turn_id/segment).

    Returns
    -------
    dict
        {
          "deduction_triggers": {"불친절": bool, "개인정보_유출": bool, "오안내_미정정": bool},
          "deduction_trigger_details": [DeductionTriggerDetail, ...],
          # Layer 3 에서 사용할 힌트 필드:
          "has_all_zero_trigger": bool,   # 불친절 탐지 시 True
          "has_category_zero_categories": list[str],  # 예: ["개인정보 보호"]
        }

    Notes
    -----
    "오안내_미정정" 은 Layer 1 에서는 사전 탐지 불가 (업무 지식 RAG 필요) —
    Layer 2 work_accuracy Sub Agent 가 accuracy_verdict 를 통해 전달.
    Layer 1 에서는 무조건 False 로 초기화하고 Layer 3 가 업데이트.
    """
    triggers = empty_deduction_triggers()
    details: list[DeductionTriggerDetail] = []

    if not turns:
        return {
            "deduction_triggers": triggers,
            "deduction_trigger_details": details,
            "has_all_zero_trigger": False,
            "has_category_zero_categories": [],
            "recommended_override": "none",
            "triggers": details,  # Dev5 overrides_adapter alias
        }

    # (1) 불친절: profanity + inappropriate_language (상담사 발화 대상)
    unfriendly_details = _detect_unfriendly(turns)
    if unfriendly_details:
        triggers["불친절"] = True
        details.extend(unfriendly_details)

    # (2) 임의 단선 (상담사 발화 대상 — 불친절 bucket 에 귀속)
    disconnect_details = _detect_arbitrary_disconnect(turns)
    if disconnect_details:
        triggers["불친절"] = True
        details.extend(disconnect_details)

    # (3) 개인정보 유출: privacy violation + third party disclosure + 선언급(preemptive)
    privacy_details = _detect_privacy_leak(turns)
    if privacy_details:
        triggers["개인정보_유출"] = True
        details.extend(privacy_details)

    # (4) 오안내 미정정 — Layer 1 미처리 (Layer 2/3 업데이트)
    # triggers["오안내_미정정"] 은 False 유지

    has_all_zero = triggers["불친절"]
    # Dev5 overrides_adapter 요구 — CategoryKey 영문값 ("privacy_protection" 등)
    category_zero: list[str] = []
    if triggers["개인정보_유출"]:
        category_zero.append("privacy_protection")

    # top-level recommended_override (단일값) — Dev5 overrides_adapter 입력
    if has_all_zero:
        top_level_override: str = "all_zero"
    elif category_zero:
        top_level_override = "category_zero"
    elif any(d.get("recommended_override") == "item_zero" for d in details):
        top_level_override = "item_zero"
    else:
        top_level_override = "none"

    logger.info(
        "deduction_trigger_detector: 불친절=%s 개인정보유출=%s 탐지 %d건 recommended=%s",
        triggers["불친절"], triggers["개인정보_유출"], len(details), top_level_override,
    )

    return {
        "deduction_triggers": triggers,
        "deduction_trigger_details": details,
        "has_all_zero_trigger": has_all_zero,
        "has_category_zero_categories": category_zero,
        # Dev5 overrides_adapter.build_overrides_block() 입력 필드
        "recommended_override": top_level_override,
        "triggers": details,  # Dev5 alias (동일 객체 — mutation 없음)
    }


# ---------------------------------------------------------------------------
# 카테고리별 탐지 (상담사 발화 한정)
# ---------------------------------------------------------------------------


def _agent_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """speaker == 'agent' 인 턴만 추출."""
    return [t for t in turns if t.get("speaker") == "agent"]


def _detect_unfriendly(turns: list[dict[str, Any]]) -> list[DeductionTriggerDetail]:
    """욕설 + 부적절 표현 + 비하 탐지. 매칭 시 DeductionTriggerDetail 반환."""
    details: list[DeductionTriggerDetail] = []

    for t in _agent_turns(turns):
        text = t.get("text", "")
        turn_id = t.get("turn_id") or t.get("turn") or 0

        # 욕설
        for pat in PROFANITY_PATTERNS:
            if re.search(pat, text):
                details.append(
                    _build_detail(
                        trigger_type="profanity",
                        turn_id=turn_id,
                        evidence_text=text,
                        pattern_id=pat,
                        confidence=0.90,
                        recommended_override="all_zero",
                    )
                )
                break

        # 부적절 표현 (반말 / 고압적 / 비하)
        for pat in INAPPROPRIATE_LANGUAGE_PATTERNS:
            if re.search(pat, text):
                details.append(
                    _build_detail(
                        trigger_type="contempt",
                        turn_id=turn_id,
                        evidence_text=text,
                        pattern_id=pat,
                        confidence=0.70,
                        recommended_override="all_zero",
                    )
                )
                break

    return details


def _detect_arbitrary_disconnect(
    turns: list[dict[str, Any]],
) -> list[DeductionTriggerDetail]:
    """임의 단선 패턴 탐지."""
    details: list[DeductionTriggerDetail] = []

    for t in _agent_turns(turns):
        text = t.get("text", "")
        turn_id = t.get("turn_id") or t.get("turn") or 0
        for pat in _ARBITRARY_DISCONNECT_PATTERNS:
            if re.search(pat, text):
                details.append(
                    _build_detail(
                        trigger_type="arbitrary_disconnect",
                        turn_id=turn_id,
                        evidence_text=text,
                        pattern_id=pat,
                        confidence=0.65,
                        recommended_override="all_zero",
                    )
                )
                break

    return details


def _detect_privacy_leak(turns: list[dict[str, Any]]) -> list[DeductionTriggerDetail]:
    """개인정보 유출 탐지: preemptive + third party disclosure + privacy violation."""
    details: list[DeductionTriggerDetail] = []

    for t in _agent_turns(turns):
        text = t.get("text", "")
        turn_id = t.get("turn_id") or t.get("turn") or 0

        # 선언급 (본인확인 전 고객정보 먼저 말함)
        for pat in PREEMPTIVE_DISCLOSURE_PATTERNS:
            if re.search(pat, text):
                details.append(
                    _build_detail(
                        trigger_type="preemptive_disclosure",
                        turn_id=turn_id,
                        evidence_text=text,
                        pattern_id=pat,
                        confidence=0.75,
                        recommended_override="item_zero",  # #17 zero
                        item_number=17,
                        category_key="privacy_protection",
                    )
                )
                break

        # 제3자 정보 안내
        for pat in THIRD_PARTY_DISCLOSURE_PATTERNS:
            if re.search(pat, text):
                details.append(
                    _build_detail(
                        trigger_type="privacy_leak",
                        turn_id=turn_id,
                        evidence_text=text,
                        pattern_id=pat,
                        confidence=0.70,
                        recommended_override="category_zero",
                        category_key="privacy_protection",
                    )
                )
                break

        # 개인정보 공개 패턴
        for pat in PRIVACY_VIOLATION_PATTERNS:
            if re.search(pat, text):
                details.append(
                    _build_detail(
                        trigger_type="privacy_leak",
                        turn_id=turn_id,
                        evidence_text=text,
                        pattern_id=pat,
                        confidence=0.80,
                        recommended_override="category_zero",
                        category_key="privacy_protection",
                    )
                )
                break

    return details


# ---------------------------------------------------------------------------
# 공용 헬퍼
# ---------------------------------------------------------------------------


def _build_detail(
    *,
    trigger_type: DeductionTriggerType,
    turn_id: int,
    evidence_text: str,
    pattern_id: str,
    confidence: float,
    recommended_override: RecommendedOverride,
    item_number: int | None = None,
    category_key: str | None = None,
    speaker: str = "상담사",
    timestamp: str = "",
) -> DeductionTriggerDetail:
    """DeductionTriggerDetail TypedDict 생성.

    Dev5 `v2/layer4/overrides_adapter.build_overrides_block()` 가 consume 하는 필드 포함.
    - item_zero 일 때: item_number 필수 (예: #17 선언급).
    - category_zero 일 때: category_key 권장 (예: "privacy_protection").
    """
    detail: DeductionTriggerDetail = {
        "trigger_type": trigger_type,
        "turn_id": turn_id,
        "evidence_text": evidence_text,
        "source": "rule",
        "confidence": confidence,
        "pattern_id": pattern_id,
        "recommended_override": recommended_override,
        "speaker": speaker,
        "timestamp": timestamp,
    }
    if item_number is not None:
        detail["item_number"] = item_number
    if category_key is not None:
        detail["category_key"] = category_key
    return detail
