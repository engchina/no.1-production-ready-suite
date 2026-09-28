"use client";

import { useMemo } from "react";

import {
  MultiSelectCombobox,
  type MultiSelectComboboxRemote,
} from "@/components/ui/multi-select-combobox";
import { DEFAULT_KNOWLEDGE_BASE_NAME, type KnowledgeBaseSummary } from "@/lib/api";
import { t } from "@/lib/i18n";

/**
 * 選択肢・チップに出す KB。`missing` は選択済みの ID が見つからない（存在しない・範囲外）KB の
 * 仮の項目（チップから外せるように出す。#302）。
 */
export type KnowledgeBasePickerItem = Pick<
  KnowledgeBaseSummary,
  "id" | "name" | "status" | "document_count"
> & { missing?: boolean };

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
  remote,
  selectedItems,
}: {
  items: KnowledgeBasePickerItem[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  ariaLabel: string;
  /** サーバー側で検索・ページングするとき（#302）。「最多」の目印と空の KB の抑制は出さない。 */
  remote?: MultiSelectComboboxRemote;
  selectedItems?: KnowledgeBasePickerItem[];
}) {
  const topId = useMemo(() => {
    // 全件を持たないとき（サーバー側の検索）は「最多」を決められない。
    if (remote) return null;
    const top = items.reduce<KnowledgeBasePickerItem | null>(
      (best, item) => (!best || item.document_count > best.document_count ? item : best),
      null
    );
    return items.length > 1 && top && top.document_count > 0 ? top.id : null;
  }, [items, remote]);

  return (
    <MultiSelectCombobox
      items={items}
      selectedIds={selectedIds}
      onChange={onChange}
      disabled={disabled}
      ariaLabel={ariaLabel}
      getId={(kb) => kb.id}
      getName={(kb) => kb.name}
      getMetaText={(kb) =>
        kb.missing ? "" : t("knowledgeBasePicker.documentCount", { count: kb.document_count })
      }
      sortItems={sortKnowledgeBases}
      isEmptyItem={(kb) =>
        kb.document_count === 0 && kb.name !== DEFAULT_KNOWLEDGE_BASE_NAME
      }
      getOptionBadge={(kb) => (kb.id === topId ? t("knowledgeBasePicker.mostDocs") : null)}
      getChipBadge={knowledgeBaseChipBadge}
      remote={remote}
      selectedItems={selectedItems}
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

/** 検索対象にならない選択済みの KB は、チップに状態を文字で添える（色だけに頼らない）。 */
function knowledgeBaseChipBadge(kb: KnowledgeBasePickerItem): string | null {
  if (kb.missing) return t("knowledgeBasePicker.missingBadge");
  if (kb.status === "ARCHIVED") return t("knowledgeBasePicker.archivedBadge");
  return null;
}

function sortKnowledgeBases(items: KnowledgeBasePickerItem[]) {
  return [...items].sort((a, b) => {
    if (a.name === DEFAULT_KNOWLEDGE_BASE_NAME) return -1;
    if (b.name === DEFAULT_KNOWLEDGE_BASE_NAME) return 1;
    if (b.document_count !== a.document_count) {
      return b.document_count - a.document_count;
    }
    return a.name.localeCompare(b.name, "ja");
  });
}
