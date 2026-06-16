"use client";

import { Database } from "lucide-react";
import { useId } from "react";

import { KnowledgeBasePickerGrid } from "@/components/knowledge-bases/KnowledgeBasePickerGrid";
import { Banner } from "@/components/ui/banner";
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

  return (
    <div className={cn("space-y-2", className)}>
      <div>
        <p id={labelId} className="flex items-center gap-1.5 text-xs font-medium text-foreground">
          <Database size={14} className="text-primary" aria-hidden />
          {label}
        </p>
        <p className="mt-1 text-xs text-muted">{helper}</p>
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
          <KnowledgeBasePickerGrid
            items={items}
            selectedIds={selectedIds}
            onChange={onChange}
            disabled={disabled}
            ariaLabel={label}
          />
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
