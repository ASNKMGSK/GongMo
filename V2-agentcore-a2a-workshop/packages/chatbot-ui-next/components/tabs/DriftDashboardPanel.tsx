// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useMemo, useState } from "react";

import { fetchDriftStats, type DriftStats } from "@/lib/api";

/* ─────────────────────────────────────────────────────────────
   DriftDashboardPanel — PDF §11 운영 모니터링 (Mastercard 톤)
   - 인간-AI 일치도 추이 (MAE 라인)
   - 검수자 수정률 (강/약/무수정 분포)
   - Tier 분포 추이 (T0~T3 스택 막대)

   백엔드 /v2/drift/stats 미구현 → MOCK_DRIFT 로 fallback.
   허용 지표: MAE / RMSE / Bias / MAPE / Accuracy 만 사용.
   상관계수(Pearson/Spearman/R²/κ) 노출 금지.
   ───────────────────────────────────────────────────────────── */

type PeriodKey = "7d" | "14d" | "30d";

const PERIOD_OPTIONS: Array<{ value: PeriodKey; label: string; days: number }> = [
  { value: "7d", label: "최근 7일", days: 7 },
  { value: "14d", label: "최근 14일", days: 14 },
  { value: "30d", label: "최근 30일", days: 30 },
];

// ★ 2026-05-07: MOCK 제거. 백엔드 /v2/drift/stats 가 실데이터 반환.
// 데이터 0 건이면 응답.empty=true → UI 가 빈 상태 카드 표시.
const EMPTY_STATS: DriftStats = {
  mae_trend: [],
  reviewer_revisions: { strong: 0, weak: 0, none: 0, total: 0 },
  tier_history: [],
  empty: true,
  period_days: 7,
  total_reviews: 0,
  total_confirmed: 0,
};

/* ─── 작은 뷰 헬퍼들 (CSS div 기반 차트 — 외부 라이브러리 X) ─── */

function ChartHeader({ eyebrow, title, sub }: { eyebrow: string; title: string; sub?: string }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <span className="section-eyebrow" style={{ fontSize: 11 }}>
        {eyebrow}
      </span>
      <h4
        style={{
          margin: "8px 0 0",
          fontSize: 18,
          fontWeight: 500,
          letterSpacing: "-0.02em",
          color: "var(--ink-display)",
        }}
      >
        {title}
      </h4>
      {sub ? (
        <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--ink-muted)" }}>{sub}</p>
      ) : null}
    </div>
  );
}

function MaeTrendChart({ trend }: { trend: DriftStats["mae_trend"] }) {
  const maxMae = useMemo(() => Math.max(1, ...trend.map((d) => d.mae)), [trend]);
  const current = trend.length > 0 ? trend[trend.length - 1].mae : 0;
  const previous = trend.length > 1 ? trend[trend.length - 2].mae : current;
  const direction = current < previous ? "↓ 개선" : current > previous ? "↑ 악화" : "→ 유지";
  // MAE 는 낮을수록 좋음 — 개선 시 success, 악화 시 accent (orange)
  const directionColor =
    current < previous
      ? "var(--success)"
      : current > previous
        ? "var(--accent)"
        : "var(--ink-muted)";
  const directionBg =
    current < previous
      ? "var(--success-bg)"
      : current > previous
        ? "var(--accent-bg)"
        : "var(--surface-sunken)";

  return (
    <div className="card" style={{ padding: 28 }}>
      <ChartHeader
        eyebrow="MAE TREND"
        title="인간-AI 일치도 추이"
        sub="MAE — 점수 평균 절대 오차 (낮을수록 좋음)"
      />

      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: 10,
          height: 168,
          padding: "20px 4px 4px",
        }}
        role="img"
        aria-label={`MAE 추이: ${trend.map((d) => `${d.date} ${d.mae.toFixed(2)}`).join(", ")}`}
      >
        {trend.map((d, i) => {
          const isLatest = i === trend.length - 1;
          const heightPct = Math.max(2, (d.mae / maxMae) * 100);
          return (
            <div
              key={d.date}
              style={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 4,
                height: "100%",
                justifyContent: "flex-end",
              }}
            >
              <div
                style={{
                  width: "100%",
                  height: `${heightPct}%`,
                  // 최신만 ink-black 강조, 나머지는 putty 톤 (Mastercard 스타일 — 색은 한 곳에만)
                  background: isLatest ? "var(--ink-cta)" : "var(--border-strong)",
                  borderRadius: "999px 999px 0 0",
                  position: "relative",
                  transition: "background 220ms cubic-bezier(0.2, 0, 0, 1)",
                }}
                title={`${d.date}: MAE ${d.mae.toFixed(2)}`}
              >
                <span
                  style={{
                    position: "absolute",
                    top: -22,
                    left: "50%",
                    transform: "translateX(-50%)",
                    fontSize: 11,
                    fontWeight: isLatest ? 700 : 450,
                    color: isLatest ? "var(--ink-display)" : "var(--ink-muted)",
                    whiteSpace: "nowrap",
                    fontVariantNumeric: "tabular-nums",
                    letterSpacing: "-0.01em",
                  }}
                >
                  {d.mae.toFixed(1)}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      <div
        style={{
          display: "flex",
          gap: 10,
          marginTop: 10,
          paddingTop: 10,
          borderTop: "1px solid var(--border-subtle)",
        }}
      >
        {trend.map((d, i) => {
          const isLatest = i === trend.length - 1;
          return (
            <div
              key={d.date}
              style={{
                flex: 1,
                fontSize: 11,
                textAlign: "center",
                color: isLatest ? "var(--ink)" : "var(--ink-subtle)",
                fontWeight: isLatest ? 600 : 450,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {d.date}
            </div>
          );
        })}
      </div>

      <div
        style={{
          marginTop: 20,
          display: "flex",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <div>
          <div style={{ fontSize: 12, color: "var(--ink-muted)", marginBottom: 2 }}>현재 MAE</div>
          <div
            style={{
              fontSize: 32,
              fontWeight: 500,
              letterSpacing: "-0.02em",
              color: "var(--ink-display)",
              lineHeight: 1,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {current.toFixed(2)}
          </div>
        </div>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            padding: "4px 12px",
            fontSize: 12,
            fontWeight: 600,
            color: directionColor,
            background: directionBg,
            borderRadius: 999,
          }}
        >
          {direction}
        </span>
      </div>
    </div>
  );
}

function ReviewerRevisionChart({
  data,
}: {
  data: DriftStats["reviewer_revisions"];
}) {
  const total = Math.max(1, data.strong + data.weak + data.none);
  // Mastercard semantic — 강수정=signal orange, 약수정=clay brown, 무수정=success
  const rows = [
    { label: "강수정 (Δ≥3)", value: data.strong, color: "var(--accent)", bg: "var(--accent-bg)" },
    { label: "약수정 (Δ=1~2)", value: data.weak, color: "var(--accent-soft)", bg: "var(--accent-bg)" },
    { label: "무수정 (Δ=0)", value: data.none, color: "var(--success)", bg: "var(--success-bg)" },
  ];

  return (
    <div className="card" style={{ padding: 28 }}>
      <ChartHeader
        eyebrow="REVISION RATE"
        title="검수자 수정률"
        sub="AI 점수 vs 검수 후 점수 차이 분포"
      />

      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {rows.map((r) => {
          const pct = (r.value / total) * 100;
          return (
            <div key={r.label} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  fontSize: 13,
                }}
              >
                <span style={{ color: "var(--ink)", fontWeight: 500 }}>{r.label}</span>
                <span
                  style={{
                    fontVariantNumeric: "tabular-nums",
                    color: "var(--ink-display)",
                    fontWeight: 600,
                    letterSpacing: "-0.01em",
                  }}
                >
                  {pct.toFixed(0)}%{" "}
                  <span style={{ color: "var(--ink-subtle)", fontWeight: 450, marginLeft: 4 }}>
                    ({r.value})
                  </span>
                </span>
              </div>
              <div
                style={{
                  height: 10,
                  background: "var(--surface-sunken)",
                  borderRadius: 999,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${pct}%`,
                    height: "100%",
                    background: r.color,
                    borderRadius: 999,
                    transition: "width 320ms cubic-bezier(0.2, 0, 0, 1)",
                  }}
                  title={`${r.label}: ${pct.toFixed(0)}%`}
                />
              </div>
            </div>
          );
        })}
      </div>

      <div
        style={{
          marginTop: 20,
          paddingTop: 16,
          borderTop: "1px solid var(--border-subtle)",
          fontSize: 13,
          color: "var(--ink-muted)",
        }}
      >
        총 검수{" "}
        <strong
          style={{
            color: "var(--ink-display)",
            fontWeight: 600,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {data.total}
        </strong>
        건
      </div>
    </div>
  );
}

function TierHistoryChart({ history }: { history: DriftStats["tier_history"] }) {
  // Tier semantic — T0 success, T1 info (link blue), T2 warn-soft, T3 signal accent
  const tierColors: Record<"T0" | "T1" | "T2" | "T3", string> = {
    T0: "var(--success)",
    T1: "var(--info)",
    T2: "var(--accent-soft)",
    T3: "var(--accent)",
  };
  const tierLabels: Record<"T0" | "T1" | "T2" | "T3", string> = {
    T0: "T0 Auto",
    T1: "T1 Light",
    T2: "T2 Uncertainty",
    T3: "T3 Mandatory",
  };

  return (
    <div className="card" style={{ padding: 28 }}>
      <ChartHeader
        eyebrow="TIER DISTRIBUTION"
        title="Tier 분포 추이"
        sub="일별 T0~T3 비중 — 자동확정 vs 사람검수 비율"
      />

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {history.map((row) => {
          const total = Math.max(1, row.T0 + row.T1 + row.T2 + row.T3);
          return (
            <div
              key={row.date}
              style={{ display: "flex", alignItems: "center", gap: 14 }}
            >
              <span
                style={{
                  fontSize: 12,
                  minWidth: 56,
                  color: "var(--ink-muted)",
                  fontVariantNumeric: "tabular-nums",
                  fontWeight: 500,
                }}
              >
                {row.date}
              </span>
              <div
                style={{
                  flex: 1,
                  display: "flex",
                  height: 22,
                  borderRadius: 999,
                  overflow: "hidden",
                  background: "var(--surface-sunken)",
                  gap: 2,
                  padding: 2,
                }}
              >
                {(["T0", "T1", "T2", "T3"] as const).map((tier) => {
                  const pct = (row[tier] / total) * 100;
                  if (pct <= 0) return null;
                  return (
                    <div
                      key={tier}
                      style={{
                        width: `${pct}%`,
                        background: tierColors[tier],
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        color: "white",
                        fontSize: 10,
                        fontWeight: 600,
                        letterSpacing: "0.01em",
                        borderRadius: 999,
                        transition: "width 320ms cubic-bezier(0.2, 0, 0, 1)",
                      }}
                      title={`${tier}: ${row[tier]}%`}
                    >
                      {pct >= 12 ? `${tier} ${row[tier]}%` : ""}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 16,
          marginTop: 20,
          paddingTop: 16,
          borderTop: "1px solid var(--border-subtle)",
          fontSize: 12,
          color: "var(--ink-muted)",
        }}
      >
        {(["T0", "T1", "T2", "T3"] as const).map((tier) => (
          <span key={tier} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span
              style={{
                display: "inline-block",
                width: 10,
                height: 10,
                background: tierColors[tier],
                borderRadius: 999,
              }}
            />
            <span style={{ color: "var(--ink)", fontWeight: 500 }}>{tierLabels[tier]}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

/* ─── 메인 패널 ─── */

export function DriftDashboardPanel() {
  const [period, setPeriod] = useState<PeriodKey>("7d");
  const [refreshTick, setRefreshTick] = useState(0);
  const [stats, setStats] = useState<DriftStats>(EMPTY_STATS);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const periodDef = PERIOD_OPTIONS.find((p) => p.value === period);
    const days = periodDef?.days ?? 7;
    let cancelled = false;

    setLoading(true);
    setError(null);
    fetchDriftStats(days)
      .then((data) => {
        if (cancelled) return;
        setStats(data);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setStats(EMPTY_STATS);
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [period, refreshTick]);

  const isEmpty = !!stats.empty || (stats.mae_trend.length === 0 && stats.reviewer_revisions.total === 0 && stats.tier_history.length === 0);

  return (
    <div className="flex flex-col gap-6" data-testid="drift-dashboard-panel">
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 16,
        }}
      >
        <div>
          <span className="section-eyebrow">STATISTICS</span>
          <h2
            style={{
              margin: "10px 0 4px",
              fontSize: 32,
              fontWeight: 500,
              letterSpacing: "-0.02em",
              color: "var(--ink-display)",
            }}
          >
            운영 통계
          </h2>
          <p style={{ margin: 0, fontSize: 14, color: "var(--ink-muted)", maxWidth: "62ch" }}>
            인간-AI 일치도 (MAE) · 검수자 수정률 · Tier 분포 — human_reviews 실시간 집계
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <select
            value={period}
            onChange={(e) => setPeriod(e.target.value as PeriodKey)}
            className="input-field input-sm"
            style={{ width: 130, height: 36, borderRadius: 999, paddingLeft: 16, paddingRight: 32 }}
            data-testid="drift-period-select"
            aria-label="기간 선택"
          >
            {PERIOD_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setRefreshTick((t) => t + 1)}
            disabled={loading}
            className="btn-secondary btn-sm"
            data-testid="drift-refresh"
            aria-label="새로고침"
            title="새로고침"
          >
            {loading ? "⟳ 로딩" : "⟳ 새로고침"}
          </button>
        </div>
      </header>

      {error ? (
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            padding: "10px 18px",
            border: "1px solid var(--danger-border)",
            borderRadius: 999,
            fontSize: 12,
            color: "var(--danger)",
            background: "var(--danger-bg)",
            alignSelf: "flex-start",
          }}
          role="alert"
        >
          ⚠ 통계 조회 실패: <code style={{ background: "transparent" }}>{error}</code>
        </div>
      ) : null}

      {isEmpty && !error ? (
        <div
          className="card"
          style={{
            padding: 40,
            textAlign: "center",
            color: "var(--ink-muted)",
          }}
        >
          <div style={{ fontSize: 48, marginBottom: 12, opacity: 0.4 }}>📊</div>
          <div style={{ fontSize: 16, fontWeight: 500, color: "var(--ink-soft)", marginBottom: 6 }}>
            아직 통계 데이터가 없습니다
          </div>
          <div style={{ fontSize: 13 }}>
            평가를 실행하고 검토 큐에서 사람 검수를 확정하면
            <br />
            인간-AI 일치도 (MAE) / 수정률 / Tier 분포가 자동 집계됩니다.
          </div>
        </div>
      ) : (
        <div style={{ display: "grid", gap: 20, gridTemplateColumns: "minmax(0, 1fr)" }}>
          {stats.summary && <SummaryKpiCard summary={stats.summary} totalWithGt={stats.total_with_gt ?? 0} />}
          {stats.gap_distribution && stats.gap_distribution.length > 0 && (
            <GapDistributionChart data={stats.gap_distribution} />
          )}
          <MaeTrendChart trend={stats.mae_trend} />
          {stats.by_model && stats.by_model.length > 0 && <ByModelTable rows={stats.by_model} />}
          {stats.by_item && stats.by_item.length > 0 && <ByItemTable rows={stats.by_item} />}
          {stats.worst_items && stats.worst_items.length > 0 && (
            <WorstItemsCard rows={stats.worst_items} />
          )}
          <ReviewerRevisionChart data={stats.reviewer_revisions} />
          <TierHistoryChart history={stats.tier_history} />
        </div>
      )}
    </div>
  );
}

// ============================================================================
// 신규 카드 — 직관 지표 위주 (LLM이 GT 보다 후함/엄격)
// ============================================================================

function SummaryKpiCard({ summary, totalWithGt }: { summary: NonNullable<DriftStats["summary"]>; totalWithGt: number }) {
  const tendencyColor =
    summary.tendency === "후함" ? "var(--accent)" :
    summary.tendency === "엄격" ? "var(--info)" :
    "var(--success)";
  return (
    <div className="card" style={{ padding: 28 }}>
      <ChartHeader
        eyebrow="LLM vs GT 요약"
        title={`전체 평가 ${totalWithGt}건 분석`}
        sub={`LLM 경향: 평균 GT 대비 ${summary.bias > 0 ? "+" : ""}${summary.bias}점 (${summary.tendency})`}
      />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 14 }}>
        <KpiTile label="후함 (AI > GT)" value={`${summary.over_rate}%`} color="var(--accent)" />
        <KpiTile label="엄격 (AI < GT)" value={`${summary.under_rate}%`} color="var(--info)" />
        <KpiTile label="일치 (AI = GT)" value={`${summary.match_rate}%`} color="var(--success)" />
        <KpiTile label="평균 점수차 (MAE)" value={summary.mae.toFixed(2)} color="var(--ink)" />
        <KpiTile label="±1점 이내 일치" value={`${summary.close_rate}%`} color="var(--ink-soft)" />
        <KpiTile label="LLM 경향" value={summary.tendency} color={tendencyColor} />
      </div>
    </div>
  );
}

function KpiTile({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div
      style={{
        padding: "14px 16px",
        background: "var(--surface-muted)",
        borderRadius: "var(--radius)",
        border: "1px solid var(--border)",
      }}
    >
      <div style={{ fontSize: 10.5, color: "var(--ink-muted)", marginBottom: 6, fontWeight: 600, letterSpacing: "0.04em" }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, color, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
    </div>
  );
}

function GapDistributionChart({ data }: { data: NonNullable<DriftStats["gap_distribution"]> }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  return (
    <div className="card" style={{ padding: 28 }}>
      <ChartHeader
        eyebrow="GAP DISTRIBUTION"
        title="점수 차이 분포"
        sub="AI 점수 - GT 점수. 음수 = AI 가 GT 보다 낮음 (엄격), 양수 = 후함."
      />
      <div style={{ display: "flex", alignItems: "flex-end", gap: 6, height: 160, padding: "16px 4px 4px" }}>
        {data.map((d) => {
          const h = Math.max(2, (d.count / max) * 100);
          const isExact = d.delta === 0;
          const color = isExact ? "var(--success)" : d.delta > 0 ? "var(--accent)" : "var(--info)";
          return (
            <div key={d.delta} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4, height: "100%" }}>
              <div style={{ fontSize: 10, color: "var(--ink-soft)", fontWeight: 600 }}>{d.count}</div>
              <div style={{ flex: 1, width: "100%", display: "flex", alignItems: "flex-end" }}>
                <div title={d.label} style={{ width: "100%", height: `${h}%`, background: color, borderRadius: 4, opacity: isExact ? 1 : 0.85 }} />
              </div>
              <div style={{ fontSize: 10, color: "var(--ink-muted)", fontWeight: 500 }}>
                {d.delta > 0 ? `+${d.delta}` : d.delta}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ByModelTable({ rows }: { rows: NonNullable<DriftStats["by_model"]> }) {
  return (
    <div className="card" style={{ padding: 28 }}>
      <ChartHeader
        eyebrow="MODEL COMPARISON"
        title="모델별 정확도 비교"
        sub="MAE 가 낮을수록 GT 와 가까움. 후함/엄격으로 모델 경향 확인."
      />
      <div style={{ overflow: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ background: "var(--surface-muted)", textAlign: "left" }}>
              <th style={th}>모델</th>
              <th style={thNum}>건수</th>
              <th style={thNum}>MAE</th>
              <th style={thNum}>Bias</th>
              <th style={thNum}>일치율</th>
              <th style={thNum}>후함%</th>
              <th style={thNum}>엄격%</th>
              <th style={th}>경향</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.model_id} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={td}><code style={{ fontSize: 11 }}>{r.model_id}</code></td>
                <td style={tdNum}>{r.n}</td>
                <td style={tdNum}><strong>{r.mae.toFixed(2)}</strong></td>
                <td style={{ ...tdNum, color: r.bias > 0 ? "var(--accent)" : r.bias < 0 ? "var(--info)" : "var(--ink)" }}>
                  {r.bias > 0 ? "+" : ""}{r.bias.toFixed(2)}
                </td>
                <td style={tdNum}>{r.accuracy.toFixed(1)}%</td>
                <td style={{ ...tdNum, color: "var(--accent)" }}>{r.over_rate.toFixed(1)}%</td>
                <td style={{ ...tdNum, color: "var(--info)" }}>{r.under_rate.toFixed(1)}%</td>
                <td style={td}>{r.tendency}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ByItemTable({ rows }: { rows: NonNullable<DriftStats["by_item"]> }) {
  return (
    <div className="card" style={{ padding: 28 }}>
      <ChartHeader
        eyebrow="BY ITEM"
        title="평가 항목별 정확도"
        sub="#1~#18 항목별로 LLM 이 GT 와 얼마나 일치하는지 + 어느 항목에서 후함/엄격."
      />
      <div style={{ overflow: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ background: "var(--surface-muted)", textAlign: "left" }}>
              <th style={thNum}>#</th>
              <th style={thNum}>건수</th>
              <th style={thNum}>MAE</th>
              <th style={thNum}>Bias</th>
              <th style={thNum}>일치율</th>
              <th style={thNum}>후함%</th>
              <th style={thNum}>엄격%</th>
              <th style={th}>경향</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.item_number} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={tdNum}>#{r.item_number}</td>
                <td style={tdNum}>{r.n}</td>
                <td style={tdNum}><strong>{r.mae.toFixed(2)}</strong></td>
                <td style={{ ...tdNum, color: r.bias > 0 ? "var(--accent)" : r.bias < 0 ? "var(--info)" : "var(--ink)" }}>
                  {r.bias > 0 ? "+" : ""}{r.bias.toFixed(2)}
                </td>
                <td style={tdNum}>{r.accuracy.toFixed(1)}%</td>
                <td style={{ ...tdNum, color: "var(--accent)" }}>{r.over_rate.toFixed(1)}%</td>
                <td style={{ ...tdNum, color: "var(--info)" }}>{r.under_rate.toFixed(1)}%</td>
                <td style={td}>{r.tendency}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function WorstItemsCard({ rows }: { rows: NonNullable<DriftStats["worst_items"]> }) {
  return (
    <div className="card" style={{ padding: 28 }}>
      <ChartHeader
        eyebrow="WORST OFFENDERS"
        title="가장 회귀가 큰 항목 Top 5"
        sub="MAE 가 큰 항목 — 우선 개선 대상."
      />
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {rows.map((r, i) => (
          <div
            key={r.item_number}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 14,
              padding: "10px 14px",
              background: "var(--danger-bg)",
              borderRadius: "var(--radius)",
              border: "1px solid var(--danger-border)",
            }}
          >
            <span style={{ fontSize: 14, fontWeight: 800, color: "var(--danger)", minWidth: 24 }}>
              {i + 1}.
            </span>
            <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>#{r.item_number}</span>
            <span style={{ fontSize: 11, color: "var(--ink-soft)" }}>
              MAE <strong style={{ color: "var(--danger)" }}>{r.mae.toFixed(2)}</strong> · {r.n}건
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

const th: React.CSSProperties = { padding: "10px 12px", fontSize: 11, fontWeight: 700, color: "var(--ink-muted)", letterSpacing: "0.04em" };
const thNum: React.CSSProperties = { ...th, textAlign: "right" };
const td: React.CSSProperties = { padding: "10px 12px", fontSize: 12, color: "var(--ink)" };
const tdNum: React.CSSProperties = { ...td, textAlign: "right", fontVariantNumeric: "tabular-nums" };

export default DriftDashboardPanel;
