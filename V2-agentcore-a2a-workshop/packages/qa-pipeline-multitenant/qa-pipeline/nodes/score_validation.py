# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# =============================================================================
# 점수 산술 검증 (Score Validation) 노드
# =============================================================================
# 이 모듈은 평가 에이전트들이 생성한 점수가 산술적·구조적으로 타당한지를 검증하는
# 비채점 검증 레이어이다. LLM 을 전혀 호출하지 않고 순수 규칙 기반으로 동작한다.
#
# [consistency_check 와의 역할 분담]
# - consistency_check: "판정의 질" 검증 (증거·신뢰도·에이전트 간 논리·중대 감점)
# - score_validation:  "점수의 수치/구조" 검증 (단계체계·배점상한·감점합산·누락·타입)
#
# [핵심 검증 5종]
# 1. 단계 체계 (stepped scoring)   — qa_rules 의 허용 점수값만 사용했는지
# 2. 배점 상한 (max_score)          — score ≤ max_score 이고 음수 아님
# 3. 감점 합산 (deduction sum)       — max_score - score == Σ deductions.points
# 4. 항목 누락 (coverage)            — 1~18 항목이 모두 평가되었는지
# 5. 타입/값 이상치 (type validity)   — score/max_score 가 int, confidence 가 0~1
#
# [파이프라인 내 위치]
# Phase B(평가) 완료 → [consistency_check || score_validation] (병렬) → gate → report_generator
#
# [출력]
# state["score_validation"] 에 검증 결과, 위반 항목, passed 여부 저장.
# passed == True 이고 consistency_check.is_consistent == True 일 때만
# orchestrator 가 report_generator 를 실행한다 (Hard gate).
# =============================================================================

"""
Score Validation node — rule-based arithmetic and structural validation.

Non-scoring verification layer: ensures that all 18 items have arithmetically
valid scores (step compliance, max-score bound, deduction sum match, coverage,
type validity). Returns ``score_validation`` dict keyed by check name.

No LLM invocation — purely deterministic.
"""

from __future__ import annotations

import logging
from nodes.qa_rules import QA_RULES, get_rule_by_item_number
from state import QAState
from typing import Any


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 항목별 허용 점수값 사전 계산
# ---------------------------------------------------------------------------
# qa_rules.py 의 각 항목에서 max_score 와 deduction_rules 의 to_score 값을 모아
# 허용 점수 집합을 구성한다. 예) #10 → {10, 7, 5, 0}, #17 → {5, 0}


def _build_allowed_scores() -> dict[int, set[int]]:
    """Pre-compute {item_number -> allowed score values} from QA_RULES."""
    allowed: dict[int, set[int]] = {}
    for rule in QA_RULES:
        item_num = rule["item_number"]
        allowed[item_num] = {rule["max_score"]}
        for dr in rule.get("deduction_rules", []):
            if "to_score" in dr:
                allowed[item_num].add(dr["to_score"])
    return allowed


_ALLOWED_SCORES: dict[int, set[int]] = _build_allowed_scores()

# 평가 대상 항목 번호 집합 (1~18)
_EXPECTED_ITEMS: set[int] = {rule["item_number"] for rule in QA_RULES}


# ---------------------------------------------------------------------------
# 검증 헬퍼 함수
# ---------------------------------------------------------------------------


def _check_stepped_score(item_number: int, score: int) -> str | None:
    """항목별 허용 점수값 준수 여부 확인. 위반 시 사유 문자열 반환."""
    allowed = _ALLOWED_SCORES.get(item_number)
    if allowed is None:
        return f"알 수 없는 항목 번호 #{item_number}"
    if score not in allowed:
        sorted_allowed = sorted(allowed, reverse=True)
        return f"점수 {score}는 항목 #{item_number}의 허용 단계 {sorted_allowed} 에 속하지 않음"
    return None


def _check_max_score_bound(item_number: int, score: int, max_score: int) -> str | None:
    """배점 상한 및 음수 금지 검증."""
    rule = get_rule_by_item_number(item_number)
    expected_max = rule["max_score"] if rule else None

    if expected_max is not None and max_score != expected_max:
        return f"max_score {max_score}가 규칙상 {expected_max} 과 다름"
    if score < 0:
        return f"음수 점수 {score} 가 부여됨"
    if score > max_score:
        return f"점수 {score}가 배점 상한 {max_score} 을 초과"
    return None


def _check_deduction_arithmetic(
    item_number: int, score: int, max_score: int, deductions: list[dict]
) -> str | None:
    """감점 합산 검증: max_score - score == Σ deductions.points 여야 함.

    단, 다음 관대한 정책을 적용한다:
    - 감점 내역이 비어있고 score == max_score 이면 OK (만점 + 감점 없음)
    - 감점 내역이 있으면 합산 정확히 일치 필요
    """
    deducted_total = sum(int(d.get("points", 0) or 0) for d in deductions)
    expected_deduction = max_score - score

    # 만점이고 감점 내역 없음 → OK
    if score == max_score and not deductions:
        return None

    if expected_deduction != deducted_total:
        return (
            f"항목 #{item_number}: 감점 합산 불일치 — "
            f"(max {max_score} - score {score}) = {expected_deduction}, "
            f"그러나 deductions 합계 = {deducted_total}"
        )
    return None


def _check_type_validity(
    agent_id: str,
    item_number: Any,
    score: Any,
    max_score: Any,
    confidence: Any,
) -> list[str]:
    """타입/값 이상치 검사. 위반 항목 목록을 반환."""
    issues: list[str] = []

    if not isinstance(item_number, int):
        issues.append(f"item_number 타입 오류: {type(item_number).__name__}={item_number!r}")
    if not isinstance(score, int) or isinstance(score, bool):
        issues.append(f"score 타입 오류: {type(score).__name__}={score!r}")
    if not isinstance(max_score, int) or isinstance(max_score, bool):
        issues.append(f"max_score 타입 오류: {type(max_score).__name__}={max_score!r}")
    if confidence is not None:
        try:
            conf_f = float(confidence)
            if not (0.0 <= conf_f <= 1.0):
                issues.append(f"confidence {conf_f} 가 [0.0, 1.0] 범위를 벗어남")
        except (TypeError, ValueError):
            issues.append(f"confidence 타입 오류: {type(confidence).__name__}={confidence!r}")

    if issues:
        return [f"[{agent_id} #{item_number}] {m}" for m in issues]
    return []


# ---------------------------------------------------------------------------
# 메인 노드 함수
# ---------------------------------------------------------------------------


def score_validation_node(state: QAState) -> dict[str, Any]:
    """Validate arithmetic and structural correctness of all evaluations.

    Checks:
      1. stepped_scores       — score must be in qa_rules allowed set
      2. max_score_bound      — score within [0, max_score] and max_score matches rule
      3. deduction_arithmetic — max_score - score == sum(deductions.points)
      4. item_coverage        — all items 1~18 are evaluated
      5. type_validity        — score/max_score are int, confidence in [0, 1]

    Returns the ``score_validation`` key with ``passed`` flag used by the
    orchestrator as a Hard gate for report_generator.
    """
    raw_eval_list = state.get("evaluations", [])

    if not raw_eval_list:
        logger.warning("score_validation: no evaluations to validate")
        return {
            "score_validation": {
                "status": "error",
                "agent_id": "score-validation-agent",
                "validation": {
                    "passed": False,
                    "summary": "평가 결과가 비어있음 — 검증 불가",
                    "issues": [],
                    "checks": {},
                    "total_items_checked": 0,
                },
            }
        }

    # evaluation dict 가 있는 항목만 수집 (error 항목도 포함)
    eval_list = [e for e in raw_eval_list if "evaluation" in e]

    # 검증 결과 누적용 구조
    stepped_violations: list[dict] = []
    max_score_violations: list[dict] = []
    arithmetic_violations: list[dict] = []
    type_violations: list[dict] = []

    stepped_pass = 0
    max_score_pass = 0
    arithmetic_pass = 0
    type_pass = 0

    found_items: set[int] = set()

    for eval_result in eval_list:
        agent_id = eval_result.get("agent_id", "unknown")
        ev = eval_result.get("evaluation", {})

        item_number = ev.get("item_number")
        score = ev.get("score", 0)
        max_score = ev.get("max_score", 0)
        deductions = ev.get("deductions", []) or []
        confidence = ev.get("confidence")
        is_errored = ev.get("error") is True

        # 타입 검증 (모든 항목에서 수행)
        type_issues = _check_type_validity(agent_id, item_number, score, max_score, confidence)
        if type_issues:
            for msg in type_issues:
                type_violations.append(
                    {
                        "agent_id": agent_id,
                        "item_number": item_number if isinstance(item_number, int) else None,
                        "description": msg,
                        "severity": "error",
                    }
                )
        else:
            type_pass += 1

        # item_number 가 int 가 아니면 이후 수치 검증은 스킵 (coverage 에도 포함 불가)
        if not isinstance(item_number, int):
            continue
        found_items.add(item_number)

        # 에러 항목(LLM 실패)은 score=0 으로 강제 처리되었을 뿐이므로 산술 검증 대상에서 제외
        if is_errored:
            continue

        # 1. 단계 체계 준수
        step_issue = _check_stepped_score(item_number, score)
        if step_issue:
            stepped_violations.append(
                {
                    "agent_id": agent_id,
                    "item_number": item_number,
                    "score": score,
                    "allowed": sorted(_ALLOWED_SCORES.get(item_number, set()), reverse=True),
                    "description": step_issue,
                    "severity": "error",
                }
            )
        else:
            stepped_pass += 1

        # 2. 배점 상한
        bound_issue = _check_max_score_bound(item_number, score, max_score)
        if bound_issue:
            max_score_violations.append(
                {
                    "agent_id": agent_id,
                    "item_number": item_number,
                    "score": score,
                    "max_score": max_score,
                    "description": bound_issue,
                    "severity": "error",
                }
            )
        else:
            max_score_pass += 1

        # 3. 감점 합산
        arith_issue = _check_deduction_arithmetic(item_number, score, max_score, deductions)
        if arith_issue:
            arithmetic_violations.append(
                {
                    "agent_id": agent_id,
                    "item_number": item_number,
                    "score": score,
                    "max_score": max_score,
                    "deductions_sum": sum(int(d.get("points", 0) or 0) for d in deductions),
                    "description": arith_issue,
                    "severity": "error",
                }
            )
        else:
            arithmetic_pass += 1

    # 4. 항목 누락 검증
    missing_items = sorted(_EXPECTED_ITEMS - found_items)
    coverage_passed = len(missing_items) == 0

    # 종합 통합 이슈 리스트 (UI 렌더링 편의용)
    all_issues: list[dict] = []
    for v in stepped_violations:
        all_issues.append({**v, "type": "invalid_step"})
    for v in max_score_violations:
        all_issues.append({**v, "type": "max_score_violation"})
    for v in arithmetic_violations:
        all_issues.append({**v, "type": "arithmetic_mismatch"})
    for v in type_violations:
        all_issues.append({**v, "type": "type_error"})
    for item_num in missing_items:
        all_issues.append(
            {
                "type": "missing_item",
                "item_number": item_num,
                "description": f"항목 #{item_num} 평가 누락",
                "severity": "error",
            }
        )

    # 최종 gate 판정: 모든 검증이 통과해야 passed=True
    passed = (
        not stepped_violations
        and not max_score_violations
        and not arithmetic_violations
        and not type_violations
        and coverage_passed
    )

    # 요약 텍스트
    if passed:
        summary = f"전체 {len(found_items)}개 항목 산술 검증 통과 — 단계체계/배점/감점합산/누락/타입 정상"
    else:
        parts = []
        if stepped_violations:
            parts.append(f"단계위반 {len(stepped_violations)}건")
        if max_score_violations:
            parts.append(f"배점위반 {len(max_score_violations)}건")
        if arithmetic_violations:
            parts.append(f"감점합산불일치 {len(arithmetic_violations)}건")
        if missing_items:
            parts.append(f"누락항목 {len(missing_items)}건({missing_items})")
        if type_violations:
            parts.append(f"타입오류 {len(type_violations)}건")
        summary = "점수 산술 검증 실패 — " + ", ".join(parts)

    logger.info(
        "score_validation: passed=%s, items_checked=%d, issues=%d",
        passed,
        len(found_items),
        len(all_issues),
    )

    return {
        "score_validation": {
            "status": "success",
            "agent_id": "score-validation-agent",
            "validation": {
                "passed": passed,                        # Gate 판정: True 여야 report_generator 실행
                "total_items_checked": len(found_items),
                "expected_items": len(_EXPECTED_ITEMS),  # 18
                "found_items": sorted(found_items),
                "missing_items": missing_items,
                "issues": all_issues,                    # 통합 이슈 목록 (UI 렌더링)
                "checks": {
                    "stepped_scores": {
                        "passed": stepped_pass,
                        "failed": len(stepped_violations),
                        "violations": stepped_violations,
                    },
                    "max_score_bound": {
                        "passed": max_score_pass,
                        "failed": len(max_score_violations),
                        "violations": max_score_violations,
                    },
                    "deduction_arithmetic": {
                        "passed": arithmetic_pass,
                        "failed": len(arithmetic_violations),
                        "violations": arithmetic_violations,
                    },
                    "item_coverage": {
                        "expected": len(_EXPECTED_ITEMS),
                        "found": len(found_items),
                        "missing": missing_items,
                        "passed": coverage_passed,
                    },
                    "type_validity": {
                        "passed": type_pass,
                        "failed": len(type_violations),
                        "violations": type_violations,
                    },
                },
                "summary": summary,
            },
        }
    }
