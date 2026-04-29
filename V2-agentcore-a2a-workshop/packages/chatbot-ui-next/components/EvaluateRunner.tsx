// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  INITIAL_DEBATE_STATE,
  type DebateRoundUI,
  type DebateState,
} from "@/components/DebatePanel";
import { DiscussionModal, type DiscussionStartMode } from "@/components/DiscussionModal";
import { ManualEvalAttach } from "@/components/ManualEvalAttach";
import PostRunReviewModal, {
  extractTurnsFromPreprocessing,
} from "@/components/PostRunReviewModal";
import { TenantStatusBadge } from "@/components/TenantStatusBadge";
import { bumpTenantFlash } from "@/lib/tenantFlash";
import { useAppState, useConsultationId } from "@/lib/AppStateContext";
import {
  apiSSE,
  getBaseUrl,
  nextDiscussionRound,
  saveResultXlsx,
  setRuntimeBaseUrl,
  startDiscussion,
} from "@/lib/api";
import { MODEL_GROUPS } from "@/lib/models";
import { decodeFileToText } from "@/lib/textDecode";
import { useToast } from "@/lib/toast";
import { buildResultsXlsx, todaySubfolder } from "@/lib/xlsxExport";
import {
  EDGES,
  LAYER1_NODES,
  LAYER2_NODES,
  LAYER3_NODES,
  LAYER4_NODES,
  LEGACY_TO_V2_NODE,
  NODE_DEFS,
  NODE_ITEMS,
  NODE_TO_DEBATE_ITEMS,
  edgeKey,
  getEffectivePipeline,
  getTenantPipelineConfig,
  type NodeState,
} from "@/lib/pipeline";
import { isPersona, type Persona } from "@/lib/personas";
import type {
  DebateFinalEvent,
  EvaluationResult,
  ModeratorVerdictEvent,
  PersonaTurnEvent,
  Report,
} from "@/lib/types";

// ReactFlow SSR 불가 → dynamic import ssr:false
const PipelineFlow = dynamic(
  () => import("@/components/PipelineFlow").then((m) => m.PipelineFlow),
  { ssr: false, loading: () => <PipelineSkeleton /> },
);

function PipelineSkeleton() {
  return (
    <div className="flex h-[640px] w-full items-center justify-center rounded-[var(--radius-lg)] border border-dashed border-[var(--border-strong)] bg-[var(--surface-muted)] text-sm text-[var(--ink-muted)]">
      파이프라인 로드 중…
    </div>
  );
}

interface LogEntry {
  time: string;
  msg: string;
  type: "info" | "success" | "warn" | "error";
}

interface RoutingEvent {
  next_node?: string;
  next_label?: string;
  phase?: string;
  phase_label?: string;
  tenant_id?: string;
}

interface StatusEvent {
  node?: string;
  label?: string;
  status?: string;
  elapsed?: number;
  scores?: Array<{ item_number?: number; score?: number }>;
  node_status?: string;
}

interface ResultEvent {
  report?: Report | null;
  verification?: unknown;
  score_validation?: unknown;
  status?: string;
}

interface DoneEvent {
  elapsed_seconds?: number;
}

interface ErrorEvent {
  message?: string;
  type?: string;
}

function initNodeStates(saved?: Record<string, string>): Record<string, NodeState> {
  const out: Record<string, NodeState> = {};
  Object.keys(NODE_DEFS).forEach((k) => {
    const prev = saved?.[k];
    out[k] = (prev as NodeState) || "pending";
  });
  return out;
}

function initEdgeStates(saved?: Record<string, string>): Record<string, NodeState> {
  const out: Record<string, NodeState> = {};
  EDGES.forEach((e) => {
    const k = edgeKey(e.from, e.to);
    const prev = saved?.[k];
    out[k] = (prev as NodeState) || "pending";
  });
  return out;
}

function timestamp(): string {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}

/* ─────────────────────────────────────────────────────────────
   ManualQALinkedCard — 사람 QA 연동 상태 인터랙티브 카드.
   - 헤더: 아바타 + 요약 (총점/항목수/소스) + 클릭 시 expand
   - expand: 항목별 점수 그리드 + sample_id + 소스 메타
   - 미연동: dashed placeholder
   ───────────────────────────────────────────────────────────── */
interface ManualQALinkedCardProps {
  manualEval: import("@/lib/manualEvalParser").ManualSheet | null;
  gtScores: import("@/lib/types").GTScore | null;
  justLinked: boolean;
}

function ManualQALinkedCard({ manualEval, gtScores, justLinked }: ManualQALinkedCardProps) {
  const [expanded, setExpanded] = useState(false);

  const linked = !!(manualEval || gtScores);
  const source: "manual" | "gt" | null = manualEval ? "manual" : gtScores ? "gt" : null;

  if (!linked) {
    return (
      <div
        className="mt-2"
        style={{
          fontSize: 11.5,
          padding: "10px 14px",
          borderRadius: 10,
          background: "#fbf8ed",
          border: "1px dashed #d4c8a8",
          color: "#806328",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <span style={{ fontSize: 14 }}>○</span>
        <span style={{ fontWeight: 500 }}>
          사람 QA 미연동 — xlsx 첨부 또는 GT 자동 매칭 시 AI 결과와 항목별 비교 활성화
        </span>
      </div>
    );
  }

  // 연동됨 — 요약 정보
  const sampleId =
    manualEval?.sheetId ??
    (gtScores as { sample_id?: string } | null)?.sample_id ??
    "?";
  const items: Array<{
    item_number?: number;
    item_name?: string | number | null;
    score?: number | null;
    max_score?: number | null;
    remark?: string | null;
    note?: string | null;
  }> =
    (manualEval?.rows as unknown as Array<{
      item_number?: number;
      item_name?: string | number | null;
      score?: number | null;
      max_score?: number | null;
      remark?: string | null;
    }>) ||
    ((gtScores as { items?: Array<{
      item_number?: number;
      item_name?: string | number | null;
      score?: number | null;
      max_score?: number | null;
      note?: string | null;
    }> } | null)?.items ?? []);

  // total 계산: 명시 필드 (manual.total 또는 gt.total_score) 우선, 없으면 items.score 합산
  const explicitTotal =
    manualEval?.total ??
    (gtScores as { total_score?: number } | null)?.total_score ??
    null;
  const computedTotal =
    explicitTotal ??
    (items.length > 0
      ? items.reduce((sum, it) => sum + (typeof it.score === "number" ? it.score : 0), 0)
      : null);
  const total = computedTotal;

  return (
    <div
      className="mt-2"
      style={{
        borderRadius: 10,
        background: "#ffffff",
        border: "1px solid #2e7d4f44",
        boxShadow: justLinked
          ? "0 0 0 0 rgba(46,125,79,0.45)"
          : "0 1px 2px rgba(0,0,0,0.04)",
        animation: justLinked ? "linkedChipPulse 1.4s ease-in-out 3" : undefined,
        overflow: "hidden",
      }}
    >
      {/* Header — 클릭 시 expand */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "10px 14px",
          background: "#e6f3ec",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
          transition: "background 0.15s ease",
        }}
        onMouseEnter={(e) => (e.currentTarget.style.background = "#d9ecdf")}
        onMouseLeave={(e) => (e.currentTarget.style.background = "#e6f3ec")}
      >
        <span style={{ fontSize: 18 }} aria-hidden="true">👤</span>
        <span style={{ flex: 1, fontSize: 12.5, color: "#14110d" }}>
          <span style={{ fontWeight: 700, color: "#2e7d4f" }}>사람 QA 연동됨</span>
          {justLinked && (
            <span
              style={{
                marginLeft: 8,
                padding: "2px 7px",
                fontSize: 10.5,
                fontWeight: 700,
                color: "#ffffff",
                background: "#2e7d4f",
                borderRadius: 9999,
                animation: "linkedChipPulse 1.4s ease-in-out 3",
                display: "inline-block",
              }}
            >
              ✨ NEW
            </span>
          )}
          <span style={{ color: "#7a7567", marginLeft: 8 }}>·</span>
          <span style={{ marginLeft: 8, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
            {total != null ? `${total}/100` : "—/100"}
          </span>
          <span style={{ color: "#7a7567", marginLeft: 8 }}>·</span>
          <span style={{ marginLeft: 8, color: "#4a4a4a" }}>{items.length}개 항목</span>
          <span style={{ color: "#7a7567", marginLeft: 8 }}>·</span>
          <span
            style={{
              marginLeft: 8,
              fontSize: 10.5,
              padding: "2px 7px",
              borderRadius: 4,
              background: source === "manual" ? "#f1e8f6" : "#eaf2fb",
              color: source === "manual" ? "#6a4485" : "#3a5a82",
              fontWeight: 600,
            }}
          >
            {source === "manual" ? "수동 첨부" : "GT 자동매칭"}
          </span>
          <span style={{ color: "#7a7567", marginLeft: 8 }}>·</span>
          <span style={{ marginLeft: 8, fontFamily: "ui-monospace, monospace", fontSize: 11, color: "#7a7567" }}>
            {sampleId}
          </span>
        </span>
        <span
          style={{
            fontSize: 10.5,
            color: "#2e7d4f",
            fontWeight: 600,
            transition: "transform 0.18s ease",
            transform: expanded ? "rotate(180deg)" : "rotate(0deg)",
          }}
          aria-hidden="true"
        >
          ▼
        </span>
      </button>

      {/* Expanded — 항목별 점수 그리드 */}
      {expanded && items.length > 0 && (
        <div
          style={{
            padding: "10px 14px 12px",
            background: "#fdfdfa",
            borderTop: "1px solid #e7e3d4",
          }}
        >
          <div
            style={{
              fontSize: 10.5,
              fontWeight: 700,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              color: "#7a7567",
              marginBottom: 6,
            }}
          >
            항목별 점수
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
              gap: 6,
            }}
          >
            {items.map((it, i) => {
              const ratio = it.score != null && it.max_score ? it.score / it.max_score : null;
              const cls =
                ratio == null
                  ? "#7a7567"
                  : ratio >= 0.8
                    ? "#2e7d4f"
                    : ratio >= 0.5
                      ? "#c96442"
                      : "#b03a2e";
              return (
                <div
                  key={`${it.item_number}-${i}`}
                  title={
                    (it.remark as string | undefined) ||
                    (it.note as string | undefined) ||
                    String(it.item_name ?? "")
                  }
                  style={{
                    fontSize: 11,
                    padding: "5px 8px",
                    borderRadius: 6,
                    background: "#ffffff",
                    border: "1px solid #e7e3d4",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 6,
                    cursor: it.remark ? "help" : "default",
                  }}
                >
                  <span style={{ color: "#14110d", fontWeight: 600, minWidth: 28 }}>
                    #{it.item_number}
                  </span>
                  <span
                    style={{
                      flex: 1,
                      fontSize: 10.5,
                      color: "#7a7567",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {String(it.item_name ?? "")}
                  </span>
                  <span
                    style={{
                      color: cls,
                      fontWeight: 700,
                      fontVariantNumeric: "tabular-nums",
                      fontSize: 11,
                    }}
                  >
                    {it.score ?? "—"}
                    <span style={{ color: "#bfbaa8", fontWeight: 400 }}>
                      /{it.max_score ?? "?"}
                    </span>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export function EvaluateRunner() {
  // ── AppStateContext bridge ──────────────────────────────────
  // EvaluateRunner 는 로컬 state 로 PipelineFlow/DebatePanel 에 직접 바인딩되지만,
  // 동일 데이터를 Context 로도 mirror → Dev3 (logs/traces/rawlogs) / Dev5 (results)
  // 가 Context 에서 읽어 각자의 탭에 렌더.
  const {
    state: appState,
    dispatch: appDispatch,
    appendLog: ctxAppendLog,
    appendTrace: ctxAppendTrace,
    appendRawLog: ctxAppendRawLog,
    setResult: ctxSetResult,
    setTenantId: ctxSetTenant,
    setTranscript: ctxSetTranscript,
    setServerUrl: ctxSetServerUrl,
  } = useAppState();
  const toast = useToast();

  const [transcript, setTranscript] = useState(appState.transcript || "");
  const [tenantId, setTenantId] = useState(appState.tenantId || "generic");
  const [backend, setBackend] = useState<"bedrock" | "sagemaker">(appState.llmBackend || "bedrock");
  // V2 qa_pipeline_reactflow.html L9076-9115 — Bedrock 선택 시 세부 모델 ID (null 이면 서버 기본값 사용).
  // Dev2 가 AppStateContext 에 추가한 bedrockModelId 필드를 Context bridge 로 사용.
  const bedrockModelId = appState.bedrockModelId;
  const setBedrockModelId = useCallback(
    (v: string | null) => appDispatch({ type: "SET_BEDROCK_MODEL", payload: v }),
    [appDispatch],
  );
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  // 평가 완료 시 자동으로 뜨는 HITL 검수 모달. done 이벤트에서 report/consultation_id 가 모두 잡히면 true.
  const [reviewModalOpen, setReviewModalOpen] = useState(false);
  const consultationIdForReview = useConsultationId();
  // ★ 탭 전환 시 EvaluateRunner 가 unmount→remount 되어도 직전 노드 결과를 잃지 않도록
  // 로컬 state 초기값을 AppStateContext 의 saved record 에서 복원 (없으면 기본값).
  // 평가 실행 중에도 여전히 setNodeStates/...로 갱신 → mirror effect 가 context 동기화.
  const [nodeStates, setNodeStates] = useState<Record<string, NodeState>>(() =>
    initNodeStates(appState.nodeStates),
  );
  const [edgeStates, setEdgeStates] = useState<Record<string, NodeState>>(() =>
    initEdgeStates(appState.edgeStates),
  );
  const [nodeScores, setNodeScores] = useState<Record<string, number>>(
    () => appState.nodeScores ?? {},
  );
  const [nodeTimings, setNodeTimings] = useState<Record<string, number>>(
    () => appState.nodeTimings ?? {},
  );
  // 노드당 평균 LLM confidence — items[].confidence 평균 (0~1). 노드에 badge 로 표시.
  const [nodeConfidence, setNodeConfidence] = useState<Record<string, number>>(
    () => appState.nodeConfidence ?? {},
  );
  const [debate, setDebate] = useState<DebateState>(INITIAL_DEBATE_STATE);
  // ★ 병렬 토론 지원 — 노드별 독립 DebateState. `debate` 는 "마지막 갱신된 토론" 하이라이트용으로만 유지.
  // 여러 item 이 동시 실행되는 경우 event 가 interleave 되어 단일 state 가 깨지므로 per-node map 필수.
  const [debateByNode, setDebateByNode] = useState<Record<string, DebateState>>({});
  // ★ item_number 별 독립 DebateState — 한 노드 안에 여러 토론 가능하도록.
  // proactiveness 노드는 #12/#13/#14 세 토론, debateByNode 하나로는 덮어씀 → per-item.
  const [debateByItem, setDebateByItem] = useState<Record<number, DebateState>>({});
  // 현재 발언 중인 페르소나 (persona_speaking 수신 → message 도착 전까지 유지)
  const [speakingByNode, setSpeakingByNode] = useState<Record<string, Persona | null>>({});
  const [attachedFile, setAttachedFile] = useState<{ name: string; size: number } | null>(null);

  // ── 노드별 토론 진행 상태 추적 ──
  // itemToNodeId 정의는 effectiveLayer2Children 뒤(아래)에 위치 — 테넌트 effective
  // pipeline 으로 필터해야 #12/#13/#14 가 dept-only 노드로 잘못 매핑되는 버그를 막음.

  // ── POST-DEBATE 게이트 ────────────────────────────────────
  // 사용자 UX 모델: "토론이 안 끝났으면 다음 단계로 불 들어오면 안 됨"
  // 백엔드 실제 순서는 layer3 → debate → layer4 (debate 가 layer3 뒤) 이지만,
  // 사용자에겐 debate 가 Layer 2 아이템에 대한 토론이라 "Layer 3 도달 = 토론 완료" 로 인식.
  // 따라서 프론트에선 debate 가 하나라도 running 인 동안 layer3 는 "active" 로 묶어두고,
  // post-debate 노드/엣지는 pending 으로 유지. 토론 모두 done 되면 보류된 전이 재생.
  const POST_DEBATE_NODES = useMemo(
    () => new Set([
      "confidence",
      "tier_router",
      "evidence_refiner",
      "layer4",
      // 시각 노드는 LLM 정성 비교 하나만 — 백엔드 점수 비교(gt_comparison) 는 alias 로 흡수.
      "gt_evidence_comparison",
      "hitl_queue_populator",
    ]),
    [],
  );
  const anyDebateRunningRef = useRef(false);
  // 토론 중에 status/routing 이벤트로 activate 하려다 막힌 노드들 — 토론 종료 후 재생
  const pendingPostDebateRef = useRef<Set<string>>(new Set());
  const layer3HeldRef = useRef(false); // layer3 가 "done" 받았지만 debate 때문에 보류 중

  // ★ 노드별 토론 상태 per-node accumulator.
  // 기존에는 debate.item_number 기준으로 단일 노드 상태만 파생 — 다음 item 으로 넘어가면
  // 이전 노드 뱃지가 사라지는 문제. SSE 이벤트 직접 수신해서 per-node 누적.
  const [debateStatusByNode, setDebateStatusByNode] = useState<
    Record<string, "idle" | "running" | "done">
  >(() => appState.debateStatusByNode ?? {});
  const [debateRoundByNode, setDebateRoundByNode] = useState<
    Record<string, { round: number; max: number }>
  >(() => appState.debateRoundByNode ?? {});

  // anyDebateRunning 의 프리미티브 값 (effect 의존) — POST_DEBATE_NODES 선언 뒤,
  // debateStatusByNode 선언 뒤로 배치해야 TDZ 안전.
  const anyDebateRunning = useMemo(
    () => Object.values(debateStatusByNode).some((s) => s === "running"),
    [debateStatusByNode],
  );
  useEffect(() => {
    anyDebateRunningRef.current = anyDebateRunning;
  }, [anyDebateRunning]);

  // 토론 모달 — 사용자가 노드 버튼 클릭 시 어떤 nodeId 토론을 띄울지
  const [discussionNodeId, setDiscussionNodeId] = useState<string | null>(null);

  // 자동 오픈 (토론 시작될 때 자동으로 모달 열기) — 기본 OFF.
  // 노드 위 인터랙티브 배지로 토론 진행 표시. 사용자가 그걸 클릭해 모달 오픈.
  const [autoOpenDiscussion, setAutoOpenDiscussion] = useState(false);

  // 백엔드가 발급한 discussion_id — 토론 시작/다음라운드/중단 API 호출에 사용.
  const [discussionIdMap, setDiscussionIdMap] = useState<Record<string, string>>({});

  // 사람 QA 연동 transition 감지 — sample_id (식별자) 기준으로 추적.
  // null → 값  : "새로 연동" 펄스 + 토스트
  // valueA → valueB : "샘플 변경" 펄스 + 토스트
  // 동일 ref 객체 재할당만 일어나면 (sample_id 동일) 발화 X — 깜빡임 방지의 핵심
  const manualSampleId = appState.manualEval?.sheetId ?? null;
  const gtSampleId =
    (appState.gtScores as { sample_id?: string } | null)?.sample_id ?? null;
  const wasManualSampleIdRef = useRef<string | null>(manualSampleId);
  const wasGtSampleIdRef = useRef<string | null>(gtSampleId);
  const [justLinked, setJustLinked] = useState<"manual" | "gt" | null>(null);

  // 1) Transition 감지 effect — sample_id 가 실제로 바뀐 경우만 setJustLinked
  useEffect(() => {
    let triggered: "manual" | "gt" | null = null;
    if (manualSampleId && manualSampleId !== wasManualSampleIdRef.current) {
      triggered = "manual";
    } else if (
      gtSampleId &&
      gtSampleId !== wasGtSampleIdRef.current &&
      !manualSampleId
    ) {
      triggered = "gt";
    }
    wasManualSampleIdRef.current = manualSampleId;
    wasGtSampleIdRef.current = gtSampleId;
    if (triggered) {
      setJustLinked(triggered);
      // 사용자 요청 — 토스트 알림 제거. 카드 헤더의 ✨ NEW 펄스 배지로만 알림.
    }
  }, [manualSampleId, gtSampleId]);

  // 2) justLinked 자동 해제 — 별도 effect 로 cleanup race 회피
  useEffect(() => {
    if (!justLinked) return;
    const t = setTimeout(() => setJustLinked(null), 4500);
    return () => clearTimeout(t);
  }, [justLinked]);

  // 사용자가 dismiss 한 nodeId 들 — 자동으로 다시 열지 않음
  const [dismissedNodes, setDismissedNodes] = useState<Set<string>>(new Set());

  // 이전 item_number (transition 감지용) — 같은 토론 세션에서 한 번만 자동 오픈
  const prevDebateItemRef = useRef<number | null>(null);

  // ★ debate.active 자동 모달 오픈 useEffect 는 itemToNodeId 정의(아래) 뒤로 이동했음.
  //   itemToNodeId 가 effectiveLayer2Children 에 의존하게 되어 선언 위치가 더 뒤로 밀렸기 때문.

  const abortRef = useRef<null | (() => void)>(null);

  // ★ PipelineFlow 로 내려갈 콜백들은 반드시 참조 안정화 — 그렇지 않으면 elapsed 타이머/state
  // 변경마다 새 참조가 내려가 PipelineFlow 의 memo(arePropsEqual)를 무력화한다.
  // 결과: 매 tick 마다 nodes useMemo 가 재실행 → ReactFlow 가 DOM 재생성 → hover 시
  // 커서가 "새로고침처럼" 깜빡이는 증상.
  const handleNodeClick = useCallback(
    (id: string) => appDispatch({ type: "SET_OPEN_NODE", payload: id }),
    [appDispatch],
  );
  const handleDebateOpen = useCallback(
    (nid: string) => setDiscussionNodeId(nid),
    [],
  );
  const startedAtRef = useRef<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // ── Context mirror — SSE 이벤트 폭주 시 끊김 방지 ──
  // 기존 구현: state 별 분리된 12개 useEffect → 한 SSE 이벤트로 5+개 state 변경 시
  //   commit → useEffect[1] → dispatch → commit → useEffect[2] → ... 의 cascade 발생
  //   → AppStateContext consumer 들이 5+회 re-render → 끊김.
  // 개선: 단일 useEffect 로 통합 + rAF throttle 로 dispatch frame coalesce.
  //   같은 frame 내 다중 state 변경 → 1회 batch dispatch → 1회 re-render.

  // 빈도가 낮은 메타 (transcript / tenantId / backend / running) 는 별도 즉시 동기화 — 사용자 액션 기반.
  useEffect(() => { ctxSetTranscript(transcript); }, [transcript, ctxSetTranscript]);
  useEffect(() => { ctxSetTenant(tenantId); }, [tenantId, ctxSetTenant]);
  useEffect(() => { appDispatch({ type: "SET_BACKEND", payload: backend }); }, [backend, appDispatch]);
  useEffect(() => { appDispatch({ type: "SET_RUNNING", payload: running }); }, [running, appDispatch]);

  // 빈도 높은 SSE-driven state 들 — rAF coalesce 로 frame 당 1회만 dispatch.
  const _hotMirrorRafRef = useRef<number | null>(null);
  useEffect(() => {
    if (_hotMirrorRafRef.current != null) return; // 이미 frame queued
    _hotMirrorRafRef.current = requestAnimationFrame(() => {
      _hotMirrorRafRef.current = null;
      // React 18 자동 batching — 같은 microtask 내 다중 dispatch 는 1회 commit 으로 묶임.
      appDispatch({ type: "SET_ELAPSED", payload: elapsed });
      appDispatch({ type: "PATCH_NODE_STATES", payload: nodeStates });
      appDispatch({ type: "PATCH_EDGE_STATES", payload: edgeStates });
      appDispatch({ type: "PATCH_NODE_TIMINGS", payload: nodeTimings });
      appDispatch({ type: "PATCH_NODE_SCORES", payload: nodeScores });
      appDispatch({ type: "PATCH_NODE_CONFIDENCE", payload: nodeConfidence });
      appDispatch({ type: "PATCH_DEBATE_STATUS_BY_NODE", payload: debateStatusByNode });
      appDispatch({ type: "PATCH_DEBATE_ROUND_BY_NODE", payload: debateRoundByNode });
    });
    return () => {
      if (_hotMirrorRafRef.current != null) {
        cancelAnimationFrame(_hotMirrorRafRef.current);
        _hotMirrorRafRef.current = null;
      }
    };
  }, [
    elapsed,
    nodeStates, edgeStates, nodeTimings, nodeScores, nodeConfidence,
    debateStatusByNode, debateRoundByNode,
    appDispatch,
  ]);

  // Context serverUrl → lib/api.ts 런타임 BASE_URL 동기 (Task #5)
  useEffect(() => {
    setRuntimeBaseUrl(appState.serverUrl || null);
  }, [appState.serverUrl]);

  // ── Tenant 변경 감지 → flash + toast (2026-04-27) ─────────
  // 키 입력마다 즉시 발화하면 노이즈가 심하므로 500ms debounce.
  // 변경 후 500ms 동안 추가 입력이 없으면 flash 애니메이션 1회 + 토스트 1회.
  const [tenantFlashKey, setTenantFlashKey] = useState(0);
  const [tenantFlashing, setTenantFlashing] = useState(false);
  const prevTenantRef = useRef<{ siteId: string; channel: string; department: string }>({
    siteId: appState.siteId,
    channel: appState.channel,
    department: appState.department,
  });
  const tenantFlashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tenantFlashOffTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const prev = prevTenantRef.current;
    const cur = {
      siteId: appState.siteId,
      channel: appState.channel,
      department: appState.department,
    };
    if (
      prev.siteId === cur.siteId &&
      prev.channel === cur.channel &&
      prev.department === cur.department
    ) {
      return;
    }
    if (tenantFlashTimerRef.current) clearTimeout(tenantFlashTimerRef.current);
    tenantFlashTimerRef.current = setTimeout(() => {
      prevTenantRef.current = cur;
      setTenantFlashKey((k) => k + 1);
      setTenantFlashing(true);
      // ReactFlow data prop 경유가 아닌 module-level emitter 로 LayerNode 직접 통지.
      // (data 전파 누락 이슈 우회)
      bumpTenantFlash();
      if (tenantFlashOffTimerRef.current) clearTimeout(tenantFlashOffTimerRef.current);
      tenantFlashOffTimerRef.current = setTimeout(() => setTenantFlashing(false), 950);
      const detected = appState.tenantAutoDetected ? " (자동 감지)" : "";
      toast.info(
        `테넌트 전환${detected} → ${cur.siteId} · ${cur.channel} · ${cur.department}`,
        { duration: 2400 },
      );
    }, 500);
    return () => {
      if (tenantFlashTimerRef.current) clearTimeout(tenantFlashTimerRef.current);
    };
  }, [
    appState.siteId,
    appState.channel,
    appState.department,
    appState.tenantAutoDetected,
    toast,
  ]);
  useEffect(() => {
    return () => {
      if (tenantFlashTimerRef.current) clearTimeout(tenantFlashTimerRef.current);
      if (tenantFlashOffTimerRef.current) clearTimeout(tenantFlashOffTimerRef.current);
    };
  }, []);

  // PipelineFlow 에 내려갈 tenantContext — 매 렌더마다 새 객체 생기지 않게 메모.
  const pipelineTenantContext = useMemo(
    () => ({
      siteId: appState.siteId || "generic",
      channel: appState.channel || "inbound",
      department: appState.department || "default",
      flashKey: tenantFlashKey,
    }),
    [appState.siteId, appState.channel, appState.department, tenantFlashKey],
  );

  // 테넌트별 effective pipeline config (KSQI/GT/HITL on/off + sub-agent 라벨 오버라이드).
  // GT 비교 노드는 사용자가 실제로 GT 데이터 (manualEval xlsx 또는 gtScores) 를 연동했을 때만 노출.
  // 미연동 시 React Flow 그래프에서 gt_comparison + gt_evidence_comparison 노드 숨김.
  const hasGtData = Boolean(manualSampleId || gtSampleId);
  const tenantPipelineConfig = useMemo(
    () => {
      const base = getTenantPipelineConfig(appState.siteId, appState.department);
      // 테넌트가 명시적으로 GT 비활성 한 경우엔 그대로 유지, default(true) 인 경우엔 데이터 유무로 결정.
      const enableGt = base.enableGtComparison === false ? false : hasGtData;
      return { ...base, enableGtComparison: enableGt };
    },
    [appState.siteId, appState.department, hasGtData],
  );

  // 동적 edges + layer2 children — 신한 부서특화 노드 포함.
  // 정적 EDGES / LAYER2_NODES 만 쓰면 dept 노드 edge / 노드가 SSE 이벤트로 active 처리 안 됨.
  const effectivePipeline = useMemo(
    () => getEffectivePipeline(tenantPipelineConfig),
    [tenantPipelineConfig],
  );
  const effectiveEdges = effectivePipeline.edges;
  const effectiveLayer2Children = useMemo(
    () => effectivePipeline.groups.group_layer2?.children || LAYER2_NODES,
    [effectivePipeline],
  );

  // item_number → nodeId 역매핑. NODE_TO_DEBATE_ITEMS 는 base + 신한 dept 노드를 같이
  // 담고 있어 단순 inverse 시 같은 item_number 가 여러 nid 로 덮어쓰기됨. 예: #12 →
  // proactiveness 로 시작하지만 comp_unfair_sale_check 가 마지막 write wins → 기본
  // 테넌트(신한 아님)에선 comp_unfair_sale_check 가 렌더되지 않아 적극성 노드 LIVE 뱃지가
  // 안 뜸. effectiveLayer2Children (현재 테넌트가 실제 렌더하는 노드) 로 필터해 1:1 보장.
  const itemToNodeId = useMemo(() => {
    const map: Record<number, string> = {};
    const activeSet = new Set(effectiveLayer2Children);
    Object.entries(NODE_TO_DEBATE_ITEMS).forEach(([nid, items]) => {
      if (!activeSet.has(nid)) return;
      items.forEach((it) => {
        map[it] = nid;
      });
    });
    return map;
  }, [effectiveLayer2Children]);

  // debate.active 가 true 가 되면 자동으로 해당 노드의 모달을 띄움 (autoOpen 옵션 + 첫 transition 만)
  useEffect(() => {
    if (!autoOpenDiscussion) return;
    if (!debate.active || debate.item_number == null) {
      prevDebateItemRef.current = null;
      return;
    }
    // 같은 item_number 가 계속 active 인 동안에는 한 번만 처리
    if (prevDebateItemRef.current === debate.item_number) return;
    prevDebateItemRef.current = debate.item_number;

    const nid = itemToNodeId[debate.item_number];
    // 사용자가 이미 닫은 노드는 다시 열지 않음
    if (nid && !dismissedNodes.has(nid)) {
      setDiscussionNodeId(nid);
    }
  }, [debate.active, debate.item_number, autoOpenDiscussion, itemToNodeId, dismissedNodes]);

  // EDGES 와 deduplicate 합집합 — base 8 (정적) + dept extras (동적). 둘 다 흐르게.
  const allRunEdges = useMemo(() => {
    const seen = new Set<string>();
    const out: typeof EDGES = [];
    for (const e of [...EDGES, ...effectiveEdges]) {
      const k = `${e.from}->${e.to}`;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(e);
    }
    return out;
  }, [effectiveEdges]);

  // 자동 저장 — 평가 완료 시 result 1건당 1회 (Task #5)
  const lastAutoSavedRef = useRef<unknown>(null);
  useEffect(() => {
    if (!appState.autoSaveResult) return;
    const result = appState.lastResult;
    if (!result) return;
    const grade = (result as unknown as { report?: { summary?: { grade?: string } } })
      ?.report?.summary?.grade;
    if (!grade) return;
    if (lastAutoSavedRef.current === result) return;
    lastAutoSavedRef.current = result;
    (async () => {
      try {
        const built = await buildResultsXlsx(result, backend);
        if (!built) return;
        const blob = new Blob([built.buf], {
          type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        });
        const subfolder = todaySubfolder();
        const ret = await saveResultXlsx(blob, built.filename, subfolder);
        if (ret.ok) {
          const path = ret.path || `${subfolder}/${built.filename}`;
          appDispatch({ type: "SET_LAST_SAVED_PATH", payload: path });
          toast.success("자동 저장됨", { description: path, duration: 3000 });
        } else {
          toast.error("자동 저장 실패", {
            description: ret.error || "알 수 없는 오류",
          });
        }
      } catch (e) {
        toast.error("자동 저장 예외", {
          description: e instanceof Error ? e.message : String(e),
        });
      }
    })();
  }, [appState.lastResult, appState.autoSaveResult, backend, appDispatch, toast]);

  const onFilePicked = useCallback(
    async (file: File | null) => {
      if (!file) return;
      if (file.size > 10 * 1024 * 1024) {
        setLogs((prev) => [
          ...prev,
          { time: timestamp(), msg: `파일이 너무 큼 (${(file.size / 1024 / 1024).toFixed(1)}MB > 10MB)`, type: "error" },
        ]);
        return;
      }
      try {
        // V2 동작 복원 — EUC-KR 우선 디코드 + UTF-8 fallback (한국어 STT txt 대응).
        // JSON/MD 는 decodeFileToText 내부에서 UTF-8 우선으로 처리됨.
        const text = await decodeFileToText(file);
        let extractedTranscript = text;
        let detectedTenant: string | null = null;
        let detectedChannel: string | null = null;
        let detectedDepartment: string | null = null;
        let detectedSampleId = "";

        // V2 동작 복원 — JSON 파일이면 obj.transcript 추출 + tenant/sample_id 자동 감지
        if (file.name.toLowerCase().endsWith(".json")) {
          try {
            const obj = JSON.parse(text);
            if (obj && typeof obj.transcript === "string") {
              extractedTranscript = obj.transcript;
            }
            // 3단계 멀티테넌트 자동 감지 (2026-04-24).
            //   site_id   : obj.site_id > obj.tenant_id(레거시) > obj.tenant > obj.meta.tenant_id > "generic"
            //   channel   : obj.channel > obj.SITE_CD(원본 JSON 필드 = 채널 코드) > obj.site_cd > "inbound"
            //   department: obj.department > obj.dept > obj.meta.department > "default"
            const TENANT_MAP: Record<string, string> = {
              kolon: "kolon",
              cartgolf: "cartgolf",
              shinhan: "shinhan",
              generic: "generic",
            };
            const rawSite =
              (typeof obj?.site_id === "string" && obj.site_id) ||
              (typeof obj?.tenant_id === "string" && obj.tenant_id) ||
              (typeof obj?.tenant === "string" && obj.tenant) ||
              (typeof obj?.meta?.tenant_id === "string" && obj.meta.tenant_id) ||
              (typeof obj?.metadata?.tenant_id === "string" && obj.metadata.tenant_id) ||
              null;
            if (rawSite) {
              const lower = rawSite.toLowerCase();
              detectedTenant = TENANT_MAP[lower] || lower;
            }
            const rawChannel =
              (typeof obj?.channel === "string" && obj.channel) ||
              (typeof obj?.SITE_CD === "string" && obj.SITE_CD) ||
              (typeof obj?.site_cd === "string" && obj.site_cd) ||
              null;
            const rawDepartment =
              (typeof obj?.department === "string" && obj.department) ||
              (typeof obj?.dept === "string" && obj.dept) ||
              (typeof obj?.meta?.department === "string" && obj.meta.department) ||
              (typeof obj?.metadata?.department === "string" && obj.metadata.department) ||
              null;
            if (rawChannel) detectedChannel = String(rawChannel).toLowerCase();
            if (rawDepartment) detectedDepartment = String(rawDepartment).toLowerCase();
            // sample_id 자동 감지 — JSON 의 다양한 필드 우선, 없으면 파일명 숫자
            // 우선순위: id > sample_id > consultation_id > call_id > interaction_id
            //         > meta.id > meta.sample_id > metadata.id > metadata.sample_id
            //         > 파일명 6자리 이상 숫자
            const fromObj =
              (typeof obj?.id !== "undefined" && obj.id !== null && String(obj.id)) ||
              (typeof obj?.sample_id !== "undefined" && obj.sample_id !== null && String(obj.sample_id)) ||
              (typeof obj?.consultation_id !== "undefined" && obj.consultation_id !== null && String(obj.consultation_id)) ||
              (typeof obj?.call_id !== "undefined" && obj.call_id !== null && String(obj.call_id)) ||
              (typeof obj?.interaction_id !== "undefined" && obj.interaction_id !== null && String(obj.interaction_id)) ||
              (typeof obj?.meta?.id !== "undefined" && obj.meta.id !== null && String(obj.meta.id)) ||
              (typeof obj?.meta?.sample_id !== "undefined" && obj.meta.sample_id !== null && String(obj.meta.sample_id)) ||
              (typeof obj?.metadata?.id !== "undefined" && obj.metadata.id !== null && String(obj.metadata.id)) ||
              (typeof obj?.metadata?.sample_id !== "undefined" && obj.metadata.sample_id !== null && String(obj.metadata.sample_id)) ||
              "";
            const fileIdMatch =
              (file.name || "").match(/(\d{6,})/) ||
              (file.name || "").match(/(\d{4,})/);
            detectedSampleId = fromObj || (fileIdMatch ? fileIdMatch[1] : "");
          } catch {
            // JSON 파싱 실패 → raw text 사용 + 파일명에서만 sample_id 추출
            const fileIdMatch =
              (file.name || "").match(/(\d{6,})/) ||
              (file.name || "").match(/(\d{4,})/);
            detectedSampleId = fileIdMatch ? fileIdMatch[1] : "";
          }
        }

        setTranscript(extractedTranscript);
        setAttachedFile({ name: file.name, size: file.size });
        // 3단계 멀티테넌트 — site_id / channel / department 동시 반영.
        // 기존 setTenantId 대신 SET_TENANT dispatch 로 3필드 묶어 전달.
        if (detectedTenant || detectedChannel || detectedDepartment) {
          appDispatch({
            type: "SET_TENANT",
            payload: {
              tenantId: detectedTenant ?? appState.tenantId,
              autoDetected: true,
              siteId: detectedTenant ?? undefined,
              channel: detectedChannel ?? undefined,
              department: detectedDepartment ?? undefined,
            },
          });
          const parts: string[] = [];
          if (detectedTenant) parts.push(`site=${detectedTenant}`);
          if (detectedChannel) parts.push(`channel=${detectedChannel}`);
          if (detectedDepartment) parts.push(`department=${detectedDepartment}`);
          setLogs((prev) => [
            ...prev,
            {
              time: timestamp(),
              msg: `파일 첨부: ${file.name} (${(file.size / 1024).toFixed(1)}KB) · ${parts.join(" · ")} 자동 감지`,
              type: "success",
            },
          ]);
        } else {
          setLogs((prev) => [
            ...prev,
            { time: timestamp(), msg: `파일 첨부: ${file.name} (${(file.size / 1024).toFixed(1)}KB)`, type: "success" },
          ]);
        }

        // Dev5 ResultsPanel 용 — sample_id Context 에 저장 + GT scores fetch
        if (detectedSampleId) {
          appDispatch({ type: "SET_CONSULTATION_ID", payload: detectedSampleId });
          appDispatch({
            type: "SET_GT",
            payload: { sampleId: detectedSampleId, scores: null, error: "" },
          });
          setLogs((prev) => [
            ...prev,
            { time: timestamp(), msg: `sample_id=${detectedSampleId} 추출됨 → GT 조회 시도`, type: "info" },
          ]);
          try {
            // 환경별 BASE_URL 사용 — 로컬 dev=`http://localhost:8081`, EC2=`/api` (nginx→:8081).
            // `getBaseUrl()` 은 runtime override (AppStateContext.serverUrl) > env var 순.
            const base = getBaseUrl().replace(/\/$/, "");
            const url = `${base}/v2/gt-scores?sample_id=${encodeURIComponent(detectedSampleId)}`;
            const res = await fetch(url);
            const body = await res.json().catch(() => ({}));
            if (res.ok && body && body.items) {
              appDispatch({
                type: "SET_GT",
                payload: { sampleId: detectedSampleId, scores: body, error: "" },
              });
              setLogs((prev) => [
                ...prev,
                {
                  time: timestamp(),
                  msg: `✓ 수기 QA 정답 연동 완료 (id=${detectedSampleId} · 시트="${body.sheet_name}" · 매칭=${body.match_method || "?"} · 총점=${body.total_score}/100)`,
                  type: "success",
                },
              ]);
            } else {
              const errMsg = body?.error || `HTTP ${res.status}`;
              const sheetCount = Array.isArray(body?.available_sheets) ? body.available_sheets.length : 0;
              const xlsxPath = body?.xlsx_path ? ` · xlsx="${body.xlsx_path}"` : "";
              const triedHint = Array.isArray(body?.tried) ? ` · 탐색=${body.tried.join(", ")}` : "";
              appDispatch({
                type: "SET_GT",
                payload: {
                  sampleId: detectedSampleId,
                  scores: null,
                  error: `${errMsg}${xlsxPath}${triedHint} (시트 ${sheetCount}개)`,
                },
              });
              setLogs((prev) => [
                ...prev,
                {
                  time: timestamp(),
                  msg: `⚠ GT 연동 실패 [id=${detectedSampleId}]: ${errMsg}${xlsxPath}${triedHint}${sheetCount > 0 ? ` · 시트 ${sheetCount}개 발견 (앞 5개: ${body.available_sheets.slice(0, 5).join(", ")})` : ""}`,
                  type: "warn",
                },
              ]);
            }
          } catch (gtErr) {
            appDispatch({
              type: "SET_GT",
              payload: { sampleId: detectedSampleId, scores: null, error: String(gtErr) },
            });
            setLogs((prev) => [
              ...prev,
              {
                time: timestamp(),
                msg: `⚠ GT 연동 네트워크 오류 [id=${detectedSampleId}]: ${String(gtErr)}`,
                type: "error",
              },
            ]);
          }
        } else if (file.name.toLowerCase().endsWith(".json")) {
          setLogs((prev) => [
            ...prev,
            {
              time: timestamp(),
              msg: `⚠ JSON 에서 sample_id 추출 실패 — obj.id/sample_id/consultation_id/call_id 또는 파일명 4자리+ 숫자 필요`,
              type: "warn",
            },
          ]);
        }
      } catch (err) {
        setLogs((prev) => [
          ...prev,
          { time: timestamp(), msg: `파일 읽기 실패: ${String(err)}`, type: "error" },
        ]);
      }
    },
    [appDispatch, appState.serverUrl],
  );

  const clearAttachment = useCallback(() => {
    setAttachedFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, []);

  const addLog = useCallback(
    (msg: string, type: LogEntry["type"] = "info") => {
      const time = timestamp();
      setLogs((prev) => [...prev, { time, msg, type }]);
      // Context 로도 mirror → Dev3 LogsPanel 이 동일 데이터 표시
      ctxAppendLog({ time, msg, type });
    },
    [ctxAppendLog],
  );

  useEffect(() => {
    if (running) {
      startedAtRef.current = Date.now();
      timerRef.current = setInterval(() => {
        if (startedAtRef.current) {
          setElapsed((Date.now() - startedAtRef.current) / 1000);
        }
      }, 250);
    } else if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [running]);

  const activatePhaseGroup = useCallback((phaseNodes: string[]) => {
    setNodeStates((prev) => {
      const next = { ...prev };
      phaseNodes.forEach((n) => {
        if (next[n] !== "done" && next[n] !== "skipped") next[n] = "active";
      });
      return next;
    });
    setEdgeStates((prev) => {
      const next = { ...prev };
      phaseNodes.forEach((n) => {
        allRunEdges.forEach((e) => {
          const k = edgeKey(e.from, e.to);
          if (e.to === n && next[k] !== "done" && next[k] !== "skipped") {
            next[k] = "active";
          }
        });
      });
      return next;
    });
  }, [allRunEdges]);

  // 엣지 상태 전이는 단방향 — pending → active → done. 이미 done 된 엣지를 다시
  // active 로 덮어쓰면 동그라미 애니메이션이 깜빡거림 (SSE 이벤트 중복 리플레이 등).
  // 상태가 실제로 바뀌지 않으면 prev reference 그대로 반환 → React 재렌더 skip.
  const activateEdgesTo = useCallback((nodeId: string) => {
    // POST-DEBATE 게이트 — 토론 중에는 post-debate 엣지 active 보류
    if (anyDebateRunningRef.current && POST_DEBATE_NODES.has(nodeId)) {
      pendingPostDebateRef.current.add(nodeId);
      return;
    }
    setEdgeStates((prev) => {
      let changed = false;
      const next: Record<string, NodeState> = { ...prev };
      allRunEdges.forEach((e) => {
        const k = edgeKey(e.from, e.to);
        // 동적 dept edge 는 prev 에 없을 수 있음 — undefined 도 pending 처럼 active 진입 허용.
        if (e.to === nodeId && (next[k] === undefined || next[k] === "pending")) {
          next[k] = "active";
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [POST_DEBATE_NODES, allRunEdges]);

  const completeEdgesTo = useCallback((nodeId: string) => {
    setEdgeStates((prev) => {
      let changed = false;
      const next: Record<string, NodeState> = { ...prev };
      allRunEdges.forEach((e) => {
        const k = edgeKey(e.from, e.to);
        // 동적 dept edge 는 prev 에 없을 수 있음 — undefined 도 done 으로 전이 (initEdgeStates 가 EDGES 만 seed 하는 한계 보완).
        if (
          e.to === nodeId &&
          (next[k] === undefined || next[k] === "pending" || next[k] === "active")
        ) {
          next[k] = "done";
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [allRunEdges]);

  const reset = useCallback(() => {
    setLogs([]);
    setNodeStates(initNodeStates());
    setEdgeStates(initEdgeStates());
    setNodeScores({});
    setNodeTimings({});
    setNodeConfidence({});
    setDebate(INITIAL_DEBATE_STATE);
    setDebateByNode({});
    setDebateByItem({});
    setSpeakingByNode({});
    setDebateStatusByNode({});
    setDebateRoundByNode({});
    // POST-DEBATE 게이트 ref 초기화
    anyDebateRunningRef.current = false;
    layer3HeldRef.current = false;
    pendingPostDebateRef.current.clear();
    setElapsed(0);
    // Dev3/5 탭 초기화 — logs/traces/rawLogs/lastResult 비우기
    appDispatch({ type: "CLEAR_LOGS" });
    appDispatch({ type: "SET_RESULT", payload: null });
  }, [appDispatch]);

  // ── 데이터 변경 시 노드/점수 자동 초기화 ──
  // transcript / tenant / channel / department 가 바뀌면 직전 평가 결과는 무효 →
  // 이전 점수·노드 상태를 비워서 사용자가 새 데이터로 다시 평가하도록 유도.
  // running 중에는 ignore (활성 평가 중단 방지). 초기 mount 도 ignore (마운트 시점에 reset 호출 안함).
  const dataSnapshotRef = useRef<string | null>(null);
  useEffect(() => {
    const snapshot = JSON.stringify([
      transcript,
      appState.siteId,
      appState.channel,
      appState.department,
      tenantId,
    ]);
    if (dataSnapshotRef.current === null) {
      // 초기 mount — reset 없이 snapshot 만 기록
      dataSnapshotRef.current = snapshot;
      return;
    }
    if (dataSnapshotRef.current !== snapshot) {
      dataSnapshotRef.current = snapshot;
      if (!running) {
        reset();
      }
    }
  }, [transcript, tenantId, appState.siteId, appState.channel, appState.department, running, reset]);

  // ── item 별 DebateState 업데이트 헬퍼 — 병렬 토론 이벤트를 item_number 단위로 라우팅 ──
  // debateByItem 이 primary store. debateByNode 는 "마지막 갱신된 item" 을 반영 (배지/하이라이트 용).
  const updateDebateForItem = useCallback(
    (itemNumber: number | null | undefined, mutator: (prev: DebateState) => DebateState) => {
      if (itemNumber == null) return;
      const nid = itemToNodeId[itemNumber];
      if (!nid) return;
      // (1) item 별 저장 — 진짜 저장소
      setDebateByItem((prev) => {
        const current = prev[itemNumber] ?? INITIAL_DEBATE_STATE;
        const next = mutator(current);
        return { ...prev, [itemNumber]: next };
      });
      // (2) 노드 단위 last-touched 동기화 (배지 등 legacy UI 호환)
      setDebateByNode((prev) => {
        const current = prev[nid] ?? INITIAL_DEBATE_STATE;
        const next = mutator(current);
        return { ...prev, [nid]: next };
      });
    },
    [itemToNodeId],
  );

  // ── Debate 이벤트 핸들러 ────────────────────────────────────
  // 병렬 토론 지원 — 모든 핸들러가 `debate` (전역 하이라이트) + `debateByNode[nid]` 두 곳을 갱신.
  const onDebateRoundStart = useCallback(
    (data: { item_number?: number; round?: number; max_rounds?: number }) => {
      if (data.item_number == null || data.round == null) return;
      addLog(`토론 시작 #${data.item_number} R${data.round}`, "info");
      const updater = (prev: DebateState): DebateState => {
        const sameItem = prev.item_number === data.item_number;
        const prevRounds = sameItem ? prev.rounds : [];
        const exists = prevRounds.some((r) => r.round === data.round);
        const rounds: DebateRoundUI[] = exists
          ? prevRounds
          : [
              ...prevRounds,
              {
                round: data.round as number,
                max_rounds: data.max_rounds ?? prev.maxRounds,
                turns: {},
                verdict: null,
              },
            ];
        return {
          active: true,
          item_number: data.item_number as number,
          item_name: sameItem ? prev.item_name : null,
          rounds,
          currentRound: data.round as number,
          maxRounds: data.max_rounds ?? prev.maxRounds ?? 0,
          final: sameItem ? prev.final : null,
          startedAt: sameItem && prev.startedAt ? prev.startedAt : Date.now(),
        };
      };
      setDebate(updater);
      updateDebateForItem(data.item_number, updater);
    },
    [addLog, updateDebateForItem],
  );

  const onPersonaTurn = useCallback(
    (data: PersonaTurnEvent) => {
      if (!isPersona(data.persona)) return;
      addLog(
        `[${data.persona}] #${data.item_number} R${data.round} · ${data.score}`,
        "info",
      );
      const updater = (prev: DebateState): DebateState => {
        const rounds = prev.rounds.map((r) =>
          r.round === data.round
            ? {
                ...r,
                turns: { ...r.turns, [data.persona]: data },
                // turn_order: 첫 등장 시에만 push (재발언 시 순서 유지)
                turn_order: r.turn_order
                  ? r.turn_order.includes(data.persona)
                    ? r.turn_order
                    : [...r.turn_order, data.persona]
                  : [data.persona],
              }
            : r,
        );
        if (!rounds.some((r) => r.round === data.round)) {
          rounds.push({
            round: data.round,
            max_rounds: prev.maxRounds,
            turns: { [data.persona]: data },
            turn_order: [data.persona],
            verdict: null,
          });
        }
        return {
          ...prev,
          active: true,
          item_number: prev.item_number ?? data.item_number,
          rounds,
          currentRound: Math.max(prev.currentRound, data.round),
        };
      };
      setDebate(updater);
      updateDebateForItem(data.item_number, updater);
    },
    [addLog, updateDebateForItem],
  );

  const onModeratorVerdict = useCallback(
    (data: ModeratorVerdictEvent) => {
      addLog(
        `모더레이터 R${data.round}: ${data.consensus ? "합의" : "미합의"}${
          data.score != null ? ` · ${data.score}` : ""
        }`,
        data.consensus ? "success" : "warn",
      );
      const updater = (prev: DebateState): DebateState => {
        const rounds = prev.rounds.map((r) =>
          r.round === data.round ? { ...r, verdict: data } : r,
        );
        if (!rounds.some((r) => r.round === data.round)) {
          rounds.push({
            round: data.round,
            max_rounds: prev.maxRounds,
            turns: {},
            verdict: data,
          });
        }
        return { ...prev, rounds };
      };
      setDebate(updater);
      updateDebateForItem(data.item_number, updater);
    },
    [addLog, updateDebateForItem],
  );

  const onDebateFinal = useCallback(
    (data: DebateFinalEvent) => {
      const mainLabel = data.converged ? "consensus" : (data.merge_rule || "median_vote");
      const judgePart =
        data.judge_score != null
          ? ` · 🎭 판사 ${data.judge_score}`
          : data.judge_failure_reason
            ? ` · 🎭 실패(${data.judge_failure_reason.slice(0, 40)})`
            : "";
      addLog(
        `토론 종료 #${data.item_number} · 점수 ${data.final_score ?? "—"} · ${mainLabel}${judgePart}`,
        "success",
      );
      const updater = (prev: DebateState): DebateState => ({
        ...prev,
        final: data,
        active: false,
      });
      setDebate(updater);
      updateDebateForItem(data.item_number, updater);
    },
    [addLog, updateDebateForItem],
  );

  const onRouting = useCallback(
    (data: RoutingEvent) => {
      const rawNext = data.next_node;
      const next = rawNext ? LEGACY_TO_V2_NODE[rawNext] || rawNext : undefined;
      addLog(
        `Routing: ${data.phase_label || data.phase || "?"} → ${data.next_label || next || "?"}`,
        "info",
      );
      const phase = data.phase;
      if (phase === "layer1" || phase === "preprocessing") {
        activatePhaseGroup(LAYER1_NODES);
      } else if (phase === "layer2" || phase === "phase_a" || phase === "phase_b1") {
        // 신한 dept 노드 포함하도록 동적 layer2 fan-out — effective.groups.group_layer2.children 사용
        const layer2Active = (effectiveLayer2Children.length > 0
          ? effectiveLayer2Children
          : LAYER2_NODES);
        activatePhaseGroup(layer2Active);
      } else if (phase === "layer3" || phase === "phase_b2") {
        activatePhaseGroup(LAYER3_NODES);
      } else if (phase === "layer4" || phase === "phase_c") {
        // POST-DEBATE 게이트 — 토론 중이면 Layer 4 그룹 활성화 보류
        if (anyDebateRunningRef.current) {
          LAYER4_NODES.forEach((n) => pendingPostDebateRef.current.add(n));
        } else {
          activatePhaseGroup(LAYER4_NODES);
        }
      } else if (phase === "reporting" || next === "report_generator") {
        if (anyDebateRunningRef.current) {
          pendingPostDebateRef.current.add("report_generator");
        } else {
          setNodeStates((prev) => ({ ...prev, report_generator: "active" }));
          activateEdgesTo("report_generator");
        }
      } else if (next && next !== "__end__" && next !== "__parallel__") {
        if (anyDebateRunningRef.current && POST_DEBATE_NODES.has(next)) {
          pendingPostDebateRef.current.add(next);
        } else {
          setNodeStates((prev) => ({ ...prev, [next]: "active" }));
          activateEdgesTo(next);
        }
      }
    },
    [addLog, activatePhaseGroup, activateEdgesTo, POST_DEBATE_NODES, effectiveLayer2Children],
  );

  const onStatus = useCallback(
    (data: StatusEvent) => {
      const rawNode = data.node;
      const node = rawNode ? LEGACY_TO_V2_NODE[rawNode] || rawNode : undefined;
      if (!node) return;
      const status = data.status;
      // TracesPanel 은 `node_trace` SSE 이벤트에서 input/output 을 포함한 풍부한 trace 를 받음 (V2 L2273 동일).
      // status 이벤트는 노드 상태만 관리 — trace 중복 방지를 위해 여기서는 append 하지 않음.
      addLog(
        `${data.label || node}: ${status}${data.elapsed ? ` (${data.elapsed.toFixed(1)}s)` : ""}`,
        status === "completed" || status === "done" ? "success" : "warn",
      );
      if (status === "completed" || status === "done") {
        if (data.scores && data.scores.length > 0) {
          const total = data.scores.reduce((s, it) => s + (it.score || 0), 0);
          setNodeScores((prev) => ({ ...prev, [node]: total }));
          // confidence 평균 — 백엔드 스키마 다형성을 흡수해 0~1 정규화.
          //   Group A: confidence = { final: int 1~5, signals: ... }  → final/5
          //   Group B: confidence(=llm_self_confidence) = { score: int 1~5 } → score/5
          //   기타: 이미 0~1 사이 숫자면 그대로 사용
          const extractConf = (it: unknown): number | null => {
            const raw = (it as { confidence?: unknown })?.confidence;
            if (raw == null) return null;
            if (typeof raw === "number" && Number.isFinite(raw)) {
              if (raw >= 0 && raw <= 1) return raw;
              if (raw >= 1 && raw <= 5) return raw / 5;
              return null;
            }
            if (typeof raw === "object") {
              const o = raw as { final?: unknown; score?: unknown };
              const v = typeof o.final === "number" ? o.final
                : typeof o.score === "number" ? o.score
                : null;
              if (v == null || !Number.isFinite(v)) return null;
              if (v >= 0 && v <= 1) return v;
              if (v >= 1 && v <= 5) return v / 5;
            }
            return null;
          };
          const confs = data.scores
            .map((it) => extractConf(it))
            .filter((c): c is number => typeof c === "number" && c >= 0 && c <= 1);
          if (confs.length > 0) {
            const avg = confs.reduce((a, b) => a + b, 0) / confs.length;
            setNodeConfidence((prev) => ({ ...prev, [node]: avg }));
          }
        }
        // ─── POST-DEBATE 게이트 ───
        // layer3 done 은 debate 가 모두 끝나야 표시. debate 중이면 "active" 유지 + held 표시.
        if (node === "layer3" && anyDebateRunningRef.current) {
          layer3HeldRef.current = true;
          if (data.elapsed !== undefined) {
            setNodeTimings((prev) => ({ ...prev, [node]: data.elapsed as number }));
          }
          // edge 도 보류 — completeEdgesTo 호출하지 않음
        } else if (anyDebateRunningRef.current && POST_DEBATE_NODES.has(node)) {
          // confidence/layer4/... 가 done 으로 왔지만 debate 중 — pending queue 에만 등록
          pendingPostDebateRef.current.add(node);
        } else {
          setNodeStates((prev) => ({
            ...prev,
            [node]: data.node_status === "error" ? "error" : "done",
          }));
          if (data.elapsed !== undefined) {
            setNodeTimings((prev) => ({ ...prev, [node]: data.elapsed as number }));
          }
          completeEdgesTo(node);
        }
      } else if (status === "error") {
        setNodeStates((prev) => ({ ...prev, [node]: "error" }));
      } else if (status === "started" || status === "active") {
        // POST-DEBATE 게이트 — 토론 중에는 post-debate 노드 active 로 전환하지 않음
        if (anyDebateRunningRef.current && POST_DEBATE_NODES.has(node)) {
          pendingPostDebateRef.current.add(node);
        } else {
          setNodeStates((prev) => ({ ...prev, [node]: "active" }));
          activateEdgesTo(node);
        }
      }
    },
    [addLog, activateEdgesTo, completeEdgesTo, POST_DEBATE_NODES],
  );

  // POST-DEBATE 게이트 해제 — 모든 debate 가 done 이 되면 보류된 전이를 재생
  useEffect(() => {
    if (anyDebateRunning) return;
    // layer3 보류 해제
    if (layer3HeldRef.current) {
      layer3HeldRef.current = false;
      setNodeStates((prev) => (prev.layer3 === "active" ? { ...prev, layer3: "done" } : prev));
      setEdgeStates((prev) => {
        let changed = false;
        const next: Record<string, NodeState> = { ...prev };
        allRunEdges.forEach((e) => {
          if (e.to === "layer3") {
            const k = edgeKey(e.from, e.to);
            if (next[k] === "pending" || next[k] === "active") {
              next[k] = "done";
              changed = true;
            }
          }
        });
        return changed ? next : prev;
      });
    }
    // post-debate 노드 replay
    if (pendingPostDebateRef.current.size > 0) {
      const queue = [...pendingPostDebateRef.current];
      pendingPostDebateRef.current.clear();
      setNodeStates((prev) => {
        const next = { ...prev };
        queue.forEach((n) => {
          if (next[n] === "pending") next[n] = "active";
        });
        return next;
      });
      setEdgeStates((prev) => {
        let changed = false;
        const next: Record<string, NodeState> = { ...prev };
        queue.forEach((n) => {
          allRunEdges.forEach((e) => {
            if (e.to === n) {
              const k = edgeKey(e.from, e.to);
              if (next[k] === "pending") {
                next[k] = "active";
                changed = true;
              }
            }
          });
        });
        return changed ? next : prev;
      });
    }
  }, [anyDebateRunning, allRunEdges]);

  const onResult = useCallback(
    (data: ResultEvent) => {
      const gateFail =
        data.status === "validation_failed" ||
        (!data.report && (data.verification || data.score_validation));
      if (gateFail) {
        addLog("Layer 3 Gate 실패 — Layer 4 건너뜀", "error");
        setNodeStates((prev) => ({
          ...prev,
          orchestrator_v2: "error",
          confidence: "skipped",
          tier_router: "skipped",
          evidence_refiner: "skipped",
          report_generator: "gate-failed",
          // 시각 노드는 gt_evidence_comparison 한 개만 노출 — gt_comparison 키도 함께 갱신
          // (LEGACY_TO_V2_NODE alias 로 흡수되긴 하지만 명시적으로 둠).
          gt_evidence_comparison: "skipped",
          qa_output: "skipped",
        }));
      } else {
        addLog("평가 결과 수신", "success");
        setNodeStates((prev) => ({
          ...prev,
          report_generator: "done",
          gt_evidence_comparison: "done",
          qa_output: "done",
        }));
      }

      // 보고서에서 카테고리별 점수 집계 — report.evaluation.categories[].items[] 구조
      const flatItems =
        data.report?.evaluation?.categories?.flatMap((c) => c.items ?? []) ?? [];
      if (flatItems.length > 0) {
        const scores: Record<string, number> = {};
        Object.entries(NODE_ITEMS).forEach(([nid, itemNums]) => {
          let sum = 0;
          let hit = false;
          itemNums.forEach((n) => {
            const found = flatItems.find((it) => it.item_number === n);
            if (found && found.score !== undefined) {
              sum += found.score;
              hit = true;
            }
          });
          if (hit) scores[nid] = sum;
        });
        setNodeScores((prev) => ({ ...prev, ...scores }));
      }

      // Dev5 ResultsPanel 용 — lastResult / lastReport 를 Context 에 저장.
      // 2026-04-27: report 누락이라도 evaluations / debates / gt_comparison 보존 — report_generator
      // 가 ThrottlingException 등으로 실패해도 토론 기록 / 평가 결과 / GT 비교는 화면에 표시.
      if (!gateFail) {
        ctxSetResult(data as unknown as EvaluationResult);
      }
    },
    [addLog, ctxSetResult],
  );

  const onDone = useCallback(
    (data: DoneEvent) => {
      addLog(
        `파이프라인 완료 (${data.elapsed_seconds ? data.elapsed_seconds.toFixed(1) : elapsed.toFixed(1)}s)`,
        "success",
      );
      setNodeStates((prev) => {
        const next = { ...prev };
        const preserve = new Set(["skipped", "gate-failed", "error"]);
        Object.keys(NODE_DEFS).forEach((k) => {
          if (!preserve.has(next[k])) next[k] = "done";
        });
        return next;
      });
      setEdgeStates((prev) => {
        const next = { ...prev };
        allRunEdges.forEach((e) => {
          const k = edgeKey(e.from, e.to);
          if (next[k] !== "skipped") next[k] = "done";
        });
        return next;
      });
      setRunning(false);
      abortRef.current = null;
      // 평가 끝나자마자 같은 탭에서 HITL 검수 모달 자동 오픈.
      // populator 의 DB UPSERT 와 done 이벤트는 별도 경로로 내려오므로, done 수신 직후 fetchReviewQueue
      // 를 때리면 아직 행이 없어 "큐에 미등록" 경고가 뜨는 레이스가 발생한다.
      // 1초 지연 후 오픈하면 populator (terminal node) 가 commit 을 끝낼 시간을 확보.
      window.setTimeout(() => setReviewModalOpen(true), 1000);
    },
    [addLog, elapsed],
  );

  const start = useCallback(() => {
    if (!transcript.trim()) {
      addLog("상담 전사가 비어있음", "warn");
      return;
    }
    reset();
    setRunning(true);
    addLog(`파이프라인 시작 [${backend} · tenant=${tenantId}]`, "info");

    setNodeStates((prev) => ({ ...prev, input_data: "done", tenant_config: "done", layer1: "active" }));
    setEdgeStates((prev) => {
      const next = { ...prev };
      allRunEdges.forEach((e) => {
        if (e.from === "input_data" || e.from === "tenant_config") {
          next[edgeKey(e.from, e.to)] = "active";
        }
      });
      return next;
    });

    // V2 9722~9728 — manualStructured 가 있으면 manual_rows/manual_total 동봉.
    // 없고 수동 텍스트 평가만 있으면 manual_evaluation 텍스트로 대체 (V3 는 텍스트 입력 생략).
    const body: {
      transcript: string;
      llm_backend: "bedrock" | "sagemaker";
      site_id: string;
      channel: string;
      department: string;
      tenant_id: string;  // 레거시 호환 — site_id 와 동일값 (한시적).
      bedrock_model_id?: string;
      persona_mode?: "single" | "ensemble";
      auto_start?: boolean;
      manual_rows?: unknown;
      manual_total?: number | null;
      manual_sheet_id?: string;
      manual_sheet_name?: string;
      manual_evaluation?: string;
      gt_sample_id?: string;  // 백엔드 gt_comparison_node 가 이 필드로 GT 비교 활성화
    } = {
      transcript: transcript.trim(),
      llm_backend: backend,
      // 3단계 멀티테넌트 (2026-04-24). 백엔드 server_v2._build_initial_state 가
      // body 에서 site_id/channel/department 를 순서대로 읽어 QAStateV2 에 주입.
      site_id: appState.siteId || tenantId || "generic",
      channel: appState.channel || "inbound",
      department: appState.department || "default",
      tenant_id: tenantId,
    };
    // ensemble 모드 = 토론 자동 진행. Single 모드가 아니면 무조건 auto_start=true.
    // (body 에 auto_start 생략 시 백엔드 기본값 true — 명시적으로 true 설정하여 게이트 우회)
    if (appState.personaMode === "ensemble") {
      body.auto_start = true;
    }
    // V2 L8953-8955 — bedrock backend + 모델 선택 시에만 bedrock_model_id 동봉.
    if (backend === "bedrock" && bedrockModelId) {
      body.bedrock_model_id = bedrockModelId;
    }
    // V2 L11050 — persona_mode (single | ensemble) 동봉.
    body.persona_mode = appState.personaMode === "single" ? "single" : "ensemble";
    if (appState.manualEval) {
      body.manual_rows = appState.manualEval.rows;
      body.manual_total = appState.manualEval.total;
      body.manual_sheet_id = appState.manualEval.sheetId;
      body.manual_sheet_name = appState.manualEval.sheetName;
      body.manual_evaluation = "";
      addLog(
        `사람 QA 평가표 동봉: ${appState.manualEvalFileName || "xlsx"} · 총점 ${
          appState.manualEval.total ?? "—"
        }`,
        "info",
      );
    }
    // GT 비교 활성화 — manualEval.sheetId (또는 별도 gtSampleId) 를 백엔드에 전달.
    // 백엔드 gt_comparison_node 가 이 sample_id 로 xlsx 시트를 매칭해 항목별 점수 비교 + LLM 근거 비교 실행.
    const gtSid = appState.manualEval?.sheetId || gtSampleId;
    if (gtSid) {
      body.gt_sample_id = String(gtSid);
    }

    abortRef.current = apiSSE(
      "/evaluate/stream",
      body,
      (eventName, rawData) => {
        const data = (rawData && typeof rawData === "object" ? rawData : {}) as Record<string, unknown>;
        // Dev3 RawLogsPanel 용 — 모든 SSE 이벤트 raw 로 저장
        ctxAppendRawLog({
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          time: timestamp(),
          event: eventName,
          data: rawData,
        });
        if (eventName === "routing") {
          onRouting(data as RoutingEvent);
        } else if (eventName === "status") {
          onStatus(data as StatusEvent);
        } else if (eventName === "result") {
          onResult(data as ResultEvent);
        } else if (eventName === "done") {
          onDone(data as DoneEvent);
        } else if (eventName === "error") {
          const err = data as ErrorEvent;
          addLog(`오류: ${err.message || JSON.stringify(data)}`, "error");
        } else if (eventName === "start") {
          if (typeof data.tenant_id === "string") setTenantId(data.tenant_id);
        } else if (eventName === "log") {
          // 백엔드 로그(Bedrock 호출, LLM req/res, 노드 진입/완료) 를 실행 로그로 실시간 반영
          const level = String(data.level || "INFO").toUpperCase();
          const msg = String(data.message || "");
          const lg = String(data.logger || "");
          // 로그 레벨 → UI 심각도 매핑
          const uiType: "info" | "warn" | "error" | "success" =
            level === "ERROR" || level === "CRITICAL"
              ? "error"
              : level === "WARNING" || level === "WARN"
                ? "warn"
                : "info";
          // [logger] message 포맷 — 긴 메시지는 프론트에서 자를 수 있게 그대로 전달
          const displayMsg = lg && !lg.startsWith("v2.serving") ? `[${lg}] ${msg}` : msg;
          setLogs((prev) => [
            ...prev,
            { time: timestamp(), msg: displayMsg, type: uiType },
          ]);
        } else if (eventName === "node_trace") {
          // V2 parity — 백엔드는 node_trace 이벤트로 각 노드의 input/output/elapsed 풍부한 trace 를 방출.
          // TracesPanel 의 점수 뱃지/Input/Output 서브탭이 이 데이터에 의존.
          const nodeRaw = typeof data.node === "string" ? data.node : "";
          const node = nodeRaw ? LEGACY_TO_V2_NODE[nodeRaw] || nodeRaw : nodeRaw;
          ctxAppendTrace({
            id: `nt-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            time: timestamp(),
            node,
            label: typeof data.label === "string" ? data.label : undefined,
            status: "completed",
            elapsed: typeof data.elapsed === "number" ? data.elapsed : undefined,
            detail: data,
          });
        } else if (eventName === "debate_round_start") {
          onDebateRoundStart(data as { item_number?: number; round?: number; max_rounds?: number });
        } else if (eventName === "persona_turn") {
          onPersonaTurn(data as unknown as PersonaTurnEvent);
        } else if (eventName === "moderator_verdict") {
          onModeratorVerdict(data as unknown as ModeratorVerdictEvent);
        } else if (eventName === "debate_final") {
          onDebateFinal(data as unknown as DebateFinalEvent);
        } else if (eventName === "discussion_started") {
          // V3 인터랙티브 — 백엔드가 토론 시작을 통보. discussion_id + node_id 매핑 저장.
          const did = typeof data.discussion_id === "string" ? data.discussion_id : "";
          const nid = typeof data.node_id === "string" ? data.node_id : "";
          const itemNo =
            typeof data.item_number === "number" ? data.item_number : null;
          // SSoT: 백엔드 v2.debate.schemas.DEFAULT_MAX_ROUNDS = 2 와 정합.
          // 백엔드가 항상 max_rounds 를 SSE 로 전송하므로 이 fallback 은 누락 시 보호망.
          const maxRounds =
            typeof data.max_rounds === "number" && data.max_rounds > 0
              ? data.max_rounds
              : 2;
          const auto = data.auto_start !== false;
          if (did && nid) {
            setDiscussionIdMap((prev) => ({ ...prev, [nid]: did }));
            addLog(
              `토론 시작 [${nid}] · 페르소나 ${
                Array.isArray(data.personas) ? data.personas.length : 0
              }명${auto ? " · 자동 진행" : " · 사용자 시작 대기"}`,
              "info",
            );
            // 토스트 — 사용자에게 토론 진행 알림
            toast.info?.(`토론 진행 중 — ${nid}`, {
              description: `페르소나 토론이 시작됐습니다${
                itemNo != null ? ` (#${itemNo})` : ""
              }${auto ? " · 자동 진행" : ""}`,
              duration: 4000,
            });
          }
          // ★ discussion_started 시점에 debate 상태 active 전환 — per-node 독립 갱신.
          if (itemNo != null) {
            const startedUpdater = (prev: DebateState): DebateState => ({
              active: true,
              item_number: itemNo,
              item_name: prev.item_number === itemNo ? prev.item_name : null,
              rounds: prev.item_number === itemNo ? prev.rounds : [],
              currentRound: 1,
              maxRounds,
              final: null,
              startedAt: Date.now(),
            });
            setDebate(startedUpdater);
            updateDebateForItem(itemNo, startedUpdater);
            const nid = itemToNodeId[itemNo];
            if (nid) {
              // 즉시 ref 설정 — setState 후 useEffect 가 ref 반영할 때까지 기다리면
              // 같은 tick 에서 온 후속 status 이벤트가 구 값 (false) 을 읽어 게이트 우회함.
              anyDebateRunningRef.current = true;
              setDebateStatusByNode((prev) => ({ ...prev, [nid]: "running" }));
              setDebateRoundByNode((prev) => ({
                ...prev,
                [nid]: { round: 1, max: maxRounds },
              }));
            }
            // ── POST-DEBATE 게이트 재설정 ──
            // discussion_started 보다 layer3 status=done 이 먼저 도착할 수 있음.
            // 이 경우 layer3 를 다시 "active" 로 demote + post-debate 노드/엣지도 되돌림.
            setNodeStates((prev) => {
              const next = { ...prev };
              let changed = false;
              if (next.layer3 === "done") {
                next.layer3 = "active";
                layer3HeldRef.current = true;
                changed = true;
              }
              POST_DEBATE_NODES.forEach((n) => {
                if (next[n] === "active" || next[n] === "done") {
                  pendingPostDebateRef.current.add(n);
                  next[n] = "pending";
                  changed = true;
                }
              });
              return changed ? next : prev;
            });
            setEdgeStates((prev) => {
              let changed = false;
              const next: Record<string, NodeState> = { ...prev };
              allRunEdges.forEach((e) => {
                const k = edgeKey(e.from, e.to);
                // layer3 로 들어오는 엣지 done 이면 active 로 demote
                if (e.to === "layer3" && next[k] === "done") {
                  next[k] = "active";
                  changed = true;
                }
                // post-debate 엣지 active/done 이면 pending 으로 되돌림
                if (POST_DEBATE_NODES.has(e.to) && (next[k] === "active" || next[k] === "done")) {
                  next[k] = "pending";
                  changed = true;
                }
              });
              return changed ? next : prev;
            });
          }
        } else if (eventName === "persona_speaking") {
          const pid = typeof data.persona_id === "string" ? data.persona_id : "?";
          const itemNo =
            typeof data.item_number === "number" ? data.item_number : null;
          const r = typeof data.round === "number" ? data.round : null;
          addLog(`💬 ${pid} 발언 중…`, "info");
          if (itemNo != null) {
            const speakingUpdater = (prev: DebateState): DebateState => ({
              ...prev,
              active: true,
              item_number: prev.item_number ?? itemNo,
              currentRound:
                r != null ? Math.max(prev.currentRound || 1, r) : prev.currentRound || 1,
            });
            setDebate(speakingUpdater);
            updateDebateForItem(itemNo, speakingUpdater);
            const nid = itemToNodeId[itemNo];
            if (nid) {
              // 현재 발언 중 persona 기록 — DiscussionModal 의 typing indicator 소스
              if (isPersona(pid)) {
                setSpeakingByNode((prev) => ({ ...prev, [nid]: pid }));
              }
              // persona_speaking 은 명시적인 "현재 토론 진행 중" 신호 — 항상 running 으로.
              // 노드에 여러 평가 항목이 있을 때 (e.g., explanation: #10 + #11), 이전 #10 이
              // "done" 으로 남은 상태에서 #11 이 시작될 때 이 핸들러가 done → running 으로 복귀시킴.
              setDebateStatusByNode((prev) =>
                prev[nid] === "running" ? prev : { ...prev, [nid]: "running" },
              );
              if (r != null) {
                setDebateRoundByNode((prev) => {
                  const cur = prev[nid];
                  // 항목 전환으로 round 가 1 부터 다시 시작하면 round 를 R1 로 reset.
                  // 같은 항목 안에서 round 진행이면 max(prev, new) 로 단조증가 유지.
                  const isLowerRound = cur != null && r < cur.round;
                  const nextRound = isLowerRound ? r : Math.max(cur?.round || 1, r);
                  return {
                    ...prev,
                    [nid]: { round: nextRound, max: cur?.max || 2 },
                  };
                });
              }
            }
          }
        } else if (eventName === "persona_message") {
          // 실시간 발언 — AG2 initiate_chat 이 blocking 이라 persona_turn 은 토론 종료 후에만 옴.
          // 라이브로 보려면 persona_message 도 rounds[].turns[persona] 를 채워야 한다.
          const pid = typeof data.persona_id === "string" ? data.persona_id : "";
          const itemNo =
            typeof data.item_number === "number" ? data.item_number : null;
          const r = typeof data.round === "number" ? data.round : 1;
          const msg = typeof data.message === "string" ? data.message : "";
          const scoreRaw =
            typeof data.score_proposal === "number"
              ? data.score_proposal
              : typeof data.score === "number"
                ? data.score
                : null;
          if (pid && itemNo != null && isPersona(pid)) {
            const turn: PersonaTurnEvent = {
              item_number: itemNo,
              round: r,
              persona: pid,
              score: scoreRaw ?? 0,
              argument: msg,
            };
            const messageUpdater = (prev: DebateState): DebateState => {
              const sameItem = prev.item_number === itemNo;
              const prevRounds = sameItem ? prev.rounds : [];
              const exists = prevRounds.some((rd) => rd.round === r);
              const rounds: DebateRoundUI[] = exists
                ? prevRounds.map((rd) =>
                    rd.round === r
                      ? {
                          ...rd,
                          turns: { ...rd.turns, [pid]: turn },
                          turn_order: rd.turn_order
                            ? rd.turn_order.includes(pid)
                              ? rd.turn_order
                              : [...rd.turn_order, pid]
                            : [pid],
                        }
                      : rd,
                  )
                : [
                    ...prevRounds,
                    {
                      round: r,
                      max_rounds: prev.maxRounds,
                      turns: { [pid]: turn },
                      turn_order: [pid],
                      verdict: null,
                    },
                  ];
              return {
                ...prev,
                active: true,
                item_number: itemNo,
                rounds,
                currentRound: Math.max(prev.currentRound || 1, r),
              };
            };
            setDebate(messageUpdater);
            updateDebateForItem(itemNo, messageUpdater);
            // 발언 완료 → typing indicator 해제
            const nidForSpeak = itemToNodeId[itemNo];
            if (nidForSpeak) {
              setSpeakingByNode((prev) => ({ ...prev, [nidForSpeak]: null }));
            }
            addLog(
              `💬 ${pid} 발언: ${msg.slice(0, 60)}${msg.length > 60 ? "…" : ""}`,
              "info",
            );
          }
        } else if (eventName === "vote_cast") {
          // 표결 — persona_message 에 score_proposal 이 이미 포함되어 있으므로 여기선 로그만.
          const pid = typeof data.persona_id === "string" ? data.persona_id : "?";
          const sc = typeof data.score === "number" ? data.score : null;
          addLog(`🗳 ${pid} 표결 · ${sc ?? "—"}`, "info");
        } else if (eventName === "discussion_round_complete") {
          const rRaw = typeof data.round === "number" ? data.round : null;
          const r = rRaw ?? "?";
          const consensus = data.consensus_reached === true;
          const itemNo =
            typeof data.item_number === "number" ? data.item_number : null;
          addLog(
            `라운드 ${r} 종료 · ${consensus ? "✓ 합의" : "△ 미합의"} · median ${
              typeof data.median === "number" ? data.median : "—"
            }`,
            consensus ? "success" : "info",
          );
          // per-node round 갱신 — 다음 라운드가 실제로 시작될 수 있을 때만 round+1 표시.
          // consensus 달성 또는 마지막 라운드 완료면 round 는 유지 (4/3 같은 오버런 방지).
          if (itemNo != null && rRaw != null) {
            const nid = itemToNodeId[itemNo];
            if (nid) {
              setDebateRoundByNode((prev) => {
                const cur = prev[nid];
                const max = cur?.max || 3;
                const nextRound = consensus
                  ? rRaw // 합의 달성 — 현재 라운드로 고정
                  : Math.min(rRaw + 1, max); // 미합의 — 다음 라운드로 but max cap
                return { ...prev, [nid]: { round: nextRound, max } };
              });
            }
          }
        } else if (eventName === "discussion_finalized") {
          const method = typeof data.method === "string" ? data.method : "unknown";
          const fs =
            typeof data.final_score === "number" ? data.final_score : null;
          const itemNo =
            typeof data.item_number === "number" ? data.item_number : null;
          addLog(
            `토론 종료 [${method}] · 최종 점수 ${fs ?? "—"}`,
            "success",
          );
          toast.success?.("토론 완료", {
            description: `방법 ${method} · 최종 점수 ${fs ?? "—"}`,
            duration: 4000,
          });
          // 백엔드가 rounds_used 를 명시적으로 보내주면 그 값 우선 (max 를 넘지 않게 cap).
          const backendRoundsUsed =
            typeof data.rounds_used === "number" ? data.rounds_used : null;
          // 백엔드가 final_reasoning 을 보내주면 그대로 사용 (post-debate 판사 결과 등).
          // 없으면 method 만으로 placeholder 생성.
          const backendReasoning =
            typeof data.final_reasoning === "string" && data.final_reasoning.trim()
              ? data.final_reasoning.trim()
              : null;
          // debate state 마무리 — per-node 독립 final 세트.
          if (itemNo != null) {
            const finalizedUpdater = (prev: DebateState): DebateState => {
              const cap = prev.maxRounds || 2;
              const fromPrev = prev.rounds.length || prev.currentRound || 0;
              const rounds_used = Math.min(
                backendRoundsUsed ?? fromPrev,
                cap,
              );
              // method → merge_rule 매핑 — FinalBlock 이 mergeRule 로 판사/합의/폴백 분기 결정.
              // 백엔드 _method 형식: "judge_post_debate" | "ag2_consensus" | "ag2_median_vote" | "ag2_fallback_median" | ...
              const mergeRule =
                method === "judge_post_debate"
                  ? "judge_post_debate"
                  : method === "ag2_consensus"
                    ? "consensus"
                    : method.startsWith("ag2_")
                      ? method.slice(4)
                      : method;
              const judgeScoreFromEvt =
                typeof data.judge_score === "number" ? data.judge_score : null;
              const judgeReasoningFromEvt =
                typeof data.judge_reasoning === "string"
                  ? data.judge_reasoning
                  : null;
              const judgeFailureReasonFromEvt =
                typeof data.judge_failure_reason === "string"
                  ? data.judge_failure_reason
                  : null;
              return {
                ...prev,
                active: false,
                item_number: itemNo,
                final: {
                  item_number: itemNo,
                  final_score: fs,
                  converged:
                    mergeRule === "judge_post_debate" ||
                    (method !== "force_vote" && method !== "fallback"),
                  rounds_used,
                  rationale: backendReasoning ?? `토론 종료 (${method})`,
                  // FinalBlock 이 분기 결정 + 판사 카드 렌더에 사용.
                  merge_rule: mergeRule,
                  judge_score: judgeScoreFromEvt,
                  judge_reasoning: judgeReasoningFromEvt,
                  judge_failure_reason: judgeFailureReasonFromEvt,
                  judge_deductions: Array.isArray(data.judge_deductions)
                    ? (data.judge_deductions as Array<{ reason: string; points: number }>)
                    : [],
                  judge_evidence: Array.isArray(data.judge_evidence)
                    ? (data.judge_evidence as Array<{ speaker: string; quote: string }>)
                    : [],
                },
              };
            };
            setDebate((prev) =>
              prev.item_number === itemNo ? finalizedUpdater(prev) : prev,
            );
            updateDebateForItem(itemNo, finalizedUpdater);
            const nid = itemToNodeId[itemNo];
            if (nid) {
              setDebateStatusByNode((prev) => {
                const next = { ...prev, [nid]: "done" as const };
                // 즉시 ref 동기화 — 방금 종료한 이 노드 제외하고 하나라도 running 이 남아있으면 true
                anyDebateRunningRef.current = Object.values(next).some((s) => s === "running");
                return next;
              });
              setSpeakingByNode((prev) => ({ ...prev, [nid]: null }));
            }
          }
        }
      },
      {
        onError: (err) => {
          addLog(`연결 오류: ${String(err)}`, "error");
          setRunning(false);
          abortRef.current = null;
        },
        onDone: () => {
          setRunning(false);
          abortRef.current = null;
        },
      },
    );
  }, [
    transcript,
    tenantId,
    appState.siteId,
    appState.channel,
    appState.department,
    backend,
    bedrockModelId,
    reset,
    addLog,
    onRouting,
    onStatus,
    onResult,
    onDone,
    onDebateRoundStart,
    onPersonaTurn,
    onModeratorVerdict,
    onDebateFinal,
    ctxAppendRawLog,
    allRunEdges,
  ]);

  const stop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current();
      abortRef.current = null;
    }
    setRunning(false);
    setNodeStates((prev) => {
      const next = { ...prev };
      Object.keys(next).forEach((k) => {
        if (next[k] === "active") next[k] = "aborted";
      });
      return next;
    });
    addLog("사용자가 중단", "warn");
  }, [addLog]);

  return (
    <div className="flex flex-col gap-5">
      <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-5 transition-[border-color,box-shadow] duration-[var(--dur)]">
        <div className="flex flex-wrap items-center gap-4">
          <div
            className={`flex flex-wrap items-center gap-3 rounded-[var(--radius-sm)] px-2 py-1 ${
              tenantFlashing ? "tenant-row-flash" : ""
            }`}
          >
            <label className="flex items-center gap-2 text-[13px]">
              <span className="font-medium text-[var(--ink-soft)]">Site</span>
              <input
                value={appState.siteId}
                onChange={(e) =>
                  appDispatch({
                    type: "SET_TENANT_3TIER",
                    payload: { siteId: e.target.value.trim() },
                  })
                }
                disabled={running}
                placeholder="kolon"
                className="w-24 rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-2.5 py-1 text-[13px] text-[var(--ink)] outline-none transition focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-ring)]"
              />
            </label>
            <label className="flex items-center gap-2 text-[13px]">
              <span className="font-medium text-[var(--ink-soft)]">Channel</span>
              <select
                value={appState.channel}
                onChange={(e) =>
                  appDispatch({
                    type: "SET_TENANT_3TIER",
                    payload: { channel: e.target.value },
                  })
                }
                disabled={running}
                className="rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-2.5 py-1 text-[13px] text-[var(--ink)] outline-none transition focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-ring)]"
              >
                <option value="inbound">inbound</option>
                <option value="outbound">outbound</option>
              </select>
            </label>
            <label className="flex items-center gap-2 text-[13px]">
              <span className="font-medium text-[var(--ink-soft)]">Dept</span>
              <input
                value={appState.department}
                onChange={(e) =>
                  appDispatch({
                    type: "SET_TENANT_3TIER",
                    payload: { department: e.target.value.trim() },
                  })
                }
                disabled={running}
                placeholder="default"
                className="w-24 rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-2.5 py-1 text-[13px] text-[var(--ink)] outline-none transition focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-ring)]"
              />
            </label>
            <TenantStatusBadge
              siteId={appState.siteId || "generic"}
              channel={appState.channel || "inbound"}
              department={appState.department || "default"}
              flashKey={tenantFlashKey}
            />
          </div>
          <label className="flex items-center gap-2 text-[13px]">
            <span className="font-medium text-[var(--ink-soft)]">Backend</span>
            <select
              value={backend}
              onChange={(e) => {
                const next = e.target.value as "bedrock" | "sagemaker";
                setBackend(next);
                // sagemaker 로 전환 시 bedrockModelId 초기화 (서버 body 에서 제외됨)
                if (next === "sagemaker") setBedrockModelId(null);
              }}
              disabled={running}
              className="rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-2.5 py-1 text-[13px] text-[var(--ink)] outline-none transition focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-ring)]"
            >
              <option value="bedrock">bedrock</option>
              <option value="sagemaker">sagemaker</option>
            </select>
          </label>
          {backend === "bedrock" && (
            <label className="flex items-center gap-2 text-[13px]">
              <span className="font-medium text-[var(--ink-soft)]">Model</span>
              <select
                value={bedrockModelId ?? ""}
                onChange={(e) => setBedrockModelId(e.target.value || null)}
                disabled={running}
                className="rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-2.5 py-1 text-[13px] text-[var(--ink)] outline-none transition focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-ring)]"
                title="V2 L9076-9115 — 선택 안 하면 서버 기본 모델 사용. 선택 시 body.bedrock_model_id 동봉."
                style={{ minWidth: 180 }}
              >
                <option value="">서버 기본값</option>
                {Object.entries(MODEL_GROUPS)
                  .filter(([group]) => group !== "SageMaker")
                  .map(([group, opts]) => (
                    <optgroup key={group} label={group}>
                      {opts.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </optgroup>
                  ))}
              </select>
            </label>
          )}
          {/* V2 L11581-11607 — Persona Mode 토글 (single | ensemble) */}
          <div className="flex items-center gap-2 text-[13px]">
            <span className="font-medium text-[var(--ink-soft)]">Mode</span>
            <div className="inline-flex rounded-[var(--radius-sm)] border border-[var(--border-strong)] overflow-hidden" role="group" aria-label="Persona Mode">
              <button
                type="button"
                onClick={() =>
                  appDispatch({ type: "SET_PERSONA_MODE", payload: "single" })
                }
                disabled={running}
                title="Neutral 1명만 호출 — 빠름 (LLM 호출 1/3 수준)"
                className="px-3 py-1 text-[12px] font-medium transition disabled:opacity-50"
                style={{
                  background:
                    appState.personaMode === "single"
                      ? "var(--accent-bg)"
                      : "var(--surface)",
                  color:
                    appState.personaMode === "single"
                      ? "var(--accent)"
                      : "var(--ink-muted)",
                  fontWeight: appState.personaMode === "single" ? 700 : 500,
                  borderRight: "1px solid var(--border-strong)",
                }}
              >
                ⚡ Single
              </button>
              <button
                type="button"
                onClick={() =>
                  appDispatch({ type: "SET_PERSONA_MODE", payload: "ensemble" })
                }
                disabled={running}
                title="Strict / Neutral / Loose 3명 병렬 + 필요 시 토론 (기본 · 고품질)"
                className="px-3 py-1 text-[12px] font-medium transition disabled:opacity-50"
                style={{
                  background:
                    appState.personaMode === "ensemble" || !appState.personaMode
                      ? "var(--accent-bg)"
                      : "var(--surface)",
                  color:
                    appState.personaMode === "ensemble" || !appState.personaMode
                      ? "var(--accent)"
                      : "var(--ink-muted)",
                  fontWeight:
                    appState.personaMode === "ensemble" || !appState.personaMode
                      ? 700
                      : 500,
                }}
              >
                🗣️ Ensemble
              </button>
            </div>
          </div>
          <label className="flex items-center gap-2 text-[13px]">
            <span className="font-medium text-[var(--ink-soft)]">Server</span>
            <input
              value={appState.serverUrl}
              onChange={(e) => ctxSetServerUrl(e.target.value)}
              disabled={running}
              placeholder="http://localhost:8081"
              className="rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-2.5 py-1 text-[13px] text-[var(--ink)] outline-none transition focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-ring)]"
              style={{ width: 220 }}
              title="평가 서버 URL — 변경 시 lib/api.ts BASE_URL 이 런타임 동기화됩니다"
            />
          </label>
          <div className="ml-auto text-[12px] text-[var(--ink-muted)]">
            elapsed:{" "}
            <span className="font-mono tabular-nums text-[var(--ink-soft)]">{elapsed.toFixed(1)}s</span>
          </div>
        </div>

        {/* 자동저장 토글 + 마지막 저장 경로 (Task #5) */}
        <div className="mt-3 flex flex-wrap items-center gap-3 text-[12px]">
          <label className="flex items-center gap-1.5 text-[var(--ink-soft)] cursor-pointer select-none">
            <input
              type="checkbox"
              checked={appState.autoSaveResult}
              onChange={(e) =>
                appDispatch({ type: "SET_AUTO_SAVE", payload: e.target.checked })
              }
            />
            평가 결과 자동 저장
          </label>
          {appState.autoSaveResult && (
            <span className="text-[11px] text-[var(--ink-muted)]">
              완료 시 xlsx 를 <code className="kbd">/save-xlsx</code> 로 POST (YYYY-MM-DD 하위폴더)
            </span>
          )}
          {appState.lastSavedPath && (
            <span className="status-chip status-chip-healthy">
              <span className="pulse-dot" />
              마지막 저장: <code className="kbd">{appState.lastSavedPath}</code>
            </span>
          )}
          {/* 중복 표시 제거 — ManualQALinkedCard 가 통합 노출 */}
        </div>

        <div className="mt-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-[var(--ink-muted)]">
              상담 전사
            </span>
            <div className="flex items-center gap-2">
              <input
                ref={fileInputRef}
                type="file"
                accept=".txt,.md,.json,.csv,.log,text/*"
                disabled={running}
                onChange={(e) => onFilePicked(e.target.files?.[0] ?? null)}
                className="hidden"
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={running}
                className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-2.5 py-1 text-[12px] font-medium text-[var(--ink-soft)] transition hover:bg-[var(--surface-hover)] hover:border-[var(--accent)] hover:text-[var(--accent)] disabled:opacity-50"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                </svg>
                파일 첨부
              </button>
              {attachedFile && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-[var(--accent-bg)] px-2.5 py-1 text-[11px] font-medium text-[var(--accent)]">
                  {attachedFile.name} · {(attachedFile.size / 1024).toFixed(1)}KB
                  <button
                    type="button"
                    onClick={clearAttachment}
                    className="ml-0.5 text-[var(--accent)] hover:text-[var(--ink)]"
                    aria-label="첨부 제거"
                  >
                    ✕
                  </button>
                </span>
              )}
            </div>
          </div>
          <textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            disabled={running}
            placeholder="상담 전사 (STT 결과) 를 붙여넣거나 위 [파일 첨부] 로 .txt/.md/.json 파일을 업로드하세요…"
            rows={6}
            className="w-full rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] p-3 text-[13px] leading-relaxed text-[var(--ink)] outline-none transition placeholder:text-[var(--ink-subtle)] focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-ring)]"
          />
        </div>

        <div className="mt-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-[var(--ink-muted)]">
              사람 QA 평가표 (선택)
            </span>
            <span className="text-[11px] text-[var(--ink-subtle)]">
              xlsx 첨부 시 AI 결과와 항목별 비교 · AI 컬럼 추가 xlsx 다운로드 가능
            </span>
          </div>
          <ManualEvalAttach transcript={transcript} disabled={running} />
          {/* 연동 상태 — interactive 카드 (클릭 시 항목별 점수 expand) */}
          {/* justLinked: null → 값 transition 직후 4.5초만 true → 카드가 펄스 + ✨ 라벨.
              transition 감지 effect 가 1회만 setJustLinked 호출 → 두번째 effect 가 4.5초 뒤 null 로 해제.
              지속 깜빡임 발생하면 transition 감지 ref (wasManualLinkedRef/wasGtLinkedRef) 가 잘못 동작하는 것이므로 그쪽 디버깅. */}
          <ManualQALinkedCard
            manualEval={appState.manualEval}
            gtScores={appState.gtScores}
            justLinked={justLinked != null}
          />
        </div>

        <div className="mt-4 flex items-center gap-2">
          {!running ? (
            <button
              onClick={start}
              className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] bg-[var(--accent)] px-4 py-2 text-[13px] font-semibold text-white shadow-sm transition hover:bg-[var(--accent-soft)] disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!transcript.trim()}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                <path d="M8 5v14l11-7z"/>
              </svg>
              평가 실행
            </button>
          ) : (
            <button
              onClick={stop}
              className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] bg-[var(--danger)] px-4 py-2 text-[13px] font-semibold text-white shadow-sm transition hover:opacity-90"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12" rx="1"/>
              </svg>
              중단
            </button>
          )}
          <button
            onClick={reset}
            disabled={running}
            className="rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-4 py-2 text-[13px] font-medium text-[var(--ink-soft)] transition hover:bg-[var(--surface-hover)] disabled:opacity-50"
          >
            초기화
          </button>

          {/* ★ QA 검수 CTA — 평가 완료 후 강조 버튼. 모달을 X 로 닫아도 여기서 다시 오픈 가능.
               평가 결과(lastReport) 와 consultation_id 가 모두 있을 때만 노출. */}
          {!running && appState.lastReport && consultationIdForReview && (
            <button
              type="button"
              onClick={() => setReviewModalOpen(true)}
              className="hitl-cta-btn relative inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] bg-[var(--success,#16a34a)] px-4 py-2 text-[13px] font-semibold text-white shadow-md transition hover:opacity-90"
              title="QA 평가 결과를 사람이 검수하는 패널을 엽니다 (만점 항목 숨기기, STT 원문, 항목별 점수/근거 확인 가능)"
            >
              <span
                className="absolute -top-1 -right-1 flex h-2.5 w-2.5"
                aria-hidden="true"
              >
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[var(--warn,#f59e0b)] opacity-75" />
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-[var(--warn,#f59e0b)]" />
              </span>
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.4"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M9 11l3 3L22 4" />
                <path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11" />
              </svg>
              QA 검수 열기
            </button>
          )}

          {transcript.trim() && (
            <span className="ml-auto text-[11px] text-[var(--ink-subtle)]">
              {transcript.length.toLocaleString()}자 · {transcript.split("\n").length}줄
            </span>
          )}
        </div>
      </div>

      <div
        className="tenant-canvas-wrap relative"
        // 캔버스 전체창 ring/glow 펄스 일시 비활성 (요청). 다시 켜려면 아래 className 으로 복귀:
        // className={`tenant-canvas-wrap relative ${tenantFlashing ? "tenant-canvas-flash" : ""}`}
      >
        <div
          key={`canvas-overlay-${tenantFlashKey}`}
          className="tenant-canvas-overlay"
          aria-live="polite"
        >
          <span className="tenant-canvas-overlay-label">테넌트</span>
          <span className="tenant-canvas-overlay-path">
            {appState.siteId || "generic"}
            <span className="tenant-canvas-sep">·</span>
            {appState.channel || "inbound"}
            <span className="tenant-canvas-sep">·</span>
            {appState.department || "default"}
          </span>
          <TenantStatusBadge
            siteId={appState.siteId || "generic"}
            channel={appState.channel || "inbound"}
            department={appState.department || "default"}
            flashKey={tenantFlashKey}
          />
        </div>
        <PipelineFlow
          nodeStates={nodeStates}
          nodeScores={nodeScores}
          nodeTimings={nodeTimings}
          nodeConfidence={nodeConfidence}
          edgeStates={edgeStates}
          onNodeClick={handleNodeClick}
          personaMode={appState.personaMode}
          debateStatusByNode={debateStatusByNode}
          debateRoundByNode={debateRoundByNode}
          onDebateOpen={handleDebateOpen}
          tenantContext={pipelineTenantContext}
          tenantPipelineConfig={tenantPipelineConfig}
        />
      </div>

      {appState.personaMode === "ensemble" && (
        <div className="flex items-center gap-3 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface-muted)] px-4 py-2.5 text-[12px]">
          <span className="font-semibold text-[var(--ink-muted)]">토론 자동 오픈</span>
          <label className="inline-flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={autoOpenDiscussion}
              onChange={(e) => setAutoOpenDiscussion(e.target.checked)}
              className="cursor-pointer"
            />
            <span className="text-[var(--ink-subtle)]">
              토론이 시작되면 자동으로 패널을 띄움 (끄면 노드의 💬 버튼 클릭으로 직접 오픈)
            </span>
          </label>
        </div>
      )}

      <DiscussionModal
        open={discussionNodeId !== null}
        nodeId={discussionNodeId}
        state={
          discussionNodeId
            ? (debateByNode[discussionNodeId] ?? INITIAL_DEBATE_STATE)
            : INITIAL_DEBATE_STATE
        }
        stateByItem={debateByItem}
        activeDebates={Object.entries(debateByNode).map(([nid, s]) => ({
          nodeId: nid,
          label: NODE_DEFS[nid]?.label ?? nid,
          phase: s.final ? "done" : s.active ? "running" : "before",
          round: s.currentRound,
          maxRounds: s.maxRounds,
        }))}
        onSelectNode={(nid) => setDiscussionNodeId(nid)}
        speakingPersona={
          discussionNodeId ? (speakingByNode[discussionNodeId] ?? null) : null
        }
        onClose={() => setDiscussionNodeId(null)}
        onStart={async (nid, _mode) => {
          const did = discussionIdMap[nid];
          if (did) {
            // 백엔드가 이미 토론 게이트 열림 대기 중 — 게이트 해제
            try {
              await startDiscussion(did);
              addLog(`토론 시작 요청 [${nid}]`, "success");
            } catch (e) {
              addLog(`토론 시작 실패: ${e instanceof Error ? e.message : String(e)}`, "error");
              toast.error("토론 시작 실패", {
                description: e instanceof Error ? e.message : String(e),
              });
            }
            return;
          }
          // discussion_id 가 없으면 — 평가가 아직 실행 안됨 → 평가 자동 시작
          if (!running) {
            if (!transcript.trim()) {
              toast.error("상담 전사가 비어있음", {
                description: "토론을 시작하려면 먼저 상담 전사를 입력하고 평가를 실행하세요.",
              });
              addLog("토론 시작 불가 — 상담 전사 비어있음", "warn");
              return;
            }
            addLog(`평가를 자동 시작합니다 (토론 모드 자동 진행) — 노드 ${nid}`, "info");
            toast.success("평가 자동 시작", {
              description:
                "토론을 시작하려면 평가가 실행되어야 합니다. 자동으로 평가를 시작합니다.",
              duration: 3500,
            });
            // 평가 실행 → 백엔드가 debate 노드 도달 시 discussion_started 이벤트 발행
            // 사용자는 모달을 그대로 두면 discussion_started 도착 시 자동 진행됨
            start();
          } else {
            addLog(
              `평가 실행 중 — 백엔드가 토론 노드 도달까지 대기 (debate 단계까지 진행되어야 토론 시작 가능)`,
              "warn",
            );
            toast.info?.("평가 진행 중", {
              description: "debate 단계 도달 시 자동으로 토론이 시작됩니다.",
              duration: 3500,
            });
          }
        }}
        onNextRound={async (nid) => {
          const did = discussionIdMap[nid];
          if (!did) return;
          try {
            await nextDiscussionRound(did);
            addLog(`다음 라운드 진행 요청 [${nid}]`, "info");
          } catch (e) {
            addLog(`다음 라운드 실패: ${e instanceof Error ? e.message : String(e)}`, "error");
          }
        }}
        onAbort={(_nid) => {
          // 백엔드 abort 엔드포인트는 현재 미구현 — SSE stream 을 끊는 방식으로 처리.
          if (abortRef.current) {
            abortRef.current();
            abortRef.current = null;
          }
          setRunning(false);
          setDiscussionNodeId(null);
          addLog("토론 중단됨", "warn");
        }}
      />

      <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-4 transition-[border-color,box-shadow] duration-[var(--dur)]">
        <div className="mb-2.5 flex items-center justify-between">
          <span className="text-[12px] font-semibold uppercase tracking-wide text-[var(--ink-muted)]">
            실행 로그
          </span>
          {logs.length > 0 && (
            <span className="text-[11px] text-[var(--ink-subtle)]">{logs.length}건</span>
          )}
        </div>
        <div className="max-h-64 overflow-y-auto rounded-[var(--radius-sm)] bg-[var(--surface-muted)] p-3 font-mono text-[11.5px] leading-relaxed">
          {logs.length === 0 ? (
            <div className="text-[var(--ink-subtle)]">아직 로그 없음</div>
          ) : (
            logs.map((l, i) => (
              <div key={i} className={logColor(l.type)}>
                <span className="text-[var(--ink-subtle)]">[{l.time}]</span> {l.msg}
              </div>
            ))
          )}
        </div>
      </div>

      {/* 평가 완료 → 같은 탭 안에서 인라인으로 튀어나오는 HITL 검수 모달.
          consultation_id 가 없으면 열지 않음 (populator 가 저장할 키가 없어 ReviewItemCard 가 upsert 불가). */}
      {reviewModalOpen && consultationIdForReview && (
        <PostRunReviewModal
          open={reviewModalOpen}
          onClose={() => setReviewModalOpen(false)}
          consultationId={consultationIdForReview}
          report={appState.lastReport}
          evaluationsFallback={
            (appState.lastResult as { evaluations?: Array<{ agent_id?: string; evaluation?: unknown; status?: string }> } | null)
              ?.evaluations as Array<{ agent_id?: string; evaluation?: import("@/lib/types").CategoryItem; status?: string }> | undefined
          }
          transcript={transcript}
          turns={extractTurnsFromPreprocessing(
            (appState.lastResult as { preprocessing?: unknown } | null)?.preprocessing,
          )}
        />
      )}
    </div>
  );
}

function logColor(t: LogEntry["type"]): string {
  if (t === "success") return "text-[var(--success)]";
  if (t === "warn") return "text-[var(--warn)]";
  if (t === "error") return "text-[var(--danger)]";
  return "text-[var(--ink-soft)]";
}

export default EvaluateRunner;
