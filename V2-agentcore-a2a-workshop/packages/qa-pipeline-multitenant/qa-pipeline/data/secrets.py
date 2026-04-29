# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Secrets Manager tenant-isolated helpers.

시크릿 경로는 `/qa/{tenant_id}/{secret_name}` 로 강제한다.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
_BOTO_CONFIG = Config(retries={"max_attempts": 5, "mode": "adaptive"})

_client_lock = threading.Lock()
_client: Any | None = None

# 간단 메모리 캐시 (TTL 5분) — 반복 호출 비용 절감
_CACHE_TTL_SEC = 300
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()

SECRET_PREFIX = "/qa"


def _require_tenant_id(tenant_id: str | None) -> None:
    if not tenant_id or not isinstance(tenant_id, str):
        raise ValueError("tenant_id is required for query isolation")


def _secret_path(tenant_id: str, secret_name: str) -> str:
    _require_tenant_id(tenant_id)
    if not secret_name:
        raise ValueError("secret_name is required")
    return f"{SECRET_PREFIX}/{tenant_id}/{secret_name}"


def get_client():
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = boto3.client("secretsmanager", region_name=_REGION, config=_BOTO_CONFIG)
    return _client


def _parse_secret_value(resp: dict) -> Any:
    if "SecretString" in resp and resp["SecretString"] is not None:
        raw = resp["SecretString"]
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
    return resp.get("SecretBinary")


def get_tenant_secret(tenant_id: str, secret_name: str, *, use_cache: bool = True) -> Any:
    """Return the parsed secret value (dict if JSON, else str/bytes)."""
    path = _secret_path(tenant_id, secret_name)
    now = time.monotonic()
    if use_cache:
        with _cache_lock:
            hit = _cache.get(path)
            if hit and hit[0] > now:
                return hit[1]

    try:
        resp = get_client().get_secret_value(SecretId=path)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ResourceNotFoundException":
            raise KeyError(f"secret not found: {path}") from e
        raise

    value = _parse_secret_value(resp)
    if use_cache:
        with _cache_lock:
            _cache[path] = (now + _CACHE_TTL_SEC, value)
    return value


def put_tenant_secret(
    tenant_id: str,
    secret_name: str,
    value: Any,
    *,
    description: str | None = None,
    kms_key_id: str | None = None,
) -> dict:
    """Create or update a tenant secret. Value can be a dict (serialized to JSON) or str/bytes."""
    path = _secret_path(tenant_id, secret_name)
    client = get_client()

    if isinstance(value, (dict, list)):
        body: dict[str, Any] = {"SecretString": json.dumps(value, ensure_ascii=False)}
    elif isinstance(value, str):
        body = {"SecretString": value}
    elif isinstance(value, (bytes, bytearray)):
        body = {"SecretBinary": bytes(value)}
    else:
        raise ValueError(f"unsupported secret value type: {type(value).__name__}")

    # 캐시 무효화
    with _cache_lock:
        _cache.pop(path, None)

    try:
        return client.put_secret_value(SecretId=path, **body)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise
        create_kwargs: dict[str, Any] = {
            "Name": path,
            "Tags": [{"Key": "tenant_id", "Value": tenant_id}],
        }
        if description:
            create_kwargs["Description"] = description
        if kms_key_id:
            create_kwargs["KmsKeyId"] = kms_key_id
        create_kwargs.update(body)
        return client.create_secret(**create_kwargs)


def delete_tenant_secret(
    tenant_id: str,
    secret_name: str,
    *,
    force: bool = False,
    recovery_window_days: int = 7,
) -> dict:
    """Delete (schedule deletion of) a tenant secret."""
    path = _secret_path(tenant_id, secret_name)
    with _cache_lock:
        _cache.pop(path, None)
    kwargs: dict[str, Any] = {"SecretId": path}
    if force:
        kwargs["ForceDeleteWithoutRecovery"] = True
    else:
        kwargs["RecoveryWindowInDays"] = recovery_window_days
    return get_client().delete_secret(**kwargs)


def invalidate_cache(tenant_id: str | None = None) -> None:
    """Clear cache globally or for a single tenant."""
    with _cache_lock:
        if tenant_id is None:
            _cache.clear()
        else:
            prefix = f"{SECRET_PREFIX}/{tenant_id}/"
            for k in [k for k in _cache if k.startswith(prefix)]:
                _cache.pop(k, None)


__all__ = [
    "SECRET_PREFIX",
    "get_client",
    "get_tenant_secret",
    "put_tenant_secret",
    "delete_tenant_secret",
    "invalidate_cache",
]
