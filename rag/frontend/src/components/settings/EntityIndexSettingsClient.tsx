import {
  FormActionBar,
  FormSkeleton,
  FormStatus,
  PageBody,
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  RadioOptionGroup,
  StatusBadge,
  TimedLoadingState,
} from "@production-ready/ui";
import { Tags, RotateCcw, Save } from "lucide-react";
import { useState } from "react";

import { ApiErrorState } from "@/components/StateViews";
import { ApiError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useEntityIndexSettings, useUpdateEntityIndexSettings } from "@/lib/queries";
import { useValuesChanged } from "@/lib/render-sync";
import { toast } from "@/lib/toast";

/** 実体・別名索引を構築する全体の既定。文書レシピの明示設定は変更しない。 */
export function EntityIndexSettingsClient() {
  const query = useEntityIndexSettings();
  const save = useUpdateEntityIndexSettings();
  const [enabled, setEnabled] = useState(false);
  const serverChanged = useValuesChanged([query.data]);
  if (serverChanged && query.data) setEnabled(query.data.enabled);
  const dirty = Boolean(query.data && enabled !== query.data.enabled);
  useLeaveGuard(dirty, save.isPending);

  if (query.isPending) {
    return (
      <PageBody wide>
        <TimedLoadingState label={t("settings.loading")} operationKey="settings-entity-index-load" placement="page">
          <FormSkeleton />
        </TimedLoadingState>
      </PageBody>
    );
  }
  if (query.isError) {
    return (
      <PageBody wide>
        <ApiErrorState error={query.error} fallback={t("settings.entityIndex.error")} onRetry={() => void query.refetch()} />
      </PageBody>
    );
  }

  return (
    <PageBody wide>
      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div data-surface-tint="info" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
              <Tags size={20} aria-hidden />
            </div>
            <div className="min-w-0">
              <CardTitle>{t("settings.entityIndex.title")}</CardTitle>
              <CardDescription>{t("settings.entityIndex.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <RadioOptionGroup<"off" | "on">
            id="entity-index-build"
            legend={t("settings.entityIndex.profile")}
            options={[
              {
                value: "off",
                label: t("settings.entityIndex.profile.off"),
                description: t("settings.entityIndex.off.description"),
              },
              {
                value: "on",
                label: t("settings.entityIndex.profile.on"),
                // 全体の既定(backend の Settings.rag_entity_index_enabled の既定 True)に「既定」と添える。
                badges: <StatusBadge variant="neutral" icon={false} label={t("settings.entityIndex.defaultTag")} />,
                description: t("settings.entityIndex.on.description"),
              },
            ]}
            value={enabled ? "on" : "off"}
            onValueChange={(next) => { save.reset(); setEnabled(next === "on"); }}
            disabled={save.isPending}
          />
          <FormStatus tone="info" message={t("settings.entityIndex.hint")} />
          <FormActionBar
            ariaLabel={t("settings.entityIndex.actions")}
            primaryActions={[{
              id: "save", label: t("settings.entityIndex.actions.save"), icon: Save,
              loading: save.isPending, disabled: !dirty,
              onClick: () => save.mutate({ enabled }, { onSuccess: () => toast.success(t("settings.entityIndex.saved")) }),
            }]}
            secondaryActions={[{
              id: "reset", label: t("settings.entityIndex.actions.reset"), icon: RotateCcw,
              disabled: !dirty || save.isPending,
              onClick: () => { save.reset(); setEnabled(query.data?.enabled ?? false); },
            }]}
            status={save.isError ? <FormStatus tone="danger" message={save.error instanceof ApiError ? save.error.message : t("settings.entityIndex.error")} /> : dirty ? <FormStatus tone="warning" message={t("settings.entityIndex.actions.unsaved")} /> : null}
          />
        </CardContent>
      </Card>
    </PageBody>
  );
}
