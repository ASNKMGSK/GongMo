// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect } from "react";

import NodeDrawer from "@/components/results/NodeDrawer";
import { useAppState } from "@/lib/AppStateContext";

/**
 * GlobalNodeDrawer — AppStateContext.openNodeId 가 세팅되면 어느 탭에서든 NodeDrawer 가 열림.
 * Pipeline 탭에서 PipelineFlow.onNodeClick 으로 열어도, Results 탭 전환 후에도 유지.
 */
export default function GlobalNodeDrawer() {
  const { state, setOpenNode } = useAppState();
  const close = useCallback(() => setOpenNode(null), [setOpenNode]);

  // openNodeId 가 열렸을 때 body scroll 잠금
  useEffect(() => {
    if (state.openNodeId) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = prev;
      };
    }
  }, [state.openNodeId]);

  return (
    <NodeDrawer
      nodeId={state.openNodeId}
      result={state.lastResult}
      nodeStates={state.nodeStates}
      nodeTimings={state.nodeTimings}
      nodeScores={state.nodeScores}
      onClose={close}
    />
  );
}
