// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useMemo, useState } from "react";

import { useAppState } from "@/lib/AppStateContext";

/**
 * TenantStatusBadge — site_id × channel × department 조합에 대한
 * fallback 해석 결과를 실시간 칩으로 표시 (2026-04-27).
 *
 * 4단계 fallback 체인:
 *   1) site/channel/dept 폴더 존재    → ✓ 부서 매칭         (exact)
 *   2) site/channel 폴더 존재          → ↘ 채널 공통 fallback (channel)
 *   3) site 폴더 존재                  → ↘ 사이트 공통        (site)
 *   4) 위 모두 없음                     → → generic           (generic)
 *
 * /v2/rag/scopes 의 scopes 배열을 참조하여 해석 (경량 endpoint — AOSS 호출 없음).
 * 모듈 레벨 1분 TTL 캐시.
 */

interface ScopeEntry {
  site_id: string;
  channel: string | null;
  department: string | null;
}

let scopesCache: { data: ScopeEntry[]; ts: number } | null = null;
const CACHE_TTL_MS = 60_000;

type Level = "loading" | "exact" | "channel" | "site" | "generic";

interface Resolution {
  level: Level;
  label: string;
  detail: string;
  badgeClass: string;
  icon: string;
  /** 실제 fallback 후 사용 중인 자원 경로 (예: "kolon/", "kolon/inbound/", "generic/") */
  resolvedPath: string;
}

function computeResolution(
  siteId: string,
  channel: string,
  department: string,
  scopes: ScopeEntry[] | null,
): Resolution {
  if (scopes === null) {
    return {
      level: "loading",
      label: "확인 중…",
      detail: "tenant scope 로딩 중",
      badgeClass: "tenant-badge-loading",
      icon: "○",
      resolvedPath: "",
    };
  }
  if (scopes.some((s) => s.site_id === siteId && s.channel === channel && s.department === department)) {
    return {
      level: "exact",
      label: "부서 매칭",
      detail: `${siteId}/${channel}/${department} 전용 폴더 사용`,
      badgeClass: "tenant-badge-exact",
      icon: "✓",
      resolvedPath: `${siteId}/${channel}/${department}`,
    };
  }
  if (scopes.some((s) => s.site_id === siteId && s.channel === channel && s.department === null)) {
    return {
      level: "channel",
      label: "채널 공통",
      detail: `${siteId}/${channel}/ (부서 폴더 없음 → 채널 공통 자원 사용)`,
      badgeClass: "tenant-badge-channel",
      icon: "↘",
      resolvedPath: `${siteId}/${channel}/`,
    };
  }
  if (scopes.some((s) => s.site_id === siteId && s.channel === null)) {
    return {
      level: "site",
      label: "사이트 공통",
      detail: `${siteId}/ (채널 공통도 없음 → 사이트 공통 자원 사용)`,
      badgeClass: "tenant-badge-site",
      icon: "↘",
      resolvedPath: `${siteId}/`,
    };
  }
  return {
    level: "generic",
    label: "generic",
    detail: `${siteId} 사이트 미존재 → generic fallback 자원 사용`,
    badgeClass: "tenant-badge-generic",
    icon: "→",
    resolvedPath: "generic/",
  };
}

interface TenantStatusBadgeProps {
  siteId: string;
  channel: string;
  department: string;
  /** 변경 감지용 카운터 — 값이 바뀌면 flash 애니메이션 재시작 */
  flashKey: number;
}

export function TenantStatusBadge({
  siteId,
  channel,
  department,
  flashKey,
}: TenantStatusBadgeProps) {
  const { state } = useAppState();
  // useState lazy initializer — 캐시 있으면 즉시 반영, 없으면 null (effect 가 fetch).
  const [scopes, setScopes] = useState<ScopeEntry[] | null>(() => {
    if (scopesCache && Date.now() - scopesCache.ts < CACHE_TTL_MS) {
      return scopesCache.data;
    }
    return null;
  });

  useEffect(() => {
    // 캐시 유효하면 fetch 생략 (state 는 lazy init 으로 이미 채워졌거나, 다음 변경 때 갱신됨).
    if (scopesCache && Date.now() - scopesCache.ts < CACHE_TTL_MS) return;
    let cancelled = false;
    fetch(`${state.serverUrl}/v2/rag/scopes`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled) return;
        const next: ScopeEntry[] = Array.isArray(data?.scopes)
          ? data.scopes.map((s: ScopeEntry) => ({
              site_id: s.site_id,
              channel: s.channel,
              department: s.department,
            }))
          : [];
        scopesCache = { data: next, ts: Date.now() };
        setScopes(next);
      })
      .catch(() => {
        // 백엔드 미가용 — 빈 배열로 처리하면 항상 generic fallback 으로 표시됨.
        if (!cancelled) setScopes([]);
      });
    return () => {
      cancelled = true;
    };
  }, [state.serverUrl]);

  const resolution = useMemo(
    () => computeResolution(siteId, channel, department, scopes),
    [siteId, channel, department, scopes],
  );

  return (
    <span
      key={flashKey}
      className={`tenant-badge ${resolution.badgeClass}`}
      title={resolution.detail}
      aria-label={`테넌트 해석 상태: ${resolution.label}. ${resolution.detail}`}
    >
      <span className="tenant-badge-icon" aria-hidden="true">
        {resolution.icon}
      </span>
      <span className="tenant-badge-text">{resolution.label}</span>
      {resolution.resolvedPath && (
        <>
          <span className="tenant-badge-sep" aria-hidden="true">
            ·
          </span>
          <span className="tenant-badge-path" title={`실제 사용 자원: ${resolution.resolvedPath}`}>
            {resolution.resolvedPath}
          </span>
        </>
      )}
    </span>
  );
}
