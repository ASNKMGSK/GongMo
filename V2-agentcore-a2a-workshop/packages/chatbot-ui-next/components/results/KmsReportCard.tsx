// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * KmsReportCard — KMS 노드 평가 결과를 *별도 보고서 카드* 로 표시.
 *
 * 통합 보고서 (8 sub-agent + KSQI) 와 분리된 *독립 섹션*.
 * lastResult.kms_evaluation 을 prop 으로 받아 렌더링.
 */

import { useState } from "react";

interface TabEvaluation {
  branch?: string;
  satisfied_keywords?: string[];
  missing_keywords?: string[];
  satisfied_statements?: string[];
  missing_statements?: string[];
  evidence?: string[];
}

interface IntentEvaluation {
  /** 0~10 점 (백엔드 _evaluate_intent 산정). */
  score?: number | null;
  /** 점수 산정 근거 (LLM reasoning). */
  reasoning?: string;
  applied_branches?: string[];
  tab_evaluations?: TabEvaluation[];
  summary?: string;
  _error?: string;
}

export interface KmsEvaluation {
  available?: boolean;
  reason?: string;
  /** Step 1 인텐트 분류 모드 — "llm" (Sonnet 4.6) | "linear_rag" (Tri-Graph) */
  intent_mode?: "llm" | "linear_rag" | string;
  detected_intents?: string[];
  classification_rationale?: string;
  evaluations_by_intent?: Record<string, IntentEvaluation>;
  used_tabs?: string[];
  /** LinearRAG 모드 일 때만 — 인텐트별 ppr_score 합산 */
  linear_rag_scores?: Record<string, number>;
}

interface Props {
  kmsEvaluation: KmsEvaluation | null | undefined;
}

/** 0~10 점수 → 색상 매핑 (10 진녹 / 8~9 녹 / 6~7 황 / 4~5 주 / 0~3 적). */
function scoreBg(score: number): string {
  if (score >= 9.5) return "#1f7a3e";
  if (score >= 8) return "#3d8c5f";
  if (score >= 6) return "#c89834";
  if (score >= 4) return "#d97a3a";
  return "#b03a2e";
}

export function KmsReportCard({ kmsEvaluation }: Props) {
  // 사용자 요청 (2026-05-06): 모든 검출 인텐트 카드를 처음부터 펼친 상태로.
  // 단일 expandedIntent 에서 Set 으로 변경 — 사용자가 개별 토글로 접을 수 있음.
  // 첫 렌더 시 detected_intents 모두 expanded 상태로 초기화.
  const [collapsedIntents, setCollapsedIntents] = useState<Set<string>>(new Set());
  const toggleIntent = (intent: string) => {
    setCollapsedIntents((prev) => {
      const next = new Set(prev);
      if (next.has(intent)) next.delete(intent);
      else next.add(intent);
      return next;
    });
  };

  if (!kmsEvaluation) {
    return null;
  }

  if (kmsEvaluation.available === false) {
    return (
      <div
        className="rounded-[var(--radius-lg)] border border-dashed border-[var(--border)] bg-[var(--surface-muted)] px-4 py-3"
        style={{ fontSize: 13 }}
      >
        <div className="font-semibold text-[var(--ink)]">KMS 평가 보고서</div>
        <div className="mt-1 text-[var(--ink-muted)]">
          비활성 또는 평가 불가 — {kmsEvaluation.reason || "사유 미상"}
        </div>
      </div>
    );
  }

  const detected = kmsEvaluation.detected_intents || [];
  const evalsByIntent = kmsEvaluation.evaluations_by_intent || {};
  const intentEntries = Object.entries(evalsByIntent);

  // 종합 점수 — 검출된 인텐트 score 의 평균 (10점 만점)
  const scores = intentEntries
    .map(([, ev]) => ev.score)
    .filter((s): s is number => typeof s === "number");
  const avgScore =
    scores.length > 0 ? scores.reduce((a, b) => a + b, 0) / scores.length : null;

  return (
    <section
      className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)]"
      style={{ overflow: "hidden" }}
    >
      {/* 헤더 */}
      <div
        style={{
          background: "linear-gradient(180deg, #fff8ec 0%, #fff3dc 100%)",
          borderBottom: "1px solid var(--border)",
          padding: "12px 16px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14, flex: 1 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 700, color: "#8c6e2d", display: "flex", alignItems: "center", gap: 8 }}>
              KMS 평가 보고서
              {kmsEvaluation.intent_mode && (
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    padding: "2px 8px",
                    borderRadius: 999,
                    background: kmsEvaluation.intent_mode === "linear_rag" ? "#5b21b6" : "#0e7490",
                    color: "white",
                    letterSpacing: "0.04em",
                  }}
                  title={
                    kmsEvaluation.intent_mode === "linear_rag"
                      ? "LinearRAG (Tri-Graph) — F1=0.435 실험적 모드"
                      : "Sonnet 4.6 LLM Tool Use — F1=0.933 권장"
                  }
                >
                  {kmsEvaluation.intent_mode === "linear_rag" ? "🕸️ LinearRAG" : "🧠 LLM"}
                </span>
              )}
            </div>
            <div style={{ fontSize: 11, color: "#a08348", marginTop: 2 }}>
              인텐트별 KMS 데이터 기반 LLM 평가 (10점 만점, 통합 보고서와 별도)
            </div>
          </div>
          {avgScore != null && (
            <div
              style={{
                padding: "6px 14px",
                background: scoreBg(avgScore),
                color: "white",
                borderRadius: 10,
                textAlign: "center",
                minWidth: 76,
              }}
              title={`인텐트 ${scores.length}개 평균`}
            >
              <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", opacity: 0.85 }}>
                종합
              </div>
              <div
                style={{
                  fontSize: 18,
                  fontWeight: 800,
                  lineHeight: 1.1,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {avgScore.toFixed(1)}
              </div>
              <div style={{ fontSize: 9, opacity: 0.85 }}>/ 10</div>
            </div>
          )}
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
          {detected.length === 0 ? (
            <span
              style={{
                padding: "4px 10px",
                background: "#f3f0e6",
                color: "#7a7059",
                fontSize: 11,
                fontWeight: 600,
                borderRadius: 6,
                letterSpacing: "0.02em",
              }}
            >
              검출 인텐트: 없음
            </span>
          ) : (
            detected.map((intent) => (
              <span
                key={intent}
                style={{
                  padding: "4px 10px",
                  background: "#c96442",
                  color: "white",
                  fontSize: 11,
                  fontWeight: 700,
                  borderRadius: 6,
                  letterSpacing: "0.02em",
                }}
              >
                {intent}
              </span>
            ))
          )}
        </div>
      </div>

      {/* 인텐트 분류 근거 — 강조 블록 (왜 이 인텐트로 분류됐는지) */}
      {kmsEvaluation.classification_rationale && (
        <div
          style={{
            margin: "12px 16px",
            padding: "12px 14px",
            background: "linear-gradient(180deg, #f5efff 0%, #efe6ff 100%)",
            border: "1px solid rgba(124, 58, 237, 0.25)",
            borderLeft: "3px solid #7c3aed",
            borderRadius: 8,
            fontSize: 12.5,
            color: "var(--ink)",
            lineHeight: 1.6,
          }}
        >
          <div
            style={{
              fontSize: 10.5,
              fontWeight: 700,
              color: "#6d28d9",
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              marginBottom: 6,
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            🧭 인텐트 분류 근거
            <span style={{ fontSize: 9.5, fontWeight: 500, color: "#8b6cb8", textTransform: "none", letterSpacing: 0 }}>
              — Step 1 LLM 이 transcript 를 분석해 검출 / 비검출 결정한 사유
            </span>
          </div>
          {kmsEvaluation.classification_rationale}
        </div>
      )}

      {/* 인텐트별 평가 — 외부구매 / 처리 X 일 때 빈 메시지 */}
      {detected.length === 0 && (
        <div style={{ padding: "16px", fontSize: 12, color: "var(--ink-muted)" }}>
          처리된 인텐트 없음 (외부 구매처 안내만, 또는 약속만 있고 실제 접수 X).
        </div>
      )}

      {/* 인텐트별 평가 카드들 */}
      {intentEntries.length > 0 && (
        <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
          {intentEntries.map(([intent, evalData]) => {
            const isOpen = !collapsedIntents.has(intent);
            const tabEvals = evalData.tab_evaluations || [];
            const totalKeywords =
              tabEvals.reduce(
                (sum, t) =>
                  sum + (t.satisfied_keywords?.length || 0) + (t.missing_keywords?.length || 0),
                0,
              );
            const satisfiedKw = tabEvals.reduce(
              (sum, t) => sum + (t.satisfied_keywords?.length || 0),
              0,
            );
            const totalStatements =
              tabEvals.reduce(
                (sum, t) =>
                  sum +
                  (t.satisfied_statements?.length || 0) +
                  (t.missing_statements?.length || 0),
                0,
              );
            const satisfiedSt = tabEvals.reduce(
              (sum, t) => sum + (t.satisfied_statements?.length || 0),
              0,
            );

            return (
              <div
                key={intent}
                style={{
                  border: "1px solid var(--border)",
                  borderRadius: 10,
                  overflow: "hidden",
                  background: "var(--surface)",
                }}
              >
                <button
                  type="button"
                  onClick={() => toggleIntent(intent)}
                  style={{
                    width: "100%",
                    padding: "10px 14px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    background: isOpen ? "#fcf9f0" : "transparent",
                    border: "none",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>
                      [{intent}]
                    </span>
                    {typeof evalData.score === "number" && (
                      <span
                        style={{
                          padding: "2px 10px",
                          background: scoreBg(evalData.score),
                          color: "white",
                          fontSize: 12,
                          fontWeight: 700,
                          borderRadius: 999,
                          letterSpacing: "0.02em",
                          fontVariantNumeric: "tabular-nums",
                        }}
                        title="0~10점"
                      >
                        {evalData.score.toFixed(1)} / 10
                      </span>
                    )}
                    <span style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                      세부사항 {tabEvals.length}건 · 키워드 {satisfiedKw}/{totalKeywords} · 안내{" "}
                      {satisfiedSt}/{totalStatements}
                    </span>
                  </div>
                  <span style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                    {isOpen ? "▾" : "▸"}
                  </span>
                </button>

                {isOpen && (
                  <div style={{ padding: "10px 14px", borderTop: "1px solid var(--border)" }}>
                    {evalData._error && (
                      <div
                        style={{
                          padding: "6px 10px",
                          background: "#fdf6f4",
                          color: "#b03a2e",
                          fontSize: 11,
                          borderRadius: 6,
                          marginBottom: 8,
                        }}
                      >
                        오류: {evalData._error}
                      </div>
                    )}
                    {/* 점수 산정 근거 (LLM reasoning) — 가장 위에 강조 표시 */}
                    {evalData.reasoning && (
                      <div
                        style={{
                          padding: "10px 12px",
                          background: "#fafaf6",
                          border: "1px solid var(--border)",
                          borderLeft: `3px solid ${scoreBg(evalData.score ?? 0)}`,
                          borderRadius: 6,
                          fontSize: 12,
                          lineHeight: 1.55,
                          color: "var(--ink)",
                          marginBottom: 10,
                        }}
                      >
                        <div
                          style={{
                            fontSize: 10,
                            fontWeight: 700,
                            color: "var(--ink-muted)",
                            letterSpacing: "0.06em",
                            textTransform: "uppercase",
                            marginBottom: 4,
                          }}
                        >
                          점수 산정 근거
                        </div>
                        {evalData.reasoning}
                      </div>
                    )}
                    {evalData.summary && (
                      <div
                        style={{
                          fontSize: 12,
                          color: "var(--ink-muted)",
                          marginBottom: 10,
                          fontStyle: "italic",
                        }}
                      >
                        {evalData.summary}
                      </div>
                    )}
                    {tabEvals.map((tab, idx) => (
                      <TabBranchView key={idx} tab={tab} />
                    ))}
                    {tabEvals.length === 0 && !evalData._error && (
                      <div style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                        평가 데이터 없음 (탭 부재 또는 적용 행 없음).
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* used_tabs (footer) */}
      {kmsEvaluation.used_tabs && kmsEvaluation.used_tabs.length > 0 && (
        <div
          style={{
            padding: "8px 16px",
            background: "#fafaf6",
            borderTop: "1px solid var(--border)",
            fontSize: 10,
            color: "var(--ink-muted)",
          }}
        >
          참조 탭: {kmsEvaluation.used_tabs.join(" / ")}
        </div>
      )}
    </section>
  );
}

function TabBranchView({ tab }: { tab: TabEvaluation }) {
  return (
    <div style={{ marginBottom: 10, paddingBottom: 8, borderBottom: "1px dashed var(--border)" }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink)", marginBottom: 6 }}>
        {tab.branch || "(세부사항 미지정)"}
      </div>

      {/* 키워드 */}
      <ItemList
        label="필수 키워드"
        satisfied={tab.satisfied_keywords}
        missing={tab.missing_keywords}
      />

      {/* 안내 */}
      <ItemList
        label="필수 안내"
        satisfied={tab.satisfied_statements}
        missing={tab.missing_statements}
      />

      {/* Evidence */}
      {tab.evidence && tab.evidence.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <div style={{ fontSize: 10, color: "var(--ink-muted)", marginBottom: 4 }}>근거 발화:</div>
          <ul style={{ margin: 0, paddingLeft: 16, fontSize: 11, color: "var(--ink)" }}>
            {tab.evidence.map((ev, i) => (
              <li key={i} style={{ marginBottom: 2 }}>
                &ldquo;{ev}&rdquo;
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ItemList({
  label,
  satisfied,
  missing,
}: {
  label: string;
  satisfied?: string[];
  missing?: string[];
}) {
  const sat = satisfied || [];
  const miss = missing || [];
  if (sat.length === 0 && miss.length === 0) return null;
  return (
    <div style={{ marginBottom: 4, fontSize: 11 }}>
      <span style={{ fontWeight: 600, color: "var(--ink-muted)" }}>{label}:</span>{" "}
      {sat.map((kw, i) => (
        <span
          key={`s-${i}`}
          style={{
            display: "inline-block",
            padding: "1px 6px",
            margin: "1px 3px 1px 0",
            background: "#e8f5ed",
            color: "#2e7d4f",
            borderRadius: 4,
            fontSize: 10,
          }}
        >
          ✓ {kw}
        </span>
      ))}
      {miss.map((kw, i) => (
        <span
          key={`m-${i}`}
          style={{
            display: "inline-block",
            padding: "1px 6px",
            margin: "1px 3px 1px 0",
            background: "#fdf6f4",
            color: "#b03a2e",
            borderRadius: 4,
            fontSize: 10,
          }}
        >
          ✗ {kw}
        </span>
      ))}
    </div>
  );
}
