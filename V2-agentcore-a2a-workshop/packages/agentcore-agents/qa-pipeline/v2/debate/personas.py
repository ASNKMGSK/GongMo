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
    hitl_cases: list[dict] | None = None,
) -> str:
    """사용자 메시지 — 평가 컨텍스트 + 1차 평가 결과 (3명 모두) + 이전 발언 삽입.

    정책 (2026-04-29 개정): 토론 첫 발언부터 의미 있게 만들기 위해 3 페르소나의
    1차 평가 결과 (점수·판정·감점·근거) 를 모두 노출. 본인 키는 "[당신]" 으로,
    다른 페르소나는 "[페르소나 X]" 로 표기. 본인 의견 + 다른 의견을 동시에 보고
    동의/반박/수정 자유 — Round 1 부터 진짜 토론 양상.

    ★ 2026-04-30: HITL 사례 주입 — 판사가 아닌 페르소나 토론 단계에서 사용 (사용자 정책).
    `hitl_cases` 가 있으면 user message 에 [과거 휴먼 검증 사례] 섹션으로 추가.

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

    # ★ 골든셋 RAG 사례 주입 — 페르소나가 토론 시 사람 검수 정답을 참고할 수 있게.
    # 골든셋은 HITL (사람 검수 누적 데이터) 등 여러 출처를 포함. 각 사례의 source 필드로 출처 식별.
    # ★ 2026-04-30: 자기상담(is_self_match) 사례가 있으면 anchor 룰 강제 발동 — 그 사람 점수를
    # ground truth 로 채택 (debate_rules.md §7). 없으면 기존 약한 참고 톤 유지 (§8).
    if hitl_cases:
        try:
            from v2.hitl.rag_retriever import format_human_cases_for_prompt
            formatted = format_human_cases_for_prompt(hitl_cases)
        except Exception as exc:
            logger.warning("페르소나 골든셋 사례 포맷 실패 — 사례 섹션 생략: %s", exc)
            formatted = ""
        if formatted:
            self_cases = [c for c in hitl_cases if c.get("is_self_match")]
            if self_cases:
                # 가장 최근(confirmed_at 내림차순) 자기상담 사례의 사람 점수를 anchor 로 명시.
                latest_self = max(
                    self_cases, key=lambda c: str(c.get("confirmed_at") or "")
                )
                anchor_score = latest_self.get("human_score")
                # ★ 2026-05-07: anchor 룰 완화 — 점수 수렴은 유지하되 페르소나 차별화 강제.
                # 이전엔 셋 다 똑같은 시작 문구 강제 + 같은 점수 → reasoning 본문 거의 동일.
                # 변경: 시작 문구 자유, 단 본인 페르소나 고유 관점에서만 anchor 동의/수정 사유 작성.
                guidance = (
                    f"\n⚠ **anchor 참고** — 위 사례 중 🔁 동일 상담 표시가 있는 것은 "
                    f"현재 평가 중인 원문 자체의 사람 검수 정답 (휴먼 {anchor_score}점) 입니다. "
                    f"강력한 근거지만 **자동 채택 금지** — 본인 페르소나 정체성으로 동의/수정 판단할 것. "
                    f"score 는 anchor 기준 ±1 단계에서 자유 조정 (±2 이상 차이 시 anchor 로 수렴).\n"
                    f"\n작성 시 주의:\n"
                    f"- 본인 페르소나 정체성 (system prompt 에 정의된 시각) 으로 자연스럽게 reasoning. 강제 템플릿 X.\n"
                    f"- 다른 페르소나 본문을 그대로 복붙 금지 (같은 발화 인용은 OK, 본인 관점 해석으로 다르게 쓸 것).\n"
                    f"- anchor 점수는 메타로 짧게만 (\"anchor {anchor_score}점에 동의\" 정도), 본문은 본인 평가 근거.\n"
                    f"- \"VOTE_FINAL / CONSENSUS / 모든 페르소나 합의\" 같은 메타 발언 금지."
                )
            else:
                guidance = (
                    "\n위 사례들은 유사 상담의 사람 검수 정답입니다 (자기상담 없음 — anchor 강제 미적용). "
                    "본인 페르소나 정체성으로 자율 판단. 다른 페르소나 본문 복붙 금지 (같은 발화 인용 시 본인 관점으로 다르게)."
                )
            parts.append(
                f"\n[골든셋 사례 — 사람 검수 정답 (평가 항목 #{item_number}, 총 {len(hitl_cases)}건)]\n"
                + formatted
                + guidance
                + "\n"
            )

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
