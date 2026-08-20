import { Button } from "@/components/ui/button";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  Database,
  Play,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Trash2,
} from "lucide-react";

import {
  Banner,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  FormStatus,
  StatusBadge,
  toast,
} from "@engchina/production-ready-ui";

import { PageHeader } from "@/components/PageHeader";
import { ProcessingIndicator } from "@/components/ProcessingState";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { ExecutionConfirmationField } from "@/features/nl2sql/components/DbAdminShared";
import { isAbortError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useRequestScope } from "@/lib/useRequestScope";
import { cn } from "@/lib/utils";
import { useAuth } from "./AuthProvider";
import { MENU_PERMISSIONS } from "./menu-permissions";
import { SecuritySearchField } from "./SecurityManagementShared";
import { securityApi } from "./api";
import type {
  DataEntitlement,
  DeepSecPlan,
  DeepSecRoleEntitlements,
  DeepSecStatus,
  DeepSecStep,
  DeepSecVerification,
} from "./types";

const ENTITLEMENT_CAPABILITIES = ["ROW_READ", "SENSITIVE_READ", "FULL"] as const;

const INPUT_CLASS =
  "h-11 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:bg-muted/20 disabled:text-muted";
const ADMIN_EXECUTE_CONFIRMATION = "ADMIN_EXECUTE";

function stepConfirmationKey(version: string, stepNo: number) {
  return `${version}:${stepNo}`;
}

function loadErrorMessage(cause: unknown) {
  return cause instanceof Error && cause.message.trim()
    ? cause.message
    : t("security.common.loadError");
}

function stepStatus(step: DeepSecStep) {
  if (step.status === "APPLIED") return { variant: "success" as const, label: t("security.deepsec.complete") };
  if (step.status === "FAILED") return { variant: "danger" as const, label: t("security.deepsec.failed") };
  if (step.status === "RUNNING") return { variant: "pending" as const, label: t("security.deepsec.running") };
  return { variant: "neutral" as const, label: t("security.deepsec.pending") };
}

function entitlementDraft(role: DeepSecRoleEntitlements | null) {
  return role?.data_entitlements.map((item) => ({ ...item })) ?? [];
}

function entitlementRoleStatus(role: DeepSecRoleEntitlements) {
  if (role.archived) return { variant: "neutral" as const, label: t("security.deepsec.entitlements.archived") };
  if (role.is_built_in) return { variant: "info" as const, label: t("security.deepsec.entitlements.builtIn") };
  return { variant: "success" as const, label: t("security.deepsec.entitlements.editable") };
}

function entitlementRoleSearchText(role: DeepSecRoleEntitlements) {
  return [
    role.role_code,
    role.display_name,
    role.description,
    ...role.data_entitlements.flatMap((item) => [item.resource_code, item.scope_code, item.capability]),
  ]
    .join(" ")
    .toLowerCase();
}

export function SecurityDeepSecPage() {
  const confirm = useConfirm();
  const { hasPermission } = useAuth();
  const mayApply = hasPermission(MENU_PERMISSIONS.securityDeepSec);
  const mayVerify = hasPermission(MENU_PERMISSIONS.securityDeepSec);
  const mayManageEntitlements = hasPermission(MENU_PERMISSIONS.securityDeepSec);
  const [status, setStatus] = useState<DeepSecStatus | null>(null);
  const [plan, setPlan] = useState<DeepSecPlan | null>(null);
  const [verification, setVerification] = useState<DeepSecVerification | null>(null);
  const [entitlementRoles, setEntitlementRoles] = useState<DeepSecRoleEntitlements[]>([]);
  const [selectedEntitlementRoleId, setSelectedEntitlementRoleId] = useState<string | null>(null);
  const [entitlementDraftRows, setEntitlementDraftRows] = useState<DataEntitlement[]>([]);
  const [entitlementSearch, setEntitlementSearch] = useState("");
  const [statusLoading, setStatusLoading] = useState(true);
  const [planLoading, setPlanLoading] = useState(true);
  const [entitlementLoading, setEntitlementLoading] = useState(true);
  const [busyStep, setBusyStep] = useState<number | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [entitlementSaving, setEntitlementSaving] = useState(false);
  const [dataUserPassword, setDataUserPassword] = useState("");
  const [configSaving, setConfigSaving] = useState(false);
  const [configError, setConfigError] = useState("");
  const [statusLoadError, setStatusLoadError] = useState("");
  const [planLoadError, setPlanLoadError] = useState("");
  const [entitlementLoadError, setEntitlementLoadError] = useState("");
  const [entitlementFormError, setEntitlementFormError] = useState("");
  const [actionError, setActionError] = useState("");
  const [stepConfirmations, setStepConfirmations] = useState<Record<string, string>>({});
  const statusLoadSequence = useRef(0);
  const planLoadSequence = useRef(0);
  const entitlementLoadSequence = useRef(0);
  const { abortAll: abortStatusRequests, run: runStatusRequest } = useRequestScope();
  const { abortAll: abortPlanRequests, run: runPlanRequest } = useRequestScope();
  const { abortAll: abortEntitlementRequests, run: runEntitlementRequest } = useRequestScope();
  const refreshing = statusLoading || planLoading || entitlementLoading;

  const selectedEntitlementRole = useMemo(
    () => entitlementRoles.find((role) => role.role_id === selectedEntitlementRoleId) ?? null,
    [entitlementRoles, selectedEntitlementRoleId]
  );
  const filteredEntitlementRoles = useMemo(() => {
    const q = entitlementSearch.trim().toLowerCase();
    return entitlementRoles
      .filter((role) => (q ? entitlementRoleSearchText(role).includes(q) : true))
      .sort((left, right) => left.display_name.localeCompare(right.display_name, "ja"));
  }, [entitlementRoles, entitlementSearch]);
  const entitlementReadOnly = Boolean(
    !mayManageEntitlements || selectedEntitlementRole?.is_built_in || selectedEntitlementRole?.archived
  );

  const loadStatus = async () => {
    const sequence = statusLoadSequence.current + 1;
    let completed = false;
    statusLoadSequence.current = sequence;
    setStatusLoading(true);
    setStatusLoadError("");
    try {
      await runStatusRequest(async (signal) => {
        const nextStatus = await securityApi.deepSecStatus({ signal });
        if (signal.aborted || sequence !== statusLoadSequence.current) return;
        setStatus(nextStatus);
        completed = true;
      });
    } catch (cause) {
      if (isAbortError(cause)) {
        return false;
      }
      if (sequence === statusLoadSequence.current) {
        setStatusLoadError(loadErrorMessage(cause));
      }
    } finally {
      if (sequence === statusLoadSequence.current) setStatusLoading(false);
    }
    return completed;
  };

  const loadPlan = async () => {
    const sequence = planLoadSequence.current + 1;
    let completed = false;
    planLoadSequence.current = sequence;
    setPlanLoading(true);
    setPlanLoadError("");
    try {
      await runPlanRequest(async (signal) => {
        const nextPlan = await securityApi.deepSecPlan({ signal });
        if (signal.aborted || sequence !== planLoadSequence.current) return;
        setPlan(nextPlan);
        completed = true;
      });
    } catch (cause) {
      if (isAbortError(cause)) {
        return false;
      }
      if (sequence === planLoadSequence.current) {
        setPlanLoadError(loadErrorMessage(cause));
      }
    } finally {
      if (sequence === planLoadSequence.current) setPlanLoading(false);
    }
    return completed;
  };

  const loadEntitlements = async () => {
    const sequence = entitlementLoadSequence.current + 1;
    let completed = false;
    entitlementLoadSequence.current = sequence;
    setEntitlementLoading(true);
    setEntitlementLoadError("");
    try {
      await runEntitlementRequest(async (signal) => {
        const rows = await securityApi.deepSecDataEntitlements({ signal });
        if (signal.aborted || sequence !== entitlementLoadSequence.current) return;
        setEntitlementRoles(rows);
        setSelectedEntitlementRoleId((current) =>
          current && rows.some((role) => role.role_id === current)
            ? current
            : rows[0]?.role_id ?? null
        );
        completed = true;
      });
    } catch (cause) {
      if (isAbortError(cause)) {
        return false;
      }
      if (sequence === entitlementLoadSequence.current) {
        setEntitlementLoadError(loadErrorMessage(cause));
      }
    } finally {
      if (sequence === entitlementLoadSequence.current) setEntitlementLoading(false);
    }
    return completed;
  };

  const load = async (announce = false) => {
    setActionError("");
    const results = await Promise.all([loadStatus(), loadPlan(), loadEntitlements()]);
    if (announce && results.every(Boolean)) {
      toast.success(t("common.action.refreshed"));
    }
  };

  useEffect(() => {
    void load();
    return () => {
      statusLoadSequence.current += 1;
      planLoadSequence.current += 1;
      entitlementLoadSequence.current += 1;
      abortStatusRequests();
      abortPlanRequests();
      abortEntitlementRequests();
    };
  }, []);

  useEffect(() => {
    setEntitlementDraftRows(entitlementDraft(selectedEntitlementRole));
    setEntitlementFormError("");
  }, [selectedEntitlementRole?.role_id, selectedEntitlementRole?.version]);

  const canApply = (step: DeepSecStep) => {
    if (
      !plan?.deepsec_enabled ||
      !plan.has_data_user_password ||
      step.status === "APPLIED" ||
      step.status === "RUNNING" ||
      busyStep !== null
    ) {
      return false;
    }
    return plan.steps.filter((item) => item.step_no < step.step_no).every((item) => item.status === "APPLIED");
  };

  const validateConfigForm = () => {
    if (dataUserPassword.length < 12 || dataUserPassword.length > 256) {
      return t("security.deepsec.config.passwordLength");
    }
    if (dataUserPassword.includes("\"") || /[\x00-\x1f\x7f-\x9f]/.test(dataUserPassword)) {
      return t("security.deepsec.config.passwordChars");
    }
    return "";
  };

  const handleSaveConfig = async () => {
    const validationError = validateConfigForm();
    if (validationError) {
      setConfigError(validationError);
      return;
    }
    setConfigSaving(true);
    setConfigError("");
    setActionError("");
    try {
      const nextStatus = await securityApi.updateDeepSecConfig(dataUserPassword);
      setStatus(nextStatus);
      setDataUserPassword("");
      toast.success(t("security.deepsec.config.saved"));
      await loadPlan();
    } catch (cause) {
      setConfigError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    } finally {
      setConfigSaving(false);
    }
  };

  const handleApply = async (step: DeepSecStep, confirmation: string) => {
    if (!plan) return;
    if (confirmation.trim() !== ADMIN_EXECUTE_CONFIRMATION) {
      setActionError(t("security.deepsec.applyConfirmationRequired"));
      return;
    }
    setBusyStep(step.step_no);
    setActionError("");
    try {
      await securityApi.applyDeepSecStep(plan.version, step, confirmation.trim());
      const key = stepConfirmationKey(plan.version, step.step_no);
      setStepConfirmations((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      toast.success(t("security.deepsec.applied"));
      void loadStatus();
      await loadPlan();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("security.common.saveError"));
      void loadStatus();
      await loadPlan();
    } finally {
      setBusyStep(null);
    }
  };

  const handleVerify = async () => {
    if (
      !(await confirm({
        title: t("security.deepsec.verify"),
        description: t("security.deepsec.verifyConfirm"),
        tone: "info",
      }))
    ) {
      return;
    }
    setVerifying(true);
    setActionError("");
    try {
      const result = await securityApi.verifyDeepSec();
      setVerification(result);
      toast.success(t("security.deepsec.verified"));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    } finally {
      setVerifying(false);
    }
  };

  const addEntitlement = () => {
    setEntitlementDraftRows((current) => [
      ...current,
      { resource_code: "NL2SQL_DEEPSEC_PROBE", scope_code: "*", capability: "ROW_READ" },
    ]);
    setEntitlementFormError("");
  };

  const updateEntitlement = (index: number, field: keyof DataEntitlement, value: string) => {
    setEntitlementDraftRows((current) =>
      current.map((item, itemIndex) =>
        itemIndex === index ? { ...item, [field]: value } : item
      )
    );
    setEntitlementFormError("");
  };

  const removeEntitlement = (index: number) => {
    setEntitlementDraftRows((current) => current.filter((_, itemIndex) => itemIndex !== index));
    setEntitlementFormError("");
  };

  const validateEntitlements = () => {
    if (
      entitlementDraftRows.some(
        (item) =>
          !item.resource_code.trim() ||
          !item.scope_code.trim() ||
          !ENTITLEMENT_CAPABILITIES.includes(
            item.capability as (typeof ENTITLEMENT_CAPABILITIES)[number]
          )
      )
    ) {
      return t("security.deepsec.entitlements.validation");
    }
    return "";
  };

  const handleSaveEntitlements = async () => {
    if (!selectedEntitlementRole || entitlementReadOnly) return;
    const validationError = validateEntitlements();
    if (validationError) {
      setEntitlementFormError(validationError);
      return;
    }
    setEntitlementSaving(true);
    setEntitlementFormError("");
    setActionError("");
    try {
      const updated = await securityApi.updateDeepSecDataEntitlements({
        ...selectedEntitlementRole,
        data_entitlements: entitlementDraftRows.map(({ resource_code, scope_code, capability }) => ({
          resource_code: resource_code.trim().toUpperCase(),
          scope_code: scope_code.trim(),
          capability,
        })),
      });
      setEntitlementRoles((rows) =>
        rows.map((role) => (role.role_id === updated.role_id ? updated : role))
      );
      setSelectedEntitlementRoleId(updated.role_id);
      toast.success(t("security.deepsec.entitlements.saved"));
    } catch (cause) {
      setEntitlementFormError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    } finally {
      setEntitlementSaving(false);
    }
  };

  return (
    <>
      <PageHeader
        title={t("nav.securityDeepSec")}
        subtitle={t("security.deepsec.subtitle")}
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            onClick: () => load(true),
            loading: refreshing,
          },
        ]}
      />
      <main className="space-y-5 p-4 lg:p-8">
        {actionError ? <Banner severity="danger">{actionError}</Banner> : null}
        {plan && !plan.deepsec_enabled ? (
          <Banner severity="warning">{t("security.deepsec.banner.disabled")}</Banner>
        ) : null}
        {plan?.deepsec_enabled && !plan.has_data_user_password ? (
          <Banner severity="warning">{t("security.deepsec.banner.passwordMissing")}</Banner>
        ) : null}
        <Card>
          <CardHeader>
            <CardTitle>{t("security.deepsec.status")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {statusLoadError ? (
              <Banner severity="danger">{statusLoadError}</Banner>
            ) : null}
            {statusLoading && !status ? (
              <ProcessingIndicator
                active
                label={t("security.deepsec.statusLoading")}
                operationKey="security-deepsec-status"
                placement="panel"
                testId="security-deepsec-loading"
              />
            ) : status ? (
              <>
                <Banner severity={status.configured ? "success" : "info"}>{status.message}</Banner>
                <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                  <div className="rounded-md border border-border p-3">
                    <dt className="text-muted">{t("security.deepsec.enabled")}</dt>
                    <dd className="mt-1 font-medium">{String(status.deepsec_enabled)}</dd>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <dt className="text-muted">{t("security.deepsec.driver")}</dt>
                    <dd className="mt-1 font-mono">{status.driver_mode}</dd>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <dt className="text-muted">{t("security.deepsec.connectionSecurity")}</dt>
                    <dd className="mt-1 break-all font-mono">
                      {status.connection_security ?? "wallet_mtls"}
                    </dd>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <dt className="text-muted">{t("security.deepsec.dataUser")}</dt>
                    <dd className="mt-1 break-all font-mono">{status.data_user}</dd>
                  </div>
                </dl>
              </>
            ) : null}
            {statusLoading && status ? (
              <p className="text-xs text-muted" role="status">{t("security.deepsec.statusLoading")}</p>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("security.deepsec.config.title")}</CardTitle>
          </CardHeader>
          <CardContent>
            <form
              className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)_auto]"
              onSubmit={(event) => {
                event.preventDefault();
                void handleSaveConfig();
              }}
            >
              <dl className="rounded-md border border-border p-3 text-sm">
                <dt className="text-muted">{t("security.deepsec.dataUser")}</dt>
                <dd className="mt-1 break-all font-mono">
                  {status?.data_user ?? plan?.data_user ?? "NL2SQL_DEEPSEC_DATA_USER"}
                </dd>
              </dl>
              <div className="space-y-2">
                <label htmlFor="deepsec-data-user-password" className="block text-sm font-medium">
                  {t("security.deepsec.config.password")}
                </label>
                <input
                  id="deepsec-data-user-password"
                  type="password"
                  autoComplete="new-password"
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
                  value={dataUserPassword}
                  onChange={(event) => {
                    setDataUserPassword(event.target.value);
                    setConfigError("");
                  }}
                  placeholder={
                    status?.has_data_user_password
                      ? t("security.deepsec.config.passwordPlaceholderSaved")
                      : t("security.deepsec.config.passwordPlaceholderNew")
                  }
                  aria-describedby="deepsec-data-user-password-state"
                  aria-invalid={Boolean(configError)}
                />
                <p id="deepsec-data-user-password-state" className="text-xs text-muted">
                  {status?.has_data_user_password
                    ? t("security.deepsec.config.secretSaved")
                    : t("security.deepsec.config.secretMissing")}
                </p>
                {configError ? <FormStatus tone="danger" message={configError} /> : null}
              </div>
              <div className="flex items-end">
                <Button
                  type="submit"
                  loading={configSaving}
                  disabled={!dataUserPassword || configSaving}
                  className="w-full lg:w-auto"
                >
                  <Save size={15} aria-hidden />
                  {t("security.deepsec.config.save")}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex min-w-0 items-center gap-2">
              <Database size={18} aria-hidden />
              <span className="min-w-0 break-words">{t("security.deepsec.entitlements.title")}</span>
            </CardTitle>
            <p className="text-sm leading-6 text-muted">{t("security.deepsec.entitlements.hint")}</p>
          </CardHeader>
          <CardContent className="space-y-4">
            {entitlementLoadError ? <Banner severity="danger">{entitlementLoadError}</Banner> : null}
            <div className="grid gap-4 xl:grid-cols-[minmax(15rem,22rem)_minmax(0,1fr)]">
              <section className="grid min-w-0 content-start gap-3" aria-labelledby="deepsec-entitlement-role-list-title">
                <h2 id="deepsec-entitlement-role-list-title" className="text-sm font-semibold">
                  {t("security.deepsec.entitlements.roles")}
                </h2>
                <SecuritySearchField
                  label={t("security.common.search")}
                  placeholder={t("security.deepsec.entitlements.searchPlaceholder")}
                  value={entitlementSearch}
                  testId="security-deepsec-entitlement-search"
                  onChange={setEntitlementSearch}
                />
                {entitlementLoading ? (
                  <ProcessingIndicator
                    active
                    label={t("security.deepsec.entitlements.loading")}
                    operationKey="security-deepsec-entitlements"
                    placement="panel"
                    testId="security-deepsec-entitlements-loading"
                    activityIcon="none"
                  />
                ) : null}
                <div className="grid max-h-[30rem] gap-2 overflow-auto pr-1" data-testid="security-deepsec-entitlement-roles">
                  {!entitlementLoading && filteredEntitlementRoles.length === 0 ? (
                    <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">
                      {entitlementSearch ? t("security.deepsec.entitlements.noResults") : t("security.common.empty")}
                    </p>
                  ) : null}
                  {filteredEntitlementRoles.map((role) => {
                    const selected = role.role_id === selectedEntitlementRoleId;
                    const statusBadge = entitlementRoleStatus(role);
                    return (
                      <button
                        key={role.role_id}
                        type="button"
                        className={cn(
                          "min-w-0 rounded-md border border-border bg-background p-3 text-left outline-none transition hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40",
                          selected && "border-primary bg-primary/5"
                        )}
                        aria-pressed={selected}
                        data-testid={`security-deepsec-entitlement-role-${role.role_id}`}
                        onClick={() => {
                          setSelectedEntitlementRoleId(role.role_id);
                          setEntitlementFormError("");
                        }}
                      >
                        <span className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                          <span className="min-w-0">
                            <span className="block break-words text-sm font-medium">{role.display_name}</span>
                            <span className="block break-all font-mono text-[11px] text-muted">{role.role_code}</span>
                          </span>
                          <StatusBadge variant={statusBadge.variant} label={statusBadge.label} />
                        </span>
                        <span className="mt-2 block text-xs text-muted">
                          {t("security.deepsec.entitlements.count", { count: role.data_entitlements.length })}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </section>

              <section className="min-w-0 rounded-md border border-border bg-background p-4" aria-labelledby="deepsec-entitlement-editor-title">
                {!selectedEntitlementRole ? (
                  <div className="py-10 text-center">
                    <h2 id="deepsec-entitlement-editor-title" className="text-sm font-semibold">
                      {t("security.deepsec.entitlements.noSelectionTitle")}
                    </h2>
                    <p className="mt-1 text-sm text-muted">{t("security.deepsec.entitlements.noSelectionHint")}</p>
                  </div>
                ) : (
                  <form
                    className="grid gap-4"
                    data-testid="security-deepsec-entitlement-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void handleSaveEntitlements();
                    }}
                  >
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                      <div className="min-w-0">
                        <h2 id="deepsec-entitlement-editor-title" className="break-words text-base font-semibold">
                          {selectedEntitlementRole.display_name}
                        </h2>
                        <p className="mt-1 break-all font-mono text-xs text-muted">{selectedEntitlementRole.role_code}</p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <StatusBadge
                          variant={entitlementRoleStatus(selectedEntitlementRole).variant}
                          label={entitlementRoleStatus(selectedEntitlementRole).label}
                        />
                        <StatusBadge
                          variant="info"
                          label={t("security.deepsec.entitlements.count", {
                            count: selectedEntitlementRole.data_entitlements.length,
                          })}
                        />
                      </div>
                    </div>
                    {selectedEntitlementRole.is_built_in ? (
                      <Banner severity="info">{t("security.deepsec.entitlements.readOnlyBuiltIn")}</Banner>
                    ) : null}
                    {selectedEntitlementRole.archived ? (
                      <Banner severity="info">{t("security.deepsec.entitlements.readOnlyArchived")}</Banner>
                    ) : null}
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h3 className="text-sm font-semibold">{t("security.deepsec.entitlements.rows")}</h3>
                      <Button type="button" size="sm" variant="secondary" onClick={addEntitlement} disabled={entitlementReadOnly}>
                        <Plus size={14} aria-hidden />
                        {t("security.deepsec.entitlements.add")}
                      </Button>
                    </div>
                    {entitlementDraftRows.length === 0 ? (
                      <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted">
                        {t("security.common.empty")}
                      </p>
                    ) : (
                      <div className="grid gap-2">
                        {entitlementDraftRows.map((entitlement, index) => (
                          <div
                            key={`${index}-${entitlement.entitlement_id ?? "new"}`}
                            className="grid gap-2 rounded-md border border-border p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,0.75fr)_minmax(0,0.85fr)_auto]"
                          >
                            <label className="grid gap-1 text-xs font-medium" htmlFor={`deepsec-entitlement-resource-${index}`}>
                              <span>{t("security.deepsec.entitlements.resource")}</span>
                              <input
                                id={`deepsec-entitlement-resource-${index}`}
                                className={INPUT_CLASS}
                                disabled={entitlementReadOnly}
                                value={entitlement.resource_code}
                                onChange={(event) => updateEntitlement(index, "resource_code", event.target.value.toUpperCase())}
                              />
                            </label>
                            <label className="grid gap-1 text-xs font-medium" htmlFor={`deepsec-entitlement-scope-${index}`}>
                              <span>{t("security.deepsec.entitlements.scope")}</span>
                              <input
                                id={`deepsec-entitlement-scope-${index}`}
                                className={INPUT_CLASS}
                                disabled={entitlementReadOnly}
                                value={entitlement.scope_code}
                                onChange={(event) => updateEntitlement(index, "scope_code", event.target.value)}
                              />
                            </label>
                            <label className="grid gap-1 text-xs font-medium" htmlFor={`deepsec-entitlement-capability-${index}`}>
                              <span>{t("security.deepsec.entitlements.capability")}</span>
                              <select
                                id={`deepsec-entitlement-capability-${index}`}
                                className={INPUT_CLASS}
                                disabled={entitlementReadOnly}
                                value={entitlement.capability}
                                onChange={(event) => updateEntitlement(index, "capability", event.target.value)}
                              >
                                {ENTITLEMENT_CAPABILITIES.map((capability) => (
                                  <option key={capability} value={capability}>{capability}</option>
                                ))}
                              </select>
                            </label>
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              className="self-end"
                              aria-label={t("security.deepsec.entitlements.remove")}
                              disabled={entitlementReadOnly}
                              onClick={() => removeEntitlement(index)}
                            >
                              <Trash2 size={14} aria-hidden />
                            </Button>
                          </div>
                        ))}
                      </div>
                    )}
                    {entitlementFormError ? <FormStatus tone="danger" message={entitlementFormError} /> : null}
                    <div className="flex flex-col gap-2 border-t border-border pt-4 sm:flex-row sm:justify-end">
                      <Button
                        type="submit"
                        loading={entitlementSaving}
                        disabled={entitlementReadOnly || entitlementSaving}
                        className="w-full sm:w-auto"
                        aria-label={t("security.deepsec.entitlements.save")}
                      >
                        <Save size={15} aria-hidden />
                        {t("security.common.save")}
                      </Button>
                    </div>
                  </form>
                )}
              </section>
            </div>
          </CardContent>
        </Card>

        <section className="space-y-4" aria-labelledby="deepsec-plan-title">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 id="deepsec-plan-title" className="text-lg font-semibold">{t("security.deepsec.plan")}</h2>
              <p className="mt-1 text-sm text-muted">{t("security.deepsec.sqlReadonly")}</p>
            </div>
            {mayVerify ? (
              <Button
                onClick={() => void handleVerify()}
                loading={verifying}
                disabled={!status?.configured || !status.has_data_user_password}
              >
                <ShieldCheck size={15} aria-hidden />
                {t("security.deepsec.verify")}
              </Button>
            ) : null}
          </div>
          {planLoadError ? (
            <Card>
              <CardContent className="space-y-3">
                <Banner severity="danger">{planLoadError}</Banner>
                <Button variant="secondary" size="sm" onClick={() => void loadPlan()}>
                  <RefreshCw size={14} aria-hidden />
                  {t("security.common.reload")}
                </Button>
              </CardContent>
            </Card>
          ) : null}
          {planLoading && !plan ? (
            <Card>
              <CardContent>
                <p className="text-sm text-muted" role="status">{t("security.deepsec.planLoading")}</p>
              </CardContent>
            </Card>
          ) : null}
          {plan && plan.steps.length === 0 ? (
            <Card>
              <CardContent>
                <p className="text-sm text-muted">{t("security.deepsec.planEmpty")}</p>
              </CardContent>
            </Card>
          ) : null}
          {plan?.steps.map((step) => {
            const statusBadge = stepStatus(step);
            const confirmationKey = stepConfirmationKey(plan.version, step.step_no);
            const confirmation = stepConfirmations[confirmationKey] ?? "";
            const confirmationMatched = confirmation.trim() === ADMIN_EXECUTE_CONFIRMATION;
            const applyAvailable = canApply(step);
            return (
              <Card key={step.step_no}>
                <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <CardTitle>{`${plan.version}.${step.step_no} ${step.title}`}</CardTitle>
                    <p className="mt-1 text-sm leading-6 text-muted">{step.description}</p>
                  </div>
                  <StatusBadge variant={statusBadge.variant} label={statusBadge.label} />
                </CardHeader>
                <CardContent className="space-y-4">
                  {step.error_message ? <Banner severity="danger">{step.error_message}</Banner> : null}
                  <div className="space-y-1">
                    <p className="text-xs font-medium text-muted">{t("security.deepsec.checksum")}</p>
                    <code className="block break-all rounded-md bg-background p-2 text-[11px]">{step.checksum}</code>
                  </div>
                  <div className="space-y-3">
                    {step.sql.map((sql, index) => (
                      <pre key={`${step.step_no}-${index}`} className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-slate-950 p-3 text-xs leading-5 text-slate-100" tabIndex={0} aria-label={`${step.title} SQL ${index + 1}`}>
                        {sql}
                      </pre>
                    ))}
                  </div>
                  <div className="space-y-3 border-t border-border pt-4">
                    {mayApply && step.status !== "APPLIED" ? (
                      <ExecutionConfirmationField
                        value={confirmation}
                        onChange={(value) => {
                          setStepConfirmations((current) => ({
                            ...current,
                            [confirmationKey]: value,
                          }));
                          setActionError("");
                        }}
                        confirmed={confirmationMatched}
                        placeholder={ADMIN_EXECUTE_CONFIRMATION}
                        expectedLabel={ADMIN_EXECUTE_CONFIRMATION}
                        helper={t("dbAdmin.confirmation.helper.danger", {
                          phrase: ADMIN_EXECUTE_CONFIRMATION,
                        })}
                        tone="danger"
                        disabled={!applyAvailable}
                        actions={
                          <Button
                            variant="danger"
                            size="sm"
                            className="w-full sm:w-auto"
                            loading={busyStep === step.step_no}
                            disabled={!applyAvailable || !confirmationMatched}
                            onClick={() => void handleApply(step, confirmation)}
                          >
                            <Play size={15} aria-hidden />
                            {t("security.deepsec.apply")}
                          </Button>
                        }
                      />
                    ) : mayApply ? (
                      <Button disabled>
                        <Play size={15} aria-hidden />
                        {t("security.deepsec.apply")}
                      </Button>
                    ) : null}
                    {step.executed_at ? <span className="text-xs tabular-nums text-muted">{formatDateTime(step.executed_at)}</span> : null}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </section>

        {verification ? (
          <Card>
            <CardHeader className="flex-row items-center justify-between gap-3">
              <CardTitle>{t("security.deepsec.result")}</CardTitle>
              <StatusBadge variant={verification.passed ? "success" : "warning"} label={verification.passed ? t("security.deepsec.complete") : t("security.deepsec.failed")} />
            </CardHeader>
            <CardContent className="space-y-2">
              {verification.checks.map((check) => (
                <div key={check.key} className="flex items-start gap-2 rounded-md border border-border p-3 text-sm">
                  <CheckCircle2 size={16} className={check.passed ? "text-success" : "text-warning"} aria-hidden />
                  <div>
                    <p className="font-mono text-xs font-medium">{check.key}</p>
                    <p className="mt-1 text-muted">{check.detail}</p>
                  </div>
                </div>
              ))}
              <FormStatus tone={verification.passed ? "success" : "warning"} message={formatDateTime(verification.checked_at)} />
            </CardContent>
          </Card>
        ) : null}
      </main>
    </>
  );
}
