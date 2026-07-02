"use client";

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { RotateCcw, Save, Sparkles } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Banner } from "@/components/ui/banner";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type GenerationProfileName,
  type GenerationProfileStatusData,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useGenerationSettings, useUpdateGenerationSettings } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { cn } from "@/lib/utils";

const PROFILE_ORDER: GenerationProfileName[] = [
  "grounded_concise",
  "detailed_cited",
  "strict_extractive",
  "structured_json",
  "bilingual_ja_en",
  "inline_cited",
  "custom",
];

/** 回答スタイルの現在設定を管理する設定画面。 */
export function GenerationSettingsClient() {
  const query = useGenerationSettings();
  const save = useUpdateGenerationSettings();
  const [profile, setProfile] = useState<GenerationProfileName | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    // 初回ロード時のみ同期する。保存後の反映は onSuccess が担うため、背景 refetch で
    // 未保存の選択を上書きしない。
    if (query.data && profile === null) {
      setProfile(query.data.profile);
    }
  }, [query.data, profile]);

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
              : t("settings.generation.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const settings = query.data;
  if (!settings || !profile) return null;

  const dirty = profile !== settings.profile;
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.generation.saveError");
  const profiles = orderedProfiles(settings.profiles);

  function selectProfile(next: GenerationProfileName) {
    save.reset();
    setSuccessMessage(null);
    setProfile(next);
  }

  function resetForm() {
    save.reset();
    setSuccessMessage(null);
    setProfile(settings.profile);
  }

  function submit() {
    if (!profile) return;
    save.mutate(
      { profile, expected_revision: settings.revision },
      {
        onSuccess: (data) => {
          setProfile(data.profile);
          setSuccessMessage(t("settings.generation.actions.saved"));
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
              <Sparkles size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.generation.overview.title")}</CardTitle>
              <CardDescription>{t("settings.generation.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <div className="text-sm font-medium text-foreground">
              {t("settings.generation.profile")}
            </div>
            {!settings.custom_prompt_configured ? (
              <Banner severity="info" title={t("settings.generation.custom.unavailableTitle")}>
                <span>{t("settings.generation.custom.unavailableDescription")} </span>
                <Link className="font-medium text-primary underline" to={APP_ROUTES.settingsPrompts}>
                  {t("settings.generation.custom.manageLink")}
                </Link>
              </Banner>
            ) : null}
            <fieldset className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3">
              <legend className="sr-only">{t("settings.generation.profile")}</legend>
              {profiles.map((item) => {
                const selected = profile === item.name;
                const disabled = save.isPending || (
                  item.name === "custom" && !settings.custom_prompt_configured
                );
                return (
                  <label
                    key={item.name}
                    htmlFor={`generation-profile-${item.name}`}
                    className={cn(
                      "min-h-[118px] rounded-md border px-3 py-2 text-left transition-colors focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2",
                      disabled && "cursor-not-allowed opacity-50",
                      selected
                        ? "border-primary bg-primary/10 text-foreground"
                        : "border-border bg-card text-foreground hover:bg-background",
                      !disabled && "cursor-pointer"
                    )}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold">{profileLabel(item.name)}</span>
                      <input
                        id={`generation-profile-${item.name}`}
                        className="h-4 w-4 shrink-0 accent-primary"
                        type="radio"
                        name="generation-profile"
                        value={item.name}
                        checked={selected}
                        disabled={disabled}
                        onChange={() => selectProfile(item.name)}
                      />
                    </span>
                    <span className="mt-1 block text-xs leading-relaxed text-muted">
                      {profileDescription(item.name)}
                    </span>
                    <span className="mt-2 block text-[11px] font-medium text-muted">
                      {t("settings.generation.validationMethod")}: {contractLabel(item.contract_mode)}
                    </span>
                    {item.repair_enabled ? (
                      <span className="mt-1 block text-[11px] text-info">
                        {t("settings.generation.repairEnabled")}
                      </span>
                    ) : null}
                    <ProfileChips profile={item} />
                  </label>
                );
              })}
            </fieldset>
            {profile === "custom" ? (
              <Link
                to={APP_ROUTES.settingsPrompts}
                className="inline-flex text-sm font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                {t("settings.generation.custom.manageLink")}
              </Link>
            ) : null}
          </div>
          <dl className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <RuntimeFact label={t("settings.generation.profile")} value={profileLabel(profile)} />
            <RuntimeFact
              label={t("settings.generation.structuredOutput")}
              value={
                profiles.find((item) => item.name === profile)?.structured_output ? "JSON" : "—"
              }
            />
            <RuntimeFact
              label={t("settings.generation.source")}
              value={t("settings.generation.source.oracle")}
            />
          </dl>
          <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
            <div className="min-h-6">
              {dirty ? (
                <FormStatus tone="warning" message={t("settings.generation.actions.unsaved")} />
              ) : null}
              {successMessage ? <FormStatus tone="success" message={successMessage} /> : null}
              {save.isError ? (
                <FormStatus
                  tone="danger"
                  message={
                    save.error instanceof ApiError && save.error.status === 409
                      ? t("settings.generation.actions.conflict")
                      : saveError
                  }
                />
              ) : null}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="ghost"
                size="lg"
                onClick={resetForm}
                disabled={!dirty || save.isPending}
                aria-label={t("settings.generation.actions.reset")}
              >
                <RotateCcw size={15} aria-hidden />
                {t("settings.generation.actions.reset")}
              </Button>
              <Button
                type="button"
                loading={save.isPending}
                disabled={!dirty}
                onClick={submit}
                size="lg"
                aria-label={t("settings.generation.actions.save")}
              >
                <Save size={15} aria-hidden />
                {save.isPending
                  ? t("settings.generation.actions.saving")
                  : t("settings.generation.actions.save")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function ProfileChips({ profile }: { profile: GenerationProfileStatusData }) {
  return (
    <span className="mt-2 flex flex-wrap gap-1">
      {profile.recommended_for.slice(0, 2).map((item) => (
        <span
          key={item}
          className="inline-flex min-h-5 items-center rounded bg-muted/20 px-1.5 text-[11px] text-muted"
        >
          {recommendedForLabel(item)}
        </span>
      ))}
      {profile.structured_output ? (
        <span className="inline-flex min-h-5 items-center rounded bg-info-bg px-1.5 text-[11px] font-medium text-info">
          {t("settings.generation.structuredOutput")}
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

function orderedProfiles(
  profiles: GenerationProfileStatusData[]
): GenerationProfileStatusData[] {
  const byName = new Map(profiles.map((item) => [item.name, item]));
  const ordered = PROFILE_ORDER.map((name) => byName.get(name)).filter(
    (item): item is GenerationProfileStatusData => Boolean(item)
  );
  return ordered.length ? ordered : profiles;
}

function profileLabel(name: GenerationProfileName) {
  // 未知 profile(将来の backend 追加など)でも空白表示にせず名称へフォールバック。
  return t(`settings.generation.profile.${name}` as I18nKey) ?? name;
}

function profileDescription(name: GenerationProfileName) {
  return t(`settings.generation.profile.${name}.description` as I18nKey) ?? "";
}

function contractLabel(mode: GenerationProfileStatusData["contract_mode"]) {
  return t(`settings.generation.contract.${mode}` as I18nKey) ?? mode;
}

function recommendedForLabel(value: string) {
  return t(`settings.generation.recommended.${value}` as I18nKey) ?? value;
}
