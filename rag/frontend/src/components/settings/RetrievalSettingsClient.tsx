"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RotateCcw, Save, Search } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type RetrievalStrategyName,
  type RetrievalStrategyStatusData,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useRetrievalSettings, useUpdateRetrievalSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

const STRATEGY_ORDER: RetrievalStrategyName[] = [
  "hybrid_rrf",
  "vector",
  "keyword",
  "graph_augmented",
  "business_context_strict",
  "corrective_multi_query",
];

/** 検索方法の現在設定を管理する設定画面。 */
export function RetrievalSettingsClient() {
  const query = useRetrievalSettings();
  const save = useUpdateRetrievalSettings();
  const [strategy, setStrategy] = useState<RetrievalStrategyName | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    if (query.data && !save.isPending) {
      setStrategy(query.data.strategy);
    }
  }, [query.data, save.isPending]);

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
            query.error instanceof ApiError ? query.error.message : t("settings.retrieval.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const settings = query.data;
  if (!settings || !strategy) return null;

  const dirty = strategy !== settings.strategy;
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.retrieval.saveError");
  const strategies = orderedStrategies(settings.strategies);

  function selectStrategy(next: RetrievalStrategyName) {
    save.reset();
    setSuccessMessage(null);
    setStrategy(next);
  }

  function resetForm() {
    save.reset();
    setSuccessMessage(null);
    setStrategy(settings.strategy);
  }

  function submit() {
    if (!strategy) return;
    save.mutate(
      { strategy },
      {
        onSuccess: (data) => {
          setStrategy(data.strategy);
          setSuccessMessage(t("settings.retrieval.actions.saved"));
        },
        onError: () => setSuccessMessage(null),
      }
    );
  }

  return (
    <div className="space-y-5 p-8">
      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
              <Search size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.retrieval.overview.title")}</CardTitle>
              <CardDescription>{t("settings.retrieval.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <div className="text-sm font-medium text-foreground">
              {t("settings.retrieval.strategy")}
            </div>
            <div
              role="radiogroup"
              aria-label={t("settings.retrieval.strategy")}
              className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3"
            >
              {strategies.map((item) => {
                const selected = strategy === item.name;
                return (
                  <button
                    key={item.name}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    disabled={save.isPending}
                    onClick={() => selectStrategy(item.name)}
                    className={cn(
                      "min-h-[104px] rounded-md border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                      selected
                        ? "border-primary bg-primary/10 text-foreground"
                        : "border-border bg-card text-foreground hover:bg-background"
                    )}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold">{strategyLabel(item.name)}</span>
                      {selected ? (
                        <CheckCircle2 size={15} className="shrink-0 text-primary" aria-hidden />
                      ) : null}
                    </span>
                    <span className="mt-1 block text-xs leading-relaxed text-muted">
                      {strategyDescription(item.name)}
                    </span>
                    <TechniqueChips strategy={item} />
                  </button>
                );
              })}
            </div>
          </div>
          <dl className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <RuntimeFact label={t("settings.retrieval.strategy")} value={strategyLabel(strategy)} />
            <RuntimeFact
              label={t("settings.retrieval.queryExpansion")}
              value={settings.query_expansion ? "ON" : "OFF"}
            />
            <RuntimeFact
              label={t("settings.retrieval.source")}
              value={t("settings.common.currentConfig")}
            />
          </dl>
          <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
            <div className="min-h-6">
              {dirty ? (
                <FormStatus tone="warning" message={t("settings.retrieval.actions.unsaved")} />
              ) : null}
              {successMessage ? <FormStatus tone="success" message={successMessage} /> : null}
              {save.isError ? <FormStatus tone="danger" message={saveError} /> : null}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="secondary"
                onClick={resetForm}
                disabled={!dirty || save.isPending}
                aria-label={t("settings.retrieval.actions.reset")}
              >
                <RotateCcw size={15} aria-hidden />
                {t("settings.retrieval.actions.reset")}
              </Button>
              <Button
                type="button"
                loading={save.isPending}
                disabled={!dirty}
                onClick={submit}
                aria-label={t("settings.retrieval.actions.save")}
              >
                <Save size={15} aria-hidden />
                {save.isPending
                  ? t("settings.retrieval.actions.saving")
                  : t("settings.retrieval.actions.save")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function TechniqueChips({ strategy }: { strategy: RetrievalStrategyStatusData }) {
  const techniques: string[] = [];
  if (strategy.gap_stop) techniques.push(t("settings.retrieval.gapStop"));
  if (strategy.corrective_retrieval) techniques.push(t("settings.retrieval.corrective"));
  if (strategy.business_fit_weighting) techniques.push(t("settings.retrieval.businessFit"));
  return (
    <span className="mt-2 flex flex-wrap gap-1">
      {strategy.recommended_for.slice(0, 2).map((item) => (
        <span
          key={item}
          className="inline-flex min-h-5 items-center rounded bg-muted px-1.5 text-[11px] text-muted"
        >
          {item}
        </span>
      ))}
      {techniques.map((label) => (
        <span
          key={label}
          className="inline-flex min-h-5 items-center rounded bg-info-bg px-1.5 text-[11px] font-medium text-info"
        >
          {label}
        </span>
      ))}
    </span>
  );
}

function RuntimeFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-muted/20 p-3">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-semibold text-foreground">{value}</dd>
    </div>
  );
}

function orderedStrategies(
  strategies: RetrievalStrategyStatusData[]
): RetrievalStrategyStatusData[] {
  const byName = new Map(strategies.map((item) => [item.name, item]));
  const ordered = STRATEGY_ORDER.map((name) => byName.get(name)).filter(
    (item): item is RetrievalStrategyStatusData => Boolean(item)
  );
  return ordered.length ? ordered : strategies;
}

function strategyLabel(name: RetrievalStrategyName) {
  return t(`settings.retrieval.strategy.${name}` as I18nKey);
}

function strategyDescription(name: RetrievalStrategyName) {
  return t(`settings.retrieval.strategy.${name}.description` as I18nKey);
}
