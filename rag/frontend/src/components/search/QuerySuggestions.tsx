import { Button } from "@engchina/production-ready-ui";
import { History } from "lucide-react";

import { t } from "@/lib/i18n";
import { useQuerySuggestions } from "@/lib/queries";
import { useDebouncedValue } from "@/lib/use-debounced-value";

/**
 * 業務ビューでよく聞かれている質問（rag_poc の質問履歴の候補）。質問履歴が有効で候補があるときだけ出す。
 * 選ぶと質問欄に入る（送信はしない）。
 */
export function QuerySuggestions({
  businessViewId,
  query,
  filters,
  disabled,
  onSelect,
}: {
  businessViewId: string | null;
  query: string;
  filters: Record<string, string>;
  disabled: boolean;
  onSelect: (question: string) => void;
}) {
  const debouncedQuery = useDebouncedValue(query.trim(), 300);
  const suggestions = useQuerySuggestions(businessViewId, debouncedQuery, filters);
  const items = (suggestions.data?.suggestions ?? []).filter(
    (item) => item.question !== query.trim()
  );
  if (items.length === 0) return null;
  return (
    <div className="space-y-1.5">
      <p className="flex items-center gap-1 text-xs font-medium text-fg-muted">
        <History size={14} aria-hidden />
        {t("search.querySuggestions.title")}
      </p>
      <ul className="flex flex-wrap gap-1" aria-label={t("search.querySuggestions.title")}>
        {items.map((item) => (
          <li key={item.question}>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={disabled}
              title={t("search.querySuggestions.count", { count: item.count })}
              onClick={() => onSelect(item.question)}
            >
              {item.question}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
