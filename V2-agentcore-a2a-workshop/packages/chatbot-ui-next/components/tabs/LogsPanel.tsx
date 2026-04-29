// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAppState, type AppLogEntry, type LogLevel } from "@/lib/AppStateContext";
import { useToast } from "@/lib/toast";

type LevelFilter = "all" | LogLevel;

const LEVEL_LABELS: Record<LevelFilter, string> = {
  all: "전체",
  info: "info",
  success: "success",
  warn: "warn",
  error: "error",
};

const LEVEL_BAR_CLASS: Record<LogLevel, string> = {
  info: "bg-[var(--info)]",
  success: "bg-[var(--success)]",
  warn: "bg-[var(--warn)]",
  error: "bg-[var(--danger)]",
};

const LEVEL_TEXT_CLASS: Record<LogLevel, string> = {
  info: "text-[var(--info)]",
  success: "text-[var(--success)]",
  warn: "text-[var(--warn)]",
  error: "text-[var(--danger)]",
};

function formatLogsAsText(entries: AppLogEntry[]): string {
  return entries
    .map((l) => `[${l.time}] ${l.type.toUpperCase().padEnd(7)} ${l.msg}`)
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

export function LogsPanel() {
  const { state, dispatch } = useAppState();
  const toast = useToast();
  const { logs } = state;

  const [levelFilter, setLevelFilter] = useState<LevelFilter>("all");
  const [query, setQuery] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return logs.filter((l) => {
      if (levelFilter !== "all" && l.type !== levelFilter) return false;
      if (q && !l.msg.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [logs, levelFilter, query]);

  const levelCounts = useMemo(() => {
    const counts: Record<LogLevel, number> = { info: 0, success: 0, warn: 0, error: 0 };
    logs.forEach((l) => {
      counts[l.type] = (counts[l.type] ?? 0) + 1;
    });
    return counts;
  }, [logs]);

  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [filtered.length, autoScroll]);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(formatLogsAsText(filtered));
      toast.success(`${filtered.length}건의 로그 복사`);
    } catch {
      toast.error("클립보드 접근 실패");
    }
  }, [filtered, toast]);

  const handleDownload = useCallback(() => {
    const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    downloadText(`logs-${ts}.txt`, formatLogsAsText(filtered));
    toast.info("로그 다운로드 완료");
  }, [filtered, toast]);

  const handleClear = useCallback(() => {
    if (logs.length === 0) return;
    if (!window.confirm(`${logs.length}건의 로그를 비울까요?`)) return;
    dispatch({ type: "CLEAR_LOGS" });
    toast.info("로그를 비웠습니다");
  }, [logs.length, dispatch, toast]);

  return (
    <div className="flex flex-col gap-3">
      {/* Toolbar */}
      <div className="card card-padded-sm flex flex-wrap items-center gap-2">
        <span className="badge badge-neutral">{logs.length}건</span>
        {filtered.length !== logs.length && (
          <span className="badge badge-accent">필터 {filtered.length}</span>
        )}

        <select
          className="input-field input-sm"
          value={levelFilter}
          onChange={(e) => setLevelFilter(e.target.value as LevelFilter)}
          aria-label="레벨 필터"
        >
          {(["all", "info", "success", "warn", "error"] as LevelFilter[]).map((lv) => (
            <option key={lv} value={lv}>
              {LEVEL_LABELS[lv]}
              {lv !== "all" ? ` (${levelCounts[lv as LogLevel]})` : ""}
            </option>
          ))}
        </select>

        <input
          type="text"
          className="input-field input-sm flex-1 min-w-[160px]"
          placeholder="로그 메시지 검색…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />

        <label className="inline-flex items-center gap-2 text-[11.5px] text-[var(--ink-muted)]">
          <span className="switch" aria-label="자동 스크롤 토글">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
            />
            <span className="switch-slider" aria-hidden="true" />
          </span>
          자동 스크롤
        </label>

        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={handleCopy}
            disabled={filtered.length === 0}
            title="필터된 로그를 클립보드에 복사"
          >
            복사
          </button>
          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={handleDownload}
            disabled={filtered.length === 0}
            title=".txt 로 다운로드"
          >
            다운로드
          </button>
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={handleClear}
            disabled={logs.length === 0}
            title="모든 로그 비우기"
          >
            비우기
          </button>
        </div>
      </div>

      {/* Logs body */}
      {logs.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon" aria-hidden="true">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="9" y1="13" x2="15" y2="13" />
              <line x1="9" y1="17" x2="15" y2="17" />
            </svg>
          </div>
          <div className="empty-state-title">아직 로그가 없습니다</div>
          <div className="empty-state-desc">평가를 실행하면 이벤트 로그가 여기에 표시됩니다.</div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">검색 결과 없음</div>
          <div className="empty-state-desc">레벨 또는 검색어를 조정해 보세요.</div>
        </div>
      ) : (
        <div className="rounded-[var(--radius-sm)] bg-[var(--surface-muted)] p-3 font-mono text-[11.5px] leading-relaxed max-h-[640px] overflow-y-auto border border-[var(--border)]">
          {filtered.map((l, i) => (
            <div
              key={i}
              className="flex items-start gap-2 py-0.5 border-l-2 pl-2 my-0.5"
              style={{ borderColor: "transparent" }}
            >
              <span
                aria-hidden="true"
                className={`mt-1 inline-block h-3 w-0.5 rounded-full flex-shrink-0 ${LEVEL_BAR_CLASS[l.type]}`}
              />
              <span className="text-[var(--ink-subtle)] flex-shrink-0 tabular-nums">[{l.time}]</span>
              <span className="text-[10px] uppercase tracking-wide font-semibold text-[var(--ink-muted)] flex-shrink-0 w-14 mt-[2px]">
                {l.type}
              </span>
              <span className={`${LEVEL_TEXT_CLASS[l.type]} break-words`}>{l.msg}</span>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  );
}

export default LogsPanel;
