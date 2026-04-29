// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { memo, useCallback, useEffect, useState } from "react";

import { confirmReview, revertReview, upsertHumanReview } from "@/lib/api";
import type {
  CategoryItem,
  DeductionEntry,
  EvidenceEntry,
  ReviewItem,
} from "@/lib/types";

import PersonaScores from "./PersonaScores";

const STATUS_LABELS: Record<
  string,
  { label: string; badgeClass: string }
> = {
  pending: { label: "검수 대기", badgeClass: "badge badge-warn" },
  confirmed: { label: "확정", badgeClass: "badge badge-success" },
  draft: { label: "임시저장", badgeClass: "badge badge-info" },
  rejected: { label: "반려", badgeClass: "badge badge-danger" },
};

interface TranscriptTurn {
  turn_id?: number;
  speaker?: string;
  text?: string;
  segment?: string;
}

interface Props {
  item: CategoryItem;
  category: string;
  hitlRow?: ReviewItem;
  /**
   * 상담 STT 전문 (raw) — turns 가 없을 때 fallback. 카드를 펼치면 collapsible 로 노출.
   */
  transcript?: string | null;
  /**
   * 파싱된 턴 리스트 — preprocessing.turns. 우선순위: turns > transcript.
   * 항목의 evidence 에 매칭되는 turn_id 는 하이라이트된다 — "항목별 파싱 원문" 의도.
   */
  turns?: TranscriptTurn[] | null;
  /**
   * hitlRow 가 아직 없을 때 lazy-upsert 에 사용할 consultation_id. 평가 직후 모달에서
   * populator 가 DB commit 끝내기 전에 사용자가 입력하는 케이스를 지원한다.
   * hitlRow.consultation_id 가 있으면 그걸 우선 사용.
   */
  consultationId?: string | null;
  onChanged?: () => void;
  onToast?: (t: { kind: "success" | "error"; title: string; message: string }) => void;
}

function normalizeEvidence(ev: EvidenceEntry | string): {
  speaker: string;
  quote: string;
  ts: string;
} {
  if (typeof ev === "string") return { speaker: "", quote: ev, ts: "" };
  return {
    speaker: ev.speaker || ev.role || "",
    quote: ev.quote || ev.text || "",
    ts: ev.timestamp || "",
  };
}

function formatDeduction(d: DeductionEntry | string): {
  text: string;
  points: number | null;
} {
  if (typeof d === "string") return { text: d, points: null };
  return {
    text: d.reason || d.detail || JSON.stringify(d),
    points: d.points ?? null,
  };
}

function ReviewItemCard({
  item,
  category,
  hitlRow,
  transcript,
  turns,
  consultationId,
  onChanged,
  onToast,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [scoreInput, setScoreInput] = useState<string>(
    hitlRow?.human_score != null ? String(hitlRow.human_score) : "",
  );
  const [noteInput, setNoteInput] = useState<string>(hitlRow?.human_note || "");
  const [busy, setBusy] = useState<"" | "confirm" | "revert">("");
  const [transcriptOpen, setTranscriptOpen] = useState(false);

  const statusKey = hitlRow?.status || "pending";
  const statusInfo = STATUS_LABELS[statusKey] || STATUS_LABELS.pending;
  const humanScore = hitlRow?.human_score;
  const humanDiff =
    humanScore != null && Number(humanScore) !== Number(item.score);

  // hitlRow 가 늦게 도착하는 케이스 (populator DB commit 지연으로 모달 첫 렌더 시 undefined →
  // 백오프 retry 후 채워짐) 동기화. 사용자가 입력 중 (editing=true) 일 때는 덮어쓰지 않음.
  const hitlRowId = hitlRow?.id;
  const incomingHumanScore = hitlRow?.human_score;
  const incomingHumanNote = hitlRow?.human_note;
  useEffect(() => {
    if (editing) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setScoreInput(incomingHumanScore != null ? String(incomingHumanScore) : "");
    setNoteInput(incomingHumanNote || "");
  }, [hitlRowId, incomingHumanScore, incomingHumanNote, editing]);

  const confidenceFinal = item.confidence?.final;
  const lowConf = confidenceFinal != null && confidenceFinal <= 2;

  // lazy-upsert 대응: hitlRow 가 없어도 consultationId 가 있으면 upsert 로 신규 행 생성 후 확정.
  const effectiveConsultationId = hitlRow?.consultation_id || consultationId || "";

  const handleConfirm = useCallback(async () => {
    if (scoreInput === "") return;
    if (!effectiveConsultationId) {
      onToast?.({
        kind: "error",
        title: "확정 불가",
        message:
          "consultation_id 를 알 수 없습니다 — 상담 ID 가 없는 평가 결과에서는 확정할 수 없어요.",
      });
      return;
    }
    const parsed = Number(scoreInput);
    if (Number.isNaN(parsed)) {
      onToast?.({ kind: "error", title: "입력 오류", message: "점수를 숫자로 입력하세요." });
      return;
    }
    setBusy("confirm");
    try {
      // HITL 규칙: UPSERT → confirm 순. hitlRow 가 있어도 없어도 항상 upsert 호출.
      const evidenceLines = Array.isArray(item.evidence)
        ? item.evidence
            .map((ev) => (typeof ev === "string" ? ev : ev.quote || ev.text || ""))
            .filter(Boolean)
        : [];
      const upsert = await upsertHumanReview({
        consultation_id: effectiveConsultationId,
        item_number: Number(item.item_number),
        ai_score: Number(item.score) || 0,
        human_score: parsed,
        ai_evidence: evidenceLines,
        ai_judgment: String(item.judgment || item.summary || ""),
        human_note: noteInput,
        ai_confidence:
          confidenceFinal != null
            ? Number(confidenceFinal)
            : hitlRow?.ai_confidence ?? null,
        reviewer_id: "ui-user",
        reviewer_role: "senior",
        force_t3: !!item.force_t3,
      });
      const reviewId = upsert?.id ?? hitlRow?.id;
      if (!reviewId) {
        throw new Error("upsert 가 id 를 반환하지 않았습니다");
      }
      const cf = await confirmReview(reviewId, {
        reviewer_id: "ui-user",
        reviewer_role: "senior",
      });
      const snapPath = cf.snapshot_path || cf.snapshot_root || "";
      onToast?.({
        kind: "success",
        title: "확정되었습니다",
        message:
          `#${item.item_number} · ${parsed}점` +
          (snapPath ? `\n${snapPath}` : ""),
      });
      setEditing(false);
      onChanged?.();
    } catch (err: unknown) {
      onToast?.({
        kind: "error",
        title: "확정 실패",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusy("");
    }
  }, [
    effectiveConsultationId,
    hitlRow,
    scoreInput,
    noteInput,
    item,
    confidenceFinal,
    onToast,
    onChanged,
  ]);

  const handleRevert = useCallback(async () => {
    if (!hitlRow) return;
    const reason = window.prompt(
      `항목 #${item.item_number} 검수 취소 사유 (선택 — 비고에 기록됨)`,
      "",
    );
    if (reason === null) return;
    setBusy("revert");
    try {
      const r = await revertReview(hitlRow.id, {
        reviewer_id: "ui-user",
        reason,
      });
      onToast?.({
        kind: "success",
        title: "검수 취소됨",
        message: `항목 #${item.item_number} → 대기로 복귀${r.snapshot_path ? `\n${r.snapshot_path}` : ""}`,
      });
      setEditing(false);
      onChanged?.();
    } catch (err: unknown) {
      onToast?.({
        kind: "error",
        title: "검수 취소 실패",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusy("");
    }
  }, [hitlRow, item.item_number, onToast, onChanged]);

  const evList = Array.isArray(item.evidence) ? item.evidence : [];
  const dedList = Array.isArray(item.deductions) ? item.deductions : [];

  // evidence 에 참조된 turn_id 집합 — 파싱 원문 섹션에서 하이라이트 대상.
  // evidence 객체가 { turn: 12 } / { turn_id: 12 } / { idx: 12 } 등 다양한 키를 사용할 수 있어 폭넓게 커버.
  const evidenceTurnIds = new Set<number>();
  for (const ev of evList) {
    if (!ev || typeof ev === "string") continue;
    const raw = ev as Record<string, unknown>;
    for (const key of ["turn", "turn_id", "idx", "index"]) {
      const v = raw[key];
      if (typeof v === "number" && Number.isFinite(v)) {
        evidenceTurnIds.add(v);
      } else if (typeof v === "string" && v.trim() && !Number.isNaN(Number(v))) {
        evidenceTurnIds.add(Number(v));
      }
    }
  }

  const hasParsedTurns = Array.isArray(turns) && turns.length > 0;
  const transcriptCharCount = hasParsedTurns
    ? (turns as TranscriptTurn[]).reduce((s, t) => s + (t.text || "").length, 0)
    : transcript
      ? transcript.length
      : 0;
  const transcriptUnitCount = hasParsedTurns
    ? (turns as TranscriptTurn[]).length
    : transcript
      ? transcript.split("\n").length
      : 0;

  return (
    <details
      style={{
        background: item.force_t3 ? "var(--danger-bg)" : "var(--surface-muted)",
        border: `1px solid ${item.force_t3 ? "var(--danger-border)" : "var(--border)"}`,
        borderRadius: "var(--radius-sm)",
      }}
      onToggle={(e) => {
        const open = (e.currentTarget as HTMLDetailsElement).open;
        if (open) {
          setEditing(true);
          setScoreInput(humanScore != null ? String(humanScore) : "");
          setNoteInput(hitlRow?.human_note || "");
        }
      }}
    >
      <summary
        style={{
          padding: "8px 12px",
          cursor: "pointer",
          fontSize: 11,
          fontWeight: 600,
          color: "var(--ink-soft)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <span style={{ fontWeight: 700 }}>
          {item.force_t3 && (
            <span style={{ color: "var(--danger)", marginRight: 2 }}>●</span>
          )}
          #{item.item_number}
        </span>
        <span>{item.item || item.item_name || "-"}</span>
        <span style={{ color: "var(--ink-muted)", fontWeight: 400 }}>· {category}</span>
        <span
          style={{
            marginLeft: "auto",
            fontSize: 12,
            fontWeight: 800,
            color: "var(--info)",
          }}
        >
          AI {item.score}/{item.max_score}
        </span>
        <span
          style={{
            fontSize: 12,
            fontWeight: 800,
            color:
              humanScore == null
                ? "var(--ink-muted)"
                : humanDiff
                  ? "var(--warn)"
                  : "var(--success)",
            padding: "0 4px",
          }}
        >
          사람 {humanScore ?? "—"}
        </span>
        {confidenceFinal != null && (
          <span className={`badge ${lowConf ? "badge-danger" : "badge-info"}`}>
            신뢰도 {confidenceFinal}/5
          </span>
        )}
        <span className={statusInfo.badgeClass}>{statusInfo.label}</span>
      </summary>
      <div
        style={{
          padding: "10px 14px",
          borderTop: "1px dashed var(--border)",
          display: "flex",
          flexDirection: "column",
          gap: 10,
          fontSize: 11,
        }}
      >
        {(hasParsedTurns || transcript) && (
          <div
            style={{
              padding: "6px 10px",
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
            }}
          >
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setTranscriptOpen((v) => !v);
              }}
              style={{
                appearance: "none",
                width: "100%",
                display: "flex",
                alignItems: "center",
                gap: 8,
                background: "transparent",
                border: 0,
                padding: 0,
                cursor: "pointer",
                textAlign: "left",
                fontSize: 10,
                fontWeight: 700,
                color: "var(--ink-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.04em",
              }}
              aria-expanded={transcriptOpen}
            >
              <span style={{ display: "inline-block", width: 10 }}>
                {transcriptOpen ? "▾" : "▸"}
              </span>
              <span>🎧 상담 원문 (파싱)</span>
              <span
                style={{
                  fontSize: 9,
                  fontWeight: 400,
                  color: "var(--ink-subtle)",
                  textTransform: "none",
                  letterSpacing: 0,
                }}
              >
                {hasParsedTurns
                  ? `${transcriptUnitCount}턴 · ${transcriptCharCount.toLocaleString()}자`
                  : `${transcriptUnitCount}줄 · ${transcriptCharCount.toLocaleString()}자`}
                {evidenceTurnIds.size > 0 && (
                  <span style={{ marginLeft: 6, color: "var(--success)" }}>
                    · 근거 {evidenceTurnIds.size}턴 강조
                  </span>
                )}
              </span>
              <span
                style={{
                  marginLeft: "auto",
                  fontSize: 9,
                  fontWeight: 400,
                  color: "var(--ink-subtle)",
                  textTransform: "none",
                }}
              >
                {transcriptOpen ? "접기" : "펼쳐 보기"}
              </span>
            </button>
            {transcriptOpen && hasParsedTurns && (
              <div
                onClick={(e) => e.stopPropagation()}
                style={{
                  marginTop: 8,
                  padding: "8px 10px",
                  background: "var(--surface-muted)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  maxHeight: 320,
                  overflow: "auto",
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                }}
              >
                {(turns as TranscriptTurn[]).map((t, i) => {
                  const tid = typeof t.turn_id === "number" ? t.turn_id : i + 1;
                  const highlighted = evidenceTurnIds.has(tid);
                  const isAgent =
                    typeof t.speaker === "string" &&
                    /(상담|agent|직원|상담사)/i.test(t.speaker);
                  return (
                    <div
                      key={`turn-${tid}-${i}`}
                      style={{
                        padding: "4px 8px",
                        background: highlighted
                          ? "rgba(201, 100, 66, 0.08)"
                          : "transparent",
                        border: highlighted
                          ? "1px solid rgba(201, 100, 66, 0.35)"
                          : "1px solid transparent",
                        borderRadius: "var(--radius-sm)",
                        fontSize: 10,
                        lineHeight: 1.55,
                        display: "flex",
                        gap: 8,
                        alignItems: "flex-start",
                      }}
                    >
                      <span
                        style={{
                          flexShrink: 0,
                          fontVariantNumeric: "tabular-nums",
                          color: "var(--ink-subtle)",
                          fontWeight: 600,
                          minWidth: 22,
                          textAlign: "right",
                        }}
                      >
                        {tid}.
                      </span>
                      <span
                        style={{
                          flexShrink: 0,
                          fontWeight: 700,
                          color: isAgent ? "var(--info)" : "var(--warn)",
                          minWidth: 48,
                        }}
                      >
                        [{t.speaker || "-"}]
                      </span>
                      {t.segment && (
                        <span
                          style={{
                            flexShrink: 0,
                            fontSize: 8,
                            fontWeight: 700,
                            padding: "1px 5px",
                            borderRadius: 8,
                            background: "var(--surface)",
                            border: "1px solid var(--border)",
                            color: "var(--ink-muted)",
                          }}
                        >
                          {t.segment}
                        </span>
                      )}
                      <span style={{ color: "var(--ink)", flex: 1 }}>
                        {t.text || ""}
                      </span>
                      {highlighted && (
                        <span
                          title="이 항목의 평가 근거로 참조된 턴"
                          style={{
                            flexShrink: 0,
                            fontSize: 8,
                            fontWeight: 700,
                            padding: "1px 5px",
                            borderRadius: 8,
                            background: "var(--accent-bg, #f9efe8)",
                            color: "var(--accent, #c96442)",
                          }}
                        >
                          근거
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
            {transcriptOpen && !hasParsedTurns && transcript && (
              <pre
                onClick={(e) => e.stopPropagation()}
                style={{
                  marginTop: 8,
                  padding: "8px 10px",
                  background: "var(--surface-muted)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  maxHeight: 260,
                  overflow: "auto",
                  fontSize: 10,
                  lineHeight: 1.6,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  fontFamily:
                    "var(--font-mono), ui-monospace, SFMono-Regular, Consolas, monospace",
                  color: "var(--ink)",
                }}
              >
                {transcript}
              </pre>
            )}
          </div>
        )}
        {item.judgment && (
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
              LLM 판정 사유 (judgment)
            </div>
            <div
              style={{
                color: "var(--ink-soft)",
                lineHeight: 1.5,
                whiteSpace: "pre-wrap",
              }}
            >
              {item.judgment}
            </div>
          </div>
        )}
        {dedList.length > 0 && (
          <div>
            <div
              style={{
                fontSize: 10,
                fontWeight: 700,
                color: "var(--danger)",
                marginBottom: 3,
                textTransform: "uppercase",
                letterSpacing: "0.04em",
              }}
            >
              감점 사유
            </div>
            <ul
              style={{
                margin: 0,
                paddingLeft: 18,
                color: "var(--ink-soft)",
                lineHeight: 1.5,
              }}
            >
              {dedList.map((d, i) => {
                const { text, points } = formatDeduction(d);
                return (
                  <li key={i}>
                    {text}
                    {points != null && (
                      <span
                        style={{
                          color: "var(--danger)",
                          fontWeight: 700,
                          marginLeft: 4,
                        }}
                      >
                        (-{points}점)
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        {evList.length > 0 && (
          <div>
            <div
              style={{
                fontSize: 10,
                fontWeight: 700,
                color: "var(--success)",
                marginBottom: 3,
                textTransform: "uppercase",
                letterSpacing: "0.04em",
              }}
            >
              평가 근거 (evidence)
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
              {evList.map((ev, i) => {
                const { speaker, quote, ts } = normalizeEvidence(ev);
                return (
                  <div
                    key={i}
                    style={{
                      padding: "4px 8px",
                      background: "var(--surface)",
                      border: "1px solid var(--border)",
                      borderRadius: "var(--radius-sm)",
                      fontSize: 10,
                      lineHeight: 1.5,
                    }}
                  >
                    {speaker && (
                      <span
                        style={{
                          fontWeight: 700,
                          color: "var(--info)",
                          marginRight: 6,
                        }}
                      >
                        [{speaker}]
                      </span>
                    )}
                    {ts && (
                      <span
                        style={{
                          color: "var(--ink-muted)",
                          marginRight: 6,
                        }}
                      >
                        {ts}
                      </span>
                    )}
                    <span style={{ color: "var(--ink-soft)" }}>{quote}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        {item.persona_votes && (
          <PersonaScores
            votes={item.persona_votes}
            mergedScore={item.score}
            mergePath={item.persona_merge_path || item.persona_merge_rule}
          />
        )}

        {editing && effectiveConsultationId ? (
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              marginTop: 4,
              padding: "10px 12px",
              background: "var(--warn-bg)",
              border: "1px solid var(--warn-border)",
              borderRadius: "var(--radius-sm)",
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            <div
              style={{
                fontSize: 10,
                fontWeight: 700,
                color: "var(--warn)",
                letterSpacing: "0.04em",
                textTransform: "uppercase",
              }}
            >
              ✏ 사람 검수 입력
              {!hitlRow && (
                <span
                  style={{
                    marginLeft: 8,
                    fontSize: 9,
                    fontWeight: 600,
                    color: "var(--ink-muted)",
                    textTransform: "none",
                    letterSpacing: 0,
                  }}
                >
                  (HITL 큐 미동기화 상태 — 확정 시 신규 등록)
                </span>
              )}
            </div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                flexWrap: "wrap",
              }}
            >
              <label
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: "var(--ink-soft)",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                사람 점수
                <input
                  type="number"
                  min={0}
                  max={item.max_score || undefined}
                  value={scoreInput}
                  onChange={(e) => setScoreInput(e.target.value)}
                  className="input-field input-sm"
                  style={{ width: 80 }}
                />
                <span
                  style={{
                    fontSize: 10,
                    color: "var(--ink-muted)",
                    fontWeight: 400,
                  }}
                >
                  / {item.max_score}
                </span>
              </label>
              <span style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                AI 점수: <b style={{ color: "var(--ink)" }}>{item.score ?? "-"}</b>
              </span>
              {humanScore != null && (
                <span style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                  이전 사람 점수:{" "}
                  <b style={{ color: "var(--ink)" }}>{humanScore}</b>
                </span>
              )}
            </div>
            <textarea
              rows={3}
              placeholder="비고 (선택) — 점수 차이 사유 / 참고할 기준 / 상담 특이사항 등"
              value={noteInput}
              onChange={(e) => setNoteInput(e.target.value)}
              className="input-field"
              style={{ resize: "vertical" }}
            />
            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                gap: 8,
                alignItems: "center",
              }}
            >
              {statusKey === "confirmed" && (
                <button
                  type="button"
                  disabled={busy === "revert"}
                  onClick={handleRevert}
                  title="이 항목의 확정을 취소하고 대기 상태로 되돌립니다"
                  className="btn-secondary btn-sm"
                  style={{ color: "var(--danger)", borderColor: "var(--danger-border)" }}
                >
                  {busy === "revert" ? "취소 중..." : "↺ 검수 취소"}
                </button>
              )}
              <button
                type="button"
                disabled={busy === "confirm" || scoreInput === ""}
                onClick={handleConfirm}
                className="btn-primary btn-sm"
              >
                {busy === "confirm"
                  ? "확정 중..."
                  : statusKey === "confirmed"
                    ? "재확정"
                    : "확정"}
              </button>
            </div>
          </div>
        ) : !effectiveConsultationId ? (
          <div
            style={{
              marginTop: 4,
              fontSize: 10,
              color: "var(--warn)",
              padding: "6px 10px",
              background: "var(--warn-bg)",
              border: "1px dashed var(--warn-border)",
              borderRadius: "var(--radius-sm)",
            }}
          >
            ℹ 상담 ID 를 찾을 수 없어 사람 검수 입력이 불가합니다 (결과 JSON 에
            consultation_id 가 누락된 레거시 실행 건).
          </div>
        ) : null}
      </div>
    </details>
  );
}

export default memo(ReviewItemCard);
