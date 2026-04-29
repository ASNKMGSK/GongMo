// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { useAppStore } from "@/lib/AppStateContext";
import { DEFAULT_TAB, resolveTab, TAB_ORDER, TABS, type TabKey } from "@/lib/tabs";
import { useTabFlash } from "@/lib/useTabFlash";

/* ─────────────────────────────────────────────────────────────
   Lazy-loaded tab panels — ssr:false 로 무거운 컴포넌트 지연 로드.
   각 stub 은 components/tabs/*.tsx 에 존재하고 Dev3/4/5 가 overwrite.
   ───────────────────────────────────────────────────────────── */

function TabLoading({ label }: { label: string }) {
  return (
    <div className="empty-state">
      <div className="spinner" aria-hidden="true" />
      <div className="empty-state-title">{label} 불러오는 중…</div>
    </div>
  );
}

const PipelinePanel = dynamic(
  () => import("./tabs/PipelinePanel").then((m) => m.PipelinePanel),
  { ssr: false, loading: () => <TabLoading label="파이프라인" /> },
);
const ResultsPanel = dynamic(
  () => import("./tabs/ResultsPanel").then((m) => m.ResultsPanel),
  { ssr: false, loading: () => <TabLoading label="평가 결과" /> },
);
const LogsPanel = dynamic(
  () => import("./tabs/LogsPanel").then((m) => m.LogsPanel),
  { ssr: false, loading: () => <TabLoading label="로그" /> },
);
const TracesPanel = dynamic(
  () => import("./tabs/TracesPanel").then((m) => m.TracesPanel),
  { ssr: false, loading: () => <TabLoading label="트레이스" /> },
);
const RawLogsPanel = dynamic(
  () => import("./tabs/RawLogsPanel").then((m) => m.RawLogsPanel),
  { ssr: false, loading: () => <TabLoading label="원본 로그" /> },
);
const ComparePanel = dynamic(
  () => import("./tabs/ComparePanel").then((m) => m.ComparePanel),
  { ssr: false, loading: () => <TabLoading label="비교" /> },
);
const MatrixPanel = dynamic(
  () => import("./tabs/MatrixPanel").then((m) => m.MatrixPanel),
  { ssr: false, loading: () => <TabLoading label="매트릭스" /> },
);
const RagAdminPanel = dynamic(
  () => import("./tabs/RagAdminPanel").then((m) => m.RagAdminPanel),
  { ssr: false, loading: () => <TabLoading label="RAG 관리" /> },
);
const ReviewQueuePanel = dynamic(
  () => import("./tabs/ReviewQueuePanel").then((m) => m.ReviewQueuePanel),
  { ssr: false, loading: () => <TabLoading label="검토 큐" /> },
);

/* ─────────────────────────────────────────────────────────────
   MainTabs — 탭 바 + 활성 패널
   ───────────────────────────────────────────────────────────── */

const FLASH_KEYS: TabKey[] = ["results", "logs", "traces", "rawlogs"];

export function MainTabs() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const rawTab = searchParams?.get("tab") ?? null;
  const activeTab = resolveTab(rawTab);
  const { logs, traces, rawLogs, streamingItems, lastReport } = useAppStore();

  const [flash, triggerFlash] = useTabFlash(FLASH_KEYS);

  // tab-flash — 배경 탭에 이벤트 유입 시 라벨 깜빡
  const prevLogsLen = useRef(0);
  const prevTracesLen = useRef(0);
  const prevRawLen = useRef(0);
  const prevStreamLen = useRef(0);
  const prevGrade = useRef<string | null>(null);

  useEffect(() => {
    if (logs.length > prevLogsLen.current && activeTab !== "logs") triggerFlash("logs");
    prevLogsLen.current = logs.length;
  }, [logs.length, activeTab, triggerFlash]);

  useEffect(() => {
    if (traces.length > prevTracesLen.current && activeTab !== "traces") triggerFlash("traces");
    prevTracesLen.current = traces.length;
  }, [traces.length, activeTab, triggerFlash]);

  useEffect(() => {
    if (rawLogs.length > prevRawLen.current && activeTab !== "rawlogs") triggerFlash("rawlogs");
    prevRawLen.current = rawLogs.length;
  }, [rawLogs.length, activeTab, triggerFlash]);

  useEffect(() => {
    if (streamingItems.length > prevStreamLen.current && activeTab !== "results") {
      triggerFlash("results");
    }
    prevStreamLen.current = streamingItems.length;
  }, [streamingItems.length, activeTab, triggerFlash]);

  useEffect(() => {
    const g =
      lastReport?.final_score?.grade ||
      (lastReport as { summary?: { grade?: string } } | null)?.summary?.grade ||
      null;
    if (g && prevGrade.current !== g && activeTab !== "results") triggerFlash("results");
    prevGrade.current = g;
  }, [lastReport, activeTab, triggerFlash]);

  // raw alias 가 들어오면 canonical 로 URL 교체
  useEffect(() => {
    if (rawTab && rawTab !== activeTab) {
      const sp = new URLSearchParams(searchParams?.toString() || "");
      sp.set("tab", activeTab);
      router.replace(`/evaluate?${sp.toString()}`, { scroll: false });
    }
  }, [rawTab, activeTab, router, searchParams]);

  const tabBadge = useCallback(
    (key: TabKey): ReactNode => {
      if (key === "logs" && logs.length > 0)
        return <span className="tab-btn-count">{logs.length}</span>;
      if (key === "traces" && traces.length > 0)
        return <span className="tab-btn-count">{traces.length}</span>;
      if (key === "rawlogs" && rawLogs.length > 0)
        return <span className="tab-btn-count">{rawLogs.length}</span>;
      if (key === "results") {
        // backend 가 final_score.grade 또는 summary.grade 둘 중 하나에 채움 — 둘 다 fallback
        const grade =
          lastReport?.final_score?.grade ||
          (lastReport as { summary?: { grade?: string } } | null)?.summary?.grade;
        if (grade) return <span className="tab-btn-count">{grade}</span>;
        if (streamingItems.length > 0)
          return <span className="tab-btn-count">{streamingItems.length}/18</span>;
      }
      return null;
    },
    [logs.length, traces.length, rawLogs.length, streamingItems.length, lastReport],
  );

  // Pipeline 탭은 SSE 연결을 유지해야 하므로 탭 전환 시 unmount 되면 안 됨.
  // → 한번 mount 된 탭은 display:none 으로 keep-alive.
  // sessionStorage 로 영속화 — MainTabs 가 remount 되어도 (페이지 navigation 등) 이전에 본 탭은
  // 다시 mount 된 상태로 복원되어 EvaluateRunner 등의 로컬 state 가 초기화되지 않음.
  //
  // ⚠ SSR/CSR hydration mismatch 방지: 서버에는 sessionStorage 가 없으므로 초기 state 는
  //    `Set([activeTab])` 로 동일하게 두고, 마운트 후 effect 에서 sessionStorage 복원 → setState.
  const MOUNTED_TABS_KEY = "qa.mountedTabs";
  const [mountedTabs, setMountedTabs] = useState<Set<TabKey>>(() => new Set([activeTab]));

  // 마운트 직후 sessionStorage 에서 복원 (한 번만)
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;
    if (typeof window === "undefined") return;
    try {
      const raw = sessionStorage.getItem(MOUNTED_TABS_KEY);
      if (!raw) return;
      const arr = JSON.parse(raw) as string[];
      const restored = arr.filter((k): k is TabKey => k in TABS);
      if (restored.length === 0) return;
      setMountedTabs((prev) => {
        const next = new Set(prev);
        for (const k of restored) next.add(k);
        return next;
      });
    } catch {
      /* ignore */
    }
  }, []);

  // mountedTabs 가 바뀔 때마다 sessionStorage 동기화
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      sessionStorage.setItem(MOUNTED_TABS_KEY, JSON.stringify(Array.from(mountedTabs)));
    } catch {
      /* quota / privacy mode 무시 */
    }
  }, [mountedTabs]);
  // activeTab 이 바뀌면 자동으로 mountedTabs 에 추가 (Link onClick 의존하지 않음 — back/forward 등 대비)
  useEffect(() => {
    setMountedTabs((prev) => {
      if (prev.has(activeTab)) return prev;
      const next = new Set(prev);
      next.add(activeTab);
      return next;
    });
  }, [activeTab]);
  const handleTabClick = useCallback((key: TabKey) => {
    setMountedTabs((prev) => {
      if (prev.has(key)) return prev;
      const next = new Set(prev);
      next.add(key);
      return next;
    });
  }, []);

  const panels: Array<{ key: TabKey; node: ReactNode }> = [
    { key: "pipeline", node: <PipelinePanel /> },
    { key: "results", node: <ResultsPanel /> },
    { key: "logs", node: <LogsPanel /> },
    { key: "traces", node: <TracesPanel /> },
    { key: "rawlogs", node: <RawLogsPanel /> },
    { key: "compare", node: <ComparePanel /> },
    { key: "matrix", node: <MatrixPanel /> },
    { key: "rag-admin", node: <RagAdminPanel /> },
    { key: "review-queue", node: <ReviewQueuePanel /> },
  ];

  return (
    <div className="flex flex-col gap-4">
      {/* 탭 스트립 — Anthropic Docs / pricing 톤. Dev1 권고: 좌우 여백 20px+. */}
      <nav
        role="tablist"
        aria-label="메인 탭"
        className="flex items-center gap-1 border-b border-[var(--border)] px-5 overflow-x-auto"
        data-testid="main-tabs"
      >
        {TAB_ORDER.map((key) => {
          const def = TABS[key];
          const active = key === activeTab;
          const isFlashing = FLASH_KEYS.includes(key) && flash[key];
          const href = key === DEFAULT_TAB ? "/evaluate" : `/evaluate?tab=${key}`;
          return (
            <Link
              key={key}
              href={href}
              role="tab"
              aria-selected={active}
              data-testid={def.testId}
              onClick={() => handleTabClick(key)}
              className={`tab-btn ${active ? "active" : ""} ${
                isFlashing ? "animate-pulse text-[var(--accent)]" : ""
              }`}
              scroll={false}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              {def.icon && (
                <svg
                  width={14}
                  height={14}
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                  style={{ flexShrink: 0, display: "inline-block" }}
                >
                  <path d={def.icon} />
                </svg>
              )}
              <span>{def.label}</span>
              {tabBadge(key)}
            </Link>
          );
        })}
      </nav>

      {/* 활성 패널 — 한번 mount 된 탭은 display:none 으로 유지 (SSE/폼 보존) */}
      {panels.map(({ key, node }) => {
        const isActive = key === activeTab;
        const wasMounted = mountedTabs.has(key);
        if (!wasMounted && !isActive) return null;
        return (
          <div
            key={key}
            role="tabpanel"
            data-testid={`panel-${key}`}
            hidden={!isActive}
            style={isActive ? undefined : { display: "none" }}
            className={isActive ? "min-h-[360px]" : undefined}
          >
            {node}
          </div>
        );
      })}
    </div>
  );
}

export default MainTabs;
