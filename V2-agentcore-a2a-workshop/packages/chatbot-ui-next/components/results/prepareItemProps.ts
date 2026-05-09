// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { STT_MAX_SCORES } from "@/lib/items";
import type {
  CategoryItem,
  DebateRecord,
  DeductionEntry,
  EvidenceEntry,
} from "@/lib/types";

/**
 * ItemCard 에 넘길 props 준비 — V2 HTML 내 prepareItemProps 유틸 이식.
 *   report 최상위의 deductions/strengths/improvements/coaching_points 배열에서
 *   해당 item_number 에 해당하는 항목만 필터링.
 */

export interface RawReport {
  deductions?: Array<DeductionEntry & { item_number?: number }>;
  strengths?: Array<
    | string
    | { description?: string; reference_items?: number[] }
  >;
  improvements?: Array<
    | string
    | { description?: string; reference_items?: number[] }
  >;
  coaching_points?: Array<{
    priority?: string;
    area?: string;
    title?: string;
    suggestion?: string;
    description?: string;
    reference_items?: number[];
  }>;
  /**
   * AG2 토론 결과 — QAStateV2.debates 직렬화. key=item_number 문자열.
   * EvaluationResult.debates 와 동일 형식. ResultsTab 이 lastResult 에서
   * 추출해 RawReport 에 주입한 뒤 prepareItemProps 가 항목별로 lookup.
   */
  debates?: Record<string, DebateRecord> | null;
}

export interface PreparedItemProps {
  sc: number;
  mx: number;
  itemDeds: DeductionEntry[];
  itemStr: Array<string | { description?: string }>;
  itemImp: Array<string | { description?: string }>;
  itemCoach: Array<{
    priority?: string;
    area?: string;
    title?: string;
    suggestion?: string;
    description?: string;
  }>;
  itemEv: Array<EvidenceEntry | string>;
  hasDetail: boolean;
  /** ★ 페르소나별 평가 상세 (strict / neutral / loose) — ItemCard 의
   *  PersonaExecutionDetails 가 펼침 영역에서 렌더. 백엔드 누락 시 null. */
  personaDetails: CategoryItem["persona_details"] | null;
  /** ★ 항목별 AG2 토론 기록 (라운드·페르소나 발언·모더레이터 판정·최종 합의).
   *  RawReport.debates[String(item_number)] 에서 lookup. 누락 시 null. */
  debateRecord: DebateRecord | null;
}

export function prepareItemProps(
  item: CategoryItem,
  num: number,
  report: RawReport,
): PreparedItemProps {
  const sc = item.score ?? 0;
  const mx = item.max_score ?? STT_MAX_SCORES[num] ?? 0;

  const itemDeds = (report.deductions || []).filter(
    (d) => d.item_number === num,
  );

  const itemStr = (report.strengths || []).filter((s) => {
    if (typeof s === "string") return false;
    return (
      Array.isArray(s.reference_items) &&
      s.reference_items.includes(num)
    );
  });

  const itemImp = (report.improvements || []).filter((s) => {
    if (typeof s === "string") return false;
    return (
      Array.isArray(s.reference_items) &&
      s.reference_items.includes(num)
    );
  });

  const itemCoach = (report.coaching_points || []).filter(
    (c) => Array.isArray(c.reference_items) && c.reference_items.includes(num),
  );

  const itemEv = Array.isArray(item.evidence) ? item.evidence : [];

  const personaDetails = item.persona_details ?? null;
  const debateRecord = report.debates?.[String(num)] ?? null;

  const hasDetail =
    itemDeds.length > 0 ||
    itemStr.length > 0 ||
    itemImp.length > 0 ||
    itemCoach.length > 0 ||
    itemEv.length > 0 ||
    !!personaDetails ||
    !!debateRecord;

  return {
    sc,
    mx,
    itemDeds,
    itemStr,
    itemImp,
    itemCoach,
    itemEv,
    hasDetail,
    personaDetails,
    debateRecord,
  };
}
