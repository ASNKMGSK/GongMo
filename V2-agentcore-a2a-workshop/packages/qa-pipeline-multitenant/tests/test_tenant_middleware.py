# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Dev1 TenantMiddleware — 401 / X-Tenant-Override / LOCAL_TENANT_ID fallback."""

from __future__ import annotations

import base64
import json
import os

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from middleware.tenant import TenantMiddleware


def _encode_unsigned_jwt(claims: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("LOCAL_TENANT_ID", raising=False)
    monkeypatch.delenv("TENANT_JWT_VERIFY_SIGNATURE", raising=False)

    app = FastAPI()
    app.add_middleware(TenantMiddleware)

    @app.get("/api/me")
    async def me(request: Request):
        return {
            "tenant_id": getattr(request.state, "tenant_id", None),
            "role": getattr(request.state, "tenant_role", ""),
            "override": getattr(request.state, "tenant_override", False),
        }

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


def test_health_bypasses_middleware(app):
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200


def test_missing_jwt_returns_401(app, monkeypatch):
    client = TestClient(app)
    r = client.get("/api/me")
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "UNAUTHORIZED"


def test_local_tenant_fallback(app, monkeypatch):
    monkeypatch.setenv("LOCAL_TENANT_ID", "kolon_default")
    client = TestClient(app)
    r = client.get("/api/me")
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "kolon_default"


def test_valid_jwt_populates_tenant_id(app):
    token = _encode_unsigned_jwt({"custom:tenant_id": "tenantA", "custom:role": ""})
    client = TestClient(app)
    r = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "tenantA"


def test_admin_override_allowed(app):
    token = _encode_unsigned_jwt({"custom:tenant_id": "tenantA", "custom:role": "admin"})
    client = TestClient(app)
    r = client.get(
        "/api/me",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Override": "tenantB"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "tenantB"
    assert body["role"] == "admin"
    assert body["override"] is True


def test_non_admin_override_rejected(app):
    token = _encode_unsigned_jwt({"custom:tenant_id": "tenantA", "custom:role": ""})
    client = TestClient(app)
    r = client.get(
        "/api/me",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Override": "tenantB"},
    )
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] in ("TENANT_MISMATCH", "FORBIDDEN")


def test_override_bad_format_rejected(app):
    token = _encode_unsigned_jwt({"custom:tenant_id": "tenantA", "custom:role": "admin"})
    client = TestClient(app)
    r = client.get(
        "/api/me",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Override": "BAD ID!!!"},
    )
    assert r.status_code == 400
