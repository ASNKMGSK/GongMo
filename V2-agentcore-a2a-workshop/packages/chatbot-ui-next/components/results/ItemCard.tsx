// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { memo, useCallback, useMemo, useState } from "react";

import JudgePanel from "@/components/JudgePanel";
import PersonaBarChart from "@/components/PersonaBarChart";
import { confirmReview, upsertHumanReview } from "@/lib/api";
import { scoreColor } from "@/lib/items";
import { useToast } from "@/lib/toast";
import type { DeductionEntry, EvidenceEntry, PersonaVotes } from "@/lib/types";

/**
 * ItemCard — V2 HTML 5815~6232 이식.
 *   Props 는 V2 원본과 동일. HITL 수정 모드 / force_t3 / judge 경로 / RAG 인용 / evidence 8개 제한.
 *   편집(confirm/revert) 기능은 ReviewItemCard 가 담당 — 이 카드는 결과 표시 전용.
 */

export interface ItemCardGtRow {
  gt_score?: number | null;
  note?: string | null;
  max_score?: number | null;
  excluded?: boolean;
}

export interface ItemCardProps {
  num: number;
  name: string;
  score: number | null | undefined;
  maxScore: number;
  deductions: DeductionEntry[];
  evidence: Array<EvidenceEntry | string>;
  strengths?: Array<string | { description?: string }>;
  improvements?: Array<string | { description?: string }>;
  coaching?: Array<{
    priority?: string;
    area?: string;
    title?: string;
    suggestion?: string;
    description?: string;
  }>;
  judgment?: string | null;
  summary?: string | null;
  personaVotes?: PersonaVotes | null;
  personaMergePath?: string | null;
  personaMergeRule?: string | null;
  personaStepSpread?: number | null;
  judgeReasoning?: string | null;
  personaDetails?: Record<string, { judgment?: string }> | null;
  personaLabelMap?: Record<string, string> | null;
  /** Post-debate judge LLM (AG2 토론 종료 후 transcript 보고 확정) — 우선 본문 표시. */
  postDebateJudgeScore?: number | null;
  postDebateJudgeReasoning?: string | null;
  postDebateJudgeDeductions?: Array<{ reason: string; points: number }> | null;
  postDebateJudgeEvidence?: Array<{ speaker: string; quote: string }> | null;
  mandatoryHumanReview?: boolean;
  forceT3?: boolean;
  aiConfidence?: number | null;
  error?: { error_type?: string; error_message?: string } | null;
  defaultOpen?: boolean;
  expandVersion?: number;
  expandAllTarget?: boolean | null;
  /** GT 비교 (Dev6 manualEval 또는 서버 `gt_comparison.items` 매핑). 없으면 표시 안 함. */
  gt?: ItemCardGtRow | null;
  /** HITL 편집 모드 — ResultsTab 상단 토글이 true 일 때 사람 점수/메모 입력 UI 노출. */
  editMode?: boolean;
  /** 현재 상담 ID — 편집 저장 시 upsertHumanReview 에 필요. */
  consultationId?: string;
  /** 이미 확정/임시저장된 사람 점수 (세션 내 저장 또는 서버 응답). */
  humanSavedScore?: number | null;
  humanConfirmed?: boolean;
  /** 사람 점수 저장 성공 시 부모가 humanSavedMap 등을 갱신. */
  onHumanSaved?: (itemNumber: number, humanScore: number, confirmed: boolean) => void;
}

function normalizeEv(ev: EvidenceEntry | string): {
  speaker: string;
  quote: string;
  turn: string;
  isRag: boolean;
} {
  if (typeof ev === "string") {
    return { speaker: "", quote: ev, turn: "", isRag: false };
  }
  const sp = ev.speaker || ev.role || "";
  const isRag = sp === "업무지식" || sp === "knowledge" || sp === "rag";
  const quote = ev.quote || ev.text || "";
  const turn = (ev as { turn?: string | number }).turn != null ? String((ev as { turn?: string | number }).turn) : "";
  const speakerLabel = sp === "agent" ? "상담사" : sp === "customer" ? "고객" : isRag ? "📚 업무지식" : sp;
  return { speaker: speakerLabel, quote, turn, isRag };
}

function ItemCard({
  num,
  name,
  score,
  maxScore,
  deductions,
  evidence,
  strengths = [],
  improvements = [],
  coaching = [],
  judgment,
  summary,
  personaVotes,
  personaMergePath,
  personaMergeRule,
  personaStepSpread,
  judgeReasoning,
  personaDetails,
  personaLabelMap,
  postDebateJudgeScore,
  postDebateJudgeReasoning,
  postDebateJudgeDeductions,
  postDebateJudgeEvidence,
  mandatoryHumanReview,
  forceT3,
  aiConfidence,
  error,
  defaultOpen,
  expandVersion,
  expandAllTarget,
  gt,
  editMode,
  consultationId,
  humanSavedScore,
  humanConfirmed,
  onHumanSaved,
}: ItemCardProps) {
  const toast = useToast();
  const hasPersona =
    !!personaVotes &&
    typeof personaVotes === "object" &&
    (personaVotes.strict !== undefined ||
      personaVotes.neutral !== undefined ||
      personaVotes.loose !== undefined);
  // 진짜 ensemble 여부 — personaVotes 에 2개 이상 키가 있을 때만 페르소나 인격 라벨 사용.
  // single mode (페르소나 1명 호출 노드) 는 페르소나 인격 빼고 일반 "LLM 판정 사유" 라벨.
  const personaCount = personaVotes
    ? (["strict", "neutral", "loose"] as const).filter(
        (k) => (personaVotes as Record<string, unknown>)[k] != null,
      ).length
    : 0;
  const isEnsemble = personaCount >= 2;
  const isJudgePath = isEnsemble && personaMergePath === "judge";

  // Post-debate 판사 의견이 있으면 deductions / evidence 도 판사 것을 우선 표시.
  // 라벨/색상도 분기하여 어느 출처인지 명확히 표시.
  const usePostDebateJudge = !!postDebateJudgeReasoning;
  const effectiveDeductions = useMemo(() => {
    if (postDebateJudgeDeductions && postDebateJudgeDeductions.length > 0) {
      return postDebateJudgeDeductions.map((d) => ({
        reason: d.reason,
        points_lost: d.points,
      }));
    }
    return deductions;
  }, [postDebateJudgeDeductions, deductions]);
  const effectiveEvidence = useMemo(() => {
    if (postDebateJudgeEvidence && postDebateJudgeEvidence.length > 0) {
      return postDebateJudgeEvidence.map((e) => ({
        speaker: e.speaker,
        quote: e.quote,
      }));
    }
    return evidence;
  }, [postDebateJudgeEvidence, evidence]);

  const hasDetail =
    effectiveDeductions.length > 0 ||
    strengths.length > 0 ||
    improvements.length > 0 ||
    coaching.length > 0 ||
    effectiveEvidence.length > 0 ||
    !!error ||
    hasPersona;

  const [open, setOpen] = useState(defaultOpen || !!error);
  const [judgeOpen, setJudgeOpen] = useState(false);
  // ── HITL 편집 모드 state ─────────────────────────────────
  const [humanScoreInput, setHumanScoreInput] = useState<string>(
    humanSavedScore != null ? String(humanSavedScore) : "",
  );
  const [humanNote, setHumanNote] = useState<string>("");
  const [savingState, setSavingState] = useState<"" | "draft" | "confirm">("");

  const submitHumanReview = useCallback(
    async (confirm: boolean) => {
      if (!consultationId) {
        toast.error("저장 실패", {
          description: "상담 ID 가 없습니다 (파일 첨부 시 sample_id 필요)",
        });
        return;
      }
      const parsed = humanScoreInput === "" ? null : Number(humanScoreInput);
      if (parsed == null || Number.isNaN(parsed)) {
        toast.error("입력 오류", { description: "점수를 숫자로 입력하세요" });
        return;
      }
      if (parsed < 0 || parsed > maxScore) {
        toast.error("점수 범위 오류", {
          description: `0 ~ ${maxScore} 범위의 점수를 입력하세요`,
        });
        return;
      }
      setSavingState(confirm ? "confirm" : "draft");
      try {
        const evidenceLines = (evidence || [])
          .map((ev) =>
            typeof ev === "string" ? ev : ev.quote || ev.text || "",
          )
          .filter(Boolean);
        const upsert = await upsertHumanReview({
          consultation_id: consultationId,
          item_number: Number(num),
          ai_score: Number(score) || 0,
          human_score: parsed,
          ai_evidence: evidenceLines,
          ai_judgment: String(judgment || summary || ""),
          human_note: humanNote,
          ai_confidence: aiConfidence != null ? Number(aiConfidence) : null,
          reviewer_id: "ui-user",
          reviewer_role: "senior",
          force_t3: !!forceT3,
        });
        if (confirm && upsert?.id != null) {
          await confirmReview(upsert.id, {
            reviewer_id: "ui-user",
            reviewer_role: "senior",
          });
        }
        toast.success(confirm ? "확정되었습니다" : "임시저장되었습니다", {
          description: `#${num} · 사람 점수 ${parsed}점`,
          duration: 3000,
        });
        onHumanSaved?.(Number(num), parsed, confirm);
      } catch (err) {
        toast.error("저장 실패", {
          description: err instanceof Error ? err.message : String(err),
        });
      } finally {
        setSavingState("");
      }
    },
    [
      consultationId,
      humanScoreInput,
      humanNote,
      maxScore,
      num,
      score,
      judgment,
      summary,
      evidence,
      aiConfidence,
      forceT3,
      toast,
      onHumanSaved,
    ],
  );
  // expandVersion 이 바뀌면 상위 "모두 펼치기/접기" 버튼이 눌린 것 → open 을 target 값으로 동기화.
  // React 권장 패턴: 직전 prop 을 state 로 기억하고 render 중 비교. setState-in-effect 회피.
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

  const sc = score ?? 0;
  const scColor = error ? "var(--danger)" : scoreColor(sc, maxScore);
  const borderLeft = forceT3 ? "var(--danger)" : scColor;

  const gtScore = gt?.gt_score;
  const gtDiff =
    gtScore != null && typeof sc === "number" ? sc - Number(gtScore) : null;
  const gtColor =
    gtDiff == null
      ? "var(--ink-subtle)"
      : gtDiff === 0
        ? "var(--success)"
        : gtDiff > 0
          ? "var(--warn)"
          : "var(--info)";

  return (
    <div
      className="card"
      style={{
        borderLeft: `3px solid ${borderLeft}`,
        background: error ? "var(--danger-bg)" : "var(--surface)",
        marginBottom: 8,
        overflow: "hidden",
      }}
    >
      {/* 헤더 */}
      <div
        onClick={() => hasDetail && setOpen(!open)}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 14px",
          cursor: hasDetail ? "pointer" : "default",
          background: open ? "var(--surface-hover)" : "transparent",
          transition: "background 150ms var(--ease)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            flex: 1,
            minWidth: 0,
          }}
        >
          {forceT3 && (
            <span
              className="badge badge-danger"
              title="이 항목은 사람 검수가 필수입니다"
            >
              ● 사람 검수 필수
            </span>
          )}
          <code className="kbd">#{num}</code>
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>
            {name}
          </span>
          {humanConfirmed && humanSavedScore != null && (
            <span
              className="badge badge-success"
              title="사람 QA 가 확정한 점수입니다"
            >
              ✓ 사람 확정 ({humanSavedScore}점)
            </span>
          )}
          {error && (
            <span className="badge badge-danger" title={error.error_message}>
              ⚠ 평가 실패
            </span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {mandatoryHumanReview ? (
            <span
              className="badge badge-warn"
              title="필수 검수 — 사람이 재확인 필요"
            >
              검수 필요
            </span>
          ) : isEnsemble && Number(personaStepSpread ?? 0) >= 2 ? (
            <span
              className="badge badge-danger"
              title={`3 persona 의 step_spread = ${personaStepSpread}`}
            >
              의견 충돌
            </span>
          ) : null}
          {isJudgePath && (
            <button
              type="button"
              className="btn-ghost"
              onClick={(e) => {
                e.stopPropagation();
                setJudgeOpen((v) => !v);
                if (!open) setOpen(true);
              }}
              title="3 persona 의견 충돌 — 판사 LLM 숙고 결과"
              style={{ padding: "2px 8px", fontSize: 11 }}
            >
              판사 숙고 {judgeOpen ? "▲" : "▼"}
            </button>
          )}
          {gt && gtScore != null && !gt.excluded && (
            <span
              className="badge badge-neutral"
              title={`사람 QA 점수 ${gtScore}점 · 차이 ${gtDiff === 0 ? "일치" : gtDiff}`}
              style={{ color: gtColor }}
            >
              👤 {gtScore}
              {gtDiff != null &&
                gtDiff !== 0 &&
                ` (${gtDiff > 0 ? "+" : ""}${gtDiff})`}
            </span>
          )}
          <span
            className="tabular-nums"
            style={{
              fontSize: 14,
              fontWeight: 700,
              color: scColor,
            }}
          >
            {sc}/{maxScore}
          </span>
          {hasDetail && (
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
          )}
        </div>
      </div>

      {/* HITL 편집 패널 — editMode ON 일 때만 */}
      {editMode && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            padding: "12px 14px",
            background: "var(--warn-bg)",
            borderTop: "1px solid var(--warn-border)",
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              flexWrap: "wrap",
            }}
          >
            <label
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: "var(--warn)",
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              사람 점수
              <input
                type="number"
                min={0}
                max={maxScore}
                step={1}
                value={humanScoreInput}
                placeholder={String(score ?? 0)}
                onChange={(e) => setHumanScoreInput(e.target.value)}
                className="input-field input-sm"
                style={{ width: 80 }}
              />
              <span
                style={{
                  fontSize: 11,
                  color: "var(--warn)",
                  fontWeight: 500,
                }}
              >
                / {maxScore}
              </span>
            </label>
            <span
              style={{
                fontSize: 11,
                color: "var(--ink-muted)",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              AI 점수:{" "}
              <b
                className="tabular-nums"
                style={{ color: "var(--ink)" }}
              >
                {score}
              </b>
              {aiConfidence != null && (() => {
                const confNum = Number(aiConfidence);
                const isIntScale = confNum > 1.0;
                const isLow = isIntScale ? confNum <= 2 : confNum <= 0.4;
                const display = isIntScale
                  ? `${Math.round(confNum)}/5`
                  : confNum.toFixed(2);
                return (
                  <span
                    className={
                      isLow ? "badge badge-danger" : "badge badge-neutral"
                    }
                    title={
                      isLow
                        ? "낮은 신뢰도 — 사람 검수 권장"
                        : "AI 평가 신뢰도"
                    }
                  >
                    신뢰도 {display}
                  </span>
                );
              })()}
            </span>
          </div>
          <textarea
            rows={3}
            placeholder="수정 사유 / 보완 설명..."
            value={humanNote}
            onChange={(e) => setHumanNote(e.target.value)}
            className="input-field"
            style={{
              fontSize: 12,
              resize: "vertical",
              fontFamily: "inherit",
              lineHeight: 1.4,
            }}
          />
          <div
            style={{
              display: "flex",
              gap: 8,
              justifyContent: "flex-end",
            }}
          >
            <button
              type="button"
              disabled={!!savingState}
              onClick={() => submitHumanReview(false)}
              className="btn-ghost"
              style={{
                fontSize: 12,
                padding: "4px 12px",
                color: "var(--warn)",
                borderColor: "var(--warn-border)",
              }}
            >
              {savingState === "draft" ? "저장 중..." : "임시저장"}
            </button>
            <button
              type="button"
              disabled={!!savingState}
              onClick={() => submitHumanReview(true)}
              className="btn-primary"
              style={{
                fontSize: 12,
                padding: "4px 14px",
                background: "var(--warn)",
                borderColor: "var(--warn)",
              }}
            >
              {savingState === "confirm" ? "확정 중..." : "확정"}
            </button>
          </div>
        </div>
      )}

      {/* 상세 */}
      {open && hasDetail && (
        <div
          style={{
            padding: "0 14px 12px",
            borderTop: "1px solid var(--border)",
          }}
        >
          {isEnsemble && (
            <div style={{ paddingTop: 10 }}>
              <PersonaBarChart
                personaVotes={personaVotes || null}
                merged={sc}
                maxScore={maxScore}
                mergeRule={personaMergeRule || undefined}
                mergePath={personaMergePath || undefined}
                stepSpread={personaStepSpread ?? 0}
              />
            </div>
          )}
          {isJudgePath && judgeOpen && (
            <JudgePanel
              personaVotes={personaVotes || null}
              judgeReasoning={judgeReasoning}
              finalScore={sc}
              maxScore={maxScore}
              stepSpread={personaStepSpread ?? 0}
              personaDetails={personaDetails || null}
              personaLabelMap={personaLabelMap || null}
            />
          )}
          {aiConfidence != null && (
            <div
              style={{
                marginTop: 8,
                fontSize: 11,
                color: "var(--ink-muted)",
                display: "flex",
                gap: 6,
                alignItems: "center",
              }}
            >
              <span>AI 신뢰도</span>
              <span className="badge badge-neutral tabular-nums">
                {Number(aiConfidence) > 1
                  ? `${Math.round(Number(aiConfidence))}/5`
                  : Number(aiConfidence).toFixed(2)}
              </span>
            </div>
          )}
          {error && (
            <div
              style={{
                marginTop: 10,
                padding: "10px 12px",
                background: "var(--danger-bg)",
                border: "1px solid var(--danger-border)",
                borderLeft: "3px solid var(--danger)",
                borderRadius: "var(--radius-sm)",
              }}
            >
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: "var(--danger)",
                  marginBottom: 4,
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                }}
              >
                ⚠ 평가 실패 — {error.error_type || "Error"}
              </div>
              <div style={{ fontSize: 12, color: "var(--ink)", lineHeight: 1.5 }}>
                {error.error_message || "알 수 없는 오류"}
              </div>
              <div
                style={{
                  fontSize: 11,
                  color: "var(--ink-muted)",
                  marginTop: 6,
                  fontStyle: "italic",
                }}
              >
                점수는 0점으로 기본 처리되었습니다.
              </div>
            </div>
          )}
          {(postDebateJudgeReasoning || judgment || summary) && (() => {
            // 우선순위: post-debate 판사 reasoning > 페르소나(neutral) judgment > summary.
            // 판사 호출 성공한 항목은 판사 의견을 메인 본문으로 표시 (라벨도 "🎭 판사 판정 사유").
            const useJudge = !!postDebateJudgeReasoning;
            const jt = String(
              useJudge ? postDebateJudgeReasoning : judgment || summary || "",
            );
            // V2 원본 라인 6111~6148 — BK / GS / RS chunk ID 패턴 추출 → highlight + 헤더 badge.
            const chunkMatches = Array.from(
              jt.matchAll(/\b(BK-[A-Z]+-\d{3}|GS-\d{2}-[A-Z-]+|r_\d{3})\b/g),
            ).map((m) => m[1]);
            const uniqueRefs = Array.from(new Set(chunkMatches));
            const parts =
              uniqueRefs.length > 0
                ? jt.split(new RegExp(`(${uniqueRefs.join("|")})`, "g"))
                : [jt];
            return (
              <div style={{ marginTop: 10 }}>
                <div
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    color: useJudge ? "#7c5cff" : "var(--ink-muted)",
                    textTransform: "uppercase",
                    letterSpacing: "0.04em",
                    marginBottom: 4,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <span>
                    {useJudge
                      ? "🎭 판사 판정 사유 (post-debate)"
                      : isEnsemble
                        ? "Neutral 페르소나 (페르소나 B) 판정 사유"
                        : "LLM 판정 사유"}
                  </span>
                  {useJudge && postDebateJudgeScore != null && (
                    <span
                      style={{
                        fontSize: 9,
                        fontWeight: 700,
                        padding: "1px 6px",
                        borderRadius: 8,
                        background: "#ede9ff",
                        color: "#5b3dd6",
                      }}
                    >
                      판사 점수 {postDebateJudgeScore}
                      {maxScore ? `/${maxScore}` : ""}
                    </span>
                  )}
                  {uniqueRefs.length > 0 && (
                    <span
                      title="RAG chunk 인용 감지"
                      style={{
                        fontSize: 9,
                        fontWeight: 700,
                        padding: "1px 6px",
                        borderRadius: 8,
                        background: "#dcfce7",
                        color: "#166534",
                      }}
                    >
                      📚 RAG {uniqueRefs.length}건 인용
                    </span>
                  )}
                </div>
                <div
                  style={{
                    fontSize: 12,
                    lineHeight: 1.55,
                    color: "var(--ink-soft)",
                    padding: "8px 10px",
                    background: "var(--accent-bg)",
                    borderLeft: "3px solid var(--accent)",
                    borderRadius: "var(--radius-sm)",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                  }}
                >
                  {parts.map((p, i) =>
                    uniqueRefs.includes(p) ? (
                      <code
                        key={i}
                        title="RAG chunk 참조"
                        style={{
                          background: "#fef3c7",
                          color: "#92400e",
                          padding: "1px 5px",
                          borderRadius: 3,
                          fontSize: 11,
                          fontWeight: 700,
                        }}
                      >
                        {p}
                      </code>
                    ) : (
                      <span key={i}>{p}</span>
                    ),
                  )}
                </div>
              </div>
            );
          })()}
          {strengths.length > 0 && (
            <Section title="우수 사항" color="var(--success)">
              {strengths.map((s, i) => (
                <DetailRow key={i} color="var(--success)">
                  {typeof s === "string" ? s : s.description || JSON.stringify(s)}
                </DetailRow>
              ))}
            </Section>
          )}
          {effectiveDeductions.length > 0 && (
            <Section
              title={usePostDebateJudge ? "🎭 판사 감점 사유" : "감점 사유"}
              color="var(--danger)"
            >
              {effectiveDeductions.map((d, i) => (
                <DetailRow key={i} color="var(--danger)">
                  {d.reason || "감점"}
                  {(d as { points_lost?: number }).points_lost != null && (
                    <span
                      style={{
                        color: "var(--danger)",
                        fontWeight: 700,
                        marginLeft: 6,
                      }}
                    >
                      (-{(d as { points_lost?: number }).points_lost}점)
                    </span>
                  )}
                  {(d as { evidence?: string }).evidence && (
                    <div
                      style={{
                        marginTop: 3,
                        fontSize: 11,
                        fontStyle: "italic",
                        color: "var(--ink-muted)",
                      }}
                    >
                      &ldquo;{(d as { evidence?: string }).evidence}&rdquo;
                    </div>
                  )}
                </DetailRow>
              ))}
            </Section>
          )}
          {improvements.length > 0 && (
            <Section title="개선 필요" color="var(--warn)">
              {improvements.map((s, i) => (
                <DetailRow key={i} color="var(--warn)">
                  {typeof s === "string" ? s : s.description || JSON.stringify(s)}
                </DetailRow>
              ))}
            </Section>
          )}
          {coaching.length > 0 && (
            <Section title="코칭 포인트" color="var(--info)">
              {coaching.map((c, i) => (
                <DetailRow key={i} color="var(--info)">
                  <strong>
                    [{c.priority || "medium"}] {c.area || c.title || "코칭"}
                  </strong>
                  <div style={{ color: "var(--ink-muted)", marginTop: 2 }}>
                    {c.suggestion || c.description || ""}
                  </div>
                </DetailRow>
              ))}
            </Section>
          )}
          {effectiveEvidence.length > 0 && (() => {
            // V2 원본 라인 6188~6201 — evidence 내 RAG (업무지식) chunk count → 헤더 badge.
            const ragCount = effectiveEvidence.filter((ev) => {
              const n = normalizeEv(ev);
              return n.isRag;
            }).length;
            return (
              <div style={{ marginTop: 10 }}>
                <div
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    color: "var(--accent)",
                    textTransform: "uppercase",
                    letterSpacing: "0.04em",
                    marginBottom: 4,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <span>{usePostDebateJudge ? "🎭 판사 인용 근거" : "평가 근거 (발췌)"}</span>
                  {ragCount > 0 && (
                    <span
                      title="RAG 업무지식 chunk 가 evidence 에 포함됨"
                      style={{
                        fontSize: 9,
                        fontWeight: 700,
                        padding: "1px 6px",
                        borderRadius: 8,
                        background: "#fef3c7",
                        color: "#92400e",
                      }}
                    >
                      📚 RAG {ragCount}건
                    </span>
                  )}
                </div>
                {effectiveEvidence.slice(0, 8).map((ev, i) => {
                  const n = normalizeEv(ev);
                  if (!n.quote) return null;
                  return (
                    <div
                      key={i}
                      style={{
                        padding: "5px 10px",
                        marginTop: 3,
                        background: n.isRag
                          ? "var(--warn-bg)"
                          : "var(--surface-muted)",
                        borderLeft: n.isRag
                          ? "2px solid var(--warn)"
                          : "2px solid var(--border-strong)",
                        borderRadius: "var(--radius-sm)",
                        fontSize: 11.5,
                        lineHeight: 1.5,
                      }}
                    >
                      {(n.speaker || n.turn) && (
                        <span
                          style={{
                            fontWeight: 700,
                            color: n.isRag ? "var(--warn)" : "var(--accent)",
                            marginRight: 6,
                          }}
                        >
                          {n.speaker}
                          {n.turn ? ` · #${n.turn}` : ""}
                        </span>
                      )}
                      <span style={{ color: "var(--ink-soft)" }}>
                        &ldquo;{n.quote}&rdquo;
                      </span>
                    </div>
                  );
                })}
                {effectiveEvidence.length > 8 && (
                  <div
                    style={{
                      marginTop: 4,
                      fontSize: 11,
                      color: "var(--ink-subtle)",
                      fontStyle: "italic",
                    }}
                  >
                    … {effectiveEvidence.length - 8}건 생략
                  </div>
                )}
              </div>
            );
          })()}
          {/* Dev6 manualEval — 사람 QA 근거 slot */}
          {gt?.note && (
            <Section title="👤 사람 QA 근거 (수기 평가표)" color="var(--success)">
              <div
                style={{
                  padding: "8px 10px",
                  background: "var(--success-bg)",
                  border: "1px solid var(--success-border)",
                  borderRadius: "var(--radius-sm)",
                  fontSize: 12,
                  lineHeight: 1.5,
                  color: "var(--ink-soft)",
                  whiteSpace: "pre-wrap",
                }}
              >
                {gt.note}
              </div>
            </Section>
          )}
        </div>
      )}
    </div>
  );
}

function Section({
  title,
  color,
  children,
}: {
  title: string;
  color?: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginTop: 10 }}>
      <div
        style={{
          fontSize: 10,
          fontWeight: 700,
          color: color || "var(--ink-muted)",
          textTransform: "uppercase",
          letterSpacing: "0.04em",
          marginBottom: 4,
        }}
      >
        {title}
      </div>
      {children}
    </div>
  );
}

function DetailRow({ color, children }: { color?: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        padding: "4px 10px",
        marginTop: 2,
        fontSize: 12,
        color: "var(--ink-soft)",
        borderLeft: `2px solid ${color || "var(--border-strong)"}`,
        background: "var(--surface-muted)",
        borderRadius: "var(--radius-sm)",
      }}
    >
      {children}
    </div>
  );
}

export default memo(ItemCard);
