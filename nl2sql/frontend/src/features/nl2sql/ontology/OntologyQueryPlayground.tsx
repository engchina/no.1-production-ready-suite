import { lazy, Suspense, useMemo, useState } from "react";
import { MessageSquareText } from "lucide-react";

import { Button, EmptyState } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import {
  DbManagementLoadingSkeleton,
  DbObjectManagementPanelShell,
  DbObjectPanelHeader,
} from "../components/DbObjectManagementShared";
import { answerOntologyQuestion, type PlaygroundResult } from "./queryPlayground";
import type { OntologyGraph } from "./types";

const LazyOntologyGraphCanvas = lazy(() => import("./OntologyGraphCanvas"));

export interface OntologyQueryPlaygroundProps {
  graph: OntologyGraph | null;
}

const STAGE_LABEL_KEYS = {
  entity_definition: "ontologyPlayground.stage.entityDefinition",
  list_all: "ontologyPlayground.stage.listAll",
  relationship: "ontologyPlayground.stage.relationship",
  property: "ontologyPlayground.stage.property",
  no_match: "ontologyPlayground.stage.noMatch",
} as const;

/**
 * 決定論 NL Query Playground(LLM 不要)。質問がオントロジーの
 * どのエンティティ/関係に接地するかをグラフ上でハイライトする。
 */
export function OntologyQueryPlayground({ graph }: OntologyQueryPlaygroundProps) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<PlaygroundResult | null>(null);

  const hasGraph = Boolean(graph && graph.nodes.length > 0);
  const runQuestion = () => {
    if (!graph) return;
    setResult(answerOntologyQuestion(graph, question));
  };

  const highlightNodeIds = useMemo(() => result?.highlightNodeIds ?? [], [result]);
  const highlightEdgeIds = useMemo(() => result?.highlightEdgeIds ?? [], [result]);

  return (
    <DbObjectManagementPanelShell
      id="ontology-query-playground-panel"
      role="region"
      ariaLabel={t("ontologyPlayground.title")}
      idPrefix="ontology-query-playground"
    >
      <DbObjectPanelHeader
        icon={MessageSquareText}
        title={t("ontologyPlayground.title")}
        description={t("ontologyPlayground.description")}
      />
      {!hasGraph ? (
        <EmptyState
          title={t("ontologyPlayground.emptyTitle")}
          hint={t("ontologyPlayground.emptyHint")}
        />
      ) : (
        <div className="grid gap-3">
          <form
            className="grid items-end gap-3 sm:grid-cols-[minmax(0,1fr)_auto]"
            onSubmit={(event) => {
              event.preventDefault();
              runQuestion();
            }}
          >
            <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
              <span>{t("ontologyPlayground.questionLabel")}</span>
              <input
                type="text"
                value={question}
                onChange={(event) => setQuestion(event.currentTarget.value)}
                placeholder={t("ontologyPlayground.questionPlaceholder")}
                className="min-h-11 min-w-0 rounded-md border border-border bg-card px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
                data-testid="ontology-playground-question"
              />
            </label>
            <Button
              type="submit"
              variant="primary"
              size="md"
              disabled={!question.trim()}
              data-testid="ontology-playground-run"
            >
              {t("ontologyPlayground.run")}
            </Button>
          </form>
          {result ? (
            <div
              className="grid gap-1 rounded-md border border-border bg-card p-3"
              aria-live="polite"
              data-testid="ontology-playground-result"
            >
              <p className="text-xs font-semibold uppercase tracking-wide text-muted">
                {t(STAGE_LABEL_KEYS[result.stage])}
              </p>
              <p className="text-sm leading-6 text-foreground">{result.explanationJa}</p>
              {result.suggestionsJa.length > 0 ? (
                <p className="text-sm text-muted">
                  {t("ontologyPlayground.suggestions")}: {result.suggestionsJa.join("、")}
                </p>
              ) : null}
            </div>
          ) : null}
          {graph ? (
            <Suspense
              fallback={
                <DbManagementLoadingSkeleton
                  idPrefix="ontology-query-playground-graph"
                  ariaLabel={t("nl2sql.ontology.loading")}
                  variant="compact"
                />
              }
            >
              <LazyOntologyGraphCanvas
                graph={graph}
                highlightNodeIds={highlightNodeIds}
                highlightEdgeIds={highlightEdgeIds}
              />
            </Suspense>
          ) : null}
        </div>
      )}
    </DbObjectManagementPanelShell>
  );
}
