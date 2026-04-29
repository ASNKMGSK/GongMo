# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""라우터 공용 테넌트 의존성 및 헬퍼.

TenantMiddleware 가 주입한 request.state.* 에서 tenant_id/role 을 꺼내거나
admin 권한을 검사한다. Dev4 의 `tenant` 패키지 API 를 사용:

    from tenant import TenantConfig, get_config, put_config, list_configs, invalidate_cache

- `get_config(tid)` — 캐시 5분 TTL, 미존재 시 `KeyError` → 여기서는 None 으로 정규화.
- `TenantConfig.to_dict()` — `/api/me` 응답 직렬화.
- `TenantConfig.from_dict(d)` + `cfg.validate()` + `put_config(cfg)` — `/admin/tenants` 생성/갱신.
"""

from __future__ import annotations

import logging
from fastapi import HTTPException, Request
from typing import Any


logger = logging.getLogger(__name__)


def _load_tenant_api() -> Any | None:
    """Dev4 의 tenant 패키지 API 를 지연 로드.

    store 내부 boto3 가 import 시점에 실패할 수 있으므로(개발 환경 AWS 미설정 등)
    실패 시 None 반환 — 라우터는 503 으로 응답.
    """
    try:
        import tenant  # type: ignore[import-not-found]
    except ImportError as e:
        logger.debug("tenant package not importable: %s", e)
        return None
    return tenant


def _get_config_or_none(tid: str) -> Any | None:
    """`tenant.get_config(tid)` 호출 — KeyError/Exception 은 None 으로 정규화."""
    api = _load_tenant_api()
    if api is None:
        return None
    try:
        return api.get_config(tid)
    except KeyError:
        return None
    except Exception as e:
        logger.warning("tenant.get_config(%s) failed: %s", tid, e)
        return None


# ---------------------------------------------------------------------------
# Request state 접근 헬퍼
# ---------------------------------------------------------------------------


def require_tenant_id(request: Request) -> str:
    """request.state.tenant_id 읽고 비어있으면 401."""
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=401, detail="tenant_id not set — middleware misconfigured")
    return tid


def get_tenant_role(request: Request) -> str:
    return getattr(request.state, "tenant_role", "") or ""


def require_admin(request: Request) -> str:
    """admin 권한 필수 — role != admin 이면 403. 반환값은 tenant_id."""
    tid = require_tenant_id(request)
    role = get_tenant_role(request)
    if role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return tid


def _resolve_request_id(request: Request) -> str:
    """요청 전체에서 동일한 request_id 를 보장.

    우선순위: X-Request-ID 헤더 → request.state.request_id → 신규 uuid.
    한 요청 내 결과는 request.state.request_id 에 캐시하여
    §10.2 에러 응답과 LangGraph state 의 tenant.request_id 가 동일 값을 공유.
    """
    import uuid

    cached = getattr(request.state, "request_id", "") or ""
    if cached:
        return cached
    hdr = request.headers.get("x-request-id") or request.headers.get("X-Request-ID")
    rid = (hdr.strip() if hdr else "") or uuid.uuid4().hex
    request.state.request_id = rid
    return rid


def tenant_context(request: Request) -> dict[str, Any]:
    """LangGraph state 의 `tenant` 필드로 주입할 dict 생성 (Dev3 TenantContext 호환).

    반환 스키마:
      {"tenant_id": str, "config": dict, "request_id": str}

    Config 로드 실패(미존재/DB 불가) 시 빈 dict 폴백 — 노드는 기본값으로 동작.
    """
    tid = require_tenant_id(request)
    config: dict[str, Any] = {}
    cfg = _get_config_or_none(tid)
    if cfg is not None:
        try:
            config = cfg.to_dict()
        except Exception as e:
            logger.warning("tenant config.to_dict failed (tenant=%s): %s", tid, e)

    return {
        "tenant_id": tid,
        "config": config,
        "request_id": _resolve_request_id(request),
    }
