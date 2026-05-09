// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { memo, useState } from "react";

import { ITEM_NAMES, scoreColor } from "@/lib/items";
import type { CategoryItem, GtComparisonItem } from "@/lib/types";

import ItemCard from "./ItemCard";
import { prepareItemProps, type RawReport } from "./prepareItemProps";

export interface AgentGroupCardProps {
  agent: string;
  label: string;
  phase: string;
  items: number[];
  report: RawReport;
  allItems: CategoryItem[];
  gtItemsByNum?: Record<number, GtComparisonItem>;
  defaultOpen?: boolean;
  expandVersion?: number;
  expandAllTarget?: boolean | null;
  editMode?: boolean;
  consultationId?: string;
  humanSavedMap?: Record<number, { score: number; confirmed: boolean }>;
  onHumanSaved?: (num: number, score: number, confirmed: boolean) => void;
  /** ★ 2026-05-07 BUGFIX: ResultsTab 의 item view 와 동일하게 NodeDrawer 열기 callback 전달.
   *  이전엔 agent view 에서 ItemCard 의 "🔍 판단 과정" 버튼이 동작 안 함. */
  onOpenNodeDrawer?: (itemNumber: number) => void;
}

function AgentGroupCard({
  agent,
  label,
  phase,
  items,
  report,
  allItems,
  gtItemsByNum,
  defaultOpen,
  expandVersion,
  expandAllTarget,
  editMode,
  consultationId,
  humanSavedMap,
  onHumanSaved,
  onOpenNodeDrawer,
}: AgentGroupCardProps) {
  // 사용자 요청 (2026-05-06): 평가 결과 탭 에이전트 그룹은 처음부터 펼친 상태로 표시.
  const [open, setOpen] = useState(defaultOpen !== false);
  const [seenExpandVersion, setSeenExpandVersion] = useState(expandVersion ?? 0);
  if (
    expandVersion !== undefined &&
    expandVersion !== seenExpandVersion &&
    expandVersion > 0 &&
    typeof expandAllTarget === "boolean"
  ) {
    setSeenExpandVersion(expandVersion);
    if (open !== expandAllTarget) setOpen(expandAllTarget);
  }

  const itemsForAgent = items
    .map((n) => allItems.find((it) => it.item_number === n))
    .filter((it): it is CategoryItem => Boolean(it));

  const { totalScore, totalMax } = itemsForAgent.reduce(
    (acc, it) => ({
      totalScore: acc.totalScore + (it.score ?? 0),
      totalMax: acc.totalMax + (it.max_score ?? 0),
    }),
    { totalScore: 0, totalMax: 0 },
  );
  const totalColor = scoreColor(totalScore, totalMax);

  // 접힘 상태에서 "어느 항목에 감점이 있는지" 미리보기를 위한 누적.
  // 점수 < 만점 인 항목을 감점으로 간주 (score-based 가 가장 신뢰 가능 — report.deductions
  // 항목별 매칭이 누락돼도 실제 감점은 score 차이로 보장됨).
  const deductedItemNums = itemsForAgent
    .filter((it) => (it.score ?? 0) < (it.max_score ?? 0))
    .map((it) => it.item_number);
  const deductionCount = deductedItemNums.length;
  const deductionPreview =
    deductionCount > 0
      ? deductedItemNums
          .slice(0, 4)
          .map((n) => `#${n}`)
          .join(", ") + (deductionCount > 4 ? "..." : "")
      : "";

  // 세부 항목 점수 미리보기 (닫힘 상태) — 한 노드에 평가 항목이 2개 이상 일 때만 표시.
  // 항목 1개인 노드 (예: shinhan explanation [10]) 는 헤더 합산 점수가 곧 항목 점수이므로 중복 노출 회피.
  const showItemBreakdown = itemsForAgent.length >= 2;
  const itemBreakdown = showItemBreakdown
    ? itemsForAgent.map((it) => {
        const sc = it.score ?? 0;
        const mx = it.max_score ?? 0;
        const isDeducted = sc < mx;
        const shortName = it.item_name || ITEM_NAMES[it.item_number] || "";
        return {
          num: it.item_number,
          shortName,
          sc,
          mx,
          isDeducted,
        };
      })
    : [];

  return (
    <div className="card" style={{ marginBottom: 10 }}>
      <div
        onClick={() => setOpen(!open)}
        style={{
          display: "flex",
          flexDirection: "column",
          padding: "12px 16px",
          cursor: "pointer",
          background: open ? "var(--surface-hover)" : "transparent",
          transition: "background 150ms var(--ease)",
          gap: 6,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="badge badge-neutral">Phase {phase}</span>
            <span style={{ fontSize: 14, fontWeight: 600, color: "var(--ink)" }}>
              {label}
            </span>
            <span style={{ fontSize: 11, color: "var(--ink-muted)" }}>
              {agent} · {itemsForAgent.length}개 항목
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {!open && deductionCount > 0 && (
              <span
                title={`감점 ${deductionCount}건: ${deductedItemNums.map((n) => `#${n}`).join(", ")}`}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "2px 10px",
                  fontSize: 11,
                  fontWeight: 600,
                  fontFamily: "'Mark For MC', var(--font-sans)",
                  color: "var(--danger)",
                  background: "var(--danger-bg)",
                  border: "1px solid var(--danger-border)",
                  borderRadius: "var(--radius-pill)",
                  whiteSpace: "nowrap",
                }}
              >
                <span aria-hidden="true">⚠</span>
                <span>
                  감점 <span className="tabular-nums">{deductionCount}</span>건 ({deductionPreview})
                </span>
              </span>
            )}
            <span
              className="tabular-nums"
              style={{
                fontSize: 16,
                fontWeight: 700,
                color: totalColor,
              }}
            >
              {totalScore}/{totalMax}
            </span>
            <span
              style={{
                fontSize: 10,
                color: "var(--ink-muted)",
                transition: "transform 200ms var(--ease)",
                transform: open ? "rotate(90deg)" : "rotate(0deg)",
              }}
            >
              ▶
            </span>
          </div>
        </div>
        {/* 닫힘 상태에서 세부 평가 항목별 점수 가로 나열. 펼친 상태에서는 ItemCard 가 렌더링하므로 숨김.
            항목 1개인 노드는 헤더 합산 점수와 동일하므로 노출 생략. */}
        {!open && showItemBreakdown && (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              gap: "4px 10px",
              paddingLeft: 2,
              fontFamily: "'Mark For MC', var(--font-sans)",
              fontSize: 11,
              lineHeight: 1.4,
            }}
          >
            {itemBreakdown.map((b, idx) => (
              <span
                key={b.num}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 3,
                  color: b.isDeducted ? "var(--danger)" : "var(--ink-soft)",
                  fontWeight: b.isDeducted ? 600 : 500,
                }}
              >
                <span style={{ opacity: 0.75 }}>#{b.num}</span>
                <span>{b.shortName}</span>
                <span className="tabular-nums">
                  {b.sc}/{b.mx}
                </span>
                {idx < itemBreakdown.length - 1 && (
                  <span
                    aria-hidden="true"
                    style={{ marginLeft: 6, color: "var(--ink-subtle)", opacity: 0.6 }}
                  >
                    ·
                  </span>
                )}
              </span>
            ))}
          </div>
        )}
      </div>
      {open && (
        <div style={{ padding: "8px 14px 14px", borderTop: "1px solid var(--border)" }}>
          {itemsForAgent.length === 0 ? (
            <div className="empty-state" style={{ padding: "20px 0" }}>
              <div className="empty-state-desc">아직 평가 결과가 없습니다</div>
            </div>
          ) : (
            itemsForAgent.map((it) => {
              const num = it.item_number;
              const props = prepareItemProps(it, num, report);
              const gtRow = gtItemsByNum?.[num];
              const saved = humanSavedMap?.[num];
              return (
                <ItemCard
                  key={num}
                  num={num}
                  name={it.item_name || ITEM_NAMES[num] || ""}
                  score={props.sc}
                  maxScore={props.mx}
                  deductions={props.itemDeds}
                  evidence={props.itemEv}
                  strengths={props.itemStr}
                  improvements={props.itemImp}
                  coaching={props.itemCoach}
                  judgment={it.judgment || null}
                  summary={it.summary || null}
                  personaVotes={it.persona_votes || null}
                  personaMergePath={it.persona_merge_path || null}
                  personaMergeRule={it.persona_merge_rule || null}
                  personaStepSpread={it.persona_step_spread ?? null}
                  postDebateJudgeScore={it.judge_score ?? null}
                  postDebateJudgeReasoning={it.judge_reasoning ?? null}
                  postDebateJudgeDeductions={
                    Array.isArray(it.judge_deductions)
                      ? (it.judge_deductions as Array<{ reason: string; points: number }>)
                      : null
                  }
                  postDebateJudgeEvidence={
                    Array.isArray(it.judge_evidence)
                      ? (it.judge_evidence as Array<{ speaker: string; quote: string }>)
                      : null
                  }
                  forceT3={!!it.force_t3 || !!it.mandatory_human_review}
                  mandatoryHumanReview={!!it.mandatory_human_review}
                  aiConfidence={it.confidence?.final ?? null}
                  defaultOpen={props.hasDetail && props.sc < props.mx}
                  expandVersion={expandVersion}
                  expandAllTarget={expandAllTarget}
                  editMode={editMode}
                  consultationId={consultationId}
                  humanSavedScore={saved?.score ?? null}
                  humanConfirmed={saved?.confirmed}
                  onHumanSaved={onHumanSaved}
                  gt={
                    gtRow
                      ? {
                          gt_score: gtRow.gt_score,
                          max_score: gtRow.max_score,
                          excluded: gtRow.excluded,
                          note: (gtRow as GtComparisonItem & { note?: string | null }).note ?? null,
                        }
                      : null
                  }
                  // ★ 2026-05-07 BUGFIX: 토론 기록 / 페르소나 상세 / NodeDrawer 콜백 누락
                  // 으로 agent view 에서 ItemProcessTimeline / DebateScoreHistory / 판단 과정 버튼
                  // 모두 표시 안되던 이슈 수정. ResultsTab item view 와 동일 prop 전달.
                  personaDetails={props.personaDetails}
                  debateRecord={props.debateRecord}
                  onOpenNodeDrawer={onOpenNodeDrawer}
                />
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

export default memo(AgentGroupCard);
