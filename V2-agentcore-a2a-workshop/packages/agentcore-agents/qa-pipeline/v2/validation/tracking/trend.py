# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""run 시계열 비교 + 회귀 탐지 + HTML 트렌드 리포트.

- `load_history(dataset=None, limit=None) -> list[RunRecord]`
- `compute_deltas(curr, prev) -> dict`
- `detect_regressions(records, baseline_run=None) -> list[Alert]`
- `render_trend_html(records, output_path) -> Path`
"""

from __future__ import annotations

import html
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from v2.scripts.compare_learning_set_vs_xlsx import ITEM_DEF  # type: ignore[import-untyped]

from v2.validation.tracking.config import REGRESSION_THRESHOLDS, TRACKING_ROOT
from v2.validation.tracking.tracker import RunRecord, load_record


logger = logging.getLogger(__name__)


@dataclass
class Alert:
    run_id: str
    metric: str           # "MAE" | "RMSE" | "Bias" | "MAPE"
    scope: str            # "overall" | f"item#{num}"
    prev: float | None
    curr: float | None
    delta: float | None   # curr - prev
    severity: str         # "warn" | "critical"
    reason: str


# ───────────────────────────────────────────────────────────────────────
# 로더
# ───────────────────────────────────────────────────────────────────────


def load_history(
    *,
    dataset: str | None = None,
    label_substr: str | None = None,
    limit: int | None = None,
) -> list[RunRecord]:
    """TRACKING_ROOT 의 run 디렉토리들을 timestamp 오름차순으로 반환."""
    if not TRACKING_ROOT.exists():
        return []
    records: list[RunRecord] = []
    for rd in sorted(TRACKING_ROOT.iterdir()):
        if not rd.is_dir():
            continue
        meta_path = rd / "meta.json"
        if not meta_path.exists():
            continue
        try:
            r = load_record(rd)
        except Exception as e:
            logger.warning("로드 실패 %s: %s", rd.name, e)
            continue
        if dataset and r.dataset != dataset:
            continue
        if label_substr and label_substr not in r.label:
            continue
        records.append(r)
    records.sort(key=lambda x: x.timestamp)
    if limit:
        records = records[-limit:]
    return records


# ───────────────────────────────────────────────────────────────────────
# delta + 회귀 탐지
# ───────────────────────────────────────────────────────────────────────


def compute_deltas(curr: RunRecord, prev: RunRecord) -> dict[str, Any]:
    """현재 run vs 직전 run 의 overall 메트릭 변화."""
    out: dict[str, Any] = {"prev_run_id": prev.run_id, "curr_run_id": curr.run_id, "metrics": {}}
    for k in ("MAE", "RMSE", "Bias", "MAPE", "MaxAbs", "Over%", "Under%"):
        c = curr.overall.get(k)
        p = prev.overall.get(k)
        d = (c - p) if (isinstance(c, (int, float)) and isinstance(p, (int, float))) else None
        out["metrics"][k] = {"prev": p, "curr": c, "delta": round(d, 3) if d is not None else None}
    return out


def detect_regressions(
    records: list[RunRecord],
    *,
    baseline_run: str | None = None,
) -> list[Alert]:
    """직전(또는 baseline) run 대비 회귀 알람 리스트.

    REGRESSION_THRESHOLDS 의 metric 별 direction/threshold 기준으로 판정.
    - lower_better: delta > +threshold → warn, delta > 2*threshold → critical
    - absolute    : |delta| > threshold → warn, > 2*threshold → critical
    """
    if len(records) < 2:
        return []
    curr = records[-1]
    if baseline_run:
        prev = next((r for r in records if r.run_id == baseline_run), None)
        if prev is None:
            return [Alert(curr.run_id, "_meta", "baseline", None, None, None, "warn",
                          f"baseline_run='{baseline_run}' 미발견")]
    else:
        prev = records[-2]

    alerts: list[Alert] = []
    for metric, cfg in REGRESSION_THRESHOLDS.items():
        c = curr.overall.get(metric)
        p = prev.overall.get(metric)
        if not isinstance(c, (int, float)) or not isinstance(p, (int, float)):
            continue
        delta = c - p
        thr = float(cfg["threshold"])
        direction = cfg["direction"]
        if direction == "lower_better":
            if delta > thr:
                sev = "critical" if delta > 2 * thr else "warn"
                alerts.append(Alert(
                    curr.run_id, metric, "overall", p, c, round(delta, 3), sev,
                    f"{metric} 악화 +{delta:.2f} (직전 {p} → {c}, 임계 +{thr})",
                ))
        else:  # absolute
            if abs(delta) > thr:
                sev = "critical" if abs(delta) > 2 * thr else "warn"
                alerts.append(Alert(
                    curr.run_id, metric, "overall", p, c, round(delta, 3), sev,
                    f"{metric} 변동 |{delta:+.2f}| (직전 {p} → {c}, 임계 ±{thr})",
                ))

    # 항목별 — MAE 만 체크 (per-item RMSE 노이즈 큼)
    item_thr = float(REGRESSION_THRESHOLDS["MAE"]["threshold"])
    for num, _name, _mx, _row in ITEM_DEF:
        c_item = curr.per_item.get(num, {})
        p_item = prev.per_item.get(num, {})
        c = c_item.get("MAE"); p = p_item.get("MAE")
        if not isinstance(c, (int, float)) or not isinstance(p, (int, float)):
            continue
        delta = c - p
        if delta > item_thr:
            sev = "critical" if delta > 2 * item_thr else "warn"
            alerts.append(Alert(
                curr.run_id, "MAE", f"item#{num}", p, c, round(delta, 3), sev,
                f"항목 #{num} ({c_item.get('item_name','')}) MAE 악화 +{delta:.2f}",
            ))
    return alerts


# ───────────────────────────────────────────────────────────────────────
# HTML 트렌드 리포트
# ───────────────────────────────────────────────────────────────────────


_HTML_TEMPLATE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>QA 성능 추적 — Trend</title>
<style>
 body {{ font-family: -apple-system, "Segoe UI", sans-serif; background:#0f172a; color:#e2e8f0; padding:20px; margin:0; }}
 h1 {{ color:#fbbf24; font-size:20px; margin-bottom:6px; }}
 .meta {{ color:#94a3b8; font-size:12px; margin-bottom:20px; }}
 table {{ border-collapse:collapse; width:100%; margin-bottom:24px; font-size:12px; background:#1e293b; }}
 th, td {{ padding:8px 10px; border-bottom:1px solid #334155; text-align:right; }}
 th:first-child, td:first-child {{ text-align:left; }}
 th {{ background:#334155; color:#fbbf24; font-weight:600; position:sticky; top:0; }}
 tr.alert-warn {{ background:rgba(245,158,11,0.08); }}
 tr.alert-critical {{ background:rgba(239,68,68,0.12); }}
 .delta-up {{ color:#fb7185; font-weight:600; }}
 .delta-down {{ color:#34d399; font-weight:600; }}
 .delta-zero {{ color:#94a3b8; }}
 .badge {{ display:inline-block; padding:2px 6px; border-radius:3px; font-size:10px; font-weight:700; margin-left:6px; }}
 .badge.warn {{ background:#f59e0b; color:#000; }}
 .badge.critical {{ background:#ef4444; color:#fff; }}
 .alert-card {{ padding:10px 14px; border-radius:6px; margin-bottom:8px; font-size:12px; }}
 .alert-card.warn {{ background:rgba(245,158,11,0.1); border-left:3px solid #f59e0b; }}
 .alert-card.critical {{ background:rgba(239,68,68,0.12); border-left:3px solid #ef4444; }}
 .label {{ color:#fbbf24; font-weight:600; }}
 .sha {{ font-family:monospace; color:#94a3b8; font-size:11px; }}
 svg {{ background:#1e293b; border-radius:6px; }}
 .legend {{ font-size:11px; color:#94a3b8; margin-bottom:6px; }}
</style></head><body>
<h1>QA 성능 추적 — 시계열</h1>
<div class="meta">총 {n_runs} 건 · TRACKING_ROOT: <span class="sha">{root}</span></div>
{alerts_html}
{chart_html}
{table_html}
</body></html>
"""


def _render_alerts(alerts: list[Alert]) -> str:
    if not alerts:
        return '<div class="alert-card warn" style="background:rgba(34,197,94,0.1);border-left:3px solid #22c55e;color:#86efac">✓ 직전 run 대비 회귀 없음</div>'
    out = ['<h2 style="font-size:14px;color:#fbbf24">⚠ 회귀 알람 ({} 건)</h2>'.format(len(alerts))]
    for a in alerts:
        out.append(
            f'<div class="alert-card {a.severity}">'
            f'<span class="badge {a.severity}">{a.severity.upper()}</span> '
            f'<b>{html.escape(a.metric)}</b> · {html.escape(a.scope)} → {html.escape(a.reason)}'
            f'</div>'
        )
    return "\n".join(out)


def _render_chart(records: list[RunRecord], metric: str = "MAE") -> str:
    """시계열 SVG (절대값). 직전 대비 악화 구간은 붉은 점."""
    if not records:
        return ""
    pts: list[tuple[int, float]] = []
    for i, r in enumerate(records):
        v = r.overall.get(metric)
        if isinstance(v, (int, float)):
            pts.append((i, float(v)))
    if not pts:
        return f"<div class='legend'>{metric}: 데이터 없음</div>"
    w, h, pad = 760, 200, 36
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x_min, x_max = 0, max(len(records) - 1, 1)
    y_min, y_max = min(ys + [0]), max(ys) * 1.15 if ys else 1.0
    if y_max - y_min < 1e-9:
        y_max = y_min + 1.0

    def sx(x): return pad + (x - x_min) / (x_max - x_min or 1) * (w - 2 * pad)
    def sy(y): return h - pad - (y - y_min) / (y_max - y_min or 1) * (h - 2 * pad)

    path = " ".join(("M" if i == 0 else "L") + f"{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(pts))
    dots = []
    for i, (x, y) in enumerate(pts):
        prev_y = pts[i - 1][1] if i > 0 else None
        color = "#fb7185" if (prev_y is not None and y > prev_y) else "#34d399" if (prev_y is not None and y < prev_y) else "#fbbf24"
        title = html.escape(f"{records[x].label} · {metric}={y:.2f}")
        dots.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4" fill="{color}"><title>{title}</title></circle>')
    grid_y = "\n".join(
        f'<line x1="{pad}" x2="{w-pad}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" stroke="#334155" stroke-dasharray="2,3"/>'
        f'<text x="6" y="{sy(t)+4:.1f}" fill="#64748b" font-size="10">{t:.1f}</text>'
        for t in [y_min, (y_min + y_max) / 2, y_max]
    )
    return (
        f'<div class="legend">메트릭: <b>{metric}</b> (낮을수록 사람 점수와 유사). '
        f'붉은 점 = 직전 대비 악화 / 초록 = 개선 / 노랑 = 첫 run.</div>'
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'{grid_y}'
        f'<path d="{path}" stroke="#fbbf24" stroke-width="2" fill="none"/>'
        f'{"".join(dots)}'
        f'</svg>'
    )


def _render_table(records: list[RunRecord], alert_runs: dict[str, str]) -> str:
    """run별 overall + 직전 대비 delta."""
    rows = ['<tr><th>run_id</th><th>label</th><th>git</th><th>dataset</th><th>n</th>'
            '<th>MAE</th><th>ΔMAE</th><th>RMSE</th><th>ΔRMSE</th><th>Bias</th><th>MAPE</th><th>MaxAbs</th><th>Over%</th><th>Under%</th></tr>']
    for i, r in enumerate(records):
        prev = records[i - 1] if i > 0 else None
        row_cls = ""
        if r.run_id in alert_runs:
            row_cls = f"alert-{alert_runs[r.run_id]}"

        def cell(metric: str) -> str:
            return _fmt(r.overall.get(metric))

        def dcell(metric: str) -> str:
            if prev is None:
                return "—"
            c = r.overall.get(metric); p = prev.overall.get(metric)
            if not isinstance(c, (int, float)) or not isinstance(p, (int, float)):
                return "—"
            d = c - p
            cls = "delta-up" if d > 0 else "delta-down" if d < 0 else "delta-zero"
            return f'<span class="{cls}">{d:+.2f}</span>'

        rows.append(
            f'<tr class="{row_cls}">'
            f'<td>{html.escape(r.run_id)}</td>'
            f'<td><span class="label">{html.escape(r.label)}</span></td>'
            f'<td class="sha">{html.escape(r.git_sha or "-")}</td>'
            f'<td>{html.escape(r.dataset)}</td>'
            f'<td>{r.matched_count}/{r.sample_count}</td>'
            f'<td>{cell("MAE")}</td><td>{dcell("MAE")}</td>'
            f'<td>{cell("RMSE")}</td><td>{dcell("RMSE")}</td>'
            f'<td>{cell("Bias")}</td><td>{cell("MAPE")}</td>'
            f'<td>{cell("MaxAbs")}</td><td>{cell("Over%")}</td><td>{cell("Under%")}</td>'
            f'</tr>'
        )
    return '<h2 style="font-size:14px;color:#fbbf24">전체 run 메트릭</h2><table>' + "\n".join(rows) + "</table>"


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def render_trend_html(
    records: list[RunRecord] | None = None,
    *,
    output_path: Path | str | None = None,
    metric_for_chart: str = "MAE",
) -> Path:
    if records is None:
        records = load_history()
    if output_path is None:
        output_path = TRACKING_ROOT / "trend.html"
    out = Path(output_path)
    alerts = detect_regressions(records) if len(records) >= 2 else []
    alert_runs = {a.run_id: a.severity for a in alerts}
    # warn 보다 critical 우선
    for a in alerts:
        if a.severity == "critical":
            alert_runs[a.run_id] = "critical"

    body = _HTML_TEMPLATE.format(
        n_runs=len(records),
        root=html.escape(str(TRACKING_ROOT)),
        alerts_html=_render_alerts(alerts),
        chart_html=_render_chart(records, metric_for_chart),
        table_html=_render_table(records, alert_runs),
    )
    out.write_text(body, encoding="utf-8")
    return out
