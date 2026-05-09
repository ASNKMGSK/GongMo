// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { Fragment, useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { useAppState } from "@/lib/AppStateContext";
import {
  BASE_URL,
  deleteHitlRagCase,
  deleteHitlRagCaseByParams,
  fetchHitlRagList,
  getHitlRagCase,
  getHitlRagStatus,
  listHitlRagCases,
  rebuildHitlRag,
  recreateHitlRagIndex,
  type HitlRagBuildResult,
  type HitlRagCaseDetail,
  type HitlRagCaseListItem,
  type HitlRagFlexDeleteResult,
  type HitlRagListItem,
  type HitlRagListResponse,
  type HitlRagOrphanIndex,
  type HitlRagRecreateResult,
  type HitlRagStatus,
} from "@/lib/api";
import { useToast } from "@/lib/toast";

/* ─────────────────────────────────────────────────────────────
   RagAdminPanel — Task #4 (Dev4)
   V2 원본: qa_pipeline_reactflow.html:4145 (RagAdminPanel)
   AOSS 인덱스 상태 조회 + 재빌드 (SSE 진행률 + PROGRESS 라인 파싱)
   ───────────────────────────────────────────────────────────── */

interface IndexInfo {
  name: string;
  label?: string;
  exists?: boolean;
  total_indexed?: number;
  total_source?: number;
  by_tenant?: Record<
    string,
    { indexed: number; source: number; status: "synced" | "not_built" | "duplicated" | "stale" | "empty" | string }
  >;
}

interface ScopeEntry {
  site_id: string;
  channel: string | null;
  department: string | null;
  is_shared: boolean;
  label: string;
  source: { golden: number; reasoning: number; knowledge: number };
  has_config: boolean;
}

interface StatusData {
  region?: string;
  tenants?: string[];
  indexes?: IndexInfo[];
  scopes?: ScopeEntry[];
}

interface BuildLog {
  ts: number;
  kind: string;
  text: string;
}

interface ProgressEntry {
  total: number;
  current: number;
  fail: number;
  status: string;
}

type ProgressMap = Record<string, Record<string, ProgressEntry>>;

const STATUS_STYLE: Record<
  string,
  { badgeCls: string; icon: string; label: string }
> = {
  synced: { badgeCls: "badge badge-success", icon: "✓", label: "동기화" },
  not_built: { badgeCls: "badge badge-danger", icon: "✗", label: "미빌드" },
  duplicated: { badgeCls: "badge badge-warn", icon: "⚠", label: "중복" },
  stale: { badgeCls: "badge badge-warn", icon: "↻", label: "보강필요" },
  empty: { badgeCls: "badge badge-neutral", icon: "—", label: "데이터없음" },
};

const PROGRESS_KINDS: Array<{ key: string; label: string }> = [
  { key: "golden", label: "Golden-set" },
  { key: "reasoning", label: "Reasoning" },
  { key: "knowledge", label: "Business KB" },
];

export function RagAdminPanel() {
  const { state } = useAppState();
  const toast = useToast();
  const serverUrl = state.serverUrl || BASE_URL;

  const [status, setStatus] = useState<{
    loading: boolean;
    data: StatusData | null;
    error: string | null;
  }>({ loading: true, data: null, error: null });
  const [buildLogs, setBuildLogs] = useState<BuildLog[]>([]);
  const [building, setBuilding] = useState(false);
  const [selectedTenant, setSelectedTenant] = useState("");
  // 3단계 멀티테넌트 빌드 타겟 (2026-04-24)
  const [buildSite, setBuildSite] = useState("");
  const [buildChannel, setBuildChannel] = useState<"" | "inbound" | "outbound">("");
  // department 는 드롭다운 선택값 + "__other__" 선택 시 customInput 사용.
  const [buildDeptSelect, setBuildDeptSelect] = useState<string>("");  // "" (전체) | dept | "__other__"
  const [buildDeptCustom, setBuildDeptCustom] = useState<string>("");
  // 항목별 빌드 (2026-05-07) — 선택된 item_number 만 인덱싱
  const [buildItemSite, setBuildItemSite] = useState<string>("");
  const [selectedItems, setSelectedItems] = useState<Set<number>>(new Set());
  // HITL 골든셋 섹션 토글 — default 닫힘 (UI 정리 + polling 절감)
  const [showHitl, setShowHitl] = useState<boolean>(false);
  // scope 테이블 site 별 토글 — 기본 모두 접힘 (가독성). Set 의 presence = expanded
  const [expandedSites, setExpandedSites] = useState<Set<string>>(new Set());
  const [progress, setProgress] = useState<ProgressMap>({});
  const abortRef = useRef<AbortController | null>(null);

  const fetchStatus = useCallback(async () => {
    setStatus((s) => ({ ...s, loading: true, error: null }));
    try {
      const r = await fetch(`${serverUrl}/v2/rag/status`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as StatusData;
      setStatus({ loading: false, data, error: null });
    } catch (e) {
      const msg = (e as Error).message || String(e);
      setStatus({ loading: false, data: null, error: msg });
    }
  }, [serverUrl]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const triggerBuild = useCallback(
    async (opts: {
      tenant?: string;
      site_id?: string;
      channel?: string;
      department?: string;
      recreate?: boolean;
      clean_tenant?: boolean;
      item_numbers?: number[];
    }) => {
      if (building) return;
      // 3단계 타겟 우선, 없으면 레거시 selectedTenant fallback.
      const tgtSite = opts.site_id ?? opts.tenant ?? selectedTenant;
      const tgtChannel = opts.channel ?? "";
      const tgtDepartment = opts.department ?? "";
      const tgtRecreate = opts.recreate ?? false;
      // 항목별 빌드 모드일 때는 clean_tenant 강제 X (해당 항목 doc 만 delete_by_item)
      const itemNumbers = opts.item_numbers && opts.item_numbers.length > 0 ? opts.item_numbers : undefined;
      const tgtCleanTenant = opts.clean_tenant ?? (!!tgtSite && !itemNumbers);

      setBuilding(true);
      setProgress({});
      const scopeLabel = tgtSite
        ? `${tgtSite}${tgtChannel ? `/${tgtChannel}` : ""}${tgtDepartment ? `/${tgtDepartment}` : ""}`
        : "ALL";
      const itemLabel = itemNumbers ? ` · items=[${itemNumbers.join(",")}]` : "";
      setBuildLogs([
        {
          ts: Date.now(),
          kind: "start",
          text: `🚀 bootstrap 시작 — ${scopeLabel}${itemLabel}, recreate=${tgtRecreate}, clean_tenant=${tgtCleanTenant}`,
        },
      ]);

      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        const r = await fetch(`${serverUrl}/v2/rag/build`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            // 3단계 멀티테넌트 필드 (2026-04-24).
            // site_id 는 tenant 와 같은 의미 — 백엔드가 둘 다 수용 (레거시 호환).
            site_id: tgtSite || undefined,
            channel: tgtChannel || undefined,
            department: tgtDepartment || undefined,
            tenant: tgtSite || undefined, // 레거시 필드 — 백엔드 fallback
            recreate: tgtRecreate,
            clean_tenant: tgtCleanTenant,
            item_numbers: itemNumbers, // 항목별 빌드 — undefined 면 전체
          }),
          signal: ctrl.signal,
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        if (!r.body) throw new Error("response.body is null");
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = "";

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const events = buf.split("\n\n");
          buf = events.pop() || "";
          for (const evt of events) {
            const lines = evt.split("\n");
            let event = "log";
            let data = "";
            for (const ln of lines) {
              if (ln.startsWith("event: ")) event = ln.slice(7).trim();
              else if (ln.startsWith("data: ")) data += ln.slice(6);
            }
            let payload: Record<string, unknown> = {};
            try {
              payload = JSON.parse(data);
            } catch {
              /* raw string */
            }
            const lineText =
              (typeof payload.line === "string" && payload.line) ||
              (typeof payload.message === "string" && payload.message) ||
              JSON.stringify(payload);

            if (typeof lineText === "string" && lineText.startsWith("PROGRESS ")) {
              const kv: Record<string, string> = {};
              lineText
                .slice(9)
                .split(/\s+/)
                .forEach((tok) => {
                  const [k, v] = tok.split("=");
                  if (k) kv[k] = v;
                });
              const t = kv.tenant;
              const k = kv.kind;
              if (t && k) {
                setProgress((prev) => ({
                  ...prev,
                  [t]: {
                    ...(prev[t] || {}),
                    [k]: {
                      total: Number(kv.total || 0),
                      current: Number(kv.current || 0),
                      fail: Number(kv.fail || 0),
                      status: kv.status || "indexing",
                    },
                  },
                }));
              }
            } else {
              setBuildLogs((prev) => [
                ...prev,
                { ts: Date.now(), kind: event, text: lineText },
              ]);
            }
            if (event === "done") {
              setBuildLogs((prev) => [
                ...prev,
                {
                  ts: Date.now(),
                  kind: "summary",
                  text: `✅ 완료 — return_code=${payload.return_code} ok=${payload.ok}`,
                },
              ]);
              setTimeout(fetchStatus, 800);
            }
          }
        }
        toast.success("RAG 빌드 완료");
      } catch (e) {
        const msg = (e as Error).message || String(e);
        setBuildLogs((prev) => [...prev, { ts: Date.now(), kind: "error", text: `❌ ${msg}` }]);
        if (msg !== "AbortError" && !(e as Error).name?.includes("Abort")) {
          toast.error("RAG 빌드 실패", { description: msg });
        }
      } finally {
        setBuilding(false);
        abortRef.current = null;
      }
    },
    [building, selectedTenant, serverUrl, fetchStatus, toast],
  );

  const buildSingleTenant = useCallback(
    (tenant: string) => {
      setSelectedTenant(tenant);
      setTimeout(() => triggerBuild({ tenant, clean_tenant: true, recreate: false }), 50);
    },
    [triggerBuild],
  );

  const buildAll = useCallback(
    (useRecreate: boolean) => {
      setSelectedTenant("");
      setTimeout(() => triggerBuild({ tenant: "", clean_tenant: false, recreate: useRecreate }), 50);
    },
    [triggerBuild],
  );

  // department 최종값 해석 — 드롭다운 값이 "__other__" 이면 custom input 사용, "" 이면 미지정.
  const resolvedDepartment = useCallback((): string | undefined => {
    if (buildDeptSelect === "__other__") {
      const v = buildDeptCustom.trim();
      return v || undefined;
    }
    return buildDeptSelect || undefined;
  }, [buildDeptSelect, buildDeptCustom]);

  // 3단계 타겟 빌드 — 상단 셀렉터에서 입력받은 site/channel/department 조합으로 빌드.
  const buildThreeTier = useCallback(() => {
    if (!buildSite) {
      toast.error("site_id 필수", { description: "사이트를 먼저 선택하세요" });
      return;
    }
    const dept = resolvedDepartment();
    setSelectedTenant(buildSite);
    setTimeout(
      () =>
        triggerBuild({
          site_id: buildSite,
          channel: buildChannel || undefined,
          department: dept,
          clean_tenant: true,
          recreate: false,
        }),
      50,
    );
  }, [buildSite, buildChannel, resolvedDepartment, triggerBuild, toast]);

  const abortBuild = useCallback(() => {
    if (abortRef.current) {
      try {
        abortRef.current.abort();
      } catch {
        /* no-op */
      }
    }
    setBuilding(false);
  }, []);

  const data = status.data;
  const tenants = data?.tenants || [];
  const indexes = data?.indexes || [];

  return (
    <div className="flex flex-col gap-5">
      <div className="panel">
        <div className="panel-header">
          <div>
            <div className="panel-title">🔧 RAG Admin — AOSS 인덱스 관리</div>
            <div className="text-[12px] text-[var(--ink-muted)] mt-0.5">
              region: <b>{data?.region || "—"}</b> · tenants: <b>{tenants.length}</b>
            </div>
          </div>
          <button
            className="btn-secondary btn-sm"
            onClick={fetchStatus}
            disabled={status.loading}
          >
            {status.loading ? "조회 중..." : "🔄 새로고침"}
          </button>
        </div>

        {status.error && (
          <div className="panel-section">
            <div className="rounded-[var(--radius-sm)] border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-[12px] text-[var(--danger)]">
              ⚠ 상태 조회 실패: {status.error}
              <button
                className="btn-ghost btn-sm ml-2"
                onClick={fetchStatus}
              >
                재시도
              </button>
            </div>
          </div>
        )}

        {!status.error && !status.loading && tenants.length === 0 && (
          <div className="panel-section">
            <div className="empty-state">
              <div className="empty-state-title">테넌트 없음</div>
              <div className="empty-state-desc">
                백엔드가 아직 tenant 정보를 반환하지 않았습니다. 새로고침을 눌러 다시 시도하세요.
              </div>
            </div>
          </div>
        )}

        {/* 인덱스 매트릭스 */}
        {tenants.length > 0 && (
          <div className="panel-section">
            <div className="overflow-auto rounded-[var(--radius-sm)] border border-[var(--border)]">
              <table className="w-full text-[12.5px] border-collapse">
                <thead>
                  <tr className="bg-[var(--surface-sunken)]">
                    <th className="px-3 py-2 text-left border-b border-[var(--border)]">AOSS Index</th>
                    <th className="px-3 py-2 text-right border-b border-[var(--border)]">
                      Total
                      <div className="text-[10px] font-normal text-[var(--ink-muted)]">
                        (idx/src)
                      </div>
                    </th>
                    {tenants.map((t) => (
                      <th
                        key={t}
                        className="px-3 py-2 text-center border-b border-[var(--border)] text-[var(--accent)]"
                      >
                        {t}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {indexes.map((idx) => (
                    <tr key={idx.name} className="border-b border-[var(--border-subtle)]">
                      <td className="px-3 py-2">
                        <code className="font-mono text-[11px] font-semibold">{idx.name}</code>
                        <div className="text-[10.5px] text-[var(--ink-muted)] mt-0.5">
                          {idx.label}
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right font-mono font-bold">
                        {idx.exists ? idx.total_indexed : "—"}
                        <span className="text-[var(--ink-muted)] font-normal"> / {idx.total_source || 0}</span>
                      </td>
                      {tenants.map((t) => {
                        const cell = idx.by_tenant?.[t] || { indexed: 0, source: 0, status: "empty" };
                        const style = STATUS_STYLE[cell.status] || STATUS_STYLE.empty;
                        const scoreColor =
                          cell.indexed === cell.source && cell.indexed > 0
                            ? "text-[var(--success)]"
                            : cell.indexed === 0 && cell.source > 0
                              ? "text-[var(--danger)]"
                              : cell.indexed > cell.source
                                ? "text-[var(--warn)]"
                                : "";
                        return (
                          <td key={t} className="px-3 py-2 text-center">
                            <div className="flex flex-col items-center gap-1">
                              <span className={`text-[12.5px] font-bold ${scoreColor}`}>
                                {cell.indexed}
                                <span className="text-[var(--ink-muted)] font-normal"> / {cell.source}</span>
                              </span>
                              <span
                                className={style.badgeCls}
                                title={`${style.label} — indexed=${cell.indexed}, source=${cell.source}`}
                              >
                                {style.icon} {style.label}
                              </span>
                            </div>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                  {/* 빌드 버튼 행 */}
                  <tr className="bg-[var(--surface-sunken)] border-t-2 border-[var(--border-strong)]">
                    <td className="px-3 py-3 text-[11px] text-[var(--ink-muted)] font-semibold">
                      Tenant 별 빌드
                    </td>
                    <td className="px-3 py-3 text-right">
                      <div className="flex justify-end gap-1.5">
                        <button
                          className="btn-primary btn-sm"
                          onClick={() => buildAll(false)}
                          disabled={building}
                          title="모든 tenant 일괄 빌드 (인덱스 보존)"
                        >
                          ▶ ALL
                        </button>
                        <button
                          className="btn-danger btn-sm"
                          onClick={() => {
                            if (
                              window.confirm(
                                "⚠ 모든 인덱스 삭제 후 전체 재생성합니다 (모든 tenant 영향). 진행할까요?",
                              )
                            ) {
                              buildAll(true);
                            }
                          }}
                          disabled={building}
                          title="인덱스 삭제 후 재생성 — 중복 정리용"
                        >
                          🗑 재생성
                        </button>
                        <button
                          className="btn-danger btn-sm"
                          onClick={async () => {
                            if (
                              !window.confirm(
                                "⚠ 3개 인덱스 (qa-golden-set / qa-reasoning-index / qa-business-knowledge) 삭제만 진행합니다.\n재빌드는 따로 ▶ ALL 또는 🗑 재생성 으로 해야 합니다.\n진행할까요?",
                              )
                            ) {
                              return;
                            }
                            try {
                              const r = await fetch(`${serverUrl}/v2/rag/delete-indices`, {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ confirm: "DELETE_ALL" }),
                              });
                              const body = (await r.json().catch(() => ({}))) as {
                                ok?: boolean;
                                results?: Array<{
                                  index: string;
                                  before?: boolean;
                                  after?: boolean;
                                  ok?: boolean;
                                  skipped?: boolean;
                                  error?: string;
                                }>;
                                error?: string;
                              };
                              if (r.ok && body.ok) {
                                const summary = (body.results ?? [])
                                  .map((x) =>
                                    x.skipped
                                      ? `${x.index}: 없음(skip)`
                                      : x.error
                                        ? `${x.index}: ❌ ${x.error}`
                                        : `${x.index}: 삭제됨`,
                                  )
                                  .join("\n");
                                toast.success("인덱스 삭제 완료", { description: summary });
                                // 새로고침 — count 다시 가져오기
                                if (typeof window !== "undefined") window.location.reload();
                              } else {
                                toast.error("인덱스 삭제 실패", {
                                  description: body.error ?? `HTTP ${r.status}`,
                                });
                              }
                            } catch (e) {
                              toast.error("인덱스 삭제 호출 실패", {
                                description: e instanceof Error ? e.message : String(e),
                              });
                            }
                          }}
                          disabled={building}
                          title="3 인덱스 삭제만 — 재빌드 안 함 (이후 ▶ ALL 등으로 별도 빌드)"
                        >
                          🗑️ 삭제만
                        </button>
                      </div>
                    </td>
                    {tenants.map((t) => (
                      <td key={t} className="px-3 py-3 text-center">
                        <button
                          className="btn-secondary btn-sm"
                          onClick={() => buildSingleTenant(t)}
                          disabled={building}
                          title={`${t} 만 정리 후 재빌드 — 다른 tenant 보존 (delete_by_query)`}
                        >
                          🔨 {t}
                        </button>
                      </td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>

            {/* 3단계 멀티테넌트 타겟 빌드 (2026-04-24) */}
            {(() => {
              const scopes = data?.scopes || [];
              // (site, channel) 에 매칭되는 실존 department 리스트.
              const deptOptions = scopes
                .filter(
                  (s) =>
                    s.site_id === buildSite &&
                    (buildChannel ? s.channel === buildChannel : s.channel === null) &&
                    s.department !== null,
                )
                .map((s) => s.department as string);
              const uniqueDepts = Array.from(new Set(deptOptions)).sort();
              return (
                <div className="mt-3 rounded-[var(--radius-sm)] border border-[var(--accent-border)] bg-[var(--accent-bg-soft)] p-3">
                  <div className="mb-2 text-[12px] font-semibold text-[var(--accent)]">
                    🎯 3단계 타겟 빌드 — site / channel / department
                  </div>
                  <div className="flex flex-wrap items-end gap-3">
                    <label className="flex flex-col gap-1 text-[11px]">
                      <span className="font-medium text-[var(--ink-soft)]">Site</span>
                      <select
                        value={buildSite}
                        onChange={(e) => {
                          setBuildSite(e.target.value);
                          setBuildDeptSelect("");
                          setBuildDeptCustom("");
                        }}
                        disabled={building}
                        className="min-w-[120px] rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-2.5 py-1.5 text-[13px] text-[var(--ink)] outline-none focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-ring)]"
                      >
                        <option value="">(선택)</option>
                        {tenants.map((t) => (
                          <option key={t} value={t}>
                            {t}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="flex flex-col gap-1 text-[11px]">
                      <span className="font-medium text-[var(--ink-soft)]">Channel</span>
                      <select
                        value={buildChannel}
                        onChange={(e) => {
                          setBuildChannel(e.target.value as "" | "inbound" | "outbound");
                          setBuildDeptSelect("");
                          setBuildDeptCustom("");
                        }}
                        disabled={building}
                        className="min-w-[110px] rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-2.5 py-1.5 text-[13px] text-[var(--ink)] outline-none focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-ring)]"
                      >
                        <option value="">(전체)</option>
                        <option value="inbound">inbound</option>
                        <option value="outbound">outbound</option>
                      </select>
                    </label>
                    <label className="flex flex-col gap-1 text-[11px]">
                      <span className="font-medium text-[var(--ink-soft)]">Department</span>
                      <select
                        value={buildDeptSelect}
                        onChange={(e) => {
                          setBuildDeptSelect(e.target.value);
                          if (e.target.value !== "__other__") setBuildDeptCustom("");
                        }}
                        disabled={building || !buildSite}
                        className="min-w-[150px] rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-2.5 py-1.5 text-[13px] text-[var(--ink)] outline-none focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-ring)]"
                      >
                        <option value="">(전체)</option>
                        {uniqueDepts.map((d) => (
                          <option key={d} value={d}>
                            {d === "_shared" ? "_shared (공통)" : d}
                          </option>
                        ))}
                        <option value="__other__">기타 (직접 입력)</option>
                      </select>
                    </label>
                    {buildDeptSelect === "__other__" && (
                      <label className="flex flex-col gap-1 text-[11px]">
                        <span className="font-medium text-[var(--ink-soft)]">
                          Dept (직접 입력)
                        </span>
                        <input
                          value={buildDeptCustom}
                          onChange={(e) => setBuildDeptCustom(e.target.value.trim())}
                          disabled={building}
                          placeholder="예: finance, vip_care"
                          autoFocus
                          className="min-w-[150px] rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-2.5 py-1.5 text-[13px] text-[var(--ink)] outline-none focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-ring)]"
                        />
                      </label>
                    )}
                    <button
                      className="btn-primary btn-sm"
                      onClick={buildThreeTier}
                      disabled={building || !buildSite}
                      title="site × channel × department 조합으로 타겟 빌드"
                    >
                      🎯 타겟 빌드
                    </button>
                  </div>
                  <div className="mt-2 text-[10.5px] text-[var(--ink-muted)]">
                    Department 드롭다운에는 <code className="kbd">tenants/{"{site}/{channel}/"}</code> 에 실존하는 폴더가 노출됩니다.
                    목록에 없는 부서를 빌드하려면 <b>기타 (직접 입력)</b> 선택.
                    Channel·Department 모두 생략 시 해당 축 전체 대상.
                  </div>
                </div>
              );
            })()}

            {/* 항목별 빌드 (2026-05-07) — 평가 항목별로 골든셋 RAG 부분 빌드 */}
            <div className="mt-3 p-3 rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface-sunken)]">
              <div className="text-[12px] font-semibold text-[var(--ink-soft)] mb-2">
                🎯 항목별 빌드 — 평가 항목 단위 (golden_set)
              </div>
              <div className="flex flex-wrap items-end gap-2 text-[12px]">
                <label className="flex flex-col gap-0.5">
                  <span className="font-medium text-[var(--ink-soft)]">Site</span>
                  <select
                    value={buildItemSite}
                    onChange={(e) => setBuildItemSite(e.target.value)}
                    disabled={building}
                    className="rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-2 py-1"
                    style={{ minWidth: 120 }}
                  >
                    <option value="">(선택)</option>
                    {(data?.tenants ?? []).map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="flex flex-col gap-0.5">
                  <span className="font-medium text-[var(--ink-soft)]">평가 항목 (multi-select)</span>
                  <div className="flex flex-wrap gap-1 max-w-[640px]">
                    {[1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18].map((n) => {
                      const sel = selectedItems.has(n);
                      return (
                        <button
                          key={n}
                          type="button"
                          onClick={() => {
                            setSelectedItems((prev) => {
                              const next = new Set(prev);
                              if (next.has(n)) next.delete(n);
                              else next.add(n);
                              return next;
                            });
                          }}
                          disabled={building}
                          className="px-2 py-0.5 text-[11px] rounded-[var(--radius-sm)] border transition disabled:opacity-50"
                          style={{
                            background: sel ? "var(--accent-bg)" : "var(--surface)",
                            color: sel ? "var(--accent)" : "var(--ink-muted)",
                            borderColor: sel ? "var(--accent)" : "var(--border-strong)",
                            fontWeight: sel ? 700 : 500,
                          }}
                          title={`item_${String(n).padStart(2, "0")}`}
                        >
                          #{n}
                        </button>
                      );
                    })}
                  </div>
                </div>
                <div className="flex gap-1">
                  <button
                    type="button"
                    onClick={() =>
                      setSelectedItems(new Set([1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18]))
                    }
                    disabled={building}
                    className="px-2 py-1 text-[11px] rounded-[var(--radius-sm)] border border-[var(--border-strong)] disabled:opacity-50"
                  >
                    전체
                  </button>
                  <button
                    type="button"
                    onClick={() => setSelectedItems(new Set())}
                    disabled={building}
                    className="px-2 py-1 text-[11px] rounded-[var(--radius-sm)] border border-[var(--border-strong)] disabled:opacity-50"
                  >
                    해제
                  </button>
                </div>
                <button
                  className="btn-primary btn-sm"
                  onClick={() => {
                    if (!buildItemSite) {
                      toast.error("site 필수", { description: "사이트를 먼저 선택하세요" });
                      return;
                    }
                    if (selectedItems.size === 0) {
                      toast.error("항목 선택 필수", { description: "1개 이상 평가 항목을 선택하세요" });
                      return;
                    }
                    const items = Array.from(selectedItems).sort((a, b) => a - b);
                    triggerBuild({
                      site_id: buildItemSite,
                      item_numbers: items,
                      recreate: false,
                      clean_tenant: false,
                    });
                  }}
                  disabled={building || !buildItemSite || selectedItems.size === 0}
                  title="선택한 평가 항목만 인덱싱 (delete_by_item 으로 기존 doc 정리 후 재색인)"
                >
                  🎯 항목별 빌드 ({selectedItems.size})
                </button>
              </div>
              <div className="mt-2 text-[10.5px] text-[var(--ink-muted)]">
                선택한 item_number 만 골든셋 인덱싱 (해당 항목 기존 doc <code className="kbd">delete_by_item</code> 후 재색인).
                다른 항목/tenant 보존.
              </div>
            </div>

            {/* 범례 — 간소화 */}
            <div className="mt-3 rounded-[var(--radius-sm)] bg-[var(--surface-muted)] p-3 text-[11px] leading-relaxed text-[var(--ink-muted)]">
              <div>
                <b className="text-[var(--ink-soft)]">셀:</b>{" "}
                <code className="font-mono">indexed / source</code>
                <span className="ml-2 badge badge-success">✓ 동기화</span>
                <span className="ml-1.5 badge badge-danger">✗ 미빌드</span>
              </div>
              <div className="mt-1">
                <b className="text-[var(--ink-soft)]">빌드 모드:</b>{" "}
                🎯 <b>항목별</b> (golden_set 부분 빌드, 권장)
                {" · "}
                🔨 <b>tenant</b> (site 전체)
                {" · "}
                ▶ <b>ALL</b> (전 tenant 추가 색인)
                {" · "}
                🗑 <b>재생성</b> (전체 인덱스 삭제 후 재빌드)
              </div>
            </div>
          </div>
        )}

        {/* 빌드 진행 표시 */}
        {building && (
          <div className="panel-section">
            <div className="flex items-center justify-between rounded-[var(--radius-sm)] border-l-4 border-[var(--warn-border)] bg-[var(--warn-bg)] px-3 py-2">
              <span className="text-[12px] font-semibold text-[var(--warn)]">
                ⏳ 빌드 진행 중 — tenant={selectedTenant || "ALL"}
              </span>
              <button className="btn-danger btn-sm" onClick={abortBuild}>
                ■ 중단
              </button>
            </div>
          </div>
        )}

        {/* 실시간 진행 바 */}
        {(building || Object.keys(progress).length > 0) && (
          <div className="panel-section">
            <div className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface-muted)] p-4">
              <div className="mb-3 text-[12.5px] font-semibold">📊 실시간 빌드 진행도</div>
              {Object.keys(progress).length === 0 ? (
                <div className="text-[11px] text-[var(--ink-muted)]">진행 신호 대기 중...</div>
              ) : (
                <div className="grid gap-2.5" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
                  {Object.entries(progress).map(([tenant, kinds]) => (
                    <div
                      key={tenant}
                      className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface)] p-3"
                    >
                      <div className="mb-2 text-[12.5px] font-semibold text-[var(--accent)]">
                        🏢 {tenant}
                      </div>
                      {PROGRESS_KINDS.map(({ key, label }) => {
                        const p = kinds[key];
                        if (!p) {
                          return (
                            <div
                              key={key}
                              className="mb-1.5 text-[10.5px] text-[var(--ink-subtle)]"
                            >
                              {label} — 대기
                            </div>
                          );
                        }
                        const pct = p.total > 0 ? Math.min(100, Math.round((p.current / p.total) * 100)) : 0;
                        const isDone = p.status === "done";
                        return (
                          <div key={key} className="mb-2">
                            <div className="flex justify-between text-[10.5px] mb-1">
                              <span className="font-medium">
                                {label}
                                {p.fail > 0 && (
                                  <span className="ml-1.5 text-[var(--danger)]">(실패 {p.fail})</span>
                                )}
                              </span>
                              <span
                                className={`font-mono font-bold ${isDone ? "text-[var(--success)]" : ""}`}
                              >
                                {p.current} / {p.total} {isDone && "✓"}
                              </span>
                            </div>
                            <div className="relative h-2.5 rounded-[var(--radius-pill)] bg-[var(--surface-sunken)] overflow-hidden">
                              <div
                                className="absolute top-0 left-0 h-full transition-[width] duration-300"
                                style={{
                                  width: `${pct}%`,
                                  background: isDone ? "var(--success)" : "var(--accent)",
                                }}
                              />
                            </div>
                            <div className="mt-0.5 text-right text-[9.5px] text-[var(--ink-muted)] font-mono">
                              {pct}%
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* 빌드 로그 */}
        {buildLogs.length > 0 && (
          <div className="panel-section">
            <div className="text-[12.5px] font-semibold mb-2">📜 빌드 로그</div>
            <div className="max-h-[400px] overflow-auto rounded-[var(--radius-sm)] bg-[#0f172a] text-[#e2e8f0] p-3 font-mono text-[10.5px] leading-relaxed">
              {buildLogs.map((l, i) => {
                const color =
                  l.kind === "error"
                    ? "#fca5a5"
                    : l.kind === "done" || l.kind === "summary"
                      ? "#86efac"
                      : l.kind === "start"
                        ? "#93c5fd"
                        : "#cbd5e1";
                return (
                  <div key={i} style={{ color }}>
                    <span className="text-[#64748b] mr-2">[{new Date(l.ts).toLocaleTimeString()}]</span>
                    {l.text}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* ★ 2026-05-08: KMS LinearRAG 디스크 캐시 초기화 — 파이프라인 탭에서 이전.
         첫 KMS 호출 시 인덱싱 결과를 TEMP/qa_kms_linear_rag 로 저장 → 캐시 무효화 시 사용. */}
      <KmsRagResetSection />

      {/* ★ 2026-04-30: 골든셋 RAG 관리 (실험) — HITL 검수 데이터를 페르소나 토론 RAG corpus 로.
         2026-05-07: default-closed collapsible — 펼치지 않으면 polling 미발생.
       */}
      <div className="panel" style={{ marginTop: 16 }}>
        <button
          type="button"
          onClick={() => setShowHitl((s) => !s)}
          className="panel-header w-full text-left"
          style={{ cursor: "pointer", background: "transparent", border: "none" }}
        >
          <div>
            <div className="panel-title">
              {showHitl ? "▼" : "▶"} 🌟 골든셋 RAG 관리 (실험, HITL)
            </div>
            <div className="text-[12px] text-[var(--ink-muted)] mt-0.5">
              {showHitl
                ? "접으려면 클릭 — HITL 검수 데이터 → 페르소나 토론 RAG corpus"
                : "펼치려면 클릭 — HITL 검수 데이터 (현재 미사용 시 닫아두면 polling 절감)"}
            </div>
          </div>
        </button>
        {showHitl && (
        <>
        <div className="panel-section">
          {/* 3단 계층 구조 트리 */}
          <div className="text-[11px]" style={{ lineHeight: 2 }}>
            {/* 1단 — umbrella */}
            <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
              <span style={{ fontWeight: 700, color: "#7c3aed" }}>🌟 골든셋 RAG</span>
              <span className="text-[var(--ink-muted)]">— 페르소나 토론에 주입되는 검수 정답 사례 묶음</span>
            </div>
            {/* 2단 — HITL (현재 출처) */}
            <div style={{ display: "flex", alignItems: "baseline", gap: 6, paddingLeft: 16 }}>
              <span style={{ color: "#6b7280", marginRight: 2 }}>└</span>
              <span style={{ fontWeight: 700, color: "#92400e" }}>📂 HITL</span>
              <span className="text-[var(--ink-muted)]">— 검수자 확정 데이터 (qa-hitl-cases 인덱스). 현재 유일 출처</span>
            </div>
            {/* 3단 — 자기 자신 HITL (HITL 의 특수 케이스) */}
            <div style={{ display: "flex", alignItems: "baseline", gap: 6, paddingLeft: 32 }}>
              <span style={{ color: "#6b7280", marginRight: 2 }}>└</span>
              <span style={{ fontWeight: 700, color: "#991b1b" }}>🔁 자기 자신 HITL</span>
              <span className="text-[var(--ink-muted)]">— 현재 평가 중인 상담과 동일한 상담 ID 의 HITL 사례 (자동 매칭)</span>
            </div>
            {/* 향후 확장 안내 */}
            <div
              className="text-[var(--ink-muted)]"
              style={{ paddingLeft: 16, marginTop: 4, fontStyle: "italic" }}
            >
              └ … 향후: 외부 큐레이션 / 강화학습 피드백 등 추가 source 확장 예정
            </div>
          </div>
        </div>
        {/* 골든셋 RAG · HITL 빌드 섹션 — 펼친 경우만 mount → polling 활성화 */}
        <HitlRagSection />
        </>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   HitlRagSection — Task #5 (Dev4)
   HITL 검수 데이터를 RAG corpus 로 빌드 (MD 파일 → OpenSearch 색인).
   /v2/hitl-rag/* 엔드포인트 5종 사용 (lib/api.ts).
   ───────────────────────────────────────────────────────────── */

const ITEM_NUMBER_OPTIONS = Array.from({ length: 21 }, (_, i) => i + 1);
const HITL_PAGE_SIZE = 20;

function fmtKstTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    const opts: Intl.DateTimeFormatOptions = {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    };
    return `${new Intl.DateTimeFormat("ko-KR", opts).format(d)} KST`;
  } catch {
    return String(iso);
  }
}

interface HitlStatCardProps {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
}

function HitlStatCard({ label, value, sub }: HitlStatCardProps) {
  return (
    <div className="flex-1 min-w-[140px] rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface)] px-3 py-3">
      <div className="text-[10.5px] font-semibold uppercase tracking-wide text-[var(--ink-muted)] mb-1.5">
        {label}
      </div>
      <div className="text-[18px] font-bold text-[var(--ink)] font-mono">{value}</div>
      {sub && <div className="text-[10.5px] text-[var(--ink-muted)] mt-1">{sub}</div>}
    </div>
  );
}

export function HitlRagSection() {
  const toast = useToast();

  const [status, setStatus] = useState<{
    loading: boolean;
    data: HitlRagStatus | null;
    error: string | null;
  }>({ loading: true, data: null, error: null });

  const [actionBusy, setActionBusy] = useState(false);
  const [actionResult, setActionResult] =
    useState<HitlRagBuildResult | HitlRagRecreateResult | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [showForceConfirm, setShowForceConfirm] = useState(false);
  const [showDropConfirm, setShowDropConfirm] = useState(false);
  const [dropTyped, setDropTyped] = useState("");
  const [showPurgeConfirm, setShowPurgeConfirm] = useState(false);
  const [purgeTyped, setPurgeTyped] = useState("");

  const [cases, setCases] = useState<{ items: HitlRagCaseListItem[]; total: number }>({
    items: [],
    total: 0,
  });
  const [casesLoading, setCasesLoading] = useState(false);
  const [casesError, setCasesError] = useState<string | null>(null);
  const [itemFilter, setItemFilter] = useState<string>("");
  const [page, setPage] = useState(0);

  const [openCase, setOpenCase] = useState<{
    filename: string;
    detail: HitlRagCaseDetail | null;
    loading: boolean;
    error: string | null;
  } | null>(null);

  // 사례 삭제 — 확인 모달 + busy state (기존 파일명 기반)
  const [deleteTarget, setDeleteTarget] = useState<HitlRagCaseListItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  // 유연 관리 list — GET /v2/hitl-rag/list
  const [ragList, setRagList] = useState<HitlRagListResponse | null>(null);
  const [ragListLoading, setRagListLoading] = useState(false);
  const [ragListError, setRagListError] = useState<string | null>(null);

  // 유연 삭제 대상 (list 기반)
  const [flexDeleteTarget, setFlexDeleteTarget] = useState<{
    item: HitlRagListItem | HitlRagOrphanIndex;
    mode: "both" | "md_only" | "index_only";
    label: string;
  } | null>(null);
  const [flexDeleting, setFlexDeleting] = useState(false);

  const refreshStatus = useCallback(async () => {
    setStatus((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await getHitlRagStatus();
      setStatus({ loading: false, data, error: null });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setStatus({ loading: false, data: null, error: msg });
    }
  }, []);

  const refreshRagList = useCallback(async () => {
    setRagListLoading(true);
    setRagListError(null);
    try {
      const r = await fetchHitlRagList();
      setRagList(r);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setRagListError(msg);
    } finally {
      setRagListLoading(false);
    }
  }, []);

  const refreshCases = useCallback(async () => {
    setCasesLoading(true);
    setCasesError(null);
    try {
      const r = await listHitlRagCases({
        item_number: itemFilter || undefined,
        limit: HITL_PAGE_SIZE,
        offset: page * HITL_PAGE_SIZE,
      });
      setCases({
        items: Array.isArray(r?.items) ? r.items : [],
        total: Number.isFinite(r?.total) ? Number(r.total) : 0,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setCasesError(msg);
      setCases({ items: [], total: 0 });
    } finally {
      setCasesLoading(false);
    }
  }, [itemFilter, page]);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  useEffect(() => {
    refreshCases();
  }, [refreshCases]);

  useEffect(() => {
    refreshRagList();
  }, [refreshRagList]);

  const runRebuild = useCallback(
    async (force: boolean) => {
      if (actionBusy) return;
      setActionBusy(true);
      setActionResult(null);
      setActionError(null);
      try {
        const r = await rebuildHitlRag(force);
        setActionResult(r);
        toast.success(
          force ? "HITL RAG 전체 재임베딩 완료" : "HITL RAG 변경분 인덱싱 완료",
          {
            description: `indexed=${r.indexed ?? 0} / skipped=${r.skipped ?? 0}${
              r.errors && r.errors.length ? ` / errors=${r.errors.length}` : ""
            }`,
          },
        );
        refreshStatus();
        refreshCases();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setActionError(msg);
        toast.error("HITL RAG 인덱싱 실패", { description: msg });
      } finally {
        setActionBusy(false);
      }
    },
    [actionBusy, refreshCases, refreshStatus, toast],
  );

  const runRecreate = useCallback(async () => {
    if (actionBusy) return;
    setShowDropConfirm(false);
    setDropTyped("");
    setActionBusy(true);
    setActionResult(null);
    setActionError(null);
    try {
      const r = await recreateHitlRagIndex();
      setActionResult(r);
      toast.success("HITL RAG 인덱스 재생성 완료", {
        description: `recreated=${String(r.recreated ?? r.ok ?? "?")}`,
      });
      refreshStatus();
      refreshCases();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setActionError(msg);
      toast.error("HITL RAG 인덱스 재생성 실패", { description: msg });
    } finally {
      setActionBusy(false);
    }
  }, [actionBusy, refreshCases, refreshStatus, toast]);

  const runPurgeAll = useCallback(async () => {
    if (actionBusy) return;
    setShowPurgeConfirm(false);
    setPurgeTyped("");
    setActionBusy(true);
    setActionResult(null);
    setActionError(null);
    try {
      const res = await fetch(`${BASE_URL}/v2/hitl-rag/purge-all`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: "PURGE-ALL" }),
        cache: "no-store",
      });
      const data = (await res.json().catch(() => ({}))) as {
        ok?: boolean;
        md_deleted?: number;
        index_dropped?: boolean;
        orphan_dropped?: number;
        errors?: string[];
        error?: string;
        detail?: string;
      };
      if (!res.ok || data.error) {
        const msg = data.detail || data.error || `HTTP ${res.status}`;
        throw new Error(msg);
      }
      setActionResult({ ok: data.ok, recreated: data.index_dropped });
      const md = data.md_deleted ?? 0;
      const orphan = data.orphan_dropped ?? 0;
      toast.success("HITL RAG 전체 삭제 완료", {
        description: `MD ${md}건 / 인덱스 doc ${orphan}건 제거${
          data.errors && data.errors.length ? ` (errors=${data.errors.length})` : ""
        }`,
      });
      refreshStatus();
      refreshCases();
      refreshRagList();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setActionError(msg);
      toast.error("HITL RAG 전체 삭제 실패", { description: msg });
    } finally {
      setActionBusy(false);
    }
  }, [actionBusy, refreshCases, refreshRagList, refreshStatus, toast]);

  const openCaseModal = useCallback(async (filename: string) => {
    setOpenCase({ filename, detail: null, loading: true, error: null });
    try {
      const detail = await getHitlRagCase(filename);
      setOpenCase({ filename, detail, loading: false, error: null });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setOpenCase({ filename, detail: null, loading: false, error: msg });
    }
  }, []);

  const closeCaseModal = useCallback(() => {
    setOpenCase(null);
  }, []);

  const runDeleteCase = useCallback(async () => {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    try {
      const r = await deleteHitlRagCase(deleteTarget.filename);
      toast.success("HITL 사례 삭제 완료", {
        description: r.warning
          ? `md 삭제 OK · ${r.warning}`
          : `md 삭제 OK · AOSS doc ${r.aoss_deleted ?? 0}건 제거`,
      });
      setDeleteTarget(null);
      // 삭제된 사례가 모달로 열려 있으면 닫음
      if (openCase?.filename === deleteTarget.filename) setOpenCase(null);
      refreshStatus();
      refreshCases();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error("HITL 사례 삭제 실패", { description: msg });
    } finally {
      setDeleting(false);
    }
  }, [deleteTarget, deleting, openCase, refreshCases, refreshStatus, toast]);

  const runFlexDelete = useCallback(async () => {
    if (!flexDeleteTarget || flexDeleting) return;
    setFlexDeleting(true);
    const { item, mode } = flexDeleteTarget;
    const cid = (item as HitlRagListItem).consultation_id ?? (item as HitlRagOrphanIndex).consultation_id;
    const itemNo = (item as HitlRagListItem).item_number ?? (item as HitlRagOrphanIndex).item_number;
    try {
      const r = await deleteHitlRagCaseByParams({
        consultation_id: cid,
        item_number: itemNo,
        mode,
      });
      toast.success("삭제 완료", {
        description: `MD ${r.md_deleted}건 · AOSS doc ${r.index_deleted}건 제거${r.fallback_used ? " (fallback)" : ""}`,
      });
      setFlexDeleteTarget(null);
      refreshRagList();
      refreshStatus();
      refreshCases();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error("삭제 실패", { description: msg });
    } finally {
      setFlexDeleting(false);
    }
  }, [flexDeleteTarget, flexDeleting, refreshCases, refreshRagList, refreshStatus, toast]);

  const totalPages = Math.max(1, Math.ceil((cases.total || 0) / HITL_PAGE_SIZE));
  const data = status.data;

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <div className="panel-title">🔁 골든셋 RAG · HITL 빌드</div>
          <div className="text-[12px] text-[var(--ink-muted)] mt-0.5">
            HITL 검수 데이터를 골든셋 RAG corpus 로 빌드 (MD 파일 → OpenSearch 색인). 페르소나 토론 단계에서 참조.
          </div>
        </div>
        <div className="flex items-center gap-2">
          {data?.tenant_id && (
            <span className="badge badge-outline" title="현재 tenant">
              tenant: <b className="ml-1">{data.tenant_id}</b>
            </span>
          )}
          <button
            className="btn-secondary btn-sm"
            onClick={() => {
              refreshStatus();
              refreshCases();
              refreshRagList();
            }}
            disabled={status.loading || casesLoading || ragListLoading}
          >
            {status.loading || casesLoading || ragListLoading ? "조회 중..." : "🔄 새로고침"}
          </button>
        </div>
      </div>

      {/* 상단 stats 카드 */}
      <div className="panel-section">
        <div className="flex flex-wrap gap-2.5">
          <HitlStatCard
            label="MD 파일 수"
            value={data?.md_count ?? (status.loading ? "…" : "—")}
            sub={
              data?.rag_root ? (
                <span title={data.rag_root} className="font-mono text-[10px]">
                  {data.rag_root.length > 32 ? `…${data.rag_root.slice(-32)}` : data.rag_root}
                </span>
              ) : null
            }
          />
          <HitlStatCard
            label="인덱싱 완료"
            value={data?.indexed_count ?? (status.loading ? "…" : "—")}
          />
          <HitlStatCard
            label="Pending"
            value={data?.pending_count ?? (status.loading ? "…" : "—")}
          />
          <HitlStatCard
            label="인덱스"
            value={
              data?.index_exists === true
                ? "있음"
                : data?.index_exists === false
                  ? "없음"
                  : status.loading
                    ? "…"
                    : "—"
            }
            sub={
              data?.index_doc_count !== undefined ? (
                <span className="font-mono">docs: {data.index_doc_count}</span>
              ) : null
            }
          />
          <HitlStatCard label="마지막 빌드" value={fmtKstTime(data?.last_built_at)} />
        </div>

        {status.error && (
          <div className="mt-3 rounded-[var(--radius-sm)] border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-[12px] text-[var(--danger)]">
            ⚠ 상태 조회 실패: {status.error}
            <button className="btn-ghost btn-sm ml-2" onClick={refreshStatus}>
              재시도
            </button>
          </div>
        )}
      </div>

      {/* 액션 버튼 */}
      <div className="panel-section">
        <div className="text-[12.5px] font-semibold mb-2">색인 작업</div>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn-primary btn-sm"
            onClick={() => runRebuild(false)}
            disabled={actionBusy}
            title="변경된 MD 파일만 임베딩 / 색인 (delta)"
          >
            ▶ 변경분만 인덱싱
          </button>
          <button
            className="btn-warn btn-sm"
            onClick={() => setShowForceConfirm(true)}
            disabled={actionBusy}
            title="모든 MD 파일을 다시 임베딩 (force=true)"
          >
            ↻ 전체 재임베딩 (force)
          </button>
          <button
            className="btn-danger btn-sm"
            onClick={() => setShowDropConfirm(true)}
            disabled={actionBusy}
            title="OpenSearch 인덱스를 삭제 후 새로 생성"
          >
            🗑 인덱스 재생성 (DROP & CREATE)
          </button>
          <button
            className="btn-danger btn-sm"
            onClick={() => setShowPurgeConfirm(true)}
            disabled={actionBusy}
            title="MD 파일 + AOSS 인덱스(orphan 포함) 모두 삭제 — 되돌릴 수 없음"
            style={{ background: "var(--danger, #ef4444)", color: "#fff" }}
          >
            🗑 전체 삭제
          </button>
        </div>

        {actionBusy && (
          <div className="mt-2.5 flex items-center gap-2 rounded-[var(--radius-sm)] border-l-4 border-[var(--warn-border)] bg-[var(--warn-bg)] px-3 py-2 text-[12px] text-[var(--warn)]">
            <span className="spinner" aria-hidden="true" />
            서버 작업 진행 중...
          </div>
        )}
        {actionError && (
          <div className="mt-2.5 rounded-[var(--radius-sm)] border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-[12px] text-[var(--danger)]">
            ⚠ 실패: {actionError}
          </div>
        )}
        {actionResult && !actionBusy && (
          <div className="mt-2.5 rounded-[var(--radius-sm)] border border-[var(--success-border)] bg-[var(--success-bg)] px-3 py-2 text-[12px]">
            <div className="font-semibold text-[var(--success)] mb-1">✅ 완료</div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[var(--ink)]">
              {"indexed" in actionResult && actionResult.indexed !== undefined && (
                <span>
                  indexed: <b>{actionResult.indexed}</b>
                </span>
              )}
              {"skipped" in actionResult && actionResult.skipped !== undefined && (
                <span>
                  skipped: <b>{actionResult.skipped}</b>
                </span>
              )}
              {"recreated" in actionResult && actionResult.recreated !== undefined && (
                <span>
                  recreated: <b>{String(actionResult.recreated)}</b>
                </span>
              )}
              {"ok" in actionResult && actionResult.ok !== undefined && (
                <span>
                  ok: <b>{String(actionResult.ok)}</b>
                </span>
              )}
            </div>
            {"errors" in actionResult &&
              Array.isArray(actionResult.errors) &&
              actionResult.errors.length > 0 && (
                <div className="mt-2">
                  <div className="text-[11px] font-semibold text-[var(--danger)] mb-1">
                    errors ({actionResult.errors.length})
                  </div>
                  <ul className="pl-5 list-disc text-[11px] font-mono text-[var(--danger)] max-h-[120px] overflow-auto">
                    {actionResult.errors.slice(0, 20).map((err, i) => (
                      <li key={i}>{typeof err === "string" ? err : JSON.stringify(err)}</li>
                    ))}
                  </ul>
                </div>
              )}
          </div>
        )}
      </div>

      {/* 사례 목록 */}
      <div className="panel-section">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2.5">
          <div className="text-[12.5px] font-semibold">
            HITL 검수 사례 — 총 <b>{cases.total}</b>건
          </div>
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-[var(--ink-muted)]">항목 번호</label>
            <select
              value={itemFilter}
              onChange={(e) => {
                setPage(0);
                setItemFilter(e.target.value);
              }}
              className="rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-2 py-1 text-[12px] text-[var(--ink)] outline-none focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-ring)]"
            >
              <option value="">전체</option>
              {ITEM_NUMBER_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  #{n}
                </option>
              ))}
            </select>
          </div>
        </div>

        {casesError && (
          <div className="mb-2.5 rounded-[var(--radius-sm)] border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-[12px] text-[var(--danger)]">
            ⚠ 사례 목록 조회 실패: {casesError}
          </div>
        )}

        <div className="overflow-auto rounded-[var(--radius-sm)] border border-[var(--border)]">
          <table className="w-full text-[12px] border-collapse">
            <thead>
              <tr className="bg-[var(--surface-sunken)] text-[var(--ink-muted)]">
                <th className="px-2 py-1.5 text-left border-b border-[var(--border)]">consultation_id</th>
                <th className="px-2 py-1.5 text-left border-b border-[var(--border)]">item</th>
                <th className="px-2 py-1.5 text-left border-b border-[var(--border)]">이름</th>
                <th className="px-2 py-1.5 text-right border-b border-[var(--border)]">AI</th>
                <th className="px-2 py-1.5 text-right border-b border-[var(--border)]">Human</th>
                <th className="px-2 py-1.5 text-right border-b border-[var(--border)]">Δ</th>
                <th className="px-2 py-1.5 text-left border-b border-[var(--border)]">confirmed_at</th>
                <th className="px-2 py-1.5 text-left border-b border-[var(--border)]">indexed_at</th>
                <th className="px-2 py-1.5 text-center border-b border-[var(--border)]">Action</th>
              </tr>
            </thead>
            <tbody>
              {casesLoading && (
                <tr>
                  <td
                    colSpan={9}
                    className="px-2 py-6 text-center text-[var(--ink-muted)]"
                  >
                    불러오는 중...
                  </td>
                </tr>
              )}
              {!casesLoading && cases.items.length === 0 && (
                <tr>
                  <td
                    colSpan={9}
                    className="px-2 py-6 text-center text-[var(--ink-muted)]"
                  >
                    표시할 사례가 없습니다.
                  </td>
                </tr>
              )}
              {!casesLoading &&
                cases.items.map((row) => {
                  const meta = row?.meta || {};
                  const ai = typeof meta.ai_score === "number" ? meta.ai_score : null;
                  const hu = typeof meta.human_score === "number" ? meta.human_score : null;
                  const delta = ai !== null && hu !== null ? hu - ai : null;
                  const deltaCls =
                    delta === null
                      ? "text-[var(--ink-muted)]"
                      : delta > 0
                        ? "text-[var(--success)]"
                        : delta < 0
                          ? "text-[var(--danger)]"
                          : "text-[var(--ink)]";
                  return (
                    <tr
                      key={row.filename}
                      className="border-b border-[var(--border-subtle)] hover:bg-[var(--surface-muted)] transition-colors"
                    >
                      <td
                        className="px-2 py-1.5 font-mono text-[11px] cursor-pointer"
                        onClick={() => openCaseModal(row.filename)}
                        title="클릭하여 MD 본문 보기"
                      >
                        {meta.consultation_id ?? "—"}
                      </td>
                      <td
                        className="px-2 py-1.5 cursor-pointer"
                        onClick={() => openCaseModal(row.filename)}
                      >
                        #{meta.item_number ?? "?"}
                      </td>
                      <td
                        className="px-2 py-1.5 cursor-pointer"
                        onClick={() => openCaseModal(row.filename)}
                      >
                        {meta.item_name ?? "—"}
                      </td>
                      <td
                        className="px-2 py-1.5 text-right font-mono cursor-pointer"
                        onClick={() => openCaseModal(row.filename)}
                      >
                        {ai !== null ? ai : "—"}
                      </td>
                      <td
                        className="px-2 py-1.5 text-right font-mono cursor-pointer"
                        onClick={() => openCaseModal(row.filename)}
                      >
                        {hu !== null ? hu : "—"}
                      </td>
                      <td
                        className={`px-2 py-1.5 text-right font-mono font-bold cursor-pointer ${deltaCls}`}
                        onClick={() => openCaseModal(row.filename)}
                      >
                        {delta === null ? "—" : delta > 0 ? `+${delta}` : delta}
                      </td>
                      <td
                        className="px-2 py-1.5 text-[var(--ink-muted)] text-[11px] cursor-pointer"
                        onClick={() => openCaseModal(row.filename)}
                      >
                        {fmtKstTime(meta.confirmed_at)}
                      </td>
                      <td
                        className="px-2 py-1.5 text-[var(--ink-muted)] text-[11px] cursor-pointer"
                        onClick={() => openCaseModal(row.filename)}
                      >
                        {fmtKstTime(meta.indexed_at)}
                      </td>
                      <td className="px-2 py-1.5 text-center">
                        <button
                          type="button"
                          className="btn-ghost btn-sm text-[12px]"
                          onClick={(e) => {
                            e.stopPropagation();
                            setDeleteTarget(row);
                          }}
                          disabled={deleting}
                          title="이 사례를 RAG corpus 에서 제거 (md 파일 + AOSS doc 삭제)"
                        >
                          🗑
                        </button>
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>

        {/* 페이지네이션 */}
        {cases.total > HITL_PAGE_SIZE && (
          <div className="mt-2.5 flex items-center justify-end gap-2">
            <button
              className="btn-ghost btn-sm"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0 || casesLoading}
            >
              ← 이전
            </button>
            <span className="text-[12px] text-[var(--ink-muted)]">
              {page + 1} / {totalPages}
            </span>
            <button
              className="btn-ghost btn-sm"
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1 || casesLoading}
            >
              다음 →
            </button>
          </div>
        )}
      </div>

      {/* RAG Corpus 유연 관리 — GET /v2/hitl-rag/list 기반 */}
      <div className="panel-section">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <div className="text-[12.5px] font-semibold">
            RAG Corpus 관리
            {ragList?.stats && (
              <span className="ml-2 text-[11px] font-normal text-[var(--ink-muted)]">
                MD {ragList.stats.md_total}건 · 인덱스 {ragList.stats.indexed_total}건
                {ragList.stats.missing_in_index > 0 && (
                  <span className="ml-1 text-[var(--warn)]">· drift {ragList.stats.missing_in_index}건</span>
                )}
                {ragList.stats.orphan_in_index > 0 && (
                  <span className="ml-1 text-[var(--danger)]">· orphan {ragList.stats.orphan_in_index}건</span>
                )}
              </span>
            )}
          </div>
          <button
            className="btn-ghost btn-sm text-[11px]"
            onClick={refreshRagList}
            disabled={ragListLoading}
          >
            {ragListLoading ? "조회 중..." : "↻ 목록 갱신"}
          </button>
        </div>

        {ragListError && (
          <div className="mb-2 rounded-[var(--radius-sm)] border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-[12px] text-[var(--danger)]">
            ⚠ 목록 조회 실패: {ragListError}
          </div>
        )}

        {/* MD 파일 목록 테이블 */}
        <div className="overflow-auto rounded-[var(--radius-sm)] border border-[var(--border)] mb-3">
          <table className="w-full text-[12px] border-collapse">
            <thead>
              <tr className="bg-[var(--surface-sunken)] text-[var(--ink-muted)]">
                <th className="px-2 py-1.5 text-left border-b border-[var(--border)]">파일</th>
                <th className="px-2 py-1.5 text-left border-b border-[var(--border)]">cid</th>
                <th className="px-2 py-1.5 text-center border-b border-[var(--border)]">item</th>
                <th className="px-2 py-1.5 text-left border-b border-[var(--border)]">항목명</th>
                <th className="px-2 py-1.5 text-right border-b border-[var(--border)]">AI</th>
                <th className="px-2 py-1.5 text-right border-b border-[var(--border)]">사람</th>
                <th className="px-2 py-1.5 text-right border-b border-[var(--border)]">Δ</th>
                <th className="px-2 py-1.5 text-left border-b border-[var(--border)]">indexed_at</th>
                <th className="px-2 py-1.5 text-center border-b border-[var(--border)]">drift</th>
                <th className="px-2 py-1.5 text-center border-b border-[var(--border)]">삭제</th>
              </tr>
            </thead>
            <tbody>
              {ragListLoading && (
                <tr>
                  <td colSpan={10} className="px-2 py-6 text-center text-[var(--ink-muted)]">
                    불러오는 중...
                  </td>
                </tr>
              )}
              {!ragListLoading && (!ragList || ragList.items.length === 0) && (
                <tr>
                  <td colSpan={10} className="px-2 py-6 text-center text-[var(--ink-muted)]">
                    {ragList ? "MD 파일이 없습니다." : "목록을 불러오려면 갱신을 누르세요."}
                  </td>
                </tr>
              )}
              {!ragListLoading &&
                ragList?.items.map((row) => {
                  const ai = typeof row.ai_score === "number" ? row.ai_score : null;
                  const hu = typeof row.human_score === "number" ? row.human_score : null;
                  const delta = ai !== null && hu !== null ? hu - ai : null;
                  const deltaCls =
                    delta === null
                      ? "text-[var(--ink-muted)]"
                      : delta > 0
                        ? "text-[var(--success)]"
                        : delta < 0
                          ? "text-[var(--danger)]"
                          : "text-[var(--ink)]";
                  return (
                    <tr
                      key={row.filename}
                      className="border-b border-[var(--border-subtle)] hover:bg-[var(--surface-muted)] transition-colors"
                    >
                      <td className="px-2 py-1.5 font-mono text-[10.5px] max-w-[120px] truncate" title={row.filename}>
                        {row.filename}
                      </td>
                      <td className="px-2 py-1.5 font-mono text-[11px]">{row.consultation_id}</td>
                      <td className="px-2 py-1.5 text-center">#{row.item_number}</td>
                      <td className="px-2 py-1.5 max-w-[160px] truncate text-[11px]" title={row.item_name ?? ""}>
                        {row.item_name ?? "—"}
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono">{ai !== null ? ai : "—"}</td>
                      <td className="px-2 py-1.5 text-right font-mono">{hu !== null ? hu : "—"}</td>
                      <td className={`px-2 py-1.5 text-right font-mono font-bold ${deltaCls}`}>
                        {delta === null ? "—" : delta > 0 ? `+${delta}` : delta}
                      </td>
                      <td className="px-2 py-1.5 text-[var(--ink-muted)] text-[10.5px]">
                        {fmtKstTime(row.indexed_at)}
                      </td>
                      <td className="px-2 py-1.5 text-center">
                        {row.in_index === null ? (
                          <span className="text-[10px] text-[var(--ink-muted)]">—</span>
                        ) : row.drift === "missing_in_index" ? (
                          <span
                            className="inline-block rounded px-1 py-0.5 text-[10px] font-semibold bg-[var(--warn-bg)] text-[var(--warn)]"
                            title="AOSS 인덱스에 없음 — 재빌드 권장"
                          >
                            인덱스 없음
                          </span>
                        ) : (
                          <span className="text-[10px] text-[var(--success)]">✓</span>
                        )}
                      </td>
                      <td className="px-2 py-1.5 text-center">
                        <button
                          type="button"
                          className="btn-ghost btn-sm text-[12px]"
                          title="MD + 인덱스 doc 삭제 (review 큐 보존)"
                          onClick={() =>
                            setFlexDeleteTarget({
                              item: row,
                              mode: "both",
                              label: `${row.consultation_id} #${row.item_number}`,
                            })
                          }
                          disabled={flexDeleting}
                        >
                          🗑
                        </button>
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>

        {/* orphan_indices 섹션 */}
        {ragList && ragList.orphan_indices.length > 0 && (
          <div>
            <div className="text-[12px] font-semibold text-[var(--danger)] mb-1.5">
              🔴 인덱스 잔여 (MD 없음) — {ragList.orphan_indices.length}건
            </div>
            <div className="overflow-auto rounded-[var(--radius-sm)] border border-[var(--danger-border)]">
              <table className="w-full text-[12px] border-collapse">
                <thead>
                  <tr className="bg-[var(--danger-bg)] text-[var(--danger)]">
                    <th className="px-2 py-1.5 text-left border-b border-[var(--danger-border)]">cid</th>
                    <th className="px-2 py-1.5 text-center border-b border-[var(--danger-border)]">item</th>
                    <th className="px-2 py-1.5 text-right border-b border-[var(--danger-border)]">doc 수</th>
                    <th className="px-2 py-1.5 text-center border-b border-[var(--danger-border)]">인덱스만 삭제</th>
                  </tr>
                </thead>
                <tbody>
                  {ragList.orphan_indices.map((orp) => (
                    <tr
                      key={`${orp.consultation_id}-${orp.item_number}`}
                      className="border-b border-[var(--border-subtle)] hover:bg-[var(--surface-muted)] transition-colors"
                    >
                      <td className="px-2 py-1.5 font-mono text-[11px]">{orp.consultation_id}</td>
                      <td className="px-2 py-1.5 text-center">#{orp.item_number}</td>
                      <td className="px-2 py-1.5 text-right font-mono">{orp.doc_count}</td>
                      <td className="px-2 py-1.5 text-center">
                        <button
                          type="button"
                          className="btn-danger btn-sm text-[11px]"
                          title="AOSS 인덱스 doc 만 삭제 (MD 없음)"
                          onClick={() =>
                            setFlexDeleteTarget({
                              item: orp,
                              mode: "index_only",
                              label: `${orp.consultation_id} #${orp.item_number} (orphan)`,
                            })
                          }
                          disabled={flexDeleting}
                        >
                          인덱스만 삭제
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* 유연 삭제 confirm 모달 */}
      {flexDeleteTarget && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center bg-black/45 px-4"
          onClick={() => !flexDeleting && setFlexDeleteTarget(null)}
        >
          <div
            className="w-full max-w-[480px] rounded-[var(--radius)] bg-[var(--surface)] p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-2 text-[16px] font-bold text-[var(--danger)]">
              ⚠ RAG 사례 삭제
            </div>
            <div className="mb-4 text-[13px] leading-relaxed text-[var(--ink)]">
              이 사례의 MD 파일 + 인덱스 doc 을 삭제합니다.
              <br />
              <span className="text-[var(--ink-muted)] text-[11px]">review 큐(human_reviews)는 영향 받지 않습니다.</span>
              <div className="mt-2 rounded-[var(--radius-sm)] bg-[var(--surface-muted)] p-2 font-mono text-[11px]">
                <b>{flexDeleteTarget.label}</b>
                <br />
                mode: <b>{flexDeleteTarget.mode}</b>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button
                className="btn-ghost btn-sm"
                onClick={() => setFlexDeleteTarget(null)}
                disabled={flexDeleting}
              >
                취소
              </button>
              <button
                className="btn-danger btn-sm"
                onClick={runFlexDelete}
                disabled={flexDeleting}
              >
                {flexDeleting ? "삭제 중..." : "삭제"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* force 재임베딩 confirm 모달 */}
      {showForceConfirm && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center bg-black/40 px-4"
          onClick={() => setShowForceConfirm(false)}
        >
          <div
            className="w-full max-w-[460px] rounded-[var(--radius)] bg-[var(--surface)] p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-2 text-[16px] font-bold text-[var(--warn)]">
              ⚠ 전체 재임베딩 확인
            </div>
            <div className="mb-4 text-[13px] leading-relaxed text-[var(--ink)]">
              모든 MD 파일을 다시 임베딩합니다. 색인된 문서가 갱신되며 시간과 비용이 발생할 수 있습니다.
              <br />
              <b>force=true</b> 로 진행할까요?
            </div>
            <div className="flex justify-end gap-2">
              <button
                className="btn-ghost btn-sm"
                onClick={() => setShowForceConfirm(false)}
              >
                취소
              </button>
              <button
                className="btn-warn btn-sm"
                onClick={() => {
                  setShowForceConfirm(false);
                  runRebuild(true);
                }}
              >
                진행
              </button>
            </div>
          </div>
        </div>
      )}

      {/* DROP-INDEX 두 단계 confirm 모달 */}
      {showDropConfirm && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 px-4"
          onClick={() => {
            setShowDropConfirm(false);
            setDropTyped("");
          }}
        >
          <div
            className="w-full max-w-[500px] rounded-[var(--radius)] bg-[var(--surface)] p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-2 text-[16px] font-bold text-[var(--danger)]">
              ⚠ 인덱스 재생성
            </div>
            <div className="mb-3 text-[13px] leading-relaxed text-[var(--ink)]">
              현재 OpenSearch 인덱스를 삭제하고 새로 생성합니다. <b>되돌릴 수 없습니다.</b>
              <br />
              계속하려면 아래에 정확히{" "}
              <code className="kbd font-bold text-[var(--danger)]">DROP-INDEX</code> 를 입력하세요.
            </div>
            <input
              type="text"
              value={dropTyped}
              onChange={(e) => setDropTyped(e.target.value)}
              placeholder="DROP-INDEX"
              autoFocus
              className={`mb-4 w-full rounded-[var(--radius-sm)] border-2 bg-[var(--surface)] px-3 py-2 font-mono text-[13px] outline-none ${
                dropTyped === "DROP-INDEX"
                  ? "border-[var(--danger)]"
                  : "border-[var(--border-strong)]"
              }`}
            />
            <div className="flex justify-end gap-2">
              <button
                className="btn-ghost btn-sm"
                onClick={() => {
                  setShowDropConfirm(false);
                  setDropTyped("");
                }}
              >
                취소
              </button>
              <button
                className="btn-danger btn-sm"
                onClick={runRecreate}
                disabled={dropTyped !== "DROP-INDEX"}
              >
                DROP & CREATE
              </button>
            </div>
          </div>
        </div>
      )}

      {/* PURGE-ALL 두 단계 confirm 모달 */}
      {showPurgeConfirm && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 px-4"
          onClick={() => {
            setShowPurgeConfirm(false);
            setPurgeTyped("");
          }}
        >
          <div
            className="w-full max-w-[520px] rounded-[var(--radius)] bg-[var(--surface)] p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-2 text-[16px] font-bold text-[var(--danger)]">
              ⚠ HITL RAG 전체 삭제
            </div>
            <div className="mb-3 text-[13px] leading-relaxed text-[var(--ink)]">
              <b>MD 파일 + AOSS 인덱스 + orphan</b> 모두 삭제합니다. <b>되돌릴 수 없습니다.</b>
              <br />
              계속하려면 아래에 정확히{" "}
              <code className="kbd font-bold text-[var(--danger)]">PURGE-ALL</code> 을 입력하세요.
            </div>
            <input
              type="text"
              value={purgeTyped}
              onChange={(e) => setPurgeTyped(e.target.value)}
              placeholder="PURGE-ALL"
              autoFocus
              className={`mb-4 w-full rounded-[var(--radius-sm)] border-2 bg-[var(--surface)] px-3 py-2 font-mono text-[13px] outline-none ${
                purgeTyped === "PURGE-ALL"
                  ? "border-[var(--danger)]"
                  : "border-[var(--border-strong)]"
              }`}
            />
            <div className="flex justify-end gap-2">
              <button
                className="btn-ghost btn-sm"
                onClick={() => {
                  setShowPurgeConfirm(false);
                  setPurgeTyped("");
                }}
                disabled={actionBusy}
              >
                취소
              </button>
              <button
                className="btn-danger btn-sm"
                onClick={runPurgeAll}
                disabled={purgeTyped !== "PURGE-ALL" || actionBusy}
              >
                {actionBusy ? "삭제 중..." : "전체 삭제"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 사례 삭제 확인 모달 */}
      {deleteTarget && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center bg-black/45 px-4"
          onClick={() => !deleting && setDeleteTarget(null)}
        >
          <div
            className="w-full max-w-[480px] rounded-[var(--radius)] bg-[var(--surface)] p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-2 text-[16px] font-bold text-[var(--danger)]">
              ⚠ HITL 사례 삭제
            </div>
            <div className="mb-4 text-[13px] leading-relaxed text-[var(--ink)]">
              이 사례를 RAG corpus 에서 제거합니다 — <b>md 파일 + AOSS 인덱스 doc</b> 모두 삭제. 되돌릴 수 없습니다.
              <div className="mt-2 rounded-[var(--radius-sm)] bg-[var(--surface-muted)] p-2 font-mono text-[11px] text-[var(--ink-soft)]">
                consultation_id: <b>{deleteTarget.meta?.consultation_id ?? "—"}</b>
                <br />
                item: <b>#{deleteTarget.meta?.item_number ?? "?"}</b> {deleteTarget.meta?.item_name ?? ""}
                <br />
                AI / Human: <b>{deleteTarget.meta?.ai_score ?? "—"}</b> / <b>{deleteTarget.meta?.human_score ?? "—"}</b>
                <br />
                file: <b>{deleteTarget.filename}</b>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button
                className="btn-ghost btn-sm"
                onClick={() => setDeleteTarget(null)}
                disabled={deleting}
              >
                취소
              </button>
              <button
                className="btn-danger btn-sm"
                onClick={runDeleteCase}
                disabled={deleting}
              >
                {deleting ? "삭제 중..." : "삭제"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 사례 상세 모달 — MD body raw 렌더 */}
      {openCase && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center bg-black/45 px-4 py-6"
          onClick={closeCaseModal}
        >
          <div
            className="flex max-h-[90vh] w-full max-w-[900px] flex-col overflow-hidden rounded-[var(--radius)] bg-[var(--surface)] shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] px-4 py-3">
              <div>
                <div className="text-[14px] font-bold text-[var(--ink)]">
                  {openCase.detail?.meta?.consultation_id ?? openCase.filename}
                </div>
                <div className="mt-0.5 font-mono text-[10.5px] text-[var(--ink-muted)]">
                  {openCase.filename}
                </div>
              </div>
              <button
                className="btn-ghost btn-sm"
                onClick={closeCaseModal}
                title="닫기"
              >
                ✕
              </button>
            </div>
            {openCase.detail?.meta && Object.keys(openCase.detail.meta).length > 0 && (
              <div className="flex flex-wrap gap-3 border-b border-[var(--border)] bg-[var(--surface-sunken)] px-4 py-2 text-[11px] text-[var(--ink)]">
                {openCase.detail.meta.item_number !== undefined && (
                  <span>
                    항목: <b>#{openCase.detail.meta.item_number}</b>
                  </span>
                )}
                {openCase.detail.meta.item_name && (
                  <span>
                    이름: <b>{openCase.detail.meta.item_name}</b>
                  </span>
                )}
                {openCase.detail.meta.ai_score !== undefined && (
                  <span>
                    AI: <b>{openCase.detail.meta.ai_score}</b>
                  </span>
                )}
                {openCase.detail.meta.human_score !== undefined && (
                  <span>
                    Human: <b>{openCase.detail.meta.human_score}</b>
                  </span>
                )}
                {openCase.detail.meta.confirmed_at && (
                  <span>
                    confirmed: <b>{fmtKstTime(openCase.detail.meta.confirmed_at)}</b>
                  </span>
                )}
                {openCase.detail.meta.indexed_at && (
                  <span>
                    indexed: <b>{fmtKstTime(openCase.detail.meta.indexed_at)}</b>
                  </span>
                )}
              </div>
            )}
            <div className="flex-1 overflow-auto px-4 py-3">
              {openCase.loading && (
                <div className="text-[13px] text-[var(--ink-muted)]">불러오는 중...</div>
              )}
              {openCase.error && (
                <div className="rounded-[var(--radius-sm)] border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-[12px] text-[var(--danger)]">
                  ⚠ {openCase.error}
                </div>
              )}
              {!openCase.loading && !openCase.error && (
                <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-[var(--ink)]">
                  {openCase.detail?.body || "(본문 없음)"}
                </pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   KmsRagResetSection — KMS LinearRAG 디스크 + in-memory 캐시 초기화
   2026-05-08: 파이프라인 탭(EvaluateRunner) 에서 이전.
   /v2/kms-rag/reset 호출 → 다음 KMS 평가 시 재인덱싱 (~30~120초).
   ───────────────────────────────────────────────────────────── */

interface KmsRagResetResult {
  ok?: boolean;
  deleted_files?: number;
  deleted_bytes?: number;
  error?: string;
}

function KmsRagResetSection() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<{
    ok: boolean;
    message: string;
    at: number;
  } | null>(null);

  const handleReset = useCallback(async () => {
    if (
      !window.confirm(
        "KMS LinearRAG 디스크 캐시를 초기화하시겠어요?\n다음 KMS 호출 시 인덱싱이 다시 수행됩니다 (~30~120초).",
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      const base = BASE_URL.replace(/\/$/, "");
      const res = await fetch(`${base}/v2/kms-rag/reset`, { method: "POST" });
      const body = (await res.json().catch(() => ({}))) as KmsRagResetResult;
      if (res.ok && body.ok) {
        const kb = ((body.deleted_bytes ?? 0) / 1024).toFixed(1);
        const msg = `초기화 완료 — 삭제 파일 ${body.deleted_files ?? 0}개 (${kb} KB). 다음 KMS 호출 시 재인덱싱.`;
        setLastResult({ ok: true, message: msg, at: Date.now() });
        toast.success("KMS RAG 캐시 초기화 완료", { description: msg });
      } else {
        const msg = `초기화 실패: ${body.error ?? res.statusText}`;
        setLastResult({ ok: false, message: msg, at: Date.now() });
        toast.error("KMS RAG 초기화 실패", { description: msg });
      }
    } catch (e) {
      const msg = `호출 실패: ${e instanceof Error ? e.message : String(e)}`;
      setLastResult({ ok: false, message: msg, at: Date.now() });
      toast.error("KMS RAG 초기화 호출 실패", { description: msg });
    } finally {
      setBusy(false);
    }
  }, [toast]);

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-header">
        <div>
          <div className="panel-title">🗑️ KMS LinearRAG 캐시 초기화</div>
          <div className="text-[12px] text-[var(--ink-muted)] mt-0.5">
            디스크 캐시 (TEMP/qa_kms_linear_rag) + in-memory 인덱스 초기화. 다음 KMS
            호출 시 재인덱싱 (~30~120초 소요).
          </div>
        </div>
        <button
          className="btn-secondary btn-sm"
          onClick={handleReset}
          disabled={busy}
          title="KMS LinearRAG 디스크 캐시 + in-memory 캐시 초기화"
        >
          {busy ? "초기화 중..." : "🗑️ 초기화 실행"}
        </button>
      </div>
      {lastResult && (
        <div className="panel-section">
          <div
            className={`rounded-[var(--radius-sm)] border px-3 py-2 text-[12px] ${
              lastResult.ok
                ? "border-[var(--border)] bg-[var(--surface-muted)] text-[var(--ink-soft)]"
                : "border-[var(--danger-border)] bg-[var(--danger-bg)] text-[var(--danger)]"
            }`}
          >
            {lastResult.ok ? "✅ " : "⚠ "}
            {lastResult.message}
          </div>
        </div>
      )}
    </div>
  );
}

export default RagAdminPanel;
