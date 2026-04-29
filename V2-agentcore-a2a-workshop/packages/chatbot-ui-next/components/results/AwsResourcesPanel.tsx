// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useState } from "react";

import { buildTenantPaths } from "@/lib/tenantPaths";

/**
 * AwsResourcesPanel — V2 원본 라인 4663~4897 이식.
 *
 * tenant_config 노드 클릭 시 드로어에 표시.
 *   - `/v2/aws-resources?tenant_id=...` 응답: AOSS · S3 · DynamoDB · API Gateway 연결 상태
 *   - Local tenant paths (rubric, golden_set, business_knowledge, etc.)
 *
 * V3 NodeDrawer 가 소비. serverUrl / tenantId 는 AppStateContext 에서 주입.
 */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AwsData = any;

interface State {
  loading: boolean;
  data: AwsData | null;
  error: string | null;
}

interface Props {
  serverUrl: string;
  tenantId: string;
  /** 3단계 멀티테넌트 (2026-04-24) — 옵셔널, 미지정 시 site 직하 레거시 경로 사용 */
  channel?: string;
  department?: string;
}

export default function AwsResourcesPanel({
  serverUrl,
  tenantId,
  channel,
  department,
}: Props) {
  const [state, setState] = useState<State>({
    loading: false,
    data: null,
    error: null,
  });

  useEffect(() => {
    if (!serverUrl) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setState({ loading: false, data: null, error: null });
      return;
    }
    let cancel = false;
    setState({ loading: true, data: null, error: null });
    const url = `${serverUrl}/v2/aws-resources?tenant_id=${encodeURIComponent(tenantId || "generic")}`;
    fetch(url)
      .then((r) =>
        r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)),
      )
      .then((d) => {
        if (!cancel) setState({ loading: false, data: d, error: null });
      })
      .catch((e: unknown) => {
        if (!cancel)
          setState({
            loading: false,
            data: null,
            error: e instanceof Error ? e.message : String(e),
          });
      });
    return () => {
      cancel = true;
    };
  }, [serverUrl, tenantId]);

  const aws = state.data;
  const connected = aws && aws.connected;

  const statusColor = state.loading
    ? { bg: "#e0e7ff", fg: "#3730a3", dot: "#6366f1", label: "확인 중..." }
    : state.error
      ? { bg: "#fee2e2", fg: "#991b1b", dot: "#ef4444", label: "연결 실패" }
      : connected
        ? { bg: "#dcfce7", fg: "#166534", dot: "#10b981", label: "연결 성공" }
        : { bg: "#fef3c7", fg: "#92400e", dot: "#f59e0b", label: "미연결 / 부분" };

  const paths = buildTenantPaths(tenantId, channel, department);
  const grouped = paths.reduce<Record<string, typeof paths>>((acc, p) => {
    (acc[p.group] ||= []).push(p);
    return acc;
  }, {});

  // 3단계 라벨 — 헤더에 표시할 site / channel / department 조합.
  const scopeLabel = (() => {
    const site = (tenantId || "generic").toUpperCase();
    if (channel && department) return `${site} / ${channel} / ${department}`;
    if (channel) return `${site} / ${channel}`;
    return site;
  })();

  const copy = (text: string) => {
    try {
      navigator.clipboard.writeText(text);
    } catch {
      /* noop */
    }
  };

  const renderResRow = (
    label: string,
    obj: AwsData,
    renderExtra?: (o: AwsData) => React.ReactNode,
  ) => {
    const exists = obj && obj.exists;
    const err = obj && obj.error;
    return (
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 8,
          padding: "6px 10px",
          marginBottom: 4,
          background: "var(--surface-muted)",
          borderRadius: 4,
          border: err ? "1px solid #fecaca" : "1px solid var(--border)",
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--ink)" }}>
            {label}
          </div>
          <code
            style={{
              display: "block",
              fontSize: 10,
              color: err ? "#991b1b" : "#6366f1",
              marginTop: 2,
              wordBreak: "break-all",
            }}
          >
            {obj?.name || obj?.url || obj?.endpoint || "—"}
          </code>
          {renderExtra && renderExtra(obj)}
          {err && (
            <div style={{ fontSize: 10, color: "#991b1b", marginTop: 3 }}>
              ⚠ {err}
            </div>
          )}
        </div>
        <span
          style={{
            fontSize: 9.5,
            fontWeight: 700,
            padding: "2px 7px",
            borderRadius: 10,
            background: exists ? "#dcfce7" : err ? "#fee2e2" : "#f3f4f6",
            color: exists ? "#166534" : err ? "#991b1b" : "#6b7280",
            flexShrink: 0,
          }}
        >
          {exists ? "✓ OK" : err ? "✗ 실패" : "— 미생성"}
        </span>
      </div>
    );
  };

  return (
    <>
      {/* AWS 연결 상태 카드 */}
      <div className="drawer-section">
        <div
          className="drawer-section-title"
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>AWS 연결 상태</span>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              fontSize: 10,
              fontWeight: 700,
              padding: "2px 9px",
              borderRadius: 10,
              background: statusColor.bg,
              color: statusColor.fg,
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: statusColor.dot,
              }}
            />
            {statusColor.label}
          </span>
        </div>

        {state.error && (
          <div
            style={{
              fontSize: 11,
              color: "#991b1b",
              padding: "6px 10px",
              marginBottom: 10,
              background: "#fef2f2",
              borderLeft: "3px solid #ef4444",
              borderRadius: 3,
            }}
          >
            연결 실패 — <b>{state.error}</b>
            <div style={{ fontSize: 10, color: "#7f1d1d", marginTop: 3 }}>
              서버 기동 여부 · AWS 자격증명 · boto3 설치 확인
            </div>
          </div>
        )}

        {aws && (
          <>
            <div
              style={{
                fontSize: 10.5,
                color: "var(--ink-muted)",
                marginBottom: 10,
                padding: "6px 10px",
                background: "rgba(0,0,0,0.03)",
                borderRadius: 4,
              }}
            >
              <div>
                region: <b>{aws.region}</b>
              </div>
              <div>
                qa_rag_backend:{" "}
                <b
                  style={{
                    color: aws.qa_rag_backend === "aoss" ? "#166534" : "#92400e",
                  }}
                >
                  {aws.qa_rag_backend}
                </b>
              </div>
              <div>
                embedding:{" "}
                <b
                  style={{
                    color:
                      aws.qa_rag_embedding === "titan" ? "#166534" : "#92400e",
                  }}
                >
                  {aws.qa_rag_embedding}
                </b>
              </div>
              {aws.qa_rag_backend !== "aoss" && (
                <div
                  style={{ marginTop: 4, fontSize: 10, color: "#92400e" }}
                >
                  💡 QA_RAG_BACKEND=aoss 환경변수 설정 시 AOSS 경로 활성화 (현재 로컬 jaccard)
                </div>
              )}
            </div>

            {aws.aoss && (
              <div style={{ marginBottom: 10 }}>
                <div
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    color: "var(--ink-muted)",
                    textTransform: "uppercase",
                    letterSpacing: "0.08em",
                    marginBottom: 5,
                  }}
                >
                  OpenSearch Serverless (AOSS)
                </div>
                <div
                  style={{
                    fontSize: 10.5,
                    color: "var(--ink-muted)",
                    marginBottom: 4,
                    padding: "4px 8px",
                    background: "rgba(0,0,0,0.02)",
                    borderRadius: 3,
                  }}
                >
                  <div>
                    collection:{" "}
                    <code style={{ color: "#6366f1" }}>
                      {aws.aoss.collection}
                    </code>
                  </div>
                  <code
                    style={{
                      display: "block",
                      fontSize: 10,
                      color: "#6366f1",
                      marginTop: 2,
                      wordBreak: "break-all",
                    }}
                  >
                    {aws.aoss.endpoint}
                  </code>
                </div>
                {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
                {(aws.aoss.indexes || []).map((idx: any) => (
                  <div
                    key={idx.name}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: "5px 10px",
                      marginBottom: 3,
                      background: "var(--surface-muted)",
                      borderRadius: 4,
                      border: idx.error
                        ? "1px solid #fecaca"
                        : "1px solid var(--border)",
                    }}
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <code
                        style={{
                          fontSize: 11,
                          color: "var(--ink)",
                          fontWeight: 600,
                        }}
                      >
                        {idx.name}
                      </code>
                      {idx.exists && (
                        <span
                          style={{
                            fontSize: 10,
                            color: "var(--ink-muted)",
                            marginLeft: 8,
                          }}
                        >
                          docs: <b>{idx.doc_count}</b> (tenant={tenantId || "generic"})
                        </span>
                      )}
                      {idx.error && (
                        <div
                          style={{
                            fontSize: 10,
                            color: "#991b1b",
                            marginTop: 2,
                          }}
                        >
                          ⚠ {idx.error}
                        </div>
                      )}
                    </div>
                    <span
                      style={{
                        fontSize: 9,
                        fontWeight: 700,
                        padding: "2px 7px",
                        borderRadius: 8,
                        background: idx.exists ? "#dcfce7" : "#f3f4f6",
                        color: idx.exists ? "#166534" : "#6b7280",
                      }}
                    >
                      {idx.exists ? "✓ 인덱스" : "— 미생성"}
                    </span>
                  </div>
                ))}
              </div>
            )}

            <div style={{ marginBottom: 10 }}>
              <div
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  color: "var(--ink-muted)",
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                  marginBottom: 5,
                }}
              >
                기타 리소스
              </div>
              {aws.s3 && renderResRow("S3 Bucket", aws.s3)}
              {aws.dynamodb &&
                renderResRow("DynamoDB Table", aws.dynamodb, (o) =>
                  o.exists ? (
                    <div
                      style={{
                        fontSize: 10,
                        color: "var(--ink-muted)",
                        marginTop: 2,
                      }}
                    >
                      status: <b>{o.status}</b> · item_count: <b>{o.item_count}</b>
                    </div>
                  ) : null,
                )}
              {aws.api_gateway && aws.api_gateway.url && (
                <div
                  style={{
                    padding: "6px 10px",
                    marginBottom: 4,
                    background: "var(--surface-muted)",
                    borderRadius: 4,
                    border: "1px solid var(--border)",
                  }}
                >
                  <div style={{ fontSize: 11, fontWeight: 700 }}>API Gateway</div>
                  <code
                    style={{
                      display: "block",
                      fontSize: 10,
                      color: "#6366f1",
                      marginTop: 2,
                      wordBreak: "break-all",
                    }}
                  >
                    {aws.api_gateway.url}
                  </code>
                </div>
              )}
            </div>

            {aws.errors && aws.errors.length > 0 && (
              <div
                style={{
                  fontSize: 10.5,
                  color: "#92400e",
                  padding: "6px 10px",
                  background: "#fef3c7",
                  borderLeft: "3px solid #f59e0b",
                  borderRadius: 3,
                }}
              >
                <b>⚠ 경고:</b> {aws.errors.join(" · ")}
              </div>
            )}
          </>
        )}
      </div>

      {/* Tenant Resources / local paths */}
      <div className="drawer-section">
        <div
          className="drawer-section-title"
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>Tenant Resources</span>
          <span
            style={{
              fontSize: 10,
              fontWeight: 800,
              padding: "2px 8px",
              borderRadius: 10,
              background: "#1e293b",
              color: "#fef3c7",
              letterSpacing: "0.05em",
            }}
          >
            {scopeLabel}
          </span>
        </div>
        <div
          style={{
            fontSize: 11,
            color: "var(--ink-muted)",
            marginBottom: 10,
            padding: "6px 10px",
            background: "rgba(59,130,246,0.06)",
            borderLeft: "3px solid var(--accent, #3b82f6)",
            borderRadius: 3,
          }}
        >
          {channel && department ? (
            <>
              site <b>{tenantId || "generic"}</b> · channel <b>{channel}</b> · department{" "}
              <b>{department}</b> 의 3단계 멀티테넌트 자원. 가장 구체 경로가 우선 적용되며,
              파일이 없는 경우 <code>채널 직하 → 사이트 직하 → generic</code> 순으로 fallback.
            </>
          ) : channel ? (
            <>
              site <b>{tenantId || "generic"}</b> · channel <b>{channel}</b> (department 미지정).
              채널 공통 자원 우선, fallback 체인 적용.
            </>
          ) : (
            <>
              tenant <b>{tenantId || "generic"}</b> 의 site-level 자원 (channel/department 미지정).
              기존 레거시 경로 사용.
            </>
          )}
        </div>
        {Object.entries(grouped).map(([group, list]) => (
          <div key={group} style={{ marginBottom: 14 }}>
            <div
              style={{
                fontSize: 10,
                fontWeight: 700,
                color: "var(--ink-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.08em",
                marginBottom: 6,
              }}
            >
              {group}
            </div>
            {list.map((p) => (
              <div
                key={p.path}
                style={{
                  marginBottom: 8,
                  padding: "8px 10px",
                  background: "var(--surface-muted)",
                  borderRadius: 4,
                  border: "1px solid var(--border)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                    gap: 8,
                  }}
                >
                  <div
                    style={{
                      fontSize: 12,
                      fontWeight: 700,
                      color: "var(--ink)",
                    }}
                  >
                    {p.label}
                  </div>
                  <button
                    type="button"
                    onClick={() => copy(p.path)}
                    title="경로 복사"
                    style={{
                      fontSize: 10,
                      padding: "2px 8px",
                      borderRadius: 4,
                      border: "1px solid var(--border)",
                      background: "var(--surface)",
                      color: "var(--accent, #3b82f6)",
                      cursor: "pointer",
                      flexShrink: 0,
                    }}
                  >
                    📋 복사
                  </button>
                </div>
                <code
                  style={{
                    display: "block",
                    fontSize: 10.5,
                    color: "#6366f1",
                    marginTop: 4,
                    wordBreak: "break-all",
                    fontFamily:
                      "ui-monospace, SFMono-Regular, monospace",
                  }}
                >
                  {p.path}
                </code>
                <div
                  style={{
                    fontSize: 10.5,
                    color: "var(--ink-muted)",
                    marginTop: 4,
                    lineHeight: 1.45,
                  }}
                >
                  {p.desc}
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
