# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Report Narrator — 자연어 총평·코칭 LLM 노드 (2026-05-08).

흐름: ``debate`` → **report_narrator** → ``layer4`` (Report Generator V2)

책임:
  - 평가 결과 (evaluations / debates / orchestrator) 를 한 묶음으로 LLM 에 전달
  - 자연어 총평 narrative + strengths/improvements 자연어 문장 + coaching_points 생성
  - 결과를 ``state["report_llm_summary"]`` 에 저장

이 노드는 점수 산출이나 판정에 관여하지 않는다 — 평가 단계에서 이미 결정된 점수/판정/근거를
재가공해서 "사람 읽기 좋은" 문장으로 풀어낼 뿐. 후속 ``layer4`` 가 이 결과를 SummaryBlock
조립 시 우선 사용 (없으면 결정적 fallback 유지).

skip_phase_c_and_reporting=True 케이스에서는 즉시 빈 결과 반환 — graph 가 narrator 를
거쳐 곧장 combined_report 로 라우팅되더라도 LLM 비용 0 으로 통과.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any


logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """당신은 콜센터 QA 평가 결과를 사람이 읽기 좋게 요약하는 평가 리포터다.
이미 채점은 끝난 상태 — 최종 점수/판정/감점 사유를 그대로 인정하고, 그것을 자연스러운 한국어 문장으로 풀어 작성한다.

**작성 범위**:
- 입력으로 받는 것은 모든 노드의 **최종 결과** 만이다 — 항목별 최종 점수·판정·감점 / KMS 평가 / KSQI 평가 / GT 사람검수 비교 / HITL 라우팅.
- 토론 과정 / 페르소나 의견 분기 / 판사 reasoning 같은 **중간 단계는 입력에 포함되지 않으며**, 결과 작성 시에도 언급하지 말 것.
- 사람 상담사가 본인의 평가 결과를 읽고 다음 상담에 반영하기 위한 마무리 총평이라 생각하라.

다음 JSON 스키마로 응답하라:

{
  "narrative": "전체 상담을 한 단락 (3~5문장) 으로 평가하는 총평. 점수 합계·등급·핵심 강점·핵심 개선점을 한 호흡으로 서술.",
  "strengths": [
    "잘한 부분 1 (자연어 문장, 항목 번호 인용 가능)",
    "잘한 부분 2"
  ],
  "improvements": [
    "개선 필요 부분 1 (감점 사유 + 권고)",
    "개선 필요 부분 2"
  ],
  "coaching_points": [
    {
      "category": "예: 인사/경청/언어/업무정확도",
      "priority": "high | medium | low",
      "title": "한 줄 요약 제목",
      "detail": "구체적 행동 가이드 (1~2문장)"
    }
  ]
}

규칙:
- strengths 와 improvements 는 각각 3~5개. 항목번호 + 짧은 이유.
- coaching_points 는 우선순위 높은 것부터 최대 5개.
- 점수를 새로 산출하지 말 것. 주어진 점수와 사유를 풀어 쓰는 것이 본 단계의 책임.
- "토론에서", "페르소나가", "판사가" 같은 중간 단계 표현은 사용 금지 — 사람 상담사 입장에서 결과만 본다.
- JSON 외 텍스트 (코드블록 마커 등) 절대 포함 금지.
- 한국어로 작성.
"""


def _coerce_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _extract_item_lines(state: dict[str, Any]) -> list[str]:
    """state.evaluations 의 **최종** 점수/판정/감점만 한 줄씩 정리.

    토론 과정 (state.debates 의 round-by-round / persona votes) 과 판사 reasoning 은
    narrator 입력에 포함하지 않는다 — 사용자 정책 (2026-05-08): "마무리 총평에는
    토론 과정 같은 중간 단계는 필요 없음, 최종 결과만 보고 작성".
    """
    evals = state.get("evaluations") or []
    out: list[str] = []
    for ev in evals:
        if not isinstance(ev, dict):
            continue
        inner = ev.get("evaluation") if isinstance(ev.get("evaluation"), dict) else ev
        if not isinstance(inner, dict):
            continue
        item_no = inner.get("item_number")
        if item_no is None:
            continue
        item_name = inner.get("item_name") or inner.get("item") or f"항목 {item_no}"
        score = inner.get("score")
        max_s = inner.get("max_score")
        mode = inner.get("evaluation_mode") or "full"
        judgment = (inner.get("judgment") or "")[:300]
        deductions = inner.get("deductions") or []
        ded_brief: list[str] = []
        for d in deductions[:3]:
            if isinstance(d, dict):
                pts = d.get("points")
                reason = (d.get("reason") or "")[:120]
                if reason:
                    ded_brief.append(f"-{pts}점 ({reason})")
        ded_text = " · ".join(ded_brief) if ded_brief else "감점 없음"

        out.append(
            f"#{item_no} {item_name}: {score}/{max_s} (mode={mode}) — {judgment} | {ded_text}"
        )
    return out


def _extract_summary_meta(state: dict[str, Any]) -> dict[str, Any]:
    """orchestrator / report 에서 등급/총점 등 메타 추출 (있는 경우)."""
    meta: dict[str, Any] = {}
    orch = state.get("orchestrator") or {}
    if isinstance(orch, dict):
        meta["grade"] = orch.get("grade")
        cats = orch.get("category_scores") or {}
        if isinstance(cats, dict) and cats:
            meta["category_scores"] = {
                k: v for k, v in cats.items() if isinstance(v, (int, float))
            }
    rep = state.get("report") or {}
    if isinstance(rep, dict):
        summary = rep.get("summary") or {}
        if isinstance(summary, dict):
            meta.setdefault("grade", summary.get("grade"))
            meta.setdefault("total_score", summary.get("total_score"))
            meta.setdefault("max_score", summary.get("max_score"))
    return meta


def _extract_kms_lines(state: dict[str, Any]) -> list[str]:
    """state.kms_evaluation 에서 인텐트별 점수/요약."""
    kms = state.get("kms_evaluation") or {}
    if not isinstance(kms, dict) or not kms.get("available"):
        return []
    out: list[str] = []
    intents = kms.get("detected_intents") or []
    if intents:
        out.append("검출 인텐트: " + ", ".join(str(i) for i in intents[:6]))
    evals_by = kms.get("evaluations_by_intent") or {}
    if isinstance(evals_by, dict):
        for intent, ev in list(evals_by.items())[:6]:
            if not isinstance(ev, dict):
                continue
            sc = ev.get("score")
            sm = (ev.get("summary") or ev.get("reasoning") or "")[:200]
            out.append(f"  - [{intent}] {sc}/10 · {sm}")
    return out


def _extract_ksqi_lines(state: dict[str, Any]) -> list[str]:
    """state.ksqi_report 에서 area_a/area_b + 항목별 결함."""
    rep = state.get("ksqi_report") or {}
    if not isinstance(rep, dict):
        return []
    out: list[str] = []
    a = rep.get("area_a") or {}
    b = rep.get("area_b") or {}
    if a:
        out.append(f"Area A 서비스품질: {a.get('scaled', '-')}점 ({a.get('grade', '-')})")
    if b:
        out.append(f"Area B 공감: {b.get('scaled', '-')}점 ({b.get('grade', '-')})")
    items = rep.get("items") or []
    defects: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("defect"):
            name = it.get("item_name") or f"항목 {it.get('item_number')}"
            rationale = (it.get("rationale") or "")[:120]
            defects.append(f"  - {name}: 결함 (-{it.get('max_score')}점) {rationale}")
    if defects:
        out.append("KSQI 결함:")
        out.extend(defects[:6])
    return out


def _extract_gt_lines(state: dict[str, Any]) -> list[str]:
    """state.gt_comparison + gt_evidence_comparison 에서 차이/판정."""
    out: list[str] = []
    gt = state.get("gt_comparison") or {}
    if isinstance(gt, dict) and gt.get("available"):
        summary = gt.get("summary") or {}
        if isinstance(summary, dict):
            out.append(
                f"GT 점수 비교: AI 총점 {summary.get('ai_total', '-')} vs GT 총점 "
                f"{summary.get('gt_total', '-')} (차이 {summary.get('total_delta', '-')})"
            )
        diffs = gt.get("items") or []
        notable: list[str] = []
        for it in diffs:
            if not isinstance(it, dict):
                continue
            delta = it.get("delta")
            if delta is None or abs(_coerce_int(delta) or 0) < 1:
                continue
            notable.append(
                f"  - #{it.get('item_number')} {it.get('item_name', '')}: "
                f"AI={it.get('ai_score')} GT={it.get('gt_score')} (Δ{delta})"
            )
        if notable:
            out.append("GT 점수 차이 (>=1):")
            out.extend(notable[:6])

    gtev = state.get("gt_evidence_comparison") or {}
    if isinstance(gtev, dict) and gtev.get("available"):
        items = gtev.get("items") or []
        verdicts: list[str] = []
        for it in items[:6]:
            if not isinstance(it, dict):
                continue
            verdict = it.get("verdict") or "-"
            reason = (it.get("reasoning") or "")[:160]
            verdicts.append(
                f"  - #{it.get('item_number')} {it.get('item_name', '')} verdict={verdict} · {reason}"
            )
        if verdicts:
            out.append("GT 근거 LLM 비교:")
            out.extend(verdicts)
    return out


def _extract_routing_lines(state: dict[str, Any]) -> list[str]:
    """state.routing 에서 HITL 라우팅 / priority_flags."""
    routing = state.get("routing") or {}
    if not isinstance(routing, dict):
        return []
    out: list[str] = []
    decision = routing.get("decision")
    if decision:
        out.append(f"라우팅: {decision} (사유: {routing.get('hitl_driver', '-')})")
    flags = routing.get("priority_flags") or []
    flag_lines: list[str] = []
    for f in flags[:5]:
        if not isinstance(f, dict):
            continue
        flag_lines.append(f"  - {f.get('code', '')} · 항목 {f.get('item_numbers', [])}")
    if flag_lines:
        out.append("우선순위 플래그:")
        out.extend(flag_lines)
    return out


def _build_user_message(state: dict[str, Any]) -> str:
    """LLM 입력 메시지 빌드 — 모든 노드 산출물 종합.

    사용 데이터:
      - 채점 메타 (orchestrator + report.summary): 등급/총점
      - 카테고리별 점수 (orchestrator.category_scores)
      - 항목별 평가 결과 (evaluations + debates 의 판사 reasoning)
      - KMS 평가 (kms_evaluation: 인텐트별 점수)
      - KSQI 평가 (ksqi_report: Area A/B + 결함)
      - GT 비교 (gt_comparison + gt_evidence_comparison: AI vs GT 점수/근거)
      - HITL 라우팅 (routing: decision + priority_flags)
    """
    meta = _extract_summary_meta(state)
    lines: list[str] = []
    if meta.get("grade") or meta.get("total_score") is not None:
        lines.append(
            f"## 채점 메타\n등급: {meta.get('grade') or '-'} · "
            f"총점: {meta.get('total_score', '-')}/{meta.get('max_score', '-')}"
        )
    cat = meta.get("category_scores")
    if cat:
        cat_text = ", ".join(f"{k}={v}" for k, v in cat.items())
        lines.append(f"## 카테고리별 점수\n{cat_text}")

    items = _extract_item_lines(state)
    if items:
        lines.append("## 항목별 최종 평가 결과 (#1~#18, 점수/판정/감점)\n" + "\n".join(items))
    else:
        lines.append("## 항목별 최종 평가 결과\n(평가 결과 없음)")

    kms_lines = _extract_kms_lines(state)
    if kms_lines:
        lines.append("## KMS 평가 (인텐트별 매뉴얼 준수)\n" + "\n".join(kms_lines))

    ksqi_lines = _extract_ksqi_lines(state)
    if ksqi_lines:
        lines.append("## KSQI 평가 (Area A 서비스품질 / B 공감)\n" + "\n".join(ksqi_lines))

    gt_lines = _extract_gt_lines(state)
    if gt_lines:
        lines.append("## GT 사람검수 비교 (AI vs 사람 평가자)\n" + "\n".join(gt_lines))

    route_lines = _extract_routing_lines(state)
    if route_lines:
        lines.append("## HITL 라우팅\n" + "\n".join(route_lines))

    lines.append(
        "## 작성 지침\n"
        "위 모든 단계의 결과를 종합한 최종 마무리 결론. narrative 는 점수·등급·핵심 강점 1~2개·"
        "핵심 개선점 1~2개·KSQI 핵심 결과·GT 와의 차이가 있다면 짧게 언급 — 한 단락(3~5문장). "
        "strengths/improvements 는 점수와 짧은 이유. coaching_points 는 우선순위 높은 것부터. "
        "점수 재산출 금지 — 이미 끝난 채점 결과를 사람이 읽기 좋은 문장으로 풀어 쓰는 것이 책임."
    )
    return "\n\n".join(lines)


_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _parse_llm_json(text: str) -> dict[str, Any] | None:
    """LLM 응답 텍스트에서 JSON 객체 추출. 실패 시 None."""
    if not text:
        return None
    s = text.strip()
    # 코드블록 제거
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.MULTILINE).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = _JSON_RE.search(s)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def _normalize_result(parsed: dict[str, Any]) -> dict[str, Any]:
    """LLM 응답을 안전한 형태로 정규화."""
    narrative = str(parsed.get("narrative") or "").strip()
    strengths_raw = parsed.get("strengths") or []
    improvements_raw = parsed.get("improvements") or []
    coaching_raw = parsed.get("coaching_points") or []

    strengths: list[str] = []
    if isinstance(strengths_raw, list):
        for s in strengths_raw[:8]:
            if isinstance(s, str) and s.strip():
                strengths.append(s.strip()[:300])

    improvements: list[str] = []
    if isinstance(improvements_raw, list):
        for s in improvements_raw[:8]:
            if isinstance(s, str) and s.strip():
                improvements.append(s.strip()[:300])

    coaching: list[dict[str, Any]] = []
    if isinstance(coaching_raw, list):
        for cp in coaching_raw[:6]:
            if not isinstance(cp, dict):
                continue
            title = str(cp.get("title") or "").strip()[:120]
            detail = str(cp.get("detail") or "").strip()[:400]
            if not title and not detail:
                continue
            priority = str(cp.get("priority") or "medium").strip().lower()
            if priority not in ("high", "medium", "low"):
                priority = "medium"
            coaching.append(
                {
                    "category": str(cp.get("category") or "").strip()[:80],
                    "priority": priority,
                    "title": title,
                    "detail": detail,
                }
            )

    return {
        "narrative": narrative,
        "strengths": strengths,
        "improvements": improvements,
        "coaching_points": coaching,
    }


async def report_narrator_node(state: dict[str, Any]) -> dict[str, Any]:
    """평가 결과 종합 → LLM 자연어 narrative/coaching 생성.

    Returns
    -------
    dict
        ``{"report_llm_summary": {...}}`` 또는 빈 dict (skip / 실패).

    Skip 조건:
      - ``state.plan.skip_phase_c_and_reporting=True`` (배치 평가 등)
      - ``state.evaluations`` 없음
    """
    plan = state.get("plan") or {}
    if plan.get("skip_phase_c_and_reporting"):
        logger.info("report_narrator: skip_phase_c_and_reporting=True → skip")
        return {}

    evaluations = state.get("evaluations") or []
    if not evaluations:
        logger.info("report_narrator: evaluations 비어있음 → skip")
        return {}

    from langchain_core.messages import HumanMessage, SystemMessage  # noqa: WPS433

    from nodes.llm import LLMTimeoutError, ainvoke_llm  # noqa: WPS433

    user_msg = _build_user_message(state)
    messages = [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=user_msg)]

    llm_backend = state.get("llm_backend")
    bedrock_model_id = state.get("bedrock_model_id")

    # 단일 LLM 호출 — 토론·평가가 끝난 뒤 한 번만 발생하므로 동시성 제어 불필요.
    try:
        text = await ainvoke_llm(
            messages,
            temperature=0.3,  # 자연어 표현 다양성 약간 부여
            max_tokens=2400,  # narrative + lists + coaching 5개 여유
            backend=llm_backend,
            bedrock_model_id=bedrock_model_id,
        )
    except LLMTimeoutError:
        logger.warning("report_narrator: LLM timeout — skip (보고서는 결정적 폴백)")
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("report_narrator: LLM 실패 — skip · %s", exc)
        return {}

    parsed = _parse_llm_json(text or "")
    if not parsed:
        logger.warning("report_narrator: JSON 파싱 실패 — skip\n응답 발췌: %s", (text or "")[:300])
        return {}

    result = _normalize_result(parsed)
    if not result["narrative"] and not result["strengths"] and not result["improvements"]:
        logger.warning("report_narrator: 빈 결과 — skip")
        return {}

    logger.info(
        "report_narrator: 완료 · narrative=%d자 strengths=%d improvements=%d coaching=%d",
        len(result["narrative"]),
        len(result["strengths"]),
        len(result["improvements"]),
        len(result["coaching_points"]),
    )
    return {"report_llm_summary": result}


__all__ = ["report_narrator_node"]
