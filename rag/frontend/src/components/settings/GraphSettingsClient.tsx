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
  TimedLoadingState,
  FormSkeleton,
} from "@engchina/production-ready-ui";
import { useState } from "react";
import { CheckCircle2, RotateCcw, Save, Share2 } from "lucide-react";

import { ApiErrorState } from "@/components/StateViews";
import { ApiError, type GraphProfileName, type GraphProfileStatusData } from "@/lib/api";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useValuesChanged } from "@/lib/render-sync";
import { t, type I18nKey } from "@/lib/i18n";
import { useGraphSettings, useUpdateGraphSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

const PROFILE_ORDER: GraphProfileName[] = ["off", "entities"];
// 全体の既定(backend の Settings.rag_graph_profile の既定)。選択肢に「既定」と文字で添える。
const DEFAULT_PROFILE: GraphProfileName = "off";

/**
 * 関係情報(文書と章・節の見出しのつながり)を取込のときに構築するかを選ぶ設定画面(#621)。
 * 構築した関係情報はナレッジベースの「関係情報グラフ」で見るだけで、回答の検索には使わない。
 */
export function GraphSettingsClient() {
  const query = useGraphSettings();
  const save = useUpdateGraphSettings();
  const [profile, setProfile] = useState<GraphProfileName | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // server 値が変わったレンダー(初回取得・保存成功)でだけ、選択を server 値に戻す。
  // 保存中フラグを条件に入れると、保存に失敗したときも未保存の選択が消えてしまう(#274)。
  const serverChanged = useValuesChanged([query.data]);
  if (serverChanged && query.data) {
    setProfile(query.data.profile);
  }

  // 未保存の選択があるときだけ、サイドナビ・内部リンク・再読込での離脱を確認する。
  useLeaveGuard(Boolean(query.data && profile !== null && profile !== query.data.profile));

  if (query.isPending) {
    return (
      <PageBody wide>
        <TimedLoadingState
          label={t("settings.loading")}
          operationKey="settings-graph-load"
          placement="page"
          testId="settings-graph-loading"
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
          fallback={t("settings.graph.loadError")}
          onRetry={() => void query.refetch()}
        />
      </PageBody>
    );
  }

  const settings = query.data;
  if (!settings || !profile) return null;

  const dirty = profile !== settings.profile;
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.graph.saveError");
  const profiles = orderedProfiles(settings.profiles);

  function selectProfile(next: GraphProfileName) {
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
          setSuccessMessage(t("settings.graph.actions.saved"));
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
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
              <Share2 size={20} aria-hidden />
            </div>
            <div className="min-w-0">
              <CardTitle>{t("settings.graph.overview.title")}</CardTitle>
              <CardDescription>{t("settings.graph.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <div className="text-sm font-medium text-fg" id="settings-graph-profile-label">
              {t("settings.graph.profile")}
            </div>
            <div
              role="radiogroup"
              aria-labelledby="settings-graph-profile-label"
              className="grid grid-cols-1 gap-2 md:grid-cols-2"
            >
              {profiles.map((item) => {
                const selected = profile === item.name;
                return (
                  <div key={item.name} className="relative min-w-0">
                    <input
                      id={`settings-graph-profile-${item.name}`}
                      className="peer absolute inset-0 z-10 cursor-pointer opacity-0 disabled:cursor-not-allowed"
                      type="radio"
                      name="settings-graph-profile"
                      value={item.name}
                      checked={selected}
                      disabled={save.isPending}
                      onChange={() => selectProfile(item.name)}
                    />
                    <label
                      htmlFor={`settings-graph-profile-${item.name}`}
                      className={cn(
                        "block h-full cursor-pointer rounded-md border px-3 py-2 text-left transition-colors peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-focus-ring peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
                        selected
                          ? "border-accent-emphasis bg-accent-subtle text-fg"
                          : "border-border bg-surface text-fg peer-hover:bg-surface-hover"
                      )}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-semibold">{profileLabel(item.name)}</span>
                          {item.name === DEFAULT_PROFILE ? (
                            <span className="inline-flex min-h-5 items-center rounded border border-border px-1.5 text-xs text-fg-muted">
                              {t("settings.graph.defaultTag")}
                            </span>
                          ) : null}
                        </span>
                        {selected ? (
                          <CheckCircle2 size={16} className="shrink-0 text-accent-fg" aria-hidden />
                        ) : null}
                      </span>
                      <span className="mt-1 block text-xs leading-relaxed text-fg-muted">
                        {profileDescription(item.name)}
                      </span>
                    </label>
                  </div>
                );
              })}
            </div>
          </div>
          <FormStatus tone="info" message={t("settings.graph.rebuildHint")} />
          <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
            <div className="min-h-6">
              {dirty ? (
                <FormStatus tone="warning" message={t("settings.graph.actions.unsaved")} />
              ) : null}
              {successMessage ? <FormStatus tone="success" message={successMessage} /> : null}
              {save.isError ? <FormStatus tone="danger" message={saveError} /> : null}
            </div>
            {/* カードの末尾の操作行: 保存は右端の primary、変更を破棄はその左（#618）。フォームの末尾なので lg（#613）。 */}
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="secondary"
                size="lg"
                onClick={resetForm}
                disabled={!dirty || save.isPending}
                aria-label={t("settings.graph.actions.reset")} icon={RotateCcw}>
                {t("settings.graph.actions.reset")}
              </Button>
              <Button
                type="button"
                size="lg"
                loading={save.isPending}
                disabled={!dirty}
                onClick={submit}
                aria-label={t("settings.graph.actions.save")} icon={Save}>
                {t("settings.graph.actions.save")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </PageBody>
  );
}

function orderedProfiles(profiles: GraphProfileStatusData[]): GraphProfileStatusData[] {
  const byName = new Map(profiles.map((item) => [item.name, item]));
  const ordered = PROFILE_ORDER.map((name) => byName.get(name)).filter(
    (item): item is GraphProfileStatusData => Boolean(item)
  );
  return ordered.length ? ordered : profiles;
}

function profileLabel(name: GraphProfileName) {
  return t(`settings.graph.profile.${name}` as I18nKey);
}

function profileDescription(name: GraphProfileName) {
  return t(`settings.graph.profile.${name}.description` as I18nKey);
}
