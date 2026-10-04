/**
 * 編集できるプロンプトの欄の検証（#1010）。規則は backend の `validate_prompt` と同じ
 * （空白だけは空、必須の placeholder `{{name}}` がすべて含まれていること）。
 */

import { t } from "@/lib/i18n";
import { requiredTextError } from "@/lib/required-fields";

export function answerPromptError(
  content: string,
  fieldLabel: string,
  requiredPlaceholders: readonly string[]
): string | null {
  const empty = requiredTextError(content, t("validation.required", { field: fieldLabel }));
  if (empty) return empty;
  const missing = requiredPlaceholders
    .map((name) => `{{${name}}}`)
    .filter((placeholder) => !content.includes(placeholder));
  return missing.length
    ? t("settings.answerPrompts.missingPlaceholders", { names: missing.join("、") })
    : null;
}
