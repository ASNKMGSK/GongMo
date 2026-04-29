# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generic 기본 프리셋 — 15개 핵심 항목.

업종이 특정되지 않은 신규 테넌트의 안전한 출발점.
"""

from __future__ import annotations

from ..config import TenantConfig

# 15개 핵심 항목: 인사/종료, 경청, 공감, 공손, 쿠션, 확인, 고객정보, 명확성,
# 결론우선, 문제해결, 부가안내, 후속약속, 정확성, IV 절차
_CORE_ITEMS = [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17]


def build() -> TenantConfig:
    return TenantConfig(
        tenant_id="preset_generic",
        display_name="Generic Preset",
        industry="generic",
        qa_items_enabled=list(_CORE_ITEMS),
        score_overrides={},
        default_models={
            "primary": "us.anthropic.claude-sonnet-4-20250514-v1:0",
            "fast": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        },
        prompt_overrides_dir=None,
        branding={
            "logo_url": "",
            "primary_color": "#1565c0",
            "secondary_color": "#757575",
        },
        rate_limit_per_minute=60,
        storage_quota_gb=10,
    )


__all__ = ["build"]
