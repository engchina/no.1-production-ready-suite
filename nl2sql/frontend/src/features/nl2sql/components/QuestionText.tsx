import { useMemo, useState } from "react";

import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type QuestionTextVariant = "list" | "detail" | "compact" | "select";

interface QuestionTextProps {
  value: string;
  variant?: QuestionTextVariant;
  maxLines?: number;
  className?: string;
  testId?: string;
  expandable?: boolean;
}

const DEFAULT_LINES: Record<QuestionTextVariant, number> = {
  list: 2,
  detail: 3,
  compact: 1,
  select: 1,
};

const VARIANT_CLASS: Record<QuestionTextVariant, string> = {
  list: "text-sm font-semibold leading-5 text-foreground",
  detail: "text-sm leading-6 text-foreground",
  compact: "text-xs leading-5 text-muted",
  select: "text-sm leading-5 text-foreground",
};

const CLAMP_CLASS: Record<number, string> = {
  1: "line-clamp-1",
  2: "line-clamp-2",
  3: "line-clamp-3",
  4: "line-clamp-4",
  5: "line-clamp-5",
  6: "line-clamp-6",
};

function compactQuestion(value: string) {
  return value.replace(/\s+/gu, " ").trim();
}

function lineClampClass(maxLines: number) {
  return CLAMP_CLASS[Math.max(1, Math.min(6, Math.round(maxLines)))] ?? CLAMP_CLASS[2];
}

function shouldOfferExpansion(value: string, maxLines: number) {
  return value.length > maxLines * 42 || value.includes("\n");
}

export function QuestionText({
  value,
  variant = "list",
  maxLines,
  className,
  testId,
  expandable = false,
}: QuestionTextProps) {
  const [expanded, setExpanded] = useState(false);
  const fullValue = value.trim() || "-";
  const displayValue = useMemo(
    () => (variant === "detail" ? fullValue : compactQuestion(fullValue) || "-"),
    [fullValue, variant]
  );
  const lines = maxLines ?? DEFAULT_LINES[variant];
  const canExpand = expandable && shouldOfferExpansion(fullValue, lines);
  const clamped = !expanded && lines > 0;

  return (
    <span className="grid w-full min-w-0 max-w-full gap-1 overflow-hidden">
      <span
        className={cn(
          "block w-full min-w-0 max-w-full whitespace-pre-wrap break-words [overflow-wrap:anywhere]",
          VARIANT_CLASS[variant],
          clamped && lineClampClass(lines),
          className
        )}
        data-testid={testId}
        aria-label={fullValue}
        title={fullValue}
      >
        {displayValue}
      </span>
      {canExpand ? (
        <button
          type="button"
          className="min-h-8 w-fit rounded-md px-2 text-xs font-semibold text-primary underline-offset-2 transition-colors hover:bg-primary/10 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? t("nl2sql.questionText.collapse") : t("nl2sql.questionText.expand")}
        </button>
      ) : null}
    </span>
  );
}
