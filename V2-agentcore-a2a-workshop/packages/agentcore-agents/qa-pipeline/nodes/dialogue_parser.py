# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Dialogue Parser — 전사록 전처리 노드.

전사록(transcript) 원문 텍스트를 구조화된 턴(turn) 데이터로 변환하고,
각 평가 에이전트에 필요한 턴 범위를 미리 할당하는 전처리 노드.

**LLM 호출 없음** — 순수 규칙/정규식 기반 처리로 빠르고 결정론적.

처리 흐름:
  1. 턴 파싱: transcript → 화자/텍스트/턴ID 리스트
  2. 구간 분할: 도입부 / 본문 / 종결부 경계 탐지
  3. 화자별 분리: agent 턴 / customer 턴 ID 목록
  4. 턴 페어링: customer 질문 → agent 응답 쌍 매핑
  5. 에이전트별 턴 할당: 각 평가 에이전트가 받을 턴 범위 결정
"""

# =============================================================================
# Dialogue Parser 전처리 노드 (dialogue_parser.py)
# =============================================================================
# 기존 파이프라인에서는 각 eval 노드가 전체 transcript를 받아
# 내부에서 필요 턴을 직접 파싱하는 구조였다.
# 이 전처리 노드를 도입하면:
#   - 파싱 로직 중복 제거 (각 노드에서 반복되던 _parse_turns 호출 해소)
#   - 일관된 턴 ID 부여 (모든 노드가 같은 turn_id 체계 사용)
#   - 각 에이전트에 필요한 턴만 선별 전달 → LLM 토큰 절약
# =============================================================================

from __future__ import annotations

import logging
import re
from typing import Any

from nodes.skills.constants import IV_PROCEDURE_PATTERNS
from state import QAState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수 정의
# ---------------------------------------------------------------------------

# 화자 접두사는 skills/constants.py 의 canonical 정의를 재사용한다.
# (pattern_matcher.py 와 동일 소스 — 마커 불일치 방지)
from nodes.skills.constants import AGENT_SPEAKER_PREFIXES, CUSTOMER_SPEAKER_PREFIXES  # noqa: E402

# 도입부 감지 키워드 (인사, 본인확인, 문의목적)
INTRO_KEYWORDS = [
    r"안녕하세요",
    r"안녕하십니까",
    r"반갑습니다",
    r"감사합니다.*전화",
    r"전화.*감사합니다",
    r"고객센터입니다",
    r"성함",
    r"이름",
    r"생년월일",
    r"본인.*확인",
    r"본인.*맞[으습]",  # ISSUE-E-004: "본인 맞습니까" 변형 매칭
    r"연락처.*(?:어떻게|부탁|확인|말씀|불러)",  # ISSUE-E-006: 본문 "연락처가 이 번호" 누출 차단
    # ISSUE-E-001: 본문 "문의" 단일 매칭 억제 — 도입 컨텍스트 어구로만 허용
    r"어떤.*문의",
    r"어떻게.*문의",
    r"무슨.*문의",
    # ISSUE-E-007: "문의.*주셨" 단독은 본문에서도 매칭 → IV 컨텍스트 한정 (선행 어구 필요)
    r"(?:어떤|무슨|혹시|어떻게).*문의.*(?:주셨|드리|드린)",
    # ISSUE-E-008: r"도와드릴" 단독은 본문 안내에서 빈출 → "무엇을/어떻게" 결합형만 유지
    r"무엇을.*도와드릴",
    r"어떻게.*도와드릴",
    r"어떤.*도움",
    r"무엇을.*도와",
    r"어떻게.*도와",
]

# 종결부 감지 키워드 (추가문의, 마무리 인사)
# ISSUE-E-003: 추가문의/끝인사 두 그룹으로 분리. 추가문의가 closing 시작점으로 더 정확.
ADDITIONAL_INQUIRY_KEYWORDS = [
    r"더\s*궁금",
    r"추가.*문의",
    r"다른.*문의",
    r"더\s*도움",
    r"더\s*필요",
]
FAREWELL_KEYWORDS = [
    r"감사합니다",
    r"좋은\s*하루",
    r"행복한\s*하루",
    r"이?였습니다",
    r"이?었습니다",
    r"수고하세요",
    r"안녕히\s*계세요",
    r"다음에\s*또",
    # ISSUE-E-011: 마무리 인사 시그니처 보강 — 상담사 자기소개형 끝인사 패턴 추가
    r"상담[사원].*(?:이었|였)",
    r"(?:상담|코오롱).*감사합니다",
]
CLOSING_KEYWORDS = ADDITIONAL_INQUIRY_KEYWORDS + FAREWELL_KEYWORDS

# ISSUE-E-005: 개인정보 유출 후보 패턴 — incorrect_check 타겟팅용
# (전체 agent 턴 스캔 대신 본문 중 PII 발화 턴 ± 1 페어로 축소)
PII_PATTERNS = [
    r"\d{2,4}-?\d{3,4}-?\d{4}",  # 전화번호
    r"카드\s*(번호|뒷)",
    r"계좌",
    r"주민(등록)?\s*번호",
    r"생년월일",
    r"주소(가|는|요|부탁)?",  # "주소요", "주소부탁" 등
    r"우편\s*번호",
]

# 에이전트별 턴 할당 규칙 설명
# 각 에이전트가 평가에 필요로 하는 전사록 구간을 정의
AGENT_TURN_RULES: dict[str, str] = {
    "greeting": "도입부 첫 3턴 + 종결부 전체",
    "understanding": "전체 dialogue (턴 페어 기반 분석)",
    "courtesy": "전체 턴 페어 + 상담사 턴 전체 (거절 맥락 복원)",
    "mandatory": "도입부 전체 + 본문 초반 5페어 (고아턴 포함)",
    "scope": "본문 전체 (agent+customer, turn_pairs 메타 포함)",
    "proactiveness": "본문 후반 4/5 + 본문 끝 3턴 + 종결부 (대안제시·사후안내 구간)",
    "work_accuracy": "전체 턴 페어 + 상담사 턴 전체 (고객 질문 맥락 복원)",
    "incorrect_check": "도입부+본문초반 페어(본인확인) + 개인정보 패턴 매칭 턴±1페어(정보보호)",
}


# ---------------------------------------------------------------------------
# (A) 턴 파싱
# ---------------------------------------------------------------------------
# transcript 문자열을 턴 단위로 분리.
# 각 턴에 turn_id(1부터 시작)를 부여하고 화자를 "agent"/"customer"로 정규화.
# ---------------------------------------------------------------------------


def _parse_turns(transcript: str) -> list[dict[str, Any]]:
    """전사록 텍스트를 턴 단위로 파싱한다.

    Parameters
    ----------
    transcript : str
        원문 전사록 텍스트 (줄바꿈으로 턴 구분).

    Returns
    -------
    list[dict]
        각 턴 딕셔너리: {"turn_id": int, "speaker": str, "text": str}
        speaker는 "agent", "customer", "unknown" 중 하나.
    """
    turns: list[dict[str, Any]] = []
    lines = transcript.strip().split("\n")
    turn_id = 0

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        turn_id += 1
        lower = line_stripped.lower()

        # 화자 식별: 접두사가 **줄 시작**에 있는 경우에만 매칭.
        # (substring 매칭이면 agent 발화 중 "고객:" 같은 내용 인용구가 있을 때
        # customer 로 오탐될 수 있어 startswith 로 엄격화.)
        if any(lower.startswith(prefix) for prefix in CUSTOMER_SPEAKER_PREFIXES):
            speaker = "customer"
        elif any(lower.startswith(prefix) for prefix in AGENT_SPEAKER_PREFIXES):
            speaker = "agent"
        else:
            speaker = "unknown"

        turns.append({"turn_id": turn_id, "speaker": speaker, "text": line_stripped})

    return turns


# ---------------------------------------------------------------------------
# (B) 구간 분할
# ---------------------------------------------------------------------------
# 전체 대화를 도입부(intro) / 본문(body) / 종결부(closing) 3구간으로 분류.
# 경험적 규칙 + 키워드 기반 감지를 병행하여 경계를 결정한다.
# ---------------------------------------------------------------------------


def _detect_segments(turns: list[dict[str, Any]]) -> dict[str, list[int]]:
    """전체 대화를 도입부/본문/종결부로 구간 분할한다.

    Parameters
    ----------
    turns : list[dict]
        _parse_turns()의 출력.

    Returns
    -------
    dict
        {"intro": [turn_ids], "body": [turn_ids], "closing": [turn_ids]}

    구간 분할 로직:
    - 도입부: 첫 턴부터 인사/본인확인/문의목적이 완료되는 시점까지.
      전체 턴의 ~25%, 최소 3턴, 최대 12턴. 키워드가 마지막으로 등장하는
      턴 + 1까지를 도입부로 설정.
    - 종결부: 추가문의 확인부터 끝까지.
      전체 턴의 ~25%, 최소 3턴, 최대 10턴. 종결 키워드가 처음
      등장하는 턴부터 마지막 턴까지를 종결부로 설정.
    - 본문: 도입부와 종결부 사이의 모든 턴.
    """
    total = len(turns)
    if total == 0:
        return {"intro": [], "body": [], "closing": []}

    # --- 도입부 경계 탐지 ---
    # 기본 범위: 전체의 25%, 최소 3턴, 최대 12턴
    # (본인확인 구간이 6턴 이후에 위치하는 경우가 67%이므로 확장)
    default_intro_end = max(3, min(12, round(total * 0.25)))

    # 도입부 키워드가 등장하는 마지막 턴 위치 탐색 (스캔 범위: default_intro_end+2와
    # 전체 대화 절반 중 큰 값, 단 최대 30턴 상한 — 고객 장문 설명 케이스 포착)
    # (예: 본인확인이 turn 19/26/29에 위치하는 샘플 존재 — ISSUE-D-001)
    # ISSUE-E-009: 본인확인(iv_hit_last) 매칭 직후 +2턴 hard cutoff 로 INTRO 추가 매칭 차단.
    # 본인확인 종료 후 등장하는 INTRO_KEYWORDS 는 본문 누출 가능성이 높아 무시한다.
    intro_keyword_last = 0
    iv_hit_last = -1  # 미매칭 시 -1 (cutoff 미적용)
    scan_limit_intro = min(max(default_intro_end + 2, total // 2), 30, total)
    for i in range(scan_limit_intro):
        text = turns[i]["text"]
        iv_matched_here = False
        for pattern in IV_PROCEDURE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                iv_hit_last = i
                iv_matched_here = True
                break
        intro_cutoff = iv_hit_last + 2 if iv_hit_last >= 0 else scan_limit_intro
        if i <= intro_cutoff:
            for pattern in INTRO_KEYWORDS:
                if re.search(pattern, text, re.IGNORECASE):
                    intro_keyword_last = i
                    break

    # 도입부 끝 인덱스: 키워드 마지막 위치 + 2 (최소 3, scan 한계까지 허용, 전체 턴 미만)
    # scan_limit_intro 까지 확장 허용해 긴 대화의 IV 15~20 구간 누락 방지 (ISSUE-D-001).
    # FIX-001h: IV_PROCEDURE_PATTERNS 병행 스캔 — intro 키워드가 약한 샘플에서도
    # 본인확인 절차(iv_hit_last)까지 intro 구간에 포함되도록 effective_last 채택.
    effective_last = max(intro_keyword_last, iv_hit_last if iv_hit_last >= 0 else 0)
    intro_end_idx = max(3, min(scan_limit_intro, effective_last + 2))
    intro_end_idx = min(intro_end_idx, total)

    # --- 종결부 경계 탐지 ---
    # 기본 범위: 전체의 25%, 최소 3턴, 최대 10턴
    # (cap 5→10, 비율 0.20→0.25: 추가 문의 구간 커버를 위해 확장,
    # ISSUE-C-003/A-002 — 짧은 샘플에서도 충분한 closing 확보).
    # ISSUE-E-013: 짧은 대화에서도 closing 최소 4턴 확보 (3→4)
    default_closing_len = max(4, min(10, round(total * 0.25)))

    # 종결 키워드 탐색 — ISSUE-E-003: 추가문의 우선, 없으면 끝인사 첫 등장 위치.
    # 추가문의("다른 문의 있으실까요" 등)는 진짜 closing 시작점에 가깝고,
    # 끝인사("감사합니다")는 본문에서도 빈출하므로 뒤쪽 영역에서만 탐색.
    closing_start_idx = total  # 기본: 종결부 없음
    # ISSUE-E-010: scan_start_closing 확대 — 본문 후반(70%) 부터 탐색
    scan_start_closing = max(intro_end_idx + 2, int(total * 0.7))

    # ISSUE-E-012: 추가문의/끝인사 모두 마지막 매칭 그룹 우선 채택.
    def _last_group_start(indices: list[int]) -> int:
        """gap ≤ 2 인 연속 매칭의 마지막 그룹 시작 인덱스."""
        if not indices:
            return -1
        start = indices[-1]
        for j in range(len(indices) - 1, 0, -1):
            if indices[j] - indices[j - 1] <= 2:
                start = indices[j - 1]
            else:
                break
        return start

    add_inquiry_indices: list[int] = []
    farewell_indices: list[int] = []
    for i in range(scan_start_closing, total):
        text = turns[i]["text"]
        for pattern in ADDITIONAL_INQUIRY_KEYWORDS:
            if re.search(pattern, text, re.IGNORECASE):
                add_inquiry_indices.append(i)
                break
        for pattern in FAREWELL_KEYWORDS:
            if re.search(pattern, text, re.IGNORECASE):
                farewell_indices.append(i)
                break

    add_inq_start = _last_group_start(add_inquiry_indices)
    farewell_start = _last_group_start(farewell_indices)

    candidates = [c for c in (add_inq_start, farewell_start) if c >= 0]
    if candidates:
        closing_start_idx = max(candidates)

    # ISSUE-E-003: 둘 다 미탐지 시 보수적 보정 (마지막 3턴만, 본문 말미 흡수 최소화)
    if closing_start_idx >= total:
        conservative_min = max(intro_end_idx + 2, total - 3)
        closing_start_idx = min(conservative_min, total)

    # 종결부 시작이 도입부 끝보다 앞이면 보정 (도입부+2 이후부터 종결부 시작)
    if closing_start_idx <= intro_end_idx:
        conservative_min = max(intro_end_idx + 2, total - 3)
        closing_start_idx = min(conservative_min, total)

    # ISSUE-E-002: closing 총 10턴 상한 강제 (default_closing_len 초과 금지)
    closing_start_idx = max(closing_start_idx, total - default_closing_len)
    closing_start_idx = min(closing_start_idx, total)

    # --- 구간별 turn_id 리스트 구성 ---
    intro_ids = [turns[i]["turn_id"] for i in range(intro_end_idx)]
    closing_ids = [turns[i]["turn_id"] for i in range(closing_start_idx, total)]
    body_ids = [turns[i]["turn_id"] for i in range(intro_end_idx, closing_start_idx)]

    return {"intro": intro_ids, "body": body_ids, "closing": closing_ids}


# ---------------------------------------------------------------------------
# (C) 화자별 분리
# ---------------------------------------------------------------------------


def _separate_speakers(turns: list[dict[str, Any]]) -> tuple[list[int], list[int]]:
    """화자별로 turn_id를 분리한다.

    Returns
    -------
    tuple[list[int], list[int]]
        (agent_turn_ids, customer_turn_ids)
    """
    agent_ids: list[int] = []
    customer_ids: list[int] = []

    for t in turns:
        if t["speaker"] == "agent":
            agent_ids.append(t["turn_id"])
        elif t["speaker"] == "customer":
            customer_ids.append(t["turn_id"])

    return agent_ids, customer_ids


# ---------------------------------------------------------------------------
# (D) 턴 페어링
# ---------------------------------------------------------------------------
# customer 질문 → agent 응답 쌍을 매핑.
# 연속된 customer 턴이 있으면 마지막 customer 턴을 질문으로 사용.
# ---------------------------------------------------------------------------


def _create_turn_pairs(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """customer 질문 → agent 응답 페어를 생성한다.

    Returns
    -------
    list[dict]
        각 페어: {"pair_id": int, "customer_turn_id": int,
                  "agent_turn_id": int, "customer_text": str,
                  "agent_text": str}
    """
    pairs: list[dict[str, Any]] = []
    pair_id = 0
    pending_customer_turn: dict[str, Any] | None = None

    for t in turns:
        if t["speaker"] == "customer":
            # 연속 customer 턴이면 마지막 것을 유지 (이전 것은 덮어씀)
            pending_customer_turn = t
        elif t["speaker"] == "agent" and pending_customer_turn is not None:
            # customer 질문에 대한 agent 응답 페어 완성
            pair_id += 1
            pairs.append({
                "pair_id": pair_id,
                "customer_turn_id": pending_customer_turn["turn_id"],
                "agent_turn_id": t["turn_id"],
                "customer_text": pending_customer_turn["text"],
                "agent_text": t["text"],
            })
            pending_customer_turn = None

    return pairs


# ---------------------------------------------------------------------------
# 텍스트 조립 헬퍼
# ---------------------------------------------------------------------------


def _assemble_text(turns: list[dict[str, Any]], turn_ids: list[int]) -> str:
    """지정된 turn_id에 해당하는 턴만 골라 텍스트로 재조립한다.

    Parameters
    ----------
    turns : list[dict]
        전체 턴 리스트.
    turn_ids : list[int]
        포함할 turn_id 목록.

    Returns
    -------
    str
        선별된 턴의 텍스트를 줄바꿈으로 연결한 문자열.
    """
    id_set = set(turn_ids)
    selected = [t["text"] for t in turns if t["turn_id"] in id_set]
    return "\n".join(selected)


# ---------------------------------------------------------------------------
# (E) 에이전트별 턴 할당
# ---------------------------------------------------------------------------
# 핵심 함수. 각 평가 에이전트가 받을 턴 범위를 미리 결정한다.
# AGENT_TURN_RULES에 정의된 규칙에 따라 턴 ID, 턴 데이터, 조립 텍스트를 생성.
# ---------------------------------------------------------------------------


def _build_turn_assignments(
    turns: list[dict[str, Any]],
    segments: dict[str, list[int]],
    agent_turn_ids: list[int],
    customer_turn_ids: list[int],
    turn_pairs: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """각 평가 에이전트에 할당할 턴 범위를 결정한다.

    Parameters
    ----------
    turns : list[dict]
        전체 턴 리스트.
    segments : dict
        {"intro": [...], "body": [...], "closing": [...]} 구간별 turn_id 목록.
    agent_turn_ids : list[int]
        상담사 턴 ID 목록.
    customer_turn_ids : list[int]
        고객 턴 ID 목록.
    turn_pairs : list[dict]
        customer→agent 페어 리스트.

    Returns
    -------
    dict[str, dict]
        에이전트별 할당 정보. 키: 에이전트 이름, 값:
        {"description": str, "turn_ids": list[int],
         "turns": list[dict], "text": str}
    """
    intro = segments.get("intro", [])
    body = segments.get("body", [])
    closing = segments.get("closing", [])
    all_ids = [t["turn_id"] for t in turns]

    # segment 매핑: turn_id → "도입"/"본문"/"종결" (ISSUE-A-003/B-005)
    # greeting.py:280 의 `t.get("segment") == "도입"` 필터 등 다운스트림이 참조.
    intro_set = set(intro)
    body_set = set(body)
    closing_set = set(closing)

    def _segment_for(tid: int) -> str:
        if tid in intro_set:
            return "도입"
        if tid in closing_set:
            return "종결"
        if tid in body_set:
            return "본문"
        return ""

    # 턴 ID → 턴 데이터 매핑 (빠른 조회용) — segment 필드를 주입한 복사본 사용
    turn_map = {
        t["turn_id"]: {**t, "segment": _segment_for(t["turn_id"])}
        for t in turns
    }

    def _get_turns_for_ids(ids: list[int]) -> list[dict[str, Any]]:
        """turn_id 목록에 해당하는 턴 데이터를 순서대로 반환 (segment 포함)."""
        return [turn_map[tid] for tid in sorted(ids) if tid in turn_map]

    def _make_assignment(description: str, turn_ids: list[int]) -> dict[str, Any]:
        """에이전트 할당 정보 딕셔너리를 생성."""
        sorted_ids = sorted(set(turn_ids))
        return {
            "description": description,
            "turn_ids": sorted_ids,
            "turns": _get_turns_for_ids(sorted_ids),
            "text": _assemble_text(turns, sorted_ids),
        }

    assignments: dict[str, dict[str, Any]] = {}

    # --- greeting: 도입부 첫 3턴 + 종결부 전체 ---
    # (LIVE-001-A/FIX-001f) closing[-3:]로 축소하면 body 말미~closing 경계에 위치한
    # "추가문의 확인" 멘트를 놓치는 샘플 발생. closing segment 전체를 포함해 L228
    # default_closing_len(max 10) 범위 내 추가문의/끝인사/상담사명 3요소 탐지 커버.
    greeting_ids = intro[:3] + closing
    assignments["greeting"] = _make_assignment(
        AGENT_TURN_RULES["greeting"],
        greeting_ids,
    )

    # --- understanding: 전체 dialogue (턴 페어 기반 분석) ---
    assignments["understanding"] = _make_assignment(
        AGENT_TURN_RULES["understanding"],
        all_ids,
    )

    # --- courtesy: 턴 페어 기반 (거절/불가/양해 맥락 복원) ---
    # ISSUE-D-002: 쿠션어 평가(#7)는 "거절/불가/양해 상황"에서 쿠션어 사용 여부를
    # 판정하는데, 기존 agent 턴만 전달 시 거절을 유발한 직전 customer 턴 맥락이
    # 사라져 LLM 판정이 곤란. work_accuracy ISSUE-C-001 패턴을 재사용해 페어
    # 기반으로 재구성하고, 페어 미형성 agent 턴 커버리지는 합집합으로 보존.
    courtesy_pair_ids: list[int] = []
    for pair in turn_pairs:
        courtesy_pair_ids.append(pair["customer_turn_id"])
        courtesy_pair_ids.append(pair["agent_turn_id"])
    courtesy_ids = courtesy_pair_ids + list(agent_turn_ids)
    courtesy_assignment = _make_assignment(
        AGENT_TURN_RULES["courtesy"],
        courtesy_ids,
    )
    courtesy_assignment["turn_pairs"] = turn_pairs
    assignments["courtesy"] = courtesy_assignment

    # --- mandatory: 도입부 전체 + 본문 초반 5턴 페어 (질문→응답 페어) ---
    # 본문 초반: 본문 시작 후 첫 5개 페어에 포함된 턴 ID (ISSUE-B-001: 상한 3→5).
    body_early_pair_ids: list[int] = []
    pair_count = 0
    for pair in turn_pairs:
        if pair["customer_turn_id"] in body_set or pair["agent_turn_id"] in body_set:
            body_early_pair_ids.append(pair["customer_turn_id"])
            body_early_pair_ids.append(pair["agent_turn_id"])
            pair_count += 1
            if pair_count >= 5:
                break
    # ISSUE-B-002: 본문 첫 agent 턴이 페어를 형성하지 못한 고아 턴인 경우 포함.
    # (customer가 intro 구간에 있고 agent가 body 첫 턴에 있으면 turn_pairs
    # 생성 로직상 페어가 만들어지지만, 그 페어가 body_set 필터에서 누락될 수
    # 있으므로 body 첫 턴이 아직 포함되지 않았다면 명시적으로 추가.)
    if body and body[0] not in body_early_pair_ids:
        body_early_pair_ids.insert(0, body[0])
    mandatory_ids = list(intro) + body_early_pair_ids
    assignments["mandatory"] = _make_assignment(
        AGENT_TURN_RULES["mandatory"],
        mandatory_ids,
    )

    # --- scope: 본문 전체 (agent+customer) ---
    # ISSUE-B-007: turn_pairs 서브셋(body 내 페어)을 메타로 주입하여
    # scope.py 등 다운스트림이 필요 시 페어 구조 활용 가능하도록 함.
    body_pairs = [
        p for p in turn_pairs
        if p["customer_turn_id"] in body_set or p["agent_turn_id"] in body_set
    ]
    scope_assignment = _make_assignment(
        AGENT_TURN_RULES["scope"],
        body,
    )
    scope_assignment["turn_pairs"] = body_pairs
    assignments["scope"] = scope_assignment

    # --- proactiveness: 본문 후반 4/5 + 본문 끝 3턴 + 종결부 ---
    # 적극성 평가 항목(문제해결의지·부연설명·사후안내·FOLLOWUP/ALT)이 body
    # 전반부에도 위치하는 경우가 많아 시작점을 1/3 → 1/4 → 1/5 로 당김
    # (ISSUE-C-002: intro 확장(D-001)으로 body 절대 인덱스가 뒤로 밀려 1/4
    # 시작점이 원하는 구간보다 뒤쪽에 놓이는 샘플이 생김 — 1/5 로 추가 완화).
    # 또한 closing 경계 직전 body 끝 3턴도 포함해 사후안내 누락 방지 (ISSUE-C-003).
    body_fifth = len(body) // 5
    body_latter = body[body_fifth:]
    proactiveness_ids = body_latter + body[-3:] + list(closing)
    assignments["proactiveness"] = _make_assignment(
        AGENT_TURN_RULES["proactiveness"],
        proactiveness_ids,
    )

    # --- work_accuracy: 턴 페어 기반 (고객 질문 + 직전/직후 상담사 응답) ---
    # courtesy 와 100% 동일하던 agent_turn_ids 할당을 페어 기반으로 재설계
    # 하여 업무 정확성 평가가 고객 질문 맥락과 함께 이루어지도록 함
    # (ISSUE-C-001). 페어가 비어있는 초기 샘플 대비 agent 턴을 합집합으로
    # 유지해 커버리지 보존.
    work_accuracy_pair_ids: list[int] = []
    for pair in turn_pairs:
        work_accuracy_pair_ids.append(pair["customer_turn_id"])
        work_accuracy_pair_ids.append(pair["agent_turn_id"])
    work_accuracy_ids = work_accuracy_pair_ids + list(agent_turn_ids)
    assignments["work_accuracy"] = _make_assignment(
        AGENT_TURN_RULES["work_accuracy"],
        work_accuracy_ids,
    )

    # --- incorrect_check: #17 본인확인(intro+본문초반) + #18 정보보호(PII 타겟팅) ---
    # ISSUE-E-005: 전체 스캔→PII 패턴 타겟팅으로 축소
    # 기존: agent 턴 전체 + closing 전체 합집합 (전체의 50~70% 광범위)
    # 신규: 본인확인 영역 + 개인정보 패턴 매칭 턴 ± 1 페어
    iv_region = sorted(set(list(intro) + body_early_pair_ids))

    # 개인정보 언급 턴 탐지 (전체 스캔)
    pii_turn_ids: list[int] = []
    for t in turns:
        for pat in PII_PATTERNS:
            if re.search(pat, t["text"], re.IGNORECASE):
                pii_turn_ids.append(t["turn_id"])
                break

    # PII 턴 ± 1 페어 확장 (페어 매핑 빌드 후 짝 추가)
    pii_expanded: set[int] = set(pii_turn_ids)
    pair_by_customer = {p["customer_turn_id"]: p for p in turn_pairs}
    pair_by_agent = {p["agent_turn_id"]: p for p in turn_pairs}
    for tid in pii_turn_ids:
        if tid in pair_by_customer:
            pii_expanded.add(pair_by_customer[tid]["agent_turn_id"])
        if tid in pair_by_agent:
            pii_expanded.add(pair_by_agent[tid]["customer_turn_id"])

    incorrect_check_ids = sorted(set(iv_region) | pii_expanded)
    assignments["incorrect_check"] = _make_assignment(
        AGENT_TURN_RULES["incorrect_check"],
        incorrect_check_ids,
    )

    return assignments


# ---------------------------------------------------------------------------
# 메인 노드 함수
# ---------------------------------------------------------------------------


def dialogue_parser_node(state: QAState) -> dict[str, Any]:
    """전사록을 구조화된 턴으로 파싱하고 에이전트별 턴 할당을 생성한다.

    LLM 호출 없음 — 순수 규칙/정규식 기반 전처리.

    Parameters
    ----------
    state : QAState
        transcript 필드를 포함하는 파이프라인 상태.

    Returns
    -------
    dict
        {"parsed_dialogue": dict, "agent_turn_assignments": dict}
        - parsed_dialogue: 턴 목록, 구간, 화자 분리, 턴 페어
        - agent_turn_assignments: 에이전트별 할당 턴 범위와 텍스트
    """
    transcript = state.get("transcript", "")

    empty_result: dict[str, Any] = {
        "turns": [],
        "segments": {"intro": [], "body": [], "closing": []},
        "agent_turns": [],
        "customer_turns": [],
        "turn_pairs": [],
    }

    if not transcript:
        logger.warning("dialogue_parser_node: 전사록이 비어있습니다.")
        return {
            "parsed_dialogue": empty_result,
            "agent_turn_assignments": {},
        }

    try:
        # (A) 턴 파싱
        turns = _parse_turns(transcript)
        logger.info(f"dialogue_parser: {len(turns)}개 턴 파싱 완료")

        # (B) 구간 분할
        segments = _detect_segments(turns)

        # 각 턴에 segment 필드 주입 (ISSUE-A-003 / B-005)
        _intro_set = set(segments.get("intro", []))
        _body_set = set(segments.get("body", []))
        _closing_set = set(segments.get("closing", []))
        for t in turns:
            tid = t.get("turn_id")
            if tid in _intro_set:
                t["segment"] = "도입"
            elif tid in _closing_set:
                t["segment"] = "종결"
            elif tid in _body_set:
                t["segment"] = "본문"
            else:
                t["segment"] = ""
        logger.info(
            f"dialogue_parser: 구간 분할 — "
            f"도입부 {len(segments['intro'])}턴, "
            f"본문 {len(segments['body'])}턴, "
            f"종결부 {len(segments['closing'])}턴"
        )

        # (C) 화자별 분리
        agent_turn_ids, customer_turn_ids = _separate_speakers(turns)
        logger.info(f"dialogue_parser: 상담사 {len(agent_turn_ids)}턴, 고객 {len(customer_turn_ids)}턴")

        # (D) 턴 페어링
        turn_pairs = _create_turn_pairs(turns)
        logger.info(f"dialogue_parser: {len(turn_pairs)}개 질문→응답 페어 생성")

        # (E) 에이전트별 턴 할당
        agent_turn_assignments = _build_turn_assignments(
            turns, segments, agent_turn_ids, customer_turn_ids, turn_pairs
        )
        logger.info(
            f"dialogue_parser: 에이전트별 턴 할당 완료 — "
            + ", ".join(f"{k}:{len(v['turn_ids'])}턴" for k, v in agent_turn_assignments.items())
        )

        # parsed_dialogue: 모든 전처리 결과를 하나의 딕셔너리로 묶어 반환
        parsed_dialogue: dict[str, Any] = {
            "turns": turns,
            "segments": segments,
            "agent_turns": agent_turn_ids,
            "customer_turns": customer_turn_ids,
            "turn_pairs": turn_pairs,
        }

        return {
            "parsed_dialogue": parsed_dialogue,
            "agent_turn_assignments": agent_turn_assignments,
        }

    except Exception:
        logger.exception("dialogue_parser_node: 전처리 중 예외 발생")
        return {
            "parsed_dialogue": empty_result,
            "agent_turn_assignments": {},
            "error": "dialogue_parser failed — downstream nodes will receive empty data",
        }
