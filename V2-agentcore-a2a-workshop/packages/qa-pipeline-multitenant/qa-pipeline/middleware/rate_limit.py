# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Per-tenant rate limit middleware.

ARCHITECTURE.md §9, §10.1:
- 테넌트당 분당 N회 제한 (TenantConfig.rate_limit_per_minute, 기본 60).
- 대상 경로: ``/evaluate``, ``/evaluate/stream``, ``/save-xlsx``.
- 초과 시 429 + ``Retry-After: <초>`` 헤더.
- 카운터 저장소: DynamoDB ``qa_quota_usage`` — PK=tenant_id, SK=``yyyy-mm``.
  행 내부 ``minute_counters`` Map 에 분 단위(``YYYY-MM-DDTHH:MM``) 서브필드로 ADD 누적.
  Dev2 의 ``data.tenant_atomic_counter`` 헬퍼를 사용 (tenant_id 가드 + SK 검증 자동).

현재 분의 카운터만 검사 — 이전 분의 카운터는 cleanup 없이 자연 소멸 (월 단위로 덮어씀).
대량 트래픽 환경에서는 Phase 6 에 Redis/DAX 전환 검토.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from data import tenant_atomic_counter
from middleware.errors import error_response
from tenant.config import TenantConfig

logger = logging.getLogger(__name__)

QUOTA_TABLE = "qa_quota_usage"
DEFAULT_LIMIT = 60
RATE_LIMIT_ENABLED_ENV = "RATE_LIMIT_ENABLED"

# Rate limit 적용 대상 경로 prefix
DEFAULT_GUARDED_PATHS: tuple[str, ...] = (
    "/evaluate",
    "/save-xlsx",
)


def _minute_key(now: datetime | None = None) -> tuple[str, str]:
    """현재 시각을 (SK=yyyy-mm, minute_bucket=YYYY-MM-DDTHH:MM) 으로 반환."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m"), now.strftime("%Y-%m-%dT%H:%M")


def _retry_after_seconds(now: datetime | None = None) -> int:
    """다음 분 경계까지 남은 초 (최소 1)."""
    now = now or datetime.now(timezone.utc)
    return max(1, 60 - now.second)


def _resolve_limit(config: Any) -> int:
    """TenantConfig 또는 dict 에서 rate_limit_per_minute 추출. 실패 시 기본 60."""
    try:
        if isinstance(config, TenantConfig):
            return int(config.rate_limit_per_minute)
        if isinstance(config, dict):
            return int(config.get("rate_limit_per_minute", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        pass
    return DEFAULT_LIMIT


def _load_tenant_config(tenant_id: str) -> Any | None:
    """Dev4 의 tenant.store 캐시에서 설정 조회. store 미배치 시 None."""
    try:
        from tenant import store  # type: ignore[attr-defined]
    except ImportError:
        return None
    try:
        return store.get_config(tenant_id)  # type: ignore[no-any-return]
    except Exception as e:  # store 실패는 Rate Limit 우회가 아니라 기본값 fallback
        logger.warning("tenant.store.get_config failed for %s: %s", tenant_id, e)
        return None


def _increment_and_check(tenant_id: str, limit: int) -> tuple[bool, int]:
    """qa_quota_usage 에 분 단위 카운터를 +1 하고 (allowed, current_count) 반환.

    Dev2 ``tenant_atomic_counter`` 헬퍼 경유 — 내부적으로
    ``ADD minute_counters.#sub :v SET updated_at = :now`` 를 생성한다.
    """
    sk_value, minute_bucket = _minute_key()
    try:
        resp = tenant_atomic_counter(
            QUOTA_TABLE,
            tenant_id,
            sk=sk_value,
            field="minute_counters",
            subfield=minute_bucket,
        )
    except Exception as e:
        # DynamoDB 실패 시 (테이블 없음 등) fail-open — 요청은 통과시키고 로그만 남김.
        logger.warning("rate_limit update failed tenant=%s: %s", tenant_id, e)
        return True, 0

    counters = resp.get("Attributes", {}).get("minute_counters", {}) or {}
    current = int(counters.get(minute_bucket, 0))
    return current <= limit, current


class RateLimitMiddleware(BaseHTTPMiddleware):
    """분당 N회 테넌트 Rate Limit.

    Args:
        guarded_paths: Rate Limit 대상 prefix 목록. 기본값은 ``DEFAULT_GUARDED_PATHS``.
    """

    def __init__(
        self,
        app: Any,
        *,
        guarded_paths: tuple[str, ...] = DEFAULT_GUARDED_PATHS,
        default_limit: int = DEFAULT_LIMIT,
    ) -> None:
        super().__init__(app)
        self._guarded: tuple[str, ...] = tuple(guarded_paths)
        self._default_limit = int(default_limit)

    def _is_guarded(self, path: str) -> bool:
        return any(path == p or path.startswith(p + "/") or path == p for p in self._guarded)

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        # 토글 — 환경변수로 전면 비활성화 (로컬 개발 편의).
        enabled = os.environ.get(RATE_LIMIT_ENABLED_ENV, "1").lower() in ("1", "true", "yes")
        if not enabled:
            return await call_next(request)

        if not self._is_guarded(request.url.path):
            return await call_next(request)

        # TenantMiddleware 이후 체인이므로 request.state.tenant_id 가 반드시 있다.
        tenant_id: str = getattr(request.state, "tenant_id", "") or ""
        if not tenant_id:
            # 예외 상황 (미들웨어 순서 오류 등) — 보호적으로 통과.
            return await call_next(request)

        config = _load_tenant_config(tenant_id)
        limit = _resolve_limit(config) if config is not None else self._default_limit

        t0 = time.perf_counter()
        allowed, current = _increment_and_check(tenant_id, limit)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        if not allowed:
            retry_after = _retry_after_seconds()
            logger.info(
                "rate_limited tenant=%s path=%s count=%d/%d dt_ms=%d",
                tenant_id,
                request.url.path,
                current,
                limit,
                elapsed_ms,
            )
            return error_response(
                code="RATE_LIMITED",
                message=(
                    f"rate limit exceeded: {current}/{limit} per minute "
                    f"for tenant {tenant_id}"
                ),
                request=request,
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
