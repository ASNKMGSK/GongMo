# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 1 (c) — PII 토큰 정규화.

설계서 p10 (c):
    외부에서 들어온 마스킹(*** 또는 [NAME] 등) 을 내부 canonical form 으로 변환.
    이 레이어가 향후 마스킹 포맷 변경 시 유일한 수정 지점이 된다.

PL 확정 R6 PII Forward-compat:
    v1_symbolic (`***`) 와 v2_categorical ([NAME]/[PHONE]/[ADDR]/[CARD]/[DOB]/
    [EMAIL]/[RRN]/[ACCT]/[ORDER]/[OTHER]) 양쪽 입력을 모두 소비 가능.
    masking_format.version 은 자동 감지로 채움 (quality_gate 출력과 교차 확인).
    v1_symbolic 에서도 inferred_category + inference_confidence 를 사전 기록 →
    v2_categorical 전환 시 downstream 변경 없음.

Canonical form:
    [PII_<CATEGORY>_<N>] 형식 (예: [PII_NAME_1], [PII_PHONE_2])
"""

from __future__ import annotations

import logging
import re
from typing import Any

from v2.contracts.preprocessing import MaskingVersion, PIICategory, PIIToken


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 토큰 감지 패턴
# ---------------------------------------------------------------------------

# v1_symbolic — 연속된 asterisk (2개 이상) 를 PII 토큰으로 간주.
# 단일 `*` 은 강조/생략 용으로 오용될 수 있으므로 2개 이상 매칭.
# ○ / × / ? 등의 마스킹 문자도 일부 샘플에 등장.
V1_TOKEN_PATTERN = re.compile(r"(\*{2,}|○{2,}|[Xx]{2,}|\?{2,})")

# v2_categorical — [NAME] / [PHONE] / ... 10 카테고리.
# 상세 스펙 · 심각도 · v1→v2 전환 체크리스트 · 코드상수 정렬 이슈: v2/docs/pii_token_spec.md
V2_CATEGORY_NAMES: tuple[str, ...] = (
    "NAME", "PHONE", "ADDR", "CARD", "DOB", "EMAIL",
    "RRN", "ACCT", "ORDER", "OTHER",
)
V2_TOKEN_PATTERN = re.compile(r"\[(" + "|".join(V2_CATEGORY_NAMES) + r")\]")


# ---------------------------------------------------------------------------
# context heuristic — 앞뒤 문맥으로 카테고리 추정 (v1_symbolic 전용)
# ---------------------------------------------------------------------------
#
# PII 토큰 주변(앞 20자 + 뒤 20자)에 등장하는 키워드로 카테고리 추정.
# V1 nodes/dialogue_parser.py 의 PII_PATTERNS 와 nodes/skills/pattern_matcher.py
# 의 PII_PATTERNS dict 를 참고해 확장.
#
# 우선순위가 높은 순서대로 검사 — 첫 매칭 카테고리를 채택.

_CATEGORY_HINTS: list[tuple[PIICategory, list[str], float]] = [
    # 주민등록번호 — 고유 특성상 신뢰도 가장 높음
    ("RRN",   [r"주민\s*(등록)?\s*번호", r"주민번호"],                               0.85),
    # 전화번호
    ("PHONE", [r"전화", r"연락처", r"핸드폰", r"휴대폰", r"번호\s*(로|가|는|입니다)"], 0.75),
    # 카드번호
    ("CARD",  [r"카드\s*번호", r"카드\s*뒷", r"카드\s*앞", r"신용카드"],              0.80),
    # 계좌
    ("ACCT",  [r"계좌\s*번호", r"계좌", r"입금\s*계좌"],                             0.75),
    # 이메일
    ("EMAIL", [r"이메일", r"메일\s*주소", r"@"],                                    0.80),
    # 생년월일
    ("DOB",   [r"생년월일", r"생일", r"출생"],                                       0.75),
    # 주소
    ("ADDR",  [r"주소", r"거주지", r"우편\s*번호", r"배송지"],                        0.70),
    # 주문
    ("ORDER", [r"주문\s*번호", r"주문", r"운송장", r"송장\s*번호"],                   0.70),
    # 이름 — 가장 약한 힌트 (마지막 검사)
    ("NAME",  [r"성함", r"성명", r"이름", r"고객\s*님", r"님\s*(이|이신가요|맞)"],     0.55),
]

# 문맥 검사 범위 (앞뒤 문자 수)
_CONTEXT_WINDOW_CHARS = 25


# ---------------------------------------------------------------------------
# 메인 함수
# ---------------------------------------------------------------------------


def normalize_pii(
    transcript: str,
    turns: list[dict[str, Any]] | None = None,
    *,
    declared_version: MaskingVersion | None = None,
) -> dict[str, Any]:
    """PII 토큰을 canonical form 으로 정규화하고 token 메타데이터를 추출.

    Parameters
    ----------
    transcript : str
        원본 전사록.
    turns : list[dict] | None
        segment_splitter 출력의 turns (optional). utterance_idx 매핑에 사용.
        None 이면 줄 번호(0-based) 로 utterance_idx 채움.
    declared_version : MaskingVersion | None
        quality_gate 단계에서 선언된 masking version. 본 모듈이 입력을
        자동 감지해 덮어쓸 수 있다 (실제 토큰과 선언 불일치 시 경고).

    Returns
    -------
    dict
        {
          "canonical_transcript": str,       # PII → [PII_<CAT>_<N>] 치환본
          "pii_tokens": list[PIIToken],     # 각 등장 1건 메타
          "masking_format_version": MaskingVersion,  # 자동 감지값
          "total_pii_count": int,
        }
    """
    if not transcript:
        return {
            "canonical_transcript": transcript,
            "pii_tokens": [],
            "masking_format_version": declared_version or "v1_symbolic",
            "total_pii_count": 0,
        }

    # (1) 입력 토큰 형태 자동 감지
    detected_version = _detect_masking_version(transcript)
    if declared_version and declared_version != detected_version:
        logger.warning(
            "pii_normalizer: declared_version=%s but detected=%s — using detected",
            declared_version, detected_version,
        )
    effective_version: MaskingVersion = detected_version

    # (2) 라인별 (= 턴) 토큰 탐지 + canonical 치환
    lines = transcript.split("\n")
    tokens: list[PIIToken] = []
    canonical_lines: list[str] = []
    category_counters: dict[PIICategory, int] = {c: 0 for c in V2_CATEGORY_NAMES}  # type: ignore[assignment]
    category_counters["UNKNOWN"] = 0

    # turn_id(1-based) → utterance_idx(0-based) 매핑
    # turns 가 제공되면 실제 턴 인덱스(빈 줄 제외) 로 매핑, 아니면 line_no 그대로.
    utterance_idx_for_line: dict[int, int] = {}
    if turns:
        # turns 는 빈 줄을 건너뛴 채 turn_id 가 1..N 으로 연속. line_no 0-based.
        turn_id_idx = 0
        for line_no, line in enumerate(lines):
            if line.strip():
                utterance_idx_for_line[line_no] = turn_id_idx
                turn_id_idx += 1

    for line_no, line in enumerate(lines):
        if not line.strip():
            canonical_lines.append(line)
            continue

        utterance_idx = utterance_idx_for_line.get(line_no, line_no)
        rewritten, line_tokens = _rewrite_line(
            line,
            utterance_idx=utterance_idx,
            effective_version=effective_version,
            category_counters=category_counters,
        )
        canonical_lines.append(rewritten)
        tokens.extend(line_tokens)

    canonical_transcript = "\n".join(canonical_lines)

    logger.info(
        "pii_normalizer: version=%s, total_pii=%d (%s)",
        effective_version,
        len(tokens),
        ", ".join(f"{c}:{n}" for c, n in category_counters.items() if n > 0) or "none",
    )

    return {
        "canonical_transcript": canonical_transcript,
        "pii_tokens": tokens,
        "masking_format_version": effective_version,
        "total_pii_count": len(tokens),
    }


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------


def _detect_masking_version(transcript: str) -> MaskingVersion:
    """전사록에 등장하는 토큰 형태로 masking version 자동 감지.

    판정 규칙:
      1. [NAME]/[PHONE]/... 토큰이 1건 이상 등장 → v2_categorical
      2. 그 외(*** 만 등장하거나 둘 다 없음) → v1_symbolic
    """
    if V2_TOKEN_PATTERN.search(transcript):
        return "v2_categorical"
    return "v1_symbolic"


def _rewrite_line(
    line: str,
    *,
    utterance_idx: int,
    effective_version: MaskingVersion,
    category_counters: dict[PIICategory, int],
) -> tuple[str, list[PIIToken]]:
    """한 줄에서 PII 토큰을 찾아 canonical 로 치환하고 PIIToken 리스트 반환."""
    collected: list[PIIToken] = []
    result_parts: list[str] = []
    cursor = 0

    if effective_version == "v2_categorical":
        pattern = V2_TOKEN_PATTERN
        for m in pattern.finditer(line):
            result_parts.append(line[cursor:m.start()])
            category: PIICategory = m.group(1)  # type: ignore[assignment]
            category_counters[category] = category_counters.get(category, 0) + 1
            canonical = f"[PII_{category}_{category_counters[category]}]"
            result_parts.append(canonical)
            collected.append(
                PIIToken(
                    raw=m.group(0),
                    utterance_idx=utterance_idx,
                    canonical_token=canonical,
                    inferred_category=category,
                    inference_confidence=1.0,  # v2_categorical 은 명시적이라 1.0
                    char_start=m.start(),
                    char_end=m.end(),
                )
            )
            cursor = m.end()
        result_parts.append(line[cursor:])
        return ("".join(result_parts), collected)

    # v1_symbolic 경로 — context heuristic 으로 inferred_category 추정
    pattern = V1_TOKEN_PATTERN
    for m in pattern.finditer(line):
        result_parts.append(line[cursor:m.start()])
        inferred_category, confidence = _infer_category_from_context(line, m.start(), m.end())
        category_counters[inferred_category] = category_counters.get(inferred_category, 0) + 1
        canonical = f"[PII_{inferred_category}_{category_counters[inferred_category]}]"
        result_parts.append(canonical)
        collected.append(
            PIIToken(
                raw=m.group(0),
                utterance_idx=utterance_idx,
                canonical_token=canonical,
                inferred_category=inferred_category,
                inference_confidence=confidence,
                char_start=m.start(),
                char_end=m.end(),
            )
        )
        cursor = m.end()
    result_parts.append(line[cursor:])
    return ("".join(result_parts), collected)


def _infer_category_from_context(
    line: str, start: int, end: int,
) -> tuple[PIICategory, float]:
    """토큰 주변 문맥으로 카테고리 추정. 실패 시 UNKNOWN."""
    prefix_start = max(0, start - _CONTEXT_WINDOW_CHARS)
    suffix_end = min(len(line), end + _CONTEXT_WINDOW_CHARS)
    context = line[prefix_start:start] + " " + line[end:suffix_end]

    for category, patterns, confidence in _CATEGORY_HINTS:
        for pat in patterns:
            if re.search(pat, context):
                return (category, confidence)

    return ("UNKNOWN", 0.30)
