import { lazy, Suspense, useMemo, useRef, useState } from "react";
import {
  Info,
  MessageSquareText,
  Network,
  RefreshCw,
  Route,
  Search,
  ServerCog,
  Table2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ClearActionButton } from "@/components/ui/clear-action-button";
import { DisclosureChevron } from "@/components/ui/disclosure-chevron";
import { Banner, EmptyState } from "@engchina/production-ready-ui";

import { StatusBadge } from "@/components/ui/status-badge";

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
import { searchOntologyContext } from "./api";
import {
  answerOntologyQuestion,
  browseRelationshipRows,
  groundedRelationshipRows,
  type PlaygroundResult,
} from "./queryPlayground";
import { normalizeGroundingText } from "./groundingMatcher";
import {
  type OntologyContextSearchResult,
  type OntologyGraph,
  type OntologyNode,
  type OntologyRelationshipRow,
  type OntologyValidationStatus,
} from "./types";
import { isOntologyDetailNodeKind, type OntologyGraphViewMode } from "./graphView";

const LazyOntologyGraphCanvas = lazy(() => import("./OntologyGraphCanvas"));

export interface OntologyQueryPlaygroundProps {
  graph: OntologyGraph | null;
  /** サーバ検索(実際の SQL 生成と同じ ontology-context 検索)に使う profile。 */
  profileId?: string;
  warningsJa?: string[];
  onRefreshSchema?: () => void | Promise<void>;
  refreshingSchema?: boolean;
}

const STAGE_LABEL_KEYS = {
  entity_definition: "ontologyPlayground.stage.entityDefinition",
  list_all: "ontologyPlayground.stage.listAll",
  relationship: "ontologyPlayground.stage.relationship",
  property: "ontologyPlayground.stage.property",
  aggregate: "ontologyPlayground.stage.aggregate",
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

type ServerSearchState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "success"; result: OntologyContextSearchResult };

/** 質問文(正規化後)の一致スパンを下線表示する。 */
function QuestionMatchedSpans({
  question,
  result,
}: {
  question: string;
  result: PlaygroundResult;
}) {
  const normalized = useMemo(() => normalizeGroundingText(question), [question]);
  const spans = useMemo(() => {
    const raw = result.candidates
      .filter((candidate) => candidate.span && candidate.score >= 0.65)
      .map((candidate) => candidate.span!)
      .sort((a, b) => a.start - b.start || b.end - a.end);
    const merged: Array<{ start: number; end: number }> = [];
    for (const span of raw) {
      const last = merged[merged.length - 1];
      if (last && span.start < last.end) {
        last.end = Math.max(last.end, span.end);
      } else {
        merged.push({ ...span });
      }
    }
    return merged;
  }, [result]);
  if (!normalized || spans.length === 0) return null;
  const parts: Array<{ text: string; matched: boolean }> = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start > cursor) parts.push({ text: normalized.slice(cursor, span.start), matched: false });
    parts.push({ text: normalized.slice(span.start, span.end), matched: true });
    cursor = span.end;
  }
  if (cursor < normalized.length) parts.push({ text: normalized.slice(cursor), matched: false });
  return (
    <p className="text-sm leading-6 text-muted" data-testid="ontology-playground-matched-spans">
      {parts.map((part, index) =>
        part.matched ? (
          <mark
            key={index}
            className="rounded bg-primary/15 px-0.5 font-medium text-foreground underline decoration-primary decoration-2 underline-offset-2"
          >
            {part.text}
          </mark>
        ) : (
          <span key={index}>{part.text}</span>
        )
      )}
    </p>
  );
}



interface GroundingComparison {
  both: string[];
  clientOnly: string[];
  serverOnly: string[];
}

/**
 * サーバ検索(SQL 生成と同じ ontology-context 検索)の結果パネル。
 * 即時判定との一致/不一致を 3 バケットのチップで示し、不一致時は
 * エイリアス追加などの改善アクションへ誘導する。
 */
function ServerSearchResultPanel({
  result,
  comparison,
  graph,
  onSelectNode,
}: {
  result: OntologyContextSearchResult;
  comparison: GroundingComparison | null;
  graph: OntologyGraph | null;
  onSelectNode: (nodeId: string) => void;
}) {
  const graphNodeIds = useMemo(
    () => new Set((graph?.nodes ?? []).map((node) => node.id)),
    [graph]
  );
  return (
    <section
      className="grid gap-3 rounded-md border border-border bg-card p-3"
      aria-label={t("ontologyPlayground.serverSearch.title")}
      data-testid="ontology-playground-server-result"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <ServerCog size={16} className="text-primary" aria-hidden="true" />
          {t("ontologyPlayground.serverSearch.title")}
        </h3>
        <StatusBadge
          variant="neutral"
          label={t("ontologyPlayground.serverSearch.hitCount", {
            count: result.hits.length,
          })}
        />
      </div>
      {comparison ? (
        <div className="grid gap-2" data-testid="ontology-playground-grounding-comparison">
          <div className="flex flex-wrap gap-2">
            <StatusBadge
              variant="success"
              label={t("ontologyPlayground.serverSearch.compareBoth", {
                count: comparison.both.length,
              })}
            />
            <StatusBadge
              variant="warning"
              label={t("ontologyPlayground.serverSearch.compareClientOnly", {
                count: comparison.clientOnly.length,
              })}
            />
            <StatusBadge
              variant="info"
              label={t("ontologyPlayground.serverSearch.compareServerOnly", {
                count: comparison.serverOnly.length,
              })}
            />
          </div>
          {comparison.serverOnly.length > 0 ? (
            <p className="text-xs leading-5 text-muted">
              {t("ontologyPlayground.serverSearch.compareHint")}
            </p>
          ) : null}
        </div>
      ) : null}
      {result.hits.length === 0 ? (
        <Banner severity="info">{t("ontologyPlayground.serverSearch.empty")}</Banner>
      ) : (
        <ol className="grid gap-1.5" data-testid="ontology-playground-server-hits">
          {result.hits.map((hit, index) => {
            const display = ontologyNodeDisplay(hit.node);
            const scorePercent = Math.round(hit.score * 100);
            const inGraph = graphNodeIds.has(hit.node.id);
            return (
              <li key={hit.node.id}>
                <button
                  type="button"
                  className="grid w-full cursor-pointer gap-1 rounded-md border border-border bg-background px-3 py-2 text-left outline-none transition-colors hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40 motion-reduce:transition-none disabled:cursor-default"
                  onClick={() => inGraph && onSelectNode(hit.node.id)}
                  disabled={!inGraph}
                  data-testid={`ontology-server-hit-${hit.node.id}`}
                >
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-semibold tabular-nums text-muted">
                      {index + 1}.
                    </span>
                    <span className="min-w-0 flex-1 break-words text-sm font-medium text-foreground">
                      {hit.node.business_name_ja}
                    </span>
                    <span className="text-xs text-muted">{display.kindLabel}</span>
                    {hit.inference_source !== "asserted" ? (
                      <StatusBadge
                        variant="info"
                        label={t("ontologyPlayground.serverSearch.inferred")}
                      />
                    ) : null}
                  </span>
                  <span className="flex items-center gap-2">
                    <span
                      className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted/20"
                      aria-hidden="true"
                    >
                      <span
                        className="block h-full rounded-full bg-primary"
                        style={{ width: `${scorePercent}%` }}
                      />
                    </span>
                    <span className="text-xs tabular-nums text-muted">
                      {t("ontologyPlayground.serverSearch.score")} {scorePercent}%
                    </span>
                  </span>
                  {hit.matched_terms.length > 0 ? (
                    <span className="text-xs leading-5 text-muted">
                      {t("ontologyPlayground.serverSearch.matchedTerms")}:{" "}
                      {hit.matched_terms.join("、")}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
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
  profileId = "",
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
  const [serverSearch, setServerSearch] = useState<ServerSearchState>({ status: "idle" });
  // 古いサーバ検索応答が新しい状態を上書きしないための世代カウンタ
  const serverSearchSeqRef = useRef(0);

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
    serverSearchSeqRef.current += 1;
    setServerSearch({ status: "idle" });
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
    // 質問が変わったら前のサーバ検索結果は無効
    serverSearchSeqRef.current += 1;
    setServerSearch({ status: "idle" });
  };

  const canServerSearch = Boolean(profileId && graphRevisionId && question.trim());
  const runServerSearch = async () => {
    const normalizedQuestion = question.trim();
    if (!graph || !canServerSearch || !normalizedQuestion) return;
    // 即時判定を未実行ならまず実行して比較の土台を揃える
    if (!result) runQuestion();
    const seq = ++serverSearchSeqRef.current;
    setServerSearch({ status: "loading" });
    try {
      const searchResult = await searchOntologyContext(profileId, {
        question: normalizedQuestion,
        ontologyRevisionId: graphRevisionId,
      });
      if (serverSearchSeqRef.current !== seq) return;
      setServerSearch({ status: "success", result: searchResult });
      setGraphViewMode("grounding");
    } catch (err) {
      if (serverSearchSeqRef.current !== seq) return;
      setServerSearch({
        status: "error",
        message:
          err instanceof Error && err.message
            ? err.message
            : t("ontologyPlayground.serverSearch.error"),
      });
    }
  };

  const hasResettableGroundingState = Boolean(
    question.length > 0 ||
      result ||
      selectedNodeId ||
      selectedEdgeId ||
      graphViewMode !== "all" ||
      serverSearch.status !== "idle"
  );

  const serverHitNodeIds = useMemo(
    () =>
      serverSearch.status === "success"
        ? serverSearch.result.hits.map((hit) => hit.node.id)
        : [],
    [serverSearch]
  );
  const serverEdgeIds = useMemo(
    () =>
      serverSearch.status === "success"
        ? serverSearch.result.edges.map((edge) => edge.id)
        : [],
    [serverSearch]
  );
  // グラフの強調は即時判定とサーバ検索の合成(比較チップでどちら由来かを示す)
  const highlightNodeIds = useMemo(
    () => [...new Set([...(result?.highlightNodeIds ?? []), ...serverHitNodeIds])],
    [result, serverHitNodeIds]
  );
  const highlightEdgeIds = useMemo(
    () => [...new Set([...(result?.highlightEdgeIds ?? []), ...serverEdgeIds])],
    [result, serverEdgeIds]
  );
  const groundingComparison = useMemo(() => {
    if (!result || serverSearch.status !== "success") return null;
    const clientIds = new Set(result.highlightNodeIds);
    const serverIds = new Set(serverHitNodeIds);
    return {
      both: [...clientIds].filter((id) => serverIds.has(id)),
      clientOnly: [...clientIds].filter((id) => !serverIds.has(id)),
      serverOnly: [...serverIds].filter((id) => !clientIds.has(id)),
    };
  }, [result, serverSearch, serverHitNodeIds]);
  const selectedNode = useMemo(
    () => graph?.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [graph, selectedNodeId]
  );
  const groundedRows = useMemo(
    () => (graph ? groundedRelationshipRows(graph, result) : []),
    [graph, result]
  );
  const browseRows = useMemo(
    () => (graph ? browseRelationshipRows(graph, groundedRows) : []),
    [graph, groundedRows]
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
            <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_auto_auto_auto]">
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
              <Button
                type="button"
                variant="secondary"
                size="lg"
                className="h-11 min-h-[44px] w-full whitespace-nowrap sm:w-auto"
                disabled={!canServerSearch || serverSearch.status === "loading"}
                loading={serverSearch.status === "loading"}
                onClick={() => void runServerSearch()}
                title={t("ontologyPlayground.serverSearch.hint")}
                data-testid="ontology-playground-server-search"
              >
                <ServerCog size={15} aria-hidden="true" />
                <span>{t("ontologyPlayground.serverSearch.run")}</span>
              </Button>
              <ClearActionButton
                className="w-full sm:w-auto"
                disabled={!hasResettableGroundingState}
                ariaLabel={t("ontologyPlayground.clearAriaLabel")}
                onClick={() => resetGroundingState({ clearQuestion: true })}
                dataTestId="ontology-playground-clear"
                label={t("ontologyPlayground.clear")}
              />
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
              <QuestionMatchedSpans question={question} result={result} />
              {result.suggestionsJa.length > 0 ? (
                <p className="text-sm text-muted">
                  {t("ontologyPlayground.suggestions")}: {result.suggestionsJa.join("、")}
                </p>
              ) : null}
            </div>
          ) : null}
          {serverSearch.status === "error" ? (
            <Banner severity="danger">{serverSearch.message}</Banner>
          ) : null}
          {serverSearch.status === "success" ? (
            <ServerSearchResultPanel
              result={serverSearch.result}
              comparison={groundingComparison}
              graph={graph}
              onSelectNode={setSelectedNodeId}
            />
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
                    <DisclosureChevron expanded={mobileGraphOpen} size={15} />
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
                <OntologyGroundingPathPanel graph={graph} result={result} rows={groundedRows} />
                <OntologyNodeDetailsPanel
                  graph={graph}
                  node={selectedNode}
                  onSelectNode={setSelectedNodeId}
                />
                {erDetails ? <OntologyErDetailsPanel details={erDetails} /> : null}
                <OntologyRelationshipListPanel
                  rows={browseRows}
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
