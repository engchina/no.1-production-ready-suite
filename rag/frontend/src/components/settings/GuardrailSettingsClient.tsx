"use client";

import {
  PageBody, Card, CardContent, CardDescription, CardHeader, CardTitle,
  FormActionBar, FormStatus, TimedLoadingState, FormSkeleton, ButtonLink,
} from "@production-ready/ui";
import { useState } from "react";
import { CheckCircle2, RotateCcw, Save, ShieldCheck } from "lucide-react";

import { ApiErrorState } from "@/components/StateViews";
import { ApiError } from "@/lib/api";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useValuesChanged } from "@/lib/render-sync";
import { t } from "@/lib/i18n";
import { useGuardrailSettings, useUpdateGuardrailSettings } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

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
          <div className="flex items-start gap-3">
            <div data-surface-tint="info" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
              <ShieldCheck size={20} aria-hidden />
            </div>
            <div className="min-w-0">
              <CardTitle>{t("settings.guardrail.ociLabel")}</CardTitle>
              <CardDescription>{t("settings.guardrail.ociDescription")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <div className="text-sm font-medium text-fg" id="settings-guardrail-enabled-label">
              {t("settings.guardrail.selection")}
            </div>
            <div
              role="radiogroup"
              aria-labelledby="settings-guardrail-enabled-label"
              className="grid grid-cols-1 gap-2 md:grid-cols-2"
            >
              {[false, true].map((value) => {
                const selected = enabled === value;
                return (
                  <div key={String(value)} className="relative min-w-0">
                    <input
                      id={`settings-guardrail-enabled-${String(value)}`}
                      className="peer absolute inset-0 z-10 cursor-pointer opacity-0 disabled:cursor-not-allowed"
                      type="radio"
                      name="settings-guardrail-enabled"
                      value={String(value)}
                      checked={selected}
                      disabled={save.isPending}
                      onChange={() => { save.reset(); setEnabled(value); }}
                    />
                    <label
                      data-surface-tint={selected ? "accent" : undefined}
                      htmlFor={`settings-guardrail-enabled-${String(value)}`}
                      className={cn(
                        "block h-full cursor-pointer rounded-md border px-3 py-2 text-left transition-colors peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-focus-ring peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
                        selected
                          ? "border-accent-emphasis bg-accent-subtle text-fg"
                          : "border-border bg-surface text-fg peer-hover:bg-surface-hover"
                      )}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-semibold">{t(value ? "settings.guardrail.enabled" : "settings.guardrail.disabled")}</span>
                          {!value ? (
                            <span className="inline-flex min-h-5 items-center rounded border border-border px-1.5 text-xs text-fg-muted">
                              {t("settings.guardrail.defaultTag")}
                            </span>
                          ) : null}
                        </span>
                        {selected ? (
                          <CheckCircle2 size={16} className="shrink-0 text-accent-fg" aria-hidden />
                        ) : null}
                      </span>
                      <span className="mt-1 block text-xs leading-relaxed text-fg-muted">
                        {t(value ? "settings.guardrail.enabled.description" : "settings.guardrail.disabled.description")}
                      </span>
                    </label>
                  </div>
                );
              })}
            </div>
          </div>
          <p className="text-sm leading-relaxed text-fg-muted">{t("settings.guardrail.resultHandling")}</p>
          <p>
            <a href="https://docs.oracle.com/en-us/iaas/Content/generative-ai/guardrails.htm" target="_blank" rel="noopener noreferrer" className="text-sm text-accent-fg underline underline-offset-2">
            {t("settings.guardrail.officialDocs")}
            </a>
          </p>
          <FormStatus tone="info" message={t("settings.guardrail.overview.description")} />
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
