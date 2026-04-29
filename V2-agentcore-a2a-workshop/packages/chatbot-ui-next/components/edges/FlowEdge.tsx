// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import {
  getSmoothStepPath,
  Position,
  type EdgeProps,
} from "@xyflow/react";
import { memo, useMemo } from "react";

import { EdgeTraveler } from "./EdgeTraveler";

/**
 * FlowEdge — 일반 smoothstep edge + progressive line-draw 애니메이션.
 *
 * 동작:
 *   - `animated` false → true 전이 시 line-draw 애니메이션 재생
 *     (`pathLength="1"` + `stroke-dashoffset` 1 → 0).
 *     source 에서 target 쪽으로 선이 뻗어나가는 효과.
 *   - 재생 완료 후엔 solid 선 유지 (점선 깜빡이지 않음).
 *   - 재활성화 시 key 변경으로 path remount → 애니메이션 replay.
 */
interface FlowEdgeStyle {
  stroke?: string;
  strokeWidth?: number | string;
  strokeDasharray?: string;
  opacity?: number | string;
  [k: string]: unknown;
}

function FlowEdgeImpl(props: EdgeProps) {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    markerEnd,
    style,
    animated,
    pathOptions,
  } = props;
  const s = (style || {}) as FlowEdgeStyle;

  const [path] = useMemo(() => {
    const opts = (pathOptions as
      | { borderRadius?: number; offset?: number }
      | undefined) || {};
    return getSmoothStepPath({
      sourceX,
      sourceY,
      targetX,
      targetY,
      sourcePosition: sourcePosition ?? Position.Right,
      targetPosition: targetPosition ?? Position.Left,
      borderRadius: opts.borderRadius,
      offset: opts.offset,
    });
  }, [sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, pathOptions]);

  const stroke = (s.stroke as string) ?? "#9a9583";
  const strokeWidth = Number(s.strokeWidth) || 1.6;
  const opacity = Number(s.opacity) || 1;
  const userDash = s.strokeDasharray as string | undefined;

  // edge id 기반 stagger — 같은 id 는 항상 같은 delay 반환 (8개 엣지 동시 활성화 시 분산).
  const staggerDelay = useMemo(() => {
    let h = 0;
    const s = String(id);
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return (h % 1000) / 1000; // 0 ~ 1초 사이
  }, [id]);

  return (
    <g id={id}>
      <path
        d={path}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeDasharray={userDash || undefined}
        style={{
          opacity: animated ? opacity * 0.55 : opacity,
          transition: "stroke 0.3s ease, stroke-width 0.3s ease",
        }}
        markerEnd={markerEnd as string | undefined}
      />
      {animated && (
        // JS rAF 기반 traveler — SMIL 제거. React re-render 와 완전 분리 → 깜빡임/정지 없음.
        <EdgeTraveler
          path={path}
          stroke={stroke}
          duration={2200}
          phaseOffset={staggerDelay}
        />
      )}
    </g>
  );
}

export const FlowEdge = memo(FlowEdgeImpl);
