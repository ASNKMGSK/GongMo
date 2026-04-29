# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""테넌트 식별 미들웨어.

ARCHITECTURE.md §1 흐름 구현:
  Authorization: Bearer <JWT> → custom:tenant_id 추출 → request.state.tenant_id 주입

권한 규칙:
  - JWT 없거나 `custom:tenant_id` 클레임 없으면 401
  - `custom:role=admin` 이면 `X-Tenant-Override: <tid>` 헤더로 다른 테넌트 조회 허용
  - 환경변수 `LOCAL_TENANT_ID` 지정 시 JWT 없어도 해당 값으로 폴백 (개발용)

검증:
  - JWT 서명 검증은 `TENANT_JWT_VERIFY_SIGNATURE=1` 시에만 수행 (Cognito JWKS 필요)
  - 기본은 `jwt.decode(..., options={"verify_signature": False})` — 테스트/개발 편의
  - 프로덕션은 반드시 서명 검증 활성화

`request.state` 에 주입하는 키:
  - `tenant_id: str`           — 유효한 테넌트 식별자
  - `tenant_role: str`         — "admin" 또는 "" (빈 문자열)
  - `tenant_claims: dict`      — 전체 JWT 클레임 (디버깅/감사 로그용)
  - `tenant_override: bool`    — X-Tenant-Override 로 다른 테넌트 조회 중인지
"""

from __future__ import annotations

import logging
import os
import re
from .errors import error_response
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Any


logger = logging.getLogger(__name__)

# tenant_id 형식 — Dev4 TenantConfig 와 동일 규칙 (소문자/숫자/언더스코어 2~64자)
_TENANT_ID_RE = re.compile(r"^[a-z0-9_]{2,64}$")

# 미들웨어가 JWT 검증/주입을 건너뛸 경로 prefix (헬스체크/정적리소스 등)
# AgentCore Runtime 프로브 경로(/ping, /invocations)와 FastAPI 내장 문서 경로 포함.
_EXEMPT_PATHS: tuple[str, ...] = (
    "/ping",
    "/health",
    "/readyz",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/ui",
)


def _decode_jwt(token: str) -> dict[str, Any]:
    """JWT 디코드 — PyJWT 사용. 서명 검증은 환경변수로 토글."""
    try:
        import jwt  # type: ignore[import-untyped]
    except ImportError as e:
        raise RuntimeError("PyJWT is required — add 'pyjwt' to requirements.txt") from e

    verify_signature = os.environ.get("TENANT_JWT_VERIFY_SIGNATURE", "0").lower() in ("1", "true", "yes")
    options: dict[str, Any] = {"verify_signature": verify_signature}
    # verify_signature=False 시 audience/exp 도 검증하지 않음 (개발 편의)
    if not verify_signature:
        options.update({"verify_aud": False, "verify_exp": False})
        return jwt.decode(token, options=options)  # type: ignore[no-any-return]

    # 프로덕션 경로 — Cognito JWKS 로 서명 검증
    jwks_url = os.environ.get("TENANT_JWT_JWKS_URL", "").strip()
    audience = os.environ.get("TENANT_JWT_AUDIENCE", "").strip() or None
    if not jwks_url:
        raise RuntimeError(
            "TENANT_JWT_VERIFY_SIGNATURE=1 requires TENANT_JWT_JWKS_URL (Cognito JWKS endpoint)"
        )
    jwks_client = jwt.PyJWKClient(jwks_url)
    signing_key = jwks_client.get_signing_key_from_jwt(token).key
    algorithms = os.environ.get("TENANT_JWT_ALGORITHMS", "RS256").split(",")
    return jwt.decode(  # type: ignore[no-any-return]
        token,
        signing_key,
        algorithms=[a.strip() for a in algorithms if a.strip()],
        audience=audience,
        options=options,
    )


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _valid_tenant_id(tid: str | None) -> bool:
    return bool(tid) and isinstance(tid, str) and bool(_TENANT_ID_RE.match(tid))


class TenantMiddleware(BaseHTTPMiddleware):
    """JWT → request.state.tenant_id 주입 미들웨어.

    Args:
        exempt_paths: 검증을 건너뛸 경로 prefix 목록. 기본값(`_EXEMPT_PATHS`) 위에 추가.
    """

    def __init__(self, app: Any, exempt_paths: tuple[str, ...] = ()) -> None:
        super().__init__(app)
        self._exempt_paths: tuple[str, ...] = _EXEMPT_PATHS + tuple(exempt_paths)

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        path = request.url.path
        # OPTIONS(preflight) 는 통과
        if request.method == "OPTIONS":
            return await call_next(request)

        # 헬스/문서/정적 경로는 검증 스킵 — tenant_id 를 "" 로 두되 downstream 접근은 최소화
        for p in self._exempt_paths:
            if path.startswith(p):
                request.state.tenant_id = ""
                request.state.tenant_role = ""
                request.state.tenant_claims = {}
                request.state.tenant_override = False
                return await call_next(request)

        tenant_id: str | None = None
        tenant_role: str = ""
        claims: dict[str, Any] = {}

        token = _extract_bearer(request)
        if token:
            try:
                claims = _decode_jwt(token)
            except Exception as e:
                logger.warning("JWT decode failed: %s", e)
                return error_response(
                    "UNAUTHORIZED", "invalid authorization token", request=request
                )
            tenant_id = claims.get("custom:tenant_id") or claims.get("tenant_id")
            tenant_role = str(claims.get("custom:role") or claims.get("role") or "").lower()

        if not tenant_id:
            tenant_id = os.environ.get("LOCAL_TENANT_ID", "").strip() or None

        if not _valid_tenant_id(tenant_id):
            return error_response(
                "UNAUTHORIZED",
                "tenant_id not found — Authorization: Bearer <JWT> required",
                request=request,
            )

        override_used = False
        override_header = request.headers.get("x-tenant-override") or request.headers.get("X-Tenant-Override")
        if override_header:
            if tenant_role != "admin":
                # 원 요청자 tenant_id 를 에러 응답에 포함
                request.state.tenant_id = tenant_id
                return error_response(
                    "TENANT_MISMATCH", "X-Tenant-Override requires role=admin", request=request
                )
            override_tid = override_header.strip()
            if not _valid_tenant_id(override_tid):
                request.state.tenant_id = tenant_id
                return error_response(
                    "INVALID_REQUEST",
                    f"invalid X-Tenant-Override: {override_tid!r}",
                    request=request,
                )
            tenant_id = override_tid
            override_used = True

        request.state.tenant_id = tenant_id
        request.state.tenant_role = tenant_role
        request.state.tenant_claims = claims
        request.state.tenant_override = override_used

        return await call_next(request)
