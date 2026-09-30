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
  Switch,
  TimedLoadingState,
  FormSkeleton,
  TextField,
} from "@engchina/production-ready-ui";
import { useState } from "react";
import { CheckCircle2, RotateCcw, Save, ShieldCheck } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { DocragUnusedNote } from "@/components/settings/DocragUnusedNote";
import {
  ApiError,
  type GroundingPipelineStatusData,
  type GroundingSettingsData,
  type PostRetrievalPipelineName,
} from "@/lib/api";
import { useLeaveGuard } from "@/lib/leave-guard";
import { focusFirstInvalidField, numberRangeError } from "@/lib/required-fields";
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

type GroundingNumberField =
  | "crag_low_confidence_threshold"
  | "crag_high_confidence_threshold"
  | "crag_max_hops";
type GroundingFieldErrors = Partial<Record<GroundingNumberField, string | null>>;

const GROUNDING_FIELD_IDS: Record<GroundingNumberField, string> = {
  crag_low_confidence_threshold: "grounding-crag-low-threshold",
  crag_high_confidence_threshold: "grounding-crag-high-threshold",
  crag_max_hops: "grounding-crag-max-hops",
};
const GROUNDING_FIELD_ORDER: GroundingNumberField[] = [
  "crag_low_confidence_threshold",
  "crag_high_confidence_threshold",
  "crag_max_hops",
];

/**
 * 欄ごとの検証（#541）。規則は backend（GroundingSettingsUpdate）と同じ: しきい値は 0〜1 で、
 * 高しきい値は低しきい値以上。再検索の上限回数は 0〜3 の整数。空（NaN）は未入力として扱う。
 */
export function validateGroundingForm(form: GroundingForm): GroundingFieldErrors {
  const lowLabel = t("settings.grounding.crag.lowThreshold");
  const highLabel = t("settings.grounding.crag.highThreshold");
  const low = numberRangeError(form.crag_low_confidence_threshold, {
    label: lowLabel,
    min: 0,
    max: 1,
    integer: false,
  });
  const high =
    numberRangeError(form.crag_high_confidence_threshold, {
      label: highLabel,
      min: 0,
      max: 1,
      integer: false,
    }) ??
    (!low && form.crag_high_confidence_threshold < form.crag_low_confidence_threshold
      ? t("validation.notLessThan", { field: highLabel, other: lowLabel })
      : null);
  return {
    crag_low_confidence_threshold: low,
    crag_high_confidence_threshold: high,
    crag_max_hops: numberRangeError(form.crag_max_hops, {
      label: t("settings.grounding.crag.maxHops"),
      min: 0,
      max: 3,
    }),
  };
}

/** 根拠確認(処理方式 + CRAG 補正)の設定画面。 */
export function GroundingSettingsClient() {
  const query = useGroundingSettings();
  const save = useUpdateGroundingSettings();
  const [form, setForm] = useState<GroundingForm | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<GroundingFieldErrors>({});

  // 初期化時のみ render 中に server 値で同期する。dirty な未保存選択は背景 refetch で上書きしない。
  if (query.data && form === null) {
    setForm(formFromSettings(query.data));
  }

  // 未保存の選択があるときだけ、サイドナビ・内部リンク・再読込での離脱を確認する。
  useLeaveGuard(Boolean(query.data && form && isDirty(form, query.data)));

  if (query.isPending) {
    return (
      <PageBody wide>
        <TimedLoadingState
          label={t("settings.loading")}
          operationKey="settings-grounding-load"
          placement="page"
          testId="settings-grounding-loading"
        >
          <FormSkeleton />
        </TimedLoadingState>
      </PageBody>
    );
  }

  if (query.isError) {
    return (
      <PageBody wide>
        <ErrorState
          message={
            query.error instanceof ApiError ? query.error.message : t("settings.grounding.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </PageBody>
    );
  }

  const settings = query.data;
  if (!settings || !form) return null;

  const dirty = isDirty(form, settings);
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.grounding.saveError");
  const pipelines = orderedPipelines(settings.pipelines);

  function updateForm(patch: Partial<GroundingForm>) {
    save.reset();
    setSuccessMessage(null);
    setForm((current) => (current ? { ...current, ...patch } : current));
    // 直した欄のエラーだけを消す（大小の関係のエラーは高しきい値に出すので、低しきい値を直したときも消す）。
    setFieldErrors((current) => {
      const next = { ...current };
      for (const field of Object.keys(patch)) delete next[field as GroundingNumberField];
      if ("crag_low_confidence_threshold" in patch) delete next.crag_high_confidence_threshold;
      return next;
    });
  }

  function resetForm() {
    save.reset();
    setSuccessMessage(null);
    setFieldErrors({});
    if (settings) setForm(formFromSettings(settings));
  }

  function submit() {
    if (!form) return;
    // 保存を押したときに欄ごとに検証し、欄の直下に出して最初のエラーの欄へ移す（#541）。
    const errors = validateGroundingForm(form);
    setFieldErrors(errors);
    if (
      focusFirstInvalidField(
        GROUNDING_FIELD_ORDER.map((field) => [GROUNDING_FIELD_IDS[field], errors[field]] as const),
      )
    ) {
      return;
    }
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
    <PageBody wide>
      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-success-subtle text-success-fg">
              <ShieldCheck size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.grounding.overview.title")}</CardTitle>
              <CardDescription>{t("settings.grounding.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <DocragUnusedNote id="grounding-docrag-note">
            {t("settings.grounding.docragUnused")}
          </DocragUnusedNote>
          <div className="space-y-2">
            <div className="text-sm font-medium text-fg">
              {t("settings.grounding.pipeline")}
            </div>
            <div
              role="radiogroup"
              aria-label={t("settings.grounding.pipeline")}
              aria-describedby="grounding-docrag-note"
              className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3"
            >
              {pipelines.map((item) => {
                const selected = form.pipeline === item.name;
                return (
                  <div key={item.name} className="relative min-w-0">
                    <input
                      id={`settings-grounding-pipeline-${item.name}`}
                      className="peer absolute inset-0 z-10 cursor-pointer opacity-0 disabled:cursor-not-allowed"
                      type="radio"
                      name="settings-grounding-pipeline"
                      value={item.name}
                      checked={selected}
                      disabled={save.isPending}
                      onChange={() => updateForm({ pipeline: item.name })}
                    />
                    <label
                      htmlFor={`settings-grounding-pipeline-${item.name}`}
                      className={cn(
                        "block h-full cursor-pointer min-h-[7.43rem] rounded-md border px-3 py-2 text-left transition-colors peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-focus-ring peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
                        selected
                          ? "border-accent-emphasis bg-accent-subtle text-fg"
                          : "border-border bg-surface text-fg peer-hover:bg-surface-hover"
                      )}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="text-sm font-semibold">{pipelineLabel(item.name)}</span>
                        {selected ? (
                          <CheckCircle2 size={16} className="shrink-0 text-accent-fg" aria-hidden />
                        ) : null}
                      </span>
                      <span className="mt-1 block text-xs leading-relaxed text-fg-muted">
                        {pipelineDescription(item.name)}
                      </span>
                      <StageChips pipeline={item} />
                    </label>
                  </div>
                );
              })}
            </div>
          </div>
          <div className="space-y-2">
            <div>
              <div className="text-sm font-medium text-fg">
                {t("settings.grounding.crag.title")}
              </div>
              <p className="text-xs text-fg-muted">{t("settings.grounding.crag.description")}</p>
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <NumberField
                id={GROUNDING_FIELD_IDS.crag_low_confidence_threshold}
                error={fieldErrors.crag_low_confidence_threshold}
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
                id={GROUNDING_FIELD_IDS.crag_high_confidence_threshold}
                error={fieldErrors.crag_high_confidence_threshold}
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
                id={GROUNDING_FIELD_IDS.crag_max_hops}
                error={fieldErrors.crag_max_hops}
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
                <div className="text-sm font-medium text-fg">
                  {t("settings.grounding.crag.abstain")}
                </div>
                <p className="mt-0.5 text-xs leading-relaxed text-fg-muted">
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
                aria-label={t("settings.grounding.actions.reset")} icon={RotateCcw}>
                {t("settings.grounding.actions.reset")}
              </Button>
              <Button
                type="button"
                loading={save.isPending}
                disabled={!dirty}
                onClick={submit}
                aria-label={t("settings.grounding.actions.save")} icon={Save}>
                {t("settings.grounding.actions.save")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </PageBody>
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
          className="inline-flex min-h-5 items-center rounded bg-surface-hover px-1.5 text-xs text-fg-muted"
        >
          {label}
        </span>
      ))}
      {stages.map((label) => (
        <span
          key={label}
          className="inline-flex min-h-5 items-center rounded bg-success-subtle px-1.5 text-xs font-medium text-success-fg"
        >
          {label}
        </span>
      ))}
    </span>
  );
}

function NumberField({
  id,
  error,
  label,
  helper,
  value,
  min,
  max,
  step,
  disabled,
  onChange,
}: {
  id: string;
  /** 欄の直下に出すエラー（保存を押したときの検証）。 */
  error?: string | null;
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
    <TextField
      id={id}
      label={label}
      helper={helper}
      error={error ?? undefined}
      // 空欄は未入力(NaN)として保存できない(validateGroundingForm が止める)ので必須(#531)。
      required
      type="number"
      inputMode="decimal"
      value={Number.isFinite(value) ? value : ""}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      // 空欄を Number("") の 0 として扱うと、低しきい値では「CRAG を無効化」という別の設定が黙って
      // 入り、途中入力(「0.」など)も 0 に書き戻される。空欄は NaN にして未入力(保存不可)とする(#275)。
      onValueChange={(next) => onChange(parseNumberInput(next))}
    />
  );
}

/** number input の値を数値へ。空欄は未入力として NaN を返す(0 と区別する)。 */
export function parseNumberInput(raw: string): number {
  return raw.trim() === "" ? Number.NaN : Number(raw);
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
/** API の推奨用途 token を日本語へ変換し、未知値を画面へ露出しない。 */
export function groundingUseCaseLabel(token: string) {
  return (
    t(`settings.grounding.useCase.${token}` as I18nKey) ||
    t("settings.grounding.useCase.unknown")
  );
}
