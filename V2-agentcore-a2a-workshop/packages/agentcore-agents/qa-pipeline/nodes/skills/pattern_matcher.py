# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Pattern Matching Skill -- deterministic pattern detection for QA evaluation.

Design principle: "Count what can be counted; only ask the LLM to judge."

This skill provides deterministic verdicts that are invariant to model
randomness.  Every method accepts either raw transcript text or pre-parsed
turn lists and returns a structured ``MatchResult``.

Pattern constants are imported from nodes.skills.constants so there
is exactly one source of truth for each regex set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Import the canonical pattern constants from the shared constants module.
#
# constants.py is the single source of truth for all regex pattern lists.
# ---------------------------------------------------------------------------
from nodes.skills.constants import (
    AFFILIATION_PATTERNS,
    AGENT_NAME_PATTERNS,
    CLOSING_ADDITIONAL_INQUIRY_PATTERNS,
    CLOSING_KEYWORDS,
    CUSHION_WORD_PATTERNS,
    EMPATHY_RESPONSE_PATTERNS,
    FIRST_GREETING_KEYWORDS,
    HOLD_AFTER_PATTERNS,
    HOLD_BEFORE_PATTERNS,
    HOLD_SILENCE_MARKERS,
    INAPPROPRIATE_LANGUAGE_PATTERNS,
    IV_PROCEDURE_PATTERNS,
    MILD_INAPPROPRIATE_PATTERNS,
    PREEMPTIVE_DISCLOSURE_PATTERNS,
    PRIVACY_VIOLATION_PATTERNS,
    PROFANITY_PATTERNS,
    REFUSAL_SITUATION_PATTERNS,
    SIGH_PATTERNS,
    SIMPLE_RESPONSE_PATTERNS,
    SPEECH_OVERLAP_PATTERNS,
    THIRD_PARTY_CONTEXT_PATTERNS,
    THIRD_PARTY_DISCLOSURE_PATTERNS,
)
from typing import Any


# ---------------------------------------------------------------------------
# PII patterns (not owned by any specific node -- defined here)
# ---------------------------------------------------------------------------

PII_PATTERNS: dict[str, str] = {
    "주민번호": r"\d{6}[-]?\d{7}",
    "전화번호": r"\d{3}[-]?\d{3,4}[-]?\d{4}",
    "카드번호": r"\d{4}[-]?\d{4}[-]?\d{4}[-]?\d{4}",
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class MatchResult:
    """Structured result from a pattern-matching operation."""

    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    match_turns: list[int] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Speaker markers — canonical source는 skills/constants.py.
# 이 모듈은 기존 호출자(AGENT_MARKERS / CUSTOMER_MARKERS 이름) 호환용 alias 만 노출.
from nodes.skills.constants import AGENT_SPEAKER_PREFIXES, CUSTOMER_SPEAKER_PREFIXES

AGENT_MARKERS = tuple(AGENT_SPEAKER_PREFIXES)
CUSTOMER_MARKERS = tuple(CUSTOMER_SPEAKER_PREFIXES)


def is_agent(line: str) -> bool:
    """줄이 상담사 발화로 시작하는지 판정. startswith 로 엄격 매칭(인용구 오탐 방지)."""
    lower = line.lower()
    return any(lower.startswith(m) for m in AGENT_MARKERS)


def is_customer(line: str) -> bool:
    """줄이 고객 발화로 시작하는지 판정. startswith 로 엄격 매칭."""
    lower = line.lower()
    return any(lower.startswith(m) for m in CUSTOMER_MARKERS)


def parse_turns(transcript: str) -> list[dict[str, Any]]:
    """Parse raw transcript text into turn dicts.

    출력 필드는 ``turn`` (레거시 호환) 과 ``turn_id`` (dialogue_parser 동일) 양쪽을
    모두 포함한다. 화자 식별은 startswith 기반(엄격)으로 dialogue_parser 와 일치.
    """
    turns: list[dict[str, Any]] = []
    turn_number = 0
    for line in transcript.strip().split("\n"):
        line_stripped = line.strip()
        if not line_stripped:
            continue
        turn_number += 1
        if is_customer(line_stripped):
            speaker = "customer"
        elif is_agent(line_stripped):
            speaker = "agent"
        else:
            speaker = "unknown"
        turns.append(
            {
                "speaker": speaker,
                "text": line_stripped,
                "turn": turn_number,
                "turn_id": turn_number,
            }
        )
    return turns


def detect_agent_patterns(transcript: str, patterns: list[str]) -> list[dict]:
    """상담사 발화에서 지정된 패턴 목록을 탐지한다."""
    findings: list[dict] = []
    lines = transcript.strip().split("\n")
    turn_number = 0
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        turn_number += 1
        if not is_agent(line_stripped):
            continue
        for pattern in patterns:
            if re.search(pattern, line_stripped):
                findings.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                break
    return findings


def detect_customer_patterns(transcript: str, patterns: list[str]) -> list[dict]:
    """고객 발화에서 지정된 패턴 목록을 탐지한다."""
    findings: list[dict] = []
    lines = transcript.strip().split("\n")
    turn_number = 0
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        turn_number += 1
        if not is_customer(line_stripped):
            continue
        for pattern in patterns:
            if re.search(pattern, line_stripped):
                findings.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                break
    return findings


def match_any(text: str, patterns: list[str]) -> list[str]:
    """Return all patterns that match *text* (case-insensitive)."""
    return [p for p in patterns if re.search(p, text, re.IGNORECASE)]


# ---------------------------------------------------------------------------
# PatternMatcher
# ---------------------------------------------------------------------------


class PatternMatcher:
    """Rule-based pattern matching -- the deterministic backbone of QA evaluation.

    All public methods return a ``dict`` whose shape is documented in the
    method docstring.  The return dicts are intentionally plain so they can
    be merged directly into LangGraph state without extra conversion.
    """

    # Expose pattern constants as class attributes for external callers.
    FIRST_GREETING_KEYWORDS = FIRST_GREETING_KEYWORDS
    CLOSING_KEYWORDS = CLOSING_KEYWORDS
    CLOSING_ADDITIONAL_INQUIRY_PATTERNS = CLOSING_ADDITIONAL_INQUIRY_PATTERNS
    AFFILIATION_PATTERNS = AFFILIATION_PATTERNS
    AGENT_NAME_PATTERNS = AGENT_NAME_PATTERNS
    SPEECH_OVERLAP_PATTERNS = SPEECH_OVERLAP_PATTERNS
    EMPATHY_RESPONSE_PATTERNS = EMPATHY_RESPONSE_PATTERNS
    SIMPLE_RESPONSE_PATTERNS = SIMPLE_RESPONSE_PATTERNS
    HOLD_BEFORE_PATTERNS = HOLD_BEFORE_PATTERNS
    HOLD_AFTER_PATTERNS = HOLD_AFTER_PATTERNS
    HOLD_SILENCE_MARKERS = HOLD_SILENCE_MARKERS
    INAPPROPRIATE_LANGUAGE_PATTERNS = INAPPROPRIATE_LANGUAGE_PATTERNS
    PROFANITY_PATTERNS = PROFANITY_PATTERNS
    SIGH_PATTERNS = SIGH_PATTERNS
    MILD_INAPPROPRIATE_PATTERNS = MILD_INAPPROPRIATE_PATTERNS
    CUSHION_WORD_PATTERNS = CUSHION_WORD_PATTERNS
    REFUSAL_SITUATION_PATTERNS = REFUSAL_SITUATION_PATTERNS
    IV_PROCEDURE_PATTERNS = IV_PROCEDURE_PATTERNS
    PREEMPTIVE_DISCLOSURE_PATTERNS = PREEMPTIVE_DISCLOSURE_PATTERNS
    PRIVACY_VIOLATION_PATTERNS = PRIVACY_VIOLATION_PATTERNS
    THIRD_PARTY_DISCLOSURE_PATTERNS = THIRD_PARTY_DISCLOSURE_PATTERNS
    THIRD_PARTY_CONTEXT_PATTERNS = THIRD_PARTY_CONTEXT_PATTERNS
    PII_PATTERNS = PII_PATTERNS

    # -----------------------------------------------------------------
    # 1. Greeting matching
    # -----------------------------------------------------------------

    def match_greeting(
        self,
        turns: list[dict[str, Any]],
        *,
        first_n: int = 5,
    ) -> dict[str, Any]:
        """Detect opening-greeting elements from the first *first_n* turns.

        Returns::

            {
                "greeting_found": bool,
                "affiliation_found": bool,
                "agent_name_found": bool,
                "elements": {"greeting": bool, "affiliation": bool, "agent_name": bool},
                "score": int,          # 5 / 3 / 0
                "detected_keywords": [str, ...],
                "greeting_turn": int | None,
                "greeting_text": str,
            }
        """
        first_turns = turns[:first_n]
        greeting_found = False
        greeting_turn = None
        greeting_text = ""
        detected_keywords: list[str] = []
        affiliation_found = False
        agent_name_found = False

        for t in first_turns:
            if t.get("speaker") != "agent":
                continue
            text = t.get("text", "")
            matches = match_any(text, FIRST_GREETING_KEYWORDS)
            if matches and not greeting_found:
                greeting_found = True
                greeting_turn = t.get("turn") or t.get("turn_id")
                greeting_text = text
                detected_keywords = matches
            if match_any(text, AFFILIATION_PATTERNS):
                affiliation_found = True
            if match_any(text, AGENT_NAME_PATTERNS):
                agent_name_found = True

        elements = {
            "greeting": greeting_found,
            "affiliation": affiliation_found,
            "agent_name": agent_name_found,
        }
        missing_count = sum(1 for v in elements.values() if not v)
        if missing_count == 0:
            score = 5
        elif missing_count == 1:
            score = 3
        else:
            score = 0

        return {
            "greeting_found": greeting_found,
            "affiliation_found": affiliation_found,
            "agent_name_found": agent_name_found,
            "elements": elements,
            "score": score,
            "detected_keywords": detected_keywords,
            "greeting_turn": greeting_turn,
            "greeting_text": greeting_text,
        }

    # -----------------------------------------------------------------
    # 2. Closing matching
    # -----------------------------------------------------------------

    def match_closing(
        self,
        turns: list[dict[str, Any]],
        *,
        last_n: int = 10,
    ) -> dict[str, Any]:
        """Detect closing-greeting elements from the last *last_n* turns.

        Returns::

            {
                "closing_found": bool,
                "additional_inquiry": bool,
                "customer_ended_first": bool,
                "agent_name_mentioned": bool,
                "elements": {"additional_inquiry": bool, "closing_greeting": bool, "agent_name": bool},
                "score": int,          # 5 / 3 / 0
                "closing_turn": int | None,
                "closing_text": str,
                "detected_keywords": [str, ...],
            }
        """
        last_turns = turns[-last_n:] if len(turns) >= last_n else turns

        closing_found = False
        closing_turn = None
        closing_text = ""
        detected_keywords: list[str] = []
        additional_inquiry_found = False
        agent_name_mentioned = False
        customer_ended_first = False

        # Check who spoke last.
        for t in reversed(last_turns):
            sp = t.get("speaker")
            if sp in ("customer", "agent"):
                customer_ended_first = sp == "customer"
                break

        for t in last_turns:
            if t.get("speaker") != "agent":
                continue
            text = t.get("text", "")

            if match_any(text, CLOSING_ADDITIONAL_INQUIRY_PATTERNS):
                additional_inquiry_found = True

            kw = match_any(text, CLOSING_KEYWORDS)
            if kw:
                closing_found = True
                closing_turn = t.get("turn") or t.get("turn_id")
                closing_text = text
                detected_keywords = kw

            if re.search(r"(이였습니다|이었습니다|였습니다|이, ?였습니다)", text):
                agent_name_mentioned = True

        elements = {
            "additional_inquiry": additional_inquiry_found,
            "closing_greeting": closing_found,
            "agent_name": agent_name_mentioned,
        }
        missing_count = sum(1 for v in elements.values() if not v)
        if missing_count == 0:
            score = 5
        elif missing_count == 1:
            score = 3
        else:
            score = 0

        return {
            "closing_found": closing_found,
            "additional_inquiry": additional_inquiry_found,
            "customer_ended_first": customer_ended_first,
            "agent_name_mentioned": agent_name_mentioned,
            "elements": elements,
            "score": score,
            "closing_turn": closing_turn,
            "closing_text": closing_text,
            "detected_keywords": detected_keywords,
        }

    # -----------------------------------------------------------------
    # 3. Empathy counting
    # -----------------------------------------------------------------

    def count_empathy(self, transcript: str) -> dict[str, Any]:
        """Count empathy/rapport expressions in agent speech.

        Returns::

            {
                "count": int,
                "patterns_found": [{"turn": int, "text": str, "pattern": str}, ...],
                "simple_only": bool,
            }
        """
        turns = parse_turns(transcript)
        empathy_found: list[dict[str, Any]] = []
        simple_found: list[dict[str, Any]] = []

        for t in turns:
            if t["speaker"] != "agent":
                continue
            text = t["text"]
            for pattern in EMPATHY_RESPONSE_PATTERNS:
                if re.search(pattern, text):
                    empathy_found.append({"turn": t["turn"], "text": text, "pattern": pattern})
                    break
            for pattern in SIMPLE_RESPONSE_PATTERNS:
                if re.search(pattern, text, re.IGNORECASE):
                    simple_found.append({"turn": t["turn"], "text": text, "pattern": pattern})
                    break

        simple_only = len(empathy_found) == 0 and len(simple_found) > 0

        return {
            "count": len(empathy_found),
            "patterns_found": empathy_found,
            "simple_only": simple_only,
        }

    # -----------------------------------------------------------------
    # 4. Speech overlap detection
    # -----------------------------------------------------------------

    def detect_speech_overlap(self, transcript: str) -> dict[str, Any]:
        """Detect speech-overlap / interruption STT markers.

        Returns::

            {
                "count": int,
                "overlaps": [{"turn": int, "text": str, "pattern": str}, ...],
            }
        """
        overlaps: list[dict[str, Any]] = []
        turn_number = 0
        for line in transcript.strip().split("\n"):
            line_stripped = line.strip()
            if not line_stripped:
                continue
            turn_number += 1
            for pattern in SPEECH_OVERLAP_PATTERNS:
                if re.search(pattern, line_stripped):
                    overlaps.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                    break

        return {"count": len(overlaps), "overlaps": overlaps}

    # -----------------------------------------------------------------
    # 5. Hold-mention detection
    # -----------------------------------------------------------------

    def detect_hold_mentions(self, transcript: str) -> dict[str, Any]:
        """Detect hold/wait guidance patterns (before, after, silence markers).

        Returns::

            {
                "hold_detected": bool,
                "before": [{"turn": int, "text": str, "pattern": str}, ...],
                "after":  [{"turn": int, "text": str, "pattern": str}, ...],
                "silence": [{"turn": int, "text": str, "pattern": str}, ...],
            }
        """
        before: list[dict[str, Any]] = []
        after: list[dict[str, Any]] = []
        silence: list[dict[str, Any]] = []
        turn_number = 0

        for line in transcript.strip().split("\n"):
            line_stripped = line.strip()
            if not line_stripped:
                continue
            turn_number += 1

            for pattern in HOLD_SILENCE_MARKERS:
                if re.search(pattern, line_stripped):
                    silence.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                    break

            if not is_agent(line_stripped):
                continue

            for pattern in HOLD_BEFORE_PATTERNS:
                if re.search(pattern, line_stripped):
                    before.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                    break
            for pattern in HOLD_AFTER_PATTERNS:
                if re.search(pattern, line_stripped):
                    after.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                    break

        hold_detected = len(before) > 0 or len(after) > 0 or len(silence) > 0

        return {
            "hold_detected": hold_detected,
            "before": before,
            "after": after,
            "silence": silence,
        }

    # -----------------------------------------------------------------
    # 6. Inappropriate language detection
    # -----------------------------------------------------------------

    def detect_inappropriate(self, transcript: str) -> dict[str, Any]:
        """Detect inappropriate language, profanity, sighs, and mild issues.

        Returns::

            {
                "profanity": [{"turn": int, "text": str, "pattern": str}, ...],
                "sighs":     [{"turn": int, "text": str, "pattern": str}, ...],
                "language":  [{"turn": int, "text": str, "pattern": str}, ...],
                "mild":      [{"turn": int, "text": str, "pattern": str}, ...],
                "total": int,
            }
        """
        profanity: list[dict[str, Any]] = []
        sighs: list[dict[str, Any]] = []
        language: list[dict[str, Any]] = []
        mild: list[dict[str, Any]] = []
        turn_number = 0

        for line in transcript.strip().split("\n"):
            line_stripped = line.strip()
            if not line_stripped:
                continue
            turn_number += 1
            if not is_agent(line_stripped):
                continue

            for pattern in PROFANITY_PATTERNS:
                if re.search(pattern, line_stripped):
                    profanity.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                    break
            for pattern in SIGH_PATTERNS:
                if re.search(pattern, line_stripped):
                    sighs.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                    break
            for pattern in INAPPROPRIATE_LANGUAGE_PATTERNS:
                if re.search(pattern, line_stripped):
                    language.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                    break
            for pattern in MILD_INAPPROPRIATE_PATTERNS:
                if re.search(pattern, line_stripped):
                    mild.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                    break

        total = len(profanity) + len(sighs) + len(language) + len(mild)

        return {
            "profanity": profanity,
            "sighs": sighs,
            "language": language,
            "mild": mild,
            "total": total,
        }

    # -----------------------------------------------------------------
    # 7. PII detection
    # -----------------------------------------------------------------

    def detect_pii(self, transcript: str) -> dict[str, Any]:
        """Detect PII patterns (resident ID, phone, card numbers) in all speech.

        Returns::

            {
                "patterns_found": [{"turn": int, "text": str, "type": str}, ...],
                "types": [str, ...],
            }
        """
        found: list[dict[str, Any]] = []
        types_seen: set[str] = set()
        turn_number = 0

        for line in transcript.strip().split("\n"):
            line_stripped = line.strip()
            if not line_stripped:
                continue
            turn_number += 1
            for pii_type, pattern in PII_PATTERNS.items():
                if re.search(pattern, line_stripped):
                    found.append({"turn": turn_number, "text": line_stripped, "type": pii_type})
                    types_seen.add(pii_type)

        return {"patterns_found": found, "types": sorted(types_seen)}

    # -----------------------------------------------------------------
    # 8. Identity verification check
    # -----------------------------------------------------------------

    def check_identity_verification(
        self,
        turns: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Check identity-verification procedure and preemptive disclosure.

        Accepts pre-parsed turns (from dialogue_parser or parse_turns).

        Returns::

            {
                "iv_performed": bool,
                "preemptive_found": bool,
                "third_party": bool,
                "iv_details": [{"turn": int, "text": str, "pattern": str}, ...],
                "preemptive_details": [{"turn": int, "text": str, "pattern": str}, ...],
                "third_party_details": [{"turn": int, "text": str, "pattern": str}, ...],
            }
        """
        iv_details: list[dict[str, Any]] = []
        preemptive_details: list[dict[str, Any]] = []
        third_party_ctx: list[dict[str, Any]] = []

        for t in turns:
            turn_id = t.get("turn") or t.get("turn_id", 0)
            text = t.get("text", "")
            speaker = t.get("speaker", "unknown")

            # IV procedure -- agent speech only
            if speaker == "agent":
                for pattern in IV_PROCEDURE_PATTERNS:
                    if re.search(pattern, text):
                        iv_details.append({"turn": turn_id, "text": text, "pattern": pattern})
                        break
                for pattern in PREEMPTIVE_DISCLOSURE_PATTERNS:
                    if re.search(pattern, text):
                        preemptive_details.append({"turn": turn_id, "text": text, "pattern": pattern})
                        break

            # Third-party context -- all speakers
            for pattern in THIRD_PARTY_CONTEXT_PATTERNS:
                if re.search(pattern, text):
                    third_party_ctx.append({"turn": turn_id, "text": text, "pattern": pattern})
                    break

        return {
            "iv_performed": len(iv_details) > 0,
            "preemptive_found": len(preemptive_details) > 0,
            "third_party": len(third_party_ctx) > 0,
            "iv_details": iv_details,
            "preemptive_details": preemptive_details,
            "third_party_details": third_party_ctx,
        }

    # -----------------------------------------------------------------
    # 9. Cushion-word detection
    # -----------------------------------------------------------------

    def detect_cushion_words(self, transcript: str) -> dict[str, Any]:
        """Detect cushion-word usage and refusal situations in agent speech.

        Returns::

            {
                "count": int,
                "patterns_found": [{"turn": int, "text": str, "pattern": str}, ...],
                "refusal_situations": [{"turn": int, "text": str, "pattern": str}, ...],
            }
        """
        cushion_found: list[dict[str, Any]] = []
        refusal_found: list[dict[str, Any]] = []
        turn_number = 0

        for line in transcript.strip().split("\n"):
            line_stripped = line.strip()
            if not line_stripped:
                continue
            turn_number += 1
            if not is_agent(line_stripped):
                continue

            for pattern in CUSHION_WORD_PATTERNS:
                if re.search(pattern, line_stripped):
                    cushion_found.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                    break
            for pattern in REFUSAL_SITUATION_PATTERNS:
                if re.search(pattern, line_stripped):
                    refusal_found.append({"turn": turn_number, "text": line_stripped, "pattern": pattern})
                    break

        return {
            "count": len(cushion_found),
            "patterns_found": cushion_found,
            "refusal_situations": refusal_found,
        }
