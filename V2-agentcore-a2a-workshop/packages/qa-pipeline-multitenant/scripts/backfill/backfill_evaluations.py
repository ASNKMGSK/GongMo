# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Backfill legacy evaluation records into qa_evaluations_v2 with tenant_id=kolon_default.

============================================================
DO NOT EXECUTE — PL approval required (2026-04-17 deploy freeze)
============================================================

Reference:
- ARCHITECTURE.md §3 (DynamoDB schema)
- docs/DATA_ISOLATION.md §2 (helper contract)
- docs/BACKFILL_PLAN.md §2.3 (execution order)
- docs/PHASE1_MIGRATION_PLAN.md §2.3 (migration step), §5 (unresolved items)

Placeholders (PL plan §5 env-externalized — do not hardcode):
- env `LEGACY_EVAL_TABLE`   : 운영 평가 테이블명 (또는 SSM /qa/legacy/eval_table)
- `<ACCOUNT_ID>`            : AWS 계정 ID (ARN 작성 시 placeholder)
- env `QA_BUCKET_NAME`      : 신규 공용 데이터 버킷 (여기선 사용 안 함)

PL §3.2 확정: 비파괴 복사. 원본 테이블은 Scan 만 수행, 삭제·수정 금지.

This script is a *stub*. Every AWS call is blocked with `NotImplementedError`
so it cannot be run (even with `--dry-run`). Execution is gated by
`if __name__ == "__main__": sys.exit(...)` as well.
"""

from __future__ import annotations

import sys
from typing import Any, Iterable

# boto3 is imported for type-hint clarity only. Live clients are NEVER instantiated.
# import boto3  # intentionally commented — uncomment only after PL approval + §1 preconditions.

TENANT_ID = "kolon_default"
SOURCE_TABLE_ENV = "LEGACY_EVAL_TABLE"  # env var name (PL plan §5)
TARGET_TABLE = "qa_evaluations_v2"
SOURCE_ACCOUNT_ARN_TEMPLATE = (
    "arn:aws:dynamodb:<REGION>:<ACCOUNT_ID>:table/${LEGACY_EVAL_TABLE}"
)


def _block(*_args: Any, **_kwargs: Any) -> None:
    raise NotImplementedError(
        "blocked by 2026-04-17 deploy freeze — see docs/BACKFILL_PLAN.md §4"
    )


def _resolve_source_table() -> str:
    """Resolve the legacy table name from env/SSM. STUB — does not hit SSM."""
    # import os
    # name = os.getenv(SOURCE_TABLE_ENV)
    # if not name:
    #     # Fallback: ssm.get_parameter(Name="/qa/legacy/eval_table") — gated behind approval
    #     raise RuntimeError(f"{SOURCE_TABLE_ENV} is not set")
    # return name
    _block()
    return ""  # unreachable


def iter_legacy_items(source_table: str) -> Iterable[dict]:
    """Scan the legacy evaluation table. STUB — does not call AWS."""
    _block(source_table)
    yield {}  # unreachable — keeps type checkers happy


def transform(legacy_item: dict) -> dict:
    """Attach tenant_id and rename/coerce attributes for qa_evaluations_v2.

    Real mapping waits on the schema diff (docs/SCHEMA_DIFF.md) which requires
    operator input on the legacy schema (PHASE1_MIGRATION_PLAN §5 #4 unresolved).
    """
    if not isinstance(legacy_item, dict):
        raise TypeError("legacy_item must be a dict")

    new_item = dict(legacy_item)
    new_item["tenant_id"] = TENANT_ID

    if "evaluation_id" not in new_item:
        # Mapping rule pending schema diff — placeholder to document intent.
        # new_item["evaluation_id"] = legacy_item["<LEGACY_ID_FIELD>"]
        raise NotImplementedError(
            "evaluation_id mapping pending — populate after schema diff review"
        )
    return new_item


def put_into_target(item: dict) -> None:
    """Write a transformed record via the tenant-isolated helper. STUB."""
    # from data import tenant_put_item  # import gated behind approval
    # tenant_put_item(TARGET_TABLE, item)  # would guard tenant_id for us
    _block(item)


def run_backfill(source_table: str | None = None, *, limit: int | None = None) -> dict:
    """Entry point. STUB — always raises, never touches AWS.

    `source_table` defaults to `_resolve_source_table()` (env/SSM) when None.
    """
    table = source_table or _resolve_source_table()
    _block(table, limit)
    return {"scanned": 0, "written": 0, "failed": 0}


if __name__ == "__main__":
    sys.exit(
        "execution blocked — backfill scripts are not runnable until PL approval "
        "(docs/BACKFILL_PLAN.md §4). See PHASE1_MIGRATION_PLAN.md §5 for unresolved items."
    )
