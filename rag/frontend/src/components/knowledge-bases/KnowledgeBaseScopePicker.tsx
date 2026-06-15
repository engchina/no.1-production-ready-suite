"use client";

import { Database, X } from "lucide-react";
import { useId } from "react";

import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useKnowledgeBases } from "@/lib/queries";
import { cn } from "@/lib/utils";

/** 検索・評価などで使う知識ベースの複数選択スコープ。 */
export function KnowledgeBaseScopePicker({
  selectedIds,
  onChange,
  disabled = false,
  label = t("knowledgeBaseScope.label"),
  helper = t("knowledgeBaseScope.helper"),
  emptySelectionText = t("knowledgeBaseScope.all"),
  className,
}: {
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  label?: string;
  helper?: string;
  emptySelectionText?: string;
  className?: string;
}) {
  const labelId = useId();
  const query = useKnowledgeBases({ status: "ACTIVE", limit: 50, offset: 0 });
  const items = query.data?.items ?? [];

  const toggle = (id: string) => {
    onChange(
      selectedIds.includes(id)
        ? selectedIds.filter((current) => current !== id)
        : [...selectedIds, id]
    );
  };

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p id={labelId} className="flex items-center gap-1.5 text-xs font-medium text-foreground">
            <Database size={14} className="text-primary" aria-hidden />
            {label}
          </p>
          <p className="mt-1 text-xs text-muted">{helper}</p>
        </div>
        {selectedIds.length > 0 ? (
          <Button type="button" variant="ghost" size="sm" onClick={() => onChange([])} disabled={disabled}>
            <X size={14} aria-hidden />
            {t("knowledgeBaseScope.clear")}
          </Button>
        ) : null}
      </div>

      {query.isError ? (
        <Banner severity="warning" title={t("knowledgeBaseScope.loadWarning")}>
          <p>
            {query.error instanceof ApiError
              ? query.error.message
              : t("knowledgeBaseScope.loadWarningHint")}
          </p>
        </Banner>
      ) : query.isPending ? (
        <p className="text-xs text-muted" role="status">
          {t("knowledgeBaseScope.loading")}
        </p>
      ) : items.length > 0 ? (
        <>
          <div
            role="group"
            aria-labelledby={labelId}
            className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3"
          >
            {items.map((knowledgeBase) => (
              <label
                key={knowledgeBase.id}
                className={cn(
                  "flex min-w-0 cursor-pointer items-start gap-2 rounded-md border border-border bg-background px-3 py-2 text-sm transition-colors hover:bg-info-bg/40",
                  selectedIds.includes(knowledgeBase.id) && "border-primary/40 bg-info-bg/50",
                  disabled && "cursor-not-allowed opacity-60"
                )}
              >
                <input
                  type="checkbox"
                  checked={selectedIds.includes(knowledgeBase.id)}
                  onChange={() => toggle(knowledgeBase.id)}
                  disabled={disabled}
                  className="mt-0.5 cursor-pointer accent-[var(--primary)] disabled:cursor-not-allowed"
                />
                <span className="min-w-0">
                  <span className="block truncate font-medium text-foreground">
                    {knowledgeBase.name}
                  </span>
                  <span className="tnum block text-xs text-muted">
                    {t("knowledgeBaseScope.documentCount", {
                      count: knowledgeBase.document_count,
                    })}
                  </span>
                </span>
              </label>
            ))}
          </div>
          <p className="text-xs text-muted">
            {selectedIds.length > 0
              ? t("knowledgeBaseScope.selected", { count: selectedIds.length })
              : emptySelectionText}
          </p>
        </>
      ) : (
        <p className="rounded-md border border-border bg-background px-3 py-2 text-xs text-muted">
          {t("knowledgeBaseScope.empty")}
        </p>
      )}
    </div>
  );
}
