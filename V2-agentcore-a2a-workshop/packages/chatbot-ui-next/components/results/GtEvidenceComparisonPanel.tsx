// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import type { GtEvidenceComparison } from "@/lib/types";

/**
 * GtEvidenceComparisonPanel — V2 원본 라인 7052~7143 이식.
 *
 * `result.gt_evidence_comparison` 출력 — AI evidence vs 사람 QA 정답 근거 텍스트 비교.
 * 각 항목별로 LLM 이 `match / partial / mismatch` 판정 + reasoning + AI evidence 인용을 제공.
 *
 * ResultsTab 상단에서 노출. gc.enabled !== false 일 때만.
 */

// V2 원본은 row 에 gt_note / ai_evidence[] / ai_judgment / verdict_label 등 추가 필드를 담는다.
// lib/types.ts 의 GtEvidenceItem 은 축소판이므로 here 에서 확장 타입으로 캐스팅.
interface FullGtEvidenceItem {
  item_number: number;
  item_name?: string;
  ai_score?: number | null;
  gt_score?: number | null;
  verdict?: "match" | "partial" | "mismatch" | "insufficient" | string;
  verdict_label?: string;
  reasoning?: string;
  gt_note?: string | null;
  ai_evidence?: string[] | null;
  ai_judgment?: string | null;
}

interface Props {
  ge: GtEvidenceComparison | (GtEvidenceComparison & { sample_id?: string; enabled?: boolean }) | null;
}

function verdictColor(v?: string): string {
  return v === "match"
    ? "#15803d"
    : v === "partial"
      ? "#a16207"
      : v === "mismatch"
        ? "#b91c1c"
        : "#6b7280";
}

function verdictBg(v?: string): string {
  return v === "match"
    ? "#dcfce7"
    : v === "partial"
      ? "#fef3c7"
      : v === "mismatch"
        ? "#fee2e2"
        : "#f3f4f6";
}

export default function GtEvidenceComparisonPanel({ ge }: Props) {
  if (!ge) return null;
  const withMeta = ge as GtEvidenceComparison & {
    enabled?: boolean;
    sample_id?: string;
  };
  if (withMeta.enabled === false) return null;

  const sm = ge.summary || {};
  const items = (ge.items || []) as FullGtEvidenceItem[];

  return (
    <div
      className="card card-padded"
      style={{
        borderLeft: "4px solid #a855f7",
        background: "rgba(168,85,247,0.05)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 10,
          flexWrap: "wrap",
        }}
      >
        <span style={{ fontSize: 14, fontWeight: 700, color: "#6b21a8" }}>
          🔍 AI vs 사람 QA 근거 비교 (LLM 판정)
        </span>
        <span style={{ fontSize: 11, color: "var(--ink-muted)" }}>
          — 상담ID {withMeta.sample_id || "-"} · 업무정확도 (#15, #16) 제외 · 항목별
          근거 텍스트가 동일 사실/구간을 가리키는지 LLM 비교
        </span>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(5, minmax(0, 1fr))",
          gap: 6,
          marginBottom: 12,
        }}
      >
        {[
          ["총 비교", sm.total ?? 0, "var(--ink)"] as const,
          ["일치", sm.match ?? 0, "#15803d"] as const,
          ["부분일치", sm.partial ?? 0, "#a16207"] as const,
          ["불일치", sm.mismatch ?? 0, "#b91c1c"] as const,
          ["일치율", `${sm.match_rate ?? 0}%`, "#6b21a8"] as const,
        ].map(([lab, val, col], i) => (
          <div
            key={i}
            style={{
              padding: "8px 10px",
              background: "var(--surface)",
              borderRadius: 6,
              border: "1px solid rgba(168,85,247,0.25)",
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
              style={{
                fontSize: 15,
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
      <div
        style={{
          background: "var(--surface)",
          border: "1px solid rgba(168,85,247,0.2)",
          borderRadius: 6,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "34px 160px 50px 50px 80px 1fr",
            padding: "6px 8px",
            background: "#f5f3ff",
            fontWeight: 700,
            fontSize: 11,
            color: "var(--ink)",
            borderBottom: "1px solid rgba(168,85,247,0.2)",
          }}
        >
          <span>#</span>
          <span>항목</span>
          <span style={{ textAlign: "center" }}>AI</span>
          <span style={{ textAlign: "center" }}>QA</span>
          <span style={{ textAlign: "center" }}>판정</span>
          <span>LLM 판단 근거</span>
        </div>
        {items.map((row) => (
          <details
            key={row.item_number}
            style={{ borderBottom: "1px solid var(--border)" }}
          >
            <summary
              style={{
                display: "grid",
                gridTemplateColumns: "34px 160px 50px 50px 80px 1fr",
                padding: "6px 8px",
                cursor: "pointer",
                fontSize: 11.5,
                listStyle: "none",
              }}
            >
              <span style={{ fontWeight: 700, color: "var(--ink-muted)" }}>
                #{row.item_number}
              </span>
              <span
                style={{
                  color: "var(--ink)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {row.item_name}
              </span>
              <span style={{ textAlign: "center", fontWeight: 600 }}>
                {row.ai_score ?? "-"}
              </span>
              <span style={{ textAlign: "center", fontWeight: 600 }}>
                {row.gt_score ?? "-"}
              </span>
              <span style={{ textAlign: "center" }}>
                <span
                  style={{
                    padding: "2px 6px",
                    borderRadius: 10,
                    background: verdictBg(row.verdict),
                    color: verdictColor(row.verdict),
                    fontSize: 10,
                    fontWeight: 700,
                    whiteSpace: "nowrap",
                  }}
                >
                  {row.verdict_label || row.verdict}
                </span>
              </span>
              <span
                style={{
                  color: "var(--ink-soft)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {row.reasoning}
              </span>
            </summary>
            <div
              style={{
                padding: "8px 12px 12px 42px",
                background: "var(--surface-muted)",
                fontSize: 11.5,
              }}
            >
              {/* LLM 상세 reasoning — 펼쳐졌을 때 전체 노출 (summary 의 한 줄 truncate 보완) */}
              {row.reasoning && (
                <div
                  style={{
                    marginBottom: 10,
                    padding: "8px 10px",
                    background: "#f8fafc",
                    border: "1px solid #cbd5e1",
                    borderLeft: "3px solid #6366f1",
                    borderRadius: 4,
                    color: "var(--ink)",
                    lineHeight: 1.55,
                    whiteSpace: "pre-wrap",
                  }}
                >
                  <div
                    style={{
                      fontWeight: 700,
                      color: "#4f46e5",
                      marginBottom: 4,
                      fontSize: 11,
                    }}
                  >
                    🧠 LLM 비교 분석 ({row.verdict_label || row.verdict})
                  </div>
                  {row.reasoning}
                </div>
              )}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 12,
                }}
              >
              <div>
                <div
                  style={{
                    fontWeight: 700,
                    color: "#15803d",
                    marginBottom: 4,
                  }}
                >
                  👤 사람 QA 정답 근거
                </div>
                <div
                  style={{
                    padding: "8px 10px",
                    background: "#f0fdf4",
                    border: "1px solid #bbf7d0",
                    borderRadius: 4,
                    whiteSpace: "pre-wrap",
                    color: "var(--ink-soft)",
                    lineHeight: 1.5,
                  }}
                >
                  {row.gt_note || "(없음)"}
                </div>
              </div>
              <div>
                <div
                  style={{
                    fontWeight: 700,
                    color: "#5b21b6",
                    marginBottom: 4,
                  }}
                >
                  🤖 AI 평가 근거
                </div>
                <div
                  style={{
                    padding: "8px 10px",
                    background: "#faf5ff",
                    border: "1px solid #ddd6fe",
                    borderRadius: 4,
                    color: "var(--ink-soft)",
                    lineHeight: 1.5,
                  }}
                >
                  {row.ai_evidence && row.ai_evidence.length > 0 ? (
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {row.ai_evidence.map((ln, i) => (
                        <li key={i} style={{ marginBottom: 2 }}>
                          {ln}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    "(없음)"
                  )}
                  {row.ai_judgment && (
                    <div
                      style={{
                        marginTop: 6,
                        paddingTop: 6,
                        borderTop: "1px dashed #ddd6fe",
                        fontStyle: "italic",
                        color: "var(--ink-muted)",
                      }}
                    >
                      판정: {row.ai_judgment}
                    </div>
                  )}
                </div>
              </div>
              </div>
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}
