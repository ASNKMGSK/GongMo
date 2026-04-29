// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useMemo } from "react";

import { useAppState } from "@/lib/AppStateContext";
import { ITEM_NAMES, STT_MAX_SCORES, scoreColor } from "@/lib/items";
import { computeClientGtComparison } from "@/lib/manualEvalMapper";
import { NODE_DEFS, NODE_ITEMS } from "@/lib/pipeline";
import { aggregateRagHitsByAgent } from "@/lib/ragHitsAggregator";
import type {
  CategoryItem,
  EvaluationResult,
  GtComparison,
} from "@/lib/types";

import AwsResourcesPanel from "./AwsResourcesPanel";
import PersonaExecutionDetails from "./PersonaExecutionDetails";
import RagHitsPanel from "./RagHitsPanel";

/**
 * NodeDrawer — 파이프라인 노드 클릭 시 우측 슬라이드 인 패널.
 * V2 HTML 4583~5687 풀 이식. 노드별 섹션:
 *   - tenant_config: AWS 연결 상태 · Tenant Resources (local paths)
 *   - gt_comparison: 4-메트릭 + 항목별 비교표 (V2 그리드)
 *   - sub-agent eval 노드: Evaluated Items (persona 메타 포함) + 3-Persona 실행 상세 + RAG Hits
 *   - 공통: Evaluation Errors (node_errors) + Deductions (drawer-evidence)
 */

interface Props {
  nodeId: string | null;
  result: EvaluationResult | null;
  nodeStates: Record<string, string>;
  nodeTimings: Record<string, number>;
  nodeScores: Record<string, number>;
  onClose: () => void;
}

interface ResultLike {
  report?: {
    item_scores?: CategoryItem[];
    deductions?: Array<{
      item_number?: number;
      item?: number;
      reason?: string;
      description?: string;
      deduction?: number;
      points?: number;
      points_lost?: number;
      evidence?: string;
      quote?: string;
    }>;
    report?: {
      item_scores?: CategoryItem[];
      deductions?: Array<unknown>;
    };
  };
  item_scores?: CategoryItem[];
  deductions?: Array<unknown>;
}

function pickReportCore(result: EvaluationResult | null): {
  items: CategoryItem[];
  deductions: Array<{
    item_number?: number;
    item?: number;
    reason?: string;
    description?: string;
    deduction?: number;
    points?: number;
    points_lost?: number;
    evidence?: string;
    quote?: string;
  }>;
} {
  if (!result) return { items: [], deductions: [] };
  const rp = (result as unknown as ResultLike).report || (result as unknown as ResultLike);
  const inner = (rp as { report?: { item_scores?: CategoryItem[]; deductions?: unknown[] } })
    .report;
  const base = inner || rp;
  const items = (base as { item_scores?: CategoryItem[] }).item_scores || [];
  const deductions =
    ((base as { deductions?: unknown[] }).deductions as Array<{
      item_number?: number;
      item?: number;
      reason?: string;
      description?: string;
      deduction?: number;
      points?: number;
      points_lost?: number;
      evidence?: string;
      quote?: string;
    }>) || [];
  return { items: Array.isArray(items) ? items : [], deductions: Array.isArray(deductions) ? deductions : [] };
}

export default function NodeDrawer({
  nodeId,
  result,
  nodeStates,
  nodeTimings,
  nodeScores,
  onClose,
}: Props) {
  const { state: appState } = useAppState();

  useEffect(() => {
    if (!nodeId) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [nodeId, onClose]);

  const gcServer = (result as unknown as { gt_comparison?: GtComparison | null })
    ?.gt_comparison;
  const gcClient = useMemo<GtComparison | null>(
    () =>
      gcServer || !appState.manualEval
        ? null
        : computeClientGtComparison(result, appState.manualEval),
    [gcServer, appState.manualEval, result],
  );

  // RAG hits aggregation — 에이전트 노드 클릭 시 사용 (primary 경로).
  // itemScores 를 이용한 2차 보강은 itemScores 확정 이후에 수행 (아래 참조).
  const ragHitsByAgent = useMemo(
    () => aggregateRagHitsByAgent(result, appState.streamingItems || []),
    [result, appState.streamingItems],
  );

  // ── 실시간 데이터 (평가 진행 중에도 노드별 데이터 노출) ──
  // appState 의 traces / rawLogs / streamingItems 에서 nodeId 매칭하는 항목만 추출.
  const liveTraces = useMemo(() => {
    if (!nodeId) return [];
    return (appState.traces || []).filter((t) => t.node === nodeId).slice(-5);
  }, [appState.traces, nodeId]);

  const liveRawEvents = useMemo(() => {
    if (!nodeId) return [];
    return (appState.rawLogs || [])
      .filter((r) => {
        const data = (r.data || {}) as Record<string, unknown>;
        const nodeField = data.node || data.node_id || data.next_node;
        return nodeField === nodeId;
      })
      .slice(-8);
  }, [appState.rawLogs, nodeId]);

  // 평가 노드 스트리밍 진행 — NODE_ITEMS[nodeId] 와 streamingItems.item_number 매칭
  const liveStreamingScores = useMemo(() => {
    if (!nodeId) return [];
    const targetItems = NODE_ITEMS[nodeId] || [];
    if (targetItems.length === 0) return [];
    return (appState.streamingItems || []).filter((s) =>
      targetItems.includes(s.item_number),
    );
  }, [appState.streamingItems, nodeId]);

  if (!nodeId) return null;
  const def = NODE_DEFS[nodeId];
  if (!def) return null;
  const state = nodeStates[nodeId] || "pending";
  const timing = nodeTimings[nodeId];
  const score = nodeScores[nodeId];
  const items = NODE_ITEMS[nodeId] || [];

  // 1차: report.item_scores (전체 평가 완료 시)
  const { items: allItems, deductions: allDeductions } = pickReportCore(result);
  let itemScores = allItems.filter((it) => items.includes(it.item_number));

  // 2차 폴백: result.evaluations[] (개별 sub-agent 완료 시 — 평가 도중에도 채워짐)
  if (itemScores.length === 0 && result) {
    const evals = (result as unknown as {
      evaluations?: Array<{
        agent_id?: string;
        evaluation?: CategoryItem;
      }>;
    })?.evaluations;
    if (Array.isArray(evals)) {
      // agent_id 가 nodeId 또는 nodeId-agent 형태와 매칭
      const matched = evals
        .filter((e) => {
          const aid = String(e.agent_id || "");
          return aid === nodeId || aid === `${nodeId}-agent` || aid.startsWith(`${nodeId}-`);
        })
        .map((e) => e.evaluation)
        .filter((ev): ev is CategoryItem => !!ev && typeof ev.item_number === "number");
      if (matched.length > 0) itemScores = matched;
    }
  }

  // 3차 폴백: live trace detail.output.evaluations (node_trace SSE)
  // 백엔드 server_v2.py::_sanitize_trace_output 은 delta 를 그대로 payload.output 에 담아 emit.
  // sub-agent 노드의 delta 는 { evaluations: [{agent_id, evaluation}] } 형태.
  if (itemScores.length === 0) {
    // 최신 trace 부터 역순으로 탐색 — 여러 trace 가 있어도 가장 최근 평가 결과 우선
    for (let i = liveTraces.length - 1; i >= 0; i--) {
      const trace = liveTraces[i];
      const detail = trace?.detail as
        | {
            evaluations?: Array<{ agent_id?: string; evaluation?: CategoryItem }>;
            output?: {
              evaluations?: Array<{ agent_id?: string; evaluation?: CategoryItem }>;
              [k: string]: unknown;
            };
          }
        | undefined;
      // node_trace 는 detail.output.evaluations (정상 경로). detail.evaluations 는 구버전 호환용.
      const evalsArr =
        detail?.output?.evaluations ?? detail?.evaluations ?? null;
      if (Array.isArray(evalsArr) && evalsArr.length > 0) {
        const fromTrace = evalsArr
          .map((e) => e.evaluation)
          .filter(
            (ev): ev is CategoryItem =>
              !!ev &&
              typeof ev.item_number === "number" &&
              items.includes(ev.item_number),
          );
        if (fromTrace.length > 0) {
          itemScores = fromTrace;
          break;
        }
      }
    }
  }

  const nodeDeductions = allDeductions.filter((d) => {
    const num = Number(d.item_number ?? d.item);
    return !isNaN(num) && items.includes(num);
  });

  const gc: GtComparison | null | undefined = gcServer ?? gcClient;
  const isGtNode = nodeId === "gt_comparison";
  const isTenantNode = nodeId === "tenant_config";

  const nodeErrMsg = appState.nodeErrors?.[nodeId];
  // ragHitsByAgent 1차 경로가 비어 있으면 itemScores 로 직접 2차 집계 — 어떤 소스에서든
  // rag_evidence 가 1곳이라도 보이면 panel 이 뜨도록 보강.
  // NOTE: 여기는 early return 뒤 영역이라 hook(useMemo) 사용 불가 — inline 계산 (비용 낮음).
  const ragHitsPrimary = ragHitsByAgent[nodeId];
  const ragHitsFromItems =
    itemScores.length > 0
      ? aggregateRagHitsByAgent({ evaluations: itemScores }, [])[nodeId]
      : undefined;
  const ragHits =
    ragHitsPrimary && (ragHitsPrimary.hasGS || ragHitsPrimary.hasRS || ragHitsPrimary.hasBK)
      ? ragHitsPrimary
      : ragHitsFromItems;

  const stateBadgeCls =
    state === "done"
      ? "badge badge-success"
      : state === "active"
        ? "badge badge-accent"
        : state === "error" || state === "gate-failed"
          ? "badge badge-danger"
          : state === "skipped"
            ? "badge badge-neutral"
            : "badge badge-neutral";

  return (
    <>
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.25)",
          zIndex: 29,
          animation: "fadeIn 200ms var(--ease)",
        }}
        aria-hidden="true"
      />
      <aside
        data-testid="node-drawer"
        className="animate-fade-in"
        role="dialog"
        aria-label={def.label}
        style={{
          position: "fixed",
          top: 0,
          bottom: 0,
          right: 0,
          width: "min(520px, 92vw)",
          background: "var(--surface)",
          borderLeft: "1px solid var(--border)",
          boxShadow: "var(--shadow-lifted)",
          padding: 0,
          overflowY: "auto",
          zIndex: 30,
        }}
      >
        <header
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 8,
            padding: "16px 20px 12px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1 }}>
            <span className="badge badge-accent">{nodeId}</span>
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: "var(--ink)" }}>
                {def.label}
              </div>
              {def.sub && (
                <div style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                  {def.sub}
                </div>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="btn-ghost"
            aria-label="닫기"
            style={{ fontSize: 16, padding: "4px 10px" }}
          >
            ✕
          </button>
        </header>

        {/* Status */}
        <div className="drawer-section">
          <div className="drawer-section-title">Status</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <span className={stateBadgeCls}>{state}</span>
            {timing != null && (
              <span className="badge badge-neutral tabular-nums">
                {timing.toFixed(2)}s
              </span>
            )}
            {score != null && (
              <span className="badge badge-neutral tabular-nums">
                점수 합계: {score}
              </span>
            )}
          </div>
          {nodeErrMsg && (
            <div
              style={{
                marginTop: 8,
                padding: "8px 10px",
                fontSize: 11.5,
                color: "#991b1b",
                background: "rgba(239,68,68,0.06)",
                border: "1px solid rgba(239,68,68,0.25)",
                borderLeft: "3px solid #ef4444",
                borderRadius: 4,
              }}
            >
              <b>노드 에러:</b> {nodeErrMsg}
            </div>
          )}
        </div>

        {/* Live Activity — 평가 진행 중에도 실시간 데이터 노출 (traces / streaming / raw events) */}
        {(liveStreamingScores.length > 0 ||
          liveTraces.length > 0 ||
          liveRawEvents.length > 0) && (
          <div className="drawer-section">
            <div className="drawer-section-title">
              실시간 활동
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 500,
                  color: "var(--ink-muted)",
                  marginLeft: 6,
                }}
              >
                — SSE 스트림에서 자동 갱신
              </span>
            </div>

            {/* 스트리밍 점수 (평가 노드만) */}
            {liveStreamingScores.length > 0 && (
              <div style={{ marginBottom: 10 }}>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    color: "var(--ink-muted)",
                    marginBottom: 4,
                  }}
                >
                  진행 중 점수 ({liveStreamingScores.length}개)
                </div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {liveStreamingScores.map((s) => {
                    const max = STT_MAX_SCORES[s.item_number] ?? 5;
                    const cls = scoreColor(s.score ?? 0, max);
                    return (
                      <span
                        key={s.item_number}
                        className={`badge ${cls}`}
                        title={s.label || ITEM_NAMES[s.item_number]}
                        style={{ fontSize: 11 }}
                      >
                        #{s.item_number}: {s.score ?? "—"}/{max}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}

            {/* 트레이스 (input/output/elapsed) */}
            {liveTraces.length > 0 && (
              <div style={{ marginBottom: 10 }}>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    color: "var(--ink-muted)",
                    marginBottom: 4,
                  }}
                >
                  최근 trace ({liveTraces.length})
                </div>
                <div
                  style={{
                    maxHeight: 180,
                    overflowY: "auto",
                    background: "var(--surface-muted)",
                    borderRadius: 6,
                    padding: 8,
                    fontSize: 11,
                    fontFamily: "ui-monospace, monospace",
                  }}
                >
                  {liveTraces.map((t) => (
                    <div
                      key={t.id}
                      style={{
                        marginBottom: 6,
                        paddingBottom: 6,
                        borderBottom: "1px dashed var(--border-subtle)",
                      }}
                    >
                      <div style={{ color: "var(--ink-subtle)" }}>
                        [{t.time}] <b>{t.status}</b>
                        {t.elapsed != null && ` · ${t.elapsed.toFixed(2)}s`}
                        {t.label && ` · ${t.label}`}
                      </div>
                      {t.detail && Object.keys(t.detail).length > 0 && (
                        <details style={{ marginTop: 2 }}>
                          <summary
                            style={{
                              cursor: "pointer",
                              color: "var(--accent)",
                              fontSize: 10,
                            }}
                          >
                            상세
                          </summary>
                          <pre
                            style={{
                              fontSize: 10,
                              margin: "4px 0 0",
                              whiteSpace: "pre-wrap",
                              wordBreak: "break-all",
                              color: "var(--ink-soft)",
                            }}
                          >
                            {JSON.stringify(t.detail, null, 2).slice(0, 1500)}
                          </pre>
                        </details>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Raw SSE 이벤트 */}
            {liveRawEvents.length > 0 && (
              <div>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    color: "var(--ink-muted)",
                    marginBottom: 4,
                  }}
                >
                  최근 SSE 이벤트 ({liveRawEvents.length})
                </div>
                <div
                  style={{
                    maxHeight: 140,
                    overflowY: "auto",
                    background: "var(--surface-muted)",
                    borderRadius: 6,
                    padding: 8,
                    fontSize: 10.5,
                    fontFamily: "ui-monospace, monospace",
                  }}
                >
                  {liveRawEvents.map((r) => (
                    <div key={r.id} style={{ marginBottom: 4 }}>
                      <span style={{ color: "var(--ink-subtle)" }}>
                        [{r.time}]
                      </span>{" "}
                      <span
                        style={{ color: "var(--accent)", fontWeight: 600 }}
                      >
                        {r.event}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* tenant_config — AWS 연결 상태 + Tenant Resources (3단계 멀티테넌트 반영) */}
        {isTenantNode && (
          <AwsResourcesPanel
            serverUrl={appState.serverUrl}
            tenantId={appState.siteId || appState.tenantId}
            channel={appState.channel}
            department={appState.department}
          />
        )}

        {/* gt_comparison — AI vs 수기 QA 비교표 (4-메트릭 + 항목별 그리드) */}
        {isGtNode && gc && gc.enabled !== false && (
          <div className="drawer-section">
            <div className="drawer-section-title">
              수기 QA 정답 비교
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 500,
                  color: "var(--ink-muted)",
                  marginLeft: 6,
                }}
              >
                — 상담ID {gc.sample_id || "-"} · 업무정확도 (#15, #16) 제외
              </span>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(4, 1fr)",
                gap: 6,
                marginBottom: 10,
              }}
            >
              {[
                ["AI 합계", gc.ai_total ?? "-", "var(--ink)"] as const,
                ["사람 QA", gc.gt_total ?? "-", "var(--ink)"] as const,
                [
                  "차이 (AI−사람)",
                  gc.diff != null
                    ? `${gc.diff > 0 ? "+" : ""}${gc.diff}`
                    : "-",
                  gc.diff === 0
                    ? "#166534"
                    : (gc.diff ?? 0) > 0
                      ? "#ea580c"
                      : "#1d4ed8",
                ] as const,
                [
                  "MAE",
                  gc.mae ?? "-",
                  "var(--ink)",
                ] as const,
              ].map(([lab, val, col], i) => (
                <div
                  key={i}
                  style={{
                    padding: "8px 10px",
                    background: "var(--surface-muted)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius-sm)",
                    textAlign: "center",
                  }}
                >
                  <div
                    style={{
                      fontSize: 10,
                      color: "var(--ink-muted)",
                      fontWeight: 600,
                      letterSpacing: "0.04em",
                    }}
                  >
                    {lab}
                  </div>
                  <div
                    className="tabular-nums"
                    style={{
                      fontSize: 14,
                      fontWeight: 800,
                      color: col,
                      marginTop: 2,
                    }}
                  >
                    {val}
                  </div>
                </div>
              ))}
            </div>
            {(gc.mae != null || gc.rmse != null || gc.match_count != null) && (
              <div
                style={{
                  fontSize: 10.5,
                  color: "var(--ink-muted)",
                  marginBottom: 8,
                  padding: "4px 8px",
                  background: "rgba(148,163,184,0.06)",
                  borderRadius: 3,
                }}
              >
                {gc.mae != null && (
                  <>
                    <b>MAE</b> {gc.mae}
                  </>
                )}
                {gc.rmse != null && (
                  <>
                    {" "}
                    · <b>RMSE</b> {gc.rmse}
                  </>
                )}
                {(gc.match_count != null || gc.mismatch_count != null) && (
                  <>
                    {" "}
                    · 일치 <b>{gc.match_count ?? 0}</b> / 불일치{" "}
                    <b>{gc.mismatch_count ?? 0}</b>
                  </>
                )}
              </div>
            )}
            {/* 항목별 비교표 — V2 원본 라인 4972~5024 */}
            {gc.items && gc.items.length > 0 && (
              <div style={{ fontSize: 10.5 }}>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "36px 1fr 60px 60px 50px",
                    fontWeight: 800,
                    padding: "4px 6px",
                    background: "var(--surface-muted)",
                    borderRadius: "3px 3px 0 0",
                    color: "var(--ink)",
                  }}
                >
                  <span>#</span>
                  <span>항목</span>
                  <span style={{ textAlign: "right" }}>AI</span>
                  <span style={{ textAlign: "right" }}>수기 QA</span>
                  <span style={{ textAlign: "center" }}>차이</span>
                </div>
                {gc.items.map((row) => {
                  const ai = row.ai_score;
                  const gt = row.gt_score;
                  const diff =
                    ai != null && gt != null
                      ? Number(ai) - Number(gt)
                      : null;
                  const bg = row.excluded
                    ? "#fef3c7"
                    : diff === 0 && ai != null
                      ? "#dcfce7"
                      : diff != null && diff !== 0
                        ? "#fee2e2"
                        : "var(--surface)";
                  return (
                    <div
                      key={row.item_number}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "36px 1fr 60px 60px 50px",
                        padding: "4px 6px",
                        background: bg,
                        borderBottom: "1px solid var(--border)",
                      }}
                    >
                      <span
                        style={{
                          fontWeight: 700,
                          color: "var(--ink-muted)",
                        }}
                      >
                        #{row.item_number}
                      </span>
                      <span style={{ color: "var(--ink)" }}>
                        {(row.item_name || "").replace(/\n/g, " ")}
                        {row.excluded && (
                          <span
                            style={{
                              marginLeft: 4,
                              fontSize: 9,
                              fontWeight: 700,
                              padding: "1px 4px",
                              borderRadius: 6,
                              background: "#fbbf24",
                              color: "white",
                            }}
                          >
                            제외
                          </span>
                        )}
                      </span>
                      <span
                        style={{ textAlign: "right", fontWeight: 700 }}
                      >
                        {ai ?? "—"}
                        {row.max_score ? `/${row.max_score}` : ""}
                      </span>
                      <span
                        style={{
                          textAlign: "right",
                          fontWeight: 700,
                          color: "#059669",
                        }}
                      >
                        {gt ?? "—"}
                        {row.max_score ? `/${row.max_score}` : ""}
                      </span>
                      <span
                        style={{
                          textAlign: "center",
                          fontWeight: 800,
                          color:
                            diff == null
                              ? "#9ca3af"
                              : diff === 0
                                ? "#166534"
                                : diff > 0
                                  ? "#ea580c"
                                  : "#1d4ed8",
                        }}
                      >
                        {diff == null ? "—" : diff > 0 ? `+${diff}` : diff}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
            <div
              style={{
                fontSize: 9,
                color: "var(--ink-muted)",
                marginTop: 6,
                fontStyle: "italic",
              }}
            >
              노란색 = 비교 제외 (업무 정확도 #15, #16) · 초록 = 일치 · 빨강 = 불일치
            </div>
          </div>
        )}

        {isGtNode && (!gc || gc.enabled === false) && (
          <div className="drawer-section">
            <div className="drawer-section-title">수기 QA 정답 비교</div>
            <div
              className="empty-state"
              style={{ padding: "12px 10px", fontSize: 11 }}
            >
              <div className="empty-state-desc">
                수기 QA 정답이 연결되지 않음 — JSON 파일의{" "}
                <code className="kbd">id</code> 필드 필요
              </div>
            </div>
          </div>
        )}

        {/* Evaluated Items — 이 sub-agent 의 항목 스코어 + persona meta */}
        {itemScores.length > 0 && (
          <EvaluatedItemsSection
            items={items}
            itemScores={itemScores}
            totalItemCount={items.length}
          />
        )}

        {/* 3-Persona 실행 상세 — 페르소나별 점수/판정 사유 표시.
            mode_majority 같은 머지 메타 라벨은 PersonaExecutionDetails 내부에서 숨김. */}
        <PersonaExecutionDetails
          nodeId={nodeId}
          items={items}
          itemScores={itemScores}
          state={state}
        />

        {/* RAG Hits — 에이전트별 집계 (agent 단위 + per-item 탭).
            rag 평가 대상 노드(= items 가 ITEM_TO_AGENT 에 매핑되는 노드)인데 hits 가 비었으면
            tenant/RAG 배선을 의심하게 하는 진단 메시지 노출 → 사용자가 원인 파악하도록. */}
        {ragHits && (ragHits.hasGS || ragHits.hasRS || ragHits.hasBK) ? (
          <RagHitsPanel hits={ragHits} />
        ) : items.length > 0 && itemScores.length > 0 ? (
          <RagDiagnosticBox
            nodeId={nodeId}
            tenantId={appState.siteId || appState.tenantId || ""}
            itemScores={itemScores}
          />
        ) : null}

        {/* Evaluation Errors — nodeErrors 의 this-node 항목들 */}
        {nodeErrMsg && (
          <div className="drawer-section">
            <div
              className="drawer-section-title"
              style={{ color: "var(--danger)" }}
            >
              ⚠ Evaluation Errors
            </div>
            <div
              style={{
                padding: "10px 12px",
                background: "rgba(239,68,68,0.06)",
                border: "1px solid rgba(239,68,68,0.25)",
                borderLeft: "3px solid #ef4444",
                borderRadius: 6,
                fontSize: 12,
                color: "var(--ink)",
                lineHeight: 1.5,
                wordBreak: "break-word",
              }}
            >
              {nodeErrMsg}
            </div>
          </div>
        )}

        {/* Deductions — drawer-evidence */}
        {nodeDeductions.length > 0 && (
          <div className="drawer-section">
            <div className="drawer-section-title">Deductions</div>
            {nodeDeductions.map((d, i) => (
              <div key={i} style={{ marginBottom: 8 }}>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--danger)",
                  }}
                >
                  Item {d.item_number ?? d.item}: -
                  {d.deduction ?? d.points ?? d.points_lost ?? 0}점
                </div>
                <div
                  style={{
                    fontSize: 12,
                    color: "var(--ink-muted)",
                    marginTop: 2,
                  }}
                >
                  {d.reason || d.description || ""}
                </div>
                {(d.evidence || d.quote) && (
                  <div className="drawer-evidence">
                    {d.evidence || d.quote}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {itemScores.length === 0 && !isGtNode && !isTenantNode && !nodeErrMsg && (
          <div className="drawer-section">
            <div className="drawer-section-title">Output</div>
            <div
              className="empty-state"
              style={{ padding: "12px 10px", fontSize: 11 }}
            >
              <div className="empty-state-desc">
                이 노드의 결과 데이터가 아직 도착하지 않았습니다
              </div>
            </div>
          </div>
        )}
      </aside>
    </>
  );
}

function EvaluatedItemsSection({
  items,
  itemScores,
  totalItemCount,
}: {
  items: number[];
  itemScores: CategoryItem[];
  totalItemCount: number;
}) {
  // V2 원본 라인 5034~5213 — persona 요약 + per-item persona indicator
  const itemsWithPersona = itemScores.filter(
    (it) =>
      it.persona_votes &&
      typeof it.persona_votes === "object" &&
      (it.persona_votes.strict !== undefined ||
        it.persona_votes.neutral !== undefined ||
        it.persona_votes.loose !== undefined),
  );
  const judgeCount = itemsWithPersona.filter(
    (it) => it.persona_merge_path === "judge",
  ).length;
  const reviewCount = itemsWithPersona.filter(
    (it) => it.mandatory_human_review,
  ).length;
  const spreadVals = itemsWithPersona
    .map((it) => Number(it.persona_step_spread))
    .filter((v) => !isNaN(v));
  const avgSpread =
    spreadVals.length > 0
      ? (spreadVals.reduce((a, b) => a + b, 0) / spreadVals.length).toFixed(1)
      : null;
  const personaSuccessCounts = itemsWithPersona.map((it) => {
    const pv = it.persona_votes || {};
    return (["strict", "neutral", "loose"] as const).filter(
      (p) => pv[p] !== undefined && pv[p] !== null,
    ).length;
  });
  const allThree = personaSuccessCounts.filter((c) => c === 3).length;

  const isAgentSingle =
    itemsWithPersona.length > 0 &&
    itemsWithPersona.every(
      (it) =>
        it.persona_merge_path === "single" ||
        (it.persona_votes &&
          !it.persona_votes.strict &&
          !it.persona_votes.loose &&
          it.persona_votes.neutral !== undefined),
    );

  return (
    <div className="drawer-section">
      <div className="drawer-section-title">
        Evaluated Items ({itemScores.length})
      </div>

      {/* 2026-04-27: 3-Persona / Single Mode / 평균 spread 등 sub-agent ensemble 메타는
          사용자 정책상 비표시. 메인 결정은 판사 의견으로 통합. 판사/검수 카운트 배지만 노출. */}
      {itemsWithPersona.length > 0 && (judgeCount > 0 || reviewCount > 0) && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 6,
            marginBottom: 8,
            padding: "6px 8px",
            background: "rgba(148,163,184,0.08)",
            borderRadius: 4,
            fontSize: 10.5,
            justifyContent: "flex-end",
          }}
        >
          {judgeCount > 0 && (
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                background: "#ede9fe",
                color: "#5b21b6",
                padding: "1px 6px",
                borderRadius: 8,
              }}
            >
              🎭 판사 {judgeCount}건
            </span>
          )}
          {reviewCount > 0 && (
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                background: "#fee2e2",
                color: "#991b1b",
                padding: "1px 6px",
                borderRadius: 8,
              }}
            >
              ⚠ 검수 {reviewCount}건
            </span>
          )}
        </div>
      )}

      {items.map((num) => {
        const it = itemScores.find((x) => x.item_number === num);
        if (!it) return null;
        const mx = it.max_score ?? STT_MAX_SCORES[num] ?? 0;
        const sc = it.score ?? 0;

        let personaIndicator: React.ReactNode = null;
        if (it.persona_votes && typeof it.persona_votes === "object") {
          const pv = it.persona_votes;
          const nSuccess = (
            ["strict", "neutral", "loose"] as const
          ).filter((p) => pv[p] !== undefined && pv[p] !== null).length;
          const spread = Number(it.persona_step_spread ?? 0);
          const isJudge = it.persona_merge_path === "judge";
          const isSingle = it.persona_merge_path === "single";
          if (isSingle) {
            personaIndicator = (
              <span
                title="Single 모드 · Neutral 1명 판정"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  marginLeft: 6,
                  fontSize: 9,
                  fontWeight: 700,
                  padding: "1px 6px",
                  borderRadius: 8,
                  background: "#dbeafe",
                  color: "#1e3a8a",
                }}
              >
                N
              </span>
            );
          } else {
            personaIndicator = (
              <span
                title={`${nSuccess}/3 persona · spread ${spread}${isJudge ? " · 판사 경로" : ""}`}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  marginLeft: 6,
                  fontSize: 9,
                  fontWeight: 700,
                  padding: "1px 6px",
                  borderRadius: 8,
                  background: isJudge
                    ? "#ede9fe"
                    : spread >= 2
                      ? "#fed7aa"
                      : spread === 0
                        ? "#dcfce7"
                        : "#e0e7ff",
                  color: isJudge
                    ? "#5b21b6"
                    : spread >= 2
                      ? "#9a3412"
                      : spread === 0
                        ? "#166534"
                        : "#3730a3",
                }}
              >
                {isJudge ? "🎭" : spread === 0 ? "✓" : "⚠"} {nSuccess}/3
              </span>
            );
          }
        }

        const confNum =
          it.confidence && typeof it.confidence === "object"
            ? it.confidence.final
            : undefined;
        let confChip: React.ReactNode = null;
        if (confNum != null) {
          const cls =
            confNum >= 4
              ? { bg: "#dcfce7", fg: "#166534" }
              : confNum >= 2.5
                ? { bg: "#fef3c7", fg: "#92400e" }
                : { bg: "#fee2e2", fg: "#991b1b" };
          confChip = (
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                marginLeft: 6,
                fontSize: 9,
                fontWeight: 700,
                padding: "1px 6px",
                borderRadius: 8,
                background: cls.bg,
                color: cls.fg,
              }}
            >
              conf {Number(confNum).toFixed(1)}
            </span>
          );
        }

        return (
          <div
            key={num}
            className="drawer-info-row"
            style={{ alignItems: "flex-start", flexWrap: "wrap" }}
          >
            <span className="drawer-info-label">
              Item {num}
              {confChip}
              {personaIndicator}
            </span>
            <span
              className="drawer-info-value tabular-nums"
              style={{ color: scoreColor(sc, mx) }}
            >
              AI <b>{sc}</b>/{mx}
            </span>
            {(it.judgment || it.summary) && (
              <div
                style={{
                  width: "100%",
                  fontSize: 11,
                  color: "var(--ink-muted)",
                  marginTop: 4,
                  lineHeight: 1.5,
                }}
              >
                {String(it.judgment || it.summary || "").slice(0, 240)}
                {(it.judgment || it.summary || "").length > 240 && "…"}
              </div>
            )}
            {/* 🎭 판사 결정 — post-debate judge LLM 이 토론 transcript 보고 확정한 점수+근거+감점+인용 */}
            {it.judge_score != null && it.judge_reasoning && (
              <div
                style={{
                  width: "100%",
                  marginTop: 6,
                  padding: "8px 10px",
                  background: "#ede9fe",
                  border: "1px solid #c4b5fd",
                  borderRadius: 4,
                  fontSize: 11,
                  lineHeight: 1.55,
                }}
              >
                <div
                  style={{
                    fontWeight: 700,
                    color: "#5b21b6",
                    marginBottom: 4,
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                  }}
                >
                  🎭 판사 결정
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 600,
                      padding: "0 5px",
                      background: "#5b21b6",
                      color: "#fff",
                      borderRadius: 8,
                    }}
                  >
                    {it.judge_score}/{mx}
                  </span>
                </div>
                <div style={{ color: "#4c1d95", marginBottom: 4 }}>
                  <span style={{ fontWeight: 600 }}>판단:</span>{" "}
                  {String(it.judge_reasoning).slice(0, 400)}
                  {String(it.judge_reasoning).length > 400 && "…"}
                </div>
                {Array.isArray(it.judge_deductions) && it.judge_deductions.length > 0 && (
                  <div style={{ color: "#4c1d95", marginBottom: 4 }}>
                    <div style={{ fontWeight: 600, marginBottom: 2 }}>감점 근거:</div>
                    {it.judge_deductions.slice(0, 5).map(
                      (d: { reason?: string; points?: number }, i: number) => (
                        <div key={i} style={{ paddingLeft: 8, fontSize: 10.5 }}>
                          −{d.points}점 · {d.reason}
                        </div>
                      ),
                    )}
                  </div>
                )}
                {Array.isArray(it.judge_evidence) && it.judge_evidence.length > 0 && (
                  <div style={{ color: "#4c1d95" }}>
                    <div style={{ fontWeight: 600, marginBottom: 2 }}>인용:</div>
                    {it.judge_evidence.slice(0, 5).map(
                      (e: { speaker?: string; quote?: string }, i: number) => (
                        <div key={i} style={{ paddingLeft: 8, fontSize: 10.5 }}>
                          [{e.speaker || "?"}] &ldquo;{e.quote}&rdquo;
                        </div>
                      ),
                    )}
                  </div>
                )}
                {/* 판사가 인용한 HITL 인간 검수 사례 — qa-hitl-cases AOSS 인덱스 KNN 결과.
                    판사가 호출된 모든 항목 (judge_score 가 set) 에 대해 항상 표시 — 빈 결과면
                    "0건 — 매칭 사례 없음" 으로 명시해 RAG 시도 자체는 일어났음을 노출. */}
                {typeof it.judge_score === "number" && (
                  <div style={{ color: "#4c1d95", marginTop: 6 }}>
                    <div style={{ fontWeight: 600, marginBottom: 2 }}>
                      📚 판사 참조 HITL 사례 ({Array.isArray(it.judge_human_cases) ? it.judge_human_cases.length : 0}건)
                    </div>
                    {(!Array.isArray(it.judge_human_cases) || it.judge_human_cases.length === 0) ? (
                      <div
                        style={{
                          paddingLeft: 8,
                          fontSize: 10.5,
                          color: "var(--ink-muted)",
                          fontStyle: "italic",
                        }}
                      >
                        매칭 사례 없음 (qa-hitl-cases 인덱스에 #{it.item_number} 항목 사례 부재) — 판사 RAG 호출은 수행됨
                      </div>
                    ) : null}
                    {Array.isArray(it.judge_human_cases) && it.judge_human_cases.slice(0, 5).map(
                      (
                        c: {
                          consultation_id?: string;
                          item_number?: number;
                          ai_score?: number;
                          human_score?: number;
                          delta?: number;
                          confirmed_at?: string;
                          knn_score?: number;
                          transcript_excerpt?: string;
                          human_note?: string;
                          ai_judgment?: string;
                          external_id?: string;
                        },
                        i: number,
                      ) => {
                        const ai = c.ai_score ?? "—";
                        const hu = c.human_score ?? "—";
                        const dt = c.delta;
                        const dtSign =
                          typeof dt === "number" && dt > 0 ? "+" : "";
                        const dtColor =
                          typeof dt === "number"
                            ? dt > 0
                              ? "#15803d"
                              : dt < 0
                                ? "#b91c1c"
                                : "#6b7280"
                            : "#6b7280";
                        return (
                          <div
                            key={c.external_id || i}
                            style={{
                              paddingLeft: 8,
                              marginTop: 3,
                              padding: "4px 6px",
                              background: "rgba(76, 29, 149, 0.04)",
                              borderLeft: "2px solid #8b5cf6",
                              borderRadius: 3,
                            }}
                          >
                            <div
                              style={{
                                display: "flex",
                                gap: 6,
                                flexWrap: "wrap",
                                alignItems: "center",
                                fontSize: 10.5,
                                marginBottom: 2,
                              }}
                            >
                              <code
                                style={{
                                  fontWeight: 700,
                                  background: "#ede9fe",
                                  padding: "0 4px",
                                  borderRadius: 2,
                                }}
                              >
                                {c.consultation_id ?? "—"}
                              </code>
                              <span style={{ color: "var(--ink-muted)" }}>
                                #{c.item_number ?? "?"}
                              </span>
                              <span style={{ fontWeight: 600 }}>
                                AI {ai} → 人 {hu}
                              </span>
                              {typeof dt === "number" && (
                                <span
                                  style={{
                                    fontWeight: 700,
                                    color: dtColor,
                                  }}
                                >
                                  Δ {dtSign}
                                  {dt}
                                </span>
                              )}
                              {typeof c.knn_score === "number" && (
                                <span
                                  title="KNN 코사인 유사도"
                                  style={{
                                    fontSize: 9,
                                    background: "#f3e8ff",
                                    color: "#6b21a8",
                                    padding: "0 4px",
                                    borderRadius: 6,
                                    fontWeight: 700,
                                  }}
                                >
                                  cos {c.knn_score.toFixed(2)}
                                </span>
                              )}
                              {c.confirmed_at && (
                                <span
                                  style={{
                                    fontSize: 9,
                                    color: "var(--ink-muted)",
                                  }}
                                >
                                  {String(c.confirmed_at).slice(0, 10)}
                                </span>
                              )}
                            </div>
                            {c.transcript_excerpt && (
                              <div
                                style={{
                                  fontSize: 10,
                                  color: "var(--ink-soft)",
                                  paddingLeft: 4,
                                  whiteSpace: "pre-wrap",
                                  wordBreak: "break-word",
                                }}
                              >
                                <b>발화:</b> {c.transcript_excerpt}
                              </div>
                            )}
                            {c.human_note && (
                              <div
                                style={{
                                  fontSize: 10,
                                  color: "var(--ink-soft)",
                                  paddingLeft: 4,
                                  marginTop: 2,
                                  whiteSpace: "pre-wrap",
                                  wordBreak: "break-word",
                                }}
                              >
                                <b>검수자 코멘트:</b> {c.human_note}
                              </div>
                            )}
                          </div>
                        );
                      },
                    )}
                  </div>
                )}
              </div>
            )}
            {!it.judgment && !it.summary && !it.persona_votes && (
              <div
                style={{
                  width: "100%",
                  fontSize: 11,
                  color: "var(--ink-subtle)",
                  marginTop: 2,
                }}
              >
                {ITEM_NAMES[num] || ""}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** 노드별 RAG 사용 정책 — 백엔드 sub-agent 구현과 동기화.
 *  value:
 *    "full"       : Few-shot + Reasoning + (선택적) Business Knowledge 적극 사용
 *    "conditional": 조건부 사용 (예: language 는 refusal_count≥1 일 때만 #7 RAG)
 *    "none"       : Rule/사전 기반 — RAG 미사용 설계
 *    "rule_only"  : compliance/rule 전용 (privacy 등) — RAG 미사용
 */
const NODE_RAG_POLICY: Record<
  string,
  { mode: "full" | "conditional" | "none" | "rule_only"; note: string }
> = {
  greeting: { mode: "none", note: "Rule + LLM verify — 인사말/상담사명 고정 패턴 판정 (RAG 불필요)" },
  listening_comm: { mode: "full", note: "공감/대기안내 Few-shot + Reasoning 사용" },
  language: {
    mode: "conditional",
    note: "#6 정중한 표현 은 금지어 사전 기반(RAG 미사용), #7 쿠션어 는 거절 상황(refusal_count≥1) 발생 시만 RAG",
  },
  needs: { mode: "full", note: "니즈 파악 Few-shot + Reasoning 사용" },
  explanation: { mode: "full", note: "설명력·두괄식 Few-shot + Reasoning 사용" },
  proactiveness: { mode: "full", note: "적극성 Few-shot + Reasoning 사용" },
  work_accuracy: {
    mode: "full",
    note: "#15 정확한 안내 는 업무 지식(Business Knowledge) RAG 추가 참조",
  },
  privacy: { mode: "rule_only", note: "compliance_based — 규정 패턴 탐지 (RAG 미대상)" },
};

/** RAG Hits 가 비어있을 때 tenant / 데이터 존재 / raw rag_evidence 내용을 노출해
 *  사용자가 백엔드 어느 단계에서 끊기는지 직관적으로 파악할 수 있게 한다. */
function RagDiagnosticBox({
  nodeId,
  tenantId,
  itemScores,
}: {
  nodeId: string;
  tenantId: string;
  itemScores: CategoryItem[];
}) {
  const policy = NODE_RAG_POLICY[nodeId];

  // RAG 미대상 노드 (none / rule_only) — 진단 박스 대신 정보 박스로 친절히 안내.
  if (policy && (policy.mode === "none" || policy.mode === "rule_only")) {
    return (
      <div className="drawer-section">
        <div className="drawer-section-title">RAG Hits</div>
        <div
          style={{
            padding: "10px 12px",
            fontSize: 11,
            lineHeight: 1.55,
            color: "var(--ink-muted)",
            background: "var(--surface-muted)",
            border: "1px dashed var(--border)",
            borderRadius: "var(--radius-sm)",
          }}
        >
          <div style={{ fontWeight: 700, color: "var(--ink)", marginBottom: 4 }}>
            ℹ 이 노드는 RAG 미사용 — 설계상 정상
          </div>
          <div style={{ fontSize: 10.5 }}>{policy.note}</div>
        </div>
      </div>
    );
  }
  // 첫 번째 item 의 rag_evidence 를 살펴봄 — 없으면 백엔드가 아예 필드를 안 싣는 것.
  const firstItem = itemScores[0] as (CategoryItem & { rag_evidence?: Record<string, unknown> }) | undefined;
  const re = firstItem?.rag_evidence;
  const hasReField = re != null && typeof re === "object";

  // conditional 모드 — rag_evidence 필드가 없으면 "조건 미충족 = 정상" 으로 친절히 안내.
  // (예: language 는 거절 상황 없으면 #7 RAG 호출 안 함)
  if (policy && policy.mode === "conditional" && !hasReField) {
    return (
      <div className="drawer-section">
        <div className="drawer-section-title">RAG Hits</div>
        <div
          style={{
            padding: "10px 12px",
            fontSize: 11,
            lineHeight: 1.55,
            color: "var(--ink-muted)",
            background: "var(--surface-muted)",
            border: "1px dashed var(--border)",
            borderRadius: "var(--radius-sm)",
          }}
        >
          <div style={{ fontWeight: 700, color: "var(--ink)", marginBottom: 4 }}>
            ℹ 조건부 RAG — 이번 상담에서는 호출 조건 미충족
          </div>
          <div style={{ fontSize: 10.5 }}>{policy.note}</div>
        </div>
      </div>
    );
  }

  const fewshotLen =
    hasReField && Array.isArray(re.fewshot_details)
      ? (re.fewshot_details as unknown[]).length
      : 0;
  const fewshotIds =
    hasReField && Array.isArray(re.fewshot_ids)
      ? (re.fewshot_ids as unknown[]).length
      : 0;
  const knowledgeLen =
    hasReField && Array.isArray(re.knowledge_details)
      ? (re.knowledge_details as unknown[]).length
      : 0;
  const reasoningStdev =
    hasReField && typeof re.reasoning_stdev === "number"
      ? (re.reasoning_stdev as number)
      : null;

  const isTenantRagCapable =
    tenantId === "kolon" || tenantId === "cartgolf" || tenantId === "shinhan";

  return (
    <div className="drawer-section">
      <div className="drawer-section-title">RAG Hits</div>
      <div
        style={{
          padding: "10px 12px",
          fontSize: 11,
          lineHeight: 1.55,
          color: "var(--ink-muted)",
          background: "var(--surface-muted)",
          border: "1px dashed var(--border)",
          borderRadius: "var(--radius-sm)",
        }}
      >
        <div style={{ marginBottom: 6 }}>
          이 노드({nodeId})의 RAG 히트가 비어 있습니다 — 진단 정보:
        </div>
        <table
          style={{
            width: "100%",
            fontSize: 10.5,
            borderCollapse: "collapse",
            marginBottom: 6,
          }}
        >
          <tbody>
            <tr>
              <td style={{ padding: "2px 6px", color: "var(--ink-muted)", width: "45%" }}>
                tenant
              </td>
              <td style={{ padding: "2px 6px", fontWeight: 700, color: isTenantRagCapable ? "var(--success)" : "var(--warn)" }}>
                {tenantId || "(없음)"} {isTenantRagCapable ? "✓ RAG 대상" : "⚠ RAG 미연결"}
              </td>
            </tr>
            <tr>
              <td style={{ padding: "2px 6px", color: "var(--ink-muted)" }}>
                rag_evidence 필드
              </td>
              <td style={{ padding: "2px 6px", fontWeight: 700, color: hasReField ? "var(--success)" : "var(--danger)" }}>
                {hasReField ? "✓ 존재" : "✗ 없음 (sub-agent 가 rag 호출 안 함)"}
              </td>
            </tr>
            {hasReField && (
              <>
                <tr>
                  <td style={{ padding: "2px 6px", color: "var(--ink-muted)" }}>
                    fewshot_details / ids
                  </td>
                  <td style={{ padding: "2px 6px", fontWeight: 700 }}>
                    {fewshotLen} / {fewshotIds}
                    {fewshotLen === 0 && fewshotIds === 0 && (
                      <span style={{ color: "var(--danger)", marginLeft: 6 }}>
                        ✗ AOSS 쿼리 실패 or RAGError 캐치
                      </span>
                    )}
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "2px 6px", color: "var(--ink-muted)" }}>
                    업무지식 RAG hits
                  </td>
                  <td style={{ padding: "2px 6px", fontWeight: 700 }}>
                    {knowledgeLen}건
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "2px 6px", color: "var(--ink-muted)" }}>
                    reasoning_stdev
                  </td>
                  <td style={{ padding: "2px 6px", fontWeight: 700 }}>
                    {reasoningStdev != null ? reasoningStdev.toFixed(3) : "(null — reasoning 미수행)"}
                  </td>
                </tr>
              </>
            )}
          </tbody>
        </table>
        {hasReField && fewshotLen === 0 && isTenantRagCapable && (
          <div
            style={{
              marginTop: 4,
              padding: "6px 8px",
              background: "rgba(239,68,68,0.08)",
              border: "1px solid rgba(239,68,68,0.2)",
              borderRadius: 4,
              color: "#991b1b",
            }}
          >
            ⚠ tenant 는 RAG 대상인데 백엔드 retrieve 가 빈 결과를 반환.
            서버 로그에서 <code className="kbd">AOSS golden retrieve 실패</code> 또는{" "}
            <code className="kbd">Titan embed 실패</code> / <code className="kbd">RAGError</code> 확인 필요.
          </div>
        )}
      </div>
    </div>
  );
}
