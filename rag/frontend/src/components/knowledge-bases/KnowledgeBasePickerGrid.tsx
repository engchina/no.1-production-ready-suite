"use client";

import { useMemo } from "react";

import { MultiSelectCombobox } from "@/components/ui/multi-select-combobox";
import { DEFAULT_KNOWLEDGE_BASE_NAME, type KnowledgeBaseSummary } from "@/lib/api";
import { t } from "@/lib/i18n";

/**
 * 知識ベースの複数選択コンボボックス。
 *
 * 選択済みチップ + 検索付きリストの共通 UI を使い、アップロード/検索/評価/業務ビュー
 * で同じ操作感に揃える。
 */
export function KnowledgeBasePickerGrid({
  items,
  selectedIds,
  onChange,
  disabled = false,
  ariaLabel,
}: {
  items: KnowledgeBaseSummary[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  ariaLabel: string;
}) {
  const topId = useMemo(() => {
    const top = items.reduce<KnowledgeBaseSummary | null>(
      (best, item) => (!best || item.document_count > best.document_count ? item : best),
      null
    );
    return items.length > 1 && top && top.document_count > 0 ? top.id : null;
  }, [items]);

  return (
    <MultiSelectCombobox
      items={items}
      selectedIds={selectedIds}
      onChange={onChange}
      disabled={disabled}
      ariaLabel={ariaLabel}
      getId={(kb) => kb.id}
      getName={(kb) => kb.name}
      getMetaText={(kb) => t("knowledgeBasePicker.documentCount", { count: kb.document_count })}
      sortItems={sortKnowledgeBases}
      isEmptyItem={(kb) =>
        kb.document_count === 0 && kb.name !== DEFAULT_KNOWLEDGE_BASE_NAME
      }
      getOptionBadge={(kb) => (kb.id === topId ? t("knowledgeBasePicker.mostDocs") : null)}
      strings={{
        addPlaceholder: t("knowledgeBasePicker.addPlaceholder"),
        toggleListAria: t("knowledgeBasePicker.toggleListAria"),
        removeChip: (name) => t("knowledgeBasePicker.removeChip", { name }),
        count: (shown, total) => t("knowledgeBasePicker.count", { shown, total }),
        noMatch: (query) => t("knowledgeBasePicker.noMatch", { query }),
        emptyList: t("knowledgeBasePicker.emptyList"),
        selectedCount: (count) => t("knowledgeBasePicker.selectedCount", { count }),
        selectAllVisible: t("knowledgeBasePicker.selectAllVisible"),
        clear: t("knowledgeBasePicker.clear"),
        hideEmpty: t("knowledgeBasePicker.hideEmpty"),
        hiddenEmptyCount: (count) => t("knowledgeBasePicker.hiddenEmptyCount", { count }),
      }}
    />
  );
}

function sortKnowledgeBases(items: KnowledgeBaseSummary[]) {
  return [...items].sort((a, b) => {
    if (a.name === DEFAULT_KNOWLEDGE_BASE_NAME) return -1;
    if (b.name === DEFAULT_KNOWLEDGE_BASE_NAME) return 1;
    if (b.document_count !== a.document_count) {
      return b.document_count - a.document_count;
    }
    return a.name.localeCompare(b.name, "ja");
  });
}
