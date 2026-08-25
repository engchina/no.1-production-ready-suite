import { lazy, Suspense, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Info,
  MessageSquareText,
  Network,
  RefreshCw,
  Route,
  Search,
  Table2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Banner, EmptyState, StatusBadge } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import {
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_SCROLL_CLASS,
} from "@/lib/list-density";
import { DbManagementLoadingSkeleton, DbObjectManagementPanelShell, DbObjectPanelHeader } from "../components/DbObjectManagementShared";
import {
  deriveOntologyErDetails,
  type OntologyErDetails,
  type OntologyErKeyRole,
} from "./erDetails";
import { ontologyNodeDisplay } from "./nodeDisplay";
import { answerOntologyQuestion, type PlaygroundResult } from "./queryPlayground";
import {
  ontologyRelationshipRows,
  type OntologyGraph,
  type OntologyNode,
  type OntologyRelationshipRow,
  type OntologyValidationStatus,
} from "./types";
import { isOntologyDetailNodeKind, type OntologyGraphViewMode } from "./graphView";

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

function erKeyRoleLabel(role: OntologyErKeyRole): string {
  switch (role) {
    case "pk":
      return t("ontologyPlayground.erKeyRolePk");
    case "fk":
      return t("ontologyPlayground.erKeyRoleFk");
    case "pk_fk":
      return t("ontologyPlayground.erKeyRolePkFk");
    case "none":
    default:
      return t("ontologyPlayground.erKeyRoleNone");
  }
}

function erObjectTypeLabel(type: OntologyErDetails["objectType"]): string {
  if (type === "table") return t("nl2sql.ontology.nodeKind.table");
  if (type === "view") return t("nl2sql.ontology.nodeKind.view");
  return t("nl2sql.ontology.nodeKind.unknown");
}

function validationVariant(status: OntologyValidationStatus) {
  if (status === "passed") return "success" as const;
  if (status === "warning") return "warning" as const;
  if (status === "blocked") return "danger" as const;
  return "neutral" as const;
}

function validationLabel(status: OntologyValidationStatus): string {
  if (status === "passed") return t("nl2sql.ontology.nodeValidation.passed");
  if (status === "warning") return t("nl2sql.ontology.nodeValidation.warning");
  if (status === "blocked") return t("nl2sql.ontology.nodeValidation.blocked");
  return t("nl2sql.ontology.nodeValidation.unreviewed");
}

function stageLabel(result: PlaygroundResult): string {
  return t(STAGE_LABEL_KEYS[result.stage]);
}

function compactRelationshipRows(
  graph: OntologyGraph,
  result: PlaygroundResult | null
): OntologyRelationshipRow[] {
  const rows = ontologyRelationshipRows(graph);
  if (!result) return rows;
  const highlightNodes = new Set(result.highlightNodeIds);
  const highlightEdges = new Set(result.highlightEdgeIds);
  const focused = rows.filter(
    (row) =>
      highlightEdges.has(row.edge_id) ||
      (highlightNodes.has(row.source_node_id) && highlightNodes.has(row.target_node_id))
  );
  return focused.length > 0 ? focused : rows;
}

function RelationshipCard({
  row,
  selected,
  onSelect,
}: {
  row: OntologyRelationshipRow;
  selected: boolean;
  onSelect?: (edgeId: string) => void;
}) {
  return (
    <button
      type="button"
      className={`grid w-full cursor-pointer gap-2 rounded-md border bg-background px-3 py-2 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 motion-reduce:transition-none ${
        selected ? "border-primary ring-2 ring-primary/20" : "border-border hover:border-primary/50"
      }`}
      onClick={() => onSelect?.(row.edge_id)}
      data-testid={`ontology-inspector-relationship-${row.edge_id}`}
    >
      <span className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 break-words text-sm font-semibold text-foreground">
          {row.relationship_label}
        </span>
        <StatusBadge variant={validationVariant(row.validation_status)} label={validationLabel(row.validation_status)} />
      </span>
      <span className="text-xs leading-5 text-muted">
        {row.source_label} → {row.target_label}
      </span>
      <code className="break-all rounded bg-card px-2 py-1 font-mono text-xs leading-5 text-foreground">
        {row.join_condition}
      </code>
    </button>
  );
}

function OntologyGroundingPathPanel({
  graph,
  result,
  rows,
}: {
  graph: OntologyGraph;
  result: PlaygroundResult | null;
  rows: OntologyRelationshipRow[];
}) {
  const nodeById = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph.nodes]);
  const highlightedNodes = useMemo(
    () => (result?.highlightNodeIds ?? []).map((nodeId) => nodeById.get(nodeId)).filter(Boolean) as OntologyNode[],
    [nodeById, result]
  );
  return (
    <section className="grid gap-3 rounded-md border border-border bg-card p-3" data-testid="ontology-grounding-path-panel">
      <div className="flex items-center gap-2">
        <Route size={16} className="text-primary" aria-hidden="true" />
        <h3 className="text-sm font-semibold text-foreground">
          {t("ontologyPlayground.inspector.groundingPath")}
        </h3>
      </div>
      {!result ? (
        <p className="text-sm leading-6 text-muted">{t("ontologyPlayground.inspector.groundingEmpty")}</p>
      ) : highlightedNodes.length === 0 && rows.length === 0 ? (
        <Banner severity="info">{t("ontologyPlayground.inspector.groundingNoMatch")}</Banner>
      ) : (
        <div className="grid gap-3">
          <div className="flex flex-wrap gap-2">
            <StatusBadge variant="info" label={stageLabel(result)} />
            <StatusBadge
              variant="neutral"
              label={t("ontologyPlayground.inspector.groundingCount", {
                nodes: highlightedNodes.length,
                edges: rows.length,
              })}
            />
          </div>
          {highlightedNodes.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {highlightedNodes.map((node) => {
                const display = ontologyNodeDisplay(node);
                return (
                  <span
                    key={node.id}
                    className="rounded-md border border-border bg-background px-2 py-1 text-xs leading-5 text-foreground"
                    title={display.ariaLabel}
                  >
                    {display.kindLabel}: {display.primaryLabel}
                  </span>
                );
              })}
            </div>
          ) : null}
          {rows.length > 0 ? (
            <div className="grid gap-2">
              {rows.slice(0, 5).map((row) => (
                <div key={row.edge_id} className="rounded-md border border-border bg-background px-3 py-2">
                  <p className="text-sm font-semibold text-foreground">{row.relationship_label}</p>
                  <p className="mt-1 text-xs leading-5 text-muted">
                    {row.source_label} → {row.target_label}
                  </p>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

function OntologyNodeDetailsPanel({
  graph,
  node,
  onSelectNode,
}: {
  graph: OntologyGraph;
  node: OntologyNode | null;
  onSelectNode: (nodeId: string) => void;
}) {
  const selectableNodes = useMemo(
    () => graph.nodes.filter((item) => !isOntologyDetailNodeKind(item.kind)).slice(0, 12),
    [graph.nodes]
  );
  return (
    <section className="grid gap-3 rounded-md border border-border bg-card p-3" data-testid="ontology-node-details-panel">
      <div className="flex items-center gap-2">
        <Info size={16} className="text-primary" aria-hidden="true" />
        <h3 className="text-sm font-semibold text-foreground">
          {t("ontologyPlayground.inspector.nodeDetails")}
        </h3>
      </div>
      {!node ? (
        <div className="grid gap-3">
          <p className="text-sm leading-6 text-muted">{t("ontologyPlayground.inspector.nodeEmpty")}</p>
          <div className="grid gap-2" data-testid="ontology-inspector-node-picker">
            <p className="text-xs font-semibold text-muted">
              {t("ontologyPlayground.inspector.nodePicker")}
            </p>
            <div className="grid gap-1.5">
              {selectableNodes.map((item) => {
                const display = ontologyNodeDisplay(item);
                return (
                  <button
                    key={item.id}
                    type="button"
                    className="grid cursor-pointer gap-0.5 rounded-md border border-border bg-background px-3 py-2 text-left outline-none transition-colors hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40 motion-reduce:transition-none"
                    onClick={() => onSelectNode(item.id)}
                    data-testid={`ontology-inspector-node-${item.id}`}
                  >
                    <span className="text-xs font-semibold text-muted">{display.kindLabel}</span>
                    <span className="break-words text-sm font-medium text-foreground">
                      {display.primaryLabel}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      ) : (
        <div className="grid gap-2">
          <div className="flex flex-wrap gap-2">
            <StatusBadge variant="info" label={ontologyNodeDisplay(node).kindLabel} />
            <StatusBadge
              variant={validationVariant(node.validation_status ?? "unreviewed")}
              label={validationLabel(node.validation_status ?? "unreviewed")}
            />
          </div>
          <p className="break-words text-sm font-semibold text-foreground">{node.business_name_ja}</p>
          {ontologyNodeDisplay(node).secondaryLabel ? (
            <p className="break-all font-mono text-xs leading-5 text-muted">
              {ontologyNodeDisplay(node).secondaryLabel}
            </p>
          ) : null}
          {node.description_ja || node.description ? (
            <p className="text-sm leading-6 text-muted">{node.description_ja || node.description}</p>
          ) : null}
          {node.aliases?.length ? (
            <p className="text-xs leading-5 text-muted">
              {t("ontologyPlayground.inspector.aliases")}: {node.aliases.join("、")}
            </p>
          ) : null}
        </div>
      )}
    </section>
  );
}

function OntologyRelationshipListPanel({
  rows,
  selectedEdgeId,
  onSelectEdge,
}: {
  rows: OntologyRelationshipRow[];
  selectedEdgeId: string | null;
  onSelectEdge: (edgeId: string) => void;
}) {
  return (
    <section className="grid gap-3 rounded-md border border-border bg-card p-3" data-testid="ontology-inspector-relationships">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Network size={16} className="text-primary" aria-hidden="true" />
          {t("ontologyPlayground.inspector.relationships")}
        </h3>
        <StatusBadge variant="neutral" label={t("ontologyPlayground.inspector.relationshipCount", { count: rows.length })} />
      </div>
      {rows.length === 0 ? (
        <Banner severity="info">{t("ontologyPlayground.inspector.relationshipsEmpty")}</Banner>
      ) : (
        <div className="grid max-h-80 gap-2 overflow-y-auto pr-1" data-testid="ontology-inspector-relationship-list">
          {rows.map((row) => (
            <RelationshipCard
              key={row.edge_id}
              row={row}
              selected={row.edge_id === selectedEdgeId}
              onSelect={onSelectEdge}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function OntologyErDetailsPanel({ details }: { details: OntologyErDetails }) {
  return (
    <section
      className="grid gap-4 rounded-md border border-border bg-card p-3"
      aria-labelledby="ontology-er-details-title"
      data-testid="ontology-er-details-panel"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3
            id="ontology-er-details-title"
            className="flex items-center gap-2 text-sm font-semibold text-foreground"
          >
            <Table2 size={16} className="text-primary" aria-hidden="true" />
            {t("ontologyPlayground.erDetailsTitle")}
          </h3>
          <p
            className="mt-1 break-all font-mono text-xs leading-5 text-muted"
            data-testid="ontology-er-detail-object-name"
          >
            {details.objectName}
          </p>
        </div>
        <div className="flex flex-wrap gap-2" aria-label={t("ontologyPlayground.erSummary")}>
          <StatusBadge variant="neutral" label={erObjectTypeLabel(details.objectType)} />
          <StatusBadge
            variant="info"
            label={t("ontologyPlayground.erColumnCount", { count: details.columns.length })}
          />
        </div>
      </div>

      {details.columns.length > 0 ? (
        <div
          className={`rounded-md border border-border ${INFORMATION_TABLE_SCROLL_CLASS}`}
          data-testid="ontology-er-columns"
        >
          <table className="min-w-[48rem] w-full table-fixed border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-background">
              <tr className="h-10">
                <th
                  scope="col"
                  className="w-[24%] px-3 py-2 text-left text-xs font-semibold text-foreground"
                >
                  {t("ontologyPlayground.erColumnName")}
                </th>
                <th
                  scope="col"
                  className="w-[16%] px-3 py-2 text-left text-xs font-semibold text-foreground"
                >
                  {t("ontologyPlayground.erDataType")}
                </th>
                <th
                  scope="col"
                  className="w-[12%] px-3 py-2 text-left text-xs font-semibold text-foreground"
                >
                  {t("ontologyPlayground.erKeyRole")}
                </th>
                <th
                  scope="col"
                  className="w-[22%] px-3 py-2 text-left text-xs font-semibold text-foreground"
                >
                  {t("ontologyPlayground.erBusinessName")}
                </th>
                <th
                  scope="col"
                  className="w-[26%] px-3 py-2 text-left text-xs font-semibold text-foreground"
                >
                  {t("ontologyPlayground.erDescription")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border bg-card">
              {details.columns.map((column) => (
                <tr
                  key={column.id}
                  className={INFORMATION_TABLE_ROW_CLASS}
                  data-testid={`ontology-er-column-${column.id}`}
                >
                  <td className="break-all px-3 py-3 font-mono text-xs leading-5 text-foreground">
                    {column.columnName}
                  </td>
                  <td className="break-all px-3 py-3 font-mono text-xs leading-5 text-muted">
                    {column.dataType}
                  </td>
                  <td className="px-3 py-3 text-foreground">{erKeyRoleLabel(column.keyRole)}</td>
                  <td className="break-words px-3 py-3 text-foreground">{column.businessNameJa}</td>
                  <td className="break-words px-3 py-3 text-muted">{column.descriptionJa || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Banner severity="info">{t("ontologyPlayground.erColumnsEmpty")}</Banner>
      )}

      <div className="grid gap-2" data-testid="ontology-er-joins">
        <h4 className="text-xs font-semibold text-muted">
          {t("ontologyPlayground.erJoinConditions")}
        </h4>
        {details.joins.length > 0 ? (
          <div className="grid gap-2">
            {details.joins.map((join) => (
              <div
                key={join.id}
                className="grid gap-1 rounded-md border border-border bg-background px-3 py-2"
                data-testid={`ontology-er-join-${join.id}`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-foreground">
                    {join.relationshipNameJa}
                  </span>
                  <span className="text-xs text-muted">{join.cardinality}</span>
                </div>
                <p className="text-xs leading-5 text-muted">
                  {join.sourceLabel} → {join.targetLabel}
                </p>
                <code className="break-all rounded bg-muted/30 px-2 py-1 font-mono text-xs leading-5 text-foreground">
                  {join.joinCondition}
                </code>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm leading-6 text-muted">{t("ontologyPlayground.erJoinsEmpty")}</p>
        )}
      </div>
    </section>
  );
}

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
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [graphViewMode, setGraphViewMode] = useState<OntologyGraphViewMode>("all");
  const [mobileGraphOpen, setMobileGraphOpen] = useState(false);

  const hasGraph = Boolean(graph && graph.nodes.length > 0);
  const graphStats = graph
    ? t("ontologyPlayground.graphStats", {
        nodes: graph.nodes.length,
        edges: graph.edges.length,
      })
    : "";
  const graphRevisionId = graph?.revision?.id ?? graph?.revision_id ?? "";

  const resetGroundingState = ({ clearQuestion = false }: { clearQuestion?: boolean } = {}) => {
    if (clearQuestion) setQuestion("");
    setResult(null);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setGraphViewMode("all");
  };

  const handleQuestionChange = (value: string) => {
    setQuestion(value);
    if (!value.trim()) resetGroundingState();
  };

  const runQuestion = () => {
    const normalizedQuestion = question.trim();
    if (!graph || !normalizedQuestion) {
      resetGroundingState();
      return;
    }
    const nextResult = answerOntologyQuestion(graph, normalizedQuestion);
    setResult(nextResult);
    setGraphViewMode("grounding");
    setSelectedNodeId(nextResult.highlightNodeIds[0] ?? null);
    setSelectedEdgeId(nextResult.highlightEdgeIds[0] ?? null);
  };
  const hasResettableGroundingState = Boolean(
    question.length > 0 || result || selectedNodeId || selectedEdgeId || graphViewMode !== "all"
  );

  const highlightNodeIds = useMemo(() => result?.highlightNodeIds ?? [], [result]);
  const highlightEdgeIds = useMemo(() => result?.highlightEdgeIds ?? [], [result]);
  const selectedNode = useMemo(
    () => graph?.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [graph, selectedNodeId]
  );
  const focusedRelationshipRows = useMemo(
    () => (graph ? compactRelationshipRows(graph, result) : []),
    [graph, result]
  );
  const erDetails = useMemo(
    () => (graph ? deriveOntologyErDetails(graph, selectedNodeId) : null),
    [graph, selectedNodeId]
  );

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
          <div className="flex flex-wrap items-center gap-2" data-testid="ontology-playground-graph-summary">
            <StatusBadge variant="neutral" label={graphStats} />
            {graphRevisionId ? (
              <span data-testid="ontology-playground-revision-id">
                <StatusBadge
                  variant="neutral"
                  label={t("ontologyPlayground.graphRevision", { revision: graphRevisionId })}
                />
              </span>
            ) : null}
          </div>
          <form
            className="space-y-1.5"
            onSubmit={(event) => {
              event.preventDefault();
              runQuestion();
            }}
          >
            <label
              htmlFor="ontology-playground-question"
              className="text-sm font-medium text-foreground"
            >
              {t("ontologyPlayground.questionLabel")}
            </label>
            <div
              className={`grid min-w-0 gap-2 ${
                hasResettableGroundingState
                  ? "sm:grid-cols-[minmax(0,1fr)_auto_auto]"
                  : "sm:grid-cols-[minmax(0,1fr)_auto]"
              }`}
            >
              <input
                id="ontology-playground-question"
                type="text"
                value={question}
                onChange={(event) => handleQuestionChange(event.currentTarget.value)}
                placeholder={t("ontologyPlayground.questionPlaceholder")}
                data-testid="ontology-playground-question"
                className="h-11 min-h-[44px] w-full min-w-0 rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
              />
              <Button
                type="submit"
                variant="primary"
                size="lg"
                className="h-11 min-h-[44px] w-full whitespace-nowrap sm:w-auto"
                disabled={!question.trim()}
                data-testid="ontology-playground-run"
              >
                <Search size={15} aria-hidden="true" />
                <span>{t("ontologyPlayground.run")}</span>
              </Button>
              {hasResettableGroundingState ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="lg"
                  className="h-11 min-h-[44px] w-full whitespace-nowrap sm:w-auto"
                  aria-label={t("ontologyPlayground.clearAriaLabel")}
                  onClick={() => resetGroundingState({ clearQuestion: true })}
                  data-testid="ontology-playground-clear"
                >
                  <X size={15} aria-hidden="true" />
                  <span>{t("ontologyPlayground.clear")}</span>
                </Button>
              ) : null}
            </div>
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
            <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(22rem,26rem)] xl:items-start">
              <section className="order-2 grid gap-2 xl:order-1" aria-label={t("ontologyPlayground.graphSection")}>
                <div className="flex items-center justify-between gap-2 xl:hidden">
                  <h3 className="text-sm font-semibold text-foreground">
                    {t("ontologyPlayground.graphSection")}
                  </h3>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => setMobileGraphOpen((current) => !current)}
                    aria-expanded={mobileGraphOpen}
                    aria-controls="ontology-playground-graph-region"
                  >
                    {mobileGraphOpen ? (
                      <ChevronUp size={15} aria-hidden="true" />
                    ) : (
                      <ChevronDown size={15} aria-hidden="true" />
                    )}
                    <span>
                      {mobileGraphOpen
                        ? t("ontologyPlayground.graphCollapse")
                        : t("ontologyPlayground.graphExpand")}
                    </span>
                  </Button>
                </div>
                <div
                  id="ontology-playground-graph-region"
                  className={mobileGraphOpen ? "block" : "hidden xl:block"}
                  data-testid="ontology-playground-graph-region"
                >
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
                      selectedNodeId={selectedNodeId}
                      selectedEdgeId={selectedEdgeId}
                      onSelectNode={setSelectedNodeId}
                      onSelectEdge={setSelectedEdgeId}
                      highlightNodeIds={highlightNodeIds}
                      highlightEdgeIds={highlightEdgeIds}
                      viewMode={graphViewMode}
                      onViewModeChange={setGraphViewMode}
                    />
                  </Suspense>
                </div>
              </section>
              <aside
                className="order-1 grid gap-3 xl:order-2"
                aria-label={t("ontologyPlayground.inspector.title")}
                data-testid="ontology-playground-inspector"
              >
                <OntologyGroundingPathPanel graph={graph} result={result} rows={focusedRelationshipRows} />
                <OntologyNodeDetailsPanel
                  graph={graph}
                  node={selectedNode}
                  onSelectNode={setSelectedNodeId}
                />
                {erDetails ? <OntologyErDetailsPanel details={erDetails} /> : null}
                <OntologyRelationshipListPanel
                  rows={focusedRelationshipRows}
                  selectedEdgeId={selectedEdgeId}
                  onSelectEdge={setSelectedEdgeId}
                />
              </aside>
            </div>
          ) : null}
        </div>
      )}
    </DbObjectManagementPanelShell>
  );
}
