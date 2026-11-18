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
  RadioOptionGroup,
  StatusBadge,
  TimedLoadingState,
  FormSkeleton,
} from "@production-ready/ui";
import { useState } from "react";
import { RotateCcw, Save, Share2 } from "lucide-react";

import { ApiErrorState } from "@/components/StateViews";
import { ApiError, type GraphProfileName, type GraphProfileStatusData } from "@/lib/api";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useValuesChanged } from "@/lib/render-sync";
import { t, type I18nKey } from "@/lib/i18n";
import { useGraphSettings, useUpdateGraphSettings } from "@/lib/queries";
import { toast } from "@/lib/toast";

const PROFILE_ORDER: GraphProfileName[] = ["off", "entities"];
// 全体の既定(backend の Settings.rag_graph_profile の既定)。選択肢に「既定」と文字で添える。
const DEFAULT_PROFILE: GraphProfileName = "off";

/**
 * 文書構造索引を取込のときに構築するかを選ぶ設定画面。
 * 構築した索引はナレッジベースの文書構造グラフで確認する。
 */
export function GraphSettingsClient() {
  const query = useGraphSettings();
  const save = useUpdateGraphSettings();
  const [profile, setProfile] = useState<GraphProfileName | null>(null);

  // server 値が変わったレンダー(初回取得・保存成功)でだけ、選択を server 値に戻す。
  // 保存中フラグを条件に入れると、保存に失敗したときも未保存の選択が消えてしまう(#274)。
  const serverChanged = useValuesChanged([query.data]);
  if (serverChanged && query.data) {
    setProfile(query.data.profile);
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
          setProfile(data.profile);
          // 保存の成功は Toast、失敗は操作の行の FormStatus（messaging.md §10.2）。
          toast.success(t("settings.graph.actions.saved"));
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
              <Share2 size={20} aria-hidden />
            </div>
            <div className="min-w-0">
              <CardTitle>{t("settings.graph.overview.title")}</CardTitle>
              <CardDescription>{t("settings.graph.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <RadioOptionGroup<GraphProfileName>
            id="settings-graph-profile"
            legend={t("settings.graph.profile")}
            options={profiles.map((item) => ({
              value: item.name,
              label: profileLabel(item.name),
              badges:
                item.name === DEFAULT_PROFILE ? (
                  <StatusBadge variant="neutral" icon={false} label={t("settings.graph.defaultTag")} />
                ) : undefined,
              description: profileDescription(item.name),
            }))}
            value={profile}
            onValueChange={selectProfile}
            disabled={save.isPending}
          />
          <FormStatus tone="info" message={t("settings.graph.rebuildHint")} />
          <FormActionBar
            ariaLabel={t("settings.graph.actions.label")}
            primaryActions={[
              {
                id: "save",
                label: t("settings.graph.actions.save"),
                icon: Save,
                loading: save.isPending,
                disabled: !dirty,
                onClick: submit,
              },
            ]}
            secondaryActions={[
              {
                id: "reset",
                label: t("settings.graph.actions.reset"),
                icon: RotateCcw,
                disabled: !dirty || save.isPending,
                onClick: resetForm,
              },
            ]}
            status={
              save.isError ? (
                <FormStatus tone="danger" message={saveError} />
              ) : dirty ? (
                <FormStatus tone="warning" message={t("settings.graph.actions.unsaved")} />
              ) : null
            }
          />
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
