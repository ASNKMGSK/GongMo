# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
QA Pipeline shared state definition.

Defines QAState — the TypedDict that flows through every LangGraph node.
Uses Annotated + operator.add for list fields so parallel branches can
append results independently.
"""

# ---------------------------------------------------------------------------
# operator.add 리듀서(reducer) 동작 원리:
#
#   LangGraph에서 Annotated[list[T], operator.add]로 선언된 필드는
#   여러 노드가 동일 필드에 값을 반환할 때 **덮어쓰기 대신 병합(append)**된다.
#
#   예) 병렬 평가 노드 A가 [결과1]을, 노드 B가 [결과2]를 반환하면
#       최종 상태는 [결과1, 결과2] — 리스트를 operator.add(+)로 합산.
#
#   이 패턴 덕분에 병렬 브랜치가 서로의 결과를 모르는 상태에서도
#   안전하게 evaluations, completed_nodes, node_timings 등에 결과를 추가할 수 있다.
# ---------------------------------------------------------------------------

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


# ---------------------------------------------------------------------------
# 개별 QA 평가 항목 결과 (채점 노드가 항목 하나를 평가할 때마다 생성)
# ---------------------------------------------------------------------------


class EvaluationResult(TypedDict, total=False):
    """Single QA evaluation item result produced by a scoring node."""

    # 평가 처리 상태: "success"(정상 채점), "partial"(일부만 채점), "error"(채점 실패)
    status: str  # "success" | "partial" | "error"

    # 이 결과를 생성한 에이전트(노드)의 식별자 — 어떤 채점 노드가 평가했는지 추적
    agent_id: str  # originating agent identifier

    # 실제 채점 데이터 딕셔너리:
    #   item_number  — 평가기준 번호 (1~18)
    #   item_name    — 평가기준 이름 (예: "첫인사", "설명의 명확성")
    #   max_score    — 해당 항목 배점 (만점, 총합 100점)
    #   score        — 실제 부여 점수
    #   deductions   — 감점 사유 목록
    #   evidence     — 근거 발화/텍스트
    #   confidence   — LLM 채점 신뢰도 (0.0~1.0)
    evaluation: dict[str, Any]  # item_number, item_name, max_score, score, deductions, evidence, confidence


# ---------------------------------------------------------------------------
# 일관성 검증(consistency check) 결과
# ---------------------------------------------------------------------------


class VerificationResult(TypedDict, total=False):
    """Output of the consistency-check node."""

    # 전체 평가 결과가 내부적으로 일관성이 있는지 여부
    is_consistent: bool

    # 사람(QA 관리자)의 추가 검토가 필요한지 여부
    needs_human_review: bool

    # 사람 검토가 필요한 구체적 사유 목록
    human_review_reasons: list[str]

    # 평가 항목 간 충돌(모순)이 발견된 경우의 상세 정보
    conflicts: list[dict[str, Any]]

    # 근거(evidence) 유효성 검증 결과 — {"verified": int, "missing": int}
    evidence_check: dict[str, Any]

    # 총점(합산 점수)과 만점
    total_score: int
    max_possible_score: int

    # 검증 결과 상세 설명 텍스트
    details: str


# ---------------------------------------------------------------------------
# 최종 보고서 구조
# ---------------------------------------------------------------------------


class ReportResult(TypedDict, total=False):
    """Output of the report-generator node."""

    # 종합 요약 정보 (총점, 등급, 한 줄 평가 등)
    summary: dict[str, Any]

    # 항목별 점수 상세 (각 QA 평가기준별 점수와 근거)
    item_scores: list[dict[str, Any]]

    # 감점 내역 목록 (어떤 항목에서 왜 감점되었는지)
    deductions: list[dict[str, Any]]

    # 상담사의 강점(잘한 점) 목록
    strengths: list[str]

    # 개선이 필요한 사항 목록
    improvements: list[str]

    # 코칭 포인트 — 상담사 교육/피드백에 활용할 구체적 조언
    coaching_points: list[dict[str, Any]]

    # 최종 보고서 전체 텍스트 (마크다운 형식)
    full_report_text: str


# ---------------------------------------------------------------------------
# 메인 그래프 상태 — 파이프라인 전체를 관통하는 공유 상태 객체
# ---------------------------------------------------------------------------
# QAState는 LangGraph의 StateGraph에서 사용되는 TypedDict이다.
# 파이프라인의 모든 노드가 이 상태를 읽고 쓴다.
#
# total=False 설정으로 모든 필드가 선택적(Optional)이며,
# 파이프라인 진행에 따라 점진적으로 채워진다.
#
# 데이터 흐름:
#   1. 입력 단계: transcript, consultation_type, customer_id, session_id 설정
#   2. 검색 단계: rules 필드에 QA 규칙/평가기준 저장
#   3. 평가 단계: evaluations 리스트에 채점 결과 누적 (operator.add로 병합)
#   4. 검증 단계: verification 필드에 일관성 검증 결과 저장
#   5. 보고 단계: report 필드에 최종 보고서 저장
# ---------------------------------------------------------------------------


class QAState(TypedDict, total=False):
    """Shared state flowing through the QA evaluation LangGraph pipeline.

    Fields
    ------
    transcript : str
        Raw consultation transcript text (input).
    consultation_type : str
        Consultation category — "insurance", "it_support", "e_commerce", "general".
    customer_id : str
        Customer identifier.
    session_id : str
        Session identifier.

    rules : dict
        Output of the retrieval node (QA rules matched from DB / vector search).

    evaluations : Annotated[list, operator.add]
        Accumulated evaluation results from scoring nodes (mandatory, incorrect_check).
        Uses operator.add reducer so parallel branches can each append independently.

    verification : dict
        Output of the consistency-check node.
    report : dict
        Output of the report-generator node.

    error : str | None
        Set by any node that encounters a fatal error; downstream nodes should
        check and short-circuit when present.
    """

    # -- 입력 필드 (파이프라인 진입 시 1회 설정) --------------------------------
    # 평가 대상 상담 녹취록 원문 텍스트
    transcript: str
    # 상담 유형: "insurance"(보험), "it_support"(IT지원), "e_commerce"(전자상거래), "general"(일반)
    consultation_type: str
    # 고객 식별자 — 평가 결과를 고객별로 추적하기 위해 사용
    customer_id: str
    # 세션 식별자 — 동일 고객의 여러 상담을 구분하기 위해 사용
    session_id: str
    # LLM 백엔드 선택: "bedrock" (Sonnet 4.6) 또는 "sagemaker" (Qwen3-8B vLLM).
    # None/누락 시 환경변수 LLM_BACKEND 기본값 사용.
    llm_backend: str
    # Bedrock 모델 ID 오버라이드 (예: "us.anthropic.claude-haiku-4-5-20251001").
    # None/누락 시 환경변수 BEDROCK_MODEL_ID 기본값 사용. sagemaker 백엔드에선 무시.
    bedrock_model_id: str | None

    # -- 중간 결과 (각 파이프라인 스테이지에서 설정) ----------------------------
    # 검색(retrieval) 노드 출력: DB/벡터 검색으로 매칭된 QA 규칙 및 평가기준
    rules: dict[str, Any]

    # -- 평가 결과 (operator.add 리듀서로 추가 전용) ---------------------------
    # 채점 노드들(필수항목 평가, 오안내 검출 등)이 생성한 결과가 누적되는 리스트.
    # Annotated[..., operator.add] 덕분에 병렬 노드들이 각자 반환한 리스트가
    # 자동으로 합쳐진다 (예: [A결과] + [B결과] → [A결과, B결과]).
    evaluations: Annotated[list[EvaluationResult], operator.add]

    # -- 평가 후처리 -----------------------------------------------------------
    # 일관성 검증 노드 출력: 평가 항목 간 충돌, 신뢰도 낮은 항목 등 검증 결과
    verification: dict[str, Any]
    # 점수 산술 검증 노드 출력: 단계체계/배점/감점합산/누락/타입 검증 결과 (Hard gate)
    # consistency_check 와 병렬로 실행되며, 둘 다 통과해야 report_generator 가 실행됨.
    score_validation: dict[str, Any]
    # 보고서 생성 노드 출력: 최종 QA 평가 보고서 (요약, 점수, 코칭 포인트 등)
    report: dict[str, Any]

    # -- 실행 플랜 (초기 상태로만 주입) ----------------------------------------
    # task_planner 를 거치지 않는 직접 실행 경로에서 주입 가능한 경량 플랜.
    # 현재 지원 키:
    #   - execution_plan: DEFAULT_EXECUTION_PLAN 을 오버라이드 (노드 리스트)
    #   - skip_phase_c_and_reporting: bool — phase_b2 완료 후 즉시 __end__
    #       (프롬프트 튜닝용 경량 실행 — consistency/score_validation/report 스킵)
    plan: dict[str, Any]

    # -- 오케스트레이터 라우팅 (Supervisor 패턴) --------------------------------
    # 현재 파이프라인 진행 단계를 나타내는 문자열.
    # 오케스트레이터 노드가 이 값을 보고 다음 실행할 노드를 결정한다.
    current_phase: str  # "init" | "plan_received" | "retrieval_done" | "parallel_eval" | "verification" | "reporting" | "complete"

    # 오케스트레이터가 결정한 다음 실행 노드 이름.
    # "__parallel__"이면 parallel_targets에 지정된 노드들을 Send()로 병렬 팬아웃.
    next_node: str  # orchestrator decision — single node name or "__parallel__"

    # 병렬 실행 대상 노드 이름 목록.
    # next_node가 "__parallel__"일 때 오케스트레이터가 이 리스트를 설정하며,
    # 라우터 함수가 Send()를 사용해 각 노드에 상태를 전달한다.
    parallel_targets: list[str]  # orchestrator sets this for Send-based parallel fan-out

    # 실행 완료된 노드 이름 추적 리스트.
    # operator.add 리듀서로 각 노드가 자신의 이름을 추가하여
    # 오케스트레이터가 모든 노드 완료 여부를 판단할 수 있게 한다.
    completed_nodes: Annotated[list[str], operator.add]  # tracks which nodes have finished

    # -- 성능 진단 정보 --------------------------------------------------------
    # 각 노드의 실행 시간 기록 리스트.
    # operator.add 리듀서로 각 노드가 {"node": 이름, "elapsed": 초} 형태를 추가.
    # 파이프라인 병목 분석 및 성능 최적화에 활용.
    node_timings: Annotated[list[dict[str, Any]], operator.add]  # [{"node": name, "elapsed": seconds}]

    # 각 노드의 입력/출력 트레이스 정보 (LangSmith-style 디버깅용).
    # _make_tracked_node 래퍼가 각 노드 실행 전후로 자동 수집한다.
    # 구조: [{"node": name, "elapsed": seconds, "input": {...}, "output": {...}}]
    node_traces: Annotated[list[dict[str, Any]], operator.add]

    # -- 전처리 결과 (Dialogue Parser 출력) -----------------------------------
    # 전사록을 구조화한 결과: 턴 목록, 구간(도입/본문/종결), 화자 분리, 턴 페어.
    # dialogue_parser_node가 1회 설정하며, 하류 노드들이 읽기 전용으로 참조한다.
    # 구조: {"turns": [...], "segments": {...}, "agent_turns": [...],
    #        "customer_turns": [...], "turn_pairs": [...]}
    parsed_dialogue: dict[str, Any]

    # 각 평가 에이전트에 할당된 턴 범위와 조립된 텍스트.
    # dialogue_parser_node가 생성하며, 오케스트레이터가 각 에이전트에 전달한다.
    # 구조: {"greeting": {"description": "...", "turn_ids": [...],
    #         "turns": [...], "text": "..."}, "understanding": {...}, ...}
    agent_turn_assignments: dict[str, Any]

    # -- LLM Wiki 공유 메모리 (에이전트 간 지식 교환) ----------------------------

    # 의도 요약 — mandatory 에이전트가 작성, scope/proactiveness/work_accuracy가 읽음.
    # 상담 전체의 주요 의도·제품·복잡도를 정리하여 하류 에이전트가 맥락을 빠르게 파악.
    # 구조: {"primary_intent": str, "sub_intents": list, "product": str, "complexity": str}
    intent_summary: dict[str, Any]

    # 감점 로그 — 모든 평가 에이전트가 추가(append), consistency_check가 읽음.
    # operator.add 리듀서로 병렬 에이전트들이 동시에 안전하게 감점 사유를 누적.
    # 각 항목: {"agent_id": str, "item_number": int, "reason": str, "points": int, "turn_ref": str}
    deduction_log: Annotated[list[dict[str, Any]], operator.add]

    # 정확성 판정 — work_accuracy가 작성, proactiveness가 읽음.
    # 오안내 여부·심각도·상세 내용을 기록하여 프로세스 평가 시 참조.
    # 구조: {"has_incorrect_guidance": bool, "severity": str, "details": str}
    accuracy_verdict: dict[str, Any]

    # 플래그 — incorrect_check가 작성, report_generator가 읽음.
    # 개인정보 위반, 선제적 안내 누락 등 특수 상황 플래그를 기록.
    # 구조: {"privacy_violation": bool, "preemptive_disclosure": bool, "details": list}
    flags: dict[str, Any]

    # -- 에러 전파 -------------------------------------------------------------
    # 어떤 노드에서든 치명적 오류 발생 시 이 필드에 에러 메시지를 설정한다.
    # 하류(downstream) 노드들은 이 값이 존재하면 처리를 건너뛰고(short-circuit)
    # 에러 상태를 그대로 전파해야 한다.
    error: str | None
