// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import type { JSX } from "react";

/**
 * DebateScoreHistory — 평가 항목별 토론 라운드별 점수 변화를 timeline 으로 시각화.
 *
 * ItemCard 안에 임베드되어 사용된다. 가로 stepper 형태:
 *   초기 → R1 → R2 → ... → 판사(있을 때) → 최종
 *
 * - 페르소나 칩 (S=엄격 / N=중립 / L=관대) + 라운드 합의/투표 결과
 * - 라운드간 점수 변화 시 화살표 (↑/↓/=) 시각 표시
 * - 최종 단계는 ink black pill (Mastercard 시그니처)
 *
 * 디자인 토큰 사용 강제 — inline hex 금지. 모두 var(--token).
 */

export interface DebateRound {
  round: number;
  turns: Array<{ persona: "strict" | "neutral" | "loose"; score: number; argument?: string }>;
  verdict?: { score?: number | null; consensus?: boolean; rationale?: string };
}

interface Props {
  rounds: DebateRound[] | null | undefined;
  initialPositions?: Record<string, number> | null;
  finalScore: number;
  maxScore: number;
  judgeScore?: number | null;
}

type PersonaKey = "strict" | "neutral" | "loose";

// ★ 2026-05-07: 페르소나 라벨 통일 — lib/personas.ts 의 canonical 매핑 (품격/정확성/고객경험) 적용.
// 이전엔 엄격/중립/관대 (별도 매핑) 였는데 사용자 정책 = 모든 화면 품격/정확성/고객경험.
const PERSONA_META: Array<{ key: PersonaKey; short: string; ko: string; color: string; bg: string }> = [
  { key: "strict", short: "품", ko: "품격", color: "var(--persona-strict)", bg: "var(--persona-strict-bg)" },
  { key: "neutral", short: "정", ko: "정확성", color: "var(--persona-neutral)", bg: "var(--persona-neutral-bg)" },
  { key: "loose", short: "고", ko: "고객경험", color: "var(--persona-loose)", bg: "var(--persona-loose-bg)" },
];

const HEADING_FONT = "'Mark For MC', var(--font-sans), sans-serif";
const BODY_FONT = "var(--font-sans), sans-serif";

interface StageScores {
  strict?: number;
  neutral?: number;
  loose?: number;
}

interface Stage {
  kind: "initial" | "round" | "judge" | "final";
  label: string;
  ariaLabel: string;
  scores?: StageScores;
  consensus?: boolean;
  verdictScore?: number | null;
  rationale?: string;
  finalValue?: number;
}

function pickPersonaScore(scores: StageScores | undefined, key: PersonaKey): number | undefined {
  if (!scores) return undefined;
  const v = scores[key];
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

function deltaSymbol(curr: number | undefined, prev: number | undefined): { sym: string; tone: "up" | "down" | "eq" | "none" } {
  if (typeof curr !== "number" || typeof prev !== "number") return { sym: "", tone: "none" };
  if (curr > prev) return { sym: "↑", tone: "up" };
  if (curr < prev) return { sym: "↓", tone: "down" };
  return { sym: "=", tone: "eq" };
}

function deltaColor(tone: "up" | "down" | "eq" | "none"): string {
  if (tone === "up") return "var(--persona-loose)";
  if (tone === "down") return "var(--persona-strict)";
  if (tone === "eq") return "var(--ink)";
  return "transparent";
}

export default function DebateScoreHistory(props: Props): JSX.Element | null {
  const { rounds, initialPositions, finalScore, maxScore, judgeScore } = props;

  if (!rounds || rounds.length === 0) return null;

  // Build stages
  const stages: Stage[] = [];

  // Initial stage (only if we have initialPositions)
  if (initialPositions && Object.keys(initialPositions).length > 0) {
    const init: StageScores = {
      strict: typeof initialPositions.strict === "number" ? initialPositions.strict : undefined,
      neutral: typeof initialPositions.neutral === "number" ? initialPositions.neutral : undefined,
      loose: typeof initialPositions.loose === "number" ? initialPositions.loose : undefined,
    };
    stages.push({
      kind: "initial",
      label: "초기",
      ariaLabel: "초기 페르소나 점수",
      scores: init,
    });
  }

  // Round stages
  rounds.forEach((r) => {
    const scoresByPersona: StageScores = {};
    r.turns.forEach((t) => {
      if (t.persona === "strict" || t.persona === "neutral" || t.persona === "loose") {
        scoresByPersona[t.persona] = t.score;
      }
    });
    stages.push({
      kind: "round",
      label: `R${r.round}`,
      ariaLabel: `라운드 ${r.round} 페르소나 점수`,
      scores: scoresByPersona,
      consensus: r.verdict?.consensus,
      verdictScore: r.verdict?.score ?? null,
      rationale: r.verdict?.rationale,
    });
  });

  // Judge stage (optional)
  if (typeof judgeScore === "number" && Number.isFinite(judgeScore)) {
    stages.push({
      kind: "judge",
      label: "판사",
      ariaLabel: `판사 점수 ${judgeScore}점`,
      finalValue: judgeScore,
    });
  }

  // Final stage
  stages.push({
    kind: "final",
    label: "최종",
    ariaLabel: `최종 점수 ${finalScore} / ${maxScore}점`,
    finalValue: finalScore,
  });

  return (
    <section
      aria-label="토론 라운드별 점수 변화 타임라인"
      style={{
        background: "var(--surface)",
        border: "1px solid var(--surface-muted)",
        borderRadius: "var(--radius-sm)",
        padding: 16,
        marginTop: 12,
        marginBottom: 12,
        fontFamily: BODY_FONT,
      }}
    >
      {/* Eyebrow */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 12,
        }}
      >
        <span
          aria-hidden="true"
          style={{
            display: "inline-block",
            width: 6,
            height: 6,
            borderRadius: "var(--radius-pill)",
            background: "var(--accent)",
          }}
        />
        <span
          style={{
            fontFamily: HEADING_FONT,
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            color: "var(--ink-display)",
          }}
        >
          토론 진행
        </span>
        <span
          style={{
            fontSize: 11,
            color: "var(--ink)",
            opacity: 0.7,
            letterSpacing: "0.04em",
          }}
        >
          · 라운드 {rounds.length}개
        </span>
      </div>

      {/* Stepper */}
      <div
        role="list"
        aria-label="토론 단계 타임라인"
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "stretch",
          gap: 8,
        }}
      >
        {stages.map((stage, idx) => {
          const prev = idx > 0 ? stages[idx - 1] : undefined;
          const isFinal = stage.kind === "final";
          const isJudge = stage.kind === "judge";
          const isRoundOrInit = stage.kind === "round" || stage.kind === "initial";

          return (
            <div
              key={`${stage.kind}-${idx}`}
              role="listitem"
              style={{
                display: "flex",
                alignItems: "stretch",
                gap: 8,
              }}
            >
              {/* Connector arrow between stages */}
              {idx > 0 && (
                <span
                  aria-hidden="true"
                  style={{
                    alignSelf: "center",
                    fontSize: 14,
                    fontWeight: 600,
                    color: "var(--ink)",
                    opacity: 0.45,
                    letterSpacing: "-0.02em",
                  }}
                >
                  →
                </span>
              )}

              {/* Stage box */}
              <div
                aria-label={stage.ariaLabel}
                title={stage.ariaLabel}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                  minWidth: isFinal || isJudge ? 96 : 132,
                  padding: isFinal ? "10px 14px" : "8px 12px",
                  borderRadius: "var(--radius-sm)",
                  background: isFinal ? "var(--ink-cta)" : "var(--surface-muted)",
                  color: isFinal ? "var(--bg)" : "var(--ink)",
                  border: isJudge ? "1.5px solid var(--accent)" : "1px solid transparent",
                }}
              >
                {/* Stage label */}
                <div
                  style={{
                    fontFamily: HEADING_FONT,
                    fontSize: 10,
                    fontWeight: 700,
                    letterSpacing: "0.1em",
                    textTransform: "uppercase",
                    color: isFinal ? "var(--bg)" : "var(--ink-display)",
                    opacity: isFinal ? 0.85 : 1,
                  }}
                >
                  {stage.label}
                </div>

                {/* Persona chips for initial / round */}
                {isRoundOrInit && stage.scores && (
                  <div
                    style={{
                      display: "flex",
                      flexWrap: "wrap",
                      gap: 4,
                    }}
                  >
                    {PERSONA_META.map((p) => {
                      const curr = pickPersonaScore(stage.scores, p.key);
                      const prevScore = prev && (prev.kind === "round" || prev.kind === "initial") ? pickPersonaScore(prev.scores, p.key) : undefined;
                      const d = deltaSymbol(curr, prevScore);
                      if (typeof curr !== "number") {
                        return (
                          <span
                            key={p.key}
                            aria-label={`${p.ko} 페르소나 점수 없음`}
                            title={`${p.ko}: 점수 없음`}
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 3,
                              padding: "2px 7px",
                              borderRadius: "var(--radius-pill)",
                              fontSize: 11,
                              fontWeight: 600,
                              fontFamily: BODY_FONT,
                              color: "var(--ink)",
                              opacity: 0.4,
                              border: "1px dashed var(--ink)",
                              background: "transparent",
                            }}
                          >
                            {p.short}
                          </span>
                        );
                      }
                      return (
                        <span
                          key={p.key}
                          aria-label={`${p.ko} 페르소나 ${curr}점${d.sym ? ` ${d.tone === "up" ? "상승" : d.tone === "down" ? "하락" : "유지"}` : ""}`}
                          title={`${p.ko} (${p.short}): ${curr}점${d.sym && d.tone !== "none" ? ` (직전 대비 ${d.sym})` : ""}`}
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 3,
                            padding: "2px 7px",
                            borderRadius: "var(--radius-pill)",
                            fontSize: 11,
                            fontWeight: 700,
                            fontFamily: BODY_FONT,
                            color: p.color,
                            background: p.bg,
                            letterSpacing: "-0.01em",
                          }}
                        >
                          <span style={{ opacity: 0.7, fontWeight: 600 }}>{p.short}</span>
                          <span>{curr}</span>
                          {d.sym && d.tone !== "none" && (
                            <span
                              aria-hidden="true"
                              style={{
                                fontSize: 10,
                                fontWeight: 700,
                                color: deltaColor(d.tone),
                                marginLeft: 1,
                              }}
                            >
                              {d.sym}
                            </span>
                          )}
                        </span>
                      );
                    })}
                  </div>
                )}

                {/* Round verdict (consensus / vote) */}
                {stage.kind === "round" && (
                  <div
                    style={{
                      display: "flex",
                      flexWrap: "wrap",
                      alignItems: "center",
                      gap: 6,
                      fontSize: 10,
                      fontWeight: 600,
                      color: "var(--ink)",
                      letterSpacing: "0.02em",
                    }}
                  >
                    {stage.consensus === true ? (
                      <span
                        aria-label="라운드 합의 도달"
                        title="이 라운드에서 합의 도달"
                        style={{
                          padding: "1px 6px",
                          borderRadius: "var(--radius-pill)",
                          background: "var(--persona-loose-bg)",
                          color: "var(--persona-loose)",
                          fontSize: 10,
                          fontWeight: 700,
                          letterSpacing: "0.04em",
                          textTransform: "uppercase",
                        }}
                      >
                        합의
                      </span>
                    ) : stage.consensus === false ? (
                      <span
                        aria-label="라운드 합의 미도달"
                        title="이 라운드에서 합의 미도달"
                        style={{
                          padding: "1px 6px",
                          borderRadius: "var(--radius-pill)",
                          background: "var(--surface)",
                          color: "var(--ink)",
                          border: "1px solid var(--surface-muted)",
                          fontSize: 10,
                          fontWeight: 700,
                          letterSpacing: "0.04em",
                          textTransform: "uppercase",
                          opacity: 0.7,
                        }}
                      >
                        미합의
                      </span>
                    ) : null}
                    {typeof stage.verdictScore === "number" && Number.isFinite(stage.verdictScore) && (
                      <span
                        aria-label={`라운드 합의 점수 ${stage.verdictScore}점`}
                        title={`라운드 합의 점수: ${stage.verdictScore}`}
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          color: "var(--ink-display)",
                          letterSpacing: "-0.02em",
                        }}
                      >
                        → {stage.verdictScore}
                      </span>
                    )}
                  </div>
                )}

                {/* Judge / Final big number */}
                {(isJudge || isFinal) && typeof stage.finalValue === "number" && (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "baseline",
                      gap: 4,
                      fontFamily: HEADING_FONT,
                      letterSpacing: "-0.02em",
                    }}
                  >
                    <span
                      style={{
                        fontSize: isFinal ? 22 : 18,
                        fontWeight: 800,
                        color: isFinal ? "var(--bg)" : "var(--accent)",
                        lineHeight: 1,
                      }}
                    >
                      {stage.finalValue}
                    </span>
                    {isFinal && (
                      <span
                        style={{
                          fontSize: 11,
                          fontWeight: 600,
                          color: "var(--bg)",
                          opacity: 0.7,
                          letterSpacing: "0.02em",
                        }}
                      >
                        / {maxScore}
                      </span>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Legend */}
      <div
        aria-label="페르소나 범례"
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 10,
          marginTop: 12,
          paddingTop: 10,
          borderTop: "1px dashed var(--surface-muted)",
          fontSize: 10,
          color: "var(--ink)",
          opacity: 0.75,
          letterSpacing: "0.02em",
        }}
      >
        {PERSONA_META.map((p) => (
          <span
            key={p.key}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <span
              aria-hidden="true"
              style={{
                width: 8,
                height: 8,
                borderRadius: "var(--radius-pill)",
                background: p.color,
                display: "inline-block",
              }}
            />
            <span style={{ fontWeight: 700, color: p.color }}>{p.short}</span>
            <span>= {p.ko}</span>
          </span>
        ))}
      </div>
    </section>
  );
}
