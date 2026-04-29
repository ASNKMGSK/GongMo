// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import ResultsTab from "@/components/results/ResultsTab";

/**
 * ResultsPanel — 평가 결과 탭 (Task #5).
 * AppStateContext.lastResult 에서 report 를 읽어 8 AgentGroupCard / 18 ItemCard 로 렌더링.
 * GT 비교, 검증 이슈, NodeDrawer 포함.
 */
export function ResultsPanel() {
  return <ResultsTab />;
}

export default ResultsPanel;
