// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { memo, useEffect, useRef, useState } from "react";

import type { NodeDef, NodeState } from "@/lib/pipeline";
import { useTenantFlashKey } from "@/lib/tenantFlash";

export interface LayerNodeData extends Record<string, unknown> {
  def: NodeDef;
  state: NodeState;
  elapsed?: number;
  /** 평균 LLM confidence (0~1) — layer1/layer3/layer4 agent 노드에도 표시. */
  confidence?: number;
  /** tenant_config 노드 전용 — 현재 site/channel/department + 변경 펄스 트리거. */
  tenantContext?: {
    siteId: string;
    channel: string;
    department: string;
    flashKey: number;
  };
  /** 테넌트 전환으로 새로 추가된 노드 — 1.6초 sparkle 애니메이션 */
  isNewlyAdded?: boolean;
}

/* ── Modern token palette ───────────────────────────────
   cream (#fcfbf8) 베이스 + 따뜻한 dark ink + Anthropic orange accent.
   glass-card: 1px subtle border + soft layered shadow + 14px radius.
*/
const STATE_TOKENS: Record<
  NodeState,
  { dot: string; ring: string; border: string; bg: string; glow: string }
> = {
  pending: {
    dot: "#c3beaf",
    ring: "transparent",
    border: "#ece8d8",
    bg: "#ffffff",
    glow: "none",
  },
  active: {
    dot: "#c96442",
    ring: "rgba(201,100,66,0.14)",
    border: "#c96442",
    bg: "#ffffff",
    glow: "0 0 0 4px rgba(201,100,66,0.12), 0 1px 2px rgba(0,0,0,0.04), 0 8px 24px rgba(201,100,66,0.12)",
  },
  done: {
    dot: "#2e7d4f",
    ring: "transparent",
    border: "#a8d4b9",
    bg: "linear-gradient(180deg, #f0faf4 0%, #e8f5ed 100%)",
    glow: "0 0 0 1px rgba(46,125,79,0.08), 0 2px 8px rgba(46,125,79,0.1)",
  },
  error: {
    dot: "#b03a2e",
    ring: "rgba(176,58,46,0.12)",
    border: "#e7c9c4",
    bg: "#fdf6f4",
    glow: "0 1px 2px rgba(176,58,46,0.08)",
  },
  "gate-failed": {
    dot: "#b03a2e",
    ring: "rgba(176,58,46,0.12)",
    border: "#e7c9c4",
    bg: "#fdf6f4",
    glow: "0 1px 2px rgba(176,58,46,0.08)",
  },
  skipped: {
    dot: "#bfbaa8",
    ring: "transparent",
    border: "#e7e3d4",
    bg: "#f7f5ed",
    glow: "none",
  },
  aborted: {
    dot: "#806328",
    ring: "transparent",
    border: "#e2d5b3",
    bg: "#fbf7e8",
    glow: "0 1px 2px rgba(128,99,40,0.08)",
  },
};

function LayerNodeImpl({ data }: NodeProps) {
  const d = data as LayerNodeData;
  const { def, state, elapsed, confidence, tenantContext, isNewlyAdded } = d;
  const disabled = !!def.disabled;
  const tok = STATE_TOKENS[state] ?? STATE_TOKENS.pending;
  const isData = def.type === "data";
  const isTenantConfig = def.id === "tenant_config";
  const confColor =
    confidence == null
      ? "#9a9583"
      : confidence >= 0.8
        ? "#2e7d4f"
        : confidence >= 0.6
          ? "#c96442"
          : "#b03a2e";

  // tenant_config 노드는 sub 텍스트 자리에 현재 site·channel·department 를 노출.
  const subText =
    isTenantConfig && tenantContext
      ? `${tenantContext.siteId} · ${tenantContext.channel} · ${tenantContext.department}`
      : def.sub;

  // tenant_config 노드 — module-level emitter (tenantFlash) 로 sparkle 발화.
  // ReactFlow data prop 경유가 누락되는 케이스가 있어 별도 채널 사용.
  const flashKey = useTenantFlashKey();
  const [sparkle, setSparkle] = useState(false);
  const lastFlashKeyRef = useRef<number>(flashKey);
  useEffect(() => {
    if (!isTenantConfig) return;
    if (flashKey === lastFlashKeyRef.current) return;
    lastFlashKeyRef.current = flashKey;
    setSparkle(true);
    const t = setTimeout(() => setSparkle(false), 1350);
    return () => clearTimeout(t);
  }, [flashKey, isTenantConfig]);

  return (
    <div
      className={sparkle ? "tenant-config-sparkle" : undefined}
      title={disabled ? "현재 비활성 (테넌트 설정에서 비활성화됨)" : undefined}
      style={{
        width: def.w,
        height: def.h,
        background: isData
          ? "linear-gradient(180deg, #fffdf4 0%, #fbf7e8 100%)"
          : tok.bg,
        border: `1px solid ${isData ? "#e6dbb4" : tok.border}`,
        borderRadius: 14,
        boxShadow: state === "active" ? tok.glow : "0 1px 2px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.03)",
        padding: "12px 16px",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: 3,
        position: "relative",
        opacity: disabled ? 0.45 : 1,
        filter: disabled ? "grayscale(0.7)" : undefined,
        transition: "box-shadow 0.22s cubic-bezier(0.2,0,0,1), border-color 0.22s cubic-bezier(0.2,0,0,1), background 0.22s cubic-bezier(0.2,0,0,1), opacity 0.3s ease",
        fontFamily:
          "var(--font-sans), -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      }}
    >
      {/* tenant_config sparkle — flashKey 변경 시 절대 위치 overlay 가 1.0초 mount.
          노드 본체 inline style (background/border/boxShadow) 와 CSS animation 충돌 회피.
          key 변경 시 remount → 애니메이션 매번 처음부터 재생. */}
      {isTenantConfig && sparkle && (
        <span
          key={flashKey}
          className="tenant-config-sparkle-overlay"
          aria-hidden="true"
        />
      )}

      {/* 신규 추가 노드 sparkle 오버레이 + 배지 (2.4초 후 자동 사라짐) */}
      {isNewlyAdded && (
        <>
          <span className="node-new-overlay" aria-hidden="true" />
          <span className="node-new-badge">✨ 신규</span>
        </>
      )}

      {/* Handles — 4방향 source+target (smoothstep + short-circuit bottom 호환) */}
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
      <Handle id="top" type="target" position={Position.Top} style={{ opacity: 0 }} />
      <Handle id="top" type="source" position={Position.Top} style={{ opacity: 0 }} />
      <Handle id="bottom" type="source" position={Position.Bottom} style={{ opacity: 0 }} />
      <Handle id="bottom" type="target" position={Position.Bottom} style={{ opacity: 0 }} />

      {/* 상태 dot — active 시 pulse halo */}
      <span
        style={{
          position: "absolute",
          top: 10,
          left: 10,
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: tok.dot,
          boxShadow: state === "active" ? `0 0 0 4px ${tok.ring}` : undefined,
          animation: state === "active" ? "nodeActivePulse 1.8s ease-in-out infinite" : undefined,
        }}
        aria-hidden="true"
      />

      {/* Type badge — data/agent 구분 */}
      {isData && state !== "done" && (
        <span
          style={{
            position: "absolute",
            top: 10,
            right: 10,
            fontSize: 9,
            fontWeight: 700,
            color: "#8c7738",
            background: "#faf2d4",
            padding: "2px 6px",
            borderRadius: 4,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
          }}
        >
          INPUT
        </span>
      )}

      {/* ✓ Done 체크마크 — 완료 상태 피드백 */}
      {state === "done" && (
        <span
          style={{
            position: "absolute",
            top: 8,
            right: 8,
            width: 18,
            height: 18,
            borderRadius: "50%",
            background: "#2e7d4f",
            color: "#ffffff",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 11,
            fontWeight: 800,
            boxShadow: "0 1px 3px rgba(46,125,79,0.3)",
          }}
          aria-label="완료"
        >
          ✓
        </span>
      )}

      <div
        style={{
          fontSize: 13,
          fontWeight: 600,
          color: "#14110d",
          letterSpacing: "-0.01em",
          lineHeight: 1.25,
          paddingLeft: 10,
        }}
      >
        {def.label}
      </div>
      {subText && (
        <div
          style={{
            fontSize: 10.5,
            color: isTenantConfig && tenantContext ? "#5a4f33" : "#7a7567",
            lineHeight: 1.35,
            paddingLeft: 10,
            fontFamily: isTenantConfig && tenantContext
              ? "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)"
              : undefined,
            fontWeight: isTenantConfig && tenantContext ? 600 : 400,
          }}
        >
          {subText}
        </div>
      )}
      {elapsed !== undefined && state === "done" && (
        <div
          style={{
            position: "absolute",
            bottom: 6,
            right: 10,
            fontSize: 10,
            color: "#9a9583",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {elapsed.toFixed(1)}s
        </div>
      )}
      {/* LLM Confidence 배지 — 좌하단 (elapsed 와 겹치지 않게). */}
      {confidence != null && (
        <div
          title={`LLM 평균 confidence · ${(confidence * 100).toFixed(1)}%`}
          style={{
            position: "absolute",
            bottom: 6,
            left: 10,
            display: "inline-flex",
            alignItems: "center",
            gap: 3,
            padding: "1px 6px",
            fontSize: 9.5,
            fontWeight: 700,
            letterSpacing: "0.02em",
            color: confColor,
            background: `${confColor}14`,
            border: `1px solid ${confColor}3a`,
            borderRadius: 9999,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          <span style={{ fontSize: 8, opacity: 0.85, fontWeight: 600 }}>conf</span>
          <span>{Math.round(confidence * 100)}%</span>
        </div>
      )}
    </div>
  );
}

/**
 * Custom data comparator — PipelineFlow 가 매 SSE 이벤트마다 data 객체 새로 생성하므로
 * 기본 shallow 비교는 무조건 false. primitive/stable-ref 필드만 비교해 변경된 노드만 re-render.
 */
function layerNodePropsEqual(prev: NodeProps, next: NodeProps): boolean {
  const a = prev.data as LayerNodeData;
  const b = next.data as LayerNodeData;
  // tenantContext 는 객체지만 flashKey 만 변경 시그널이므로 그것만 비교 + siteId/channel/department.
  const tcA = a.tenantContext;
  const tcB = b.tenantContext;
  const tcEqual =
    tcA === tcB ||
    (tcA != null && tcB != null &&
      tcA.siteId === tcB.siteId &&
      tcA.channel === tcB.channel &&
      tcA.department === tcB.department &&
      tcA.flashKey === tcB.flashKey);
  return (
    a.def === b.def &&
    a.state === b.state &&
    a.elapsed === b.elapsed &&
    a.confidence === b.confidence &&
    a.isNewlyAdded === b.isNewlyAdded &&
    tcEqual &&
    prev.selected === next.selected &&
    prev.dragging === next.dragging
  );
}

export const LayerNode = memo(LayerNodeImpl, layerNodePropsEqual);
