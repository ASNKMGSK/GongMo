# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# =============================================================================
# QA Pipeline 중앙 설정 모듈
# =============================================================================
# 모든 환경별 설정을 데이터클래스로 관리한다.
# 환경변수 → dataclass 기본값 순서로 결정되며, AppConfig.from_env()로 생성한다.
#
# [설정 그룹]
# - SageMakerConfig: vLLM 엔드포인트 접속 정보 및 추론 파라미터
# - AppConfig: 전체 설정 통합 (싱글턴 인스턴스 제공)
# =============================================================================

"""Centralized configuration for the QA evaluation pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class SageMakerConfig:
    """SageMaker vLLM 엔드포인트 설정.

    환경변수 우선, 없으면 기본값 사용.
    """

    endpoint_name: str = ""
    region: str = ""
    max_concurrent: int = 3
    default_temperature: float = 0.1
    default_max_tokens: int = 4096

    def __post_init__(self) -> None:
        if not self.endpoint_name:
            self.endpoint_name = os.environ.get("SAGEMAKER_ENDPOINT_NAME", "qwen3-8b-vllm")
        if not self.region:
            self.region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "us-east-1"
        if self.max_concurrent == 3:
            env_val = os.environ.get("SAGEMAKER_MAX_CONCURRENT")
            if env_val is not None:
                self.max_concurrent = int(env_val)


@dataclass
class AppConfig:
    """QA Pipeline 전체 설정 — SageMaker 설정."""

    sagemaker: SageMakerConfig = field(default_factory=SageMakerConfig)

    @classmethod
    def from_env(cls) -> AppConfig:
        """환경변수에서 설정을 읽어 AppConfig 인스턴스를 생성한다."""
        return cls(sagemaker=SageMakerConfig())


# ---------------------------------------------------------------------------
# 모듈 레벨 싱글턴 — 다른 모듈에서 `from config import app_config` 로 사용
# ---------------------------------------------------------------------------
app_config: AppConfig = AppConfig.from_env()
