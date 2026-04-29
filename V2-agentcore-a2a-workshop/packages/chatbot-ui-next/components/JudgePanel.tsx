// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { memo } from "react";

import type { PersonaVotes } from "@/lib/types";

/** JudgePanel — V2 HTML 5749~5811 이식. 3 persona 충돌 시 판사 LLM 숙고 결과 패널. */

const PERSONA_ORDER: Array<"strict" | "neutral" | "loose"> = [
  "strict",
  "neutral",
  "loose",
];
const PERSONA_LABELS_KO: Record<string, string> = {
  strict: "품격",
  neutral: "정확성",
  loose: "고객경험",
};
const ANON_LABELS = ["A", "B", "C"];

interface PersonaDetail {
  judgment?: string;
  deductions?: unknown;
  evidence?: unknown;
}

interface Props {
  personaVotes: PersonaVotes | null | undefined;
  judgeReasoning?: string | null;
  finalScore: number | null | undefined;
  maxScore: number;
  stepSpread?: number;
  personaDetails?: Record<string, PersonaDetail> | null;
  personaLabelMap?: Record<string, string> | null;
}

function JudgePanel({
  personaVotes,
  judgeReasoning,
  finalScore,
  maxScore,
  stepSpread,
  personaDetails,
  personaLabelMap,
}: Props) {
  if (!personaVotes) return null;

  return (
    <div
      style={{
        border: "1px solid var(--warn-border)",
        background: "var(--warn-bg)",
        borderRadius: "var(--radius-sm)",
        padding: 12,
        marginTop: 8,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 12,
          fontWeight: 700,
          color: "var(--warn)",
          marginBottom: 4,
        }}
      >
        <span>판사 숙고 결과</span>
      </div>
      <div style={{ fontSize: 11, color: "var(--ink-muted)", marginBottom: 10 }}>
        의견 충돌 감지 — step_spread:{" "}
        <b className="tabular-nums">{stepSpread ?? "?"}</b> · 판사 LLM 이 3 평가자
        의견을 종합해 최종 결정.
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 8,
          marginBottom: 10,
        }}
      >
        {PERSONA_ORDER.map((p, i) => {
          const raw = personaVotes[p];
          const hasVote =
            raw !== null && raw !== undefined && !Number.isNaN(Number(raw));
          const details = (personaDetails && personaDetails[p]) || null;
          const personaDisplayLabel =
            (personaLabelMap && personaLabelMap[p]) || PERSONA_LABELS_KO[p];
          return (
            <div
              key={p}
              className="card card-padded"
              style={{
                background: "var(--surface)",
                padding: 10,
                border: hasVote
                  ? "1px solid var(--border)"
                  : "1px dashed var(--border-strong)",
                opacity: hasVote ? 1 : 0.6,
              }}
            >
              <div
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  color: "var(--ink-muted)",
                  marginBottom: 4,
                }}
              >
                평가자 {ANON_LABELS[i]}{" "}
                <span style={{ color: "var(--ink-subtle)", fontWeight: 500 }}>
                  ({personaDisplayLabel})
                </span>
              </div>
              <div
                className="tabular-nums"
                style={{
                  fontSize: 16,
                  fontWeight: 700,
                  color: hasVote
                    ? `var(--persona-${p})`
                    : "var(--ink-subtle)",
                }}
              >
                {hasVote ? `${Number(raw)} / ${maxScore}` : "실패 / 누락"}
              </div>
              {details?.judgment && (
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--ink-muted)",
                    lineHeight: 1.45,
                    marginTop: 6,
                  }}
                >
                  {String(details.judgment).slice(0, 220)}
                  {String(details.judgment).length > 220 && "…"}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div
        style={{
          padding: 10,
          background: "var(--accent-bg)",
          border: "1px solid var(--accent-bg-strong)",
          borderRadius: "var(--radius-sm)",
        }}
      >
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            color: "var(--accent-strong)",
            marginBottom: 4,
          }}
        >
          판사 최종 결정
        </div>
        <div
          className="tabular-nums"
          style={{
            fontSize: 18,
            fontWeight: 800,
            color: "var(--accent)",
            marginBottom: 6,
          }}
        >
          {finalScore ?? "—"} / {maxScore}
        </div>
        {judgeReasoning ? (
          <div
            style={{
              fontSize: 12,
              color: "var(--ink-soft)",
              lineHeight: 1.5,
              whiteSpace: "pre-wrap",
            }}
          >
            {judgeReasoning}
          </div>
        ) : (
          <div style={{ fontSize: 11, color: "var(--ink-subtle)", fontStyle: "italic" }}>
            판사 근거 텍스트가 비어있습니다 — 백엔드 judge 경로 성공 여부 확인 필요
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(JudgePanel);
