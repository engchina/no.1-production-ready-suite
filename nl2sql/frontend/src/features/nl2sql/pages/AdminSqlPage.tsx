import { useState } from "react";
import { Play, X } from "lucide-react";

import { Button } from "@engchina/production-ready-ui";

import { PageHeader } from "@/components/PageHeader";
import { PageNotice } from "@/components/page-notice";
import { apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import {
  DbAdminExecutionResult,
  ExecutionConfirmationField,
  SqlFileInput,
} from "../components/DbAdminShared";
import type { DbAdminExecuteData } from "../types";

const ADMIN_EXECUTE_CONFIRMATION = "ADMIN_EXECUTE";
const MUTATING_SQL_TOKEN =
  /\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|begin|declare|call)\b/i;

function stripLeadingSqlComments(sql: string): string {
  let rest = sql.trim();
  let changed = true;
  while (changed) {
    changed = false;
    if (rest.startsWith("--")) {
      const nextLine = rest.indexOf("\n");
      rest = nextLine >= 0 ? rest.slice(nextLine + 1).trimStart() : "";
      changed = true;
    }
    if (rest.startsWith("/*")) {
      const close = rest.indexOf("*/");
      if (close < 0) return rest;
      rest = rest.slice(close + 2).trimStart();
      changed = true;
    }
  }
  return rest;
}

function isSingleSelectSql(sql: string): boolean {
  const normalized = sql.trim().replace(/;+$/g, "").trim();
  if (!normalized || normalized.includes(";")) return false;
  if (MUTATING_SQL_TOKEN.test(normalized)) return false;
  return /^(select|with)\b/i.test(stripLeadingSqlComments(normalized));
}

/** 管理者向け SQL 実行ページ。更新系 SQL は確認語・RBAC・監査を必須とする。 */
export function AdminSqlPage() {
  const [sqlText, setSqlText] = useState("");
  const [sqlFileResetSignal, setSqlFileResetSignal] = useState(0);
  const [confirmation, setConfirmation] = useState("");
  const [result, setResult] = useState<DbAdminExecuteData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const trimmedSql = sqlText.trim();
  const requiresConfirmation = Boolean(trimmedSql) && !isSingleSelectSql(trimmedSql);
  const confirmed = confirmation.trim() === ADMIN_EXECUTE_CONFIRMATION;
  const canExecute = Boolean(trimmedSql) && !loading && (!requiresConfirmation || confirmed);

  const execute = async () => {
    if (!canExecute) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const data = await apiPost<DbAdminExecuteData>("/api/nl2sql/db-admin/execute", {
        sql: trimmedSql,
        row_limit: 100,
        confirmation: requiresConfirmation ? confirmation.trim() : "",
        reason: requiresConfirmation ? "admin-sql-admin" : "admin-sql-select",
      });
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("nl2sql.error.executeSqlFailed"));
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  const clear = () => {
    setSqlText("");
    setConfirmation("");
    setResult(null);
    setError("");
    setSqlFileResetSignal((value) => value + 1);
  };

  const actionButtons = (
    <>
      <Button
        type="button"
        variant={requiresConfirmation ? "danger" : "primary"}
        size="lg"
        className="w-full sm:w-auto"
        loading={loading}
        disabled={!canExecute}
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
    </>
  );

  return (
    <>
      <PageHeader title={t("nav.adminSql")} subtitle={t("nl2sql.adminSqlRunner.description")} />
      <main className="grid gap-4 p-4 lg:p-8" data-testid="nl2sql-admin-sql">
        <PageNotice notice={error ? { tone: "danger", message: error } : null} />
        <section className="grid gap-4 rounded-md border border-border bg-card p-4">
          <label className="grid gap-2 text-sm font-medium text-foreground">
            <span>{t("nl2sql.adminSqlRunner.label")}</span>
            <textarea
              aria-label={t("nl2sql.adminSqlRunner.label")}
              value={sqlText}
              onChange={(event) => setSqlText(event.currentTarget.value)}
              disabled={loading}
              rows={12}
              className="min-h-64 rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
              placeholder={t("nl2sql.adminSqlRunner.placeholder")}
            />
          </label>
          <SqlFileInput
            resetSignal={sqlFileResetSignal}
            disabled={loading}
            onLoad={(text) => {
              setSqlText(text);
              setConfirmation("");
              setResult(null);
              setError("");
            }}
          />
          {requiresConfirmation ? (
            <ExecutionConfirmationField
              value={confirmation}
              onChange={setConfirmation}
              confirmed={confirmed}
              placeholder={ADMIN_EXECUTE_CONFIRMATION}
              expectedLabel={ADMIN_EXECUTE_CONFIRMATION}
              helper={t("nl2sql.adminSqlRunner.adminHelper")}
              tone="danger"
              disabled={loading}
              actions={actionButtons}
            />
          ) : (
            <div className="flex flex-col gap-2 border-t border-border pt-4 sm:flex-row sm:flex-wrap sm:items-center">
              {actionButtons}
            </div>
          )}
          {result && <DbAdminExecutionResult result={result} />}
        </section>
      </main>
    </>
  );
}
