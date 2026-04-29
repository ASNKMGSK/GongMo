// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { type EdgeProps } from "@xyflow/react";
import { memo, useMemo } from "react";

import { EdgeTraveler } from "./EdgeTraveler";

/**
 * BusEdge — V2 fan-out/fan-in 트렁크 라우팅.
 *
 * animated 토글 시 FlowEdge 와 동일한 progressive line-draw 재생.
 */
const TRUNK_OFFSET = 45;
const BEND_RADIUS = 10;

interface BusEdgeData extends Record<string, unknown> {
  busIn?: boolean;
  /** 공통 trunk X 좌표 — 모든 fan-out/fan-in 이 동일한 trunk 공유 (없으면 source/target+offset). */
  trunkX?: number;
}

interface BusEdgeStyle {
  stroke?: string;
  strokeWidth?: number | string;
  strokeDasharray?: string;
  opacity?: number | string;
  [k: string]: unknown;
}

function BusEdgeImpl(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY, markerEnd, style, data, animated } =
    props;
  const d = data as BusEdgeData | undefined;
  const busIn = !!d?.busIn;
  const sharedTrunkX = typeof d?.trunkX === "number" ? d.trunkX : undefined;
  const s = (style || {}) as BusEdgeStyle;

  const path = useMemo(() => {
    // 외부 trunkX 주입 시 모든 fan-out/fan-in 이 동일한 trunk 공유 — 신한 dept 노드도
    // base 8 sub-agent 와 같은 trunk 라인에 정렬됨.
    const trunkX =
      sharedTrunkX !== undefined
        ? sharedTrunkX
        : busIn
          ? targetX - TRUNK_OFFSET
          : sourceX + TRUNK_OFFSET;
    const dy = targetY - sourceY;
    if (Math.abs(dy) < 1) {
      return `M ${sourceX},${sourceY} L ${trunkX},${sourceY} L ${targetX},${targetY}`;
    }
    // segRoom 이 충분하지 않으면 곡선 빼고 직각 path 로 fallback — path 끊김 방지
    const segRoomBefore = busIn ? trunkX - sourceX : trunkX - sourceX;
    const segRoomAfter = busIn ? targetX - trunkX : targetX - trunkX;
    const segMin = Math.min(Math.abs(segRoomBefore), Math.abs(segRoomAfter));
    const r = Math.min(BEND_RADIUS, Math.abs(dy) / 2, segMin / 2);
    if (r < 1) {
      // r 너무 작으면 degenerate arc → 순수 직각 (수평-수직-수평) path
      return [
        `M ${sourceX},${sourceY}`,
        `L ${trunkX},${sourceY}`,
        `L ${trunkX},${targetY}`,
        `L ${targetX},${targetY}`,
      ].join(" ");
    }
    const dir = dy > 0 ? 1 : -1;
    const sweep = dir > 0 ? 1 : 0;
    return [
      `M ${sourceX},${sourceY}`,
      `L ${trunkX - r},${sourceY}`,
      `A ${r},${r} 0 0 ${sweep} ${trunkX},${sourceY + r * dir}`,
      `L ${trunkX},${targetY - r * dir}`,
      `A ${r},${r} 0 0 ${sweep === 1 ? 0 : 1} ${trunkX + r},${targetY}`,
      `L ${targetX},${targetY}`,
    ].join(" ");
  }, [sourceX, sourceY, targetX, targetY, busIn, sharedTrunkX]);

  const stroke = (s.stroke as string) ?? "#9a9583";
  const strokeWidth = Number(s.strokeWidth) || 1.6;
  const opacity = Number(s.opacity) || 1;
  const userDash = s.strokeDasharray as string | undefined;

  const staggerDelay = useMemo(() => {
    let h = 0;
    const s = String(id);
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return (h % 1000) / 1000;
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

export const BusEdge = memo(BusEdgeImpl);
