// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAppStore, type RawLogEntry } from "@/lib/AppStateContext";
import { useToast } from "@/lib/toast";

/* ── JSON syntax highlight ────────────────────────────────── */

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

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
      let cls = "text-[var(--info)]";
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

/* ── Event → badge class ──────────────────────────────────── */

function eventBadgeClass(event: string): string {
  switch (event) {
    case "status":
      return "badge badge-success";
    case "routing":
      return "badge badge-accent";
    case "node_trace":
      return "badge badge-info";
    case "result":
      return "badge badge-warn";
    case "done":
      return "badge badge-neutral";
    case "error":
      return "badge badge-danger";
    case "start":
      return "badge badge-outline";
    default:
      return "badge badge-outline";
  }
}

/* ── Summary extractor — V2 RawLogEntry 로직 이식 ─────────── */

function summarize(event: string, data: unknown): string {
  if (data == null || typeof data !== "object") return typeof data === "string" ? data : "";
  const d = data as Record<string, unknown>;
  const asNum = (v: unknown): number | null => (typeof v === "number" ? v : null);

  if (event === "status") {
    const label = (d.label as string) ?? (d.node as string) ?? "";
    const status = (d.status as string) ?? "";
    const el = asNum(d.elapsed);
    return `${label} · ${status}${el != null ? ` · ${el.toFixed(2)}s` : ""}`;
  }
  if (event === "routing") {
    const from = (d.phase_label as string) ?? (d.phase as string) ?? "";
    const to = (d.next_label as string) ?? (d.next_node as string) ?? "";
    return `${from} → ${to}`;
  }
  if (event === "node_trace") {
    const label = (d.label as string) ?? (d.node as string) ?? "";
    const el = asNum(d.elapsed);
    return `${label}${el != null ? ` · ${el.toFixed(2)}s` : ""}`;
  }
  if (event === "result") {
    const report = d.report as Record<string, unknown> | undefined;
    const summary = report?.summary as Record<string, unknown> | undefined;
    const grade = summary?.grade ?? "—";
    const el = asNum(d.elapsed_seconds);
    return `grade=${String(grade)}${el != null ? ` · ${el.toFixed(2)}s` : ""}`;
  }
  if (event === "done") {
    const el = asNum(d.elapsed_seconds);
    return el != null ? `elapsed=${el.toFixed(2)}s` : "";
  }
  if (event === "error") {
    return (d.message as string) ?? "";
  }
  if (event === "persona_turn") {
    return `${(d.persona as string) ?? "?"} · #${d.item_number ?? "?"} R${d.round ?? "?"} · ${d.score ?? "?"}`;
  }
  if (event === "moderator_verdict") {
    return `R${d.round ?? "?"} · ${d.consensus ? "consensus" : "split"}${d.score != null ? ` · ${d.score}` : ""}`;
  }
  if (event === "debate_round_start" || event === "debate_final") {
    const parts: string[] = [];
    if (d.item_number != null) parts.push(`#${d.item_number}`);
    if (d.round != null) parts.push(`R${d.round}`);
    if (d.final_score != null) parts.push(`score=${d.final_score}`);
    return parts.join(" · ");
  }
  // fallback — first few keys
  const keys = Object.keys(d).slice(0, 3);
  return keys.map((k) => `${k}=${shortStr(d[k])}`).join(" · ");
}

function shortStr(v: unknown): string {
  if (v == null) return "null";
  if (typeof v === "string") return v.length > 24 ? `${v.slice(0, 24)}…` : v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return `[${v.length}]`;
  return "{…}";
}

/* ── Entry card ───────────────────────────────────────────── */

function RawLogCard({
  entry,
  index,
  open,
  onToggle,
}: {
  entry: RawLogEntry;
  index: number;
  open: boolean;
  onToggle: () => void;
}) {
  const toast = useToast();
  const summary = useMemo(() => summarize(entry.event, entry.data), [entry]);
  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);

  const handleCopy = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      try {
        const txt = JSON.stringify(entry.data, null, 2);
        navigator.clipboard.writeText(txt);
        setCopied(true);
        if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
        copyTimerRef.current = setTimeout(() => setCopied(false), 2000);
      } catch {
        toast.error("복사 실패");
      }
    },
    [entry, toast],
  );

  // ⚠ 접근성: <button> 안에 <button> 은 HTML invalid (hydration 에러).
  // → 외곽을 div role="button" 으로 바꿔서 nested button 회피. 키보드 인터랙션은
  //    Enter / Space 핸들러로 동등하게 보장.
  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onToggle();
    }
  };

  return (
    <div className="card card-hoverable card-padded-sm">
      <div
        role="button"
        tabIndex={0}
        className="flex w-full items-center gap-2 text-left cursor-pointer"
        onClick={onToggle}
        onKeyDown={handleKeyDown}
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
        <span className="text-[11px] text-[var(--ink-subtle)] tabular-nums flex-shrink-0 w-10">
          #{index + 1}
        </span>
        <span className="text-[11px] text-[var(--ink-subtle)] tabular-nums flex-shrink-0 font-mono">
          {entry.time}
        </span>
        <span className={`${eventBadgeClass(entry.event)} flex-shrink-0`}>{entry.event}</span>
        <span className="text-[12px] text-[var(--ink-soft)] truncate flex-1 min-w-0">
          {summary}
        </span>
        <button
          type="button"
          className={`btn-sm flex-shrink-0 ${copied ? "btn-primary" : "btn-ghost"}`}
          onClick={(e) => {
            e.stopPropagation();
            handleCopy(e);
          }}
          title="JSON 복사"
        >
          {copied ? "복사됨!" : "복사"}
        </button>
      </div>

      {open && (
        <pre
          className="mt-3 rounded-[var(--radius-sm)] bg-[var(--surface-muted)] p-3 font-mono text-[11.5px] leading-relaxed max-h-[420px] overflow-auto border border-[var(--border)] text-[var(--ink-soft)]"
          dangerouslySetInnerHTML={{ __html: syntaxHighlightJson(entry.data) }}
        />
      )}
    </div>
  );
}

/* ── Main panel ───────────────────────────────────────────── */

function formatRawLogsAsText(entries: RawLogEntry[]): string {
  return entries
    .map((e) => {
      let payload: string;
      try {
        payload = JSON.stringify(e.data);
      } catch {
        payload = String(e.data);
      }
      return `[${e.time}] event: ${e.event}\ndata: ${payload}\n`;
    })
    .join("\n");
}

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function RawLogsPanel() {
  const { rawLogs } = useAppStore();
  const toast = useToast();

  const [query, setQuery] = useState("");
  const [eventFilter, setEventFilter] = useState("all");
  const [openSet, setOpenSet] = useState<Set<string>>(new Set());

  const eventOptions = useMemo(() => {
    const set = new Set<string>();
    rawLogs.forEach((e) => {
      if (e.event) set.add(e.event);
    });
    return Array.from(set).sort();
  }, [rawLogs]);

  const eventCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    rawLogs.forEach((e) => {
      counts[e.event] = (counts[e.event] ?? 0) + 1;
    });
    return counts;
  }, [rawLogs]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rawLogs.filter((e) => {
      if (eventFilter !== "all" && e.event !== eventFilter) return false;
      if (q) {
        let hay = e.event.toLowerCase();
        try {
          hay += " " + JSON.stringify(e.data).toLowerCase();
        } catch {
          /* skip */
        }
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [rawLogs, query, eventFilter]);

  const toggle = useCallback((id: string) => {
    setOpenSet((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const expandAll = useCallback(() => {
    setOpenSet(new Set(filtered.map((e) => e.id)));
  }, [filtered]);

  const collapseAll = useCallback(() => setOpenSet(new Set()), []);

  const handleCopyAll = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(formatRawLogsAsText(filtered));
      toast.success(`${filtered.length}건의 원본 이벤트 복사`);
    } catch {
      toast.error("복사 실패");
    }
  }, [filtered, toast]);

  const handleDownload = useCallback(() => {
    const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    downloadText(`raw-logs-${ts}.txt`, formatRawLogsAsText(filtered));
    toast.info("원본 로그 다운로드 완료");
  }, [filtered, toast]);

  return (
    <div className="flex flex-col gap-3">
      {/* Toolbar */}
      <div className="card card-padded-sm flex flex-wrap items-center gap-2">
        <span className="badge badge-neutral">{rawLogs.length}건</span>
        {filtered.length !== rawLogs.length && (
          <span className="badge badge-accent">필터 {filtered.length}</span>
        )}
        <span className="badge badge-outline">펼침 {openSet.size}</span>

        <select
          className="input-field input-sm"
          value={eventFilter}
          onChange={(e) => setEventFilter(e.target.value)}
          aria-label="이벤트 타입 필터"
        >
          <option value="all">전체 이벤트</option>
          {eventOptions.map((ev) => (
            <option key={ev} value={ev}>
              {ev} ({eventCounts[ev] ?? 0})
            </option>
          ))}
        </select>

        <input
          type="text"
          className="input-field input-sm flex-1 min-w-[160px]"
          placeholder="이벤트/JSON 페이로드 검색…"
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
            className="btn-secondary btn-sm"
            onClick={handleCopyAll}
            disabled={filtered.length === 0}
          >
            복사
          </button>
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={handleDownload}
            disabled={filtered.length === 0}
          >
            다운로드
          </button>
        </div>
      </div>

      {/* Body */}
      {rawLogs.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon" aria-hidden="true">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="16 18 22 12 16 6" />
              <polyline points="8 6 2 12 8 18" />
            </svg>
          </div>
          <div className="empty-state-title">아직 원본 SSE 이벤트가 없습니다</div>
          <div className="empty-state-desc">
            평가를 실행하면 서버가 보내는 모든 SSE 이벤트가 여기에 그대로 표시됩니다.
          </div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">검색 결과 없음</div>
          <div className="empty-state-desc">필터 또는 검색어를 조정해 보세요.</div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {filtered.map((entry, i) => (
            <RawLogCard
              key={entry.id}
              entry={entry}
              index={i}
              open={openSet.has(entry.id)}
              onToggle={() => toggle(entry.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default RawLogsPanel;
