// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useMemo, useState } from "react";

import { useAppState } from "@/lib/AppStateContext";
import {
  computeManualComparison,
  type ComparisonRow,
  type ComparisonStatus,
} from "@/lib/manualEvalMapper";
import {
  downloadXlsxWithAiAppended,
  downloadManualComparisonXlsx,
} from "@/lib/manualEvalExport";

interface ManualEvalCompareTableProps {
  /** 모델명 (UI 표시 + xlsx 헤더) */
  modelName?: string;
  /** 다운로드 버튼 표시 여부 */
  showDownload?: boolean;
}

const STATUS_META: Record<
  ComparisonStatus,
  { label: string; bg: string; fg: string; tooltip: string }
> = {
  match: {
    label: "일치",
    bg: "bg-emerald-50 dark:bg-emerald-900/20",
    fg: "text-emerald-700 dark:text-emerald-300",
    tooltip: "AI 와 사람 QA 점수가 동일",
  },
  high: {
    label: "AI 후함",
    bg: "bg-orange-50 dark:bg-orange-900/20",
    fg: "text-orange-700 dark:text-orange-300",
    tooltip: "AI 점수가 사람 QA 보다 높음",
  },
  low: {
    label: "AI 박함",
    bg: "bg-blue-50 dark:bg-blue-900/20",
    fg: "text-blue-700 dark:text-blue-300",
    tooltip: "AI 점수가 사람 QA 보다 낮음",
  },
  missing: {
    label: "미비교",
    bg: "bg-zinc-100 dark:bg-zinc-800",
    fg: "text-zinc-500 dark:text-zinc-400",
    tooltip: "AI 또는 사람 QA 점수 누락",
  },
};

function diffCellStyle(row: ComparisonRow): string {
  if (row.status === "match") return "text-emerald-700 dark:text-emerald-300 font-semibold";
  if (row.status === "high") return "text-orange-700 dark:text-orange-300 font-semibold";
  if (row.status === "low") return "text-blue-700 dark:text-blue-300 font-semibold";
  return "text-zinc-400";
}

/**
 * AppStateContext 의 `manualEval` + `lastResult` 를 사용해 수동-AI 비교 패널을 렌더.
 * - 둘 중 하나라도 없으면 안내 메시지.
 * - exceljs 로 AI 컬럼 append 한 xlsx 다운로드 지원.
 */
export function ManualEvalCompareTable({
  modelName = "AI",
  showDownload = true,
}: ManualEvalCompareTableProps) {
  const { state } = useAppState();
  const manual = state.manualEval;
  const aiResult = state.lastResult;
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string>("");

  const comparison = useMemo(
    () => computeManualComparison(modelName, aiResult, manual),
    [modelName, aiResult, manual],
  );

  const onDownload = async () => {
    if (!manual || !aiResult) return;
    setDownloading(true);
    setDownloadError("");
    try {
      await downloadXlsxWithAiAppended({
        originalBuffer: state.manualEvalBuffer,
        manualSheet: manual,
        aiMods: [{ name: modelName, result: aiResult }],
        fileNameBase: state.manualEvalFileName
          ? state.manualEvalFileName.replace(/\.xlsx?$/i, "")
          : undefined,
      });
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : String(err));
    } finally {
      setDownloading(false);
    }
  };

  /** 독립 비교 xlsx 다운로드 (V2 downloadManualXlsx). 원본과 별개로 18행+판정 테이블만 */
  const onDownloadComparison = async () => {
    if (!comparison) return;
    setDownloading(true);
    setDownloadError("");
    try {
      await downloadManualComparisonXlsx([comparison], {
        fileNameBase: `qa_comparison_${manual?.sheetId || "unknown"}`,
      });
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : String(err));
    } finally {
      setDownloading(false);
    }
  };

  if (!manual) {
    return (
      <div className="rounded-[var(--radius-lg)] border border-dashed border-[var(--border-strong)] bg-[var(--surface-muted)] p-5 text-[13px] text-[var(--ink-muted)]">
        사람 QA 평가표가 첨부되지 않았습니다. 상단의{" "}
        <strong className="text-[var(--accent)]">사람 QA 평가표 첨부</strong> 버튼으로 xlsx 를
        올려주세요.
      </div>
    );
  }

  if (!aiResult) {
    return (
      <div className="rounded-[var(--radius-lg)] border border-dashed border-[var(--border-strong)] bg-[var(--surface-muted)] p-5 text-[13px] text-[var(--ink-muted)]">
        <div className="mb-1 font-semibold text-[var(--ink-soft)]">
          수동 평가표 (상담 ID: {manual.sheetId || "-"}, 총점 {manual.total ?? "—"}/100)
        </div>
        AI 평가를 먼저 실행하면 항목별 비교 결과가 표시됩니다.
      </div>
    );
  }

  if (!comparison) return null;

  const { summary, rows } = comparison;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <div className="text-[15px] font-semibold text-emerald-700 dark:text-emerald-400">
            수동 평가표 비교
          </div>
          <div className="text-[11.5px] text-[var(--ink-muted)]">
            상담 ID {manual.sheetId || "-"} · 시트 <code>{manual.sheetName}</code>
          </div>
        </div>
        {showDownload && (
          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              onClick={onDownloadComparison}
              disabled={downloading}
              className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-zinc-300 bg-white px-3 py-1.5 text-[12px] font-semibold text-zinc-700 transition hover:bg-zinc-50 disabled:opacity-50 dark:bg-transparent dark:text-zinc-300 dark:border-zinc-700 dark:hover:bg-zinc-800"
              title="비교 표만 독립 xlsx 로 다운로드 (원본과 별개)"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
              </svg>
              비교 xlsx
            </button>
            <button
              type="button"
              onClick={onDownload}
              disabled={downloading}
              className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-emerald-600 bg-white px-3 py-1.5 text-[12px] font-semibold text-emerald-700 transition hover:bg-emerald-50 disabled:opacity-50 dark:bg-transparent dark:text-emerald-400 dark:hover:bg-emerald-900/20"
              title="원본 xlsx 에 AI 모델 컬럼을 추가해 다운로드"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="7 10 12 15 17 10"/>
                <line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
              {downloading ? "생성 중..." : "원본+AI 컬럼"}
            </button>
          </div>
        )}
      </div>

      {downloadError && (
        <div className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded-[var(--radius-sm)] px-2.5 py-1.5 dark:bg-red-900/20 dark:text-red-300 dark:border-red-800">
          다운로드 실패: {downloadError}
        </div>
      )}

      {/* 요약 카드 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <SummaryCard
          title="사람 QA 총점"
          value={summary.manual_total != null ? `${summary.manual_total}` : "—"}
          suffix="/100"
          tone="emerald"
        />
        <SummaryCard
          title={`${modelName} 총점`}
          value={`${summary.ai_total}`}
          suffix="/100"
          tone="violet"
        />
        <SummaryCard
          title="일치 / 불일치"
          value={`${summary.match_count} / ${summary.mismatch_count}`}
          suffix={`(AI 후함 ${summary.high_count} · 박함 ${summary.low_count})`}
          tone="zinc"
        />
        <SummaryCard
          title="MAE · Bias"
          value={`${summary.mae.toFixed(2)} · ${summary.bias >= 0 ? "+" : ""}${summary.bias.toFixed(2)}`}
          suffix={summary.bias > 0 ? "AI 후함" : summary.bias < 0 ? "AI 박함" : ""}
          tone="zinc"
        />
      </div>

      {/* 18행 비교 표 */}
      <div className="overflow-auto rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] max-h-[480px]">
        <table className="w-full text-[12px] font-[var(--font)]">
          <thead className="sticky top-0 z-10 bg-emerald-50/80 backdrop-blur dark:bg-emerald-900/30">
            <tr className="text-left">
              <th className="px-2 py-2 border-b-2 border-emerald-600 whitespace-nowrap">#</th>
              <th className="px-2 py-2 border-b-2 border-emerald-600 whitespace-nowrap">대분류</th>
              <th className="px-2 py-2 border-b-2 border-emerald-600 whitespace-nowrap">항목</th>
              <th className="px-2 py-2 border-b-2 border-emerald-600 text-center whitespace-nowrap">배점</th>
              <th className="px-2 py-2 border-b-2 border-emerald-600 text-center bg-emerald-100 dark:bg-emerald-800/40 whitespace-nowrap">QA</th>
              <th className="px-2 py-2 border-b-2 border-emerald-600 text-center bg-violet-100 dark:bg-violet-800/40 whitespace-nowrap">{modelName}</th>
              <th className="px-2 py-2 border-b-2 border-emerald-600 text-center whitespace-nowrap">Δ</th>
              <th className="px-2 py-2 border-b-2 border-emerald-600 text-center whitespace-nowrap">판정</th>
              <th className="px-2 py-2 border-b-2 border-emerald-600 bg-emerald-100 dark:bg-emerald-800/40">QA 근거</th>
              <th className="px-2 py-2 border-b-2 border-emerald-600 bg-violet-100 dark:bg-violet-800/40">{modelName} 근거</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, idx) => {
              const meta = STATUS_META[r.status];
              const catChange = idx === 0 || rows[idx - 1].category !== r.category;
              return (
                <tr
                  key={r.no}
                  className={catChange ? "border-t-2 border-[var(--border)]" : "border-t border-[var(--border-soft,rgba(0,0,0,0.06))]"}
                >
                  <td className="px-2 py-1.5 text-center text-[var(--ink-muted)]">{r.no}</td>
                  <td className="px-2 py-1.5 whitespace-nowrap text-[var(--ink-soft)]">
                    {catChange ? r.category : ""}
                  </td>
                  <td className="px-2 py-1.5 whitespace-nowrap font-medium text-[var(--ink)]">{r.item}</td>
                  <td className="px-2 py-1.5 text-center text-[var(--ink-muted)]">{r.max_score}</td>
                  <td className="px-2 py-1.5 text-center bg-emerald-50/50 dark:bg-emerald-900/20 font-semibold">
                    {r.manual_score ?? "—"}
                  </td>
                  <td className="px-2 py-1.5 text-center bg-violet-50/50 dark:bg-violet-900/20 font-semibold">
                    {r.ai_score ?? "—"}
                  </td>
                  <td className={`px-2 py-1.5 text-center ${diffCellStyle(r)}`}>
                    {r.diff == null ? "—" : r.diff > 0 ? `+${r.diff}` : `${r.diff}`}
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    <span
                      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10.5px] font-semibold whitespace-nowrap ${meta.bg} ${meta.fg}`}
                      title={meta.tooltip}
                    >
                      {meta.label}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 bg-emerald-50/30 dark:bg-emerald-900/10 max-w-[240px] text-[var(--ink-soft)] whitespace-pre-wrap">
                    {r.manual_evidence || <span className="text-[var(--ink-subtle)]">—</span>}
                  </td>
                  <td className="px-2 py-1.5 bg-violet-50/30 dark:bg-violet-900/10 max-w-[320px] text-[var(--ink-soft)] whitespace-pre-wrap">
                    {r.ai_evidence || <span className="text-[var(--ink-subtle)]">—</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot className="sticky bottom-0 bg-[var(--surface-muted)] z-10">
            <tr className="border-t-2 border-[var(--ink)]">
              <td colSpan={3} className="px-2 py-2 text-right font-bold text-[var(--ink)]">
                총점
              </td>
              <td className="px-2 py-2 text-center font-bold text-[var(--ink)]">100</td>
              <td className="px-2 py-2 text-center bg-emerald-100 dark:bg-emerald-800/40 font-bold text-emerald-800 dark:text-emerald-200">
                {summary.manual_total ?? "—"}
              </td>
              <td className="px-2 py-2 text-center bg-violet-100 dark:bg-violet-800/40 font-bold text-violet-800 dark:text-violet-200">
                {summary.ai_total}
              </td>
              <td
                colSpan={4}
                className={`px-2 py-2 text-right font-mono text-[11.5px] ${
                  summary.manual_total != null && summary.ai_total - summary.manual_total > 0
                    ? "text-orange-700 dark:text-orange-300"
                    : summary.manual_total != null && summary.ai_total - summary.manual_total < 0
                      ? "text-blue-700 dark:text-blue-300"
                      : "text-[var(--ink-muted)]"
                }`}
              >
                {summary.manual_total != null
                  ? `총점 Δ = ${summary.ai_total - summary.manual_total > 0 ? "+" : ""}${
                      summary.ai_total - summary.manual_total
                    } · MAE ${summary.mae.toFixed(2)} · Bias ${summary.bias >= 0 ? "+" : ""}${summary.bias.toFixed(2)}`
                  : "—"}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

function SummaryCard({
  title,
  value,
  suffix,
  tone,
}: {
  title: string;
  value: string;
  suffix?: string;
  tone: "emerald" | "violet" | "zinc";
}) {
  const toneClass =
    tone === "emerald"
      ? "border-emerald-300 dark:border-emerald-800"
      : tone === "violet"
        ? "border-violet-300 dark:border-violet-800"
        : "border-[var(--border-strong)]";
  const titleTone =
    tone === "emerald"
      ? "text-emerald-700 dark:text-emerald-400"
      : tone === "violet"
        ? "text-violet-700 dark:text-violet-400"
        : "text-[var(--ink-muted)]";
  return (
    <div className={`rounded-[var(--radius-lg)] border bg-[var(--surface)] px-3 py-2 ${toneClass}`}>
      <div className={`text-[11px] uppercase tracking-wide font-semibold ${titleTone}`}>{title}</div>
      <div className="text-[15px] font-bold text-[var(--ink)] leading-tight">
        {value}
        {suffix && <span className="ml-1 text-[11px] font-medium text-[var(--ink-muted)]">{suffix}</span>}
      </div>
    </div>
  );
}

export default ManualEvalCompareTable;
