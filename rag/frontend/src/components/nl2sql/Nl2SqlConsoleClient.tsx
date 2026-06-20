"use client";

import { type FormEvent, useState } from "react";
import { Database, Play, RotateCcw, ShieldAlert, ShieldCheck, Sparkles } from "lucide-react";

import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FormStatus } from "@/components/ui/form-status";
import {
  ApiError,
  type JsonValue,
  type Nl2SqlExecuteResponse,
  type Nl2SqlGenerateResponse,
  type Nl2SqlGuardrailVerdict,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { useExecuteNl2Sql, useGenerateNl2Sql } from "@/lib/queries";
import { cn } from "@/lib/utils";

/** NL2SQL コンソール: 生成 → 人手プレビュー確認 → read-only 実行 の 2 段ゲート。 */
export function Nl2SqlConsoleClient() {
  const [question, setQuestion] = useState("");
  const [profile, setProfile] = useState("");
  const [allowed, setAllowed] = useState("");
  const [sql, setSql] = useState("");
  const [generation, setGeneration] = useState<Nl2SqlGenerateResponse | null>(null);
  const [execution, setExecution] = useState<Nl2SqlExecuteResponse | null>(null);

  const generate = useGenerateNl2Sql();
  const execute = useExecuteNl2Sql();

  const allowedObjects = () =>
    allowed
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);

  function onGenerate(event: FormEvent) {
    event.preventDefault();
    if (!question.trim()) return;
    execute.reset();
    setExecution(null);
    generate.mutate(
      {
        question: question.trim(),
        profile_name: profile.trim() || null,
        allowed_objects: allowedObjects(),
      },
      {
        onSuccess: (data) => {
          setGeneration(data);
          setSql(data.generated_sql);
        },
      }
    );
  }

  function onExecute() {
    if (!sql.trim()) return;
    execute.mutate(
      { sql: sql.trim(), allowed_objects: allowedObjects() },
      { onSuccess: (data) => setExecution(data) }
    );
  }

  function onReset() {
    setQuestion("");
    setProfile("");
    setAllowed("");
    setSql("");
    setGeneration(null);
    setExecution(null);
    generate.reset();
    execute.reset();
  }

  const generateError =
    generate.error instanceof ApiError ? generate.error.message : t("nl2sql.console.generateError");
  const executeError =
    execute.error instanceof ApiError ? execute.error.message : t("nl2sql.console.executeError");

  return (
    <>
      <PageHeader
        title={t("nl2sql.console.title")}
        subtitle={t("nl2sql.console.description")}
      />
      <div className="space-y-5 p-8">
        <Card>
          <CardHeader>
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
                <Sparkles size={20} aria-hidden />
              </div>
              <div>
                <CardTitle>{t("nl2sql.console.title")}</CardTitle>
                <CardDescription>{t("nl2sql.console.description")}</CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <form onSubmit={onGenerate} className="space-y-4">
              <div className="space-y-1.5">
                <label htmlFor="nl2sql-question" className="text-sm font-medium text-foreground">
                  {t("nl2sql.console.question.label")}
                </label>
                <textarea
                  id="nl2sql-question"
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  placeholder={t("nl2sql.console.question.placeholder")}
                  rows={3}
                  className="w-full resize-y rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                />
                <p className="text-xs text-muted">{t("nl2sql.console.question.help")}</p>
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div className="space-y-1.5">
                  <label htmlFor="nl2sql-profile" className="text-sm font-medium text-foreground">
                    {t("nl2sql.console.profile.label")}
                  </label>
                  <input
                    id="nl2sql-profile"
                    value={profile}
                    onChange={(event) => setProfile(event.target.value)}
                    placeholder={t("nl2sql.console.profile.placeholder")}
                    className="w-full rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  />
                </div>
                <div className="space-y-1.5">
                  <label htmlFor="nl2sql-allowed" className="text-sm font-medium text-foreground">
                    {t("nl2sql.console.allowedObjects.label")}
                  </label>
                  <input
                    id="nl2sql-allowed"
                    value={allowed}
                    onChange={(event) => setAllowed(event.target.value)}
                    placeholder={t("nl2sql.console.allowedObjects.placeholder")}
                    className="w-full rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  />
                </div>
              </div>
              <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
                <div className="min-h-6">
                  {generate.isError ? <FormStatus tone="danger" message={generateError} /> : null}
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={onReset}
                    disabled={generate.isPending || execute.isPending}
                    aria-label={t("nl2sql.console.actions.reset")}
                  >
                    <RotateCcw size={15} aria-hidden />
                    {t("nl2sql.console.actions.reset")}
                  </Button>
                  <Button
                    type="submit"
                    loading={generate.isPending}
                    disabled={!question.trim()}
                    aria-label={t("nl2sql.console.actions.generate")}
                  >
                    <Sparkles size={15} aria-hidden />
                    {generate.isPending
                      ? t("nl2sql.console.actions.generating")
                      : t("nl2sql.console.actions.generate")}
                  </Button>
                </div>
              </div>
            </form>
          </CardContent>
        </Card>

        {generation ? (
          <Card data-testid="nl2sql-generation">
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <CardTitle>{t("nl2sql.console.sql.label")}</CardTitle>
                <GuardrailBadge verdict={generation.guardrail} />
              </div>
              <CardDescription>{t("nl2sql.console.sql.help")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <RuntimeFact
                  label={t("nl2sql.console.router.backend")}
                  value={generation.generation_backend}
                />
                <RuntimeFact
                  label={t("nl2sql.console.router.profile")}
                  value={generation.profile_name || "—"}
                />
                <RuntimeFact
                  label={t("nl2sql.console.router.complexity")}
                  value={String(generation.router.complexity_score)}
                />
              </dl>

              {generation.narration ? (
                <div className="rounded-md border border-border bg-muted/20 p-3">
                  <div className="text-xs font-medium text-muted">
                    {t("nl2sql.console.narration")}
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                    {generation.narration}
                  </p>
                </div>
              ) : null}

              <div className="space-y-1.5">
                <label htmlFor="nl2sql-sql" className="text-sm font-medium text-foreground">
                  {t("nl2sql.console.sql.label")}
                </label>
                <textarea
                  id="nl2sql-sql"
                  value={sql}
                  onChange={(event) => setSql(event.target.value)}
                  rows={6}
                  spellCheck={false}
                  className="w-full resize-y rounded-md border border-border bg-muted/20 px-3 py-2 font-mono text-[13px] leading-relaxed text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                />
              </div>

              {generation.guardrail.allowed ? (
                <FormStatus tone="info" message={t("nl2sql.console.gate.review")} />
              ) : (
                <ViolationList violations={generation.guardrail.violations} />
              )}

              <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
                <div className="min-h-6">
                  {execute.isError ? <FormStatus tone="danger" message={executeError} /> : null}
                </div>
                <Button
                  type="button"
                  loading={execute.isPending}
                  disabled={!sql.trim()}
                  onClick={onExecute}
                  aria-label={t("nl2sql.console.actions.execute")}
                >
                  <Play size={15} aria-hidden />
                  {execute.isPending
                    ? t("nl2sql.console.actions.executing")
                    : t("nl2sql.console.actions.execute")}
                </Button>
              </div>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="py-10 text-center text-sm text-muted">
              {t("nl2sql.console.empty")}
            </CardContent>
          </Card>
        )}

        {execution ? (
          <Card data-testid="nl2sql-execution">
            <CardHeader>
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
                  <Database size={20} aria-hidden />
                </div>
                <CardTitle>{t("nl2sql.console.result.title")}</CardTitle>
              </div>
            </CardHeader>
            <CardContent>
              {execution.executed && execution.result ? (
                <ResultTable result={execution.result} />
              ) : (
                <div className="space-y-3">
                  <FormStatus tone="danger" message={t("nl2sql.console.blocked")} />
                  <ViolationList
                    violations={
                      execution.guardrail.violations.length
                        ? execution.guardrail.violations
                        : execution.blocked_reason
                          ? [execution.blocked_reason]
                          : []
                    }
                  />
                </div>
              )}
            </CardContent>
          </Card>
        ) : null}
      </div>
    </>
  );
}

function GuardrailBadge({ verdict }: { verdict: Nl2SqlGuardrailVerdict }) {
  const allowed = verdict.allowed;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold",
        allowed ? "bg-success-bg text-success" : "bg-danger-bg text-danger"
      )}
    >
      {allowed ? (
        <ShieldCheck size={14} aria-hidden />
      ) : (
        <ShieldAlert size={14} aria-hidden />
      )}
      {allowed ? t("nl2sql.console.guardrail.allowed") : t("nl2sql.console.guardrail.blocked")}
      <span className="font-normal text-muted">
        · {t("nl2sql.console.guardrail.statementType")}: {verdict.statement_type}
      </span>
    </span>
  );
}

function ViolationList({ violations }: { violations: string[] }) {
  if (!violations.length) return null;
  return (
    <div className="rounded-md border border-danger bg-danger-bg/40 p-3">
      <ul className="list-disc space-y-1 pl-5 text-xs text-danger">
        {violations.map((violation) => (
          <li key={violation}>{violation}</li>
        ))}
      </ul>
    </div>
  );
}

function RuntimeFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-muted/20 p-3">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-semibold text-foreground">{value}</dd>
    </div>
  );
}

function ResultTable({
  result,
}: {
  result: NonNullable<Nl2SqlExecuteResponse["result"]>;
}) {
  if (result.row_count === 0) {
    return <p className="py-6 text-center text-sm text-muted">{t("nl2sql.console.result.empty")}</p>;
  }
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/40">
              {result.columns.map((column) => (
                <th
                  key={column}
                  scope="col"
                  className="whitespace-nowrap px-3 py-2 text-left font-semibold text-foreground"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, rowIndex) => (
              <tr key={rowIndex} className="border-b border-border last:border-0">
                {row.map((cell, cellIndex) => (
                  <td
                    key={cellIndex}
                    className="whitespace-nowrap px-3 py-2 font-mono text-[13px] tabular-nums text-foreground"
                  >
                    {formatCell(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">
        {result.row_count} {t("nl2sql.console.result.rows")}
        {result.truncated ? ` · ${t("nl2sql.console.result.truncated")}` : ""}
      </p>
    </div>
  );
}

function formatCell(cell: JsonValue): string {
  if (cell === null) return "—";
  if (typeof cell === "object") return JSON.stringify(cell);
  return String(cell);
}
