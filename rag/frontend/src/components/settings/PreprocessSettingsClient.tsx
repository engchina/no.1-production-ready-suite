"use client";

import {
  PageBody,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FormActionBar,
  FormStatus,
  TimedLoadingState,
  FormSkeleton,
  RadioOptionGroup,
  surfaceTintFor,
} from "@production-ready/ui";
import { useState } from "react";
import { RotateCcw, Save, Shuffle } from "lucide-react";

import { ApiErrorState } from "@/components/StateViews";
import {
  ApiError,
  type PreprocessProfileName,
  type PreprocessProfileStatusData,
} from "@/lib/api";
import { useLeaveGuard } from "@/lib/leave-guard";
import { t, type I18nKey } from "@/lib/i18n";
import { usePreprocessSettings, useUpdatePreprocessSettings } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

const PROFILE_ORDER: PreprocessProfileName[] = [
  "passthrough",
  "office_to_pdf",
  "csv_to_jsonl",
  "excel_to_jsonl",
  "word_to_jsonl",
];

/** 前処理(Preprocess)アダプター(parse 前の原本変換)の runtime 設定を管理する設定画面。 */
export function PreprocessSettingsClient() {
  const query = usePreprocessSettings();
  const save = useUpdatePreprocessSettings();
  const [profile, setProfile] = useState<PreprocessProfileName | null>(null);
  // 直前に取り込んだ server 値。server 値が変わったレンダーだけ、未編集なら選択をそろえる。
  // 保存の開始・終了（isPending）では選択を戻さない（失敗しても選択を残す。#956）。
  const [base, setBase] = useState<PreprocessProfileName | null>(null);
  const serverProfile = query.data?.profile ?? null;
  if (serverProfile !== null && serverProfile !== base) {
    setBase(serverProfile);
    if (profile === null || profile === base) setProfile(serverProfile);
  }

  // 未保存の選択があるときは離脱を確認し、保存中は離脱を止める。
  useLeaveGuard(
    Boolean(query.data && profile !== null && profile !== query.data.profile),
    save.isPending
  );

  if (query.isPending) {
    return (
      <PageBody wide>
        <TimedLoadingState
          label={t("settings.loading")}
          operationKey="settings-preprocess-load"
          placement="page"
          testId="settings-preprocess-loading"
        >
          <FormSkeleton />
        </TimedLoadingState>
      </PageBody>
    );
  }

  if (query.isError) {
    return (
      <PageBody wide>
        <ApiErrorState
          error={query.error}
          fallback={t("settings.preprocess.loadError")}
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
    setProfile(next);
  }

  function resetForm() {
    save.reset();
    setProfile(settings.profile);
  }

  function submit() {
    if (!profile || save.isPending) return;
    save.mutate(
      { profile },
      {
        onSuccess: (data) => {
          setBase(data.profile);
          setProfile(data.profile);
          // 保存の成功は Toast、失敗は操作の行の FormStatus（messaging.md §10.2）。
          toast.success(t("settings.preprocess.actions.saved"));
        },
      }
    );
  }

  return (
    <PageBody wide>
      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div data-surface-tint="info" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
              <Shuffle size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.preprocess.overview.title")}</CardTitle>
              <CardDescription>{t("settings.preprocess.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <RadioOptionGroup<PreprocessProfileName>
            id="settings-preprocess-profile"
            legend={t("settings.preprocess.profile")}
            options={profiles.map((status) => ({
              value: status.name,
              label: profileLabel(status.name),
              badges: profileBadges(status),
              description: profileDescription(status.name),
            }))}
            value={profile}
            onValueChange={choose}
            disabled={save.isPending}
          />
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
          <FormActionBar
            ariaLabel={t("settings.preprocess.actions.label")}
            primaryActions={[
              {
                id: "save",
                label: t("settings.preprocess.actions.save"),
                icon: Save,
                loading: save.isPending,
                disabled: !dirty,
                onClick: submit,
              },
            ]}
            secondaryActions={[
              {
                id: "reset",
                label: t("settings.preprocess.actions.reset"),
                icon: RotateCcw,
                disabled: !dirty || save.isPending,
                onClick: resetForm,
              },
            ]}
            status={
              save.isError ? (
                <FormStatus tone="danger" message={saveError} />
              ) : dirty ? (
                <FormStatus tone="warning" message={t("settings.preprocess.actions.unsaved")} />
              ) : null
            }
          />
        </CardContent>
      </Card>
    </PageBody>
  );
}

/** 選択肢の名前の横のバッジ（無ければ undefined）。 */
function profileBadges(status: PreprocessProfileStatusData) {
  if (!status.in_process && !status.requires_service && status.available) return undefined;
  return (
    <>
      {status.in_process ? <Badge tone="info">{t("settings.preprocess.inProcess")}</Badge> : null}
      {status.requires_service ? (
        <Badge tone="muted">{t("settings.preprocess.requiresService")}</Badge>
      ) : null}
      {!status.available ? <Badge tone="warning">{t("settings.preprocess.unavailable")}</Badge> : null}
    </>
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
    <span data-surface-tint={surfaceTintFor(toneClass)} className={cn("rounded px-1.5 py-0.5 text-xs font-medium", toneClass)}>
      {children}
    </span>
  );
}

function RuntimeFact({ label, value }: { label: string; value: string }) {
  return (
    <div data-surface-tint="neutral" className="rounded-md border border-border bg-surface-hover p-3">
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
