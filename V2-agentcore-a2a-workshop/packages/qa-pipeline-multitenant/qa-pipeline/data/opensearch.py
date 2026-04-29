# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""OpenSearch tenant-isolated helpers.

검색 쿼리에 자동으로 `tenant_id` term 필터를 주입하고,
문서 인덱싱 시 `tenant_id` 필드를 강제한다.
"""

from __future__ import annotations

import copy
import os
import threading
from typing import Any

_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
_HOST = os.getenv("OPENSEARCH_HOST")  # 예: my-domain.us-east-1.es.amazonaws.com
_PORT = int(os.getenv("OPENSEARCH_PORT", "443"))
_SERVICE = os.getenv("OPENSEARCH_SERVICE", "aoss")  # aoss (Serverless) or es (managed)

_client_lock = threading.Lock()
_client: Any | None = None

TENANT_FIELD = "tenant_id"


def _require_tenant_id(tenant_id: str | None) -> None:
    if not tenant_id or not isinstance(tenant_id, str):
        raise ValueError("tenant_id is required for query isolation")


def get_client():
    """Return a process-wide opensearch-py client authenticated via SigV4."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                if not _HOST:
                    raise RuntimeError("OPENSEARCH_HOST env var is not set")
                # Lazy import so pure-DynamoDB consumers don't need opensearch-py.
                import boto3  # noqa: WPS433
                from opensearchpy import OpenSearch, RequestsHttpConnection  # noqa: WPS433
                from requests_aws4auth import AWS4Auth  # noqa: WPS433

                creds = boto3.Session().get_credentials()
                if creds is None:
                    raise RuntimeError("No AWS credentials available for OpenSearch")
                awsauth = AWS4Auth(
                    creds.access_key,
                    creds.secret_key,
                    _REGION,
                    _SERVICE,
                    session_token=creds.token,
                )
                _client = OpenSearch(
                    hosts=[{"host": _HOST, "port": _PORT}],
                    http_auth=awsauth,
                    use_ssl=True,
                    verify_certs=True,
                    connection_class=RequestsHttpConnection,
                    timeout=30,
                )
    return _client


def _inject_tenant_filter(query: dict, tenant_id: str) -> dict:
    """Wrap the incoming query in a bool filter that enforces tenant_id."""
    if not isinstance(query, dict):
        raise ValueError("query must be a dict")
    body = copy.deepcopy(query)
    inner = body.pop("query", {"match_all": {}})

    tenant_term = {"term": {TENANT_FIELD: tenant_id}}

    if isinstance(inner, dict) and "bool" in inner and isinstance(inner["bool"], dict):
        bool_q = copy.deepcopy(inner["bool"])
        filt = bool_q.get("filter") or []
        if isinstance(filt, dict):
            filt = [filt]
        filt.append(tenant_term)
        bool_q["filter"] = filt
        body["query"] = {"bool": bool_q}
    else:
        body["query"] = {"bool": {"must": [inner], "filter": [tenant_term]}}
    return body


def tenant_search(tenant_id: str, index: str, query: dict, **kwargs) -> dict:
    """Run a search with an auto-injected tenant_id filter."""
    _require_tenant_id(tenant_id)
    if not index:
        raise ValueError("index is required")
    body = _inject_tenant_filter(query, tenant_id)
    return get_client().search(index=index, body=body, **kwargs)


def tenant_index_doc(
    tenant_id: str,
    index: str,
    doc: dict,
    *,
    doc_id: str | None = None,
    refresh: bool | str = False,
) -> dict:
    """Index a document, forcing tenant_id to the provided value."""
    _require_tenant_id(tenant_id)
    if not isinstance(doc, dict):
        raise ValueError("doc must be a dict")
    payload = dict(doc)
    existing = payload.get(TENANT_FIELD)
    if existing and existing != tenant_id:
        raise ValueError(f"doc tenant_id '{existing}' does not match arg '{tenant_id}'")
    payload[TENANT_FIELD] = tenant_id

    kwargs: dict[str, Any] = {"index": index, "body": payload, "refresh": refresh}
    if doc_id is not None:
        kwargs["id"] = doc_id
    return get_client().index(**kwargs)


def tenant_delete_doc(tenant_id: str, index: str, doc_id: str, **kwargs) -> dict:
    """Delete a doc by id. Caller is responsible for ensuring ownership by tenant_id."""
    _require_tenant_id(tenant_id)
    return get_client().delete(index=index, id=doc_id, **kwargs)


def tenant_count(tenant_id: str, index: str, query: dict | None = None) -> int:
    """Count documents for a tenant (optionally filtered by query)."""
    _require_tenant_id(tenant_id)
    body = _inject_tenant_filter(query or {"query": {"match_all": {}}}, tenant_id)
    resp = get_client().count(index=index, body=body)
    return int(resp.get("count", 0))


__all__ = [
    "TENANT_FIELD",
    "get_client",
    "tenant_search",
    "tenant_index_doc",
    "tenant_delete_doc",
    "tenant_count",
]
