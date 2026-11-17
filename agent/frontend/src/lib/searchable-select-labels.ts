import type { SearchableSelectLabels } from "@production-ready/ui";

import { t } from "./i18n";

/**
 * 共有の `SearchableSelectField` と `FilterSelect` の `searchable` に渡す文言（検索欄の placeholder と読み上げ名・
 * 検索語のクリア・件数・候補なし・検索中・続きの読み込み。#5266）。選択肢がデータから来る選択で使い、
 * 3 製品と system-settings で同じ文にする（i18n の `searchableSelect.*`）。画面ごとに言い換えるときは上書きする。
 */
export function searchableSelectLabels(): SearchableSelectLabels {
  return {
    searchPlaceholder: t("searchableSelect.searchPlaceholder"),
    searchLabel: (label) => t("searchableSelect.searchLabel", { label }),
    clearSearch: t("searchableSelect.clearSearch"),
    count: (shown, total) => t("searchableSelect.count", { shown, total }),
    noMatch: (query) => t("searchableSelect.noMatch", { query }),
    empty: t("searchableSelect.empty"),
    searching: t("searchableSelect.searching"),
    loadMore: t("searchableSelect.loadMore"),
  };
}
