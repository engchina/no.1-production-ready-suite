import { TextareaField, defaultTextareaCount } from "@production-ready/ui";

import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";

/** 追加の指示の長さの上限（backend の `QUESTION_SUFFIX_MAX_CHARS` / `ANSWER_INSTRUCTION_MAX_CHARS` と同じ値）。 */
export const QUESTION_SUFFIX_MAX_CHARS = 500;
export const ANSWER_INSTRUCTION_MAX_CHARS = 2000;

export const QUESTION_SUFFIX_FIELD_ID = "search-answer-profile-question-suffix";
export const ANSWER_INSTRUCTION_FIELD_ID = "search-answer-profile-answer-instruction";

/**
 * 上限を超えた欄のエラー（超えていなければ null）。貼り付けた文の後ろを黙って切らないよう、入力は止めずに
 * 欄の下で知らせ、保存を止める。数え方は欄の文字数の表示と同じ。
 */
export function extraInstructionLengthError(label: string, value: string, max: number): string | null {
  if (value.length <= max) return null;
  return t("searchAnswerProfiles.extraInstructions.tooLong", {
    label,
    max: formatNumber(max),
    count: formatNumber(value.length),
  });
}

/**
 * 追加の指示（#5848）。このプロファイルのすべての質問に足す文と、回答の書き方の指示に足す文。
 * どちらも空なら何も足さない。エラー（上限・保存で返った欄のエラー）は呼び出し側が決めて渡す。
 */
export function ExtraInstructionsFieldset({
  questionSuffix,
  answerInstruction,
  questionSuffixError,
  answerInstructionError,
  disabled,
  onChange,
}: {
  questionSuffix: string;
  answerInstruction: string;
  questionSuffixError: string | null;
  answerInstructionError: string | null;
  disabled: boolean;
  onChange: (patch: { question_suffix?: string; answer_instruction?: string }) => void;
}) {
  return (
    <fieldset
      className="space-y-3 rounded-lg border border-border p-4"
      data-testid="search-answer-profile-extra-instructions"
    >
      <legend className="px-1 text-sm font-semibold text-fg">
        {t("searchAnswerProfiles.extraInstructions.title")}
      </legend>
      <p className="text-xs text-fg-muted">{t("searchAnswerProfiles.extraInstructions.helper")}</p>
      <div className="space-y-4 rounded-lg border border-border bg-surface-sunken p-3">
        <TextareaField
          id={QUESTION_SUFFIX_FIELD_ID}
          rows={3}
          width="full"
          label={t("searchAnswerProfiles.extraInstructions.questionSuffix")}
          value={questionSuffix}
          onValueChange={(value) => onChange({ question_suffix: value })}
          placeholder={disabled ? undefined : t("searchAnswerProfiles.extraInstructions.questionSuffixPlaceholder")}
          helper={t("searchAnswerProfiles.extraInstructions.questionSuffixHelper")}
          error={questionSuffixError || undefined}
          showCount={(count) => defaultTextareaCount(count, QUESTION_SUFFIX_MAX_CHARS)}
          disabled={disabled}
        />
        <TextareaField
          id={ANSWER_INSTRUCTION_FIELD_ID}
          rows={6}
          width="full"
          label={t("searchAnswerProfiles.extraInstructions.answerInstruction")}
          value={answerInstruction}
          onValueChange={(value) => onChange({ answer_instruction: value })}
          placeholder={disabled ? undefined : t("searchAnswerProfiles.extraInstructions.answerInstructionPlaceholder")}
          helper={t("searchAnswerProfiles.extraInstructions.answerInstructionHelper")}
          error={answerInstructionError || undefined}
          showCount={(count) => defaultTextareaCount(count, ANSWER_INSTRUCTION_MAX_CHARS)}
          disabled={disabled}
        />
      </div>
    </fieldset>
  );
}
