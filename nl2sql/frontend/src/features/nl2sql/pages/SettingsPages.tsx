import { useEffect, useRef, useState } from "react";
import { Database, RefreshCw, ShieldCheck } from "lucide-react";

import { Banner, Button, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { ManagementPanelHeader, ManagementPanelShell, ManagementTabs } from "../components/DbAdminShared";
import type {
  DiagnosticReadiness,
  DiagnosticsData,
  SchemaCatalog,
} from "../types";

type DatabaseSettingsView = "boundary" | "readiness";

export function DatabaseSettingsPage() {
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [diagnostics, setDiagnostics] = useState<DiagnosticsData | null>(null);
  const [activeView, setActiveView] = useState<DatabaseSettingsView>("boundary");
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [overviewError, setOverviewError] = useState("");
  const loadSequence = useRef(0);

  const loadOverview = async () => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setOverviewLoading(true);
    setOverviewError("");
    try {
      const [nextCatalog, nextDiagnostics] = await Promise.all([
        apiGet<SchemaCatalog>("/api/schema/catalog"),
        apiGet<DiagnosticsData>("/api/nl2sql/diagnostics"),
      ]);
      if (sequence === loadSequence.current) {
        setCatalog(nextCatalog);
        setDiagnostics(nextDiagnostics);
      }
    } catch (err) {
      const nextError =
        err instanceof Error && err.message.trim()
          ? err.message
          : t("nl2sqlSettings.database.overview.error");
      if (sequence === loadSequence.current) setOverviewError(nextError);
    } finally {
      if (sequence === loadSequence.current) setOverviewLoading(false);
    }
  };

  useEffect(() => {
    void loadOverview();
  }, []);

  const totalColumns = catalog?.tables.reduce((sum, table) => sum + table.columns.length, 0) ?? 0;
  const databaseReadiness = (diagnostics?.readiness ?? []).filter((item) =>
    ["oracle_adb", "persistence", "feedback_embedding", "select_ai", "select_ai_agent"].includes(item.area)
  );
  const readyCount = databaseReadiness.filter((item) => item.status === "ok").length;

  return (
    <>
      <PageHeader
        title={t("nav.nl2sqlSettingsDatabase")}
        subtitle={t("nl2sqlSettings.database.subtitle")}
      />
      <main className="grid gap-4 p-4 lg:p-8">
        {overviewError ? (
          <Banner
            severity="danger"
            action={
              <Button type="button" variant="secondary" size="sm" onClick={() => void loadOverview()}>
                <RefreshCw size={15} aria-hidden="true" />
                <span>{t("nl2sqlSettings.database.overview.refresh")}</span>
              </Button>
            }
          >
            {overviewError}
          </Banner>
        ) : null}

        <DatabaseStatusBar
          tableCount={catalog?.tables.length ?? 0}
          columnCount={totalColumns}
          readyCount={readyCount}
          readinessCount={databaseReadiness.length}
          refreshedAt={catalog?.refreshed_at ?? ""}
          loading={overviewLoading}
          onRefresh={() => void loadOverview()}
        />

        <ManagementTabs
          activeView={activeView}
          tabs={[
            { id: "boundary", label: t("nl2sqlSettings.database.tabs.boundary"), icon: ShieldCheck },
            { id: "readiness", label: t("nl2sqlSettings.database.tabs.readiness"), icon: Database },
          ]}
          idPrefix="nl2sql-database"
          ariaLabel={t("nl2sqlSettings.database.tabs.label")}
          onViewChange={(view) => setActiveView(view)}
        />

        {activeView === "boundary" ? (
          <ManagementPanelShell
            id="nl2sql-database-panel-boundary"
            labelledBy="nl2sql-database-tab-boundary"
            aria-label={t("nl2sqlSettings.database.workspace.boundary")}
          >
            <SafetyBoundaryPanel
              tableCount={catalog?.tables.length ?? 0}
              columnCount={totalColumns}
              refreshedAt={catalog?.refreshed_at ?? ""}
            />
          </ManagementPanelShell>
        ) : (
          <ManagementPanelShell
            id="nl2sql-database-panel-readiness"
            labelledBy="nl2sql-database-tab-readiness"
            aria-label={t("nl2sqlSettings.database.workspace.readiness")}
          >
            <ReadinessPanel readiness={databaseReadiness} readyCount={readyCount} />
          </ManagementPanelShell>
        )}
      </main>
    </>
  );
}

function DatabaseStatusBar({
  tableCount,
  columnCount,
  readyCount,
  readinessCount,
  refreshedAt,
  loading,
  onRefresh,
}: {
  tableCount: number;
  columnCount: number;
  readyCount: number;
  readinessCount: number;
  refreshedAt: string;
  loading: boolean;
  onRefresh: () => void;
}) {
  return (
    <section className="rounded-md border border-border bg-card px-4 py-3 shadow-sm" aria-label={t("nl2sqlSettings.database.toolbar.status")}>
      <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:flex xl:flex-wrap xl:items-center">
          <StatusMetric label={t("nl2sqlSettings.database.tables")} value={formatNumber(tableCount)} />
          <StatusMetric label={t("nl2sqlSettings.database.columns")} value={formatNumber(columnCount)} />
          <StatusMetric
            label={t("nl2sqlSettings.database.readiness.status")}
            value={`${formatNumber(readyCount)}/${formatNumber(readinessCount)}`}
          />
          <StatusMetric label={t("nl2sqlSettings.database.schemaRefreshed")} value={formatDateTime(refreshedAt)} />
        </dl>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
          <Button type="button" variant="secondary" size="sm" loading={loading} onClick={onRefresh}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("nl2sqlSettings.database.overview.refresh")}</span>
          </Button>
        </div>
      </div>
    </section>
  );
}

function StatusMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-background px-3 py-2">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1 font-semibold text-foreground">{value}</dd>
    </div>
  );
}

function SafetyBoundaryPanel({
  tableCount,
  columnCount,
  refreshedAt,
}: {
  tableCount: number;
  columnCount: number;
  refreshedAt: string;
}) {
  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="nl2sql-boundary-heading">
      <ManagementPanelHeader
        headingId="nl2sql-boundary-heading"
        icon={ShieldCheck}
        title={t("nl2sqlSettings.database.boundary")}
        description={t("nl2sqlSettings.database.boundary.description")}
        action={<StatusBadge variant="success" label="SELECT/WITH" />}
      />
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <StatusMetric label={t("nl2sqlSettings.database.tables")} value={formatNumber(tableCount)} />
        <StatusMetric label={t("nl2sqlSettings.database.columns")} value={formatNumber(columnCount)} />
        <StatusMetric label={t("nl2sqlSettings.database.safety")} value="SELECT/WITH" />
        <StatusMetric label={t("nl2sqlSettings.database.schemaRefreshed")} value={formatDateTime(refreshedAt)} />
      </div>
    </section>
  );
}

function ReadinessPanel({
  readiness,
  readyCount,
}: {
  readiness: DiagnosticReadiness[];
  readyCount: number;
}) {
  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="nl2sql-readiness-heading">
      <ManagementPanelHeader
        headingId="nl2sql-readiness-heading"
        icon={Database}
        title={t("nl2sqlSettings.database.readiness.title")}
        description={t("nl2sqlSettings.database.readiness.description")}
        action={
          <StatusBadge
            variant={readiness.length > 0 && readyCount === readiness.length ? "success" : "warning"}
            label={`${readyCount}/${readiness.length || 0}`}
          />
        }
      />
      {readiness.length > 0 ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {readiness.map((item) => (
            <ReadinessCard key={item.area} item={item} />
          ))}
        </div>
      ) : (
        <div className="grid min-h-32 place-items-center rounded-md border border-dashed border-border bg-background p-4 text-sm text-muted">
          {t("nl2sqlSettings.database.overview.empty")}
        </div>
      )}
    </section>
  );
}

function ReadinessCard({ item }: { item: DiagnosticReadiness }) {
  return (
    <section className="grid gap-3 rounded-md border border-border bg-background p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold text-foreground">{item.label}</p>
          <p className="mt-1 text-sm leading-6 text-foreground">{item.summary}</p>
        </div>
        <StatusBadge variant={item.status === "ok" ? "success" : "warning"} label={item.status} />
      </div>
      {item.next_action && (
        <p className="rounded-md bg-card px-3 py-2 text-sm leading-6 text-foreground">
          <span className="font-medium text-foreground">{t("nl2sqlSettings.database.readiness.nextAction")}: </span>
          {item.next_action}
        </p>
      )}
      {item.related_checks.length > 0 && (
        <p className="break-words font-mono text-xs leading-5 text-muted">
          {t("nl2sqlSettings.database.readiness.relatedChecks")}: {item.related_checks.join(", ")}
        </p>
      )}
    </section>
  );
}
