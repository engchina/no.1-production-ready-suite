import { Database, Play, RefreshCw, Save, ScrollText } from "lucide-react";
import { Banner, StatusBadge, toast } from "@engchina/production-ready-ui";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError } from "@/components/ui/field-error";
import { Skeleton } from "@/components/ui/skeleton";
import { TimedLoadingState } from "@/components/ProcessingState";
import { ExecutionConfirmationField } from "@/features/nl2sql/components/DbAdminShared";
import { useAuth } from "@/features/security/AuthProvider";
import { MENU_PERMISSIONS } from "@/features/security/menu-permissions";
import { ApiError, type RdfNetworkSettingsData, type RdfNetworkStatus } from "@/lib/api";
import { t } from "@/lib/i18n";
import {
  useApplyRdfNetworkPlan,
  useRdfNetworkPlan,
  useRdfNetworkSettings,
  useUpdateRdfNetworkSettings,
} from "@/lib/queries";

interface RdfNetworkForm {
  network_owner: string;
  network_name: string;
  tablespace: string;
  options: string;
}

interface RdfNetworkFormErrors {
  network_owner?: string;
  network_name?: string;
  tablespace?: string;
  options?: string;
}

const EMPTY_FORM: RdfNetworkForm = {
  network_owner: "",
  network_name: "",
  tablespace: "",
  options: "",
};

const INPUT_CLASS =
  "h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:bg-muted/20 disabled:text-muted";

const STATUS_VARIANTS: Record<RdfNetworkStatus, "success" | "neutral" | "warning" | "danger" | "info"> = {
  not_configured: "neutral",
  ready: "success",
  missing: "warning",
  unavailable: "danger",
  manual_required: "info",
};

function formFromData(data: RdfNetworkSettingsData | undefined): RdfNetworkForm {
  if (!data) return EMPTY_FORM;
  return {
    network_owner: data.network_owner,
    network_name: data.network_name,
    tablespace: data.tablespace,
    options: data.options,
  };
}

function formChanged(form: RdfNetworkForm, data: RdfNetworkSettingsData | undefined) {
  const current = formFromData(data);
  return (
    form.network_owner !== current.network_owner ||
    form.network_name !== current.network_name ||
    form.tablespace !== current.tablespace ||
    form.options !== current.options
  );
}

function statusLabel(status: RdfNetworkStatus) {
  return t(`settings.database.rdfNetwork.status.${status}`);
}

/** Ontology publish 用 Oracle RDF network の設定と明示 DDL apply。 */
export function RdfNetworkCard() {
  const { hasPermission } = useAuth();
  const settingsQuery = useRdfNetworkSettings();
  const planQuery = useRdfNetworkPlan();
  const save = useUpdateRdfNetworkSettings();
  const apply = useApplyRdfNetworkPlan();
  const [form, setForm] = useState<RdfNetworkForm>(EMPTY_FORM);
  const [errors, setErrors] = useState<RdfNetworkFormErrors>({});
  const [confirmation, setConfirmation] = useState("");
  const [operationError, setOperationError] = useState("");

  const data = settingsQuery.data;
  const plan = planQuery.data;
  const mayExecute = hasPermission(MENU_PERMISSIONS.settingsSystemTables);
  const busy = save.isPending || apply.isPending;
  const dirty = formChanged(form, data);
  const confirmed = confirmation.trim() === (plan?.confirmation_phrase ?? "");
  const primaryStep = plan?.steps[0];

  useEffect(() => {
    if (settingsQuery.data) {
      setForm(formFromData(settingsQuery.data));
      setErrors({});
      setConfirmation("");
      setOperationError("");
    }
  }, [settingsQuery.data]);

  const warnings = useMemo(
    () => [...(data?.warnings_ja ?? []), ...(plan?.warnings_ja ?? [])],
    [data?.warnings_ja, plan?.warnings_ja]
  );

  function updateForm(update: Partial<RdfNetworkForm>) {
    setForm((current) => ({ ...current, ...update }));
    setErrors((current) => {
      const next = { ...current };
      for (const key of Object.keys(update) as Array<keyof RdfNetworkFormErrors>) {
        delete next[key];
      }
      return next;
    });
    setOperationError("");
  }

  function validate(requireTablespace: boolean) {
    const nextErrors: RdfNetworkFormErrors = {};
    const hasOwner = Boolean(form.network_owner.trim());
    const hasName = Boolean(form.network_name.trim());
    if (hasOwner !== hasName) {
      nextErrors.network_owner = t("settings.database.rdfNetwork.validation.ownerNamePair");
      nextErrors.network_name = t("settings.database.rdfNetwork.validation.ownerNamePair");
    }
    if (requireTablespace && hasOwner && hasName && !form.tablespace.trim()) {
      nextErrors.tablespace = t("settings.database.rdfNetwork.validation.tablespaceRequired");
    }
    if (form.tablespace.trim().toUpperCase() === "SYSTEM") {
      nextErrors.tablespace = t("settings.database.rdfNetwork.validation.systemTablespace");
    }
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  }

  function saveSettings() {
    if (!validate(false)) return;
    save.mutate(form, {
      onSuccess: () => toast.success(t("settings.database.rdfNetwork.saved")),
      onError: (cause) => {
        setOperationError(
          cause instanceof ApiError ? cause.message : t("settings.database.rdfNetwork.error.save")
        );
      },
    });
  }

  function applyPlan() {
    if (!plan || !validate(true)) return;
    apply.mutate(
      {
        checksum: plan.checksum,
        confirmation,
      },
      {
        onSuccess: (result) => {
          toast.success(
            result.status === "already_configured"
              ? t("settings.database.rdfNetwork.alreadyConfigured")
              : t("settings.database.rdfNetwork.applied")
          );
          setConfirmation("");
        },
        onError: (cause) => {
          setOperationError(
            cause instanceof ApiError ? cause.message : t("settings.database.rdfNetwork.error.apply")
          );
        },
      }
    );
  }

  async function refresh() {
    setOperationError("");
    const [settingsResult, planResult] = await Promise.all([
      settingsQuery.refetch(),
      planQuery.refetch(),
    ]);
    if (!settingsResult.error && !planResult.error) {
      toast.success(t("common.action.refreshed"));
    }
  }

  return (
    <Card
      id="rdf-network"
      className="min-w-0 max-w-full scroll-mt-24 rounded-md"
      aria-busy={busy}
    >
      <CardHeader className="p-6 pb-0">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-5">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Database size={18} aria-hidden />
              <CardTitle className="text-lg">
                {t("settings.database.rdfNetwork.title")}
              </CardTitle>
            </div>
            <CardDescription className="mt-2 leading-relaxed">
              {t("settings.database.rdfNetwork.description")}
            </CardDescription>
          </div>
          {data ? (
            <div className="flex flex-wrap items-center gap-2" aria-live="polite">
              <StatusBadge variant={STATUS_VARIANTS[data.status]} label={statusLabel(data.status)} />
              <StatusBadge
                variant={data.mode === "oracle_rdf" ? "info" : "neutral"}
                label={t(`settings.database.rdfNetwork.mode.${data.mode}`)}
              />
            </div>
          ) : null}
        </div>
      </CardHeader>

      <CardContent className="min-w-0 space-y-5 p-6">
        {settingsQuery.isPending ? <RdfNetworkSkeleton /> : null}

        {settingsQuery.isError ? (
          <Banner severity="danger" title={t("settings.database.rdfNetwork.error.loadTitle")}>
            {t("settings.database.rdfNetwork.error.load")}
          </Banner>
        ) : null}

        {data ? (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <SummaryItem
                label={t("settings.database.rdfNetwork.summary.owner")}
                value={data.network_owner || "—"}
              />
              <SummaryItem
                label={t("settings.database.rdfNetwork.summary.network")}
                value={data.network_name || "—"}
              />
              <SummaryItem
                label={t("settings.database.rdfNetwork.summary.currentUser")}
                value={data.current_oracle_user || "—"}
              />
            </div>

            <Banner severity={data.status === "ready" ? "success" : data.status === "unavailable" ? "danger" : "info"}>
              {data.message_ja}
            </Banner>

            {warnings.length ? (
              <Banner severity="warning" title={t("settings.database.rdfNetwork.warningTitle")}>
                {warnings.join(" ")}
              </Banner>
            ) : null}

            {operationError ? (
              <Banner severity="danger" title={t("settings.database.rdfNetwork.error.operationTitle")}>
                {operationError}
              </Banner>
            ) : null}

            {!mayExecute ? (
              <Banner severity="info">
                {t("settings.database.rdfNetwork.readOnly")}
              </Banner>
            ) : null}

            <section className="space-y-4 border-t border-border pt-5" aria-labelledby="rdf-network-config-title">
              <div>
                <h3 id="rdf-network-config-title" className="text-sm font-semibold text-foreground">
                  {t("settings.database.rdfNetwork.configTitle")}
                </h3>
                <p className="mt-1 text-xs leading-relaxed text-muted">
                  {t("settings.database.rdfNetwork.configDescription")}
                </p>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <TextField
                  id="rdf-network-owner"
                  label={t("settings.database.rdfNetwork.field.owner")}
                  value={form.network_owner}
                  onChange={(value) => updateForm({ network_owner: value })}
                  error={errors.network_owner}
                  placeholder="NL2SQL_APP"
                  disabled={!mayExecute || busy}
                />
                <TextField
                  id="rdf-network-name"
                  label={t("settings.database.rdfNetwork.field.name")}
                  value={form.network_name}
                  onChange={(value) => updateForm({ network_name: value })}
                  error={errors.network_name}
                  placeholder="NET1"
                  disabled={!mayExecute || busy}
                />
                <TextField
                  id="rdf-network-tablespace"
                  label={t("settings.database.rdfNetwork.field.tablespace")}
                  value={form.tablespace}
                  onChange={(value) => updateForm({ tablespace: value })}
                  error={errors.tablespace}
                  placeholder="RDFTBS"
                  disabled={!mayExecute || busy}
                />
                <TextField
                  id="rdf-network-options"
                  label={t("settings.database.rdfNetwork.field.options")}
                  value={form.options}
                  onChange={(value) => updateForm({ options: value })}
                  error={errors.options}
                  placeholder="MODEL_PARTITIONING=BY_HASH_P MODEL_PARTITIONS=16"
                  disabled={!mayExecute || busy}
                />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {mayExecute ? (
                  <Button
                    size="md"
                    onClick={saveSettings}
                    loading={save.isPending}
                    disabled={busy || !dirty}
                  >
                    <Save size={16} aria-hidden />
                    {t("settings.database.rdfNetwork.action.save")}
                  </Button>
                ) : null}
                <Button
                  size="md"
                  variant="secondary"
                  onClick={() => void refresh()}
                  loading={settingsQuery.isFetching || planQuery.isFetching}
                  disabled={busy}
                >
                  <RefreshCw size={16} aria-hidden />
                  {t("settings.database.rdfNetwork.action.refresh")}
                </Button>
              </div>
            </section>

            <section className="space-y-4 border-t border-border pt-5" aria-labelledby="rdf-network-plan-title">
              <div className="flex items-start gap-2">
                <ScrollText className="mt-0.5 shrink-0 text-muted" size={17} aria-hidden />
                <div>
                  <h3 id="rdf-network-plan-title" className="text-sm font-semibold text-foreground">
                    {t("settings.database.rdfNetwork.planTitle")}
                  </h3>
                  <p className="mt-1 text-xs leading-relaxed text-muted">
                    {t("settings.database.rdfNetwork.planDescription")}
                  </p>
                </div>
              </div>
              {primaryStep?.sql ? (
                <details className="rounded-md border border-border">
                  <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
                    {t("settings.database.rdfNetwork.sqlDetails")}
                  </summary>
                  <pre
                    data-testid="rdf-network-sql"
                    className="max-h-72 overflow-auto border-t border-border bg-slate-950 p-4 text-xs leading-relaxed text-slate-100"
                  >
                    {primaryStep.sql}
                  </pre>
                </details>
              ) : (
                <Banner severity="info">{t("settings.database.rdfNetwork.sqlEmpty")}</Banner>
              )}

              {mayExecute && plan?.can_apply ? (
                <ExecutionConfirmationField
                  value={confirmation}
                  onChange={setConfirmation}
                  confirmed={confirmed}
                  placeholder={plan.confirmation_phrase}
                  expectedLabel={plan.confirmation_phrase}
                  helper={t("settings.database.rdfNetwork.applyHelper", {
                    phrase: plan.confirmation_phrase,
                  })}
                  disabled={busy}
                  actions={
                    <Button
                      size="sm"
                      className="w-full sm:w-auto"
                      onClick={applyPlan}
                      loading={apply.isPending}
                      disabled={busy || !confirmed || dirty}
                    >
                      <Play size={15} aria-hidden />
                      {t("settings.database.rdfNetwork.action.apply")}
                    </Button>
                  }
                />
              ) : null}
            </section>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 truncate font-mono text-sm font-semibold text-foreground">{value}</p>
    </div>
  );
}

function TextField({
  id,
  label,
  value,
  onChange,
  error,
  placeholder,
  disabled,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  placeholder: string;
  disabled: boolean;
}) {
  const errorId = `${id}-error`;
  return (
    <label htmlFor={id} className="block space-y-1.5 text-sm font-medium text-foreground">
      <span>{label}</span>
      <input
        id={id}
        className={INPUT_CLASS}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? errorId : undefined}
      />
      {error ? <FieldError id={errorId} message={error} /> : null}
    </label>
  );
}

function RdfNetworkSkeleton() {
  return (
    <TimedLoadingState
      label={t("settings.database.rdfNetwork.loading")}
      operationKey="rdf-network-status"
      placement="panel"
      testId="rdf-network-loading"
    >
      <Skeleton className="h-16 w-full rounded-md" />
      <Skeleton className="h-10 w-full rounded-md" />
    </TimedLoadingState>
  );
}
