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
import { useEffect, useState } from "react";
import { CheckCircle2, RotateCcw, Save, Shuffle } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
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
  "office_to_pdf",
  "pdf_to_page_images",
  "csv_to_json",
  "excel_to_json",
  "url_to_markdown",
  "image_enhance",
  "pii_redact",
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
              : t("settings.preprocess.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </PageBody>
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
    <PageBody>
      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
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
            <div className="text-sm font-medium text-fg">
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
                      "min-h-[104px] rounded-md border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                      selected
                        ? "border-accent-emphasis bg-accent-subtle text-fg"
                        : "border-border bg-surface text-fg hover:bg-surface-hover"
                    )}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold">{profileLabel(status.name)}</span>
                      {selected ? (
                        <CheckCircle2 size={16} className="shrink-0 text-accent-fg" aria-hidden />
                      ) : null}
                    </span>
                    <span className="mt-1 block text-xs leading-relaxed text-fg-muted">
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
            <RuntimeFact
              label={t("settings.preprocess.source")}
              value={t("settings.common.currentConfig")}
            />
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
                aria-label={t("settings.preprocess.actions.reset")} icon={RotateCcw}>
                {t("settings.preprocess.actions.reset")}
              </Button>
              <Button
                type="button"
                loading={save.isPending}
                disabled={!dirty}
                onClick={submit}
                aria-label={t("settings.preprocess.actions.save")} icon={Save}>
                {t("settings.preprocess.actions.save")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </PageBody>
  );
}

function Badge({ tone, children }: { tone: "info" | "muted" | "warning"; children: string }) {
  const toneClass =
    tone === "info"
      ? "bg-info-subtle text-info-fg"
      : tone === "warning"
        ? "bg-warning-subtle text-warning-fg"
        : "bg-surface-hover text-fg-muted";
  return (
    <span className={cn("rounded px-1.5 py-0.5 text-xs font-medium", toneClass)}>
      {children}
    </span>
  );
}

function RuntimeFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-surface-hover p-3">
      <dt className="text-xs font-medium text-fg-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-semibold text-fg">{value}</dd>
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
