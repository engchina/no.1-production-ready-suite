import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, Database, PlayCircle, Wrench, type LucideIcon } from "lucide-react";

import {
  Banner,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
  type StatusVariant,
} from "@engchina/production-ready-ui";

import { agentApi, type RunState } from "@/lib/api";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";

const statusVariant: Record<RunState["status"], StatusVariant> = {
  queued: "neutral",
  running: "info",
  waiting_approval: "pending",
  completed: "success",
  failed: "danger",
  cancelled: "warning",
};

export function DashboardPage() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: agentApi.listRuns, refetchInterval: 5000 });
  const tools = useQuery({ queryKey: ["tools"], queryFn: agentApi.listTools });
  const ragSettings = useQuery({ queryKey: ["settings", "rag"], queryFn: agentApi.getExternalRagSettings });
  const nl2sqlSettings = useQuery({
    queryKey: ["settings", "nl2sql"],
    queryFn: agentApi.getExternalNl2SqlSettings,
  });
  const observability = useQuery({
    queryKey: ["observability"],
    queryFn: agentApi.getObservabilityStatus,
  });

  const runItems = runs.data?.runs ?? [];
  const pendingApprovals = runItems.flatMap((run) =>
    run.approvals.filter((approval) => approval.status === "pending")
  );
  const latestRuns = runItems.slice(0, 5);

  return (
    <>
      <PageHeader
        title={t("nav.dashboard")}
        subtitle={t("page.dashboard.subtitle")}
        actions={
          <Link
            to={APP_ROUTES.runs}
            className="hidden h-9 items-center justify-center gap-1.5 rounded-md border border-border bg-card px-4 text-sm font-medium text-foreground transition-colors hover:bg-background focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring sm:inline-flex"
          >
            <PlayCircle size={15} aria-hidden />
            {t("nav.runs")}
          </Link>
        }
      />
      <main className="space-y-6 p-6 md:p-8">
        {pendingApprovals.length ? (
          <Banner severity="warning" title={t("run.waitingApproval")}>
            {pendingApprovals.map((approval) => approval.tool_call.name).join(", ")}
          </Banner>
        ) : null}

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard icon={PlayCircle} label={t("nav.runs")} value={String(runItems.length)} />
          <MetricCard icon={BadgeCheck} label={t("nav.approvals")} value={String(pendingApprovals.length)} />
          <MetricCard icon={Wrench} label={t("nav.tools")} value={String(tools.data?.tools.length ?? 0)} />
          <MetricCard
            icon={Database}
            label={t("nav.settingsExternalNl2Sql")}
            value={nl2sqlSettings.data?.configured ? t("common.configured") : t("common.notConfigured")}
          />
        </div>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
          <Card>
            <CardHeader>
              <CardTitle>{t("run.latest")}</CardTitle>
              <CardDescription>{t("page.runs.subtitle")}</CardDescription>
            </CardHeader>
            <CardContent>
              {runs.isLoading ? <LoadingState rows={4} label={t("common.loading")} /> : null}
              {runs.error ? <ErrorState message={runs.error.message} retryLabel={t("common.retry")} /> : null}
              {!runs.isLoading && !runs.error && !latestRuns.length ? (
                <EmptyState title={t("common.empty.title")} hint={t("common.empty.hint")} />
              ) : null}
              {latestRuns.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[560px] text-left text-sm">
                    <thead>
                      <tr className="border-b border-border text-xs text-muted">
                        <th className="px-3 py-2 font-medium">Run</th>
                        <th className="px-3 py-2 font-medium">{t("common.status")}</th>
                        <th className="px-3 py-2 font-medium">{t("common.tool")}</th>
                        <th className="px-3 py-2 font-medium">{t("common.updatedAt")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {latestRuns.map((run) => (
                        <tr key={run.id} className="border-b border-border/70">
                          <td className="px-3 py-2 text-foreground">{run.goal}</td>
                          <td className="px-3 py-2">
                            <StatusBadge variant={statusVariant[run.status]} label={run.status} />
                          </td>
                          <td className="px-3 py-2 text-muted">
                            {run.steps[0]?.tool_call?.name ?? t("run.form.noTool")}
                          </td>
                          <td className="px-3 py-2 text-muted">{formatDate(run.updated_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>{t("nav.section.settings")}</CardTitle>
              <CardDescription>{t("page.settings.subtitle")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <ConnectionRow
                label={t("nav.settingsExternalRag")}
                configured={ragSettings.data?.configured}
                href={APP_ROUTES.settingsExternalRag}
              />
              <ConnectionRow
                label={t("nav.settingsExternalNl2Sql")}
                configured={nl2sqlSettings.data?.configured}
                href={APP_ROUTES.settingsExternalNl2Sql}
              />
              <ConnectionRow label={t("nav.agents")} configured href={APP_ROUTES.agents} />
              {observability.data ? (
                <div className="space-y-2 rounded-md border border-border p-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm font-medium text-foreground">
                      {t("observability.title")}
                    </span>
                    <StatusBadge
                      variant={observability.data.metrics_enabled ? "success" : "warning"}
                      label={
                        observability.data.metrics_enabled
                          ? t("common.configured")
                          : t("common.notConfigured")
                      }
                    />
                  </div>
                  <StatusRow
                    label={t("observability.prometheus")}
                    value={observability.data.prometheus_metrics_path}
                    configured={observability.data.metrics_enabled}
                  />
                  <StatusRow
                    label={t("observability.langfuse")}
                    configured={observability.data.langfuse_configured}
                  />
                  <StatusRow
                    label={t("observability.opentelemetry")}
                    configured={observability.data.opentelemetry_configured}
                  />
                </div>
              ) : null}
            </CardContent>
          </Card>
        </div>
      </main>
    </>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center justify-between gap-4 pt-5">
        <div>
          <p className="text-xs text-muted">{label}</p>
          <p className="mt-1 text-2xl font-semibold text-foreground">{value}</p>
        </div>
        <div className="flex h-10 w-10 items-center justify-center rounded-md border border-border bg-background">
          <Icon size={18} aria-hidden />
        </div>
      </CardContent>
    </Card>
  );
}

function ConnectionRow({
  label,
  configured,
  href,
}: {
  label: string;
  configured?: boolean;
  href: string;
}) {
  return (
    <Link
      to={href}
      className="flex min-h-12 items-center justify-between gap-3 rounded-md border border-border px-3 py-2 text-sm transition-colors hover:bg-background focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      <span className="text-foreground">{label}</span>
      <StatusBadge
        variant={configured ? "success" : "warning"}
        label={configured ? t("common.configured") : t("common.notConfigured")}
      />
    </Link>
  );
}

function StatusRow({
  label,
  configured,
  value,
}: {
  label: string;
  configured?: boolean;
  value?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 text-sm">
      <span className="min-w-0 text-muted">{value ? `${label}: ${value}` : label}</span>
      <StatusBadge
        variant={configured ? "success" : "warning"}
        label={configured ? t("common.configured") : t("common.notConfigured")}
      />
    </div>
  );
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
