// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import StatusLight from "@/components/StatusLight";
import { useAppState } from "@/lib/AppStateContext";
import {
  bulkConfirm,
  bulkRevert,
  deleteConsultation,
  exportReviewQueueXlsx,
  fetchResultFull,
  fetchReviewQueue,
} from "@/lib/api";
import { groupByConsultation } from "@/lib/group";
import {
  buildResultJsonPayload,
  buildResultMarkdown,
} from "@/lib/resultExport";
import { useToast } from "@/lib/toast";
import type { EvaluationResult, ReviewItem } from "@/lib/types";
import { buildResultsXlsx } from "@/lib/xlsxExport";

type StatusFilter = "pending" | "confirmed" | "all";

/**
 * ★ 2026-05-07: ISO 8601 (예: "2026-05-07T15:18:53+09:00") 를 한국 친화 형식으로.
 * 백엔드 _now_iso() 가 이미 +09:00 으로 emit 하지만 ISO 가 읽기 어려움.
 * 출력 형식: "2026-05-07 15:18".
 */
function formatKstDateTime(s: string | null | undefined): string {
  if (!s) return "";
  try {
    const d = new Date(s);
    if (Number.isNaN(d.getTime())) return s;
    const fmt = new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    // ko-KR 출력: "2026. 05. 07. 15:18" → 정리해서 "2026-05-07 15:18"
    const parts = fmt.formatToParts(d).reduce<Record<string, string>>(
      (acc, p) => ((acc[p.type] = p.value), acc),
      {},
    );
    const yyyy = parts.year ?? "";
    const mm = parts.month ?? "";
    const dd = parts.day ?? "";
    const hh = parts.hour ?? "";
    const mn = parts.minute ?? "";
    if (yyyy && mm && dd && hh && mn) return `${yyyy}-${mm}-${dd} ${hh}:${mn}`;
    return s;
  } catch {
    return s;
  }
}

/**
 * 모델 ID 짧은 라벨로 변환. 예: "us.anthropic.claude-haiku-4-5-20251001-v1:0" → "Haiku 4.5".
 */
function shortModelLabel(modelId: string | null | undefined): string {
  if (!modelId) return "";
  const m = modelId.toLowerCase();
  if (m.includes("opus-4-7")) return "Opus 4.7";
  if (m.includes("opus-4-6")) return "Opus 4.6";
  if (m.includes("opus-4-5")) return "Opus 4.5";
  if (m.includes("sonnet-4-6")) return "Sonnet 4.6";
  if (m.includes("sonnet-4-5")) return "Sonnet 4.5";
  if (m.includes("sonnet-4")) return "Sonnet 4";
  if (m.includes("sonnet-3-7") || m.includes("3.7-sonnet")) return "Sonnet 3.7";
  if (m.includes("haiku-4-5")) return "Haiku 4.5";
  if (m.includes("haiku-3-5") || m.includes("3.5-haiku")) return "Haiku 3.5";
  if (m.includes("nova-2-lite")) return "Nova 2 Lite";
  if (m.includes("nova-2-omni")) return "Nova 2 Omni";
  if (m.includes("nova-2-sonic")) return "Nova 2 Sonic";
  if (m.includes("nova-pro")) return "Nova Pro";
  if (m.includes("nova-lite")) return "Nova Lite";
  if (m.includes("nova-micro")) return "Nova Micro";
  if (m.includes("nova-premier")) return "Nova Premier";
  if (m.includes("deepseek")) return "DeepSeek V3.2";
  if (m.includes("llama4-maverick") || m.includes("llama 4 maverick")) return "Llama 4 Maverick";
  if (m.includes("llama4-scout") || m.includes("llama 4 scout")) return "Llama 4 Scout";
  if (m.includes("llama3-3-70b") || m.includes("llama 3.3 70b")) return "Llama 3.3 70B";
  if (m.includes("gemma-3-27b")) return "Gemma 3 27B";
  if (m.includes("gemma-3-12b")) return "Gemma 3 12B";
  if (m.includes("gemma-3-4b")) return "Gemma 3 4B";
  if (m.includes("pixtral")) return "Pixtral Large";
  if (m.includes("qwen3-32b") || m.includes("qwen3 32b")) return "Qwen3 32B";
  if (m.includes("jamba")) return "Jamba 1.5";
  if (m.includes("glm-5") || m.includes("glm5")) return "GLM 5";
  if (m.includes("nemotron")) return "Nemotron";
  // ★ 2026-05-07 fallback: 마지막 경로 한 단계만 — `.` 으로 split 하지 않음.
  // 이전엔 `.` 도 split 해서 "deepseek.v3.2" → "2" 표시되는 버그.
  const parts = modelId.split("/");
  return parts[parts.length - 1] || modelId;
}

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
  const [downloadBusy, setDownloadBusy] = useState<"" | "xlsx" | "md" | "json">(
    "",
  );
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  const toast = useToast();
  const { state } = useAppState();
  const llmBackend = state.llmBackend;

  const triggerDownload = useCallback((blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 200);
  }, []);

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

  // 선택된 cid 목록. 0건 선택 시 전체 (현재 필터 적용된 consultations) 사용.
  const targetCids = useMemo(() => {
    return selectedIds.size > 0
      ? Array.from(selectedIds)
      : consultations.map((c) => c.consultation_id);
  }, [selectedIds, consultations]);

  const filterTag =
    statusFilter === "pending"
      ? "대기"
      : statusFilter === "confirmed"
        ? "확정"
        : "전체";

  /**
   * cid 목록 → 평가 결과 풀(/v2/result/full/{cid}) 병렬 fetch → 평가 결과 탭과
   * 동일한 xlsx/md/json 빌더 호출 → JSZip 으로 묶어 단일 .zip 다운로드.
   * 1건 선택 시에는 zip 우회 — 단일 파일 직접 다운로드.
   */
  const downloadAsZipOrSingle = useCallback(
    async (ext: "xlsx" | "md" | "json", cids: string[]): Promise<void> => {
      if (cids.length === 0) return;

      // 풀 결과 병렬 fetch — 8개 동시 제한 청크 처리.
      const CHUNK = 8;
      const results: { cid: string; full: EvaluationResult | null }[] = [];
      for (let i = 0; i < cids.length; i += CHUNK) {
        const slice = cids.slice(i, i + CHUNK);
        const chunkResults = await Promise.all(
          slice.map(async (cid) => {
            try {
              const r = await fetchResultFull(cid);
              // ConsultationFull → data 가 EvaluationResult. not_found 응답은 data 부재.
              if ("data" in r && r.data) {
                return { cid, full: r.data as EvaluationResult };
              }
              return { cid, full: null as EvaluationResult | null };
            } catch {
              return { cid, full: null as EvaluationResult | null };
            }
          }),
        );
        results.push(...chunkResults);
      }

      const ok = results.filter((r) => r.full) as {
        cid: string;
        full: EvaluationResult;
      }[];
      const fail = results.filter((r) => !r.full);
      if (ok.length === 0) {
        toast.error("다운로드 실패", {
          description: "평가 결과를 가져오지 못했습니다",
        });
        return;
      }
      if (fail.length > 0) {
        toast.warn(`${fail.length}건 result 없음 — 스킵`, {
          description: fail
            .slice(0, 5)
            .map((f) => f.cid)
            .join(", "),
        });
      }

      // 단건 — zip 우회.
      if (ok.length === 1) {
        const { cid, full } = ok[0];
        if (ext === "xlsx") {
          const out = await buildResultsXlsx(full, llmBackend);
          if (!out) {
            toast.error("xlsx 모듈 로드 실패", {
              description: "네트워크/패키지 확인",
            });
            return;
          }
          const blob = new Blob([out.buf], {
            type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          });
          triggerDownload(blob, `${cid}.xlsx`);
        } else if (ext === "md") {
          const md = buildResultMarkdown({
            result: full,
            llmBackend,
            consultationId: cid,
          });
          triggerDownload(
            new Blob([md], { type: "text/markdown;charset=utf-8" }),
            `${cid}.md`,
          );
        } else {
          const payload = buildResultJsonPayload({
            result: full,
            llmBackend,
            consultationId: cid,
          });
          triggerDownload(
            new Blob([JSON.stringify(payload, null, 2)], {
              type: "application/json",
            }),
            `${cid}.json`,
          );
        }
        return;
      }

      // 다건 — zip 묶음.
      const JSZip = (await import("jszip")).default;
      const zip = new JSZip();
      for (const { cid, full } of ok) {
        if (ext === "xlsx") {
          const out = await buildResultsXlsx(full, llmBackend);
          if (out) zip.file(`${cid}.xlsx`, out.buf);
        } else if (ext === "md") {
          zip.file(
            `${cid}.md`,
            buildResultMarkdown({
              result: full,
              llmBackend,
              consultationId: cid,
            }),
          );
        } else {
          const payload = buildResultJsonPayload({
            result: full,
            llmBackend,
            consultationId: cid,
          });
          zip.file(`${cid}.json`, JSON.stringify(payload, null, 2));
        }
      }
      const blob = await zip.generateAsync({ type: "blob" });
      const ts = new Date()
        .toISOString()
        .replace(/:/g, "-")
        .replace(/\..+$/, "");
      triggerDownload(blob, `qa_review_queue_${filterTag}_${ts}.zip`);
    },
    [llmBackend, toast, triggerDownload, filterTag],
  );

  const handleDownloadXlsx = useCallback(async () => {
    if (targetCids.length === 0) return;
    setDownloadBusy("xlsx");
    try {
      await downloadAsZipOrSingle("xlsx", targetCids);
    } catch (err) {
      toast.error("xlsx 다운로드 실패", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setDownloadBusy("");
    }
  }, [targetCids, downloadAsZipOrSingle, toast]);

  const handleDownloadMd = useCallback(async () => {
    if (targetCids.length === 0) return;
    setDownloadBusy("md");
    try {
      await downloadAsZipOrSingle("md", targetCids);
    } catch (err) {
      toast.error("md 다운로드 실패", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setDownloadBusy("");
    }
  }, [targetCids, downloadAsZipOrSingle, toast]);

  const handleDownloadJson = useCallback(async () => {
    if (targetCids.length === 0) return;
    setDownloadBusy("json");
    try {
      await downloadAsZipOrSingle("json", targetCids);
    } catch (err) {
      toast.error("json 다운로드 실패", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setDownloadBusy("");
    }
  }, [targetCids, downloadAsZipOrSingle, toast]);

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
            title="현재 필터 기준 검토 큐를 xlsx 로 내보냅니다 (서버 파일시스템 저장)"
          >
            {exporting ? (
              <>
                <span className="spinner" style={{ width: 10, height: 10 }} />
                내보내는 중
              </>
            ) : (
              "📊 xlsx 내보내기"
            )}
          </button>
          <button
            type="button"
            onClick={() =>
              toggleAll(selectedIds.size !== consultations.length)
            }
            disabled={consultations.length === 0}
            className="btn-ghost"
            style={{ fontSize: 12 }}
            title="현재 필터에 보이는 상담 전체 선택/해제"
          >
            {selectedIds.size === consultations.length &&
            consultations.length > 0
              ? "선택 해제"
              : "전체 선택"}
          </button>
          {/* 브라우저 직접 다운로드 — xlsx / md / json (평가 결과 탭과 동일 빌더, cid 별 파일을 zip 으로 묶음. 단건 선택 시 zip 우회) */}
          <div
            style={{
              display: "inline-flex",
              border: "1px solid var(--border-strong)",
              borderRadius: "var(--radius-sm)",
              overflow: "hidden",
            }}
          >
            <button
              type="button"
              className="btn-ghost"
              title={
                selectedIds.size === 0
                  ? "현재 필터의 전체 상담을 zip 으로 다운로드 (cid별 .xlsx)"
                  : `선택한 ${selectedIds.size}건을 zip 으로 다운로드 (cid별 .xlsx) — 1건일 땐 단일 파일`
              }
              disabled={downloadBusy !== "" || targetCids.length === 0}
              onClick={handleDownloadXlsx}
              style={{
                borderRadius: 0,
                border: "none",
                fontSize: 12,
                padding: "4px 12px",
              }}
            >
              {downloadBusy === "xlsx"
                ? "내보내는 중..."
                : `⬇ xlsx (${targetCids.length}${selectedIds.size === 0 ? " · 전체" : ""})`}
            </button>
            <button
              type="button"
              className="btn-ghost"
              title={
                selectedIds.size === 0
                  ? "현재 필터의 전체 상담을 zip 으로 다운로드 (cid별 .md)"
                  : `선택한 ${selectedIds.size}건을 zip 으로 다운로드 (cid별 .md) — 1건일 땐 단일 파일`
              }
              disabled={downloadBusy !== "" || targetCids.length === 0}
              onClick={handleDownloadMd}
              style={{
                borderRadius: 0,
                border: "none",
                borderLeft: "1px solid var(--border-strong)",
                fontSize: 12,
                padding: "4px 12px",
              }}
            >
              {downloadBusy === "md"
                ? "..."
                : `⬇ md (${targetCids.length}${selectedIds.size === 0 ? " · 전체" : ""})`}
            </button>
            <button
              type="button"
              className="btn-ghost"
              title={
                selectedIds.size === 0
                  ? "현재 필터의 전체 상담을 zip 으로 다운로드 (cid별 .json)"
                  : `선택한 ${selectedIds.size}건을 zip 으로 다운로드 (cid별 .json) — 1건일 땐 단일 파일`
              }
              disabled={downloadBusy !== "" || targetCids.length === 0}
              onClick={handleDownloadJson}
              style={{
                borderRadius: 0,
                border: "none",
                borderLeft: "1px solid var(--border-strong)",
                fontSize: 12,
                padding: "4px 12px",
              }}
            >
              {downloadBusy === "json"
                ? "..."
                : `⬇ json (${targetCids.length}${selectedIds.size === 0 ? " · 전체" : ""})`}
            </button>
          </div>
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
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                    }}
                  >
                    <span>{formatKstDateTime(con.createdAt) || "-"}</span>
                    {con.modelId && (
                      <span
                        title={con.modelMixed ? `mixed: ${con.modelId}` : con.modelId}
                        style={{
                          fontSize: 9.5,
                          fontWeight: 700,
                          padding: "1px 6px",
                          borderRadius: "var(--radius-pill)",
                          background: "var(--surface-muted)",
                          color: "var(--ink-soft)",
                          border: "1px solid var(--border)",
                          letterSpacing: "0.02em",
                        }}
                      >
                        {shortModelLabel(con.modelId)}
                        {con.modelMixed ? " ⚠" : ""}
                      </span>
                    )}
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
