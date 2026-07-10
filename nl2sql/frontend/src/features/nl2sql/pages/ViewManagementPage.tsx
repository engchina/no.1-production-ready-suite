import { useEffect, useState } from "react";
import { Eye, RefreshCw, Sparkles, Trash2 } from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, EmptyState, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  DbAdminExecutionResult,
  ObjectDetailPanel,
  ObjectListPanel,
  PageMetric,
  StatementRunnerCard,
  WorkSection,
} from "../components/DbAdminShared";
import type {
  DbAdminExecuteData,
  DbAdminJoinWhereData,
  DbAdminObjectDetail,
  DbAdminObjectsData,
  SchemaCatalog,
} from "../types";

export function ViewManagementPage() {
  const [views, setViews] = useState<DbAdminObjectsData | null>(null);
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [detail, setDetail] = useState<DbAdminObjectDetail | null>(null);
  const [dropExecute, setDropExecute] = useState(false);
  const [dropConfirmation, setDropConfirmation] = useState("");
  const [dropResult, setDropResult] = useState<DbAdminExecuteData | null>(null);
  const [joinWhere, setJoinWhere] = useState<DbAdminJoinWhereData | null>(null);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");

  const load = async () => {
    setLoading("load");
    setMessage("");
    try {
      const [viewData, catalogData] = await Promise.all([
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/views"),
        apiGet<SchemaCatalog>("/api/schema/catalog"),
      ]);
      setViews(viewData);
      setCatalog(catalogData);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("viewMgmt.error.load"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const selectView = async (name: string) => {
    setLoading(`detail-${name}`);
    setMessage("");
    setJoinWhere(null);
    try {
      setDetail(await apiGet<DbAdminObjectDetail>(`/api/nl2sql/db-admin/views/${encodeURIComponent(name)}`));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("viewMgmt.error.load"));
    } finally {
      setLoading("");
    }
  };

  const dropView = async () => {
    if (!detail) return;
    setLoading("drop");
    setMessage("");
    try {
      const result = await apiPost<DbAdminExecuteData>("/api/nl2sql/db-admin/drop-view", {
        view_name: detail.name,
        execute: dropExecute,
        confirmation: dropExecute ? dropConfirmation : "",
        reason: "ui-view-management-drop",
      });
      setDropResult(result);
      if (result.executed) {
        setDetail(null);
        await load();
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("viewMgmt.error.drop"));
    } finally {
      setLoading("");
    }
  };

  const extractJoinWhere = async () => {
    if (!detail?.ddl) return;
    setLoading("join-where");
    setMessage("");
    try {
      setJoinWhere(
        await apiPost<DbAdminJoinWhereData>("/api/nl2sql/db-admin/extract-join-where", {
          ddl: detail.ddl,
        })
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("viewMgmt.error.extract"));
    } finally {
      setLoading("");
    }
  };

  return (
    <>
      <PageHeader
        title={t("nav.viewManagement")}
        subtitle={t("viewMgmt.subtitle")}
        actions={
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={loading === "load"}
            onClick={() => void load()}
          >
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("tableMgmt.action.refresh")}</span>
          </Button>
        }
      />
      <main className="grid gap-5 p-4 lg:p-8">
        {message && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
            {message}
          </div>
        )}

        <Card>
          <CardContent className="grid gap-3 p-4 md:grid-cols-3">
            <PageMetric label={t("viewMgmt.metric.views")} value={formatNumber(views?.items.length ?? 0)} />
            <PageMetric label={t("tableMgmt.metric.runtime")} value={views?.runtime ?? "deterministic"} />
            <PageMetric label={t("tableMgmt.metric.schemaRefreshed")} value={formatDateTime(catalog?.refreshed_at)} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="flex items-center gap-2">
                <Eye size={18} aria-hidden="true" />
                {t("viewMgmt.list.title")}
              </CardTitle>
              <StatusBadge variant="neutral" label={views?.runtime ?? "deterministic"} />
              <StatusBadge variant="info" label={`${views?.items.length ?? 0} views`} />
            </div>
          </CardHeader>
          <CardContent className="grid gap-5 xl:grid-cols-[minmax(0,0.7fr)_minmax(0,1.3fr)]">
            <ObjectListPanel
              items={views?.items ?? []}
              selectedName={detail?.name}
              onSelect={(item) => void selectView(item.name)}
              emptyTitle={t("viewMgmt.list.emptyTitle")}
              emptyHint={t("viewMgmt.list.emptyHint")}
            />
            <ObjectDetailPanel detail={detail} catalog={catalog} />
          </CardContent>
        </Card>

        <WorkSection title={t("viewMgmt.create.title")} description={t("viewMgmt.create.note")}>
          <StatementRunnerCard
            policy="view_ddl"
            target="view"
            title={t("viewMgmt.create.title")}
            placeholder={t("viewMgmt.create.placeholder")}
            onExecuted={() => load()}
          />
        </WorkSection>

        <WorkSection title={t("viewMgmt.joinWhere.title")} description={t("viewMgmt.joinWhere.subtitle")}>
          <section className="grid gap-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm text-slate-600">{detail ? detail.name : t("viewMgmt.joinWhere.empty")}</p>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={loading === "join-where"}
                disabled={!detail?.ddl}
                onClick={() => void extractJoinWhere()}
              >
                <Sparkles size={15} aria-hidden="true" />
                <span>{t("viewMgmt.joinWhere.extract")}</span>
              </Button>
            </div>
            {joinWhere ? (
              <div className="grid gap-3">
                <div className="flex flex-wrap gap-2">
                  <StatusBadge
                    variant={joinWhere.source === "oci_enterprise_ai" ? "success" : "neutral"}
                    label={joinWhere.source}
                  />
                </div>
                {joinWhere.warnings.map((warning) => (
                  <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                    {warning}
                  </p>
                ))}
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("viewMgmt.joinWhere.join")}</span>
                  <textarea
                    readOnly
                    value={joinWhere.join_text}
                    rows={4}
                    className="min-h-24 rounded-md border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-xs leading-5 text-slate-800 outline-none"
                  />
                </label>
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("viewMgmt.joinWhere.where")}</span>
                  <textarea
                    readOnly
                    value={joinWhere.where_text}
                    rows={4}
                    className="min-h-24 rounded-md border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-xs leading-5 text-slate-800 outline-none"
                  />
                </label>
              </div>
            ) : (
              <p className="text-sm text-slate-500">{t("viewMgmt.joinWhere.empty")}</p>
            )}
          </section>
        </WorkSection>

        <WorkSection title={t("viewMgmt.danger.title")} description={t("viewMgmt.danger.subtitle")} tone="danger">
          {detail ? (
            <div className="grid gap-3 rounded-md border border-red-200 bg-red-50 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="break-all font-mono text-sm font-semibold text-red-950">{detail.name}</p>
                  <p className="mt-1 text-sm text-red-800">{t("viewMgmt.danger.subtitle")}</p>
                </div>
                <Button
                  type="button"
                  variant={dropExecute ? "danger" : "secondary"}
                  size="sm"
                  loading={loading === "drop"}
                  onClick={() => void dropView()}
                >
                  <Trash2 size={15} aria-hidden="true" />
                  <span>{dropExecute ? t("viewMgmt.drop.run") : t("viewMgmt.drop.dryRun")}</span>
                </Button>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex min-h-11 items-start gap-3 rounded-md border border-red-200 bg-white p-3 text-sm text-slate-800">
                  <input
                    type="checkbox"
                    checked={dropExecute}
                    onChange={(event) => setDropExecute(event.currentTarget.checked)}
                    className="mt-1 h-4 w-4 rounded border-slate-300 text-red-700 focus:ring-red-500"
                  />
                  <span>{t("viewMgmt.drop.execute")}</span>
                </label>
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("viewMgmt.drop.confirmation")}</span>
                  <input
                    value={dropConfirmation}
                    onChange={(event) => setDropConfirmation(event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-red-200 bg-white px-3 py-2 focus:border-red-600 focus:ring-2 focus:ring-red-200"
                    placeholder={detail.name}
                  />
                </label>
              </div>
              {dropResult && <DbAdminExecutionResult result={dropResult} />}
            </div>
          ) : (
            <EmptyState title={t("viewMgmt.danger.emptyTitle")} hint={t("viewMgmt.danger.emptyHint")} />
          )}
        </WorkSection>
      </main>
    </>
  );
}
