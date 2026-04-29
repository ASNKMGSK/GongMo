# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tenant-isolated data-access layer.

전 모듈은 Pool 모델(`tenant_id` 키 격리)을 강제한다. 직접 boto3 호출 대신
본 패키지의 헬퍼를 사용한다. ARCHITECTURE.md 3~4절 + DATA_ISOLATION.md 참조.

사용 예::

    from data import tenant_query, tenant_put_object, get_tenant_secret

    evals = tenant_query("qa_evaluations_v2", tenant_id, sk_prefix="2026-04-")
    tenant_put_object(tenant_id, "raw/2026-04-17/call.txt", body=b"...")
    creds = get_tenant_secret(tenant_id, "llm/anthropic-api-key")
"""

from .dynamo import (
    TENANT_PK,
    get_resource as get_dynamodb_resource,
    get_table,
    sk_name,
    tenant_atomic_counter,
    tenant_delete_item,
    tenant_get_item,
    tenant_put_item,
    tenant_query,
    tenant_update_item,
)
from .s3 import (
    TENANT_PREFIX,
    get_client as get_s3_client,
    tenant_delete_object,
    tenant_get_object,
    tenant_iter_objects,
    tenant_list_objects,
    tenant_presigned_url,
    tenant_put_object,
)
from .secrets import (
    SECRET_PREFIX,
    delete_tenant_secret,
    get_client as get_secrets_client,
    get_tenant_secret,
    invalidate_cache as invalidate_secret_cache,
    put_tenant_secret,
)

__all__ = [
    # dynamo
    "TENANT_PK",
    "get_dynamodb_resource",
    "get_table",
    "sk_name",
    "tenant_query",
    "tenant_get_item",
    "tenant_put_item",
    "tenant_delete_item",
    "tenant_update_item",
    "tenant_atomic_counter",
    # s3
    "TENANT_PREFIX",
    "get_s3_client",
    "tenant_put_object",
    "tenant_get_object",
    "tenant_list_objects",
    "tenant_iter_objects",
    "tenant_delete_object",
    "tenant_presigned_url",
    # secrets
    "SECRET_PREFIX",
    "get_secrets_client",
    "get_tenant_secret",
    "put_tenant_secret",
    "delete_tenant_secret",
    "invalidate_secret_cache",
]


def _lazy_opensearch():
    """OpenSearch 는 opensearch-py / requests-aws4auth 옵셔널 의존성 — 지연 로드."""
    from . import opensearch as _os  # noqa: WPS433

    return _os


def tenant_search(tenant_id: str, index: str, query: dict, **kwargs):
    return _lazy_opensearch().tenant_search(tenant_id, index, query, **kwargs)


def tenant_index_doc(tenant_id: str, index: str, doc: dict, **kwargs):
    return _lazy_opensearch().tenant_index_doc(tenant_id, index, doc, **kwargs)


__all__ += ["tenant_search", "tenant_index_doc"]
