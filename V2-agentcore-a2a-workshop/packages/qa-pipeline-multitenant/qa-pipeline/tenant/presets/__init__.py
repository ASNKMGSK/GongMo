# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""업종 프리셋 — get_preset(industry) → TenantConfig 템플릿.

프리셋은 **템플릿**이다. 실제 테넌트 레코드로 쓰려면 ``dataclasses.replace`` 로
``tenant_id``/``display_name``/``branding`` 등을 테넌트 고유 값으로 덮어써야 한다.
"""

from __future__ import annotations

from dataclasses import replace

from ..config import TenantConfig
from .ecommerce import build as _build_ecommerce
from .generic import build as _build_generic
from .industrial import build as _build_industrial
from .insurance import build as _build_insurance

_BUILDERS = {
    "industrial": _build_industrial,
    "insurance": _build_insurance,
    "ecommerce": _build_ecommerce,
    "generic": _build_generic,
}


def get_preset(industry: str) -> TenantConfig:
    """업종 프리셋 TenantConfig 템플릿 반환.

    Raises:
        KeyError: 지원되지 않는 업종.
    """
    if industry not in _BUILDERS:
        raise KeyError(
            f"no preset for industry={industry!r}; supported: {sorted(_BUILDERS)}"
        )
    return _BUILDERS[industry]()


def available_industries() -> list[str]:
    return sorted(_BUILDERS)


def apply_preset(
    industry: str,
    *,
    tenant_id: str,
    display_name: str,
    branding: dict | None = None,
) -> TenantConfig:
    """프리셋 템플릿을 기반으로 실제 테넌트 레코드를 생성하는 헬퍼."""
    tpl = get_preset(industry)
    overrides: dict = {"tenant_id": tenant_id, "display_name": display_name}
    if branding is not None:
        overrides["branding"] = branding
    cfg = replace(tpl, **overrides)
    cfg.validate()
    return cfg


__all__ = ["get_preset", "apply_preset", "available_industries"]
