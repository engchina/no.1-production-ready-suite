import { useState } from "react";
import { Play, X } from "lucide-react";

import { Button, PageHeader } from "@engchina/production-ready-ui";

import { PageNotice } from "@/components/page-notice";
import { Banner } from "@/components/ui/banner";
import { useAuth } from "@/features/security/AuthProvider";
import { apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { SqlFileInput } from "../components/DbAdminShared";
import { Nl2SqlResultTable } from "../components/Nl2SqlResultTable";
import { sqlExecutePayload } from "../previewState";
import type { QueryResults } from "../types";
import { emptySelection, toAllowedObjects } from "../workbenchState";

/**
 * SELECT/WITH を直接実行する AI 活用ページ。
 * 管理 SQL API へは接続せず、通常の SELECT-only 実行境界を使用する。
 */
export function DirectSqlPage() {
  const { hasPermission } = useAuth();
  const canExecute = hasPermission("search.execute");

  if (!canExecute) {
    return (
      <>
        <PageHeader title={t("nav.directSql")} subtitle={t("nl2sql.sqlRunner.description")} />
        <main className="p-4 lg:p-8">
          <Banner severity="info">{t("nl2sql.permission.executeRequired")}</Banner>
        </main>
      </>
    );
  }

  return <ExecutableDirectSqlPage />;
}

function ExecutableDirectSqlPage() {
  const [sqlText, setSqlText] = useState("");
  const [sqlFileResetSignal, setSqlFileResetSignal] = useState(0);
  const [results, setResults] = useState<QueryResults | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const execute = async () => {
    const trimmedSql = sqlText.trim();
    if (!trimmedSql || loading) return;
    setLoading(true);
    setError("");
    setResults(null);
    try {
      const data = await apiPost<QueryResults>("/api/nl2sql/execute", {
        ...sqlExecutePayload(trimmedSql, null, toAllowedObjects(emptySelection())),
      });
      setResults(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("nl2sql.error.executeSqlFailed"));
      setResults(null);
    } finally {
      setLoading(false);
    }
  };

  const clear = () => {
    setSqlText("");
    setResults(null);
    setError("");
    setSqlFileResetSignal((value) => value + 1);
  };

  return (
    <>
      <PageHeader title={t("nav.directSql")} subtitle={t("nl2sql.sqlRunner.description")} />
      <main className="grid gap-4 p-4 lg:p-8" data-testid="nl2sql-direct-sql">
        <PageNotice notice={error ? { tone: "danger", message: error } : null} />
        <section className="grid gap-4 rounded-md border border-border bg-card p-4">
          <label className="grid gap-2 text-sm font-medium text-foreground">
            <span>{t("nl2sql.sqlRunner.label")}</span>
            <textarea
              aria-label={t("nl2sql.sqlRunner.label")}
              value={sqlText}
              onChange={(event) => setSqlText(event.currentTarget.value)}
              disabled={loading}
              rows={12}
              className="min-h-64 rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
              placeholder={t("nl2sql.sqlRunner.placeholder")}
            />
          </label>
          <SqlFileInput
            resetSignal={sqlFileResetSignal}
            disabled={loading}
            onLoad={(text) => {
              setSqlText(text);
              setResults(null);
              setError("");
            }}
          />
          <div className="flex flex-col gap-2 border-t border-border pt-4 sm:flex-row sm:flex-wrap sm:items-center">
            <Button
              type="button"
              variant="primary"
              size="lg"
              className="w-full sm:w-auto"
              loading={loading}
              disabled={!sqlText.trim() || loading}
              onClick={() => void execute()}
            >
              <Play size={16} aria-hidden="true" />
              <span>{t("nl2sql.action.executeSql")}</span>
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="lg"
              className="w-full sm:w-auto"
              disabled={!sqlText || loading}
              onClick={clear}
            >
              <X size={16} aria-hidden="true" />
              <span>{t("nl2sql.action.clearSql")}</span>
            </Button>
          </div>
          <Nl2SqlResultTable results={results} />
        </section>
      </main>
    </>
  );
}
