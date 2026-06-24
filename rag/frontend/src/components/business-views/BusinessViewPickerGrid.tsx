"use client";

import { useMemo } from "react";

import { MultiSelectCombobox } from "@/components/ui/multi-select-combobox";
import type { BusinessViewSummary } from "@/lib/api";
import { t } from "@/lib/i18n";

/** 業務ビューの複数選択コンボボックス。 */
export function BusinessViewPickerGrid({
  items,
  selectedIds,
  onChange,
  disabled = false,
  ariaLabel,
}: {
  items: BusinessViewSummary[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  ariaLabel: string;
}) {
  const primaryId = selectedIds[0] ?? null;
  const sortedItems = useMemo(() => sortBusinessViews(items), [items]);

  return (
    <MultiSelectCombobox
      items={sortedItems}
      selectedIds={selectedIds}
      onChange={onChange}
      disabled={disabled}
      ariaLabel={ariaLabel}
      getId={(view) => view.id}
      getName={(view) => view.name}
      getSearchText={(view) => `${view.name} ${view.description ?? ""}`}
      getMetaText={(view) =>
        t("businessViewPicker.knowledgeBaseCount", {
          count: view.knowledge_base_count,
        })
      }
      isEmptyItem={(view) => view.knowledge_base_count === 0}
      getChipBadge={(view) =>
        view.id === primaryId ? t("businessViewPicker.primary") : null
      }
      getOptionBadge={(view) =>
        view.id === primaryId ? t("businessViewPicker.primary") : null
      }
      strings={{
        addPlaceholder: t("businessViewPicker.addPlaceholder"),
        toggleListAria: t("businessViewPicker.toggleListAria"),
        removeChip: (name) => t("businessViewPicker.removeChip", { name }),
        count: (shown, total) => t("businessViewPicker.count", { shown, total }),
        noMatch: (query) => t("businessViewPicker.noMatch", { query }),
        emptyList: t("businessViewPicker.emptyList"),
        selectedCount: (count) => t("businessViewPicker.selectedCount", { count }),
        selectAllVisible: t("businessViewPicker.selectAllVisible"),
        clear: t("businessViewPicker.clear"),
        hideEmpty: t("businessViewPicker.hideEmpty"),
        hiddenEmptyCount: (count) => t("businessViewPicker.hiddenEmptyCount", { count }),
      }}
      triggerClassName="bg-background focus-within:bg-background"
    />
  );
}

function sortBusinessViews(items: BusinessViewSummary[]) {
  return [...items].sort((a, b) => {
    if (b.knowledge_base_count !== a.knowledge_base_count) {
      return b.knowledge_base_count - a.knowledge_base_count;
    }
    return a.name.localeCompare(b.name, "ja");
  });
}
