// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  flashElement,
  subscribeScrollToTurn,
  turnDomId,
} from "@/lib/transcriptNav";

/**
 * TranscriptTurnList — preprocessing.turns 로 파싱된 발화를 행 단위로 렌더.
 *   - 각 행에 stable `id="turn-{N}"` 부여 → ItemCard evidence T#N 클릭 시 점프 대상.
 *   - `qa:scroll-to-turn` CustomEvent listen → scrollIntoView + flash highlight.
 *
 * Pipeline 탭의 transcript textarea 아래 inline 으로 표시.
 * preprocessing.turns 가 없으면 (실행 전 / 실패) null 반환.
 */
export interface TranscriptTurn {
  turn_id?: number;
  speaker?: string;
  text?: string;
  segment?: string;
}

interface Props {
  turns: TranscriptTurn[] | null | undefined;
  /** 기본 펼침 여부.
   *  ★ 2026-05-07: false 기본값으로 변경. 170 턴이 파이프라인 메인 화면을 덮는 이슈.
   *  T#N 클릭 시 subscribeScrollToTurn 콜백이 setOpen(true) 강제하므로 정상 동작 유지. */
  defaultOpen?: boolean;
}

export function TranscriptTurnList({ turns, defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const list = useMemo(() => {
    if (!Array.isArray(turns)) return [];
    return turns.map((t, i) => ({
      tid: typeof t.turn_id === "number" ? t.turn_id : i + 1,
      speaker: t.speaker || "-",
      text: t.text || "",
      segment: t.segment,
    }));
  }, [turns]);

  // CustomEvent listener — Results 탭에서 evidence 클릭 시 호출됨.
  // 자동으로 collapsed 상태에서도 펼쳐서 scroll 가능하도록 setOpen(true) 강제.
  useEffect(() => {
    const unsubscribe = subscribeScrollToTurn((turnId) => {
      setOpen(true);
      // setOpen 직후 DOM 이 아직 mount 안 됐을 수 있어 next frame 에 lookup.
      requestAnimationFrame(() => {
        const el = document.getElementById(turnDomId(turnId));
        if (!el) return;
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        flashElement(el);
      });
    });
    return unsubscribe;
  }, []);

  if (list.length === 0) return null;

  return (
    <div
      ref={containerRef}
      className="mt-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface-muted)]"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] font-semibold text-[var(--ink-muted)] hover:text-[var(--accent)]"
        aria-expanded={open}
      >
        <span className="inline-block w-3">{open ? "▾" : "▸"}</span>
        <span className="uppercase tracking-wide">파싱된 발화 · {list.length}턴</span>
        <span className="ml-auto text-[10px] font-normal text-[var(--ink-subtle)]">
          평가 근거의 #N 클릭 시 자동 스크롤
        </span>
      </button>
      {open && (
        <div
          style={{
            maxHeight: 360,
            overflow: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 2,
            padding: "6px 8px 10px",
          }}
        >
          {list.map(({ tid, speaker, text, segment }, i) => {
            const isAgent = /(상담|agent|직원|상담사)/i.test(speaker);
            return (
              <div
                key={`turn-${tid}-${i}`}
                id={turnDomId(tid)}
                className="transcript-turn-row"
                style={{
                  padding: "4px 8px",
                  borderRadius: "var(--radius-sm)",
                  fontSize: 11,
                  lineHeight: 1.55,
                  display: "flex",
                  gap: 8,
                  alignItems: "flex-start",
                  border: "1px solid transparent",
                }}
              >
                <span
                  style={{
                    flexShrink: 0,
                    fontVariantNumeric: "tabular-nums",
                    color: "var(--ink-subtle)",
                    fontWeight: 600,
                    minWidth: 24,
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
                    minWidth: 52,
                  }}
                >
                  [{speaker}]
                </span>
                {segment && (
                  <span
                    style={{
                      flexShrink: 0,
                      fontSize: 9,
                      fontWeight: 700,
                      padding: "1px 5px",
                      borderRadius: 8,
                      background: "var(--surface)",
                      border: "1px solid var(--border)",
                      color: "var(--ink-muted)",
                    }}
                  >
                    {segment}
                  </span>
                )}
                <span style={{ color: "var(--ink)", flex: 1, wordBreak: "break-word" }}>
                  {text}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default TranscriptTurnList;
