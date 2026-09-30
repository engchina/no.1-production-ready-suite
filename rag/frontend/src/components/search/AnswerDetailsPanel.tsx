import { Disclosure, StatusBadge } from "@engchina/production-ready-ui";

import { confidenceVariant, parseDocragDiagnostics } from "@/lib/docrag-answer";
import { t } from "@/lib/i18n";
import { DocragAnswerEvaluation } from "./DocragAnswerEvaluation";

/**
 * 回答の根拠の構成と実行記録(信頼度・人手確認・根拠木・工程)。
 * traceId を渡すと、保存した回答を標準回答で評価する欄も出す。
 */
export function DocragAnswerPanel({
  docrag,
  traceId,
  evaluation,
}: {
  docrag: unknown;
  traceId?: string | null;
  evaluation?: unknown;
}) {
  const data = parseDocragDiagnostics(docrag);
  if (!data) return null;
  return (
    <section
      className="space-y-3 border-t border-border pt-3"
      aria-label={t("search.docrag.title")}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-fg">
          {t("search.docrag.title")}
        </span>
        {data.confidence ? (
          <StatusBadge
            variant={confidenceVariant(data.confidence)}
            label={t("search.docrag.confidence", { value: data.confidence })}
          />
        ) : null}
        {data.needsHumanReview ? (
          <StatusBadge
            variant="warning"
            label={t("search.docrag.humanReview")}
          />
        ) : null}
      </div>
      {data.rewrittenQuestion ? (
        <p className="break-words text-xs leading-relaxed text-fg-muted">
          {t("search.docrag.rewritten", { question: data.rewrittenQuestion })}
        </p>
      ) : null}
      {data.insufficientReason ? (
        <p className="text-xs leading-relaxed text-fg-muted">
          {t("search.docrag.insufficient", { reason: data.insufficientReason })}
        </p>
      ) : null}
      <Disclosure variant="plain" summary={`${t("search.docrag.evidence")}（${data.tree.length}）`}>
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
                        label={t("search.docrag.modelUsed")}
                      />
                    ) : null}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      </Disclosure>
      <Disclosure variant="plain" summary={`${t("search.docrag.steps")}（${data.steps.length}）`}>
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
                  {t("search.docrag.llmCalls", { count: step.llmCalls })}
                </span>
              ) : null}
            </li>
          ))}
        </ol>
        {data.generatedQueries.length ? (
          <p className="mt-2 break-words text-xs text-fg-muted">
            {t("search.docrag.queries")}: {data.generatedQueries.join(" / ")}
          </p>
        ) : null}
      </Disclosure>
      {traceId ? (
        <DocragAnswerEvaluation key={traceId} traceId={traceId} evaluation={evaluation} />
      ) : null}
    </section>
  );
}

function roleLabel(role: string): string {
  if (role === "retrieved_anchor") return t("search.docrag.role.anchor");
  if (role === "adjacent_child") return t("search.docrag.role.adjacent");
  return role;
}
