"use client";

import {
  PageBody, Card, CardContent, CardDescription, CardHeader, CardTitle,
  FormActionBar, FormStatus, TimedLoadingState, FormSkeleton, Switch, ButtonLink,
} from "@production-ready/ui";
import { useState } from "react";
import { RotateCcw, Save } from "lucide-react";

import { ApiErrorState } from "@/components/StateViews";
import { ApiError } from "@/lib/api";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useValuesChanged } from "@/lib/render-sync";
import { t } from "@/lib/i18n";
import { useGuardrailSettings, useUpdateGuardrailSettings } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";

/** 全体の OCI Guardrails の有効・無効を管理する。 */
export function GuardrailSettingsClient() {
  const query = useGuardrailSettings();
  const save = useUpdateGuardrailSettings();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const serverChanged = useValuesChanged([query.data]);
  if (serverChanged && query.data) setEnabled(query.data.enabled);
  const dirty = enabled !== null && query.data !== undefined && enabled !== query.data.enabled;
  useLeaveGuard(dirty, save.isPending);

  if (query.isPending) return (
    <PageBody wide>
      <TimedLoadingState label={t("settings.loading")} operationKey="settings-guardrail-load" placement="page" testId="settings-guardrail-loading">
        <FormSkeleton />
      </TimedLoadingState>
    </PageBody>
  );
  if (query.isError) return (
    <PageBody wide>
      <ApiErrorState error={query.error} fallback={t("settings.guardrail.loadError")} onRetry={() => void query.refetch()} />
    </PageBody>
  );
  const settings = query.data;
  if (!settings || enabled === null) return null;
  const warning = enabled && !settings.oci_configured
    ? t(settings.oci_warning_code === "oci_guardrails_compartment_missing"
      ? "settings.guardrail.ociWarning.compartmentMissing"
      : "settings.guardrail.ociWarning.credentialsInvalid")
    : null;

  return (
    <PageBody wide>
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.guardrail.overview.title")}</CardTitle>
          <CardDescription>{t("settings.guardrail.overview.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="flex items-start justify-between gap-4 rounded-md border border-border bg-surface p-3">
            <div className="min-w-0">
              <p className="text-sm font-semibold">{t("settings.guardrail.ociLabel")}</p>
              <p id="guardrails-description" className="mt-1 text-sm leading-relaxed text-fg-muted">
                {t("settings.guardrail.ociDescription")}
              </p>
            </div>
            <Switch checked={enabled} disabled={save.isPending} aria-label={t("settings.guardrail.ociLabel")} aria-describedby="guardrails-description"
              onCheckedChange={(value) => { save.reset(); setEnabled(value); }} />
          </div>
          <p className="text-sm leading-relaxed text-fg-muted">{t("settings.guardrail.resultHandling")}</p>
          <a href="https://docs.oracle.com/en-us/iaas/Content/generative-ai/guardrails.htm" target="_blank" rel="noopener noreferrer" className="text-sm text-accent-fg underline underline-offset-2">
            {t("settings.guardrail.officialDocs")}
          </a>
          {warning ? (
            <div className="space-y-2">
              <FormStatus tone="warning" message={warning} />
              <ButtonLink to={APP_ROUTES.settingsOci} variant="secondary">{t("settings.guardrail.ociSettingsLink")}</ButtonLink>
            </div>
          ) : null}
          <FormActionBar ariaLabel={t("settings.guardrail.actions.label")}
            primaryActions={[{ id: "save", label: t("settings.guardrail.actions.save"), icon: Save, loading: save.isPending, disabled: !dirty,
              onClick: () => save.mutate({ enabled }, { onSuccess: (data) => { setEnabled(data.enabled); toast.success(t("settings.guardrail.actions.saved")); } }),
            }]}
            secondaryActions={[{ id: "reset", label: t("settings.guardrail.actions.reset"), icon: RotateCcw, disabled: !dirty || save.isPending,
              onClick: () => { save.reset(); setEnabled(settings.enabled); },
            }]}
            status={save.isError ? <FormStatus tone="danger" message={save.error instanceof ApiError ? save.error.message : t("settings.guardrail.saveError")} />
              : dirty ? <FormStatus tone="warning" message={t("settings.guardrail.actions.unsaved")} /> : null}
          />
        </CardContent>
      </Card>
    </PageBody>
  );
}
