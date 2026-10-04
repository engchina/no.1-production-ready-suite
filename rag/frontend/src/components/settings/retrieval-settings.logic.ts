/**
 * 検索方法の画面（回答の記録の保存期間・質問履歴）の検証と選択肢（#1002）。
 * 範囲は backend の `QueryHistorySettingsData` / `AnswerRecordSettingsUpdate` と同じにする。
 */

import type { SelectFieldOption } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { numberRangeError } from "@/lib/required-fields";

/** 質問履歴の除外する語の上限（backend の `blocklist` の `max_length`）。 */
export const QUERY_HISTORY_BLOCKLIST_MAX = 200;

export const QUERY_HISTORY_FIELD_IDS = {
  minCount: "query-history-min-count",
  suggestionLimit: "query-history-limit",
  blocklist: "query-history-blocklist",
} as const;

const RETENTION_PRESET_DAYS = [30, 90, 180, 365] as const;

/** 保存期間の選択肢。保存値が既定の選択肢に無い（.env で決めた）ときは「N 日」として足す。 */
export function retentionOptions(savedDays: number): SelectFieldOption<string>[] {
  const options: SelectFieldOption<string>[] = [
    ...RETENTION_PRESET_DAYS.map((days) => ({
      value: String(days),
      label: t("settings.answerRecords.days", { days }),
    })),
    { value: "0", label: t("settings.answerRecords.unlimited") },
  ];
  if (!options.some((option) => option.value === String(savedDays))) {
    options.push({ value: String(savedDays), label: t("settings.answerRecords.days", { days: savedDays }) });
  }
  return options;
}

/**
 * 保存期間を短くするか（0 = 無期限）。短くすると backend は保存の時に期限を過ぎた記録を削除するため、
 * 画面は保存の前に確認する。
 */
export function shortensRetention(savedDays: number, nextDays: number): boolean {
  if (nextDays <= 0) return false;
  return savedDays <= 0 || nextDays < savedDays;
}

/** 除外する語の欄（1 行 1 語）を、backend と同じく前後の空白を除き、空と重複を除いた一覧にする。 */
export function blocklistFromText(text: string): string[] {
  return Array.from(new Set(text.split("\n").map((item) => item.trim()).filter(Boolean)));
}

/** 数値の欄の文字列を数値にする。空は NaN（0 として扱わない）。 */
function parseNumberInput(value: string): number {
  return value.trim() === "" ? Number.NaN : Number(value);
}

export type QueryHistoryFieldErrors = {
  minCount?: string | null;
  suggestionLimit?: string | null;
  blocklist?: string | null;
};

/** 質問履歴の欄ごとの検証。規則は backend の `QueryHistorySettingsData` と同じ。 */
export function queryHistoryErrors(draft: {
  minCount: string;
  suggestionLimit: string;
  blocklistText: string;
}): QueryHistoryFieldErrors {
  const blocklistCount = blocklistFromText(draft.blocklistText).length;
  return {
    minCount: numberRangeError(parseNumberInput(draft.minCount), {
      label: t("settings.queryHistory.minCount"),
      min: 1,
      max: 1000,
    }),
    suggestionLimit: numberRangeError(parseNumberInput(draft.suggestionLimit), {
      label: t("settings.queryHistory.limit"),
      min: 1,
      max: 20,
    }),
    blocklist:
      blocklistCount > QUERY_HISTORY_BLOCKLIST_MAX
        ? t("settings.queryHistory.blocklistTooMany", {
            max: QUERY_HISTORY_BLOCKLIST_MAX,
            count: blocklistCount,
          })
        : null,
  };
}
