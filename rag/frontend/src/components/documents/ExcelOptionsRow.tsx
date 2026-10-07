"use client";

import { useState } from "react";

import { SelectField, TextField, ToggleChip, type SelectFieldOption } from "@engchina/production-ready-ui";
import type { ExcelOptions } from "@/lib/api";
import { t } from "@/lib/i18n";

/** 前処理 excel_to_json の選択肢の既定（backend の `ExcelOptions` と同じ）。 */
export const DEFAULT_EXCEL_OPTIONS: ExcelOptions = {
  mode: "auto",
  header_row: null,
  header_row_count: 1,
  sheets: [],
  exclude_sheets: [],
  include_hidden_sheets: false,
  exclude_columns: [],
};

const MODE_OPTIONS: SelectFieldOption<ExcelOptions["mode"]>[] = [
  { value: "auto", label: t("documents.excelOptions.mode.auto") },
  { value: "table", label: t("documents.excelOptions.mode.table") },
  { value: "procedure", label: t("documents.excelOptions.mode.procedure") },
];

const HEADER_ROW_COUNT_OPTIONS: SelectFieldOption<string>[] = [1, 2, 3, 4, 5].map((count) => ({
  value: String(count),
  label: t("documents.excelOptions.headerRowCountValue", { count }),
}));

/** 「、」「,」・改行で区切った名前の一覧（空は除く）。 */
export function parseNameList(text: string): string[] {
  return text
    .split(/[,，、\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/** 選択肢の要約（継承の値の表示）。 */
export function excelOptionsSummary(options: ExcelOptions | null | undefined): string {
  const value = options ?? DEFAULT_EXCEL_OPTIONS;
  const parts = [
    MODE_OPTIONS.find((option) => option.value === value.mode)?.label ?? value.mode,
    value.header_row
      ? t("documents.excelOptions.summary.headerRow", { row: value.header_row })
      : t("documents.excelOptions.summary.headerDetected"),
  ];
  if (value.sheets.length) parts.push(t("documents.excelOptions.summary.sheets", { names: value.sheets.join("、") }));
  if (value.exclude_columns.length) {
    parts.push(t("documents.excelOptions.summary.excludeColumns", { names: value.exclude_columns.join("、") }));
  }
  return parts.join(" · ");
}

/**
 * 文書レシピの「Excel の読み方」（前処理が excel_to_json のときだけ出す。#1221）。
 * 継承（全体の既定に従う）か上書きかを選び、上書きのときに読み方・表頭・シート・列を指定する。
 */
export function ExcelOptionsRow({
  documentId,
  value,
  effectiveValue,
  disabled,
  onChange,
}: {
  documentId: string;
  value: ExcelOptions | null;
  effectiveValue: ExcelOptions | null;
  disabled: boolean;
  onChange: (value: ExcelOptions | null) => void;
}) {
  const overriding = value !== null;
  const current = value ?? effectiveValue ?? DEFAULT_EXCEL_OPTIONS;
  // 名前の一覧は入力の途中（末尾の「、」など）を保つため、文字列のまま持つ。
  const [lists, setLists] = useState(() => ({
    sheets: current.sheets.join("、"),
    exclude_sheets: current.exclude_sheets.join("、"),
    exclude_columns: current.exclude_columns.join("、"),
  }));
  const [headerRowText, setHeaderRowText] = useState(current.header_row ? String(current.header_row) : "");
  const labelId = `document-excel-options-${documentId}`;
  const update = (patch: Partial<ExcelOptions>) => onChange({ ...current, ...patch });
  const updateList = (key: "sheets" | "exclude_sheets" | "exclude_columns", text: string) => {
    setLists((previous) => ({ ...previous, [key]: text }));
    update({ [key]: parseNameList(text) });
  };

  return (
    <div
      data-config-field="excel_options"
      data-testid="document-excel-options"
      className="space-y-2 rounded-lg border border-border bg-surface p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span id={labelId} className="text-sm font-medium text-fg">
          {t("documents.excelOptions.title")}
        </span>
        <div className="flex flex-wrap gap-1" role="group" aria-labelledby={labelId}>
          <ToggleChip selected={!overriding} disabled={disabled} onClick={() => onChange(null)}>
            {t("knowledgeBases.adapter.inherit")}
          </ToggleChip>
          <ToggleChip
            selected={overriding}
            disabled={disabled}
            onClick={() => !overriding && onChange({ ...current })}
          >
            {t("knowledgeBases.adapter.override")}
          </ToggleChip>
        </div>
      </div>
      {overriding ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <SelectField
            id={`${labelId}-mode`}
            label={t("documents.excelOptions.mode")}
            helper={t("documents.excelOptions.mode.hint")}
            value={current.mode}
            options={MODE_OPTIONS}
            disabled={disabled}
            onValueChange={(mode) => update({ mode })}
          />
          <TextField
            id={`${labelId}-header-row`}
            label={t("documents.excelOptions.headerRow")}
            helper={t("documents.excelOptions.headerRow.hint")}
            type="number"
            inputMode="numeric"
            min={1}
            max={1000}
            value={headerRowText}
            disabled={disabled}
            onChange={(event) => {
              const text = event.target.value;
              setHeaderRowText(text);
              const row = Number.parseInt(text, 10);
              update({ header_row: Number.isFinite(row) && row >= 1 && row <= 1000 ? row : null });
            }}
          />
          <SelectField
            id={`${labelId}-header-row-count`}
            label={t("documents.excelOptions.headerRowCount")}
            value={String(current.header_row_count)}
            options={HEADER_ROW_COUNT_OPTIONS}
            disabled={disabled}
            onValueChange={(count) => update({ header_row_count: Number(count) })}
          />
          <TextField
            id={`${labelId}-sheets`}
            label={t("documents.excelOptions.sheets")}
            helper={t("documents.excelOptions.namesHint")}
            value={lists.sheets}
            disabled={disabled}
            onChange={(event) => updateList("sheets", event.target.value)}
          />
          <TextField
            id={`${labelId}-exclude-sheets`}
            label={t("documents.excelOptions.excludeSheets")}
            helper={t("documents.excelOptions.namesHint")}
            value={lists.exclude_sheets}
            disabled={disabled}
            onChange={(event) => updateList("exclude_sheets", event.target.value)}
          />
          <TextField
            id={`${labelId}-exclude-columns`}
            label={t("documents.excelOptions.excludeColumns")}
            helper={t("documents.excelOptions.excludeColumns.hint")}
            value={lists.exclude_columns}
            disabled={disabled}
            onChange={(event) => updateList("exclude_columns", event.target.value)}
          />
          <div className="space-y-1 sm:col-span-2">
            <span id={`${labelId}-hidden`} className="text-sm font-medium text-fg">
              {t("documents.excelOptions.hiddenSheets")}
            </span>
            <div className="flex flex-wrap gap-1" role="group" aria-labelledby={`${labelId}-hidden`}>
              <ToggleChip
                selected={!current.include_hidden_sheets}
                disabled={disabled}
                onClick={() => update({ include_hidden_sheets: false })}
              >
                {t("documents.excelOptions.hiddenSheets.skip")}
              </ToggleChip>
              <ToggleChip
                selected={current.include_hidden_sheets}
                disabled={disabled}
                onClick={() => update({ include_hidden_sheets: true })}
              >
                {t("documents.excelOptions.hiddenSheets.read")}
              </ToggleChip>
            </div>
          </div>
        </div>
      ) : (
        <p className="text-xs text-fg-muted" data-testid="document-excel-options-inherited">
          {t("documents.excelOptions.inherited", { summary: excelOptionsSummary(effectiveValue) })}
        </p>
      )}
    </div>
  );
}
