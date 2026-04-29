# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""토론 요청/응답 Pydantic 스키마 (qa-pipeline 인프로세스 버전).

CLAUDE.md 의 Phase 2 Debate 스펙을 구현체에 반영:
 - ``DebateRequest`` : run_debate() 입력 — 단일 평가 항목.
 - ``PersonaTurn`` / ``ModeratorVerdict`` : 내부 트레이스 포맷.
 - ``DebateRecord`` : QAStateV2.debates[item_no] 에 저장되는 최종 구조 (CLAUDE.md 명시).
 - ``RoundRecord`` : 라운드 단위 — CLAUDE.md 의 ``turns[].argument`` / ``verdict.rationale`` 필드명 준수.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


# ===========================================================================
# 토론 라운드 SSoT (Single Source of Truth) — 사용자 정책 2026-04-27
# ===========================================================================
# 모든 토론 노드/팀/스키마/env reader/프론트가 이 상수에서만 default 를 도출.
# env QA_DEBATE_MAX_ROUNDS 가 set 되면 우선이지만, default fallback 은 항상 이 값.
# 변경 시 .env 파일들과 chatbot-ui-next/DEPLOYMENT.md 도 동시 갱신.
DEFAULT_MAX_ROUNDS: int = 2


class DebateRequest(BaseModel):
    """LangGraph debate_node 가 run_debate() 에 넘기는 단일 평가 항목 요청."""

    consultation_id: str = Field(..., description="상담 ID — 추적용")
    item_number: int = Field(..., ge=1, le=18, description="평가 항목 번호 (1~18)")
    item_name: str = Field(..., description="예: 첫인사, 경청, 문제해결 의지")
    max_score: int = Field(..., description="해당 항목 최대 점수 (5 / 10 / 15)")
    allowed_steps: list[int] = Field(..., description="ALLOWED_STEPS — 이 단계 외 점수 반환 금지")
    transcript: str = Field(..., description="상담 원문 (마스킹 완료 상태)")
    rag_context: str | None = Field(None, description="RAG 에서 받은 관련 규정/가이드")
    ai_evidence: list[dict] | None = Field(None, description="AI 가 뽑은 근거 발화 (legacy — 토론 페르소나에는 미주입)")
    ai_judgment: str | None = Field(None, description="AI 의 합쳐진 판정 사유 (legacy — 토론 페르소나에는 미주입)")
    persona_details: dict[str, dict] | None = Field(
        None,
        description=(
            "페르소나별 1차 평가 결과 {strict: {score, judgment, deductions, evidence}, ...}. "
            "토론 라운드 1 첫 발언 시 각 페르소나는 본인 키의 결과만 보고 출발 — "
            "다른 페르소나의 1차 결과는 가려짐 (편향 차단)."
        ),
    )
    initial_positions: dict[str, int] = Field(
        ..., description="각 페르소나의 초기 점수 {strict: N, neutral: N, loose: N}"
    )
    max_rounds: int = Field(
        DEFAULT_MAX_ROUNDS,
        ge=1,
        le=10,
        description=f"최대 토론 라운드 (기본 {DEFAULT_MAX_ROUNDS} — schemas.DEFAULT_MAX_ROUNDS SSoT)",
    )
    consensus_threshold: int = Field(0, ge=0, description="합의 판정 임계값 — 최대-최소 점수 차이 <= 이 값이면 합의")


class PersonaTurn(BaseModel):
    """한 페르소나의 한 라운드 발언."""

    round_no: int
    persona: Literal["strict", "neutral", "loose"]
    persona_label: str
    score: int
    reasoning: str
    rebuttal: str | None = None
    timestamp: str | None = None


class ModeratorVerdict(BaseModel):
    """모더레이터의 라운드 종료 시 판정."""

    round_no: int
    consensus_reached: bool
    spread: int
    standings: dict[str, int]
    next_action: Literal["continue", "finalize", "force_vote"]
    summary: str


class DebateResponse(BaseModel):
    """토론 완료 후 내부 응답 (legacy 호환). 외부 저장은 ``DebateRecord`` 사용."""

    consultation_id: str
    item_number: int
    final_score: int
    merge_rule: Literal["consensus", "median_vote", "majority_vote", "judge_override", "fallback_median"]
    rounds_used: int
    consensus_reached: bool
    transcripts: list[PersonaTurn]
    verdicts: list[ModeratorVerdict]
    final_reasoning: str
    debate_stats: dict


# ===========================================================================
# CLAUDE.md 명시 — QAState 에 저장되는 구조 (외부 계약)
# ===========================================================================


class TurnRecord(BaseModel):
    """라운드 안의 단일 페르소나 발언 — CLAUDE.md 명시 필드명."""

    persona: Literal["strict", "neutral", "loose"]
    score: int
    argument: str  # CLAUDE.md: turns[].argument


class VerdictRecord(BaseModel):
    """라운드 종료 시 모더레이터 판정 — CLAUDE.md 명시."""

    consensus: bool
    score: int | None = None
    rationale: str  # CLAUDE.md: verdict.rationale


class RoundRecord(BaseModel):
    """한 라운드 = turns 3개 + verdict — CLAUDE.md 명시."""

    round: int  # CLAUDE.md: round (round_no 아님)
    turns: list[TurnRecord]
    verdict: VerdictRecord


class DebateRecord(BaseModel):
    """QAStateV2.debates[item_number] 에 저장되는 최종 구조 — CLAUDE.md 명시."""

    item_number: int
    item_name: str
    max_score: int
    allowed_steps: list[int]
    initial_positions: dict[str, int]  # {strict, neutral, loose}
    rounds: list[RoundRecord]
    final_score: float | None  # fallback 이나 unresolved 시 None 가능
    final_rationale: str
    converged: bool
    ended_at: str  # ISO-8601
    # 운영/디버깅 보조 — CLAUDE.md 스키마 외지만 downstream 분석용
    merge_rule: str = "consensus"
    rounds_used: int = 0
    debate_stats: dict = Field(default_factory=dict)
    # 판사 (post-debate) 가 페르소나 형식으로 리턴하는 추가 필드 (2026-04-27).
    # 각 토론 후 판사가 transcript 보고 deductions/evidence 도 명시 — 페르소나 출력과 동일한 정보량.
    # final_score / final_rationale 과 분리 — 메인 본문은 _decide_final 결과 유지, 판사 의견은 별도.
    judge_score: float | None = None
    judge_reasoning: str | None = None
    judge_failure_reason: str | None = None  # 판사 호출 실패 사유 (성공 시 None)
    judge_deductions: list[dict] = Field(default_factory=list)
    judge_evidence: list[dict] = Field(default_factory=list)
    # 판사가 인용한 HITL 인간 검수 사례 — frontend 노드 드로어에 표시 (2026-04-30).
    # 각 entry: {consultation_id, item_number, ai_score, human_score, delta, confirmed_at,
    #            external_id, knn_score, transcript_excerpt, human_note, ai_judgment}
    judge_human_cases: list[dict] = Field(default_factory=list)
