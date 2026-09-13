"use client";

import {
  PageBody,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Button,
  FormStatus,
  Skeleton,
} from "@engchina/production-ready-ui";
import { useEffect, useRef, useState } from "react";
import { CheckCircle2, ClipboardCheck, RotateCcw, Save } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import {
  ApiError,
  type EvaluationSuiteName,
  type EvaluationSuiteStatusData,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useEvaluationSettings, useUpdateEvaluationSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

const SUITE_ORDER: EvaluationSuiteName[] = [
  "request_only",
  "retrieval_focused",
  "balanced",
  "strict_ci",
  "ragas_like",
];

/** 品質評価の基準と閾値の現在設定を管理する設定画面。 */
export function EvaluationSettingsClient() {
  const query = useEvaluationSettings();
  const save = useUpdateEvaluationSettings();
  const [suite, setSuite] = useState<EvaluationSuiteName | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const lastServerSuite = useRef<EvaluationSuiteName | null>(null);

  // サーバの suite が実際に変わったときだけ local 選択へ同期する。
  // 背景再取得で同じ値が返っても未保存の選択を上書きしない。
  useEffect(() => {
    const serverSuite = query.data?.suite ?? null;
    if (serverSuite && serverSuite !== lastServerSuite.current) {
      lastServerSuite.current = serverSuite;
      setSuite(serverSuite);
    }
  }, [query.data]);

  if (query.isPending) {
    return (
      <PageBody>
        <Skeleton className="h-64 w-full rounded-lg" />
      </PageBody>
    );
  }

  if (query.isError) {
    return (
      <PageBody>
        <ErrorState
          message={
            query.error instanceof ApiError
              ? query.error.message
              : t("settings.evaluation.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </PageBody>
    );
  }

  const settings = query.data;
  if (!settings || !suite) return null;

  const dirty = suite !== settings.suite;
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.evaluation.saveError");
  const suites = orderedSuites(settings.suites);
  const selectedSuite = suites.find((item) => item.name === suite);

  function selectSuite(next: EvaluationSuiteName) {
    save.reset();
    setSuccessMessage(null);
    setSuite(next);
  }

  function resetForm() {
    save.reset();
    setSuccessMessage(null);
    setSuite(settings.suite);
  }

  function submit() {
    if (!suite) return;
    save.mutate(
      { suite },
      {
        onSuccess: (data) => {
          setSuite(data.suite);
          setSuccessMessage(t("settings.evaluation.actions.saved"));
        },
        onError: () => setSuccessMessage(null),
      }
    );
  }

  const thresholdEntries = Object.entries(selectedSuite?.thresholds ?? {});

  return (
    <PageBody>
      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
              <ClipboardCheck size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.evaluation.overview.title")}</CardTitle>
              <CardDescription>{t("settings.evaluation.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <div className="text-sm font-medium text-fg">
              {t("settings.evaluation.suite")}
            </div>
            <div
              role="radiogroup"
              aria-label={t("settings.evaluation.suite")}
              className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3"
            >
              {suites.map((item) => {
                const selected = suite === item.name;
                return (
                  <button
                    key={item.name}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    disabled={save.isPending}
                    onClick={() => selectSuite(item.name)}
                    className={cn(
                      "min-h-[104px] rounded-md border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                      selected
                        ? "border-accent-emphasis bg-accent-subtle text-fg"
                        : "border-border bg-surface text-fg hover:bg-surface-hover"
                    )}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold">{suiteLabel(item.name)}</span>
                      {selected ? (
                        <CheckCircle2 size={16} className="shrink-0 text-accent-fg" aria-hidden />
                      ) : null}
                    </span>
                    <span className="mt-1 block text-xs leading-relaxed text-fg-muted">
                      {suiteDescription(item.name)}
                    </span>
                    <SuiteChips suite={item} />
                  </button>
                );
              })}
            </div>
          </div>
          <div className="rounded-md border border-border bg-surface-hover p-3">
            <div className="text-xs font-medium text-fg-muted">{t("settings.evaluation.thresholds")}</div>
            {thresholdEntries.length ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {thresholdEntries.map(([metric, value]) => (
                  <span
                    key={metric}
                    className="inline-flex min-h-6 items-center rounded-md bg-surface px-2 text-xs font-medium text-fg ring-1 ring-border"
                  >
                    {metric}
                    <span className="ml-1 font-semibold text-accent-fg">{value}</span>
                  </span>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-sm text-fg">
                {t("settings.evaluation.noThresholds")}
              </p>
            )}
          </div>
          <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
            <div className="min-h-6">
              {dirty ? (
                <FormStatus tone="warning" message={t("settings.evaluation.actions.unsaved")} />
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
                aria-label={t("settings.evaluation.actions.reset")} icon={RotateCcw}>
                {t("settings.evaluation.actions.reset")}
              </Button>
              <Button
                type="button"
                loading={save.isPending}
                disabled={!dirty}
                onClick={submit}
                aria-label={t("settings.evaluation.actions.save")} icon={Save}>
                {t("settings.evaluation.actions.save")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </PageBody>
  );
}

function SuiteChips({ suite }: { suite: EvaluationSuiteStatusData }) {
  const thresholdCount = Object.keys(suite.thresholds).length;
  return (
    <span className="mt-2 flex flex-wrap gap-1">
      {suite.recommended_for.slice(0, 2).map((item) => (
        <span
          key={item}
          className="inline-flex min-h-5 items-center rounded bg-surface-hover px-1.5 text-xs text-fg-muted"
        >
          {item}
        </span>
      ))}
      <span className="inline-flex min-h-5 items-center rounded bg-info-subtle px-1.5 text-xs font-medium text-info-fg">
        {t("settings.evaluation.thresholds")} {thresholdCount}
      </span>
    </span>
  );
}

function orderedSuites(suites: EvaluationSuiteStatusData[]): EvaluationSuiteStatusData[] {
  const byName = new Map(suites.map((item) => [item.name, item]));
  const ordered = SUITE_ORDER.map((name) => byName.get(name)).filter(
    (item): item is EvaluationSuiteStatusData => Boolean(item)
  );
  return ordered.length ? ordered : suites;
}

function suiteLabel(name: EvaluationSuiteName) {
  return t(`settings.evaluation.suite.${name}` as I18nKey);
}

function suiteDescription(name: EvaluationSuiteName) {
  return t(`settings.evaluation.suite.${name}.description` as I18nKey);
}
