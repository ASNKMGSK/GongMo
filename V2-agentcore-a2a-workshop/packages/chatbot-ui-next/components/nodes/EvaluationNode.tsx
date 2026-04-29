// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { memo } from "react";

import type { NodeDef, NodeState } from "@/lib/pipeline";
import { useCountUp, useFlashOnChange } from "@/lib/useAnimations";

export interface EvaluationNodeData extends Record<string, unknown> {
  def: NodeDef;
  state: NodeState;
  score?: number;
  elapsed?: number;
  /** 평균 LLM confidence (0~1) — items[].confidence 평균. 노드 우상단 배지로 표시. */
  confidence?: number;
  debateEnabled?: boolean;
  debateStatus?: "idle" | "running" | "done";
  debateRound?: number;
  debateMaxRounds?: number;
  onDebateOpen?: (nodeId: string) => void;
  /** 테넌트 전환으로 새로 추가된 노드 — 1.6초 sparkle 애니메이션 */
  isNewlyAdded?: boolean;
}

const STATE_TOKENS: Record<
  NodeState,
  { dot: string; ring: string; border: string; bg: string; glow: string }
> = {
  pending: {
    dot: "#c3beaf",
    ring: "transparent",
    border: "#ece8d8",
    bg: "#ffffff",
    glow: "0 1px 2px rgba(0,0,0,0.03), 0 4px 10px rgba(0,0,0,0.03)",
  },
  active: {
    dot: "#c96442",
    ring: "rgba(201,100,66,0.18)",
    border: "#c96442",
    bg: "#ffffff",
    glow: "0 0 0 4px rgba(201,100,66,0.14), 0 2px 4px rgba(0,0,0,0.04), 0 12px 28px rgba(201,100,66,0.18)",
  },
  done: {
    dot: "#2e7d4f",
    ring: "transparent",
    border: "#cfe5d8",
    bg: "#ffffff",
    glow: "0 1px 2px rgba(0,0,0,0.04), 0 4px 10px rgba(0,0,0,0.04)",
  },
  error: {
    dot: "#b03a2e",
    ring: "rgba(176,58,46,0.14)",
    border: "#e7c9c4",
    bg: "#fdf6f4",
    glow: "0 1px 2px rgba(176,58,46,0.1)",
  },
  "gate-failed": {
    dot: "#b03a2e",
    ring: "rgba(176,58,46,0.14)",
    border: "#e7c9c4",
    bg: "#fdf6f4",
    glow: "0 1px 2px rgba(176,58,46,0.1)",
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

function EvaluationNodeImpl({ data }: NodeProps) {
  const d = data as EvaluationNodeData;
  const {
    def,
    state,
    score,
    elapsed,
    confidence,
    debateEnabled,
    debateStatus,
    debateRound,
    debateMaxRounds,
    onDebateOpen,
    isNewlyAdded,
  } = d;
  const tok = STATE_TOKENS[state] ?? STATE_TOKENS.pending;

  // 점수 count-up — undefined → 숫자 도착 시 0 부터 count-up, 그 다음부턴 이전 값 → 새 값.
  const animatedScore = useCountUp(score, 700);
  // 상태 전환 시 테두리 짧게 빛나는 플래시 (done 으로 막 전환했을 때 특히 효과적).
  const stateFlash = useFlashOnChange(state, 900);
  // 점수 갱신 플래시 — 숫자 바뀔 때 "찰칵" 하이라이트
  const scoreFlash = useFlashOnChange(score, 700);
  // LLM confidence (0~1) — 도착 시 0 부터 count-up.
  const animatedConf = useCountUp(
    typeof confidence === "number" ? confidence * 100 : undefined,
    700,
  );
  const confFlash = useFlashOnChange(confidence, 700);
  const confColor =
    confidence == null
      ? "#9a9583"
      : confidence >= 0.8
        ? "#2e7d4f"
        : confidence >= 0.6
          ? "#c96442"
          : "#b03a2e";

  // 점수 ratio 기반 color — done 상태에서만 점수 색상 부여
  const ratio = def.score && score !== undefined ? score / def.score : 0;
  const scoreColor =
    state !== "done"
      ? "#9a9583"
      : ratio >= 0.8
        ? "#2e7d4f"
        : ratio >= 0.5
          ? "#c96442"
          : "#b03a2e";

  const handleDebateClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onDebateOpen) onDebateOpen(def.id);
  };

  // 토론 버튼 라벨 — 3가지 상태 대응.
  //   idle    : "💬 토론" (대기, 회색 톤)
  //   running : "LIVE · R1" (실시간, 보라 + 빨간 펄스 dot)
  //   done    : "토론 결과" (완료, 초록)
  const debateBtnLabel = (() => {
    if (debateStatus === "running") {
      const r = debateRound != null ? ` · R${debateRound}` : "";
      return `LIVE${r}`;
    }
    if (debateStatus === "done") return "토론 결과";
    return "💬 토론";
  })();

  // idle 상태에선 은은한 ghost 스타일, running 에선 보라 gradient + pulse, done 은 초록.
  // 이렇게 해서 "평가 노드엔 항상 토론 버튼이 있고 진행 상태에 따라 동적으로 변화" UX 를 만든다.
  const debateBtnBg =
    debateStatus === "running"
      ? "linear-gradient(135deg, #7b519c 0%, #6a4485 100%)"
      : debateStatus === "done"
        ? "linear-gradient(135deg, #3d8c5f 0%, #2e7d4f 100%)"
        : "#ffffff"; // idle — ghost 톤
  const debateBtnColor =
    debateStatus === "running" || debateStatus === "done"
      ? "#ffffff"
      : "#7b519c"; // idle 시 보라 텍스트 (토론 테마 유지)
  const debateBtnBorder =
    debateStatus === "running" || debateStatus === "done"
      ? "transparent"
      : "#d8c9ec"; // idle 외곽선 — 보라 hint

  const disabled = !!def.disabled;

  return (
    <div
      title={disabled ? "현재 비활성 (테넌트 설정에서 비활성화됨)" : undefined}
      style={{
        width: def.w,
        height: def.h,
        background: tok.bg,
        border: `1px solid ${tok.border}`,
        borderRadius: 14,
        boxShadow:
          state === "active"
            ? tok.glow
            : debateStatus === "running"
              ? "0 0 0 4px rgba(106,68,133,0.14), 0 2px 4px rgba(0,0,0,0.04)"
              : stateFlash
                ? "0 0 0 3px rgba(46,125,79,0.25), 0 2px 4px rgba(0,0,0,0.04)"
                : tok.glow,
        padding: "11px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 3,
        position: "relative",
        opacity: disabled ? 0.45 : 1,
        filter: disabled ? "grayscale(0.7)" : undefined,
        // overflow: visible — LIVE 배지 (top: -8, right: -8) 가 노드 밖으로 튀어나가게.
        // shimmer 효과는 overflow:hidden 필요하지만 배지 우선 → shimmer 제거.
        transition:
          "box-shadow 0.3s cubic-bezier(0.2,0,0,1), border-color 0.3s cubic-bezier(0.2,0,0,1), transform 0.2s ease, opacity 0.3s ease",
        transform: scoreFlash ? "scale(1.015)" : "scale(1)",
        fontFamily:
          "var(--font-sans), -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
      <Handle id="top" type="target" position={Position.Top} style={{ opacity: 0 }} />
      <Handle id="top" type="source" position={Position.Top} style={{ opacity: 0 }} />
      <Handle id="bottom" type="source" position={Position.Bottom} style={{ opacity: 0 }} />
      <Handle id="bottom" type="target" position={Position.Bottom} style={{ opacity: 0 }} />

      {/* 신규 추가 노드 sparkle 오버레이 + 배지 (2.4초 후 자동 사라짐) */}
      {isNewlyAdded && (
        <>
          <span className="node-new-overlay" aria-hidden="true" />
          <span className="node-new-badge">✨ 신규</span>
        </>
      )}

      {/* 상태 dot */}
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

      {/* 토론 진행 중 배지 */}
      {debateStatus === "running" && (
        <span
          title={`토론 진행 중 — R${debateRound ?? "?"}/${debateMaxRounds ?? "?"}`}
          style={{
            position: "absolute",
            top: -8,
            right: -8,
            display: "inline-flex",
            alignItems: "center",
            gap: 3,
            padding: "3px 8px",
            background: "linear-gradient(135deg, #7b519c 0%, #6a4485 100%)",
            color: "#ffffff",
            fontSize: 9.5,
            fontWeight: 700,
            letterSpacing: "0.04em",
            borderRadius: 9999,
            border: "1.5px solid #ffffff",
            boxShadow: "0 2px 8px rgba(106,68,133,0.35)",
            animation: "debateBadgePulse 1.4s ease-in-out infinite",
            zIndex: 12,
          }}
        >
          <span
            style={{
              width: 5,
              height: 5,
              borderRadius: "50%",
              background: "#ffffff",
              animation: "pulseDot 1.1s ease-out infinite",
            }}
            aria-hidden="true"
          />
          LIVE
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
      {def.sub && (
        <div
          style={{
            fontSize: 10,
            color: "#9a9583",
            lineHeight: 1.3,
            paddingLeft: 10,
          }}
        >
          {def.sub}
        </div>
      )}

      {/* 점수 row — 토론 버튼을 inline 으로 같이 배치해서 absolute 겹침 제거 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginTop: "auto",
          paddingTop: 4,
          paddingLeft: 10,
          gap: 6,
        }}
      >
        {def.score !== undefined && (
          <span
            style={{
              display: "inline-flex",
              alignItems: "baseline",
              gap: 3,
              fontSize: 12,
              fontWeight: 700,
              color: scoreColor,
              fontVariantNumeric: "tabular-nums",
              transition: "color 0.3s ease",
              textShadow: scoreFlash ? `0 0 8px ${scoreColor}66` : "none",
              flexShrink: 0,
            }}
          >
            <span style={{ fontSize: 13 }}>
              {animatedScore !== null
                ? Number.isInteger(animatedScore)
                  ? animatedScore
                  : animatedScore.toFixed(1)
                : "—"}
            </span>
            <span style={{ color: "#bfbaa8", fontWeight: 400, fontSize: 10.5 }}>
              / {def.score}
            </span>
          </span>
        )}
        {/* 중앙 spacer — 점수 왼쪽 / 우측은 conf + elapsed + button */}
        <div style={{ display: "inline-flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
          {/* LLM Confidence pill — items[].confidence 평균. 도착 즉시 count-up. */}
          {confidence != null && (
            <span
              title={`LLM 평균 confidence · ${(confidence * 100).toFixed(1)}%`}
              style={{
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
                transition: "color 0.3s ease, box-shadow 0.3s ease",
                boxShadow: confFlash ? `0 0 0 3px ${confColor}28` : "none",
                flexShrink: 0,
              }}
            >
              <span style={{ fontSize: 8, opacity: 0.85, fontWeight: 600 }}>conf</span>
              <span>
                {animatedConf !== null
                  ? `${Math.round(animatedConf)}%`
                  : "—"}
              </span>
            </span>
          )}
          {elapsed !== undefined && state === "done" && (
            <span
              style={{
                fontSize: 10,
                color: "#9a9583",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {elapsed.toFixed(1)}s
            </span>
          )}
          {debateEnabled && onDebateOpen && (
            <button
              type="button"
              onClick={handleDebateClick}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "2px 7px",
                fontSize: 9.5,
                fontWeight: 700,
                color: debateBtnColor,
                background: debateBtnBg,
                border: `1px solid ${debateBtnBorder}`,
                borderRadius: 9999,
                cursor: "pointer",
                boxShadow:
                  debateStatus === "running"
                    ? "0 1px 4px rgba(106,68,133,0.35)"
                    : debateStatus === "done"
                      ? "0 1px 2px rgba(46,125,79,0.2)"
                      : "0 1px 2px rgba(0,0,0,0.05)",
                whiteSpace: "nowrap",
                transition:
                  "transform 0.12s ease, box-shadow 0.22s ease, background 0.3s ease, color 0.3s ease, border-color 0.3s ease",
                letterSpacing: "-0.01em",
                flexShrink: 0,
                opacity: debateStatus === "running" || debateStatus === "done" ? 1 : 0.85,
              }}
              title={
                debateStatus === "running"
                  ? "토론 진행 중 — 클릭하여 페르소나 대화 실시간으로 보기"
                  : debateStatus === "done"
                    ? "토론 완료 — 결과 및 대화 로그 보기"
                    : "토론 가능한 평가 항목 — 토론이 시작되면 여기서 실시간 대화를 볼 수 있습니다"
              }
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = "translateY(-1px)";
                if (debateStatus !== "running" && debateStatus !== "done") {
                  e.currentTarget.style.background = "#f5eef9"; // idle hover — 은은한 보라 배경
                }
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = "none";
                if (debateStatus !== "running" && debateStatus !== "done") {
                  e.currentTarget.style.background = "#ffffff";
                }
              }}
            >
              {debateStatus === "running" && (
                <span
                  style={{
                    width: 5,
                    height: 5,
                    borderRadius: "50%",
                    background: "#ff5050",
                    animation: "pulseDot 1.1s ease-out infinite",
                    boxShadow: "0 0 0 1.5px rgba(255,80,80,0.3)",
                  }}
                  aria-hidden="true"
                />
              )}
              {debateBtnLabel}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Custom data comparator — PipelineFlow 의 nodes useMemo 가 매 SSE 이벤트마다
 * 새로운 data 객체를 생성하므로, default shallow comparator 는 항상 false → 모든 노드 re-render.
 * data 의 primitive/stable-ref 필드를 직접 비교해 변경된 노드만 re-render 하도록.
 */
function evalNodePropsEqual(prev: NodeProps, next: NodeProps): boolean {
  const a = prev.data as EvaluationNodeData;
  const b = next.data as EvaluationNodeData;
  return (
    a.def === b.def &&
    a.state === b.state &&
    a.score === b.score &&
    a.elapsed === b.elapsed &&
    a.confidence === b.confidence &&
    a.debateEnabled === b.debateEnabled &&
    a.debateStatus === b.debateStatus &&
    a.debateRound === b.debateRound &&
    a.debateMaxRounds === b.debateMaxRounds &&
    a.onDebateOpen === b.onDebateOpen &&
    a.isNewlyAdded === b.isNewlyAdded &&
    prev.selected === next.selected &&
    prev.dragging === next.dragging
  );
}

export const EvaluationNode = memo(EvaluationNodeImpl, evalNodePropsEqual);
