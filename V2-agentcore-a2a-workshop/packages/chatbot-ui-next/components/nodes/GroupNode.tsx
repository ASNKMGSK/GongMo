// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { type NodeProps } from "@xyflow/react";
import { memo } from "react";

import type { GroupDef } from "@/lib/pipeline";

export interface GroupNodeData extends Record<string, unknown> {
  group: GroupDef;
  width: number;
  height: number;
  /** 모든 자식 노드가 disabled 면 그룹 박스도 회색 처리 */
  disabled?: boolean;
}

/* ── Accent palette — modern muted saturation ──
   각 Layer 에 고유한 hue 를 부여해 파이프라인 흐름이 시각적으로 읽힘.
   bg: 10% 불투명 tinted 배경 (glassmorphism 느낌)
   border: 1.5px 주 강조
   labelBg: 라벨 pill 배경 — 같은 hue 진한 버전
*/
const ACCENT_TONES: Record<
  GroupDef["accent"],
  { bg: string; border: string; label: string; labelBg: string; grad: string }
> = {
  layer1: {
    bg: "rgba(233, 239, 248, 0.4)",
    border: "#c8d4e8",
    label: "#2d4a73",
    labelBg: "#dfe8f5",
    grad: "linear-gradient(135deg, rgba(200,212,232,0.35) 0%, rgba(200,212,232,0) 60%)",
  },
  layer2a: {
    bg: "rgba(249, 241, 231, 0.45)",
    border: "#e8d4b2",
    label: "#8c6529",
    labelBg: "#f5e7cf",
    grad: "linear-gradient(135deg, rgba(232,212,178,0.4) 0%, rgba(232,212,178,0) 60%)",
  },
  layer2b: {
    bg: "rgba(250, 241, 236, 0.45)",
    border: "#e8cec0",
    label: "#8c503d",
    labelBg: "#f5dfd2",
    grad: "linear-gradient(135deg, rgba(232,206,192,0.4) 0%, rgba(232,206,192,0) 60%)",
  },
  layer3: {
    bg: "rgba(234, 245, 238, 0.45)",
    border: "#bfdec9",
    label: "#2e6b42",
    labelBg: "#d4ebdd",
    grad: "linear-gradient(135deg, rgba(191,222,201,0.4) 0%, rgba(191,222,201,0) 60%)",
  },
  layer4: {
    bg: "rgba(245, 238, 249, 0.45)",
    border: "#d6c3e0",
    label: "#5d3c7d",
    labelBg: "#e7d8ee",
    grad: "linear-gradient(135deg, rgba(214,195,224,0.4) 0%, rgba(214,195,224,0) 60%)",
  },
};

function GroupNodeImpl({ data }: NodeProps) {
  const d = data as GroupNodeData;
  const { group, width, height, disabled } = d;
  const tone = ACCENT_TONES[group.accent];

  return (
    <div
      title={disabled ? "현재 비활성 (KSQI 평가는 일시 비활성 상태)" : undefined}
      style={{
        width,
        height,
        background: tone.bg,
        backgroundImage: tone.grad,
        border: `1.5px solid ${tone.border}`,
        borderRadius: 20,
        position: "relative",
        pointerEvents: "none",
        boxShadow: `inset 0 1px 0 rgba(255,255,255,0.6), 0 1px 2px rgba(0,0,0,0.02)`,
        opacity: disabled ? 0.45 : 1,
        filter: disabled ? "grayscale(0.7)" : undefined,
        transition: "opacity 0.3s ease, filter 0.3s ease",
      }}
    >
      {/* Label pill — top-left 오버랩, 그룹 accent hue */}
      <div
        style={{
          position: "absolute",
          top: -13,
          left: 20,
          padding: "4px 12px",
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: tone.label,
          background: tone.labelBg,
          borderRadius: 7,
          boxShadow: "0 0 0 1px rgba(0,0,0,0.05), 0 1px 2px rgba(0,0,0,0.04)",
          pointerEvents: "none",
          whiteSpace: "nowrap",
          fontFamily:
            "var(--font-sans), -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        }}
      >
        <span>{group.label}</span>
        {group.sublabel && (
          <>
            <span
              style={{
                width: 1,
                height: 11,
                background: "currentColor",
                opacity: 0.3,
              }}
              aria-hidden="true"
            />
            <span
              style={{
                fontWeight: 500,
                opacity: 0.85,
                textTransform: "none",
                letterSpacing: "0.02em",
                fontSize: 10.5,
              }}
            >
              {group.sublabel}
            </span>
          </>
        )}
      </div>
    </div>
  );
}

/** Custom comparator — PipelineFlow 가 매 SSE 이벤트마다 data 객체 새로 생성. */
function groupNodePropsEqual(prev: NodeProps, next: NodeProps): boolean {
  const a = prev.data as GroupNodeData;
  const b = next.data as GroupNodeData;
  return (
    a.group === b.group &&
    a.width === b.width &&
    a.height === b.height &&
    a.disabled === b.disabled &&
    prev.selected === next.selected
  );
}

export const GroupNode = memo(GroupNodeImpl, groupNodePropsEqual);
