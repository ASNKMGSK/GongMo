# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""S3 tenant-isolated helpers.

모든 객체 키에 `tenants/{tenant_id}/` prefix 를 강제한다.
ARCHITECTURE.md 4절 참조.
"""

from __future__ import annotations

import os
import threading
from typing import Any, BinaryIO, Iterator

import boto3
from botocore.config import Config

_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
_BOTO_CONFIG = Config(retries={"max_attempts": 5, "mode": "adaptive"}, signature_version="s3v4")
_DEFAULT_BUCKET = os.getenv("QA_BUCKET_NAME")

_client_lock = threading.Lock()
_client: Any | None = None

TENANT_PREFIX = "tenants"


def _require_tenant_id(tenant_id: str | None) -> None:
    if not tenant_id or not isinstance(tenant_id, str):
        raise ValueError("tenant_id is required for query isolation")


def _resolve_bucket(bucket: str | None) -> str:
    b = bucket or _DEFAULT_BUCKET
    if not b:
        raise ValueError("bucket is required (pass bucket= or set QA_BUCKET_NAME)")
    return b


def _tenant_key(tenant_id: str, key: str) -> str:
    _require_tenant_id(tenant_id)
    if key is None:
        raise ValueError("key is required")
    k = key.lstrip("/")
    return f"{TENANT_PREFIX}/{tenant_id}/{k}"


def get_client():
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = boto3.client("s3", region_name=_REGION, config=_BOTO_CONFIG)
    return _client


def tenant_put_object(
    tenant_id: str,
    key: str,
    body: bytes | str | BinaryIO,
    *,
    bucket: str | None = None,
    content_type: str | None = None,
    **kwargs,
) -> dict:
    """Put an object at tenants/{tenant_id}/{key}."""
    _require_tenant_id(tenant_id)
    b = _resolve_bucket(bucket)
    real_key = _tenant_key(tenant_id, key)
    params: dict[str, Any] = {"Bucket": b, "Key": real_key, "Body": body}
    if content_type:
        params["ContentType"] = content_type
    params.update(kwargs)
    return get_client().put_object(**params)


def tenant_get_object(tenant_id: str, key: str, *, bucket: str | None = None) -> dict:
    """Get an object. Caller is responsible for streaming/closing ['Body']."""
    _require_tenant_id(tenant_id)
    b = _resolve_bucket(bucket)
    real_key = _tenant_key(tenant_id, key)
    return get_client().get_object(Bucket=b, Key=real_key)


def tenant_list_objects(
    tenant_id: str,
    prefix: str = "",
    *,
    bucket: str | None = None,
    max_keys: int | None = None,
) -> list[dict]:
    """List objects under tenants/{tenant_id}/{prefix}, paginating until exhausted."""
    _require_tenant_id(tenant_id)
    b = _resolve_bucket(bucket)  # noqa: — tenant_id 가드가 반드시 선행되어야 함
    full_prefix = f"{TENANT_PREFIX}/{tenant_id}/{(prefix or '').lstrip('/')}"
    client = get_client()

    items: list[dict] = []
    kwargs: dict[str, Any] = {"Bucket": b, "Prefix": full_prefix}
    while True:
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []) or []:
            items.append(obj)
            if max_keys is not None and len(items) >= max_keys:
                return items[:max_keys]
        if not resp.get("IsTruncated"):
            break
        kwargs["ContinuationToken"] = resp["NextContinuationToken"]
    return items


def tenant_delete_object(tenant_id: str, key: str, *, bucket: str | None = None) -> dict:
    """Delete a single object."""
    _require_tenant_id(tenant_id)
    b = _resolve_bucket(bucket)
    real_key = _tenant_key(tenant_id, key)
    return get_client().delete_object(Bucket=b, Key=real_key)


def tenant_presigned_url(
    tenant_id: str,
    key: str,
    *,
    bucket: str | None = None,
    method: str = "get_object",
    expires_in: int = 900,
) -> str:
    """Generate a presigned URL scoped to the tenant key."""
    _require_tenant_id(tenant_id)
    b = _resolve_bucket(bucket)
    real_key = _tenant_key(tenant_id, key)
    return get_client().generate_presigned_url(
        ClientMethod=method,
        Params={"Bucket": b, "Key": real_key},
        ExpiresIn=expires_in,
    )


def tenant_iter_objects(
    tenant_id: str,
    prefix: str = "",
    *,
    bucket: str | None = None,
) -> Iterator[dict]:
    """Generator variant for very large listings."""
    _require_tenant_id(tenant_id)
    b = _resolve_bucket(bucket)
    full_prefix = f"{TENANT_PREFIX}/{tenant_id}/{(prefix or '').lstrip('/')}"
    paginator = get_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=b, Prefix=full_prefix):
        for obj in page.get("Contents", []) or []:
            yield obj


__all__ = [
    "TENANT_PREFIX",
    "get_client",
    "tenant_put_object",
    "tenant_get_object",
    "tenant_list_objects",
    "tenant_delete_object",
    "tenant_presigned_url",
    "tenant_iter_objects",
]
