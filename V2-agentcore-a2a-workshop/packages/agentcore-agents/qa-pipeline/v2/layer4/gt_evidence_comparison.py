# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 4 후속 — GT 정답 근거 vs AI 평가 근거 LLM 비교 노드.

`gt_comparison_node` 다음에 실행되어 항목별로 사람 QA 의 비고(근거) 와
AI 의 evidence 를 LLM 으로 비교 → verdict (match / partial / mismatch) +
reasoning 산출. 업무 정확도 (#15, #16) 는 점수 비교와 동일한 이유로 제외.

입력 (state):
  - gt_comparison: dict (이전 노드 산출물 — items[] 에 ai_evidence/note 포함)
  - bedrock_model_id / llm_backend (선택)

출력 (state.gt_evidence_comparison):
  {
    "enabled": bool,
    "sample_id": str | None,
    "excluded_items": [15, 16],
    "items": [
      {
        item_number, item_name,
        ai_score, gt_score,
        ai_evidence, gt_note,
        verdict: "match"|"partial"|"mismatch"|"insufficient",
        reasoning: str,
      }
    ],
    "summary": {
      total: int, match: int, partial: int, mismatch: int, insufficient: int,
      match_rate: float (%)
    }
  }
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any


logger = logging.getLogger(__name__)

EXCLUDED_ITEMS: frozenset[int] = frozenset({15, 16})

# 항목당 LLM 호출 동시 실행 상한 — Bedrock throttle 보호.
_PER_ITEM_CONCURRENCY = 5

_VERDICT_LABELS = {
    "match": "✅ 일치",
    "partial": "⚠️ 부분일치",
    "mismatch": "❌ 불일치",
    "insufficient": "ℹ️ 근거 부족",
}

_SYSTEM_PROMPT = """당신은 콜센터 QA 평가의 메타-감사관입니다.

상담 평가 1개 항목에 대해 두 가지 근거를 비교합니다:
- (A) 사람 QA 정답 근거 — 시니어 QA 담당자가 작성한 채점 사유
- (B) AI 평가 근거 — 자동 평가 파이프라인이 산출한 채점 사유

목표: 두 근거가 **같은 사실/관찰을 가리키고 있는지** 판정. 점수 일치 여부가 아니라
근거 텍스트가 동일 화행/사실/구간을 지목하는지를 본다.

판정 기준:
- "match"        : 핵심 사실/근거가 사실상 동일 (사소한 표현 차이 무방)
- "partial"      : 일부 겹치나 한쪽이 누락/추가 사실 있음
- "mismatch"     : 서로 다른 사실/구간을 가리킴 (모순도 포함)
- "insufficient" : 한쪽 또는 양쪽이 비어 비교 불가

reasoning 작성 규칙 (필수 — 한 줄 요약 금지):
- **3~5 문장**, 한국어, 총 300~500자 사이로 상세히 작성
- 다음 4요소를 반드시 포함:
  1. **사람 QA 가 짚은 사실/구간** — 어떤 발화/턴/패턴을 근거로 했는지 구체적 인용
  2. **AI 가 짚은 사실/구간** — 어떤 발화/턴/패턴을 근거로 했는지 구체적 인용
  3. **겹치는 부분** vs **다른 부분** 을 명확히 대비
  4. **판정 사유** — 왜 match/partial/mismatch 인지 1문장 결론
- 각 측의 근거에서 핵심 quote 또는 turn 번호 (예: T19, T84) 가 있으면 명시
- "양쪽 모두 X" 같은 추상 표현 대신 "사람 QA 는 T19 의 '...' 을, AI 는 T12 의 '...' 을" 처럼 구체적으로

JSON 으로만 응답 (다른 텍스트 금지):
{"verdict": "match|partial|mismatch|insufficient", "reasoning": "위 4요소 포함 3~5 문장"}

예시 (mismatch 케이스):
{"verdict": "mismatch", "reasoning": "사람 QA 는 T19 의 '주문번호 확인 부탁드립니다' 발화를 본인확인 절차의 핵심으로 짚었으나, AI 는 T12 의 '연락처 다시 한 번 확인 부탁드릴게요' 를 근거로 채택했음. 두 발화 모두 정보확인 시도이긴 하나 다른 시점·다른 정보유형을 가리키며, 사람 QA 가 본 본인확인 핵심 단계를 AI 는 별개 정보확인 시도로 분리 인식. 결과적으로 동일 사실이 아닌 인접 두 사실을 각자 다르게 짚었으므로 mismatch."}
"""


def _build_user_message(item: dict[str, Any]) -> str:
    ai_lines = item.get("ai_evidence") or []
    gt_note = item.get("note") or ""
    ai_text = "\n".join(f"  - {ln}" for ln in ai_lines) if ai_lines else "  (없음)"
    return (
        f"## 평가 항목\n"
        f"#{item.get('item_number')} {item.get('item_name') or ''}  (배점 {item.get('max_score')})\n\n"
        f"## 점수\n"
        f"- 사람 QA: {item.get('gt_score')}\n"
        f"- AI    : {item.get('ai_score')}\n\n"
        f"## (A) 사람 QA 정답 근거 (xlsx 비고)\n"
        f"{gt_note or '(없음)'}\n\n"
        f"## (B) AI 평가 근거 (evidence)\n"
        f"{ai_text}\n\n"
        "## (B) AI 판정 요약\n"
        f"{item.get('ai_judgment') or '(없음)'}\n"
    )


def _parse_verdict(text: str) -> dict[str, str]:
    """LLM 응답에서 JSON 추출. 실패 시 verdict=insufficient + reasoning=원문 일부."""
    if not text:
        return {"verdict": "insufficient", "reasoning": "(빈 응답)"}
    # 첫 번째 { ... } 블록 추출
    m = re.search(r"\{[^{}]*\"verdict\"[^{}]*\}", text, re.DOTALL)
    raw = m.group(0) if m else text
    try:
        obj = json.loads(raw)
        v = str(obj.get("verdict", "")).strip().lower()
        if v not in {"match", "partial", "mismatch", "insufficient"}:
            v = "insufficient"
        r = str(obj.get("reasoning", "")).strip() or "(설명 없음)"
        # 상세 reasoning 지원 — 800자까지 보존 (이전 200 은 한 줄 요약 시절 한도)
        return {"verdict": v, "reasoning": r[:800]}
    except Exception:
        # JSON 파싱 실패 — 키워드로 추론
        low = text.lower()
        if "match" in low and "mis" not in low:
            v = "match"
        elif "partial" in low:
            v = "partial"
        elif "mismatch" in low or "mis" in low:
            v = "mismatch"
        else:
            v = "insufficient"
        return {"verdict": v, "reasoning": text.strip()[:800]}


async def _judge_item(
    item: dict[str, Any],
    *,
    llm_backend: str | None,
    bedrock_model_id: str | None,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    from langchain_core.messages import HumanMessage, SystemMessage  # noqa: WPS433

    from nodes.llm import LLMTimeoutError, ainvoke_llm  # noqa: WPS433

    base = {
        "item_number": item.get("item_number"),
        "item_name": item.get("item_name"),
        "max_score": item.get("max_score"),
        "ai_score": item.get("ai_score"),
        "gt_score": item.get("gt_score"),
        "ai_evidence": item.get("ai_evidence") or [],
        "ai_judgment": item.get("ai_judgment") or "",
        "gt_note": item.get("note") or "",
    }

    has_ai = bool(base["ai_evidence"]) or bool(base["ai_judgment"])
    has_gt = bool(base["gt_note"])
    if not has_ai or not has_gt:
        return {
            **base,
            "verdict": "insufficient",
            "reasoning": (
                "AI 근거 누락" if not has_ai and has_gt
                else "사람 QA 비고 누락" if has_ai and not has_gt
                else "양쪽 모두 근거 없음"
            ),
        }

    user_msg = _build_user_message(item)
    messages = [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=user_msg)]
    async with semaphore:
        try:
            text = await ainvoke_llm(
                messages,
                temperature=0.1,
                # 3~5 문장 상세 reasoning (이전 한 줄 요약 시절 400 → 1000)
                max_tokens=1000,
                backend=llm_backend,
                bedrock_model_id=bedrock_model_id,
            )
        except LLMTimeoutError:
            raise
        except Exception as e:  # pragma: no cover
            logger.warning("gt_evidence judge #%s LLM 실패: %s", base["item_number"], e)
            return {**base, "verdict": "insufficient", "reasoning": f"(LLM 실패: {e})"}

    parsed = _parse_verdict(text)
    return {**base, **parsed}


async def gt_evidence_comparison_node(state: dict[str, Any]) -> dict[str, Any]:
    """`gt_comparison` 결과를 받아 항목별 근거 LLM 비교 수행."""
    gc = state.get("gt_comparison") or {}
    if not gc.get("enabled"):
        return {
            "gt_evidence_comparison": {
                "enabled": False,
                "reason": gc.get("reason") or gc.get("error") or "gt_comparison 비활성",
            }
        }

    items = [it for it in (gc.get("items") or []) if it.get("item_number") not in EXCLUDED_ITEMS]
    if not items:
        return {
            "gt_evidence_comparison": {
                "enabled": False,
                "reason": "비교 가능한 항목 없음",
            }
        }

    llm_backend = state.get("llm_backend")
    bedrock_model_id = state.get("bedrock_model_id")

    semaphore = asyncio.Semaphore(_PER_ITEM_CONCURRENCY)
    judged = await asyncio.gather(
        *[_judge_item(it, llm_backend=llm_backend, bedrock_model_id=bedrock_model_id, semaphore=semaphore)
          for it in items],
        return_exceptions=False,
    )

    counts = {"match": 0, "partial": 0, "mismatch": 0, "insufficient": 0}
    for j in judged:
        v = j.get("verdict") or "insufficient"
        counts[v] = counts.get(v, 0) + 1
    total = len(judged)
    rate = round((counts["match"] / total * 100.0) if total else 0.0, 1)

    # verdict 한글 라벨 부착 (UI 가독성)
    for j in judged:
        j["verdict_label"] = _VERDICT_LABELS.get(j.get("verdict") or "insufficient", "")

    result = {
        "enabled": True,
        "sample_id": gc.get("sample_id"),
        "sheet_name": gc.get("sheet_name"),
        "excluded_items": sorted(EXCLUDED_ITEMS),
        "items": judged,
        "summary": {
            "total": total,
            "match": counts["match"],
            "partial": counts["partial"],
            "mismatch": counts["mismatch"],
            "insufficient": counts["insufficient"],
            "match_rate": rate,
        },
    }
    logger.info(
        "gt_evidence_comparison: total=%d match=%d partial=%d mismatch=%d insufficient=%d match_rate=%.1f%%",
        total, counts["match"], counts["partial"], counts["mismatch"], counts["insufficient"], rate,
    )
    return {"gt_evidence_comparison": result}
