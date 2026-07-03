"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RotateCcw, Save, ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type GuardrailBackend,
  type GuardrailPolicyName,
  type GuardrailPolicyStatusData,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useGuardrailSettings, useUpdateGuardrailSettings } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { cn } from "@/lib/utils";

const POLICY_ORDER: GuardrailPolicyName[] = ["standard", "strict", "lenient", "regulated"];

/** 安全チェックの現在設定を管理する設定画面。 */
export function GuardrailSettingsClient() {
  const query = useGuardrailSettings();
  const save = useUpdateGuardrailSettings();
  const [policy, setPolicy] = useState<GuardrailPolicyName | null>(null);
  const [backend, setBackend] = useState<GuardrailBackend | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    if (query.data) {
      setPolicy(query.data.policy);
      setBackend(query.data.backend);
    }
  }, [query.data]);

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
            query.error instanceof ApiError ? query.error.message : t("settings.guardrail.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const settings = query.data;
  if (!settings || !policy || !backend) return null;

  const dirty = policy !== settings.policy || backend !== settings.backend;
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.guardrail.saveError");
  const policies = orderedPolicies(settings.policies);
  // サマリーは選択中ポリシーの閾値を表示する(未保存時に保存済み値とずれないように)。
  const selectedStatus = policies.find((item) => item.name === policy);
  const summaryOverlap = selectedStatus?.grounding_min_overlap ?? settings.grounding_min_overlap;
  const summaryRatio = selectedStatus?.grounding_min_ratio ?? settings.grounding_min_ratio;
  const ociWarning =
    ociWarningMessage(settings.oci_warning_code) ??
    (backend === "oci_guardrails" && !settings.oci_configured
      ? t("settings.guardrail.ociWarning.credentialsInvalid")
      : null);

  function selectPolicy(next: GuardrailPolicyName) {
    save.reset();
    setSuccessMessage(null);
    setPolicy(next);
  }

  function selectBackend(next: GuardrailBackend) {
    save.reset();
    setSuccessMessage(null);
    setBackend(next);
  }

  function resetForm() {
    save.reset();
    setSuccessMessage(null);
    setPolicy(settings.policy);
    setBackend(settings.backend);
  }

  function submit() {
    if (!policy || !backend) return;
    save.mutate(
      { policy, backend },
      {
        onSuccess: (data) => {
          setPolicy(data.policy);
          setBackend(data.backend);
          setSuccessMessage(t("settings.guardrail.actions.saved"));
        },
        onError: () => setSuccessMessage(null),
      }
    );
  }

  return (
    <div className="space-y-5 p-4 sm:p-6 lg:p-8">
      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-warning-bg text-warning">
              <ShieldAlert size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.guardrail.overview.title")}</CardTitle>
              <CardDescription>{t("settings.guardrail.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          {ociWarning ? (
            <div className="space-y-2">
              <FormStatus tone="warning" message={ociWarning} />
              <Link
                to={APP_ROUTES.settingsOci}
                className="inline-flex min-h-11 items-center rounded-md text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:min-h-9"
              >
                {t("settings.guardrail.ociSettingsLink")}
              </Link>
            </div>
          ) : null}
          <fieldset className="space-y-2" disabled={save.isPending}>
            <legend className="text-sm font-medium text-foreground">
              {t("settings.guardrail.backend")}
            </legend>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {(["local", "oci_guardrails"] as const).map((item) => {
                const selected = backend === item;
                return (
                  <div key={item} className="relative min-w-0">
                    <input
                      id={`guardrail-backend-${item}`}
                      className="peer sr-only"
                      type="radio"
                      name="guardrail-backend"
                      value={item}
                      checked={selected}
                      onChange={() => selectBackend(item)}
                    />
                    <label
                      htmlFor={`guardrail-backend-${item}`}
                      className={cn(
                        "flex min-h-20 cursor-pointer flex-col rounded-md border px-3 py-2 text-left transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
                        selected
                          ? "border-primary bg-primary/10 text-foreground"
                          : "border-border bg-card text-foreground hover:bg-background"
                      )}
                    >
                      <span className="text-sm font-semibold">
                        {t(`settings.guardrail.backend.${item}` as I18nKey)}
                      </span>
                      <span className="mt-1 text-xs leading-relaxed text-muted">
                        {t(`settings.guardrail.backend.${item}.description` as I18nKey)}
                      </span>
                    </label>
                  </div>
                );
              })}
            </div>
          </fieldset>
          <div className="space-y-2">
            <div className="text-sm font-medium text-foreground">
              {t("settings.guardrail.policy")}
            </div>
            <fieldset
              className="grid grid-cols-1 gap-2 md:grid-cols-2"
              disabled={save.isPending}
            >
              <legend className="sr-only">{t("settings.guardrail.policy")}</legend>
              {policies.map((item) => {
                const selected = policy === item.name;
                return (
                  <div key={item.name} className="relative min-w-0">
                    <input
                      id={`guardrail-policy-${item.name}`}
                      className="peer sr-only"
                      type="radio"
                      name="guardrail-policy"
                      value={item.name}
                      checked={selected}
                      onChange={() => selectPolicy(item.name)}
                    />
                    <label
                      htmlFor={`guardrail-policy-${item.name}`}
                      className={cn(
                        "block min-h-[104px] cursor-pointer rounded-md border px-3 py-2 text-left transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
                        selected
                          ? "border-primary bg-primary/10 text-foreground"
                          : "border-border bg-card text-foreground hover:bg-background"
                      )}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="text-sm font-semibold">{policyLabel(item.name)}</span>
                        {selected ? (
                          <CheckCircle2 size={15} className="shrink-0 text-primary" aria-hidden />
                        ) : null}
                      </span>
                      <span className="mt-1 block text-xs leading-relaxed text-muted">
                        {policyDescription(item.name)}
                      </span>
                      <PolicyChips policy={item} />
                    </label>
                  </div>
                );
              })}
            </fieldset>
          </div>
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <RuntimeFact label={t("settings.guardrail.policy")} value={policyLabel(policy)} />
            <RuntimeFact
              label={t("settings.guardrail.groundingOverlap")}
              value={String(summaryOverlap)}
            />
            <RuntimeFact
              label={t("settings.guardrail.groundingRatio")}
              value={summaryRatio.toFixed(2)}
            />
            <RuntimeFact
              label={t("settings.guardrail.maxQueryChars")}
              value={String(settings.max_query_chars)}
            />
          </dl>
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <RuntimeFact
              label={t("settings.guardrail.promptInjection")}
              value={
                settings.block_prompt_injection
                  ? t("settings.guardrail.enabled")
                  : t("settings.guardrail.disabled")
              }
            />
            <RuntimeFact
              label={t("settings.guardrail.piiMask")}
              value={
                settings.mask_sensitive_identifiers
                  ? t("settings.guardrail.enabled")
                  : t("settings.guardrail.disabled")
              }
            />
            <RuntimeFact
              label={t("settings.guardrail.ociReadiness")}
              value={
                settings.oci_configured
                  ? t("settings.guardrail.ready")
                  : t("settings.guardrail.notReady")
              }
            />
          </dl>
          <p className="text-xs leading-relaxed text-muted">
            {t("settings.guardrail.capabilityNote")}
          </p>
          <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
            <div className="min-h-6">
              {dirty ? (
                <FormStatus tone="warning" message={t("settings.guardrail.actions.unsaved")} />
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
                aria-label={t("settings.guardrail.actions.reset")}
              >
                <RotateCcw size={15} aria-hidden />
                {t("settings.guardrail.actions.reset")}
              </Button>
              <Button
                type="button"
                loading={save.isPending}
                disabled={!dirty}
                onClick={submit}
                aria-label={t("settings.guardrail.actions.save")}
              >
                <Save size={15} aria-hidden />
                {save.isPending
                  ? t("settings.guardrail.actions.saving")
                  : t("settings.guardrail.actions.save")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function PolicyChips({ policy }: { policy: GuardrailPolicyStatusData }) {
  return (
    <span className="mt-2 flex flex-wrap gap-1">
      <span className="inline-flex min-h-5 items-center rounded bg-muted/20 px-1.5 text-[11px] text-muted">
        {t("settings.guardrail.groundingOverlap")} {policy.grounding_min_overlap}
      </span>
      <span className="inline-flex min-h-5 items-center rounded bg-muted/20 px-1.5 text-[11px] text-muted">
        {t("settings.guardrail.groundingRatio")} {policy.grounding_min_ratio.toFixed(2)}
      </span>
      {policy.audit_emphasis ? (
        <span className="inline-flex min-h-5 items-center rounded bg-warning-bg px-1.5 text-[11px] font-medium text-warning">
          {t("settings.guardrail.auditEmphasis")}
        </span>
      ) : null}
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

function orderedPolicies(policies: GuardrailPolicyStatusData[]): GuardrailPolicyStatusData[] {
  const byName = new Map(policies.map((item) => [item.name, item]));
  const ordered = POLICY_ORDER.map((name) => byName.get(name)).filter(
    (item): item is GuardrailPolicyStatusData => Boolean(item)
  );
  return ordered.length ? ordered : policies;
}

function ociWarningMessage(code: string | null): string | null {
  if (code === "oci_guardrails_compartment_missing") {
    return t("settings.guardrail.ociWarning.compartmentMissing");
  }
  if (code === "oci_guardrails_credentials_invalid") {
    return t("settings.guardrail.ociWarning.credentialsInvalid");
  }
  return code ? t("settings.guardrail.ociWarning.unavailable") : null;
}

function policyLabel(name: GuardrailPolicyName) {
  return t(`settings.guardrail.policy.${name}` as I18nKey);
}

function policyDescription(name: GuardrailPolicyName) {
  return t(`settings.guardrail.policy.${name}.description` as I18nKey);
}
