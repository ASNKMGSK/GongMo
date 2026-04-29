// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * Placeholder — 다른 Dev 의 탭이 미완성일 때 임시로 채우는 스켈레톤.
 * 각 탭 stub 파일 (ResultsPanel.tsx 등) 이 기본으로 이 컴포넌트를 export 하고,
 * Dev3/4/5 가 해당 파일 전체를 overwrite 하면서 구현체로 교체.
 */
export function Placeholder({
  title,
  owner,
  note,
}: {
  title: string;
  owner: string;
  note?: string;
}) {
  return (
    <div className="empty-state" data-testid={`placeholder-${title}`}>
      <svg
        className="empty-state-icon"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <path d="M3 9h18M9 21V9" />
      </svg>
      <div className="empty-state-title">준비 중 — {owner} 작업</div>
      <div className="empty-state-desc">
        <b>{title}</b> 패널은 {owner} 가 구현 예정입니다.
        {note ? (
          <>
            <br />
            <span className="text-[var(--ink-subtle)]">{note}</span>
          </>
        ) : null}
      </div>
    </div>
  );
}

export default Placeholder;
