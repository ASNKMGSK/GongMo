# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
LinearRAG — Tri-Graph 자료구조 및 결과 타입.

Clean-room 구현: ICLR 2026 LinearRAG 논문 (arxiv 2510.10114) 의 알고리즘만
참조하여 V3 환경 (Bedrock · 한국어 · 멀티테넌트 · AOSS) 에 맞게 설계.
원 GPL-3.0 코드와 코드 공유 없음.

§3.1 Tri-Graph:
    Vp (passage 노드), Vs (sentence 노드), Ve (entity 노드)
    C: |Vp| × |Ve| contain matrix    — passage 가 entity 를 포함하면 1
    M: |Vs| × |Ve| mention matrix    — sentence 가 entity 를 언급하면 1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── 노드 타입 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Passage:
    """Vp 노드 — 검색 단위 텍스트.

    KMS 데이터의 경우 표 1행 또는 prose 약관 chunk 1개에 해당.
    """

    pid: str
    text: str
    metadata: dict = field(default_factory=dict)
    # Bedrock Titan Embed v2 결과 (1024-dim L2 정규화). 인덱싱 시 채움.
    embedding: Optional[tuple[float, ...]] = None


@dataclass(frozen=True)
class Sentence:
    """Vs 노드 — 단일 문장. Passage 를 punctuation 으로 분할한 결과."""

    sid: str
    text: str
    parent_pid: str  # 소속 passage
    embedding: Optional[tuple[float, ...]] = None


@dataclass(frozen=True)
class Entity:
    """Ve 노드 — Korean NER 결과 (canonical form).

    canonical_form: 동의어/이형 표기를 통합한 정규형 (예: "환불" / "환불처리" / "리펀드"
                    → 모두 canonical "환불"). 빈 문자열이면 미정규화.
    surface: 원문 표면형 (디버깅/로깅용).
    """

    eid: str
    canonical_form: str
    surface: str
    embedding: Optional[tuple[float, ...]] = None


# ── 검색 결과 타입 ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ActivatedEntity:
    """§3.2.1 Stage 1 결과 — 활성화된 entity 1개."""

    eid: str
    canonical_form: str
    activation_score: float
    iteration: int  # 몇 번째 iteration 에서 활성화됐는지 (0 = initial)


@dataclass(frozen=True)
class RetrievedPassage:
    """§3.2.2 Stage 2 결과 — top-k passage 1개."""

    pid: str
    text: str
    ppr_score: float
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class LinearRAGResult:
    """Retrieval API 최종 반환."""

    passages: list[RetrievedPassage]
    activated_entities: list[ActivatedEntity]
    # 디버깅/감사용 — Stage 1 iteration 별 entity 수, Stage 2 PPR 수렴 정보
    diagnostics: dict = field(default_factory=dict)


# ── 예외 ────────────────────────────────────────────────────────────────


class LinearRAGError(Exception):
    """LinearRAG 일반 예외."""


class IndexingError(LinearRAGError):
    """인덱싱 단계 실패 — NER, embedding, 매트릭스 저장 등."""


class RetrievalError(LinearRAGError):
    """검색 단계 실패 — Stage 1/2 어느 단계라도 복구 불가 시."""


class TenantNotIndexed(LinearRAGError):
    """해당 tenant 의 Tri-Graph 가 아직 빌드되지 않음."""
