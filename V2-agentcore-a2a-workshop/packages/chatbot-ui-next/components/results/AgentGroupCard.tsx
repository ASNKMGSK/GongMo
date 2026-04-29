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
}: AgentGroupCardProps) {
  const [open, setOpen] = useState(!!defaultOpen);
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

  return (
    <div className="card" style={{ marginBottom: 10 }}>
      <div
        onClick={() => setOpen(!open)}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 16px",
          cursor: "pointer",
          background: open ? "var(--surface-hover)" : "transparent",
          transition: "background 150ms var(--ease)",
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
