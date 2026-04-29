// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { Suspense } from "react";

import { GlobalEffects } from "@/components/GlobalEffects";
import GlobalNodeDrawer from "@/components/GlobalNodeDrawer";
import { MainTabs } from "@/components/MainTabs";

export const metadata = {
  title: "평가 · QA Pipeline V3",
};

function TabsFallback() {
  return (
    <div className="empty-state">
      <div className="spinner" aria-hidden="true" />
      <div className="empty-state-title">탭 로드 중…</div>
    </div>
  );
}

export default function EvaluatePage() {
  // AppStateProvider 는 root layout 에 위치 — 페이지 navigation 사이에도 컨텍스트 유지.
  return (
    <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-5">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">파이프라인 대시보드</h2>
        <p className="mt-1 text-sm text-[var(--ink-muted)]">
          QA Pipeline V3 — 4-Layer · LangGraph · AG2 Debate. 9개 탭으로 평가 실행, 결과, 로그,
          비교, RAG 관리까지 모두 관리합니다.
        </p>
      </section>

      <GlobalEffects />
      {/* useSearchParams 는 Suspense 경계 안에서만 안전 (Next.js 16) */}
      <Suspense fallback={<TabsFallback />}>
        <MainTabs />
      </Suspense>
      <GlobalNodeDrawer />
    </div>
  );
}
