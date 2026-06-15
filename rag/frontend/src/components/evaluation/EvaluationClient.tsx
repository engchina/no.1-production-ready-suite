import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  FileSearch,
  FlaskConical,
  GitCompare,
  XCircle,
} from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";

import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/StateViews";
import { KnowledgeBaseScopePicker } from "@/components/knowledge-bases/KnowledgeBaseScopePicker";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import {
  ApiError,
  type EvaluationCompareResponse,
  type EvaluationExperiment,
  type EvaluationMetricName,
  type EvaluationMetrics,
  type EvaluationRunRequestBody,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { useCompareEvaluation, useRunEvaluation } from "@/lib/queries";
import { cn } from "@/lib/utils";

const SAMPLE_REQUEST = JSON.stringify(
  {
    cases: [
      {
        id: "policy-approval-flow-basic",
        query: "経費申請の承認フローを教えてください。",
        relevant_document_ids: ["doc-expense-policy"],
        expected_answer_keywords: ["部門長", "承認"],
      },
    ],
    top_k: 10,
    rerank_top_n: 5,
    mode: "hybrid",
    filters: { status: "INDEXED" },
    thresholds: {
      precision_at_k: 0.4,
      recall_at_k: 0.8,
      mrr: 0.7,
      answer_keyword_hit_rate: 0.8,
      groundedness_pass_rate: 0.9,
    },
  },
  null,
  2
);

const SAMPLE_EXPERIMENTS = JSON.stringify(
  [
    {
      id: "hybrid-k10",
      top_k: 10,
      rerank_top_n: 5,
      mode: "hybrid",
      filters: { status: "INDEXED" },
    },
    {
      id: "keyword-k10",
      top_k: 10,
      rerank_top_n: 5,
      mode: "keyword",
      filters: { status: "INDEXED" },
    },
  ],
  null,
  2
);

const RANKING_METRICS: EvaluationMetricName[] = [
  "mrr",
  "recall_at_k",
  "precision_at_k",
  "answer_keyword_hit_rate",
  "groundedness_pass_rate",
];
const RANKING_METRIC_OPTIONS = RANKING_METRICS.map((metric) => ({
  value: metric,
  label: metricLabel(metric),
})) satisfies SelectFieldOption<EvaluationMetricName>[];

/** RAG golden set 評価画面。 */
export function EvaluationClient() {
  const runMutation = useRunEvaluation();
  const compareMutation = useCompareEvaluation();
  const [requestJson, setRequestJson] = useState(SAMPLE_REQUEST);
  const [experimentsJson, setExperimentsJson] = useState(SAMPLE_EXPERIMENTS);
  const [rankingMetric, setRankingMetric] = useState<EvaluationMetricName>("mrr");
  const [knowledgeBaseIds, setKnowledgeBaseIds] = useState<string[]>([]);
  const [runError, setRunError] = useState("");
  const [compareError, setCompareError] = useState("");

  const parsedRequest = useMemo(() => parseEvaluationRequest(requestJson), [requestJson]);
  const parsedExperiments = useMemo(() => parseExperiments(experimentsJson), [experimentsJson]);
  const canRun = parsedRequest.ok;
  const canCompare = parsedRequest.ok && parsedExperiments.ok;

  const runEvaluation = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!parsedRequest.ok) return;
    setRunError("");
    runMutation.reset();
    try {
      await runMutation.mutateAsync(
        applyRequestKnowledgeBaseScope(parsedRequest.value, knowledgeBaseIds)
      );
    } catch (error) {
      setRunError(error instanceof ApiError ? error.message : t("evaluation.error.run"));
    }
  };

  const compareEvaluation = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!parsedRequest.ok || !parsedExperiments.ok) return;
    setCompareError("");
    compareMutation.reset();
    try {
      await compareMutation.mutateAsync({
        cases: parsedRequest.value.cases,
        thresholds: parsedRequest.value.thresholds ?? null,
        experiments: applyExperimentKnowledgeBaseScope(parsedExperiments.value, knowledgeBaseIds),
        ranking_metric: rankingMetric,
      });
    } catch (error) {
      setCompareError(error instanceof ApiError ? error.message : t("evaluation.error.compare"));
    }
  };

  const validationMessage = !parsedRequest.ok ? parsedRequest.error : "";
  const experimentValidationMessage = !parsedExperiments.ok ? parsedExperiments.error : "";

  return (
    <div>
      <PageHeader title={t("nav.evaluation")} subtitle={t("evaluation.subtitle")} />
      <div className="space-y-6 p-8">
        <Card className="min-w-0">
          <CardContent className="pt-5">
            <KnowledgeBaseScopePicker
              selectedIds={knowledgeBaseIds}
              onChange={setKnowledgeBaseIds}
              disabled={runMutation.isPending || compareMutation.isPending}
              helper={t("evaluation.knowledgeBaseScope.helper")}
            />
          </CardContent>
        </Card>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FlaskConical size={16} className="text-primary" aria-hidden />
                {t("evaluation.input.title")}
              </CardTitle>
              <CardDescription>{t("evaluation.input.description")}</CardDescription>
            </CardHeader>
            <CardContent>
              <form className="space-y-4" onSubmit={(event) => void runEvaluation(event)}>
                <JsonField
                  id="evaluation-request-json"
                  label={t("evaluation.input.label")}
                  value={requestJson}
                  rows={18}
                  placeholder={t("evaluation.input.placeholder")}
                  onChange={setRequestJson}
                />
                {validationMessage ? <ValidationNotice message={validationMessage} /> : null}
                {runError ? <ErrorNotice message={runError} /> : null}
                <div className="flex flex-wrap items-center gap-2">
                  <Button type="submit" loading={runMutation.isPending} disabled={!canRun}>
                    <BarChart3 size={15} aria-hidden />
                    {runMutation.isPending
                      ? t("evaluation.actions.running")
                      : t("evaluation.actions.run")}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => {
                      setRequestJson(SAMPLE_REQUEST);
                      setRunError("");
                    }}
                  >
                    {t("evaluation.actions.loadSample")}
                  </Button>
                </div>
              </form>
            </CardContent>
          </Card>

          <Card className="min-w-0">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <GitCompare size={16} className="text-primary" aria-hidden />
                {t("evaluation.compare.title")}
              </CardTitle>
              <CardDescription>{t("evaluation.compare.description")}</CardDescription>
            </CardHeader>
            <CardContent>
              <form className="space-y-4" onSubmit={(event) => void compareEvaluation(event)}>
                <SelectField
                  id="evaluation-ranking-metric"
                  label={t("evaluation.compare.metric")}
                  value={rankingMetric}
                  options={RANKING_METRIC_OPTIONS}
                  onValueChange={setRankingMetric}
                />
                <JsonField
                  id="evaluation-experiments-json"
                  label={t("evaluation.compare.experiments")}
                  value={experimentsJson}
                  rows={13}
                  placeholder={t("evaluation.compare.placeholder")}
                  onChange={setExperimentsJson}
                />
                {experimentValidationMessage ? (
                  <ValidationNotice message={experimentValidationMessage} />
                ) : null}
                {compareError ? <ErrorNotice message={compareError} /> : null}
                <Button
                  type="submit"
                  className="w-full"
                  loading={compareMutation.isPending}
                  disabled={!canCompare}
                >
                  <GitCompare size={15} aria-hidden />
                  {compareMutation.isPending
                    ? t("evaluation.actions.comparing")
                    : t("evaluation.actions.compare")}
                </Button>
              </form>
            </CardContent>
          </Card>
        </div>

        {runMutation.data ? (
          <EvaluationResult metrics={runMutation.data} />
        ) : (
          <Card>
            <CardContent className="pt-5">
              <EmptyState
                title={t("evaluation.result.empty")}
                hint={t("evaluation.result.emptyHint")}
              />
            </CardContent>
          </Card>
        )}

        {compareMutation.data ? <CompareResult comparison={compareMutation.data} /> : null}
      </div>
    </div>
  );
}

function EvaluationResult({ metrics }: { metrics: EvaluationMetrics }) {
  return (
    <section className="min-w-0 space-y-4" aria-labelledby="evaluation-result-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="evaluation-result-title" className="text-base font-semibold text-foreground">
          {t("evaluation.result.title")}
        </h2>
        <StatusBadge passed={metrics.passed} />
      </div>
      <MetricGrid metrics={metrics} />
      <IngestionQualityPanel metrics={metrics} />

      {metrics.threshold_failures.length ? (
        <Banner severity="warning" title={t("evaluation.thresholdFailures")}>
          <ul className="space-y-1">
            {metrics.threshold_failures.map((failure) => (
              <li key={failure.metric}>
                {metricLabel(failure.metric)}: {formatPercent(failure.actual)} /{" "}
                {formatPercent(failure.threshold)}
              </li>
            ))}
          </ul>
        </Banner>
      ) : null}

      {Object.keys(metrics.failure_reason_counts).length ? (
        <div className="rounded-md border border-border bg-card p-4 text-sm">
          <p className="font-medium text-foreground">{t("evaluation.failureReasons")}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {Object.entries(metrics.failure_reason_counts).map(([reason, count]) => (
              <span
                key={reason}
                className="rounded-full border border-border bg-background px-2.5 py-1 text-xs text-muted"
              >
                {reason}: {count}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      <CaseTable metrics={metrics} />
    </section>
  );
}

function IngestionQualityPanel({ metrics }: { metrics: EvaluationMetrics }) {
  const quality = metrics.ingestion_quality;
  const warningEntries = Object.entries(quality.warning_counts);
  const parserEntries = Object.entries(quality.parser_profile_counts);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <FileSearch size={16} className="text-primary" aria-hidden />
          {t("evaluation.ingestionQuality.title")}
        </CardTitle>
        <CardDescription>{t("evaluation.ingestionQuality.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <QualityStat
            label={t("evaluation.ingestionQuality.documents")}
            value={quality.document_count}
          />
          <QualityStat
            label={t("evaluation.ingestionQuality.tables")}
            value={quality.table_document_count}
          />
          <QualityStat
            label={t("evaluation.ingestionQuality.figures")}
            value={quality.figure_document_count}
          />
          <QualityStat
            label={t("evaluation.ingestionQuality.longDocuments")}
            value={quality.long_document_count}
          />
        </div>

        {quality.risk_counts.high || quality.risk_counts.medium ? (
          <Banner severity="warning" title={t("evaluation.ingestionQuality.riskTitle")}>
            <p>
              {t("evaluation.ingestionQuality.riskSummary", {
                high: quality.risk_counts.high ?? 0,
                medium: quality.risk_counts.medium ?? 0,
              })}
            </p>
          </Banner>
        ) : null}

        <div className="grid gap-4 lg:grid-cols-2">
          <QualityChipGroup
            title={t("evaluation.ingestionQuality.warnings")}
            emptyText={t("evaluation.ingestionQuality.noWarnings")}
            entries={warningEntries}
            icon="warning"
          />
          <QualityChipGroup
            title={t("evaluation.ingestionQuality.parserProfiles")}
            emptyText={t("evaluation.ingestionQuality.noParserProfiles")}
            entries={parserEntries}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function QualityStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="tnum mt-1 text-xl font-semibold text-foreground">{value}</p>
    </div>
  );
}

function QualityChipGroup({
  title,
  emptyText,
  entries,
  icon,
}: {
  title: string;
  emptyText: string;
  entries: [string, number][];
  icon?: "warning";
}) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <p className="text-sm font-medium text-foreground">{title}</p>
      {entries.length ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {entries.map(([name, count]) => (
            <span
              key={name}
              className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 text-xs text-muted"
            >
              {icon === "warning" ? <AlertTriangle size={13} aria-hidden /> : null}
              {qualityLabel(name)}: {count}
            </span>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-sm text-muted">{emptyText}</p>
      )}
    </div>
  );
}

function MetricGrid({ metrics }: { metrics: EvaluationMetrics }) {
  const items = [
    { label: t("evaluation.metric.precision"), value: formatPercent(metrics.precision_at_k) },
    { label: t("evaluation.metric.recall"), value: formatPercent(metrics.recall_at_k) },
    { label: t("evaluation.metric.mrr"), value: formatPercent(metrics.mrr) },
    {
      label: t("evaluation.metric.answerHit"),
      value: formatPercent(metrics.answer_keyword_hit_rate),
    },
    {
      label: t("evaluation.metric.groundedness"),
      value: formatPercent(metrics.groundedness_pass_rate),
    },
    {
      label: t("evaluation.metric.errors"),
      value: `${metrics.error_count} / ${metrics.case_count}`,
    },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {items.map((item) => (
        <Card key={item.label}>
          <CardContent className="pt-5">
            <p className="text-xs text-muted">{item.label}</p>
            <p className="tnum mt-2 text-2xl font-semibold text-foreground">{item.value}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function qualityLabel(value: string) {
  const map: Record<string, string> = {
    table_structure_review: t("evaluation.ingestionQuality.warning.table"),
    figure_ocr_review: t("evaluation.ingestionQuality.warning.figure"),
    long_document: t("evaluation.ingestionQuality.warning.longDocument"),
    low_extraction_confidence: t("evaluation.ingestionQuality.warning.lowConfidence"),
    no_structured_elements: t("evaluation.ingestionQuality.warning.noElements"),
    content_type_missing: t("evaluation.ingestionQuality.warning.contentTypeMissing"),
    content_type_extension_mismatch: t("evaluation.ingestionQuality.warning.contentTypeMismatch"),
    unknown_modality: t("evaluation.ingestionQuality.warning.unknownModality"),
    duplicate_content: t("evaluation.ingestionQuality.warning.duplicate"),
    large_file: t("evaluation.ingestionQuality.warning.largeFile"),
    enterprise_ai_pdf_layout: t("sourceProfile.parser.pdf"),
    enterprise_ai_image_ocr: t("sourceProfile.parser.image"),
    enterprise_ai_text_structure: t("sourceProfile.parser.text"),
    enterprise_ai_office_structure: t("sourceProfile.parser.office"),
    enterprise_ai_generic: t("sourceProfile.parser.generic"),
    legacy: t("evaluation.ingestionQuality.parser.legacy"),
  };
  return map[value] ?? value;
}

function CaseTable({ metrics }: { metrics: EvaluationMetrics }) {
  return (
    <section aria-labelledby="evaluation-cases-title">
      <h3 id="evaluation-cases-title" className="mb-3 text-sm font-semibold text-foreground">
        {t("evaluation.cases")}
      </h3>
      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[680px] text-left text-sm">
            <thead className="bg-background text-xs text-muted">
              <tr>
                <th className="whitespace-nowrap px-3 py-2 font-medium sm:px-4 sm:py-3">{t("evaluation.case.id")}</th>
                <th className="hidden whitespace-nowrap px-3 py-2 font-medium sm:table-cell sm:px-4 sm:py-3">{t("evaluation.metric.precision")}</th>
                <th className="hidden whitespace-nowrap px-3 py-2 font-medium sm:table-cell sm:px-4 sm:py-3">{t("evaluation.metric.recall")}</th>
                <th className="whitespace-nowrap px-3 py-2 font-medium sm:px-4 sm:py-3">{t("evaluation.metric.mrr")}</th>
                <th className="whitespace-nowrap px-3 py-2 font-medium sm:px-4 sm:py-3">{t("evaluation.case.hit")}</th>
                <th className="hidden whitespace-nowrap px-3 py-2 font-medium md:table-cell sm:px-4 sm:py-3">{t("evaluation.case.failures")}</th>
                <th className="hidden whitespace-nowrap px-3 py-2 font-medium lg:table-cell sm:px-4 sm:py-3">{t("evaluation.case.trace")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {metrics.case_results.map((result) => (
                <tr key={result.case_id}>
                  <td className="break-words px-3 py-2 font-medium text-foreground sm:px-4 sm:py-3">{result.case_id}</td>
                  <td className="tnum hidden whitespace-nowrap px-3 py-2 sm:table-cell sm:px-4 sm:py-3">{formatPercent(result.precision_at_k)}</td>
                  <td className="tnum hidden whitespace-nowrap px-3 py-2 sm:table-cell sm:px-4 sm:py-3">{formatPercent(result.recall_at_k)}</td>
                  <td className="tnum whitespace-nowrap px-3 py-2 sm:px-4 sm:py-3">{formatPercent(result.reciprocal_rank)}</td>
                  <td className="px-3 py-2 sm:px-4 sm:py-3">
                    <BooleanIcon value={result.answer_keyword_hit && result.groundedness_passed} />
                  </td>
                  <td className="hidden break-words px-3 py-2 text-xs text-muted md:table-cell sm:px-4 sm:py-3">
                    {result.failure_reasons.length ? result.failure_reasons.join(", ") : "-"}
                  </td>
                  <td className="tnum hidden whitespace-nowrap px-3 py-2 text-xs text-muted lg:table-cell sm:px-4 sm:py-3">
                    {result.trace_id.slice(0, 12)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function CompareResult({ comparison }: { comparison: EvaluationCompareResponse }) {
  return (
    <section className="min-w-0 space-y-3" aria-labelledby="evaluation-compare-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="evaluation-compare-title" className="text-base font-semibold text-foreground">
          {t("evaluation.compare.title")}
        </h2>
        {comparison.best_experiment_id ? (
          <span className="rounded-full bg-success-bg px-3 py-1 text-xs font-medium text-success">
            {t("evaluation.compare.best")}: {comparison.best_experiment_id}
          </span>
        ) : null}
      </div>
      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="bg-background text-xs text-muted">
              <tr>
                <th className="whitespace-nowrap px-3 py-2 font-medium sm:px-4 sm:py-3">{t("evaluation.compare.rank")}</th>
                <th className="whitespace-nowrap px-3 py-2 font-medium sm:px-4 sm:py-3">{t("evaluation.compare.experiment")}</th>
                <th className="whitespace-nowrap px-3 py-2 font-medium sm:px-4 sm:py-3">{t("evaluation.compare.score")}</th>
                <th className="hidden whitespace-nowrap px-3 py-2 font-medium md:table-cell sm:px-4 sm:py-3">{t("evaluation.metric.precision")}</th>
                <th className="hidden whitespace-nowrap px-3 py-2 font-medium md:table-cell sm:px-4 sm:py-3">{t("evaluation.metric.recall")}</th>
                <th className="hidden whitespace-nowrap px-3 py-2 font-medium md:table-cell sm:px-4 sm:py-3">{t("evaluation.metric.mrr")}</th>
                <th className="whitespace-nowrap px-3 py-2 font-medium sm:px-4 sm:py-3">
                  <span className="sr-only">{t("evaluation.status.passed")}</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {comparison.results.map((result) => (
                <tr key={result.experiment.id}>
                  <td className="tnum whitespace-nowrap px-3 py-2 sm:px-4 sm:py-3">{result.rank}</td>
                  <td className="break-words px-3 py-2 font-medium text-foreground sm:px-4 sm:py-3">
                    {result.experiment.id}
                  </td>
                  <td className="tnum whitespace-nowrap px-3 py-2 sm:px-4 sm:py-3">{formatPercent(result.ranking_score)}</td>
                  <td className="tnum hidden whitespace-nowrap px-3 py-2 md:table-cell sm:px-4 sm:py-3">
                    {formatPercent(result.metrics.precision_at_k)}
                  </td>
                  <td className="tnum hidden whitespace-nowrap px-3 py-2 md:table-cell sm:px-4 sm:py-3">{formatPercent(result.metrics.recall_at_k)}</td>
                  <td className="tnum hidden whitespace-nowrap px-3 py-2 md:table-cell sm:px-4 sm:py-3">{formatPercent(result.metrics.mrr)}</td>
                  <td className="px-3 py-2 sm:px-4 sm:py-3">
                    <StatusBadge passed={result.metrics.passed} compact />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function JsonField({
  id,
  label,
  value,
  rows,
  placeholder,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  rows: number;
  placeholder: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-sm font-medium text-foreground">
        {label}
      </label>
      <textarea
        id={id}
        value={value}
        rows={rows}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="min-w-0 w-full resize-y rounded-md border border-border bg-card px-3 py-2 font-mono text-xs leading-relaxed text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary"
      />
    </div>
  );
}

function ValidationNotice({ message }: { message: string }) {
  return <Banner severity="warning">{message}</Banner>;
}

function ErrorNotice({ message }: { message: string }) {
  return <Banner severity="danger">{message}</Banner>;
}

function applyRequestKnowledgeBaseScope(
  request: EvaluationRunRequestBody,
  knowledgeBaseIds: string[]
): EvaluationRunRequestBody {
  if (knowledgeBaseIds.length === 0) return request;
  return {
    ...request,
    filters: stripKnowledgeBaseFilter(request.filters) ?? {},
    knowledge_base_ids: knowledgeBaseIds,
  };
}

function applyExperimentKnowledgeBaseScope(
  experiments: EvaluationExperiment[],
  knowledgeBaseIds: string[]
): EvaluationExperiment[] {
  if (knowledgeBaseIds.length === 0) return experiments;
  return experiments.map((experiment) => ({
    ...experiment,
    filters: stripKnowledgeBaseFilter(experiment.filters) ?? {},
    knowledge_base_ids: knowledgeBaseIds,
  }));
}

function stripKnowledgeBaseFilter(
  filters: Record<string, string> | undefined
): Record<string, string> | undefined {
  if (!filters) return undefined;
  const next = { ...filters };
  delete next.knowledge_base_id;
  return Object.keys(next).length ? next : undefined;
}

function StatusBadge({ passed, compact = false }: { passed: boolean; compact?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium",
        passed ? "bg-success-bg text-success" : "bg-danger-bg text-danger"
      )}
    >
      {passed ? (
        <CheckCircle2 size={14} aria-hidden />
      ) : (
        <XCircle size={14} aria-hidden />
      )}
      {compact ? (
        <span className="sr-only">
          {passed ? t("evaluation.status.passed") : t("evaluation.status.failed")}
        </span>
      ) : passed ? (
        t("evaluation.status.passed")
      ) : (
        t("evaluation.status.failed")
      )}
    </span>
  );
}

function BooleanIcon({ value }: { value: boolean }) {
  return value ? (
    <CheckCircle2 size={16} className="text-success" aria-label={t("evaluation.status.passed")} />
  ) : (
    <XCircle size={16} className="text-danger" aria-label={t("evaluation.status.failed")} />
  );
}

function parseEvaluationRequest(raw: string): ParseResult<EvaluationRunRequestBody> {
  const parsed = parseJson(raw);
  if (!parsed.ok) return parsed;
  if (!isRecord(parsed.value) || !Array.isArray(parsed.value.cases)) {
    return { ok: false, error: t("evaluation.input.noCases") };
  }
  if (parsed.value.cases.length < 1) {
    return { ok: false, error: t("evaluation.input.noCases") };
  }
  return { ok: true, value: parsed.value as unknown as EvaluationRunRequestBody };
}

function parseExperiments(raw: string): ParseResult<EvaluationExperiment[]> {
  const parsed = parseJson(raw);
  if (!parsed.ok) return parsed;
  if (!Array.isArray(parsed.value) || parsed.value.length < 1) {
    return { ok: false, error: t("evaluation.input.invalidJson") };
  }
  return { ok: true, value: parsed.value as unknown as EvaluationExperiment[] };
}

function parseJson(raw: string): ParseResult<unknown> {
  try {
    return { ok: true, value: JSON.parse(raw) };
  } catch {
    return { ok: false, error: t("evaluation.input.invalidJson") };
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatPercent(value: number) {
  return `${Math.round(value * 1000) / 10}%`;
}

function metricLabel(metric: EvaluationMetricName) {
  switch (metric) {
    case "precision_at_k":
      return t("evaluation.metric.precision");
    case "recall_at_k":
      return t("evaluation.metric.recall");
    case "mrr":
      return t("evaluation.metric.mrr");
    case "answer_keyword_hit_rate":
      return t("evaluation.metric.answerHit");
    case "groundedness_pass_rate":
      return t("evaluation.metric.groundedness");
  }
}

type ParseResult<T> = { ok: true; value: T } | { ok: false; error: string };
