# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST 엔드포인트 입력 스키마 (Pydantic v2) — 멀티테넌트 확장.

단일 테넌트 버전(packages/agentcore-agents/qa-pipeline/routers/schemas.py)과 필드는 동일하나
테넌트 식별은 스키마가 아닌 `request.state.tenant_id` 에서 읽는다 (ARCHITECTURE.md §1).

`transcript` / `CONTENT` 최대 길이는 AgentCore 요청 크기 및 LLM 컨텍스트 제약을 따른다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Literal


MAX_TRANSCRIPT_LENGTH = 50000


class EvaluateRequest(BaseModel):
    """POST /evaluate 요청."""

    model_config = ConfigDict(extra="allow")

    transcript: str = Field(..., min_length=1, max_length=MAX_TRANSCRIPT_LENGTH)
    consultation_type: str = Field(default="general", max_length=100)
    customer_id: str = Field(default="anonymous", max_length=200)
    session_id: str | None = Field(default=None, max_length=200)
    llm_backend: str | None = Field(default=None, max_length=100)
    bedrock_model_id: str | None = Field(default=None, max_length=200)


class EvaluateCsvRequest(BaseModel):
    """POST /evaluate/csv-compatible 요청 — DB I/O 명세서 포맷 (배치/CSV 연계용)."""

    model_config = ConfigDict(extra="allow")

    ID: str = Field(..., min_length=1, max_length=200)
    CALL_SEQ: str | int = Field(...)
    CDATE: str = Field(..., min_length=1, max_length=50)
    UID: str = Field(..., min_length=1, max_length=200)
    CONTENT: str = Field(..., min_length=1, max_length=MAX_TRANSCRIPT_LENGTH)
    llm_backend: str | None = Field(default=None, max_length=100)
    bedrock_model_id: str | None = Field(default=None, max_length=200)


class EvaluatePentagonRequest(BaseModel):
    """POST /evaluate/pentagon 요청."""

    model_config = ConfigDict(extra="allow")

    CONTENT: str = Field(..., min_length=1, max_length=MAX_TRANSCRIPT_LENGTH)
    ID: str = Field(default="", max_length=200)
    CALL_SEQ: str | int = Field(default="")
    CDATE: str = Field(default="", max_length=50)
    UID: str = Field(default="", max_length=200)
    llm_backend: str | None = Field(default=None, max_length=100)
    bedrock_model_id: str | None = Field(default=None, max_length=200)


class AnalyzeCompareRequest(BaseModel):
    """POST /analyze-compare 요청."""

    left_result: dict[str, Any] = Field(...)
    right_result: dict[str, Any] = Field(...)
    left_model: str = Field(default="모델 A", max_length=200)
    right_model: str = Field(default="모델 B", max_length=200)
    transcript: str = Field(default="", max_length=MAX_TRANSCRIPT_LENGTH)


class AnalyzeManualCompareModel(BaseModel):
    name: str = Field(default="모델", max_length=200)
    result: dict[str, Any] = Field(default_factory=dict)


class AnalyzeManualCompareManualRow(BaseModel):
    no: int = Field(..., ge=1, le=100)
    category: str = Field(default="", max_length=100)
    item: str = Field(..., min_length=1, max_length=100)
    max_score: float | None = Field(default=None)
    qa_score: float | None = Field(default=None)
    qa_evidence: str = Field(default="", max_length=2000)


class AnalyzeManualCompareRequest(BaseModel):
    models: list[AnalyzeManualCompareModel] = Field(..., min_length=1, max_length=5)
    manual_rows: list[AnalyzeManualCompareManualRow] | None = Field(default=None)
    manual_total: float | None = Field(default=None)
    manual_evaluation: str = Field(default="", max_length=MAX_TRANSCRIPT_LENGTH)
    transcript: str = Field(default="", max_length=MAX_TRANSCRIPT_LENGTH)


class WikiQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=5000)


class WikiSaveAnswerRequest(BaseModel):
    question: str = Field(default="", max_length=5000)
    answer: str = Field(..., min_length=1, max_length=MAX_TRANSCRIPT_LENGTH)
    title: str = Field(default="", max_length=500)


class WikiIngestRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=500)
    force: bool = Field(default=False)


# ---------------------------------------------------------------------------
# 테넌트 관리자용 스키마 (routers/me.py)
# ---------------------------------------------------------------------------


class TenantConfigCreateRequest(BaseModel):
    """POST /admin/tenants — 신규 테넌트 생성 요청 (admin 전용).

    ARCHITECTURE.md §2 의 TenantConfig 스키마를 입력으로 받는다.
    실제 dataclass 로 변환은 `tenant.store.create_config` 가 담당 (Dev4).
    """

    model_config = ConfigDict(extra="forbid")

    # Dev4 TenantConfig._TENANT_ID_RE 와 동일 규칙 — 소문자/숫자/언더스코어, 2~64자
    tenant_id: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    display_name: str = Field(..., min_length=1, max_length=200)
    industry: Literal[
        "industrial", "insurance", "ecommerce", "banking", "healthcare", "telco", "generic"
    ] = Field(...)
    qa_items_enabled: list[int] = Field(default_factory=list)
    score_overrides: dict[int, int] = Field(default_factory=dict)
    default_models: dict[str, str] = Field(default_factory=dict)
    prompt_overrides_dir: str | None = Field(default=None, max_length=500)
    branding: dict[str, Any] = Field(default_factory=dict)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10000)
    storage_quota_gb: int = Field(default=10, ge=1, le=10000)
    is_active: bool = Field(default=True)
