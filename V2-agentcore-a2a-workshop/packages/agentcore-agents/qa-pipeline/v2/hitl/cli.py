# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""HITL 피드백 루프 CLI 진입점.

서브커맨드:
    nightly [--dry-run]                         — 일배치 실행
    stats [--item N] [--window N]               — 항목 통계 출력
    triggers <review_id>                        — 특정 리뷰의 발동 조건 진단

사용:
    python -m v2.hitl.cli nightly --dry-run
    python -m v2.hitl.cli stats
    python -m v2.hitl.cli stats --item 7
    python -m v2.hitl.cli triggers 42
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any


_QA_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _QA_PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _QA_PIPELINE_DIR)


from v2.hitl import db, feedback_loop  # noqa: E402
from v2.hitl.trigger_conditions import (  # noqa: E402
    detect_tuning_priority,
    is_eligible_for_golden,
)


def _dump(payload: Any) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")


def _cmd_nightly(args: argparse.Namespace) -> int:
    result = feedback_loop.run_nightly_batch(dry_run=bool(args.dry_run))
    _dump(result)
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    window = int(args.window)
    if args.item is not None:
        stats = feedback_loop.compute_item_stats(int(args.item), rolling_window=window)
        needs_tuning, reasons = detect_tuning_priority(stats)
        _dump({"stats": stats, "needs_tuning": needs_tuning, "reasons": reasons})
        return 0

    all_stats = feedback_loop.compute_all_item_stats(rolling_window=window)
    enriched: list[dict[str, Any]] = []
    for s in all_stats:
        needs_tuning, reasons = detect_tuning_priority(s)
        enriched.append({**s, "needs_tuning": needs_tuning, "reasons": reasons})
    _dump(enriched)
    return 0


def _cmd_triggers(args: argparse.Namespace) -> int:
    db.init_db()
    review = db.get_review(int(args.review_id))
    if review is None:
        _dump({"error": f"review_id={args.review_id} not found"})
        return 1
    eligible, reasons = is_eligible_for_golden(review)
    _dump(
        {
            "review_id": review.get("id"),
            "consultation_id": review.get("consultation_id"),
            "item_number": review.get("item_number"),
            "status": review.get("status"),
            "eligible": eligible,
            "reasons": reasons,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="v2.hitl.cli", description="HITL feedback loop CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_nightly = sub.add_parser("nightly", help="run nightly batch")
    p_nightly.add_argument("--dry-run", action="store_true", help="평가만 수행, INSERT 생략")
    p_nightly.set_defaults(func=_cmd_nightly)

    p_stats = sub.add_parser("stats", help="item-level rolling stats")
    p_stats.add_argument("--item", type=int, default=None, help="단일 항목 번호 (생략 시 전체)")
    p_stats.add_argument("--window", type=int, default=50, help="rolling window (default 50)")
    p_stats.set_defaults(func=_cmd_stats)

    p_triggers = sub.add_parser("triggers", help="diagnose trigger conditions for a review")
    p_triggers.add_argument("review_id", type=int)
    p_triggers.set_defaults(func=_cmd_triggers)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
