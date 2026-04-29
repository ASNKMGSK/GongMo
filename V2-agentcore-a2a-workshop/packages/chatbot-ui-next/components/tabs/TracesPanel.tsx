// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useMemo, useState } from "react";

import { useAppStore, type TraceEntry } from "@/lib/AppStateContext";
import { useToast } from "@/lib/toast";

/* ── JSON pretty print with syntax highlighting ───────────── */

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * JSON 을 pretty-print 후 토큰별로 color span 씌움.
 * 직접 안전한 HTML 만 생성 — innerHTML 에 바로 꽂아도 OK.
 */
function syntaxHighlightJson(value: unknown): string {
  let text: string;
  try {
    text = JSON.stringify(value, null, 2) ?? "null";
  } catch {
    text = String(value);
  }
  text = escapeHtml(text);
  return text.replace(
    /("(?:\\.|[^"\\])*"(?:\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
    (match) => {
      let cls = "text-[var(--info)]"; // number default
      if (/^"/.test(match)) {
        cls = /:$/.test(match)
          ? "text-[var(--accent)] font-medium"
          : "text-[var(--success)]";
      } else if (/true|false/.test(match)) {
        cls = "text-[var(--warn)]";
      } else if (/null/.test(match)) {
        cls = "text-[var(--ink-muted)]";
      }
      return `<span class="${cls}">${match}</span>`;
    },
  );
}

/* ── Trace card ───────────────────────────────────────────── */

function TraceCard({
  trace,
  index,
  maxElapsed,
  open,
  onToggle,
}: {
  trace: TraceEntry;
  index: number;
  maxElapsed: number;
  open: boolean;
  onToggle: () => void;
}) {
  const [subTab, setSubTab] = useState<"input" | "output" | "detail">("detail");
  const toast = useToast();

  const elapsed = trace.elapsed ?? 0;
  const barWidth = maxElapsed > 0 ? Math.max(4, (elapsed / maxElapsed) * 100) : 4;

  const isError = trace.status === "error" || trace.status === "failed";
  const isDone = trace.status === "completed" || trace.status === "done";

  const statusBadgeCls = isError
    ? "badge badge-danger"
    : isDone
      ? "badge badge-success"
      : trace.status === "active" || trace.status === "started"
        ? "badge badge-info"
        : "badge badge-neutral";

  const detail = trace.detail ?? {};
  const input = (detail as Record<string, unknown>).input;
  const output = (detail as Record<string, unknown>).output;
  const hasInput = input !== undefined && input !== null;
  const hasOutput = output !== undefined && output !== null;

  // V2 parity — output.evaluations[].{score, max_score} 합산 → 점수 배지
  const score = useMemo(() => {
    const evals = (output as Record<string, unknown> | undefined)?.evaluations;
    if (!Array.isArray(evals) || evals.length === 0) return null;
    let total = 0;
    let max = 0;
    let hit = false;
    for (const entry of evals) {
      const inner = (entry as Record<string, unknown>)?.evaluation ?? entry;
      const s = (inner as Record<string, unknown>)?.score;
      const m = (inner as Record<string, unknown>)?.max_score;
      if (typeof s === "number" && typeof m === "number") {
        total += s;
        max += m;
        hit = true;
      }
    }
    if (!hit) return null;
    const variant: "full" | "partial" | "zero" =
      total >= max ? "full" : total > 0 ? "partial" : "zero";
    return { total, max, variant };
  }, [output]);

  const scoreBadgeCls =
    score?.variant === "full"
      ? "badge badge-success"
      : score?.variant === "partial"
        ? "badge badge-warn"
        : "badge badge-danger";

  const activeView = subTab === "input" && hasInput
    ? input
    : subTab === "output" && hasOutput
      ? output
      : detail;

  const handleCopy = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      try {
        const txt = JSON.stringify(activeView, null, 2);
        navigator.clipboard.writeText(txt);
        toast.success("트레이스 JSON 복사");
      } catch {
        toast.error("복사 실패");
      }
    },
    [activeView, toast],
  );

  return (
    <div className="card card-hoverable card-padded-sm">
      <button
        type="button"
        className="flex w-full items-center gap-2 text-left"
        onClick={onToggle}
        aria-expanded={open}
      >
        <span
          className={`inline-block transition-transform ${open ? "rotate-90" : ""}`}
          aria-hidden="true"
        >
          <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 5v14l11-7z" />
          </svg>
        </span>
        <span className="text-[11px] text-[var(--ink-subtle)] tabular-nums">#{index + 1}</span>
        <span className={statusBadgeCls}>{trace.status || "—"}</span>
        <span className="font-semibold text-[13px] text-[var(--ink)]">
          {trace.label || trace.node}
        </span>
        <span className="font-mono text-[11px] text-[var(--ink-muted)]">{trace.node}</span>
        {score && (
          <span className={scoreBadgeCls} title="평가 항목 점수 합계">
            {score.total}/{score.max}
          </span>
        )}
        <span className="ml-auto text-[11px] text-[var(--ink-subtle)] tabular-nums">
          {trace.time}
        </span>
        <span className="font-mono text-[12px] tabular-nums text-[var(--ink-soft)] min-w-[52px] text-right">
          {elapsed.toFixed(2)}s
        </span>
      </button>

      {/* Duration bar */}
      <div
        className="mt-2 h-[3px] w-full rounded-full bg-[var(--surface-muted)] overflow-hidden"
        aria-hidden="true"
      >
        <div
          className={`h-full ${isError ? "bg-[var(--danger)]" : "bg-[var(--accent)]"} transition-all`}
          style={{ width: `${barWidth}%` }}
        />
      </div>

      {open && (
        <div className="mt-3 border-t border-[var(--border)] pt-3">
          <div className="flex items-center gap-1 mb-2">
            <button
              type="button"
              className={`btn-ghost btn-sm ${subTab === "detail" ? "bg-[var(--surface-hover)] text-[var(--accent)]" : ""}`}
              onClick={() => setSubTab("detail")}
            >
              Detail
            </button>
            <button
              type="button"
              className={`btn-ghost btn-sm ${subTab === "input" ? "bg-[var(--surface-hover)] text-[var(--accent)]" : ""}`}
              onClick={() => setSubTab("input")}
              disabled={!hasInput}
            >
              Input
            </button>
            <button
              type="button"
              className={`btn-ghost btn-sm ${subTab === "output" ? "bg-[var(--surface-hover)] text-[var(--accent)]" : ""}`}
              onClick={() => setSubTab("output")}
              disabled={!hasOutput}
            >
              Output
            </button>
            <button
              type="button"
              className="btn-secondary btn-sm ml-auto"
              onClick={handleCopy}
            >
              복사
            </button>
          </div>

          <pre
            className="rounded-[var(--radius-sm)] bg-[var(--surface-muted)] p-3 font-mono text-[11.5px] leading-relaxed max-h-[420px] overflow-auto border border-[var(--border)] text-[var(--ink-soft)]"
            dangerouslySetInnerHTML={{ __html: syntaxHighlightJson(activeView) }}
          />
        </div>
      )}
    </div>
  );
}

/* ── Main panel ───────────────────────────────────────────── */

export function TracesPanel() {
  const { traces } = useAppStore();
  const toast = useToast();

  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [openSet, setOpenSet] = useState<Set<string>>(new Set());

  const statusOptions = useMemo(() => {
    const set = new Set<string>();
    traces.forEach((t) => {
      if (t.status) set.add(t.status);
    });
    return Array.from(set).sort();
  }, [traces]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return traces.filter((t) => {
      if (statusFilter !== "all" && t.status !== statusFilter) return false;
      if (q) {
        const hay = `${t.node} ${t.label ?? ""} ${t.status ?? ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [traces, query, statusFilter]);

  const maxElapsed = useMemo(() => {
    return Math.max(...filtered.map((t) => t.elapsed ?? 0), 1);
  }, [filtered]);

  const toggle = useCallback((id: string) => {
    setOpenSet((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const expandAll = useCallback(() => {
    setOpenSet(new Set(filtered.map((t) => t.id)));
  }, [filtered]);

  const collapseAll = useCallback(() => {
    setOpenSet(new Set());
  }, []);

  const handleCopyAll = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(filtered, null, 2));
      toast.success(`${filtered.length}개 트레이스 복사`);
    } catch {
      toast.error("복사 실패");
    }
  }, [filtered, toast]);

  return (
    <div className="flex flex-col gap-3">
      {/* Toolbar */}
      <div className="card card-padded-sm flex flex-wrap items-center gap-2">
        <span className="badge badge-neutral">{traces.length}개</span>
        {filtered.length !== traces.length && (
          <span className="badge badge-accent">필터 {filtered.length}</span>
        )}
        <span className="badge badge-outline">펼침 {openSet.size}</span>

        <select
          className="input-field input-sm"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          aria-label="상태 필터"
        >
          <option value="all">전체 상태</option>
          {statusOptions.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        <input
          type="text"
          className="input-field input-sm flex-1 min-w-[160px]"
          placeholder="노드/레이블 검색…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />

        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={expandAll}
            disabled={filtered.length === 0 || openSet.size === filtered.length}
          >
            모두 펼치기
          </button>
          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={collapseAll}
            disabled={openSet.size === 0}
          >
            모두 접기
          </button>
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={handleCopyAll}
            disabled={filtered.length === 0}
          >
            전체 복사
          </button>
        </div>
      </div>

      {/* Body */}
      {traces.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon" aria-hidden="true">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
          </div>
          <div className="empty-state-title">아직 트레이스가 없습니다</div>
          <div className="empty-state-desc">
            평가를 실행하면 노드별 input/output/경과시간이 여기에 표시됩니다.
          </div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">검색 결과 없음</div>
          <div className="empty-state-desc">필터 또는 검색어를 조정해 보세요.</div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {filtered.map((trace, i) => (
            <TraceCard
              key={trace.id}
              trace={trace}
              index={i}
              maxElapsed={maxElapsed}
              open={openSet.has(trace.id)}
              onToggle={() => toggle(trace.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default TracesPanel;
