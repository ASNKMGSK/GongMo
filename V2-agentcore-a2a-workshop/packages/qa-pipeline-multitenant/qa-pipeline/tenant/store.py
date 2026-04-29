# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tenant config store — DynamoDB ``qa_tenants`` CRUD with in-memory LRU.

ARCHITECTURE.md 2~3절 참조. 캐시 TTL 5분.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace

from data import (
    get_table,
    tenant_get_item,
    tenant_put_item,
)

from .config import TenantConfig

TABLE_NAME = "qa_tenants"
_CACHE_TTL_SECONDS = 300  # 5 min

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, TenantConfig]] = {}


def _cache_get(tenant_id: str) -> TenantConfig | None:
    with _cache_lock:
        entry = _cache.get(tenant_id)
        if entry is None:
            return None
        expires_at, cfg = entry
        if time.time() >= expires_at:
            _cache.pop(tenant_id, None)
            return None
        return cfg


def _cache_put(cfg: TenantConfig) -> None:
    with _cache_lock:
        _cache[cfg.tenant_id] = (time.time() + _CACHE_TTL_SECONDS, cfg)


def invalidate_cache(tenant_id: str | None = None) -> None:
    """캐시 무효화. ``tenant_id=None`` 이면 전체 flush."""
    with _cache_lock:
        if tenant_id is None:
            _cache.clear()
        else:
            _cache.pop(tenant_id, None)


def get_config(tenant_id: str) -> TenantConfig:
    """테넌트 Config 조회. 캐시 히트 우선, 미스 시 DynamoDB 로 폴백.

    Raises:
        KeyError: 테넌트가 존재하지 않음.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required")

    cached = _cache_get(tenant_id)
    if cached is not None:
        return cached

    item = tenant_get_item(TABLE_NAME, tenant_id)
    if not item:
        raise KeyError(f"tenant not found: {tenant_id}")

    cfg = TenantConfig.from_dict(item)
    _cache_put(cfg)
    return cfg


def put_config(config: TenantConfig) -> TenantConfig:
    """Config 생성/갱신. ``updated_at`` 을 현재 시각으로 갱신하고 캐시에 반영."""
    if not isinstance(config, TenantConfig):
        raise TypeError("config must be TenantConfig")
    config.validate()

    from .config import _utcnow_iso  # local import to avoid circular

    refreshed = replace(config, updated_at=_utcnow_iso())
    tenant_put_item(TABLE_NAME, refreshed.to_dict())
    _cache_put(refreshed)
    return refreshed


def list_configs() -> list[TenantConfig]:
    """전체 테넌트 스캔. 운영상 드물게 호출 (슈퍼어드민용).

    Pool 모델에서 전체 스캔은 관리용으로만 허용됨 — 일반 요청 경로에서는 금지.
    """
    table = get_table(TABLE_NAME)
    items: list[dict] = []
    kwargs: dict = {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last = resp.get("LastEvaluatedKey")
        if not last:
            break
        kwargs["ExclusiveStartKey"] = last

    configs = [TenantConfig.from_dict(i) for i in items]
    with _cache_lock:
        now = time.time()
        for cfg in configs:
            _cache[cfg.tenant_id] = (now + _CACHE_TTL_SECONDS, cfg)
    return configs


__all__ = [
    "TABLE_NAME",
    "get_config",
    "put_config",
    "list_configs",
    "invalidate_cache",
]
