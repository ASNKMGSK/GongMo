// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useState } from "react";

import { ITEM_NAMES, STT_MAX_SCORES } from "@/lib/items";

/**
 * PersonaExecutionDetails — V2 원본 라인 5222~5612 이식.
 *
 * 평가 sub-agent 노드 (greeting / listening_comm / language / needs / explanation /
 * proactiveness / work_accuracy / privacy) 의 드로어에서 "3-Persona 실행 상세" 섹션 렌더.
 *
 *   - Single-persona 에이전트 (greeting / listening_comm / privacy) 는 호출 측에서 제외.
 *   - 실행 중 + persona 미도착 → placeholder (3 persona spinner).
 *   - 완료 → 항목별 카드 (최종 판단/감점/인용 + persona breakdown 토글).
 */

type AnyRec = Record<string, unknown>;
type Dict = Record<string, AnyRec>;

const PERSONA_META = [
  {
    key: "strict",
    label: "Strict",
    ko: "엄격",
    bg: "#fee2e2",
    color: "#991b1b",
    border: "#fca5a5",
  },
  {
    key: "neutral",
    label: "Neutral",
    ko: "중립",
    bg: "#dbeafe",
    color: "#1e3a8a",
    border: "#93c5fd",
  },
  {
    key: "loose",
    label: "Loose",
    ko: "관대",
    bg: "#dcfce7",
    color: "#166534",
    border: "#86efac",
  },
];

const PERSONA_META_RUN = [
  {
    key: "strict",
    label: "품격",
    ko: "응대·경청",
    bg: "#fee2e2",
    color: "#991b1b",
    border: "#fca5a5",
  },
  {
    key: "neutral",
    label: "정확성",
    ko: "업무·팩트",
    bg: "#dbeafe",
    color: "#1e3a8a",
    border: "#93c5fd",
  },
  {
    key: "loose",
    label: "고객경험",
    ko: "적극·만족",
    bg: "#dcfce7",
    color: "#166534",
    border: "#86efac",
  },
];

interface PersonaVotes {
  strict?: number | null;
  neutral?: number | null;
  loose?: number | null;
}

interface PersonaDetail {
  score?: number;
  judgment?: string;
  summary?: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  deductions?: any[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  evidence?: any[];
}

// ItemLike 는 CategoryItem (lib/types.ts) 과 호환. V2 원본의 `item` alias 는 쓰지 않음 (item_number 만).
interface ItemLike {
  item_number?: number;
  score?: number;
  max_score?: number;
  persona_votes?: PersonaVotes;
  persona_details?: Record<string, PersonaDetail>;
  persona_merge_path?: string;
  persona_merge_rule?: string;
  persona_step_spread?: number;
  judge_reasoning?: string | null;
  // post-debate judge 결과 (디베이트 통과 항목에만 채워짐)
  judge_score?: number | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  judge_deductions?: any[] | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  judge_evidence?: any[] | null;
  mandatory_human_review?: boolean;
  judgment?: string;
  summary?: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  deductions?: any[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  evidence?: any[];
}

interface Props {
  nodeId: string;
  items: number[];
  itemScores: ItemLike[];
  state: string;
}

const SINGLE_PERSONA_AGENTS = new Set(["greeting", "listening_comm", "privacy"]);
const SUB_AGENT_IDS = new Set([
  "greeting",
  "listening_comm",
  "language",
  "needs",
  "explanation",
  "proactiveness",
  "work_accuracy",
  "privacy",
]);

export default function PersonaExecutionDetails({
  nodeId,
  items,
  itemScores,
  state,
}: Props) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const toggle = (num: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(num)) next.delete(num);
      else next.add(num);
      return next;
    });
  };

  if (!SUB_AGENT_IDS.has(nodeId)) return null;
  if (SINGLE_PERSONA_AGENTS.has(nodeId)) return null;

  const isRunning = state === "active" || state === "running" || state === "pending";

  const personaItems = items
    .map((num) => ({
      num,
      it: itemScores.find((it) => it.item_number === num),
    }))
    .filter(
      (x): x is { num: number; it: ItemLike } =>
        !!x.it && !!x.it.persona_votes && typeof x.it.persona_votes === "object",
    );

  if (personaItems.length === 0 && !isRunning) return null;

  // Placeholder — 실행 중 + 데이터 미도착
  if (personaItems.length === 0 && isRunning) {
    return (
      <div className="drawer-section">
        <div className="drawer-section-title">
          3-Persona 실행 상세
          <span
            style={{
              fontSize: 10,
              fontWeight: 500,
              color: "var(--ink-muted)",
              marginLeft: 6,
            }}
          >
            — 3 persona 병렬 호출 중
          </span>
        </div>
        {items.map((num) => (
          <div
            key={num}
            style={{
              marginBottom: 10,
              padding: 10,
              background: "var(--surface-muted)",
              border: "1px solid var(--border)",
              borderRadius: 6,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginBottom: 8,
                flexWrap: "wrap",
              }}
            >
              <span
                style={{ fontSize: 12, fontWeight: 800, color: "var(--ink)" }}
              >
                #{num} {ITEM_NAMES[num] || `Item ${num}`}
              </span>
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  padding: "1px 6px",
                  borderRadius: 8,
                  background: "#fef3c7",
                  color: "#92400e",
                }}
              >
                ⏳ 3 persona 병렬 호출 중
              </span>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(3, 1fr)",
                gap: 6,
              }}
            >
              {PERSONA_META_RUN.map((p) => (
                <div
                  key={p.key}
                  style={{
                    padding: "6px 8px",
                    borderRadius: 5,
                    background: p.bg,
                    border: `1px solid ${p.border}`,
                    position: "relative",
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      marginBottom: 3,
                    }}
                  >
                    <span
                      style={{
                        fontSize: 10,
                        fontWeight: 800,
                        color: p.color,
                        letterSpacing: 0.3,
                      }}
                    >
                      {p.label}
                    </span>
                    <span
                      style={{ fontSize: 9, color: p.color, opacity: 0.7 }}
                    >
                      {p.ko}
                    </span>
                  </div>
                  <div
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      color: p.color,
                      lineHeight: 1.1,
                    }}
                  >
                    대기
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="drawer-section">
      <div className="drawer-section-title">
        3-Persona 실행 상세
        <span
          style={{
            fontSize: 10,
            fontWeight: 500,
            color: "var(--ink-muted)",
            marginLeft: 6,
          }}
        >
          — 이 Sub Agent 안에서 돌아간 평가자 3명의 개별 점수
        </span>
      </div>

      {personaItems.map(({ num, it }) => {
        const pv = it.persona_votes || {};
        const pd = it.persona_details || {};
        const merged = Number(it.score ?? 0);
        const maxScore = it.max_score || STT_MAX_SCORES[num] || null;
        const spread = Number(it.persona_step_spread ?? 0);
        const mergePath = it.persona_merge_path || "stats";
        const mergeRule = it.persona_merge_rule || null;
        const isJudge = mergePath === "judge";
        const mhr = !!it.mandatory_human_review;
        const judgeReasoning = it.judge_reasoning || null;
        const itemName = ITEM_NAMES[num] || `Item ${num}`;
        const isExpanded = expanded.has(num);

        // Post-debate 판사 결과 (메인 평가).
        const judgeScore = it.judge_score;
        const judgeReasoningPost = it.judge_reasoning || null;
        const judgeDeductions = Array.isArray(it.judge_deductions)
          ? (it.judge_deductions as Array<{ reason: string; points: number }>)
          : [];
        const judgeEvidence = Array.isArray(it.judge_evidence)
          ? (it.judge_evidence as Array<{ speaker: string; quote: string }>)
          : [];
        const judgeAvailable = judgeScore != null && !!judgeReasoningPost;

        return (
          <div
            key={num}
            style={{
              marginBottom: 10,
              padding: 10,
              background: "var(--surface-muted)",
              border: "1px solid var(--border)",
              borderRadius: 6,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginBottom: 8,
                flexWrap: "wrap",
              }}
            >
              <span
                style={{ fontSize: 12, fontWeight: 800, color: "var(--ink)" }}
              >
                #{num} {itemName}
              </span>
              <span style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                최종:{" "}
                <b style={{ color: "var(--ink)", fontSize: 13 }}>
                  {merged}
                  {maxScore ? ` / ${maxScore}` : ""}
                </b>
              </span>
              {isJudge && (
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    padding: "1px 6px",
                    borderRadius: 8,
                    background: "#ede9fe",
                    color: "#5b21b6",
                    border: "1px solid rgba(124,58,237,0.3)",
                  }}
                >
                  🎭 판사 숙고
                </span>
              )}
              {/* spread / mode_majority 등 머지 메타 라벨은 사용자 정책상 비표시. */}
              {mhr && (
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 800,
                    padding: "1px 6px",
                    borderRadius: 8,
                    background: "#fee2e2",
                    color: "#991b1b",
                    marginLeft: "auto",
                  }}
                >
                  ⚠ 검수 필요
                </span>
              )}
            </div>

            {/* 🎭 판사 결정 박스는 상위 Evaluated Items 섹션에 이미 메인으로 표시됨 (NodeDrawer.tsx:1128).
                3-Persona 실행 상세 섹션은 페르소나별 개별 점수만 보여주는 게 사용자 요구사항 (2026-04-28).
                만장일치 케이스에서 "판사 결정 미도착" 라벨도 부적절 — 만장일치는 판사 호출 자체가 불필요.
                → 이 위치에서는 판사 결정 박스/라벨 모두 비표시. */}

            {isJudge && judgeReasoning && (
              <div
                style={{
                  marginTop: 4,
                  marginBottom: 8,
                  padding: "6px 10px",
                  background: "#ede9fe",
                  borderLeft: "3px solid #7c3aed",
                  borderRadius: 3,
                  fontSize: 11,
                  lineHeight: 1.5,
                  color: "#4c1d95",
                }}
              >
                <span style={{ fontWeight: 800, marginRight: 4 }}>
                  🎭 판사 숙고:
                </span>
                {judgeReasoning}
              </div>
            )}

            {Object.keys(pv).length > 0 && (
              <>
                <button
                  type="button"
                  onClick={() => toggle(num)}
                  style={{
                    width: "100%",
                    padding: "5px 8px",
                    background: isExpanded ? "#eef2ff" : "var(--surface)",
                    border: "1px solid var(--border)",
                    borderRadius: 4,
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    fontSize: 10.5,
                    fontWeight: 700,
                    color: isExpanded ? "#3730a3" : "var(--ink-muted)",
                    transition: "background 0.15s",
                  }}
                >
                  <span
                    style={{
                      display: "inline-block",
                      transition: "transform 0.15s",
                      transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)",
                    }}
                  >
                    ▸
                  </span>
                  <span>
                    각 평가자별 판정 상세 (품격 · 정확성 · 고객경험)
                  </span>
                  <span
                    style={{
                      marginLeft: "auto",
                      fontSize: 9,
                      fontWeight: 600,
                      color: "var(--ink-muted)",
                    }}
                  >
                    {Object.keys(pv).length}/3 평가자
                  </span>
                </button>

                {isExpanded && (
                  <div style={{ marginTop: 8 }}>
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "repeat(3, 1fr)",
                        gap: 6,
                        marginBottom: 8,
                      }}
                    >
                      {PERSONA_META.map((p) => {
                        const rawVote = (pv as Dict)[p.key];
                        const hasVote =
                          rawVote !== undefined && rawVote !== null;
                        const score = hasVote ? Number(rawVote) : null;
                        const isMerged = hasVote && score === merged;
                        return (
                          <div
                            key={p.key}
                            style={{
                              padding: "6px 8px",
                              borderRadius: 5,
                              background: hasVote ? p.bg : "var(--surface)",
                              border: `1px solid ${hasVote ? p.border : "var(--border)"}`,
                              opacity: hasVote ? 1 : 0.55,
                              outline: isMerged
                                ? `2px solid ${p.color}`
                                : "none",
                              outlineOffset: isMerged ? 1 : 0,
                            }}
                          >
                            <div
                              style={{
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "space-between",
                                marginBottom: 3,
                              }}
                            >
                              <span
                                style={{
                                  fontSize: 10,
                                  fontWeight: 800,
                                  color: p.color,
                                  letterSpacing: 0.3,
                                }}
                              >
                                {p.label}
                              </span>
                              <span
                                style={{
                                  fontSize: 9,
                                  color: p.color,
                                  opacity: 0.7,
                                }}
                              >
                                {p.ko}
                              </span>
                            </div>
                            <div
                              style={{
                                fontSize: 14,
                                fontWeight: 800,
                                color: hasVote ? p.color : "#9ca3af",
                                lineHeight: 1.1,
                              }}
                            >
                              {hasVote ? (
                                <>
                                  {score}
                                  {maxScore ? (
                                    <span
                                      style={{
                                        fontSize: 10,
                                        fontWeight: 500,
                                        opacity: 0.7,
                                      }}
                                    >
                                      {" "}
                                      / {maxScore}
                                    </span>
                                  ) : null}
                                  {isMerged && (
                                    <span
                                      style={{
                                        fontSize: 8,
                                        fontWeight: 700,
                                        marginLeft: 4,
                                        padding: "1px 4px",
                                        borderRadius: 6,
                                        background: "white",
                                        color: p.color,
                                      }}
                                    >
                                      ✓ 채택
                                    </span>
                                  )}
                                </>
                              ) : (
                                <span
                                  style={{ fontSize: 10, fontWeight: 600 }}
                                >
                                  호출 실패
                                </span>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>

                    {Object.keys(pd).length > 0 &&
                      PERSONA_META.filter((p) => (pd as Dict)[p.key]).map(
                        (p) => {
                          const det = ((pd as unknown) as Record<
                            string,
                            PersonaDetail
                          >)[p.key] || {};
                          const judg = det.judgment || det.summary || "";
                          const deducts = Array.isArray(det.deductions)
                            ? det.deductions
                            : [];
                          const evs = Array.isArray(det.evidence)
                            ? det.evidence
                            : [];
                          return (
                            <div
                              key={p.key}
                              style={{
                                marginBottom: 6,
                                padding: "6px 8px",
                                background: "var(--surface)",
                                border: `1px solid ${p.border}`,
                                borderLeft: `3px solid ${p.color}`,
                                borderRadius: 4,
                                fontSize: 10.5,
                                lineHeight: 1.5,
                              }}
                            >
                              <div
                                style={{
                                  display: "flex",
                                  alignItems: "center",
                                  gap: 6,
                                  marginBottom: 3,
                                }}
                              >
                                <span
                                  style={{
                                    fontSize: 9,
                                    fontWeight: 800,
                                    color: p.color,
                                    letterSpacing: 0.3,
                                  }}
                                >
                                  {p.label}
                                </span>
                                <span
                                  style={{
                                    fontSize: 9,
                                    color: p.color,
                                    opacity: 0.65,
                                  }}
                                >
                                  {p.ko}
                                </span>
                                <span
                                  style={{
                                    fontSize: 9,
                                    color: "var(--ink-muted)",
                                    marginLeft: "auto",
                                  }}
                                >
                                  점수 {det.score ?? "?"}
                                  {maxScore ? ` / ${maxScore}` : ""}
                                </span>
                              </div>
                              {judg && (
                                <div
                                  style={{
                                    color: "var(--ink)",
                                    marginBottom: deducts.length ? 4 : 0,
                                  }}
                                >
                                  <b style={{ color: p.color }}>판단:</b>{" "}
                                  {judg}
                                </div>
                              )}
                              {deducts.length > 0 && (
                                <div
                                  style={{
                                    marginTop: 2,
                                    marginBottom: evs.length ? 4 : 0,
                                  }}
                                >
                                  <b style={{ color: p.color, fontSize: 10 }}>
                                    감점 근거:
                                  </b>
                                  <ul
                                    style={{
                                      margin: "2px 0 0 0",
                                      paddingLeft: 16,
                                      listStyle: "disc",
                                    }}
                                  >
                                    {deducts.map(
                                      (
                                        d: {
                                          points?: number;
                                          points_lost?: number;
                                          reason?: string;
                                        },
                                        i: number,
                                      ) => (
                                        <li
                                          key={i}
                                          style={{
                                            color: "var(--ink-soft)",
                                            marginBottom: 1,
                                          }}
                                        >
                                          <span
                                            style={{
                                              fontWeight: 700,
                                              color: "#991b1b",
                                            }}
                                          >
                                            -
                                            {d.points ||
                                              d.points_lost ||
                                              0}
                                            점
                                          </span>
                                          {" · "}
                                          {d.reason || ""}
                                        </li>
                                      ),
                                    )}
                                  </ul>
                                </div>
                              )}
                              {evs.length > 0 && (
                                <div style={{ marginTop: 2 }}>
                                  <b style={{ color: p.color, fontSize: 10 }}>
                                    인용:
                                  </b>
                                  <ul
                                    style={{
                                      margin: "2px 0 0 0",
                                      paddingLeft: 16,
                                      listStyle: '"— "',
                                    }}
                                  >
                                    {evs.map(
                                      (
                                        e: {
                                          speaker?: string;
                                          quote?: string;
                                          text?: string;
                                        },
                                        i: number,
                                      ) => (
                                        <li
                                          key={i}
                                          style={{
                                            color: "var(--ink-muted)",
                                            marginBottom: 1,
                                            fontStyle: "italic",
                                          }}
                                        >
                                          {e.speaker && (
                                            <span
                                              style={{
                                                fontWeight: 700,
                                                fontStyle: "normal",
                                                marginRight: 3,
                                              }}
                                            >
                                              [{e.speaker}]
                                            </span>
                                          )}
                                          &ldquo;{e.quote || e.text || ""}&rdquo;
                                        </li>
                                      ),
                                    )}
                                  </ul>
                                </div>
                              )}
                            </div>
                          );
                        },
                      )}
                  </div>
                )}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
