// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import DOMPurify from "dompurify";
import { marked } from "marked";
import { memo, useMemo } from "react";

import { PERSONA_ORDER, PERSONA_STYLES } from "@/lib/personas";
import type { DebateRecord, DebateTurn } from "@/lib/types";

// marked 글로벌 hook — 모든 parse 결과를 DOMPurify 로 세정. 파일 로드 시 1회.
marked.use({
  hooks: {
    postprocess(html: string) {
      return DOMPurify.sanitize(html, {
        ALLOWED_TAGS: [
          "p",
          "strong",
          "em",
          "a",
          "ul",
          "ol",
          "li",
          "code",
          "pre",
          "blockquote",
          "br",
          "h3",
          "h4",
        ],
        ALLOWED_ATTR: ["href", "title"],
      });
    },
  },
});

function renderMarkdown(src: string): string {
  if (!src) return "";
  const out = marked.parse(src, { async: false });
  return typeof out === "string" ? out : "";
}

interface Props {
  record: DebateRecord;
  defaultOpen?: boolean;
}

function PersonaBadge({ persona }: { persona: DebateTurn["persona"] }) {
  const style = PERSONA_STYLES[persona];
  return (
    <span
      style={{
        fontSize: 10,
        fontWeight: 700,
        padding: "2px 8px",
        borderRadius: "var(--radius-pill)",
        background: style.bg,
        color: style.color,
        border: `1px solid ${style.border}`,
      }}
    >
      {style.label}
    </span>
  );
}

function PersonaTurnCard({ turn }: { turn: DebateTurn }) {
  const style = PERSONA_STYLES[turn.persona];
  const html = useMemo(() => renderMarkdown(turn.argument), [turn.argument]);
  return (
    <div
      style={{
        border: `1px solid ${style.border}`,
        borderLeft: `3px solid ${style.color}`,
        borderRadius: "var(--radius-sm)",
        padding: "8px 12px",
        background: "var(--surface)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 6,
        }}
      >
        <PersonaBadge persona={turn.persona} />
        <span
          style={{
            fontSize: 12,
            fontWeight: 800,
            color: style.color,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {turn.score}
        </span>
      </div>
      <div
        style={{
          fontSize: 12,
          color: "var(--ink)",
          lineHeight: 1.6,
        }}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
}

function MergeRuleBadge({ mergeRule }: { mergeRule: string }) {
  const variantByRule: Record<string, string> = {
    consensus: "badge badge-success",
    median_vote: "badge badge-warn",
    fallback_median: "badge badge-danger",
  };
  const label: Record<string, string> = {
    consensus: "합의",
    median_vote: "중앙값",
    fallback_median: "폴백(중앙값)",
  };
  const cls = variantByRule[mergeRule] || "badge badge-neutral";
  return <span className={cls}>{label[mergeRule] || mergeRule}</span>;
}

function DebateRecordCard({ record, defaultOpen = false }: Props) {
  const finalHtml = useMemo(
    () => renderMarkdown(record.final_rationale || ""),
    [record.final_rationale],
  );

  // 페르소나별 "마지막 발언 점수" — 최종 입장 비교용
  const lastPersonaTurns = useMemo(() => {
    const out: Record<string, { score: number; argument: string } | null> = {
      strict: null,
      neutral: null,
      loose: null,
    };
    for (const r of record.rounds || []) {
      for (const t of r.turns || []) {
        if (t.persona in out) {
          out[t.persona] = { score: t.score, argument: t.argument };
        }
      }
    }
    return out;
  }, [record.rounds]);

  // UI-level consensus 판정 — 백엔드 merge_rule 이 consensus 여도 실제 마지막 점수가 갈리면 vote 로 표기
  const allFinalMatch = PERSONA_ORDER.every((p) => {
    const last = lastPersonaTurns[p];
    return last?.score != null && last.score === record.final_score;
  });
  const uiVerdict: "consensus" | "vote" | "fallback" =
    record.merge_rule === "fallback_median"
      ? "fallback"
      : allFinalMatch
        ? "consensus"
        : "vote";

  return (
    <details
      open={defaultOpen}
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        background: "var(--surface)",
        overflow: "hidden",
        boxShadow: "var(--shadow-flat)",
      }}
    >
      <summary
        style={{
          padding: "10px 14px",
          cursor: "pointer",
          fontSize: 12,
          fontWeight: 700,
          color: "var(--ink)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
          background: "var(--surface-muted)",
        }}
      >
        <span style={{ fontSize: 14 }}>🗣️</span>
        <span>
          토론 기록 · #{record.item_number} {record.item_name}
        </span>
        <span style={{ color: "var(--ink-muted)", fontWeight: 400 }}>
          · {record.rounds_used} 라운드
        </span>
        <MergeRuleBadge mergeRule={record.merge_rule} />
        {record.converged ? (
          <span className="badge badge-success">✅ 수렴</span>
        ) : (
          <span className="badge badge-danger">⚠ 미수렴</span>
        )}
        <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <span className="badge badge-accent">
            최종:{" "}
            <b style={{ fontSize: 13 }}>
              {record.final_score != null ? record.final_score : "—"}
            </b>
            /{record.max_score}
          </span>
        </span>
      </summary>
      <div
        style={{
          padding: "12px 14px",
          borderTop: "1px dashed var(--border)",
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        {/* ── 토론 요약 배너 — 합의/투표/폴백 한눈에 구분 ── */}
        <div
          style={{
            padding: "10px 14px",
            borderRadius: 10,
            background:
              uiVerdict === "consensus"
                ? "linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%)"
                : uiVerdict === "fallback"
                  ? "linear-gradient(135deg, #fee2e2 0%, #fecaca 100%)"
                  : "linear-gradient(135deg, #ede9fe 0%, #ddd6fe 100%)",
            color:
              uiVerdict === "consensus"
                ? "#064e3b"
                : uiVerdict === "fallback"
                  ? "#7f1d1d"
                  : "#4c1d95",
            display: "flex",
            alignItems: "center",
            gap: 12,
            flexWrap: "wrap",
          }}
        >
          <span style={{ fontSize: 22 }} aria-hidden="true">
            {uiVerdict === "consensus" ? "🤝" : uiVerdict === "fallback" ? "⚠️" : "🗳️"}
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 800, lineHeight: 1.2 }}>
              {uiVerdict === "consensus"
                ? "만장일치 합의"
                : uiVerdict === "fallback"
                  ? "폴백(중앙값) — 토론 실패"
                  : "합의 불가 · 과반 투표로 결정"}
            </div>
            <div style={{ fontSize: 11, lineHeight: 1.45, opacity: 0.82, marginTop: 2 }}>
              {uiVerdict === "consensus"
                ? "3명의 페르소나가 모두 동일한 점수를 선택했습니다."
                : uiVerdict === "fallback"
                  ? "토론 엔진 실패로 페르소나 초기 점수의 중앙값을 최종값으로 사용했습니다."
                  : `${record.rounds_used}라운드 토론 후에도 의견이 갈려, 중앙값(median) 기준 과반 점수를 최종값으로 선택했습니다.`}
            </div>
          </div>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-end",
              gap: 2,
            }}
          >
            <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.06em" }}>
              최종 점수
            </div>
            <div style={{ fontSize: 24, fontWeight: 800, fontVariantNumeric: "tabular-nums", lineHeight: 1 }}>
              {record.final_score != null ? record.final_score : "—"}
              <span style={{ fontSize: 13, opacity: 0.55, marginLeft: 3 }}>
                / {record.max_score}
              </span>
            </div>
          </div>
        </div>

        {/* ── 초기 vs 최종 비교 — 페르소나별 위치 이동 한눈에 ── */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            gap: 8,
          }}
        >
          {PERSONA_ORDER.map((p) => {
            const s = PERSONA_STYLES[p];
            const init = record.initial_positions?.[p];
            const last = lastPersonaTurns[p];
            const matchesFinal =
              last?.score != null && last.score === record.final_score;
            const moved = init != null && last?.score != null && init !== last.score;
            return (
              <div
                key={p}
                style={{
                  border: `1.5px solid ${matchesFinal ? s.color : s.border}`,
                  background: s.bg,
                  borderRadius: 10,
                  padding: "8px 10px",
                  opacity: matchesFinal ? 1 : 0.78,
                  boxShadow: matchesFinal ? "0 2px 8px rgba(0,0,0,0.08)" : "none",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 14 }} aria-hidden="true">
                    {p === "strict" ? "🧑‍⚖️" : p === "neutral" ? "🤝" : "🌿"}
                  </span>
                  <span style={{ fontSize: 11.5, fontWeight: 700, color: s.color, flex: 1 }}>
                    {s.label}
                  </span>
                  {matchesFinal ? (
                    <span
                      style={{
                        fontSize: 9.5,
                        fontWeight: 800,
                        color: "#fff",
                        background: s.color,
                        padding: "2px 7px",
                        borderRadius: 9999,
                        letterSpacing: "0.04em",
                      }}
                    >
                      {uiVerdict === "consensus" ? "✓ 일치" : "✓ 채택"}
                    </span>
                  ) : (
                    <span
                      style={{
                        fontSize: 9.5,
                        fontWeight: 700,
                        color: "#8a857a",
                        background: "#f3f2ed",
                        padding: "2px 7px",
                        borderRadius: 9999,
                        border: "1px solid #d9d4c5",
                      }}
                    >
                      ✗ 비채택
                    </span>
                  )}
                </div>
                <div
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    gap: 4,
                    marginTop: 6,
                    color: s.color,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  <span style={{ fontSize: 10, opacity: 0.65 }}>초기</span>
                  <span style={{ fontSize: 13, fontWeight: 700 }}>{init ?? "—"}</span>
                  <span style={{ fontSize: 11, opacity: 0.55 }}>→</span>
                  <span style={{ fontSize: 10, opacity: 0.65 }}>최종</span>
                  <span style={{ fontSize: 18, fontWeight: 800 }}>{last?.score ?? "—"}</span>
                  {moved && (
                    <span
                      style={{
                        fontSize: 9,
                        marginLeft: "auto",
                        color: s.color,
                        opacity: 0.65,
                      }}
                    >
                      {(last!.score - init!) > 0 ? "↑" : "↓"}
                      {Math.abs(last!.score - init!)}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <div
          style={{
            display: "flex",
            gap: 8,
            flexWrap: "wrap",
            fontSize: 11,
            color: "var(--ink-soft)",
          }}
        >
          <span style={{ fontWeight: 700 }}>초기 점수:</span>
          {PERSONA_ORDER.map((p) => {
            const init = record.initial_positions?.[p];
            const s = PERSONA_STYLES[p];
            return (
              <span
                key={p}
                style={{
                  padding: "2px 8px",
                  borderRadius: "var(--radius-pill)",
                  background: s.bg,
                  color: s.color,
                  fontWeight: 600,
                }}
              >
                {s.label} {init ?? "—"}
              </span>
            );
          })}
          {Array.isArray(record.allowed_steps) &&
            record.allowed_steps.length > 0 && (
              <span
                style={{
                  marginLeft: "auto",
                  color: "var(--ink-muted)",
                  fontFamily: "var(--font-mono), monospace",
                }}
                title="ALLOWED_STEPS — 토론 중 허용된 점수 단계"
              >
                steps: [{record.allowed_steps.join(", ")}]
              </span>
            )}
        </div>

        {record.rounds.map((r) => (
          <div
            key={r.round}
            style={{
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              padding: "10px 12px",
              background: "var(--surface-muted)",
            }}
          >
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: "var(--ink-soft)",
                marginBottom: 8,
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              라운드 {r.round}
              {r.verdict?.consensus ? (
                <span className="badge badge-success">합의</span>
              ) : (
                <span className="badge badge-warn">의견 상이</span>
              )}
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                gap: 8,
                marginBottom: 10,
              }}
            >
              {(r.turns || []).map((t, i) => (
                <PersonaTurnCard key={`${r.round}-${t.persona}-${i}`} turn={t} />
              ))}
            </div>
            {r.verdict && (
              <div
                style={{
                  padding: "8px 12px",
                  borderLeft: "3px solid var(--warn)",
                  background: "var(--surface)",
                  borderRadius: "var(--radius-sm)",
                  fontSize: 11,
                  color: "var(--ink-soft)",
                  lineHeight: 1.6,
                }}
              >
                <div
                  style={{
                    fontWeight: 700,
                    color: "var(--warn)",
                    marginBottom: 3,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  ⚖ 모더레이터 판정
                  {r.verdict.score != null && (
                    <span className="badge badge-warn">
                      제안 점수 {r.verdict.score}
                    </span>
                  )}
                </div>
                <div
                  dangerouslySetInnerHTML={{
                    __html: renderMarkdown(r.verdict.rationale || ""),
                  }}
                />
              </div>
            )}
          </div>
        ))}

        {record.final_rationale && (
          <div
            style={{
              padding: "10px 14px",
              background: "var(--warn-bg)",
              border: "1px solid var(--warn-border)",
              borderRadius: "var(--radius-sm)",
            }}
          >
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: "var(--warn)",
                marginBottom: 4,
              }}
            >
              최종 판정 근거
            </div>
            <div
              style={{
                fontSize: 12,
                color: "var(--ink)",
                lineHeight: 1.6,
              }}
              dangerouslySetInnerHTML={{ __html: finalHtml }}
            />
          </div>
        )}

        {record.ended_at && (
          <div
            style={{
              fontSize: 10,
              color: "var(--ink-subtle)",
              textAlign: "right",
              fontFamily: "var(--font-mono), monospace",
            }}
          >
            {record.ended_at}
          </div>
        )}
      </div>
    </details>
  );
}

export default memo(DebateRecordCard);
