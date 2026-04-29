# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Post-backfill verification: count + sample parity between legacy and new stores.

============================================================
DO NOT EXECUTE — PL approval required (2026-04-17 deploy freeze)
============================================================

Reference:
- docs/BACKFILL_PLAN.md §3 (verification criteria)
- docs/DATA_ISOLATION.md §2, §3 (helper contracts used for the new store)
- docs/PHASE1_MIGRATION_PLAN.md §2.3

Placeholders (PL plan §5 env-externalized — do not hardcode):
- env `LEGACY_EVAL_TABLE`, `LEGACY_SESSION_TABLE`, `LEGACY_S3_PREFIX`, `QA_BUCKET_NAME`
- `<ACCOUNT_ID>`

PL §3.2 확정: 비파괴 복사 — verify 는 read-only 지만 freeze 대상이므로 실행 금지.

Checks planned (none run here):
1. DynamoDB item count parity (legacy scan count vs tenant_query count).
2. Random sample of 10 items — attribute equality except `tenant_id`.
3. S3 object count parity (legacy list vs tenant_list_objects).
4. Random sample of 20 objects — ETag/MD5 match.
"""

from __future__ import annotations

import sys
from typing import Any

# import boto3  # intentionally commented

TENANT_ID = "kolon_default"


def _block(*_args: Any, **_kwargs: Any) -> None:
    raise NotImplementedError(
        "blocked by 2026-04-17 deploy freeze — see docs/BACKFILL_PLAN.md §4"
    )


def count_parity_dynamo(legacy_table: str, new_table: str) -> dict:
    """Return {legacy_count, new_count, delta}. STUB."""
    _block(legacy_table, new_table)
    return {"legacy_count": 0, "new_count": 0, "delta": 0}


def sample_equality_dynamo(legacy_table: str, new_table: str, *, n: int = 10) -> list[dict]:
    """Sample comparison result per item. STUB."""
    _block(legacy_table, new_table, n)
    return []


def count_parity_s3(legacy_bucket: str, legacy_prefix: str, new_bucket: str) -> dict:
    """Return {legacy_count, new_count, delta}. STUB."""
    _block(legacy_bucket, legacy_prefix, new_bucket)
    return {"legacy_count": 0, "new_count": 0, "delta": 0}


def etag_sample_s3(legacy_bucket: str, new_bucket: str, *, n: int = 20) -> list[dict]:
    """Compare ETags for N random objects. STUB."""
    _block(legacy_bucket, new_bucket, n)
    return []


def build_report() -> dict:
    """Aggregate all checks into a single report. STUB."""
    _block()
    return {
        "dynamo": {"evaluations": {}, "sessions": {}},
        "s3": {},
        "samples": {"dynamo": [], "s3": []},
    }


if __name__ == "__main__":
    sys.exit(
        "execution blocked — verification is read-only but still gated by deploy freeze "
        "(docs/BACKFILL_PLAN.md §4). Unblock after PL approval + §1 preconditions."
    )
