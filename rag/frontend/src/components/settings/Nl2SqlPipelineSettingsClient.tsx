"use client";

import { useState } from "react";
import { CheckCircle2, SlidersHorizontal } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, type PipelineAdapterData } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useNl2SqlPipelineSettings, useUpdateNl2SqlPipelineSetting } from "@/lib/queries";
import { cn } from "@/lib/utils";

/** NL2SQL パイプライン preset 群(schema_source 〜 evaluation)を切り替える設定画面。 */
export function Nl2SqlPipelineSettingsClient() {
  const query = useNl2SqlPipelineSettings();
  const update = useUpdateNl2SqlPipelineSetting();
  const [applied, setApplied] = useState<{ key: string; message: string } | null>(null);

  if (query.isPending) {
    return (
      <div className="space-y-4 p-8">
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="p-8">
        <ErrorState
          message={
            query.error instanceof ApiError
              ? query.error.message
              : t("settings.nl2sqlPipeline.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const adapters = query.data?.adapters ?? [];

  function selectOption(adapter: PipelineAdapterData, selection: string) {
    if (selection === adapter.selected || update.isPending) return;
    update.reset();
    setApplied(null);
    update.mutate(
      { adapterKey: adapter.key, selection },
      {
        onSuccess: () => {
          setApplied({
            key: adapter.key,
            message: t("settings.nl2sqlPipeline.applied", {
              label: adapter.label,
              selection,
            }),
          });
        },
      }
    );
  }

  const saveError =
    update.error instanceof ApiError
      ? update.error.message
      : t("settings.nl2sqlPipeline.saveError");
  const pendingKey = update.isPending ? update.variables?.adapterKey : undefined;

  return (
    <div className="space-y-5 p-8">
      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
              <SlidersHorizontal size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.nl2sqlPipeline.overview.title")}</CardTitle>
              <CardDescription>
                {t("settings.nl2sqlPipeline.overview.description")}
              </CardDescription>
            </div>
          </div>
        </CardHeader>
      </Card>

      {adapters.map((adapter) => {
        const busy = pendingKey === adapter.key;
        return (
          <Card key={adapter.key} data-testid={`nl2sql-adapter-${adapter.key}`}>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <CardTitle className="text-base">{adapter.label}</CardTitle>
                <span className="inline-flex items-center gap-1.5 rounded-md bg-muted px-2 py-1 text-xs text-muted">
                  {t("settings.nl2sqlPipeline.current")}: {adapter.selected}
                </span>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <div
                role="radiogroup"
                aria-label={adapter.label}
                className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3"
              >
                {adapter.options.map((option) => {
                  const selected = option.name === adapter.selected;
                  return (
                    <button
                      key={option.name}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      disabled={update.isPending}
                      onClick={() => selectOption(adapter, option.name)}
                      className={cn(
                        "min-h-[88px] rounded-md border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                        selected
                          ? "border-primary bg-primary/10 text-foreground"
                          : "border-border bg-card text-foreground hover:bg-background"
                      )}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="font-mono text-sm font-semibold">{option.name}</span>
                        {selected ? (
                          <CheckCircle2 size={15} className="shrink-0 text-primary" aria-hidden />
                        ) : null}
                      </span>
                      <span className="mt-1 block text-xs leading-relaxed text-muted">
                        {option.summary}
                      </span>
                    </button>
                  );
                })}
              </div>
              <div className="min-h-5">
                {busy ? (
                  <FormStatus tone="info" message={t("nl2sql.console.actions.executing")} />
                ) : applied?.key === adapter.key ? (
                  <FormStatus tone="success" message={applied.message} />
                ) : update.isError && pendingKey === adapter.key ? (
                  <FormStatus tone="danger" message={saveError} />
                ) : null}
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
