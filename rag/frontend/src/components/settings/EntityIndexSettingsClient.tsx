import {
  FormActionBar,
  FormSkeleton,
  FormStatus,
  PageBody,
  SaveErrorBanner,
  Section,
  Switch,
  TimedLoadingState,
} from "@production-ready/ui";
import { RotateCcw, Save } from "lucide-react";
import { useState } from "react";

import { ApiErrorState } from "@/components/StateViews";
import { ApiError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useEntityIndexSettings, useUpdateEntityIndexSettings } from "@/lib/queries";
import { useValuesChanged } from "@/lib/render-sync";
import { toast } from "@/lib/toast";

/** 名称・別名の索引を構築する全体の既定。文書レシピの明示設定は変更しない。 */
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
      <Section title={t("nav.settingsEntityIndex")}>
        <div className="space-y-4">
          <p className="text-sm text-fg-muted">{t("settings.entityIndex.description")}</p>
          <div className="flex items-start justify-between gap-4">
            <label htmlFor="entity-index-enabled" className="text-sm font-medium text-fg">
              {t("settings.entityIndex.enabled")}
            </label>
            <Switch
              id="entity-index-enabled"
              checked={enabled}
              disabled={save.isPending}
              aria-describedby="entity-index-hint"
              onCheckedChange={(value) => { save.reset(); setEnabled(value); }}
            />
          </div>
          <p id="entity-index-hint" className="text-sm text-fg-muted">{t("settings.entityIndex.hint")}</p>
          {save.isError ? (
            <SaveErrorBanner message={save.error instanceof ApiError ? save.error.message : t("settings.entityIndex.error")} />
          ) : null}
          <FormActionBar
            ariaLabel={t("settings.entityIndex.actions")}
            primaryActions={[{
              id: "save", label: t("settings.graph.actions.save"), icon: Save,
              loading: save.isPending, disabled: !dirty,
              onClick: () => save.mutate({ enabled }, { onSuccess: () => toast.success(t("settings.entityIndex.saved")) }),
            }]}
            secondaryActions={[{
              id: "reset", label: t("settings.graph.actions.reset"), icon: RotateCcw,
              disabled: !dirty || save.isPending,
              onClick: () => { save.reset(); setEnabled(query.data?.enabled ?? false); },
            }]}
            status={dirty ? <FormStatus tone="warning" message={t("settings.graph.actions.unsaved")} /> : null}
          />
        </div>
      </Section>
    </PageBody>
  );
}
