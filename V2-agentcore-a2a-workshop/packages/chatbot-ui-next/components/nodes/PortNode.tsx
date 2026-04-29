// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { Handle, Position } from "@xyflow/react";
import { memo } from "react";

/**
 * PortNode — 그룹 경계 / Y-merge junction 용 보이지 않는 점.
 *
 * 1×1 div 에 좌/우/상/하 source+target 핸들 모두 부착. ReactFlow edge 가
 * 이 노드의 좌표에서 출발/도착하는 시각 효과만 만든다.
 *
 * 사용처:
 *  - input_join_port  : Input STT + Tenant Config 가 합류한 후 layer1 으로 가는 Y-merge 점
 *  - layer2_in_port   : layer1 → Layer 2 그룹의 LEFT 경계
 *  - layer2_out_port  : Layer 2 그룹의 RIGHT 경계 → layer3
 */
function PortNodeImpl() {
  return (
    <div
      style={{
        // 8×8 — 핸들이 안정된 좌표 갖도록 미세 크기 부여. 시각적으로는 보이지 않음.
        width: 8,
        height: 8,
        background: "transparent",
        border: "none",
        position: "relative",
        pointerEvents: "none",
      }}
    >
      <Handle id="left" type="target" position={Position.Left} style={{ opacity: 0, width: 1, height: 1, minWidth: 1, minHeight: 1 }} />
      <Handle id="left-source" type="source" position={Position.Left} style={{ opacity: 0, width: 1, height: 1, minWidth: 1, minHeight: 1 }} />
      <Handle id="right" type="source" position={Position.Right} style={{ opacity: 0, width: 1, height: 1, minWidth: 1, minHeight: 1 }} />
      <Handle id="right-target" type="target" position={Position.Right} style={{ opacity: 0, width: 1, height: 1, minWidth: 1, minHeight: 1 }} />
      <Handle id="top" type="target" position={Position.Top} style={{ opacity: 0, width: 1, height: 1, minWidth: 1, minHeight: 1 }} />
      <Handle id="top-source" type="source" position={Position.Top} style={{ opacity: 0, width: 1, height: 1, minWidth: 1, minHeight: 1 }} />
      <Handle id="bottom" type="target" position={Position.Bottom} style={{ opacity: 0, width: 1, height: 1, minWidth: 1, minHeight: 1 }} />
      <Handle id="bottom-source" type="source" position={Position.Bottom} style={{ opacity: 0, width: 1, height: 1, minWidth: 1, minHeight: 1 }} />
    </div>
  );
}

export const PortNode = memo(PortNodeImpl);
