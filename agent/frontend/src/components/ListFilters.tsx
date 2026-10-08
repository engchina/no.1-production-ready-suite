import type { ReactNode } from "react";
import { ClearActionButton, EmptyState, SearchField } from "@production-ready/ui";

import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { isString, useWorkspaceState, type WorkspaceField } from "@/lib/workspace-state";

/**
 * 一覧の絞り込みの検索（UX 契約 page-archetypes.md「一覧の絞り込みの検索（#535）」「一覧のツールバー（#600）」。#808）。
 * 全件を持つ一覧を画面側で絞る。検索語は作業状態（このタブの sessionStorage）に残し、変わったら 1 ページ目へ戻す
 * （`PagedDataTable` の `resetKey` に渡す）。debounce・IME・Enter は `SearchField` の 1 か所だけが持つ。
 */
export type ListSearchKey = WorkspaceField<"listSearch">;

export function useListSearch(key: ListSearchKey): [string, (next: string) => void] {
  const [query, setQuery] = useWorkspaceState("listSearch", key, "", isString);
  return [query, setQuery];
}

function normalize(value: string): string {
  return value.normalize("NFKC").toLowerCase();
}

/** 検索語（空白区切りの語をすべて含む）が、いずれかの欄に一致するか。大文字・小文字と全角・半角は区別しない。 */
export function matchesSearch(query: string, fields: ReadonlyArray<string | null | undefined>): boolean {
  const terms = normalize(query).split(/\s+/).filter(Boolean);
  if (!terms.length) return true;
  const haystack = normalize(fields.filter((field): field is string => Boolean(field)).join("\n"));
  return terms.every((term) => haystack.includes(term));
}

/** 一覧のツールバーの検索欄。ラベルは見せず（placeholder と同じ文言を読み上げる）、件数を読み上げる。 */
export function ListSearchField({
  id,
  label,
  value,
  onSearch,
  count,
}: {
  id: string;
  /** 「名前・ID で絞り込み」など、何で絞るかを書いた文言（i18n 済み）。 */
  label: string;
  value: string;
  onSearch: (next: string) => void;
  /** 絞り込んだ後の件数（検索語があるときだけ読み上げる）。 */
  count: number;
}) {
  return (
    <SearchField
      id={id}
      label={label}
      labelHidden
      placeholder={label}
      value={value}
      onSearch={onSearch}
      clearLabel={t("common.clearSearch")}
      resultCountLabel={t("common.searchResultCount", { count: formatNumber(count) })}
    />
  );
}

/** ツールバーの右の件数（「12 件」。絞り込み中は「3 / 12 件」）。 */
export function listCountLabel(shown: number, total: number): string {
  return shown === total
    ? t("list.count", { count: formatNumber(total) })
    : t("list.countFiltered", { shown: formatNumber(shown), total: formatNumber(total) });
}

/** 検索・絞り込みに一致する行が無いとき（データがそもそも無いときの空の状態とは文言を分ける）。 */
export function NoMatchState({
  title,
  onClear,
  clearLabel = t("common.clearSearch"),
}: {
  title: string;
  onClear: () => void;
  clearLabel?: string;
}) {
  return (
    <EmptyState
      title={title}
      hint={t("list.noMatchHint")}
      action={<ClearActionButton label={clearLabel} onClick={onClear} />}
    />
  );
}

/** 絞り込みのチップの群（`ToggleChip` を並べる。読み上げの名前を付ける）。 */
export function FilterChipGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1" role="group" aria-label={label}>
      {children}
    </div>
  );
}
