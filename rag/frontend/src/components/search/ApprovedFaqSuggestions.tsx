import { BookCheck, MessageSquareText } from "lucide-react";

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  StatusBadge,
} from "@engchina/production-ready-ui";

import type { ApprovedFaqSuggestionData } from "@/lib/api";
import { t } from "@/lib/i18n";

/** 回答前に提示する類似の承認済み FAQ。選ぶと LLM を使わず FAQ の回答を表示する。 */
export function ApprovedFaqSuggestions({
  suggestions,
  onUse,
  onSkip,
}: {
  suggestions: ApprovedFaqSuggestionData[];
  onUse: (suggestion: ApprovedFaqSuggestionData) => void;
  onSkip: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookCheck size={16} className="text-accent-fg" aria-hidden />
          {t("search.faq.title")}
        </CardTitle>
        <CardDescription>{t("search.faq.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <ul className="space-y-2">
          {suggestions.map((suggestion) => (
            <li
              key={suggestion.id}
              className="flex min-w-0 flex-col gap-2 rounded-md border border-border bg-surface p-3 sm:flex-row sm:items-start sm:justify-between"
            >
              <div className="min-w-0 space-y-1">
                <p className="break-words text-sm font-medium text-fg">
                  {suggestion.question}
                </p>
                <p className="line-clamp-2 whitespace-pre-wrap break-words text-xs text-fg-muted">
                  {suggestion.answer}
                </p>
                <StatusBadge
                  variant={suggestion.direct ? "success" : "neutral"}
                  label={t("search.faq.score", {
                    value: Math.round(suggestion.score * 100),
                  })}
                />
              </div>
              <Button
                size="sm"
                variant={suggestion.direct ? "primary" : "secondary"}
                className="shrink-0"
                onClick={() => onUse(suggestion)}
              >
                {t("search.faq.use")}
              </Button>
            </li>
          ))}
        </ul>
        <Button
          size="sm"
          variant="ghost"
          icon={MessageSquareText}
          onClick={onSkip}
        >
          {t("search.faq.skip")}
        </Button>
      </CardContent>
    </Card>
  );
}

/** 選んだ承認済み FAQ の回答(LLM 不使用)。 */
export function ApprovedFaqAnswer({
  suggestion,
}: {
  suggestion: ApprovedFaqSuggestionData;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookCheck size={16} className="text-accent-fg" aria-hidden />
          {t("search.faq.answerTitle")}
        </CardTitle>
        <CardDescription>{t("search.faq.answerDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="break-words text-xs text-fg-muted">
          {t("search.faq.matched", { question: suggestion.question })}
        </p>
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg">
          {suggestion.answer}
        </p>
      </CardContent>
    </Card>
  );
}
