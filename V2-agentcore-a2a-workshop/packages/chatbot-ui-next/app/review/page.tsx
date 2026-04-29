// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import StatusLight from "@/components/StatusLight";
import { bulkConfirm, bulkRevert, fetchReviewQueue } from "@/lib/api";
import { groupByConsultation } from "@/lib/group";
import { useToast } from "@/lib/toast";
import type { ReviewItem } from "@/lib/types";

type StatusFilter = "pending" | "confirmed" | "all";

export default function ReviewQueuePage() {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [forceT3Only, setForceT3Only] = useState(false);
  const [sortDesc, setSortDesc] = useState(true);
  const [bulkBusyId, setBulkBusyId] = useState<string | null>(null);
  const toast = useToast();

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await fetchReviewQueue({
        status: statusFilter,
        force_t3_only: forceT3Only,
        limit: 500,
      });
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [statusFilter, forceT3Only]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
  }, [refresh]);

  const consultations = useMemo(
    () => groupByConsultation(items, sortDesc),
    [items, sortDesc],
  );

  const totalPending = consultations.reduce((s, c) => s + c.pendingCount, 0);
  const totalConfirmed = consultations.reduce((s, c) => s + c.confirmedCount, 0);
  const filterLabel =
    statusFilter === "pending"
      ? "검토 대기"
      : statusFilter === "confirmed"
        ? "확정 완료"
        : "전체";

  const handleBulkConfirm = useCallback(
    async (cid: string, pendingCount: number) => {
      const msg =
        `상담 ${cid} 의 대기 ${pendingCount}건을 전체 확정하시겠습니까?\n\n` +
        `· 사람 점수가 비어있는 항목은 AI 점수를 그대로 승인합니다\n` +
        `· 이미 확정된 항목은 건너뜁니다\n` +
        `· 확정 완료 후 ~/Desktop/QA평가결과/HITL_수정/${cid}.json 스냅샷 저장`;
      if (!window.confirm(msg)) return;
      setBulkBusyId(cid);
      try {
        const r = await bulkConfirm(cid, { accept_ai_score: true, overwrite: false });
        toast.success("전체 검토 완료", {
          description: `확정 ${r.confirmed}건 · AI 자동채움 ${r.filled_from_ai}건 · 건너뜀 ${r.skipped}건${r.snapshot_path ? `\n${r.snapshot_path}` : ""}`,
        });
        await refresh();
      } catch (err: unknown) {
        toast.error("전체 확정 실패", {
          description: err instanceof Error ? err.message : String(err),
        });
      } finally {
        setBulkBusyId(null);
      }
    },
    [refresh, toast],
  );

  const handleBulkRevert = useCallback(
    async (cid: string, confirmedCount: number) => {
      const reason = window.prompt(
        `상담 ${cid} 의 확정 ${confirmedCount}건을 전체 검수 취소하시겠습니까?\n\n사유 (선택 — 각 항목 비고에 기록됨)`,
        "",
      );
      if (reason === null) return;
      setBulkBusyId(cid);
      try {
        const r = await bulkRevert(cid, { reason });
        toast.success("전체 검수 취소", {
          description: `${r.reverted}건 → 대기로 복귀${r.snapshot_path ? `\n${r.snapshot_path}` : ""}`,
        });
        await refresh();
      } catch (err: unknown) {
        toast.error("전체 취소 실패", {
          description: err instanceof Error ? err.message : String(err),
        });
      } finally {
        setBulkBusyId(null);
      }
    },
    [refresh, toast],
  );

  return (
    <div className="mx-auto max-w-[1400px] flex flex-col gap-4">
      {/* 브레드크럼 — 큼직한 pill 버튼. 탭바 없는 페이지에서 뒤로 돌아가기 명확하게. */}
      <nav
        aria-label="페이지 경로"
        className="flex items-center gap-2 flex-wrap"
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
            color: "var(--ink-display)",
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 9999,
            textDecoration: "none",
            transition: "all 0.15s ease",
            boxShadow: "0 1px 2px rgba(0,0,0,0.03)",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "var(--accent-bg)";
            e.currentTarget.style.borderColor = "var(--accent)";
            e.currentTarget.style.color = "var(--accent)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "var(--surface)";
            e.currentTarget.style.borderColor = "var(--border)";
            e.currentTarget.style.color = "var(--ink-display)";
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
        <span
          aria-hidden="true"
          style={{ color: "var(--ink-subtle)", fontSize: 16, userSelect: "none" }}
        >
          /
        </span>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "7px 14px",
            fontSize: 13,
            fontWeight: 700,
            color: "var(--accent)",
            background: "var(--accent-bg)",
            border: "1px solid var(--accent)",
            borderRadius: 9999,
            letterSpacing: "-0.01em",
          }}
        >
          검토 큐
        </span>
      </nav>

      <section className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="badge badge-accent">HITL</span>
          <span className="badge badge-outline">{filterLabel}</span>
        </div>
        <h1 className="text-[26px] font-bold tracking-[-0.02em]">검토 큐</h1>
        <p className="text-[13px] text-[var(--ink-muted)]">
          {loading
            ? "불러오는 중…"
            : `상담 ${consultations.length}건 · 항목 ${items.length}건 (대기 ${totalPending} / 확정 ${totalConfirmed})`}
        </p>
      </section>

      {/* Filters */}
      <div className="card card-padded-sm flex flex-wrap items-center gap-2.5">
        <div className="flex items-center gap-2">
          <span className="section-label">상태</span>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            title="DB human_reviews.status 기준 필터"
            className="input-field input-sm"
            style={{ width: "auto" }}
          >
            <option value="all">전체</option>
            <option value="pending">검토 대기</option>
            <option value="confirmed">확정 완료</option>
          </select>
        </div>

        <label className="flex items-center gap-1.5 text-[12px] font-medium text-[var(--danger)] cursor-pointer select-none">
          <input
            type="checkbox"
            checked={forceT3Only}
            onChange={(e) => setForceT3Only(e.target.checked)}
            className="accent-[var(--danger)]"
          />
          force_t3 만
        </label>

        <div className="flex items-center gap-1.5 ml-auto">
          <button
            type="button"
            onClick={() => setSortDesc((v) => !v)}
            className="btn-secondary btn-sm"
          >
            정렬 {sortDesc ? "▼ 최신" : "▲ 과거"}
          </button>
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            className="btn-secondary btn-sm"
          >
            {loading ? (
              <>
                <span className="spinner" style={{ width: 10, height: 10 }} />
                로딩
              </>
            ) : (
              "새로고침"
            )}
          </button>
        </div>
      </div>

      {error && (
        <div
          style={{
            padding: "10px 14px",
            background: "var(--danger-bg)",
            border: "1px solid var(--danger-border)",
            color: "var(--danger)",
            borderRadius: "var(--radius-sm)",
            fontSize: 12,
          }}
        >
          로드 실패: {error}
        </div>
      )}

      {consultations.length === 0 && !loading && (
        <div className="empty-state">
          <svg
            className="empty-state-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2M9 12l2 2 4-4"
            />
          </svg>
          <div className="empty-state-title">검토할 상담이 없습니다</div>
          <div className="empty-state-desc">
            평가를 실행하면 확신도가 낮은 항목이 자동으로 이 큐에 등록됩니다.{" "}
            <Link href="/" className="text-[var(--accent)] underline">
              파이프라인으로 이동 →
            </Link>
          </div>
        </div>
      )}

      {consultations.length > 0 && (
        <div className="panel">
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "260px 1fr 80px 80px 90px 110px 100px 100px",
              padding: "14px 16px",
              background: "var(--surface-sunken)",
              fontSize: 13,
              fontWeight: 700,
              color: "var(--ink-soft)",
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              borderBottom: "1px solid var(--border)",
              gap: 10,
            }}
          >
            <span>상담 ID</span>
            <span>생성일 (가장 빠른)</span>
            <span style={{ textAlign: "center" }}>항목</span>
            <span style={{ textAlign: "center" }} title="force_t3 항목 수">
              ⚠ T3
            </span>
            <span
              style={{ textAlign: "center" }}
              title="낮은 confidence 항목 수"
            >
              Low Conf.
            </span>
            <span style={{ textAlign: "center" }}>대기/확정</span>
            <span style={{ textAlign: "right" }}>AI 합계</span>
            <span style={{ textAlign: "center" }}>상세</span>
          </div>
          {consultations.map((con) => {
            const allDone = con.confirmedCount > 0 && con.pendingCount === 0;
            const partialDone = con.confirmedCount > 0 && !allDone;
            const rowBg = allDone
              ? "var(--success-bg)"
              : partialDone
                ? "var(--surface-muted)"
                : con.forceT3Count > 0
                  ? "var(--danger-bg)"
                  : "transparent";
            const borderLeft = allDone
              ? "3px solid var(--success)"
              : "3px solid transparent";
            return (
              <div
                key={con.consultation_id}
                style={{
                  borderBottom: "1px solid var(--border-subtle)",
                  background: rowBg,
                  borderLeft,
                  transition: "background var(--dur) var(--ease)",
                }}
              >
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns:
                      "260px 1fr 80px 80px 90px 110px 100px 100px",
                    padding: "12px 16px",
                    gap: 10,
                    fontSize: 14,
                    alignItems: "center",
                    color: "var(--ink)",
                  }}
                >
                  <span
                    style={{
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      fontFamily: "var(--font-mono), monospace",
                      fontSize: 13,
                    }}
                  >
                    <StatusLight
                      pendingCount={con.pendingCount}
                      confirmedCount={con.confirmedCount}
                      forceT3Count={con.forceT3Count}
                    />
                    {con.consultation_id}
                  </span>
                  <span
                    style={{
                      color: "var(--ink-muted)",
                      fontSize: 13,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {con.createdAt || "-"}
                  </span>
                  <span style={{ textAlign: "center" }}>{con.items.length}</span>
                  <span
                    style={{
                      textAlign: "center",
                      color:
                        con.forceT3Count > 0
                          ? "var(--danger)"
                          : "var(--ink-muted)",
                      fontWeight: con.forceT3Count > 0 ? 700 : 400,
                    }}
                  >
                    {con.forceT3Count}
                  </span>
                  <span
                    style={{
                      textAlign: "center",
                      color:
                        con.lowConfCount > 0
                          ? "var(--danger)"
                          : "var(--ink-muted)",
                      fontWeight: con.lowConfCount > 0 ? 700 : 400,
                    }}
                  >
                    {con.lowConfCount}
                  </span>
                  <span style={{ textAlign: "center", fontSize: 13 }}>
                    <span style={{ color: "var(--warn)", fontWeight: 700 }}>
                      {con.pendingCount}
                    </span>
                    <span
                      style={{ color: "var(--ink-subtle)", margin: "0 4px" }}
                    >
                      /
                    </span>
                    <span style={{ color: "var(--success)", fontWeight: 700 }}>
                      {con.confirmedCount}
                    </span>
                    {con.confirmedCount > 0 && (
                      <span
                        className="badge badge-success"
                        title={`~/Desktop/QA평가결과/HITL_수정/${con.consultation_id}.json`}
                        style={{ marginLeft: 5, fontSize: 10, padding: "2px 6px" }}
                      >
                        저장
                      </span>
                    )}
                  </span>
                  <span
                    style={{
                      textAlign: "right",
                      fontVariantNumeric: "tabular-nums",
                      fontWeight: 600,
                    }}
                  >
                    {con.totalAi}
                  </span>
                  <span style={{ textAlign: "center" }}>
                    <Link
                      href={`/result/${encodeURIComponent(con.consultation_id)}`}
                      className="btn-secondary btn-sm"
                      style={{ padding: "5px 14px", fontSize: 13, fontWeight: 600 }}
                    >
                      열기 →
                    </Link>
                  </span>
                </div>
                <div
                  style={{
                    padding: "4px 12px 12px 12px",
                    display: "flex",
                    gap: 8,
                    flexWrap: "wrap",
                  }}
                >
                  {con.pendingCount > 0 && (
                    <button
                      type="button"
                      disabled={bulkBusyId === con.consultation_id}
                      onClick={() =>
                        handleBulkConfirm(
                          con.consultation_id,
                          con.pendingCount,
                        )
                      }
                      title={`대기 ${con.pendingCount}건 전체 확정 — 사람 점수 미입력 항목은 AI 점수로 승인`}
                      className="btn-primary btn-sm"
                      style={{
                        background: "var(--success)",
                        borderColor: "var(--success)",
                      }}
                    >
                      {bulkBusyId === con.consultation_id ? (
                        <>
                          <span className="spinner" />
                          확정 중
                        </>
                      ) : (
                        `✓ 전체 검토 완료 (대기 ${con.pendingCount}건)`
                      )}
                    </button>
                  )}
                  {con.pendingCount === 0 && con.confirmedCount > 0 && (
                    <span className="badge badge-success">
                      ✅ 전체 검토 완료 ({con.confirmedCount}건)
                    </span>
                  )}
                  {con.confirmedCount > 0 && (
                    <button
                      type="button"
                      disabled={bulkBusyId === con.consultation_id}
                      onClick={() =>
                        handleBulkRevert(
                          con.consultation_id,
                          con.confirmedCount,
                        )
                      }
                      title={`확정 ${con.confirmedCount}건을 전체 대기로 되돌림`}
                      className="btn-secondary btn-sm"
                      style={{
                        color: "var(--danger)",
                        borderColor: "var(--danger-border)",
                      }}
                    >
                      {bulkBusyId === con.consultation_id
                        ? "취소 중..."
                        : `↺ 전체 취소 (${con.confirmedCount}건)`}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
