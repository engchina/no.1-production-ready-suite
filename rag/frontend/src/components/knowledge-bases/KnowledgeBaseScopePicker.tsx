"use client";

import { Database } from "lucide-react";
import { useId, useMemo, useState } from "react";

import { KnowledgeBasePickerGrid } from "@/components/knowledge-bases/KnowledgeBasePickerGrid";
import { Banner, TimedLoadingState, Skeleton } from "@engchina/production-ready-ui";
import { ApiError } from "@/lib/api";
import { t } from "@/lib/i18n";
import {
  type KnowledgeBaseRefLike,
  type KnowledgeBaseSelectionHealth,
  resolveKnowledgeBaseSelection,
} from "@/lib/knowledge-base-refs";
import { useKnowledgeBasesByIds, useKnowledgeBaseSearch } from "@/lib/queries";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { cn } from "@/lib/utils";

// 入力のたびに一覧 API を呼ばないよう、打ち終わってから検索する。
const SEARCH_DEBOUNCE_MS = 300;

/**
 * 選択済みの KB を ID で引き、名前・状態（アーカイブ済み / 見つからない）を解決する（#302）。
 * `known` は画面が既に持つ参照（業務ビュー詳細の参照 KB など。利用者の KB 範囲の外も含む）。
 */
export function useKnowledgeBaseSelectionHealth(
  ids: string[],
  {
    known,
    knownMissingIds,
  }: { known?: readonly KnowledgeBaseRefLike[]; knownMissingIds?: readonly string[] } = {}
): KnowledgeBaseSelectionHealth {
  const lookup = useKnowledgeBasesByIds(ids);
  const found = lookup.data?.items;
  // 別の ID 集合の結果を出している間（placeholder）と失敗時は、見つからないと判定しない。
  const lookupSettled = lookup.isSuccess && !lookup.isPlaceholderData;
  return useMemo(
    () =>
      resolveKnowledgeBaseSelection({
        ids,
        found,
        known,
        knownMissingIds,
        lookupSettled,
        missingName: (id) => t("knowledgeBasePicker.missingName", { id }),
      }),
    [ids, found, known, knownMissingIds, lookupSettled]
  );
}

/**
 * 検索・評価・業務ビュー・文書詳細で使うナレッジベースの複数選択スコープ。
 * 候補はサーバー側で名前・説明を検索し、「さらに表示」で次のページを取る（件数の上限なし。#302）。
 */
export function KnowledgeBaseScopePicker({
  selectedIds,
  onChange,
  disabled = false,
  label = t("knowledgeBaseScope.label"),
  helper = t("knowledgeBaseScope.helper"),
  emptySelectionText = t("knowledgeBaseScope.all"),
  className,
  knownKnowledgeBases,
  knownMissingIds,
}: {
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  label?: string;
  helper?: string;
  emptySelectionText?: string;
  className?: string;
  /** 画面が既に持つ選択済みの参照（範囲外の KB の名前もチップに出すため）。 */
  knownKnowledgeBases?: readonly KnowledgeBaseRefLike[];
  knownMissingIds?: readonly string[];
}) {
  const labelId = useId();
  const [filter, setFilter] = useState("");
  const q = useDebouncedValue(filter.trim(), SEARCH_DEBOUNCE_MS);
  const search = useKnowledgeBaseSearch({ status: "ACTIVE", q: q || undefined });
  const items = useMemo(
    () => search.data?.pages.flatMap((page) => page.items) ?? [],
    [search.data]
  );
  const total = search.data?.pages[0]?.total ?? 0;
  const selection = useKnowledgeBaseSelectionHealth(selectedIds, {
    known: knownKnowledgeBases,
    knownMissingIds,
  });
  const searching = filter.trim() !== q || (search.isPlaceholderData && search.isFetching);
  // 検索語なしで 1 件も無いときだけ「有効なナレッジベースがありません」（検索の 0 件は一覧内で示す）。
  const noKnowledgeBases = search.isSuccess && !q && !filter.trim() && total === 0;

  return (
    <div className={cn("space-y-2", className)}>
      <div>
        <p id={labelId} className="flex items-center gap-1.5 text-xs font-medium text-fg">
          <Database size={14} className="text-accent-fg" aria-hidden />
          {label}
        </p>
        <p className="mt-1 text-xs text-fg-muted">{helper}</p>
      </div>

      {search.isError ? (
        <Banner severity="warning" title={t("knowledgeBaseScope.loadWarning")}>
          <p>
            {search.error instanceof ApiError
              ? search.error.message
              : t("knowledgeBaseScope.loadWarningHint")}
          </p>
        </Banner>
      ) : search.isPending ? (
        <TimedLoadingState
          label={t("knowledgeBaseScope.loading")}
          operationKey="knowledge-base-scope-load"
          framed={false}
          testId="knowledge-base-scope-loading"
        >
          <Skeleton className="h-[var(--button-height-md)] w-full" />
        </TimedLoadingState>
      ) : noKnowledgeBases && selectedIds.length === 0 ? (
        <p className="rounded-md border border-border bg-surface-sunken px-3 py-2 text-xs text-fg-muted">
          {t("knowledgeBaseScope.empty")}
        </p>
      ) : (
        <>
          <KnowledgeBasePickerGrid
            items={items}
            selectedIds={selectedIds}
            onChange={onChange}
            disabled={disabled}
            ariaLabel={label}
            selectedItems={selection.items}
            remote={{
              onFilterChange: setFilter,
              total,
              hasMore: Boolean(search.hasNextPage),
              loadingMore: search.isFetchingNextPage,
              searching,
              onLoadMore: () => void search.fetchNextPage(),
              loadMoreLabel: t("knowledgeBasePicker.loadMore"),
              searchingLabel: t("knowledgeBasePicker.searching"),
            }}
          />
          <p className="text-xs text-fg-muted">
            {selectedIds.length > 0
              ? t("knowledgeBaseScope.selected", { count: selectedIds.length })
              : emptySelectionText}
          </p>
        </>
      )}
    </div>
  );
}
