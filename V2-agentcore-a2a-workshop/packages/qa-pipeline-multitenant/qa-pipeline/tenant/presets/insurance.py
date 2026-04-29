# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Insurance 업종 프리셋.

특징
- 법적 고지(item 8: 질문 의도 재확인/설명 의무) 가중치 상향
- 상품 정확성(item 16: 필수 멘트 준수) 가중치 상향
- 개인정보 보호(item 18) 활성화 필수
- 후속 약속(item 14) 중요
"""

from __future__ import annotations

from ..config import TenantConfig


def build() -> TenantConfig:
    return TenantConfig(
        tenant_id="preset_insurance",
        display_name="Insurance Preset",
        industry="insurance",
        qa_items_enabled=[1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
        score_overrides={
            8: 15,   # 법적 고지/질문 의도 재확인 강화
            16: 15,  # 필수 멘트(약관 고지 등) 강화
            18: 12,  # 개인정보 보호
        },
        default_models={
            "primary": "us.anthropic.claude-sonnet-4-20250514-v1:0",
            "fast": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        },
        prompt_overrides_dir=None,
        branding={
            "logo_url": "",
            "primary_color": "#005baa",
            "secondary_color": "#e4002b",
        },
        rate_limit_per_minute=90,
        storage_quota_gb=30,
    )


__all__ = ["build"]
