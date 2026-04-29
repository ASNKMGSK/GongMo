// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { memo } from "react";

import type { PersonaVotes } from "@/lib/types";

/**
 * PersonaBarChart — V2 HTML 5687~5744 이식. 3-persona 앙상블 막대 + spread 뱃지 + merged 하이라이트.
 */

const PERSONA_ORDER: Array<"strict" | "neutral" | "loose"> = [
  "strict",
  "neutral",
  "loose",
];
const PERSONA_LABELS: Record<string, string> = {
  strict: "품격",
  neutral: "정확성",
  loose: "고객경험",
};
const PERSONA_COLOR: Record<string, string> = {
  strict: "var(--persona-strict)",
  neutral: "var(--persona-neutral)",
  loose: "var(--persona-loose)",
};
const PERSONA_BG: Record<string, string> = {
  strict: "var(--persona-strict-bg)",
  neutral: "var(--persona-neutral-bg)",
  loose: "var(--persona-loose-bg)",
};

interface Props {
  personaVotes: PersonaVotes | null | undefined;
  merged: number | null | undefined;
  maxScore: number;
  mergeRule?: string;
  mergePath?: string;
  stepSpread?: number;
}

function PersonaBarChart({
  personaVotes,
  merged,
  maxScore,
  mergeRule,
  mergePath,
  stepSpread,
}: Props) {
  if (!personaVotes || typeof personaVotes !== "object") return null;
  const max = Math.max(1, maxScore || 10);
  const mergedPersona =
    PERSONA_ORDER.find((p) => personaVotes[p] === merged) || null;

  const spread = Number(stepSpread ?? 0);
  let badgeLabel = "합의";
  let badgeCls = "badge badge-success";
  if (spread > 0 && spread <= 2) {
    badgeLabel = `경미한 차이 · spread ${spread}`;
    badgeCls = "badge badge-warn";
  } else if (spread > 2) {
    badgeLabel = `의견 충돌 · spread ${spread}`;
    badgeCls = "badge badge-danger";
  }

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
        padding: 10,
        background: "var(--surface-muted)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 6,
        }}
      >
        <span
          style={{
            fontSize: 11,
            fontWeight: 700,
            color: "var(--ink-soft)",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
          }}
        >
          3-Persona 앙상블
        </span>
        <span className={badgeCls}>{badgeLabel}</span>
      </div>
      {PERSONA_ORDER.map((p) => {
        const raw = personaVotes[p];
        const hasVote = raw !== null && raw !== undefined && !Number.isNaN(Number(raw));
        const numScore = hasVote ? Number(raw) : null;
        const pct = hasVote ? Math.max(2, Math.min(100, ((numScore as number) / max) * 100)) : 0;
        const isMerged = hasVote && mergedPersona === p;
        return (
          <div
            key={p}
            style={{
              display: "grid",
              gridTemplateColumns: "90px 1fr 36px",
              gap: 8,
              alignItems: "center",
              marginBottom: 3,
            }}
          >
            <span
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: PERSONA_COLOR[p],
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: PERSONA_COLOR[p],
                }}
              />
              {PERSONA_LABELS[p]}
            </span>
            <div
              style={{
                height: 8,
                borderRadius: 4,
                background: "var(--surface)",
                border: isMerged
                  ? "1px solid var(--accent)"
                  : "1px solid var(--border)",
                overflow: "hidden",
              }}
              title={
                isMerged
                  ? `최종 채택 (${PERSONA_LABELS[p]})`
                  : PERSONA_LABELS[p]
              }
            >
              {hasVote && (
                <div
                  style={{
                    width: `${pct}%`,
                    height: "100%",
                    background: PERSONA_BG[p],
                    borderRight: `2px solid ${PERSONA_COLOR[p]}`,
                  }}
                />
              )}
            </div>
            <span
              className="tabular-nums"
              style={{
                fontSize: 11,
                fontWeight: 700,
                textAlign: "right",
                color: hasVote ? "var(--ink-soft)" : "var(--ink-subtle)",
              }}
            >
              {hasVote ? numScore : "—"}
            </span>
          </div>
        );
      })}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginTop: 6,
          paddingTop: 6,
          borderTop: "1px dashed var(--border)",
          fontSize: 11,
          color: "var(--ink-muted)",
        }}
      >
        <span>
          Merged:{" "}
          <b
            className="tabular-nums"
            style={{ color: "var(--accent)" }}
          >
            {merged ?? "—"} / {maxScore}
          </b>
        </span>
        {(mergeRule || mergePath) && (
          <span
            title={`merge_path: ${mergePath || "—"} · rule: ${mergeRule || "—"}`}
            style={{
              fontSize: 10,
              color: "var(--ink-subtle)",
            }}
          >
            경로: {mergeRule || mergePath}
          </span>
        )}
      </div>
    </div>
  );
}

export default memo(PersonaBarChart);
