// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import {
  getResultComparison,
  type ComparisonByCategory,
  type ComparisonItem,
  type ComparisonResponse,
} from "@/lib/api";

/* ─────────────────────────────────────────────────────────────
   HumanAiComparison — Task #7 (Dev4)
   /v2/result/comparison/{cid} 응답을 4개 영역으로 시각화:
   A. 요약 카드 4개 (정확도 / MAE / Bias / 비교 항목 수)
   B. 카테고리별 비교 테이블 (by_category)
   C. 항목별 비교 테이블 (items) — 행 클릭 → 본문 모달
   D. CSV 다운로드 버튼

   허용 지표: MAE / RMSE / Bias / MAPE / Accuracy 만 사용.
   Pearson / Spearman / R² 등 상관계수 지표는 정책상 사용 금지.
   ───────────────────────────────────────────────────────────── */

interface HumanAiComparisonProps {
  consultationId: string;
}

function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toFixed(digits);
}

function fmtPercent(rate: number | null | undefined, digits = 1): string {
  if (rate === null || rate === undefined || Number.isNaN(rate)) return "—";
  // exact_match_rate 가 0~1 범위인지 0~100 범위인지 자동 감지
  const v = Math.abs(Number(rate)) <= 1 ? Number(rate) * 100 : Number(rate);
  return `${v.toFixed(digits)}%`;
}

function fmtDelta(delta: number | null | undefined, digits = 0): string {
  if (delta === null || delta === undefined || Number.isNaN(delta)) return "—";
  const v = Number(delta);
  if (v > 0) return `+${digits === 0 ? v : v.toFixed(digits)}`;
  return digits === 0 ? String(v) : v.toFixed(digits);
}

function biasColor(bias: number | null | undefined): string {
  if (bias === null || bias === undefined || Number.isNaN(bias))
    return "var(--ink-muted)";
  if (bias > 0) return "var(--danger)"; // AI 후함 — 위험 신호
  if (bias < 0) return "var(--accent)"; // AI 엄격
  return "var(--success)";
}

const AGREEMENT_STYLE: Record<
  string,
  { bg: string; color: string; icon: string; label: string }
> = {
  exact: { bg: "#dcfce7", color: "#166534", icon: "✓", label: "일치" },
  close: { bg: "#fef3c7", color: "#92400e", icon: "≈", label: "근접" },
  diverge: { bg: "#fee2e2", color: "#b91c1c", icon: "✗", label: "불일치" },
};

interface CardProps {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  emphasis?: "default" | "warn" | "danger" | "success" | "accent";
}

function ComparisonCard({ label, value, sub, emphasis = "default" }: CardProps) {
  const valueColor =
    emphasis === "warn"
      ? "var(--warn)"
      : emphasis === "danger"
        ? "var(--danger)"
        : emphasis === "success"
          ? "var(--success)"
          : emphasis === "accent"
            ? "var(--accent)"
            : "var(--ink)";
  return (
    <div className="flex-1 min-w-[150px] rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface)] px-3 py-3">
      <div className="text-[10.5px] font-semibold uppercase tracking-wide text-[var(--ink-muted)] mb-1.5">
        {label}
      </div>
      <div
        className="text-[20px] font-bold font-mono"
        style={{ color: valueColor }}
      >
        {value}
      </div>
      {sub && <div className="mt-1 text-[11px] text-[var(--ink-muted)]">{sub}</div>}
    </div>
  );
}

function buildCsv(rows: ComparisonItem[]): string {
  const header = [
    "item_number",
    "item_name",
    "category",
    "ai_score",
    "human_score",
    "delta",
    "agreement",
    "ai_judgment",
    "human_note",
  ];
  const escape = (v: unknown): string => {
    if (v === null || v === undefined) return "";
    let s = typeof v === "string" ? v : Array.isArray(v) ? v.join(" | ") : String(v);
    s = s.replace(/"/g, '""');
    if (/[",\n\r]/.test(s)) s = `"${s}"`;
    return s;
  };
  const lines = [header.join(",")];
  for (const r of rows) {
    lines.push(
      [
        r.item_number,
        r.item_name ?? "",
        r.category ?? "",
        r.ai_score ?? "",
        r.human_score ?? "",
        r.delta ?? "",
        r.agreement ?? "",
        r.ai_judgment ?? "",
        r.human_note ?? "",
      ]
        .map(escape)
        .join(","),
    );
  }
  return lines.join("\r\n");
}

function downloadCsv(filename: string, content: string): void {
  const blob = new Blob([`﻿${content}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function HumanAiComparison({ consultationId }: HumanAiComparisonProps) {
  const [state, setState] = useState<{
    loading: boolean;
    data: ComparisonResponse | null;
    error: string | null;
  }>({ loading: true, data: null, error: null });

  const [openItem, setOpenItem] = useState<ComparisonItem | null>(null);

  const refresh = useCallback(async () => {
    if (!consultationId) return;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const r = await getResultComparison(consultationId);
      setState({ loading: false, data: r, error: null });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setState({ loading: false, data: null, error: msg });
    }
  }, [consultationId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
  }, [refresh]);

  const data = state.data;
  const summary = data?.summary || {};
  const byCategory = data?.by_category || [];
  const items = useMemo(() => data?.items || [], [data]);

  const onDownloadCsv = useCallback(() => {
    if (items.length === 0) return;
    const csv = buildCsv(items);
    downloadCsv(`comparison_${consultationId}.csv`, csv);
  }, [items, consultationId]);

  if (state.loading) {
    return (
      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">📊 사람-AI 비교</div>
        </div>
        <div className="panel-section">
          <div className="empty-state">
            <div className="spinner" aria-hidden="true" />
            <div className="empty-state-title">비교 데이터 불러오는 중…</div>
          </div>
        </div>
      </div>
    );
  }

  if (state.error) {
    return (
      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">📊 사람-AI 비교</div>
          <button className="btn-secondary btn-sm" onClick={refresh}>
            ↻ 재시도
          </button>
        </div>
        <div className="panel-section">
          <div className="rounded-[var(--radius-sm)] border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-[12px] text-[var(--danger)]">
            ⚠ 비교 데이터 조회 실패: {state.error}
          </div>
        </div>
      </div>
    );
  }

  // available=false → 안내 카드만
  if (!data || !data.available) {
    return (
      <div className="panel">
        <div className="panel-header">
          <div>
            <div className="panel-title">📊 사람-AI 비교</div>
            <div className="text-[12px] text-[var(--ink-muted)] mt-0.5">
              사람 검수 확정 시점부터 자동 비교가 활성화됩니다
            </div>
          </div>
        </div>
        <div className="panel-section">
          <div className="empty-state">
            <div className="empty-state-title">사람 검수 확정된 항목이 없어 비교 불가</div>
            <div className="empty-state-desc">
              {data?.reason ||
                "이 상담에 대해 아직 사람 검수가 확정된 항목이 없습니다. 검수 큐에서 항목을 검토·확정한 뒤 다시 확인하세요."}
            </div>
            <div className="mt-3 flex gap-2">
              <Link className="btn-primary btn-sm" href="/review">
                → HITL 검수 큐로 이동
              </Link>
              <button className="btn-ghost btn-sm" onClick={refresh}>
                ↻ 새로고침
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const exactRate = summary.exact_match_rate;
  const mae = summary.mae;
  const bias = summary.bias;
  const mape = summary.mape;
  const rmse = summary.rmse;
  const compared = summary.compared_count;
  const total = summary.total_items;

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <div className="panel-title">📊 사람-AI 비교</div>
          <div className="text-[12px] text-[var(--ink-muted)] mt-0.5">
            확정된 사람 검수 점수와 AI 점수를 항목 단위로 비교합니다
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn-secondary btn-sm"
            onClick={onDownloadCsv}
            disabled={items.length === 0}
            title="비교 결과 CSV 다운로드"
          >
            ⬇ CSV
          </button>
          <button className="btn-secondary btn-sm" onClick={refresh}>
            ↻ 새로고침
          </button>
        </div>
      </div>

      {/* A. 상단 요약 카드 4개 */}
      <div className="panel-section">
        <div className="flex flex-wrap gap-2.5">
          <ComparisonCard
            label="정확도 (Accuracy)"
            value={fmtPercent(exactRate)}
            sub={
              summary.agreement_label ? (
                <span className="badge badge-outline">{summary.agreement_label}</span>
              ) : null
            }
            emphasis="success"
          />
          <ComparisonCard
            label="MAE"
            value={fmtNum(mae, 2)}
            sub={<span>점 (평균 절대 오차)</span>}
            emphasis="warn"
          />
          <ComparisonCard
            label="Bias"
            value={
              <span style={{ color: biasColor(bias) }}>
                {bias === null || bias === undefined
                  ? "—"
                  : (bias > 0 ? "+" : "") + Number(bias).toFixed(2)}
              </span>
            }
            sub={
              bias === null || bias === undefined ? null : bias > 0 ? (
                <span className="text-[var(--danger)]">AI 후함 (사람보다 높게 매김)</span>
              ) : bias < 0 ? (
                <span className="text-[var(--accent)]">AI 엄격 (사람보다 낮게 매김)</span>
              ) : (
                <span className="text-[var(--success)]">균형</span>
              )
            }
          />
          <ComparisonCard
            label="비교 항목 수"
            value={
              <>
                {compared ?? "—"}
                <span className="text-[var(--ink-muted)] font-normal">
                  {" / "}
                  {total ?? "—"}
                </span>
              </>
            }
            sub={
              <span>
                {mape !== undefined && mape !== null && (
                  <>MAPE {fmtPercent(mape)} </>
                )}
                {rmse !== undefined && rmse !== null && (
                  <>· RMSE {fmtNum(rmse, 2)}</>
                )}
              </span>
            }
            emphasis="accent"
          />
        </div>
      </div>

      {/* B. 카테고리별 비교 */}
      {byCategory.length > 0 && (
        <div className="panel-section">
          <div className="text-[12.5px] font-semibold mb-2">카테고리별 비교</div>
          <div className="overflow-auto rounded-[var(--radius-sm)] border border-[var(--border)]">
            <table className="w-full text-[12px] border-collapse">
              <thead>
                <tr className="bg-[var(--surface-sunken)] text-[var(--ink-muted)]">
                  <th className="px-2 py-1.5 text-left border-b border-[var(--border)]">
                    카테고리
                  </th>
                  <th className="px-2 py-1.5 text-right border-b border-[var(--border)]">
                    비교 N
                  </th>
                  <th className="px-2 py-1.5 text-right border-b border-[var(--border)]">
                    MAE
                  </th>
                  <th className="px-2 py-1.5 text-right border-b border-[var(--border)]">
                    Bias
                  </th>
                  <th className="px-2 py-1.5 text-right border-b border-[var(--border)]">
                    일치율
                  </th>
                </tr>
              </thead>
              <tbody>
                {byCategory.map((c: ComparisonByCategory, idx) => (
                  <tr
                    key={`${c.category}-${idx}`}
                    className="border-b border-[var(--border-subtle)]"
                  >
                    <td className="px-2 py-1.5 font-medium">{c.category}</td>
                    <td className="px-2 py-1.5 text-right font-mono">
                      {c.compared ?? "—"}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono">
                      {fmtNum(c.mae, 2)}
                    </td>
                    <td
                      className="px-2 py-1.5 text-right font-mono font-bold"
                      style={{ color: biasColor(c.bias) }}
                    >
                      {c.bias === null || c.bias === undefined
                        ? "—"
                        : (c.bias > 0 ? "+" : "") + Number(c.bias).toFixed(2)}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono">
                      {fmtPercent(c.exact_match_rate)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* C. 항목별 비교 */}
      <div className="panel-section">
        <div className="text-[12.5px] font-semibold mb-2">
          항목별 비교 — {items.length}건
        </div>
        {items.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-desc">표시할 비교 항목이 없습니다.</div>
          </div>
        ) : (
          <div className="overflow-auto rounded-[var(--radius-sm)] border border-[var(--border)]">
            <table className="w-full text-[12px] border-collapse">
              <thead>
                <tr className="bg-[var(--surface-sunken)] text-[var(--ink-muted)]">
                  <th className="px-2 py-1.5 text-left border-b border-[var(--border)]">
                    #
                  </th>
                  <th className="px-2 py-1.5 text-left border-b border-[var(--border)]">
                    항목명
                  </th>
                  <th className="px-2 py-1.5 text-right border-b border-[var(--border)]">
                    AI
                  </th>
                  <th className="px-2 py-1.5 text-right border-b border-[var(--border)]">
                    사람
                  </th>
                  <th className="px-2 py-1.5 text-right border-b border-[var(--border)]">
                    Δ
                  </th>
                  <th className="px-2 py-1.5 text-center border-b border-[var(--border)]">
                    일치
                  </th>
                  <th className="px-2 py-1.5 text-center border-b border-[var(--border)]">
                    액션
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((it: ComparisonItem) => {
                  const ai =
                    typeof it.ai_score === "number" ? it.ai_score : null;
                  const hu =
                    typeof it.human_score === "number" ? it.human_score : null;
                  const delta =
                    typeof it.delta === "number"
                      ? it.delta
                      : ai !== null && hu !== null
                        ? hu - ai
                        : null;
                  const agreement = it.agreement || "";
                  const aStyle =
                    AGREEMENT_STYLE[agreement] || {
                      bg: "#e5e7eb",
                      color: "#4b5563",
                      icon: "·",
                      label: agreement || "—",
                    };
                  const deltaCls =
                    delta === null
                      ? "text-[var(--ink-muted)]"
                      : delta > 0
                        ? "text-[var(--success)]"
                        : delta < 0
                          ? "text-[var(--danger)]"
                          : "text-[var(--ink)]";
                  return (
                    <tr
                      key={it.item_number}
                      className="cursor-pointer border-b border-[var(--border-subtle)] hover:bg-[var(--surface-muted)] transition-colors"
                      onClick={() => setOpenItem(it)}
                      title="클릭하여 AI 판정 / 사람 비고 비교 보기"
                    >
                      <td className="px-2 py-1.5 font-mono font-semibold">
                        #{it.item_number}
                      </td>
                      <td className="px-2 py-1.5">
                        {it.item_name ?? "—"}
                        {it.category && (
                          <span className="ml-1.5 text-[10.5px] text-[var(--ink-muted)]">
                            ({it.category})
                          </span>
                        )}
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono">
                        {ai !== null ? ai : "—"}
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono">
                        {hu !== null ? hu : "—"}
                      </td>
                      <td
                        className={`px-2 py-1.5 text-right font-mono font-bold ${deltaCls}`}
                      >
                        {fmtDelta(delta)}
                      </td>
                      <td className="px-2 py-1.5 text-center">
                        <span
                          className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px] font-bold"
                          style={{ background: aStyle.bg, color: aStyle.color }}
                          title={agreement}
                        >
                          <span>{aStyle.icon}</span>
                          <span>{aStyle.label}</span>
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-center">
                        <button
                          type="button"
                          className="btn-ghost btn-sm text-[10.5px]"
                          onClick={(e) => {
                            e.stopPropagation();
                            setOpenItem(it);
                          }}
                        >
                          상세
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 항목 상세 모달 — ai_judgment vs human_note 좌우 비교 */}
      {openItem && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center bg-black/45 px-4 py-6"
          onClick={() => setOpenItem(null)}
        >
          <div
            className="flex max-h-[90vh] w-full max-w-[960px] flex-col overflow-hidden rounded-[var(--radius)] bg-[var(--surface)] shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] px-4 py-3">
              <div>
                <div className="text-[14px] font-bold text-[var(--ink)]">
                  #{openItem.item_number} {openItem.item_name ?? ""}
                </div>
                <div className="mt-0.5 text-[11px] text-[var(--ink-muted)]">
                  {openItem.category && <span>카테고리: {openItem.category} · </span>}
                  AI <b>{openItem.ai_score ?? "—"}</b> / 사람{" "}
                  <b>{openItem.human_score ?? "—"}</b> · Δ{" "}
                  <b>{fmtDelta(openItem.delta)}</b>
                </div>
              </div>
              <button
                className="btn-ghost btn-sm"
                onClick={() => setOpenItem(null)}
                title="닫기"
              >
                ✕
              </button>
            </div>
            <div className="grid flex-1 grid-cols-1 gap-0 overflow-auto md:grid-cols-2">
              <div className="border-b border-[var(--border)] p-4 md:border-b-0 md:border-r">
                <div className="mb-2 flex items-center gap-2">
                  <span className="badge badge-outline">🤖 AI 판정</span>
                  {openItem.ai_score !== null && openItem.ai_score !== undefined && (
                    <span className="text-[11px] font-mono text-[var(--ink-muted)]">
                      {openItem.ai_score}점
                    </span>
                  )}
                </div>
                {openItem.ai_judgment ? (
                  <pre className="whitespace-pre-wrap break-words font-sans text-[12px] leading-relaxed text-[var(--ink)]">
                    {openItem.ai_judgment}
                  </pre>
                ) : (
                  <div className="text-[12px] text-[var(--ink-muted)]">(판정 본문 없음)</div>
                )}
                {openItem.ai_evidence && (
                  <div className="mt-3">
                    <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-wide text-[var(--ink-muted)]">
                      근거
                    </div>
                    <ul className="list-disc pl-5 text-[11.5px] leading-relaxed text-[var(--ink-soft)]">
                      {(Array.isArray(openItem.ai_evidence)
                        ? openItem.ai_evidence
                        : [openItem.ai_evidence]
                      )
                        .filter(Boolean)
                        .map((ev, i) => (
                          <li key={i}>{ev}</li>
                        ))}
                    </ul>
                  </div>
                )}
              </div>
              <div className="p-4">
                <div className="mb-2 flex items-center gap-2">
                  <span className="badge badge-outline">👤 사람 비고</span>
                  {openItem.human_score !== null &&
                    openItem.human_score !== undefined && (
                      <span className="text-[11px] font-mono text-[var(--ink-muted)]">
                        {openItem.human_score}점
                      </span>
                    )}
                </div>
                {openItem.human_note ? (
                  <pre className="whitespace-pre-wrap break-words font-sans text-[12px] leading-relaxed text-[var(--ink)]">
                    {openItem.human_note}
                  </pre>
                ) : (
                  <div className="text-[12px] text-[var(--ink-muted)]">
                    (사람 비고 없음 — 점수만 입력된 검수)
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default HumanAiComparison;
