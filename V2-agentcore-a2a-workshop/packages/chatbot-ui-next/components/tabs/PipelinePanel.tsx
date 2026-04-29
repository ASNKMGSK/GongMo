// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { EvaluateRunner } from "@/components/EvaluateRunner";

/**
 * PipelinePanel — 파이프라인 탭 본문.
 *
 * MainTabs 가 활성 탭일 때만 mount 하므로 EvaluateRunner 내부 state 유실 방지를 위해
 * unmount 시에도 AppStateContext 로 결과를 미리 sync 시켜둘 것 (Dev5 에서 bridge 추가 예정).
 *
 * 현재: 기존 EvaluateRunner 를 그대로 노출. 탭 전환 시 EvaluateRunner 의 로컬 state 는
 * 유지되지 않지만, 평가 결과는 Context 로 mirror 되어 Results 탭 등에서 읽을 수 있음.
 */
export function PipelinePanel() {
  return (
    <div className="flex flex-col gap-4">
      <EvaluateRunner />
    </div>
  );
}

export default PipelinePanel;
