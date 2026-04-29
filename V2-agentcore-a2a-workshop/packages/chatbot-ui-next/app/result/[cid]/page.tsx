// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import DebateRecordCard from "@/components/DebateRecord";
import { extractTurnsFromPreprocessing } from "@/components/PostRunReviewModal";
import { HumanAiComparison } from "@/components/results/HumanAiComparison";
import ReviewItemCard from "@/components/ReviewItemCard";
import { fetchResultFull, fetchReviewQueue } from "@/lib/api";
import type {
  CategoryItem,
  DebateRecord,
  GtComparison,
  GtEvidenceComparison,
  Report,
  ReviewItem,
} from "@/lib/types";

interface TranscriptTurn {
  turn_id?: number;
  speaker?: string;
  text?: string;
  segment?: string;
}

interface FullState {
  loading: boolean;
  notFound: boolean;
  error: string;
  report: Report | null;
  gt: GtComparison | null;
  gtEv: GtEvidenceComparison | null;
  debates: Record<string, DebateRecord> | null;
  transcript: string | null;
  turns: TranscriptTurn[] | null;
}

const INITIAL_FULL: FullState = {
  loading: true,
  notFound: false,
  error: "",
  report: null,
  gt: null,
  gtEv: null,
  debates: null,
  transcript: null,
  turns: null,
};

const VERDICT_STYLES: Record<
  string,
  { bg: string; color: string; icon: string; label: string }
> = {
  match: { bg: "#dcfce7", color: "#166534", icon: "✅", label: "일치" },
  partial: { bg: "#fef3c7", color: "#92400e", icon: "⚠️", label: "부분일치" },
  mismatch: { bg: "#fee2e2", color: "#b91c1c", icon: "❌", label: "불일치" },
  insufficient: {
    bg: "#e5e7eb",
    color: "#4b5563",
    icon: "❓",
    label: "근거 부족",
  },
};

export default function ResultDetailPage() {
  const params = useParams<{ cid: string }>();
  const cid = decodeURIComponent(params?.cid || "");

  const [full, setFull] = useState<FullState>(INITIAL_FULL);
  const [hitlRows, setHitlRows] = useState<ReviewItem[]>([]);
  const [toast, setToast] = useState<{
    kind: "success" | "error";
    title: string;
    message: string;
  } | null>(null);
  // HITL 검수 동선 — 기본값은 "만점 아닌 것만" 표시 (검수 대상만 빠르게 훑기 위함).
  // 체크 해제 시 전체 항목 노출. force_t3/저신뢰도 항목은 만점이어도 항상 표시.
  const [onlyNonPerfect, setOnlyNonPerfect] = useState(true);
  const [transcriptOpen, setTranscriptOpen] = useState(false);

  const loadFull = useCallback(async () => {
    if (!cid) return;
    setFull((prev) => ({ ...prev, loading: true, error: "", notFound: false }));
    try {
      const r = await fetchResultFull(cid);
      if (!r.ok && "error" in r && r.error === "not_found") {
        setFull({
          loading: false,
          notFound: true,
          error: "",
          report: null,
          gt: null,
          gtEv: null,
          debates: null,
          transcript: null,
          turns: null,
        });
        return;
      }
      if (!r.ok || !("data" in r)) {
        throw new Error("invalid response");
      }
      const data = r.data || {};
      setFull({
        loading: false,
        notFound: false,
        error: "",
        report: data.report ?? null,
        gt: data.gt_comparison ?? null,
        gtEv: data.gt_evidence_comparison ?? null,
        debates: data.debates ?? null,
        transcript:
          typeof data.transcript === "string" ? data.transcript : null,
        turns: extractTurnsFromPreprocessing(
          (data as { preprocessing?: unknown }).preprocessing,
        ),
      });
    } catch (err: unknown) {
      setFull({
        loading: false,
        notFound: false,
        error: err instanceof Error ? err.message : String(err),
        report: null,
        gt: null,
        gtEv: null,
        debates: null,
        transcript: null,
        turns: null,
      });
    }
  }, [cid]);

  const loadHitl = useCallback(async () => {
    if (!cid) return;
    try {
      const r = await fetchReviewQueue({ status: "all", limit: 500 });
      setHitlRows(
        (r.items || []).filter((it) => it.consultation_id === cid),
      );
    } catch (err: unknown) {
      // 조용히 무시 — HITL row 가 없어도 결과 상세는 보여야 함
      console.error("hitl load failed:", err);
    }
  }, [cid]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadFull();
    loadHitl();
  }, [loadFull, loadHitl]);

  const hitlByNum = useMemo(() => {
    const m = new Map<number, ReviewItem>();
    for (const r of hitlRows) m.set(Number(r.item_number), r);
    return m;
  }, [hitlRows]);

  const finalScore = full.report?.final_score || {};
  const categories = full.report?.evaluation?.categories || [];
  const flatItems: Array<CategoryItem & { category: string }> =
    categories.flatMap((cat) =>
      (cat.items || []).map((it) => ({ ...it, category: cat.category })),
    );
  // 사용자 의도: "만점이면 검수할 게 없다" — 만점 여부를 최우선으로 판정.
  // force_t3 / 저신뢰도 플래그는 감점 원인이 있을 때만 의미 있으므로 만점이면 숨긴다.
  const nonPerfectItems = flatItems.filter((it) => {
    if (it.max_score == null) return true;
    return Number(it.score) < Number(it.max_score);
  });
  const visibleItems = onlyNonPerfect ? nonPerfectItems : flatItems;
  const perfectHiddenCount = flatItems.length - nonPerfectItems.length;

  const handleChanged = useCallback(() => {
    loadHitl();
  }, [loadHitl]);

  if (!cid) {
    return (
      <div style={{ padding: 20 }}>
        <Link href="/review">← 검토 큐로</Link>
        <div style={{ marginTop: 20 }}>잘못된 URL — consultation_id 가 없습니다.</div>
      </div>
    );
  }

  return (
    <div style={{ padding: 20, fontFamily: "Arial, sans-serif" }}>
      {/* 브레드크럼 — 홈 / 검토 큐 / 상담ID. 탭바 없는 페이지에서 명확한 네비. */}
      <nav
        aria-label="페이지 경로"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
          marginBottom: 14,
        }}
      >
        <Link
          href="/"
          aria-label="홈으로 이동"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "7px 14px",
            fontSize: 13,
            fontWeight: 600,
            color: "var(--ink-display, #14110d)",
            background: "var(--surface, #fff)",
            border: "1px solid var(--border, #ece8d8)",
            borderRadius: 9999,
            textDecoration: "none",
            transition: "all 0.15s ease",
            boxShadow: "0 1px 2px rgba(0,0,0,0.03)",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "var(--accent-bg, #f9efe8)";
            e.currentTarget.style.borderColor = "var(--accent, #c96442)";
            e.currentTarget.style.color = "var(--accent, #c96442)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "var(--surface, #fff)";
            e.currentTarget.style.borderColor = "var(--border, #ece8d8)";
            e.currentTarget.style.color = "var(--ink-display, #14110d)";
          }}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M15 18l-6-6 6-6" />
          </svg>
          <span>홈</span>
        </Link>
        <span aria-hidden="true" style={{ color: "#9a9583", fontSize: 16 }}>
          /
        </span>
        <Link
          href="/review"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "7px 14px",
            fontSize: 13,
            fontWeight: 600,
            color: "var(--ink-display, #14110d)",
            background: "var(--surface, #fff)",
            border: "1px solid var(--border, #ece8d8)",
            borderRadius: 9999,
            textDecoration: "none",
            transition: "all 0.15s ease",
            boxShadow: "0 1px 2px rgba(0,0,0,0.03)",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "var(--accent-bg, #f9efe8)";
            e.currentTarget.style.borderColor = "var(--accent, #c96442)";
            e.currentTarget.style.color = "var(--accent, #c96442)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "var(--surface, #fff)";
            e.currentTarget.style.borderColor = "var(--border, #ece8d8)";
            e.currentTarget.style.color = "var(--ink-display, #14110d)";
          }}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M15 18l-6-6 6-6" />
          </svg>
          <span>검토 큐</span>
        </Link>
        <span aria-hidden="true" style={{ color: "#9a9583", fontSize: 16 }}>
          /
        </span>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "7px 14px",
            fontSize: 13,
            fontWeight: 700,
            color: "var(--accent, #c96442)",
            background: "var(--accent-bg, #f9efe8)",
            border: "1px solid var(--accent, #c96442)",
            borderRadius: 9999,
            fontFamily: "monospace",
          }}
        >
          {cid}
        </span>
        <button
          type="button"
          onClick={() => {
            loadFull();
            loadHitl();
          }}
          style={{
            marginLeft: "auto",
            fontSize: 12,
            padding: "6px 12px",
            background: "#fff",
            border: "1px solid #e5d8c3",
            borderRadius: 6,
            cursor: "pointer",
            fontWeight: 600,
          }}
        >
          ↻ 새로고침
        </button>
      </nav>

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
            marginBottom: 10,
            whiteSpace: "pre-wrap",
            cursor: "pointer",
          }}
        >
          <b>{toast.title}</b> · {toast.message}
          <span style={{ float: "right", opacity: 0.7 }}>× 닫기</span>
        </div>
      )}

      {full.loading && (
        <div style={{ fontSize: 12, color: "#71717a" }}>불러오는 중...</div>
      )}

      {full.notFound && (
        <div
          style={{
            fontSize: 11,
            color: "#92400e",
            background: "#fef3c7",
            padding: "8px 12px",
            border: "1px solid #fcd34d",
            borderRadius: 6,
            marginBottom: 10,
          }}
        >
          ℹ 이 상담의 풀 결과 JSON 이 없습니다. 파이프라인이 결과를 저장하지 않은
          구 버전 실행 건일 수 있습니다. 아래 HITL 편집은 정상 동작합니다.
        </div>
      )}

      {full.error && (
        <div
          style={{
            fontSize: 11,
            color: "#b91c1c",
            background: "#fee2e2",
            padding: "8px 12px",
            border: "1px solid #fca5a5",
            borderRadius: 6,
            marginBottom: 10,
          }}
        >
          풀 결과 로드 실패: {full.error}
        </div>
      )}

      {!full.loading && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {full.report && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 16,
                padding: "12px 16px",
                background: "#fff",
                border: "1px solid #e5d8c3",
                borderRadius: 8,
                flexWrap: "wrap",
              }}
            >
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: "#6b5b48",
                  letterSpacing: 0.3,
                }}
              >
                QA 종합 평가
              </div>
              <div
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  gap: 6,
                  padding: "4px 12px",
                  background: "#fef3c7",
                  border: "1px solid #fcd34d",
                  borderRadius: 6,
                }}
              >
                <span
                  style={{
                    fontSize: 22,
                    fontWeight: 800,
                    color: "#78350f",
                  }}
                >
                  {finalScore.grade || "-"}
                </span>
                <span
                  style={{
                    fontSize: 13,
                    fontWeight: 700,
                    color: "#92400e",
                  }}
                >
                  {finalScore.after_overrides ?? finalScore.raw_total ?? "-"} /
                  100
                </span>
              </div>
              {full.report.routing?.decision && (
                <div
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    padding: "3px 8px",
                    borderRadius: 10,
                    background: "#e0e7ff",
                    color: "#3730a3",
                  }}
                >
                  {full.report.routing.decision}
                </div>
              )}
              <div style={{ flex: 1 }} />
              <div style={{ fontSize: 10, color: "#71717a" }}>
                {full.report.evaluated_at || "-"} · {full.report.tenant || "-"}
              </div>
            </div>
          )}

          {/* Task #7 — 사람-AI 비교 (점수 카드 바로 아래, 다른 비교 블록보다 먼저) */}
          <HumanAiComparison consultationId={cid} />

          {full.gt && full.gt.items && full.gt.items.length > 0 && (
            <GtScoreBlock gt={full.gt} cid={cid} />
          )}

          {full.gtEv && full.gtEv.items && full.gtEv.items.length > 0 && (
            <GtEvidenceBlock gtEv={full.gtEv} />
          )}

          {full.debates && Object.keys(full.debates).length > 0 && (
            <DebateList debates={full.debates} />
          )}

          {full.transcript && (
            <TranscriptBlock
              text={full.transcript}
              open={transcriptOpen}
              onToggle={() => setTranscriptOpen((v) => !v)}
            />
          )}

          {flatItems.length > 0 && (
            <div
              style={{
                padding: "12px 16px",
                background: "#fff",
                border: "1px solid #e5d8c3",
                borderRadius: 8,
              }}
            >
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 700,
                  color: "#4a3f35",
                  marginBottom: 6,
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  flexWrap: "wrap",
                }}
              >
                📋 항목별 평가 상세 · 사람 검수 입력
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 400,
                    color: "#71717a",
                  }}
                >
                  — 항목을 클릭하면 LLM 판정·근거·앙상블과 함께 사람 점수/비고
                  입력창이 펼쳐집니다
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
                  title="만점 항목은 숨겨 검수 필요 항목만 빠르게 훑을 수 있습니다 (force_t3/신뢰도≤2 는 만점이어도 항상 표시)"
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
              <div
                style={{
                  fontSize: 10,
                  color: "#71717a",
                  marginBottom: 10,
                  lineHeight: 1.5,
                }}
              >
                <span style={{ color: "#dc2626" }}>●</span> force_t3 ·{" "}
                <span style={{ color: "#b91c1c" }}>⚠</span> 신뢰도≤2 · 사람 점수
                수정 시 AI 점수와 다르면 주황색 강조
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {visibleItems.map((it) => (
                  <ReviewItemCard
                    key={`${it.category}-${it.item_number}`}
                    item={it}
                    category={it.category}
                    hitlRow={hitlByNum.get(Number(it.item_number))}
                    transcript={full.transcript}
                    turns={full.turns}
                    consultationId={cid}
                    onChanged={handleChanged}
                    onToast={setToast}
                  />
                ))}
                {onlyNonPerfect && visibleItems.length === 0 && (
                  <div
                    style={{
                      padding: "16px 12px",
                      textAlign: "center",
                      fontSize: 12,
                      color: "#16a34a",
                      background: "#f0fdf4",
                      border: "1px dashed #86efac",
                      borderRadius: 6,
                    }}
                  >
                    ✅ 모든 항목 만점 — 검수 대상이 없습니다.
                    {perfectHiddenCount > 0 && (
                      <span style={{ color: "#71717a", marginLeft: 6 }}>
                        ({perfectHiddenCount}개 숨김)
                      </span>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}

          {flatItems.length === 0 && !full.notFound && !full.loading && hitlRows.length > 0 && (
            <HitlOnlyList rows={hitlRows} onChanged={handleChanged} onToast={setToast} />
          )}
        </div>
      )}
    </div>
  );
}

function GtScoreBlock({ gt, cid }: { gt: GtComparison; cid: string }) {
  return (
    <div
      style={{
        padding: "12px 16px",
        background: "#fff",
        border: "1px solid #e5d8c3",
        borderRadius: 8,
      }}
    >
      <div
        style={{
          fontSize: 12,
          fontWeight: 700,
          color: "#4a3f35",
          marginBottom: 8,
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        📊 AI vs 사람 QA 점수 비교
        <span style={{ fontSize: 10, fontWeight: 400, color: "#71717a" }}>
          — 상담ID {gt.sample_id || cid} · 업무 정확도 (#15, #16) 제외
        </span>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))",
          gap: 8,
          marginBottom: 10,
        }}
      >
        {[
          { label: "AI 합계", value: gt.ai_total ?? "-", color: "#1e3a8a" },
          { label: "사람 합계", value: gt.gt_total ?? "-", color: "#166534" },
          {
            label: "차이 (AI-사람)",
            value:
              gt.diff != null ? (gt.diff > 0 ? `+${gt.diff}` : gt.diff) : "-",
            color: "#b91c1c",
          },
          {
            label: "MAE",
            value: gt.mae != null ? Number(gt.mae).toFixed(2) : "-",
            color: "#78350f",
          },
          {
            label: "RMSE",
            value: gt.rmse != null ? Number(gt.rmse).toFixed(3) : "-",
            color: "#78350f",
          },
          {
            label: "일치 / 불일치",
            value: `${gt.match_count ?? "-"} / ${gt.mismatch_count ?? "-"}`,
            color: "#4a3f35",
          },
        ].map((m) => (
          <div
            key={m.label}
            style={{
              padding: "6px 10px",
              background: "#fdfaf2",
              border: "1px solid #e5d8c3",
              borderRadius: 4,
              textAlign: "center",
            }}
          >
            <div style={{ fontSize: 9, fontWeight: 600, color: "#71717a" }}>
              {m.label}
            </div>
            <div
              style={{
                fontSize: 14,
                fontWeight: 800,
                color: m.color,
                marginTop: 2,
              }}
            >
              {m.value}
            </div>
          </div>
        ))}
      </div>
      <div
        style={{
          fontSize: 10,
          color: "#71717a",
          marginBottom: 6,
          fontStyle: "italic",
        }}
      >
        MAE = Σ|AI−사람| / 항목수 · RMSE = √(Σ(AI−사람)² / 항목수)
      </div>
      <div
        style={{
          border: "1px solid #e5d8c3",
          borderRadius: 4,
          overflow: "hidden",
          background: "#fff",
        }}
      >
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "50px 1fr 60px 70px 70px",
            padding: "5px 10px",
            background: "#f5efe0",
            fontSize: 10,
            fontWeight: 700,
            color: "#4a3f35",
            borderBottom: "1px solid #e5d8c3",
          }}
        >
          <span>#</span>
          <span>항목</span>
          <span style={{ textAlign: "right" }}>AI</span>
          <span style={{ textAlign: "right" }}>사람</span>
          <span style={{ textAlign: "center" }}>차이</span>
        </div>
        {(gt.items || []).map((row, idx) => {
          const delta =
            row.ai_score != null && row.gt_score != null
              ? Number(row.ai_score) - Number(row.gt_score)
              : null;
          const isExcluded =
            row.excluded || row.item_number === 15 || row.item_number === 16;
          return (
            <div
              key={idx}
              style={{
                display: "grid",
                gridTemplateColumns: "50px 1fr 60px 70px 70px",
                padding: "5px 10px",
                fontSize: 11,
                borderBottom: "1px solid #efe4cf",
                background:
                  delta !== 0 && delta != null && !isExcluded
                    ? "#fef3c7"
                    : "transparent",
              }}
            >
              <span style={{ fontWeight: 600 }}>#{row.item_number}</span>
              <span
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {row.item_name || "-"} ({row.max_score || "-"})
              </span>
              <span
                style={{
                  textAlign: "right",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {row.ai_score ?? "-"}
              </span>
              <span
                style={{
                  textAlign: "right",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {row.gt_score ?? "-"}
              </span>
              <span
                style={{
                  textAlign: "center",
                  fontWeight: 700,
                  color: isExcluded
                    ? "#71717a"
                    : delta === 0
                      ? "#166534"
                      : (delta ?? 0) > 0
                        ? "#b91c1c"
                        : "#1d4ed8",
                }}
              >
                {isExcluded
                  ? "제외"
                  : delta == null
                    ? "-"
                    : delta > 0
                      ? `+${delta}`
                      : delta}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function GtEvidenceBlock({ gtEv }: { gtEv: GtEvidenceComparison }) {
  return (
    <div
      style={{
        padding: "12px 16px",
        background: "#fff",
        border: "1px solid #e5d8c3",
        borderRadius: 8,
      }}
    >
      <div
        style={{
          fontSize: 12,
          fontWeight: 700,
          color: "#4a3f35",
          marginBottom: 8,
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        🔍 AI vs 사람 QA 근거 비교 (LLM 판정)
        <span style={{ fontSize: 10, fontWeight: 400, color: "#71717a" }}>
          — 항목별 근거 텍스트가 동일 사실/구간을 가리키는지 LLM 비교
        </span>
      </div>
      {gtEv.summary && (
        <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
          {[
            { label: "총 비교", value: gtEv.summary.total ?? "-", color: "#4a3f35" },
            { label: "일치", value: gtEv.summary.match ?? "-", color: "#166534" },
            { label: "부분일치", value: gtEv.summary.partial ?? "-", color: "#ca8a04" },
            { label: "불일치", value: gtEv.summary.mismatch ?? "-", color: "#b91c1c" },
            {
              label: "일치율",
              value:
                gtEv.summary.match_rate != null
                  ? `${gtEv.summary.match_rate}%`
                  : "-",
              color: "#1e3a8a",
            },
          ].map((m) => (
            <div
              key={m.label}
              style={{
                padding: "4px 10px",
                background: "#fdfaf2",
                border: "1px solid #e5d8c3",
                borderRadius: 4,
                minWidth: 70,
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: 9, fontWeight: 600, color: "#71717a" }}>
                {m.label}
              </div>
              <div style={{ fontSize: 13, fontWeight: 800, color: m.color }}>
                {m.value}
              </div>
            </div>
          ))}
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {(gtEv.items || []).map((row, idx) => {
          const vs = VERDICT_STYLES[row.verdict || ""] || {
            bg: "#e5e7eb",
            color: "#4b5563",
            icon: "·",
            label: row.verdict || "-",
          };
          return (
            <div
              key={idx}
              style={{
                padding: "8px 12px",
                background: "#fdfaf2",
                border: "1px solid #e5d8c3",
                borderRadius: 4,
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  marginBottom: 4,
                  flexWrap: "wrap",
                }}
              >
                <span style={{ fontSize: 11, fontWeight: 700, color: "#4a3f35" }}>
                  #{row.item_number}
                </span>
                <span style={{ fontSize: 11, color: "#27272a" }}>
                  {row.item_name || "-"}
                </span>
                <span style={{ fontSize: 10, color: "#71717a" }}>
                  AI {row.ai_score ?? "-"} / 사람 {row.gt_score ?? "-"}
                </span>
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    padding: "2px 8px",
                    borderRadius: 10,
                    background: vs.bg,
                    color: vs.color,
                  }}
                >
                  {vs.icon} {vs.label}
                </span>
              </div>
              {row.reasoning && (
                <div
                  style={{
                    fontSize: 11,
                    color: "#4a3f35",
                    lineHeight: 1.5,
                    paddingLeft: 8,
                    borderLeft: "2px solid #d9c9b3",
                  }}
                >
                  {row.reasoning}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function HitlOnlyList({
  rows,
  onChanged,
  onToast,
}: {
  rows: ReviewItem[];
  onChanged: () => void;
  onToast: (t: {
    kind: "success" | "error";
    title: string;
    message: string;
  }) => void;
}) {
  return (
    <div
      style={{
        padding: "12px 16px",
        background: "#fff",
        border: "1px solid #e5d8c3",
        borderRadius: 8,
      }}
    >
      <div
        style={{
          fontSize: 12,
          fontWeight: 700,
          color: "#4a3f35",
          marginBottom: 8,
        }}
      >
        📋 HITL 항목 (결과 JSON 없음 — HITL 행만 표시)
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {rows.map((r) => {
          const fauxItem: CategoryItem = {
            item_number: r.item_number,
            item: `항목 #${r.item_number}`,
            score: Number(r.ai_score) || 0,
            max_score: 0,
            judgment: r.ai_judgment || undefined,
            evidence: Array.isArray(r.ai_evidence)
              ? r.ai_evidence
              : typeof r.ai_evidence === "string" && r.ai_evidence
                ? [r.ai_evidence]
                : undefined,
            confidence:
              r.ai_confidence != null ? { final: Number(r.ai_confidence) } : undefined,
            force_t3: !!r.force_t3,
          };
          return (
            <ReviewItemCard
              key={r.id}
              item={fauxItem}
              category="-"
              hitlRow={r}
              onChanged={onChanged}
              onToast={onToast}
            />
          );
        })}
      </div>
    </div>
  );
}

function DebateList({ debates }: { debates: Record<string, DebateRecord> }) {
  const entries = useMemo(() => {
    const list = Object.values(debates).filter(
      (d): d is DebateRecord => !!d && typeof d === "object",
    );
    list.sort((a, b) => Number(a.item_number) - Number(b.item_number));
    return list;
  }, [debates]);
  if (entries.length === 0) return null;
  const convergedN = entries.filter((e) => e.converged).length;
  return (
    <div
      style={{
        padding: "12px 16px",
        background: "#fff",
        border: "1px solid #e5d8c3",
        borderRadius: 8,
      }}
    >
      <div
        style={{
          fontSize: 12,
          fontWeight: 700,
          color: "#4a3f35",
          marginBottom: 10,
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        🗣️ AG2 토론 기록 · 항목 {entries.length}건
        <span style={{ fontSize: 10, fontWeight: 400, color: "#71717a" }}>
          — 수렴 {convergedN} / 미수렴 {entries.length - convergedN} · 항목별
          카드를 펼쳐서 라운드·페르소나 발언·모더레이터 판정을 확인
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {entries.map((rec) => (
          <DebateRecordCard key={rec.item_number} record={rec} />
        ))}
      </div>
    </div>
  );
}

function TranscriptBlock({
  text,
  open,
  onToggle,
}: {
  text: string;
  open: boolean;
  onToggle: () => void;
}) {
  const lineCount = text.split("\n").length;
  const charCount = text.length;
  return (
    <div
      style={{
        padding: "12px 16px",
        background: "#fff",
        border: "1px solid #e5d8c3",
        borderRadius: 8,
      }}
    >
      <button
        type="button"
        onClick={onToggle}
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
          fontSize: 12,
          fontWeight: 700,
          color: "#4a3f35",
          textAlign: "left",
        }}
        aria-expanded={open}
      >
        <span style={{ display: "inline-block", width: 14 }}>
          {open ? "▾" : "▸"}
        </span>
        <span>🎧 상담 STT 전문</span>
        <span style={{ fontSize: 10, fontWeight: 400, color: "#71717a" }}>
          — {lineCount}줄 · {charCount.toLocaleString()}자
        </span>
        <span
          style={{
            marginLeft: "auto",
            fontSize: 10,
            fontWeight: 400,
            color: "#71717a",
          }}
        >
          {open ? "접기" : "펼쳐 보기"}
        </span>
      </button>
      {open && (
        <pre
          style={{
            marginTop: 10,
            padding: "12px 14px",
            background: "#fdfaf2",
            border: "1px solid #e5d8c3",
            borderRadius: 6,
            maxHeight: 420,
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
          {text}
        </pre>
      )}
    </div>
  );
}
