# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""DynamoDB tenant-isolated helpers.

모든 쿼리/쓰기는 tenant_id 를 필수 입력으로 받아 Pool 모델 격리를 강제한다.
ARCHITECTURE.md 3절 참조.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
_BOTO_CONFIG = Config(retries={"max_attempts": 5, "mode": "adaptive"})

_resource_lock = threading.Lock()
_resource: Any | None = None
_table_cache: dict[str, Any] = {}

TENANT_PK = "tenant_id"

# 테이블 SK 매핑 (ARCHITECTURE.md 3절)
_TABLE_SK: dict[str, str | None] = {
    "qa_tenants": None,
    "qa_evaluations_v2": "evaluation_id",
    "qa_sessions": "session_id",
    "qa_audit_log": "timestamp",
    "qa_quota_usage": "yyyy-mm",
}


def _require_tenant_id(tenant_id: str | None) -> None:
    if not tenant_id or not isinstance(tenant_id, str):
        raise ValueError("tenant_id is required for query isolation")


def get_resource():
    """Return a process-wide boto3 DynamoDB resource (lazy singleton)."""
    global _resource
    if _resource is None:
        with _resource_lock:
            if _resource is None:
                _resource = boto3.resource("dynamodb", region_name=_REGION, config=_BOTO_CONFIG)
    return _resource


def get_table(table_name: str):
    """Return a cached Table handle."""
    if table_name not in _table_cache:
        _table_cache[table_name] = get_resource().Table(table_name)
    return _table_cache[table_name]


def sk_name(table_name: str) -> str | None:
    """Look up the sort-key attribute name for a known table."""
    return _TABLE_SK.get(table_name)


def tenant_query(
    table_name: str,
    tenant_id: str,
    sk_prefix: str | None = None,
    *,
    limit: int | None = None,
    scan_index_forward: bool = True,
    exclusive_start_key: dict | None = None,
    **kwargs,
) -> list[dict]:
    """Query items scoped to tenant_id, optionally filtered by SK prefix.

    Returns all items (paginates until exhausted or `limit` reached).
    """
    _require_tenant_id(tenant_id)
    table = get_table(table_name)

    cond = Key(TENANT_PK).eq(tenant_id)
    sk = sk_name(table_name)
    if sk_prefix is not None:
        if not sk:
            raise ValueError(f"Table '{table_name}' has no sort key; sk_prefix not supported")
        cond = cond & Key(sk).begins_with(sk_prefix)

    params: dict[str, Any] = {
        "KeyConditionExpression": cond,
        "ScanIndexForward": scan_index_forward,
    }
    if limit is not None:
        params["Limit"] = limit
    if exclusive_start_key is not None:
        params["ExclusiveStartKey"] = exclusive_start_key
    params.update(kwargs)

    items: list[dict] = []
    while True:
        resp = table.query(**params)
        items.extend(resp.get("Items", []))
        if limit is not None and len(items) >= limit:
            return items[:limit]
        last = resp.get("LastEvaluatedKey")
        if not last:
            break
        params["ExclusiveStartKey"] = last
    return items


def tenant_get_item(table_name: str, tenant_id: str, sk: str | None = None) -> dict | None:
    """Get a single item by (tenant_id, sk). For tables without an SK pass sk=None."""
    _require_tenant_id(tenant_id)
    table = get_table(table_name)

    sk_attr = sk_name(table_name)
    key: dict[str, Any] = {TENANT_PK: tenant_id}
    if sk_attr is not None:
        if sk is None:
            raise ValueError(f"Table '{table_name}' requires sort key '{sk_attr}'")
        key[sk_attr] = sk
    resp = table.get_item(Key=key)
    return resp.get("Item")


def tenant_put_item(table_name: str, item: dict, **kwargs) -> dict:
    """Put an item — guards that `tenant_id` is present."""
    if not isinstance(item, dict):
        raise ValueError("item must be a dict")
    tid = item.get(TENANT_PK)
    if not tid:
        raise ValueError("tenant_id is required for query isolation")
    table = get_table(table_name)
    return table.put_item(Item=item, **kwargs)


def tenant_delete_item(table_name: str, tenant_id: str, sk: str | None = None, **kwargs) -> dict:
    """Delete an item by (tenant_id, sk)."""
    _require_tenant_id(tenant_id)
    table = get_table(table_name)

    sk_attr = sk_name(table_name)
    key: dict[str, Any] = {TENANT_PK: tenant_id}
    if sk_attr is not None:
        if sk is None:
            raise ValueError(f"Table '{table_name}' requires sort key '{sk_attr}'")
        key[sk_attr] = sk
    return table.delete_item(Key=key, **kwargs)


def tenant_update_item(
    table_name: str,
    tenant_id: str,
    sk: str | None,
    update_expression: str,
    expression_values: dict,
    expression_names: dict | None = None,
    **kwargs,
) -> dict:
    """Update helper scoped to (tenant_id, sk)."""
    _require_tenant_id(tenant_id)
    table = get_table(table_name)

    sk_attr = sk_name(table_name)
    key: dict[str, Any] = {TENANT_PK: tenant_id}
    if sk_attr is not None:
        if sk is None:
            raise ValueError(f"Table '{table_name}' requires sort key '{sk_attr}'")
        key[sk_attr] = sk

    params: dict[str, Any] = {
        "Key": key,
        "UpdateExpression": update_expression,
        "ExpressionAttributeValues": expression_values,
        "ReturnValues": "ALL_NEW",
    }
    if expression_names:
        params["ExpressionAttributeNames"] = expression_names
    params.update(kwargs)
    return table.update_item(**params)


def tenant_atomic_counter(
    table_name: str,
    tenant_id: str,
    sk: str | None,
    field: str,
    increment: int = 1,
    *,
    subfield: str | None = None,
    now_iso: str | None = None,
) -> dict:
    """Atomically bump a numeric counter scoped to (tenant_id, sk).

    - `field`: top-level attribute name (e.g. "request_count" or "minute_counters").
    - `subfield`: optional Map key (e.g. minute bucket "2026-04-17T02:45"). When set,
      the counter is ADD'd to `field.#subfield`; else ADD'd to `field`.
    - `now_iso`: ISO8601 timestamp for `updated_at` (defaults to current UTC).
    """
    if not field:
        raise ValueError("field is required")
    if increment == 0:
        raise ValueError("increment must be non-zero")

    from datetime import datetime, timezone  # stdlib-only, local to avoid top-level import

    ts = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    names: dict[str, str] = {"#f": field, "#u": "updated_at"}
    values: dict[str, Any] = {":v": increment, ":now": ts}

    if subfield is None:
        add_expr = "#f :v"
    else:
        names["#sub"] = subfield
        add_expr = "#f.#sub :v"

    update_expression = f"ADD {add_expr} SET #u = :now"
    return tenant_update_item(
        table_name,
        tenant_id,
        sk,
        update_expression=update_expression,
        expression_values=values,
        expression_names=names,
    )


__all__ = [
    "TENANT_PK",
    "get_resource",
    "get_table",
    "sk_name",
    "tenant_query",
    "tenant_get_item",
    "tenant_put_item",
    "tenant_delete_item",
    "tenant_update_item",
    "tenant_atomic_counter",
]
