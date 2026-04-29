# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""ARCHITECTURE.md §10.2 에러 응답 규격 (Dev1 owner).

모든 4xx/5xx 응답은 다음 JSON 구조로 통일:

    {
      "error": {
        "code": "UNAUTHORIZED" | "TENANT_MISMATCH" | "TENANT_NOT_FOUND"
              | "RATE_LIMITED" | "INTERNAL" | "INVALID_REQUEST",
        "message": "...",
        "tenant_id": "kolon_default" | null,
        "request_id": "uuid"
      }
    }

HTTP 상태 ↔ code 매핑:
  401 UNAUTHORIZED     — JWT 누락/검증 실패
  403 TENANT_MISMATCH  — 리소스 tenant_id 와 요청 tenant_id 불일치 / role 부족
  404 TENANT_NOT_FOUND — qa_tenants 조회 실패
  429 RATE_LIMITED     — 분당 한도 초과
  500 INTERNAL         — 비정상 예외
  400 INVALID_REQUEST  — 요청 스키마/파라미터 오류 (문서 표에 없지만 실무상 필요)

모듈은 의존성 최소 — fastapi 만 사용. 다른 미들웨어/라우터가 공유.
"""

from __future__ import annotations

import uuid
from fastapi import Request
from fastapi.responses import JSONResponse
from typing import Literal


ErrorCode = Literal[
    "UNAUTHORIZED",
    "TENANT_MISMATCH",
    "TENANT_NOT_FOUND",
    "RATE_LIMITED",
    "INTERNAL",
    "INVALID_REQUEST",
]

# code → 기본 HTTP status (호출자가 override 가능)
_DEFAULT_STATUS: dict[str, int] = {
    "UNAUTHORIZED": 401,
    "TENANT_MISMATCH": 403,
    "TENANT_NOT_FOUND": 404,
    "RATE_LIMITED": 429,
    "INTERNAL": 500,
    "INVALID_REQUEST": 400,
}


def _request_id(request: Request | None) -> str:
    """요청 ID 추출 — X-Request-ID 헤더 우선, 없으면 request.state.request_id, 없으면 신규 uuid."""
    if request is None:
        return uuid.uuid4().hex
    hdr = request.headers.get("x-request-id") or request.headers.get("X-Request-ID")
    if hdr:
        return hdr.strip()
    rid = getattr(request.state, "request_id", "") or ""
    return rid or uuid.uuid4().hex


def _tenant_id_of(request: Request | None) -> str | None:
    """request.state.tenant_id — 인증 전/빈 문자열이면 None."""
    if request is None:
        return None
    tid = getattr(request.state, "tenant_id", "") or ""
    return tid or None


def error_response(
    code: ErrorCode,
    message: str,
    *,
    request: Request | None = None,
    status_code: int | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """§10.2 규격 에러 JSON 응답 생성.

    Args:
        code: ErrorCode 문자열 — ARCHITECTURE.md §10.2 표 참조.
        message: 사람이 읽을 메시지.
        request: 에러가 발생한 Request — tenant_id / request_id 추출용.
        status_code: 기본 매핑과 다른 HTTP 상태 지정 시.
        headers: 추가 헤더 (Retry-After 등).
    """
    body = {
        "error": {
            "code": code,
            "message": message,
            "tenant_id": _tenant_id_of(request),
            "request_id": _request_id(request),
        }
    }
    return JSONResponse(
        status_code=status_code if status_code is not None else _DEFAULT_STATUS.get(code, 500),
        content=body,
        headers=headers,
    )
