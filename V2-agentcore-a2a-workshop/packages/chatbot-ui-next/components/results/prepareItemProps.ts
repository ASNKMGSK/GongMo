// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { STT_MAX_SCORES } from "@/lib/items";
import type { CategoryItem, DeductionEntry, EvidenceEntry } from "@/lib/types";

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

  const hasDetail =
    itemDeds.length > 0 ||
    itemStr.length > 0 ||
    itemImp.length > 0 ||
    itemCoach.length > 0 ||
    itemEv.length > 0;

  return { sc, mx, itemDeds, itemStr, itemImp, itemCoach, itemEv, hasDetail };
}
