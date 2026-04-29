# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""KSQI 규칙 기반 노드 4개 — 키워드/패턴 매칭으로 즉시 판정.

#1 맞이인사 구성요소  (10점)
#2 단답형 응대        (5점)
#6 종료인사 구성요소  (10점)
#7 답례표현           (10점)

각 노드는 transcript 를 받아 결함 여부 판정 후 ksqi_evaluations 에 1건 append.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .ksqi_rules import get_rule

logger = logging.getLogger(__name__)


# ===========================================================================
# 공통 유틸
# ===========================================================================

# 화자 마커 — agent (상담사) 발화만 추출
_AGENT_MARKERS = ("상담사:", "상담원:", "AGENT:", "agent:")
_CUSTOMER_MARKERS = ("고객:", "CUSTOMER:", "customer:")


def _split_turns(transcript: str) -> list[tuple[str, str]]:
    """줄 단위 (speaker, text) 튜플 리스트."""
    out: list[tuple[str, str]] = []
    for raw in (transcript or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        speaker = "etc"
        text = line
        for m in _AGENT_MARKERS:
            if line.startswith(m):
                speaker = "agent"
                text = line[len(m):].strip()
                break
        else:
            for m in _CUSTOMER_MARKERS:
                if line.startswith(m):
                    speaker = "customer"
                    text = line[len(m):].strip()
                    break
        out.append((speaker, text))
    return out


def _agent_turns(transcript: str) -> list[str]:
    return [t for s, t in _split_turns(transcript) if s == "agent"]


def _make_eval(
    item_number: int,
    *,
    defect: bool,
    rationale: str,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rule = get_rule(item_number)
    score = 0 if defect else rule["max_score"]
    return {
        "item_number": item_number,
        "item_name": rule["item_name"],
        "area": rule["area"],
        "max_score": rule["max_score"],
        "score": score,
        "defect": defect,
        "evidence": evidence or [],
        "rationale": rationale,
    }


# ===========================================================================
# #1 맞이인사 구성요소 — 첫 agent 턴에서 4요소 검사
# ===========================================================================

# 첫인사 키워드
_FIRST_GREETING = ("안녕", "반갑", "여보세요", "감사합니다")
# 소속 키워드 (회사/센터 명시) — 단어 패턴 보조 사용
_AFFILIATION_PATTERNS = (
    r"(?:고객\s*센터|상담\s*센터|콜\s*센터|센터)",
    r"(?:[가-힣A-Za-z]+\s*텔레콤)",
    r"(?:[가-힣A-Za-z]+\s*은행)",
    r"(?:[가-힣A-Za-z]+\s*카드)",
    r"(?:[가-힣A-Za-z]+\s*보험)",
    r"(?:[가-힣A-Za-z]+\s*그룹)",
    r"(?:코오롱|cartgolf|kolon)",  # 샘플 사이트
)
# 이름 키워드 — "***입니다" / "OOO 상담사" / "OOO 입니다" 패턴
_NAME_PATTERNS = (
    r"[가-힣]{2,4}\s*입니다",
    r"[가-힣]{2,4}\s*(?:상담사|상담원)\s*(?:입니다)?",
    r"상담사\s*[가-힣]{2,4}",
)
# 용무 문의 — 어떻게/무엇/도와드릴까요/문의/말씀
_INQUIRY = ("도와드릴", "도와 드릴", "무엇을", "어떻게 도와", "문의", "어떤 일", "말씀해", "용무")


def ksqi_greeting_open_node(state: dict[str, Any]) -> dict[str, Any]:
    transcript = state.get("transcript") or ""
    agents = _agent_turns(transcript)
    # 도입부 — 첫 5턴까지 검사 범위로 (인사가 흩어진 경우 호환)
    head = " ".join(agents[:5]) if agents else ""

    has_greeting = any(k in head for k in _FIRST_GREETING)
    has_affiliation = any(re.search(p, head) for p in _AFFILIATION_PATTERNS)
    has_name = any(re.search(p, head) for p in _NAME_PATTERNS)
    has_inquiry = any(k in head for k in _INQUIRY)

    missing: list[str] = []
    if not has_greeting:
        missing.append("첫인사")
    if not has_affiliation:
        missing.append("소속")
    if not has_name:
        missing.append("이름")
    if not has_inquiry:
        missing.append("용무문의")

    defect = bool(missing)
    rationale = (
        f"4요소 모두 포함 (첫인사·소속·이름·용무문의)"
        if not defect
        else f"누락: {', '.join(missing)}"
    )
    evidence = [{"turn": 1, "text": head[:180]}] if head else []
    return {"ksqi_evaluations": [_make_eval(1, defect=defect, rationale=rationale, evidence=evidence)]}


# ===========================================================================
# #2 단답형 응대 — agent 발화 중 단답형 비율
# ===========================================================================

_TERSE_PATTERNS = (
    r"^네\.?$",
    r"^네네\.?$",
    r"^예\.?$",
    r"^맞아요\.?$",
    r"^맞습니다\.?$",
    r"^아니요\.?$",
    r"^그렇죠\.?$",
)
_TERSE_RE = re.compile("|".join(_TERSE_PATTERNS))


def ksqi_terse_response_node(state: dict[str, Any]) -> dict[str, Any]:
    transcript = state.get("transcript") or ""
    agents = _agent_turns(transcript)
    if not agents:
        return {"ksqi_evaluations": [_make_eval(2, defect=False, rationale="agent 발화 없음 — 평가 보류")]}

    # 단답형 비율 — 도입부/마무리 인사 제외 (앞 2턴, 뒤 2턴 제외)
    middle = agents[2:-2] if len(agents) > 4 else agents
    terse_count = sum(1 for t in middle if _TERSE_RE.match(t.strip()))
    total = len(middle) or 1
    ratio = terse_count / total

    # 결함 기준: 중간부 단답형 비율 30% 이상 OR 연속 3턴 이상 단답형
    consecutive = 0
    max_consecutive = 0
    for t in middle:
        if _TERSE_RE.match(t.strip()):
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
        else:
            consecutive = 0

    defect = ratio >= 0.30 or max_consecutive >= 3
    rationale = (
        f"단답형 비율 {terse_count}/{total} ({ratio*100:.0f}%) · 연속 최대 {max_consecutive}턴"
        + (" → 결함" if defect else " → 정상")
    )
    return {"ksqi_evaluations": [_make_eval(2, defect=defect, rationale=rationale)]}


# ===========================================================================
# #6 종료인사 구성요소 — 마지막 agent 턴에서 종료인사 + 이름
# ===========================================================================

_CLOSING_KEYWORDS = (
    "감사합니다",
    "안녕히",
    "좋은 하루",
    "행복한 하루",
    "수고하세요",
    "끊겠습니다",
    "이용해 주셔서",
    "전화 주셔서",
    "문의 감사",
    "이상입니다",
)


def ksqi_greeting_close_node(state: dict[str, Any]) -> dict[str, Any]:
    transcript = state.get("transcript") or ""
    agents = _agent_turns(transcript)
    tail = " ".join(agents[-5:]) if agents else ""

    has_closing = any(k in tail for k in _CLOSING_KEYWORDS)
    has_name = any(re.search(p, tail) for p in _NAME_PATTERNS)

    missing: list[str] = []
    if not has_closing:
        missing.append("종료인사")
    if not has_name:
        missing.append("이름")

    defect = bool(missing)
    rationale = "종료인사·이름 모두 포함" if not defect else f"누락: {', '.join(missing)}"
    evidence = [{"turn": "tail", "text": tail[:180]}] if tail else []
    return {"ksqi_evaluations": [_make_eval(6, defect=defect, rationale=rationale, evidence=evidence)]}


# ===========================================================================
# #7 답례표현 — 고객 인사/감사에 대한 답례
# ===========================================================================

_CUSTOMER_GREETING = ("안녕하세요", "수고하세요", "감사합니다", "수고하십쇼", "고생하세요")
_PROPER_REPLY = ("네~", "네 ~", "안녕하세요", "감사합니다", "반갑습니다", "좋은 하루")
_IMPROPER_REPLY_EXACT = ("네", "예", "여보세요", "말씀하세요", "말씀하시죠")


def ksqi_acknowledgment_node(state: dict[str, Any]) -> dict[str, Any]:
    transcript = state.get("transcript") or ""
    turns = _split_turns(transcript)

    # 고객 인사/감사 발화 → 직후 agent 턴이 적절한 답례인지
    triggers: list[tuple[int, str, str]] = []  # (idx, customer_text, agent_reply)
    for i, (s, t) in enumerate(turns):
        if s != "customer":
            continue
        if not any(k in t for k in _CUSTOMER_GREETING):
            continue
        # 다음 agent 턴 찾기
        for j in range(i + 1, min(i + 4, len(turns))):
            if turns[j][0] == "agent":
                triggers.append((i, t, turns[j][1]))
                break

    if not triggers:
        return {"ksqi_evaluations": [_make_eval(7, defect=False, rationale="고객 인사·감사 트리거 없음 — 평가 보류")]}

    improper: list[dict[str, Any]] = []
    for idx, cust_text, reply in triggers:
        reply_stripped = reply.strip().rstrip("?.,!")
        is_proper = any(p in reply for p in _PROPER_REPLY)
        is_short_improper = reply_stripped in _IMPROPER_REPLY_EXACT
        if is_short_improper or not is_proper:
            improper.append({
                "turn": idx + 1,
                "customer": cust_text[:80],
                "agent_reply": reply[:80],
            })

    defect = bool(improper)
    rationale = (
        f"답례 트리거 {len(triggers)}건 모두 적절"
        if not defect
        else f"부적절 답례 {len(improper)}건 / 트리거 {len(triggers)}건"
    )
    return {
        "ksqi_evaluations": [_make_eval(7, defect=defect, rationale=rationale, evidence=improper)]
    }
