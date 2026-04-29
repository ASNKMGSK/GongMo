# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""테넌트/계정 메타 API.

ARCHITECTURE.md §6 요구사항 구현:
  - GET  /api/me          — 현재 요청자의 tenant_id / role / email / TenantConfig
  - GET  /api/tenants     — admin 전용. 전체 테넌트 리스트
  - POST /admin/tenants   — admin 전용. 신규 TenantConfig 생성 (put_config 로 upsert)

Dev4 (`tenant` 패키지) API 사용:
  from tenant import TenantConfig, get_config, put_config, list_configs

  - get_config(tid) -> TenantConfig  (없으면 KeyError)
  - put_config(cfg) -> TenantConfig  (upsert, updated_at 자동 갱신)
  - list_configs() -> list[TenantConfig]  (전체 scan, admin 전용)
  - TenantConfig.from_dict(d) / .to_dict() / .validate()

`validate()` 실패는 ValueError → 400 INVALID_REQUEST 로 변환.
"""

from __future__ import annotations

import logging
from ._tenant_deps import _get_config_or_none, _load_tenant_api, get_tenant_role, require_admin, require_tenant_id
from .schemas import TenantConfigCreateRequest
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from typing import Any


logger = logging.getLogger(__name__)

router = APIRouter(tags=["me"])


def _extract_email(claims: dict[str, Any]) -> str:
    """Cognito JWT claims 에서 사용자 이메일 추출. 없으면 빈 문자열."""
    if not isinstance(claims, dict):
        return ""
    for k in ("email", "cognito:username", "username", "sub"):
        v = claims.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


@router.get("/api/me")
async def get_me(request: Request) -> JSONResponse:
    """현재 요청자의 테넌트 메타 반환 (Dev5 UI 계약).

    반환 스키마:
      {
        "tenant_id": "kolon_default",
        "role": "admin" | "member",
        "email": "user@example.com",
        "override": bool,
        "config": TenantConfig.to_dict() | null
      }
    """
    tid = require_tenant_id(request)
    raw_role = get_tenant_role(request)
    role = "admin" if raw_role == "admin" else "member"
    override = bool(getattr(request.state, "tenant_override", False))
    claims = getattr(request.state, "tenant_claims", {}) or {}
    email = _extract_email(claims)

    config: dict[str, Any] | None = None
    cfg = _get_config_or_none(tid)
    if cfg is not None:
        try:
            config = cfg.to_dict()
        except Exception as e:
            logger.warning("tenant config.to_dict failed (tenant=%s): %s", tid, e)

    return JSONResponse(
        {
            "tenant_id": tid,
            "role": role,
            "email": email,
            "override": override,
            "config": config,
        }
    )


@router.get("/api/tenants")
async def list_tenants(request: Request) -> JSONResponse:
    """admin 전용 — 전체 테넌트 Config 리스트."""
    require_admin(request)

    api = _load_tenant_api()
    if api is None:
        raise HTTPException(status_code=503, detail="tenant API not available")

    try:
        configs = api.list_configs()
    except Exception as e:
        logger.error("tenant.list_configs failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"list_configs failed: {e}") from e

    return JSONResponse({"tenants": [c.to_dict() for c in configs], "total": len(configs)})


@router.post("/admin/tenants")
async def create_tenant(payload: TenantConfigCreateRequest, request: Request) -> JSONResponse:
    """admin 전용 — 신규 테넌트 생성/갱신.

    Dev4 권고 흐름:
      body dict → TenantConfig.from_dict(d) → cfg.validate() → put_config(cfg)

    - `validate()` 실패 시 ValueError → 400 INVALID_REQUEST.
    - `put_config()` 는 upsert — `updated_at` 을 자동 갱신하고 캐시에 반영.
    """
    require_admin(request)

    api = _load_tenant_api()
    if api is None:
        raise HTTPException(status_code=503, detail="tenant API not available")

    body_dict = payload.model_dump()
    try:
        cfg = api.TenantConfig.from_dict(body_dict)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"invalid TenantConfig payload: {e}") from e

    try:
        cfg.validate()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"TenantConfig validation failed: {e}") from e

    try:
        saved = api.put_config(cfg)
    except Exception as e:
        logger.error("tenant.put_config failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"put_config failed: {e}") from e

    return JSONResponse(status_code=201, content=saved.to_dict())
