import {
  Button,
  DataTable,
  type DataTableColumn,
  Disclosure,
  FormStatus,
  ProcessingIndicator,
  StatusBadge,
  TextareaField,
} from "@engchina/production-ready-ui";
import { ClipboardCheck } from "lucide-react";
import { useState } from "react";

import {
  formatMetricValue,
  metricLabel,
  suiteLabel,
} from "@/components/evaluation/evaluation-metrics";
import { ApiError } from "@/lib/api";
import {
  evaluationOutcome,
  parseAnswerEvaluation,
  type AnswerEvaluationView,
} from "@/lib/answer-diagnostics";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useEvaluateAnswerRecord } from "@/lib/queries";

type Metric = AnswerEvaluationView["metrics"][number];

const COVERAGE_VARIANT = { addressed: "success", partial: "warning", missing: "danger" } as const;

/**
 * 保存された回答を標準回答で評価する。評価の基準（standard / strict）の指標のうち 1 件の回答で
 * 測れるものを、その閾値で判定する（#680）。評価は LLM を複数回呼ぶので時間がかかる。
 */
export function AnswerRecordEvaluation({
  traceId,
  evaluation: initial,
}: {
  traceId: string;
  evaluation?: unknown;
}) {
  const evaluate = useEvaluateAnswerRecord();
  const [standardAnswer, setStandardAnswer] = useState(
    () => parseAnswerEvaluation(initial)?.standardAnswer ?? ""
  );
  const evaluation = parseAnswerEvaluation(evaluate.data?.evaluation ?? initial);
  const inputId = `standard-answer-${traceId}`;
  const canSubmit = standardAnswer.trim().length > 0 && !evaluate.isPending;

  return (
    <section
      className="space-y-3 rounded-md border border-border bg-surface p-3"
      aria-label={t("search.evaluation.title")}
    >
      <div>
        <h4 className="text-sm font-semibold text-fg">{t("search.evaluation.title")}</h4>
        <p className="mt-1 text-xs leading-relaxed text-fg-muted">
          {t("search.evaluation.description")}
        </p>
      </div>
      <TextareaField
        id={inputId}
        label={t("search.evaluation.standardAnswer")}
        required
        value={standardAnswer}
        onChange={(event) => setStandardAnswer(event.target.value)}
        rows={3}
        maxLength={20000}
        disabled={evaluate.isPending}
        placeholder={t("search.evaluation.standardAnswerPlaceholder")}
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          size="md"
          variant="secondary"
          icon={ClipboardCheck}
          loading={evaluate.isPending}
          disabled={!canSubmit}
          onClick={() => evaluate.mutate({ traceId, standardAnswer })}
        >
          {t("search.evaluation.run")}
        </Button>
        {evaluate.isError ? (
          <FormStatus
            tone="danger"
            message={
              evaluate.error instanceof ApiError
                ? evaluate.error.message
                : t("search.evaluation.error")
            }
          />
        ) : null}
      </div>
      {evaluate.isPending ? (
        // LLM を複数回呼ぶため数十秒かかる。スピナーはボタンの loading が担う（messaging.md §3.7）。
        <ProcessingIndicator
          active
          label={t("search.evaluation.running")}
          operationKey={`answer-evaluation-${traceId}`}
          placement="action"
          activityIcon="none"
          testId="answer-evaluation-processing"
        />
      ) : null}
      {evaluation ? <EvaluationResult evaluation={evaluation} /> : null}
    </section>
  );
}

function EvaluationResult({ evaluation }: { evaluation: AnswerEvaluationView }) {
  const outcome = evaluationOutcome(evaluation);
  const columns: DataTableColumn<Metric>[] = [
    {
      key: "metric",
      header: t("search.evaluation.metric"),
      rowHeader: true,
      render: (metric) => metricLabel(metric.name),
    },
    {
      key: "value",
      header: t("search.evaluation.value"),
      align: "right",
      className: "tnum whitespace-nowrap",
      render: (metric) => formatMetricValue(metric.value),
    },
    {
      key: "threshold",
      header: t("search.evaluation.threshold"),
      align: "right",
      className: "tnum whitespace-nowrap",
      render: (metric) => formatMetricValue(metric.threshold),
    },
    {
      key: "verdict",
      header: t("search.evaluation.verdict"),
      render: (metric) =>
        metric.reference ? (
          <StatusBadge variant="neutral" label={t("search.evaluation.metric.reference")} />
        ) : (
          <StatusBadge
            variant={metric.passed ? "success" : "danger"}
            label={t(
              metric.passed ? "search.evaluation.metric.passed" : "search.evaluation.metric.failed"
            )}
          />
        ),
    },
  ];
  return (
    <div className="space-y-3 border-t border-border pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge
          variant={outcome.variant}
          label={t(`search.evaluation.outcome.${outcome.labelKey}`)}
        />
        {evaluation.suite ? (
          <span className="text-sm font-semibold text-fg">
            {t("search.evaluation.suite", { suite: suiteLabel(evaluation.suite) })}
          </span>
        ) : null}
        {evaluation.evaluatedAt ? (
          <span className="text-xs text-fg-muted">
            {t("search.evaluation.evaluatedAt", { value: formatDateTime(evaluation.evaluatedAt) })}
          </span>
        ) : null}
      </div>
      {evaluation.legacy ? (
        <p className="text-xs leading-relaxed text-fg-muted">{t("search.evaluation.legacy")}</p>
      ) : evaluation.message ? (
        <p className="text-xs leading-relaxed text-fg-muted">{evaluation.message}</p>
      ) : null}
      {evaluation.metrics.length ? (
        <DataTable
          columns={columns}
          rows={evaluation.metrics}
          getRowKey={(metric) => metric.name}
          dense
        />
      ) : null}
      {evaluation.externalDataItems.length ? (
        <p className="text-xs leading-relaxed text-fg-muted">
          {t("search.evaluation.externalData", {
            items: evaluation.externalDataItems.join("、"),
          })}
        </p>
      ) : null}
      {evaluation.coverage.length ? (
        <Disclosure
          variant="plain"
          summary={t("search.evaluation.coverage", { count: evaluation.coverage.length })}
        >
          <ul className="space-y-2">
            {evaluation.coverage.map((item) => (
              <li key={item.index} className="space-y-1 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge
                    variant={
                      COVERAGE_VARIANT[item.status as keyof typeof COVERAGE_VARIANT] ?? "neutral"
                    }
                    label={t(`search.evaluation.coverage.${item.status as "addressed"}`)}
                  />
                  <span className="break-words text-fg">{item.requirement}</span>
                </div>
                {item.quote ? (
                  <p className="break-words text-fg-muted">「{item.quote}」</p>
                ) : null}
              </li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
      {evaluation.claims.length ? (
        <Disclosure
          variant="plain"
          summary={t("search.evaluation.claims", { count: evaluation.claims.length })}
        >
          <ul className="space-y-2">
            {evaluation.claims.map((claim, index) => (
              <li key={index} className="space-y-1 text-xs">
                <p className="font-medium text-fg">
                  {t(`search.evaluation.claim.${claim.status as "supported"}`)}
                </p>
                <p className="break-words text-fg">「{claim.quote}」</p>
                <p className="break-words text-fg-muted">{claim.reason}</p>
              </li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
    </div>
  );
}
