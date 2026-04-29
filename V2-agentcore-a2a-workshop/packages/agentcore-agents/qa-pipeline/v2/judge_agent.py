# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""판사형 에이전트 — 3-Persona 의견 충돌 시 숙고로 최종 점수 확정 (Phase 5, 2026-04-21).

**하이브리드 병합 전략**:
- `reconcile_personas()` 로 먼저 통계 머지 수행
- `step_spread >= 2` (3 step 이상 간 의견 충돌) 시 → `deliberate()` 로 판사 호출
- 판사는 3 persona 의 판정 + 근거 + evidence 를 **익명화** (A/B/C) 해서 받고, 자기 편향 배제 후 재판정
- 판사 출력도 `snap_score_v2` 로 강제 + ALLOWED_STEPS 범위 외 시 통계 머지로 fallback

**왜 익명화**: 판사에게 "너(=neutral)의 판정" 을 밝히면 자기 편향 유지 가능성. A/B/C 라벨로 익명화하면
evidence 기반 순수 비교가 가능. 판사 응답 매핑을 위해 내부적으로 (persona↔label) 순서만 기록.

**폴백 체인**:
1. 판사 호출 성공 + 유효 점수 → 채택
2. 판사 JSON 파싱 실패 / 범위 외 점수 → median/mode 통계 머지 결과 사용
3. 판사 LLM 타임아웃 → LLMTimeoutError 상위 전파 (파이프라인 중단 시그널, CLAUDE.md 규약)
"""

from __future__ import annotations

import logging
import random
from typing import Any
from v2.contracts.rubric import ALLOWED_STEPS, snap_score_v2
from v2.reconciler_personas import PERSONAS, reconcile_personas


logger = logging.getLogger(__name__)


# step_spread 가 이 값 이상이면 판사 호출 (아니면 통계 머지 채택)
# 2026-04-21: 2 → 3 상향 — 판사 호출 빈도 절반 이하로 줄여 latency 단축.
# step_spread 2 는 인접 단계 차이라 median/mode 로도 합리적 머지 가능.
DEFAULT_JUDGE_THRESHOLD: int = 3

# 판사 응답 max_tokens — 짧은 결정 + 근거 2~4 문장이면 충분.
# 1024 → 640 축소 (출력 생성 시간 40% 단축).
# 판사는 caller 의 bedrock_model_id (Sonnet 4.6) 를 그대로 사용 — 판정 품질 유지.
JUDGE_MAX_TOKENS: int = 640


JUDGE_SYSTEM_PROMPT = """당신은 콜센터 상담 평가의 **중립 심판관**입니다.
세 명의 동료 평가자 (A, B, C — **익명**) 가 동일한 평가 항목을 독립적으로 평가했고,
점수 차이가 유의미하게 커서 (step_spread ≥ 3) 자동 머지로는 품질 보장이 불가하다고 시스템이 판단해 당신에게 넘겨진 건입니다.
당신의 임무는 세 평가자의 근거를 비판적으로 검토하고 **상담 원문을 1차 근거로 삼아** 최종 점수를 확정하는 것입니다.

===========================================================================
## 1. 입력으로 받는 것 (User Message 구조)
===========================================================================
- `평가 항목 #N · 항목명`
- `허용 점수 단계 (ALLOWED_STEPS)` — 최종 점수는 이 중 하나여야 함
- `상담 원문 발췌` — 해당 항목 판정에 필요한 턴 구간 (~2500자 이내)
- `세 평가자의 독립 판정` — 각 A/B/C 에 대해:
  - `점수`
  - `판단 (judgment)` — 평가자가 쓴 판정 요약
  - `감점 (deductions)` — 감점 사유 + 점수
  - `근거 인용 (evidence)` — STT 원문 quote + 화자
  - `override_hint` — 평가자가 감지한 override 신호 (있으면)

===========================================================================
## 2. 판정 SOP (Standard Operating Procedure) — 반드시 이 순서로
===========================================================================

### Step 1. 원문 1차 정독 (평가자 주장을 보기 전에)
- `상담 원문 발췌` 를 먼저 끝까지 읽습니다. 평가자 A/B/C 의 judgment/evidence 를 보기 전에.
- 이유: 평가자 주장에 휘둘리기 전에 원문의 인상을 체득해야 공정한 판단 가능.

### Step 2. Evidence 실재성 검증 (hallucination 필터)
- 각 평가자의 evidence quote 를 하나씩 **원문 발췌와 대조**.
- 원문에 그 문장이 **실제로 존재하지 않으면** 그 evidence 는 무효.
  - quote 가 원문의 **부분 문자열** 인지 확인
  - 화자가 **상담사** 라 주장했는데 실제로는 **고객** 발화면 무효
- Evidence 가 무효인 평가자의 judgment 는 가중치 대폭 하향.

### Step 3. 각 평가자의 근거 체인 평가
- 각 A/B/C 에 대해 아래 체크리스트:
  - [ ] Evidence 가 실재하는가 (Step 2 결과)
  - [ ] Evidence 와 judgment 가 논리적으로 연결되는가
  - [ ] judgment 가 rubric 기준 (`허용 점수 단계` 가 암시하는 판정 기준) 에 맞는가
  - [ ] 점수가 judgment 의 강도와 일관되는가 (심각한 감점 사유인데 높은 점수 등 모순 없는가)
- 모든 체크 통과한 평가자의 근거가 가장 신뢰할 만함.

### Step 4. 판정 유형 분류
다음 중 어느 유형인지 판단:
- **(a) 해석 차이형** — 세 평가자 모두 같은 사실을 봤지만 평가 기준을 다르게 적용
  → 어느 해석이 rubric 에 가장 부합하는지 판단해 선택
- **(b) 사실 오인형** — 한 평가자가 원문을 오독했거나 evidence 가 hallucination
  → 오독·hallucination 제거 후 남은 의견으로 결정
- **(c) 비중 차이형** — 세 평가자가 서로 다른 관점에 가중치를 둠 (예: 품격 vs 정확도 vs 고객 경험)
  → 해당 평가 항목의 **rubric 핵심 기준** 에 가장 직결된 관점을 우선
- **(d) 모두 애매형** — 원문 자체가 불명확해 세 평가자 모두 확신 없음
  → 보수적 점수 선택 + `mandatory_human_review=true`

### Step 5. 점수 확정 + ALLOWED_STEPS snap
- Step 3/4 에 따라 최종 점수 결정.
- **반드시 ALLOWED_STEPS 중 하나로 snap**. 예: 허용이 [5, 3, 0] 이면 4 는 3 으로 내림.
- "중간값이니까" / "평균 내서" 같은 **산술 타협은 절대 금지**.

### Step 6. Override hint 감지
원문에서 아래 징후를 직접 찾아 해당 시 `override_hint` 에 태그:
- `profanity` — 욕설, 반말, 사물존칭, 고압적 어투, 고객 비하
- `privacy_leak` — 본인확인 절차 전 제3자 정보 발설, PII (주민번호·카드번호 등) 무단 언급
- `uncorrected_misinfo` — 상담사가 명백히 틀린 정보 안내 후 통화 종료까지 정정 없음
- 해당 없으면 `null`

### Step 7. Human Review 판단
다음 중 하나라도 해당하면 `mandatory_human_review=true`:
- Step 4 에서 (d) 유형으로 분류됨
- 세 평가자 모두의 evidence 가 무효 (Step 2)
- 원문 자체가 짧거나 핵심 발화가 마스킹 (***) 으로 가려져 판정 불가
- 점수가 등급 경계선에 위치 (ALLOWED_STEPS 상 가장 높은 단계 또는 가장 낮은 단계에서 애매)
- Compliance 항목 (#9/#17/#18) 이면서 근거가 약함

===========================================================================
## 3. 판정 유형별 구체 규칙
===========================================================================

### 규칙 1. Compliance 항목 (#9, #17, #18)
- 어느 한 평가자라도 "위반 감지" 라고 했고 그 evidence 가 실재하면 **그 판정을 기본값으로 수용**.
- 엄격 쪽이 기본. 의심스러우면 더 낮은 점수 + human_review=true.

### 규칙 2. 다수결 vs 소수 의견
- 2명이 같은 점수 + 1명이 다른 점수인 경우:
  - 다수 2명의 evidence 가 실재하고 논리가 맞으면 → **다수 채택**
  - 소수 1명의 evidence 가 더 정확하고 원문에 강하게 부합하면 → **소수 채택 가능**
- 다수결은 편의일 뿐 정답이 아님.

### 규칙 3. 모두 다른 점수 (1/1/1 완전 분할)
- 각 평가자의 근거 체인 강도를 비교.
- 가장 강한 근거 체인을 채택.
- 셋 다 비슷하게 약하면 → **중간 단계 선택 + human_review=true**.

### 규칙 4. Evidence 충돌 (같은 발화를 다르게 해석)
- 원문의 맥락 (앞뒤 턴) 을 함께 보고 결정.
- 맥락상 어느 해석이 자연스러운지 판단.

### 규칙 5. 점수는 같은데 근거가 다름
- 근거가 다양하더라도 결론이 같으면 그 점수 채택.
- 단 evidence 가 모두 실재한 경우에만.

===========================================================================
## 4. 익명성·편향 방지
===========================================================================
- A/B/C 는 **익명**. 누가 누구인지 (strict/neutral/loose 인지) 추측하지 마세요.
- 근거의 질만으로 판단. 라벨에 의존한 결정은 실격.
- 평가자 자신이 어느 관점인지 암시하는 표현 ("제가 품격을 중시하는 관점에서..." 등) 이 있어도
  그것만으로 가산/감산 하지 마세요. 근거 자체의 강도로만 평가.

===========================================================================
## 5. reasoning 작성 규칙 (2~4 문장)
===========================================================================
반드시 다음 3요소를 포함:
1. **채택한 근거** — "평가자 X 의 judgment Y (원문 '...' 인용) 가 rubric 에 부합"
2. **기각한 근거 + 이유** — "평가자 Z 의 판정은 evidence 가 원문에 없어 기각" / "평가자 W 의 해석은 rubric 벗어남"
3. **(선택) 특수 상황** — Compliance 항목이라 엄격 쪽 기본 / 원문 모호해 human_review 상향 등

===========================================================================
## 6. 출력 형식 (순수 JSON, 추가 텍스트 금지)
===========================================================================
```json
{
  "final_score": <ALLOWED_STEPS 중 하나의 정수>,
  "chosen_evaluator": "A" | "B" | "C" | "median" | "custom",
  "reasoning": "어떤 평가자의 어떤 근거를 채택했고 왜 다른 의견은 기각했는지 + 원문 인용 1~2개. 2~4 문장.",
  "override_hint": null | "profanity" | "privacy_leak" | "uncorrected_misinfo",
  "mandatory_human_review": <true|false>
}
```

- `final_score` 가 ALLOWED_STEPS 에 없으면 시스템이 기각 → 통계 머지로 fallback 됩니다.
- JSON 앞뒤에 설명·주석·마크다운 블록 표시 등 어떤 추가 텍스트도 붙이지 마세요 (파서 깨짐).
- `chosen_evaluator` 값 의미:
  - `"A"`, `"B"`, `"C"` — 특정 평가자의 판정 채택
  - `"median"` — 세 평가자 결과의 중간값을 ALLOWED_STEPS snap 한 것 (근거 체인이 비슷할 때)
  - `"custom"` — 세 평가자 중 누구의 점수도 아닌 제3의 점수 (원문 재분석 결과)

===========================================================================
## 7. 금지 사항 체크리스트 (이것 하면 판정 무효)
===========================================================================
- ❌ ALLOWED_STEPS 에 없는 점수
- ❌ "중간값이라서" / "평균 내서" 같은 산술 타협
- ❌ Evidence 확인 없이 평가자 주장을 그대로 수용
- ❌ 원문에 없는 quote 를 reasoning 에 인용
- ❌ JSON 외 추가 텍스트 출력
- ❌ Compliance 항목을 "관대 쪽" 으로 판정
- ❌ reasoning 에 "정확한 근거 없음" 같은 회피성 서술만 있음
"""


def _format_evaluator_block(label: str, persona_output: dict[str, Any]) -> str:
    """판사에게 넘길 평가자 블록 — 익명 (A/B/C) 라벨. persona 는 노출 금지."""
    score = persona_output.get("score", persona_output.get("merged_score", "?"))
    judgment = persona_output.get("judgment", "") or persona_output.get("summary", "") or ""
    deductions = persona_output.get("deductions", []) or []
    evidence = persona_output.get("evidence", []) or []
    override_hint = persona_output.get("override_hint")

    lines: list[str] = [f"### 평가자 {label}", f"- 점수: {score}"]
    if judgment:
        lines.append(f"- 판단: {judgment[:400]}")
    if deductions:
        lines.append("- 감점:")
        for d in deductions[:5]:
            reason = (d.get("reason") or "")[:200]
            points = d.get("points", 0)
            lines.append(f"  • -{points}점 · {reason}")
    if evidence:
        lines.append("- 근거 인용:")
        for ev in evidence[:3]:
            quote = (ev.get("quote") or "")[:200]
            speaker = ev.get("speaker", "")
            lines.append(f"  • [{speaker}] {quote}")
    if override_hint:
        lines.append(f"- override_hint: {override_hint}")
    return "\n".join(lines)


def _build_judge_user_message(
    *,
    item_number: int,
    item_name: str,
    transcript_slice: str,
    persona_outputs: dict[str, dict[str, Any]],
    label_map: dict[str, str],
) -> str:
    """판사 user message 구성. label_map: {"A": "strict", "B": "neutral", "C": "loose"} 내부 기록."""
    allowed = ALLOWED_STEPS.get(item_number, [])
    sections = [
        f"## 평가 항목 #{item_number} · {item_name}",
        f"허용 점수 단계 (ALLOWED_STEPS): {allowed}",
        "",
        "## 상담 원문 발췌",
        transcript_slice[:2500],
        "",
        "## 세 평가자의 독립 판정",
    ]
    for label, persona in label_map.items():
        out = persona_outputs.get(persona)
        if out is None:
            continue
        sections.append(_format_evaluator_block(label, out))
        sections.append("")
    sections.append("## 당신의 과제")
    sections.append(
        "위 세 평가자의 근거를 비판적으로 검토한 뒤, 허용 단계 중 하나로 "
        "최종 점수를 확정하고 그 근거를 설명하세요. JSON 만 반환."
    )
    return "\n".join(sections)


async def deliberate(
    *,
    item_number: int,
    item_name: str,
    transcript_slice: str,
    persona_outputs: dict[str, dict[str, Any]],
    llm_backend: str = "bedrock",
    bedrock_model_id: str | None = None,
    fallback_votes: dict[str, int] | None = None,
) -> dict[str, Any]:
    """의견 충돌 시 판사 호출 → 최종 점수 확정.

    Parameters
    ----------
    item_number : int
        평가 항목 번호.
    item_name : str
        항목 표시명 (예: "설명력").
    transcript_slice : str
        해당 항목 평가 구간 (assigned_turns 합친 텍스트, ~2500자 이내).
    persona_outputs : dict[str, dict]
        {"strict": {score, judgment, deductions, evidence, override_hint},
         "neutral": {...}, "loose": {...}} — 실패한 persona 는 누락 가능.
    fallback_votes : dict[str, int] | None
        판사 실패 시 복귀할 통계 머지 입력 (reconcile_personas 용).
        None 이면 persona_outputs 에서 추출.

    Returns
    -------
    dict with keys:
      - final_score            : int (snap 후)
      - reasoning              : str
      - chosen_evaluator       : "A"|"B"|"C"|"median"|"custom"|"fallback"
      - override_hint          : str | None
      - mandatory_human_review : bool
      - judge_used             : bool (False 면 통계 머지로 복귀한 것)
      - persona_label_map      : dict (감사 추적용: {"A":"strict", ...})

    Raises
    ------
    LLMTimeoutError
        판사 LLM 호출 타임아웃 (파이프라인 중단 시그널 — CLAUDE.md 규약).
    """
    # ── 1) persona ↔ A/B/C 익명화 (랜덤 순서로 섞어 자기 편향 배제)
    present_personas = [p for p in PERSONAS if p in persona_outputs and persona_outputs[p] is not None]
    if not present_personas:
        raise ValueError(f"deliberate: item #{item_number} persona_outputs 전부 비어있음")

    labels = ["A", "B", "C"][: len(present_personas)]
    shuffled = list(present_personas)
    random.shuffle(shuffled)
    label_map: dict[str, str] = dict(zip(labels, shuffled, strict=False))
    # reverse map: persona → label (판사 응답 매핑에 사용)
    reverse_map: dict[str, str] = {v: k for k, v in label_map.items()}

    # ── 1.5) HITL 과거 휴먼 검증 사례 retrieval — 판사 호출 일관성 (post_debate 와 동일)
    human_cases: list[dict[str, Any]] = []
    try:
        from v2.hitl.rag_retriever import retrieve_human_cases

        human_cases = retrieve_human_cases(
            item_number=item_number,
            query_text=transcript_slice or item_name,
            top_k=3,
        ) or []
    except Exception as exc:  # noqa: BLE001 — 평가 자체는 그대로 진행
        logger.warning(
            "judge_agent #%d retrieve_human_cases 실패 — 사례 없이 진행: %s",
            item_number, exc,
        )
        human_cases = []

    user_message = _build_judge_user_message(
        item_number=item_number, item_name=item_name,
        transcript_slice=transcript_slice, persona_outputs=persona_outputs,
        label_map=label_map,
    )

    # 판사 user 메시지에 HITL 사례 섹션 추가 — post_debate 와 동일 패턴
    if human_cases:
        try:
            from v2.hitl.rag_retriever import format_human_cases_for_prompt

            formatted = format_human_cases_for_prompt(human_cases)
            if formatted:
                user_message += (
                    f"\n\n## [과거 휴먼 검증 사례 — 동일 평가 항목 #{item_number}, 총 {len(human_cases)}건]\n"
                    + formatted
                    + "\n**이 사례들은 사람이 최종 검수 후 확정한 정답입니다. "
                    + "동일/유사 패턴이면 사람 점수에 우선 가중하세요.**"
                )
        except Exception as exc:  # noqa: BLE001 — 사례 섹션 생략, 평가는 그대로 진행
            logger.warning("judge_agent #%d format_human_cases 실패 — 사례 섹션 생략: %s", item_number, exc)

    # ── 2) 판사 LLM 호출 (Bedrock JSON 모드)
    #   판사는 caller 의 bedrock_model_id (기본 Sonnet 4.6) 를 그대로 사용.
    #   판정 품질 유지 + max_tokens 만 축소로 latency 절감.
    try:
        from v2.agents.group_b._llm import LLMTimeoutError, call_bedrock_json

        raw = await call_bedrock_json(
            system_prompt=JUDGE_SYSTEM_PROMPT,
            user_message=user_message,
            max_tokens=JUDGE_MAX_TOKENS,
            backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
        )
    except LLMTimeoutError:
        raise  # 상위로 전파 — CLAUDE.md 규약
    except Exception as e:
        logger.warning("judge_agent #%d LLM 실패 → 통계 머지 fallback: %s", item_number, e)
        return _fallback_to_stats(item_number, fallback_votes or _extract_votes(persona_outputs), reverse_map)

    # ── 3) 판사 응답 검증
    try:
        final_raw = int(raw.get("final_score", -1))
    except (TypeError, ValueError):
        logger.warning("judge_agent #%d final_score 타입 오류: %r — fallback", item_number, raw.get("final_score"))
        return _fallback_to_stats(item_number, fallback_votes or _extract_votes(persona_outputs), reverse_map)

    allowed = ALLOWED_STEPS.get(item_number, [])
    if final_raw not in allowed:
        logger.warning(
            "judge_agent #%d final_score=%d 허용값 %s 에 없음 — snap + 경고",
            item_number, final_raw, allowed,
        )

    final_score = snap_score_v2(item_number, final_raw)
    chosen = str(raw.get("chosen_evaluator", "custom"))[:16]
    reasoning = str(raw.get("reasoning", ""))[:800]
    override_hint_raw = raw.get("override_hint")
    override_hint = override_hint_raw if override_hint_raw in ("profanity", "privacy_leak", "uncorrected_misinfo") else None
    mhr = bool(raw.get("mandatory_human_review", False))

    return {
        "final_score": final_score,
        "reasoning": reasoning,
        "chosen_evaluator": chosen,
        "override_hint": override_hint,
        "mandatory_human_review": mhr,
        "judge_used": True,
        "persona_label_map": label_map,
        # frontend HITL 사례 표시용 — post_debate 와 동일 메타 (item_number 필드 추가 보장)
        "human_cases_meta": _summarize_human_cases(human_cases),
    }


def _extract_votes(persona_outputs: dict[str, dict[str, Any]]) -> dict[str, int]:
    """persona_outputs 에서 score 만 뽑아 votes dict 구성."""
    votes: dict[str, int] = {}
    for p, out in persona_outputs.items():
        if out is None:
            continue
        s = out.get("score")
        if s is None:
            continue
        try:
            votes[p] = int(s)
        except (TypeError, ValueError):
            continue
    return votes


def _fallback_to_stats(
    item_number: int, votes: dict[str, int], reverse_map: dict[str, str]
) -> dict[str, Any]:
    """판사 실패 시 통계 머지 (median/mode/min) 로 복귀."""
    stats = reconcile_personas(item_number=item_number, votes=votes)
    return {
        "final_score": stats["merged_score"],
        "reasoning": f"[JUDGE_FALLBACK] 판사 호출 실패 — 통계 머지({stats['merge_rule']}) 채택",
        "chosen_evaluator": "fallback",
        "override_hint": None,
        "mandatory_human_review": stats["mandatory_human_review"],
        "judge_used": False,
        "persona_label_map": {v: k for k, v in reverse_map.items()},
        "human_cases_meta": [],  # fallback 경로에서는 retrieval 무관 — 빈 배열 유지
    }


# ===========================================================================
# 하이브리드 엔트리포인트 — Sub Agent 가 1줄로 호출
# ===========================================================================


async def reconcile_hybrid(
    *,
    item_number: int,
    item_name: str,
    transcript_slice: str,
    persona_outputs: dict[str, dict[str, Any]],
    judge_threshold: int = DEFAULT_JUDGE_THRESHOLD,
    llm_backend: str = "bedrock",
    bedrock_model_id: str | None = None,
) -> dict[str, Any]:
    """하이브리드 머지: 통계 머지 우선 → step_spread>=threshold 시 판사 호출.

    반환 키:
      - final_score           : int
      - persona_votes         : dict (원본 snap 점수)
      - step_spread           : int
      - confidence            : int (1~5)
      - mandatory_human_review: bool
      - merge_path            : "stats" | "judge" | "judge_fallback"
      - merge_rule            : str (통계 머지 경로) or None (판사 경로)
      - judge_reasoning       : str | None (판사 경로일 때만)
      - override_hint         : str | None (판사가 감지했거나 OR-merge)
      - persona_label_map     : dict | None (판사 경로일 때만)
    """
    votes = _extract_votes(persona_outputs)
    if not votes:
        raise ValueError(f"reconcile_hybrid: item #{item_number} 유효 votes 없음")

    stats = reconcile_personas(item_number=item_number, votes=votes)

    # ── 합의 또는 낮은 spread → 통계 머지 그대로 채택 (fast path)
    if stats["step_spread"] < judge_threshold:
        return {
            "final_score": stats["merged_score"],
            "persona_votes": stats["persona_votes"],
            "step_spread": stats["step_spread"],
            "confidence": stats["confidence"],
            "mandatory_human_review": stats["mandatory_human_review"],
            "merge_path": "stats",
            "merge_rule": stats["merge_rule"],
            "judge_reasoning": None,
            "override_hint": _or_merge_override(persona_outputs),
            "persona_label_map": None,
        }

    # ── 의견 충돌 → 판사 호출 (deep path)
    judge = await deliberate(
        item_number=item_number, item_name=item_name,
        transcript_slice=transcript_slice, persona_outputs=persona_outputs,
        llm_backend=llm_backend, bedrock_model_id=bedrock_model_id,
        fallback_votes=votes,
    )

    # 판사가 결정한 override_hint 우선, 없으면 persona OR-merge
    override = judge["override_hint"] or _or_merge_override(persona_outputs)

    return {
        "final_score": judge["final_score"],
        "persona_votes": stats["persona_votes"],
        "step_spread": stats["step_spread"],
        # 판사가 결정한 경우 confidence 는 stats 대비 +1 (판사 숙고 가산), 최대 5
        "confidence": min(5, stats["confidence"] + (1 if judge["judge_used"] else 0)),
        "mandatory_human_review": judge["mandatory_human_review"] or stats["mandatory_human_review"],
        "merge_path": "judge" if judge["judge_used"] else "judge_fallback",
        "merge_rule": None if judge["judge_used"] else stats["merge_rule"],
        "judge_reasoning": judge["reasoning"],
        "override_hint": override,
        "persona_label_map": judge["persona_label_map"],
        # frontend HITL 사례 표시용 — deliberate() 가 retrieve_human_cases 호출 결과
        "judge_human_cases": judge.get("human_cases_meta") or [],
    }


def _or_merge_override(persona_outputs: dict[str, dict[str, Any]]) -> str | None:
    """persona 별 override_hint 중 가장 심각한 것 채택 (privacy > profanity > misinfo)."""
    priority = ("privacy_leak", "profanity", "uncorrected_misinfo")
    hints = {
        (out.get("override_hint") if out else None) for out in persona_outputs.values()
    }
    for p in priority:
        if p in hints:
            return p
    return None


# ===========================================================================
# Post-Debate Judge — AG2 토론 종료 후 transcript 통째로 보고 최종 판정
# ===========================================================================


JUDGE_POST_DEBATE_SYSTEM_PROMPT = """당신은 콜센터 상담 평가의 **최종 심판관**입니다.
세 명의 평가자 (A, B, C — **익명**) 가 동일 항목을 두고 **여러 라운드 토론** 을 진행했고,
당신은 그 토론 transcript 전체를 읽고 **최종 점수와 근거** 를 확정합니다.

===========================================================================
## 1. 입력 (User Message 구조)
===========================================================================
- `평가 항목 #N · 항목명`
- `허용 점수 단계 (ALLOWED_STEPS)` — 최종 점수는 이 중 하나여야 함
- `상담 원문 발췌` (~2500자)
- `세 평가자 초기 위치` — 토론 시작 시 점수
- `토론 transcript` — 라운드별로 각 평가자의 점수 + 주장 (rebuttal 포함)

===========================================================================
## 2. 판정 SOP
===========================================================================

### Step 0 (HITL 우선 검토 — 모든 판정에 선행)

[과거 휴먼 검증 사례] 섹션이 있으면 다음 규칙으로 baseline 결정:

**규칙 A — 강한 신호 (HITL 채택)**
조건: 2건 이상 모든 사례가 동일 점수 방향 (예: 모두 "사람 10점") AND 평균 cosine 유사도 ≥ 0.55
- → 사람 점수를 default 채택. Step 1~6 SOP 는 이 baseline 을 검증하는 보조 도구로만 사용.
- 단, 토론에 사례에 등장하지 않은 NEW 결정적 quote (사례에 없는 발화) 가 있으면 ±1 step 만 조정 가능.
- "고객 반응 해석 차이", "토론 합의" 같은 재해석은 NEW 사실 아님 — 무시.
- reasoning 첫 문장에 "HITL 사례 N건 (cos=X.XX) 이 사람 Y점 일치 → baseline 채택" 명시.

**규칙 B — 약한 신호 (HITL 참조)**
조건: 사례 1건만 있거나, 평균 cos < 0.55, 또는 사례 점수가 갈림
- → HITL 점수를 reasoning 에 명시적 비교 후 토론 근거로 결정.

**규칙 C — 사례 없음**
- → Step 1~6 SOP 만 사용.

### Step 1. 원문 1차 정독
토론을 보기 전에 `상담 원문 발췌` 를 끝까지 읽어 인상 체득.

### Step 2. Evidence 실재성 검증
토론 중 평가자들이 인용한 quote 가 원문에 실재하는지 대조.
원문에 없는 quote 를 인용한 평가자의 주장은 가중치 대폭 하향.

### Step 3. 토론 흐름 분석
- 어느 평가자가 어떤 근거로 어느 점수를 주장했는가
- 라운드를 거치며 점수/주장이 **수렴** 했는가, **분기** 했는가
- 마지막 라운드에서 합의된 점수가 있는가, 아니면 갈렸는가
- rebuttal 이 다른 평가자의 주장을 무력화시켰는가

### Step 4. 판정
- 토론을 통해 가장 **rubric 에 부합하는 근거 체인** 을 식별
- 그 근거가 가리키는 점수를 채택 (다수결 X — 근거 강도)
- **반드시 ALLOWED_STEPS 중 하나로 snap**. 산술 평균/타협 금지.

### Step 5. Override 감지 (원문 기반)
원문에서 직접 다음 징후를 찾아 해당 시 태그:
- `profanity` — 욕설/반말/사물존칭/고압 어투/고객 비하
- `privacy_leak` — 본인확인 전 PII 노출, 제3자 정보 발설
- `uncorrected_misinfo` — 명백 오안내 후 정정 없음
없으면 `null`.

### Step 6. Human Review 판단
다음 중 하나라도 해당 시 `mandatory_human_review=true`:
- 토론이 끝까지 갈렸고 (수렴 실패) 어느 근거도 결정적이지 않음
- 모든 평가자의 evidence 가 원문에 없음 (집단 hallucination 의심)
- Compliance 항목 (#9/#17/#18) 인데 근거 약함
- 점수가 등급 경계라 0.5 step 차이로 합리적 해석 가능

===========================================================================
## 3. reasoning 작성 규칙 (3~5 문장)
===========================================================================
다음 4요소 포함:
1. **토론 요약** — "라운드 X 까지 평가자 A/B/C 가 N/M/K 점에서 P/Q/R 로 수렴" 형태
2. **채택한 근거** — "평가자 X 의 주장 (원문 '...' 인용) 이 rubric 에 부합"
3. **기각한 근거** — "평가자 Y 의 주장은 evidence 가 원문에 없어 / rubric 벗어나 기각"
4. **(선택) 특수 상황** — Compliance / 모호 / Human review 필요 등

===========================================================================
## 4. 출력 형식 (순수 JSON, 추가 텍스트 금지) — 페르소나와 동일 정보량
===========================================================================
```json
{
  "final_score": <ALLOWED_STEPS 중 하나의 정수>,
  "chosen_evaluator": "A" | "B" | "C" | "median" | "custom",
  "reasoning": "토론 요약 + 채택/기각 근거 + 원문 인용 1~2개. 3~5 문장.",
  "deductions": [
    {"reason": "감점 사유 한 줄 설명", "points": <감점 점수, 양수>}
  ],
  "evidence": [
    {"speaker": "상담사" | "고객", "quote": "원문 발화 직접 인용 (요약·의역 금지)"}
  ],
  "override_hint": null | "profanity" | "privacy_leak" | "uncorrected_misinfo",
  "mandatory_human_review": <true|false>
}
```

`deductions` / `evidence` 작성 규칙:
- **deductions**: 감점이 없으면 빈 배열 `[]`. 만점이면 비움. 감점 시 사유 + 점수 명시.
- **evidence**: 토론에서 등장한 원문 quote 중 판정 근거가 된 1~5개를 인용. 화자 명시 필수.
  토론 transcript 에 등장하지 않은 quote 는 절대 만들지 말 것 (hallucination 금지).

  ⚠ **"해당 상황 부재로 만점" 케이스** (예: #7 쿠션어 — 거절/불가 상황 미발생, #5 대기 멘트 — 대기
  상황 미발생 등) 에는 직접 근거가 될 quote 가 없으므로 **evidence 를 빈 배열 `[]` 로 둘 것**.
  억지로 무관한 quote 를 채우지 말 것. reasoning 에 "해당 상황 부재 → 평가 대상 아님 → 만점" 명시.

===========================================================================
## 5. 금지 사항 (위반 시 판정 무효)
===========================================================================
- ❌ ALLOWED_STEPS 에 없는 점수
- ❌ "중간값/평균/타협" 같은 산술 결정
- ❌ 토론에 등장하지 않은 새로운 quote 를 reasoning / evidence 에 인용
- ❌ JSON 외 추가 텍스트 출력
- ❌ "토론 결과 그대로 채택" 같은 무비판 수용 (반드시 evidence 검증)
- ❌ 평가자 라벨 (A/B/C) 의 정체를 추측한 가중치 부여
- ❌ deductions 의 points 합이 (max_score - final_score) 와 크게 어긋남 (정합성 유지)
"""


def _format_round_block(round_no: int, turns: list[dict[str, Any]]) -> str:
    """한 라운드의 페르소나 발언들을 익명 라벨로 포맷."""
    lines = [f"### 라운드 {round_no}"]
    for t in turns:
        label = t.get("_label") or t.get("persona", "?")
        score = t.get("score", "?")
        argument = (t.get("argument") or t.get("reasoning") or "")[:500]
        lines.append(f"- **평가자 {label}** · 점수 {score}")
        if argument:
            lines.append(f"  주장: {argument}")
    return "\n".join(lines)


def _build_post_debate_user_message(
    *,
    item_number: int,
    item_name: str,
    transcript_slice: str,
    initial_positions: dict[str, int],
    debate_rounds: list[dict[str, Any]],
    label_map: dict[str, str],
    human_cases: list[dict[str, Any]] | None = None,
) -> str:
    """post-debate 판사 user message — 토론 transcript 익명화 후 포맷."""
    allowed = ALLOWED_STEPS.get(item_number, [])
    # reverse map: persona → label
    persona_to_label = {v: k for k, v in label_map.items()}

    sections: list[str] = [
        f"## 평가 항목 #{item_number} · {item_name}",
        f"허용 점수 단계 (ALLOWED_STEPS): {allowed}",
        "",
        "## 상담 원문 발췌",
        transcript_slice[:2500],
        "",
        "## 세 평가자 초기 위치 (토론 시작 시점)",
    ]
    for persona, score in initial_positions.items():
        label = persona_to_label.get(persona, "?")
        sections.append(f"- 평가자 {label}: {score}점")

    sections.append("")
    sections.append("## 토론 Transcript")
    for r in debate_rounds:
        round_no = r.get("round", 0)
        turns_raw = r.get("turns") or []
        # turns 에 _label 주입 (익명화)
        turns_anon: list[dict[str, Any]] = []
        for t in turns_raw:
            if not isinstance(t, dict):
                continue
            persona = t.get("persona", "")
            t_copy = dict(t)
            t_copy["_label"] = persona_to_label.get(persona, "?")
            turns_anon.append(t_copy)
        sections.append(_format_round_block(round_no, turns_anon))
        sections.append("")

    sections.append("## 당신의 과제")
    sections.append(
        "위 토론 transcript 를 비판적으로 검토하고, 허용 단계 중 하나로 최종 점수를 "
        "확정하세요. 토론 흐름 + 원문 근거를 함께 reasoning 에 명시. JSON 만 반환."
    )

    if human_cases:
        try:
            from v2.hitl.rag_retriever import format_human_cases_for_prompt

            formatted = format_human_cases_for_prompt(human_cases)
        except Exception as exc:  # noqa: BLE001 — silent skip, 평가는 그대로 진행
            logger.warning("format_human_cases_for_prompt 실패 — 사례 섹션 생략: %s", exc)
            formatted = ""
        if formatted:
            sections.append("")
            sections.append(
                f"## [과거 휴먼 검증 사례 — 동일 평가 항목 #{item_number}, 총 {len(human_cases)}건]"
            )
            sections.append(formatted)
            sections.append(
                "**이 사례들은 사람이 최종 검수 후 확정한 정답입니다. "
                "동일/유사 패턴이면 사람 점수에 우선 가중하세요.**"
            )
    return "\n".join(sections)


async def deliberate_post_debate(
    *,
    item_number: int,
    item_name: str,
    transcript_slice: str,
    debate_rounds: list[dict[str, Any]],
    initial_positions: dict[str, int],
    fallback_score: int | None = None,
    fallback_reason: str = "",
    llm_backend: str = "bedrock",
    bedrock_model_id: str | None = None,
) -> dict[str, Any]:
    """AG2 토론 종료 후 transcript 전체를 보고 최종 판정.

    Parameters
    ----------
    debate_rounds : list[dict]
        run_debate 의 RoundRecord.model_dump() 리스트.
        각 항목: {"round": int, "turns": [{"persona", "score", "argument"}], "verdict": {...}}
    initial_positions : dict[str, int]
        {"strict": N, "neutral": N, "loose": N} — 토론 시작 시 점수.
    fallback_score : int | None
        판사 LLM 실패 시 채택할 폴백 점수 (run_debate 의 _decide_final 결과 등).
    fallback_reason : str
        폴백 시 reasoning 으로 사용할 사유 문구.

    Returns
    -------
    dict:
      - final_score            : int (snap 후)
      - reasoning              : str
      - chosen_evaluator       : "A"|"B"|"C"|"median"|"custom"|"fallback"
      - override_hint          : str | None
      - mandatory_human_review : bool
      - judge_used             : bool (False 면 fallback_score 채택한 것)
      - persona_label_map      : dict — {"A": "strict", ...} 감사 추적용

    Raises
    ------
    LLMTimeoutError
        판사 LLM 타임아웃 — 파이프라인 중단 시그널 (CLAUDE.md 규약).
    """
    # ── 1) persona ↔ A/B/C 익명화 (랜덤 셔플로 자기 편향 배제)
    present_personas = [p for p in PERSONAS if p in initial_positions]
    if not present_personas:
        # initial_positions 키가 비표준이면 PERSONAS 순서로 fallback
        present_personas = list(PERSONAS)

    labels = ["A", "B", "C"][: len(present_personas)]
    shuffled = list(present_personas)
    random.shuffle(shuffled)
    label_map: dict[str, str] = dict(zip(labels, shuffled, strict=False))

    # ── 1.5) HITL 과거 휴먼 검증 사례 retrieval — 실패 시 silent skip
    human_cases: list[dict[str, Any]] = []
    try:
        from v2.hitl.rag_retriever import retrieve_human_cases

        human_cases = retrieve_human_cases(
            item_number=item_number,
            query_text=transcript_slice or item_name,
            top_k=3,
        ) or []
    except Exception as exc:  # noqa: BLE001 — 평가 자체는 그대로 진행
        logger.warning(
            "judge_post_debate #%d retrieve_human_cases 실패 — 사례 없이 진행: %s",
            item_number, exc,
        )
        human_cases = []

    user_message = _build_post_debate_user_message(
        item_number=item_number,
        item_name=item_name,
        transcript_slice=transcript_slice,
        initial_positions=initial_positions,
        debate_rounds=debate_rounds,
        label_map=label_map,
        human_cases=human_cases,
    )

    # ── 2) 판사 LLM 호출
    try:
        from v2.agents.group_b._llm import LLMTimeoutError, call_bedrock_json

        raw = await call_bedrock_json(
            system_prompt=JUDGE_POST_DEBATE_SYSTEM_PROMPT,
            user_message=user_message,
            max_tokens=JUDGE_MAX_TOKENS,
            backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
        )
    except LLMTimeoutError:
        raise  # 상위 전파 — CLAUDE.md 규약
    except Exception as e:
        # 2026-04-27: warning → exception 으로 승격해 traceback 가시화 + failure_reason 전파
        failure = f"{type(e).__name__}: {str(e)[:160]}"
        logger.exception(
            "judge_post_debate #%d LLM 실패 → fallback (%s) | %s",
            item_number, fallback_reason or "median", failure,
        )
        return _post_debate_fallback(
            item_number, fallback_score, fallback_reason, label_map,
            failure_reason=failure, retrieved_human_cases_count=len(human_cases),
        )

    # ── 3) 응답 검증
    try:
        final_raw = int(raw.get("final_score", -1))
    except (TypeError, ValueError):
        failure = f"final_score type error: {raw.get('final_score')!r}"
        logger.warning(
            "judge_post_debate #%d %s — fallback",
            item_number, failure,
        )
        return _post_debate_fallback(
            item_number, fallback_score, fallback_reason, label_map,
            failure_reason=failure, retrieved_human_cases_count=len(human_cases),
        )

    allowed = ALLOWED_STEPS.get(item_number, [])
    if final_raw not in allowed:
        logger.warning(
            "judge_post_debate #%d final_score=%d 허용값 %s 외 — snap 적용",
            item_number, final_raw, allowed,
        )

    final_score = snap_score_v2(item_number, final_raw)
    chosen = str(raw.get("chosen_evaluator", "custom"))[:16]
    reasoning = str(raw.get("reasoning", ""))[:1000]
    override_hint_raw = raw.get("override_hint")
    override_hint = override_hint_raw if override_hint_raw in (
        "profanity", "privacy_leak", "uncorrected_misinfo"
    ) else None
    mhr = bool(raw.get("mandatory_human_review", False))

    # 판사 deductions/evidence 파싱 — 페르소나 형식과 동일
    deductions = _parse_deductions(raw.get("deductions"))
    evidence = _parse_evidence(raw.get("evidence"))

    return {
        "final_score": final_score,
        "reasoning": reasoning,
        "chosen_evaluator": chosen,
        "deductions": deductions,
        "evidence": evidence,
        "override_hint": override_hint,
        "mandatory_human_review": mhr,
        "judge_used": True,
        "persona_label_map": label_map,
        "retrieved_human_cases_count": len(human_cases),
        # frontend 가 판사가 참조한 HITL 사례를 노드 드로어에 표시할 수 있도록 메타 보존.
        # 본문 (transcript_excerpt / human_note) 은 200자로 잘라 응답 페이로드 비대화 방지.
        "human_cases_meta": _summarize_human_cases(human_cases),
    }


def _parse_deductions(raw: Any) -> list[dict[str, Any]]:
    """판사 출력의 deductions 배열 정규화 → [{reason, points}]. 무효 항목 제거."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for d in raw[:10]:  # 상한 10개
        if not isinstance(d, dict):
            continue
        reason = str(d.get("reason") or "").strip()[:200]
        if not reason:
            continue
        try:
            points = abs(float(d.get("points", 0)))
        except (TypeError, ValueError):
            continue
        if points <= 0:
            continue
        out.append({"reason": reason, "points": points})
    return out


def _parse_evidence(raw: Any) -> list[dict[str, Any]]:
    """판사 출력의 evidence 배열 정규화 → [{speaker, quote}]. 빈 quote 제거."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for e in raw[:8]:  # 상한 8개
        if not isinstance(e, dict):
            continue
        quote = str(e.get("quote") or "").strip()[:300]
        if not quote:
            continue
        speaker = str(e.get("speaker") or "").strip()[:20] or "상담사"
        out.append({"speaker": speaker, "quote": quote})
    return out


def _post_debate_fallback(
    item_number: int,
    fallback_score: int | None,
    fallback_reason: str,
    label_map: dict[str, str],
    *,
    failure_reason: str | None = None,
    retrieved_human_cases_count: int = 0,
) -> dict[str, Any]:
    """post-debate 판사 실패 시 caller 가 준 fallback_score (median 등) 그대로 채택.

    failure_reason: 호출자(run_debate) 가 DebateRecord.judge_failure_reason 으로 보존 →
    프론트가 "판사 LLM 호출 실패: {reason}" 표기 가능.
    """
    snapped = snap_score_v2(item_number, fallback_score) if fallback_score is not None else None
    return {
        "final_score": snapped,
        "reasoning": f"[JUDGE_POST_DEBATE_FALLBACK] 판사 호출 실패 — {fallback_reason or '폴백 점수 채택'}",
        "chosen_evaluator": "fallback",
        "deductions": [],
        "evidence": [],
        "override_hint": None,
        "mandatory_human_review": False,
        "judge_used": False,
        "judge_failure_reason": failure_reason,
        "persona_label_map": label_map,
        "retrieved_human_cases_count": retrieved_human_cases_count,
        "human_cases_meta": [],  # fallback 에서는 사례 미주입
    }


def _summarize_human_cases(human_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """retrieve_human_cases 결과 → frontend 표시용 요약 메타 리스트.

    원본 hit 의 본문 (transcript_excerpt / human_note / ai_judgment / body) 은 200자로 잘라
    DebateRecord 페이로드 비대화 방지. consultation_id + AI/Human score + delta + 본문 일부
    조합으로 frontend 가 노드 드로어에 "판사 참조 HITL 사례" 표시 가능.
    """
    summaries: list[dict[str, Any]] = []
    for h in human_cases or []:
        if not isinstance(h, dict):
            continue
        summaries.append(
            {
                "consultation_id": h.get("consultation_id"),
                "item_number": h.get("item_number"),
                "ai_score": h.get("ai_score"),
                "human_score": h.get("human_score"),
                "delta": h.get("delta"),
                "confirmed_at": h.get("confirmed_at"),
                "external_id": h.get("external_id"),
                "knn_score": h.get("_knn_score"),
                "transcript_excerpt": (h.get("transcript_excerpt") or "")[:200],
                "human_note": (h.get("human_note") or "")[:200],
                "ai_judgment": (h.get("ai_judgment") or "")[:200],
            }
        )
    return summaries
