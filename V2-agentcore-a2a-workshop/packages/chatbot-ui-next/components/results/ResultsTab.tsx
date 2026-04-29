// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useMemo, useState } from "react";

import DebateRecordCard from "@/components/DebateRecord";
import ManualEvalCompareTable from "@/components/ManualEvalCompareTable";
import { useAppState } from "@/lib/AppStateContext";
import { AGENT_ITEMS, ITEM_NAMES, STT_MAX_SCORES, scoreColor } from "@/lib/items";
import {
  computeClientGtComparison,
  getManualRowByItem,
} from "@/lib/manualEvalMapper";
import { useToast } from "@/lib/toast";
import type {
  CategoryItem,
  DebateRecord as DebateRecordType,
  GtComparison,
  GtComparisonItem,
  GtEvidenceComparison,
} from "@/lib/types";
import { buildResultsXlsx } from "@/lib/xlsxExport";

import AgentGroupCard from "./AgentGroupCard";
import GtComparisonPanel from "./GtComparisonPanel";
import GtEvidenceComparisonPanel from "./GtEvidenceComparisonPanel";
import ItemCard from "./ItemCard";
import { prepareItemProps, type RawReport } from "./prepareItemProps";

/**
 * ResultsTab — V2 HTML 6775~7497 이식.
 *   - 최상단 등급 카드 + 메트릭
 *   - GT 비교 패널 (있을 때)
 *   - 항목별 / 에이전트별 뷰 토글
 *   - 모두 펼치기 / 접기
 *   - NodeDrawer (openNodeId 로 열림)
 */

interface VerifIssueReason {
  severity?: string;
  origin?: string;
  type?: string;
  source?: string;
  description?: string;
  affected_items?: number[];
  evidence?: string;
}

interface MissedIssueRef {
  description?: string;
  item_number?: number;
}

interface ScoreAdjustmentRef {
  item_number: number;
  current_score?: number;
  suggested_score?: number;
  reason?: string;
}

interface PickedReport extends RawReport {
  summary?: { total_score?: number; max_score?: number; grade?: string };
  item_scores?: CategoryItem[];
  verification_issues?: {
    reasons?: VerifIssueReason[];
    critical_issues?: VerifIssueReason[];
    soft_warnings?: VerifIssueReason[];
    missed_issues?: Array<MissedIssueRef | string>;
    score_adjustments?: ScoreAdjustmentRef[];
  };
  verification?: {
    critical_issues?: VerifIssueReason[];
    soft_warnings?: VerifIssueReason[];
    missed_issues?: Array<MissedIssueRef | string>;
    score_adjustments?: ScoreAdjustmentRef[];
  };
  score_validation?: {
    issues?: VerifIssueReason[];
  };
}

function pickReport(result: unknown): PickedReport {
  if (!result) return {};
  const rp = (result as { report?: PickedReport }).report ?? (result as PickedReport);
  const nested = (rp as { report?: PickedReport }).report;
  return (nested || rp || {}) as PickedReport;
}

export function ResultsTab() {
  const { state } = useAppState();
  const {
    lastResult,
    streamingItems,
    gtScores: manualGt,
    manualEval,
    consultationId,
  } = state;

  const [viewMode, setViewMode] = useState<"item" | "agent">("agent");
  const [expandVersion, setExpandVersion] = useState(0);
  const [expandAllTarget, setExpandAllTarget] = useState<boolean | null>(null);
  const [editMode, setEditMode] = useState(false);
  const [humanSavedMap, setHumanSavedMap] = useState<
    Record<number, { score: number; confirmed: boolean }>
  >({});
  const handleHumanSaved = useCallback(
    (num: number, humanScore: number, confirmed: boolean) => {
      setHumanSavedMap((prev) => ({
        ...prev,
        [num]: { score: humanScore, confirmed },
      }));
    },
    [],
  );

  const report = useMemo(() => pickReport(lastResult), [lastResult]);
  const sm = report.summary || {};
  const finalItems: CategoryItem[] = report.item_scores || [];
  const hasFinal = finalItems.length > 0 || !!sm.grade;

  // 부분 스트리밍 결과를 CategoryItem 형태로 승격
  const partialItems: CategoryItem[] = useMemo(() => {
    return (streamingItems || []).map((s) => ({
      item_number: s.item_number,
      item_name: ITEM_NAMES[s.item_number] || s.label || "",
      score: s.score ?? 0,
      max_score: STT_MAX_SCORES[s.item_number] ?? 0,
    }));
  }, [streamingItems]);

  // report 가 비어있을 때 (backend layer3 gate / report_generator 실패 케이스) evaluations 에서 직접 추출.
  // backend 가 site_id=shinhan 시 SHINHAN_CATEGORY_META 적용해 100점 정합으로 떨어지므로
  // frontend 측 #11/#13 통합 / #15/#16 제거 로직은 제거. 단순 dedup 만 수행.
  const evaluationsFallback: CategoryItem[] = useMemo(() => {
    const evals = (lastResult as { evaluations?: Array<{ evaluation?: CategoryItem }> } | null)
      ?.evaluations;
    if (!Array.isArray(evals) || evals.length === 0) return [];
    const byNum: Record<number, CategoryItem> = {};
    for (const e of evals) {
      const ev = e?.evaluation;
      if (!ev || typeof ev.item_number !== "number") continue;
      byNum[ev.item_number] = { ...ev };
    }
    return Object.values(byNum).sort(
      (a, b) => (a.item_number ?? 0) - (b.item_number ?? 0),
    );
  }, [lastResult]);

  // 우선순위: report final → evaluations fallback → partial streaming
  const items = hasFinal
    ? finalItems
    : evaluationsFallback.length > 0
      ? evaluationsFallback
      : partialItems;
  const isStreaming = !hasFinal && evaluationsFallback.length === 0 && partialItems.length > 0;

  const runningTotal = items.reduce(
    (acc, it) => ({
      // unevaluable / score=null 은 합산 제외 (dept items SKIPPED_INFRA 등)
      score: acc.score + (typeof it.score === "number" ? it.score : 0),
      max: acc.max + (typeof it.score === "number" ? (it.max_score ?? 0) : 0),
    }),
    { score: 0, max: 0 },
  );
  // backend report 가 site_id 별 META 로 정합 점수 산출 (신한=100, generic=110).
  const totalScore = hasFinal ? sm.total_score ?? 0 : runningTotal.score;
  const maxScore = hasFinal ? sm.max_score ?? 100 : runningTotal.max;

  // 등급 산출 — backend summary 우선. 없으면 totalScore/maxScore 비율로 자동.
  const computeGrade = (s: number, m: number): string | null => {
    if (m <= 0) return null;
    const pct = (s / m) * 100;
    if (pct >= 95) return "S";
    if (pct >= 90) return "A";
    if (pct >= 80) return "B";
    if (pct >= 70) return "C";
    return "D";
  };
  const grade = sm.grade || computeGrade(totalScore, maxScore);

  // 등급 라벨 — UI 표시용
  const gradeLabel: Record<string, string> = {
    S: "최우수",
    A: "우수",
    B: "양호",
    C: "보통",
    D: "미흡",
  };

  // GT 비교: 서버 응답 우선, 없으면 Dev6 manualEval 클라이언트 계산으로 fallback.
  const gcServer: GtComparison | null =
    (lastResult as unknown as { gt_comparison?: GtComparison | null })?.gt_comparison ?? null;
  const gcClient: GtComparison | null = useMemo(
    () => (gcServer || !manualEval ? null : computeClientGtComparison(lastResult, manualEval)),
    [gcServer, manualEval, lastResult],
  );
  const gc: GtComparison | null = gcServer ?? gcClient;

  const gtItemsByNum = useMemo<Record<number, GtComparisonItem>>(() => {
    const map: Record<number, GtComparisonItem> = {};
    if (gc?.items) {
      for (const row of gc.items) map[row.item_number] = row;
    }
    // manualEval (Dev6) — xlsx 파싱된 수기 QA 평가표 rows 를 직접 주입 (gc 에 비해 note/근거까지 전달).
    if (manualEval) {
      for (const row of manualEval.rows) {
        const existing = map[row.no];
        map[row.no] = {
          item_number: row.no,
          item_name: row.item,
          ai_score: existing?.ai_score ?? null,
          gt_score: row.qa_score,
          max_score: row.max_score ?? existing?.max_score ?? STT_MAX_SCORES[row.no] ?? undefined,
          // AgentGroupCard → ItemCard 가 자동 전달하도록 확장 속성
          note: row.qa_evidence || null,
        } as GtComparisonItem & { note?: string | null };
      }
    } else if (!gc && manualGt && Array.isArray(manualGt.items)) {
      // /v2/gt-scores 응답 fallback
      for (const row of manualGt.items) {
        if (row.score == null) continue;
        map[row.item_number] = {
          item_number: row.item_number,
          item_name: String(row.item_name ?? "") || ITEM_NAMES[row.item_number] || "",
          ai_score: null,
          gt_score: Number(row.score),
          max_score: row.max_score ?? undefined,
        } as GtComparisonItem;
      }
    }
    return map;
  }, [gc, manualEval, manualGt]);

  const handleExpandAll = useCallback(() => {
    setExpandAllTarget(true);
    setExpandVersion((v) => v + 1);
  }, []);
  const handleCollapseAll = useCallback(() => {
    setExpandAllTarget(false);
    setExpandVersion((v) => v + 1);
  }, []);

  // ── 다운로드 버튼 핸들러 (xlsx / md / json) ─────────────────
  const toast = useToast();
  const [downloadBusy, setDownloadBusy] = useState<"" | "xlsx" | "md" | "json">("");

  const triggerDownload = useCallback(
    (blob: Blob, filename: string) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 200);
    },
    [],
  );

  const handleDownloadXlsx = useCallback(async () => {
    if (!lastResult) return;
    setDownloadBusy("xlsx");
    try {
      const out = await buildResultsXlsx(lastResult, state.llmBackend);
      if (!out) {
        toast.error("다운로드 실패", {
          description: "xlsx 모듈 로드 실패 — 네트워크/패키지 확인",
        });
        return;
      }
      const blob = new Blob([out.buf], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      triggerDownload(blob, out.filename);
    } catch (err) {
      toast.error("다운로드 실패", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setDownloadBusy("");
    }
  }, [lastResult, state.llmBackend, toast, triggerDownload]);

  const handleDownloadJson = useCallback(() => {
    if (!lastResult) return;
    setDownloadBusy("json");
    try {
      const payload = JSON.stringify(lastResult, null, 2);
      const blob = new Blob([payload], { type: "application/json" });
      const ts = new Date().toISOString().replace(/:/g, "-").replace(/\..+$/, "");
      triggerDownload(blob, `qa_result_${ts}.json`);
    } catch (err) {
      toast.error("다운로드 실패", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setDownloadBusy("");
    }
  }, [lastResult, toast, triggerDownload]);

  const handleDownloadMd = useCallback(() => {
    if (!lastResult) return;
    setDownloadBusy("md");
    try {
      // Report → markdown. V2 원본은 별도 함수가 없으므로 요약/항목/감점 중심 포맷.
      const rp = pickReport(lastResult);
      const sm = rp.summary || {};
      const lines: string[] = [];
      lines.push("# QA 평가 결과 리포트");
      lines.push("");
      lines.push(`- 생성일시: ${new Date().toLocaleString("ko-KR")}`);
      lines.push(`- LLM 백엔드: ${state.llmBackend === "sagemaker" ? "Qwen3-8B" : "Sonnet-4.6"}`);
      lines.push(`- 총점: **${sm.total_score ?? 0}** / ${sm.max_score ?? 100}점`);
      if (sm.grade) lines.push(`- 등급: **${sm.grade}**`);
      lines.push("");
      lines.push("## 항목별 평가");
      lines.push("");
      lines.push("| # | 항목 | 점수 | 판정 사유 |");
      lines.push("|---|---|---|---|");
      for (const it of rp.item_scores || []) {
        const jd = String(it.judgment || it.summary || "").replace(/\|/g, "\\|").replace(/\n/g, " ");
        lines.push(
          `| ${it.item_number} | ${it.item_name || ITEM_NAMES[it.item_number] || ""} | ${it.score ?? "-"}/${it.max_score ?? STT_MAX_SCORES[it.item_number] ?? ""} | ${jd} |`,
        );
      }
      lines.push("");
      const gtEv = (lastResult as unknown as { gt_evidence_comparison?: GtEvidenceComparison })
        ?.gt_evidence_comparison;
      if (gtEv?.summary) {
        lines.push("## GT 근거 비교 (LLM 판정)");
        lines.push("");
        lines.push(
          `일치 ${gtEv.summary.match ?? 0} / 부분 ${gtEv.summary.partial ?? 0} / 불일치 ${gtEv.summary.mismatch ?? 0} — 일치율 ${gtEv.summary.match_rate ?? 0}%`,
        );
        lines.push("");
      }
      const blob = new Blob([lines.join("\n")], {
        type: "text/markdown;charset=utf-8",
      });
      const ts = new Date().toISOString().replace(/:/g, "-").replace(/\..+$/, "");
      triggerDownload(blob, `qa_result_${ts}.md`);
    } catch (err) {
      toast.error("다운로드 실패", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setDownloadBusy("");
    }
  }, [lastResult, state.llmBackend, toast, triggerDownload]);

  if (!hasFinal && !isStreaming && evaluationsFallback.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-title">아직 평가 결과가 없습니다</div>
        <div className="empty-state-desc">
          파이프라인 탭에서 평가를 실행하면 여기에 결과가 자동 표시됩니다.
        </div>
      </div>
    );
  }

  const totalColor = scoreColor(totalScore, maxScore);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* 헤드라인 */}
      <div
        className="card card-padded"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div>
          <div className="section-eyebrow">평가 결과</div>
          <div
            className="h-display tabular-nums"
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 10,
              color: totalColor,
            }}
          >
            {totalScore}
            <span
              style={{
                fontSize: 20,
                color: "var(--ink-muted)",
                fontWeight: 500,
              }}
            >
              / {maxScore}점
            </span>
            {grade && (
              <span
                className="badge badge-accent"
                style={{
                  fontSize: 14,
                  padding: "3px 12px",
                  marginLeft: 8,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                }}
                title="S 95↑ · A 90~94 · B 80~89 · C 70~79 · D 70↓"
              >
                <span style={{ fontWeight: 700 }}>등급</span>
                <span style={{ fontSize: 18, fontWeight: 800, letterSpacing: 0.3 }}>
                  {grade}
                </span>
                {gradeLabel[grade] && (
                  <span style={{ fontSize: 12, opacity: 0.85 }}>
                    · {gradeLabel[grade]}
                  </span>
                )}
              </span>
            )}
            {isStreaming && (
              <span className="status-chip">
                <span className="pulse-dot" />
                집계 중
              </span>
            )}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {/* 다운로드 버튼 — xlsx / md / json */}
          <div
            style={{
              display: "inline-flex",
              border: "1px solid var(--border-strong)",
              borderRadius: "var(--radius-sm)",
              overflow: "hidden",
            }}
          >
            <button
              type="button"
              className="btn-ghost"
              title="xlsx 다운로드 — 요약 / 항목 / GT 3시트"
              disabled={!!downloadBusy || !lastResult}
              onClick={handleDownloadXlsx}
              style={{
                borderRadius: 0,
                border: "none",
                fontSize: 12,
                padding: "4px 12px",
              }}
            >
              {downloadBusy === "xlsx" ? "내보내는 중..." : "⬇ xlsx"}
            </button>
            <button
              type="button"
              className="btn-ghost"
              title="markdown 다운로드 — 항목/판정 요약"
              disabled={!!downloadBusy || !lastResult}
              onClick={handleDownloadMd}
              style={{
                borderRadius: 0,
                border: "none",
                borderLeft: "1px solid var(--border-strong)",
                fontSize: 12,
                padding: "4px 12px",
              }}
            >
              {downloadBusy === "md" ? "..." : "⬇ md"}
            </button>
            <button
              type="button"
              className="btn-ghost"
              title="raw JSON 다운로드 — QAOutputV2 전체"
              disabled={!!downloadBusy || !lastResult}
              onClick={handleDownloadJson}
              style={{
                borderRadius: 0,
                border: "none",
                borderLeft: "1px solid var(--border-strong)",
                fontSize: 12,
                padding: "4px 12px",
              }}
            >
              {downloadBusy === "json" ? "..." : "⬇ json"}
            </button>
          </div>
          <label
            className="btn-ghost"
            style={{
              cursor: "pointer",
              userSelect: "none",
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "4px 12px",
              fontSize: 12,
              background: editMode ? "var(--warn-bg)" : "transparent",
              color: editMode ? "var(--warn)" : "var(--ink-soft)",
              borderColor: editMode ? "var(--warn-border)" : undefined,
            }}
            title="ON 시 각 항목에 사람 점수 / 메모 입력 영역 표시"
          >
            <input
              type="checkbox"
              checked={editMode}
              onChange={(e) => setEditMode(e.target.checked)}
              style={{ margin: 0 }}
            />
            ✏ 편집 모드
          </label>
          <button
            type="button"
            className="btn-ghost"
            onClick={handleExpandAll}
            title="모든 카드를 펼칩니다"
          >
            모두 펼치기
          </button>
          <button
            type="button"
            className="btn-ghost"
            onClick={handleCollapseAll}
            title="모든 카드를 접습니다"
          >
            모두 접기
          </button>
          <div
            role="tablist"
            style={{
              display: "inline-flex",
              border: "1px solid var(--border-strong)",
              borderRadius: "var(--radius-sm)",
              overflow: "hidden",
            }}
          >
            <button
              type="button"
              role="tab"
              aria-selected={viewMode === "agent"}
              onClick={() => setViewMode("agent")}
              className={viewMode === "agent" ? "btn-primary" : "btn-ghost"}
              style={{
                borderRadius: 0,
                fontSize: 12,
                padding: "4px 12px",
                border: "none",
              }}
            >
              에이전트별
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={viewMode === "item"}
              onClick={() => setViewMode("item")}
              className={viewMode === "item" ? "btn-primary" : "btn-ghost"}
              style={{
                borderRadius: 0,
                fontSize: 12,
                padding: "4px 12px",
                border: "none",
              }}
            >
              항목별
            </button>
          </div>
        </div>
      </div>

      {/* GT 비교 패널 */}
      <GtComparisonPanel gc={gc} />

      {/* GT 근거 비교 (LLM 판정) — V2 원본 라인 7052~7143 */}
      <GtEvidenceComparisonPanel
        ge={
          (lastResult as unknown as {
            gt_evidence_comparison?: GtEvidenceComparison;
          })?.gt_evidence_comparison ?? null
        }
      />

      {/* 사람 QA 평가표 비교 (Dev6 — xlsx 첨부 시 노출) */}
      {manualEval && (
        <div
          className="card card-padded"
          style={{ borderLeft: "4px solid var(--success, #16a34a)" }}
        >
          <ManualEvalCompareTable modelName="AI 평가" />
        </div>
      )}

      {/* Phase C 검증 이슈 (통합 뷰 — V2 7146~7290 이식) */}
      {(() => {
        const vi = report.verification_issues || {};
        const verification = report.verification || {};
        const scoreVal = report.score_validation || {};
        const combinedReasons: VerifIssueReason[] = [
          ...(vi.reasons || []),
          ...(vi.critical_issues || verification.critical_issues || []).map((c) => ({
            ...c,
            severity: "critical",
          })),
          ...(vi.soft_warnings || verification.soft_warnings || []).map((w) => ({
            ...w,
            severity: "soft",
          })),
          ...(scoreVal.issues || []).map((i) => ({
            ...i,
            origin: "score_validation",
          })),
        ];
        const missedIssues =
          vi.missed_issues || verification.missed_issues || [];
        const scoreAdjustments =
          vi.score_adjustments || verification.score_adjustments || [];
        const criticalCount = combinedReasons.filter(
          (r) => r.severity === "critical",
        ).length;
        const softCount = combinedReasons.filter(
          (r) => r.severity !== "critical",
        ).length;
        const show =
          combinedReasons.length > 0 ||
          missedIssues.length > 0 ||
          scoreAdjustments.length > 0;
        if (!show) return null;
        return (
          <div
            className="card card-padded"
            style={{
              borderLeft: `4px solid ${criticalCount > 0 ? "var(--danger)" : "var(--warn)"}`,
              background:
                criticalCount > 0
                  ? "rgba(239,68,68,0.06)"
                  : "rgba(245,158,11,0.06)",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginBottom: 6,
              }}
            >
              <span style={{ fontSize: 16 }}>
                {criticalCount > 0 ? "🚨" : "⚠️"}
              </span>
              <div style={{ flex: 1 }}>
                <div
                  className="section-eyebrow"
                  style={{
                    color:
                      criticalCount > 0 ? "var(--danger)" : "var(--warn)",
                    marginBottom: 0,
                  }}
                >
                  검증 탐지 문제
                </div>
                <div
                  style={{
                    fontSize: 12,
                    color: "var(--ink-muted)",
                    marginTop: 2,
                  }}
                >
                  일관성 검증·점수 산술 검증이 탐지한 원인
                </div>
              </div>
              {criticalCount > 0 && (
                <span
                  className="badge badge-danger"
                  style={{ fontSize: 11 }}
                >
                  critical {criticalCount}
                </span>
              )}
              {softCount > 0 && (
                <span
                  className="badge badge-warn"
                  style={{ fontSize: 11 }}
                >
                  soft {softCount}
                </span>
              )}
            </div>
            <div
              style={{ display: "flex", flexDirection: "column", gap: 6 }}
            >
              {combinedReasons.map((r, i) => (
                <div
                  key={i}
                  style={{
                    padding: "8px 10px",
                    background:
                      r.severity === "critical"
                        ? "var(--danger-bg)"
                        : "var(--warn-bg)",
                    border: `1px solid ${
                      r.severity === "critical"
                        ? "var(--danger-border)"
                        : "var(--warn-border)"
                    }`,
                    borderRadius: "var(--radius-sm)",
                    fontSize: 12,
                    color: "var(--ink-soft)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      gap: 6,
                      flexWrap: "wrap",
                      alignItems: "center",
                      marginBottom: 4,
                    }}
                  >
                    <span
                      className={
                        r.severity === "critical"
                          ? "badge badge-danger"
                          : "badge badge-warn"
                      }
                    >
                      {r.severity || "soft"}
                    </span>
                    {r.type && (
                      <span style={{ fontWeight: 600 }}>{r.type}</span>
                    )}
                    <span
                      style={{
                        fontSize: 11,
                        color: "var(--ink-muted)",
                      }}
                    >
                      {r.origin === "score_validation"
                        ? "점수 산술"
                        : "일관성"}
                    </span>
                    {r.source && (
                      <span
                        style={{
                          fontSize: 10,
                          padding: "1px 6px",
                          borderRadius: 3,
                          background:
                            r.source === "rule"
                              ? "rgba(59,130,246,0.1)"
                              : "rgba(168,85,247,0.1)",
                          color: r.source === "rule" ? "#3b82f6" : "#a855f7",
                        }}
                      >
                        {r.source === "rule" ? "규칙" : "LLM"}
                      </span>
                    )}
                    {r.affected_items && r.affected_items.length > 0 && (
                      <span
                        style={{ fontSize: 11, color: "var(--ink-muted)" }}
                      >
                        항목 #{r.affected_items.join(", #")}
                      </span>
                    )}
                  </div>
                  {r.description && <div>{r.description}</div>}
                  {r.evidence && (
                    <div
                      style={{
                        fontSize: 11,
                        color: "var(--ink-muted)",
                        marginTop: 3,
                        fontStyle: "italic",
                      }}
                    >
                      근거: {r.evidence}
                    </div>
                  )}
                </div>
              ))}

              {missedIssues.length > 0 && (
                <div
                  style={{
                    padding: "10px 12px",
                    borderRadius: 6,
                    background: "rgba(168,85,247,0.06)",
                    border: "1px solid rgba(168,85,247,0.25)",
                  }}
                >
                  <div
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      color: "#a855f7",
                      marginBottom: 6,
                    }}
                  >
                    🔍 LLM 이 지적한 놓친 이슈 ({missedIssues.length}건)
                  </div>
                  {missedIssues.map((m, i) => (
                    <div
                      key={i}
                      style={{
                        fontSize: 11,
                        color: "var(--ink)",
                        lineHeight: 1.5,
                        marginTop: i > 0 ? 4 : 0,
                      }}
                    >
                      {typeof m === "string"
                        ? m
                        : m.description || JSON.stringify(m)}
                    </div>
                  ))}
                </div>
              )}

              {scoreAdjustments.length > 0 && (
                <div
                  style={{
                    padding: "10px 12px",
                    borderRadius: 6,
                    background: "rgba(59,130,246,0.06)",
                    border: "1px solid rgba(59,130,246,0.25)",
                  }}
                >
                  <div
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      color: "#3b82f6",
                      marginBottom: 6,
                    }}
                  >
                    📊 LLM 점수 조정 제안 ({scoreAdjustments.length}건)
                  </div>
                  {scoreAdjustments.map((a, i) => (
                    <div
                      key={i}
                      style={{
                        fontSize: 11,
                        color: "var(--ink)",
                        lineHeight: 1.5,
                        marginTop: i > 0 ? 4 : 0,
                      }}
                    >
                      <span style={{ fontWeight: 600 }}>
                        #{a.item_number}
                      </span>{" "}
                      {a.current_score}점 → {a.suggested_score}점
                      {a.reason && (
                        <span
                          style={{
                            color: "var(--ink-muted)",
                            marginLeft: 6,
                          }}
                        >
                          — {a.reason}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        );
      })()}

      {/* 항목별 / 에이전트별 */}
      <div>
        {viewMode === "agent"
          ? AGENT_ITEMS.map((ag) => {
              const hasAnyItem = ag.items.some((n) =>
                items.find((it) => it.item_number === n),
              );
              if (!hasAnyItem) return null;
              const hasGap = ag.items.some((n) => {
                const it = items.find((x) => x.item_number === n);
                return it && (it.score ?? 0) < (it.max_score ?? 0);
              });
              return (
                <AgentGroupCard
                  key={ag.agent}
                  agent={ag.agent}
                  label={ag.label}
                  phase={ag.phase}
                  items={ag.items}
                  report={report}
                  allItems={items}
                  gtItemsByNum={gtItemsByNum}
                  defaultOpen={hasGap}
                  expandVersion={expandVersion}
                  expandAllTarget={expandAllTarget}
                  editMode={editMode}
                  consultationId={consultationId}
                  humanSavedMap={humanSavedMap}
                  onHumanSaved={handleHumanSaved}
                />
              );
            })
          : items.map((it) => {
              const num = it.item_number;
              const props = prepareItemProps(it, num, report);
              const gtRow = gtItemsByNum[num];
              const manualRow = getManualRowByItem(manualEval, num);
              const saved = humanSavedMap[num];
              return (
                <ItemCard
                  key={num}
                  num={num}
                  name={it.item_name || ITEM_NAMES[num] || ""}
                  score={props.sc}
                  maxScore={props.mx}
                  deductions={props.itemDeds}
                  evidence={props.itemEv}
                  strengths={props.itemStr}
                  improvements={props.itemImp}
                  coaching={props.itemCoach}
                  judgment={it.judgment || null}
                  summary={it.summary || null}
                  personaVotes={it.persona_votes || null}
                  personaMergePath={it.persona_merge_path || null}
                  personaMergeRule={it.persona_merge_rule || null}
                  personaStepSpread={it.persona_step_spread ?? null}
                  postDebateJudgeScore={it.judge_score ?? null}
                  postDebateJudgeReasoning={it.judge_reasoning ?? null}
                  postDebateJudgeDeductions={
                    Array.isArray(it.judge_deductions)
                      ? (it.judge_deductions as Array<{ reason: string; points: number }>)
                      : null
                  }
                  postDebateJudgeEvidence={
                    Array.isArray(it.judge_evidence)
                      ? (it.judge_evidence as Array<{ speaker: string; quote: string }>)
                      : null
                  }
                  forceT3={!!it.force_t3 || !!it.mandatory_human_review}
                  mandatoryHumanReview={!!it.mandatory_human_review}
                  aiConfidence={it.confidence?.final ?? null}
                  defaultOpen={props.hasDetail && props.sc < props.mx}
                  expandVersion={expandVersion}
                  expandAllTarget={expandAllTarget}
                  editMode={editMode}
                  consultationId={consultationId}
                  humanSavedScore={saved?.score ?? null}
                  humanConfirmed={saved?.confirmed}
                  onHumanSaved={handleHumanSaved}
                  gt={
                    gtRow || manualRow
                      ? {
                          gt_score: gtRow?.gt_score ?? manualRow?.qa_score ?? null,
                          max_score: gtRow?.max_score ?? manualRow?.max_score ?? null,
                          excluded: gtRow?.excluded,
                          note: manualRow?.qa_evidence || null,
                        }
                      : null
                  }
                />
              );
            })}
      </div>

      {/* 토론 기록 — 항목별 AG2 토론 결과 (페르소나 발언, 모더레이터 판정, 최종 합의/투표) */}
      {(() => {
        const debates = (lastResult as unknown as {
          debates?: Record<string, DebateRecordType> | null;
        })?.debates;
        if (!debates) return null;
        const entries = Object.values(debates).filter(
          (d): d is DebateRecordType => !!d && typeof d === "object",
        );
        if (entries.length === 0) return null;
        entries.sort((a, b) => Number(a.item_number) - Number(b.item_number));
        const convergedN = entries.filter((e) => e.converged).length;
        return (
          <section
            style={{
              padding: "14px 18px",
              background: "#fff",
              border: "1px solid var(--border, #e6e2d5)",
              borderRadius: 12,
              display: "flex",
              flexDirection: "column",
              gap: 10,
            }}
          >
            <header
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                flexWrap: "wrap",
              }}
            >
              <span style={{ fontSize: 18 }}>🗣️</span>
              <h3 style={{ fontSize: 15, fontWeight: 700, margin: 0 }}>
                AG2 토론 결과 · {entries.length}건
              </h3>
              <span style={{ fontSize: 11.5, color: "var(--ink-subtle, #6b6b6b)" }}>
                만장일치 {convergedN} / 투표 {entries.length - convergedN} — 각 항목 카드를
                펼쳐서 페르소나 발언·라운드·모더레이터 판정·최종 근거를 확인
              </span>
            </header>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {entries.map((rec) => (
                <DebateRecordCard key={rec.item_number} record={rec} />
              ))}
            </div>
          </section>
        );
      })()}

      {/* 경과 시간 */}
      {lastResult && (lastResult as unknown as { elapsed_seconds?: number })?.elapsed_seconds != null && (
        <div
          style={{
            textAlign: "center",
            fontSize: 12,
            color: "var(--ink-muted)",
          }}
        >
          {(
            lastResult as unknown as { elapsed_seconds: number }
          ).elapsed_seconds.toFixed(1)}
          초
        </div>
      )}

      {/* NodeDrawer 는 app/evaluate/page.tsx 의 <GlobalNodeDrawer /> 가 렌더 — 여기선 중복 방지 */}
    </div>
  );
}

export default ResultsTab;
