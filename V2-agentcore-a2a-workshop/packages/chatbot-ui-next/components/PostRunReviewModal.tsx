// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * PostRunReviewModal — 파이프라인 평가 완료 직후 같은 탭에서 팝업으로 뜨는 HITL 검토 패널.
 *
 * 흐름:
 *   1) EvaluateRunner 가 done SSE 이벤트 수신 + report state 세팅
 *   2) consultation_id 감지되면 이 모달을 자동 오픈 (제어형 open prop)
 *   3) 만점 아닌 항목 + force_t3 + 신뢰도≤2 를 기본 노출 → 바로 사람 점수 입력
 *
 * 데이터 소스:
 *   - report (Report): EvaluateRunner 에서 SSE 로 수집한 최종 evaluation 결과
 *   - transcript: 원문 STT (전체 보기 전용, 증거 구간 매칭에는 쓰지 않음)
 *   - hitlRows: /v2/review/queue 에서 consultation_id 필터. 모달 open 시 로드.
 *
 * /result/[cid] 페이지와 거의 같은 렌더링이지만 라우팅 전환 없이 한 탭에서 즉시 처리.
 * "전체 결과 페이지 열기" 링크로 /result/[cid] 이동도 제공 (긴 호흡 리뷰용).
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import ReviewItemCard from "@/components/ReviewItemCard";
import { fetchReviewQueue } from "@/lib/api";
import type { CategoryItem, Report, ReviewItem } from "@/lib/types";

interface TranscriptTurn {
  turn_id?: number;
  speaker?: string;
  text?: string;
  segment?: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  consultationId: string;
  report: Report | null;
  /** report 가 비어있을 때 (e.g. dept-aware layer3 gate 실패) evaluations 로 fallback. */
  evaluationsFallback?: Array<{ agent_id?: string; evaluation?: CategoryItem; status?: string }> | null;
  transcript: string | null;
  /** preprocessing.turns — 있으면 항목 카드에서 파싱 원문 렌더. */
  turns?: TranscriptTurn[] | null;
}

/** preprocessing 필드에서 turns 배열을 안전하게 추출. 다양한 스키마 변화 대응. */
export function extractTurnsFromPreprocessing(preprocessing: unknown): TranscriptTurn[] | null {
  if (!preprocessing || typeof preprocessing !== "object") return null;
  const pp = preprocessing as Record<string, unknown>;
  const candidates: unknown[] = [pp.turns, pp.parsed_dialogue, pp.dialogue];
  for (const c of candidates) {
    if (Array.isArray(c) && c.length > 0) {
      return c as TranscriptTurn[];
    }
    if (c && typeof c === "object") {
      const inner = (c as Record<string, unknown>).turns;
      if (Array.isArray(inner) && inner.length > 0) {
        return inner as TranscriptTurn[];
      }
    }
  }
  return null;
}

type FlatItem = CategoryItem & { category: string };

function flatten(report: Report | null): FlatItem[] {
  const cats = report?.evaluation?.categories || [];
  return cats.flatMap((cat) =>
    (cat.items || []).map((it) => ({ ...it, category: cat.category })),
  );
}

/** report 가 비어있을 때 evaluations array 로부터 FlatItem[] 생성.
 *  backend 의 layer3/layer4 gate 가 dept items 처리 실패 시 report 가 누락될 수 있으므로 fallback. */
function flattenFromEvaluations(
  evals: Array<{ agent_id?: string; evaluation?: CategoryItem; status?: string }> | null | undefined,
): FlatItem[] {
  if (!evals) return [];
  const out: FlatItem[] = [];
  for (const e of evals) {
    const ev = e?.evaluation;
    if (!ev || typeof ev.item_number !== "number") continue;
    out.push({ ...ev, category: e.agent_id || "(no-category)" });
  }
  return out;
}

function isReviewTarget(it: FlatItem): boolean {
  // 사용자 의도: "만점이면 검수할 게 없다" — 만점 여부를 최우선으로 판정한다.
  // force_t3 / 저신뢰도 는 감점 원인이 있을 때만 의미 있는 플래그이므로 만점이면 noise.
  if (it.max_score == null) return true; // 만점 판정 불가 → 안전하게 노출
  const perfect = Number(it.score) >= Number(it.max_score);
  if (perfect) return false;
  // 이하는 감점이 있는 항목 중 "특히 주의" 케이스. 사실상 전부 노출이지만 명시적으로 남김.
  if (it.force_t3) return true;
  const cf = it.confidence?.final;
  if (typeof cf === "number" && cf <= 2) return true;
  return true;
}

export default function PostRunReviewModal({
  open,
  onClose,
  consultationId,
  report,
  evaluationsFallback,
  transcript,
  turns,
}: Props) {
  const [hitlRows, setHitlRows] = useState<ReviewItem[]>([]);
  const [hitlLoading, setHitlLoading] = useState(false);
  const [hitlError, setHitlError] = useState("");
  const [hitlSyncAttempt, setHitlSyncAttempt] = useState(0);
  const [onlyNonPerfect, setOnlyNonPerfect] = useState(true);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  // 자동 백오프 retry 가 아직 살아있는 동안 사용자의 수동 ↻ 클릭이 들어오면
  // 잔여 setTimeout 핸들을 모두 죽이기 위한 보관소 (modal close 시에도 일괄 해제).
  const autoRetryTimersRef = useRef<number[]>([]);
  const [toast, setToast] = useState<{
    kind: "success" | "error";
    title: string;
    message: string;
  } | null>(null);

  const flatItems = useMemo(() => {
    const fromReport = flatten(report);
    if (fromReport.length > 0) return fromReport;
    // report 가 비어있으면 evaluations fallback (dept items 가 layer3 gate 에 막힌 케이스)
    return flattenFromEvaluations(evaluationsFallback);
  }, [report, evaluationsFallback]);
  const nonPerfectItems = useMemo(
    () => flatItems.filter(isReviewTarget),
    [flatItems],
  );
  const visibleItems = onlyNonPerfect ? nonPerfectItems : flatItems;
  const perfectHidden = flatItems.length - nonPerfectItems.length;

  const hitlByNum = useMemo(() => {
    const m = new Map<number, ReviewItem>();
    for (const r of hitlRows) m.set(Number(r.item_number), r);
    return m;
  }, [hitlRows]);

  const loadHitl = useCallback(async (): Promise<number> => {
    if (!consultationId) return 0;
    setHitlLoading(true);
    setHitlError("");
    try {
      // HITL 큐는 평가 종료 시 populator 가 upsert. populator 가 DB commit 을 끝내기 전에
      // fetch 가 먼저 일어나면 빈 배열이 내려온다 — 호출부에서 백오프 retry 로 커버.
      const r = await fetchReviewQueue({ status: "all", limit: 500 });
      const filtered = (r.items || []).filter(
        (it) => it.consultation_id === consultationId,
      );
      setHitlRows(filtered);
      return filtered.length;
    } catch (err: unknown) {
      setHitlError(err instanceof Error ? err.message : String(err));
      setHitlRows([]);
      return 0;
    } finally {
      setHitlLoading(false);
    }
  }, [consultationId]);

  // 사용자 수동 ↻ 재조회 — 자동 retry 와 별개로 즉시 1회만 fetch.
  const reloadHitl = useCallback(() => {
    void loadHitl();
  }, [loadHitl]);

  useEffect(() => {
    if (!open) return;
    // populator commit 타이밍을 알 수 없으므로 다단 백오프로 재시도.
    // 0ms (즉시) → 700ms → 1500ms → 3000ms → 5000ms. 어느 시점이든 hitl 행이
    // 들어오면 후속 retry 는 cancel 해 불필요한 호출을 막는다.
    const delays = [0, 700, 1500, 3000, 5000];
    let cancelled = false;

    // eslint-disable-next-line react-hooks/set-state-in-effect
    setHitlSyncAttempt(0);
    autoRetryTimersRef.current.forEach((t) => window.clearTimeout(t));
    autoRetryTimersRef.current = [];

    delays.forEach((ms, idx) => {
      const handle = window.setTimeout(async () => {
        if (cancelled) return;
        setHitlSyncAttempt(idx + 1);
        const n = await loadHitl();
        if (n > 0) {
          // 더 이상 retry 가 필요 없음 — 잔여 timer 정리.
          autoRetryTimersRef.current.forEach((h) => window.clearTimeout(h));
          autoRetryTimersRef.current = [];
        }
      }, ms);
      autoRetryTimersRef.current.push(handle);
    });

    return () => {
      cancelled = true;
      autoRetryTimersRef.current.forEach((t) => window.clearTimeout(t));
      autoRetryTimersRef.current = [];
    };
  }, [open, loadHitl]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  const finalScore = report?.final_score;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="post-run-review-title"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1100,
        background: "rgba(20, 17, 13, 0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(1100px, 100%)",
          maxHeight: "92vh",
          background: "#fff",
          border: "1px solid #e5d8c3",
          borderRadius: 12,
          boxShadow: "0 20px 50px rgba(0,0,0,0.18)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <header
          style={{
            padding: "14px 18px",
            borderBottom: "1px solid #ece8d8",
            display: "flex",
            alignItems: "center",
            gap: 12,
            flexWrap: "wrap",
            background: "#fdfaf2",
          }}
        >
          <div
            id="post-run-review-title"
            style={{ display: "flex", alignItems: "center", gap: 10 }}
          >
            <span style={{ fontSize: 18 }}>📝</span>
            <span
              style={{
                fontSize: 14,
                fontWeight: 800,
                color: "#14110d",
                letterSpacing: 0.1,
              }}
            >
              QA 평가 완료 · 휴먼인더루프 검수
            </span>
            {consultationId && (
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: "#c96442",
                  background: "#f9efe8",
                  border: "1px solid #c96442",
                  borderRadius: 9999,
                  padding: "2px 10px",
                  fontFamily: "monospace",
                }}
              >
                {consultationId}
              </span>
            )}
          </div>

          {finalScore && (
            <div
              style={{
                display: "inline-flex",
                alignItems: "baseline",
                gap: 6,
                padding: "3px 10px",
                background: "#fef3c7",
                border: "1px solid #fcd34d",
                borderRadius: 6,
              }}
            >
              <span style={{ fontSize: 16, fontWeight: 800, color: "#78350f" }}>
                {finalScore.grade || "-"}
              </span>
              <span style={{ fontSize: 11, fontWeight: 700, color: "#92400e" }}>
                {finalScore.after_overrides ?? finalScore.raw_total ?? "-"}/100
              </span>
            </div>
          )}

          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              padding: "3px 8px",
              borderRadius: 10,
              background:
                nonPerfectItems.length > 0 ? "#fee2e2" : "#dcfce7",
              color: nonPerfectItems.length > 0 ? "#b91c1c" : "#166534",
            }}
          >
            검수 대상 {nonPerfectItems.length}건 / 전체 {flatItems.length}건
          </span>

          <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
            {consultationId && (
              <Link
                href={`/result/${encodeURIComponent(consultationId)}`}
                className="btn-ghost"
                style={{ fontSize: 11, padding: "5px 10px" }}
                title="전체 결과 페이지 — GT 비교 · 토론 기록까지 포함"
              >
                전체 결과 페이지 →
              </Link>
            )}
            <button
              type="button"
              onClick={onClose}
              aria-label="닫기"
              className="btn-ghost"
              style={{ fontSize: 12, padding: "5px 12px", fontWeight: 700 }}
            >
              × 닫기
            </button>
          </div>
        </header>

        <div
          style={{
            flex: 1,
            overflow: "auto",
            padding: "14px 18px",
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
        >
          {toast && (
            <div
              onClick={() => setToast(null)}
              style={{
                padding: "8px 12px",
                background: toast.kind === "success" ? "#dcfce7" : "#fee2e2",
                border: `1px solid ${toast.kind === "success" ? "#86efac" : "#fca5a5"}`,
                color: toast.kind === "success" ? "#166534" : "#b91c1c",
                borderRadius: 4,
                fontSize: 12,
                whiteSpace: "pre-wrap",
                cursor: "pointer",
              }}
            >
              <b>{toast.title}</b> · {toast.message}
              <span style={{ float: "right", opacity: 0.7 }}>× 닫기</span>
            </div>
          )}

          {transcript && (
            <div
              style={{
                padding: "10px 14px",
                background: "#fff",
                border: "1px solid #e5d8c3",
                borderRadius: 8,
              }}
            >
              <button
                type="button"
                onClick={() => setTranscriptOpen((v) => !v)}
                style={{
                  appearance: "none",
                  width: "100%",
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  background: "transparent",
                  border: 0,
                  padding: 0,
                  cursor: "pointer",
                  textAlign: "left",
                  fontSize: 12,
                  fontWeight: 700,
                  color: "#4a3f35",
                }}
                aria-expanded={transcriptOpen}
              >
                <span style={{ display: "inline-block", width: 14 }}>
                  {transcriptOpen ? "▾" : "▸"}
                </span>
                <span>🎧 상담 STT 전문</span>
                <span style={{ fontSize: 10, fontWeight: 400, color: "#71717a" }}>
                  — {transcript.split("\n").length}줄 ·{" "}
                  {transcript.length.toLocaleString()}자
                </span>
                <span
                  style={{
                    marginLeft: "auto",
                    fontSize: 10,
                    fontWeight: 400,
                    color: "#71717a",
                  }}
                >
                  {transcriptOpen ? "접기" : "펼쳐 보기"}
                </span>
              </button>
              {transcriptOpen && (
                <pre
                  style={{
                    marginTop: 10,
                    padding: "12px 14px",
                    background: "#fdfaf2",
                    border: "1px solid #e5d8c3",
                    borderRadius: 6,
                    maxHeight: 320,
                    overflow: "auto",
                    fontSize: 12,
                    lineHeight: 1.6,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    fontFamily:
                      "var(--font-mono), ui-monospace, SFMono-Regular, Consolas, monospace",
                    color: "#1f1b16",
                  }}
                >
                  {transcript}
                </pre>
              )}
            </div>
          )}

          <div
            style={{
              padding: "12px 14px",
              background: "#fff",
              border: "1px solid #e5d8c3",
              borderRadius: 8,
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
              <span
                style={{ fontSize: 12, fontWeight: 700, color: "#4a3f35" }}
              >
                📋 항목별 평가 · 사람 검수 입력
              </span>
              <span style={{ fontSize: 10, color: "#71717a" }}>
                항목을 펼치면 LLM 판정·감점·근거가 보이고 그 자리에서 점수 확정 가능
              </span>
              <label
                style={{
                  marginLeft: "auto",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#4a3f35",
                  cursor: "pointer",
                  userSelect: "none",
                }}
                title="만점 항목은 숨기고 검수 필요 항목만 봅니다 (force_t3/신뢰도≤2 는 만점이어도 항상 표시)"
              >
                <input
                  type="checkbox"
                  checked={onlyNonPerfect}
                  onChange={(e) => setOnlyNonPerfect(e.target.checked)}
                  style={{ accentColor: "#c96442" }}
                />
                만점 숨기기 ({visibleItems.length}/{flatItems.length})
              </label>
            </div>

            {hitlError && (
              <div
                style={{
                  fontSize: 11,
                  color: "#b91c1c",
                  background: "#fee2e2",
                  padding: "6px 10px",
                  border: "1px solid #fca5a5",
                  borderRadius: 6,
                }}
              >
                HITL 큐 로드 실패: {hitlError} · 새로고침 하면 다시 시도합니다.
              </div>
            )}

            {hitlLoading && hitlRows.length === 0 && (
              <div style={{ fontSize: 11, color: "#71717a" }}>
                검수 큐 불러오는 중…
              </div>
            )}

            {!hitlLoading && hitlRows.length === 0 && flatItems.length > 0 && (
              <div
                style={{
                  fontSize: 11,
                  color: "#92400e",
                  background: "#fef3c7",
                  padding: "8px 12px",
                  border: "1px dashed #fcd34d",
                  borderRadius: 6,
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  flexWrap: "wrap",
                }}
              >
                <span>
                  ℹ HITL 큐 동기화 대기 중 (populator DB commit). 자동으로 재조회합니다
                  {hitlSyncAttempt > 0 && (
                    <span style={{ fontWeight: 600, marginLeft: 4 }}>
                      — 시도 {hitlSyncAttempt}/5
                    </span>
                  )}
                  . 사람 점수는 입력창에 직접 적어 확정하면 신규 등록됩니다.
                </span>
                <button
                  type="button"
                  onClick={reloadHitl}
                  className="btn-ghost"
                  style={{
                    marginLeft: "auto",
                    fontSize: 11,
                    padding: "3px 10px",
                    fontWeight: 700,
                    color: "#92400e",
                    borderColor: "#fcd34d",
                  }}
                >
                  ↻ 재조회
                </button>
              </div>
            )}

            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {visibleItems.map((it) => (
                <ReviewItemCard
                  key={`${it.category}-${it.item_number}`}
                  item={it}
                  category={it.category}
                  hitlRow={hitlByNum.get(Number(it.item_number))}
                  transcript={transcript}
                  turns={turns}
                  consultationId={consultationId}
                  onChanged={loadHitl}
                  onToast={setToast}
                />
              ))}
              {onlyNonPerfect && visibleItems.length === 0 && (
                <div
                  style={{
                    padding: "20px 12px",
                    textAlign: "center",
                    fontSize: 12,
                    color: "#16a34a",
                    background: "#f0fdf4",
                    border: "1px dashed #86efac",
                    borderRadius: 6,
                  }}
                >
                  ✅ 모든 항목 만점 — 검수 대상이 없습니다.
                  {perfectHidden > 0 && (
                    <span style={{ color: "#71717a", marginLeft: 6 }}>
                      ({perfectHidden}개 숨김)
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
