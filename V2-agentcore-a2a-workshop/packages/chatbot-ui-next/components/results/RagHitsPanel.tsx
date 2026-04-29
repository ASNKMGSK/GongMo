// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useState } from "react";

import { STT_MAX_SCORES } from "@/lib/items";
import type {
  RagAgentBundle,
  RagFewshotDetail,
  RagKnowledgeDetail,
  RagPerItemBucket,
  RagQueries,
  RagReasoningBundle,
} from "@/lib/ragHitsAggregator";

/**
 * RagHitsPanel — V2 원본 라인 3740~4060 `RagHitsPerItemSection` 이식.
 *
 * 에이전트 노드 드로어에서 RAG 조회 결과(Golden-set / Reasoning Index / Business Knowledge)를
 * 항목별 탭으로 분리 표시. `aggregateRagHitsByAgent` 결과(bundle) 를 prop 으로 받음.
 */

interface Props {
  hits: RagAgentBundle;
}

function SimilarityBadge({
  value,
  cosine,
  bm25,
  rrf,
  bm25Rank,
  knnRank,
}: {
  value?: number;
  cosine?: number;
  bm25?: number;
  rrf?: number;
  bm25Rank?: number;
  knnRank?: number;
}) {
  const hasCosine =
    cosine !== undefined && cosine !== null && !isNaN(Number(cosine));
  const hasValue =
    value !== undefined && value !== null && !isNaN(Number(value));
  if (!hasCosine && !hasValue) return null;

  // 색상은 cosine(0~1, 의미 유사도) 기준. cosine 없으면 legacy value 사용.
  const colorBasis = hasCosine ? Number(cosine) : Number(value);
  const bg =
    colorBasis >= 0.7 ? "#dcfce7" : colorBasis >= 0.4 ? "#fef3c7" : "#fee2e2";
  const fg =
    colorBasis >= 0.7 ? "#166534" : colorBasis >= 0.4 ? "#92400e" : "#991b1b";

  const tooltip = [
    hasCosine && `cosine ${Number(cosine).toFixed(3)} (의미 유사도 · 0~1)`,
    bm25 != null && `bm25 ${Number(bm25).toFixed(2)} (키워드 매칭)`,
    rrf != null && `rrf ${Number(rrf).toFixed(3)} (BM25+KNN 결합 · max≈0.033)`,
    hasValue && !rrf && `score ${Number(value).toFixed(3)}`,
    bm25Rank != null && `bm25 rank #${bm25Rank}`,
    knnRank != null && `knn rank #${knnRank}`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <span
      title={tooltip || undefined}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 9,
        fontWeight: 700,
        background: bg,
        color: fg,
        padding: "1px 6px",
        borderRadius: 8,
      }}
    >
      {hasCosine && <span>cos {Number(cosine).toFixed(2)}</span>}
      {hasCosine && hasValue && (
        <span style={{ opacity: 0.5, fontWeight: 500 }}>·</span>
      )}
      {hasValue && (
        <span
          style={{
            opacity: hasCosine ? 0.7 : 1,
            fontWeight: hasCosine ? 500 : 700,
          }}
        >
          {hasCosine ? "rrf" : "sim"} {Number(value).toFixed(hasCosine ? 3 : 2)}
        </span>
      )}
    </span>
  );
}

function RagToggle({
  badge,
  title,
  color,
  query,
  children,
}: {
  badge: string;
  title: string;
  color: string;
  query?: string | null;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ marginBottom: 10 }}>
      <div
        onClick={() => setOpen((o) => !o)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          cursor: "pointer",
          padding: "4px 6px",
          borderRadius: 4,
          userSelect: "none",
        }}
      >
        <span
          style={{
            display: "inline-block",
            fontSize: 10,
            transition: "transform 0.15s",
            transform: open ? "rotate(90deg)" : "rotate(0deg)",
          }}
        >
          ▶
        </span>
        <span
          style={{
            fontSize: 9,
            fontWeight: 800,
            padding: "1px 6px",
            borderRadius: 6,
            background: color,
            color: "white",
            letterSpacing: 0.3,
          }}
        >
          {badge}
        </span>
        <span style={{ fontSize: 11, fontWeight: 700, color }}>{title}</span>
      </div>
      {open && (
        <div style={{ marginLeft: 14 }}>
          {query && (
            <div
              style={{
                margin: "0 0 8px 0",
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
                검색어 (RAG retrieval query)
              </div>
              <div
                style={{
                  fontSize: 11,
                  color: "#78350f",
                  lineHeight: 1.55,
                  wordBreak: "break-word",
                  whiteSpace: "pre-wrap",
                  fontFamily:
                    'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                  maxHeight: 180,
                  overflowY: "auto",
                  background: "rgba(255,255,255,0.6)",
                  padding: "6px 8px",
                  borderRadius: 3,
                  border: "1px solid rgba(245,158,11,0.2)",
                }}
              >
                {query}
              </div>
            </div>
          )}
          {children}
        </div>
      )}
    </div>
  );
}

function renderFewshot(d: RagFewshotDetail) {
  const itemNum =
    d.item_number ||
    (d.example_id ? Number(String(d.example_id).split("-")[1]) : undefined) ||
    null;
  const maxScore = itemNum ? STT_MAX_SCORES[itemNum] : null;
  return (
    <div
      key={d.example_id}
      style={{
        marginBottom: 6,
        padding: "6px 8px",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderLeft: "3px solid #10b981",
        borderRadius: 4,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          flexWrap: "wrap",
          marginBottom: 4,
        }}
      >
        <code
          style={{
            fontSize: 10,
            fontWeight: 700,
            color: "#065f46",
            background: "#d1fae5",
            padding: "1px 5px",
            borderRadius: 3,
          }}
        >
          {d.example_id}
        </code>
        {d.score !== undefined && d.score !== null && (
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              color: "#065f46",
            }}
          >
            {d.score}
            {maxScore ? `/${maxScore}` : ""}점
            {d.score_bucket ? ` · ${d.score_bucket}` : ""}
          </span>
        )}
        <SimilarityBadge
          value={d.similarity}
          cosine={d.cosine_score}
          bm25={d.bm25_score}
          rrf={d.rrf_score}
          bm25Rank={d.bm25_rank}
          knnRank={d.knn_rank}
        />
        {itemNum && (
          <span
            style={{
              fontSize: 9,
              color: "var(--ink-muted)",
              background: "var(--surface-muted)",
              padding: "1px 5px",
              borderRadius: 4,
            }}
          >
            item #{itemNum}
          </span>
        )}
        {d.intent && d.intent !== "*" && (
          <span
            style={{
              fontSize: 9,
              color: "var(--ink-muted)",
              background: "var(--surface-muted)",
              padding: "1px 5px",
              borderRadius: 4,
            }}
          >
            {d.intent}
          </span>
        )}
        {d.rater_type && (
          <span
            title={d.rater_source || ""}
            style={{
              fontSize: 9,
              color: "var(--ink-muted)",
              background: "var(--surface-muted)",
              padding: "1px 5px",
              borderRadius: 4,
            }}
          >
            {d.rater_type}
          </span>
        )}
      </div>
      {d.segment_text && (
        <div style={{ fontSize: 10.5, color: "var(--ink-soft)", marginBottom: 3 }}>
          <span style={{ fontWeight: 700, marginRight: 4 }}>발화</span>
          {d.segment_text}
        </div>
      )}
      {d.rationale && (
        <div style={{ fontSize: 10.5, color: "var(--ink-soft)" }}>
          <span style={{ fontWeight: 700, marginRight: 4 }}>근거</span>
          {d.rationale}
        </div>
      )}
      {d.rationale_tags && d.rationale_tags.length > 0 && (
        <div
          style={{ marginTop: 3, display: "flex", flexWrap: "wrap", gap: 3 }}
        >
          {d.rationale_tags.map((t, i) => (
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

function renderReasoning(r: RagReasoningBundle) {
  const maxScore = r.item ? STT_MAX_SCORES[r.item] || 5 : 5;
  const stdevNorm = r.stdev / maxScore;
  const stdevLevel =
    stdevNorm < 0.2 ? "low" : stdevNorm < 0.4 ? "mid" : "high";
  const stdevLabel = {
    low: "낮음 · 합의",
    mid: "보통",
    high: "높음 · 분분",
  }[stdevLevel];
  const stdevColor = {
    low: { bg: "#dcfce7", fg: "#166534", dot: "#10b981" },
    mid: { bg: "#fef3c7", fg: "#92400e", dot: "#f59e0b" },
    high: { bg: "#fecaca", fg: "#991b1b", dot: "#ef4444" },
  }[stdevLevel];
  const sampleWarning = r.sample_size != null && r.sample_size < 3;

  return (
    <div
      key={r.item}
      style={{
        marginBottom: 6,
        padding: "6px 8px",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderLeft: "3px solid #3b82f6",
        borderRadius: 4,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          flexWrap: "wrap",
          marginBottom: 4,
        }}
      >
        <code
          style={{
            fontSize: 10,
            fontWeight: 700,
            color: "#1e3a8a",
            background: "#dbeafe",
            padding: "1px 5px",
            borderRadius: 3,
          }}
        >
          항목 #{r.item}
        </code>
        <span
          title={`stdev ${r.stdev.toFixed(2)} / max ${maxScore}`}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            fontSize: 10,
            fontWeight: 700,
            background: stdevColor.bg,
            color: stdevColor.fg,
            padding: "2px 8px",
            borderRadius: 10,
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: stdevColor.dot,
            }}
          />
          σ̄ {(stdevNorm * 100).toFixed(0)}% · {stdevLabel}
        </span>
        <span
          title={sampleWarning ? "표본 < 3 — confidence 약화 적용됨" : ""}
          style={{
            fontSize: 9,
            color: sampleWarning ? "#991b1b" : "var(--ink-muted)",
            background: sampleWarning ? "#fee2e2" : "var(--surface-muted)",
            padding: "1px 5px",
            borderRadius: 4,
          }}
        >
          n={r.sample_size ?? "?"}
          {sampleWarning && " ⚠"}
        </span>
      </div>
      {r.examples && r.examples.length > 0 ? (
        r.examples.slice(0, 8).map((ex, j) => (
          <div
            key={j}
            style={{
              marginTop: 4,
              padding: "4px 6px",
              background: "var(--surface-muted)",
              borderRadius: 3,
            }}
          >
            <div
              style={{
                display: "flex",
                gap: 5,
                flexWrap: "wrap",
                alignItems: "center",
              }}
            >
              <code
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  color: "#1e3a8a",
                }}
              >
                {ex.example_id}
              </code>
              {ex.score !== undefined && ex.score !== null && (
                <span style={{ fontSize: 10, fontWeight: 700 }}>
                  {ex.score}
                  {maxScore ? `/${maxScore}` : ""}점
                </span>
              )}
              <SimilarityBadge
                value={ex.similarity}
                cosine={ex.cosine_score}
                bm25={ex.bm25_score}
                rrf={ex.rrf_score}
                bm25Rank={ex.bm25_rank}
                knnRank={ex.knn_rank}
              />
              {ex.evaluator_id && (
                <span style={{ fontSize: 9, color: "var(--ink-muted)" }}>
                  {ex.evaluator_id}
                </span>
              )}
            </div>
            {ex.quote_example && (
              <div
                style={{
                  fontSize: 10.5,
                  color: "var(--ink-soft)",
                  marginTop: 3,
                }}
              >
                <span style={{ fontWeight: 700, marginRight: 4 }}>발화</span>
                {ex.quote_example}
              </div>
            )}
            {ex.rationale && (
              <div
                style={{
                  fontSize: 10.5,
                  color: "var(--ink-soft)",
                  marginTop: 2,
                }}
              >
                <span style={{ fontWeight: 700, marginRight: 4 }}>근거</span>
                {ex.rationale}
              </div>
            )}
          </div>
        ))
      ) : r.example_ids && r.example_ids.length > 0 ? (
        <div style={{ fontSize: 10.5, color: "var(--ink-muted)" }}>
          {r.example_ids.join(", ")}
        </div>
      ) : null}
    </div>
  );
}

function renderKnowledge(d: RagKnowledgeDetail) {
  return (
    <div
      key={d.chunk_id}
      style={{
        marginBottom: 6,
        padding: "6px 8px",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderLeft: "3px solid #f59e0b",
        borderRadius: 4,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          flexWrap: "wrap",
          marginBottom: 4,
        }}
      >
        <code
          style={{
            fontSize: 10,
            fontWeight: 700,
            color: "#78350f",
            background: "#fef3c7",
            padding: "1px 5px",
            borderRadius: 3,
          }}
        >
          {d.chunk_id}
        </code>
        <SimilarityBadge value={d.score} />
        {d.source_ref && (
          <span
            style={{
              fontSize: 9,
              color: "var(--ink-muted)",
              background: "var(--surface-muted)",
              padding: "1px 5px",
              borderRadius: 4,
            }}
          >
            {d.source_ref}
          </span>
        )}
      </div>
      {d.text && (
        <div style={{ fontSize: 10.5, color: "var(--ink-soft)" }}>{d.text}</div>
      )}
      {d.tags && d.tags.length > 0 && (
        <div
          style={{ marginTop: 3, display: "flex", flexWrap: "wrap", gap: 3 }}
        >
          {d.tags.map((t, i) => (
            <span
              key={i}
              style={{
                fontSize: 9,
                padding: "1px 5px",
                borderRadius: 3,
                background: "#fef3c7",
                color: "#78350f",
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

function renderBundle(
  bundle: {
    fewshot?: RagFewshotDetail[];
    reasoning?: RagReasoningBundle | RagReasoningBundle[] | null;
    knowledge?: RagKnowledgeDetail[];
  },
  queries: RagQueries | null,
) {
  const fewshot = bundle.fewshot || [];
  const reasoning = Array.isArray(bundle.reasoning)
    ? bundle.reasoning
    : bundle.reasoning
      ? [bundle.reasoning]
      : [];
  const knowledge = bundle.knowledge || [];
  const hasGS = fewshot.length > 0;
  const hasRS = reasoning.length > 0 && reasoning.some((r) => r && r.stdev != null);
  const hasBK = knowledge.length > 0;
  if (!hasGS && !hasRS && !hasBK) {
    return (
      <div
        style={{
          fontSize: 11,
          color: "var(--ink-muted)",
          padding: "6px 8px",
        }}
      >
        RAG 조회 결과 없음
      </div>
    );
  }
  return (
    <>
      {hasGS && (
        <RagToggle
          badge="GS"
          title={`Golden-set (${fewshot.length}건)`}
          color="#065f46"
          query={
            Array.isArray(queries?.fewshot)
              ? queries?.fewshot[0]
              : queries?.fewshot
          }
        >
          {fewshot.map(renderFewshot)}
        </RagToggle>
      )}
      {hasRS && (
        <RagToggle
          badge="RS"
          title={`Reasoning (${reasoning.length}항목)`}
          color="#1e3a8a"
          query={
            Array.isArray(queries?.reasoning)
              ? queries?.reasoning[0]
              : queries?.reasoning
          }
        >
          {reasoning.map(renderReasoning)}
        </RagToggle>
      )}
      {hasBK && (
        <RagToggle
          badge="BK"
          title={`업무지식 RAG (${knowledge.length}건)`}
          color="#78350f"
          query={
            Array.isArray(queries?.knowledge)
              ? queries?.knowledge[0]
              : queries?.knowledge
          }
        >
          {knowledge.map(renderKnowledge)}
        </RagToggle>
      )}
    </>
  );
}

export default function RagHitsPanel({ hits }: Props) {
  const perItemMap: Record<string | number, RagPerItemBucket> =
    hits.perItem || {};
  const itemNums = Object.keys(perItemMap)
    .map(Number)
    .filter((n) => !isNaN(n))
    .sort((a, b) => a - b);
  const multiItem = itemNums.length > 1;

  const [activeItem, setActiveItem] = useState<number | null>(
    itemNums.length > 0 ? itemNums[0] : null,
  );

  if (!hits.hasGS && !hits.hasRS && !hits.hasBK) return null;

  // 단일 항목 — 탭 없이 바로 렌더
  if (!multiItem) {
    const onlyItem = itemNums.length === 1 ? itemNums[0] : null;
    const bundle = onlyItem != null ? perItemMap[onlyItem] : null;
    const fallbackBundle = {
      fewshot: hits.fewshot || [],
      reasoning: (hits.reasoning && hits.reasoning[0]) || null,
      knowledge: hits.knowledge || [],
    };
    const fallbackQueries: RagQueries = {
      fewshot: hits.queries?.fewshot?.[0] || null,
      reasoning: hits.queries?.reasoning?.[0] || null,
      knowledge: hits.queries?.knowledge?.[0] || null,
      intent: hits.queries?.intent || null,
    };
    const useBundle = bundle || fallbackBundle;
    const useQueries = bundle?.queries || fallbackQueries;
    return (
      <div className="drawer-section">
        <div className="drawer-section-title">RAG Hits</div>
        {useQueries && useQueries.intent && (
          <div
            style={{
              fontSize: 10.5,
              color: "var(--ink-muted)",
              marginBottom: 6,
              background: "rgba(0,0,0,0.03)",
              padding: "4px 8px",
              borderRadius: 4,
            }}
          >
            intent: <b style={{ color: "var(--ink)" }}>{useQueries.intent}</b>
          </div>
        )}
        {renderBundle(useBundle, useQueries)}
      </div>
    );
  }

  // 복수 항목 — 탭
  const activeBundle = activeItem != null ? perItemMap[activeItem] : null;
  const activeQueries = activeBundle?.queries || null;
  return (
    <div className="drawer-section">
      <div className="drawer-section-title">
        RAG Hits — 평가 항목별 ({itemNums.length}개)
      </div>
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
          const b = perItemMap[n];
          const isActive = n === activeItem;
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
              #{n}
              {b && (b.hasGS || b.hasRS || b.hasBK) ? "" : " ○"}
            </button>
          );
        })}
      </div>
      {activeBundle ? (
        <>
          {activeQueries?.intent && (
            <div
              style={{
                fontSize: 10.5,
                color: "var(--ink-muted)",
                marginBottom: 6,
                background: "rgba(0,0,0,0.03)",
                padding: "4px 8px",
                borderRadius: 4,
              }}
            >
              intent:{" "}
              <b style={{ color: "var(--ink)" }}>{activeQueries.intent}</b>
            </div>
          )}
          {renderBundle(activeBundle, activeQueries)}
        </>
      ) : (
        <div style={{ fontSize: 11, color: "var(--ink-muted)" }}>
          선택한 항목의 RAG 결과 없음
        </div>
      )}
    </div>
  );
}
