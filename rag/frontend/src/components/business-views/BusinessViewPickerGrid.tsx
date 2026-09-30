"use client";

import { SearchableMultiSelect, type SearchableSelectOption } from "@engchina/production-ready-ui";
import { useMemo } from "react";

import { DEFAULT_BUSINESS_VIEW_NAME, type BusinessViewSummary } from "@/lib/api";
import { t } from "@/lib/i18n";

/**
 * 業務ビューの複数選択（検索 ＋ 候補の一覧 ＋ 選択済みの chip。共有の SearchableMultiSelect。#578）。
 * 業務ビューは件数が少ない前提で、全件を画面側で絞り込む。
 */
export function BusinessViewPickerGrid({
  id,
  labelledBy,
  items,
  selectedIds,
  onChange,
  disabled = false,
  ariaLabel,
  required = false,
  invalid = false,
  describedBy,
}: {
  /** 検索欄の id（外の FieldLabel の htmlFor と結ぶ）。 */
  id: string;
  /** 外に出している見出し（FieldLabel）の id。 */
  labelledBy?: string;
  items: BusinessViewSummary[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  ariaLabel: string;
  required?: boolean;
  invalid?: boolean;
  describedBy?: string;
}) {
  const primaryId = selectedIds[0] ?? null;
  const options = useMemo<SearchableSelectOption[]>(
    () =>
      sortBusinessViews(items).map((view) => ({
        value: view.id,
        label: view.name,
        searchText: `${view.name} ${view.description ?? ""}`,
        meta: t("businessViewPicker.knowledgeBaseCount", { count: view.knowledge_base_count }),
        badge: view.id === primaryId ? t("businessViewPicker.primary") : undefined,
        hideable: view.knowledge_base_count === 0,
      })),
    [items, primaryId]
  );

  return (
    <SearchableMultiSelect
      id={id}
      label={ariaLabel}
      labelHidden
      labelledBy={labelledBy}
      required={required}
      invalid={invalid}
      describedBy={describedBy}
      options={options}
      value={selectedIds}
      onValueChange={onChange}
      disabled={disabled}
      labels={{
        searchPlaceholder: t("businessViewPicker.addPlaceholder"),
        clearSearch: t("common.clearSearch"),
        toggleList: t("businessViewPicker.toggleListAria"),
        removeChip: (name) => t("businessViewPicker.removeChip", { name }),
        selectedList: () => t("businessViewPicker.selectedList"),
        count: (shown, total) => t("businessViewPicker.count", { shown, total }),
        noMatch: (query) => t("businessViewPicker.noMatch", { query }),
        empty: t("businessViewPicker.emptyList"),
        selectedCount: (count) => t("businessViewPicker.selectedCount", { count }),
        selectAllVisible: t("businessViewPicker.selectAllVisible"),
        clear: t("businessViewPicker.clear"),
        done: t("businessViewPicker.done"),
        hideHideable: t("businessViewPicker.hideEmpty"),
        hiddenCount: (count) => t("businessViewPicker.hiddenEmptyCount", { count }),
      }}
    />
  );
}

function sortBusinessViews(items: BusinessViewSummary[]) {
  return [...items].sort((a, b) => {
    if (a.name === DEFAULT_BUSINESS_VIEW_NAME) return -1;
    if (b.name === DEFAULT_BUSINESS_VIEW_NAME) return 1;
    if (b.knowledge_base_count !== a.knowledge_base_count) {
      return b.knowledge_base_count - a.knowledge_base_count;
    }
    return a.name.localeCompare(b.name, "ja");
  });
}
