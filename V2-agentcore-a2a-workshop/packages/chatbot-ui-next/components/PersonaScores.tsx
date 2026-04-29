// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { memo } from "react";

import type { PersonaVotes } from "@/lib/types";

interface Props {
  votes: PersonaVotes;
  mergedScore?: number | null;
  mergePath?: string;
}

const PERSONA_DEFS = [
  { k: "strict" as const, label: "품격", color: "var(--persona-strict)", bg: "var(--persona-strict-bg)" },
  { k: "neutral" as const, label: "정확성", color: "var(--persona-neutral)", bg: "var(--persona-neutral-bg)" },
  { k: "loose" as const, label: "고객경험", color: "var(--persona-loose)", bg: "var(--persona-loose-bg)" },
];

function PersonaScores({ votes, mergedScore, mergePath }: Props) {
  return (
    <div>
      <div
        style={{
          fontSize: 10,
          fontWeight: 700,
          color: "var(--ink-muted)",
          marginBottom: 3,
          textTransform: "uppercase",
          letterSpacing: "0.04em",
        }}
      >
        3-Persona 앙상블
      </div>
      <div
        style={{
          display: "flex",
          gap: 8,
          flexWrap: "wrap",
          fontSize: 10,
        }}
      >
        {PERSONA_DEFS.map((p) => {
          const v = votes?.[p.k];
          return (
            <div
              key={p.k}
              style={{
                padding: "3px 8px",
                background: p.bg,
                color: p.color,
                borderRadius: "var(--radius-sm)",
                fontWeight: 600,
              }}
            >
              {p.label}: {v != null ? v : "—"}
            </div>
          );
        })}
        {mergedScore != null && (
          <div
            style={{
              padding: "3px 8px",
              background: "var(--accent-bg)",
              color: "var(--accent)",
              borderRadius: "var(--radius-sm)",
              fontWeight: 700,
            }}
          >
            → 합의: {mergedScore} ({mergePath || "-"})
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(PersonaScores);
