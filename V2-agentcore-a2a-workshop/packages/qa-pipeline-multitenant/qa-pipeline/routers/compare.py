# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""/analyze-compare, /analyze-manual-compare — 멀티테넌트 확장.

단일 테넌트 원본과 비교 로직은 동일. 테넌트별 차이:
  - request.state.tenant_id 를 읽어 응답에 포함 (감사 로그 / 프론트 표시)
  - 향후 Dev4 의 `tenant.store.get_config(tid).default_models` 로 비교 모델 기본값 전환 가능
    (현재는 원본처럼 sonnet-4-6 고정 — 테넌트별 모델 선택은 Phase 3 이후)
"""

from __future__ import annotations

import json
import logging
import re
from ._tenant_deps import require_tenant_id
from .schemas import AnalyzeCompareRequest, AnalyzeManualCompareRequest
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from typing import Any


logger = logging.getLogger(__name__)

router = APIRouter(tags=["compare"])


# ---------------------------------------------------------------------------
# POST /analyze-compare
# ---------------------------------------------------------------------------


@router.post("/analyze-compare")
async def analyze_compare(payload: AnalyzeCompareRequest, request: Request) -> JSONResponse:
    tid = require_tenant_id(request)

    left_result = payload.left_result
    right_result = payload.right_result
    left_model = payload.left_model
    right_model = payload.right_model
    transcript = payload.transcript

    if not left_result or not right_result:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "left_result and right_result are required.", "tenant_id": tid},
        )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from nodes.llm import _extract_text, get_chat_model

        llm = get_chat_model(
            backend="bedrock", bedrock_model_id="us.anthropic.claude-sonnet-4-6", temperature=0.3, max_tokens=8192
        )

        left_json = json.dumps(left_result, ensure_ascii=False)
        right_json = json.dumps(right_result, ensure_ascii=False)

        system_prompt = (
            "당신은 QA 평가 전문가입니다. 원본 상담 전사록과 두 모델의 평가 결과를 비교 분석합니다.\n"
            "원문 대화를 직접 확인하여 각 모델의 판정이 정확한지 검증하세요.\n\n"
            "## 출력 형식 규칙\n"
            "- Markdown 형식, 3000자 이내\n"
            "- 비교표는 반드시 **Markdown 테이블** 사용 (`| 항목 | A | B |` 형식)\n"
            "- 원문 인용 시 `> 인용문` (blockquote) 사용\n"
            "- 각 항목 판정에 ✅ 정확 / ❌ 오판 / ⚠️ 과감점 아이콘 표기\n"
            "- 섹션 사이에 `---` 구분선 삽입\n"
            "- 오판 항목은 테이블로 정리: `| 모델 | 항목 | 오판 내용 | 정확 점수 |`\n"
        )

        transcript_section = ""
        if transcript:
            transcript_section = f"""## 원본 상담 전사록
```
{transcript[:3000]}
```

"""

        human_prompt = f"""다음 원본 상담 전사록과 두 모델의 QA 평가 결과를 비교 분석해 주세요.

{transcript_section}## 모델 A: {left_model}
```json
{left_json}
```

## 모델 B: {right_model}
```json
{right_json}
```

아래 형식에 맞춰 분석하세요:

---

## 1. 총점 및 등급 비교

아래 테이블 형식으로 작성:

| 구분 | {left_model} | {right_model} |
|------|------------|------------|
| 총점 | ?점 | ?점 |
| 등급 | ? | ? |
| 점수 차이 | — | — |

차이가 발생한 주요 항목 번호를 나열하세요.

---

## 2. 항목별 점수 차이 — 원문 대조 검증

점수가 **2점 이상** 차이나는 항목만 분석. 각 항목마다:
1. 항목명과 양쪽 점수 (`**#N 항목명** (A: ?점 / B: ?점)`)
2. 원문 발화 인용 (blockquote `>`)
3. 어느 모델이 정확한지 판정 + 아이콘 (✅/❌/⚠️)
4. 한 줄 판정 이유

---

## 3. 근거 품질 비교

각 모델의 evidence 인용이 실제 원문에 존재하는지 교차 확인.
자기모순(근거를 제시하면서 감점)이 있으면 지적.

---

## 4. 오판 항목 종합

테이블로 정리:

| 모델 | 항목 | 판정 점수 | 적정 점수 | 오판 내용 |
|------|------|----------|----------|----------|

---

## 5. 종합 판정

- 어느 모델이 더 우수한지 1문장 결론
- 각 모델의 강점/약점 1~2줄
- 개선 필요 사항"""

        messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]
        response = await llm.ainvoke(messages)
        analysis = _extract_text(response.content)

        logger.info("analyze-compare completed: left=%s, right=%s (tenant=%s)", left_model, right_model, tid)
        return JSONResponse(content={"status": "success", "analysis": analysis, "tenant_id": tid})

    except Exception as e:
        logger.error("analyze-compare error: %s (tenant=%s)", e, tid, exc_info=True)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e), "tenant_id": tid})


# ---------------------------------------------------------------------------
# POST /analyze-manual-compare
# ---------------------------------------------------------------------------


_ITEM_NAME_TO_NUMBER = {
    "첫인사": 1,
    "끝인사": 2,
    "경청": 3,
    "경청 말겹침/말자름": 3,
    "경청(말겹침/말자름)": 3,
    "호응 및 공감": 4,
    "대기 멘트": 5,
    "정중한 표현": 6,
    "쿠션어 활용": 7,
    "문의 파악 및 재확인": 8,
    "문의 파악 및 재확인(복창)": 8,
    "고객정보 확인": 9,
    "설명의 명확성": 10,
    "두괄식 답변": 11,
    "문제 해결 의지": 12,
    "부연 설명 및 추가 안내": 13,
    "사후 안내": 14,
    "정확한 안내": 15,
    "필수 안내 이행": 16,
    "정보 확인 절차": 17,
    "정보 보호 준수": 18,
}


def _normalize_item(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


_ITEM_NORM_MAP = {_normalize_item(k): v for k, v in _ITEM_NAME_TO_NUMBER.items()}


def _item_name_to_number(name: str, fallback: int | None = None) -> int | None:
    norm = _normalize_item(name)
    if norm in _ITEM_NORM_MAP:
        return _ITEM_NORM_MAP[norm]
    for key, num in _ITEM_NORM_MAP.items():
        if key and key in norm:
            return num
    return fallback


def _extract_ai_items(result: dict[str, Any]) -> dict[int, dict[str, Any]]:
    if not isinstance(result, dict):
        return {}
    containers: list[Any] = [
        result.get("evaluations"),
        result.get("items"),
        result.get("item_scores"),
    ]
    report = result.get("report")
    if isinstance(report, dict):
        containers.extend([report.get("evaluations"), report.get("items"), report.get("item_scores")])
        inner = report.get("report")
        if isinstance(inner, dict):
            containers.extend([inner.get("evaluations"), inner.get("items"), inner.get("item_scores")])

    items: dict[int, dict[str, Any]] = {}
    for arr in containers:
        if not isinstance(arr, list):
            continue
        for raw in arr:
            e = raw.get("evaluation") if isinstance(raw, dict) and isinstance(raw.get("evaluation"), dict) else raw
            if not isinstance(e, dict):
                continue
            num = e.get("item_number") or e.get("item") or e.get("no")
            try:
                num_i = int(num) if num is not None else None
            except (TypeError, ValueError):
                num_i = None
            if num_i is None or num_i in items:
                continue
            items[num_i] = {
                "score": e.get("score"),
                "max_score": e.get("max_score"),
                "item_name": e.get("item_name") or "",
                "deductions": e.get("deductions") or [],
                "evidence": e.get("evidence") or [],
            }
    return items


def _format_ai_evidence(ai: dict[str, Any]) -> str:
    if not ai:
        return ""
    parts: list[str] = []
    deds = ai.get("deductions") or []
    if isinstance(deds, list):
        for d in deds[:2]:
            if isinstance(d, dict):
                reason = str(d.get("reason") or d.get("text") or "").strip()
                pts = d.get("points") or d.get("score_delta")
                if reason:
                    parts.append(reason + (f" (-{abs(pts)}점)" if isinstance(pts, (int, float)) else ""))
            elif isinstance(d, str) and d.strip():
                parts.append(d.strip())
    if not parts:
        ev = ai.get("evidence")
        if isinstance(ev, list):
            for e in ev[:1]:
                if isinstance(e, dict):
                    txt = str(e.get("text") or e.get("excerpt") or "").strip()
                    if txt:
                        parts.append(f'"{txt[:80]}"')
                elif isinstance(e, str) and e.strip():
                    parts.append(e.strip())
    return " / ".join(parts)[:240]


def _verdict_from_diff(ai_score: float | None, qa_score: float | None) -> str:
    if ai_score is None or qa_score is None:
        return "—"
    diff = abs(float(ai_score) - float(qa_score))
    if diff <= 0.5:
        return "일치"
    if diff <= 2.0:
        return "부분차이"
    return "불일치"


def _final_verdict(ai_scores: list[float], qa_score: float | None) -> str:
    if qa_score is None:
        return "⚠️"
    valid = [float(s) for s in ai_scores if s is not None]
    if not valid:
        return "❌"
    max_diff = max(abs(s - float(qa_score)) for s in valid)
    if max_diff <= 0.5:
        return "✅"
    if max_diff <= 2.0:
        return "⚠️"
    return "❌"


def _deterministic_manual_compare(payload: AnalyzeManualCompareRequest, tid: str) -> JSONResponse:
    models = [m.model_dump() for m in payload.models]
    model_names = [m.get("name") or f"모델{i + 1}" for i, m in enumerate(models)]
    ai_items_per_model = [_extract_ai_items(m.get("result") or {}) for m in models]

    rows: list[dict[str, Any]] = []
    assert payload.manual_rows is not None
    for mr in payload.manual_rows:
        item_num = _item_name_to_number(mr.item, fallback=mr.no)
        row: dict[str, Any] = {
            "no": mr.no,
            "category": mr.category,
            "item": mr.item,
            "max_score": mr.max_score,
            "qa_score": mr.qa_score,
            "qa_evidence": mr.qa_evidence or "",
        }
        ai_scores: list[float] = []
        for idx, ai_items in enumerate(ai_items_per_model):
            k = idx + 1
            ai = ai_items.get(item_num or -1) or {}
            s = ai.get("score")
            row[f"model{k}_score"] = s
            row[f"model{k}_evidence"] = _format_ai_evidence(ai)
            row[f"model{k}_verdict"] = _verdict_from_diff(s, mr.qa_score)
            if isinstance(s, (int, float)):
                ai_scores.append(float(s))
        if mr.qa_score is None:
            row["diff_summary"] = "수동 점수 없음"
        elif not ai_scores:
            row["diff_summary"] = "AI 평가 누락"
        else:
            avg = sum(ai_scores) / len(ai_scores)
            row["diff_summary"] = f"수동 {mr.qa_score} vs AI 평균 {avg:.1f} (Δ{avg - float(mr.qa_score):+.1f})"
        row["final_verdict"] = _final_verdict(ai_scores, mr.qa_score)
        rows.append(row)

    manual_total = payload.manual_total
    if manual_total is None:
        manual_total = sum(float(r.qa_score) for r in payload.manual_rows if r.qa_score is not None)

    model_totals: dict[str, float | None] = {}
    match_rate: dict[str, float | None] = {}
    for idx, name in enumerate(model_names):
        k = idx + 1
        scores = [r[f"model{k}_score"] for r in rows if isinstance(r[f"model{k}_score"], (int, float))]
        model_totals[name] = sum(scores) if scores else None
        matches = sum(1 for r in rows if r[f"model{k}_verdict"] == "일치")
        match_rate[name] = round(matches / len(rows), 3) if rows else None

    verdict_parts: list[str] = []
    for name in model_names:
        mt = model_totals.get(name)
        if mt is None:
            verdict_parts.append(f"{name}: AI 점수 없음")
            continue
        d = mt - float(manual_total)
        label = "거의 일치" if abs(d) <= 3 else ("유사" if abs(d) <= 10 else "큰 차이")
        verdict_parts.append(f"{name} 총점 {mt:.0f} (수동 {manual_total:.0f} 대비 {d:+.0f}점 · {label})")
    overall = "; ".join(verdict_parts) if verdict_parts else ""

    summary = {
        "models": model_names,
        "manual_total": manual_total,
        "model_totals": model_totals,
        "match_rate": match_rate,
        "overall_verdict": overall,
    }

    logger.info(
        "analyze-manual-compare (deterministic): models=%s, rows=%d (tenant=%s)", model_names, len(rows), tid
    )
    return JSONResponse(
        content={
            "status": "success",
            "summary": summary,
            "rows": rows,
            "model_names": model_names,
            "tenant_id": tid,
            "raw": "deterministic_parser_v1",
        }
    )


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def _extract_json_block(text: str) -> Any | None:
    if not text:
        return None
    m = _JSON_FENCE_RE.search(text)
    candidate = m.group(1) if m else None
    if candidate is None:
        start = text.find("{")
        alt = text.find("[")
        if alt != -1 and (start == -1 or alt < start):
            start = alt
        if start == -1:
            return None
        candidate = text[start:]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        for end_char in ("}", "]"):
            end = candidate.rfind(end_char)
            if end != -1:
                try:
                    return json.loads(candidate[: end + 1])
                except json.JSONDecodeError:
                    continue
    return None


@router.post("/analyze-manual-compare")
async def analyze_manual_compare(payload: AnalyzeManualCompareRequest, request: Request) -> JSONResponse:
    tid = require_tenant_id(request)

    if payload.manual_rows:
        try:
            return _deterministic_manual_compare(payload, tid)
        except Exception as e:
            logger.error("deterministic manual-compare error: %s (tenant=%s)", e, tid, exc_info=True)
            return JSONResponse(
                status_code=500, content={"status": "error", "message": str(e), "tenant_id": tid}
            )

    models = [m.model_dump() for m in payload.models]
    manual_evaluation = payload.manual_evaluation
    transcript = payload.transcript

    if not models or not manual_evaluation:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "models (array) and manual_evaluation (or manual_rows) are required.",
                "tenant_id": tid,
            },
        )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from nodes.llm import _extract_text, get_chat_model

        llm = get_chat_model(
            backend="bedrock", bedrock_model_id="us.anthropic.claude-sonnet-4-6", temperature=0.2, max_tokens=12000
        )

        is_single = len(models) == 1
        model_names = [m.get("name", f"모델{i + 1}") for i, m in enumerate(models)]

        models_section = ""
        for m in models:
            name = m.get("name", "모델")
            result_json = json.dumps(m.get("result", {}), ensure_ascii=False)
            models_section += f"""## AI 자동 평가: {name}
```json
{result_json}
```

"""

        transcript_section = ""
        if transcript:
            transcript_section = f"""## 원본 상담 전사록
```
{transcript[:3000]}
```

"""

        model_col_specs = [
            {
                "name": name,
                "score_key": f"model{i + 1}_score",
                "evidence_key": f"model{i + 1}_evidence",
                "verdict_key": f"model{i + 1}_verdict",
            }
            for i, name in enumerate(model_names)
        ]
        model_col_spec_lines = "\n".join(
            f'  - "{s["score_key"]}": {s["name"]} 모델 부여 점수 (숫자 또는 null)\n'
            f'  - "{s["evidence_key"]}": {s["name"]} 모델 근거 요약 (1~2문장, 문자열)\n'
            f'  - "{s["verdict_key"]}": {s["name"]} vs 수동 판정 ("일치" | "부분차이" | "불일치")'
            for s in model_col_specs
        )

        system_prompt = (
            "당신은 QA 평가 전문가입니다. AI 자동 평가 결과와 사람(수동) QA 데이터를 항목별로 매칭하여 "
            "엑셀 호환 비교표를 만듭니다.\n\n"
            "## 중요 규칙\n"
            "- 반드시 JSON 객체 하나만 반환 (```json ... ``` 코드펜스 사용)\n"
            "- JSON 이외의 설명·주석·텍스트는 일체 출력 금지\n"
            "- 수동 평가 데이터 포맷은 자유(CSV/JSON/표/평문)이므로 유연하게 파싱\n"
            "- 항목 매칭은 평가항목명(예: 첫인사, 경청, 정확한 안내) 의미 기반으로 정렬\n"
            "- AI 모델 측 점수·근거는 입력 JSON 의 evaluations / items / results 필드에서 추출\n"
            "- 수동 데이터에 해당 항목이 없으면 qa_score=null, qa_evidence=\"\"\n"
            "- 모델 결과에 해당 항목이 없으면 modelN_score=null\n"
            "- 점수 차이 비교 시 AI 모델 평균과 수동 점수를 비교해 verdict 결정\n"
        )

        human_prompt = f"""{transcript_section}{models_section}## 수동 QA 데이터 (자유 포맷)
```
{manual_evaluation}
```

위 정보를 바탕으로 **엑셀 비교표용 JSON** 을 생성하세요.

## 반환 JSON 스키마

```json
{{
  "summary": {{
    "models": {json.dumps(model_names, ensure_ascii=False)},
    "manual_total": <수동 총점 또는 null>,
    "model_totals": {{{", ".join(f'"{n}": <숫자 또는 null>' for n in model_names)}}},
    "match_rate": {{{", ".join(f'"{n}": <일치율 0~1 또는 null>' for n in model_names)}}},
    "overall_verdict": "<2~3문장 종합 판정>"
  }},
  "rows": [
    {{
      "no": 1,
      "category": "<대분류, 예: 인사 예절>",
      "item": "<평가항목명, 예: 첫인사>",
      "max_score": <배점 숫자>,
      "qa_score": <수동 점수 숫자 또는 null>,
      "qa_evidence": "<수동 근거 문자열 or 빈 문자열>",
{model_col_spec_lines},
      "diff_summary": "<차이 요약 1문장>",
      "final_verdict": "✅" | "⚠️" | "❌"
    }}
  ]
}}
```

## 판정 규칙
- 모든 모델과 수동이 ±0.5 이내: `final_verdict="✅"`
- 일부 차이 (1~2점 또는 부분 일치): `final_verdict="⚠️"`
- 큰 차이 (3점 이상) 또는 상반 판정: `final_verdict="❌"`
- `modelN_verdict` 는 해당 모델과 수동 간 비교 결과

## 출력
반드시 아래 형식으로 JSON 하나만 출력:

```json
{{...}}
```"""

        messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]
        response = await llm.ainvoke(messages)
        raw = _extract_text(response.content)

        parsed = _extract_json_block(raw)
        comparison: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
        rows = comparison.get("rows") if isinstance(comparison.get("rows"), list) else []
        summary = comparison.get("summary") if isinstance(comparison.get("summary"), dict) else {}

        logger.info(
            "analyze-manual-compare completed: models=%s, rows=%d, single=%s (tenant=%s)",
            model_names,
            len(rows),
            is_single,
            tid,
        )
        return JSONResponse(
            content={
                "status": "success",
                "summary": summary,
                "rows": rows,
                "model_names": model_names,
                "tenant_id": tid,
                "raw": raw,
            }
        )

    except Exception as e:
        logger.error("analyze-manual-compare error: %s (tenant=%s)", e, tid, exc_info=True)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e), "tenant_id": tid})
