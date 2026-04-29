// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import DOMPurify from "dompurify";
import { marked } from "marked";
import React, { useCallback, useMemo, useRef, useState } from "react";

import { useAppState } from "@/lib/AppStateContext";
import {
  parseManualXlsx,
  parseSheetFromWorkbook,
  buildManualPreview,
  type ManualSheet,
  type SheetPickerInfo,
} from "@/lib/manualEvalParser";
import {
  computeManualComparison,
  type ManualComparison,
} from "@/lib/manualEvalMapper";
import { downloadManualComparisonXlsx } from "@/lib/manualEvalExport";
import {
  MODEL_GROUPS,
  MODEL_LEFT_DEFAULT,
  MODEL_RIGHT_DEFAULT,
  labelFor,
  modelLabelFor,
  resolveModelSelection,
} from "@/lib/models";
import { useToast } from "@/lib/toast";
import { usePipelineRun } from "@/lib/usePipelineRun";
import type { EvaluationResult, Report } from "@/lib/types";

/* ─────────────────────────────────────────────────────────────
   ComparePanel — Task #4 (Dev4)
   V2 원본: qa_pipeline_reactflow.html:9568 (CompareTab)
   좌/우 두 컬럼 모델 평가 + Sonnet Judge 분석 + 수동 평가표 비교
   ───────────────────────────────────────────────────────────── */

export function ComparePanel() {
  const { state } = useAppState();
  const toast = useToast();

  const [leftModel, setLeftModel] = useState(MODEL_LEFT_DEFAULT);
  const [rightModel, setRightModel] = useState(MODEL_RIGHT_DEFAULT);
  const leftSel = useMemo(() => resolveModelSelection(leftModel), [leftModel]);
  const rightSel = useMemo(() => resolveModelSelection(rightModel), [rightModel]);

  const leftRun = usePipelineRun({ column: "left", serverUrl: state.serverUrl });
  const rightRun = usePipelineRun({ column: "right", serverUrl: state.serverUrl });

  const [subTabLeft, setSubTabLeft] = useState<"results" | "logs" | "traces" | "raw">("results");
  const [subTabRight, setSubTabRight] = useState<"results" | "logs" | "traces" | "raw">("results");

  const [judgeLoading, setJudgeLoading] = useState(false);
  const [judgeResult, setJudgeResult] = useState<string | null>(null);

  // 수동 평가표 비교 (V2: manualEvalText + manualStructured + manualComparison)
  const [manualEvalText, setManualEvalText] = useState("");
  const [manualFileName, setManualFileName] = useState("");
  const [manualError, setManualError] = useState("");
  const [manualLoading, setManualLoading] = useState(false);
  const [manualComparison, setManualComparison] = useState<ManualComparisonData | null>(null);
  // Dev6 — xlsx 첨부 시 결정적 파싱된 ManualSheet 보관
  const [manualStructured, setManualStructured] = useState<ManualSheet | null>(null);
  const [manualSheetPicker, setManualSheetPicker] = useState<SheetPickerInfo | null>(null);
  // Dev6 — 결정적 비교 (xlsx 기반) 결과 보관. V3 clientStructured comparison.
  const [structuredComparisons, setStructuredComparisons] = useState<ManualComparison[]>([]);
  const manualFileRef = useRef<HTMLInputElement | null>(null);

  const transcript = state.transcript;
  const transcriptReady = !!transcript.trim();

  const runLeft = useCallback(async () => {
    if (!transcriptReady) return;
    setJudgeResult(null);
    await leftRun.start({
      transcript,
      llmBackend: leftSel.backend,
      bedrockModelId: leftSel.bedrock_model_id,
      tenantId: state.tenantId,
    });
  }, [leftRun, leftSel, transcript, transcriptReady, state.tenantId]);

  const runRight = useCallback(async () => {
    if (!transcriptReady) return;
    setJudgeResult(null);
    await rightRun.start({
      transcript,
      llmBackend: rightSel.backend,
      bedrockModelId: rightSel.bedrock_model_id,
      tenantId: state.tenantId,
    });
  }, [rightRun, rightSel, transcript, transcriptReady, state.tenantId]);

  const runBoth = useCallback(async () => {
    if (!transcriptReady) return;
    setJudgeResult(null);
    await Promise.all([
      leftRun.start({
        transcript,
        llmBackend: leftSel.backend,
        bedrockModelId: leftSel.bedrock_model_id,
        tenantId: state.tenantId,
      }),
      rightRun.start({
        transcript,
        llmBackend: rightSel.backend,
        bedrockModelId: rightSel.bedrock_model_id,
        tenantId: state.tenantId,
      }),
    ]);
  }, [leftRun, rightRun, leftSel, rightSel, transcript, transcriptReady, state.tenantId]);

  const abortBoth = useCallback(() => {
    leftRun.abort();
    rightRun.abort();
  }, [leftRun, rightRun]);

  const leftLabel = labelFor(leftSel);
  const rightLabel = labelFor(rightSel);

  // 클라이언트 사이드 Judge 분석 — /analyze-compare 백엔드 미구현 상태라 Markdown 리포트 합성.
  const runJudge = useCallback(async () => {
    if (!leftRun.result || !rightRun.result || judgeLoading) return;
    setJudgeLoading(true);
    setJudgeResult(null);
    try {
      const md = buildClientJudgeMarkdown(
        leftRun.result,
        rightRun.result,
        leftLabel,
        rightLabel,
      );
      setJudgeResult(md);
      toast.success("Judge 분석 완료 (클라이언트 사이드)");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setJudgeResult(`분석 실패: ${msg}`);
      toast.error("Judge 분석 실패", { description: msg });
    } finally {
      setJudgeLoading(false);
    }
  }, [leftRun.result, rightRun.result, leftLabel, rightLabel, judgeLoading, toast]);

  const matrix = useMemo(
    () => buildComparisonMatrix(leftRun.result, rightRun.result, leftLabel, rightLabel),
    [leftRun.result, rightRun.result, leftLabel, rightLabel],
  );

  const hasAnyResult = !!(leftRun.result || rightRun.result);

  const onManualFileSelect = useCallback(
    async (ev: React.ChangeEvent<HTMLInputElement>) => {
      const file = ev.target.files?.[0];
      ev.target.value = "";
      if (!file) return;
      setManualError("");
      setManualFileName(file.name);
      setManualStructured(null);
      setManualSheetPicker(null);
      const lower = file.name.toLowerCase();
      try {
        if (lower.endsWith(".xlsx") || lower.endsWith(".xls")) {
          // Dev6 — parseManualXlsx 결정적 파싱. 시트 매칭 성공 시 manualStructured 세팅,
          // 실패 시 picker UI 를 띄워 사용자가 탭 선택하도록.
          const buf = await file.arrayBuffer();
          const outcome = parseManualXlsx(buf, {
            fileName: file.name,
            transcript,
          });
          if (outcome.kind === "parsed") {
            setManualStructured(outcome.sheet);
            setManualEvalText(buildManualPreview(outcome.sheet));
          } else {
            setManualSheetPicker(outcome.info);
            setManualEvalText("");
          }
        } else if (lower.endsWith(".json")) {
          const text = await file.text();
          try {
            const obj = JSON.parse(text);
            setManualEvalText(JSON.stringify(obj, null, 2));
          } catch {
            setManualEvalText(text);
          }
        } else {
          // .csv / .txt / 기타 — UTF-8 fallback → EUC-KR
          let text: string;
          try {
            text = await file.text();
            if (text.includes("�")) throw new Error("utf8-fallback");
          } catch {
            const buf = await file.arrayBuffer();
            try {
              text = new TextDecoder("euc-kr").decode(buf);
            } catch {
              text = new TextDecoder("utf-8", { fatal: false }).decode(buf);
            }
          }
          setManualEvalText(text);
        }
      } catch (err) {
        setManualError(`파일 읽기 실패: ${(err as Error).message || String(err)}`);
      }
    },
    [],
  );

  const clearManualFile = useCallback(() => {
    setManualFileName("");
    setManualEvalText("");
    setManualError("");
    setManualComparison(null);
    setManualStructured(null);
    setManualSheetPicker(null);
    setStructuredComparisons([]);
  }, []);

  const handleManualSheetPick = useCallback(
    (sheetName: string) => {
      if (!manualSheetPicker || !sheetName) return;
      try {
        const sheet = parseSheetFromWorkbook(manualSheetPicker.workbook, sheetName);
        setManualStructured(sheet);
        setManualEvalText(buildManualPreview(sheet));
        setManualSheetPicker(null);
        setManualError("");
      } catch (err) {
        setManualError(`탭 파싱 실패: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [manualSheetPicker],
  );

  // 클라이언트 사이드 수동 평가표 비교 — /analyze-manual-compare 백엔드 미구현.
  // 사용자가 붙여넣은 텍스트에서 "항목번호 점수" 라인을 추출 → 모델 결과와 비교.
  const runManualCompare = useCallback(async () => {
    if (manualLoading) return;
    if (!manualEvalText.trim()) {
      toast.warn("수동 평가표 내용이 비어있습니다");
      return;
    }
    const models: Array<{ name: string; result: EvaluationResult }> = [];
    if (leftRun.result) models.push({ name: leftLabel, result: leftRun.result });
    if (rightRun.result) models.push({ name: rightLabel, result: rightRun.result });
    if (models.length === 0) {
      toast.warn("모델 평가를 먼저 실행하세요");
      return;
    }
    setManualLoading(true);
    setManualComparison(null);
    setManualError("");
    try {
      const manualRows = parseManualEvalText(manualEvalText);
      if (manualRows.length === 0) {
        throw new Error(
          "수동 평가표에서 항목 점수를 추출하지 못했습니다. '#번호 점수' 또는 'No\\t점수' 형식을 포함시켜 주세요.",
        );
      }
      const data = buildClientManualComparison(manualRows, models);
      setManualComparison(data);
      toast.success("수동 평가표 비교 완료 (클라이언트 사이드)");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setManualError(`분석 실패: ${msg}`);
      toast.error("수동 평가표 비교 실패", { description: msg });
    } finally {
      setManualLoading(false);
    }
  }, [manualLoading, manualEvalText, leftRun.result, rightRun.result, leftLabel, rightLabel, toast]);

  const bothRunning = leftRun.isRunning && rightRun.isRunning;
  const anyRunning = leftRun.isRunning || rightRun.isRunning;

  return (
    <div className="flex flex-col gap-5">
      {/* 헤더 — Run 버튼 + 배지 */}
      <div className="card card-padded">
        <div className="flex flex-wrap items-center gap-2.5">
          <button
            className="btn-primary"
            disabled={!transcriptReady || bothRunning}
            onClick={runBoth}
            data-testid="compare-run-both"
          >
            {bothRunning ? "Running..." : "▶ Run Both"}
          </button>
          <button
            className="btn-secondary btn-sm"
            disabled={!transcriptReady || leftRun.isRunning}
            onClick={runLeft}
            data-testid="compare-run-left"
          >
            {leftRun.isRunning ? "Running..." : `▶ ${leftLabel}`}
          </button>
          <button
            className="btn-secondary btn-sm"
            disabled={!transcriptReady || rightRun.isRunning}
            onClick={runRight}
            data-testid="compare-run-right"
          >
            {rightRun.isRunning ? "Running..." : `▶ ${rightLabel}`}
          </button>
          {anyRunning && (
            <button
              className="btn-danger btn-sm"
              onClick={abortBoth}
              data-testid="compare-abort-both"
            >
              ■ Abort
            </button>
          )}
          <div className="ml-auto flex items-center gap-2 text-[11px]">
            <StatusBadge label={leftLabel} run={leftRun} column="left" />
            <StatusBadge label={rightLabel} run={rightRun} column="right" />
          </div>
        </div>

        {!transcriptReady && (
          <div className="mt-3 rounded-[var(--radius-sm)] border border-[var(--warn-border)] bg-[var(--warn-bg)] px-3 py-2 text-[12px] text-[var(--warn)]">
            상담 전사가 비어있습니다. Evaluate 탭에서 transcript 를 붙여넣거나 파일을 첨부한 뒤 비교를 실행하세요.
          </div>
        )}
      </div>

      {/* 좌/우 컬럼 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ColumnPane
          column="left"
          modelValue={leftModel}
          onModelChange={setLeftModel}
          run={leftRun}
          label={leftLabel}
          backend={leftSel.backend}
          subTab={subTabLeft}
          onSubTabChange={setSubTabLeft}
        />
        <ColumnPane
          column="right"
          modelValue={rightModel}
          onModelChange={setRightModel}
          run={rightRun}
          label={rightLabel}
          backend={rightSel.backend}
          subTab={subTabRight}
          onSubTabChange={setSubTabRight}
        />
      </div>

      {/* 매트릭스 테이블 */}
      {matrix && (
        <div className="card card-padded">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-[15px] font-semibold">비교 매트릭스 — 항목별 점수</h3>
            <div className="text-[12px] text-[var(--ink-muted)]">
              총점 차이:{" "}
              <b
                className={
                  matrix.diff > 0
                    ? "text-[var(--success)]"
                    : matrix.diff < 0
                      ? "text-[var(--danger)]"
                      : ""
                }
              >
                {matrix.diff > 0 ? "+" : ""}
                {matrix.diff}
              </b>{" "}
              (L: {matrix.leftTotal} / R: {matrix.rightTotal})
            </div>
          </div>
          <ComparisonMatrixTable matrix={matrix} />
        </div>
      )}

      {/* Judge 분석 */}
      <div className="card card-padded">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex-1">
            <div className="font-semibold text-[15px]">Sonnet 4.6 Judge Analysis</div>
            <div className="text-[12px] text-[var(--ink-muted)] mt-0.5">
              {leftRun.isRunning || rightRun.isRunning
                ? "평가 진행 중..."
                : leftRun.result && rightRun.result
                  ? "양쪽 결과 준비 완료 — 분석을 시작하세요"
                  : "양쪽 모델 평가가 완료되면 비교 분석할 수 있습니다"}
            </div>
          </div>
          <button
            className="btn-primary btn-sm"
            disabled={
              judgeLoading ||
              leftRun.isRunning ||
              rightRun.isRunning ||
              !leftRun.result ||
              !rightRun.result
            }
            onClick={runJudge}
          >
            {judgeLoading ? "분석 중..." : "Judge 분석 실행"}
          </button>
        </div>
        {judgeResult && (
          <div
            className="mt-4 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface-muted)] p-4 text-[13px] leading-relaxed"
            dangerouslySetInnerHTML={{
              __html: DOMPurify.sanitize(marked.parse(judgeResult) as string),
            }}
          />
        )}
      </div>

      {/* 수동 평가표 비교 — V2: CompareTab manualComparison 섹션 */}
      <div className="card card-padded">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <div>
            <div className="font-semibold text-[15px]">수동 평가표 비교</div>
            <div className="text-[12px] text-[var(--ink-muted)] mt-0.5">
              {manualLoading
                ? "분석 중..."
                : leftRun.isRunning || rightRun.isRunning
                  ? "평가 진행 중..."
                  : hasAnyResult
                    ? `비교 대상: ${[leftRun.result && leftLabel, rightRun.result && rightLabel]
                        .filter(Boolean)
                        .join(" + ")}`
                    : "모델 평가를 먼저 실행하세요"}
            </div>
          </div>
          <button
            className="btn-primary btn-sm"
            disabled={
              manualLoading ||
              !manualEvalText.trim() ||
              !hasAnyResult ||
              leftRun.isRunning ||
              rightRun.isRunning
            }
            onClick={runManualCompare}
          >
            {manualLoading ? "분석 중..." : "비교 분석 실행"}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2 mb-2">
          <input
            ref={manualFileRef}
            type="file"
            accept=".csv,.txt,.json,.xlsx,.xls,text/csv,text/plain,application/json"
            className="hidden"
            onChange={onManualFileSelect}
          />
          <button
            className="btn-secondary btn-sm"
            onClick={() => manualFileRef.current?.click()}
            type="button"
          >
            📎 파일 첨부
          </button>
          {manualFileName && (
            <span className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] bg-[var(--accent-bg)] px-2.5 py-1 text-[11px] text-[var(--accent-strong)]">
              📄 {manualFileName}
              <button
                className="text-[var(--accent)] hover:text-[var(--ink)]"
                onClick={clearManualFile}
                aria-label="첨부 해제"
              >
                ✕
              </button>
            </span>
          )}
          <span className="text-[11px] text-[var(--ink-subtle)]">
            또는 아래에 직접 붙여넣기 (CSV · JSON · 표 · 평문 자유)
          </span>
        </div>

        <textarea
          value={manualEvalText}
          onChange={(e) => {
            setManualEvalText(e.target.value);
            if (manualFileName) setManualFileName("");
          }}
          placeholder="QA 프로그램에서 추출한 배점·근거를 여기에 붙여넣으세요 (CSV / JSON / 표 / 평문 자유 포맷)"
          className="input-field w-full font-mono text-[12px] min-h-[100px] max-h-[300px] resize-y"
        />

        {manualError && (
          <div className="mt-3 rounded-[var(--radius-sm)] border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-[12px] text-[var(--danger)]">
            {manualError}
          </div>
        )}

        {manualComparison && <ManualComparisonTable data={manualComparison} />}
      </div>
    </div>
  );
}

/* ── 서브 컴포넌트 ─────────────────────────────────────────── */

function StatusBadge({
  label,
  run,
  column,
}: {
  label: string;
  run: ReturnType<typeof usePipelineRun>;
  column: string;
}) {
  let cls = "badge badge-neutral";
  let text = "idle";
  if (run.isRunning) {
    cls = "badge badge-accent";
    text = "running";
  } else if (run.errorAlert) {
    cls = "badge badge-danger";
    text = "error";
  } else if (run.result) {
    cls = "badge badge-success";
    text = "done";
  }
  return (
    <span
      className={cls}
      title={run.errorAlert?.message || ""}
      data-testid={`compare-badge-${column}`}
    >
      <b className="mr-1">{label}</b>
      {text} · {run.elapsed.toFixed(1)}s
    </span>
  );
}

function ColumnPane({
  column,
  modelValue,
  onModelChange,
  run,
  label,
  backend,
  subTab,
  onSubTabChange,
}: {
  column: "left" | "right";
  modelValue: string;
  onModelChange: (v: string) => void;
  run: ReturnType<typeof usePipelineRun>;
  label: string;
  backend: "bedrock" | "sagemaker";
  subTab: "results" | "logs" | "traces" | "raw";
  onSubTabChange: (v: "results" | "logs" | "traces" | "raw") => void;
}) {
  const suffix = backend === "sagemaker" ? "SageMaker" : "Bedrock";
  return (
    <div
      className="card card-padded"
      data-column={column}
      data-backend={backend}
      data-testid={`compare-col-${column}`}
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="font-semibold text-[14px]">
          {label}{" "}
          <span className="text-[11px] font-normal text-[var(--ink-muted)]">({suffix})</span>
        </div>
        <select
          className="input-field input-sm ml-auto max-w-[220px]"
          value={modelValue}
          onChange={(e) => onModelChange(e.target.value)}
          disabled={run.isRunning}
          data-testid={`compare-model-select-${column}`}
        >
          {Object.entries(MODEL_GROUPS).map(([gname, opts]) => (
            <optgroup key={gname} label={gname}>
              {opts.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <span className="text-[11px] text-[var(--ink-muted)]">
          {run.elapsed.toFixed(1)}s
          {run.isRunning ? " · 실행 중" : ""}
        </span>
      </div>

      {/* 서브탭 */}
      <div className="flex gap-1 border-b border-[var(--border)] mb-3">
        {(["results", "logs", "traces", "raw"] as const).map((k) => (
          <button
            key={k}
            className={`px-3 py-1.5 text-[12px] font-medium transition ${
              subTab === k
                ? "text-[var(--accent)] border-b-2 border-[var(--accent)] -mb-px"
                : "text-[var(--ink-muted)] hover:text-[var(--ink)]"
            }`}
            onClick={() => onSubTabChange(k)}
            data-testid={`compare-subtab-${column}-${k}`}
          >
            {k === "results" ? "평가 결과" : k === "logs" ? "로그" : k === "traces" ? "트레이스" : "Raw"}
            {k === "logs" && run.logs.length > 0 && (
              <span className="ml-1.5 inline-flex min-w-[18px] items-center justify-center rounded-full bg-[var(--surface-muted)] px-1 text-[10px]">
                {run.logs.length}
              </span>
            )}
            {k === "traces" && run.traces.length > 0 && (
              <span className="ml-1.5 inline-flex min-w-[18px] items-center justify-center rounded-full bg-[var(--surface-muted)] px-1 text-[10px]">
                {run.traces.length}
              </span>
            )}
            {k === "raw" && run.rawLogs.length > 0 && (
              <span className="ml-1.5 inline-flex min-w-[18px] items-center justify-center rounded-full bg-[var(--surface-muted)] px-1 text-[10px]">
                {run.rawLogs.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {run.errorAlert && (
        <div className="mb-3 rounded-[var(--radius-sm)] border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-[12px] text-[var(--danger)]">
          {run.errorAlert.message}
        </div>
      )}

      <div className="max-h-[560px] overflow-auto">
        {subTab === "results" && <ResultsView result={run.result} streamingItems={run.streamingItems} />}
        {subTab === "logs" && <LogsView logs={run.logs} />}
        {subTab === "traces" && <TracesView traces={run.traces} />}
        {subTab === "raw" && <RawLogsView rawLogs={run.rawLogs} />}
      </div>
    </div>
  );
}

function ResultsView({
  result,
  streamingItems,
}: {
  result: EvaluationResult | null;
  streamingItems: ReturnType<typeof usePipelineRun>["streamingItems"];
}) {
  const report: Report | null | undefined = result?.report;
  if (!result && streamingItems.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-title">결과 없음</div>
        <div className="empty-state-desc">Run 버튼을 눌러 평가를 시작하세요.</div>
      </div>
    );
  }
  const categories = report?.evaluation?.categories || [];
  return (
    <div className="space-y-3">
      {report?.final_score && (
        <div className="flex flex-wrap items-baseline gap-3 rounded-[var(--radius)] bg-[var(--accent-bg)] p-3">
          <div className="text-[22px] font-bold text-[var(--accent-strong)]">
            {report.final_score.after_overrides ?? report.final_score.raw_total ?? "—"}
          </div>
          <div className="text-[12px] text-[var(--ink-muted)]">
            grade: <b>{report.final_score.grade || "—"}</b>
            {report.final_score.raw_total != null &&
              report.final_score.after_overrides != null &&
              report.final_score.raw_total !== report.final_score.after_overrides && (
                <span className="ml-2 text-[var(--ink-subtle)]">
                  (raw {report.final_score.raw_total})
                </span>
              )}
          </div>
        </div>
      )}
      {categories.length === 0 && streamingItems.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--ink-muted)]">
            스트리밍 중 ({streamingItems.length}/18)
          </div>
          {streamingItems.map((s) => (
            <div key={s.item_number} className="flex items-center justify-between rounded-[var(--radius-sm)] bg-[var(--surface-muted)] px-3 py-1.5 text-[12px]">
              <span>
                #{s.item_number} {s.label || ""}
              </span>
              <span className="font-mono font-bold">{s.score ?? "—"}</span>
            </div>
          ))}
        </div>
      )}
      {categories.map((cat) => (
        <div key={cat.category} className="rounded-[var(--radius)] border border-[var(--border)] p-3">
          <div className="mb-2 text-[12px] font-semibold text-[var(--ink-soft)]">{cat.category}</div>
          <div className="space-y-1.5">
            {cat.items.map((item) => (
              <div
                key={item.item_number}
                className="flex items-start justify-between gap-2 text-[12px]"
              >
                <div className="flex-1">
                  <span className="text-[var(--ink-muted)]">#{item.item_number}</span>{" "}
                  <span className="font-medium">{item.item || item.item_name}</span>
                  {item.judgment && (
                    <div className="mt-0.5 text-[11px] text-[var(--ink-muted)] line-clamp-2">
                      {item.judgment}
                    </div>
                  )}
                </div>
                <span className="font-mono font-bold whitespace-nowrap">
                  {item.score}/{item.max_score}
                </span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function LogsView({ logs }: { logs: ReturnType<typeof usePipelineRun>["logs"] }) {
  if (logs.length === 0) {
    return <div className="text-[12px] text-[var(--ink-subtle)]">로그 없음</div>;
  }
  return (
    <div className="space-y-0.5 font-mono text-[11.5px]">
      {logs.map((l, i) => (
        <div
          key={i}
          className={
            l.type === "success"
              ? "text-[var(--success)]"
              : l.type === "warn"
                ? "text-[var(--warn)]"
                : l.type === "error"
                  ? "text-[var(--danger)]"
                  : "text-[var(--ink-soft)]"
          }
        >
          <span className="text-[var(--ink-subtle)]">[{l.time}]</span> {l.msg}
        </div>
      ))}
    </div>
  );
}

function TracesView({ traces }: { traces: ReturnType<typeof usePipelineRun>["traces"] }) {
  if (traces.length === 0) {
    return <div className="text-[12px] text-[var(--ink-subtle)]">트레이스 없음</div>;
  }
  return (
    <div className="space-y-1 text-[11.5px]">
      {traces.map((t) => (
        <div key={t.id} className="rounded-[var(--radius-sm)] bg-[var(--surface-muted)] px-2.5 py-1.5">
          <span className="text-[var(--ink-subtle)]">[{t.time}]</span>{" "}
          <b>{t.label || t.node}</b> · {t.status}
          {t.elapsed != null && (
            <span className="ml-1 text-[var(--ink-muted)]">({t.elapsed.toFixed(2)}s)</span>
          )}
        </div>
      ))}
    </div>
  );
}

function RawLogsView({ rawLogs }: { rawLogs: ReturnType<typeof usePipelineRun>["rawLogs"] }) {
  if (rawLogs.length === 0) {
    return <div className="text-[12px] text-[var(--ink-subtle)]">Raw 이벤트 없음</div>;
  }
  return (
    <div className="space-y-1 font-mono text-[10.5px]">
      {rawLogs.map((r) => (
        <div key={r.id} className="rounded-[var(--radius-sm)] bg-[var(--surface-muted)] px-2 py-1">
          <span className="text-[var(--ink-subtle)]">[{r.time}]</span>{" "}
          <b className="text-[var(--accent)]">{r.event}</b>{" "}
          <span className="break-all">{JSON.stringify(r.data)}</span>
        </div>
      ))}
    </div>
  );
}

/* ── 매트릭스 빌더 + 렌더 ─────────────────────────────────── */

interface MatrixRow {
  item_number: number;
  item_name: string;
  max_score: number;
  left: number | null;
  right: number | null;
  diff: number;
}

interface MatrixData {
  leftLabel: string;
  rightLabel: string;
  rows: MatrixRow[];
  leftTotal: number;
  rightTotal: number;
  diff: number;
}

function buildComparisonMatrix(
  leftResult: EvaluationResult | null,
  rightResult: EvaluationResult | null,
  leftLabel: string,
  rightLabel: string,
): MatrixData | null {
  if (!leftResult && !rightResult) return null;
  const flatten = (r: EvaluationResult | null) =>
    r?.report?.evaluation?.categories?.flatMap((c) => c.items ?? []) ?? [];
  const left = flatten(leftResult);
  const right = flatten(rightResult);

  const byNum = new Map<number, MatrixRow>();
  const merge = (items: typeof left, side: "left" | "right") => {
    items.forEach((it) => {
      const n = it.item_number;
      const existing = byNum.get(n) || {
        item_number: n,
        item_name: it.item || it.item_name || `#${n}`,
        max_score: it.max_score,
        left: null,
        right: null,
        diff: 0,
      };
      if (!existing.item_name && (it.item || it.item_name)) {
        existing.item_name = it.item || it.item_name || `#${n}`;
      }
      if (!existing.max_score && it.max_score) existing.max_score = it.max_score;
      existing[side] = it.score ?? null;
      byNum.set(n, existing);
    });
  };
  merge(left, "left");
  merge(right, "right");

  const rows = Array.from(byNum.values())
    .sort((a, b) => a.item_number - b.item_number)
    .map((r) => ({
      ...r,
      diff: (r.left ?? 0) - (r.right ?? 0),
    }));
  const leftTotal = rows.reduce((s, r) => s + (r.left ?? 0), 0);
  const rightTotal = rows.reduce((s, r) => s + (r.right ?? 0), 0);
  return { leftLabel, rightLabel, rows, leftTotal, rightTotal, diff: leftTotal - rightTotal };
}

function ComparisonMatrixTable({ matrix }: { matrix: MatrixData }) {
  return (
    <div className="overflow-auto max-h-[520px] rounded-[var(--radius-sm)] border border-[var(--border)]">
      <table className="w-full text-[12px] border-collapse">
        <thead className="sticky top-0 bg-[var(--surface-sunken)]">
          <tr>
            <th className="px-2 py-2 text-left border-b border-[var(--border)] w-12">#</th>
            <th className="px-2 py-2 text-left border-b border-[var(--border)]">평가 항목</th>
            <th className="px-2 py-2 text-center border-b border-[var(--border)] w-16">배점</th>
            <th className="px-2 py-2 text-center border-b border-[var(--border)] w-24">
              {matrix.leftLabel}
            </th>
            <th className="px-2 py-2 text-center border-b border-[var(--border)] w-24">
              {matrix.rightLabel}
            </th>
            <th className="px-2 py-2 text-center border-b border-[var(--border)] w-20">차이</th>
          </tr>
        </thead>
        <tbody>
          {matrix.rows.map((r) => {
            const diffAbs = Math.abs(r.diff);
            const diffEmphasis = diffAbs >= 3 ? "danger" : diffAbs >= 1 ? "warn" : "neutral";
            return (
              <tr
                key={r.item_number}
                className="border-b border-[var(--border-subtle)] hover:bg-[var(--surface-hover)]"
              >
                <td className="px-2 py-1.5 text-[var(--ink-muted)]">{r.item_number}</td>
                <td className="px-2 py-1.5 font-medium">{r.item_name}</td>
                <td className="px-2 py-1.5 text-center text-[var(--ink-muted)]">{r.max_score}</td>
                <td className="px-2 py-1.5 text-center font-mono font-semibold">
                  {r.left ?? "—"}
                </td>
                <td className="px-2 py-1.5 text-center font-mono font-semibold">
                  {r.right ?? "—"}
                </td>
                <td className="px-2 py-1.5 text-center">
                  <span className={`badge badge-${diffEmphasis}`}>
                    {r.diff > 0 ? "+" : ""}
                    {r.diff}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot className="sticky bottom-0 bg-[var(--surface-sunken)] font-semibold">
          <tr>
            <td colSpan={2} className="px-2 py-2 text-right border-t-2 border-[var(--border-strong)]">
              총점
            </td>
            <td className="px-2 py-2 text-center border-t-2 border-[var(--border-strong)]">—</td>
            <td className="px-2 py-2 text-center border-t-2 border-[var(--border-strong)] font-mono">
              {matrix.leftTotal}
            </td>
            <td className="px-2 py-2 text-center border-t-2 border-[var(--border-strong)] font-mono">
              {matrix.rightTotal}
            </td>
            <td className="px-2 py-2 text-center border-t-2 border-[var(--border-strong)]">
              <span
                className={`badge badge-${
                  Math.abs(matrix.diff) >= 5 ? "danger" : Math.abs(matrix.diff) >= 2 ? "warn" : "neutral"
                }`}
              >
                {matrix.diff > 0 ? "+" : ""}
                {matrix.diff}
              </span>
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

// unused export — suppress TS warning
void modelLabelFor;

/* ── 수동 평가표 비교 — ManualComparisonTable (V2:9334) ───── */

interface ManualComparisonRow {
  no?: number;
  category?: string;
  item?: string;
  max_score?: number;
  qa_score?: number | null;
  qa_evidence?: string;
  diff_summary?: string;
  final_verdict?: string;
  [modelKey: string]: unknown;
}

interface ManualComparisonSummary {
  manual_total?: number;
  model_totals?: Record<string, number>;
  match_rate?: Record<string, number>;
  overall_verdict?: string;
}

interface ManualComparisonData {
  rows: ManualComparisonRow[];
  summary: ManualComparisonSummary;
  modelNames: string[];
}

function verdictBadgeClass(v?: string): string {
  if (v === "✅" || v === "일치") return "badge badge-success";
  if (v === "⚠️" || v === "부분차이") return "badge badge-warn";
  if (v === "❌" || v === "불일치") return "badge badge-danger";
  return "badge badge-neutral";
}

function fmtScore(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  return String(v);
}

function ManualComparisonTable({ data }: { data: ManualComparisonData }) {
  const { rows, modelNames, summary } = data;
  if (!rows?.length) {
    return (
      <div className="mt-3 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] p-3 text-[12.5px] text-[var(--ink-muted)]">
        비교할 항목을 추출하지 못했습니다. 입력 데이터를 확인해 주세요.
      </div>
    );
  }
  const hasSummary =
    summary &&
    (summary.manual_total != null ||
      Object.keys(summary.model_totals || {}).length > 0);

  return (
    <div className="mt-3">
      {hasSummary && (
        <div className="flex flex-wrap gap-2 mb-2.5">
          {summary.manual_total != null && (
            <div className="rounded-[var(--radius-sm)] bg-[var(--success-bg)] border border-[var(--success-border)] px-3 py-1.5 text-[12px]">
              <b className="text-[var(--success)]">QA 총점</b> {summary.manual_total}점
            </div>
          )}
          {modelNames.map((n) => {
            const total = summary.model_totals?.[n];
            const match = summary.match_rate?.[n];
            if (total == null && match == null) return null;
            return (
              <div
                key={n}
                className="rounded-[var(--radius-sm)] bg-[var(--accent-bg)] border border-[var(--accent-ring)] px-3 py-1.5 text-[12px]"
              >
                <b className="text-[var(--accent-strong)]">{n}</b>
                {total != null && <> {total}점</>}
                {match != null && (
                  <span className="text-[var(--ink-muted)] ml-1">
                    (일치율 {Math.round(match * 100)}%)
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
      <div className="overflow-auto max-h-[420px] rounded-[var(--radius-sm)] border border-[var(--border)]">
        <table className="w-full text-[11.5px] border-collapse">
          <thead className="sticky top-0 bg-[var(--surface-sunken)] z-10">
            <tr>
              <th className="px-2 py-2 text-left border-b border-[var(--border)] whitespace-nowrap">#</th>
              <th className="px-2 py-2 text-left border-b border-[var(--border)] whitespace-nowrap">대분류</th>
              <th className="px-2 py-2 text-left border-b border-[var(--border)] whitespace-nowrap">평가항목</th>
              <th className="px-2 py-2 text-center border-b border-[var(--border)] whitespace-nowrap">배점</th>
              <th className="px-2 py-2 text-center border-b border-[var(--border)] bg-[var(--success-bg)] whitespace-nowrap">QA</th>
              <th className="px-2 py-2 text-left border-b border-[var(--border)] bg-[var(--success-bg)]">QA 근거</th>
              {modelNames.map((n) => (
                <th
                  key={n}
                  colSpan={3}
                  className="px-2 py-2 text-center border-b border-[var(--border)] bg-[var(--accent-bg)] whitespace-nowrap"
                >
                  {n}
                </th>
              ))}
              <th className="px-2 py-2 text-left border-b border-[var(--border)]">차이</th>
              <th className="px-2 py-2 text-center border-b border-[var(--border)] whitespace-nowrap">최종</th>
            </tr>
            <tr className="bg-[var(--surface-sunken)]">
              <th colSpan={6} />
              {modelNames.map((n) => (
                <React.Fragment key={n}>
                  <th className="px-1 py-1 text-center text-[10px] font-medium text-[var(--ink-muted)] border-b border-[var(--border)] bg-[var(--accent-bg)]">
                    점수
                  </th>
                  <th className="px-1 py-1 text-left text-[10px] font-medium text-[var(--ink-muted)] border-b border-[var(--border)] bg-[var(--accent-bg)]">
                    근거
                  </th>
                  <th className="px-1 py-1 text-center text-[10px] font-medium text-[var(--ink-muted)] border-b border-[var(--border)] bg-[var(--accent-bg)]">
                    판정
                  </th>
                </React.Fragment>
              ))}
              <th colSpan={2} />
            </tr>
          </thead>
          <tbody>
            {rows.map((r, idx) => (
              <tr key={idx} className="border-b border-[var(--border-subtle)] hover:bg-[var(--surface-hover)]">
                <td className="px-2 py-1.5 text-center text-[var(--ink-muted)]">{r.no ?? idx + 1}</td>
                <td className="px-2 py-1.5 whitespace-nowrap">{r.category ?? ""}</td>
                <td className="px-2 py-1.5 font-semibold whitespace-nowrap">{r.item ?? ""}</td>
                <td className="px-2 py-1.5 text-center text-[var(--ink-muted)]">{r.max_score ?? "—"}</td>
                <td className="px-2 py-1.5 text-center bg-[var(--success-bg)] font-semibold">
                  {fmtScore(r.qa_score)}
                </td>
                <td className="px-2 py-1.5 bg-[var(--success-bg)] max-w-[280px] text-[11px]">
                  {r.qa_evidence ?? ""}
                </td>
                {modelNames.map((_, k) => {
                  const key = k + 1;
                  const verdict = r[`model${key}_verdict`] as string | undefined;
                  return (
                    <React.Fragment key={k}>
                      <td className="px-2 py-1.5 text-center bg-[var(--accent-bg)] font-semibold">
                        {fmtScore(r[`model${key}_score`])}
                      </td>
                      <td className="px-2 py-1.5 bg-[var(--accent-bg)] max-w-[280px] text-[11px]">
                        {(r[`model${key}_evidence`] as string | undefined) ?? ""}
                      </td>
                      <td className="px-2 py-1.5 text-center bg-[var(--accent-bg)]">
                        <span className={verdictBadgeClass(verdict)}>{verdict ?? "—"}</span>
                      </td>
                    </React.Fragment>
                  );
                })}
                <td className="px-2 py-1.5 text-[11px] max-w-[220px]">{r.diff_summary ?? ""}</td>
                <td className="px-2 py-1.5 text-center">
                  <span className={verdictBadgeClass(r.final_verdict)}>{r.final_verdict ?? "—"}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {summary?.overall_verdict && (
        <div className="mt-2.5 rounded-[var(--radius-sm)] border border-[var(--success-border)] bg-[var(--success-bg)] px-3 py-2.5 text-[12.5px] leading-relaxed">
          <b className="text-[var(--success)]">종합:</b> {summary.overall_verdict}
        </div>
      )}
    </div>
  );
}

/* ── 클라이언트 사이드 분석 유틸 (백엔드 미구현 대체) ─── */

function buildClientJudgeMarkdown(
  left: EvaluationResult,
  right: EvaluationResult,
  leftLabel: string,
  rightLabel: string,
): string {
  const matrix = buildComparisonMatrix(left, right, leftLabel, rightLabel);
  if (!matrix) return "_비교할 결과가 없습니다._";

  const lines: string[] = [];
  lines.push(`## 모델 비교 요약`);
  lines.push("");
  lines.push(`- **${leftLabel}** 총점: **${matrix.leftTotal}**점`);
  lines.push(`- **${rightLabel}** 총점: **${matrix.rightTotal}**점`);
  const diff = matrix.leftTotal - matrix.rightTotal;
  const winner = diff > 0 ? leftLabel : diff < 0 ? rightLabel : "—";
  lines.push(
    `- 총점 차이: **${diff > 0 ? "+" : ""}${diff}** ${diff === 0 ? "(동점)" : `(${winner} 우세)`}`,
  );
  const mae =
    matrix.rows.length > 0
      ? (
          matrix.rows.reduce((s, r) => s + Math.abs(r.diff), 0) / matrix.rows.length
        ).toFixed(2)
      : "—";
  lines.push(`- MAE (항목 평균 절대 편차): **${mae}**`);
  lines.push("");

  // 큰 차이 항목 top 3
  const bigDiffs = [...matrix.rows]
    .sort((a, b) => Math.abs(b.diff) - Math.abs(a.diff))
    .slice(0, 3)
    .filter((r) => Math.abs(r.diff) >= 1);
  if (bigDiffs.length > 0) {
    lines.push(`### 편차가 큰 항목`);
    lines.push("");
    bigDiffs.forEach((r) => {
      lines.push(
        `- **#${r.item_number} ${r.item_name}** (배점 ${r.max_score}) — ${leftLabel} ${r.left ?? "—"} vs ${rightLabel} ${r.right ?? "—"} → ${r.diff > 0 ? "+" : ""}${r.diff}`,
      );
    });
    lines.push("");
  } else {
    lines.push(`### 편차가 큰 항목`);
    lines.push("");
    lines.push("- 모든 항목에서 점수 차이가 1점 이하입니다.");
    lines.push("");
  }

  lines.push(`### 항목별 점수 비교`);
  lines.push("");
  lines.push(`| # | 항목 | 배점 | ${leftLabel} | ${rightLabel} | 차이 |`);
  lines.push(`|---|---|---:|---:|---:|---:|`);
  matrix.rows.forEach((r) => {
    const sign = r.diff > 0 ? "+" : "";
    lines.push(
      `| ${r.item_number} | ${r.item_name} | ${r.max_score} | ${r.left ?? "—"} | ${r.right ?? "—"} | ${sign}${r.diff} |`,
    );
  });
  lines.push("");
  lines.push(
    `> _참고: Judge LLM 호출 백엔드가 아직 준비되지 않아 클라이언트 사이드 점수 diff 요약만 제공합니다._`,
  );

  return lines.join("\n");
}

/**
 * 수동 평가표 텍스트에서 `{항목번호, 점수, 근거?}` 를 추출.
 * 허용 포맷 (유연):
 *   - "#1  8  인사 완료"
 *   - "1\t8\t인사"
 *   - "항목 1 : 8점"
 *   - "No 1, 점수 8, 메모 ..."
 *   - JSON `[{"no":1,"score":8,"evidence":"..."}]`
 */
function parseManualEvalText(
  text: string,
): Array<{ no: number; score: number; evidence?: string; max_score?: number; category?: string; item?: string }> {
  const trimmed = text.trim();
  // JSON array 시도
  if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
    try {
      const obj = JSON.parse(trimmed);
      const arr = Array.isArray(obj) ? obj : Array.isArray(obj?.rows) ? obj.rows : null;
      if (arr) {
        type Parsed = { no: number; score: number; evidence?: string; max_score?: number; category?: string; item?: string };
        const mapped: Array<Parsed | null> = arr.map((r: Record<string, unknown>) => {
          const no = Number(r.no ?? r.item_number ?? r.item_no ?? NaN);
          const score = Number(r.score ?? r.qa_score ?? r.human_score ?? NaN);
          if (!Number.isFinite(no) || !Number.isFinite(score)) return null;
          return {
            no,
            score,
            evidence: (r.evidence || r.qa_evidence || r.note || "") as string,
            max_score: Number(r.max_score ?? r.배점) || undefined,
            category: (r.category || r.대분류 || "") as string,
            item: (r.item || r.평가항목 || "") as string,
          };
        });
        return mapped.filter((x: Parsed | null): x is Parsed => x !== null);
      }
    } catch {
      /* 폴스루 */
    }
  }

  // 라인 스캔 (CSV/TSV/평문 혼용 대응)
  const out: Array<{ no: number; score: number; evidence?: string }> = [];
  text.split(/\r?\n/).forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) return;
    // "#N 점수 ..." / "N, 점수, ..." / "N\t점수\t..." / "항목 N : 점수 ..."
    // 숫자 두 개를 찾아 첫 번째는 no, 두 번째는 score 로 본다.
    const m = line.match(
      /(?:^|[^0-9])(\d{1,2})(?:\s*[번항목no.:,\t]\s*|\s+)(\d{1,3})(?:[^0-9]|$)/i,
    );
    if (!m) return;
    const no = Number(m[1]);
    const score = Number(m[2]);
    if (!Number.isFinite(no) || !Number.isFinite(score)) return;
    if (no < 1 || no > 99 || score < 0 || score > 100) return;
    // 두 숫자 뒤 나머지를 evidence 로
    const afterIdx = line.indexOf(m[0]) + m[0].length;
    const evidence = line.slice(afterIdx).replace(/^[,\t\s:점]+/, "").trim();
    out.push({ no, score, evidence });
  });
  return out;
}

function buildClientManualComparison(
  manualRows: Array<{ no: number; score: number; evidence?: string; max_score?: number; category?: string; item?: string }>,
  models: Array<{ name: string; result: EvaluationResult }>,
): ManualComparisonData {
  // 모델 결과를 item_number → {score, evidence} 맵으로
  const modelMaps = models.map((m) => {
    const items =
      m.result.report?.evaluation?.categories?.flatMap((c) => c.items ?? []) ?? [];
    const byNum = new Map<number, { score: number; evidence: string; max_score: number; item: string; category: string }>();
    (m.result.report?.evaluation?.categories ?? []).forEach((cat) => {
      (cat.items ?? []).forEach((it) => {
        const evidenceText = Array.isArray(it.evidence)
          ? it.evidence
              .map((e) => (typeof e === "string" ? e : e.quote || e.text || ""))
              .filter(Boolean)
              .join(" · ")
          : "";
        byNum.set(it.item_number, {
          score: it.score,
          evidence: it.judgment || evidenceText || "",
          max_score: it.max_score,
          item: it.item || it.item_name || `#${it.item_number}`,
          category: cat.category,
        });
      });
    });
    // Fallback: items 경로만 존재할 때
    if (byNum.size === 0) {
      items.forEach((it) => {
        byNum.set(it.item_number, {
          score: it.score,
          evidence: it.judgment || "",
          max_score: it.max_score,
          item: it.item || it.item_name || `#${it.item_number}`,
          category: "",
        });
      });
    }
    return { name: m.name, byNum };
  });

  // 항목 번호 집합 (수동 ∪ 모든 모델)
  const allNos = new Set<number>();
  manualRows.forEach((r) => allNos.add(r.no));
  modelMaps.forEach((m) => m.byNum.forEach((_, n) => allNos.add(n)));
  const sortedNos = Array.from(allNos).sort((a, b) => a - b);

  const rows: ManualComparisonRow[] = sortedNos.map((no, idx) => {
    const manualEntry = manualRows.find((r) => r.no === no) || null;
    // 카테고리/항목명은 가장 먼저 찾은 모델에서 추출
    let category = manualEntry?.category || "";
    let item = manualEntry?.item || "";
    let maxScore: number | undefined = manualEntry?.max_score;
    for (const m of modelMaps) {
      const mv = m.byNum.get(no);
      if (mv) {
        if (!category) category = mv.category;
        if (!item) item = mv.item;
        if (maxScore == null) maxScore = mv.max_score;
        break;
      }
    }
    const row: ManualComparisonRow = {
      no,
      category,
      item,
      max_score: maxScore,
      qa_score: manualEntry?.score ?? null,
      qa_evidence: manualEntry?.evidence || "",
    };
    // 모델별 컬럼
    const diffs: number[] = [];
    modelMaps.forEach((m, i) => {
      const k = i + 1;
      const mv = m.byNum.get(no);
      row[`model${k}_score`] = mv?.score ?? null;
      row[`model${k}_evidence`] = mv?.evidence || "";
      if (manualEntry && mv) {
        const d = Math.abs(manualEntry.score - mv.score);
        diffs.push(d);
        row[`model${k}_verdict`] = d === 0 ? "일치" : d <= 1 ? "부분차이" : "불일치";
      } else {
        row[`model${k}_verdict`] = "—";
      }
    });
    // 차이 요약 & 최종 판정
    if (manualEntry && diffs.length > 0) {
      const maxDiff = Math.max(...diffs);
      row.diff_summary = `최대 ${maxDiff}점 편차`;
      row.final_verdict = maxDiff === 0 ? "일치" : maxDiff <= 1 ? "부분차이" : "불일치";
    } else {
      row.diff_summary = manualEntry ? "모델 결과 없음" : "수동 점수 없음";
      row.final_verdict = "—";
    }
    void idx;
    return row;
  });

  // 요약
  const manualTotal = manualRows.reduce((s, r) => s + r.score, 0);
  const modelTotals: Record<string, number> = {};
  const matchRate: Record<string, number> = {};
  modelMaps.forEach((m) => {
    let total = 0;
    let matched = 0;
    let denom = 0;
    m.byNum.forEach((mv) => (total += mv.score));
    manualRows.forEach((r) => {
      const mv = m.byNum.get(r.no);
      if (mv) {
        denom++;
        if (Math.abs(mv.score - r.score) <= 1) matched++;
      }
    });
    modelTotals[m.name] = total;
    matchRate[m.name] = denom > 0 ? matched / denom : 0;
  });

  const overallVerdict = (() => {
    if (modelMaps.length === 0) return "모델 결과 없음";
    const rates = Object.values(matchRate);
    const avg = rates.reduce((s, v) => s + v, 0) / rates.length;
    if (avg >= 0.9) return "대체로 QA 와 일치합니다 (일치율 ≥ 90%).";
    if (avg >= 0.7) return "일부 항목에서 편차가 있습니다 (일치율 70~90%).";
    return "편차가 큽니다 — 모델 결과를 재검토하세요 (일치율 < 70%).";
  })();

  return {
    rows,
    summary: {
      manual_total: manualTotal,
      model_totals: modelTotals,
      match_rate: matchRate,
      overall_verdict: overallVerdict,
    },
    modelNames: models.map((m) => m.name),
  };
}

export default ComparePanel;
