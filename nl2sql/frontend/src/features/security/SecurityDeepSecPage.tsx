import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Play, RefreshCw, ShieldCheck } from "lucide-react";

import {
  Banner,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  FormStatus,
  PageHeader,
  StatusBadge,
  toast,
} from "@engchina/production-ready-ui";

import { useConfirm } from "@/components/ui/confirm-dialog";
import { isAbortError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useRequestScope } from "@/lib/useRequestScope";
import { useAuth } from "./AuthProvider";
import { securityApi } from "./api";
import type { DeepSecPlan, DeepSecStatus, DeepSecStep, DeepSecVerification } from "./types";

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

export function SecurityDeepSecPage() {
  const confirm = useConfirm();
  const { hasPermission } = useAuth();
  const mayApply = hasPermission("security.deepsec.apply");
  const mayVerify = hasPermission("security.deepsec.verify");
  const [status, setStatus] = useState<DeepSecStatus | null>(null);
  const [plan, setPlan] = useState<DeepSecPlan | null>(null);
  const [verification, setVerification] = useState<DeepSecVerification | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [planLoading, setPlanLoading] = useState(true);
  const [busyStep, setBusyStep] = useState<number | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [statusLoadError, setStatusLoadError] = useState("");
  const [planLoadError, setPlanLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const statusLoadSequence = useRef(0);
  const planLoadSequence = useRef(0);
  const { abortAll: abortStatusRequests, run: runStatusRequest } = useRequestScope();
  const { abortAll: abortPlanRequests, run: runPlanRequest } = useRequestScope();
  const refreshing = statusLoading || planLoading;

  const loadStatus = async () => {
    const sequence = statusLoadSequence.current + 1;
    statusLoadSequence.current = sequence;
    setStatusLoading(true);
    setStatusLoadError("");
    try {
      await runStatusRequest(async (signal) => {
        const nextStatus = await securityApi.deepSecStatus({ signal });
        if (signal.aborted || sequence !== statusLoadSequence.current) return;
        setStatus(nextStatus);
      });
    } catch (cause) {
      if (isAbortError(cause)) {
        return;
      }
      if (sequence === statusLoadSequence.current) {
        setStatusLoadError(loadErrorMessage(cause));
      }
    } finally {
      if (sequence === statusLoadSequence.current) setStatusLoading(false);
    }
  };

  const loadPlan = async () => {
    const sequence = planLoadSequence.current + 1;
    planLoadSequence.current = sequence;
    setPlanLoading(true);
    setPlanLoadError("");
    try {
      await runPlanRequest(async (signal) => {
        const nextPlan = await securityApi.deepSecPlan({ signal });
        if (signal.aborted || sequence !== planLoadSequence.current) return;
        setPlan(nextPlan);
      });
    } catch (cause) {
      if (isAbortError(cause)) {
        return;
      }
      if (sequence === planLoadSequence.current) {
        setPlanLoadError(loadErrorMessage(cause));
      }
    } finally {
      if (sequence === planLoadSequence.current) setPlanLoading(false);
    }
  };

  const load = () => {
    setActionError("");
    void loadStatus();
    void loadPlan();
  };

  useEffect(() => {
    load();
    return () => {
      statusLoadSequence.current += 1;
      planLoadSequence.current += 1;
      abortStatusRequests();
      abortPlanRequests();
    };
  }, []);

  const canApply = (step: DeepSecStep) => {
    if (!plan?.deepsec_enabled || step.status === "APPLIED") return false;
    return plan.steps.filter((item) => item.step_no < step.step_no).every((item) => item.status === "APPLIED");
  };

  const handleApply = async (step: DeepSecStep) => {
    if (!plan) return;
    if (
      !(await confirm({
        title: `${plan.version} / ${step.title}`,
        description: t("security.deepsec.applyConfirm"),
        tone: "warning",
        dismissOnOverlay: false,
      }))
    ) {
      return;
    }
    setBusyStep(step.step_no);
    setActionError("");
    try {
      await securityApi.applyDeepSecStep(plan.version, step);
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

  return (
    <>
      <PageHeader
        className="px-4 sm:px-8"
        title={t("nav.securityDeepSec")}
        subtitle={t("security.deepsec.subtitle")}
        actions={
          <Button variant="secondary" size="sm" onClick={load}>
            <RefreshCw size={14} className={refreshing ? "animate-spin" : undefined} aria-hidden />
            {t("security.common.reload")}
          </Button>
        }
      />
      <main className="space-y-5 p-4 lg:p-8">
        {actionError ? <Banner severity="danger">{actionError}</Banner> : null}
        {plan && !plan.deepsec_enabled ? (
          <Banner severity="warning">{t("security.deepsec.banner.disabled")}</Banner>
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
              <p className="text-sm text-muted" role="status">{t("security.deepsec.statusLoading")}</p>
            ) : status ? (
              <>
                <Banner severity={status.configured ? "success" : "info"}>{status.message}</Banner>
                <dl className="grid gap-3 text-sm sm:grid-cols-3">
                  <div className="rounded-md border border-border p-3">
                    <dt className="text-muted">{t("security.deepsec.enabled")}</dt>
                    <dd className="mt-1 font-medium">{String(status.deepsec_enabled)}</dd>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <dt className="text-muted">{t("security.deepsec.driver")}</dt>
                    <dd className="mt-1 font-mono">{status.driver_mode}</dd>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <dt className="text-muted">{t("security.deepsec.endUser")}</dt>
                    <dd className="mt-1 break-all font-mono">{status.end_user}</dd>
                  </div>
                </dl>
              </>
            ) : null}
            {statusLoading && status ? (
              <p className="text-xs text-muted" role="status">{t("security.deepsec.statusLoading")}</p>
            ) : null}
          </CardContent>
        </Card>

        <section className="space-y-4" aria-labelledby="deepsec-plan-title">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 id="deepsec-plan-title" className="text-lg font-semibold">{t("security.deepsec.plan")}</h2>
              <p className="mt-1 text-sm text-muted">{t("security.deepsec.sqlReadonly")}</p>
            </div>
            {mayVerify ? (
              <Button onClick={() => void handleVerify()} loading={verifying} disabled={!status?.configured}>
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
                  <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
                    {mayApply ? (
                      <Button loading={busyStep === step.step_no} disabled={!canApply(step)} onClick={() => void handleApply(step)}>
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
