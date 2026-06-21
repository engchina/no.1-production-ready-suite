import { Check, Copy, Lightbulb, Play, ShieldCheck, Wrench } from "lucide-react";
import { useState } from "react";

import { Button, Card, CardContent, CardHeader, CardTitle, StatusBadge } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { engineLabel } from "../labels";
import type { GeneratedSqlPanelData } from "../types";

export function GeneratedSqlPanel({
  result,
  mode = "result",
  executeLoading = false,
  onExecute,
}: {
  result: GeneratedSqlPanelData | null;
  mode?: "result" | "preview";
  executeLoading?: boolean;
  onExecute?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  if (!result) return null;
  const displayedSql = result.executable_sql || result.generated_sql;
  const recommendations = result.recommendations ?? [];
  const optimizationHints = result.optimization_hints ?? [];
  const showRepairedSql = Boolean(result.repaired_sql && result.repaired_sql !== displayedSql);

  const copySql = async () => {
    await navigator.clipboard.writeText(displayedSql);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="space-y-2">
          <CardTitle>{t("nl2sql.sql.title")}</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge
              variant={result.safety.is_safe ? "success" : "danger"}
              label={result.safety.is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
            />
            <span className="rounded-md bg-slate-100 px-2 py-1 text-xs text-slate-700">
              {engineLabel(result.engine)}
            </span>
            {mode === "preview" && (
              <span className="rounded-md bg-sky-50 px-2 py-1 text-xs text-sky-900">
                {t("nl2sql.sql.preview")}
              </span>
            )}
            {result.fallback_reason && (
              <span className="rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-900">
                {t("nl2sql.result.fallback")}
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          {mode === "preview" && onExecute && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={executeLoading}
              disabled={!result.safety.is_safe}
              onClick={onExecute}
            >
              <Play size={15} aria-hidden="true" />
              <span>{t("nl2sql.action.executePreview")}</span>
            </Button>
          )}
          <Button type="button" variant="secondary" size="sm" onClick={copySql}>
            {copied ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
            <span>{copied ? t("nl2sql.sql.copied") : t("nl2sql.sql.copy")}</span>
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <pre className="max-h-72 overflow-auto rounded-md border border-slate-200 bg-slate-950 p-4 text-sm leading-6 text-slate-50">
          <code>{displayedSql}</code>
        </pre>
        <div className="grid gap-3 text-sm text-slate-700 md:grid-cols-2">
          <p>{result.explanation}</p>
          <dl className="grid gap-1 rounded-md bg-slate-50 p-3">
            <div className="flex justify-between gap-3">
              <dt>{t("nl2sql.result.rewritten")}</dt>
              <dd className="text-right">{result.rewritten_question}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt>{t("nl2sql.result.tables")}</dt>
              <dd className="font-mono text-xs">{result.safety.referenced_tables.join(", ") || "-"}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt>{t("nl2sql.result.columns")}</dt>
              <dd className="text-right font-mono text-xs">{result.safety.referenced_columns.join(", ") || "-"}</dd>
            </div>
          </dl>
        </div>
        {(recommendations.length > 0 || optimizationHints.length > 0) && (
          <div className="grid gap-3 md:grid-cols-2">
            {recommendations.length > 0 && (
              <section className="rounded-md border border-slate-200 bg-white p-3">
                <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <ShieldCheck size={15} className="text-sky-700" aria-hidden="true" />
                  {t("nl2sql.analysis.recommendations")}
                </h3>
                <ul className="grid gap-1 text-sm text-slate-700">
                  {recommendations.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </section>
            )}
            {optimizationHints.length > 0 && (
              <section className="rounded-md border border-slate-200 bg-white p-3">
                <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <Lightbulb size={15} className="text-amber-700" aria-hidden="true" />
                  {t("nl2sql.analysis.optimization")}
                </h3>
                <ul className="grid gap-1 text-sm text-slate-700">
                  {optimizationHints.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
        {showRepairedSql && (
          <section className="grid gap-2 rounded-md border border-sky-200 bg-sky-50 p-3">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <Wrench size={15} className="text-sky-700" aria-hidden="true" />
              {t("nl2sql.analysis.repairedSql")}
            </h3>
            <pre className="max-h-56 overflow-auto rounded-md border border-sky-100 bg-white p-3 text-sm leading-6 text-slate-900">
              <code>{result.repaired_sql}</code>
            </pre>
          </section>
        )}
        {(result.safety.blocked_reason || result.safety.warnings.length > 0) && (
          <div className="grid gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
            {result.safety.blocked_reason && <p>{result.safety.blocked_reason}</p>}
            {result.safety.warnings.map((warning) => (
              <p key={warning}>{warning}</p>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
