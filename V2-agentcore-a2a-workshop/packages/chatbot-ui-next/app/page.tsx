// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { Suspense } from "react";

import { GlobalEffects } from "@/components/GlobalEffects";
import GlobalNodeDrawer from "@/components/GlobalNodeDrawer";
import { MainTabs } from "@/components/MainTabs";

function TabsFallback() {
  return (
    <div className="empty-state">
      <div className="spinner" aria-hidden="true" />
      <div className="empty-state-title">탭 로드 중…</div>
    </div>
  );
}

// AppStateProvider 는 root layout 에 위치 — 페이지 navigation 사이에도 컨텍스트 유지.
export default function DashboardPage() {
  return (
    <div className="flex flex-col gap-10">
      <section>
        <span className="section-eyebrow">QA Pipeline V3</span>
        <h1 className="section-title">상담 QA 솔루션</h1>
        <p className="section-lead">
          실시간 SSE 스트리밍 파이프라인과 AG2 토론 기반 3-페르소나 합의를 한 화면에서
          확인하고, 로그·비교·RAG 관리까지 하나의 흐름으로 이어집니다.
        </p>
        <div className="mt-5 flex items-center gap-2 flex-wrap">
          <span className="badge badge-outline">LangGraph · 4-Layer</span>
          <span className="badge badge-outline">AG2 Debate</span>
          <span className="badge badge-outline">Bedrock Sonnet 4</span>
        </div>
      </section>

      <GlobalEffects />
      {/* useSearchParams 는 Suspense 경계 안에서만 안전 (Next.js 16) */}
      <Suspense fallback={<TabsFallback />}>
        <MainTabs />
      </Suspense>
      {/* 파이프라인 노드 클릭 시 열리는 글로벌 드로어 — /evaluate 와 동일 */}
      <GlobalNodeDrawer />
    </div>
  );
}
