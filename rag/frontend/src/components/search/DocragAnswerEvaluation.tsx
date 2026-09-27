import {
  Button,
  DataTable,
  type DataTableColumn,
  FormStatus,
  StatusBadge,
} from "@engchina/production-ready-ui";
import { ClipboardCheck } from "lucide-react";
import { useState } from "react";

import { ApiError } from "@/lib/api";
import {
  evaluationOutcome,
  parseAnswerEvaluation,
  type AnswerEvaluationView,
} from "@/lib/docrag-answer";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useEvaluateDocragAnswer } from "@/lib/queries";

type Axis = AnswerEvaluationView["axes"][number];

const COVERAGE_VARIANT = { addressed: "success", partial: "warning", missing: "danger" } as const;

/**
 * 保存した DocRAG 回答を標準回答で評価する（rag_poc の「LLM による回答評価」、4 軸・20 点満点）。
 * rag_poc と違い、生成の後に標準回答を入れて評価する。評価は LLM を複数回呼ぶので時間がかかる。
 */
export function DocragAnswerEvaluation({
  traceId,
  evaluation: initial,
}: {
  traceId: string;
  evaluation?: unknown;
}) {
  const evaluate = useEvaluateDocragAnswer();
  const [standardAnswer, setStandardAnswer] = useState(
    () => parseAnswerEvaluation(initial)?.standardAnswer ?? ""
  );
  const evaluation = parseAnswerEvaluation(evaluate.data?.evaluation ?? initial);
  const inputId = `docrag-standard-answer-${traceId}`;
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
      <div>
        <label htmlFor={inputId} className="text-sm font-medium text-fg">
          {t("search.evaluation.standardAnswer")}
        </label>
        <textarea
          id={inputId}
          value={standardAnswer}
          onChange={(event) => setStandardAnswer(event.target.value)}
          rows={3}
          maxLength={20000}
          disabled={evaluate.isPending}
          placeholder={t("search.evaluation.standardAnswerPlaceholder")}
          className="mt-1 w-full rounded-md border border-border-control bg-surface-sunken px-3 py-2 text-sm outline-none focus-visible:border-focus-ring disabled:cursor-not-allowed disabled:opacity-50"
        />
      </div>
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
      {evaluation ? <EvaluationResult evaluation={evaluation} /> : null}
    </section>
  );
}

function EvaluationResult({ evaluation }: { evaluation: AnswerEvaluationView }) {
  const outcome = evaluationOutcome(evaluation);
  const columns: DataTableColumn<Axis>[] = [
    {
      key: "axis",
      header: t("search.evaluation.axis"),
      rowHeader: true,
      render: (axis) => t(`search.evaluation.axis.${axis.key as "accuracy"}`),
    },
    {
      key: "score",
      header: t("search.evaluation.score"),
      align: "right",
      className: "tnum whitespace-nowrap",
      render: (axis) => (axis.score == null ? "—" : `${axis.score} / 5`),
    },
    {
      key: "reason",
      header: t("search.evaluation.reason"),
      render: (axis) => <span className="whitespace-pre-wrap break-words">{axis.reason}</span>,
    },
  ];
  return (
    <div className="space-y-3 border-t border-border pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge
          variant={outcome.variant}
          label={t(`search.evaluation.outcome.${outcome.labelKey}`)}
        />
        {evaluation.totalScore != null ? (
          <span className="tnum text-sm font-semibold text-fg">
            {t("search.evaluation.total", {
              score: evaluation.totalScore,
              max: evaluation.maxScore,
              threshold: evaluation.passThreshold,
            })}
          </span>
        ) : null}
        {evaluation.evaluatedAt ? (
          <span className="text-xs text-fg-muted">
            {t("search.evaluation.evaluatedAt", { value: formatDateTime(evaluation.evaluatedAt) })}
          </span>
        ) : null}
      </div>
      {evaluation.message ? (
        <p className="text-xs leading-relaxed text-fg-muted">{evaluation.message}</p>
      ) : null}
      {evaluation.axes.length ? (
        <DataTable
          columns={columns}
          rows={evaluation.axes}
          getRowKey={(axis) => axis.key}
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
        <details>
          <summary className="cursor-pointer text-sm font-medium text-fg">
            {t("search.evaluation.coverage", { count: evaluation.coverage.length })}
          </summary>
          <ul className="mt-2 space-y-2">
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
        </details>
      ) : null}
      {evaluation.claims.length ? (
        <details>
          <summary className="cursor-pointer text-sm font-medium text-fg">
            {t("search.evaluation.claims", { count: evaluation.claims.length })}
          </summary>
          <ul className="mt-2 space-y-2">
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
        </details>
      ) : null}
    </div>
  );
}
