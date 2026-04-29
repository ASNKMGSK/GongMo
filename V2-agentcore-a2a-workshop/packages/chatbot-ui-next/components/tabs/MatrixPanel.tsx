// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAppState } from "@/lib/AppStateContext";
import { BASE_URL } from "@/lib/api";
import {
  clearSavedHandle,
  isFsaSupported,
  pickAndPersistHandle,
  restoreSavedHandle,
  todaySubFolder,
  writeWithFallback,
  type SavedHandleInfo,
} from "@/lib/fsaStore";
import {
  buildSyntheticResultFromModelResult,
  extractAiItemByNumber,
} from "@/lib/manualEvalMapper";
import { downloadXlsxWithAiAppended, type AiModelForExport } from "@/lib/manualEvalExport";
import {
  extractConsultationId,
  findMatchingSheet,
  parseManualEvalSheet,
  type ManualSheet,
} from "@/lib/manualEvalParser";
import {
  MODEL_GROUPS,
  MODEL_LEFT_DEFAULT,
  MODEL_RIGHT_DEFAULT,
  labelFor,
  resolveModelSelection,
} from "@/lib/models";
import { useToast } from "@/lib/toast";
import { usePipelineRun } from "@/lib/usePipelineRun";
import type { EvaluationResult } from "@/lib/types";
import * as XLSX from "xlsx";

/* ─────────────────────────────────────────────────────────────
   MatrixPanel — Task #4 (Dev4)
   V2 원본: qa_pipeline_reactflow.html:10097 (MatrixTab)
   transcript 파일 다수 업로드 → 좌/우 모델 각자 독립 인덱스로 순차 평가
   → sample_id × 18 항목 매트릭스 + csv 다운로드 + 자동 저장 (백엔드 /save-xlsx)
   ───────────────────────────────────────────────────────────── */

interface BatchFile {
  name: string;
  transcript: string;
}

interface BatchEntry {
  /** per-model 결과 (leftLabel / rightLabel 키) */
  [modelLabel: string]: EvaluationResult | null;
}

type BatchResults = Record<string, BatchEntry>;

async function readTranscriptFile(file: File): Promise<string> {
  try {
    const text = await file.text();
    if (!text.includes("�")) return text;
  } catch {
    /* fallthrough */
  }
  const buf = await file.arrayBuffer();
  try {
    return new TextDecoder("euc-kr").decode(buf);
  } catch {
    return new TextDecoder("utf-8", { fatal: false }).decode(buf);
  }
}

function flatItems(result: EvaluationResult | null) {
  return result?.report?.evaluation?.categories?.flatMap((c) => c.items ?? []) ?? [];
}

function totalFor(result: EvaluationResult | null): number | null {
  if (!result) return null;
  const fs = result.report?.final_score;
  if (fs?.after_overrides != null) return fs.after_overrides;
  if (fs?.raw_total != null) return fs.raw_total;
  const items = flatItems(result);
  if (items.length === 0) return null;
  return items.reduce((s, it) => s + (it.score || 0), 0);
}

export function MatrixPanel() {
  const { state } = useAppState();
  const toast = useToast();
  const serverUrl = state.serverUrl || BASE_URL;

  const [leftModel, setLeftModel] = useState(MODEL_LEFT_DEFAULT);
  const [rightModel, setRightModel] = useState(MODEL_RIGHT_DEFAULT);
  const leftSel = useMemo(() => resolveModelSelection(leftModel), [leftModel]);
  const rightSel = useMemo(() => resolveModelSelection(rightModel), [rightModel]);
  const leftLabel = labelFor(leftSel);
  const rightLabel = labelFor(rightSel);

  const leftRun = usePipelineRun({ column: "matrix-left", serverUrl });
  const rightRun = usePipelineRun({ column: "matrix-right", serverUrl });

  const [batchFiles, setBatchFiles] = useState<BatchFile[]>([]);
  const [leftIdx, setLeftIdx] = useState(-1);
  const [rightIdx, setRightIdx] = useState(-1);
  const [batchResults, setBatchResults] = useState<BatchResults>({});
  const [selectedFileName, setSelectedFileName] = useState("");

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // FSA — 자동 저장용 루트 폴더 (V2 MatrixTab FSA 이식)
  const fsaSupported = isFsaSupported();
  const [saveHandle, setSaveHandle] = useState<SavedHandleInfo | null>(null);
  const [autoSaveOn, setAutoSaveOn] = useState(true);
  const autoSavedKeysRef = useRef<Set<string>>(new Set()); // "fname::label" 중복 방지

  // 복원: 마운트 시 영속화된 핸들 불러오기
  useEffect(() => {
    if (!fsaSupported) return;
    let cancelled = false;
    restoreSavedHandle().then((info) => {
      if (!cancelled && info) setSaveHandle(info);
    });
    return () => {
      cancelled = true;
    };
  }, [fsaSupported]);

  const pickSaveDir = useCallback(async () => {
    if (!fsaSupported) {
      toast.error("이 브라우저는 폴더 직접 지정 기능을 지원하지 않습니다 (Chrome/Edge 필요).");
      return;
    }
    try {
      const info = await pickAndPersistHandle({ startIn: "desktop" });
      setSaveHandle(info);
      toast.success(`저장 폴더 지정 완료: ${info.name}`);
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        toast.error("폴더 지정 실패", { description: String((err as Error).message || err) });
      }
    }
  }, [fsaSupported, toast]);

  const clearSaveDir = useCallback(async () => {
    await clearSavedHandle();
    setSaveHandle(null);
    toast.info("저장 폴더 지정 해제");
  }, [toast]);

  // Dev6 — 정답 xlsx (수동 QA 평가표) 단일 workbook, 여러 시트 = 여러 샘플
  const [answerWorkbook, setAnswerWorkbook] = useState<XLSX.WorkBook | null>(null);
  const [answerBuffer, setAnswerBuffer] = useState<ArrayBuffer | null>(null);
  const [answerFileName, setAnswerFileName] = useState("");
  const [answerMap, setAnswerMap] = useState<Record<string, ManualSheet>>({});
  const [answerUnmatched, setAnswerUnmatched] = useState<string[]>([]);
  const answerFileInputRef = useRef<HTMLInputElement | null>(null);

  const onAnswerFileSelect = useCallback(async (ev: React.ChangeEvent<HTMLInputElement>) => {
    const file = ev.target.files?.[0];
    ev.target.value = "";
    if (!file) return;
    try {
      const buf = await file.arrayBuffer();
      const wb = XLSX.read(buf, { type: "array" });
      setAnswerWorkbook(wb);
      setAnswerBuffer(buf);
      setAnswerFileName(file.name);
      toast.success(`정답 xlsx 로드: ${file.name} · 시트 ${wb.SheetNames.length}개`);
    } catch (err) {
      toast.error(`정답 xlsx 읽기 실패: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, [toast]);

  const clearAnswer = useCallback(() => {
    setAnswerWorkbook(null);
    setAnswerBuffer(null);
    setAnswerFileName("");
    setAnswerMap({});
    setAnswerUnmatched([]);
  }, []);

  // answerWorkbook 또는 batchFiles 변경 시 매칭 재계산
  useEffect(() => {
    if (!answerWorkbook) {
      setAnswerMap({});
      setAnswerUnmatched([]);
      return;
    }
    const map: Record<string, ManualSheet> = {};
    const unmatched: string[] = [];
    batchFiles.forEach((f) => {
      const id = extractConsultationId(f.name);
      const matched = findMatchingSheet(answerWorkbook, id);
      if (matched) {
        try {
          map[f.name] = parseManualEvalSheet(answerWorkbook.Sheets[matched], matched);
        } catch {
          unmatched.push(f.name);
        }
      } else {
        unmatched.push(f.name);
      }
    });
    setAnswerMap(map);
    setAnswerUnmatched(unmatched);
  }, [answerWorkbook, batchFiles]);

  // 정답 xlsx 에 이미 특정 모델 결과가 저장돼 있으면 batchResults 에 synthetic 주입 (실행 skip).
  useEffect(() => {
    if (!Object.keys(answerMap).length) return;
    setBatchResults((prev) => {
      const merged: BatchResults = { ...prev };
      Object.entries(answerMap).forEach(([fname, sheet]) => {
        const entry = merged[fname] || {};
        (["left", "right"] as const).forEach((side) => {
          const label = side === "left" ? leftLabel : rightLabel;
          const mr = sheet.modelResults?.[label];
          if (mr && !entry[label]) {
            const syn = buildSyntheticResultFromModelResult(mr);
            if (syn) entry[label] = syn;
          }
        });
        if (Object.keys(entry).length) merged[fname] = entry;
      });
      return merged;
    });
  }, [answerMap, leftLabel, rightLabel]);

  const running = leftIdx >= 0 || rightIdx >= 0;

  const onFilesSelected = useCallback(async (ev: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(ev.target.files || []);
    ev.target.value = "";
    if (!files.length) return;
    const loaded: BatchFile[] = [];
    for (const f of files) {
      const text = await readTranscriptFile(f);
      loaded.push({ name: f.name, transcript: text });
    }
    setBatchFiles((prev) => {
      const byName = new Map(prev.map((f) => [f.name, f]));
      let replaced = 0;
      let added = 0;
      for (const f of loaded) {
        if (byName.has(f.name)) replaced++;
        else added++;
        byName.set(f.name, f);
      }
      if (replaced > 0) {
        toast.info(`신규 ${added}개 · 중복 ${replaced}개 교체`);
      } else {
        toast.success(`파일 ${added}개 업로드 완료`);
      }
      return Array.from(byName.values());
    });
  }, [toast]);

  const clearBatch = useCallback(() => {
    if (running) return;
    setBatchFiles([]);
    setBatchResults({});
    setSelectedFileName("");
  }, [running]);

  const removeFile = useCallback(
    (name: string) => {
      const leftProcessing = leftIdx >= 0 && batchFiles[leftIdx]?.name === name;
      const rightProcessing = rightIdx >= 0 && batchFiles[rightIdx]?.name === name;
      if (leftProcessing || rightProcessing) return;
      setBatchFiles((prev) => prev.filter((f) => f.name !== name));
      setBatchResults((prev) => {
        const c = { ...prev };
        delete c[name];
        return c;
      });
      if (selectedFileName === name) setSelectedFileName("");
    },
    [leftIdx, rightIdx, batchFiles, selectedFileName],
  );

  // 완료된 파일만 일괄 제거 (V2: removeCompletedFiles)
  const removeCompletedFiles = useCallback(() => {
    if (running) return;
    const doneNames = Object.keys(batchResults).filter((n) => {
      const e = batchResults[n] || {};
      return !!e[leftLabel] || !!e[rightLabel];
    });
    if (!doneNames.length) return;
    setBatchFiles((prev) => prev.filter((f) => !doneNames.includes(f.name)));
    setBatchResults((prev) => {
      const c = { ...prev };
      doneNames.forEach((n) => delete c[n]);
      return c;
    });
    if (doneNames.includes(selectedFileName)) setSelectedFileName("");
    toast.info(`완료된 ${doneNames.length}개 파일 제거`);
  }, [running, batchResults, leftLabel, rightLabel, selectedFileName, toast]);

  // advance — 다음 처리할 인덱스 결정 + start 호출.
  // 정답 xlsx 에 이미 해당 모델 결과가 있으면 synthetic 으로 대체, LLM 호출 skip.
  const advanceSide = useCallback(
    (side: "left" | "right", startIdx: number): number => {
      const run = side === "left" ? leftRun : rightRun;
      const sel = side === "left" ? leftSel : rightSel;
      const label = side === "left" ? leftLabel : rightLabel;
      let idx = startIdx;
      while (idx < batchFiles.length) {
        const f = batchFiles[idx];
        const mr = answerMap[f.name]?.modelResults?.[label];
        if (mr) {
          // Excel 에 이미 저장 → batchResults 에 주입 후 다음으로
          const syn = buildSyntheticResultFromModelResult(mr);
          if (syn) {
            setBatchResults((prev) => ({
              ...prev,
              [f.name]: { ...(prev[f.name] || {}), [label]: syn },
            }));
          }
          idx++;
          continue;
        }
        run.start({
          transcript: f.transcript,
          llmBackend: sel.backend,
          bedrockModelId: sel.bedrock_model_id,
          tenantId: state.tenantId,
        });
        return idx;
      }
      return -1;
    },
    [leftRun, rightRun, leftSel, rightSel, leftLabel, rightLabel, batchFiles, answerMap, state.tenantId],
  );

  const startBatch = useCallback(() => {
    if (!batchFiles.length || running) return;
    setSelectedFileName(batchFiles[0].name);
    setLeftIdx(advanceSide("left", 0));
    setRightIdx(advanceSide("right", 0));
  }, [batchFiles, running, advanceSide]);

  const abortBatch = useCallback(() => {
    leftRun.abort();
    rightRun.abort();
    setLeftIdx(-1);
    setRightIdx(-1);
  }, [leftRun, rightRun]);

  // 좌측 완료 감지 → 결과 저장 + 다음 파일로 진행
  useEffect(() => {
    if (leftIdx < 0) return;
    if (leftRun.isRunning) return;
    const cur = batchFiles[leftIdx];
    if (!cur) return;
    const existing = batchResults[cur.name] || {};
    if (leftRun.result && existing[leftLabel] === undefined) {
      setBatchResults((prev) => ({
        ...prev,
        [cur.name]: { ...(prev[cur.name] || {}), [leftLabel]: leftRun.result },
      }));
      return;
    }
    if (existing[leftLabel] !== undefined || leftRun.result === null) {
      const nextIdx = advanceSide("left", leftIdx + 1);
      setLeftIdx(nextIdx);
    }
  }, [leftRun.isRunning, leftRun.result, leftIdx, batchFiles, batchResults, leftLabel, advanceSide]);

  // 우측 완료 감지
  useEffect(() => {
    if (rightIdx < 0) return;
    if (rightRun.isRunning) return;
    const cur = batchFiles[rightIdx];
    if (!cur) return;
    const existing = batchResults[cur.name] || {};
    if (rightRun.result && existing[rightLabel] === undefined) {
      setBatchResults((prev) => ({
        ...prev,
        [cur.name]: { ...(prev[cur.name] || {}), [rightLabel]: rightRun.result },
      }));
      return;
    }
    if (existing[rightLabel] !== undefined || rightRun.result === null) {
      const nextIdx = advanceSide("right", rightIdx + 1);
      setRightIdx(nextIdx);
    }
  }, [rightRun.isRunning, rightRun.result, rightIdx, batchFiles, batchResults, rightLabel, advanceSide]);

  // 자동 저장 — batchResults 변화 감지 → (fileName, label) 조합이 완료되면 FSA 쓰기.
  // V2 autoSave + autoSavedRef 이식 (qa_pipeline_reactflow.html:10129~).
  useEffect(() => {
    if (!autoSaveOn || !saveHandle) return;
    if (!Object.keys(answerMap).length) return;
    if (typeof window === "undefined") return;

    (async () => {
      const subFolder = todaySubFolder();
      for (const [fname, entry] of Object.entries(batchResults)) {
        const sheet = answerMap[fname];
        if (!sheet) continue;
        const aiMods: AiModelForExport[] = [];
        for (const label of [leftLabel, rightLabel]) {
          if (!entry[label]) continue;
          const key = `${fname}::${label}`;
          if (autoSavedKeysRef.current.has(key)) continue;
          aiMods.push({ name: label, result: entry[label]! });
        }
        if (!aiMods.length) continue;
        try {
          // Dev6 의 downloadXlsxWithAiAppended 는 anchor download 전용 → FSA 경로는 xlsxExport 에서 직접 buffer 얻어야 함.
          // 간이 경로: FSA 가 있으면 해당 폴더에 CSV 형태로 요약만 저장 (Dev6 의 xlsx 생성은 anchor 기반).
          // 여기선 파일별 요약 CSV 를 FSA 로 저장 (xlsx append 는 사용자가 수동 다운로드).
          const aiSummary = aiMods
            .map((m) => {
              const items = flatItems(m.result as EvaluationResult);
              const lines = items
                .map((it) => `#${it.item_number}\t${it.score}\t${it.max_score}\t${it.item || it.item_name || ""}`)
                .join("\n");
              return `[${m.name}]\n${lines}`;
            })
            .join("\n\n");
          const content = `파일: ${fname}\n저장 시각: ${new Date().toISOString()}\n\n${aiSummary}\n`;
          const blob = new Blob(["﻿" + content], { type: "text/plain;charset=utf-8" });
          const autoFileName = `${fname.replace(/\.(txt|json|csv|md|log)$/i, "")}_summary.txt`;
          const res = await writeWithFallback({
            data: blob,
            subFolder,
            fileName: autoFileName,
            rootHandle: saveHandle.handle,
            serverUrl,
          });
          if (res.via === "fsa") {
            aiMods.forEach((m) => autoSavedKeysRef.current.add(`${fname}::${m.name}`));
          }
        } catch {
          /* best-effort — 실패해도 UI 는 계속 진행 */
        }
      }
    })();
  }, [autoSaveOn, saveHandle, answerMap, batchResults, leftLabel, rightLabel, serverUrl]);

  // 선택된 파일의 정답 xlsx 에 AI 컬럼 append 후 다운로드
  const downloadAnswerXlsxForFile = useCallback(
    async (fileName: string) => {
      const sheet = answerMap[fileName];
      if (!sheet) {
        toast.error("해당 파일에 매칭된 정답 xlsx 시트가 없습니다.");
        return;
      }
      const entry = batchResults[fileName] || {};
      const aiMods: AiModelForExport[] = [];
      if (entry[leftLabel]) aiMods.push({ name: leftLabel, result: entry[leftLabel]! });
      if (entry[rightLabel] && rightLabel !== leftLabel) {
        aiMods.push({ name: rightLabel, result: entry[rightLabel]! });
      }
      if (!aiMods.length) {
        toast.error("AI 평가 결과가 없습니다. 배치 실행 후 다시 시도해주세요.");
        return;
      }
      try {
        await downloadXlsxWithAiAppended({
          originalBuffer: answerBuffer,
          manualSheet: sheet,
          aiMods,
          fileNameBase: answerFileName.replace(/\.xlsx?$/i, ""),
        });
        toast.success("정답 xlsx 다운로드 완료 (AI 컬럼 추가)");
      } catch (err) {
        toast.error(`다운로드 실패: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [answerMap, answerBuffer, answerFileName, batchResults, leftLabel, rightLabel, toast],
  );

  const downloadCsv = useCallback(() => {
    if (!Object.keys(batchResults).length) return;
    const itemNumbers = Array.from(
      new Set(
        Object.values(batchResults)
          .flatMap((entry) => [entry[leftLabel], entry[rightLabel]])
          .flatMap((r) => flatItems(r))
          .map((it) => it.item_number),
      ),
    ).sort((a, b) => a - b);

    const header = [
      "파일명",
      `${leftLabel} 총점`,
      `${rightLabel} 총점`,
      ...itemNumbers.flatMap((n) => [`#${n} ${leftLabel}`, `#${n} ${rightLabel}`]),
    ];
    const rows = Object.entries(batchResults).map(([fname, entry]) => {
      const l = entry[leftLabel];
      const r = entry[rightLabel];
      const li = new Map(flatItems(l).map((it) => [it.item_number, it.score]));
      const ri = new Map(flatItems(r).map((it) => [it.item_number, it.score]));
      return [
        fname,
        totalFor(l) ?? "",
        totalFor(r) ?? "",
        ...itemNumbers.flatMap((n) => [li.get(n) ?? "", ri.get(n) ?? ""]),
      ];
    });
    const csv = [header, ...rows]
      .map((row) => row.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(","))
      .join("\n");
    const bom = "﻿";
    const blob = new Blob([bom + csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    a.download = `qa_matrix_${ts}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    toast.success("CSV 다운로드 완료");
  }, [batchResults, leftLabel, rightLabel, toast]);

  // 현재 진행 상황
  const totalFiles = batchFiles.length;
  const leftDone = Object.keys(batchResults).filter((k) => batchResults[k][leftLabel]).length;
  const rightDone = Object.keys(batchResults).filter((k) => batchResults[k][rightLabel]).length;
  const leftCurrent = leftIdx >= 0 && batchFiles[leftIdx] ? batchFiles[leftIdx].name : "";
  const rightCurrent = rightIdx >= 0 && batchFiles[rightIdx] ? batchFiles[rightIdx].name : "";

  // 매트릭스 렌더 대상 파일 (선택된 파일)
  const viewName = selectedFileName || leftCurrent || rightCurrent || batchFiles[0]?.name || "";
  const viewEntry = batchResults[viewName] || {};
  const viewLeft = viewEntry[leftLabel] || null;
  const viewRight = viewEntry[rightLabel] || null;

  return (
    <div className="flex flex-col gap-5">
      {/* 설정 + 업로드 */}
      <div className="card card-padded">
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-[13px]">
            <span className="font-medium text-[var(--ink-soft)]">Left</span>
            <select
              className="input-field input-sm"
              value={leftModel}
              onChange={(e) => setLeftModel(e.target.value)}
              disabled={running}
            >
              {Object.entries(MODEL_GROUPS).map(([g, opts]) => (
                <optgroup key={g} label={g}>
                  {opts.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-[13px]">
            <span className="font-medium text-[var(--ink-soft)]">Right</span>
            <select
              className="input-field input-sm"
              value={rightModel}
              onChange={(e) => setRightModel(e.target.value)}
              disabled={running}
            >
              {Object.entries(MODEL_GROUPS).map(([g, opts]) => (
                <optgroup key={g} label={g}>
                  {opts.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>

          <div className="ml-auto flex items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".txt,.md,.json,.csv,.log,text/*"
              className="hidden"
              onChange={onFilesSelected}
            />
            <button
              className="btn-secondary btn-sm"
              onClick={() => fileInputRef.current?.click()}
              disabled={running}
            >
              📎 transcript 추가
            </button>
            {!running ? (
              <button
                className="btn-primary btn-sm"
                onClick={startBatch}
                disabled={!batchFiles.length}
              >
                ▶ 배치 실행 ({batchFiles.length}개)
              </button>
            ) : (
              <button className="btn-danger btn-sm" onClick={abortBatch}>
                ■ 중단
              </button>
            )}
            <button
              className="btn-ghost btn-sm"
              onClick={removeCompletedFiles}
              disabled={running || !Object.keys(batchResults).length}
              title="완료된 파일만 제거"
            >
              ✓ 완료 제거
            </button>
            <button
              className="btn-ghost btn-sm"
              onClick={clearBatch}
              disabled={running || !batchFiles.length}
            >
              Clear
            </button>
            <button
              className="btn-secondary btn-sm"
              onClick={downloadCsv}
              disabled={!Object.keys(batchResults).length}
            >
              📥 CSV 다운로드
            </button>
          </div>
        </div>

        {/* Dev6 — 정답 xlsx 첨부 영역 */}
        <div className="mt-4 pt-4 border-t border-[var(--border)]">
          <div className="flex items-center gap-2 flex-wrap">
            <input
              ref={answerFileInputRef}
              type="file"
              accept=".xlsx,.xls"
              className="hidden"
              onChange={onAnswerFileSelect}
            />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--ink-muted)]">
              정답 xlsx (사람 QA 평가표)
            </span>
            <button
              className="btn-secondary btn-sm"
              onClick={() => answerFileInputRef.current?.click()}
              disabled={running}
              title="여러 샘플을 모은 통합 평가표 xlsx 를 첨부하면 파일명 ID 로 자동 매칭됩니다."
            >
              📊 정답 xlsx 첨부
            </button>
            {answerFileName && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-medium text-emerald-700 border border-emerald-200 dark:bg-emerald-900/20 dark:text-emerald-300 dark:border-emerald-800">
                <span className="max-w-[240px] truncate" title={answerFileName}>
                  {answerFileName}
                </span>
                <span className="px-1.5 py-0.5 rounded bg-emerald-100 dark:bg-emerald-800/40 font-mono">
                  매칭 {Object.keys(answerMap).length}/{batchFiles.length}
                </span>
                <button
                  type="button"
                  className="ml-0.5 text-emerald-700 hover:text-red-600"
                  onClick={clearAnswer}
                  aria-label="정답 xlsx 제거"
                >
                  ✕
                </button>
              </span>
            )}
            {answerWorkbook && answerUnmatched.length > 0 && (
              <span className="text-[11px] text-amber-700 dark:text-amber-400">
                미매칭: {answerUnmatched.length}건
              </span>
            )}
          </div>
        </div>

        {/* FSA — 자동 저장 루트 폴더 (V2 MatrixTab FSA 이식) */}
        <div className="mt-4 pt-4 border-t border-[var(--border)]">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--ink-muted)]">
              자동 저장 폴더
            </span>
            {!fsaSupported ? (
              <span className="text-[11px] text-[var(--warn)]">
                이 브라우저는 폴더 직접 지정 미지원 — 서버 /save-xlsx 또는 다운로드로 폴백.
              </span>
            ) : (
              <>
                <button
                  className="btn-secondary btn-sm"
                  onClick={pickSaveDir}
                  disabled={running}
                  title="배치 평가 완료 파일을 자동으로 이 폴더 아래 날짜별 서브폴더에 저장합니다 (Chrome/Edge)."
                >
                  📁 저장 폴더 선택
                </button>
                {saveHandle && (
                  <span className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] bg-[var(--accent-bg)] px-2.5 py-1 text-[11px] font-medium text-[var(--accent-strong)] border border-[var(--accent-ring)]">
                    <span className="max-w-[200px] truncate">📁 {saveHandle.name}</span>
                    {saveHandle.permission !== "granted" && (
                      <span className="text-[var(--warn)]">권한 재승인 필요</span>
                    )}
                    <button
                      type="button"
                      className="text-[var(--accent-strong)] hover:text-[var(--danger)]"
                      onClick={clearSaveDir}
                      aria-label="저장 폴더 해제"
                    >
                      ✕
                    </button>
                  </span>
                )}
                <label className="switch ml-2" title="자동 저장 on/off">
                  <input
                    type="checkbox"
                    checked={autoSaveOn}
                    onChange={(e) => setAutoSaveOn(e.target.checked)}
                    disabled={!saveHandle}
                  />
                  <span className="switch-slider" />
                </label>
                <span className="text-[11px] text-[var(--ink-muted)]">자동 저장 {autoSaveOn ? "on" : "off"}</span>
              </>
            )}
          </div>
        </div>

        {/* 파일 리스트 */}
        {batchFiles.length > 0 && (
          <div className="mt-4">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--ink-muted)] mb-2">
              파일 {batchFiles.length}개
            </div>
            <div className="flex flex-wrap gap-1.5 max-h-[120px] overflow-auto">
              {batchFiles.map((f, i) => {
                const entry = batchResults[f.name] || {};
                const leftReady = !!entry[leftLabel];
                const rightReady = !!entry[rightLabel];
                const isActive = leftCurrent === f.name || rightCurrent === f.name;
                return (
                  <button
                    key={f.name}
                    onClick={() => setSelectedFileName(f.name)}
                    className={`group flex items-center gap-1.5 rounded-[var(--radius-sm)] px-2.5 py-1.5 text-[11px] transition ${
                      selectedFileName === f.name
                        ? "bg-[var(--accent-bg)] text-[var(--accent-strong)] border border-[var(--accent)]"
                        : "bg-[var(--surface-muted)] hover:bg-[var(--surface-hover)] border border-[var(--border)]"
                    }`}
                  >
                    <span className="text-[10px] text-[var(--ink-subtle)]">{i + 1}</span>
                    <span className="max-w-[180px] overflow-hidden text-ellipsis whitespace-nowrap">
                      {f.name}
                    </span>
                    {isActive ? (
                      <span className="badge badge-accent text-[9px]">running</span>
                    ) : (
                      <>
                        {leftReady && <span className="badge badge-success text-[9px]">L</span>}
                        {rightReady && <span className="badge badge-success text-[9px]">R</span>}
                      </>
                    )}
                    {!running && (
                      <span
                        role="button"
                        tabIndex={0}
                        aria-label={`${f.name} 제거`}
                        className="opacity-0 group-hover:opacity-100 text-[var(--ink-subtle)] hover:text-[var(--danger)] cursor-pointer"
                        onClick={(e) => {
                          e.stopPropagation();
                          removeFile(f.name);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.stopPropagation();
                            removeFile(f.name);
                          }
                        }}
                      >
                        ✕
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* 진행 상태 */}
        {totalFiles > 0 && (
          <div className="mt-4 grid grid-cols-2 gap-3">
            <ProgressLine
              label={leftLabel}
              done={leftDone}
              total={totalFiles}
              current={leftCurrent}
              isRunning={leftRun.isRunning}
              elapsed={leftRun.elapsed}
            />
            <ProgressLine
              label={rightLabel}
              done={rightDone}
              total={totalFiles}
              current={rightCurrent}
              isRunning={rightRun.isRunning}
              elapsed={rightRun.elapsed}
            />
          </div>
        )}
      </div>

      {/* 매트릭스 테이블 — 선택된 파일의 항목별 비교 */}
      {viewName && (viewLeft || viewRight) ? (
        <div className="card card-padded">
          <div className="mb-3 flex items-center justify-between gap-3 flex-wrap">
            <div>
              <h3 className="text-[14px] font-semibold">{viewName}</h3>
              <div className="text-[11.5px] text-[var(--ink-muted)] mt-0.5">
                L({leftLabel}) {totalFor(viewLeft) ?? "—"}{" "}
                <span className="text-[var(--ink-subtle)]">vs</span>{" "}
                R({rightLabel}) {totalFor(viewRight) ?? "—"}
                {answerMap[viewName] && (
                  <>
                    {" "}
                    <span className="text-[var(--ink-subtle)]">·</span>{" "}
                    <span className="text-emerald-700 dark:text-emerald-400">
                      사람 QA {answerMap[viewName].total ?? "—"}
                    </span>
                  </>
                )}
              </div>
            </div>
            {answerMap[viewName] && (
              <button
                className="btn-secondary btn-sm"
                onClick={() => downloadAnswerXlsxForFile(viewName)}
                disabled={!viewLeft && !viewRight}
                title="정답 xlsx 에 AI 모델 컬럼 추가해서 다운로드"
              >
                📥 정답 xlsx + AI
              </button>
            )}
          </div>
          <SingleFileMatrix
            left={viewLeft}
            right={viewRight}
            leftLabel={leftLabel}
            rightLabel={rightLabel}
            manualSheet={answerMap[viewName] || null}
          />
        </div>
      ) : (
        <div className="card card-padded">
          <div className="empty-state">
            <div className="empty-state-title">배치 결과 없음</div>
            <div className="empty-state-desc">
              transcript 파일을 업로드하고 ▶ 배치 실행을 눌러 평가를 시작하세요.
            </div>
          </div>
        </div>
      )}

      {/* 전체 요약 테이블 */}
      {Object.keys(batchResults).length > 0 && (
        <div className="card card-padded">
          <h3 className="text-[14px] font-semibold mb-3">전체 요약 — 총점 비교</h3>
          <div className="overflow-auto max-h-[400px] rounded-[var(--radius-sm)] border border-[var(--border)]">
            <table className="w-full text-[12px] border-collapse">
              <thead className="sticky top-0 bg-[var(--surface-sunken)]">
                <tr>
                  <th className="px-3 py-2 text-left border-b border-[var(--border)]">파일</th>
                  <th className="px-3 py-2 text-center border-b border-[var(--border)]">
                    {leftLabel}
                  </th>
                  <th className="px-3 py-2 text-center border-b border-[var(--border)]">
                    {rightLabel}
                  </th>
                  <th className="px-3 py-2 text-center border-b border-[var(--border)]">차이</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(batchResults).map(([fname, entry]) => {
                  const l = totalFor(entry[leftLabel] || null);
                  const r = totalFor(entry[rightLabel] || null);
                  const diff = l != null && r != null ? l - r : null;
                  const diffAbs = Math.abs(diff ?? 0);
                  const emphasis = diffAbs >= 5 ? "danger" : diffAbs >= 2 ? "warn" : "neutral";
                  return (
                    <tr
                      key={fname}
                      className={`border-b border-[var(--border-subtle)] hover:bg-[var(--surface-hover)] cursor-pointer ${
                        selectedFileName === fname ? "bg-[var(--accent-bg)]" : ""
                      }`}
                      onClick={() => setSelectedFileName(fname)}
                    >
                      <td className="px-3 py-1.5 max-w-[280px] overflow-hidden text-ellipsis whitespace-nowrap">
                        {fname}
                      </td>
                      <td className="px-3 py-1.5 text-center font-mono font-semibold">
                        {l ?? "—"}
                      </td>
                      <td className="px-3 py-1.5 text-center font-mono font-semibold">
                        {r ?? "—"}
                      </td>
                      <td className="px-3 py-1.5 text-center">
                        {diff != null ? (
                          <span className={`badge badge-${emphasis}`}>
                            {diff > 0 ? "+" : ""}
                            {diff}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function ProgressLine({
  label,
  done,
  total,
  current,
  isRunning,
  elapsed,
}: {
  label: string;
  done: number;
  total: number;
  current: string;
  isRunning: boolean;
  elapsed: number;
}) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] p-3">
      <div className="flex justify-between items-center mb-1.5">
        <span className="text-[12px] font-semibold">{label}</span>
        <span className="text-[11px] font-mono text-[var(--ink-muted)]">
          {done} / {total}
          {isRunning && <span className="ml-2 text-[var(--accent)]">{elapsed.toFixed(1)}s</span>}
        </span>
      </div>
      <div className="relative h-2 rounded-[var(--radius-pill)] bg-[var(--surface-sunken)] overflow-hidden">
        <div
          className="absolute top-0 left-0 h-full transition-[width] duration-300"
          style={{
            width: `${pct}%`,
            background: isRunning ? "var(--accent)" : "var(--success)",
          }}
        />
      </div>
      {current && (
        <div className="mt-1.5 text-[10.5px] text-[var(--ink-muted)] truncate">
          현재: {current}
        </div>
      )}
    </div>
  );
}

function SingleFileMatrix({
  left,
  right,
  leftLabel,
  rightLabel,
  manualSheet,
}: {
  left: EvaluationResult | null;
  right: EvaluationResult | null;
  leftLabel: string;
  rightLabel: string;
  manualSheet?: ManualSheet | null;
}) {
  const leftItems = flatItems(left);
  const rightItems = flatItems(right);
  const byNum = new Map<
    number,
    {
      item_number: number;
      item_name: string;
      max_score: number;
      left: number | null;
      right: number | null;
      manual: number | null;
    }
  >();
  const upsertAi = (it: { item_number: number; item?: string; item_name?: string; max_score: number; score?: number | null }, side: "left" | "right") => {
    const existing = byNum.get(it.item_number) || {
      item_number: it.item_number,
      item_name: it.item || it.item_name || `#${it.item_number}`,
      max_score: it.max_score,
      left: null,
      right: null,
      manual: null,
    };
    existing[side] = it.score ?? null;
    if (!existing.item_name && (it.item || it.item_name)) {
      existing.item_name = it.item || it.item_name || `#${it.item_number}`;
    }
    byNum.set(it.item_number, existing);
  };
  leftItems.forEach((it) => upsertAi(it, "left"));
  rightItems.forEach((it) => upsertAi(it, "right"));
  if (manualSheet) {
    manualSheet.rows.forEach((row) => {
      const existing = byNum.get(row.no) || {
        item_number: row.no,
        item_name: row.item,
        max_score: row.max_score ?? 0,
        left: null,
        right: null,
        manual: null,
      };
      existing.manual = row.qa_score;
      if (!existing.item_name) existing.item_name = row.item;
      byNum.set(row.no, existing);
    });
  }
  const rows = Array.from(byNum.values()).sort((a, b) => a.item_number - b.item_number);

  if (rows.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-desc">이 파일의 평가 결과가 아직 없습니다.</div>
      </div>
    );
  }
  const hasManual = !!manualSheet;
  return (
    <div className="overflow-auto max-h-[500px] rounded-[var(--radius-sm)] border border-[var(--border)]">
      <table className="w-full text-[12px] border-collapse">
        <thead className="sticky top-0 bg-[var(--surface-sunken)]">
          <tr>
            <th className="px-3 py-2 text-left border-b border-[var(--border)] w-12">#</th>
            <th className="px-3 py-2 text-left border-b border-[var(--border)]">항목</th>
            <th className="px-3 py-2 text-center border-b border-[var(--border)] w-14">배점</th>
            {hasManual && (
              <th className="px-3 py-2 text-center border-b border-[var(--border)] bg-emerald-50 dark:bg-emerald-900/20">
                사람 QA
              </th>
            )}
            <th className="px-3 py-2 text-center border-b border-[var(--border)]">{leftLabel}</th>
            <th className="px-3 py-2 text-center border-b border-[var(--border)]">{rightLabel}</th>
            <th className="px-3 py-2 text-center border-b border-[var(--border)] w-20">L−R</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const diff = (r.left ?? 0) - (r.right ?? 0);
            const diffAbs = Math.abs(diff);
            const emphasis = diffAbs >= 3 ? "danger" : diffAbs >= 1 ? "warn" : "neutral";
            return (
              <tr
                key={r.item_number}
                className="border-b border-[var(--border-subtle)] hover:bg-[var(--surface-hover)]"
              >
                <td className="px-3 py-1.5 text-[var(--ink-muted)]">{r.item_number}</td>
                <td className="px-3 py-1.5">{r.item_name}</td>
                <td className="px-3 py-1.5 text-center text-[var(--ink-muted)]">{r.max_score}</td>
                {hasManual && (
                  <td className="px-3 py-1.5 text-center font-mono font-semibold bg-emerald-50/50 dark:bg-emerald-900/10">
                    {r.manual ?? "—"}
                  </td>
                )}
                <td className="px-3 py-1.5 text-center font-mono font-semibold">
                  {r.left ?? "—"}
                </td>
                <td className="px-3 py-1.5 text-center font-mono font-semibold">
                  {r.right ?? "—"}
                </td>
                <td className="px-3 py-1.5 text-center">
                  {r.left != null || r.right != null ? (
                    <span className={`badge badge-${emphasis}`}>
                      {diff > 0 ? "+" : ""}
                      {diff}
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default MatrixPanel;
