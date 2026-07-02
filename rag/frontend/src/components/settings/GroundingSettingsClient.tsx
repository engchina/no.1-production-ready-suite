"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RotateCcw, Save, ShieldCheck } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
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

/** 画面ローカルの編集フォーム状態(処理方式 + CRAG 補正パラメータ)。 */
interface GroundingForm {
  pipeline: PostRetrievalPipelineName;
  crag_low_confidence_threshold: number;
  crag_high_confidence_threshold: number;
  crag_max_hops: number;
  crag_low_evidence_abstain: boolean;
}

function formFromSettings(settings: GroundingSettingsData): GroundingForm {
  return {
    pipeline: settings.pipeline,
    crag_low_confidence_threshold: settings.crag_low_confidence_threshold,
    crag_high_confidence_threshold: settings.crag_high_confidence_threshold,
    crag_max_hops: settings.crag_max_hops,
    crag_low_evidence_abstain: settings.crag_low_evidence_abstain,
  };
}

function isDirty(form: GroundingForm, settings: GroundingSettingsData): boolean {
  const base = formFromSettings(settings);
  return (Object.keys(base) as (keyof GroundingForm)[]).some((key) => form[key] !== base[key]);
}

function isValid(form: GroundingForm): boolean {
  return (
    Number.isFinite(form.crag_low_confidence_threshold) &&
    form.crag_low_confidence_threshold >= 0 &&
    form.crag_low_confidence_threshold <= 1 &&
    Number.isFinite(form.crag_high_confidence_threshold) &&
    form.crag_high_confidence_threshold >= form.crag_low_confidence_threshold &&
    form.crag_high_confidence_threshold <= 1 &&
    Number.isInteger(form.crag_max_hops) &&
    form.crag_max_hops >= 0 &&
    form.crag_max_hops <= 3
  );
}

/** 根拠確認(処理方式 + CRAG 補正)の設定画面。 */
export function GroundingSettingsClient() {
  const query = useGroundingSettings();
  const save = useUpdateGroundingSettings();
  const [form, setForm] = useState<GroundingForm | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    // 初期化時のみ server 値で同期する。dirty な未保存選択は背景 refetch で上書きしない。
    if (query.data && form === null) {
      setForm(formFromSettings(query.data));
    }
  }, [query.data, form]);

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
  if (!settings || !form) return null;

  const dirty = isDirty(form, settings);
  const valid = isValid(form);
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.grounding.saveError");
  const pipelines = orderedPipelines(settings.pipelines);

  function updateForm(patch: Partial<GroundingForm>) {
    save.reset();
    setSuccessMessage(null);
    setForm((current) => (current ? { ...current, ...patch } : current));
  }

  function resetForm() {
    save.reset();
    setSuccessMessage(null);
    if (settings) setForm(formFromSettings(settings));
  }

  function submit() {
    if (!form || !isValid(form)) return;
    save.mutate(
      { ...form },
      {
        onSuccess: (data) => {
          setForm(formFromSettings(data));
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
                const selected = form.pipeline === item.name;
                return (
                  <button
                    key={item.name}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    disabled={save.isPending}
                    onClick={() => updateForm({ pipeline: item.name })}
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
          <div className="space-y-2">
            <div>
              <div className="text-sm font-medium text-foreground">
                {t("settings.grounding.crag.title")}
              </div>
              <p className="text-xs text-muted">{t("settings.grounding.crag.description")}</p>
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <NumberField
                label={t("settings.grounding.crag.lowThreshold")}
                helper={t("settings.grounding.crag.lowThreshold.helper")}
                value={form.crag_low_confidence_threshold}
                min={0}
                max={1}
                step={0.05}
                disabled={save.isPending}
                onChange={(value) => updateForm({ crag_low_confidence_threshold: value })}
              />
              <NumberField
                label={t("settings.grounding.crag.highThreshold")}
                helper={t("settings.grounding.crag.highThreshold.helper")}
                value={form.crag_high_confidence_threshold}
                min={0}
                max={1}
                step={0.05}
                disabled={save.isPending}
                onChange={(value) => updateForm({ crag_high_confidence_threshold: value })}
              />
              <NumberField
                label={t("settings.grounding.crag.maxHops")}
                helper={t("settings.grounding.crag.maxHops.helper")}
                value={form.crag_max_hops}
                min={0}
                max={3}
                step={1}
                disabled={save.isPending}
                onChange={(value) => updateForm({ crag_max_hops: value })}
              />
            </div>
            <div className="flex items-start justify-between gap-4 rounded-md border border-border px-3 py-3">
              <div className="min-w-0">
                <div className="text-sm font-medium text-foreground">
                  {t("settings.grounding.crag.abstain")}
                </div>
                <p className="mt-0.5 text-xs leading-relaxed text-muted">
                  {t("settings.grounding.crag.abstain.helper")}
                </p>
              </div>
              <Switch
                checked={form.crag_low_evidence_abstain}
                disabled={save.isPending}
                aria-label={t("settings.grounding.crag.abstain")}
                onCheckedChange={(checked) => updateForm({ crag_low_evidence_abstain: checked })}
                className="mt-0.5 shrink-0"
              />
            </div>
            {!valid ? (
              <FormStatus tone="danger" message={t("settings.grounding.crag.invalid")} />
            ) : null}
          </div>
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
                disabled={!dirty || !valid}
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
  return (
    <span className="mt-2 flex flex-wrap gap-1">
      {stages.length ? (
        stages.map((label) => (
          <span
            key={label}
            className="inline-flex min-h-5 items-center rounded bg-success-bg px-1.5 text-[11px] font-medium text-success"
          >
            {label}
          </span>
        ))
      ) : (
        <span className="inline-flex min-h-5 items-center rounded bg-muted/20 px-1.5 text-[11px] text-muted">
          {pipeline.recommended_for[0] ?? t("settings.grounding.none")}
        </span>
      )}
    </span>
  );
}

function NumberField({
  label,
  helper,
  value,
  min,
  max,
  step,
  disabled,
  onChange,
}: {
  label: string;
  helper: string;
  value: number;
  min: number;
  max: number;
  step: number;
  disabled: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-sm font-medium text-foreground">{label}</span>
      <input
        type="number"
        inputMode="decimal"
        value={Number.isFinite(value) ? value : ""}
        min={min}
        max={max}
        step={step}
        aria-label={label}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary disabled:cursor-not-allowed disabled:opacity-50"
      />
      <span className="block text-xs text-muted">{helper}</span>
    </label>
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
