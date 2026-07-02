"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RotateCcw, Save, ShieldCheck } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type GroundingPipelineStatusData,
  type GroundingSettingsData,
  type PostRetrievalPipelineName,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useGroundingSettings, useUpdateGroundingSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

const PIPELINE_ORDER: PostRetrievalPipelineName[] = [
  "custom",
  "lean",
  "verified_context",
  "context_enrich",
  "compact",
  "full_governed",
];

/** 根拠確認の現在設定を管理する設定画面。 */
export function GroundingSettingsClient() {
  const query = useGroundingSettings();
  const save = useUpdateGroundingSettings();
  const [pipeline, setPipeline] = useState<PostRetrievalPipelineName | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    // 初期化時のみ server 値で同期する。dirty な未保存選択は背景 refetch で上書きしない。
    if (query.data && pipeline === null) {
      setPipeline(query.data.pipeline);
    }
  }, [query.data, pipeline]);

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
            query.error instanceof ApiError ? query.error.message : t("settings.grounding.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const settings = query.data;
  if (!settings || !pipeline) return null;

  const dirty = pipeline !== settings.pipeline;
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.grounding.saveError");
  const pipelines = orderedPipelines(settings.pipelines);

  function selectPipeline(next: PostRetrievalPipelineName) {
    save.reset();
    setSuccessMessage(null);
    setPipeline(next);
  }

  function resetForm() {
    save.reset();
    setSuccessMessage(null);
    setPipeline(settings.pipeline);
  }

  function submit() {
    if (!pipeline) return;
    save.mutate(
      { pipeline },
      {
        onSuccess: (data) => {
          setPipeline(data.pipeline);
          setSuccessMessage(t("settings.grounding.actions.saved"));
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
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-success-bg text-success">
              <ShieldCheck size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.grounding.overview.title")}</CardTitle>
              <CardDescription>{t("settings.grounding.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <div className="text-sm font-medium text-foreground">
              {t("settings.grounding.pipeline")}
            </div>
            <div
              role="radiogroup"
              aria-label={t("settings.grounding.pipeline")}
              className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3"
            >
              {pipelines.map((item) => {
                const selected = pipeline === item.name;
                return (
                  <button
                    key={item.name}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    disabled={save.isPending}
                    onClick={() => selectPipeline(item.name)}
                    className={cn(
                      "min-h-[104px] rounded-md border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                      selected
                        ? "border-primary bg-primary/10 text-foreground"
                        : "border-border bg-card text-foreground hover:bg-background"
                    )}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold">{pipelineLabel(item.name)}</span>
                      {selected ? (
                        <CheckCircle2 size={15} className="shrink-0 text-primary" aria-hidden />
                      ) : null}
                    </span>
                    <span className="mt-1 block text-xs leading-relaxed text-muted">
                      {pipelineDescription(item.name)}
                    </span>
                    <StageChips pipeline={item} />
                  </button>
                );
              })}
            </div>
          </div>
          <dl className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <RuntimeFact label={t("settings.grounding.pipeline")} value={pipelineLabel(pipeline)} />
            <RuntimeFact
              label={t("settings.grounding.expansion")}
              value={expansionLabel(settings.expansion_mode)}
            />
            <RuntimeFact
              label={t("settings.grounding.source")}
              value={t("settings.common.currentConfig")}
            />
          </dl>
          <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
            <div className="min-h-6">
              {dirty ? (
                <FormStatus tone="warning" message={t("settings.grounding.actions.unsaved")} />
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
                aria-label={t("settings.grounding.actions.reset")}
              >
                <RotateCcw size={15} aria-hidden />
                {t("settings.grounding.actions.reset")}
              </Button>
              <Button
                type="button"
                loading={save.isPending}
                disabled={!dirty}
                onClick={submit}
                aria-label={t("settings.grounding.actions.save")}
              >
                <Save size={15} aria-hidden />
                {save.isPending
                  ? t("settings.grounding.actions.saving")
                  : t("settings.grounding.actions.save")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function StageChips({ pipeline }: { pipeline: GroundingPipelineStatusData }) {
  const stages: string[] = [];
  if (pipeline.dependency_promotion) stages.push(t("settings.grounding.dependency"));
  if (pipeline.diversity) stages.push(t("settings.grounding.diversity"));
  if (pipeline.expansion_mode !== "none") stages.push(t("settings.grounding.expansion"));
  if (pipeline.compression) stages.push(t("settings.grounding.compression"));
  if (pipeline.corrective) stages.push(t("settings.grounding.corrective"));
  const useCases = pipeline.recommended_for.slice(0, 2).map(groundingUseCaseLabel);
  return (
    <span className="mt-2 flex flex-wrap gap-1">
      {(useCases.length ? useCases : [t("settings.grounding.useCase.unknown")]).map((label) => (
        <span
          key={`use-case-${label}`}
          className="inline-flex min-h-5 items-center rounded bg-muted/20 px-1.5 text-[11px] text-muted"
        >
          {label}
        </span>
      ))}
      {stages.map((label) => (
        <span
          key={label}
          className="inline-flex min-h-5 items-center rounded bg-success-bg px-1.5 text-[11px] font-medium text-success"
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

function orderedPipelines(
  pipelines: GroundingPipelineStatusData[]
): GroundingPipelineStatusData[] {
  const byName = new Map(pipelines.map((item) => [item.name, item]));
  const ordered = PIPELINE_ORDER.map((name) => byName.get(name)).filter(
    (item): item is GroundingPipelineStatusData => Boolean(item)
  );
  return ordered.length ? ordered : pipelines;
}

function pipelineLabel(name: PostRetrievalPipelineName) {
  return t(`settings.grounding.pipeline.${name}` as I18nKey);
}

function pipelineDescription(name: PostRetrievalPipelineName) {
  return t(`settings.grounding.pipeline.${name}.description` as I18nKey);
}

function expansionLabel(mode: GroundingSettingsData["expansion_mode"]) {
  return t(`settings.grounding.expansionMode.${mode}` as I18nKey);
}

/** API の推奨用途 token を日本語へ変換し、未知値を画面へ露出しない。 */
export function groundingUseCaseLabel(token: string) {
  return (
    t(`settings.grounding.useCase.${token}` as I18nKey) ||
    t("settings.grounding.useCase.unknown")
  );
}
