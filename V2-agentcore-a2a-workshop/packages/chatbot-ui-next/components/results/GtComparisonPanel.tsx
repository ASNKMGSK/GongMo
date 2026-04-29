// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { memo } from "react";

import type { GtComparison } from "@/lib/types";

interface Props {
  gc: GtComparison | null | undefined;
}

function GtComparisonPanel({ gc }: Props) {
  if (!gc || (gc as { enabled?: boolean }).enabled === false) return null;
  const items = gc.items || [];
  if (!items.length) return null;

  return (
    <div
      className="card card-padded"
      style={{
        borderLeft: "4px solid var(--success)",
        marginBottom: 16,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
          marginBottom: 10,
        }}
      >
        <span
          style={{
            fontSize: 14,
            fontWeight: 700,
            color: "var(--success)",
          }}
        >
          AI vs 사람 QA 점수 비교
        </span>
        <span style={{ fontSize: 11, color: "var(--ink-muted)" }}>
          상담ID <code className="kbd">{gc.sample_id}</code> · 업무정확도 (#15,
          #16) 제외
        </span>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
          gap: 8,
          marginBottom: 12,
        }}
      >
        <Stat label="AI 합계" value={gc.ai_total ?? "-"} />
        <Stat label="사람 QA 합계" value={gc.gt_total ?? "-"} />
        <Stat
          label="차이 (AI−사람)"
          value={
            gc.diff == null ? "-" : gc.diff > 0 ? `+${gc.diff}` : `${gc.diff}`
          }
          tint={gc.diff == null ? undefined : gc.diff === 0 ? "good" : "warn"}
        />
        <Stat label="MAE" value={gc.mae ?? "-"} />
        <Stat label="RMSE" value={gc.rmse ?? "-"} />
        <Stat
          label="일치/불일치"
          value={`${gc.match_count ?? 0}/${gc.mismatch_count ?? 0}`}
        />
      </div>
      <div style={{ fontSize: 11 }}>
        <table className="table-clean" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left", width: 36 }}>#</th>
              <th style={{ textAlign: "left" }}>항목</th>
              <th style={{ textAlign: "right", width: 60 }}>AI</th>
              <th style={{ textAlign: "right", width: 70 }}>사람 QA</th>
              <th style={{ textAlign: "center", width: 60 }}>Δ</th>
            </tr>
          </thead>
          <tbody>
            {items.map((row) => {
              const diff =
                row.ai_score != null && row.gt_score != null
                  ? Number(row.ai_score) - Number(row.gt_score)
                  : null;
              const diffColor =
                row.excluded
                  ? "var(--ink-muted)"
                  : diff == null
                    ? "var(--ink-subtle)"
                    : diff === 0
                      ? "var(--success)"
                      : diff > 0
                        ? "var(--warn)"
                        : "var(--info)";
              return (
                <tr
                  key={row.item_number}
                  title={(row as { note?: string }).note || ""}
                  style={{
                    background: row.excluded
                      ? "var(--warn-bg)"
                      : diff === 0
                        ? "var(--success-bg)"
                        : diff == null
                          ? "transparent"
                          : "var(--danger-bg)",
                  }}
                >
                  <td style={{ fontWeight: 700, color: "var(--ink-muted)" }}>
                    #{row.item_number}
                  </td>
                  <td
                    style={{
                      color: "var(--ink)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      maxWidth: 200,
                    }}
                  >
                    {row.item_name}
                  </td>
                  <td
                    className="num tabular-nums"
                    style={{ textAlign: "right", fontWeight: 600 }}
                  >
                    {row.ai_score ?? "-"}
                  </td>
                  <td
                    className="num tabular-nums"
                    style={{ textAlign: "right", fontWeight: 600 }}
                  >
                    {row.gt_score ?? "-"}
                  </td>
                  <td
                    className="num tabular-nums"
                    style={{
                      textAlign: "center",
                      fontWeight: 700,
                      color: diffColor,
                    }}
                  >
                    {row.excluded
                      ? "제외"
                      : diff == null
                        ? "—"
                        : diff > 0
                          ? `+${diff}`
                          : `${diff}`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tint,
}: {
  label: string;
  value: string | number;
  tint?: "good" | "warn";
}) {
  const color =
    tint === "good"
      ? "var(--success)"
      : tint === "warn"
        ? "var(--warn)"
        : "var(--ink)";
  return (
    <div
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
        {label}
      </div>
      <div
        className="tabular-nums"
        style={{
          fontSize: 15,
          fontWeight: 800,
          color,
          marginTop: 2,
        }}
      >
        {value}
      </div>
    </div>
  );
}

export default memo(GtComparisonPanel);
