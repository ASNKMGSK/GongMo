// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useMemo, useState } from "react";

import { STT_MAX_SCORES } from "@/lib/items";
import {
  buildUnifiedRagBundle,
  type PersonaHitlCaseLike,
  type RagFewshotDetail,
  type UnifiedRagHit,
} from "@/lib/ragHitsAggregator";

/**
 * UnifiedRagPanel — 두 RAG panel (RagHitsPanel + PersonaHitlSection) 의 통합 후속.
 *
 * 두 시점 (sub-agent 평가 / 페르소나 broadcast) 에서 retrieve 된 hit 를 example_id 단위로
 * dedup 합산해 1 panel 로 보여준다. 같은 example_id 가 양쪽에 있으면 1 카드 + "🤖 / 🎭 ✓"
 * 두 배지로 표시.
 */

const _DASH_LINE_RE = /^[\-=*_~·•·\s]{3,}$/;
function stripSeparatorLines(text: string | null | undefined): string {
  if (!text) return "";
  const out: string[] = [];
  for (const raw of String(text).split("\n")) {
    const stripped = raw.trim();
    if (stripped && _DASH_LINE_RE.test(stripped)) continue;
    out.push(raw.replace(/\s+$/, ""));
  }
  const compact: string[] = [];
  let prevBlank = false;
  for (const ln of out) {
    const blank = !ln.trim();
    if (blank && prevBlank) continue;
    compact.push(ln);
    prevBlank = blank;
  }
  return compact.join("\n").trim();
}

function SourceBadge({ source }: { source: UnifiedRagHit["source"] }) {
  const meta = (() => {
    switch (source) {
      case "golden_set":
        return {
          label: "🌱 골든셋",
          title: "qa-golden-set — 학습셋 사람 검수 정답",
          bg: "var(--success-bg)",
          fg: "var(--success)",
        };
      case "hitl":
        return {
          label: "📚 HITL",
          title: "qa-hitl-cases — 운영 검수 누적",
          bg: "var(--warn-bg)",
          fg: "var(--warn)",
        };
      case "self_match":
        return {
          label: "🔁 동일상담",
          title: "현재 평가 중인 원문 자체의 매칭",
          bg: "var(--danger-bg)",
          fg: "var(--danger)",
        };
      default:
        return {
          label: "❓ 미분류",
          title: "출처 미식별",
          bg: "var(--surface-muted)",
          fg: "var(--ink-muted)",
        };
    }
  })();
  return (
    <span
      title={meta.title}
      style={{
        fontSize: 9.5,
        fontWeight: 700,
        background: meta.bg,
        color: meta.fg,
        padding: "1px 9px",
        borderRadius: "var(--radius-pill)",
        letterSpacing: "0.04em",
      }}
    >
      {meta.label}
    </span>
  );
}

// ★ 2026-05-08: 사용자 결정 — sub-agent 와 페르소나 가 같은 segment_text 로 RAG 통일
// 후 두 시점 사용 표시 (🤖 / 🎭) 가 항상 양쪽 켜져 노이즈만 됨. UsageBadges 제거.

function SimilarityChips({ hit }: { hit: UnifiedRagHit }) {
  const chips: React.ReactNode[] = [];
  const cos = hit.cosine_score;
  if (cos != null && Number.isFinite(cos)) {
    chips.push(
      <span
        key="cos"
        title="cosine 의미 유사도 (0~1)"
        style={{
          fontSize: 9.5,
          fontWeight: 700,
          background: "var(--accent-bg)",
          color: "var(--accent-strong)",
          padding: "1px 7px",
          borderRadius: "var(--radius-pill)",
        }}
      >
        cos {Number(cos).toFixed(2)}
      </span>,
    );
  }
  const rrf = hit.rrf_score;
  if (rrf != null && Number.isFinite(rrf)) {
    // 2026-05-08: RRF 0~1 정규화. 이론상 max = 2 retriever / (k+1) = 2/61 ≈ 0.0328.
    // 사용자 가독성을 위해 max=1.00 으로 스케일 + 소수점 2자리.
    const RRF_MAX = 2 / 61; // ≈ 0.03278689
    const rrfNorm = Math.min(1, Math.max(0, Number(rrf) / RRF_MAX));
    chips.push(
      <span
        key="rrf"
        title={`RRF (BM25+KNN 결합) — 0~1 정규화. raw=${Number(rrf).toFixed(4)} / max=${RRF_MAX.toFixed(4)} (k=60, retriever=2). 1.00 = 양쪽 retriever 모두 1위`}
        style={{
          fontSize: 9.5,
          fontWeight: 600,
          background: "var(--surface-muted)",
          color: "var(--ink-soft)",
          padding: "1px 7px",
          borderRadius: "var(--radius-pill)",
        }}
      >
        rrf {rrfNorm.toFixed(2)}
      </span>,
    );
  }
  const rr = hit.cohere_rerank_score;
  // 2026-05-08: provider 별 색상/라벨 차별화 — cohere 는 초록, llm 은 파랑.
  const provider = hit.rerank_provider;
  const providerLabel =
    provider === "llm" ? "🧠 LLM" : provider === "cohere" ? "🪶 Cohere" : "🎯";
  const providerBg = provider === "llm" ? "#dbeafe" : "#dcfce7";
  const providerFg = provider === "llm" ? "#1e40af" : "#166534";
  const providerTip =
    provider === "llm"
      ? "LLM (Haiku 4.5) reranker — 자연어 task 정의 기반 평가 패턴 매칭 (0~1)"
      : "Cohere Rerank 3.5 cross-encoder relevance_score (0~1)";
  if (rr != null && Number.isFinite(rr) && Number(rr) > 0) {
    chips.push(
      <span
        key="rerank"
        title={providerTip}
        style={{
          fontSize: 9.5,
          fontWeight: 700,
          background: providerBg,
          color: providerFg,
          padding: "1px 7px",
          borderRadius: "var(--radius-pill)",
        }}
      >
        {providerLabel} relevance_score {Number(rr).toFixed(3)}
      </span>,
    );
  } else if (hit.reranked && (rr == null || Number(rr) === 0)) {
    chips.push(
      <span
        key="rerank-fallback"
        title="Reranker 호출 실패 — 입력 순서 폴백 (relevance_score 미산출)"
        style={{
          fontSize: 9.5,
          fontWeight: 700,
          background: "#fef3c7",
          color: "#92400e",
          padding: "1px 7px",
          borderRadius: "var(--radius-pill)",
        }}
      >
        {providerLabel} relevance_score 폴백
      </span>,
    );
  }
  // ★ 2026-05-08: 사용자 결정 — sub-agent 와 페르소나 가 같은 segment_text 로 RAG 통일
  // 후 persona_knn_score 칩(`cos 0.xx`) 이 sub-agent cosine_score 와 동일 라벨로 중복
  // 표시되어 혼란 유발. UsageBadges 제거와 동일 논리로 칩 제거.
  // (persona_knn_score 데이터는 정렬 fallback 에서 계속 사용됨 — 칩만 비노출)
  if (chips.length === 0) return null;
  return (
    <span style={{ display: "inline-flex", gap: 4, flexWrap: "wrap" }}>
      {chips}
    </span>
  );
}

function CardSection({
  eyebrow,
  body,
  tone,
  collapsible,
  defaultOpen = true,
  alwaysCollapsible = false,
}: {
  eyebrow: string;
  body: string;
  tone: "default" | "info";
  collapsible?: boolean;
  /** 토글 초기 상태 — false 면 기본 접힘. parsed_text 등 긴 문맥에 사용. */
  defaultOpen?: boolean;
  /** true 면 body 길이와 무관하게 토글 강제 표시 (파싱원문은 짧아도 토글 노출). */
  alwaysCollapsible?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const showToggle =
    !!body && collapsible && (alwaysCollapsible || body.length > 320);
  const visible = showToggle && !open ? "" : body;
  if (!body) return null;
  return (
    <div
      style={{
        marginTop: 6,
        padding: tone === "info" ? "6px 8px" : "0 0 0 4px",
        background: tone === "info" ? "var(--info-bg)" : "transparent",
        border: tone === "info" ? "1px solid var(--info-border)" : "none",
        borderRadius: tone === "info" ? "var(--radius-sm)" : 0,
      }}
    >
      <div
        style={{
          fontSize: 9,
          fontWeight: 700,
          color: "var(--ink-muted)",
          marginBottom: 3,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          display: "flex",
          alignItems: "center",
          gap: 5,
        }}
      >
        <span>{eyebrow}</span>
        {showToggle && (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            style={{
              fontSize: 9,
              padding: "0 5px",
              background: "transparent",
              border: "1px solid var(--border-subtle)",
              borderRadius: 3,
              cursor: "pointer",
              color: "var(--ink-muted)",
            }}
          >
            {open ? "접기" : "펼치기"}
          </button>
        )}
      </div>
      <div
        style={{
          fontSize: 11,
          color: "var(--ink-soft)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          lineHeight: 1.55,
        }}
      >
        {visible}
      </div>
    </div>
  );
}

function UnifiedRagCard({ hit }: { hit: UnifiedRagHit }) {
  const itemNum = hit.item_number;
  const maxScore = itemNum ? STT_MAX_SCORES[itemNum] : null;
  const human = hit.human_score;
  const ai = hit.ai_score;
  const dt = hit.delta;
  const dtSign = typeof dt === "number" && dt > 0 ? "+" : "";
  const dtColor =
    typeof dt === "number"
      ? dt > 0
        ? "var(--success)"
        : dt < 0
          ? "var(--danger)"
          : "var(--ink-subtle)"
      : "var(--ink-subtle)";
  const isSelf = hit.source === "self_match" || !!hit.is_self_match;
  // 본문 우선순위:
  // - parsed_text (긴 문맥, persona case 만 있음) → 별도 info 박스로
  // - segment_text (sub-agent fewshot) 또는 transcript_excerpt (persona case)
  // - rationale (sub-agent) 또는 human_note (persona)
  // 2026-05-08: trim 후 truthy 체크 → whitespace-only string 으로 빈 라벨 표시되는 버그 수정.
  // 또한 segment/transcript 모두 비어있고 parsed_text 만 있는 케이스 (일부 골든셋 데이터에서
  // segment_text 추출 누락) 를 위해 parsed_text 첫 300자 fallback 추가.
  const _trim = (s: string | null | undefined) => String(s || "").trim();
  const _segRaw = _trim(hit.segment_text) || _trim(hit.transcript_excerpt);
  const _parsedRaw = _trim(hit.parsed_text);
  let segmentBody = _segRaw;
  let segmentFallbackUsed = false;
  if (!segmentBody && _parsedRaw) {
    // parsed_text 첫 300자 추출 — 발화 섹션 비어있는 골든셋 entries 보완.
    segmentBody = _parsedRaw.slice(0, 300) + (_parsedRaw.length > 300 ? " …" : "");
    segmentFallbackUsed = true;
  }
  const segmentLabel = segmentFallbackUsed
    ? "발화 (파싱 원문에서 자동 추출)"
    : hit.segment_text
      ? "발화"
      : hit.parsed_text
        ? "근거 · 검색된 발화"
        : "발화";
  const noteLabel = hit.source === "golden_set" ? "이유 · 검수자 코멘트" : "검수자 코멘트";
  const noteBody = _trim(hit.rationale) || _trim(hit.human_note);

  return (
    <div
      style={{
        marginBottom: 8,
        padding: "10px 12px",
        background: isSelf ? "var(--danger-bg)" : "var(--surface)",
        border: `1px solid ${isSelf ? "var(--danger-border)" : "var(--border)"}`,
        borderLeft: `3px solid ${
          isSelf
            ? "var(--danger)"
            : hit.source === "hitl"
              ? "#f59e0b"
              : hit.source === "golden_set"
                ? "#10b981"
                : "var(--border)"
        }`,
        borderRadius: "var(--radius-sm)",
      }}
    >
      <div
        style={{
          display: "flex",
          gap: 6,
          flexWrap: "wrap",
          alignItems: "center",
          marginBottom: 5,
        }}
      >
        <SourceBadge source={hit.source} />
        <code
          style={{
            fontSize: 10,
            fontWeight: 700,
            background: "var(--surface-muted)",
            color: "var(--ink)",
            padding: "1px 7px",
            borderRadius: "var(--radius-pill)",
          }}
        >
          {hit.example_id}
        </code>
        {hit.consultation_id && hit.consultation_id !== hit.example_id && (
          <code
            title="consultation_id"
            style={{
              fontSize: 9.5,
              background: "var(--surface-muted)",
              color: "var(--ink-muted)",
              padding: "1px 7px",
              borderRadius: "var(--radius-pill)",
            }}
          >
            cid {hit.consultation_id}
          </code>
        )}
        {itemNum && (
          <span
            style={{
              fontSize: 9.5,
              color: "var(--ink-muted)",
              background: "var(--surface-muted)",
              padding: "1px 6px",
              borderRadius: 4,
            }}
          >
            #{itemNum}
          </span>
        )}
        {hit.score_bucket && (
          <span
            style={{
              fontSize: 9.5,
              color: "var(--ink-muted)",
              background: "var(--surface-muted)",
              padding: "1px 6px",
              borderRadius: 4,
            }}
          >
            {hit.score_bucket}
          </span>
        )}
      </div>

      <div
        style={{
          display: "flex",
          gap: 6,
          flexWrap: "wrap",
          alignItems: "center",
          marginBottom: 5,
          fontSize: 11,
        }}
      >
        {(human != null || ai != null) && (
          <span style={{ fontWeight: 600 }}>
            {ai != null && (
              <>
                AI <b>{ai}</b>
                {maxScore ? `/${maxScore}` : ""}
                {" → "}
              </>
            )}
            {human != null && (
              <>
                人 <b>{human}</b>
                {maxScore ? `/${maxScore}` : ""}
              </>
            )}
          </span>
        )}
        {typeof dt === "number" && (
          <span style={{ fontWeight: 700, color: dtColor }}>
            Δ {dtSign}
            {dt}
          </span>
        )}
        <SimilarityChips hit={hit} />
        {hit.intent && hit.intent !== "*" && (
          <span
            style={{
              fontSize: 9.5,
              color: "var(--ink-muted)",
              background: "var(--surface-muted)",
              padding: "1px 6px",
              borderRadius: 4,
            }}
          >
            {hit.intent}
          </span>
        )}
        {hit.rater_type && (
          <span
            title={hit.rater_source || ""}
            style={{
              fontSize: 9.5,
              color: "var(--ink-muted)",
              background: "var(--surface-muted)",
              padding: "1px 6px",
              borderRadius: 4,
            }}
          >
            {hit.rater_type}
          </span>
        )}
        {hit.confirmed_at && (
          <span style={{ fontSize: 9.5, color: "var(--ink-subtle)" }}>
            {String(hit.confirmed_at).slice(0, 10)}
          </span>
        )}
      </div>

      {hit.parsed_text && (
        <CardSection
          eyebrow="📄 파싱 원문 (평가항목별)"
          body={stripSeparatorLines(hit.parsed_text)}
          tone="info"
          collapsible
          defaultOpen={false}
          alwaysCollapsible
        />
      )}
      {segmentBody && (
        <CardSection
          eyebrow={segmentLabel}
          body={stripSeparatorLines(segmentBody)}
          tone="default"
          collapsible
          /* 2026-05-08: 검색 원문 truncation 폐지 → 길이 무관 토글 노출. */
          alwaysCollapsible
          defaultOpen={!segmentFallbackUsed}
        />
      )}
      {noteBody && (
        <CardSection
          eyebrow={noteLabel}
          body={stripSeparatorLines(noteBody)}
          tone="default"
          collapsible
          alwaysCollapsible
          defaultOpen={true}
        />
      )}
      {hit.rationale_tags && hit.rationale_tags.length > 0 && (
        <div style={{ marginTop: 5, display: "flex", flexWrap: "wrap", gap: 3 }}>
          {hit.rationale_tags.map((t, i) => (
            <span
              key={i}
              style={{
                fontSize: 9,
                padding: "1px 5px",
                borderRadius: 3,
                background: "#dbeafe",
                color: "#1e3a8a",
              }}
            >
              {t}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function QueryDisplay({
  subAgent,
  persona,
  intent,
}: {
  subAgent?: string | null;
  persona?: string | null;
  intent?: string | null;
}) {
  // 2026-05-08: 두 쿼리가 conceptually 같은 segment_text 인데 백엔드 truncation /
  // 화이트스페이스 / "…" suffix 차이로 string match 가 실패해서 두 박스가 따로
  // 나오는 케이스 회피. 정규화 후 prefix 일치 OR 한쪽이 다른쪽의 ≥80% 포함 여부 체크.
  const _normalize = (s: string | null | undefined): string =>
    String(s || "")
      .replace(/[……]+$/g, "") // 끝 "…" 제거
      .replace(/\.{3,}$/g, "") // 끝 "..." 제거
      .replace(/\s+/g, " ") // 공백 정규화
      .trim();
  const aN = _normalize(subAgent);
  const bN = _normalize(persona);
  let same = false;
  if (aN && bN) {
    if (aN === bN) same = true;
    else {
      // 한쪽이 다른쪽 prefix 면 같다고 봄 (한쪽이 더 truncated)
      const shorter = aN.length <= bN.length ? aN : bN;
      const longer = aN.length <= bN.length ? bN : aN;
      if (shorter.length >= 100 && longer.startsWith(shorter)) same = true;
      // 또는 긴쪽 길이의 80% 이상 첫 부분 일치
      else if (
        shorter.length >= 100 &&
        longer.slice(0, shorter.length) === shorter
      )
        same = true;
    }
  }
  if (!subAgent && !persona && !intent) return null;
  // 동일이면 정규화된(= "…" 절단 표시 제거 후) 길이가 더 긴 버전을 선택 —
  // 즉 실제 정보가 더 많은 쪽. 둘이 같으면 끝에 "…" 가 붙지 않은 깨끗한 쪽 우선.
  const unified = (() => {
    if (!same || !subAgent || !persona) return null;
    const aRaw = String(subAgent);
    const bRaw = String(persona);
    const aHasTrunc = /[……]+$|\.{3,}$/.test(aRaw.trimEnd());
    const bHasTrunc = /[……]+$|\.{3,}$/.test(bRaw.trimEnd());
    if (aN.length !== bN.length) return aN.length >= bN.length ? aRaw : bRaw;
    // 정규화 길이 동일 — 절단 표시 없는 쪽 우선
    if (aHasTrunc && !bHasTrunc) return bRaw;
    if (bHasTrunc && !aHasTrunc) return aRaw;
    // 둘 다 같으면 더 긴 raw (whitespace 포함) 선택
    return aRaw.length >= bRaw.length ? aRaw : bRaw;
  })();
  return (
    <div style={{ marginBottom: 10 }}>
      {intent && (
        <div
          style={{
            fontSize: 10.5,
            color: "var(--ink-muted)",
            marginBottom: 5,
            background: "rgba(0,0,0,0.03)",
            padding: "4px 8px",
            borderRadius: 4,
          }}
        >
          intent: <b style={{ color: "var(--ink)" }}>{intent}</b>
        </div>
      )}
      {same && unified ? (
        <QueryBox label="검색어 · sub-agent + 페르소나 (동일)" body={unified} />
      ) : (
        <>
          {subAgent && <QueryBox label="검색어 · sub-agent fewshot" body={subAgent} />}
          {persona && <QueryBox label="검색어 · 페르소나 RAG" body={persona} />}
        </>
      )}
    </div>
  );
}

function QueryBox({ label, body }: { label: string; body: string }) {
  return (
    <div
      style={{
        marginBottom: 6,
        padding: "8px 10px",
        background: "#fef9e7",
        borderLeft: "3px solid #f59e0b",
        borderRadius: 4,
      }}
    >
      <div
        style={{
          fontSize: 9.5,
          fontWeight: 800,
          color: "#92400e",
          letterSpacing: "0.05em",
          marginBottom: 4,
          textTransform: "uppercase",
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 11,
          color: "#78350f",
          lineHeight: 1.55,
          wordBreak: "break-word",
          whiteSpace: "pre-wrap",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
          maxHeight: 360,
          overflowY: "auto",
          background: "rgba(255,255,255,0.6)",
          padding: "6px 8px",
          borderRadius: 3,
          border: "1px solid rgba(245,158,11,0.2)",
        }}
      >
        {/* 2026-05-08: 사용자 요청 — 원문 truncation 폐지. 스크롤로 처리. */}
        {body}
      </div>
    </div>
  );
}

interface UnifiedRagPanelProps {
  /** sub-agent fewshot_details — RagHitsPanel 이 받던 data. */
  fewshotDetails?: RagFewshotDetail[] | null;
  /** persona_hitl_cases — PersonaHitlSection 이 받던 data 평탄화. */
  personaCases?: PersonaHitlCaseLike[] | null;
  /** 외부 평가항목 탭 동기화 — 한 항목만 노출. */
  filterItemNumber?: number;
  /** 검색어 / intent — sub-agent / persona 별. */
  subAgentQuery?: string | null;
  personaQuery?: string | null;
  intent?: string | null;
  /** 라이브 partial hits 표시 (토론 시작 전). */
  isLive?: boolean;
}

export default function UnifiedRagPanel({
  fewshotDetails,
  personaCases,
  filterItemNumber,
  subAgentQuery,
  personaQuery,
  intent,
  isLive,
}: UnifiedRagPanelProps) {
  const bundle = useMemo(
    () =>
      buildUnifiedRagBundle(fewshotDetails || [], personaCases || [], {
        subAgentQuery,
        personaQuery,
        intent,
      }),
    [fewshotDetails, personaCases, subAgentQuery, personaQuery, intent],
  );

  const { hits, perItem, totalCases, goldenCount, hitlCount, selfMatchCount } = bundle;

  const allItemNums = Object.keys(perItem)
    .map(Number)
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b);
  const itemNums =
    filterItemNumber != null
      ? allItemNums.filter((n) => n === filterItemNumber)
      : allItemNums;
  const multiItem = itemNums.length > 1;

  // 사용자 클릭으로 선택한 멀티 모드 탭 (null = 미선택, 자동 = 첫 항목).
  // filterItemNumber 가 외부에서 강제되거나 multiItem 모드가 아니면 이 state 는 무시되고
  // itemNums[0] 이 derive 됨 — useEffect 로 동기화할 필요 없음.
  const [userPicked, setUserPicked] = useState<number | null>(null);
  // 실제로 표시할 항목 — 외부 강제 (filterItemNumber) > 사용자 클릭 (userPicked) > 첫 항목.
  const activeItem: number | null = (() => {
    if (filterItemNumber != null && itemNums.includes(filterItemNumber)) {
      return filterItemNumber;
    }
    if (userPicked != null && itemNums.includes(userPicked)) {
      return userPicked;
    }
    return itemNums.length > 0 ? itemNums[0] : null;
  })();
  const setActiveItem = setUserPicked;

  if (totalCases === 0) return null;

  // 단일 항목 — 탭 없이 그 항목 hits 만 (filterItemNumber 가 있으면 그쪽).
  // 항목 분류 안 된 hit (item_number 없음) 도 fallback 으로 보여줌.
  const visibleHits: UnifiedRagHit[] = (() => {
    if (multiItem) {
      const ai = activeItem;
      return ai != null ? perItem[ai] || [] : [];
    }
    if (itemNums.length === 1) return perItem[itemNums[0]] || [];
    // item_number 가 전혀 없는 경우 — 전체 hits 리턴.
    return hits;
  })();

  // ★ 사용자 지시 (2026-05-08): bucket 별 그룹 표시 — stratified RAG (full/partial/zero × 2)
  // 컨셉이 시각적으로 드러나도록 score_bucket 우선 정렬 + section header 렌더.
  const bucketOrder: Record<string, number> = {
    full: 0,
    partial: 1,
    zero: 2,
    unevaluable: 3,
  };
  const sourceOrder: Record<UnifiedRagHit["source"], number> = {
    golden_set: 0,
    self_match: 1,
    hitl: 2,
    unknown: 3,
  };
  const sortedHits = [...visibleHits].sort((a, b) => {
    // 1순위: score_bucket (full → partial → zero → unevaluable → unknown)
    const ba = a.score_bucket ? bucketOrder[a.score_bucket] ?? 99 : 99;
    const bb = b.score_bucket ? bucketOrder[b.score_bucket] ?? 99 : 99;
    if (ba !== bb) return ba - bb;
    // 2순위: source (golden_set → self_match → hitl → unknown)
    const sa = sourceOrder[a.source];
    const sb = sourceOrder[b.source];
    if (sa !== sb) return sa - sb;
    // 3순위: cohere rerank score 내림차순 (가장 reranker 가 골랐다고 한 것 우선)
    const ra = a.cohere_rerank_score ?? -Infinity;
    const rb = b.cohere_rerank_score ?? -Infinity;
    if (ra !== rb) return rb - ra;
    // 4순위: cosine 내림차순.
    const ca = a.cosine_score ?? a.persona_knn_score ?? -Infinity;
    const cb = b.cosine_score ?? b.persona_knn_score ?? -Infinity;
    return cb - ca;
  });

  // bucket 별 그룹 — section header 렌더용
  const groupedByBucket: { bucket: string; label: string; color: string; hits: UnifiedRagHit[] }[] = (() => {
    const map = new Map<string, UnifiedRagHit[]>();
    for (const h of sortedHits) {
      const key = h.score_bucket || "unknown";
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(h);
    }
    const meta: Record<string, { label: string; color: string }> = {
      full: { label: "🟢 full · 만점 사례", color: "#10b981" },
      partial: { label: "🟡 partial · 부분점수 사례", color: "#f59e0b" },
      zero: { label: "🔴 zero · 0점 사례", color: "#ef4444" },
      unevaluable: { label: "⚫ unevaluable · 평가 불가", color: "#6b7280" },
      unknown: { label: "❓ unknown · bucket 없음", color: "#9ca3af" },
    };
    return Array.from(map.entries())
      .sort(([a], [b]) => (bucketOrder[a] ?? 99) - (bucketOrder[b] ?? 99))
      .map(([bucket, bhits]) => ({
        bucket,
        label: meta[bucket]?.label || `❓ ${bucket}`,
        color: meta[bucket]?.color || "#9ca3af",
        hits: bhits,
      }));
  })();

  return (
    <div className="drawer-section">
      <div className="drawer-section-title">
        🌟 골든셋 RAG · 총 {totalCases}건
        {goldenCount > 0 && ` · 🌱 골든셋 ${goldenCount}`}
        {hitlCount > 0 && ` · 📚 HITL ${hitlCount}`}
        {selfMatchCount > 0 && ` · 🔁 동일상담 ${selfMatchCount}`}
      </div>

      {isLive && (
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "3px 8px",
            marginBottom: 8,
            fontSize: 11,
            fontWeight: 600,
            background: "rgba(239,68,68,0.08)",
            color: "#b91c1c",
            border: "1px solid rgba(239,68,68,0.3)",
            borderRadius: 999,
            letterSpacing: "-0.01em",
          }}
          title="RAG 검색 직후 실시간으로 도착한 hits — 토론이 끝나면 확정 결과로 갱신됩니다"
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: 999,
              background: "#ef4444",
              animation: "pulse 1.6s ease-in-out infinite",
            }}
            aria-hidden="true"
          />
          라이브 (토론 시작 전)
        </div>
      )}

      <div
        style={{
          fontSize: 10.5,
          color: "var(--ink-muted)",
          marginBottom: 10,
          lineHeight: 1.55,
        }}
      >
        AI 평가 시 + 페르소나 broadcast 시 사용된 사람 검수 정답. 같은 example_id 는
        통합 표시 (🤖 AI 평가 / 🎭 페르소나 배지로 시점 구분). 판사는 RAG 미사용.
      </div>

      {bundle.bothUsed > 0 && (
        <div
          style={{
            fontSize: 10,
            color: "var(--ink-muted)",
            marginBottom: 10,
            display: "flex",
            gap: 8,
            flexWrap: "wrap",
          }}
        >
          <span>
            🤝 양쪽 사용 <b style={{ color: "var(--ink)" }}>{bundle.bothUsed}</b>
          </span>
          {bundle.subAgentOnly > 0 && (
            <span>
              🤖 AI 만 <b style={{ color: "var(--ink)" }}>{bundle.subAgentOnly}</b>
            </span>
          )}
          {bundle.personaOnly > 0 && (
            <span>
              🎭 페르소나 만 <b style={{ color: "var(--ink)" }}>{bundle.personaOnly}</b>
            </span>
          )}
        </div>
      )}

      {multiItem && (
        <div
          style={{
            display: "flex",
            gap: 4,
            flexWrap: "wrap",
            marginBottom: 8,
            borderBottom: "1px solid var(--border)",
            paddingBottom: 6,
          }}
        >
          {itemNums.map((n) => {
            const isActive = n === activeItem;
            const cnt = (perItem[n] || []).length;
            return (
              <button
                key={n}
                type="button"
                onClick={() => setActiveItem(n)}
                style={{
                  fontSize: 11,
                  fontWeight: isActive ? 700 : 500,
                  padding: "3px 8px",
                  borderRadius: 4,
                  border: `1px solid ${isActive ? "var(--accent, #3b82f6)" : "var(--border)"}`,
                  background: isActive
                    ? "var(--accent, #3b82f6)"
                    : "var(--surface)",
                  color: isActive ? "white" : "var(--ink-soft)",
                  cursor: "pointer",
                }}
              >
                #{n} ({cnt})
              </button>
            );
          })}
        </div>
      )}

      <QueryDisplay
        subAgent={subAgentQuery}
        persona={personaQuery}
        intent={intent}
      />

      {sortedHits.length > 0 ? (
        groupedByBucket.map((group) => (
          <div key={group.bucket} style={{ marginBottom: 12 }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                margin: "8px 0 6px 0",
                paddingBottom: 4,
                borderBottom: `2px solid ${group.color}`,
                fontSize: 12,
                fontWeight: 700,
                color: group.color,
              }}
            >
              <span>{group.label}</span>
              <span style={{ fontSize: 10, fontWeight: 500, color: "var(--ink-muted)" }}>
                · {group.hits.length}건
              </span>
            </div>
            {group.hits.map((h) => (
              <UnifiedRagCard hit={h} key={h.example_id} />
            ))}
          </div>
        ))
      ) : (
        <div
          style={{
            fontSize: 11,
            color: "var(--ink-muted)",
            padding: "6px 8px",
          }}
        >
          선택한 항목의 RAG 결과 없음
        </div>
      )}
    </div>
  );
}
