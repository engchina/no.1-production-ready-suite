"use client";

import { useEffect, useState } from "react";
import { Boxes, CheckCircle2, RotateCcw, Save } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type VectorIndexProfileName,
  type VectorIndexProfileStatusData,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useUpdateVectorIndexSettings, useVectorIndexSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

const PROFILE_ORDER: VectorIndexProfileName[] = ["balanced", "accurate", "fast"];

/** Vector Index アダプター(索引/検索精度)の runtime 設定を管理する設定画面。 */
export function VectorIndexSettingsClient() {
  const query = useVectorIndexSettings();
  const save = useUpdateVectorIndexSettings();
  const [profile, setProfile] = useState<VectorIndexProfileName | null>(null);
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
              : t("settings.vectorIndex.loadError")
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
    save.error instanceof ApiError ? save.error.message : t("settings.vectorIndex.saveError");
  const profiles = orderedProfiles(settings.profiles);
  const selectedProfile = profiles.find((item) => item.name === profile);

  function selectProfile(next: VectorIndexProfileName) {
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
          setSuccessMessage(t("settings.vectorIndex.actions.saved"));
        },
        onError: () => setSuccessMessage(null),
      }
    );
  }

  const showReprovision = requiresReprovision(selectedProfile);

  return (
    <div className="space-y-5 p-8">
      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
              <Boxes size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.vectorIndex.overview.title")}</CardTitle>
              <CardDescription>{t("settings.vectorIndex.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <div className="text-sm font-medium text-foreground">
              {t("settings.vectorIndex.profile")}
            </div>
            <div
              role="radiogroup"
              aria-label={t("settings.vectorIndex.profile")}
              className="grid grid-cols-1 gap-2 md:grid-cols-3"
            >
              {profiles.map((item) => {
                const selected = profile === item.name;
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
          <dl className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <RuntimeFact
              label={t("settings.vectorIndex.targetAccuracy")}
              value={String(settings.target_accuracy)}
            />
            <RuntimeFact
              label={t("settings.vectorIndex.build")}
              value={`${t("settings.vectorIndex.neighbors")} ${settings.neighbors} / ${t(
                "settings.vectorIndex.efconstruction"
              )} ${settings.efconstruction}`}
            />
            <RuntimeFact label={t("settings.vectorIndex.distance")} value={settings.distance} />
          </dl>
          {showReprovision ? (
            <FormStatus tone="warning" message={t("settings.vectorIndex.reprovision")} />
          ) : null}
          <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
            <div className="min-h-6">
              {dirty ? (
                <FormStatus tone="warning" message={t("settings.vectorIndex.actions.unsaved")} />
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
                aria-label={t("settings.vectorIndex.actions.reset")}
              >
                <RotateCcw size={15} aria-hidden />
                {t("settings.vectorIndex.actions.reset")}
              </Button>
              <Button
                type="button"
                loading={save.isPending}
                disabled={!dirty}
                onClick={submit}
                aria-label={t("settings.vectorIndex.actions.save")}
              >
                <Save size={15} aria-hidden />
                {save.isPending
                  ? t("settings.vectorIndex.actions.saving")
                  : t("settings.vectorIndex.actions.save")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function ProfileChips({ profile }: { profile: VectorIndexProfileStatusData }) {
  return (
    <span className="mt-2 flex flex-wrap gap-1">
      <span className="inline-flex min-h-5 items-center rounded bg-info-bg px-1.5 text-[11px] font-medium text-info">
        {t("settings.vectorIndex.targetAccuracy")} {profile.target_accuracy}
      </span>
      <span className="inline-flex min-h-5 items-center rounded bg-muted px-1.5 text-[11px] text-muted">
        {t("settings.vectorIndex.neighbors")} {profile.neighbors}
      </span>
      <span className="inline-flex min-h-5 items-center rounded bg-muted px-1.5 text-[11px] text-muted">
        {t("settings.vectorIndex.efconstruction")} {profile.efconstruction}
      </span>
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
  profiles: VectorIndexProfileStatusData[]
): VectorIndexProfileStatusData[] {
  const byName = new Map(profiles.map((item) => [item.name, item]));
  const ordered = PROFILE_ORDER.map((name) => byName.get(name)).filter(
    (item): item is VectorIndexProfileStatusData => Boolean(item)
  );
  return ordered.length ? ordered : profiles;
}

function requiresReprovision(profile: VectorIndexProfileStatusData | undefined) {
  if (!profile) return false;
  const balanced = PROFILE_ORDER[0];
  return profile.name !== balanced;
}

function profileLabel(name: VectorIndexProfileName) {
  return t(`settings.vectorIndex.profile.${name}` as I18nKey);
}

function profileDescription(name: VectorIndexProfileName) {
  return t(`settings.vectorIndex.profile.${name}.description` as I18nKey);
}
