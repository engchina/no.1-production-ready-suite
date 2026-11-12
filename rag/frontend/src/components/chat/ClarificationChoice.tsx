import { ClarificationCard, type ClarificationBusyAction } from "@production-ready/ui";

import type { GuideConditionAnswer, ClarificationSuggestionData } from "@/lib/api";

/**
 * 回答前の確認（業務ガイドの条件の確認。#1238。3 製品共通の `ClarificationCard` に載せた。#1839）。選択肢は
 * 複数選択（チェック）か 1 つ（ラジオ）。「その他（自由入力）」は常に出す（`allow_other` は読まない）。
 * 確認を終えるのは「確認せずに回答」（確認を使わずに前提を示して回答する）だけで、カードに「確認をやめる」は
 * 置かない（質問を取り消すのは入力欄の「停止」。#2311）。
 */
export function ClarificationChoice({
  suggestion,
  busyAction = null,
  disabled = false,
  onAnswer,
  onSkip,
}: {
  suggestion: ClarificationSuggestionData;
  busyAction?: ClarificationBusyAction | null;
  disabled?: boolean;
  onAnswer: (answer: GuideConditionAnswer) => void;
  onSkip: () => void;
}) {
  const { clarification } = suggestion;
  return (
    <ClarificationCard
      testId="chat-clarification-choice"
      autoFocus
      items={[
        {
          id: suggestion.guide_condition_id,
          prompt: clarification.question,
          selection: clarification.multiple ? "multiple" : "single",
          options: clarification.options.map((option) => ({
            id: option.id,
            label: option.label,
            description: option.description || undefined,
          })),
        },
      ]}
      busyAction={busyAction}
      disabled={disabled}
      onConfirm={([answer]) =>
        onAnswer({
          guide_condition_id: suggestion.guide_condition_id,
          option_ids: answer?.optionIds ?? [],
          other_text: answer?.otherText ?? "",
        })
      }
      onSkip={onSkip}
    />
  );
}
