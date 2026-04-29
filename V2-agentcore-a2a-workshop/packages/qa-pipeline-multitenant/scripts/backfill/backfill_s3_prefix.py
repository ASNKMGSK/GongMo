# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Copy legacy S3 objects under a tenants/kolon_default/ prefix.

============================================================
DO NOT EXECUTE — PL approval required (2026-04-17 deploy freeze)
============================================================

Reference:
- ARCHITECTURE.md §4 (S3 key structure)
- docs/DATA_ISOLATION.md §3 (S3 helper contract)
- docs/BACKFILL_PLAN.md §2.2 (non-destructive copy strategy)
- docs/PHASE1_MIGRATION_PLAN.md §2.3 (migration step)

Placeholders (PL plan §5 env-externalized — do not hardcode):
- env `QA_BUCKET_NAME`      : 공용 버킷 (§3.2 — 원본/대상 동일 버킷, prefix 만 상이)
- `<ACCOUNT_ID>`            : AWS 계정 ID
- env `LEGACY_S3_PREFIX`    : 기존 객체의 root prefix (예: raw/, reports/)

PL §3.2 확정: 비파괴 복사 — CopyObject only. 원본 prefix 의 객체는 삭제·수정 금지.

All AWS calls are stubbed via NotImplementedError. `__main__` guard prevents
execution even if imported as a script.
"""

from __future__ import annotations

import sys
from typing import Any, Iterable

# import boto3  # intentionally commented — see BACKFILL_PLAN §4

TENANT_ID = "kolon_default"
BUCKET_ENV = "QA_BUCKET_NAME"           # shared bucket per §3.2
SOURCE_PREFIX_ENV = "LEGACY_S3_PREFIX"  # e.g. "raw/" or "reports/"


def _block(*_args: Any, **_kwargs: Any) -> None:
    raise NotImplementedError(
        "blocked by 2026-04-17 deploy freeze — see docs/BACKFILL_PLAN.md §4"
    )


def _resolve_bucket() -> str:
    """Resolve the shared QA bucket from env. STUB."""
    # import os
    # v = os.getenv(BUCKET_ENV)
    # if not v: raise RuntimeError(f"{BUCKET_ENV} is not set")
    # return v
    _block()
    return ""


def _resolve_source_prefix() -> str:
    """Resolve the legacy S3 root prefix from env. STUB."""
    # import os
    # v = os.getenv(SOURCE_PREFIX_ENV)
    # if not v: raise RuntimeError(f"{SOURCE_PREFIX_ENV} is not set")
    # return v
    _block()
    return ""


def target_key_for(legacy_key: str) -> str:
    """Derive the new tenant-scoped key. Pure function — no AWS."""
    if not legacy_key:
        raise ValueError("legacy_key is required")
    relative = legacy_key.lstrip("/")
    return f"tenants/{TENANT_ID}/{relative}"


def iter_legacy_keys(bucket: str, prefix: str) -> Iterable[str]:
    """List legacy objects. STUB."""
    _block(bucket, prefix)
    yield ""  # unreachable


def copy_one(source_bucket: str, target_bucket: str, legacy_key: str) -> None:
    """CopyObject from legacy bucket/key to target `tenants/kolon_default/...`. STUB."""
    # from data import get_s3_client  # gated behind approval
    # client = get_s3_client()
    # client.copy_object(
    #     Bucket=target_bucket,
    #     Key=target_key_for(legacy_key),
    #     CopySource={"Bucket": source_bucket, "Key": legacy_key},
    # )
    _block(source_bucket, target_bucket, legacy_key)


def run_copy(
    source_bucket: str | None = None,
    target_bucket: str | None = None,
    prefix: str | None = None,
) -> dict:
    """Entry point. STUB — always raises.

    Bucket args default to env resolution. Source/target bucket are the SAME
    bucket (§3.2 shared-bucket decision) — only the prefix differs.
    """
    bucket = source_bucket or target_bucket or _resolve_bucket()
    pfx = prefix or _resolve_source_prefix()
    _block(bucket, pfx)
    return {"listed": 0, "copied": 0, "failed": 0}


if __name__ == "__main__":
    sys.exit(
        "execution blocked — backfill scripts are not runnable until PL approval "
        "(docs/BACKFILL_PLAN.md §4). See PHASE1_MIGRATION_PLAN.md §5 for unresolved items."
    )
