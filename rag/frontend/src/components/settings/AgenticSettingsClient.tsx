"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RotateCcw, Save, Workflow } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type AgenticProfileName,
  type AgenticProfileStatusData,
  type AgenticSettingsData,
  type AgenticSettingsUpdate,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useAgenticSettings, useUpdateAgenticSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

const PROFILE_ORDER: AgenticProfileName[] = [
  "off",
  "smart_routing",
  "query_rewrite",
  "hyde",
  "decompose",
  "multi_hop",
];

/** 高度な検索方式の現在設定を管理する設定画面。 */
export function AgenticSettingsClient() {
  const query = useAgenticSettings();
  const save = useUpdateAgenticSettings();
  const [form, setForm] = useState<AgenticSettingsUpdate | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    if (query.data && !save.isPending) {
      setForm(formFromSettings(query.data));
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
            query.error instanceof ApiError ? query.error.message : t("settings.agentic.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const settings = query.data;
  if (!settings || !form) return null;

  const baseline = formFromSettings(settings);
  const dirty =
    form.profile !== baseline.profile || form.max_subqueries !== baseline.max_subqueries;
  const maxSubqueriesError =
    !Number.isInteger(form.max_subqueries) || form.max_subqueries < 1 || form.max_subqueries > 8;
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.agentic.saveError");
  const profiles = orderedProfiles(settings.profiles);
  const selectedProfile = profiles.find((item) => item.name === form.profile);
  const showLlmWarning = Boolean(selectedProfile?.enabled ?? settings.enabled);

  function selectProfile(next: AgenticProfileName) {
    save.reset();
    setSuccessMessage(null);
    setForm((current) => (current ? { ...current, profile: next } : current));
  }

  function updateMaxSubqueries(value: number) {
    save.reset();
    setSuccessMessage(null);
    setForm((current) => (current ? { ...current, max_subqueries: value } : current));
  }

  function resetForm() {
    save.reset();
    setSuccessMessage(null);
    setForm(formFromSettings(settings));
  }

  function submit() {
    if (!form || maxSubqueriesError) return;
    save.mutate(form, {
      onSuccess: (data) => {
        setForm(formFromSettings(data));
        setSuccessMessage(t("settings.agentic.actions.saved"));
      },
      onError: () => setSuccessMessage(null),
    });
  }

  return (
    <div className="space-y-5 p-8">
      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
              <Workflow size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.agentic.overview.title")}</CardTitle>
              <CardDescription>{t("settings.agentic.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <div className="text-sm font-medium text-foreground">
              {t("settings.agentic.profile")}
            </div>
            <div
              role="radiogroup"
              aria-label={t("settings.agentic.profile")}
              className="grid grid-cols-1 gap-2 md:grid-cols-2"
            >
              {profiles.map((item) => {
                const selected = form.profile === item.name;
                return (
                  <button
                    key={item.name}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    disabled={save.isPending}
                    onClick={() => selectProfile(item.name)}
                    className={cn(
                      "min-h-[112px] rounded-md border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                      selected
                        ? "border-primary bg-primary/10 text-foreground"
                        : "border-border bg-card text-foreground hover:bg-background"
                    )}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold">{profileLabel(item.name)}</span>
                      {selected ? (
                        <CheckCircle2 size={15} className="shrink-0 text-primary" aria-hidden />
                      ) : null}
                    </span>
                    <span className="mt-1 block text-xs leading-relaxed text-muted">
                      {profileDescription(item.name)}
                    </span>
                    <ProfileChips profile={item} />
                  </button>
                );
              })}
            </div>
          </div>
          {showLlmWarning ? (
            <FormStatus tone="warning" message={t("settings.agentic.llmWarning")} />
          ) : null}
          <dl className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <RuntimeFact
              label={t("settings.agentic.rewrite")}
              value={settings.rewrite ? t("settings.agentic.on") : t("settings.agentic.off")}
            />
            <RuntimeFact
              label={t("settings.agentic.decompose")}
              value={settings.decompose ? t("settings.agentic.on") : t("settings.agentic.off")}
            />
            <RuntimeFact
              label={t("settings.agentic.multiHop")}
              value={settings.multi_hop ? t("settings.agentic.on") : t("settings.agentic.off")}
            />
          </dl>
          <div className="max-w-xs">
            <NumberField
              label={t("settings.agentic.maxSubqueries")}
              value={form.max_subqueries}
              min={1}
              max={8}
              disabled={save.isPending}
              helper={t("settings.agentic.maxSubqueriesHelper")}
              onChange={updateMaxSubqueries}
            />
          </div>
          <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
            <div className="min-h-6">
              {maxSubqueriesError ? (
                <FormStatus tone="danger" message={t("settings.agentic.maxSubqueriesError")} />
              ) : dirty ? (
                <FormStatus tone="warning" message={t("settings.agentic.actions.unsaved")} />
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
                aria-label={t("settings.agentic.actions.reset")}
              >
                <RotateCcw size={15} aria-hidden />
                {t("settings.agentic.actions.reset")}
              </Button>
              <Button
                type="button"
                loading={save.isPending}
                disabled={!dirty || maxSubqueriesError}
                onClick={submit}
                aria-label={t("settings.agentic.actions.save")}
              >
                <Save size={15} aria-hidden />
                {save.isPending
                  ? t("settings.agentic.actions.saving")
                  : t("settings.agentic.actions.save")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function ProfileChips({ profile }: { profile: AgenticProfileStatusData }) {
  return (
    <span className="mt-2 flex flex-wrap gap-1">
      {profile.hyde ? (
        <span className="inline-flex min-h-5 items-center rounded bg-info-bg px-1.5 text-[11px] font-medium text-info">
          {t("settings.agentic.hyde")}
        </span>
      ) : null}
      {profile.rewrite ? (
        <span className="inline-flex min-h-5 items-center rounded bg-info-bg px-1.5 text-[11px] font-medium text-info">
          {t("settings.agentic.rewrite")}
        </span>
      ) : null}
      {profile.decompose ? (
        <span className="inline-flex min-h-5 items-center rounded bg-muted/20 px-1.5 text-[11px] text-muted">
          {t("settings.agentic.decompose")}
        </span>
      ) : null}
      {profile.multi_hop ? (
        <span className="inline-flex min-h-5 items-center rounded bg-muted/20 px-1.5 text-[11px] text-muted">
          {t("settings.agentic.multiHop")}
        </span>
      ) : null}
      {!profile.enabled ? (
        <span className="inline-flex min-h-5 items-center rounded bg-muted/20 px-1.5 text-[11px] text-muted">
          {t("settings.agentic.off")}
        </span>
      ) : null}
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

function NumberField({
  label,
  value,
  min,
  max,
  disabled,
  helper,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  disabled: boolean;
  helper?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="space-y-1.5">
      <span className="block text-sm font-medium text-foreground">{label}</span>
      <input
        type="number"
        inputMode="numeric"
        value={Number.isFinite(value) ? value : ""}
        min={min}
        max={max}
        aria-label={label}
        disabled={disabled}
        onChange={(event) => onChange(Number.parseInt(event.target.value, 10))}
        className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary disabled:cursor-not-allowed disabled:opacity-50"
      />
      {helper ? <span className="block text-xs text-muted">{helper}</span> : null}
    </label>
  );
}

function formFromSettings(settings: AgenticSettingsData): AgenticSettingsUpdate {
  return { profile: settings.profile, max_subqueries: settings.max_subqueries };
}

function orderedProfiles(profiles: AgenticProfileStatusData[]): AgenticProfileStatusData[] {
  const byName = new Map(profiles.map((item) => [item.name, item]));
  const ordered = PROFILE_ORDER.map((name) => byName.get(name)).filter(
    (item): item is AgenticProfileStatusData => Boolean(item)
  );
  // PROFILE_ORDER に無い API 返却 profile も捨てず末尾に出す(将来の profile 追加でも
  // アクティブ値が必ず描画されるよう堅牢化)。
  const known = new Set(PROFILE_ORDER as string[]);
  const extras = profiles.filter((item) => !known.has(item.name));
  return [...ordered, ...extras];
}

function profileLabel(name: AgenticProfileName) {
  return t(`settings.agentic.profile.${name}` as I18nKey);
}

function profileDescription(name: AgenticProfileName) {
  return t(`settings.agentic.profile.${name}.description` as I18nKey);
}
