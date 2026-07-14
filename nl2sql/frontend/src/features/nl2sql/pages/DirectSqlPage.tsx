import { useState } from "react";
import { Play, X } from "lucide-react";

import { Button, PageHeader } from "@engchina/production-ready-ui";

import { PageNotice } from "@/components/page-notice";
import { apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { SqlFileInput } from "../components/DbAdminShared";
import { Nl2SqlResultTable } from "../components/Nl2SqlResultTable";
import { sqlExecutePayload } from "../previewState";
import type { QueryResults } from "../types";
import { emptySelection, toAllowedObjects } from "../workbenchState";

/**
 * SELECT SQL を直接実行する専用ページ。
 * NL2SQL 生成とは独立した「SQL を手で書いて実行し結果を見る」導線。
 * プロファイル/スキーマ選択は持たず（profile_id=null, allowed_objects=[]）、
 * バックエンドの SELECT/WITH ガードと安全チェックに委ねる。
 */
export function DirectSqlPage() {
  const [sqlText, setSqlText] = useState("");
  const [sqlFileResetSignal, setSqlFileResetSignal] = useState(0);
  const [results, setResults] = useState<QueryResults | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const execute = async () => {
    const trimmed = sqlText.trim();
    if (!trimmed || loading) return;
    setLoading(true);
    setError("");
    setResults(null);
    try {
      const data = await apiPost<QueryResults>("/api/nl2sql/execute", {
        ...sqlExecutePayload(trimmed, null, toAllowedObjects(emptySelection())),
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
          {/* 主アクションバー: spec §4(border-t で区切り、primary → secondary を左から、size 統一) */}
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
            <Button
              type="button"
              variant="primary"
              size="lg"
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
