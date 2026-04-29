// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useRef, useState } from "react";

import { useAppState } from "@/lib/AppStateContext";
import {
  parseManualXlsx,
  parseSheetFromWorkbook,
  buildManualPreview,
  type ManualSheet,
  type SheetPickerInfo,
} from "@/lib/manualEvalParser";

interface ManualEvalAttachProps {
  /** 전사 텍스트 — 상담 ID 자동 추출에 사용 */
  transcript?: string;
  /** disable 플래그 — 실행 중엔 차단 */
  disabled?: boolean;
}

/**
 * 평가 실행 패널용 "사람 QA 평가표 첨부" 버튼 + 미리보기.
 * 첨부 성공 시 `AppStateContext.manualEval` 에 저장 → Results/NodeDrawer/Matrix 가 자동 참조.
 */
export function ManualEvalAttach({ transcript, disabled = false }: ManualEvalAttachProps) {
  const { state, dispatch } = useAppState();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [error, setError] = useState<string>("");
  const [picker, setPicker] = useState<SheetPickerInfo | null>(null);
  const [pickerBuffer, setPickerBuffer] = useState<ArrayBuffer | null>(null);
  const [pickerFileName, setPickerFileName] = useState<string>("");
  const [showPreview, setShowPreview] = useState(false);

  const setManual = useCallback(
    (sheet: ManualSheet, fileName: string, buffer: ArrayBuffer) => {
      dispatch({
        type: "SET_MANUAL_EVAL",
        payload: { sheet, fileName, buffer },
      });
      setError("");
      setPicker(null);
      setPickerBuffer(null);
      setPickerFileName("");
    },
    [dispatch],
  );

  const clearAll = useCallback(() => {
    dispatch({ type: "CLEAR_MANUAL_EVAL" });
    setError("");
    setPicker(null);
    setPickerBuffer(null);
    setPickerFileName("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, [dispatch]);

  const onFileChange = useCallback(
    async (ev: React.ChangeEvent<HTMLInputElement>) => {
      const file = ev.target.files?.[0];
      ev.target.value = "";
      if (!file) return;
      if (!/\.xlsx?$/i.test(file.name)) {
        setError("xlsx/xls 파일만 지원됩니다.");
        return;
      }
      setError("");
      try {
        const buffer = await file.arrayBuffer();
        const outcome = parseManualXlsx(buffer, {
          fileName: file.name,
          transcript,
        });
        if (outcome.kind === "parsed") {
          setManual(outcome.sheet, file.name, buffer);
        } else {
          setPicker(outcome.info);
          setPickerBuffer(buffer);
          setPickerFileName(file.name);
        }
      } catch (err) {
        setError(`파일 읽기 실패: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [transcript, setManual],
  );

  const onPickSheet = useCallback(
    (sheetName: string) => {
      if (!picker || !pickerBuffer || !sheetName) return;
      try {
        const sheet = parseSheetFromWorkbook(picker.workbook, sheetName);
        setManual(sheet, pickerFileName, pickerBuffer);
      } catch (err) {
        setError(`탭 파싱 실패: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [picker, pickerBuffer, pickerFileName, setManual],
  );

  const sheet = state.manualEval;
  const preview = sheet ? buildManualPreview(sheet) : "";

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 flex-wrap">
        <input
          ref={fileInputRef}
          type="file"
          accept=".xlsx,.xls,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          disabled={disabled}
          onChange={onFileChange}
          className="hidden"
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-dashed border-[var(--accent)] bg-[var(--surface)] px-2.5 py-1 text-[12px] font-medium text-[var(--accent)] transition hover:bg-[var(--accent-bg)] disabled:opacity-50"
          title="STT 기반 통합 상담평가표 xlsx 파일을 첨부하여 AI 평가와 비교합니다."
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <polyline points="14 2 14 8 20 8"/>
            <line x1="9" y1="13" x2="15" y2="13"/>
            <line x1="9" y1="17" x2="15" y2="17"/>
          </svg>
          사람 QA 평가표 첨부
        </button>
        {sheet && state.manualEvalFileName && (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-medium text-emerald-700 border border-emerald-200 dark:bg-emerald-900/20 dark:text-emerald-300 dark:border-emerald-800">
            <span className="truncate max-w-[220px]" title={state.manualEvalFileName}>
              {state.manualEvalFileName}
            </span>
            <span className="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 dark:bg-emerald-800/40 dark:text-emerald-200 font-mono">
              {sheet.sheetId || "?"} · 총점 {sheet.total ?? "—"}/100
            </span>
            <button
              type="button"
              onClick={() => setShowPreview((v) => !v)}
              className="text-emerald-700 hover:text-emerald-900 underline-offset-2 hover:underline dark:text-emerald-300"
              title="미리보기 토글"
            >
              {showPreview ? "접기" : "보기"}
            </button>
            <button
              type="button"
              onClick={clearAll}
              className="ml-0.5 text-emerald-700 hover:text-red-600"
              aria-label="평가표 제거"
              title="평가표 제거"
            >
              ✕
            </button>
          </span>
        )}
      </div>

      {picker && (
        <div className="flex items-center gap-2 flex-wrap p-2.5 rounded-[var(--radius-sm)] bg-amber-50 border border-amber-300 dark:bg-amber-900/20 dark:border-amber-700">
          <span className="text-[12px] text-amber-900 dark:text-amber-200 font-medium">
            💡 {picker.targetId
              ? `상담 ID "${picker.targetId}" 자동 매칭 실패 — 탭을 직접 선택하세요:`
              : "상담 ID 추출 불가 — 사용할 탭을 선택하세요:"}
          </span>
          <select
            defaultValue=""
            onChange={(e) => onPickSheet(e.target.value)}
            className="rounded-[var(--radius-sm)] border border-amber-300 bg-white px-2 py-1 text-[12px] dark:bg-zinc-800 dark:border-amber-700"
          >
            <option value="" disabled>
              — 탭 선택 ({picker.sheetNames.length}개) —
            </option>
            {picker.sheetNames.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => {
              setPicker(null);
              setPickerBuffer(null);
              setPickerFileName("");
            }}
            className="text-[11px] text-amber-700 hover:text-red-600 ml-auto"
          >
            취소
          </button>
        </div>
      )}

      {error && (
        <div className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded-[var(--radius-sm)] px-2.5 py-1.5 dark:bg-red-900/20 dark:text-red-300 dark:border-red-800">
          {error}
        </div>
      )}

      {sheet && showPreview && preview && (
        <pre className="text-[11px] leading-relaxed whitespace-pre-wrap rounded-[var(--radius-sm)] bg-[var(--surface-muted)] p-2.5 max-h-48 overflow-y-auto font-mono text-[var(--ink-soft)]">
          {preview}
        </pre>
      )}
    </div>
  );
}

export default ManualEvalAttach;
