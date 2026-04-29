# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""페르소나 프롬프트 로더 — 모든 프롬프트를 외부 MD 파일에서 관리.

prompts/ 폴더 구조:
  persona_a.md      — VOC 품격 평가자 (key: strict)
  persona_b.md      — 정확성 평가자 (key: neutral)
  persona_c.md      — 고객경험 평가자 (key: loose)
  debate_rules.md   — 토론 모드 규칙 (모든 페르소나에 자동 append)

프롬프트 수정 시 MD 파일만 편집하면 코드 변경 없이 반영됨.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_PERSONA_FILE_MAP: dict[str, str] = {
    "strict": "persona_a.md",
    "neutral": "persona_b.md",
    "loose": "persona_c.md",
}


def _load_prompt(filename: str) -> str:
    """prompts/ 폴더에서 MD 파일 로드. 실패 시 빈 문자열 + 경고."""
    path = _PROMPTS_DIR / filename
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        logger.warning("프롬프트 파일 로드 실패: %s", path)
        return ""


def _load_all_personas() -> dict[str, str]:
    """페르소나 MD 파일 전부 로드."""
    result: dict[str, str] = {}
    for key, filename in _PERSONA_FILE_MAP.items():
        content = _load_prompt(filename)
        if content:
            result[key] = content
        else:
            logger.warning("페르소나 %s (%s) 로드 실패 — 기본 프롬프트 사용", key, filename)
            result[key] = f"[평가자 페르소나 — {key}]\n기본 평가 기준에 따라 판단한다.\n"
    return result


def _load_debate_rules() -> str:
    """토론 규칙 MD 파일 로드."""
    content = _load_prompt("debate_rules.md")
    if content:
        return "\n\n" + content
    return ""


PERSONA_PROMPTS: dict[str, str] = _load_all_personas()

DEBATE_RULES: str = _load_debate_rules()

# auto 모드에서는 별도 모더레이터 에이전트 불필요.
MODERATOR_SYSTEM = ""

PERSONA_LABELS: dict[str, str] = {"strict": "품격", "neutral": "정확성", "loose": "고객경험"}

PERSONA_ORDER: tuple[str, ...] = ("strict", "neutral", "loose")


def build_persona_system_prompt(persona: str, allowed_steps: list[int]) -> str:
    """페르소나 MD 프롬프트 + 토론 규칙 MD 를 합쳐서 시스템 프롬프트 생성.

    allowed_steps 는 토론 규칙 안의 {allowed_steps} 플레이스홀더에 치환.
    """
    base = PERSONA_PROMPTS.get(persona, PERSONA_PROMPTS.get("neutral", ""))
    rules = DEBATE_RULES.replace("{allowed_steps}", str(allowed_steps))
    return base + rules


def build_speak_user_message(
    *,
    item_name: str,
    item_number: int,
    max_score: int,
    transcript: str,
    rag_context: str | None,
    ai_evidence: list[dict] | None,
    ai_judgment: str | None,
    prev_turns: list[dict],
    persona: str | None = None,
    persona_details: dict[str, dict] | None = None,
) -> str:
    """사용자 메시지 — 평가 컨텍스트 + 1차 평가 결과 (3명 모두) + 이전 발언 삽입.

    정책 (2026-04-29 개정): 토론 첫 발언부터 의미 있게 만들기 위해 3 페르소나의
    1차 평가 결과 (점수·판정·감점·근거) 를 모두 노출. 본인 키는 "[당신]" 으로,
    다른 페르소나는 "[페르소나 X]" 로 표기. 본인 의견 + 다른 의견을 동시에 보고
    동의/반박/수정 자유 — Round 1 부터 진짜 토론 양상.

    합쳐진 ``ai_judgment`` (legacy 필드) 는 미주입 — persona_details 가 더 풍부한 정보 제공.
    """
    _ = ai_evidence  # legacy — persona_details 로 대체
    _ = ai_judgment  # legacy — persona_details 로 대체

    parts: list[str] = [
        f"[평가 항목 #{item_number} — {item_name} / 배점 {max_score}]",
        f"\n[상담 원문]\n{transcript.strip()}\n",
    ]
    if rag_context:
        parts.append(f"\n[관련 규정/가이드]\n{rag_context.strip()}\n")

    # 첫 발언이면 모든 페르소나의 1차 평가 결과 노출 — 토론 출발점
    if not prev_turns and isinstance(persona_details, dict) and persona_details:
        parts.append("\n[1차 평가 결과 — 3 페르소나 사전 채점]")

        def _persona_block(pkey: str, pdata: Any) -> list[str]:
            if not isinstance(pdata, dict):
                return []
            tag = "[당신]" if pkey == persona else f"[{PERSONA_LABELS.get(pkey, pkey)}]"
            score = pdata.get("score")
            judgment = str(pdata.get("judgment") or pdata.get("summary") or "").strip()
            block: list[str] = [f"\n{tag} 페르소나={pkey}"]
            if score is not None:
                block.append(f"  점수: {score}")
            if judgment:
                block.append(f"  판정: {judgment[:300]}")
            ded_lines: list[str] = []
            for d in (pdata.get("deductions") or [])[:3]:
                if isinstance(d, dict):
                    reason = str(d.get("reason") or "")[:120]
                    points = d.get("points")
                    if reason:
                        ded_lines.append(f"    - −{points}점 · {reason}")
            if ded_lines:
                block.append("  감점:")
                block.extend(ded_lines)
            ev_lines: list[str] = []
            for e in (pdata.get("evidence") or [])[:2]:
                if isinstance(e, dict):
                    speaker = str(e.get("speaker") or "?")
                    quote = str(e.get("quote") or "")[:120]
                    if quote:
                        ev_lines.append(f"    - [{speaker}] \"{quote}\"")
            if ev_lines:
                block.append("  근거:")
                block.extend(ev_lines)
            return block

        # 본인 → 다른 페르소나 순서 (본인 의견을 가장 먼저 보게)
        seen_keys: set[str] = set()
        if persona and persona in persona_details:
            parts.extend(_persona_block(persona, persona_details[persona]))
            seen_keys.add(persona)
        for pkey in PERSONA_ORDER:
            if pkey in seen_keys:
                continue
            if pkey in persona_details:
                parts.extend(_persona_block(pkey, persona_details[pkey]))
                seen_keys.add(pkey)

        parts.append(
            "\n위 1차 평가 결과를 보고 토론 첫 발언을 작성하세요. "
            "본인 1차 결과 방어 / 다른 페르소나 의견 반박 / 새로 보니 점수 수정 — 자유. "
            "본인 페르소나 시각 (품격/정확성/고객경험) 을 일관되게 유지하되 합리적 근거에는 양보 가능."
        )

    if prev_turns:
        parts.append("\n[이전 발언]")
        for t in prev_turns:
            parts.append(
                f"  - [{t.get('persona_label') or t.get('persona')}] "
                f"score={t.get('score')} — {t.get('reasoning', '')[:200]}"
            )
    elif not persona_details:
        parts.append("\n(첫 발언 — 1차 평가 부재 · 본인 시각으로 채점)")

    parts.append("\n위 규칙에 따라 JSON 한 개로만 응답하시오.")
    return "\n".join(parts)


def reload_prompts() -> None:
    """런타임에 MD 파일 변경 후 재로드. 서버 재시작 없이 프롬프트 반영."""
    global PERSONA_PROMPTS, DEBATE_RULES  # noqa: PLW0603
    PERSONA_PROMPTS = _load_all_personas()
    DEBATE_RULES = _load_debate_rules()
    logger.info("프롬프트 재로드 완료: %d 페르소나, 규칙 %d자", len(PERSONA_PROMPTS), len(DEBATE_RULES))
