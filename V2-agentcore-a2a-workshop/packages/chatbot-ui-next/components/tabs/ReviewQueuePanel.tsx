// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import StatusLight from "@/components/StatusLight";
import {
  bulkConfirm,
  bulkRevert,
  deleteConsultation,
  exportReviewQueueXlsx,
  fetchReviewQueue,
} from "@/lib/api";
import { groupByConsultation } from "@/lib/group";
import { useToast } from "@/lib/toast";
import type { ReviewItem } from "@/lib/types";

type StatusFilter = "pending" | "confirmed" | "all";

/**
 * ReviewQueuePanel — 기존 /review 페이지의 HITL 큐 로직을 탭 패널로 이관.
 *   /review 페이지는 계속 동작 (병행 운영 가능) — 이 패널은 탭 내부에서 동일 기능 제공.
 */
export function ReviewQueuePanel() {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [forceT3Only, setForceT3Only] = useState(false);
  const [sortDesc, setSortDesc] = useState(true);
  const [bulkBusyId, setBulkBusyId] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  const toast = useToast();

  const handleExport = useCallback(async () => {
    setExporting(true);
    try {
      // Dev7 의 wrapper: 서버가 xlsx 를 파일 시스템에 저장하고 경로 반환.
      const ret = await exportReviewQueueXlsx(statusFilter);
      if (!ret.ok) {
        toast.error("xlsx 내보내기 실패", {
          description: ret.error || "알 수 없는 오류",
        });
        return;
      }
      toast.success("xlsx 내보내기 완료", {
        description: `${ret.row_count ?? 0}건 · ${ret.path || ""}`,
        duration: 4000,
      });
    } catch (err) {
      toast.error("xlsx 내보내기 예외", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setExporting(false);
    }
  }, [statusFilter, toast]);

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

  const toggleOne = useCallback((cid: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(cid)) next.delete(cid);
      else next.add(cid);
      return next;
    });
  }, []);

  const toggleAll = useCallback(
    (checked: boolean) => {
      if (!checked) {
        setSelectedIds(new Set());
        return;
      }
      setSelectedIds(new Set(consultations.map((c) => c.consultation_id)));
    },
    [consultations],
  );

  const handleDeleteSelected = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    const msg =
      `선택한 상담 ${ids.length}건을 영구 삭제하시겠습니까?\n\n` +
      `· DB 의 human_reviews 행이 영구 삭제됩니다 (복구 불가)\n` +
      `· 평가 결과(/v2/result/full) 와 스냅샷 파일은 그대로 유지됩니다\n\n` +
      ids.slice(0, 10).map((id) => `  • ${id}`).join("\n") +
      (ids.length > 10 ? `\n  • … 외 ${ids.length - 10}건` : "");
    if (!window.confirm(msg)) return;

    setDeleting(true);
    let okCount = 0;
    let failCount = 0;
    let totalDeleted = 0;
    const failed: string[] = [];

    for (const cid of ids) {
      try {
        const r = await deleteConsultation(cid, { reason: "ui-bulk-delete" });
        if (r.ok) {
          okCount += 1;
          totalDeleted += r.deleted ?? 0;
        } else {
          failCount += 1;
          failed.push(cid);
        }
      } catch (err) {
        failCount += 1;
        failed.push(cid);
        console.error("deleteConsultation 실패", cid, err);
      }
    }

    setSelectedIds(new Set());
    setDeleting(false);

    if (failCount === 0) {
      toast.success(`${okCount}건 상담 삭제 완료`, {
        description: `총 ${totalDeleted}개 review 행 제거`,
      });
    } else {
      toast.error(`${failCount}건 삭제 실패`, {
        description: `성공 ${okCount} · 실패 ${failCount}${failed.length ? `\n실패: ${failed.slice(0, 5).join(", ")}` : ""}`,
      });
    }
    await refresh();
  }, [selectedIds, refresh, toast]);

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
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="badge badge-accent">HITL</span>
          <span className="badge badge-neutral">{filterLabel}</span>
        </div>
        <p className="text-[13px] text-[var(--ink-muted)]">
          {loading
            ? "불러오는 중…"
            : `상담 ${consultations.length}건 · 항목 ${items.length}건 (대기 ${totalPending} / 확정 ${totalConfirmed})`}
        </p>
      </section>

      {/* Filters */}
      <div className="card card-padded flex flex-wrap items-center gap-2.5">
        <div className="flex items-center gap-2">
          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: "var(--ink-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.04em",
            }}
          >
            상태
          </span>
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
            className="btn-ghost"
          >
            정렬 {sortDesc ? "▼ 최신" : "▲ 과거"}
          </button>
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            className="btn-ghost"
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
          <button
            type="button"
            onClick={handleExport}
            disabled={exporting || loading || consultations.length === 0}
            className="btn-ghost"
            title="현재 필터 기준 검토 큐를 xlsx 로 내보냅니다"
          >
            {exporting ? (
              <>
                <span className="spinner" style={{ width: 10, height: 10 }} />
                내보내는 중
              </>
            ) : (
              "📥 내보내기"
            )}
          </button>
          <button
            type="button"
            onClick={handleDeleteSelected}
            disabled={deleting || selectedIds.size === 0}
            className="btn-ghost"
            style={{
              color:
                selectedIds.size > 0 ? "var(--danger)" : "var(--ink-subtle)",
              borderColor:
                selectedIds.size > 0
                  ? "var(--danger-border)"
                  : "var(--border)",
            }}
            title="선택한 상담들을 DB 에서 영구 삭제 (복구 불가)"
          >
            {deleting ? (
              <>
                <span className="spinner" style={{ width: 10, height: 10 }} />
                삭제 중
              </>
            ) : (
              `🗑 선택 삭제${selectedIds.size > 0 ? ` (${selectedIds.size})` : ""}`
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
          <div className="empty-state-title">검토할 상담이 없습니다</div>
          <div className="empty-state-desc">
            평가를 실행하면 확신도가 낮은 항목이 자동으로 이 큐에 등록됩니다.
          </div>
        </div>
      )}

      {consultations.length > 0 && (
        <div className="panel">
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "32px 240px 1fr 70px 70px 70px 100px 90px 90px",
              padding: "12px 14px",
              background: "var(--surface-sunken)",
              fontSize: 11,
              fontWeight: 700,
              color: "var(--ink-soft)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              borderBottom: "1px solid var(--border)",
              gap: 8,
            }}
          >
            <span style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
              <input
                type="checkbox"
                aria-label="전체 선택"
                checked={
                  consultations.length > 0 &&
                  selectedIds.size === consultations.length
                }
                ref={(el) => {
                  if (el)
                    el.indeterminate =
                      selectedIds.size > 0 &&
                      selectedIds.size < consultations.length;
                }}
                onChange={(e) => toggleAll(e.target.checked)}
                className="accent-[var(--accent)]"
              />
            </span>
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
                      "32px 240px 1fr 70px 70px 70px 100px 90px 90px",
                    padding: "10px 12px",
                    gap: 8,
                    fontSize: 12,
                    alignItems: "center",
                    color: "var(--ink)",
                  }}
                >
                  <span
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <input
                      type="checkbox"
                      aria-label={`${con.consultation_id} 선택`}
                      checked={selectedIds.has(con.consultation_id)}
                      onChange={() => toggleOne(con.consultation_id)}
                      className="accent-[var(--accent)]"
                    />
                  </span>
                  <span
                    style={{
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      fontFamily: "var(--font-mono), monospace",
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
                      fontSize: 11,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {con.createdAt || "-"}
                  </span>
                  <span
                    className="tabular-nums"
                    style={{ textAlign: "center" }}
                  >
                    {con.items.length}
                  </span>
                  <span
                    className="tabular-nums"
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
                    className="tabular-nums"
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
                  <span
                    className="tabular-nums"
                    style={{ textAlign: "center", fontSize: 11 }}
                  >
                    <span style={{ color: "var(--warn)", fontWeight: 600 }}>
                      {con.pendingCount}
                    </span>
                    <span
                      style={{ color: "var(--ink-subtle)", margin: "0 3px" }}
                    >
                      /
                    </span>
                    <span style={{ color: "var(--success)", fontWeight: 600 }}>
                      {con.confirmedCount}
                    </span>
                    {con.confirmedCount > 0 && (
                      <span
                        className="badge badge-success"
                        title={`~/Desktop/QA평가결과/HITL_수정/${con.consultation_id}.json`}
                        style={{ marginLeft: 4, fontSize: 9, padding: "1px 5px" }}
                      >
                        저장
                      </span>
                    )}
                  </span>
                  <span
                    className="tabular-nums"
                    style={{
                      textAlign: "right",
                      fontWeight: 600,
                    }}
                  >
                    {con.totalAi}
                  </span>
                  <span style={{ textAlign: "center" }}>
                    <Link
                      href={`/result/${encodeURIComponent(con.consultation_id)}`}
                      className="btn-ghost"
                      style={{ padding: "3px 10px", fontSize: 11 }}
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
                      className="btn-primary"
                      style={{
                        background: "var(--success)",
                        borderColor: "var(--success)",
                        fontSize: 12,
                        padding: "4px 12px",
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
                      className="btn-ghost"
                      style={{
                        color: "var(--danger)",
                        borderColor: "var(--danger-border)",
                        fontSize: 12,
                        padding: "4px 12px",
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

export default ReviewQueuePanel;
