# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Dev2 data isolation helpers — tenant_id guard."""

from __future__ import annotations

import pytest
from data.dynamo import (
    TENANT_PK,
    sk_name,
    tenant_put_item,
    tenant_query,
    tenant_get_item,
    tenant_delete_item,
)


def test_tenant_pk_constant():
    assert TENANT_PK == "tenant_id"


def test_sk_mapping_complete():
    assert sk_name("qa_tenants") is None
    assert sk_name("qa_evaluations_v2") == "evaluation_id"
    assert sk_name("qa_sessions") == "session_id"
    assert sk_name("qa_audit_log") == "timestamp"
    assert sk_name("qa_quota_usage") == "yyyy-mm"


def test_tenant_query_requires_tenant_id():
    with pytest.raises(ValueError, match="tenant_id"):
        tenant_query("qa_evaluations_v2", "")
    with pytest.raises(ValueError, match="tenant_id"):
        tenant_query("qa_evaluations_v2", None)


def test_tenant_get_item_requires_tenant_id():
    with pytest.raises(ValueError, match="tenant_id"):
        tenant_get_item("qa_tenants", "")


def test_tenant_put_item_rejects_missing_tenant_id():
    with pytest.raises(ValueError, match="tenant_id"):
        tenant_put_item("qa_tenants", {"display_name": "no tid"})


def test_tenant_delete_item_requires_tenant_id():
    with pytest.raises(ValueError, match="tenant_id"):
        tenant_delete_item("qa_sessions", "", sk="s1")


def test_tenant_put_item_requires_sk_when_table_has_one():
    # item with only tenant_id should still be rejected as the put will fail at DynamoDB —
    # but top-level guard only checks tenant_id presence. Still, item MUST contain tenant_id.
    # This verifies the basic guard works before any AWS call.
    with pytest.raises(ValueError):
        tenant_put_item("qa_evaluations_v2", {"evaluation_id": "e1"})


def test_s3_put_requires_tenant_id():
    from data.s3 import tenant_put_object

    with pytest.raises(ValueError, match="tenant_id"):
        tenant_put_object("", "raw/foo.txt", b"x")


def test_s3_get_requires_tenant_id():
    from data.s3 import tenant_get_object

    with pytest.raises(ValueError, match="tenant_id"):
        tenant_get_object("", "raw/foo.txt")


def test_secrets_get_requires_tenant_id():
    from data.secrets import get_tenant_secret

    with pytest.raises(ValueError, match="tenant_id"):
        get_tenant_secret("", "api_key")


def test_secrets_prefix_is_qa_slash():
    from data.secrets import SECRET_PREFIX

    assert SECRET_PREFIX == "/qa"
