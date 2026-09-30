import { Disclosure, StatusBadge } from "@engchina/production-ready-ui";

import { confidenceVariant, parseAnswerDiagnostics } from "@/lib/answer-diagnostics";
import { t } from "@/lib/i18n";
import { AnswerRecordEvaluation } from "./AnswerRecordEvaluation";

/**
 * 回答の根拠の構成と実行記録(信頼度・人手確認・根拠木・工程)。
 * traceId を渡すと、保存した回答を標準回答で評価する欄も出す。
 */
export function AnswerDetailsPanel({
  diagnostics,
  traceId,
  evaluation,
  title = t("search.answerDetails.title"),
}: {
  diagnostics: unknown;
  traceId?: string | null;
  evaluation?: unknown;
  /** 回答を作らない RAG 検索では「検索の〜」にする（#649）。 */
  title?: string;
}) {
  const data = parseAnswerDiagnostics(diagnostics);
  if (!data) return null;
  const models = [
    data.models.llm
      ? {
          key: "llm",
          label: t("search.answerDetails.model.llm"),
          value: data.models.llm.label,
          title: data.models.llm.modelId,
        }
      : null,
    data.models.vision
      ? {
          key: "vision",
          label: t("search.answerDetails.model.vision"),
          value: data.models.vision.label,
          title: data.models.vision.modelId,
        }
      : null,
    data.models.embedding
      ? { key: "embedding", label: t("search.answerDetails.model.embedding"), value: data.models.embedding }
      : null,
    data.models.rerank
      ? { key: "rerank", label: t("search.answerDetails.model.rerank"), value: data.models.rerank }
      : null,
  ].flatMap((item) => (item ? [item] : []));
  return (
    <section
      className="space-y-3 border-t border-border pt-3"
      aria-label={title}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-fg">
          {title}
        </span>
        {data.confidence ? (
          <StatusBadge
            variant={confidenceVariant(data.confidence)}
            label={t("search.answerDetails.confidence", { value: data.confidence })}
          />
        ) : null}
        {data.needsHumanReview ? (
          <StatusBadge
            variant="warning"
            label={t("search.answerDetails.humanReview")}
          />
        ) : null}
      </div>
      {models.length ? (
        <div
          className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs"
          data-testid="answer-models"
        >
          <span className="font-medium text-fg-muted">{t("search.answerDetails.models")}</span>
          <dl className="contents">
            {models.map((model) => (
              <div key={model.key} className="flex min-w-0 items-center gap-1.5">
                <dt className="text-fg-muted">{model.label}</dt>
                <dd className="break-all font-medium text-fg" title={model.title}>
                  {model.value}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
      {data.rewrittenQuestion ? (
        <p className="break-words text-xs leading-relaxed text-fg-muted">
          {t("search.answerDetails.rewritten", { question: data.rewrittenQuestion })}
        </p>
      ) : null}
      {data.insufficientReason ? (
        <p className="text-xs leading-relaxed text-fg-muted">
          {t("search.answerDetails.insufficient", { reason: data.insufficientReason })}
        </p>
      ) : null}
      <Disclosure variant="plain" summary={`${t("search.answerDetails.evidence")}（${data.tree.length}）`}>
        <ol className="space-y-2">
          {data.tree.map((parent) => (
            <li
              key={parent.parentId}
              className="rounded-md border border-border bg-surface-sunken p-2"
            >
              <p className="break-words text-xs font-medium text-fg">
                {parent.source || parent.parentId}
                {parent.page != null
                  ? ` / ${t("flow.extraction.page", { page: parent.page })}`
                  : ""}
              </p>
              <ul className="mt-1 space-y-1">
                {parent.children.map((child) => (
                  <li
                    key={child.chunkId}
                    className="flex flex-wrap items-center gap-2 text-xs text-fg-muted"
                  >
                    <span className="break-all">{child.chunkId}</span>
                    <span>{roleLabel(child.role)}</span>
                    {child.modelUsed ? (
                      <StatusBadge
                        variant="info"
                        label={t("search.answerDetails.modelUsed")}
                      />
                    ) : null}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      </Disclosure>
      <Disclosure variant="plain" summary={`${t("search.answerDetails.steps")}（${data.steps.length}）`}>
        <ol className="space-y-1 text-xs text-fg-muted">
          {data.steps.map((step, index) => (
            <li key={`${step.name}-${index}`} className="flex flex-wrap gap-2">
              <span className="text-fg">{step.name}</span>
              <span>{step.status}</span>
              {step.elapsedSeconds != null ? (
                <span className="tnum">{step.elapsedSeconds.toFixed(1)}s</span>
              ) : null}
              {step.llmCalls ? (
                <span className="tnum">
                  {t("search.answerDetails.llmCalls", { count: step.llmCalls })}
                </span>
              ) : null}
            </li>
          ))}
        </ol>
        {data.generatedQueries.length ? (
          <p className="mt-2 break-words text-xs text-fg-muted">
            {t("search.answerDetails.queries")}: {data.generatedQueries.join(" / ")}
          </p>
        ) : null}
      </Disclosure>
      {traceId ? (
        <AnswerRecordEvaluation key={traceId} traceId={traceId} evaluation={evaluation} />
      ) : null}
    </section>
  );
}

function roleLabel(role: string): string {
  if (role === "retrieved_anchor") return t("search.answerDetails.role.anchor");
  if (role === "adjacent_child") return t("search.answerDetails.role.adjacent");
  return role;
}
