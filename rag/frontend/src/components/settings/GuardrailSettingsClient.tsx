"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RotateCcw, Save, ShieldAlert } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type GuardrailPolicyName,
  type GuardrailPolicyStatusData,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useGuardrailSettings, useUpdateGuardrailSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

const POLICY_ORDER: GuardrailPolicyName[] = ["standard", "strict", "lenient", "regulated"];

/** 安全チェックの現在設定を管理する設定画面。 */
export function GuardrailSettingsClient() {
  const query = useGuardrailSettings();
  const save = useUpdateGuardrailSettings();
  const [policy, setPolicy] = useState<GuardrailPolicyName | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    if (query.data && !save.isPending) {
      setPolicy(query.data.policy);
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
            query.error instanceof ApiError ? query.error.message : t("settings.guardrail.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const settings = query.data;
  if (!settings || !policy) return null;

  const dirty = policy !== settings.policy;
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.guardrail.saveError");
  const policies = orderedPolicies(settings.policies);

  function selectPolicy(next: GuardrailPolicyName) {
    save.reset();
    setSuccessMessage(null);
    setPolicy(next);
  }

  function resetForm() {
    save.reset();
    setSuccessMessage(null);
    setPolicy(settings.policy);
  }

  function submit() {
    if (!policy) return;
    save.mutate(
      { policy },
      {
        onSuccess: (data) => {
          setPolicy(data.policy);
          setSuccessMessage(t("settings.guardrail.actions.saved"));
        },
        onError: () => setSuccessMessage(null),
      }
    );
  }

  return (
    <div className="space-y-5 p-8">
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
          <div className="space-y-2">
            <div className="text-sm font-medium text-foreground">
              {t("settings.guardrail.policy")}
            </div>
            <div
              role="radiogroup"
              aria-label={t("settings.guardrail.policy")}
              className="grid grid-cols-1 gap-2 md:grid-cols-2"
            >
              {policies.map((item) => {
                const selected = policy === item.name;
                return (
                  <button
                    key={item.name}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    disabled={save.isPending}
                    onClick={() => selectPolicy(item.name)}
                    className={cn(
                      "min-h-[104px] rounded-md border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
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
                  </button>
                );
              })}
            </div>
          </div>
          <dl className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <RuntimeFact label={t("settings.guardrail.policy")} value={policyLabel(policy)} />
            <RuntimeFact
              label={t("settings.guardrail.groundingOverlap")}
              value={String(settings.grounding_min_overlap)}
            />
            <RuntimeFact
              label={t("settings.guardrail.groundingRatio")}
              value={settings.grounding_min_ratio.toFixed(2)}
            />
          </dl>
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
      <span className="inline-flex min-h-5 items-center rounded bg-muted px-1.5 text-[11px] text-muted">
        {t("settings.guardrail.groundingOverlap")} {policy.grounding_min_overlap}
      </span>
      <span className="inline-flex min-h-5 items-center rounded bg-muted px-1.5 text-[11px] text-muted">
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

function policyLabel(name: GuardrailPolicyName) {
  return t(`settings.guardrail.policy.${name}` as I18nKey);
}

function policyDescription(name: GuardrailPolicyName) {
  return t(`settings.guardrail.policy.${name}.description` as I18nKey);
}
