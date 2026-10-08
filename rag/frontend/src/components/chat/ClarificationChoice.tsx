import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  TextareaField,
} from "@production-ready/ui";
import { MessageCircleQuestion, MessageSquareText, Send } from "lucide-react";
import { useId, useState } from "react";

import type {
  ClarificationAnswer,
  ClarificationSection,
  ClarificationSuggestionData,
} from "@/lib/api";
import { t } from "@/lib/i18n";

function sectionLabel(section: ClarificationSection): string {
  const { page_start: start, page_end: end } = section;
  const pages =
    start === null ? "" : end === null || end === start ? ` p.${start}` : ` p.${start}–${end}`;
  return `「${section.document_name || section.document_id}」${section.title}${pages}`;
}

/**
 * 回答の前に出す確認の質問（検索・回答プロファイルのルール。#717）。選択肢は複数選択（チェック）か 1 つ（ラジオ）で、
 * 「その他」の自由入力を併せて書ける。「選ばずに回答する」で確認を使わずに回答する。
 */
export function ClarificationChoice({
  suggestion,
  disabled = false,
  onAnswer,
  onSkip,
}: {
  suggestion: ClarificationSuggestionData;
  disabled?: boolean;
  onAnswer: (answer: ClarificationAnswer) => void;
  onSkip: () => void;
}) {
  const { clarification } = suggestion;
  const [selected, setSelected] = useState<string[]>([]);
  const [other, setOther] = useState("");
  const groupId = useId();
  const ready = selected.length > 0 || other.trim().length > 0;

  const toggle = (optionId: string) =>
    setSelected((current) =>
      clarification.multiple
        ? current.includes(optionId)
          ? current.filter((id) => id !== optionId)
          : [...current, optionId]
        : [optionId]
    );

  return (
    <Card data-testid="chat-clarification-choice">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageCircleQuestion size={16} className="text-accent-fg" aria-hidden />
          {t("chat.clarify.title")}
        </CardTitle>
        <CardDescription>
          {t(clarification.multiple ? "chat.clarify.descriptionMultiple" : "chat.clarify.description")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <fieldset className="space-y-2" aria-describedby={`${groupId}-question`}>
          <legend id={`${groupId}-question`} className="text-sm font-medium text-fg">
            {clarification.question}
          </legend>
          {clarification.options.map((option) => {
            const checked = selected.includes(option.id);
            return (
              <label
                key={option.id}
                className="flex cursor-pointer items-start gap-3 rounded-md border border-border bg-surface p-3 has-[:checked]:border-accent-emphasis has-[:checked]:bg-accent-subtle"
              >
                <input
                  type={clarification.multiple ? "checkbox" : "radio"}
                  name={`${groupId}-option`}
                  className="mt-0.5"
                  checked={checked}
                  disabled={disabled}
                  onChange={() => toggle(option.id)}
                />
                <span className="min-w-0 space-y-0.5">
                  <span className="block break-words text-sm font-medium text-fg">
                    {option.label}
                  </span>
                  {option.description ? (
                    <span className="block break-words text-xs text-fg-muted">
                      {option.description}
                    </span>
                  ) : null}
                  {option.sections.length > 0 ? (
                    <span className="block break-words text-xs text-fg-muted">
                      {t("chat.clarify.sections", {
                        sections: option.sections.map(sectionLabel).join("、"),
                      })}
                    </span>
                  ) : null}
                </span>
              </label>
            );
          })}
        </fieldset>
        {clarification.allow_other ? (
          <TextareaField
            id={`${groupId}-other`}
            label={t("chat.clarify.other")}
            rows={2}
            value={other}
            disabled={disabled}
            onValueChange={setOther}
          />
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            icon={Send}
            disabled={disabled || !ready}
            onClick={() =>
              onAnswer({ rule_id: suggestion.rule_id, option_ids: selected, other_text: other.trim() })
            }
          >
            {t("chat.clarify.answer")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            icon={MessageSquareText}
            disabled={disabled}
            onClick={onSkip}
          >
            {t("chat.clarify.skip")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
