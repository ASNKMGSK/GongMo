// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEscapeClosesDrawer } from "@/lib/useGlobalEffects";

/**
 * GlobalEffects — AppStateProvider 내부에서 한번만 mount.
 * 전역 단축키, polling 같은 사이드이펙트를 연결.
 *
 * V2 원본의 App 함수에 산재한 useEffect 중 탭별 패널로 이관하기 어려운
 * (= 앱 레벨로 존재해야 하는) 훅들을 모아 실행.
 */
export function GlobalEffects() {
  useEscapeClosesDrawer();
  // 자동저장은 Dev5 가 ResultsPanel 에서 exportResultsToXlsx 제공 후 활성화.
  return null;
}

export default GlobalEffects;
