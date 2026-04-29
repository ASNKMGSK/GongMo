# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
V2 RAG 공통 타입 정의.

Dev2/Dev3 Sub Agent 와 공유하는 반환 스키마. 변경 시 설계 계약 위반이므로
다른 팀원에게 SendMessage 로 합의 필요.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# 공통 예외
# ---------------------------------------------------------------------------


class RAGError(Exception):
    """RAG 내부 오류 공통 기반."""


class RAGUnavailable(RAGError):
    """RAG 리소스 부재로 평가 불가 — Sub Agent 는 'unevaluable' 분기 권고."""


# ---------------------------------------------------------------------------
# Golden-set RAG
# ---------------------------------------------------------------------------


@dataclass
class FewshotExample:
    """Golden-set 내 단일 예시."""

    example_id: str
    item_number: int
    score: Optional[float]         # unevaluable 샘플의 경우 None
    score_bucket: str              # full | partial | zero | unevaluable
    intent: str
    segment_text: str
    rationale: str
    rationale_tags: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    rater_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class FewshotResult:
    """`retrieve_fewshot` 반환 스키마."""

    item_number: int
    intent: str
    examples: list[FewshotExample]
    query_segment: str
    match_reason: str              # 선택된 이유 요약 (debug/trace)
    total_pool: int                # 골든셋 내 후보 총 개수


# ---------------------------------------------------------------------------
# Reasoning RAG
# ---------------------------------------------------------------------------


@dataclass
class ReasoningExample:
    """과거 판정 근거 문장 단일 항목."""

    example_id: str
    item_number: int
    rationale: str                 # embedding 대상이 되는 판정 근거 문장
    score: Optional[float]
    rationale_tags: list[str] = field(default_factory=list)
    rater_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReasoningResult:
    """`retrieve_reasoning` 반환 스키마."""

    item_number: int
    examples: list[ReasoningExample]
    stdev: float                   # 예시 점수 표준편차 → confidence 지표
    mean: float                    # 평균 점수 (참고용, 가중평균 산출에 사용 금지!)
    sample_size: int
    query_slice: str
    match_reason: str


# ---------------------------------------------------------------------------
# Business Knowledge RAG
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeChunk:
    """업무지식 매뉴얼 내 단일 chunk."""

    chunk_id: str
    text: str
    intents: list[str]
    tags: list[str]
    source_ref: str
    score: float                   # retrieval 유사도 (0.0 ~ 1.0)


@dataclass
class KnowledgeResult:
    """`retrieve_knowledge` 반환 스키마.

    **3 경로 구분 (Dev3 #15 A안 절충, 2026-04-20)**:
      1. `unevaluable=False, no_hit_but_evaluable=False` — 정상 hit. Sub Agent 점수 산출 가능.
      2. `unevaluable=False, no_hit_but_evaluable=True`  — intent 매칭 chunk 있으나 top hit 유사도 낮음.
         Sub Agent 는 `evaluation_mode=partial_with_review` 로 진행, force_hitl 권고 + llm_self cap.
      3. `unevaluable=True, truly_unevaluable=True`      — 매뉴얼 부재 / intent 범위 밖.
         Sub Agent 는 `evaluation_mode=unevaluable` 로 분기, 점수 산출 중단.
    """

    intent: str
    query: str
    chunks: list[KnowledgeChunk]
    source_refs: list[str]
    unevaluable: bool              # True → #15 Sub Agent 가 unevaluable 분기 (경로 3)
    match_reason: str
    # --- Dev3 A안 절충 flag ---
    no_hit_but_evaluable: bool = False   # 경로 2: chunk 있으나 낮은 confidence
    truly_unevaluable: bool = False      # 경로 3: 매뉴얼 부재 / intent 범위 밖 (unevaluable=True 동반)
