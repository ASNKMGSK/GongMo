# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Industrial 업종 프리셋 (코오롱 등 산업재).

특징
- 21개 전체 평가 항목 활성화 (원본 기준)
- 자사 특화 필수 멘트 (item 16) 가중치 유지
- 기본 모델: Sonnet-4 primary, Haiku-4.5 fast
"""

from __future__ import annotations

from ..config import TenantConfig


def build() -> TenantConfig:
    return TenantConfig(
        tenant_id="preset_industrial",
        display_name="Industrial Preset",
        industry="industrial",
        qa_items_enabled=list(range(1, 22)),
        score_overrides={},
        default_models={
            "primary": "us.anthropic.claude-sonnet-4-20250514-v1:0",
            "fast": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        },
        prompt_overrides_dir=None,
        branding={
            "logo_url": "",
            "primary_color": "#003a70",
            "secondary_color": "#f5a623",
        },
        rate_limit_per_minute=120,
        storage_quota_gb=50,
    )


__all__ = ["build"]
