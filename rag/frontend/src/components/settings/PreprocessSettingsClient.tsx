"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RotateCcw, Save, Shuffle } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type PreprocessProfileName,
  type PreprocessProfileStatusData,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { usePreprocessSettings, useUpdatePreprocessSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

const PROFILE_ORDER: PreprocessProfileName[] = [
  "passthrough",
  "text_normalize",
  "office_to_pdf",
  "pdf_to_page_images",
  "auto",
];

/** 前処理(Preprocess)アダプター(parse 前の原本変換)の runtime 設定を管理する設定画面。 */
export function PreprocessSettingsClient() {
  const query = usePreprocessSettings();
  const save = useUpdatePreprocessSettings();
  const [profile, setProfile] = useState<PreprocessProfileName | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    if (query.data && !save.isPending) {
      setProfile(query.data.profile);
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
            query.error instanceof ApiError
              ? query.error.message
              : t("settings.preprocess.loadError")
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
    save.error instanceof ApiError ? save.error.message : t("settings.preprocess.saveError");
  const profiles = orderedProfiles(settings.profiles);

  function choose(next: PreprocessProfileName) {
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
      { profile },
      {
        onSuccess: (data) => {
          setProfile(data.profile);
          setSuccessMessage(t("settings.preprocess.actions.saved"));
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
              <Shuffle size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.preprocess.overview.title")}</CardTitle>
              <CardDescription>{t("settings.preprocess.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <div className="text-sm font-medium text-foreground">
              {t("settings.preprocess.profile")}
            </div>
            <div
              role="radiogroup"
              aria-label={t("settings.preprocess.profile")}
              className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3"
            >
              {profiles.map((status) => {
                const selected = profile === status.name;
                return (
                  <button
                    key={status.name}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    disabled={save.isPending}
                    onClick={() => choose(status.name)}
                    className={cn(
                      "min-h-[104px] rounded-md border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                      selected
                        ? "border-primary bg-primary/10 text-foreground"
                        : "border-border bg-card text-foreground hover:bg-background"
                    )}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold">{profileLabel(status.name)}</span>
                      {selected ? (
                        <CheckCircle2 size={15} className="shrink-0 text-primary" aria-hidden />
                      ) : null}
                    </span>
                    <span className="mt-1 block text-xs leading-relaxed text-muted">
                      {profileDescription(status.name)}
                    </span>
                    <span className="mt-2 flex flex-wrap gap-1.5">
                      {status.in_process ? (
                        <Badge tone="info">{t("settings.preprocess.inProcess")}</Badge>
                      ) : null}
                      {status.requires_service ? (
                        <Badge tone="muted">{t("settings.preprocess.requiresService")}</Badge>
                      ) : null}
                      {!status.available ? (
                        <Badge tone="warning">{t("settings.preprocess.unavailable")}</Badge>
                      ) : null}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
          <dl className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <RuntimeFact
              label={t("settings.preprocess.serviceEnabled")}
              value={
                settings.service_enabled
                  ? t("settings.preprocess.serviceEnabled.on")
                  : t("settings.preprocess.serviceEnabled.off")
              }
            />
            <RuntimeFact
              label={t("settings.preprocess.canonicalPrefix")}
              value={settings.canonical_artifact_prefix}
            />
            <RuntimeFact label={t("settings.preprocess.source")} value="runtime" />
          </dl>
          <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
            <div className="min-h-6">
              {dirty ? (
                <FormStatus tone="warning" message={t("settings.preprocess.actions.unsaved")} />
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
                aria-label={t("settings.preprocess.actions.reset")}
              >
                <RotateCcw size={15} aria-hidden />
                {t("settings.preprocess.actions.reset")}
              </Button>
              <Button
                type="button"
                loading={save.isPending}
                disabled={!dirty}
                onClick={submit}
                aria-label={t("settings.preprocess.actions.save")}
              >
                <Save size={15} aria-hidden />
                {save.isPending
                  ? t("settings.preprocess.actions.saving")
                  : t("settings.preprocess.actions.save")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function Badge({ tone, children }: { tone: "info" | "muted" | "warning"; children: string }) {
  const toneClass =
    tone === "info"
      ? "bg-info-bg text-info"
      : tone === "warning"
        ? "bg-warning-bg text-warning"
        : "bg-muted/30 text-muted";
  return (
    <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-medium", toneClass)}>
      {children}
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
  profiles: PreprocessProfileStatusData[]
): PreprocessProfileStatusData[] {
  const byName = new Map(profiles.map((status) => [status.name, status]));
  const ordered = PROFILE_ORDER.map((name) => byName.get(name)).filter(
    (status): status is PreprocessProfileStatusData => Boolean(status)
  );
  return ordered.length ? ordered : profiles;
}

function profileLabel(profile: PreprocessProfileName) {
  return t(`settings.preprocess.profile.${profile}` as I18nKey);
}

function profileDescription(profile: PreprocessProfileName) {
  return t(`settings.preprocess.profile.${profile}.description` as I18nKey);
}

export default PreprocessSettingsClient;
