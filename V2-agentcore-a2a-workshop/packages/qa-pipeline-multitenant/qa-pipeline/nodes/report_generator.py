# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# =============================================================================
# 보고서 생성 (Report Generator) 노드
# =============================================================================
# 이 모듈은 QA 파이프라인의 최종 노드로, 모든 평가 결과를 집계하여
# 종합 QA 보고서를 생성한다.
#
# [핵심 역할]
# 1. 점수 집계: 모든 평가 에이전트의 개별 점수를 합산하여 총점/만점/비율 계산
# 2. 등급 부여: 총점 비율에 따라 S(탁월)/A(우수)/B(보통)/C(미흡)/D(부진) 등급 매핑
# 3. LLM 기반 코칭: 강점, 개선점, 코칭 포인트를 증거 기반으로 생성
# 4. 종합 보고서 텍스트: 등급 요약 → 감점 분석 → 증거 인용 → 코칭 → 격려 순으로 구성
#
# [파이프라인 내 위치]
# ...consistency_check(검증 완료) → [report_generator] → 파이프라인 종료
#
# [출력]
# state["report"]에 점수 요약, 항목별 점수, 감점 내역, 강점/개선점/코칭, 전체 보고서 텍스트 저장
# =============================================================================

"""
Report Generator node — produces the final comprehensive QA report.

Ported from: packages/agentcore-agents/report-generator-agent/report_generator.py
Aggregates scores, assigns grades, and uses LLM for coaching insights.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from langchain_core.messages import HumanMessage, SystemMessage
from nodes.llm import LLMTimeoutError, get_chat_model, invoke_and_parse
from state import QAState
from typing import Any


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 한자/중국어 → 한국어 치환 (Qwen3-8B 가 간헐적으로 한자를 섞어 출력하는 문제 대응)
# ---------------------------------------------------------------------------
# QA 리포트 맥락에서 자주 새어 나오는 한자/중국어 어휘를 한글로 치환.
# 매핑에 없는 한자가 남아있으면 경고 로그만 남기고 원문 유지 (디버깅 가능).
_CJK_TO_KO = {
    # 점수/평가 관련
    "满分": "만점", "滿分": "만점",
    "获得": "획득", "獲得": "획득",
    "问题": "문제", "問題": "문제",
    "评价": "평가", "評價": "평가",
    "分数": "점수", "分數": "점수",
    "总分": "총점", "總分": "총점",
    "扣分": "감점",
    "加分": "가점",
    "最高": "최고",
    "最低": "최저",
    # 보고서 공통 어휘
    "改善": "개선",
    "部分": "부분",
    "详细": "상세", "詳細": "상세",
    "规则": "규칙", "規則": "규칙",
    "建议": "제안", "建議": "제안",
    "具体": "구체적", "具體": "구체적",
    "说明": "설명", "說明": "설명",
    "确认": "확인", "確認": "확인",
    "信息": "정보",
    "需要": "필요",
    "通过": "통과", "通過": "통과",
    "结果": "결과", "結果": "결과",
    "过程": "과정", "過程": "과정",
    "强化": "강화", "強化": "강화",
    "表现": "표현", "表現": "표현",
    "客户": "고객", "客戶": "고객",
    "咨询": "상담", "諮詢": "상담",
    "处理": "처리", "處理": "처리",
    "项目": "항목", "項目": "항목",
    "类别": "카테고리", "類別": "카테고리",
    "评估": "평가", "評估": "평가",
    "审核": "검토", "審核": "검토",
    "反馈": "피드백", "反饋": "피드백",
    "重要": "중요",
    "必要": "필수",
    "应该": "해야", "應該": "해야",
    "完成": "완료",
    "错误": "오류", "錯誤": "오류",
    "正确": "정확", "正確": "정확",
    "优点": "강점", "優點": "강점",
    "缺点": "약점", "缺點": "약점",
    "提供": "제공",
    "要求": "요구",
    "开始": "시작", "開始": "시작",
    "结束": "종료", "結束": "종료",
}

# CJK Unified Ideographs 블록 감지 (한자가 남아있는지 확인)
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")

# 매핑 사전의 모든 키를 한 번에 매칭하는 컴파일드 정규식 (긴 키 우선하여 부분 매치 회피)
_CJK_REPLACE_PATTERN = re.compile(
    "|".join(re.escape(k) for k in sorted(_CJK_TO_KO, key=len, reverse=True))
)


def _sanitize_korean(text: str) -> str:
    """한자/중국어를 한국어로 치환. 매핑에 없으면 경고 후 원문 유지."""
    if not isinstance(text, str) or not text:
        return text
    text = _CJK_REPLACE_PATTERN.sub(lambda m: _CJK_TO_KO[m.group(0)], text)
    remaining = _CJK_PATTERN.findall(text)
    if remaining:
        logger.warning("report: 치환되지 않은 한자 %d건 잔존 — %s", len(remaining), remaining[:5])
    return text


def _sanitize_report_recursive(obj: Any) -> Any:
    """dict/list 안의 모든 문자열을 재귀적으로 한자 치환."""
    if isinstance(obj, str):
        return _sanitize_korean(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_report_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_report_recursive(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# G-3: 인용문 환각 방어 — LLM 이 프롬프트 예시 문장을 그대로 복사하는 경우 탐지 + 마킹
# ---------------------------------------------------------------------------
_QUOTE_PATTERN = re.compile(r'"([^"\n]{3,200})"')


def _normalize_for_match(text: str) -> str:
    """인용문 매칭용 정규화: 공백 축소 + 양끝 구두점/어조사 trim."""
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text).strip()
    # 양끝 구두점/말줄임 trim (인용 시 원문과 약간 달라지는 변형 허용)
    t = t.strip(".。,·…~!?()[]{}·")
    return t


def _coerce_text(v: Any) -> str:
    """LLM 응답이 string 이 아니라 list/dict/None 으로 오더라도 안전하게 flatten."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):
        return " ".join(_coerce_text(x) for x in v)
    if isinstance(v, dict):
        # 우선순위: text → quote → excerpt → 그 외 모든 값
        for k in ("text", "quote", "excerpt", "content"):
            if k in v and v[k]:
                return _coerce_text(v[k])
        return " ".join(_coerce_text(x) for x in v.values())
    return str(v)


def _collect_allowed_quote_sources(transcript: str, eval_list: list[dict]) -> str:
    """전사록 + 모든 evaluation evidence text 를 하나의 문자열로 flatten.

    LLM 응답 스키마 위반(evidence_ref 가 list 등) 에 대비해 모든 필드를 _coerce_text 로 정규화.
    """
    pieces: list[str] = [_coerce_text(transcript)]
    for ev in eval_list or []:
        evaluation = ev.get("evaluation", {}) if isinstance(ev, dict) else {}
        for ev_item in evaluation.get("evidence", []) or []:
            if isinstance(ev_item, dict):
                pieces.append(_coerce_text(ev_item.get("text") or ev_item.get("quote") or ""))
            else:
                pieces.append(_coerce_text(ev_item))
        for ded in evaluation.get("deductions", []) or []:
            if isinstance(ded, dict):
                pieces.append(_coerce_text(ded.get("evidence") or ded.get("evidence_ref") or ""))
    # 방어: 혹시라도 str 이 아닌 값이 남아있으면 coerce
    pieces = [p if isinstance(p, str) else _coerce_text(p) for p in pieces]
    return _normalize_for_match(" ".join(pieces))


def _verify_report_quotes(report_text: str, allowed_sources: str) -> tuple[str, list[str]]:
    """full_report_text 의 모든 인용문 중 allowed_sources 에 없는 것을 탐지.

    탐지된 hallucination 인용문은 "[근거 검증 실패] ...원문..." 로 마킹하고
    리스트로 반환 (metadata 기록용).

    예외: "## 코칭 포인트" 섹션(권장 발화 예시) 은 검증 대상에서 제외 — 제안 멘트는
    전사록에 없는 게 정상이다. 코칭 포인트 이하 텍스트는 원문 그대로 반환하되,
    탐지된 hallucination 은 로그용 리스트에는 남긴다.
    """
    if not report_text or not allowed_sources:
        return report_text, []

    # 코칭 포인트 섹션을 분리 (있을 경우)
    coaching_marker = "## 코칭 포인트"
    split_idx = report_text.find(coaching_marker)
    if split_idx >= 0:
        head = report_text[:split_idx]
        tail = report_text[split_idx:]
    else:
        head = report_text
        tail = ""

    hallucinated: list[str] = []

    def _replacer(match: re.Match) -> str:
        raw = match.group(1)
        normalized = _normalize_for_match(raw)
        if not normalized:
            return match.group(0)
        # 정규화된 인용문이 allowed_sources 에 substring 으로 존재하는지 확인
        if normalized in allowed_sources:
            return match.group(0)
        # 짧은 변형 허용 (마지막 3글자 trim 후 재시도)
        if len(normalized) > 8 and normalized[:-2] in allowed_sources:
            return match.group(0)
        hallucinated.append(raw)
        return f'"[근거 검증 실패: {raw}]"'

    cleaned_head = _QUOTE_PATTERN.sub(_replacer, head)

    # 코칭 포인트 섹션은 원문 유지, 대신 hallucination 은 로그용 리스트에만 수집
    if tail:
        for m in _QUOTE_PATTERN.finditer(tail):
            raw = m.group(1)
            normalized = _normalize_for_match(raw)
            if not normalized:
                continue
            if normalized in allowed_sources:
                continue
            if len(normalized) > 8 and normalized[:-2] in allowed_sources:
                continue
            hallucinated.append(raw)

    cleaned = cleaned_head + tail
    return cleaned, hallucinated

# ---------------------------------------------------------------------------
# Grade mapping (ported from report-generator-agent)
# ---------------------------------------------------------------------------
# 등급 매핑 테이블: (기준점수%, 등급문자, 한국어 라벨)
# 높은 기준부터 순서대로 비교하여 첫 번째로 충족하는 등급을 부여
# S: 95% 이상(탁월), A: 90-94%(우수), B: 80-89%(보통), C: 70-79%(미흡), D: 70% 미만(부진)

GRADE_MAP = [(95, "S", "탁월"), (90, "A", "우수"), (80, "B", "보통"), (70, "C", "미흡"), (0, "D", "부진")]

# 보고서 생성용 시스템 프롬프트.
# LLM에게 18개 평가 항목(8개 카테고리, 100점 만점) 정보와 출력 형식을 명시하여
# 구조화된 보고서를 생성하도록 지시.
# 강점(최대 5개), 개선점(최대 5개), 코칭(최대 3개)은 모두 reference_items 필수.
from prompts import load_prompt

# 자체 LANGUAGE RULES 보유로 preamble opt-out
def _get_report_prompt(backend: str | None = None, tenant_id: str = "") -> str:
    # backend="bedrock" 이면 report_generator.sonnet.md 우선, 없으면 .md 폴백.
    # tenant_id 는 Dev4 의 오버라이드 로더로 전달 — 테넌트 전용 프롬프트 우선 조회.
    return load_prompt(
        "report_generator", tenant_id=tenant_id, include_preamble=False, backend=backend,
    )


def _calculate_grade(percentage: float) -> tuple[str, str]:
    """Map a percentage score to a grade letter and Korean label."""
    # 총점 비율(%)을 등급(S/A/B/C/D)과 한국어 라벨로 변환
    # GRADE_MAP을 높은 기준부터 순회하여 첫 번째로 충족하는 등급 반환
    for threshold, grade, label in GRADE_MAP:
        if percentage >= threshold:
            return grade, label
    return "D", "부진"


def _extract_turn_keys(evidence_ref: str) -> list[str]:
    """Extract candidate turn-key strings from an evidence_ref field.

    Handles formats: 'turn_42', 'turn_1, turn_2', 'turn_1_to_3', 'turn_42~49', '42'.
    Returns list of candidate keys to try in order.
    """
    if not evidence_ref:
        return []
    s = str(evidence_ref).strip()
    parts = [p.strip() for p in s.split(",") if p.strip()]
    keys: list[str] = []
    for part in parts:
        cleaned = part.replace("turn_", "").strip()
        if not cleaned:
            continue
        keys.append(cleaned)
        for sep in ("_to_", "~", "-"):
            if sep in cleaned:
                for sub in cleaned.split(sep):
                    sub = sub.strip()
                    if sub and sub not in keys:
                        keys.append(sub)
                break
    return keys


def _aggregate_scores(evaluations: list[dict]) -> tuple[list[dict], list[dict], int, int]:
    """Walk through evaluation results, collect per-item scores and deductions.

    Items with ``evaluation`` dict are included even if they have error status
    (they contribute max_score for accurate percentage).  Items without
    ``evaluation`` key are skipped.

    Returns:
        (item_scores, deductions, total_score, max_score)
    """
    # 모든 평가 결과를 순회하며 항목별 점수와 감점 내역을 수집한다.
    # evaluation 딕셔너리가 없는 항목은 건너뛰고, 에러 항목(score=0)도 max_score에 반영하여
    # 정확한 총점 비율을 계산한다.
    #
    # 각 항목의 상태(status) 결정:
    #   - "pass": 만점 획득 (score == max_score)
    #   - "partial": 부분 점수 (0 < score < max_score)
    #   - "fail": 0점 (score == 0)
    item_scores: list[dict] = []
    deductions: list[dict] = []
    total_score = 0
    max_score = 0

    for eval_result in evaluations:
        # evaluation 딕셔너리가 완전히 없는 항목은 건너뜀
        if "evaluation" not in eval_result:
            continue
        ev = eval_result.get("evaluation", eval_result)

        # evidence 리스트에서 turn → 실제 인용문 텍스트 매핑 구축
        # (리포트 LLM 이 "근거 없음" 으로 폴백하지 않고 실제 발화를 인용하도록 데이터 공급)
        evidence_list = ev.get("evidence", []) or []
        evidence_by_turn: dict[str, str] = {}
        evidence_top: list[dict] = []
        for ev_item in evidence_list:
            if not isinstance(ev_item, dict):
                continue
            raw_turn = ev_item.get("turn")
            if raw_turn in (None, ""):
                raw_turn = ev_item.get("turn_id", "")
            turn_str = str(raw_turn).strip() if raw_turn not in (None, "") else ""
            text = ev_item.get("text", "") or ev_item.get("quote", "")
            if text:
                evidence_top.append({"turn": turn_str, "text": text})
                if turn_str:
                    evidence_by_turn[turn_str] = text

        item_number = ev.get("item_number", 0)
        item_name = ev.get("item_name", "")
        score = ev.get("score", 0)
        item_max = ev.get("max_score", 0)
        total_score += score
        max_score += item_max
        # 항목 상태 판정: 만점/부분점수/0점
        status = "pass" if score == item_max else ("partial" if score > 0 else "fail")
        item_scores.append(
            {
                "item_number": item_number,
                "item_name": item_name,
                "score": score,
                "max_score": item_max,
                "status": status,
                # 리포트 LLM 이 "근거: ..." 블록에서 실제 인용할 수 있도록 상위 3개 증거 전달
                "evidence": evidence_top[:3],
            }
        )

        # 감점 내역 수집: 각 감점 사유, 차감 점수, 증거 참조 + 실제 인용문 기록
        for ded in ev.get("deductions", []):
            evidence_ref = ded.get("evidence_ref") or ded.get("turn_ref", "")
            evidence_quote = ""
            for candidate_key in _extract_turn_keys(evidence_ref):
                if candidate_key in evidence_by_turn:
                    evidence_quote = evidence_by_turn[candidate_key]
                    break
            if not evidence_quote and evidence_top:
                evidence_quote = evidence_top[0].get("text", "")
            deductions.append(
                {
                    "item_number": item_number,
                    "reason": ded.get("reason", ""),
                    "points_lost": ded.get("points", item_max - score),
                    "evidence_ref": evidence_ref,
                    "evidence_quote": evidence_quote,
                }
            )

    return item_scores, deductions, total_score, max_score


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------
# 보고서 생성 메인 노드 함수.
# 점수 집계(규칙 기반)와 코칭 인사이트 생성(LLM 기반)을 결합하여 최종 보고서를 만든다.


async def _generate_combined_report(
    user_message: str, backend: str | None = None, bedrock_model_id: str | None = None,
    tenant_id: str = "",
) -> dict:
    """Generate full report (structured + text) in a single LLM call."""
    # 단일 LLM 호출로 구조화 데이터(강점/개선점/코칭)와 전체 보고서 텍스트를 동시 생성
    # temperature=0.3으로 창의성과 일관성의 균형을 맞춤
    llm = get_chat_model(
        temperature=0.3, max_tokens=4096, backend=backend, bedrock_model_id=bedrock_model_id,
    )
    return await invoke_and_parse(
        llm,
        [
            SystemMessage(content=_get_report_prompt(backend, tenant_id=tenant_id)),
            HumanMessage(content=user_message),
        ],
    )


async def report_generator_node(state: QAState) -> dict[str, Any]:
    """Generate the final QA report from all evaluations and verification.

    Reads ``evaluations`` and ``verification`` from state, aggregates scores,
    assigns a grade (S/A/B/C/D), and invokes LLM in parallel for structured
    data and full report text.
    """
    eval_list = state.get("evaluations", [])
    verification = state.get("verification", {})
    score_validation = state.get("score_validation", {})
    consultation_type = state.get("consultation_type", "general")
    customer_id = state.get("customer_id", "")
    session_id = state.get("session_id", "")

    # 평가 결과가 없으면 에러 반환
    if not eval_list:
        return {"report": {"status": "error", "message": "No evaluations for report generation."}}

    # 에러 항목과 누락 항목을 별도 추적 (보고서에 건수를 표시하기 위함)
    # 에러 항목: LLM 실패 등으로 score=0 처리된 항목 (max_score는 반영)
    errored_evals = [e for e in eval_list if e.get("evaluation", {}).get("error") is True]
    # 누락 항목: evaluation 딕셔너리 자체가 없는 항목 (집계에서 제외)
    skipped_items = [e for e in eval_list if "evaluation" not in e]
    if errored_evals:
        logger.warning(f"Report generator: {len(errored_evals)} evaluation(s) had LLM errors (score=0)")
    if skipped_items:
        logger.warning(f"Report generator: {len(skipped_items)} item(s) without evaluation dict skipped")

    # --- 점수 집계 (규칙 기반, LLM 미사용) ---
    item_scores, deductions, total_score, max_score = _aggregate_scores(eval_list)
    # 총점 비율(%) 계산 및 등급 부여
    percentage = round((total_score / max_score) * 100, 1) if max_score > 0 else 0.0
    grade, grade_label = _calculate_grade(percentage)

    # --- 카테고리별 점수 집계 (8개 카테고리, 100점 만점) ---
    # 각 카테고리에 속하는 항목 번호와 만점을 정의하고 실제 획득 점수를 합산
    category_definitions = {
        "인사 예절": {"items": [1, 2], "max": 10},
        "경청 및 소통": {"items": [3, 4, 5], "max": 15},
        "언어 표현": {"items": [6, 7], "max": 10},
        "니즈 파악": {"items": [8, 9], "max": 10},
        "설명력 및 전달력": {"items": [10, 11], "max": 15},
        "적극성": {"items": [12, 13, 14], "max": 15},
        "업무 정확도": {"items": [15, 16], "max": 15},
        "개인정보 보호": {"items": [17, 18], "max": 10},
    }
    # item_number → score 매핑 구성
    item_score_map = {item["item_number"]: item["score"] for item in item_scores}
    category_scores = []
    for cat_name, cat_def in category_definitions.items():
        cat_score = sum(item_score_map.get(i, 0) for i in cat_def["items"])
        category_scores.append(
            {
                "category": cat_name,
                "score": cat_score,
                "max_score": cat_def["max"],
                "items": cat_def["items"],
            }
        )

    # --- flags 읽기 (incorrect_check가 작성한 개인정보 위반 상세 정보) ---
    flags = state.get("flags", {})

    # --- 공통 감점 항목 확인 (일관성 검증에서 전달받은 결과 활용) ---
    verification_data = verification.get("verification", verification)
    common_penalties = verification_data.get("common_penalties", {})
    common_penalty_notes: list[str] = []

    # 불친절 시 전체 0점 처리 후 후속 블록은 건너뛴다 (이중감산 잠재 버그 회피)
    rudeness_zero = bool(common_penalties.get("rudeness_zero"))

    if rudeness_zero:
        common_penalty_notes.append("※ 불친절 행위로 전체 평가 0점 처리")
        total_score = 0
        percentage = 0.0
        grade, grade_label = "D", "부진"
        for cat in category_scores:
            cat["score"] = 0
        for item in item_scores:
            item["score"] = 0
        logger.warning("Common penalty applied: rudeness_zero — all scores set to 0")

    # 개인정보 유출: 해당 카테고리 0점 + 별도 보고
    # 증거 유무 — flags.details 비어있지 않거나, consistency_check 이 유출을 critical 로 명시한 경우
    privacy_evidence_present = bool(
        (isinstance(flags.get("details"), list) and flags.get("details"))
        or (flags.get("privacy_violation_detail") or "").strip()
        or common_penalties.get("privacy_breach_evidence")
    )
    if common_penalties.get("privacy_breach") or flags.get("privacy_violation"):
        if privacy_evidence_present:
            common_penalty_notes.append("※ 개인정보 유출 - 별도 보고 대상")
            if not rudeness_zero:
                # 개인정보 보호 카테고리(#17, #18) 0점 처리
                for item in item_scores:
                    if item["item_number"] in (17, 18):
                        total_score -= item["score"]
                        item["score"] = 0
                for cat in category_scores:
                    if cat["category"] == "개인정보 보호":
                        cat["score"] = 0
                percentage = round((total_score / max_score) * 100, 1) if max_score > 0 else 0.0
                grade, grade_label = _calculate_grade(percentage)
            logger.warning("Common penalty applied: privacy_breach — 개인정보 보호 category set to 0")
        else:
            common_penalty_notes.append("※ 개인정보 관련 플래그 감지 — 구체적 유출 증거 부재로 점수 미차감")
            logger.info("privacy_breach flagged but no concrete evidence — score untouched")

    # 오안내 미정정: 업무 정확도 카테고리 0점
    if common_penalties.get("uncorrected_misinfo"):
        # 수집: #15/#16 의 감점 사유들에 "정정" 흔적이 있는지
        _CORRECTION_REVERSAL = (
            "정정", "바로잡", "바로 잡", "수정 안내", "재안내", "재 안내",
            "고쳐 안내", "번복", "정정함", "정정하여", "정정하며",
        )
        corrected_found = False
        for item in item_scores:
            if item["item_number"] in (15, 16):
                for ded in item.get("deductions", []) or []:
                    reason = (ded.get("reason") or "") + " " + (ded.get("description") or "")
                    if any(m in reason for m in _CORRECTION_REVERSAL):
                        corrected_found = True
                        break
                if corrected_found:
                    break
        if corrected_found:
            common_penalty_notes.append("※ 오안내 감지되었으나 대화 내 정정 확인 — 업무 정확도 0점 미적용")
            logger.info("uncorrected_misinfo flagged but correction evidence found — score untouched")
        else:
            common_penalty_notes.append("※ 오안내 미정정으로 업무 정확도 0점 처리")
            if not rudeness_zero:
                for item in item_scores:
                    if item["item_number"] in (15, 16):
                        total_score -= item["score"]
                        item["score"] = 0
                for cat in category_scores:
                    if cat["category"] == "업무 정확도":
                        cat["score"] = 0
                percentage = round((total_score / max_score) * 100, 1) if max_score > 0 else 0.0
                grade, grade_label = _calculate_grade(percentage)
            logger.warning("Common penalty applied: uncorrected_misinfo — 업무 정확도 category set to 0")

    # flags 상세 정보를 보고서에 포함
    if flags:
        flag_details = []
        if flags.get("privacy_violation"):
            flag_details.append(f"개인정보 위반: {flags.get('privacy_violation_detail', '상세 정보 없음')}")
        if flags.get("pii_preannouncement"):
            flag_details.append(f"고객정보 선언급: {flags.get('pii_preannouncement_detail', '상세 정보 없음')}")
        if flag_details:
            common_penalty_notes.extend(flag_details)

    # --- 일관성 검증 결과에서 사람 검토 필요 여부 가져오기 ---
    needs_human_review = verification_data.get("needs_human_review", False)

    # --- LLM에 전달할 사용자 메시지 구성 ---
    # 점수 요약, 카테고리별 점수, 항목별 점수, 감점 내역, 공통 감점, 일관성 검증 결과를
    # 모두 포함하여 LLM이 종합적인 코칭 인사이트를 생성할 수 있도록 함
    user_message = (
        f"## Evaluation Summary\n"
        f"- Total Score: {total_score}/{max_score} ({percentage}%)\n"
        f"- Grade: {grade} ({grade_label})\n"
        f"- Items Evaluated: {len(item_scores)}\n\n"
        f"## Category Scores\n{json.dumps(category_scores, ensure_ascii=False, indent=2)}\n\n"
        f"## Per-Item Scores\n{json.dumps(item_scores, ensure_ascii=False, indent=2)}\n\n"
    )
    if deductions:
        user_message += f"## Deductions\n{json.dumps(deductions, ensure_ascii=False, indent=2)}\n\n"
    if common_penalty_notes:
        user_message += f"## Common Penalties\n" + "\n".join(common_penalty_notes) + "\n\n"
    if verification_data:
        verification_summary = {
            "is_consistent": verification_data.get("is_consistent"),
            "needs_human_review": verification_data.get("needs_human_review"),
            "confidence": verification_data.get("confidence"),
            "critical_issues": verification_data.get("critical_issues", []),
            "soft_warnings": verification_data.get("soft_warnings", []),
            "missed_issues": verification_data.get("missed_issues", []),
            "common_penalties": verification_data.get("common_penalties", {}),
            "details": verification_data.get("details", ""),
        }
        user_message += (
            f"## Consistency Verification\n{json.dumps(verification_summary, ensure_ascii=False, indent=2)}\n\n"
        )

    # 점수 산술 검증 결과 (Phase C 병렬로 실행됨 — Hard gate 통과한 시점이므로 항상 passed=True)
    score_val_data = score_validation.get("validation", score_validation) if score_validation else {}
    if score_val_data:
        user_message += (
            f"## Score Arithmetic Validation\n{json.dumps(score_val_data, ensure_ascii=False, indent=2)}\n\n"
        )
    user_message += "## Instructions\nBased on the evaluation data above, generate a comprehensive QA report."

    # --- 단일 LLM 호출: 구조화 데이터 + 전체 보고서 텍스트 동시 생성 ---
    try:
        llm_report = await _generate_combined_report(
            user_message,
            backend=state.get("llm_backend"),
            bedrock_model_id=state.get("bedrock_model_id"),
            tenant_id=(state.get("tenant") or {}).get("tenant_id", ""),
        )
    except LLMTimeoutError:
        raise
    except Exception as e:
        # LLM 실패 시 빈 보고서 구조 반환 (점수 데이터는 규칙 기반이므로 유지됨)
        logger.warning(f"Report LLM failed: {e}")
        llm_report = {"strengths": [], "improvements": [], "coaching_points": [], "full_report_text": ""}

    # LLM 출력에 섞여 나오는 한자/중국어를 한국어로 치환 (Qwen3-8B 특성 대응)
    llm_report = _sanitize_report_recursive(llm_report)

    # G-3: 인용문 환각 검증 — LLM 이 프롬프트 예시를 그대로 복사한 경우 탐지 + 마킹
    transcript_for_verify = state.get("transcript", "")
    allowed_sources = _collect_allowed_quote_sources(transcript_for_verify, eval_list)
    report_text = llm_report.get("full_report_text", "")
    if report_text and allowed_sources:
        cleaned_text, hallucinated_quotes = _verify_report_quotes(report_text, allowed_sources)
        llm_report["full_report_text"] = cleaned_text
        if hallucinated_quotes:
            logger.warning(
                "Report hallucination detected: %d quote(s) not in transcript/evidence — %s",
                len(hallucinated_quotes),
                hallucinated_quotes[:3],
            )
            llm_report["_hallucinated_quotes"] = hallucinated_quotes
    # strengths / improvements / coaching_points 내부의 description/suggestion 도 검증
    # coaching_points 는 "제안 멘트" 성격이므로 마커 치환 대신 원문 유지 + 로그만 남긴다
    # (독자 가독성 보호; 마커 유출 이슈 Iter1 P2-2)
    for section_key in ("strengths", "improvements", "coaching_points"):
        for entry in llm_report.get(section_key, []) or []:
            if not isinstance(entry, dict):
                continue
            for field in ("description", "suggestion"):
                val = entry.get(field, "")
                if isinstance(val, str) and '"' in val:
                    cleaned_val, bad = _verify_report_quotes(val, allowed_sources)
                    if bad:
                        if section_key == "coaching_points":
                            # 원문 유지, 로그만
                            logger.warning(
                                "Hallucinated quote in %s.%s (kept original) — %s",
                                section_key, field, bad[:1]
                            )
                        else:
                            entry[field] = cleaned_val
                            logger.warning(
                                "Hallucinated quote in %s.%s — %s", section_key, field, bad[:1]
                            )

    # --- 일관성 검증 / 점수 산술 검증에서 탐지된 문제를 별도 섹션으로 추출 ---
    # Gate 를 제거했으므로 문제가 있어도 보고서는 생성되지만, 문제 상세는 이 필드에
    # 명시적으로 담아 UI 가 강조 표시할 수 있게 한다.
    critical_issues = verification_data.get("critical_issues", []) or []
    soft_warnings = verification_data.get("soft_warnings", []) or []
    missed_issues = verification_data.get("missed_issues", []) or []
    score_adjustments = verification_data.get("score_adjustments", []) or []
    score_val_issues = score_val_data.get("issues", []) if score_val_data else []

    is_consistent = verification_data.get("is_consistent", True)
    score_passed = score_val_data.get("passed", True) if score_val_data else True

    failure_reasons: list[dict[str, Any]] = []
    for c in critical_issues:
        if not isinstance(c, dict):
            continue
        failure_reasons.append({
            "severity": "critical",
            "origin": "consistency_check",
            "type": c.get("type", "unknown"),
            "source": c.get("source", "llm"),
            "description": c.get("description", ""),
            "affected_items": c.get("affected_items", []),
            "evidence": c.get("evidence", ""),
        })
    for w in soft_warnings:
        if not isinstance(w, dict):
            continue
        failure_reasons.append({
            "severity": "soft",
            "origin": "consistency_check",
            "type": w.get("type", "unknown"),
            "source": w.get("source", "llm"),
            "description": w.get("description", ""),
            "affected_items": w.get("affected_items", []),
            "evidence": w.get("evidence", ""),
        })
    for iss in score_val_issues:
        if not isinstance(iss, dict):
            continue
        failure_reasons.append({
            "severity": iss.get("severity", "soft"),
            "origin": "score_validation",
            "type": iss.get("type", "arithmetic"),
            "source": "rule",
            "description": iss.get("message", iss.get("description", "")),
            "affected_items": [iss.get("item_number")] if iss.get("item_number") else [],
            "evidence": iss.get("detail", ""),
        })

    verification_issues = {
        "is_consistent": is_consistent,
        "score_validation_passed": score_passed,
        "gate_status": "pass" if (is_consistent and score_passed) else "fail",
        "critical_count": sum(1 for r in failure_reasons if r["severity"] == "critical"),
        "soft_count": sum(1 for r in failure_reasons if r["severity"] == "soft"),
        "reasons": failure_reasons,
        "missed_issues": missed_issues,
        "score_adjustments": score_adjustments,
        "summary": verification_data.get("details", ""),
    }

    # 최종 보고서 구조 조립
    report = {
        "summary": {
            "total_score": total_score,          # 총 획득 점수
            "max_score": max_score,              # 총 만점 (100점)
            "percentage": percentage,            # 득점률 (%)
            "grade": grade,                      # 등급 (S/A/B/C/D)
            "grade_label": grade_label,          # 등급 한국어 라벨 (탁월/우수/보통/미흡/부진)
            "evaluation_date": datetime.now().strftime("%Y-%m-%d"),  # 평가 일자
            "consultation_type": consultation_type,  # 상담 유형
            "customer_id": customer_id,          # 고객 ID
            "session_id": session_id,            # 세션 ID
            "items_evaluated": len(item_scores), # 평가된 항목 수 (18개 기준)
            "items_errored": len(errored_evals), # 에러 항목 수
            "items_skipped": len(skipped_items),  # 누락 항목 수
            "needs_human_review": needs_human_review,  # 사람 검토 필요 여부
            "score_validation_passed": score_val_data.get("passed", False),  # 산술 검증 통과 여부
            "consistency_passed": is_consistent,  # 일관성 검증 통과 여부 (보고용 — Gate 없음)
        },
        "verification_issues": verification_issues,  # 일관성/산술 검증 탐지 문제 (UI 강조 표시용)
        "score_validation": score_val_data,      # 점수 산술 검증 결과 전체 (checks / issues / summary)
        "category_scores": category_scores,      # 카테고리별 점수 (8개 카테고리)
        "item_scores": item_scores,              # 항목별 점수 상세 (18개 항목)
        "deductions": deductions,                # 감점 내역
        "common_penalty_notes": common_penalty_notes,  # 공통 감점 사항 문구 (불친절/개인정보유출/오안내미정정)
        "strengths": llm_report.get("strengths", []),        # 강점 목록 (LLM 생성, 최대 5개)
        "improvements": llm_report.get("improvements", []),  # 개선점 목록 (LLM 생성, 최대 5개)
        "coaching_points": llm_report.get("coaching_points", []),  # 코칭 포인트 (LLM 생성, 최대 3개)
        "full_report_text": llm_report.get("full_report_text", ""),  # 전체 보고서 텍스트 (LLM 생성)
    }

    return {
        "report": {
            "status": "success",
            "agent_id": "report-generator-agent",
            "tenant_id": (state.get("tenant") or {}).get("tenant_id", ""),
            "report": report,
        }
    }
