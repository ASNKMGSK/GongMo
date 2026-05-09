// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { memo, useEffect, useRef, useState } from "react";

import type { NodeDef, NodeState } from "@/lib/pipeline";
import { useTenantFlashKey } from "@/lib/tenantFlash";
import { useFlashOnChange } from "@/lib/useAnimations";

export interface LayerNodeData extends Record<string, unknown> {
  def: NodeDef;
  state: NodeState;
  /** ★ 2026-05-07: Layer/agent 노드도 score 받을 수 있도록 — KMS 등 동적 점수. */
  score?: number;
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
  /** 동적 sub 텍스트 override — 결과 들어오면 def.sub 보다 우선 표시.
   *  KMS 노드의 검출 인텐트 ("교환, 반품") 등에 사용. */
  dynamicSub?: string;
  /** ★ 2026-05-07: 명시적 "📋 상세" 액션 버튼 → NodeDrawer 오픈. */
  onOpenDetail?: (nodeId: string) => void;
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
    dot: "var(--ink-subtle)",
    ring: "transparent",
    border: "var(--border)",
    bg: "var(--surface)",
    glow: "var(--shadow-subtle)",
  },
  active: {
    dot: "var(--accent)",
    ring: "var(--accent-ring)",
    border: "var(--accent)",
    bg: "var(--surface)",
    glow: "0 0 0 4px var(--accent-ring), 0 2px 4px rgba(0,0,0,0.04), 0 12px 28px var(--accent-ring)",
  },
  done: {
    dot: "var(--success)",
    ring: "transparent",
    border: "var(--success-border)",
    bg: "var(--surface)",
    glow: "var(--shadow-subtle)",
  },
  error: {
    dot: "var(--danger)",
    ring: "rgba(176,58,46,0.12)",
    border: "var(--danger-border)",
    bg: "var(--danger-bg)",
    glow: "0 1px 2px rgba(176,58,46,0.08)",
  },
  "gate-failed": {
    dot: "var(--danger)",
    ring: "rgba(176,58,46,0.12)",
    border: "var(--danger-border)",
    bg: "var(--danger-bg)",
    glow: "0 1px 2px rgba(176,58,46,0.08)",
  },
  skipped: {
    dot: "var(--ink-subtle)",
    ring: "transparent",
    border: "var(--border-subtle)",
    bg: "var(--surface-muted)",
    glow: "none",
  },
  aborted: {
    dot: "var(--warn)",
    ring: "transparent",
    border: "var(--warn-border)",
    bg: "var(--warn-bg)",
    glow: "0 1px 2px rgba(128,99,40,0.08)",
  },
};

function LayerNodeImpl({ data }: NodeProps) {
  const d = data as LayerNodeData;
  const {
    def,
    state,
    score,
    elapsed,
    confidence,
    tenantContext,
    isNewlyAdded,
    dynamicSub,
  } = d;
  const disabled = !!def.disabled;
  const tok = STATE_TOKENS[state] ?? STATE_TOKENS.pending;
  const isData = def.type === "data";
  const isTenantConfig = def.id === "tenant_config";
  const confColor =
    confidence == null
      ? "var(--ink-subtle)"
      : confidence >= 0.8
        ? "var(--success)"
        : confidence >= 0.6
          ? "var(--accent)"
          : "var(--danger)";

  const scoreRatio = def.score && score !== undefined ? score / def.score : 0;
  const scoreColor =
    state !== "done"
      ? "var(--ink-subtle)"
      : scoreRatio >= 0.8
        ? "var(--success)"
        : scoreRatio >= 0.5
          ? "var(--accent)"
          : "var(--danger)";

  // ★ 2026-05-07: 라이브 이벤트 도착 액션 — dynamicSub / score 가 바뀔 때 1.4초 강조 flash.
  // KMS 노드: 인텐트 검출 + 인텐트별 점수 산출마다 플래시 → "방금 백엔드에서 결과 도착" 시각 신호.
  const subFlash = useFlashOnChange(dynamicSub, 1100);
  const scoreFlash = useFlashOnChange(score, 1100);
  const liveFlash = subFlash || scoreFlash;


  // tenant_config 노드는 sub 텍스트 자리에 현재 site·channel·department 를 노출.
  // dynamicSub 가 있으면 (예: KMS 의 검출 인텐트) 가장 우선 표시.
  const subText = dynamicSub
    ? dynamicSub
    : isTenantConfig && tenantContext
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
      className={[
        sparkle ? "tenant-config-sparkle" : "",
        liveFlash ? "kms-live-flash" : "",
      ]
        .filter(Boolean)
        .join(" ") || undefined}
      title={disabled ? "현재 비활성 (테넌트 설정에서 비활성화됨)" : undefined}
      style={{
        width: def.w,
        height: def.h,
        background: liveFlash
          ? "var(--accent-bg)"
          : isData
            ? "var(--surface-muted)"
            : tok.bg,
        border: `${liveFlash ? "2px" : "1.5px"} solid ${
          liveFlash ? "var(--accent)" : isData ? "var(--border-strong)" : tok.border
        }`,
        borderRadius: "var(--radius)",
        // boxShadow 와 transform 은 .kms-live-flash 클래스가 키프레임으로 박동 → inline 비움.
        boxShadow: liveFlash
          ? undefined
          : state === "active"
            ? tok.glow
            : "var(--shadow-subtle)",
        padding: "12px 16px",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: 3,
        position: "relative",
        opacity: disabled ? 0.45 : 1,
        filter: disabled ? "grayscale(0.7)" : undefined,
        cursor: "pointer",
        zIndex: liveFlash ? 50 : undefined,
        transition: liveFlash
          ? undefined
          : "box-shadow 0.3s cubic-bezier(0.2,0,0,1), border-color 0.3s ease, background 0.3s ease, opacity 0.3s ease",
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
            color: "var(--warn)",
            background: "var(--warn-bg)",
            border: "1px solid var(--warn-border)",
            padding: "2px 8px",
            borderRadius: "var(--radius-pill)",
            letterSpacing: "0.08em",
            textTransform: "uppercase",
          }}
        >
          INPUT
        </span>
      )}

      {/* ✓ Done 체크마크 */}
      {state === "done" && (
        <span
          style={{
            position: "absolute",
            top: 8,
            right: 8,
            width: 18,
            height: 18,
            borderRadius: "50%",
            background: "var(--success)",
            color: "var(--bg)",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 11,
            fontWeight: 800,
          }}
          aria-label="완료"
        >
          ✓
        </span>
      )}

      <div
        style={{
          fontSize: 14,
          fontWeight: 500,
          color: "var(--ink-display)",
          letterSpacing: "-0.02em",
          lineHeight: 1.25,
          paddingLeft: 10,
          fontFamily: "'Mark For MC', var(--font-sans), sans-serif",
        }}
      >
        {def.label}
      </div>
      {subText && (
        <div
          style={{
            fontSize: 11,
            color: dynamicSub
              ? "var(--accent-strong)"
              : isTenantConfig && tenantContext
                ? "var(--ink-soft)"
                : "var(--ink-muted)",
            lineHeight: 1.35,
            paddingLeft: 10,
            fontFamily:
              isTenantConfig && tenantContext
                ? "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)"
                : undefined,
            fontWeight: dynamicSub || (isTenantConfig && tenantContext) ? 600 : 400,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {subText}
        </div>
      )}

      {/* KMS 노드 전용 점수 칩 — 우상단. layer2 평가 노드들처럼 "—/100" placeholder + 결과 시 갱신 */}
      {def.id === "kms" && def.score !== undefined && (
        <span
          title="KMS 평가 — 인텐트별 점수의 평균"
          style={{
            position: "absolute",
            top: 8,
            right: state === "done" ? 32 : 10,
            display: "inline-flex",
            alignItems: "baseline",
            gap: 3,
            fontSize: 12,
            fontWeight: 700,
            color: scoreColor,
            background: "var(--surface)",
            border: `1px solid ${typeof score === "number" ? scoreColor : "var(--border)"}`,
            padding: "1px 8px",
            borderRadius: "var(--radius-pill)",
            fontVariantNumeric: "tabular-nums",
            fontFamily: "'Mark For MC', var(--font-sans), sans-serif",
          }}
        >
          <span style={{ fontSize: 13 }}>
            {typeof score === "number"
              ? Number.isInteger(score)
                ? score
                : score.toFixed(1)
              : "—"}
          </span>
          <span style={{ color: "var(--ink-subtle)", fontWeight: 400, fontSize: 10 }}>
            / {def.score}
          </span>
        </span>
      )}

      {elapsed !== undefined && state === "done" && (
        <div
          style={{
            position: "absolute",
            bottom: 6,
            right: 10,
            fontSize: 10,
            color: "var(--ink-subtle)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {elapsed.toFixed(1)}s
        </div>
      )}
      {/* LLM Confidence 배지 — 좌하단 */}
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
            padding: "1px 7px",
            fontSize: 9.5,
            fontWeight: 700,
            letterSpacing: "0.02em",
            color: confColor,
            background: "var(--surface-muted)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-pill)",
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
    a.score === b.score &&
    a.elapsed === b.elapsed &&
    a.confidence === b.confidence &&
    a.isNewlyAdded === b.isNewlyAdded &&
    a.dynamicSub === b.dynamicSub &&
    a.onOpenDetail === b.onOpenDetail &&
    tcEqual &&
    prev.selected === next.selected &&
    prev.dragging === next.dragging
  );
}

export const LayerNode = memo(LayerNodeImpl, layerNodePropsEqual);
