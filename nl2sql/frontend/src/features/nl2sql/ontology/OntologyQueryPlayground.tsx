import { lazy, Suspense, useMemo, useState } from "react";
import { MessageSquareText, RefreshCw, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { InputActionField } from "@/components/ui/input-action-field";
import { Banner, EmptyState } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { DbManagementLoadingSkeleton, DbObjectManagementPanelShell, DbObjectPanelHeader } from "../components/DbObjectManagementShared";
import { answerOntologyQuestion, type PlaygroundResult } from "./queryPlayground";
import type { OntologyGraph } from "./types";

const LazyOntologyGraphCanvas = lazy(() => import("./OntologyGraphCanvas"));

export interface OntologyQueryPlaygroundProps {
  graph: OntologyGraph | null;
  warningsJa?: string[];
  onRefreshSchema?: () => void | Promise<void>;
  refreshingSchema?: boolean;
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
export function OntologyQueryPlayground({
  graph,
  warningsJa = [],
  onRefreshSchema,
  refreshingSchema = false,
}: OntologyQueryPlaygroundProps) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<PlaygroundResult | null>(null);

  const hasGraph = Boolean(graph && graph.nodes.length > 0);
  const graphStats = graph
    ? t("ontologyPlayground.graphStats", {
        nodes: graph.nodes.length,
        edges: graph.edges.length,
      })
    : "";
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
      {warningsJa.length > 0 ? (
        <div data-testid="profile-ontology-unresolved">
          <Banner
            severity="warning"
            title={t("profiles.ontology.unresolvedTitle")}
            action={
              onRefreshSchema ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  loading={refreshingSchema}
                  disabled={refreshingSchema}
                  onClick={() => void onRefreshSchema()}
                >
                  <RefreshCw size={15} aria-hidden="true" />
                  <span>
                    {refreshingSchema
                      ? t("profiles.schemaRefresh.status.running")
                      : t("profiles.schemaRefresh.action")}
                  </span>
                </Button>
              ) : undefined
            }
          >
            <ul className="grid gap-1 pl-4">
              {warningsJa.map((warning) => (
                <li key={warning} className="list-disc break-words">
                  {warning}
                </li>
              ))}
            </ul>
          </Banner>
        </div>
      ) : null}
      {!hasGraph ? (
        <EmptyState
          title={t("ontologyPlayground.emptyTitle")}
          hint={t("ontologyPlayground.emptyHint")}
          action={
            <p className="text-xs font-medium leading-5 text-muted">
              {t("ontologyPlayground.emptyFlow")}
            </p>
          }
        />
      ) : (
        <div className="grid gap-3">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              runQuestion();
            }}
          >
            <InputActionField
              id="ontology-playground-question"
              label={t("ontologyPlayground.questionLabel")}
              value={question}
              onChange={setQuestion}
              placeholder={t("ontologyPlayground.questionPlaceholder")}
              inputTestId="ontology-playground-question"
              action={{
                type: "submit",
                variant: "primary",
                label: t("ontologyPlayground.run"),
                icon: <Search size={15} aria-hidden="true" />,
                disabled: !question.trim(),
                dataTestId: "ontology-playground-run",
              }}
            />
          </form>
          {!result ? (
            <div
              className="rounded-md border border-border bg-muted/20 px-3 py-2"
              data-testid="ontology-playground-ready-state"
            >
              <p className="text-sm leading-6 text-foreground">
                {t("ontologyPlayground.readyHint")}
              </p>
              <p className="text-xs leading-5 text-muted">{graphStats}</p>
            </div>
          ) : null}
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
