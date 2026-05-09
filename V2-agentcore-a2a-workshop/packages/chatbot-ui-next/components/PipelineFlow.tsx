// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import {
  Background,
  ConnectionLineType,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import ELK, { type ElkNode } from "elkjs/lib/elk.bundled.js";
import { memo, useEffect, useMemo, useRef, useState } from "react";

import { BusEdge } from "@/components/edges/BusEdge";
import { FlowEdge } from "@/components/edges/FlowEdge";
import { EvaluationNode } from "@/components/nodes/EvaluationNode";
import { GroupNode } from "@/components/nodes/GroupNode";
import { LayerNode } from "@/components/nodes/LayerNode";
import {
  isDebateCapableNode,
  getEffectivePipeline,
  type EdgeDef,
  type GroupDef,
  type NodeDef,
  type NodeState,
  type TenantPipelineConfig,
  edgeKey,
} from "@/lib/pipeline";

/* ════════════════════════════════════════════════════════════════
   PipelineFlow (V3 modern)

   - ELK layered LR 자동 레이아웃 (V2 dagre 와 동등 매핑)
   - BusEdge: fan-out/fan-in 트렁크 라우팅 (V2 reference 그대로)
   - 상태 통합: nodeStates / edgeStates / debateStatus / debateRound
   - 인터랙션: onNodeClick (노드 드로어) + onDebateOpen (토론 모달)
   ════════════════════════════════════════════════════════════════ */

// ReactFlow inline objects — 모듈 스코프로 hoist 하여 매 렌더마다 새 ref 생성 방지.
// 매 SSE 이벤트로 PipelineFlowImpl 가 re-render 되어도 ReactFlow 가 props ref 변경으로 인식하지 않음.
const FIT_VIEW_OPTIONS = { padding: 0.12, includeHiddenNodes: false, minZoom: 0.7, maxZoom: 1.2 };
const PRO_OPTIONS = { hideAttribution: true };
const DEFAULT_EDGE_OPTIONS = { zIndex: 0 };

// ReactFlow #002 warning 방지 — 모듈 스코프 상수.
// 컴포넌트 내에서 useMemo 로 추가 안정화 (HMR 대응).
const NODE_TYPES_DEF = {
  evaluation: EvaluationNode,
  layer: LayerNode,
  group: GroupNode,
};

const EDGE_TYPES_DEF = {
  bus: BusEdge,
  flow: FlowEdge,
};

const DEFAULT_NODE_W = 200;
const DEFAULT_NODE_H = 80;

function pickNodeComponent(def: NodeDef): "evaluation" | "layer" {
  return def.type === "eval" ? "evaluation" : "layer";
}

const elk = new ELK();

// 동적으로 effective Layer 2 set 에서 fan-out targets 산출 (PipelineFlowImpl 내부 useMemo).
// 정적 fallback (테넌트 무관 base 8) — 사용 안 하지만 호환성 위해 유지.
const FAN_OUT_TARGETS_BASE = new Set([
  "greeting",
  "listening_comm",
  "language",
  "needs",
  "explanation",
  "proactiveness",
  "work_accuracy",
  "privacy",
]);

const SHORT_CIRCUIT_EDGES = new Set(["layer1->layer4"]);

type LayoutResult = {
  nodes: Array<{ id: string; x: number; y: number; w: number; h: number }>;
  groups: Array<{ id: string; x: number; y: number; w: number; h: number }>;
};

const ELK_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.layered.spacing.nodeNodeBetweenLayers": "120",
  "elk.spacing.nodeNode": "28",
  "elk.spacing.edgeNode": "20",
  "elk.spacing.edgeEdge": "10",
  "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  "elk.layered.cycleBreaking.strategy": "GREEDY",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.padding": "[top=48,left=28,bottom=28,right=28]",
};

const GROUP_PAD = { x: 20, top: 30, bottom: 20 };

async function computeFlatLayout(
  effectiveDefs: Record<string, NodeDef>,
  effectiveGroups: Record<string, GroupDef>,
  effectiveEdges: EdgeDef[],
  layer2Children: string[],
): Promise<LayoutResult> {
  const elkNodes: ElkNode[] = Object.values(effectiveDefs).map((def) => ({
    id: def.id,
    width: def.w || DEFAULT_NODE_W,
    height: def.h || DEFAULT_NODE_H,
  }));

  const elkEdges = effectiveEdges
    .filter((e) => effectiveDefs[e.from] && effectiveDefs[e.to])
    .map((e) => ({
      id: edgeKey(e.from, e.to),
      sources: [e.from],
      targets: [e.to],
    }));

  const root: ElkNode = {
    id: "root",
    layoutOptions: ELK_OPTIONS,
    children: elkNodes,
    edges: elkEdges,
  };

  const layout = await elk.layout(root);

  const nodes: LayoutResult["nodes"] = (layout.children || []).map((c) => ({
    id: c.id,
    x: c.x || 0,
    y: c.y || 0,
    w: c.width || DEFAULT_NODE_W,
    h: c.height || DEFAULT_NODE_H,
  }));

  // ★ 2026-05-08: 사용자 보고 — input_stt / tenant_config / layer1 (preprocessing) /
  // kms 등 단일-노드 swimlane 이 evaluation (Layer 2 sub-agents) 노드들과 vertical
  // center 가 어긋나 직각 정렬이 깨짐.
  //
  // 원인: ELK layered LR + NETWORK_SIMPLEX 는 단일 노드 lane 의 y 를 multi-fanout
  //       lane 중심에 자동 정렬하지 않음 (edge length 최소화에만 집중).
  //
  // 해결: Layer 2 sub-agents 들의 vertical bbox 중심 (evalCenterY) 을 reference 로 잡고,
  //       동일 swimlane 의 single-row 노드들 (layer1 / kms / layer2_barrier / layer3 /
  //       confidence / tier_router / evidence_refiner / layer4 / gt_evidence_comparison /
  //       layer5 / report_generator) 의 y 를 모두 evalCenterY 에 정렬.
  //       input_data + tenant_config 는 layer1 (이제 evalCenterY 에 정렬됨) 기준으로
  //       위/아래 stacked 정렬 → 자동으로 evaluation center 와 동일 라인.
  // 단일 통합 그룹으로 묶여도 fan-out swimlane vertical alignment 는 sub-agent (eval) 노드 기준.
  // KMS 는 같은 layer 영역에 있지만 sub-agent 가 아니므로 layer2Children 에서 제외 (이미 그렇게 산출됨).
  const layer2ChildIds = layer2Children;
  const layer2ChildNodes = nodes.filter((n) => layer2ChildIds.includes(n.id));
  if (layer2ChildNodes.length > 0) {
    const evalMinY = Math.min(...layer2ChildNodes.map((n) => n.y));
    const evalMaxY = Math.max(...layer2ChildNodes.map((n) => n.y + n.h));
    const evalCenterY = (evalMinY + evalMaxY) / 2;
    const SINGLE_LANE_IDS = [
      "layer1",
      "kms",
      "layer2_barrier",
      "layer3",
      "confidence",
      "tier_router",
      "evidence_refiner",
      "layer4",
      "gt_comparison",
      "gt_evidence_comparison",
      "layer5",
      "report_generator",
    ];
    for (const id of SINGLE_LANE_IDS) {
      const n = nodes.find((nn) => nn.id === id);
      if (!n) continue;
      n.y = evalCenterY - n.h / 2;
    }
  }

  // input_data + tenant_config 를 layer1 중심 y 에 맞춰 vertical center 정렬.
  // 위 single-lane 정렬로 layer1.y 가 evalCenterY 기준으로 이동했으므로, 이 두 노드도
  // 자동으로 evaluation 노드들과 같은 vertical 라인에 정렬됨.
  const layer1 = nodes.find((n) => n.id === "layer1");
  const inputNode = nodes.find((n) => n.id === "input_data");
  const tenantNode = nodes.find((n) => n.id === "tenant_config");
  if (layer1 && inputNode && tenantNode) {
    const centerY = layer1.y + layer1.h / 2;
    const gap = 16;
    const totalH = inputNode.h + gap + tenantNode.h;
    const startY = centerY - totalH / 2;
    inputNode.y = startY;
    tenantNode.y = startY + inputNode.h + gap;
  }

  // KSQI 분기 — Layer 3/4 체인과 시각적으로 완전 분리 (별도 swimlane).
  // ELK 가 9 노드 vertical stack 으로 배치하면서 Layer 4 영역으로 침범하는 문제 해결.
  // 후처리로 KSQI 그룹 전체를 Layer 3/4 체인의 위쪽으로 시프트 (separation gap = 80px).
  const KSQI_IDS = [
    "ksqi_orchestrator",
    "ksqi_greeting_open",
    "ksqi_terse_response",
    "ksqi_refusal_followup",
    "ksqi_easy_explain",
    "ksqi_inquiry_grasp",
    "ksqi_greeting_close",
    "ksqi_acknowledgment",
    "ksqi_basic_empathy",
    "ksqi_advanced_empathy",
    "ksqi_barrier",
    "ksqi_report",
  ];
  const MAIN_CHAIN_IDS = [
    "layer3",
    "confidence",
    "tier_router",
    "evidence_refiner",
    "layer4",
    "gt_evidence_comparison",
  ];
  const ksqiNodes = nodes.filter((n) => KSQI_IDS.includes(n.id));
  const mainChainNodes = nodes.filter((n) => MAIN_CHAIN_IDS.includes(n.id));
  if (ksqiNodes.length > 0 && mainChainNodes.length > 0) {
    const ksqiMinY = Math.min(...ksqiNodes.map((n) => n.y));
    const ksqiMaxY = Math.max(...ksqiNodes.map((n) => n.y + n.h));
    const mainMinY = Math.min(...mainChainNodes.map((n) => n.y));
    // KSQI 의 바닥 (ksqiMaxY) 이 Layer 3/4 체인 상단 (mainMinY) 위쪽에 충분한 gap 으로 위치하도록 시프트.
    const SEPARATION_GAP = 80;
    const targetMaxY = mainMinY - SEPARATION_GAP;
    const dy = targetMaxY - ksqiMaxY;
    if (dy < 0) {
      // KSQI 가 main chain 과 겹치거나 너무 가까움 → 위로 이동
      for (const n of ksqiNodes) n.y += dy;
    }
    // KSQI 그룹의 X 시작점을 layer3 와 동일하게 정렬 (분기점이 명확하도록)
    const layer3 = nodes.find((n) => n.id === "layer3");
    const ksqiOrch = nodes.find((n) => n.id === "ksqi_orchestrator");
    if (layer3 && ksqiOrch) {
      const dx = layer3.x - ksqiOrch.x;
      if (Math.abs(dx) > 4) {
        for (const n of ksqiNodes) n.x += dx;
      }
    }
    // 위쪽 시프트 후 KSQI 그룹 상단이 viewport 위로 너무 멀어지면 (ksqiMinY < 0)
    // 전체 그래프를 아래로 시프트할지 — 일단 그대로 두고 ReactFlow viewport 자동 fit.
    void ksqiMinY;
  }

  const posMap = new Map(nodes.map((n) => [n.id, n]));
  const groups: LayoutResult["groups"] = Object.values(effectiveGroups)
    .map((g) => {
      const childPositions = g.children
        .map((cid) => posMap.get(cid))
        .filter((p): p is NonNullable<typeof p> => !!p);
      if (childPositions.length === 0) return null;
      const xs = childPositions.map((c) => c.x);
      const ys = childPositions.map((c) => c.y);
      const xe = childPositions.map((c) => c.x + c.w);
      const ye = childPositions.map((c) => c.y + c.h);
      const x = Math.min(...xs) - GROUP_PAD.x;
      const y = Math.min(...ys) - GROUP_PAD.top;
      const w = Math.max(...xe) + GROUP_PAD.x - x;
      const h = Math.max(...ye) + GROUP_PAD.bottom - y;
      return { id: g.id, x, y, w, h };
    })
    .filter((g): g is NonNullable<typeof g> => !!g);

  return { nodes, groups };
}

export interface PipelineFlowProps {
  nodeStates: Record<string, NodeState>;
  nodeScores: Record<string, number>;
  nodeTimings: Record<string, number>;
  edgeStates: Record<string, NodeState>;
  onNodeClick?: (nodeId: string) => void;
  personaMode?: "single" | "ensemble";
  debateStatusByNode?: Record<string, "idle" | "running" | "done">;
  debateRoundByNode?: Record<string, { round: number; max: number }>;
  /** 항목별 토론 완료 플래시 — 멀티 항목 노드에서 각 item_number 가 finalized 될 때
   *  부모가 4초간 채워줌. 노드 우상단 satellite 배지로 "✓ #N · 점수 토론완료" 표시. */
  debateFinishFlashByNode?: Record<
    string,
    { item_number: number; score: number | null; at: number }
  >;
  onDebateOpen?: (nodeId: string) => void;
  /** node-level LLM 평균 confidence (1~5) — V2 qa_pipeline_reactflow.html 의
   *  aggregateConfidenceByAgent 결과. Sub-agent 노드 좌하단 chip 으로 표시. */
  nodeConfidence?: Record<string, number>;
  /** tenant_config 노드 표시용 — 현재 site/channel/department + 변경 펄스 트리거.
   *  flashKey 가 변경되면 tenant_config 노드 테두리에 1회성 러닝 라이트 효과. */
  tenantContext?: {
    siteId: string;
    channel: string;
    department: string;
    flashKey: number;
  };
  /** 테넌트별 effective pipeline config — 미지정 시 default (KSQI 활성, 모두 표시). */
  tenantPipelineConfig?: TenantPipelineConfig;
  /** 노드별 *동적* sub 텍스트 override.
   *  KMS 노드의 검출 인텐트 (예: "교환, 반품") 처럼 결과 들어오면 sub 라인 동적 변경.
   *  미지정/빈 문자열이면 NODE_DEFS 의 정적 sub 사용. */
  nodeSubOverrides?: Record<string, string>;
}

function PipelineFlowImpl({
  nodeStates,
  nodeScores,
  nodeTimings,
  edgeStates,
  onNodeClick,
  personaMode = "single",
  debateStatusByNode,
  debateRoundByNode,
  debateFinishFlashByNode,
  onDebateOpen,
  nodeConfidence,
  tenantContext,
  tenantPipelineConfig,
  nodeSubOverrides,
}: PipelineFlowProps) {
  const [layout, setLayout] = useState<LayoutResult | null>(null);

  // tenant config 변경 시 effective NODE_DEFS / GROUP_DEFS / EDGES 재산출.
  // useMemo 안정화 — JSON 직렬화로 cfg 객체 referential 변경에도 정합성 유지.
  const cfgKey = useMemo(
    () => JSON.stringify(tenantPipelineConfig || {}),
    [tenantPipelineConfig],
  );
  const effective = useMemo(
    () => getEffectivePipeline(tenantPipelineConfig || {}),
    // cfgKey 가 deps — JSON.stringify 결과만 바뀌면 재계산
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cfgKey],
  );

  useEffect(() => {
    let alive = true;
    // setLayout(null) 호출하지 않음 — 이전 layout 유지하면서 새 layout 비동기 계산 후 교체.
    // 평가 진행 중 또는 SSE 이벤트 폭주 중에 layout=null 로 잠시 비워지는 깜빡임 방지.
    computeFlatLayout(effective.defs, effective.groups, effective.edges, effective.layer2Children)
      .then((res) => {
        if (alive) setLayout(res);
      })
      .catch((e) => {
        // eslint-disable-next-line no-console
        console.error("ELK layout failed:", e);
      });
    return () => {
      alive = false;
    };
  }, [effective]);

  // 신규 노드 추적 — tenant 전환 시 새로 추가된 노드에 sparkle.
  // prevIds 와 비교해 신규 ID 검출 → newlyAddedIds set, CSS 애니메이션 (2.4s) 완전 종료
  // 후 unmount (2500ms) 하여 fade-out 이 끊기지 않도록 보장.
  const prevNodeIdsRef = useRef<Set<string>>(new Set());
  const [newlyAddedIds, setNewlyAddedIds] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!layout) return;
    const currentIds = new Set(layout.nodes.map((n) => n.id));
    const prev = prevNodeIdsRef.current;
    // 첫 마운트 (prev 비어있으면) sparkle 생략 — 초기 진입은 모두 "신규"가 아님
    if (prev.size === 0) {
      prevNodeIdsRef.current = currentIds;
      return;
    }
    const added = new Set<string>();
    currentIds.forEach((id) => {
      if (!prev.has(id)) added.add(id);
    });
    prevNodeIdsRef.current = currentIds;
    if (added.size === 0) return;
    setNewlyAddedIds(added);
    // CSS animation duration (2.4s) + 100ms 버퍼 — fade-out 끝까지 보장
    const t = setTimeout(() => setNewlyAddedIds(new Set()), 2500);
    return () => clearTimeout(t);
  }, [layout]);

  const defs = useMemo(() => Object.values(effective.defs), [effective]);
  // ReactFlow #002 방지 — HMR 에도 참조 안정화
  const nodeTypes = useMemo(() => NODE_TYPES_DEF, []);
  const edgeTypes = useMemo(() => EDGE_TYPES_DEF, []);

  // ReactFlow onNodeClick — 매 렌더 새 함수 ref 생성 방지 위해 useMemo.
  const handleNodeClickInternal = useMemo(
    () =>
      onNodeClick
        ? (_e: unknown, node: Node) => {
            if (node.type === "group") return;
            onNodeClick(node.id);
          }
        : undefined,
    [onNodeClick],
  );

  // 초기 뷰포트 — 시작 노드(input_data)의 좌측이 화면 왼쪽에 붙도록 세팅.
  // fitView 는 그래프 전체 중앙을 맞추므로 확대 시 중간만 보이고 시작부가 잘림.
  // React Flow 좌표계: screenX(world.x) = world.x * zoom + viewport.x.
  //   따라서 viewport.x = leftPad - inputNode.x * zoom 이면 inputNode 왼쪽이 leftPad 위치에 옴.
  //
  // ★ 2026-05-08: 사용자 보고 — 모델 선택 (Haiku 등) / 토글 변경 후 viewport 가 reset 됨.
  // 원인: ReactFlow `defaultViewport` prop 이 새 ref 가 되면 (layout 재계산 etc.) 내부적으로
  // viewport 를 그 새 값으로 다시 적용하는 동작. 사용자가 수동으로 옮긴 viewport 가 무효화.
  // 해결: 첫 layout 도착 시 1회만 ref 로 캡처 → 이후 어떤 prop 변경에도 같은 ref 유지.
  //       ReactFlow defaultViewport 가 referentially stable → viewport reset 발생 안 함.
  const initialViewportRef = useRef<{ x: number; y: number; zoom: number } | null>(null);
  if (layout && initialViewportRef.current === null) {
    const inputNode = layout.nodes.find((n) => n.id === "input_data");
    if (inputNode) {
      const zoom = 0.85; // 시각적 품질(텍스트 선명)과 정보 밀도 사이 타협점
      const padLeft = 32;
      const containerH = 760;
      const x = padLeft - inputNode.x * zoom;
      const y = containerH / 2 - (inputNode.y + inputNode.h / 2) * zoom;
      initialViewportRef.current = { x, y, zoom };
    } else {
      initialViewportRef.current = { x: 0, y: 0, zoom: 0.85 };
    }
  }
  const initialViewport = initialViewportRef.current ?? { x: 0, y: 0, zoom: 0.85 };

  const nodes: Node[] = useMemo(() => {
    if (!layout) return [];
    const posMap = new Map(layout.nodes.map((n) => [n.id, n]));

    const groupNodes: Node[] = layout.groups
      .map((g) => {
        const groupDef = effective.groups[g.id];
        if (!groupDef) return null;
        // 그룹 자식 모두가 disabled (회색 처리) 면 그룹 박스도 disabled.
        const allChildrenDisabled =
          groupDef.children.length > 0 &&
          groupDef.children.every((c) => effective.defs[c]?.disabled);
        return {
          id: g.id,
          type: "group",
          position: { x: g.x, y: g.y },
          data: {
            group: groupDef,
            width: g.w,
            height: g.h,
            disabled: allChildrenDisabled,
          },
          draggable: false,
          selectable: false,
          // ★ 2026-05-08: group 을 edges layer 보다 뒤로 — fan-out 박스 traveler 가
          // group 의 반투명 배경 위에서 또렷하게 보이도록.
          zIndex: -1,
          style: {
            width: g.w,
            height: g.h,
            background: "transparent",
            border: "none",
          },
        } as Node;
      })
      .filter((n): n is Node => n !== null);

    const childNodes: Node[] = defs
      .map((def) => {
        const pos = posMap.get(def.id);
        if (!pos) return null;
        const state = nodeStates[def.id] || "pending";
        const nodeType = pickNodeComponent(def);
        // 토론 버튼 노출 조건:
        //  - evaluation 타입 노드
        //  - debate 가능 노드 (#1/#2/#16/#17/#18 같은 rule-based 도 사용자 정책상 토론 가능)
        //  - personaMode 무관 — 백엔드가 토론 활성 시 자동 ensemble 로 전환하므로
        //    UI 도 토론 가능 노드 모두에 버튼 노출 (idle/running/done 상태로 동작 표시).
        const debateEnabled =
          nodeType === "evaluation" && isDebateCapableNode(def.id);
        const debateStatus = debateStatusByNode?.[def.id] ?? "idle";
        const debateRoundInfo = debateRoundByNode?.[def.id];
        const debateFinishFlash = debateFinishFlashByNode?.[def.id];

        return {
          id: def.id,
          type: nodeType,
          position: { x: pos.x, y: pos.y },
          data: {
            def,
            state,
            score: nodeScores[def.id],
            elapsed: nodeTimings[def.id],
            confidence: nodeConfidence?.[def.id],
            debateEnabled,
            debateStatus,
            debateRound: debateRoundInfo?.round,
            debateMaxRounds: debateRoundInfo?.max,
            debateFinishFlash,
            onDebateOpen,
            // ★ 2026-05-07: 명시적 "📋 상세" 액션 버튼 — onNodeClick 과 동일 효과지만 affordance.
            onOpenDetail: onNodeClick,
            // tenant_config 노드에만 현재 테넌트 정보 + 변경 트리거 전달.
            tenantContext: def.id === "tenant_config" ? tenantContext : undefined,
            // 테넌트 전환으로 새로 추가된 노드 — 1.6초 sparkle 애니메이션
            isNewlyAdded: newlyAddedIds.has(def.id),
            // 동적 sub override — KMS 의 검출 인텐트 등. 빈 값이면 def.sub fallback.
            dynamicSub: nodeSubOverrides?.[def.id] || undefined,
          },
          draggable: false,
          // ⚠ React Flow 최근 버전에서 selectable:false 면 onNodeClick 미발사. 자식 노드는
          // selectable:true 로 두어 클릭 → NodeDrawer 가 열리게. 그룹 노드는 위에서 false
          // 유지 (clickHandler 에서도 type==="group" early-return).
          selectable: true,
          zIndex: 10,
        } as Node;
      })
      .filter((n): n is Node => n !== null);

    return [...groupNodes, ...childNodes];
  }, [
    layout,
    defs,
    effective,
    nodeStates,
    nodeScores,
    nodeTimings,
    nodeConfidence,
    personaMode,
    debateStatusByNode,
    debateRoundByNode,
    debateFinishFlashByNode,
    onDebateOpen,
    onNodeClick,
    tenantContext,
    newlyAddedIds,
    nodeSubOverrides,
  ]);

  // 동적 fan-out targets — effective Layer 2 노드 set (base + extras).
  // 신한 부서특화 노드도 BusEdge 로 렌더되어 base sub-agent 와 동일 trunk 공유 (이중 간선 방지).
  // group_layer2 안의 sub-agent 만 fan-out 트렁크 대상 (Layer 1/3/4 노드는 제외).
  const fanOutTargets = useMemo(
    () => new Set(effective.layer2Children),
    [effective],
  );

  const edges: Edge[] = useMemo(() => {
    /* ── Edge styling tokens (muted, modern) ──
       Pending / bus 엣지는 매우 흐리게 → 실행 중/완료된 것만 눈에 띔.
       색상은 채도 낮춰서 "진한" 느낌 제거. */
    const strokeFor = (state: string): string => {
      if (state === "error" || state === "gate-failed") return "#c95c4f";
      if (state === "skipped") return "#b5b0a0";
      if (state === "done") return "#7ab896"; // 연한 그린
      if (state === "active") return "#d98166"; // 연한 오렌지
      return "#9a9583"; // pending — 기존 #c8c4b5 보다 진한 톤 (가독성 UP)
    };

    // ── 공유 trunk X 좌표 — 모든 fan-out/fan-in BusEdge 가 동일 trunk 정렬.
    // KMS 우측 + 45 (fan-out 트렁크) / layer2_barrier 좌측 - 45 (fan-in 트렁크).
    // 새 토폴로지: layer1 → kms → [8 sub-agents fan-out] → layer2_barrier.
    const layoutNodes = layout?.nodes || [];
    const kmsPos = layoutNodes.find((n) => n.id === "kms");
    const fanOutTrunkX = kmsPos
      ? kmsPos.x + kmsPos.w + 45
      : undefined;
    const barrierPos = layoutNodes.find((n) => n.id === "layer2_barrier");
    const fanInTrunkX = barrierPos ? barrierPos.x - 45 : undefined;

    return effective.edges.filter((e) => {
      if (!effective.defs[e.from] || !effective.defs[e.to]) return false;
      // short-circuit (layer1 → layer4 점선) 제거 — 사용자 요청
      if (SHORT_CIRCUIT_EDGES.has(edgeKey(e.from, e.to))) return false;
      return true;
    }).map((e) => {
      const k = edgeKey(e.from, e.to);
      const state = edgeStates[k] || "pending";
      const active = state === "active";
      const done = state === "done";
      const skipped = state === "skipped";
      const isShortCircuit = SHORT_CIRCUIT_EDGES.has(k);
      // Layer 2 fan-out: kms → 모든 active L2 노드 (base + 부서특화 extras)
      const isFanOut = e.from === "kms" && fanOutTargets.has(e.to);
      // Layer 2 fan-in: 모든 active L2 노드 → layer2_barrier
      const isFanIn = fanOutTargets.has(e.from) && e.to === "layer2_barrier";
      const useBus = isFanOut || isFanIn;

      const stroke = strokeFor(state);

      // pending 상태 가시성 살짝 올림 — 실행 전에도 파이프라인 구조가 눈에 띄도록.
      let opacity: number;
      if (skipped) opacity = 0.35;
      else if (isShortCircuit) opacity = 0.55;
      else if (useBus) {
        if (active) opacity = 0.95;
        else if (done) opacity = 0.5;
        else opacity = 0.4; // pending bus — 기존 0.22 → 0.4
      } else {
        if (active) opacity = 0.95;
        else if (done) opacity = 0.7;
        else opacity = 0.7; // pending 일반 — 기존 0.55 → 0.7
      }

      // BusEdge 는 이제 Framer Motion 기반이라 arc 구간에서도 부드럽게 흐름.
      // 모든 active edge 에 marching-ants 애니메이션 활성화.
      const shouldAnimate = active;

      // ksqi_report → combined_report: KSQI 분기는 메인 체인 위쪽에 배치되므로
      // ksqi_report 의 bottom 핸들에서 combined_report 의 top 핸들로 수직 진입.
      const isKsqiToCombined =
        e.from === "ksqi_report" && e.to === "combined_report";

      return {
        id: k,
        source: e.from,
        target: e.to,
        sourceHandle: isShortCircuit
          ? "bottom"
          : isKsqiToCombined
            ? "bottom"
            : undefined,
        targetHandle: isShortCircuit
          ? "bottom"
          : isKsqiToCombined
            ? "top"
            : undefined,
        // Framer Motion 기반 커스텀 edge 로 통일 — bus / flow 구분해 렌더
        type: useBus ? "bus" : "flow",
        animated: shouldAnimate,
        label: undefined,
        data: useBus
          ? {
              busIn: isFanIn,
              // 공유 trunkX 주입 — 신한 dept 노드 fan-out 도 base 8 과 동일 trunk 라인 정렬
              trunkX: isFanIn ? fanInTrunkX : fanOutTrunkX,
            }
          : undefined,
        pathOptions: isShortCircuit
          ? { borderRadius: 24, offset: 60 }
          : { borderRadius: 8, offset: 0 },
        style: {
          stroke,
          strokeWidth: active ? 2.2 : done ? 1.6 : 1.2,
          // strokeDasharray 는 EDGE DEF 에만 의존 — 실행 중 점선↔실선 flip-flop 방지.
          //   (이전: `e.dashed || skipped` 때문에 skipped 전환 시 dashed 가 토글됨)
          strokeDasharray: e.dashed ? "5 4" : undefined,
          opacity,
        },
        zIndex: 0,
      } as Edge;
    });
  }, [edgeStates, effective, fanOutTargets, layout]);

  return (
    <div
      className="pipeline-flow-container"
      style={{
        width: "100%",
        height: 760,
        borderRadius: 16,
        border: "1px solid #ece8d8",
        background:
          "linear-gradient(180deg, #fdfcf8 0%, #fbfaf5 100%)",
        boxShadow: "0 1px 2px rgba(0,0,0,0.03), 0 4px 16px rgba(0,0,0,0.03)",
        overflow: "hidden",
        // 텍스트 렌더링 품질 — 서브픽셀 blur 최소화.
        textRendering: "geometricPrecision" as const,
        WebkitFontSmoothing: "antialiased" as const,
      }}
    >
      {!layout ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: "100%",
            color: "#9a9583",
            fontSize: 12,
          }}
        >
          파이프라인 레이아웃 계산 중…
        </div>
      ) : (
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          // 초기 viewport 를 시작 노드(input_data) 좌측 기준으로 고정.
          // fitView 는 전체 중앙을 맞추므로 확대 시 중간만 보이고 좌측 시작부가 잘린다.
          // Controls 의 "fit view" 버튼은 수동 호출 시 아래 fitViewOptions 을 사용.
          defaultViewport={initialViewport}
          fitViewOptions={FIT_VIEW_OPTIONS}
          minZoom={0.3}
          maxZoom={2.5}
          connectionLineType={ConnectionLineType.SmoothStep}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={!!onNodeClick}
          proOptions={PRO_OPTIONS}
          defaultEdgeOptions={DEFAULT_EDGE_OPTIONS}
          elevateEdgesOnSelect={false}
          elevateNodesOnSelect={true}
          onNodeClick={handleNodeClickInternal}
        >
          <Background gap={28} size={1.2} color="#e7e3d4" />
          <Controls
            showInteractive={false}
            style={{
              border: "1px solid #ece8d8",
              borderRadius: 10,
              background: "#ffffff",
              boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
            }}
          />
          <MiniMap
            style={{
              width: 140,
              height: 90,
              border: "1px solid #ece8d8",
              borderRadius: 10,
              background: "#ffffff",
            }}
            maskColor="rgba(232,229,217,0.45)"
            nodeColor={(n) => {
              if (n.type === "group") return "transparent";
              const state =
                (n.data as { state?: NodeState } | undefined)?.state ||
                "pending";
              if (state === "active") return "#c96442";
              if (state === "done") return "#3d8c5f";
              if (state === "error" || state === "gate-failed") return "#b03a2e";
              if (state === "skipped") return "#d9d5c5";
              return "#cdc8b8";
            }}
          />
        </ReactFlow>
      )}
    </div>
  );
}

// 커스텀 비교자 — 부모 (EvaluateRunner) 의 100ms elapsed 타이머 tick 에도
// nodeStates / edgeStates 객체 레퍼런스가 매번 달라지는 경우 shallow diff 로 얕은 비교.
// Record 값이 실제로 바뀌지 않았으면 리렌더 skip 해서 edge 애니메이션 끊김 방지.
function areRecordEqual<T>(a: Record<string, T>, b: Record<string, T>): boolean {
  if (a === b) return true;
  const ak = Object.keys(a);
  const bk = Object.keys(b);
  if (ak.length !== bk.length) return false;
  for (const k of ak) if (a[k] !== b[k]) return false;
  return true;
}

function arePropsEqual(prev: PipelineFlowProps, next: PipelineFlowProps): boolean {
  const tcPrev = prev.tenantContext;
  const tcNext = next.tenantContext;
  const tenantSame =
    (tcPrev === tcNext) ||
    (!!tcPrev &&
      !!tcNext &&
      tcPrev.siteId === tcNext.siteId &&
      tcPrev.channel === tcNext.channel &&
      tcPrev.department === tcNext.department &&
      tcPrev.flashKey === tcNext.flashKey);
  // tenantPipelineConfig 는 EvaluateRunner 에서 useMemo([siteId,department]) 로 안정 ref →
  // reference equality 로 충분 (JSON.stringify 비용 제거 — 250ms 마다 호출되던 비용 큼).
  const cfgSame = prev.tenantPipelineConfig === next.tenantPipelineConfig;
  // debateRoundByNode 는 {round, max} 객체 값 — 한 단계 deep compare.
  const debateRoundSame = areDebateRoundEqual(
    prev.debateRoundByNode ?? {},
    next.debateRoundByNode ?? {},
  );
  // debateFinishFlashByNode 는 {item_number, score, at} 객체 — at 타임스탬프가 핵심 변경 신호.
  const debateFinishFlashSame = areDebateFinishFlashEqual(
    prev.debateFinishFlashByNode ?? {},
    next.debateFinishFlashByNode ?? {},
  );
  return (
    areRecordEqual(prev.nodeStates, next.nodeStates) &&
    areRecordEqual(prev.nodeScores, next.nodeScores) &&
    areRecordEqual(prev.nodeTimings, next.nodeTimings) &&
    areRecordEqual(prev.edgeStates, next.edgeStates) &&
    prev.personaMode === next.personaMode &&
    prev.onNodeClick === next.onNodeClick &&
    prev.onDebateOpen === next.onDebateOpen &&
    tenantSame &&
    cfgSame &&
    areRecordEqual(
      (prev.debateStatusByNode ?? {}) as Record<string, string>,
      (next.debateStatusByNode ?? {}) as Record<string, string>,
    ) &&
    debateRoundSame &&
    debateFinishFlashSame &&
    areRecordEqual(
      (prev.nodeSubOverrides ?? {}) as Record<string, string>,
      (next.nodeSubOverrides ?? {}) as Record<string, string>,
    )
  );
}

function areDebateFinishFlashEqual(
  a: Record<
    string,
    { item_number?: number; score?: number | null; at?: number } | undefined
  >,
  b: Record<
    string,
    { item_number?: number; score?: number | null; at?: number } | undefined
  >,
): boolean {
  if (a === b) return true;
  const ak = Object.keys(a);
  const bk = Object.keys(b);
  if (ak.length !== bk.length) return false;
  for (const k of ak) {
    const av = a[k];
    const bv = b[k];
    if (av === bv) continue;
    if (!av || !bv) return false;
    if (
      av.item_number !== bv.item_number ||
      av.score !== bv.score ||
      av.at !== bv.at
    ) {
      return false;
    }
  }
  return true;
}

function areDebateRoundEqual(
  a: Record<string, { round?: number; max?: number } | undefined>,
  b: Record<string, { round?: number; max?: number } | undefined>,
): boolean {
  if (a === b) return true;
  const ak = Object.keys(a);
  const bk = Object.keys(b);
  if (ak.length !== bk.length) return false;
  for (const k of ak) {
    const av = a[k];
    const bv = b[k];
    if (av === bv) continue;
    if (!av || !bv) return false;
    if (av.round !== bv.round || av.max !== bv.max) return false;
  }
  return true;
}

export const PipelineFlow = memo(PipelineFlowImpl, arePropsEqual);
export default PipelineFlow;
