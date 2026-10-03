"use client";

import {
  SearchableSelectField,
  Skeleton,
  type SearchableSelectOption,
} from "@engchina/production-ready-ui";
import { Search } from "lucide-react";
import { useMemo } from "react";

import { DEFAULT_SEARCH_ANSWER_PROFILE_NAME, type SearchAnswerProfileSummary } from "@/lib/api";
import { t } from "@/lib/i18n";

/**
 * 対象の検索・回答プロファイルを 1 つ選ぶ欄（RAG 検索とチャットで同じ部品・同じ文言。#635）。
 *
 * 共有の SearchableSelectField（ボタン → 検索欄と候補の一覧）。検索・回答プロファイルは件数が少ない前提で、
 * 全件を画面側で名前と説明で絞り込む。候補の右端に参照 KB の件数を出し、参照 KB が 0 件の検索・回答プロファイルは
 * 後ろに並べる（選んだときの理由の表示と送信の抑止は画面側）。
 *
 * 幅は全幅にする。RAG 検索では直下の質問欄（全幅）と左右の端をそろえ、チャットでも同じ見た目にする。
 * 欄の下に説明文（helper）は出さない（毎回読む情報ではなく、縦の面積を取るため。#664）。
 */
export function SearchAnswerProfileSelect({
  id,
  items,
  value,
  onChange,
  disabled = false,
  error,
}: {
  id: string;
  items: SearchAnswerProfileSummary[];
  value: string | null;
  onChange: (id: string) => void;
  disabled?: boolean;
  /** 欄の直下に出す誤り（未選択・参照 KB なしなど）。 */
  error?: string;
}) {
  const options = useMemo<SearchableSelectOption[]>(
    () =>
      sortSearchAnswerProfiles(items).map((view) => ({
        value: view.id,
        label: view.name,
        searchText: `${view.name} ${view.description ?? ""}`,
        meta: t("searchAnswerProfileSelect.knowledgeBaseCount", { count: view.knowledge_base_count }),
      })),
    [items]
  );

  return (
    <SearchableSelectField
      id={id}
      label={t("searchAnswerProfiles.scope.label")}
      required
      value={value ?? ""}
      options={options}
      onValueChange={(next) => {
        if (!disabled) onChange(next);
      }}
      placeholder={t("searchAnswerProfileSelect.placeholder")}
      leadingIcon={Search}
      error={error || undefined}
      disabled={disabled}
      width="full"
      labels={{
        searchPlaceholder: t("searchAnswerProfileSelect.searchPlaceholder"),
        clearSearch: t("common.clearSearch"),
        count: (shown, total) => t("searchAnswerProfileSelect.count", { shown, total }),
        noMatch: (query) => t("searchAnswerProfileSelect.noMatch", { query }),
        empty: t("searchAnswerProfileSelect.emptyList"),
      }}
    />
  );
}

/** 検索・回答プロファイルを読み込んでいる間の欄の形（ラベル・欄）。RAG 検索とチャットで同じにする。 */
export function SearchAnswerProfileSelectSkeleton() {
  return (
    <div className="space-y-1.5" aria-hidden>
      <Skeleton className="h-4 w-32" />
      <Skeleton className="h-[var(--button-height-md)] w-full" />
    </div>
  );
}

function sortSearchAnswerProfiles(items: SearchAnswerProfileSummary[]) {
  return [...items].sort((a, b) => {
    if (a.name === DEFAULT_SEARCH_ANSWER_PROFILE_NAME) return -1;
    if (b.name === DEFAULT_SEARCH_ANSWER_PROFILE_NAME) return 1;
    if (b.knowledge_base_count !== a.knowledge_base_count) {
      return b.knowledge_base_count - a.knowledge_base_count;
    }
    return a.name.localeCompare(b.name, "ja");
  });
}
