// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useMemo, useState } from "react";

import { useAppState } from "@/lib/AppStateContext";
import { ITEM_NAMES, STT_MAX_SCORES, scoreColor } from "@/lib/items";
import { computeClientGtComparison } from "@/lib/manualEvalMapper";
import { NODE_DEFS, NODE_ITEMS } from "@/lib/pipeline";
import {
  aggregateRagHitsByAgent,
  buildPartialAgentBundle,
} from "@/lib/ragHitsAggregator";
import type {
  PersonaHitlCaseLike,
  RagFewshotDetail,
} from "@/lib/ragHitsAggregator";
import type {
  CategoryItem,
  EvaluationResult,
  GtComparison,
} from "@/lib/types";

import AwsResourcesPanel from "./AwsResourcesPanel";
import { KmsReportCard, type KmsEvaluation } from "./KmsReportCard";
import PersonaExecutionDetails from "./PersonaExecutionDetails";
// ★ 2026-05-08: 두 RAG panel (RagHitsPanel + PersonaHitlSection) 을 example_id 단위로 dedup
//   해 1개 panel 로 통합. 기존 RagHitsPanel / PersonaHitlSection 은 dead code (외부 import 없음).
import UnifiedRagPanel from "./UnifiedRagPanel";

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

  // ★ 2026-05-07: 평가항목 필터 — early return 보다 위에 hook 호출 (Rules of Hooks).
  const [selectedItemNumber, setSelectedItemNumber] = useState<number | null>(null);
  useEffect(() => {
    setSelectedItemNumber(null);
  }, [nodeId]);

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

  // ★ 2026-05-07: 평가항목 필터 (다중 항목 노드 #6/#7, #10/#11 등에서 한 항목만 보기).
  // useState/useEffect 는 early return 위에 위치 — 여기는 plain 계산 (hook 아님).
  const itemNumbersInScopeSet = new Set<number>();
  for (const it of itemScores) {
    if (typeof it.item_number === "number") itemNumbersInScopeSet.add(it.item_number);
  }
  for (const n of items) itemNumbersInScopeSet.add(n);
  const itemNumbersInScope = Array.from(itemNumbersInScopeSet).sort((a, b) => a - b);

  const filteredItemScores =
    selectedItemNumber == null
      ? itemScores
      : itemScores.filter((it) => it.item_number === selectedItemNumber);
  const filteredItems =
    selectedItemNumber == null
      ? items
      : items.filter((n) => n === selectedItemNumber);
  const filteredNodeDeductions =
    selectedItemNumber == null
      ? nodeDeductions
      : nodeDeductions.filter((d) => {
          const num = Number(d.item_number ?? d.item);
          return num === selectedItemNumber;
        });

  const gc: GtComparison | null | undefined = gcServer ?? gcClient;
  const isGtNode = nodeId === "gt_comparison";
  const isTenantNode = nodeId === "tenant_config";
  const isKmsNode = nodeId === "kms";
  // ★ 2026-05-08: Layer 4 시각 노드 (백엔드 layer4 fan-out) — 평가 항목이 아닌 메타 단계.
  // 이 노드들에서는 persona / 골든셋 RAG / Evaluated Items 섹션 비활성화 (데이터 없음 + UI 노이즈).
  // 대신 Layer4Section 이 dedicated sub-panel 로 confidence·tier·evidence·report·GT-evidence 표시.
  const LAYER4_NODE_IDS = new Set([
    "confidence",
    "tier_router",
    "evidence_refiner",
    "layer4",
    "gt_evidence_comparison",
  ]);
  const isLayer4Node = LAYER4_NODE_IDS.has(nodeId);
  // ★ 2026-05-08: 평가 sub-agent 8종 — RAG hits / 페르소나 / item-level 점수 탭은 이 노드에서만 의미 있음.
  // 그 외 (combined_report, report_narrator, layer1/2/3, kms, system, gt_comparison, tenant_config 등)
  // 은 메타/리포팅 단계이므로 골든셋 RAG · 페르소나 참조 자료 섹션을 노출하면 사용자에게 혼란.
  // 음수 gating (`!isKmsNode && !isLayer4Node && ...`) 대신 positive whitelist 로 단순화.
  const EVAL_NODE_IDS = new Set([
    "greeting",
    "listening_comm",
    "language",
    "needs",
    "explanation",
    "proactiveness",
    "work_accuracy",
    "privacy",
  ]);
  const isEvalNode = EVAL_NODE_IDS.has(nodeId);
  // ★ 2026-05-08: System / aggregator 노드 (평가 sub-agent 가 아닌 파이프라인 메타 단계).
  // 기존엔 "Status: done" 만 떠서 빈 드로어. SystemNodeSection 이 dedicated 패널 노출.
  // kms 는 별도 KmsReportCard 로 이미 처리되므로 미포함.
  const SYSTEM_NODE_IDS = new Set([
    "layer1",
    "layer2_barrier",
    "layer3",
    "combined_report",
    "report_narrator",
  ]);
  const isSystemNode = SYSTEM_NODE_IDS.has(nodeId);

  // KMS 결과 추출 — result 에서 직접, 또는 live trace output 에서 폴백
  let kmsEvaluation: KmsEvaluation | null = null;
  if (isKmsNode) {
    const r = result as unknown as {
      kms_evaluation?: KmsEvaluation;
      state?: { kms_evaluation?: KmsEvaluation };
    } | null;
    kmsEvaluation =
      r?.kms_evaluation || r?.state?.kms_evaluation || null;
    // 폴백: 최신 trace output 의 kms_evaluation (평가 진행 중에도 노출)
    if (!kmsEvaluation && liveTraces.length > 0) {
      for (let i = liveTraces.length - 1; i >= 0; i--) {
        const t = liveTraces[i];
        const detail = t?.detail as
          | {
              output?: { kms_evaluation?: KmsEvaluation };
              kms_evaluation?: KmsEvaluation;
            }
          | undefined;
        const fromTrace =
          detail?.output?.kms_evaluation || detail?.kms_evaluation || null;
        if (fromTrace) {
          kmsEvaluation = fromTrace;
          break;
        }
      }
    }
  }

  const nodeErrMsg = appState.nodeErrors?.[nodeId];
  // ragHitsByAgent 1차 경로가 비어 있으면 itemScores 로 직접 2차 집계 — 어떤 소스에서든
  // rag_evidence 가 1곳이라도 보이면 panel 이 뜨도록 보강.
  // NOTE: 여기는 early return 뒤 영역이라 hook(useMemo) 사용 불가 — inline 계산 (비용 낮음).
  const ragHitsPrimary = ragHitsByAgent[nodeId];
  const ragHitsFromItems =
    itemScores.length > 0
      ? aggregateRagHitsByAgent({ evaluations: itemScores }, [])[nodeId]
      : undefined;
  // 2026-05-08 — `rag_hits_ready` SSE 로 도착한 partial hits (토론 시작 전).
  // 정식 result.rag_evidence 가 비어있을 때만 사용 → 토론 finalized 후 자동으로 가려짐.
  const ragHitsPartialRaw = appState.ragHitsPartialByNode?.[nodeId];
  const ragHitsPartialBundle = ragHitsPartialRaw
    ? buildPartialAgentBundle(ragHitsPartialRaw)
    : undefined;
  const primaryHasData =
    !!ragHitsPrimary &&
    (ragHitsPrimary.hasGS || ragHitsPrimary.hasRS || ragHitsPrimary.hasBK);
  const itemsHasData =
    !!ragHitsFromItems &&
    (ragHitsFromItems.hasGS || ragHitsFromItems.hasRS || ragHitsFromItems.hasBK);
  const ragHits = primaryHasData
    ? ragHitsPrimary
    : itemsHasData
      ? ragHitsFromItems
      : ragHitsPartialBundle;
  // 라이브 partial hits 표시 여부 — 정식 데이터가 아직 안 도착했고 partial 만 있는 경우.
  // 2026-05-08 — 노드가 이미 "done" (debate finalized) 이면 partial bundle 이 잔존해도
  // "라이브 (토론 시작 전)" 배지를 노출하지 않도록 강제 false. node-level "done" 또는
  // debate-level "done" 둘 중 하나라도 만족하면 토론이 종료된 상태이므로 라이브 표시 부적절.
  const debateDoneForNode =
    appState.debateStatusByNode?.[nodeId] === "done";
  const nodeIsDone = state === "done";
  const ragHitsIsLive =
    !!ragHitsPartialBundle &&
    !primaryHasData &&
    !itemsHasData &&
    !debateDoneForNode &&
    !nodeIsDone;

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

        {/* kms 노드 — 인텐트별 KMS 평가 결과 카드 (결과 미도착 시 안내) */}
        {isKmsNode && (
          <div className="drawer-section">
            <div className="drawer-section-title">KMS 평가</div>
            {kmsEvaluation ? (
              <KmsReportCard kmsEvaluation={kmsEvaluation} />
            ) : (
              <div
                style={{
                  padding: "12px 14px",
                  background: "var(--surface-muted)",
                  border: "1px dashed var(--border)",
                  borderRadius: 6,
                  fontSize: 12,
                  color: "var(--ink-muted)",
                }}
              >
                {state === "active"
                  ? "KMS 평가 실행 중 — 결과 도착 즉시 표시됩니다."
                  : state === "done"
                    ? "결과 적재 대기 중 (state.kms_evaluation 미도착)."
                    : "아직 실행되지 않았습니다."}
              </div>
            )}
          </div>
        )}

        {/* ★ 2026-05-08: System / aggregator 노드 (layer1 / layer2_barrier / layer3 /
            combined_report / report_narrator) — 평가 sub-agent 가 아닌 파이프라인
            메타 단계. dedicated 패널로 단계별 데이터 표시. */}
        {isSystemNode && (
          <SystemNodeSection
            nodeId={nodeId}
            result={result}
            allItems={allItems}
            allDeductions={allDeductions}
            liveTraces={liveTraces}
            nodeStates={nodeStates}
            state={state}
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

        {/* ★ 2026-05-07: 평가항목 필터 탭 — 다중 항목 노드 (#6/#7, #10/#11 등) 에서 항목별 보기 전환.
            한 항목만 있으면 탭 숨김 (visual noise 방지). 평가 sub-agent 가 아닌 노드는 항목 단위 아님 → 비노출. */}
        {isEvalNode && itemNumbersInScope.length > 1 && (
          <div
            style={{
              display: "flex",
              gap: 8,
              marginBottom: 14,
              padding: "10px 12px",
              background: "var(--surface-muted)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              flexWrap: "wrap",
              alignItems: "center",
            }}
          >
            <span
              style={{
                fontSize: 9.5,
                fontWeight: 700,
                color: "var(--ink-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.08em",
                marginRight: 4,
              }}
            >
              평가 항목
            </span>
            <button
              type="button"
              onClick={() => setSelectedItemNumber(null)}
              style={{
                padding: "4px 12px",
                fontSize: 11,
                fontWeight: 700,
                color:
                  selectedItemNumber == null ? "var(--bg)" : "var(--ink)",
                background:
                  selectedItemNumber == null ? "var(--ink)" : "var(--surface)",
                border: "1.5px solid var(--ink)",
                borderRadius: "var(--radius-pill)",
                cursor: "pointer",
                transition:
                  "background 0.15s ease, color 0.15s ease, transform 0.12s ease",
                fontFamily: "'Mark For MC', var(--font-sans), sans-serif",
              }}
            >
              전체
            </button>
            {itemNumbersInScope.map((n) => {
              const active = selectedItemNumber === n;
              return (
                <button
                  key={n}
                  type="button"
                  onClick={() => setSelectedItemNumber(n)}
                  style={{
                    padding: "4px 12px",
                    fontSize: 11,
                    fontWeight: 700,
                    color: active ? "var(--bg)" : "var(--ink)",
                    background: active ? "var(--accent)" : "var(--surface)",
                    border: `1.5px solid ${active ? "var(--accent)" : "var(--ink)"}`,
                    borderRadius: "var(--radius-pill)",
                    cursor: "pointer",
                    transition:
                      "background 0.15s ease, color 0.15s ease, border-color 0.15s ease",
                    fontFamily: "'Mark For MC', var(--font-sans), sans-serif",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  #{n}
                </button>
              );
            })}
          </div>
        )}

        {/* Evaluated Items — 이 sub-agent 의 항목 스코어 + persona meta */}
        {isEvalNode && filteredItemScores.length > 0 && (
          <EvaluatedItemsSection
            items={filteredItems}
            itemScores={filteredItemScores}
            totalItemCount={filteredItems.length}
          />
        )}

        {/* Layer 4 dedicated section — confidence / tier / evidence / report / GT evidence.
            평가 sub-agent 가 아니라 메타 단계이므로 persona/RAG 섹션 대신 전용 패널 노출. */}
        {isLayer4Node && (
          <Layer4Section
            nodeId={nodeId}
            result={result}
            allItems={allItems}
            allDeductions={allDeductions}
            liveTraces={liveTraces}
          />
        )}

        {/* 3-Persona 실행 상세 — 페르소나별 점수/판정 사유 표시.
            mode_majority 같은 머지 메타 라벨은 PersonaExecutionDetails 내부에서 숨김.
            평가 sub-agent (8종) 외 노드는 페르소나 토론 / 골든셋 RAG 모두 비노출. */}
        {isEvalNode && (
          <PersonaExecutionDetails
            nodeId={nodeId}
            items={filteredItems}
            itemScores={filteredItemScores}
            state={state}
          />
        )}

        {/* ★ 2026-04-30: 골든셋 RAG umbrella — 시드(GS-*) + HITL 누적(qa-hitl-cases) 통합 섹션.
            이전엔 RAG Hits 와 PersonaHitlSection 이 분리되어 있어 "두 출처가 별개" 처럼 보였음.
            사용자 멘탈 모델: "🌟 골든셋 RAG → 🌱 시드 + 📚 HITL → 🔁 자기상담". 하나의 부모 wrapper 로 묶음.
            ★ 2026-05-08: 평가 sub-agent 8종 외 노드 (combined_report / report_narrator / layer1·2·3 /
            kms / system 등) 는 RAG hits 가 의미 없으므로 positive whitelist 로 가드. */}
        {isEvalNode && (
        <div
          style={{
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-lg)",
            padding: "16px 18px",
            background: "var(--surface)",
            marginBottom: 16,
          }}
        >
          <div
            style={{
              fontSize: 10.5,
              fontWeight: 700,
              color: "var(--accent-strong)",
              marginBottom: 6,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: "var(--accent)",
                display: "inline-block",
              }}
            />
            골든셋 RAG · 페르소나 참조 자료
            {/* Reranker 신호등 — 활성 + 실제 호출 성공 시 초록 배지 */}
            {appState.rerankerRuntime?.actually_active && (
              <span
                title={`Cohere Rerank 3.5 활성 — ${appState.rerankerRuntime.success}회 호출 성공 (${appState.rerankerRuntime.documents_reranked ?? 0} docs 재정렬)`}
                style={{
                  marginLeft: 6,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "1px 7px",
                  fontSize: 9.5,
                  fontWeight: 700,
                  letterSpacing: "0.04em",
                  color: "#166534",
                  background: "#dcfce7",
                  border: "1px solid #bbf7d0",
                  borderRadius: "var(--radius-pill)",
                  textTransform: "none",
                }}
              >
                <span
                  aria-hidden="true"
                  style={{
                    display: "inline-block",
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background: "#16a34a",
                  }}
                />
                🎯 Reranker 활성
              </span>
            )}
            {appState.rerankerRuntime?.enabled &&
              !appState.rerankerRuntime?.actually_active &&
              (appState.rerankerRuntime?.fail ?? 0) > 0 && (
                <span
                  title={`Reranker 호출 실패: ${appState.rerankerRuntime?.last_error ?? "원인 불명"}`}
                  style={{
                    marginLeft: 6,
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    padding: "1px 7px",
                    fontSize: 9.5,
                    fontWeight: 700,
                    letterSpacing: "0.04em",
                    color: "#991b1b",
                    background: "#fee2e2",
                    border: "1px solid #fecaca",
                    borderRadius: "var(--radius-pill)",
                    textTransform: "none",
                  }}
                >
                  ⚠ Reranker 실패
                </span>
              )}
          </div>
          <div
            title="모든 사례는 사람 검수 정답 골든셋. 🌱 시드 = 운영 시작 시 검수 / 📚 HITL = 사용자 검수 누적 / 🔁 자기상담 = 동일 cid 매칭"
            style={{
              fontSize: 14,
              fontWeight: 500,
              color: "var(--ink-display)",
              marginBottom: 14,
              letterSpacing: "-0.01em",
            }}
          >
            🌟 골든셋 — 페르소나 참조 사례
          </div>

          {/* 2026-05-08 — 항목별 "RAG 사용 안 함" 안내.
              사용자가 #6 같은 LLM 단독 항목 탭을 선택했고 백엔드가 rag_disabled_for_item
              플래그로 보내면 표시. 전역 ragDisabled 보다 우선 (item-specific). */}
          {(() => {
            const itemRagDisabledMap =
              ragHitsPartialRaw?.rag_disabled_by_item || {};
            const itemRagDisabledReason =
              selectedItemNumber != null
                ? itemRagDisabledMap[selectedItemNumber]
                : undefined;
            if (selectedItemNumber != null && itemRagDisabledReason) {
              return (
                <div
                  style={{
                    padding: "14px 16px",
                    background: "rgba(100,116,139,0.06)",
                    border: "1px dashed rgba(100,116,139,0.45)",
                    borderRadius: 8,
                    fontSize: 12,
                    lineHeight: 1.6,
                    color: "var(--ink-soft)",
                  }}
                >
                  <div
                    style={{
                      fontSize: 13,
                      fontWeight: 700,
                      color: "#475569",
                      marginBottom: 6,
                      letterSpacing: "-0.01em",
                    }}
                  >
                    🚫 #{selectedItemNumber} — RAG 사용 안 함
                  </div>
                  <div style={{ color: "var(--ink-muted)" }}>
                    {itemRagDisabledReason}
                  </div>
                  <div style={{ color: "var(--ink-muted)", marginTop: 4 }}>
                    토론은 정상 진행되며 페르소나는 RAG 컨텍스트 없이 LLM 판정만으로
                    의견을 형성합니다.
                  </div>
                </div>
              );
            }
            return null;
          })()}

          {/* RAG 전역 비활성 모드 안내 — 토글 현재값 또는 마지막 실행값 둘 중 하나만
              true 여도 안내 카드 표시 (사용자가 토글 켜고 평가 시작 전이어도 즉시 반영).
              백엔드 진입점 4종이 SKIPPED 되므로 RagHitsPanel/RagDiagnosticBox 둘 다 숨김. */}
          {(appState.ragDisabled || appState.ragDisabledInLastRun) ? (
            <div
              style={{
                padding: "14px 16px",
                background: "rgba(239,68,68,0.06)",
                border: "1px dashed rgba(239,68,68,0.45)",
                borderRadius: 8,
                fontSize: 12,
                lineHeight: 1.6,
                color: "var(--ink-soft)",
              }}
            >
              <div
                style={{
                  fontSize: 13,
                  fontWeight: 700,
                  color: "#991b1b",
                  marginBottom: 6,
                  letterSpacing: "-0.01em",
                }}
              >
                🚫 RAG 비활성화됨 — 이번 평가는 RAG 미사용 모드로 실행
              </div>
              <div style={{ color: "var(--ink-muted)" }}>
                상단 토글에서 <b>RAG OFF</b> 로 평가했기 때문에 모든 종류의 RAG 호출이
                SKIP 되었습니다 — Golden-set fewshot · Reasoning stdev · 업무지식 RAG ·
                HITL 골든셋 anchor. #15 (정확한 안내) 는 업무지식 RAG 부재 → unevaluable
                분기로 빠져 평가/토론도 함께 skip 됩니다. LLM 단독 판정 결과로 RAG-ON
                결과와 비교해 보세요.
              </div>
            </div>
          ) : (() => {
            // ★ 2026-05-08: 두 RAG panel 통합 — example_id 단위 dedup.
            // sub-agent fewshot_details (RagAgentBundle) + 페르소나 persona_hitl_cases
            // (CategoryItem) 를 평탄화해 UnifiedRagPanel 1개로 표시.
            //
            // 케이스:
            //  1. partial 라이브 모드 — fewshot 만 있고 persona 없음 → 라이브 배지 + sub-agent only.
            //  2. 평가 완료 (debate 적용 후) — 양쪽 다 있음 → 정상 통합 + bothUsed/Only 통계.
            //  3. debate 비대상 노드 (greeting / privacy 등) — persona_hitl_cases 항상 [] →
            //     bothUsed=0, subAgentOnly 만 표시 (또는 RAG 미사용 노드 경우 빈 hits).
            //  4. ragHits 가 비고 itemScores 만 있는 경우 — RagDiagnosticBox 로 폴백.

            // sub-agent fewshot_details 평탄화 — perItem 우선, 없으면 fewshot[].
            const flatFewshot: RagFewshotDetail[] = [];
            if (ragHits) {
              const seen = new Set<string>();
              const pi = ragHits.perItem || {};
              Object.values(pi).forEach((bucket) => {
                (bucket?.fewshot || []).forEach((d) => {
                  const eid = String(d.example_id || "");
                  if (eid && seen.has(eid)) return;
                  if (eid) seen.add(eid);
                  flatFewshot.push(d);
                });
              });
              // perItem 에 누락된 fewshot (item_number 미부여 케이스) 도 fallback 으로 포함.
              (ragHits.fewshot || []).forEach((d) => {
                const eid = String(d.example_id || "");
                if (eid && seen.has(eid)) return;
                if (eid) seen.add(eid);
                flatFewshot.push(d);
              });
            }

            // persona_hitl_cases 평탄화 — filteredItemScores 의 각 item 에서.
            type _CIWithCases = {
              persona_hitl_cases?: PersonaHitlCaseLike[];
              judge_human_cases?: PersonaHitlCaseLike[];
            };
            const flatPersona: PersonaHitlCaseLike[] = [];
            // ★ 2026-05-08: selectedItemNumber 매칭 우선 — sub-agent fewshot 와 페르소나 RAG
            // 가 같은 item 기준으로 비교되어야 "검색어 통일" 표시가 정확. 이전엔 단순 첫
            // 후보를 채택해 다른 item 의 query 와 짝지어져 두 박스가 분리되어 보이는 케이스.
            const personaQueryByItem: Record<number, string> = {};
            const personaQueryCandidates: string[] = [];
            filteredItemScores.forEach((it) => {
              const wide = it as unknown as _CIWithCases;
              const persona = Array.isArray(wide.persona_hitl_cases)
                ? wide.persona_hitl_cases
                : [];
              const legacy = Array.isArray(wide.judge_human_cases)
                ? wide.judge_human_cases
                : [];
              const cases = persona.length > 0 ? persona : legacy;
              cases.forEach((c) => {
                // item_number 가 case 자체에 누락이면 부모 item_number 채움.
                flatPersona.push({
                  ...c,
                  item_number: c.item_number ?? Number(it.item_number),
                });
              });
              if (it.persona_rag_query) {
                const q = String(it.persona_rag_query);
                personaQueryCandidates.push(q);
                const itemNo = Number(it.item_number);
                if (Number.isFinite(itemNo)) {
                  personaQueryByItem[itemNo] = q;
                }
              }
            });
            const personaQueryFirst = (() => {
              if (selectedItemNumber != null && personaQueryByItem[selectedItemNumber]) {
                return personaQueryByItem[selectedItemNumber];
              }
              return personaQueryCandidates.length > 0 ? personaQueryCandidates[0] : null;
            })();

            // ★ 2026-05-08: sub-agent fewshot query 도 selectedItemNumber 우선 매칭 →
            // 페르소나 query 와 비교 시 같은 item 기준 → 통일 박스로 정확히 표시.
            const subAgentQuery = (() => {
              if (selectedItemNumber != null && ragHits?.perItem) {
                const piQ = ragHits.perItem[selectedItemNumber]?.queries?.fewshot;
                if (typeof piQ === "string" && piQ.trim()) return piQ;
                if (Array.isArray(piQ) && piQ[0]) return String(piQ[0]);
              }
              return ragHits?.queries?.fewshot?.[0] || null;
            })();
            const intent = ragHits?.queries?.intent || null;

            const hasUnifiedData =
              flatFewshot.length > 0 || flatPersona.length > 0;

            if (hasUnifiedData) {
              return (
                <UnifiedRagPanel
                  fewshotDetails={flatFewshot}
                  personaCases={flatPersona}
                  filterItemNumber={selectedItemNumber ?? undefined}
                  subAgentQuery={subAgentQuery}
                  personaQuery={personaQueryFirst}
                  intent={intent}
                  isLive={ragHitsIsLive}
                />
              );
            }
            // 2026-05-08 — 선택 항목이 백엔드 rag_disabled_by_item 에 포함되면
            // 위쪽에 이미 "🚫 #N — RAG 사용 안 함" 안내 카드가 노출되므로
            // RagDiagnosticBox 의 "조건부 RAG / 호출 조건 미충족" 폴백 메시지가
            // 중복으로 쌓이지 않도록 여기서 일찍 종료.
            const itemRagDisabledForSelected =
              selectedItemNumber != null &&
              !!ragHitsPartialRaw?.rag_disabled_by_item?.[selectedItemNumber];
            if (itemRagDisabledForSelected) {
              return null;
            }
            if (filteredItems.length > 0 && filteredItemScores.length > 0) {
              return (
                <RagDiagnosticBox
                  nodeId={nodeId}
                  tenantId={appState.siteId || appState.tenantId || ""}
                  itemScores={filteredItemScores}
                />
              );
            }
            return null;
          })()}
        </div>
        )}

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
        {filteredNodeDeductions.length > 0 && (
          <div className="drawer-section">
            <div className="drawer-section-title">Deductions</div>
            {filteredNodeDeductions.map((d, i) => (
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

        {/* "데이터 도착 안 함" 빈 상태 — 평가 sub-agent 노드에 한해 표시.
            Layer4 / KMS / GT / Tenant / combined_report / report_narrator / layer1·2·3 등은
            전용 panel 또는 raw output (live trace) 로 자체 노출되므로 이 빈 상태가 노이즈가 됨. */}
        {isEvalNode && itemScores.length === 0 && !nodeErrMsg && (
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
                {/* ★ 2026-04-30: 페르소나 참조 골든셋은 RAG Hits 영역(PersonaHitlSection)으로 이동.
                    여기(판사 결정 카드 안) 에 두면 "토론 페르소나가 참조한 사례"라는 출처가
                    "판사가 본 자료"로 잘못 읽혔음 (사용자 보고 2026-04-30). */}
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
            <ConfidenceDetailPanel confidence={(it as unknown as { confidence?: ConfidenceLike }).confidence} />
            <RagReferencePanel evaluation={it} />
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

// ===========================================================================
// ConfidenceDetailPanel — PDF §8.1 4-신호 분해 패널 (NodeDrawer 항목 카드 내부).
// confidence chip 직후 펼침 토글 형태로 노출. 데이터 없으면 미렌더.
// ===========================================================================
type ConfidenceLike = {
  final?: number;
  signals?: {
    llm_self?: number;
    rule_llm_agreement?: boolean;
    rag_stdev?: number | null;
    evidence_quality?: "high" | "medium" | "low" | string;
    weighted_composite?: number;
    rag_sample_size?: number | null;
    rag_small_sample_penalty_applied?: boolean;
  };
};

function ConfidenceDetailPanel({ confidence }: { confidence?: ConfidenceLike }) {
  const [open, setOpen] = useState(false);
  const signals = confidence?.signals;
  if (!signals || typeof signals !== "object") return null;

  const {
    llm_self,
    rule_llm_agreement,
    rag_stdev,
    evidence_quality,
    weighted_composite,
    rag_sample_size,
    rag_small_sample_penalty_applied,
  } = signals;

  // 모든 핵심 신호가 비어있으면 미렌더
  const hasAny =
    llm_self != null ||
    rule_llm_agreement != null ||
    rag_stdev != null ||
    evidence_quality != null ||
    weighted_composite != null;
  if (!hasAny) return null;

  const finalNum =
    typeof confidence?.final === "number" ? confidence.final : null;

  // 색상 팔레트
  const COLOR_GOOD = "#16a34a";
  const COLOR_WARN = "#f59e0b";
  const COLOR_BAD = "#ef4444";
  const COLOR_NEUTRAL = "#94a3b8";

  // LLM Self 1~5
  const llmSelfPct =
    typeof llm_self === "number" ? Math.max(0, Math.min(100, (llm_self / 5) * 100)) : 0;
  const llmSelfColor =
    typeof llm_self !== "number"
      ? COLOR_NEUTRAL
      : llm_self >= 4
        ? COLOR_GOOD
        : llm_self >= 2.5
          ? COLOR_WARN
          : COLOR_BAD;

  // Rule vs LLM 일치
  const ruleColor =
    rule_llm_agreement === true
      ? COLOR_GOOD
      : rule_llm_agreement === false
        ? COLOR_BAD
        : COLOR_NEUTRAL;

  // RAG stdev (낮을수록 좋음). 0~3 스케일 가정 → 색상: <0.7 good, <1.5 warn, else bad
  const stdevColor =
    typeof rag_stdev !== "number"
      ? COLOR_NEUTRAL
      : rag_stdev < 0.7
        ? COLOR_GOOD
        : rag_stdev < 1.5
          ? COLOR_WARN
          : COLOR_BAD;
  // bar 는 (1 - stdev/3) 로 표현 (낮을수록 길게)
  const stdevPct =
    typeof rag_stdev === "number"
      ? Math.max(0, Math.min(100, (1 - rag_stdev / 3) * 100))
      : 0;

  // Evidence quality
  const eqStr = String(evidence_quality || "").toLowerCase();
  const eqColor =
    eqStr === "high"
      ? COLOR_GOOD
      : eqStr === "medium"
        ? COLOR_WARN
        : eqStr === "low"
          ? COLOR_BAD
          : COLOR_NEUTRAL;
  const eqEmoji =
    eqStr === "high" ? "🟢" : eqStr === "medium" ? "🟡" : eqStr === "low" ? "🔴" : "⚪";
  const eqPct = eqStr === "high" ? 100 : eqStr === "medium" ? 60 : eqStr === "low" ? 25 : 0;

  // Weighted composite 0~5
  const compPct =
    typeof weighted_composite === "number"
      ? Math.max(0, Math.min(100, (weighted_composite / 5) * 100))
      : 0;

  // Final 5 dots
  const finalDots =
    typeof finalNum === "number"
      ? Array.from({ length: 5 }, (_, i) => (i < Math.round(finalNum) ? "●" : "○")).join("")
      : "";

  const labelStyle: React.CSSProperties = {
    width: 130,
    fontSize: 10.5,
    color: "var(--ink-soft)",
    flexShrink: 0,
  };
  const barWrapStyle: React.CSSProperties = {
    flex: 1,
    height: 8,
    background: "rgba(148,163,184,0.18)",
    borderRadius: 3,
    overflow: "hidden",
    minWidth: 60,
  };
  const valueStyle: React.CSSProperties = {
    width: 90,
    fontSize: 10.5,
    fontWeight: 700,
    textAlign: "right",
    flexShrink: 0,
  };
  const rowStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 6,
    marginBottom: 4,
  };

  return (
    <div
      style={{
        width: "100%",
        marginTop: 6,
        border: "1px solid rgba(148,163,184,0.25)",
        borderRadius: 4,
        background: "rgba(248,250,252,0.5)",
        fontSize: 11,
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          width: "100%",
          textAlign: "left",
          padding: "6px 10px",
          background: "transparent",
          border: "none",
          cursor: "pointer",
          fontSize: 11,
          fontWeight: 700,
          color: "var(--ink-soft)",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span style={{ fontSize: 9 }}>{open ? "▼" : "▶"}</span>
        <span>📊 Confidence Detail</span>
        {finalNum != null && (
          <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--ink-muted)" }}>
            final {Number(finalNum).toFixed(1)}/5
          </span>
        )}
      </button>
      {open && (
        <div style={{ padding: "8px 10px 10px", borderTop: "1px solid rgba(148,163,184,0.2)" }}>
          {finalNum != null && (
            <div style={{ marginBottom: 6, fontSize: 11 }}>
              <span style={{ fontWeight: 700, color: "var(--ink-soft)" }}>
                Final Confidence:
              </span>{" "}
              <span style={{ letterSpacing: 1, color: "#0f172a" }}>{finalDots}</span>{" "}
              <span style={{ fontWeight: 700 }}>
                ({Number(finalNum).toFixed(1)}/5)
              </span>
            </div>
          )}
          {typeof weighted_composite === "number" && (
            <div style={rowStyle}>
              <span style={labelStyle}>Weighted Composite</span>
              <span style={barWrapStyle}>
                <span
                  style={{
                    display: "block",
                    height: "100%",
                    width: `${compPct}%`,
                    background: "#6366f1",
                  }}
                />
              </span>
              <span style={valueStyle}>{weighted_composite.toFixed(2)}/5</span>
            </div>
          )}

          <div
            style={{
              marginTop: 8,
              marginBottom: 4,
              fontSize: 10,
              fontWeight: 700,
              color: "var(--ink-muted)",
            }}
          >
            4개 신호 분해 (PDF §8.1)
          </div>

          <div
            style={{
              padding: "6px 8px",
              background: "rgba(255,255,255,0.6)",
              borderRadius: 3,
              border: "1px solid rgba(148,163,184,0.15)",
            }}
          >
            {/* LLM Self-Confidence */}
            <div style={rowStyle}>
              <span style={labelStyle}>LLM Self-Confidence</span>
              <span style={barWrapStyle}>
                <span
                  style={{
                    display: "block",
                    height: "100%",
                    width: `${llmSelfPct}%`,
                    background: llmSelfColor,
                  }}
                />
              </span>
              <span style={{ ...valueStyle, color: llmSelfColor }}>
                {typeof llm_self === "number" ? `${llm_self.toFixed(1)}/5` : "—"}
              </span>
            </div>

            {/* Rule vs LLM */}
            <div style={rowStyle}>
              <span style={labelStyle}>Rule vs LLM 일치</span>
              <span style={barWrapStyle}>
                <span
                  style={{
                    display: "block",
                    height: "100%",
                    width:
                      rule_llm_agreement == null
                        ? "0%"
                        : rule_llm_agreement
                          ? "100%"
                          : "20%",
                    background: ruleColor,
                  }}
                />
              </span>
              <span style={{ ...valueStyle, color: ruleColor }}>
                {rule_llm_agreement == null
                  ? "—"
                  : rule_llm_agreement
                    ? "✓ 일치"
                    : "✗ 불일치"}
              </span>
            </div>

            {/* RAG stdev */}
            <div style={rowStyle}>
              <span style={labelStyle}>RAG 점수 분산 (stdev)</span>
              <span style={barWrapStyle}>
                <span
                  style={{
                    display: "block",
                    height: "100%",
                    width: `${stdevPct}%`,
                    background: stdevColor,
                  }}
                />
              </span>
              <span style={{ ...valueStyle, color: stdevColor }}>
                {typeof rag_stdev === "number" ? rag_stdev.toFixed(2) : "—"}
              </span>
            </div>
            {typeof rag_stdev === "number" && (
              <div
                style={{
                  marginLeft: 130 + 6,
                  marginTop: -2,
                  marginBottom: 4,
                  fontSize: 9.5,
                  color: "var(--ink-muted)",
                }}
              >
                낮을수록 좋음 (페르소나 점수 일관성)
              </div>
            )}

            {/* Evidence quality */}
            <div style={rowStyle}>
              <span style={labelStyle}>Evidence 품질</span>
              <span style={barWrapStyle}>
                <span
                  style={{
                    display: "block",
                    height: "100%",
                    width: `${eqPct}%`,
                    background: eqColor,
                  }}
                />
              </span>
              <span style={{ ...valueStyle, color: eqColor }}>
                {evidence_quality ? `${eqEmoji} ${evidence_quality}` : "—"}
              </span>
            </div>
          </div>

          {(rag_sample_size != null || rag_small_sample_penalty_applied != null) && (
            <div
              style={{
                marginTop: 6,
                fontSize: 10,
                color: "var(--ink-muted)",
              }}
            >
              rag_sample_size:{" "}
              <b style={{ color: "var(--ink-soft)" }}>
                {rag_sample_size == null ? "—" : rag_sample_size}
              </b>
              {" / penalty: "}
              <b
                style={{
                  color: rag_small_sample_penalty_applied ? COLOR_BAD : COLOR_GOOD,
                }}
              >
                {rag_small_sample_penalty_applied == null
                  ? "—"
                  : rag_small_sample_penalty_applied
                    ? "적용"
                    : "미적용"}
              </b>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// RagReferencePanel — Reasoning RAG 사례 + 업무 지식 RAG (#15) 호출 로그.
// reasoning_examples / knowledge_chunks 응답 키를 fallback 으로 시도. 데이터 없으면 미렌더.
// ===========================================================================
type ReasoningExample = {
  score?: number;
  rationale?: string;
  similarity?: number;
  example_id?: string;
};

type KnowledgeChunk = {
  chunk_id?: string;
  intent?: string[] | string;
  content?: string;
  source_ref?: string;
  similarity?: number;
};

function RagReferencePanel({ evaluation }: { evaluation: CategoryItem }) {
  const [open, setOpen] = useState(false);
  const [showAllReasoning, setShowAllReasoning] = useState(false);

  const ev = evaluation as unknown as {
    reasoning_examples?: ReasoningExample[];
    confidence?: { signals?: { examples?: ReasoningExample[] } };
    knowledge_chunks?: KnowledgeChunk[];
    business_knowledge?: KnowledgeChunk[];
  };

  const reasoningExamples: ReasoningExample[] = Array.isArray(ev.reasoning_examples)
    ? ev.reasoning_examples
    : Array.isArray(ev.confidence?.signals?.examples)
      ? (ev.confidence!.signals!.examples as ReasoningExample[])
      : [];

  const knowledgeChunks: KnowledgeChunk[] = Array.isArray(ev.knowledge_chunks)
    ? ev.knowledge_chunks
    : Array.isArray(ev.business_knowledge)
      ? ev.business_knowledge
      : [];

  const hasReasoning = reasoningExamples.length > 0;
  const hasKnowledge = knowledgeChunks.length > 0;

  if (!hasReasoning && !hasKnowledge) return null;

  const visibleReasoning = showAllReasoning
    ? reasoningExamples
    : reasoningExamples.slice(0, 3);
  const reasoningRest = reasoningExamples.length - visibleReasoning.length;

  return (
    <div
      style={{
        width: "100%",
        marginTop: 6,
        border: "1px solid rgba(148,163,184,0.25)",
        borderRadius: 4,
        background: "rgba(248,250,252,0.5)",
        fontSize: 11,
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          width: "100%",
          textAlign: "left",
          padding: "6px 10px",
          background: "transparent",
          border: "none",
          cursor: "pointer",
          fontSize: 11,
          fontWeight: 700,
          color: "var(--ink-soft)",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span style={{ fontSize: 9 }}>{open ? "▼" : "▶"}</span>
        <span>🔍 RAG 참조 로그</span>
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--ink-muted)" }}>
          {hasReasoning && `reasoning ${reasoningExamples.length}건`}
          {hasReasoning && hasKnowledge && " · "}
          {hasKnowledge && `knowledge ${knowledgeChunks.length}건`}
        </span>
      </button>
      {open && (
        <div
          style={{
            padding: "8px 10px 10px",
            borderTop: "1px solid rgba(148,163,184,0.2)",
          }}
        >
          {hasReasoning && (
            <div style={{ marginBottom: hasKnowledge ? 10 : 0 }}>
              <div
                style={{
                  fontSize: 10.5,
                  fontWeight: 700,
                  color: "var(--ink-soft)",
                  marginBottom: 4,
                }}
              >
                📚 Reasoning RAG (판정 근거 인덱스 — {reasoningExamples.length}건 참조)
              </div>
              {visibleReasoning.map((ex, i) => {
                const sc = ex.score;
                const sim = typeof ex.similarity === "number" ? ex.similarity : null;
                const id = ex.example_id || "";
                const rationale = String(ex.rationale || "").trim();
                const rationaleShort =
                  rationale.length > 160
                    ? rationale.slice(0, 160) + "…"
                    : rationale;
                return (
                  <div
                    key={id || i}
                    style={{
                      marginTop: 3,
                      padding: "4px 6px",
                      background: "rgba(99,102,241,0.04)",
                      borderLeft: "2px solid #6366f1",
                      borderRadius: 3,
                    }}
                  >
                    <div
                      style={{
                        fontSize: 10.5,
                        color: "#0f172a",
                        marginBottom: 2,
                      }}
                    >
                      {sc != null && (
                        <span
                          style={{
                            fontWeight: 700,
                            background: "#e0e7ff",
                            color: "#3730a3",
                            padding: "0 5px",
                            borderRadius: 8,
                            marginRight: 4,
                          }}
                        >
                          [{sc}점]
                        </span>
                      )}
                      {rationaleShort && <span>&ldquo;{rationaleShort}&rdquo;</span>}
                    </div>
                    {(sim != null || id) && (
                      <div
                        style={{
                          fontSize: 9.5,
                          color: "var(--ink-muted)",
                          paddingLeft: 4,
                        }}
                      >
                        {sim != null && (
                          <>
                            유사도{" "}
                            <b style={{ color: "var(--ink-soft)" }}>{sim.toFixed(2)}</b>
                          </>
                        )}
                        {sim != null && id && " · "}
                        {id && <code style={{ fontSize: 9.5 }}>{id}</code>}
                      </div>
                    )}
                  </div>
                );
              })}
              {reasoningRest > 0 && (
                <button
                  type="button"
                  onClick={() => setShowAllReasoning(true)}
                  style={{
                    marginTop: 4,
                    padding: "2px 8px",
                    fontSize: 10,
                    background: "transparent",
                    border: "1px dashed rgba(148,163,184,0.4)",
                    borderRadius: 3,
                    cursor: "pointer",
                    color: "var(--ink-muted)",
                  }}
                >
                  + {reasoningRest}건 더 보기
                </button>
              )}
            </div>
          )}

          {hasKnowledge && (
            <div>
              <div
                style={{
                  fontSize: 10.5,
                  fontWeight: 700,
                  color: "var(--ink-soft)",
                  marginBottom: 4,
                }}
              >
                📖 업무 지식 RAG (#15 전용 — {knowledgeChunks.length} chunks)
              </div>
              {knowledgeChunks.map((ck, i) => {
                const intentStr = Array.isArray(ck.intent)
                  ? ck.intent.join(", ")
                  : ck.intent || "";
                const content = String(ck.content || "").trim();
                const contentShort =
                  content.length > 200 ? content.slice(0, 200) + "…" : content;
                return (
                  <div
                    key={ck.chunk_id || i}
                    style={{
                      marginTop: 3,
                      padding: "4px 6px",
                      background: "rgba(20,184,166,0.05)",
                      borderLeft: "2px solid #14b8a6",
                      borderRadius: 3,
                    }}
                  >
                    <div
                      style={{
                        fontSize: 10,
                        color: "var(--ink-muted)",
                        marginBottom: 2,
                        display: "flex",
                        flexWrap: "wrap",
                        gap: 4,
                        alignItems: "center",
                      }}
                    >
                      {ck.chunk_id && (
                        <code
                          style={{
                            fontWeight: 700,
                            background: "#ccfbf1",
                            color: "#115e59",
                            padding: "0 4px",
                            borderRadius: 2,
                          }}
                        >
                          {ck.chunk_id}
                        </code>
                      )}
                      {intentStr && (
                        <span style={{ color: "var(--ink-soft)" }}>
                          intent: <b>{intentStr}</b>
                        </span>
                      )}
                      {typeof ck.similarity === "number" && (
                        <span
                          style={{
                            background: "#f3e8ff",
                            color: "#6b21a8",
                            padding: "0 4px",
                            borderRadius: 6,
                            fontWeight: 700,
                            fontSize: 9,
                          }}
                        >
                          sim {ck.similarity.toFixed(2)}
                        </span>
                      )}
                    </div>
                    {contentShort && (
                      <div
                        style={{
                          fontSize: 10.5,
                          color: "#0f172a",
                          paddingLeft: 4,
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-word",
                        }}
                      >
                        &ldquo;{contentShort}&rdquo;
                      </div>
                    )}
                    {ck.source_ref && (
                      <div
                        style={{
                          fontSize: 9.5,
                          color: "var(--ink-muted)",
                          paddingLeft: 4,
                          marginTop: 2,
                        }}
                      >
                        source: <i>{ck.source_ref}</i>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// Layer4Section — Layer 4 노드 (confidence / tier_router / evidence_refiner /
// layer4 / gt_evidence_comparison) 클릭 시 드로어에 표시되는 dedicated 패널.
//
// 백엔드 server_v2.py `_BACKEND_TO_FRONTEND_NODES["layer4"]` 가 단일 backend
// layer4 노드를 4개 fan-out 시켜 각 시각 노드에 status/node_trace 이벤트를 보냄.
// 이 패널은 각 시각 노드별로 다음 데이터를 EvaluationResult 에서 추출해 표시:
//
//   confidence            → evaluations[].evaluation.confidence (4-신호 분해)
//   tier_router           → evaluations[].evaluation.{tier, force_t3, mandatory_human_review}
//   evidence_refiner      → evaluations[].evaluation.{evidence, judge_evidence}
//   layer4                → report.{final_score, item_scores, deductions} 요약
//   gt_evidence_comparison → result.gt_evidence_comparison.items (LLM 정성 비교)
// ===========================================================================
type Layer4SectionProps = {
  nodeId: string;
  result: EvaluationResult | null;
  allItems: CategoryItem[];
  allDeductions: Array<{
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
  liveTraces: Array<{
    id: string;
    time: string;
    node?: string;
    status?: string;
    elapsed?: number;
    label?: string;
    detail?: unknown;
  }>;
};

function Layer4Section({
  nodeId,
  result,
  allItems,
  allDeductions,
  liveTraces,
}: Layer4SectionProps) {
  // 노드별 메타 — 상단 카드 ("이 노드가 무엇을 하는지").
  const META: Record<
    string,
    { title: string; purpose: string; input: string; output: string }
  > = {
    confidence: {
      title: "Confidence 4-Signal",
      purpose:
        "각 평가 항목의 4개 신호 (LLM self · Rule-LLM 일치 · RAG stdev · Evidence 품질) 를 가중 합성해 final confidence 산출.",
      input: "evaluations[].score / rule_score / rag_examples / evidence",
      output: "evaluations[].confidence = { final, signals: {...} }",
    },
    tier_router: {
      title: "Tier Router",
      purpose:
        "신뢰도 + Policy 규칙으로 항목별 처리 등급 (T0=자동승인 / T1=리뷰 권장 / T2=필수 검수 / T3=강제 보류) 을 분류.",
      input: "evaluations[].confidence + force_t3 + mandatory_human_review",
      output: "evaluations[].tier 및 라우팅 결정",
    },
    evidence_refiner: {
      title: "Evidence Refiner",
      purpose:
        "evidence 인용을 화자별 quote 형태로 정제 + 길이 trim (200자) + 중복 제거. low-quality evidence 는 플래그.",
      input: "evaluations[].evidence (raw STT 인용 — speaker/text)",
      output: "evaluations[].evidence (refined) + judge_evidence",
    },
    layer4: {
      title: "Report Generator V2",
      purpose:
        "8개 sub-agent 결과를 QAOutputV2 스키마로 직렬화 — 카테고리별 점수 합계, 감점 목록, 등급 산정.",
      input: "evaluations[].evaluation (8 sub-agent 결과 + 토론 적용)",
      output: "report.{final_score, item_scores, deductions, evaluation.categories}",
    },
    gt_evidence_comparison: {
      title: "GT vs LLM 근거 비교",
      purpose:
        "사람 QA 비고 (GT note) ↔ AI 인용 evidence 를 LLM 으로 정성 비교 — match / partial / mismatch 판정 + 사유 작성.",
      input: "GT.note + evaluations[].evidence",
      output: "result.gt_evidence_comparison.items[]",
    },
  };
  const meta = META[nodeId];

  // ── evaluations 추출 — evaluations[i].evaluation 또는 report.item_scores 둘 다 시도 ──
  const evalsArr: Array<{ evaluation?: CategoryItem }> = (() => {
    const r = result as unknown as {
      evaluations?: Array<{ evaluation?: CategoryItem }> | unknown;
    } | null;
    const arr = r?.evaluations;
    return Array.isArray(arr) ? (arr as Array<{ evaluation?: CategoryItem }>) : [];
  })();
  const evalsCategoryItems: CategoryItem[] = evalsArr
    .map((e) => e?.evaluation)
    .filter((ev): ev is CategoryItem => !!ev && typeof ev?.item_number === "number");

  // 우선순위: evaluations[].evaluation > report.item_scores
  const itemsForLayer4 =
    evalsCategoryItems.length > 0 ? evalsCategoryItems : allItems;

  // live trace 에서도 detail.output 폴백 (평가 진행 중)
  let traceOutput: Record<string, unknown> | null = null;
  for (let i = liveTraces.length - 1; i >= 0; i--) {
    const t = liveTraces[i];
    const detail = t?.detail as { output?: Record<string, unknown> } | undefined;
    if (detail?.output && typeof detail.output === "object") {
      traceOutput = detail.output as Record<string, unknown>;
      break;
    }
  }

  return (
    <>
      {/* 노드 메타 카드 */}
      {meta && (
        <div
          className="drawer-section"
          style={{
            background: "rgba(99,102,241,0.04)",
            borderLeft: "3px solid #6366f1",
          }}
        >
          <div
            style={{
              fontSize: 11.5,
              fontWeight: 700,
              color: "#3730a3",
              marginBottom: 4,
            }}
          >
            🧩 {meta.title}
          </div>
          <div
            style={{
              fontSize: 11,
              color: "var(--ink-soft)",
              lineHeight: 1.55,
              marginBottom: 6,
            }}
          >
            {meta.purpose}
          </div>
          <div style={{ fontSize: 10.5, color: "var(--ink-muted)", lineHeight: 1.5 }}>
            <div>
              <b style={{ color: "var(--ink-soft)" }}>입력</b>: {meta.input}
            </div>
            <div>
              <b style={{ color: "var(--ink-soft)" }}>출력</b>: {meta.output}
            </div>
          </div>
        </div>
      )}

      {/* 노드별 데이터 패널 */}
      {nodeId === "confidence" && (
        <ConfidencePanel items={itemsForLayer4} />
      )}
      {nodeId === "tier_router" && (
        <TierRouterPanel items={itemsForLayer4} />
      )}
      {nodeId === "evidence_refiner" && (
        <EvidenceRefinerPanel items={itemsForLayer4} />
      )}
      {nodeId === "layer4" && (
        <ReportGeneratorPanel
          result={result}
          items={itemsForLayer4}
          deductions={allDeductions}
          traceOutput={traceOutput}
        />
      )}
      {nodeId === "gt_evidence_comparison" && (
        <GtEvidencePanel result={result} traceOutput={traceOutput} />
      )}
    </>
  );
}

// ── Layer4 sub-panels ──

function ConfidencePanel({ items }: { items: CategoryItem[] }) {
  const rows = items
    .filter((it) => typeof it.item_number === "number")
    .sort((a, b) => a.item_number - b.item_number);
  if (rows.length === 0) {
    return <Layer4EmptyHint label="confidence 데이터가 아직 도착하지 않았습니다" />;
  }
  return (
    <div className="drawer-section">
      <div className="drawer-section-title">
        Confidence — 항목별 4-신호 분해 ({rows.length})
      </div>
      <div style={{ fontSize: 10.5, color: "var(--ink-muted)", marginBottom: 8 }}>
        final = LLM self · Rule-LLM 일치 · RAG stdev · Evidence 품질 가중합 (0~5)
      </div>
      <table style={{ width: "100%", fontSize: 10.5, borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ background: "var(--surface-muted)", color: "var(--ink-soft)" }}>
            <th style={{ textAlign: "left", padding: "4px 6px" }}>#</th>
            <th style={{ textAlign: "left", padding: "4px 6px" }}>항목</th>
            <th style={{ textAlign: "right", padding: "4px 6px" }}>Final</th>
            <th style={{ textAlign: "right", padding: "4px 6px" }}>LLM self</th>
            <th style={{ textAlign: "center", padding: "4px 6px" }}>Rule=LLM</th>
            <th style={{ textAlign: "right", padding: "4px 6px" }}>stdev</th>
            <th style={{ textAlign: "center", padding: "4px 6px" }}>Ev 품질</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((it) => {
            const conf = (it.confidence || {}) as {
              final?: number;
              signals?: {
                llm_self?: number;
                rule_llm_agreement?: boolean;
                rag_stdev?: number | null;
                evidence_quality?: string;
              };
            };
            const sig = conf.signals || {};
            const final = typeof conf.final === "number" ? conf.final : null;
            const finalColor =
              final == null
                ? "#94a3b8"
                : final >= 4
                  ? "#16a34a"
                  : final >= 2.5
                    ? "#f59e0b"
                    : "#ef4444";
            const eqStr = String(sig.evidence_quality || "").toLowerCase();
            const eqEmoji =
              eqStr === "high"
                ? "🟢"
                : eqStr === "medium"
                  ? "🟡"
                  : eqStr === "low"
                    ? "🔴"
                    : "⚪";
            return (
              <tr
                key={it.item_number}
                style={{ borderBottom: "1px solid var(--border)" }}
              >
                <td style={{ padding: "3px 6px", fontWeight: 700 }}>
                  #{it.item_number}
                </td>
                <td
                  style={{
                    padding: "3px 6px",
                    color: "var(--ink-soft)",
                    maxWidth: 110,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {ITEM_NAMES[it.item_number] || it.item || ""}
                </td>
                <td
                  style={{
                    padding: "3px 6px",
                    textAlign: "right",
                    fontWeight: 800,
                    color: finalColor,
                  }}
                >
                  {final == null ? "—" : final.toFixed(1)}
                </td>
                <td style={{ padding: "3px 6px", textAlign: "right" }}>
                  {typeof sig.llm_self === "number"
                    ? sig.llm_self.toFixed(1)
                    : "—"}
                </td>
                <td style={{ padding: "3px 6px", textAlign: "center" }}>
                  {sig.rule_llm_agreement == null
                    ? "—"
                    : sig.rule_llm_agreement
                      ? "✓"
                      : "✗"}
                </td>
                <td style={{ padding: "3px 6px", textAlign: "right" }}>
                  {typeof sig.rag_stdev === "number"
                    ? sig.rag_stdev.toFixed(2)
                    : "—"}
                </td>
                <td style={{ padding: "3px 6px", textAlign: "center" }}>
                  {eqEmoji}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TierRouterPanel({ items }: { items: CategoryItem[] }) {
  type WithTier = CategoryItem & {
    tier?: string;
    routing?: string;
    routing_decision?: string;
  };
  const rows = items
    .filter((it) => typeof it.item_number === "number")
    .map((it) => it as WithTier)
    .sort((a, b) => a.item_number - b.item_number);
  if (rows.length === 0) {
    return <Layer4EmptyHint label="tier 데이터가 아직 도착하지 않았습니다" />;
  }
  // Tier 분포 카운트
  const tierCount: Record<string, number> = { T0: 0, T1: 0, T2: 0, T3: 0, "—": 0 };
  rows.forEach((it) => {
    const t = String(it.tier || "").toUpperCase();
    if (t in tierCount) tierCount[t] += 1;
    else tierCount["—"] += 1;
  });
  const tierColor: Record<string, string> = {
    T0: "#16a34a",
    T1: "#facc15",
    T2: "#f97316",
    T3: "#ef4444",
    "—": "#94a3b8",
  };
  return (
    <div className="drawer-section">
      <div className="drawer-section-title">
        Tier Router — 항목별 등급 분류 ({rows.length})
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
        {(["T0", "T1", "T2", "T3"] as const).map((t) => (
          <span
            key={t}
            style={{
              padding: "2px 8px",
              fontSize: 10.5,
              fontWeight: 700,
              borderRadius: 12,
              background: tierColor[t] + "20",
              color: tierColor[t],
              border: `1px solid ${tierColor[t]}55`,
            }}
          >
            {t}: {tierCount[t]}
          </span>
        ))}
      </div>
      <div style={{ fontSize: 10, color: "var(--ink-muted)", marginBottom: 6 }}>
        T0=자동승인 · T1=리뷰 권장 · T2=필수 검수 · T3=강제 보류
      </div>
      <table style={{ width: "100%", fontSize: 10.5, borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ background: "var(--surface-muted)", color: "var(--ink-soft)" }}>
            <th style={{ textAlign: "left", padding: "4px 6px" }}>#</th>
            <th style={{ textAlign: "left", padding: "4px 6px" }}>항목</th>
            <th style={{ textAlign: "center", padding: "4px 6px" }}>Tier</th>
            <th style={{ textAlign: "right", padding: "4px 6px" }}>conf</th>
            <th style={{ textAlign: "center", padding: "4px 6px" }}>force_t3</th>
            <th style={{ textAlign: "center", padding: "4px 6px" }}>검수</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((it) => {
            const tier = String(it.tier || "—").toUpperCase();
            const tcol = tierColor[tier] ?? tierColor["—"];
            const conf =
              (it.confidence as { final?: number } | undefined)?.final ?? null;
            return (
              <tr
                key={it.item_number}
                style={{ borderBottom: "1px solid var(--border)" }}
              >
                <td style={{ padding: "3px 6px", fontWeight: 700 }}>
                  #{it.item_number}
                </td>
                <td
                  style={{
                    padding: "3px 6px",
                    color: "var(--ink-soft)",
                    maxWidth: 130,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {ITEM_NAMES[it.item_number] || it.item || ""}
                </td>
                <td style={{ padding: "3px 6px", textAlign: "center" }}>
                  <span
                    style={{
                      padding: "1px 8px",
                      fontSize: 10,
                      fontWeight: 800,
                      borderRadius: 10,
                      background: tcol + "20",
                      color: tcol,
                      border: `1px solid ${tcol}55`,
                    }}
                  >
                    {tier}
                  </span>
                </td>
                <td
                  style={{
                    padding: "3px 6px",
                    textAlign: "right",
                    color: "var(--ink-soft)",
                  }}
                >
                  {conf == null ? "—" : conf.toFixed(1)}
                </td>
                <td style={{ padding: "3px 6px", textAlign: "center" }}>
                  {it.force_t3 ? "✓" : "—"}
                </td>
                <td style={{ padding: "3px 6px", textAlign: "center" }}>
                  {it.mandatory_human_review ? "🔍" : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function EvidenceRefinerPanel({ items }: { items: CategoryItem[] }) {
  const rows = items
    .filter((it) => typeof it.item_number === "number")
    .sort((a, b) => a.item_number - b.item_number);
  if (rows.length === 0) {
    return <Layer4EmptyHint label="evidence 데이터가 아직 도착하지 않았습니다" />;
  }
  return (
    <div className="drawer-section">
      <div className="drawer-section-title">
        Refined Evidence — 항목별 정제 인용 ({rows.length})
      </div>
      <div style={{ fontSize: 10.5, color: "var(--ink-muted)", marginBottom: 8 }}>
        화자/quote 단위로 정제 + 200자 trim + 중복 제거 후의 evidence
      </div>
      {rows.map((it) => {
        const ev = Array.isArray(it.evidence) ? it.evidence : [];
        const judgeEv = Array.isArray(it.judge_evidence) ? it.judge_evidence : [];
        const allEv = ev.length > 0 ? ev : judgeEv;
        return (
          <div
            key={it.item_number}
            style={{
              marginBottom: 10,
              padding: "8px 10px",
              background: "var(--surface-muted)",
              borderRadius: 6,
              border: "1px solid var(--border)",
            }}
          >
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: "var(--ink)",
                marginBottom: 4,
              }}
            >
              #{it.item_number} {ITEM_NAMES[it.item_number] || ""}{" "}
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 500,
                  color: "var(--ink-muted)",
                  marginLeft: 4,
                }}
              >
                · {allEv.length} 개 evidence
              </span>
            </div>
            {allEv.length === 0 ? (
              <div
                style={{
                  fontSize: 10.5,
                  color: "var(--ink-muted)",
                  fontStyle: "italic",
                }}
              >
                인용 없음 (refiner skip 또는 LLM 미제공)
              </div>
            ) : (
              <ul
                style={{
                  margin: 0,
                  paddingLeft: 16,
                  fontSize: 10.5,
                  color: "var(--ink-soft)",
                  lineHeight: 1.55,
                }}
              >
                {allEv.slice(0, 5).map((e, i) => {
                  const speaker =
                    typeof e === "object" && e
                      ? (e as { speaker?: string; role?: string }).speaker ||
                        (e as { role?: string }).role
                      : "";
                  const quote =
                    typeof e === "string"
                      ? e
                      : (e as { quote?: string; text?: string }).quote ||
                        (e as { text?: string }).text ||
                        "";
                  return (
                    <li key={i} style={{ marginBottom: 3 }}>
                      {speaker && (
                        <b style={{ color: "var(--accent-strong)" }}>
                          [{speaker}]{" "}
                        </b>
                      )}
                      <span>“{String(quote).slice(0, 200)}”</span>
                    </li>
                  );
                })}
                {allEv.length > 5 && (
                  <li
                    style={{
                      listStyle: "none",
                      fontSize: 9.5,
                      color: "var(--ink-muted)",
                      fontStyle: "italic",
                    }}
                  >
                    + {allEv.length - 5} 개 더
                  </li>
                )}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ReportGeneratorPanel({
  result,
  items,
  deductions,
  traceOutput,
}: {
  result: EvaluationResult | null;
  items: CategoryItem[];
  deductions: Array<{
    item_number?: number;
    item?: number;
    reason?: string;
    description?: string;
    deduction?: number;
    points?: number;
    points_lost?: number;
  }>;
  traceOutput: Record<string, unknown> | null;
}) {
  // report.{final_score, evaluation.categories, item_scores, deductions} 추출.
  const report = (result?.report || {}) as {
    final_score?: { grade?: string; raw_total?: number; after_overrides?: number };
    evaluated_at?: string;
    tenant?: string;
    evaluation?: { categories?: Array<{ category: string; items: CategoryItem[] }> };
  } | null;
  const fs = report?.final_score || {};
  const categories = report?.evaluation?.categories || [];
  const totalDeductionPts = deductions.reduce(
    (s, d) => s + (d.deduction ?? d.points ?? d.points_lost ?? 0),
    0,
  );

  // 데이터 부재 시 traceOutput 폴백 안내
  const hasReportData =
    !!fs.raw_total ||
    !!fs.after_overrides ||
    categories.length > 0 ||
    items.length > 0;

  if (!hasReportData) {
    return (
      <Layer4EmptyHint
        label={
          traceOutput
            ? "보고서 직렬화 진행 중 (trace.detail 에 partial output 도달)"
            : "report_generator 결과가 아직 도착하지 않았습니다"
        }
      />
    );
  }

  return (
    <div className="drawer-section">
      <div className="drawer-section-title">최종 보고서 요약</div>
      {/* 4-cell 요약 카드 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 6,
          marginBottom: 10,
        }}
      >
        {[
          ["Grade", fs.grade ?? "—"] as const,
          ["raw 총점", fs.raw_total ?? "—"] as const,
          ["override 총점", fs.after_overrides ?? "—"] as const,
          ["감점 합계", `−${totalDeductionPts}`] as const,
        ].map(([lab, val], i) => (
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
                fontSize: 9.5,
                color: "var(--ink-muted)",
                fontWeight: 700,
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
                color: "var(--ink)",
                marginTop: 2,
              }}
            >
              {val}
            </div>
          </div>
        ))}
      </div>

      {/* 카테고리별 점수 */}
      {categories.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: "var(--ink-soft)",
              marginBottom: 4,
            }}
          >
            카테고리별 점수
          </div>
          <table
            style={{ width: "100%", fontSize: 10.5, borderCollapse: "collapse" }}
          >
            <thead>
              <tr
                style={{
                  background: "var(--surface-muted)",
                  color: "var(--ink-soft)",
                }}
              >
                <th style={{ textAlign: "left", padding: "4px 6px" }}>카테고리</th>
                <th style={{ textAlign: "right", padding: "4px 6px" }}>점수</th>
                <th style={{ textAlign: "right", padding: "4px 6px" }}>최대</th>
                <th style={{ textAlign: "right", padding: "4px 6px" }}>항목수</th>
              </tr>
            </thead>
            <tbody>
              {categories.map((c, i) => {
                const total = c.items.reduce(
                  (s, it) => s + (Number(it.score) || 0),
                  0,
                );
                const maxTotal = c.items.reduce(
                  (s, it) => s + (Number(it.max_score) || 0),
                  0,
                );
                return (
                  <tr
                    key={i}
                    style={{ borderBottom: "1px solid var(--border)" }}
                  >
                    <td style={{ padding: "3px 6px", color: "var(--ink-soft)" }}>
                      {c.category}
                    </td>
                    <td
                      style={{
                        padding: "3px 6px",
                        textAlign: "right",
                        fontWeight: 700,
                      }}
                    >
                      {total}
                    </td>
                    <td
                      style={{
                        padding: "3px 6px",
                        textAlign: "right",
                        color: "var(--ink-muted)",
                      }}
                    >
                      {maxTotal}
                    </td>
                    <td
                      style={{
                        padding: "3px 6px",
                        textAlign: "right",
                        color: "var(--ink-muted)",
                      }}
                    >
                      {c.items.length}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 감점 요약 (top 5) */}
      {deductions.length > 0 && (
        <div>
          <div
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: "var(--ink-soft)",
              marginBottom: 4,
            }}
          >
            감점 목록 ({deductions.length}건, 상위 5)
          </div>
          {deductions.slice(0, 5).map((d, i) => (
            <div
              key={i}
              style={{
                marginBottom: 4,
                padding: "4px 8px",
                background: "rgba(239,68,68,0.05)",
                borderLeft: "2px solid #ef4444",
                borderRadius: 3,
                fontSize: 10.5,
              }}
            >
              <b style={{ color: "#b91c1c" }}>
                #{d.item_number ?? d.item}: −
                {d.deduction ?? d.points ?? d.points_lost ?? 0}점
              </b>
              <span style={{ color: "var(--ink-muted)", marginLeft: 6 }}>
                {d.reason || d.description || ""}
              </span>
            </div>
          ))}
          {deductions.length > 5 && (
            <div
              style={{
                fontSize: 10,
                color: "var(--ink-muted)",
                fontStyle: "italic",
              }}
            >
              + {deductions.length - 5} 건 더 — 아래 Deductions 섹션 참조
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function GtEvidencePanel({
  result,
  traceOutput,
}: {
  result: EvaluationResult | null;
  traceOutput: Record<string, unknown> | null;
}) {
  type GtEvidenceItemFull = {
    item_number: number;
    item_name?: string;
    ai_score?: number | null;
    gt_score?: number | null;
    verdict?: string;
    verdict_label?: string;
    reasoning?: string;
    gt_note?: string | null;
    gt_evidence_excerpt?: string | null;
    ai_evidence?: string[] | null;
    ai_judgment?: string | null;
  };
  type GeShape = {
    summary?: {
      total?: number;
      match?: number;
      partial?: number;
      mismatch?: number;
      match_rate?: number;
    };
    items?: GtEvidenceItemFull[];
  };
  const ge: GeShape | null =
    (result?.gt_evidence_comparison as GeShape | null | undefined) ??
    (traceOutput?.gt_evidence_comparison as GeShape | undefined) ??
    null;

  const items = (ge?.items || []) as GtEvidenceItemFull[];
  const summary = ge?.summary || {};

  if (items.length === 0) {
    return (
      <Layer4EmptyHint
        label={
          traceOutput
            ? "GT 비교 LLM 호출 진행 중 (trace 도달, items 미적재)"
            : "GT 데이터 없음 — 사람 QA 평가표 (xlsx) 미연동 또는 비활성"
        }
      />
    );
  }

  const verdictColor = (v?: string) =>
    v === "match"
      ? "#15803d"
      : v === "partial"
        ? "#a16207"
        : v === "mismatch"
          ? "#b91c1c"
          : "#6b7280";
  const verdictBg = (v?: string) =>
    v === "match"
      ? "#dcfce7"
      : v === "partial"
        ? "#fef3c7"
        : v === "mismatch"
          ? "#fee2e2"
          : "#f3f4f6";

  return (
    <div className="drawer-section">
      <div className="drawer-section-title">
        GT vs LLM 근거 비교 ({items.length})
      </div>
      {/* summary */}
      {(summary.total != null || summary.match_rate != null) && (
        <div
          style={{
            display: "flex",
            gap: 6,
            flexWrap: "wrap",
            marginBottom: 10,
          }}
        >
          <span className="badge badge-neutral">총 {summary.total ?? items.length}</span>
          <span className="badge" style={{ background: "#dcfce7", color: "#15803d" }}>
            match {summary.match ?? 0}
          </span>
          <span className="badge" style={{ background: "#fef3c7", color: "#a16207" }}>
            partial {summary.partial ?? 0}
          </span>
          <span className="badge" style={{ background: "#fee2e2", color: "#b91c1c" }}>
            mismatch {summary.mismatch ?? 0}
          </span>
          {typeof summary.match_rate === "number" && (
            <span className="badge badge-accent">
              일치율 {(summary.match_rate * 100).toFixed(1)}%
            </span>
          )}
        </div>
      )}
      {/* 항목별 카드 */}
      {items.map((it) => {
        const v = String(it.verdict || "").toLowerCase();
        const aiEv = Array.isArray(it.ai_evidence) ? it.ai_evidence : [];
        return (
          <div
            key={it.item_number}
            style={{
              marginBottom: 8,
              padding: "8px 10px",
              borderLeft: `3px solid ${verdictColor(v)}`,
              background: verdictBg(v),
              borderRadius: 4,
              fontSize: 11,
            }}
          >
            <div
              style={{
                display: "flex",
                gap: 8,
                alignItems: "baseline",
                marginBottom: 4,
                flexWrap: "wrap",
              }}
            >
              <b style={{ color: "var(--ink)" }}>
                #{it.item_number} {it.item_name || ITEM_NAMES[it.item_number] || ""}
              </b>
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  padding: "1px 6px",
                  background: "white",
                  color: verdictColor(v),
                  borderRadius: 8,
                  border: `1px solid ${verdictColor(v)}55`,
                }}
              >
                {it.verdict_label || it.verdict || "—"}
              </span>
              <span style={{ fontSize: 10, color: "var(--ink-muted)" }}>
                AI {it.ai_score ?? "—"} / GT {it.gt_score ?? "—"}
              </span>
            </div>
            {it.gt_note && (
              <div style={{ fontSize: 10.5, marginBottom: 3 }}>
                <b style={{ color: "var(--ink-soft)" }}>GT 비고:</b>{" "}
                <span style={{ color: "var(--ink)" }}>“{it.gt_note}”</span>
              </div>
            )}
            {aiEv.length > 0 && (
              <div style={{ fontSize: 10.5, marginBottom: 3 }}>
                <b style={{ color: "var(--ink-soft)" }}>AI 근거:</b>{" "}
                <span style={{ color: "var(--ink)" }}>
                  “{String(aiEv[0]).slice(0, 200)}”
                  {aiEv.length > 1 && (
                    <i style={{ color: "var(--ink-muted)" }}>
                      {" "}
                      (+{aiEv.length - 1})
                    </i>
                  )}
                </span>
              </div>
            )}
            {it.reasoning && (
              <div
                style={{
                  fontSize: 10.5,
                  color: "var(--ink-muted)",
                  marginTop: 4,
                  paddingTop: 4,
                  borderTop: "1px dashed rgba(0,0,0,0.08)",
                  lineHeight: 1.5,
                }}
              >
                <b style={{ color: "var(--ink-soft)" }}>LLM 판정 사유:</b>{" "}
                {it.reasoning}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Layer4EmptyHint({ label }: { label: string }) {
  return (
    <div className="drawer-section">
      <div
        className="empty-state"
        style={{
          padding: "12px 10px",
          fontSize: 11,
          color: "var(--ink-muted)",
          background: "var(--surface-muted)",
          border: "1px dashed var(--border)",
          borderRadius: 6,
          textAlign: "center",
        }}
      >
        {label}
      </div>
    </div>
  );
}

// ===========================================================================
// SystemNodeSection — System / aggregator 노드 (layer1 / layer2_barrier /
// layer3 / combined_report / report_narrator) 클릭 시 드로어에 dedicated 패널.
// 평가 sub-agent 가 아니라 파이프라인 메타 단계이므로 persona / golden RAG /
// item-level 점수 탭 대신 단계별 의미를 가진 데이터 표시.
// ===========================================================================
type SystemNodeSectionProps = {
  nodeId: string;
  result: EvaluationResult | null;
  allItems: CategoryItem[];
  allDeductions: Array<{
    item_number?: number;
    item?: number;
    reason?: string;
    description?: string;
    deduction?: number;
    points?: number;
    points_lost?: number;
  }>;
  liveTraces: Array<{
    id: string;
    time: string;
    node?: string;
    status?: string;
    elapsed?: number;
    label?: string;
    detail?: unknown;
  }>;
  nodeStates: Record<string, string>;
  state: string;
};

function SystemNodeSection({
  nodeId,
  result,
  allItems,
  allDeductions,
  liveTraces,
  nodeStates,
  state,
}: SystemNodeSectionProps) {
  const META: Record<
    string,
    { title: string; purpose: string; input: string; output: string }
  > = {
    layer1: {
      title: "Layer 1 · Preprocessing",
      purpose:
        "STT 원문을 화자 정규화 · 세그먼트 분류 · PII 마스킹 · rule pre-verdict 산출까지 진행. 이후 모든 sub-agent 의 입력 (canonical_transcript) 을 생성.",
      input: "원본 STT + tenant rubric / PII 정책",
      output: "preprocessing.{quality, segments, pii_redacted, rule_pre_verdicts, canonical_transcript}",
    },
    layer2_barrier: {
      title: "Layer 2 Barrier",
      purpose:
        "8개 sub-agent (인사·경청·언어·니즈·설명·적극·정확·개인정보) 의 fan-out 결과를 모두 모은 뒤 다음 단계로 단일 진입시키는 동기화 지점. 데이터 변환 없음 — no-op.",
      input: "8 sub-agent 의 evaluation 결과",
      output: "동일 데이터 (단순 동기화)",
    },
    layer3: {
      title: "Layer 3 · Orchestrator V2",
      purpose:
        "8 sub-agent 결과를 카테고리별로 집계 · LLM-vs-Rule override 적용 · consistency 검증 · 등급 산정. 후속 Layer 4 (confidence/tier/evidence) 에 표준화된 report 객체를 전달.",
      input: "evaluations[] (8 sub-agent 결과)",
      output: "report.{evaluation.categories, override 적용, consistency 검사 결과}",
    },
    combined_report: {
      title: "통합 보고서",
      purpose:
        "Layer 4 chain (기존 18 항목 평가) + KSQI chain (만족도 모델) 결과를 합류시켜 사용자에게 노출되는 최종 보고서를 직렬화.",
      input: "Layer 4 report + KSQI report",
      output: "통합 final_score · 카테고리 합계 · 감점 목록",
    },
    report_narrator: {
      title: "AI 마무리 총평",
      purpose:
        "전체 결과 (평가 18항목 + KMS + KSQI + GT 비교 + HITL 라우팅) 를 LLM 으로 종합해 자연어 결론 / 강점·개선점 / 코칭 포인트 작성.",
      input: "통합 보고서 + 토론 + GT/KMS/KSQI 결과",
      output: "report_llm_summary.{narrative, strengths, improvements, coaching_points}",
    },
  };
  const meta = META[nodeId];

  // 최신 trace.detail.output 추출 — 평가 진행 중에도 partial 노출.
  let traceOutput: Record<string, unknown> | null = null;
  for (let i = liveTraces.length - 1; i >= 0; i--) {
    const t = liveTraces[i];
    const detail = t?.detail as { output?: Record<string, unknown> } | undefined;
    if (detail?.output && typeof detail.output === "object") {
      traceOutput = detail.output as Record<string, unknown>;
      break;
    }
  }

  return (
    <>
      {meta && (
        <div
          className="drawer-section"
          style={{
            background: "rgba(99,102,241,0.04)",
            borderLeft: "3px solid #6366f1",
          }}
        >
          <div
            style={{
              fontSize: 11.5,
              fontWeight: 700,
              color: "#3730a3",
              marginBottom: 4,
            }}
          >
            🧩 {meta.title}
          </div>
          <div
            style={{
              fontSize: 11,
              color: "var(--ink-soft)",
              lineHeight: 1.55,
              marginBottom: 6,
            }}
          >
            {meta.purpose}
          </div>
          <div style={{ fontSize: 10.5, color: "var(--ink-muted)", lineHeight: 1.5 }}>
            <div>
              <b style={{ color: "var(--ink-soft)" }}>입력</b>: {meta.input}
            </div>
            <div>
              <b style={{ color: "var(--ink-soft)" }}>출력</b>: {meta.output}
            </div>
          </div>
        </div>
      )}

      {nodeId === "layer1" && (
        <Layer1Panel result={result} traceOutput={traceOutput} state={state} />
      )}
      {nodeId === "layer2_barrier" && (
        <Layer2BarrierPanel nodeStates={nodeStates} />
      )}
      {nodeId === "layer3" && (
        <Layer3Panel
          result={result}
          allItems={allItems}
          allDeductions={allDeductions}
          traceOutput={traceOutput}
        />
      )}
      {nodeId === "combined_report" && (
        <CombinedReportPanel
          result={result}
          allItems={allItems}
          allDeductions={allDeductions}
        />
      )}
      {nodeId === "report_narrator" && (
        <ReportNarratorPanel result={result} traceOutput={traceOutput} state={state} />
      )}
    </>
  );
}

// ── System / aggregator sub-panels ──

function Layer1Panel({
  result,
  traceOutput,
  state,
}: {
  result: EvaluationResult | null;
  traceOutput: Record<string, unknown> | null;
  state: string;
}) {
  // preprocessing 추출 — result.preprocessing 우선, 없으면 trace output 폴백.
  const pre =
    ((result as unknown as { preprocessing?: Record<string, unknown> } | null)
      ?.preprocessing as Record<string, unknown> | undefined) ||
    (traceOutput?.preprocessing as Record<string, unknown> | undefined) ||
    null;

  if (!pre) {
    return (
      <Layer4EmptyHint
        label={
          state === "active"
            ? "Layer 1 전처리 진행 중 — 결과 도착 즉시 표시됩니다."
            : state === "done"
              ? "preprocessing 데이터 미도착 (state.preprocessing 없음)."
              : "아직 실행되지 않았습니다."
        }
      />
    );
  }

  const quality = (pre.quality as { unevaluable?: boolean; reason?: string } | undefined) || {};
  const segments =
    (pre.segments as Record<string, unknown[]> | undefined) ||
    (pre.segment_counts as Record<string, number> | undefined) ||
    null;
  const piiRaw =
    pre.pii_redacted ?? pre.pii_redactions ?? pre.pii ?? pre.pii_count ?? null;
  const piiCount = Array.isArray(piiRaw)
    ? piiRaw.length
    : typeof piiRaw === "number"
      ? piiRaw
      : null;
  const ruleVerdicts =
    (pre.rule_pre_verdicts as Record<string, unknown> | undefined) || null;
  const canonical =
    (pre.canonical_transcript as
      | Array<unknown>
      | { turns?: unknown[]; text?: string }
      | undefined) || null;
  const turnCount = Array.isArray(canonical)
    ? canonical.length
    : (canonical as { turns?: unknown[] } | null)?.turns?.length ?? null;

  return (
    <div className="drawer-section">
      <div className="drawer-section-title">Layer 1 — 전처리 결과</div>

      {/* 품질 카드 */}
      <div
        style={{
          marginBottom: 10,
          padding: "8px 10px",
          background: quality.unevaluable
            ? "rgba(239,68,68,0.06)"
            : "rgba(34,197,94,0.06)",
          border: `1px solid ${
            quality.unevaluable ? "rgba(239,68,68,0.25)" : "rgba(34,197,94,0.25)"
          }`,
          borderRadius: 4,
          fontSize: 11,
        }}
      >
        <div style={{ fontWeight: 700, color: quality.unevaluable ? "#991b1b" : "#166534" }}>
          품질: {quality.unevaluable ? "평가 불가 (unevaluable)" : "평가 가능"}
        </div>
        {quality.reason && (
          <div style={{ marginTop: 2, color: "var(--ink-muted)", fontSize: 10.5 }}>
            사유: {quality.reason}
          </div>
        )}
      </div>

      {/* 4-cell 요약 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(2, 1fr)",
          gap: 6,
          marginBottom: 10,
        }}
      >
        {[
          ["Canonical Turns", turnCount ?? "—"] as const,
          ["PII Redacted", piiCount ?? "—"] as const,
          [
            "Segment Categories",
            segments ? Object.keys(segments).length : "—",
          ] as const,
          [
            "Rule Pre-Verdicts",
            ruleVerdicts ? Object.keys(ruleVerdicts).length : "—",
          ] as const,
        ].map(([lab, val], i) => (
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
                fontSize: 9.5,
                color: "var(--ink-muted)",
                fontWeight: 700,
                letterSpacing: "0.04em",
              }}
            >
              {lab}
            </div>
            <div
              className="tabular-nums"
              style={{ fontSize: 14, fontWeight: 800, color: "var(--ink)", marginTop: 2 }}
            >
              {val}
            </div>
          </div>
        ))}
      </div>

      {/* 세그먼트 카테고리별 카운트 */}
      {segments && Object.keys(segments).length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--ink-soft)", marginBottom: 4 }}>
            세그먼트 분류 ({Object.keys(segments).length})
          </div>
          <table style={{ width: "100%", fontSize: 10.5, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--surface-muted)", color: "var(--ink-soft)" }}>
                <th style={{ textAlign: "left", padding: "4px 6px" }}>카테고리</th>
                <th style={{ textAlign: "right", padding: "4px 6px" }}>개수</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(segments).map(([cat, val]) => {
                const count = Array.isArray(val) ? val.length : Number(val) || 0;
                return (
                  <tr key={cat} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "3px 6px", color: "var(--ink-soft)" }}>{cat}</td>
                    <td
                      style={{ padding: "3px 6px", textAlign: "right", fontWeight: 700 }}
                      className="tabular-nums"
                    >
                      {count}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* rule_pre_verdicts 요약 */}
      {ruleVerdicts && Object.keys(ruleVerdicts).length > 0 && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--ink-soft)", marginBottom: 4 }}>
            Rule Pre-Verdicts ({Object.keys(ruleVerdicts).length})
          </div>
          <table style={{ width: "100%", fontSize: 10.5, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--surface-muted)", color: "var(--ink-soft)" }}>
                <th style={{ textAlign: "left", padding: "4px 6px" }}>항목</th>
                <th style={{ textAlign: "left", padding: "4px 6px" }}>판정</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(ruleVerdicts)
                .sort(([a], [b]) => Number(a) - Number(b))
                .slice(0, 18)
                .map(([item, v]) => {
                  const verdict =
                    typeof v === "string"
                      ? v
                      : (v as { verdict?: string; type?: string } | null)?.verdict ||
                        (v as { verdict?: string; type?: string } | null)?.type ||
                        "unknown";
                  const cls =
                    verdict === "hard"
                      ? { bg: "#fee2e2", color: "#991b1b" }
                      : verdict === "soft"
                        ? { bg: "#fef3c7", color: "#92400e" }
                        : { bg: "#e5e7eb", color: "#374151" };
                  return (
                    <tr key={item} style={{ borderBottom: "1px solid var(--border)" }}>
                      <td style={{ padding: "3px 6px", color: "var(--ink-soft)" }}>#{item}</td>
                      <td style={{ padding: "3px 6px" }}>
                        <span
                          style={{
                            background: cls.bg,
                            color: cls.color,
                            fontSize: 10,
                            fontWeight: 700,
                            padding: "1px 6px",
                            borderRadius: 8,
                          }}
                        >
                          {verdict}
                        </span>
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Layer2BarrierPanel({ nodeStates }: { nodeStates: Record<string, string> }) {
  // 8 sub-agent 완료 체크리스트.
  const SUB_AGENTS = [
    { id: "greeting", label: "인사 예절" },
    { id: "listening_comm", label: "경청 및 소통" },
    { id: "language", label: "언어 표현" },
    { id: "needs", label: "니즈 파악" },
    { id: "explanation", label: "설명력 및 전달력" },
    { id: "proactiveness", label: "적극성" },
    { id: "work_accuracy", label: "업무 정확도" },
    { id: "privacy", label: "개인정보 보호" },
  ];
  const doneCount = SUB_AGENTS.filter((a) => nodeStates[a.id] === "done").length;

  return (
    <div className="drawer-section">
      <div className="drawer-section-title">
        Layer 2 Barrier — Sub-Agent 동기화 ({doneCount}/{SUB_AGENTS.length})
      </div>
      <div style={{ fontSize: 10.5, color: "var(--ink-muted)", marginBottom: 8 }}>
        fan-out 동기화 지점 (no-op). 8개 sub-agent 가 모두 완료되어야 다음 단계 진입.
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {SUB_AGENTS.map((a) => {
          const st = nodeStates[a.id] || "pending";
          const icon =
            st === "done"
              ? "✓"
              : st === "active"
                ? "▶"
                : st === "error" || st === "gate-failed"
                  ? "✗"
                  : st === "skipped"
                    ? "—"
                    : "○";
          const color =
            st === "done"
              ? "#16a34a"
              : st === "active"
                ? "#3b82f6"
                : st === "error" || st === "gate-failed"
                  ? "#ef4444"
                  : "#94a3b8";
          return (
            <div
              key={a.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "5px 8px",
                background: "var(--surface-muted)",
                border: "1px solid var(--border)",
                borderRadius: 4,
                fontSize: 11,
              }}
            >
              <span style={{ color, fontWeight: 700, minWidth: 14 }}>{icon}</span>
              <span style={{ flex: 1, color: "var(--ink-soft)" }}>{a.label}</span>
              <span style={{ fontSize: 10, color: "var(--ink-muted)" }}>{a.id}</span>
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  color,
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                }}
              >
                {st}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Layer3Panel({
  result,
  allItems,
  allDeductions,
  traceOutput,
}: {
  result: EvaluationResult | null;
  allItems: CategoryItem[];
  allDeductions: Array<{
    item_number?: number;
    item?: number;
    reason?: string;
    description?: string;
    deduction?: number;
    points?: number;
    points_lost?: number;
  }>;
  traceOutput: Record<string, unknown> | null;
}) {
  const report = (result?.report || {}) as {
    final_score?: { grade?: string; raw_total?: number; after_overrides?: number };
    evaluation?: { categories?: Array<{ category: string; items: CategoryItem[] }> };
  } | null;
  const fs = report?.final_score || {};
  const categories = report?.evaluation?.categories || [];

  // override 적용 카운트 — item.score !== item.llm_score (또는 override 플래그) 인 항목 수.
  const overrideCount = allItems.filter((it) => {
    const r = it as unknown as {
      override_applied?: boolean;
      llm_score?: number;
      rule_score?: number;
      score?: number;
    };
    if (r.override_applied) return true;
    if (typeof r.llm_score === "number" && typeof r.score === "number" && r.llm_score !== r.score) {
      return true;
    }
    return false;
  }).length;

  const reviewCount = allItems.filter(
    (it) => (it as unknown as { mandatory_human_review?: boolean }).mandatory_human_review,
  ).length;

  // consistency_check — orchestrator 결과 또는 trace.output 폴백.
  const consistency =
    ((result as unknown as { consistency_check?: Record<string, unknown> } | null)
      ?.consistency_check as Record<string, unknown> | undefined) ||
    (traceOutput?.consistency_check as Record<string, unknown> | undefined) ||
    null;
  const consistencyOk =
    consistency && (consistency.ok === true || consistency.passed === true);
  const consistencyIssues =
    (consistency?.issues as unknown[] | undefined)?.length ??
    (consistency?.violations as unknown[] | undefined)?.length ??
    0;

  const hasData =
    !!fs.raw_total || !!fs.after_overrides || categories.length > 0 || allItems.length > 0;
  if (!hasData) {
    return <Layer4EmptyHint label="Layer 3 집계 결과가 아직 도착하지 않았습니다" />;
  }

  return (
    <div className="drawer-section">
      <div className="drawer-section-title">Layer 3 — 집계 / 보정</div>

      {/* 4-cell 요약 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 6,
          marginBottom: 10,
        }}
      >
        {[
          ["raw 총점", fs.raw_total ?? "—"] as const,
          ["override 후", fs.after_overrides ?? "—"] as const,
          ["override 적용", overrideCount] as const,
          ["검수 필수", reviewCount] as const,
        ].map(([lab, val], i) => (
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
                fontSize: 9.5,
                color: "var(--ink-muted)",
                fontWeight: 700,
                letterSpacing: "0.04em",
              }}
            >
              {lab}
            </div>
            <div
              className="tabular-nums"
              style={{ fontSize: 14, fontWeight: 800, color: "var(--ink)", marginTop: 2 }}
            >
              {val}
            </div>
          </div>
        ))}
      </div>

      {/* consistency_check */}
      {consistency && (
        <div
          style={{
            marginBottom: 10,
            padding: "6px 10px",
            background: consistencyOk
              ? "rgba(34,197,94,0.06)"
              : "rgba(245,158,11,0.06)",
            border: `1px solid ${
              consistencyOk ? "rgba(34,197,94,0.25)" : "rgba(245,158,11,0.25)"
            }`,
            borderRadius: 4,
            fontSize: 11,
          }}
        >
          <b style={{ color: consistencyOk ? "#166534" : "#92400e" }}>
            Consistency Check: {consistencyOk ? "통과" : `위반 ${consistencyIssues}건`}
          </b>
        </div>
      )}

      {/* 카테고리별 점수 합계 */}
      {categories.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--ink-soft)", marginBottom: 4 }}>
            카테고리별 점수
          </div>
          <table style={{ width: "100%", fontSize: 10.5, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--surface-muted)", color: "var(--ink-soft)" }}>
                <th style={{ textAlign: "left", padding: "4px 6px" }}>카테고리</th>
                <th style={{ textAlign: "right", padding: "4px 6px" }}>점수</th>
                <th style={{ textAlign: "right", padding: "4px 6px" }}>최대</th>
                <th style={{ textAlign: "right", padding: "4px 6px" }}>항목수</th>
              </tr>
            </thead>
            <tbody>
              {categories.map((c, i) => {
                const total = c.items.reduce((s, it) => s + (Number(it.score) || 0), 0);
                const maxTotal = c.items.reduce(
                  (s, it) => s + (Number(it.max_score) || 0),
                  0,
                );
                return (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "3px 6px", color: "var(--ink-soft)" }}>
                      {c.category}
                    </td>
                    <td
                      style={{ padding: "3px 6px", textAlign: "right", fontWeight: 700 }}
                      className="tabular-nums"
                    >
                      {total}
                    </td>
                    <td
                      style={{
                        padding: "3px 6px",
                        textAlign: "right",
                        color: "var(--ink-muted)",
                      }}
                      className="tabular-nums"
                    >
                      {maxTotal}
                    </td>
                    <td
                      style={{
                        padding: "3px 6px",
                        textAlign: "right",
                        color: "var(--ink-muted)",
                      }}
                      className="tabular-nums"
                    >
                      {c.items.length}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {allDeductions.length > 0 && (
        <div style={{ fontSize: 10.5, color: "var(--ink-muted)" }}>
          감점 목록 {allDeductions.length}건 — 자세한 항목은 Deductions 섹션 참조.
        </div>
      )}
    </div>
  );
}

function CombinedReportPanel({
  result,
  allItems,
  allDeductions,
}: {
  result: EvaluationResult | null;
  allItems: CategoryItem[];
  allDeductions: Array<{
    item_number?: number;
    item?: number;
    reason?: string;
    description?: string;
    deduction?: number;
    points?: number;
    points_lost?: number;
  }>;
}) {
  // ReportGeneratorPanel 의 데이터 구조와 동일 (기존 평가 + KSQI 합류).
  // 라벨만 "통합 리포트" 로 강조.
  const report = (result?.report || {}) as {
    final_score?: { grade?: string; raw_total?: number; after_overrides?: number };
    evaluation?: { categories?: Array<{ category: string; items: CategoryItem[] }> };
  } | null;
  const fs = report?.final_score || {};
  const categories = report?.evaluation?.categories || [];
  const totalDeductionPts = allDeductions.reduce(
    (s, d) => s + (d.deduction ?? d.points ?? d.points_lost ?? 0),
    0,
  );

  // KSQI 결과 폴백 (있으면 함께 노출).
  const ksqi = (result as unknown as { ksqi?: Record<string, unknown> } | null)
    ?.ksqi as { score?: number; grade?: string } | undefined;

  const hasData =
    !!fs.raw_total || !!fs.after_overrides || categories.length > 0 || allItems.length > 0;
  if (!hasData) {
    return <Layer4EmptyHint label="통합 보고서 데이터가 아직 도착하지 않았습니다" />;
  }

  return (
    <div className="drawer-section">
      <div className="drawer-section-title">통합 리포트 (KSQI + Layer 4)</div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 6,
          marginBottom: 10,
        }}
      >
        {[
          ["Grade", fs.grade ?? "—"] as const,
          ["raw 총점", fs.raw_total ?? "—"] as const,
          ["override 총점", fs.after_overrides ?? "—"] as const,
          ["감점 합계", `−${totalDeductionPts}`] as const,
        ].map(([lab, val], i) => (
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
                fontSize: 9.5,
                color: "var(--ink-muted)",
                fontWeight: 700,
                letterSpacing: "0.04em",
              }}
            >
              {lab}
            </div>
            <div
              className="tabular-nums"
              style={{ fontSize: 14, fontWeight: 800, color: "var(--ink)", marginTop: 2 }}
            >
              {val}
            </div>
          </div>
        ))}
      </div>

      {ksqi && (typeof ksqi.score === "number" || typeof ksqi.grade === "string") && (
        <div
          style={{
            marginBottom: 10,
            padding: "6px 10px",
            background: "rgba(99,102,241,0.05)",
            border: "1px solid rgba(99,102,241,0.2)",
            borderRadius: 4,
            fontSize: 11,
          }}
        >
          <b style={{ color: "#3730a3" }}>KSQI:</b>{" "}
          {typeof ksqi.score === "number" ? `${ksqi.score}점` : ""}
          {typeof ksqi.grade === "string" ? ` (${ksqi.grade})` : ""}
        </div>
      )}

      {categories.length > 0 && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--ink-soft)", marginBottom: 4 }}>
            카테고리별 점수 (통합)
          </div>
          <table style={{ width: "100%", fontSize: 10.5, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--surface-muted)", color: "var(--ink-soft)" }}>
                <th style={{ textAlign: "left", padding: "4px 6px" }}>카테고리</th>
                <th style={{ textAlign: "right", padding: "4px 6px" }}>점수</th>
                <th style={{ textAlign: "right", padding: "4px 6px" }}>최대</th>
              </tr>
            </thead>
            <tbody>
              {categories.map((c, i) => {
                const total = c.items.reduce((s, it) => s + (Number(it.score) || 0), 0);
                const maxTotal = c.items.reduce(
                  (s, it) => s + (Number(it.max_score) || 0),
                  0,
                );
                return (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "3px 6px", color: "var(--ink-soft)" }}>
                      {c.category}
                    </td>
                    <td
                      style={{ padding: "3px 6px", textAlign: "right", fontWeight: 700 }}
                      className="tabular-nums"
                    >
                      {total}
                    </td>
                    <td
                      style={{
                        padding: "3px 6px",
                        textAlign: "right",
                        color: "var(--ink-muted)",
                      }}
                      className="tabular-nums"
                    >
                      {maxTotal}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ReportNarratorPanel({
  result,
  traceOutput,
  state,
}: {
  result: EvaluationResult | null;
  traceOutput: Record<string, unknown> | null;
  state: string;
}) {
  // report_llm_summary — narrative + strengths + improvements + coaching_points.
  type Summary = {
    narrative?: string;
    overall_summary?: string;
    summary?: string;
    strengths?: string[] | string;
    improvements?: string[] | string;
    coaching_points?: string[] | string;
    coaching?: string[] | string;
  };
  const fromResult = (result as unknown as { report_llm_summary?: Summary } | null)
    ?.report_llm_summary;
  const fromTrace = (traceOutput?.report_llm_summary as Summary | undefined) || undefined;
  const summary: Summary | null = fromResult || fromTrace || null;

  if (!summary) {
    const reason =
      state === "skipped"
        ? "LLM 호출 안 됨 (skipped — skip_phase_c_and_reporting 등)"
        : state === "active"
          ? "AI 마무리 총평 작성 중 — 결과 도착 즉시 표시됩니다."
          : state === "done"
            ? "report_llm_summary 미도착 (LLM 실패 또는 비활성)"
            : "아직 실행되지 않았습니다.";
    return <Layer4EmptyHint label={reason} />;
  }

  const narrative = summary.narrative || summary.overall_summary || summary.summary || "";
  const toList = (v: string[] | string | undefined): string[] => {
    if (!v) return [];
    return Array.isArray(v) ? v.filter((s) => !!s) : [v];
  };
  const strengths = toList(summary.strengths);
  const improvements = toList(summary.improvements);
  const coaching = toList(summary.coaching_points || summary.coaching);

  return (
    <div className="drawer-section">
      <div className="drawer-section-title">AI 마무리 총평</div>

      {narrative && (
        <div
          style={{
            marginBottom: 10,
            padding: "10px 12px",
            background: "var(--surface-muted)",
            border: "1px solid var(--border)",
            borderRadius: 4,
            fontSize: 12,
            color: "var(--ink-soft)",
            lineHeight: 1.6,
            whiteSpace: "pre-wrap",
          }}
        >
          {narrative}
        </div>
      )}

      {strengths.length > 0 && (
        <NarratorList title="강점" items={strengths} accent="#16a34a" bg="rgba(34,197,94,0.06)" />
      )}
      {improvements.length > 0 && (
        <NarratorList
          title="개선점"
          items={improvements}
          accent="#d97706"
          bg="rgba(245,158,11,0.06)"
        />
      )}
      {coaching.length > 0 && (
        <NarratorList
          title="코칭 포인트"
          items={coaching}
          accent="#6366f1"
          bg="rgba(99,102,241,0.06)"
        />
      )}

      {!narrative && strengths.length === 0 && improvements.length === 0 && coaching.length === 0 && (
        <Layer4EmptyHint label="LLM 응답이 비어 있습니다 — 토큰 한도 초과 또는 파싱 실패 의심" />
      )}
    </div>
  );
}

function NarratorList({
  title,
  items,
  accent,
  bg,
}: {
  title: string;
  items: string[];
  accent: string;
  bg: string;
}) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          color: accent,
          marginBottom: 4,
          letterSpacing: "0.04em",
        }}
      >
        {title} ({items.length})
      </div>
      <ul
        style={{
          margin: 0,
          padding: "6px 10px 6px 22px",
          background: bg,
          border: `1px solid ${accent}33`,
          borderRadius: 4,
          fontSize: 11,
          color: "var(--ink-soft)",
          lineHeight: 1.55,
        }}
      >
        {items.map((s, i) => (
          <li key={i} style={{ marginBottom: 2 }}>
            {s}
          </li>
        ))}
      </ul>
    </div>
  );
}
