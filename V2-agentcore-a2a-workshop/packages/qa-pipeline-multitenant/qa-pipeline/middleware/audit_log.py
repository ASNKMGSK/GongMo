# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Audit log middleware.

ARCHITECTURE.md §9, §10.1:
- 모든 ``/evaluate*`` 와 ``/admin/*`` 요청을 ``qa_audit_log`` 에 1행 기록.
- 기록 타이밍: 응답 phase (status_code / duration_ms 포함).
- 백그라운드 task 로 실행 — 요청 latency 에 영향 최소화.

기록 필드 (ARCHITECTURE.md §3 + 임무):
  tenant_id, timestamp, path, method, user_id, status_code, duration_ms, request_id, ttl
  - timestamp: ISO8601 (SK 용)
  - ttl: 에폭 초 + 30일 (테이블 TTL 속성)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from data import dynamo

logger = logging.getLogger(__name__)

AUDIT_TABLE = "qa_audit_log"
AUDIT_TTL_DAYS = 30
AUDIT_ENABLED_ENV = "AUDIT_LOG_ENABLED"

DEFAULT_AUDIT_PATHS: tuple[str, ...] = (
    "/evaluate",
    "/admin/",
)


def _now_iso_and_ttl() -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    ts_iso = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    ttl = int((now + timedelta(days=AUDIT_TTL_DAYS)).timestamp())
    return ts_iso, ttl


def _user_id_from_claims(claims: Any) -> str:
    if not isinstance(claims, dict):
        return ""
    # Cognito: sub / username / cognito:username
    for k in ("sub", "username", "cognito:username", "email"):
        v = claims.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def _write_audit(item: dict) -> None:
    """동기 put — 백그라운드 task 로 호출된다."""
    try:
        dynamo.tenant_put_item(AUDIT_TABLE, item)
    except Exception as e:
        logger.warning("audit_log write failed tenant=%s: %s", item.get("tenant_id"), e)


class AuditLogMiddleware(BaseHTTPMiddleware):
    """``/evaluate*`` / ``/admin/*`` 요청을 qa_audit_log 에 백그라운드로 기록.

    Args:
        audit_paths: 감사 대상 prefix. 기본 ``DEFAULT_AUDIT_PATHS``.
    """

    def __init__(
        self,
        app: Any,
        *,
        audit_paths: tuple[str, ...] = DEFAULT_AUDIT_PATHS,
    ) -> None:
        super().__init__(app)
        self._paths: tuple[str, ...] = tuple(audit_paths)

    def _should_audit(self, path: str) -> bool:
        return any(path == p or path.startswith(p) for p in self._paths)

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        enabled = os.environ.get(AUDIT_ENABLED_ENV, "1").lower() in ("1", "true", "yes")
        if not enabled or not self._should_audit(request.url.path):
            return await call_next(request)

        t0 = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500))
            return response
        except Exception:
            status_code = 500
            raise
        finally:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            tenant_id: str = getattr(request.state, "tenant_id", "") or ""
            # 인증 전 경로(헬스체크 등)는 tenant_id 빈값 — 감사 대상 아님으로 쓰지 않음.
            if tenant_id:
                ts_iso, ttl = _now_iso_and_ttl()
                request_id = (
                    request.headers.get("x-request-id")
                    or getattr(request.state, "request_id", "")
                    or uuid.uuid4().hex
                )
                claims = getattr(request.state, "tenant_claims", {}) or {}
                user_id = _user_id_from_claims(claims)
                item = {
                    "tenant_id": tenant_id,
                    "timestamp": ts_iso,
                    "path": request.url.path,
                    "method": request.method,
                    "user_id": user_id,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "request_id": request_id,
                    "ttl": ttl,
                }
                # 요청을 블로킹하지 않도록 백그라운드 태스크로 기록.
                asyncio.get_running_loop().run_in_executor(None, _write_audit, item)
