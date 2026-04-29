# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""E-commerce 업종 프리셋.

특징
- 응대 속도 / 결론 우선(item 11) 가중치 상향
- 문제 해결(item 12) 및 후속 약속(item 14) 가중치 상향
- 환불/배송 등 상품 정확성(item 15) 가중치 상향
- 법적 고지 비중 축소 (보험 대비)
"""

from __future__ import annotations

from ..config import TenantConfig


def build() -> TenantConfig:
    return TenantConfig(
        tenant_id="preset_ecommerce",
        display_name="E-commerce Preset",
        industry="ecommerce",
        qa_items_enabled=[1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 17],
        score_overrides={
            11: 12,  # 결론 우선 (응대 속도)
            12: 15,  # 문제 해결 (환불/배송 처리)
            14: 12,  # 후속 약속 / 재확인
            15: 12,  # 상품/배송 정확성
        },
        default_models={
            "primary": "us.anthropic.claude-sonnet-4-20250514-v1:0",
            "fast": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        },
        prompt_overrides_dir=None,
        branding={
            "logo_url": "",
            "primary_color": "#ff6f00",
            "secondary_color": "#212121",
        },
        rate_limit_per_minute=150,
        storage_quota_gb=40,
    )


__all__ = ["build"]
