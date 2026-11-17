import { BookCheck, MessageSquareText } from "lucide-react";

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  StatusBadge,
} from "@production-ready/ui";

import type { ApprovedFaqSuggestionData } from "@/lib/api";
import { t } from "@/lib/i18n";

/**
 * 回答の前に提示する、質問に近い登録済みQ&A（一致度の下限以上・最大件数は backend が絞る）。
 * RAG Chat だけが提示する（RAG 検索は基本の RAG で提示しない。#5332）。選ぶと資料の検索と回答の生成をせず、
 * Q&A の回答をそのまま返す（#5107）。「どれでもない」は Q&A を使わずに資料を検索して回答する。
 */
export function ApprovedFaqSuggestions({
  suggestions,
  onUse,
  onSkip,
  disabled = false,
}: {
  suggestions: ApprovedFaqSuggestionData[];
  onUse: (suggestion: ApprovedFaqSuggestionData) => void;
  onSkip: () => void;
  disabled?: boolean;
}) {
  return (
    <Card data-testid="chat-approved-faq-suggestions">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookCheck size={16} className="text-accent-fg" aria-hidden />
          {t("chat.faq.title")}
        </CardTitle>
        <CardDescription>{t("chat.faq.description")}</CardDescription>
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
                  variant="neutral"
                  label={t("chat.faq.score", {
                    value: Math.round(suggestion.score * 100),
                  })}
                />
              </div>
              <Button
                size="sm"
                variant="secondary"
                className="shrink-0"
                disabled={disabled}
                onClick={() => onUse(suggestion)}
              >
                {t("chat.faq.use")}
              </Button>
            </li>
          ))}
        </ul>
        <Button
          size="sm"
          variant="ghost"
          icon={MessageSquareText}
          disabled={disabled}
          onClick={onSkip}
        >
          {t("chat.faq.skip")}
        </Button>
      </CardContent>
    </Card>
  );
}
