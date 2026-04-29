// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { BASE_URL } from "@/lib/api";
import { useToast } from "@/lib/toast";

/* ─────────────────────────────────────────────────────────────
   AppShell — 헤더 (로고 + 상태 + 액션) + 메인 콘텐츠
   네비게이션은 MainTabs 의 탭 바가 담당 (아이콘 + 라벨 — lib/tabs.ts).
   사이드바 제거됨.
   Anthropic Claude Docs 톤 — Warm Cream + Orange accent
   ───────────────────────────────────────────────────────────── */

// ── Theme (light/dark) ──────────────────────────────────────
type Theme = "light" | "dark" | "system";

function useTheme() {
  const [theme, setTheme] = useState<Theme>("system");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    try {
      const stored = localStorage.getItem("qa-theme") as Theme | null;
      if (stored === "light" || stored === "dark") {
        setTheme(stored);
      } else {
        setTheme("system");
      }
    } catch {
      /* noop */
    }
  }, []);

  const apply = useCallback((t: Theme) => {
    setTheme(t);
    try {
      if (t === "system") {
        localStorage.removeItem("qa-theme");
        document.documentElement.removeAttribute("data-theme");
      } else {
        localStorage.setItem("qa-theme", t);
        document.documentElement.setAttribute("data-theme", t);
      }
    } catch {
      /* noop */
    }
  }, []);

  const toggle = useCallback(() => {
    if (theme === "dark") apply("light");
    else apply("dark");
  }, [theme, apply]);

  return { theme, toggle, mounted };
}

// ── Backend health polling ──────────────────────────────────
type HealthState = "healthy" | "down" | "unknown";

function useBackendHealth() {
  const [status, setStatus] = useState<HealthState>("unknown");
  const [latency, setLatency] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    const check = async () => {
      const t0 = performance.now();
      try {
        const res = await fetch(`${BASE_URL}/health`, {
          method: "GET",
          cache: "no-store",
          signal: AbortSignal.timeout(3500),
        });
        const ms = Math.round(performance.now() - t0);
        if (!alive) return;
        setStatus(res.ok ? "healthy" : "down");
        setLatency(ms);
      } catch {
        if (!alive) return;
        setStatus("down");
        setLatency(null);
      }
    };
    check();
    const id = setInterval(check, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return { status, latency };
}

// ── Backend health state toast bridge — health 변경 시 토스트 알림 ─────
function useHealthToasts(health: HealthState) {
  const toast = useToast();
  const [prev, setPrev] = useState<HealthState>(health);
  useEffect(() => {
    if (prev === health) return;
    // unknown → 확정 상태 전이: 한 번만 안내
    if (prev === "unknown") {
      if (health === "down") {
        toast.warn("백엔드 연결 불가", {
          description: `${BASE_URL}/health 응답 없음 — 서버 기동 확인 필요`,
        });
      }
    } else if (prev === "healthy" && health === "down") {
      toast.error("백엔드 오프라인", {
        description: "진행 중인 작업이 중단될 수 있음",
      });
    } else if (prev === "down" && health === "healthy") {
      toast.success("백엔드 복구됨");
    }
    setPrev(health);
  }, [health, prev, toast]);
}

// ── Keyboard shortcut help modal ───────────────────────────────────────
const SHORTCUTS: Array<{ keys: string; description: string }> = [
  { keys: "?", description: "숏컷 도움말" },
  { keys: "Esc", description: "모달/도움말 닫기" },
  { keys: "Ctrl+K / ⌘K", description: "커맨드 팔레트 (예정)" },
  { keys: "Ctrl+,", description: "설정으로 이동 (예정)" },
  { keys: "G then R", description: "Results 탭으로 이동 (예정)" },
];

function ShortcutHelpModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="키보드 숏컷"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        zIndex: 900,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        animation: "fadeIn 150ms ease",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 420,
          maxWidth: "calc(100vw - 48px)",
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: 12,
          boxShadow: "0 20px 50px rgba(0,0,0,0.2)",
          padding: "20px 22px",
        }}
      >
        <div
          style={{
            fontSize: 15,
            fontWeight: 600,
            color: "var(--ink-display)",
            marginBottom: 14,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span>키보드 숏컷</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            style={{
              background: "transparent",
              border: "none",
              color: "var(--ink-subtle)",
              fontSize: 18,
              cursor: "pointer",
              width: 24,
              height: 24,
            }}
          >
            ×
          </button>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {SHORTCUTS.map((s) => (
            <div
              key={s.keys}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "8px 0",
                borderBottom: "1px dashed var(--border)",
              }}
            >
              <span style={{ fontSize: 12.5, color: "var(--ink-subtle)" }}>{s.description}</span>
              <kbd
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  padding: "3px 8px",
                  background: "var(--surface-muted)",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  color: "var(--ink-display)",
                }}
              >
                {s.keys}
              </kbd>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { theme, toggle, mounted } = useTheme();
  const { status: health, latency } = useBackendHealth();
  const [showShortcutHelp, setShowShortcutHelp] = useState(false);

  useHealthToasts(health);

  // `?` 키 = 숏컷 도움말 토글, ESC = 닫기. input/textarea/select 에선 무시.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      const inField = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
      if (inField) return;
      if (e.key === "?" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        setShowShortcutHelp((v) => !v);
      } else if (e.key === "Escape" && showShortcutHelp) {
        setShowShortcutHelp(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showShortcutHelp]);

  const healthClass =
    health === "healthy"
      ? "status-chip-healthy"
      : health === "down"
        ? "status-chip-down"
        : "status-chip-unknown";

  const healthLabel =
    health === "healthy"
      ? `백엔드 정상${latency != null ? ` · ${latency}ms` : ""}`
      : health === "down"
        ? "백엔드 오프라인"
        : "백엔드 확인 중";

  const healthTitle = useMemo(() => `GET ${BASE_URL}/health → ${health}`, [health]);

  return (
    <div className="flex min-h-screen flex-col">
      {/* ── Header (logo + horizontal nav + status/actions) ── */}
      <header className="sticky top-0 z-20 border-b border-[var(--border)] bg-[var(--surface)]/95 backdrop-blur">
        <div className="flex h-14 items-center gap-4 px-5 md:px-6">
          {/* Logo — 클릭 시 홈으로 */}
          <Link
            href="/"
            className="flex items-center gap-2.5 shrink-0 rounded-md px-1 py-0.5 -mx-1 transition-colors hover:bg-[var(--surface-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-ring)] no-underline"
            style={{ textDecoration: "none", color: "inherit" }}
            aria-label="홈으로 이동"
            title="홈으로"
          >
            <span
              className="inline-flex h-7 w-7 items-center justify-center rounded-md font-bold text-white"
              style={{
                background: "var(--ink-cta)",
                fontSize: 11,
                letterSpacing: "-0.02em",
              }}
            >
              QA
            </span>
            <div className="flex flex-col leading-tight">
              <span className="text-[14.5px] font-semibold tracking-[-0.01em] text-[var(--ink-display)]">
                QA Pipeline <span className="text-[var(--accent)]">V3</span>
              </span>
              <span className="text-[10.5px] text-[var(--ink-subtle)] font-medium tracking-wide hidden sm:block">
                LangGraph · AG2 Debate · Claude
              </span>
            </div>
          </Link>

          {/* 가로 네비는 탭 바 (MainTabs) 가 담당 — 헤더에서는 제거. 가운데 빈 공간 확보. */}
          <div className="flex-1" />


          {/* Right: status + actions */}
          <div className="ml-auto flex items-center gap-1.5 shrink-0">
            <span
              className={`status-chip hidden md:inline-flex ${healthClass}`}
              title={healthTitle}
              aria-live="polite"
            >
              <span className="pulse-dot" />
              {healthLabel}
            </span>
            <a
              href="https://platform.claude.com/docs"
              target="_blank"
              rel="noreferrer"
              className="btn-ghost hidden md:inline-flex"
              title="Claude Docs"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <circle cx="12" cy="12" r="10" />
                <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
                <line x1="12" y1="17" x2="12.01" y2="17" />
              </svg>
              도움말
            </a>
            <button
              type="button"
              onClick={toggle}
              className="btn-ghost"
              aria-label={theme === "dark" ? "라이트 모드로" : "다크 모드로"}
              title={theme === "dark" ? "라이트 모드로" : "다크 모드로"}
            >
              {!mounted || theme !== "dark" ? (
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
                </svg>
              ) : (
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="12" cy="12" r="4" />
                  <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
                </svg>
              )}
            </button>
          </div>
        </div>

      </header>

      {/* ── Main (사이드바 제거 — 전체 폭 사용) ──────────── */}
      <main className="flex-1 min-w-0 animate-fade-in">
        <div className="container-app py-8 md:py-12">{children}</div>
      </main>

      {/* 전역 오버레이 레이어 — ToastProvider 는 layout.tsx 에서 이미 마운트됨 */}
      <ShortcutHelpModal open={showShortcutHelp} onClose={() => setShowShortcutHelp(false)} />
    </div>
  );
}

export default AppShell;
