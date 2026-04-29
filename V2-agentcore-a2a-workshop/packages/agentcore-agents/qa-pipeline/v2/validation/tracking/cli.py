# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""tracking CLI — 변경 후 'record' / 'trend' / 'list' / 'show' / 'delete'.

사용 예 (qa-pipeline 디렉토리에서):

  # 1. 이미 평가된 결과 디렉토리로부터 메트릭 적재 (배치 재실행 없음 — 빠름)
  python -m v2.validation.tracking.cli record \
      --label iter06_clean \
      --dataset training \
      --from-results "C:/Users/META M/Desktop/학습셋_비교분석_20260422_000409" \
      --notes "language 프롬프트 #6 cushion 강화"

  # 2. 샘플 디렉토리부터 배치 평가 + 적재 (느림)
  python -m v2.validation.tracking.cli record \
      --label iter07 --dataset test --notes "evidence judge 임계 조정"

  # 3. 전체 시계열 + 회귀 알람 + HTML 트렌드 리포트
  python -m v2.validation.tracking.cli trend

  # 4. run 목록 / 단일 상세
  python -m v2.validation.tracking.cli list
  python -m v2.validation.tracking.cli show <run_id>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import webbrowser
from pathlib import Path

# v2 import 경로 보장
_HERE = Path(__file__).resolve()
_PIPELINE_DIR = _HERE.parents[3]
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))

from v2.validation.tracking.config import TRACKING_ROOT  # noqa: E402
from v2.validation.tracking.tracker import (  # noqa: E402
    delete_run, load_record, record_run,
)
from v2.validation.tracking.trend import (  # noqa: E402
    detect_regressions, load_history, render_trend_html,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("tracking.cli")


def _cmd_record(args: argparse.Namespace) -> int:
    rec = record_run(
        label=args.label,
        dataset=args.dataset,
        model=args.model,
        from_results_dir=args.from_results,
        samples_dir=args.samples_dir,
        xlsx_path=args.xlsx,
        notes=args.notes or "",
        parent_run=args.parent,
        max_concurrent=args.max_concurrent,
        per_sample_timeout=args.timeout,
    )
    print(f"\n[OK] run 적재됨: {rec.run_id}")
    print(f"     overall: {json.dumps(rec.overall, ensure_ascii=False)}")
    print(f"     dir    : {TRACKING_ROOT / rec.run_id}")

    # 자동 회귀 체크
    history = load_history()
    if len(history) >= 2 and history[-1].run_id == rec.run_id:
        alerts = detect_regressions(history)
        if alerts:
            print(f"\n⚠ 직전 run 대비 회귀 {len(alerts)} 건:")
            for a in alerts[:10]:
                print(f"   [{a.severity}] {a.metric} ({a.scope}): {a.reason}")
            if len(alerts) > 10:
                print(f"   ... +{len(alerts)-10} more")
        else:
            print("\n✓ 직전 run 대비 회귀 없음.")
    return 0


def _cmd_trend(args: argparse.Namespace) -> int:
    records = load_history(dataset=args.dataset, label_substr=args.label_substr, limit=args.limit)
    if not records:
        print(f"[ERR] 적재된 run 없음 ({TRACKING_ROOT})")
        return 1
    html = render_trend_html(records, output_path=args.output, metric_for_chart=args.chart_metric)
    print(f"트렌드 리포트 생성: {html}")
    if args.open:
        webbrowser.open(html.as_uri())
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    records = load_history(dataset=args.dataset, label_substr=args.label_substr)
    if not records:
        print(f"(empty) — TRACKING_ROOT: {TRACKING_ROOT}")
        return 0
    print(f"{'run_id':40s} {'label':24s} {'ds':10s} {'n':6s} {'MAE':>6s} {'RMSE':>6s} {'Bias':>6s}  notes")
    print("-" * 130)
    for r in records:
        notes = (r.notes or "")[:50]
        print(
            f"{r.run_id:40s} {r.label[:24]:24s} {r.dataset:10s} "
            f"{r.matched_count}/{r.sample_count:<3d}  "
            f"{_fmt(r.overall.get('MAE')):>6s} {_fmt(r.overall.get('RMSE')):>6s} "
            f"{_fmt(r.overall.get('Bias')):>6s}  {notes}"
        )
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    rd = TRACKING_ROOT / args.run_id
    if not rd.exists():
        print(f"[ERR] 미존재: {rd}")
        return 1
    rec = load_record(rd)
    print(json.dumps({
        "run_id": rec.run_id, "label": rec.label, "timestamp": rec.timestamp,
        "git_sha": rec.git_sha, "dataset": rec.dataset, "model": rec.model,
        "matched": f"{rec.matched_count}/{rec.sample_count}",
        "notes": rec.notes, "parent_run": rec.parent_run,
        "overall": rec.overall,
        "per_item": {str(k): v for k, v in rec.per_item.items()},
    }, ensure_ascii=False, indent=2))
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    if delete_run(args.run_id):
        print(f"[OK] 삭제: {args.run_id}")
        return 0
    print(f"[ERR] 미존재: {args.run_id}")
    return 1


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tracking", description="QA 성능 변화 지속 추적")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("record", help="단일 run 적재")
    pr.add_argument("--label", required=True, help="짧은 식별 태그 (예: iter06)")
    pr.add_argument("--dataset", default="training", choices=["training", "test", "custom"])
    pr.add_argument("--model", default="us.anthropic.claude-sonnet-4-20250514-v1:0")
    pr.add_argument("--from-results", help="이미 평가된 *_result.json 디렉토리. 지정 시 배치 재실행 없음.")
    pr.add_argument("--samples-dir", help="custom dataset 이거나 기본 경로 오버라이드")
    pr.add_argument("--xlsx", help="GT xlsx 경로 (미지정 시 default_xlsx_path)")
    pr.add_argument("--notes", default="", help="변경사항 메모")
    pr.add_argument("--parent", help="비교 기준 run_id (생략 시 직전 run 자동)")
    pr.add_argument("--max-concurrent", type=int, default=3)
    pr.add_argument("--timeout", type=float, default=900.0, help="샘플당 타임아웃(s)")
    pr.set_defaults(func=_cmd_record)

    pt = sub.add_parser("trend", help="시계열 + 회귀 알람 + HTML 리포트")
    pt.add_argument("--dataset", help="필터 (training/test)")
    pt.add_argument("--label-substr", help="label 부분일치 필터")
    pt.add_argument("--limit", type=int, help="최근 N 개만")
    pt.add_argument("--chart-metric", default="MAE", choices=["MAE", "RMSE", "Bias", "MAPE"])
    pt.add_argument("--output", help="HTML 출력 경로 (기본 TRACKING_ROOT/trend.html)")
    pt.add_argument("--open", action="store_true", help="생성 후 브라우저 자동 열기")
    pt.set_defaults(func=_cmd_trend)

    pl = sub.add_parser("list", help="run 목록")
    pl.add_argument("--dataset", help="필터")
    pl.add_argument("--label-substr", help="label 부분일치 필터")
    pl.set_defaults(func=_cmd_list)

    ps = sub.add_parser("show", help="단일 run 상세")
    ps.add_argument("run_id")
    ps.set_defaults(func=_cmd_show)

    pd = sub.add_parser("delete", help="run 삭제 (디렉토리 제거)")
    pd.add_argument("run_id")
    pd.set_defaults(func=_cmd_delete)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
